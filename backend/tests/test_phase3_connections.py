from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError
from tigr.connection_models import WorkspaceConnection
from tigr.connection_schemas import ConnectionCreate
from tigr.connection_testing import test_connection as run_connection_test
from tigr.iam import UserIdentity, Workspace, WorkspaceMembership
from tigr.identity import HumanRole, Principal


def create_workspace(session, workspace_id):
    workspace = Workspace(id=workspace_id, slug=f"conn-{workspace_id.hex[:8]}", name="Connection test", created_by="test-setup")
    session.add(workspace)
    session.flush()
    return workspace


def test_connection_inventory_test_enable_edit_lifecycle(app_context):
    client, session, current = app_context
    workspace_id = uuid4()
    create_workspace(session, workspace_id)
    current["principal"] = Principal(subject="phase3-tde", development_role=HumanRole.TEST_DATA_ENGINEER)
    payload = {
        "name": "Customer file",
        "connector_type": "csv",
        "direction": "source",
        "environment": "Development",
        "purpose": "Source structure discovery",
        "operation_scope": "read_only",
        "connector_config": {"file_path": "customers.csv", "delimiter": ",", "has_header": True},
    }
    created = client.post(f"/api/v1/workspaces/{workspace_id}/connections", json=payload)
    assert created.status_code == 201
    connection = created.json()
    assert connection["status"] == "draft"
    assert "secret_reference" not in connection

    premature = client.post(f"/api/v1/workspaces/{workspace_id}/connections/{connection['id']}/enable")
    assert premature.status_code == 409

    tested = client.post(f"/api/v1/workspaces/{workspace_id}/connections/{connection['id']}/test")
    assert tested.status_code == 200
    assert tested.json()["status"] == "tested"
    assert tested.json()["test_result"]["succeeded"] is True
    assert tested.json()["structure_preview"]["values_included"] is False
    assert "customer_id" in [field["name"] for field in tested.json()["structure_preview"]["fields"]]

    enabled = client.post(f"/api/v1/workspaces/{workspace_id}/connections/{connection['id']}/enable")
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "enabled"
    assert client.get("/api/v1/dashboard", params={"workspace_id": str(workspace_id)}).json()["metrics"]["connections"]["count"] == 1

    updated = client.patch(f"/api/v1/workspaces/{workspace_id}/connections/{connection['id']}", json={"purpose": "Updated"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "draft"
    assert updated.json()["tested_at"] is None
    assert client.post(f"/api/v1/workspaces/{workspace_id}/connections/{connection['id']}/enable").status_code == 409


def test_qa_cannot_configure_connections(app_context):
    client, session, current = app_context
    workspace_id = uuid4()
    create_workspace(session, workspace_id)
    current["principal"] = Principal(subject="phase3-qa", development_role=HumanRole.QA_ENGINEER)
    response = client.post(f"/api/v1/workspaces/{workspace_id}/connections", json={
        "name": "QA attempt", "connector_type": "csv", "direction": "source", "environment": "Testing",
        "connector_config": {"file_path": "customers.csv"},
    })
    assert response.status_code == 403


def test_workspace_admin_has_global_admin_access(app_context):
    client, session, current = app_context
    own_workspace_id = uuid4()
    other_workspace_id = uuid4()
    create_workspace(session, own_workspace_id)
    create_workspace(session, other_workspace_id)
    subject = "workspace-scoped-admin"
    session.add(UserIdentity(subject=subject, display_name=subject))
    session.add(WorkspaceMembership(workspace_id=own_workspace_id, subject=subject, role=HumanRole.ADMIN.value, granted_by="test-setup"))
    session.flush()
    current["principal"] = Principal(subject=subject)

    identity = client.get("/api/v1/me").json()
    assert identity["platform_admin"] is True
    assert [workspace["id"] for workspace in identity["workspaces"]] == [str(own_workspace_id)]
    assert {workspace["id"] for workspace in client.get("/api/v1/workspaces").json()} == {str(own_workspace_id), str(other_workspace_id)}
    assert client.post("/api/v1/workspaces", json={"slug": "admin-created", "name": "Admin Created"}).status_code == 201
    assert client.get(f"/api/v1/workspaces/{other_workspace_id}/connections").status_code == 200

    allowed = client.post(f"/api/v1/workspaces/{own_workspace_id}/connections", json={
        "name": "Scoped file", "connector_type": "csv", "direction": "source", "environment": "Test",
        "connector_config": {"file_path": "customers.csv"},
    })
    assert allowed.status_code == 201


def test_connector_contract_rejects_path_traversal_and_secretless_postgres():
    with pytest.raises(ValueError):
        ConnectionCreate(
            name="Unsafe file", connector_type="csv", direction="source", environment="Dev",
            connector_config={"file_path": "../private.csv"},
        )
    with pytest.raises(ValueError):
        ConnectionCreate(name="Database", connector_type="postgresql", direction="source", environment="Dev")


def test_malformed_json_and_database_errors_are_redacted(tmp_path, monkeypatch):
    nested_json = tmp_path / "nested.json"
    nested_json.write_text('{"records": [{"id": 1, "profile": {"city": "private-example"}}]}', encoding="utf-8")
    monkeypatch.setattr("tigr.connection_testing.resolve_file", lambda path: nested_json)
    nested_connection = WorkspaceConnection(connector_type="json", connector_config={"file_path": "nested.json"})
    nested_result = run_connection_test(nested_connection)
    assert nested_result.succeeded
    assert nested_result.structure["fields"][0]["name"] == "records"
    assert nested_result.structure["fields"][0]["fields"][1]["fields"][0]["name"] == "city"
    assert "private-example" not in str(nested_result.structure)

    bad_json = tmp_path / "malformed.json"
    bad_json.write_text('{"customer": [', encoding="utf-8")
    monkeypatch.setattr("tigr.connection_testing.resolve_file", lambda path: bad_json)
    file_connection = WorkspaceConnection(connector_type="json", connector_config={"file_path": "malformed.json"})
    file_result = run_connection_test(file_connection)
    assert not file_result.succeeded
    assert "syntax" in file_result.message
    assert "customer" not in file_result.message

    monkeypatch.setenv("TIGR_SECRET_TEST_DB", "postgresql://user:sensitive-value@127.0.0.1/example")
    def fail_engine(*args, **kwargs):
        raise SQLAlchemyError("connection string includes sensitive-value")
    monkeypatch.setattr("tigr.connection_testing.create_engine", fail_engine)
    db_connection = WorkspaceConnection(
        connector_type="postgresql", secret_reference="env://TIGR_SECRET_TEST_DB", connector_config={"schema": "public"},
    )
    result = run_connection_test(db_connection)
    assert not result.succeeded
    assert "sensitive-value" not in result.message
    assert result.message == "Connection failed. Check the secret reference, network access, and database permissions"
