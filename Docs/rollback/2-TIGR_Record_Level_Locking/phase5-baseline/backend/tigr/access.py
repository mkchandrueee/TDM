from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tigr.audit import AuditEvent
from tigr.iam import AccessGrant, UserIdentity, Workspace, WorkspaceMembership
from tigr.identity import HumanRole, Principal, is_platform_admin


def ensure_user(db: Session, principal: Principal) -> UserIdentity:
    if principal.actor_type == "service":
        return UserIdentity(subject=principal.subject, display_name=principal.display_name)
    now = datetime.now(UTC)
    table = UserIdentity.__table__
    statement = pg_insert(UserIdentity).values(
        subject=principal.subject,
        email=principal.email,
        display_name=principal.display_name,
        last_seen_at=now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.subject],
        set_={
            "email": func.coalesce(statement.excluded.email, table.c.email),
            "display_name": func.coalesce(statement.excluded.display_name, table.c.display_name),
            "last_seen_at": now,
        },
    )
    db.execute(statement)
    return db.get(UserIdentity, principal.subject)


def record_audit(
    db: Session,
    principal: Principal,
    request_id: str,
    action: str,
    object_type: str,
    object_id: str | None,
    workspace_id: UUID | None,
    role: HumanRole | None,
    metadata: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            id=uuid4(),
            actor_type=principal.actor_type,
            actor_id=principal.subject,
            actor_role=role.value if role else None,
            workspace_id=str(workspace_id) if workspace_id else None,
            action=action,
            object_type=object_type,
            object_id=object_id,
            outcome="succeeded",
            request_id=request_id,
            safe_metadata=metadata or {},
        )
    )


def workspace_role(
    db: Session, principal: Principal, workspace_id: UUID, *, allow_inactive: bool = False
) -> HumanRole:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if workspace.status != "active" and not allow_inactive:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if is_platform_admin(db, principal):
        return HumanRole.ADMIN
    if principal.actor_type == "service":
        raise HTTPException(status_code=403, detail="A human workspace role is required")
    if principal.development_role is not None:
        return principal.development_role
    now = datetime.now(UTC)
    membership = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.subject == principal.subject,
            WorkspaceMembership.revoked_at.is_(None),
            WorkspaceMembership.valid_from <= now,
            (WorkspaceMembership.valid_until.is_(None) | (WorkspaceMembership.valid_until > now)),
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return HumanRole(membership.role)


def require_workspace_admin(
    db: Session, principal: Principal, workspace_id: UUID, *, allow_inactive: bool = False
) -> HumanRole:
    role = workspace_role(db, principal, workspace_id, allow_inactive=allow_inactive)
    if role != HumanRole.ADMIN:
        raise HTTPException(status_code=403, detail="Administrator access is required")
    return role


def membership_role(db: Session, subject: str, workspace_id: UUID) -> HumanRole | None:
    now = datetime.now(UTC)
    membership = db.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.subject == subject,
            WorkspaceMembership.revoked_at.is_(None),
            WorkspaceMembership.valid_from <= now,
            (WorkspaceMembership.valid_until.is_(None) | (WorkspaceMembership.valid_until > now)),
        )
    )
    return HumanRole(membership.role) if membership else None


def require_object_grant(
    db: Session,
    principal: Principal,
    workspace_id: UUID,
    scope_type: str,
    scope_id: str,
    permission: str,
) -> None:
    role = workspace_role(db, principal, workspace_id)
    if role in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER):
        return
    now = datetime.now(UTC)
    grant = db.scalar(
        select(AccessGrant.id).where(
            AccessGrant.subject == principal.subject,
            AccessGrant.workspace_id == workspace_id,
            AccessGrant.revoked_at.is_(None),
            AccessGrant.valid_from <= now,
            (AccessGrant.valid_until.is_(None) | (AccessGrant.valid_until > now)),
            or_(
                (AccessGrant.scope_type == scope_type) & (AccessGrant.scope_id == scope_id),
                AccessGrant.scope_type == "workspace",
            ),
            AccessGrant.permissions.contains([permission]),
        )
    )
    if grant is None:
        raise HTTPException(status_code=404, detail="Resource not found")


def require_service_scope(principal: Principal, workspace_id: UUID, permission: str) -> None:
    if principal.actor_type != "service" or principal.service_workspace_id != str(workspace_id):
        raise HTTPException(status_code=403, detail="Service identity scope does not allow this action")
    if permission not in principal.service_scopes:
        raise HTTPException(status_code=403, detail="Service identity scope does not allow this action")
