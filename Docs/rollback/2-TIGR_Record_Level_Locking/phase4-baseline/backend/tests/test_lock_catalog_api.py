from uuid import uuid4

from tigr.connection_models import WorkspaceConnection
from tigr.connection_testing import ConnectionTestResult
from tigr.iam import Workspace
from tigr.identity import HumanRole, Principal


def test_connection_snapshots_and_tde_configuration_are_versioned(app_context, monkeypatch):
    client, session, current = app_context
    workspace_id = uuid4()
    connection_id = uuid4()
    session.add(Workspace(
        id=workspace_id, slug=f"lock-catalog-{workspace_id.hex[:8]}",
        name="Lock Catalog", created_by="test-setup",
    ))
    session.flush()
    session.add(WorkspaceConnection(
        id=connection_id, workspace_id=workspace_id, name="Customer source",
        connector_type="postgresql", direction="source", environment="test",
        owner="test-setup", operation_scope="read_only", secret_reference="env://SOURCE_DB",
        connector_config={"schema": "public"}, status="draft", created_by="test-setup",
        updated_by="test-setup",
    ))
    session.flush()
    current["principal"] = Principal(subject="lock-tde", development_role=HumanRole.TEST_DATA_ENGINEER)

    structure = {
        "format": "postgresql",
        "objects": [
            {
                "name": "customers", "type": "table",
                "fields": [{"name": "tenant_id", "type": "INTEGER"}, {"name": "customer_id", "type": "INTEGER"}],
                "primary_key": {"name": "customers_pkey", "fields": ["tenant_id", "customer_id"], "evidence": "database_primary_key", "validated": True, "enforced": True},
                "unique_constraints": [], "unique_indexes": [], "foreign_keys": [], "foreign_key_edges": [],
            },
            {
                "name": "accounts", "type": "table",
                "fields": [{"name": "tenant_id", "type": "INTEGER"}, {"name": "customer_id", "type": "INTEGER"}],
                "primary_key": {"name": "accounts_pkey", "fields": ["tenant_id", "customer_id"], "evidence": "database_primary_key", "validated": True, "enforced": True},
                "unique_constraints": [], "unique_indexes": [],
                "foreign_keys": [
                    {"field": "tenant_id", "references_object": "customers", "references_field": "tenant_id"},
                    {"field": "customer_id", "references_object": "customers", "references_field": "customer_id"},
                ],
                "foreign_key_edges": [{
                    "id": "fk_accounts_customers", "name": "accounts_customer_fk",
                    "source_object": "accounts", "source_fields": ["tenant_id", "customer_id"],
                    "target_object": "customers", "target_fields": ["tenant_id", "customer_id"],
                    "evidence": "database_foreign_key", "validated": True, "enforced": True,
                }],
            },
        ],
        "values_included": False,
    }
    monkeypatch.setattr(
        "tigr.connections.test_connection",
        lambda _connection: ConnectionTestResult(True, "Connection test passed", structure),
    )

    tested = client.post(f"/api/v1/workspaces/{workspace_id}/connections/{connection_id}/test")
    assert tested.status_code == 200, tested.text
    assert tested.json()["structure_preview"]["structure_snapshot_version"] == 1

    approval = client.post(
        f"/api/v1/workspaces/{workspace_id}/connections/{connection_id}/lock-configurations",
        json={
            "anchors": [{
                "object_name": "customers", "key_kind": "primary",
                "key_name": "customers_pkey", "key_fields": ["tenant_id", "customer_id"],
            }],
            "paths": [{
                "name": "customer-accounts", "anchor_object": "customers",
                "steps": [{"relationship_id": "fk_accounts_customers", "direction": "reverse"}],
                "max_depth": 1,
            }],
            "manual_relationships": [],
        },
    )
    assert approval.status_code == 201, approval.text
    assert approval.json()["version_number"] == 1

    current_schema = client.get(f"/api/v1/workspaces/{workspace_id}/connections/{connection_id}/lock-schema")
    assert current_schema.status_code == 200
    assert current_schema.json()["configuration"]["matches_current_snapshot"] is True
    assert current_schema.json()["configuration"]["configuration"]["paths"][0]["steps"][0]["direction"] == "reverse"

    changed_structure = {**structure, "objects": structure["objects"][:1]}
    monkeypatch.setattr(
        "tigr.connections.test_connection",
        lambda _connection: ConnectionTestResult(True, "Connection test passed", changed_structure),
    )
    retested = client.post(f"/api/v1/workspaces/{workspace_id}/connections/{connection_id}/test")
    assert retested.status_code == 200
    assert retested.json()["structure_preview"]["structure_snapshot_version"] == 2
    current_schema = client.get(f"/api/v1/workspaces/{workspace_id}/connections/{connection_id}/lock-schema")
    assert current_schema.json()["configuration"]["matches_current_snapshot"] is False
    assert current_schema.json()["configuration"]["version_number"] == 1
