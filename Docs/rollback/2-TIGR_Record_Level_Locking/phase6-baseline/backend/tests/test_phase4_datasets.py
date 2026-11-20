from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from tigr.datasets import DataSetLineageEvent, DataSetVersion
from tigr.iam import AccessGrant, UserIdentity, Workspace, WorkspaceMembership
from tigr.identity import HumanRole, Principal


def add_workspace(session, workspace_id):
    workspace = Workspace(id=workspace_id, slug=f"dataset-{workspace_id.hex[:8]}", name="Dataset workspace", created_by="test-setup")
    session.add(workspace)
    session.flush()
    return workspace


def draft_payload(**overrides):
    payload = {
        "name": "Customer test set",
        "description": "Versioned source-backed test data metadata",
        "creation_pathway": "source_to_data",
        "schema_scope": [{"entity": "customer", "field": "customer_id", "data_type": "integer", "required": True}],
        "selected_fields": ["customer.customer_id"],
        "provenance": {"origin": "source_backed", "source_object": "customers", "source_schema_version": "schema-v1"},
        "permitted_use": ["testing", "validation"],
    }
    payload.update(overrides)
    return payload


def create_dataset(client, workspace_id):
    response = client.post(f"/api/v1/workspaces/{workspace_id}/datasets", json=draft_payload())
    assert response.status_code == 201, response.text
    return response.json()


def record_validation(client, current, workspace_id, dataset_id, payload):
    previous = current["principal"]
    current["principal"] = Principal(
        subject="phase4-validation-service", actor_type="service", service_workspace_id=str(workspace_id),
        service_scopes=("dataset:validate",),
    )
    response = client.post(f"/api/v1/datasets/{dataset_id}/versions/1/validation", json=payload)
    current["principal"] = previous
    return response


def test_dataset_publish_gate_versions_and_lineage_ancestors(app_context):
    client, session, current = app_context
    workspace_id = uuid4()
    add_workspace(session, workspace_id)
    current["principal"] = Principal(subject="phase4-tde", development_role=HumanRole.TEST_DATA_ENGINEER)
    created = create_dataset(client, workspace_id)
    dataset_id = created["id"]
    assert created["latest_version"]["version_number"] == 1
    assert created["latest_version"]["lifecycle_state"] == "draft"

    catalog = client.get("/api/v1/datasets", params={"workspace_id": str(workspace_id)})
    assert catalog.status_code == 200
    assert catalog.json()["total"] == 1
    assert catalog.json()["items"][0]["latest_version"]["lifecycle_state"] == "draft"

    detail = client.get(f"/api/v1/datasets/{dataset_id}")
    first_event = detail.json()["lineage"][0]
    assert first_event["event_type"] == "draft_created"
    premature = client.post(f"/api/v1/datasets/{dataset_id}/versions/1/publish")
    assert premature.status_code == 409

    human_validation = client.post(f"/api/v1/datasets/{dataset_id}/versions/1/validation", json={"validation_state": "passed", "privacy_state": "compliant"})
    assert human_validation.status_code == 403
    awaiting_mask = record_validation(client, current, workspace_id, dataset_id, {
        "validation_state": "passed", "privacy_state": "requires_masking", "evidence": {"field_count": 1},
    })
    assert awaiting_mask.status_code == 200
    assert awaiting_mask.json()["lifecycle_state"] == "draft"
    assert client.post(f"/api/v1/datasets/{dataset_id}/versions/1/publish").status_code == 409

    ready = record_validation(client, current, workspace_id, dataset_id, {
        "validation_state": "passed", "privacy_state": "compliant", "evidence": {"field_count": 1, "summary": "Checks passed"},
    })
    assert ready.json()["lifecycle_state"] == "ready"
    current["principal"] = Principal(subject="phase4-admin", development_role=HumanRole.ADMIN)
    assert client.post(f"/api/v1/datasets/{dataset_id}/versions/1/publish").status_code == 403
    current["principal"] = Principal(subject="phase4-tde", development_role=HumanRole.TEST_DATA_ENGINEER)
    published = client.post(f"/api/v1/datasets/{dataset_id}/versions/1/publish")
    assert published.status_code == 200
    assert published.json()["publication_state"] == "published"
    published_lineage = client.get(f"/api/v1/datasets/{dataset_id}").json()["lineage"]
    last_v1_event_id = published_lineage[-1]["id"]

    version_two = client.post(f"/api/v1/datasets/{dataset_id}/versions", json=draft_payload(name="Customer test set v2"))
    assert version_two.status_code == 201
    assert version_two.json()["version_number"] == 2
    second_detail = client.get(f"/api/v1/datasets/{dataset_id}", params={"version_number": 2}).json()
    event_rows = second_detail["lineage"]
    assert [event["sequence_number"] for event in event_rows] == sorted(event["sequence_number"] for event in event_rows)
    version_event = next(event for event in event_rows if event["event_type"] == "version_created")
    assert version_event["version_number"] == 2
    assert last_v1_event_id in version_event["parent_event_ids"]

    missing_parent = client.post(f"/api/v1/datasets/{dataset_id}/versions/2/lineage", json={
        "event_type": "mapping_selected", "stage": "mapping", "parent_event_ids": [str(uuid4())],
    })
    assert missing_parent.status_code == 404


def test_qa_only_sees_published_dataset_versions_with_grant(app_context):
    client, session, current = app_context
    workspace_id = uuid4()
    add_workspace(session, workspace_id)
    current["principal"] = Principal(subject="phase4-author", development_role=HumanRole.TEST_DATA_ENGINEER)
    created = create_dataset(client, workspace_id)
    dataset_id = created["id"]
    record_validation(client, current, workspace_id, dataset_id, {"validation_state": "passed", "privacy_state": "compliant"})
    current["principal"] = Principal(subject="phase4-author", development_role=HumanRole.TEST_DATA_ENGINEER)
    client.post(f"/api/v1/datasets/{dataset_id}/versions/1/publish")
    qa_subject = "phase4-qa-reader"
    session.add(UserIdentity(subject=qa_subject, display_name=qa_subject))
    session.add(WorkspaceMembership(workspace_id=workspace_id, subject=qa_subject, role=HumanRole.QA_ENGINEER.value, granted_by="test-setup"))
    session.flush()
    current["principal"] = Principal(subject=qa_subject)
    assert client.get(f"/api/v1/datasets/{dataset_id}").status_code == 404
    assert client.get("/api/v1/datasets", params={"workspace_id": str(workspace_id)}).json()["total"] == 0
    assert client.get("/api/v1/dashboard", params={"workspace_id": str(workspace_id)}).json()["metrics"]["datasets"]["count"] == 0

    session.add(AccessGrant(
        id=uuid4(), workspace_id=workspace_id, subject=qa_subject, scope_type="dataset", scope_id=dataset_id,
        permissions=["data:read"], granted_by="test-setup", valid_from=datetime.now(UTC) - timedelta(minutes=1),
    ))
    session.flush()
    detail = client.get(f"/api/v1/datasets/{dataset_id}")
    assert detail.status_code == 200
    assert detail.json()["selected_version"]["lifecycle_state"] == "published"
    assert client.get("/api/v1/datasets", params={"workspace_id": str(workspace_id)}).json()["total"] == 1
    assert client.get("/api/v1/dashboard", params={"workspace_id": str(workspace_id)}).json()["metrics"]["datasets"]["count"] == 1

    current["principal"] = Principal(subject="phase4-author", development_role=HumanRole.TEST_DATA_ENGINEER)
    created_v2 = client.post(f"/api/v1/datasets/{dataset_id}/versions", json=draft_payload(name="Customer test set v2"))
    assert created_v2.status_code == 201
    current["principal"] = Principal(subject=qa_subject)
    qa_detail = client.get(f"/api/v1/datasets/{dataset_id}").json()
    assert qa_detail["selected_version"]["version_number"] == 1
    assert [version["version_number"] for version in qa_detail["versions"]] == [1]
    assert client.get("/api/v1/datasets", params={"workspace_id": str(workspace_id)}).json()["items"][0]["latest_version"]["version_number"] == 1


def test_dataset_version_manifest_and_lineage_are_immutable(app_context):
    client, session, current = app_context
    workspace_id = uuid4()
    add_workspace(session, workspace_id)
    current["principal"] = Principal(subject="phase4-immutable", development_role=HumanRole.TEST_DATA_ENGINEER)
    created = create_dataset(client, workspace_id)
    version_id = created["latest_version"]["id"]
    event = session.query(DataSetLineageEvent).filter_by(dataset_id=created["id"]).one()
    event_id = event.id

    with pytest.raises(SQLAlchemyError):
        session.execute(update(DataSetVersion).where(DataSetVersion.id == version_id).values(schema_scope=[]))
    session.rollback()
    with pytest.raises(SQLAlchemyError):
        session.execute(update(DataSetLineageEvent).where(DataSetLineageEvent.id == event_id).values(safe_evidence={"summary": "changed"}))
    session.rollback()


def test_validation_failure_quarantines_or_fails_and_expiry_is_enforced(app_context, monkeypatch):
    client, session, current = app_context
    workspace_id = uuid4()
    add_workspace(session, workspace_id)
    current["principal"] = Principal(subject="phase4-lifecycle", development_role=HumanRole.TEST_DATA_ENGINEER)
    created = create_dataset(client, workspace_id)
    dataset_id = created["id"]
    failed = record_validation(client, current, workspace_id, dataset_id, {"validation_state": "failed", "privacy_state": "blocked"})
    assert failed.json()["lifecycle_state"] == "failed"
    assert client.post(f"/api/v1/datasets/{dataset_id}/versions/1/publish").status_code == 409

    expiring = create_dataset(client, workspace_id)
    expiring_id = expiring["id"]
    record_validation(client, current, workspace_id, expiring_id, {"validation_state": "passed", "privacy_state": "compliant"})
    client.post(f"/api/v1/datasets/{expiring_id}/versions/1/publish")
    version = session.query(DataSetVersion).filter_by(dataset_id=expiring_id, version_number=1).one()
    version.expiry_at = version.created_at + timedelta(seconds=1)
    session.flush()
    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=1)
    monkeypatch.setattr("tigr.dataset_services.datetime", FutureDateTime)
    expired = client.post(f"/api/v1/datasets/{expiring_id}/versions/1/expire")
    assert expired.status_code == 200
    assert expired.json()["lifecycle_state"] == "expired"
