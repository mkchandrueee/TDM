from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tigr.access import (
    ensure_user,
    membership_role,
    record_audit,
    require_workspace_admin,
    workspace_role,
)
from tigr.access_schemas import (
    GrantCreate,
    GrantOut,
    MembershipCreate,
    MembershipOut,
    MembershipUpdate,
    ServiceIdentityCreate,
    ServiceIdentityOut,
    UserDirectoryOut,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)
from tigr.audit import AuditEvent
from tigr.iam import AccessGrant, ServiceIdentity, UserIdentity, Workspace, WorkspaceMembership
from tigr.identity import HumanRole, Principal, get_db, get_principal, is_platform_admin
from tigr.connection_models import WorkspaceConnection
from tigr.datasets import DataSet, DataSetVersion
from tigr.jobs import Job

router = APIRouter(prefix="/api/v1", tags=["platform access"])


def is_tigr_admin(db: Session, principal: Principal) -> bool:
    return principal.development_role == HumanRole.ADMIN or is_platform_admin(db, principal)


def require_admin_in_workspace(db: Session, principal: Principal, workspace_id: UUID, *, allow_inactive: bool = False) -> HumanRole:
    if is_tigr_admin(db, principal):
        workspace = db.get(Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        if workspace.status != "active" and not allow_inactive:
            raise HTTPException(status_code=404, detail="Workspace not found")
        return HumanRole.ADMIN
    return require_workspace_admin(db, principal, workspace_id, allow_inactive=allow_inactive)


def write_event(db: Session, request: Request, principal: Principal, action: str, kind: str, object_id: str | None, workspace_id: UUID | None = None, role: HumanRole | None = HumanRole.ADMIN, metadata: dict | None = None) -> None:
    record_audit(db, principal, request.state.request_id, action, kind, object_id, workspace_id, role, metadata)


def record_session_once(db: Session, request: Request, principal: Principal, session_key: str, auth_mode: str, role: HumanRole | None) -> None:
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(session_key, 0))))
    already_seen = db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.actor_id == principal.subject,
            AuditEvent.action == "identity.session_authenticated",
            AuditEvent.safe_metadata["session_key"].astext == session_key,
        ).limit(1)
    )
    if not already_seen:
        write_event(
            db,
            request,
            principal,
            "identity.session_authenticated",
            "identity",
            principal.subject,
            role=role,
            metadata={"session_key": session_key, "auth_mode": auth_mode},
        )


def get_workspace_or_404(db: Session, workspace_id: UUID) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


def prevent_last_admin_loss(
    db: Session,
    workspace_id: UUID,
    member: WorkspaceMembership,
    role: HumanRole,
    valid_from: datetime,
    valid_until: datetime | None,
) -> None:
    if member.role != HumanRole.ADMIN.value:
        return
    now = datetime.now(UTC)
    active_admin_count = db.scalar(
        select(func.count()).select_from(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.role == HumanRole.ADMIN.value,
            WorkspaceMembership.revoked_at.is_(None),
            WorkspaceMembership.valid_from <= now,
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now),
        )
    ) or 0
    stays_active = role == HumanRole.ADMIN and valid_from <= now and (valid_until is None or valid_until > now)
    if active_admin_count <= 1 and not stays_active:
        raise HTTPException(status_code=409, detail="A workspace must retain an active Admin")


@router.get("/me")
def me(
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    user = ensure_user(db, principal)
    now = datetime.now(UTC)
    if principal.development_role is not None:
        development_workspaces = db.scalars(select(Workspace).where(Workspace.status == "active").order_by(Workspace.name)).all()
        workspace_contexts = [
            {"id": str(workspace.id), "slug": workspace.slug, "name": workspace.name, "role": principal.development_role.value}
            for workspace in development_workspaces
        ]
    else:
        memberships = db.execute(
            select(WorkspaceMembership, Workspace)
            .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
            .where(
                WorkspaceMembership.subject == principal.subject,
                WorkspaceMembership.revoked_at.is_(None),
                WorkspaceMembership.valid_from <= now,
                or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now),
                Workspace.status == "active",
            )
            .order_by(Workspace.name)
        ).all()
        workspace_contexts = [
            {"id": str(workspace.id), "slug": workspace.slug, "name": workspace.name, "role": membership.role}
            for membership, workspace in memberships
        ]
    if principal.development_role is not None:
        session_key = f"development:{principal.subject}:{principal.development_role.value}"
        record_session_once(db, request, principal, session_key, "development", principal.development_role)
    elif principal.token_id:
        session_key = f"oidc:{principal.subject}:{principal.token_id[:160]}"
        record_session_once(db, request, principal, session_key, "oidc", None)
    db.commit()
    return {
        "subject": user.subject,
        "email": user.email,
        "display_name": user.display_name,
        "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
        "development_role": principal.development_role.value if principal.development_role else None,
        "platform_admin": is_tigr_admin(db, principal),
        "workspaces": workspace_contexts,
    }


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[Workspace]:
    ensure_user(db, principal)
    if is_tigr_admin(db, principal):
        return list(db.scalars(select(Workspace).order_by(Workspace.name)).all())
    if principal.development_role is not None:
        return list(db.scalars(select(Workspace).where(Workspace.status == "active").order_by(Workspace.name)).all())
    now = datetime.now(UTC)
    return list(
        db.scalars(
            select(Workspace)
            .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .where(
                WorkspaceMembership.subject == principal.subject,
                WorkspaceMembership.revoked_at.is_(None),
                WorkspaceMembership.valid_from <= now,
                or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now),
                Workspace.status == "active",
            )
            .order_by(Workspace.name)
        ).all()
    )


@router.get("/users", response_model=list[UserDirectoryOut])
def list_user_directory(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[UserDirectoryOut]:
    if not is_tigr_admin(db, principal):
        raise HTTPException(status_code=403, detail="Administrator access is required")
    users = db.scalars(select(UserIdentity).order_by(UserIdentity.display_name, UserIdentity.email, UserIdentity.subject)).all()
    return [UserDirectoryOut(subject=user.subject, email=user.email, display_name=user.display_name) for user in users]


@router.post("/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(
    payload: WorkspaceCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Workspace:
    ensure_user(db, principal)
    now = datetime.now(UTC)
    is_workspace_admin = db.scalar(
        select(WorkspaceMembership.workspace_id)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.subject == principal.subject,
            WorkspaceMembership.role == HumanRole.ADMIN.value,
            WorkspaceMembership.revoked_at.is_(None),
            WorkspaceMembership.valid_from <= now,
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now),
            Workspace.status == "active",
        )
        .limit(1)
    ) is not None
    if not is_tigr_admin(db, principal) and not is_workspace_admin:
        raise HTTPException(status_code=403, detail="Administrator access is required")
    if db.scalar(select(Workspace.id).where(Workspace.slug == payload.slug)):
        raise HTTPException(status_code=409, detail="A workspace with this identifier already exists")
    workspace = Workspace(id=uuid4(), slug=payload.slug, name=payload.name, purpose=payload.purpose,
                          pii_masking_enabled=payload.pii_masking_enabled, created_by=principal.subject)
    db.add(workspace)
    db.flush()
    membership = WorkspaceMembership(
        workspace_id=workspace.id,
        subject=principal.subject,
        role=HumanRole.ADMIN.value,
        granted_by=principal.subject,
    )
    db.add(membership)
    write_event(db, request, principal, "workspace.created", "workspace", str(workspace.id), workspace.id, metadata={"slug": workspace.slug, "status": workspace.status})
    write_event(db, request, principal, "workspace.membership_granted", "membership", principal.subject, workspace.id, metadata={"role": HumanRole.ADMIN.value})
    db.commit()
    db.refresh(workspace)
    return workspace


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(
    workspace_id: UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Workspace:
    workspace_role(db, principal, workspace_id)
    return get_workspace_or_404(db, workspace_id)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    workspace_id: UUID,
    payload: WorkspaceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Workspace:
    role = require_admin_in_workspace(db, principal, workspace_id, allow_inactive=True)
    workspace = get_workspace_or_404(db, workspace_id)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return workspace
    if "slug" in changes:
        if changes["slug"] is None:
            raise HTTPException(status_code=422, detail="Workspace identifier cannot be empty")
        duplicate = db.scalar(select(Workspace.id).where(Workspace.slug == changes["slug"], Workspace.id != workspace_id))
        if duplicate:
            raise HTTPException(status_code=409, detail="A workspace with this identifier already exists")
    if "name" in changes and changes["name"] is None:
        raise HTTPException(status_code=422, detail="Workspace name cannot be empty")
    if "status" in changes and changes["status"] is None:
        raise HTTPException(status_code=422, detail="Workspace status cannot be empty")
    if "pii_masking_enabled" in changes and changes["pii_masking_enabled"] is None:
        raise HTTPException(status_code=422, detail="PII & Masking setting cannot be empty")
    if workspace.pii_masking_enabled and changes.get("pii_masking_enabled") is False:
        pending_versions = db.scalars(
            select(DataSetVersion).join(DataSet, DataSet.id == DataSetVersion.dataset_id).where(
                DataSet.workspace_id == workspace_id,
                DataSetVersion.lifecycle_state == "draft",
                DataSetVersion.validation_state == "passed",
            )
        ).all()
        waiting_for_masking = [
            version for version in pending_versions
            if (version.provenance or {}).get("origin") == "source_backed"
            and (version.provenance or {}).get("pii_masking_required", True) is not False
            and not (version.validation_evidence or {}).get("masking_run_id")
        ]
        if waiting_for_masking:
            raise HTTPException(
                status_code=409,
                detail="Complete PII & Masking or remove the validated source-backed draft versions before disabling this workspace setting.",
            )
    for key, value in changes.items():
        setattr(workspace, key, value)
    workspace.updated_at = datetime.now(UTC)
    write_event(db, request, principal, "workspace.updated", "workspace", str(workspace.id), workspace.id, role, metadata={"changed_fields": sorted(changes)})
    db.commit()
    db.refresh(workspace)
    return workspace


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(
    workspace_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> None:
    role = require_admin_in_workspace(db, principal, workspace_id, allow_inactive=True)
    workspace = get_workspace_or_404(db, workspace_id)
    dataset_count = db.scalar(select(func.count()).select_from(DataSet).where(DataSet.workspace_id == workspace_id)) or 0
    connection_count = db.scalar(select(func.count()).select_from(WorkspaceConnection).where(WorkspaceConnection.workspace_id == workspace_id)) or 0
    job_count = db.scalar(select(func.count()).select_from(Job).where(Job.workspace_id == str(workspace_id))) or 0
    if dataset_count or connection_count or job_count:
        raise HTTPException(
            status_code=409,
            detail=(f"Workspace contains {dataset_count} data set(s), {connection_count} connection(s), and "
                    f"{job_count} job(s). Deactivate it or remove its resources before deleting it."),
        )
    write_event(db, request, principal, "workspace.deleted", "workspace", str(workspace.id), workspace.id, role,
                metadata={"slug": workspace.slug, "status": workspace.status})
    db.delete(workspace)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Workspace still has linked resources and cannot be deleted") from exc


@router.get("/workspaces/{workspace_id}/memberships", response_model=list[MembershipOut])
def list_memberships(
    workspace_id: UUID,
    include_revoked: bool = Query(default=False),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[MembershipOut]:
    require_admin_in_workspace(db, principal, workspace_id, allow_inactive=True)
    query = select(WorkspaceMembership, UserIdentity).join(UserIdentity, UserIdentity.subject == WorkspaceMembership.subject).where(WorkspaceMembership.workspace_id == workspace_id)
    if not include_revoked:
        query = query.where(WorkspaceMembership.revoked_at.is_(None))
    rows = db.execute(query.order_by(UserIdentity.subject)).all()
    return [MembershipOut(subject=user.subject, email=user.email, display_name=user.display_name, role=HumanRole(member.role), valid_from=member.valid_from, valid_until=member.valid_until, revoked_at=member.revoked_at) for member, user in rows]


@router.post("/workspaces/{workspace_id}/memberships", response_model=MembershipOut, status_code=status.HTTP_201_CREATED)
def grant_membership(
    workspace_id: UUID,
    payload: MembershipCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> MembershipOut:
    role = require_admin_in_workspace(db, principal, workspace_id)
    ensure_user(db, principal)
    get_workspace_or_404(db, workspace_id)
    target = db.get(UserIdentity, payload.subject)
    if target is None:
        target = UserIdentity(subject=payload.subject, email=payload.email, display_name=payload.display_name)
        db.add(target)
    else:
        if payload.email:
            target.email = payload.email
        if payload.display_name:
            target.display_name = payload.display_name
    member = db.get(WorkspaceMembership, (workspace_id, payload.subject))
    if member is None:
        member = WorkspaceMembership(workspace_id=workspace_id, subject=payload.subject, role=payload.role.value, valid_from=payload.valid_from, valid_until=payload.valid_until, granted_by=principal.subject)
        db.add(member)
    else:
        prevent_last_admin_loss(db, workspace_id, member, payload.role, payload.valid_from, payload.valid_until)
        member.role = payload.role.value
        member.valid_from = payload.valid_from
        member.valid_until = payload.valid_until
        member.granted_by = principal.subject
        member.revoked_at = None
        member.revoked_by = None
    write_event(db, request, principal, "workspace.membership_granted", "membership", payload.subject, workspace_id, role, metadata={"role": payload.role.value, "valid_until": payload.valid_until.isoformat() if payload.valid_until else None})
    db.commit()
    return MembershipOut(subject=target.subject, email=target.email, display_name=target.display_name, role=HumanRole(member.role), valid_from=member.valid_from, valid_until=member.valid_until, revoked_at=member.revoked_at)


@router.patch("/workspaces/{workspace_id}/memberships/{subject}", response_model=MembershipOut)
def update_membership(
    workspace_id: UUID,
    subject: str,
    payload: MembershipUpdate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> MembershipOut:
    role = require_admin_in_workspace(db, principal, workspace_id)
    member = db.get(WorkspaceMembership, (workspace_id, subject))
    if member is None or member.revoked_at is not None:
        raise HTTPException(status_code=404, detail="Membership not found")
    target = db.get(UserIdentity, subject)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return MembershipOut(subject=target.subject, email=target.email, display_name=target.display_name, role=HumanRole(member.role), valid_from=member.valid_from, valid_until=member.valid_until, revoked_at=member.revoked_at)
    new_role = changes.get("role", HumanRole(member.role))
    new_start = changes.get("valid_from", member.valid_from)
    new_end = changes.get("valid_until", member.valid_until)
    display_name = changes.pop("display_name", None) if "display_name" in changes else None
    if new_end is not None and new_end <= new_start:
        raise HTTPException(status_code=422, detail="Membership expiry must be after its start time")
    prevent_last_admin_loss(db, workspace_id, member, new_role, new_start, new_end)
    if "display_name" in payload.model_fields_set:
        target.display_name = display_name
    if changes.get("role"):
        changes["role"] = changes["role"].value
    for key, value in changes.items():
        setattr(member, key, value)
    changed_fields = list(changes)
    if "display_name" in payload.model_fields_set:
        changed_fields.append("display_name")
    changed_fields.sort()
    write_event(db, request, principal, "workspace.membership_updated", "membership", subject, workspace_id, role, metadata={"changed_fields": changed_fields})
    db.commit()
    return MembershipOut(subject=target.subject, email=target.email, display_name=target.display_name, role=HumanRole(member.role), valid_from=member.valid_from, valid_until=member.valid_until, revoked_at=member.revoked_at)


@router.delete("/workspaces/{workspace_id}/memberships/{subject}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_membership(
    workspace_id: UUID,
    subject: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> None:
    role = require_admin_in_workspace(db, principal, workspace_id)
    member = db.get(WorkspaceMembership, (workspace_id, subject))
    if member is None or member.revoked_at is not None:
        raise HTTPException(status_code=404, detail="Membership not found")
    if member.role == HumanRole.ADMIN.value:
        active_admins = db.scalar(select(func.count()).select_from(WorkspaceMembership).where(WorkspaceMembership.workspace_id == workspace_id, WorkspaceMembership.role == HumanRole.ADMIN.value, WorkspaceMembership.revoked_at.is_(None), WorkspaceMembership.valid_from <= datetime.now(UTC), or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > datetime.now(UTC)))) or 0
        if active_admins <= 1:
            raise HTTPException(status_code=409, detail="A workspace must retain an active Admin")
    member.revoked_at = datetime.now(UTC)
    member.revoked_by = principal.subject
    write_event(db, request, principal, "workspace.membership_revoked", "membership", subject, workspace_id, role, metadata={"role": member.role})
    db.commit()


@router.get("/workspaces/{workspace_id}/grants", response_model=list[GrantOut])
def list_access_grants(
    workspace_id: UUID,
    include_revoked: bool = Query(default=False),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[AccessGrant]:
    require_admin_in_workspace(db, principal, workspace_id, allow_inactive=True)
    query = select(AccessGrant).where(AccessGrant.workspace_id == workspace_id)
    if not include_revoked:
        query = query.where(AccessGrant.revoked_at.is_(None))
    return list(db.scalars(query.order_by(AccessGrant.created_at.desc())).all())


@router.post("/workspaces/{workspace_id}/grants", response_model=GrantOut, status_code=status.HTTP_201_CREATED)
def create_access_grant(
    workspace_id: UUID,
    payload: GrantCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> AccessGrant:
    role = require_admin_in_workspace(db, principal, workspace_id)
    if any(permission.strip().casefold() == "data:read" for permission in payload.permissions):
        raise HTTPException(status_code=409, detail="The data:read grant is disabled. QA access to published data sets is managed by workspace role and reservation approval.")
    if payload.scope_type.value == "connection":
        try:
            connection = db.get(WorkspaceConnection, UUID(payload.scope_id))
        except ValueError:
            connection = None
        if connection is None or connection.workspace_id != workspace_id:
            raise HTTPException(404, "Connection not found")
        if not set(payload.permissions) <= {"ssd:select", "ssd:insert", "ssd:update", "ssd:delete"}:
            raise HTTPException(422, "Connection grants accept only SSD operations")
    elif any(p.startswith("ssd:") for p in payload.permissions):
        raise HTTPException(422, "SSD operations require an explicit connection grant")
    ensure_user(db, principal)
    if membership_role(db, payload.subject, workspace_id) is None:
        raise HTTPException(status_code=409, detail="Add the identity to this workspace before granting scoped access")
    target = db.get(UserIdentity, payload.subject)
    if target is None:
        target = UserIdentity(subject=payload.subject)
        db.add(target)
    grant = AccessGrant(
        id=uuid4(), workspace_id=workspace_id, subject=payload.subject, scope_type=payload.scope_type.value,
        scope_id=payload.scope_id, permissions=payload.permissions, valid_from=payload.valid_from,
        valid_until=payload.valid_until, granted_by=principal.subject, reason=payload.reason,
    )
    db.add(grant)
    write_event(db, request, principal, "access_grant.created", "access_grant", str(grant.id), workspace_id, role, metadata={"subject": payload.subject, "scope_type": payload.scope_type.value, "scope_id": payload.scope_id, "permissions": payload.permissions, "valid_until": payload.valid_until.isoformat() if payload.valid_until else None, "reason": payload.reason})
    db.commit()
    db.refresh(grant)
    return grant


@router.post("/workspaces/{workspace_id}/grants/{grant_id}/revoke", response_model=GrantOut)
def revoke_access_grant(
    workspace_id: UUID,
    grant_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> AccessGrant:
    role = require_admin_in_workspace(db, principal, workspace_id)
    grant = db.get(AccessGrant, grant_id)
    if grant is None or grant.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Access grant not found")
    if grant.revoked_at is not None:
        return grant
    grant.revoked_at = datetime.now(UTC)
    grant.revoked_by = principal.subject
    write_event(db, request, principal, "access_grant.revoked", "access_grant", str(grant.id), workspace_id, role, metadata={"subject": grant.subject, "scope_type": grant.scope_type, "scope_id": grant.scope_id, "reason": grant.reason})
    db.commit()
    db.refresh(grant)
    return grant


@router.get("/workspaces/{workspace_id}/service-identities", response_model=list[ServiceIdentityOut])
def list_service_identities(
    workspace_id: UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[ServiceIdentity]:
    require_admin_in_workspace(db, principal, workspace_id)
    return list(db.scalars(select(ServiceIdentity).where(ServiceIdentity.workspace_id == workspace_id).order_by(ServiceIdentity.subject)).all())


@router.post("/workspaces/{workspace_id}/service-identities", response_model=ServiceIdentityOut, status_code=status.HTTP_201_CREATED)
def create_service_identity(
    workspace_id: UUID,
    payload: ServiceIdentityCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ServiceIdentity:
    role = require_admin_in_workspace(db, principal, workspace_id)
    if db.get(ServiceIdentity, payload.subject):
        raise HTTPException(status_code=409, detail="Service identity already exists")
    identity = ServiceIdentity(subject=payload.subject, display_name=payload.display_name, workspace_id=workspace_id, scopes=payload.scopes, created_by=principal.subject)
    db.add(identity)
    write_event(db, request, principal, "service_identity.created", "service_identity", identity.subject, workspace_id, role, metadata={"scopes": payload.scopes})
    db.commit()
    db.refresh(identity)
    return identity


@router.post("/workspaces/{workspace_id}/service-identities/{subject}/disable", response_model=ServiceIdentityOut)
def disable_service_identity(
    workspace_id: UUID,
    subject: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ServiceIdentity:
    role = require_admin_in_workspace(db, principal, workspace_id)
    identity = db.get(ServiceIdentity, subject)
    if identity is None or identity.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Service identity not found")
    identity.enabled = False
    write_event(db, request, principal, "service_identity.disabled", "service_identity", subject, workspace_id, role)
    db.commit()
    db.refresh(identity)
    return identity
