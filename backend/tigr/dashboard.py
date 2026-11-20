from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from tigr.access import ensure_user
from tigr.audit import AuditEvent
from tigr.connection_models import WorkspaceConnection
from tigr.datasets import DataSet, DataSetVersion
from tigr.governance import DataSetLock, DataSetReservation, DataUseRequest
from tigr.iam import Workspace, WorkspaceMembership
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.jobs import Job
from tigr.platform_access import is_tigr_admin

router = APIRouter(prefix="/api/v1", tags=["dashboard and activities"])


def visible_workspace_roles(db: Session, principal: Principal) -> dict[UUID, HumanRole]:
    now = datetime.now(UTC)
    if principal.development_role is not None:
        return {workspace.id: principal.development_role for workspace in db.scalars(select(Workspace).where(Workspace.status == "active"))}
    rows = db.execute(
        select(WorkspaceMembership.workspace_id, WorkspaceMembership.role)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.subject == principal.subject,
            WorkspaceMembership.revoked_at.is_(None),
            WorkspaceMembership.valid_from <= now,
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now),
            Workspace.status == "active",
        )
    ).all()
    return {workspace_id: HumanRole(role) for workspace_id, role in rows}


def activity_query(db: Session, principal: Principal, roles: dict[UUID, HumanRole], workspace_id: UUID | None = None):
    query = select(AuditEvent)
    if is_tigr_admin(db, principal):
        if workspace_id:
            query = query.where(AuditEvent.workspace_id == str(workspace_id))
        return query
    own_scope = or_(AuditEvent.workspace_id.is_(None), AuditEvent.workspace_id.in_([str(wid) for wid in roles]))
    visible_conditions = [((AuditEvent.actor_id == principal.subject) & own_scope)]
    admin_workspace_ids = [str(wid) for wid, role in roles.items() if role == HumanRole.ADMIN]
    if admin_workspace_ids:
        visible_conditions.append(AuditEvent.workspace_id.in_(admin_workspace_ids))
    tde_workspace_ids = [str(wid) for wid, role in roles.items() if role == HumanRole.TEST_DATA_ENGINEER]
    if tde_workspace_ids:
        visible_conditions.append(
            (AuditEvent.actor_role == HumanRole.QA_ENGINEER.value)
            & AuditEvent.workspace_id.in_(tde_workspace_ids)
        )
    query = query.where(or_(*visible_conditions))
    if workspace_id:
        query = query.where(AuditEvent.workspace_id == str(workspace_id))
    return query


def activity_item(event: AuditEvent, object_reference: str | None = None) -> dict:
    return {
        "id": str(event.id),
        "actor_id": event.actor_id,
        "actor_role": event.actor_role,
        "workspace_id": event.workspace_id,
        "action": event.action,
        "object_type": event.object_type,
        "object_id": event.object_id,
        "object_reference": object_reference,
        "outcome": event.outcome,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "request_id": event.request_id,
    }


def activity_rows(db: Session, principal: Principal, roles: dict[UUID, HumanRole], *, workspace_id: UUID | None, page: int, page_size: int, search: str | None = None) -> dict:
    query = activity_query(db, principal, roles, workspace_id)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(or_(AuditEvent.actor_id.ilike(term), AuditEvent.actor_role.ilike(term), AuditEvent.action.ilike(term), AuditEvent.object_type.ilike(term), AuditEvent.object_id.ilike(term), AuditEvent.outcome.ilike(term)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id).offset((page - 1) * page_size).limit(page_size)).all()
    def parse_id(value: str | None) -> UUID | None:
        try:
            return UUID(value) if value else None
        except ValueError:
            return None

    version_ids = {parsed for event in rows if event.object_type == "dataset_version" if (parsed := parse_id(event.object_id))}
    lock_ids = {parsed for event in rows if event.object_type == "lock" if (parsed := parse_id(event.object_id))}
    reservation_ids = {parsed for event in rows if event.object_type == "reservation" if (parsed := parse_id(event.object_id))}
    request_ids = {parsed for event in rows if event.object_type == "data_use_request" if (parsed := parse_id(event.object_id))}
    dataset_ids = {
        parsed
        for event in rows
        for raw_id in [event.safe_metadata.get("dataset_id") if isinstance(event.safe_metadata, dict) else None]
        if (parsed := parse_id(raw_id))
    }
    dataset_ids.update(parsed for event in rows if event.object_type == "dataset" if (parsed := parse_id(event.object_id)))
    references: dict[str, str] = {}
    if dataset_ids:
        references.update({str(dataset_id): reference for dataset_id, reference in db.execute(
            select(DataSet.id, DataSet.reference_code).where(DataSet.id.in_(dataset_ids))
        ).all()})
    if version_ids:
        references.update({str(version_id): reference for version_id, reference in db.execute(
            select(DataSetVersion.id, DataSet.reference_code)
            .join(DataSet, DataSet.id == DataSetVersion.dataset_id)
            .where(DataSetVersion.id.in_(version_ids))
        ).all()})
    if lock_ids:
        references.update({str(lock_id): reference for lock_id, reference in db.execute(
            select(DataSetLock.id, DataSet.reference_code)
            .join(DataSet, DataSet.id == DataSetLock.dataset_id)
            .where(DataSetLock.id.in_(lock_ids))
        ).all()})
    if reservation_ids:
        references.update({str(reservation_id): reference for reservation_id, reference in db.execute(
            select(DataSetReservation.id, DataSet.reference_code)
            .join(DataSet, DataSet.id == DataSetReservation.dataset_id)
            .where(DataSetReservation.id.in_(reservation_ids))
        ).all()})
    if request_ids:
        references.update({str(request_id): reference for request_id, reference in db.execute(
            select(DataUseRequest.id, DataSet.reference_code)
            .join(DataSet, DataSet.id == DataUseRequest.dataset_id)
            .where(DataUseRequest.id.in_(request_ids))
        ).all()})
    return {"items": [activity_item(event, references.get(event.object_id or "")) for event in rows], "page": page, "page_size": page_size, "total": total, "has_more": page * page_size < total}


@router.get("/activities")
def list_activities(
    workspace_id: UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    search: str | None = Query(default=None, max_length=180),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    ensure_user(db, principal)
    roles = visible_workspace_roles(db, principal)
    if workspace_id and not is_tigr_admin(db, principal) and workspace_id not in roles:
        return {"items": [], "page": page, "page_size": page_size, "total": 0, "has_more": False}
    return activity_rows(db, principal, roles, workspace_id=workspace_id, page=page, page_size=page_size, search=search)


@router.get("/dashboard")
def dashboard(
    workspace_id: UUID | None = None,
    activity_limit: int = Query(default=6, ge=1, le=20),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    ensure_user(db, principal)
    roles = visible_workspace_roles(db, principal)
    if workspace_id and not is_tigr_admin(db, principal) and workspace_id not in roles:
        # Do not reveal whether an inaccessible workspace exists.
        roles = {}
        workspace_id = None
    else:
        if not workspace_id and roles and not is_tigr_admin(db, principal):
            workspace_id = next(iter(roles))
        if workspace_id and not is_tigr_admin(db, principal):
            roles = {workspace_id: roles[workspace_id]}

    job_query = select(Job)
    if workspace_id and not is_tigr_admin(db, principal):
        selected_role = roles[workspace_id]
        job_query = job_query.where(Job.workspace_id == str(workspace_id))
        if selected_role == HumanRole.TEST_DATA_ENGINEER:
            job_query = job_query.where(or_(Job.actor_id == principal.subject, Job.actor_role == HumanRole.QA_ENGINEER.value))
        elif selected_role == HumanRole.QA_ENGINEER:
            job_query = job_query.where(Job.actor_id == principal.subject)
    elif not is_tigr_admin(db, principal):
        scoped_job_visibility = []
        for scoped_workspace_id, scoped_role in roles.items():
            actors = Job.workspace_id == str(scoped_workspace_id)
            if scoped_role == HumanRole.TEST_DATA_ENGINEER:
                actors = and_(actors, or_(Job.actor_id == principal.subject, Job.actor_role == HumanRole.QA_ENGINEER.value))
            elif scoped_role == HumanRole.QA_ENGINEER:
                actors = and_(actors, Job.actor_id == principal.subject)
            scoped_job_visibility.append(actors)
        scoped_job_visibility.append(and_(Job.workspace_id.is_(None), Job.actor_id == principal.subject))
        job_query = job_query.where(or_(*scoped_job_visibility))
    job_rows = job_query.with_only_columns(Job.status).subquery()
    job_counts = dict(db.execute(select(job_rows.c.status, func.count()).group_by(job_rows.c.status)).all())
    connection_query = select(WorkspaceConnection).where(WorkspaceConnection.status != "disabled")
    if workspace_id:
        connection_query = connection_query.where(WorkspaceConnection.workspace_id == workspace_id)
    elif not is_tigr_admin(db, principal):
        connection_query = connection_query.where(WorkspaceConnection.workspace_id.in_(list(roles))) if roles else connection_query.where(False)
    connection_rows = connection_query.with_only_columns(WorkspaceConnection.status).subquery()
    connection_counts = dict(db.execute(select(connection_rows.c.status, func.count()).group_by(connection_rows.c.status)).all())
    dataset_query = select(DataSet, DataSetVersion).join(DataSetVersion, DataSetVersion.dataset_id == DataSet.id)
    if workspace_id:
        dataset_query = dataset_query.where(DataSet.workspace_id == workspace_id)
    elif not is_tigr_admin(db, principal):
        dataset_query = dataset_query.where(DataSet.workspace_id.in_(list(roles))) if roles else dataset_query.where(False)
    if not is_tigr_admin(db, principal):
        admin_tde_ids = [wid for wid, role in roles.items() if role in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER)]
        qa_ids = [wid for wid, role in roles.items() if role == HumanRole.QA_ENGINEER]
        visible_versions = []
        if admin_tde_ids:
            visible_versions.append(and_(DataSet.workspace_id.in_(admin_tde_ids), DataSetVersion.version_number == DataSet.latest_version_number))
        if qa_ids:
            latest_published = select(func.max(DataSetVersion.version_number)).where(
                DataSetVersion.dataset_id == DataSet.id, DataSetVersion.lifecycle_state == "published",
                DataSetVersion.publication_state == "published",
            ).correlate(DataSet).scalar_subquery()
            visible_versions.append(and_(DataSet.workspace_id.in_(qa_ids), DataSetVersion.version_number == latest_published))
        dataset_query = dataset_query.where(or_(*visible_versions)) if visible_versions else dataset_query.where(False)
    else:
        dataset_query = dataset_query.where(DataSetVersion.version_number == DataSet.latest_version_number)
    dataset_rows = dataset_query.with_only_columns(DataSetVersion.lifecycle_state).subquery()
    dataset_counts = dict(db.execute(select(dataset_rows.c.lifecycle_state, func.count()).group_by(dataset_rows.c.lifecycle_state)).all())
    reservation_query = select(DataSetReservation)
    lock_query = select(DataSetLock)
    if workspace_id:
        reservation_query=reservation_query.where(DataSetReservation.workspace_id==workspace_id)
        lock_query=lock_query.where(DataSetLock.workspace_id==workspace_id)
    elif not is_tigr_admin(db,principal):
        visible_ids=list(roles)
        reservation_query=reservation_query.where(DataSetReservation.workspace_id.in_(visible_ids)) if visible_ids else reservation_query.where(False)
        lock_query=lock_query.where(DataSetLock.workspace_id.in_(visible_ids)) if visible_ids else lock_query.where(False)
    if not is_tigr_admin(db, principal) and workspace_id and roles.get(workspace_id) == HumanRole.QA_ENGINEER:
        reservation_query=reservation_query.where(DataSetReservation.actor_id==principal.subject)
        lock_query=lock_query.where(DataSetLock.actor_id==principal.subject)
    elif not is_tigr_admin(db, principal) and not workspace_id:
        reservation_visibility=[]
        lock_visibility=[]
        for visible_workspace, visible_role in roles.items():
            reservation_scope=DataSetReservation.workspace_id==visible_workspace
            lock_scope=DataSetLock.workspace_id==visible_workspace
            if visible_role==HumanRole.QA_ENGINEER:
                reservation_scope=and_(reservation_scope,DataSetReservation.actor_id==principal.subject)
                lock_scope=and_(lock_scope,DataSetLock.actor_id==principal.subject)
            reservation_visibility.append(reservation_scope)
            lock_visibility.append(lock_scope)
        reservation_query=reservation_query.where(or_(*reservation_visibility)) if reservation_visibility else reservation_query.where(False)
        lock_query=lock_query.where(or_(*lock_visibility)) if lock_visibility else lock_query.where(False)
    reservation_rows=reservation_query.with_only_columns(DataSetReservation.status).subquery()
    reservation_counts=dict(db.execute(select(reservation_rows.c.status,func.count()).group_by(reservation_rows.c.status)).all())
    active_reservation_count=db.scalar(select(func.count()).select_from(reservation_query.where(
        DataSetReservation.status=="active",DataSetReservation.starts_at<=datetime.now(UTC),
        DataSetReservation.ends_at>datetime.now(UTC)).subquery())) or 0
    lock_rows=lock_query.with_only_columns(DataSetLock.status).subquery()
    lock_counts=dict(db.execute(select(lock_rows.c.status,func.count()).group_by(lock_rows.c.status)).all())
    active_lock_count=db.scalar(select(func.count()).select_from(lock_query.where(
        DataSetLock.status=="active",DataSetLock.expires_at>datetime.now(UTC)).subquery())) or 0
    request_query=select(DataUseRequest)
    if workspace_id:
        request_query=request_query.where(DataUseRequest.workspace_id==workspace_id)
    elif not is_tigr_admin(db,principal):
        request_query=request_query.where(DataUseRequest.workspace_id.in_(list(roles))) if roles else request_query.where(False)
    if not is_tigr_admin(db,principal):
        if workspace_id and roles.get(workspace_id)==HumanRole.QA_ENGINEER:
            request_query=request_query.where(DataUseRequest.requester_id==principal.subject)
        elif not workspace_id:
            request_visibility=[]
            for visible_workspace,visible_role in roles.items():
                request_scope=DataUseRequest.workspace_id==visible_workspace
                if visible_role==HumanRole.QA_ENGINEER:
                    request_scope=and_(request_scope,DataUseRequest.requester_id==principal.subject)
                request_visibility.append(request_scope)
            request_query=request_query.where(or_(*request_visibility)) if request_visibility else request_query.where(False)
    request_rows=request_query.with_only_columns(DataUseRequest.status).subquery()
    request_counts=dict(db.execute(select(request_rows.c.status,func.count()).group_by(request_rows.c.status)).all())
    recent = activity_rows(db, principal, roles, workspace_id=workspace_id, page=1, page_size=activity_limit)
    visible_activities = activity_query(db, principal, roles, workspace_id)
    activity_count = db.scalar(select(func.count()).select_from(visible_activities.subquery())) or 0
    return {
        "workspace_id": str(workspace_id) if workspace_id else None,
        "metrics": {
            "activities": {"available": True, "count": activity_count},
            "datasets": {"available": True, "count": sum(dataset_counts.values()), "by_status": dataset_counts},
            "jobs": {"available": True, "count": sum(job_counts.values()), "by_status": job_counts},
            "reservations": {"available": True, "count": sum(reservation_counts.values()), "active_count": active_reservation_count, "by_status": reservation_counts},
            "locks": {"available": True, "count": sum(lock_counts.values()), "active_count": active_lock_count, "by_status": lock_counts},
            "reservation_requests": {"available": True, "count": sum(request_counts.values()), "by_status": request_counts},
            "connections": {"available": True, "count": sum(connection_counts.values()), "by_status": connection_counts},
        },
        "recent_activities": recent["items"],
    }
