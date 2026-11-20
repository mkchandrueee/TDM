from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from tigr import entity_claims
from tigr.connection_models import WorkspaceConnection
from tigr.datasets import DataSet, DataSetVersion
from tigr.entity_claims import ensure_claims_current, lineage_claim_keys
from tigr.entity_lock_operations import reconcile_entity_locks
from tigr.governance import DataSetLock, DataSetReservation, DataUseRequest, EntityClaim
from tigr.iam import Workspace
from tigr.identity import HumanRole, Principal
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


def test_entity_claim_registry_indexes_active_identity_lookups_without_unique_owner_conflicts():
    indexes = {index.name: index for index in EntityClaim.__table__.indexes}
    active_identity = indexes["ix_entity_claim_active_identity"]
    assert not active_identity.unique
    assert str(active_identity.dialect_options["postgresql"]["where"]) == "status = 'active'"
    assert {column.name for column in active_identity.columns} == {
        "source_namespace", "source_object", "token_version", "entity_token",
    }


def test_entity_lock_rollout_is_disabled_by_default():
    assert WorkspaceConnection.entity_locking_enabled.default.arg is False


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
    connection_id = uuid4()
    db.add(WorkspaceConnection(
        id=connection_id, workspace_id=workspace_id, name="Claim source", connector_type="postgresql",
        direction="source", environment="test", owner="test", operation_scope="read_only",
        status="tested", created_by="test", updated_by="test",
    ))
    db.flush()
    now = datetime.now(UTC)

    def lease_for(token: str, actor: str):
        dataset_id, version_id, lock_id = uuid4(), uuid4(), uuid4()
        dataset = DataSet(id=dataset_id, reference_code=f"TDS-{dataset_id.hex}", workspace_id=workspace_id,
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
            source_namespace=f"connection:{connection_id}", source_object="public.customers",
            key_definition={"kind": "primary", "fields": ["id"]}, token_version="hmac-sha256:v1",
            entity_token=token, relationship_path=[],
        ))
        db.flush()
        return dataset, version, lock

    first_dataset, first_version, first_lock = lease_for("1" * 64, "qa-one")
    assert entity_claims.create_entity_claims(db, first_dataset, first_version, first_lock, "qa-one", now) == 0
    source_connection = db.get(WorkspaceConnection, connection_id)
    assert source_connection.entity_locking_enabled is False
    source_connection.entity_locking_enabled = True
    db.flush()
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
    db.refresh(first_claim)
    assert first_claim.status == "expired"


def test_reconciliation_releases_disabled_connection_claims(app_context):
    _client, db, _current = app_context
    workspace_id = uuid4()
    connection_id = uuid4()
    dataset_id, version_id, lock_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    db.add(Workspace(id=workspace_id, slug=f"reconcile-{workspace_id.hex[:8]}", name="Reconcile test", created_by="test"))
    db.flush()
    db.add(WorkspaceConnection(
        id=connection_id, workspace_id=workspace_id, name="Disabled source", connector_type="postgresql",
        direction="source", environment="test", owner="test", operation_scope="read_only",
        status="tested", created_by="test", updated_by="test",
    ))
    dataset = DataSet(id=dataset_id, reference_code=f"TDS-{dataset_id.hex}", workspace_id=workspace_id,
                      name="Reconcile test", owner_id="qa-one", creation_pathway="source_to_data", latest_version_number=1)
    version = DataSetVersion(id=version_id, dataset_id=dataset_id, workspace_id=workspace_id,
                             version_number=1, created_by="qa-one")
    lock = DataSetLock(id=lock_id, workspace_id=workspace_id, dataset_id=dataset_id, version_id=version_id,
                       actor_id="qa-one", actor_role="QA_ENGINEER", fencing_token=1,
                       acquired_at=now, expires_at=now + timedelta(minutes=5), status="active")
    claim = EntityClaim(
        id=uuid4(), workspace_id=workspace_id, dataset_id=dataset_id, version_id=version_id, lock_id=lock_id,
        actor_id="qa-one", source_namespace=f"connection:{connection_id}", source_object="public.customers",
        key_definition={"kind": "primary", "fields": ["id"]}, token_version="hmac-sha256:v1",
        entity_token="a" * 64, fencing_token=1, acquired_at=now, expires_at=now + timedelta(minutes=5), status="active",
    )
    db.add_all([dataset, version, lock, claim])
    db.flush()

    report = reconcile_entity_locks(db)
    db.flush()

    assert report["disabled_connection_claims_closed"] >= 1
    assert db.get(EntityClaim, claim.id).status == "released"
    assert "entity_token" not in report
    ensure_claims_current(db, lock, 1, now)


def test_two_qa_users_get_safe_cross_dataset_conflict_through_lock_and_preview_apis(app_context):
    client, db, current = app_context
    workspace_id, connection_id = uuid4(), uuid4()
    db.add(Workspace(id=workspace_id, slug=f"e2e-{workspace_id.hex[:8]}", name="Entity lock E2E", created_by="test"))
    db.flush()
    db.add(WorkspaceConnection(
        id=connection_id, workspace_id=workspace_id, name="Enabled source", connector_type="postgresql",
        direction="source", environment="test", owner="test", operation_scope="read_only",
        status="tested", created_by="test", updated_by="test", entity_locking_enabled=True,
    ))
    db.flush()
    now = datetime.now(UTC)
    datasets = []
    for index, actor in enumerate(("qa-one", "qa-two"), start=1):
        dataset_id, version_id = uuid4(), uuid4()
        dataset = DataSet(id=dataset_id, reference_code=f"TDS-{dataset_id.hex}", workspace_id=workspace_id,
                          name=f"Overlap set {index}", owner_id=actor, creation_pathway="scenario_to_data", latest_version_number=1)
        version = DataSetVersion(
            id=version_id, dataset_id=dataset_id, workspace_id=workspace_id, version_number=1,
            lifecycle_state="published", privacy_state="compliant", validation_state="passed",
            publication_state="published", record_count=1, provenance={"origin": "synthetic"},
            selected_fields=["customer"], schema_scope=[{"field": "customer", "data_type": "TEXT", "required": True}],
            created_by=actor,
        )
        request_id = uuid4()
        request = DataUseRequest(
            id=request_id, workspace_id=workspace_id, dataset_id=dataset_id, version_id=version_id,
            requester_id=actor, purpose="approved test use", starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(hours=1), status="approved", decided_by="workspace-admin", decided_at=now,
        )
        datasets.append((dataset, version, actor, request_id))
        db.add_all([dataset, version, request])
    db.flush()
    for dataset, version, actor, request_id in datasets:
        db.add(DataSetReservation(
            id=uuid4(), workspace_id=workspace_id, dataset_id=dataset.id, version_id=version.id,
            request_id=request_id, actor_id=actor, starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(hours=1), purpose="approved test use", status="active",
        ))
        db.add(DataSetRecord(
            id=uuid4(), workspace_id=workspace_id, dataset_id=dataset.id,
            version_id=version.id, ordinal=0, values={"customer": "masked-customer"},
        ))
        db.add(DatasetRecordEntityLineage(
            id=uuid4(), workspace_id=workspace_id, dataset_id=dataset.id, version_id=version.id, ordinal=0,
            source_namespace=f"connection:{connection_id}", source_object="public.customers",
            key_definition={"kind": "primary", "fields": ["customer_id"]},
            token_version="hmac-sha256:v1", entity_token="b" * 64, relationship_path=[],
        ))
    db.flush()

    current["principal"] = Principal(subject="qa-one", development_role=HumanRole.QA_ENGINEER)
    first = client.post(f"/api/v1/datasets/{datasets[0][0].id}/versions/1/locks", json={"lease_seconds": 300})
    assert first.status_code == 201, first.text
    current["principal"] = Principal(subject="qa-two", development_role=HumanRole.QA_ENGINEER)
    overlap = client.post(f"/api/v1/datasets/{datasets[1][0].id}/versions/1/locks", json={"lease_seconds": 300})
    assert overlap.status_code == 409
    assert "entity_token" not in overlap.text
    detail = client.get(f"/api/v1/datasets/{datasets[1][0].id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["sample_records"] == []
    assert detail.json()["blocked_record_count"] == 1
