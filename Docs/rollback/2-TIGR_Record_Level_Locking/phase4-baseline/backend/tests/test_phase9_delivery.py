import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from tigr.audit import AuditEvent
from tigr.connection_models import WorkspaceConnection
from tigr.datasets import DataSet, DataSetVersion
from tigr.delivery import ExportArtifact, ProvisioningRun
from tigr.delivery_api import _safe_cell
from tigr.iam import AccessGrant, UserIdentity, Workspace
from tigr.identity import HumanRole, Principal
from tigr.jobs import Job, OutboxEvent
from tigr.outbox_worker import STALE_AFTER, claim_next


def add_published_dataset(session, workspace_id):
    dataset_id = uuid4()
    version_id = uuid4()
    dataset = DataSet(
        id=dataset_id, workspace_id=workspace_id, name="Delivery fixture", description="",
        owner_id="delivery-test", creation_pathway="source_to_data", latest_version_number=1,
    )
    version = DataSetVersion(
        id=version_id, dataset_id=dataset_id, workspace_id=workspace_id, version_number=1, lifecycle_state="published",
        schema_scope=[{"field": "customer_id", "data_type": "integer", "required": True}],
        selected_fields=["customer_id"], provenance={}, privacy_state="compliant", validation_state="passed",
        publication_state="published", permitted_use=["testing"], validation_evidence={"artifact_ref": "fixture.json"},
        created_by="delivery-test", published_at=datetime.now(UTC),
    )
    session.add_all([dataset, version])
    session.flush()
    return dataset


def test_csv_formula_leading_values_are_neutralized():
    for value in ("=1+1", " +cmd", "-10", "@SUM(A1:A2)", "\tvalue", "\rvalue"):
        assert _safe_cell(value).startswith("'")
    assert _safe_cell("normal") == "normal"


def test_file_provisioning_is_idempotent_lineaged_and_cleanable(app_context, monkeypatch, tmp_path):
    client, session, current = app_context
    workspace_id = uuid4()
    session.add(Workspace(id=workspace_id, slug=f"deliver-{workspace_id.hex[:8]}", name="Delivery", created_by="test"))
    session.flush()
    dataset = add_published_dataset(session, workspace_id)
    connection = WorkspaceConnection(
        id=uuid4(), workspace_id=workspace_id, name="Output", connector_type="json", direction="target",
        environment="test", purpose="delivery test", owner="test", operation_scope="write_only",
        connector_config={"file_path": "out.json"}, status="enabled", tested_by="test",
        test_result={"succeeded": True}, structure_preview={}, created_by="test", updated_by="test",
    )
    session.add(connection)
    session.flush()
    current["principal"] = Principal(subject="delivery-tde", development_role=HumanRole.TEST_DATA_ENGINEER)
    (tmp_path / f"{dataset.id}.json").write_text(json.dumps([{"customer_id": 101}]), encoding="utf-8")
    monkeypatch.setattr("tigr.delivery_api.get_settings", lambda: type("Settings", (), {"artifact_root": str(tmp_path), "env": "test"})())
    monkeypatch.setattr("tigr.delivery_api.resolve_file", lambda relative: tmp_path / relative)
    path = f"/api/v1/datasets/{dataset.id}/versions/1/provision"
    payload = {"target_connection_id": str(connection.id), "idempotency_key": "stable-key-0001"}
    first = client.post(path, json=payload)
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "succeeded"
    assert first.json()["record_count"] == 1
    output = tmp_path / f"out-{first.json()['id'].replace('-', '')}.json"
    assert json.loads(output.read_text(encoding="utf-8")) == [{"customer_id": 101}]
    retry = client.post(path, json=payload)
    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]
    lineage = client.get(f"/api/v1/datasets/{dataset.id}/versions/1/lineage").json()
    assert any(event["event_type"] == "target_provisioned" and event["safe_evidence"]["target_connection_id"] == str(connection.id) for event in lineage)
    assert session.query(ProvisioningRun).count() == 1
    current["principal"] = Principal(subject="delivery-qa", development_role=HumanRole.QA_ENGINEER)
    assert client.get(f"/api/v1/datasets/{dataset.id}/provisioning-runs").status_code == 404
    session.add(UserIdentity(subject="delivery-qa", display_name="Delivery QA"))
    session.flush()
    session.add(AccessGrant(id=uuid4(), workspace_id=workspace_id, subject="delivery-qa", scope_type="dataset",
                            scope_id=str(dataset.id), permissions=["data:read"], granted_by="test",
                            valid_from=datetime.now(UTC) - timedelta(minutes=1)))
    session.flush()
    visible_runs = client.get(f"/api/v1/datasets/{dataset.id}/provisioning-runs").json()
    assert visible_runs[0]["target_object"] is None
    current["principal"] = Principal(subject="delivery-tde", development_role=HumanRole.TEST_DATA_ENGINEER)
    cleaned = client.delete(f"/api/v1/provisioning-runs/{first.json()['id']}")
    assert cleaned.status_code == 200
    assert cleaned.json()["status"] == "cleaned"
    assert not output.exists()
    (tmp_path / f"{dataset.id}.json").write_text(json.dumps([{"unexpected": "value"}]), encoding="utf-8")
    rejected = client.post(path, json={**payload, "idempotency_key": "stable-key-0002"})
    assert rejected.status_code == 201
    assert rejected.json()["status"] == "failed"
    assert rejected.json()["record_count"] == 0


def test_expired_csv_export_is_unavailable(app_context, tmp_path, monkeypatch):
    client, session, current = app_context
    workspace_id = uuid4()
    session.add(Workspace(id=workspace_id, slug=f"export-{workspace_id.hex[:8]}", name="Export", created_by="test"))
    session.flush()
    monkeypatch.setattr("tigr.delivery_api.get_settings", lambda: type("Settings", (), {"artifact_root": str(tmp_path)})())
    current["principal"] = Principal(subject="export-admin", development_role=HumanRole.ADMIN)
    created = client.post(f"/api/v1/workspaces/{workspace_id}/exports?report_type=datasets")
    assert created.status_code == 201, created.text
    artifact = session.get(ExportArtifact, created.json()["id"])
    artifact.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()
    expired = client.get(created.json()["download_url"])
    assert expired.status_code == 404
    assert session.get(ExportArtifact, artifact.id) is None


def test_jobs_inventory_applies_role_visibility_before_totals(app_context):
    client, session, current = app_context
    workspace_id = uuid4()
    session.add(Workspace(id=workspace_id, slug=f"jobs-{workspace_id.hex[:8]}", name="Jobs", created_by="test"))
    session.flush()
    jobs = [
        Job(id=uuid4(), job_type=f"job_{actor}", status="succeeded", workspace_id=str(workspace_id),
            scope_key=f"workspace:{workspace_id}", idempotency_key=f"job-{actor}", actor_type="human",
            actor_id=actor, actor_role=actor_role, request_id=f"request-{actor}", progress=100,
            safe_summary=f"safe {actor} summary")
        for actor, actor_role in (("jobs-tde", HumanRole.TEST_DATA_ENGINEER.value),
                                  ("jobs-qa", HumanRole.QA_ENGINEER.value),
                                  ("jobs-admin", HumanRole.ADMIN.value))
    ]
    session.add_all(jobs)
    session.flush()
    current["principal"] = Principal(subject="jobs-tde", development_role=HumanRole.TEST_DATA_ENGINEER)
    result = client.get("/api/v1/jobs", params={"workspace_id": str(workspace_id)})
    assert result.status_code == 200
    assert result.json()["total"] == 2
    assert {item["job_type"] for item in result.json()["items"]} == {"job_jobs-tde", "job_jobs-qa"}
    current["principal"] = Principal(subject="jobs-qa", development_role=HumanRole.QA_ENGINEER)
    qa_result = client.get("/api/v1/jobs", params={"workspace_id": str(workspace_id)})
    assert qa_result.json()["total"] == 1
    hidden = client.get(f"/api/v1/jobs/{jobs[0].id}")
    assert hidden.status_code == 404


def test_activity_csv_uses_the_same_role_scope_as_activity_api(app_context, tmp_path, monkeypatch):
    client, session, current = app_context
    workspace_id = uuid4()
    session.add(Workspace(id=workspace_id, slug=f"audit-{workspace_id.hex[:8]}", name="Audit", created_by="test"))
    session.flush()
    for actor, actor_role in (("audit-tde", HumanRole.TEST_DATA_ENGINEER.value),
                              ("audit-qa", HumanRole.QA_ENGINEER.value),
                              ("audit-admin", HumanRole.ADMIN.value)):
        session.add(AuditEvent(id=uuid4(), actor_type="human", actor_id=actor, actor_role=actor_role,
            workspace_id=str(workspace_id), action=f"action_{actor}", object_type="fixture", object_id=None,
            outcome="succeeded", request_id=f"request_{actor}", safe_metadata={}))
    session.flush()
    monkeypatch.setattr("tigr.delivery_api.get_settings", lambda: type("Settings", (), {"artifact_root": str(tmp_path)})())
    current["principal"] = Principal(subject="audit-tde", development_role=HumanRole.TEST_DATA_ENGINEER)
    created = client.post(f"/api/v1/workspaces/{workspace_id}/exports?report_type=activities")
    assert created.status_code == 201
    artifact = session.get(ExportArtifact, created.json()["id"])
    csv_text = Path(artifact.file_path).read_text(encoding="utf-8")
    assert "action_audit-tde" in csv_text
    assert "action_audit-qa" in csv_text
    assert "action_audit-admin" not in csv_text


def test_worker_claim_is_exclusive_and_expired_claim_can_be_recovered(app_context, monkeypatch):
    _, session, _ = app_context
    event = OutboxEvent(
        id=uuid4(), aggregate_type="dataset", aggregate_id="worker-test", workspace_id=None,
        event_type="scenario.generation_requested", payload={"job_id": str(uuid4())},
        request_id="worker-request",
    )
    session.add(event)
    session.flush()

    class SessionFactory:
        @staticmethod
        def begin():
            return nullcontext(session)

    monkeypatch.setattr("tigr.outbox_worker.SessionLocal", SessionFactory)
    first_claim = claim_next()
    assert first_claim is not None
    assert first_claim["id"] == event.id
    assert first_claim["attempt_count"] == 1
    assert claim_next() is None

    event.claimed_at = datetime.now(UTC) - STALE_AFTER - timedelta(seconds=1)
    session.flush()
    recovered_claim = claim_next()
    assert recovered_claim is not None
    assert recovered_claim["id"] == event.id
    assert recovered_claim["attempt_count"] == 2
    assert recovered_claim["claim_token"] != first_claim["claim_token"]
