from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from tigr import entity_claims
from tigr.datasets import DataSet, DataSetVersion
from tigr.entity_claims import lineage_claim_keys
from tigr.governance import DataSetLock, EntityClaim
from tigr.iam import Workspace
from tigr.schema_records import DataSetRecord, DatasetRecordEntityLineage


def test_claim_scope_deduplicates_repeated_lineage_references():
    item = SimpleNamespace(
        source_namespace="tenant:a", source_object="public.customers",
        token_version="hmac-sha256:v1", entity_token="a" * 64,
        key_definition={"kind": "primary", "fields": ["id"]},
    )

    class Result:
        def all(self):
            return [item, item]

    class FakeDb:
        def scalars(self, _query):
            return Result()

    keys = lineage_claim_keys(FakeDb(), "version-id")
    assert len(keys) == 1
    assert keys[0]["entity_token"] == "a" * 64


def test_entity_claim_registry_has_database_partial_unique_constraint():
    indexes = {index.name: index for index in EntityClaim.__table__.indexes}
    active_identity = indexes["uq_entity_claim_active_identity"]
    assert active_identity.unique
    assert str(active_identity.dialect_options["postgresql"]["where"]) == "status = 'active'"
    assert {column.name for column in active_identity.columns} == {
        "source_namespace", "source_object", "token_version", "entity_token",
    }


def test_central_record_filter_returns_only_unclaimed_ordinals(monkeypatch):
    monkeypatch.setattr(entity_claims, "conflicting_ordinals", lambda _db, _version, _actor, _now: {1})
    rows, allowed_count, blocked_count = entity_claims.filter_dataset_records(
        object(), SimpleNamespace(id="version-id"), "reader", [{"name": "allowed"}, {"name": "claimed"}],
    )
    assert rows == [{"name": "allowed"}]
    assert (allowed_count, blocked_count) == (1, 1)


def test_derivation_denial_does_not_expose_protected_tokens(monkeypatch):
    monkeypatch.setattr(entity_claims, "conflicting_ordinals", lambda _db, _version, _actor, _now: {4})
    with pytest.raises(HTTPException) as error:
        entity_claims.require_dataset_derivation_access(object(), SimpleNamespace(id="version-id"), "reader")
    assert error.value.status_code == 409
    assert "entity_token" not in error.value.detail
    assert "temporarily unavailable" in error.value.detail


def test_stale_worker_cannot_commit_after_job_is_superseded():
    class FakeDb:
        def scalar(self, _query):
            return SimpleNamespace(status="succeeded")

    with pytest.raises(HTTPException) as error:
        entity_claims.assert_job_current(FakeDb(), "job-id")
    assert error.value.status_code == 409
    assert "current" in error.value.detail


def test_postgres_claims_allow_disjoint_scope_and_reject_overlapping_scope(app_context):
    _client, db, _current = app_context
    workspace_id = uuid4()
    db.add(Workspace(id=workspace_id, slug=f"claim-{workspace_id.hex[:8]}", name="Claim test", created_by="test"))
    db.flush()
    now = datetime.now(UTC)

    def lease_for(token: str, actor: str):
        dataset_id, version_id, lock_id = uuid4(), uuid4(), uuid4()
        dataset = DataSet(id=dataset_id, reference_code=f"DS-{dataset_id.hex}", workspace_id=workspace_id,
                          name="Claim test", owner_id=actor, creation_pathway="source_to_data", latest_version_number=1)
        version = DataSetVersion(id=version_id, dataset_id=dataset_id, workspace_id=workspace_id,
                                 version_number=1, created_by=actor)
        lock = DataSetLock(id=lock_id, workspace_id=workspace_id, dataset_id=dataset_id, version_id=version_id,
                           actor_id=actor, actor_role="QA_ENGINEER", fencing_token=1,
                           acquired_at=now, expires_at=now + timedelta(minutes=5), status="active")
        db.add_all([dataset, version, lock])
        db.flush()
        db.add(DatasetRecordEntityLineage(
            id=uuid4(), workspace_id=workspace_id, dataset_id=dataset_id, version_id=version_id, ordinal=0,
            source_namespace="claim-test-namespace", source_object="public.customers",
            key_definition={"kind": "primary", "fields": ["id"]}, token_version="hmac-sha256:v1",
            entity_token=token, relationship_path=[],
        ))
        db.flush()
        return dataset, version, lock

    first_dataset, first_version, first_lock = lease_for("1" * 64, "qa-one")
    assert entity_claims.create_entity_claims(db, first_dataset, first_version, first_lock, "qa-one", now) == 1
    db.flush()

    disjoint_dataset, disjoint_version, disjoint_lock = lease_for("2" * 64, "qa-two")
    assert entity_claims.create_entity_claims(db, disjoint_dataset, disjoint_version, disjoint_lock, "qa-two", now) == 1
    db.flush()

    overlapping_dataset, overlapping_version, overlapping_lock = lease_for("1" * 64, "qa-three")
    db.add(DataSetRecord(
        id=uuid4(), workspace_id=workspace_id, dataset_id=overlapping_dataset.id,
        version_id=overlapping_version.id, ordinal=0, values={"private": "must-not-be-returned"},
    ))
    db.flush()
    allowed, allowed_count, blocked_count = entity_claims.filter_dataset_records(db, overlapping_version, "qa-three")
    assert allowed == []
    assert (allowed_count, blocked_count) == (0, 1)
    with pytest.raises(IntegrityError), db.begin_nested():
        entity_claims.create_entity_claims(db, overlapping_dataset, overlapping_version, overlapping_lock, "qa-three", now)
        db.flush()
    first_claim = db.query(EntityClaim).filter_by(lock_id=first_lock.id, status="active").one()
    first_claim.expires_at = now - timedelta(seconds=1)
    db.flush()
    assert entity_claims.create_entity_claims(
        db, overlapping_dataset, overlapping_version, overlapping_lock, "qa-three", now,
    ) == 1
    db.flush()
    assert first_claim.status == "expired"
