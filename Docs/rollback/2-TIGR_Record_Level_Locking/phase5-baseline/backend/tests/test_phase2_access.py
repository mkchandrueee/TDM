from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from tigr.access import require_object_grant, require_service_scope, workspace_role
from tigr.audit import AuditEvent
from tigr.iam import AccessGrant, PlatformAdmin, UserIdentity, Workspace, WorkspaceMembership
from tigr.identity import HumanRole, Principal
from tigr.jobs import Job


def add_identity(session, subject: str) -> None:
    session.add(UserIdentity(subject=subject, display_name=subject))


def add_workspace(session, workspace_id, slug: str) -> Workspace:
    workspace = Workspace(id=workspace_id, slug=slug, name=slug.replace("-", " ").title(), created_by="test-setup")
    session.add(workspace)
    return workspace


def test_workspace_admin_flow_is_role_protected(app_context):
    client, session, current = app_context
    current["principal"] = Principal(subject="workspace-qa", development_role=HumanRole.QA_ENGINEER)
    denied = client.post("/api/v1/workspaces", json={"slug": "qa-space", "name": "QA Space"})
    assert denied.status_code == 403
    assert denied.json()["request_id"]

    current["principal"] = Principal(subject="workspace-admin", development_role=HumanRole.ADMIN)
    created = client.post("/api/v1/workspaces", json={"slug": "qa-space", "name": "QA Space", "purpose": "Role-boundary verification"})
    assert created.status_code == 201
    body = created.json()
    assert body["slug"] == "qa-space"
    workspace_id = body["id"]
    members = client.get(f"/api/v1/workspaces/{workspace_id}/memberships")
    assert members.status_code == 200
    assert [(row["subject"], row["role"]) for row in members.json()] == [("workspace-admin", "Admin")]
    assert session.scalar(select(Workspace).where(Workspace.slug == "qa-space")) is not None


def test_activity_visibility_is_filtered_before_count_and_pagination(app_context):
    client, session, current = app_context
    workspace_id = uuid4()
    other_workspace_id = uuid4()
    add_workspace(session, workspace_id, f"role-scope-{workspace_id.hex[:8]}")
    add_workspace(session, other_workspace_id, f"other-scope-{other_workspace_id.hex[:8]}")
    subjects = ["actor-tde", "actor-qa", "actor-outsider", "actor-admin"]
    for subject in subjects:
        add_identity(session, subject)
    session.flush()
    session.add_all([
        PlatformAdmin(subject="actor-admin", granted_by="test-setup"),
        WorkspaceMembership(workspace_id=workspace_id, subject="actor-tde", role=HumanRole.TEST_DATA_ENGINEER.value, granted_by="test-setup"),
        WorkspaceMembership(workspace_id=workspace_id, subject="actor-qa", role=HumanRole.QA_ENGINEER.value, granted_by="test-setup"),
    ])
    expected = [
        ("actor-tde", HumanRole.TEST_DATA_ENGINEER.value, workspace_id),
        ("actor-qa", HumanRole.QA_ENGINEER.value, workspace_id),
        ("actor-outsider", HumanRole.TEST_DATA_ENGINEER.value, workspace_id),
        ("actor-qa", HumanRole.QA_ENGINEER.value, other_workspace_id),
    ]
    for actor, actor_role, scope in expected:
        session.add(AuditEvent(
            id=uuid4(), actor_type="human", actor_id=actor, actor_role=actor_role,
            workspace_id=str(scope), action=f"action.{actor}", object_type="test", object_id=uuid4().hex,
            outcome="succeeded", request_id=uuid4().hex, safe_metadata={},
        ))
    for actor, actor_role in [
        ("actor-tde", HumanRole.TEST_DATA_ENGINEER.value),
        ("actor-qa", HumanRole.QA_ENGINEER.value),
        ("actor-outsider", HumanRole.TEST_DATA_ENGINEER.value),
    ]:
        session.add(Job(
            id=uuid4(), job_type="foundation-check", status="queued", workspace_id=str(workspace_id),
            scope_key=str(workspace_id), idempotency_key=uuid4().hex, actor_type="human", actor_id=actor,
            actor_role=actor_role, request_id=uuid4().hex,
        ))
    session.flush()

    current["principal"] = Principal(subject="actor-qa")
    qa_page = client.get("/api/v1/activities", params={"page_size": 1, "search": "action.actor"})
    assert qa_page.status_code == 200
    assert qa_page.json()["total"] == 1
    assert qa_page.json()["items"][0]["actor_id"] == "actor-qa"
    qa_dashboard = client.get("/api/v1/dashboard", params={"workspace_id": str(workspace_id)})
    assert qa_dashboard.json()["metrics"]["jobs"]["count"] == 1

    current["principal"] = Principal(subject="actor-tde")
    tde_page = client.get("/api/v1/activities", params={"page_size": 1, "search": "action.actor"})
    assert tde_page.json()["total"] == 2
    assert tde_page.json()["has_more"] is True
    tde_dashboard = client.get("/api/v1/dashboard", params={"workspace_id": str(workspace_id)})
    assert tde_dashboard.json()["metrics"]["jobs"]["count"] == 2

    current["principal"] = Principal(subject="actor-admin")
    admin_page = client.get("/api/v1/activities", params={"page_size": 1, "search": "action.actor"})
    assert admin_page.json()["total"] == 4
    admin_dashboard = client.get("/api/v1/dashboard", params={"workspace_id": str(workspace_id)})
    assert admin_dashboard.json()["metrics"]["jobs"]["count"] == 3


def test_object_grants_fail_closed_for_expired_or_revoked_access(app_context):
    _, session, _ = app_context
    workspace_id = uuid4()
    add_workspace(session, workspace_id, f"grant-scope-{workspace_id.hex[:8]}")
    add_identity(session, "grant-qa")
    session.flush()
    now = datetime.now(UTC)
    session.add(WorkspaceMembership(workspace_id=workspace_id, subject="grant-qa", role=HumanRole.QA_ENGINEER.value, granted_by="test-setup"))
    valid = AccessGrant(id=uuid4(), workspace_id=workspace_id, subject="grant-qa", scope_type="dataset", scope_id="dataset-current", permissions=["data:read"], valid_from=now-timedelta(minutes=1), granted_by="test-setup")
    expired = AccessGrant(id=uuid4(), workspace_id=workspace_id, subject="grant-qa", scope_type="dataset", scope_id="dataset-expired", permissions=["data:read"], valid_from=now-timedelta(days=2), valid_until=now-timedelta(days=1), granted_by="test-setup")
    revoked = AccessGrant(id=uuid4(), workspace_id=workspace_id, subject="grant-qa", scope_type="dataset", scope_id="dataset-revoked", permissions=["data:read"], valid_from=now-timedelta(minutes=1), granted_by="test-setup", revoked_at=now, revoked_by="test-admin")
    session.add_all([valid, expired, revoked])
    session.flush()
    principal = Principal(subject="grant-qa", development_role=HumanRole.QA_ENGINEER)

    require_object_grant(session, principal, workspace_id, "dataset", "dataset-current", "data:read")
    for object_id in ("dataset-expired", "dataset-revoked", "dataset-other"):
        with pytest.raises(HTTPException) as error:
            require_object_grant(session, principal, workspace_id, "dataset", object_id, "data:read")
        assert error.value.status_code == 404


def test_service_identity_scope_is_separate_from_human_roles(app_context):
    _, session, _ = app_context
    workspace_id = uuid4()
    add_workspace(session, workspace_id, f"service-scope-{workspace_id.hex[:8]}")
    service = Principal(subject="pipeline-service", actor_type="service", service_workspace_id=str(workspace_id), service_scopes=("jobs:submit",))
    require_service_scope(service, workspace_id, "jobs:submit")
    with pytest.raises(HTTPException) as error:
        require_service_scope(service, workspace_id, "datasets:write")
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        workspace_role(session, service, workspace_id)
    assert error.value.status_code == 403
