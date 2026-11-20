"""Test data request ticket workflow, scoped to TIGR workspace access."""
from datetime import UTC, date, datetime
from uuid import UUID, uuid4
from typing import Literal
import csv
import io
import statistics

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.orm import Session

from tigr.access import ensure_user, workspace_role
from tigr.datasets import DataSet, DataSetVersion
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.iam import UserIdentity, Workspace, WorkspaceMembership
from tigr.identity import is_platform_admin
from tigr.jobs import OutboxEvent
from tigr.ticket_models import Ticket, TicketBlockPeriod, TicketComment, TicketEvent, TicketNotification

router = APIRouter(prefix="/api/v1", tags=["test data tickets"])
OPEN = ("Submitted", "In Progress", "On Hold")
TERMINAL = ("Closed", "Cancelled")


class TicketInput(BaseModel):
    workspace_id: UUID
    project_id: str | None = None
    project_name: str | None = None
    title: str | None = Field(None, max_length=160)
    description: str | None = None
    request_type: Literal["New dataset", "Change to existing dataset", "Additional records for existing dataset"] | None = None
    environment: str | None = None
    application: str | None = None
    scenarios: list[str] = Field(default_factory=list)
    schema_reference: str | None = None
    record_count: int | None = Field(None, gt=0, le=10000)
    data_source_preference: Literal["Source-backed", "Synthetic", "Either"] | None = None
    data_date_from: date | None = None
    data_date_to: date | None = None
    required_until: date | None = None
    requested_dataset_id: UUID | None = None
    relationships: str | None = None
    masking_needs: str | None = None
    needed_by: datetime | None = None
    test_window_start: datetime | None = None
    test_window_end: datetime | None = None
    acceptance_criteria: str | None = None
    priority: Literal["Low", "Normal", "Urgent"] = "Normal"
    urgent_justification: str | None = None


class Mutation(BaseModel):
    row_version: int
    reason: str | None = None


class Assignment(Mutation):
    assignee_user_id: str


class CommentInput(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
    row_version: int | None = None


class LinkDataset(Mutation):
    dataset_id: UUID
    version_id: UUID


class CloseInput(Mutation):
    fulfillment_summary: str = Field(min_length=1, max_length=10000)
    dataset_id: UUID | None = None
    shared_with_user_id: str | None = None
    no_dataset: bool = False


def _role(db: Session, principal: Principal, ticket: Ticket) -> HumanRole:
    try:
        role = workspace_role(db, principal, ticket.workspace_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if role == HumanRole.QA_ENGINEER and ticket.requester_user_id != principal.subject:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.status == "Draft" and role == HumanRole.TEST_DATA_ENGINEER:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if principal.actor_type != "human":
        raise HTTPException(status_code=403, detail="Human access is required")
    return role


def _ticket(db: Session, ticket_id: UUID, principal: Principal, *, lock: bool = False) -> tuple[Ticket, HumanRole]:
    ticket = db.scalar(select(Ticket).where(Ticket.id == ticket_id).with_for_update()) if lock else db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket, _role(db, principal, ticket)


def _event(db: Session, ticket: Ticket, principal: Principal, role: HumanRole, event_type: str,
           request: Request, *, old: str | None = None, new: str | None = None,
           reason: str | None = None, data: dict | None = None) -> None:
    db.add(TicketEvent(id=uuid4(), ticket_id=ticket.id, event_type=event_type,
        actor_user_id=principal.subject, actor_role=role.value, from_status=old, to_status=new,
        reason=reason, event_data=data or {}, workspace_id=ticket.workspace_id,
        project_id=ticket.project_id, correlation_id=ticket.correlation_id))


def _notify(db: Session, ticket: Ticket, event_type: str, request: Request, recipient: str,
            details: dict | None = None) -> None:
    details = details or {}
    db.add(OutboxEvent(id=uuid4(), aggregate_type="test_data_ticket", aggregate_id=str(ticket.id),
        workspace_id=str(ticket.workspace_id), event_type=event_type,
        payload={"recipient": recipient, "ticket_id": ticket.display_id, "title": ticket.title or "Draft",
                 "status": ticket.status, "summary": event_type.replace("ticket.", "").replace("_", " "), **details},
        request_id=request.state.request_id))
    now = datetime.now(UTC)
    if recipient == "workspace:TDE":
        recipients = list(db.scalars(select(WorkspaceMembership.subject).where(
            WorkspaceMembership.workspace_id == ticket.workspace_id, WorkspaceMembership.role == HumanRole.TEST_DATA_ENGINEER.value,
            WorkspaceMembership.revoked_at.is_(None), WorkspaceMembership.valid_from <= now,
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now))))
    else:
        recipients = [recipient]
    for user_id in set(recipients):
        db.add(TicketNotification(id=uuid4(), ticket_id=ticket.id, user_id=user_id, event_type=event_type,
            payload={"ticket_id": ticket.display_id, "ticket_uuid": str(ticket.id), "title": ticket.title or "Draft", "status": ticket.status, **details}, created_at=now))
    db.add(TicketEvent(id=uuid4(), ticket_id=ticket.id, event_type="notification_queued", actor_user_id=None,
        actor_role="System", occurred_at=now, to_status=ticket.status,
        event_data={"notification_type": event_type, "recipient_count": len(set(recipients))},
        workspace_id=ticket.workspace_id, project_id=ticket.project_id, correlation_id=ticket.correlation_id))


def _claim_ticket_for_actor(ticket: Ticket, db: Session, request: Request,
                            principal: Principal, role: HumanRole, reason: str) -> None:
    previous = ticket.assignee_user_id
    if previous == principal.subject:
        return
    ticket.assignee_user_id = principal.subject
    previous_identity = db.get(UserIdentity, previous) if previous else None
    current_identity = db.get(UserIdentity, principal.subject)
    details = {"assigned_from": previous, "assigned_from_name": (previous_identity.display_name or previous_identity.email or previous) if previous_identity else previous,
        "assigned_to": principal.subject, "assigned_to_name": (current_identity.display_name or current_identity.email or principal.subject) if current_identity else (principal.display_name or principal.subject),
        "assignment_reason": reason}
    _event(db, ticket, principal, role, "assignment_changed", request, reason=reason,
        data={"before": previous, "after": principal.subject,
            "before_name": details["assigned_from_name"], "after_name": details["assigned_to_name"], "automatic": True})
    recipients = {ticket.requester_user_id, previous} - {principal.subject, None}
    for recipient in recipients:
        _notify(db, ticket, "ticket.assigned", request, recipient, details)


def _set_ticket_assignee(ticket: Ticket, db: Session, request: Request,
                         principal: Principal, role: HumanRole, assignee_user_id: str, reason: str) -> None:
    previous = ticket.assignee_user_id
    if previous == assignee_user_id:
        return
    ticket.assignee_user_id = assignee_user_id
    previous_identity = db.get(UserIdentity, previous) if previous else None
    current_identity = db.get(UserIdentity, assignee_user_id)
    details = {"assigned_from": previous,
        "assigned_from_name": (previous_identity.display_name or previous_identity.email or previous) if previous_identity else previous,
        "assigned_to": assignee_user_id,
        "assigned_to_name": (current_identity.display_name or current_identity.email or assignee_user_id) if current_identity else assignee_user_id,
        "assignment_reason": reason}
    _event(db, ticket, principal, role, "assignment_changed", request, reason=reason,
        data={"before": previous, "after": assignee_user_id,
            "before_name": details["assigned_from_name"], "after_name": details["assigned_to_name"], "automatic": True})
    for recipient in {previous, assignee_user_id} - {principal.subject, None}:
        _notify(db, ticket, "ticket.assigned", request, recipient, details)


def _duration(start: datetime | None, end: datetime) -> tuple[int | None, int | None]:
    if not start: return None, None
    seconds = max(0, int((end - start).total_seconds()))
    return seconds, seconds // 86400


def _out(db: Session, ticket: Ticket, *, detail: bool = False) -> dict:
    now = datetime.now(UTC)
    end = ticket.closed_at or ticket.cancelled_at or now
    seconds, days = _duration(ticket.submitted_at, end)
    blocked_seconds = 0
    for period in db.scalars(select(TicketBlockPeriod).where(TicketBlockPeriod.ticket_id == ticket.id)):
        blocked_seconds += max(0, int(((period.ended_at or now) - period.started_at).total_seconds()))
    assignee = db.get(UserIdentity, ticket.assignee_user_id) if ticket.assignee_user_id else None
    requester = db.get(UserIdentity, ticket.requester_user_id)
    data = {"id": str(ticket.id), "display_id": ticket.display_id, "title": ticket.title, "description": ticket.description,
        "request_type": ticket.request_type, "environment": ticket.environment, "application": ticket.application,
        "scenarios": ticket.scenarios, "schema_reference": ticket.schema_reference, "record_count": ticket.record_count,
        "data_source_preference": ticket.data_source_preference, "data_date_from": ticket.data_date_from,
        "data_date_to": ticket.data_date_to, "required_until": ticket.required_until,
        "requested_dataset_id": str(ticket.requested_dataset_id) if ticket.requested_dataset_id else None,
        "relationships": ticket.relationships, "masking_needs": ticket.masking_needs,
        "needed_by": ticket.needed_by, "test_window_start": ticket.test_window_start, "test_window_end": ticket.test_window_end,
        "acceptance_criteria": ticket.acceptance_criteria, "urgent_justification": ticket.urgent_justification,
        "workspace_id": str(ticket.workspace_id), "workspace_name": db.get(Workspace, ticket.workspace_id).name if db.get(Workspace, ticket.workspace_id) else None, "project_id": ticket.project_id, "project_name": ticket.project_name,
        "requester_user_id": ticket.requester_user_id, "requester_name": requester.display_name if requester else ticket.requester_user_id,
        "assignee_user_id": ticket.assignee_user_id,
        "assignee_name": (assignee.display_name or assignee.email or ticket.assignee_user_id) if assignee else ticket.assignee_user_id,
        "priority": ticket.priority, "status": ticket.status, "created_at": ticket.created_at,
        "submitted_at": ticket.submitted_at, "updated_at": ticket.updated_at, "closed_at": ticket.closed_at,
        "cancelled_at": ticket.cancelled_at, "reopened_at": ticket.reopened_at, "row_version": ticket.row_version,
        "current_age_seconds": seconds if ticket.status in OPEN else None,
        "final_duration_seconds": seconds if ticket.status in TERMINAL else None,
        "elapsed_calendar_days": days, "on_hold_duration_seconds": blocked_seconds,
        "blocked_duration_seconds": blocked_seconds,
        "linked_dataset_id": str(ticket.linked_dataset_id) if ticket.linked_dataset_id else None,
        "linked_dataset_version_id": str(ticket.linked_dataset_version_id) if ticket.linked_dataset_version_id else None,
        "fulfillment_summary": ticket.fulfillment_summary, "closure_actor_user_id": ticket.closure_actor_user_id,
        "correlation_id": ticket.correlation_id}
    if detail:
        event_rows = list(db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id).order_by(TicketEvent.occurred_at, TicketEvent.id)))
        event_actor_ids = {event.actor_user_id for event in event_rows if event.actor_user_id}
        event_actors = {user.subject: user.display_name or user.email or user.subject for user in db.scalars(
            select(UserIdentity).where(UserIdentity.subject.in_(event_actor_ids)))} if event_actor_ids else {}
        data["events"] = [{"id": str(e.id), "event_type": e.event_type, "actor_user_id": e.actor_user_id,
            "actor_name": event_actors.get(e.actor_user_id, e.actor_user_id) if e.actor_user_id else None,
            "actor_role": e.actor_role, "occurred_at": e.occurred_at,
            "from_status": "On Hold" if e.from_status == "Blocked" else e.from_status,
            "to_status": "On Hold" if e.to_status == "Blocked" else e.to_status,
            "reason": e.reason, "event_data": e.event_data}
            for e in event_rows]
        closed_intervals = []
        closure_start = None
        for event in event_rows:
            if event.event_type == "closed":
                closure_start = (event.event_data or {}).get("closed_at") or event.occurred_at.isoformat()
            elif event.event_type == "reopened" and closure_start:
                closure_end = (event.event_data or {}).get("prior_closed_at")
                closed_intervals.append({"closed_at": closure_start, "reopened_at": closure_end})
                closure_start = None
        if closure_start and ticket.closed_at:
            closed_intervals.append({"closed_at": closure_start, "reopened_at": ticket.closed_at.isoformat()})
        data["closed_intervals"] = closed_intervals
        comment_rows = list(db.scalars(select(TicketComment).where(TicketComment.ticket_id == ticket.id).order_by(TicketComment.created_at, TicketComment.id)))
        comment_author_ids = {comment.author_user_id for comment in comment_rows}
        comment_authors = {user.subject: user.display_name or user.email or user.subject for user in db.scalars(
            select(UserIdentity).where(UserIdentity.subject.in_(comment_author_ids)))} if comment_author_ids else {}
        data["comments"] = [{"id": str(c.id), "author_user_id": c.author_user_id,
            "author_name": comment_authors.get(c.author_user_id) or c.author_user_id, "author_role": c.author_role,
            "body": c.body, "created_at": c.created_at} for c in comment_rows]
        # Older tickets may have TDE responses from before pickup automatically set
        # the assignee. Use the latest recorded TDE handling action as a display
        # fallback so those tickets do not continue to show "Unassigned".
        if not ticket.assignee_user_id:
            tde_activity = [(comment.created_at, comment.author_user_id) for comment in comment_rows
                if comment.author_role == HumanRole.TEST_DATA_ENGINEER.value]
            handling_events = {"status_changed", "dataset_linked", "closed", "reopened", "cancelled"}
            tde_activity.extend((event.occurred_at, event.actor_user_id) for event in event_rows
                if event.actor_user_id and event.actor_role == HumanRole.TEST_DATA_ENGINEER.value
                and event.event_type in handling_events)
            tde_activity.extend((event.occurred_at, (event.event_data or {}).get("after")) for event in event_rows
                if event.actor_role == HumanRole.TEST_DATA_ENGINEER.value and event.event_type == "assignment_changed"
                and (event.event_data or {}).get("automatic") and (event.event_data or {}).get("after"))
            if tde_activity:
                _, fallback_assignee_id = max(tde_activity, key=lambda activity: activity[0])
                fallback_identity = db.get(UserIdentity, fallback_assignee_id)
                data["assignee_user_id"] = fallback_assignee_id
                data["assignee_name"] = (fallback_identity.display_name or fallback_identity.email or fallback_assignee_id) if fallback_identity else fallback_assignee_id
        if ticket.linked_dataset_id:
            ds = db.get(DataSet, ticket.linked_dataset_id)
            ver = db.get(DataSetVersion, ticket.linked_dataset_version_id) if ticket.linked_dataset_version_id else None
            data["dataset"] = {"id": str(ds.id), "name": ds.name, "reference_code": ds.reference_code,
                "version_number": ver.version_number if ver else None, "state": ver.lifecycle_state if ver else "unavailable"} if ds else {"state": "unavailable"}
    if ticket.requested_dataset_id:
        requested = db.get(DataSet, ticket.requested_dataset_id)
        data["requested_dataset"] = {"id": str(requested.id), "name": requested.name,
            "reference_code": requested.reference_code} if requested else None
    return data


def _validate_requested_dataset(db: Session, principal: Principal, workspace_id: UUID, request_type: str | None, dataset_id: UUID | None) -> None:
    if request_type == "New dataset":
        if dataset_id:
            raise HTTPException(status_code=422, detail="A new dataset request cannot select an existing dataset")
        return
    if request_type in ("Change to existing dataset", "Additional records for existing dataset"):
        if not dataset_id:
            return
        dataset = db.get(DataSet, dataset_id)
        if not dataset or dataset.workspace_id != workspace_id:
            raise HTTPException(status_code=422, detail="Select a published dataset in this workspace")
        published = db.scalar(select(DataSetVersion).where(
            DataSetVersion.dataset_id == dataset.id,
            DataSetVersion.version_number == dataset.latest_version_number,
            DataSetVersion.lifecycle_state == "published",
            DataSetVersion.publication_state == "published",
        ))
        if not published:
            raise HTTPException(status_code=422, detail="Select a currently published dataset")
        from tigr.dataset_api import authorize_dataset_read
        authorize_dataset_read(db, principal, dataset, published)


def _required_to_submit(ticket: Ticket) -> None:
    required = {"title": ticket.title, "description": ticket.description, "request_type": ticket.request_type,
        "environment": ticket.environment, "application": ticket.application, "scenarios": ticket.scenarios,
        "record_count": ticket.record_count, "data_source_preference": ticket.data_source_preference, "needed_by": ticket.needed_by}
    missing = [key for key, value in required.items() if value is None or value == "" or value == []]
    if ticket.request_type != "New dataset" and ticket.requested_dataset_id is None:
        missing.append("requested_dataset_id")
    if missing: raise HTTPException(status_code=422, detail="Complete required request fields: " + ", ".join(missing))
    if ticket.needed_by.tzinfo is None or ticket.needed_by.utcoffset() is None: raise HTTPException(status_code=422, detail="Needed-by must include a timezone")
    if any(value is not None and (value.tzinfo is None or value.utcoffset() is None) for value in (ticket.test_window_start, ticket.test_window_end)):
        raise HTTPException(status_code=422, detail="Test window timestamps must include a timezone")
    if ticket.needed_by <= datetime.now(UTC): raise HTTPException(status_code=422, detail="Needed-by must be in the future")
    if ticket.priority == "Urgent" and not ticket.urgent_justification: raise HTTPException(status_code=422, detail="Urgent priority requires a justification")
    if ticket.test_window_start and ticket.test_window_end and ticket.test_window_end <= ticket.test_window_start:
        raise HTTPException(status_code=422, detail="Test window end must be later than start")


@router.post("/tickets")
def create_ticket(body: TicketInput, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    if principal.actor_type != "human" or workspace_role(db, principal, body.workspace_id) != HumanRole.QA_ENGINEER:
        raise HTTPException(status_code=403, detail="Only a QA Engineer can create a ticket")
    ensure_user(db, principal)
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        existing = db.scalar(select(Ticket).where(Ticket.requester_user_id == principal.subject, Ticket.idempotency_key == idempotency_key))
        if existing: return _out(db, existing)
    # Serialize draft creation per QA user so simultaneous requests cannot create multiple drafts.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"), {"subject": principal.subject})
    active_draft = db.scalar(select(Ticket).where(Ticket.requester_user_id == principal.subject, Ticket.status == "Draft").order_by(Ticket.created_at.desc()).limit(1))
    if active_draft:
        raise HTTPException(status_code=409, detail=f"You already have draft {active_draft.display_id}. Submit or cancel it before creating another request.")
    _validate_requested_dataset(db, principal, body.workspace_id, body.request_type, body.requested_dataset_id)
    now = datetime.now(UTC); ticket_id = uuid4(); correlation = getattr(request.state, "request_id", str(uuid4()))
    display_number = db.scalar(text("SELECT nextval('dataops.ticket_display_sequence')"))
    ticket = Ticket(id=ticket_id, display_id=f"TDR-{now.year}-{display_number:06d}", requester_user_id=principal.subject,
        status="Draft", created_at=now, updated_at=now, row_version=1,
        correlation_id=correlation, idempotency_key=idempotency_key, **body.model_dump())
    db.add(ticket); db.flush()
    _event(db, ticket, principal, HumanRole.QA_ENGINEER, "created", request)
    db.commit()
    return _out(db, ticket)


@router.get("/tickets")
def list_tickets(request: Request, workspace_id: UUID | None = None, status_filter: str | None = Query(None, alias="status"),
    priority: str | None = None, project_id: str | None = None, search: str | None = None, assigned_to_me: bool = False,
    open_only: bool = False,
    requester_user_id: str | None = None, assignee_user_id: str | None = None,
    submitted_after: datetime | None = None, submitted_before: datetime | None = None,
    closed_after: datetime | None = None, closed_before: datetime | None = None,
    needed_by_after: datetime | None = None, needed_by_before: datetime | None = None, age_bucket: str | None = None,
    sort: str = "submitted_at", direction: str = "desc", limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    q = select(Ticket)
    if workspace_id:
        role = workspace_role(db, principal, workspace_id)
        q = q.where(Ticket.workspace_id == workspace_id)
        if role == HumanRole.QA_ENGINEER: q = q.where(Ticket.requester_user_id == principal.subject)
        elif role == HumanRole.TEST_DATA_ENGINEER: q = q.where(Ticket.status != "Draft")
    elif principal.actor_type != "human": raise HTTPException(status_code=403, detail="Human access is required")
    else:
        # Restrict to workspaces with active membership; QA remains requester-scoped.
        from tigr.iam import WorkspaceMembership
        now = datetime.now(UTC)
        if is_platform_admin(db, principal):
            q = q.join(Workspace, Workspace.id == Ticket.workspace_id).where(Workspace.status == "active")
        else:
            q = q.join(WorkspaceMembership, WorkspaceMembership.workspace_id == Ticket.workspace_id).where(
            WorkspaceMembership.subject == principal.subject, WorkspaceMembership.revoked_at.is_(None), WorkspaceMembership.valid_from <= now,
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now))
            q = q.where(or_(
                and_(WorkspaceMembership.role == HumanRole.TEST_DATA_ENGINEER.value, Ticket.status != "Draft"),
                and_(WorkspaceMembership.role == HumanRole.QA_ENGINEER.value, Ticket.requester_user_id == principal.subject),
                WorkspaceMembership.role == HumanRole.ADMIN.value,
            ))
    if status_filter: q = q.where(Ticket.status == status_filter)
    if open_only: q = q.where(Ticket.status.in_(OPEN))
    if priority: q = q.where(Ticket.priority == priority)
    if project_id: q = q.where(Ticket.project_id == project_id)
    if assigned_to_me: q = q.where(Ticket.assignee_user_id == principal.subject)
    if requester_user_id: q = q.where(Ticket.requester_user_id == requester_user_id)
    if assignee_user_id: q = q.where(Ticket.assignee_user_id == assignee_user_id)
    if submitted_after: q = q.where(Ticket.submitted_at >= submitted_after)
    if submitted_before: q = q.where(Ticket.submitted_at < submitted_before)
    if closed_after: q = q.where(Ticket.closed_at >= closed_after)
    if closed_before: q = q.where(Ticket.closed_at < closed_before)
    if needed_by_after: q = q.where(Ticket.needed_by >= needed_by_after)
    if needed_by_before: q = q.where(Ticket.needed_by <= needed_by_before)
    if age_bucket:
        bounds = {"under_1_day": (None, 24), "1_3_days": (24, 96), "4_7_days": (96, 192), "8_14_days": (192, 360), "over_14_days": (360, None)}
        if age_bucket not in bounds: raise HTTPException(status_code=422, detail="Unknown age bucket")
        lower, upper = bounds[age_bucket]; elapsed_hours = func.extract("epoch", func.now() - Ticket.submitted_at) / 3600
        q = q.where(Ticket.status.in_(OPEN))
        if lower is not None: q = q.where(elapsed_hours >= lower)
        if upper is not None: q = q.where(elapsed_hours < upper)
    if search: q = q.where(or_(Ticket.display_id.ilike(f"%{search}%"), Ticket.title.ilike(f"%{search}%")))
    total = db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0
    sort_col = {"submitted_at": Ticket.submitted_at, "age": Ticket.submitted_at, "needed_by": Ticket.needed_by, "priority": Ticket.priority,
        "updated_at": Ticket.updated_at, "status": Ticket.status, "created_at": Ticket.created_at, "closed_at": Ticket.closed_at}.get(sort, Ticket.submitted_at)
    if sort == "age": direction = "asc"
    q = q.order_by(case((Ticket.status == "Draft", 0), else_=1), sort_col.asc().nulls_last() if direction == "asc" else sort_col.desc().nulls_last()).offset(offset).limit(limit)
    rows = list(db.scalars(q).unique())
    return {"items": [_out(db, row) for row in rows], "limit": limit, "offset": offset, "total": total, "has_more": offset + len(rows) < total}


@router.get("/tickets/draft")
def get_requester_draft(db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    if principal.actor_type != "human": raise HTTPException(status_code=403, detail="Human access is required")
    draft = db.scalar(select(Ticket).where(Ticket.requester_user_id == principal.subject, Ticket.status == "Draft").order_by(Ticket.created_at.desc()).limit(1))
    if draft and _role(db, principal, draft) != HumanRole.QA_ENGINEER:
        raise HTTPException(status_code=403, detail="Only the requester can access a draft")
    return _out(db, draft) if draft else None


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket, _ = _ticket(db, ticket_id, principal)
    return _out(db, ticket, detail=True)


@router.patch("/tickets/{ticket_id}")
def update_ticket(ticket_id: UUID, body: TicketInput, request: Request, row_version: int = Query(...), db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket, role = _ticket(db, ticket_id, principal, lock=True)
    if role != HumanRole.QA_ENGINEER or ticket.requester_user_id != principal.subject or ticket.status != "Draft": raise HTTPException(status_code=403, detail="Only your Draft ticket can be edited")
    if ticket.row_version != row_version: raise HTTPException(status_code=409, detail="Ticket changed; refresh and retry")
    _validate_requested_dataset(db, principal, ticket.workspace_id, body.request_type, body.requested_dataset_id)
    before = {"title": ticket.title, "record_count": ticket.record_count}
    for key, value in body.model_dump(exclude={"workspace_id"}).items(): setattr(ticket, key, value)
    ticket.row_version += 1; ticket.updated_at = datetime.now(UTC)
    _event(db, ticket, principal, role, "draft_edited", request, data={"before": before, "after": {"title": ticket.title, "record_count": ticket.record_count}})
    db.commit(); return _out(db, ticket)


@router.post("/tickets/{ticket_id}/submit")
def submit_ticket(ticket_id: UUID, request: Request, body: Mutation, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket, role = _ticket(db, ticket_id, principal, lock=True)
    if role != HumanRole.QA_ENGINEER or ticket.requester_user_id != principal.subject: raise HTTPException(status_code=403, detail="Only the requester can submit")
    if ticket.status == "Submitted": return _out(db, ticket)
    if ticket.row_version != body.row_version: raise HTTPException(status_code=409, detail="Ticket changed; refresh and retry")
    if ticket.status != "Draft": raise HTTPException(status_code=409, detail="Only Draft tickets can be submitted")
    _required_to_submit(ticket)
    _validate_requested_dataset(db, principal, ticket.workspace_id, ticket.request_type, ticket.requested_dataset_id)
    now = datetime.now(UTC); ticket.status="Submitted"; ticket.submitted_at=now; ticket.updated_at=now; ticket.row_version += 1
    _event(db, ticket, principal, role, "submitted", request, old="Draft", new="Submitted")
    _notify(db, ticket, "ticket.submitted", request, "workspace:TDE")
    db.commit(); return _out(db, ticket)


@router.post("/tickets/{ticket_id}/assign")
def assign_ticket(ticket_id: UUID, body: Assignment, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket, role = _ticket(db, ticket_id, principal, lock=True)
    if role not in (HumanRole.TEST_DATA_ENGINEER, HumanRole.ADMIN): raise HTTPException(status_code=403, detail="TDE access is required")
    if ticket.status not in OPEN: raise HTTPException(status_code=409, detail="Only open tickets can be assigned")
    if ticket.row_version != body.row_version: raise HTTPException(status_code=409, detail="Ticket changed; refresh and retry")
    if not body.reason: raise HTTPException(status_code=422, detail="Assignment reason is required")
    from tigr.iam import WorkspaceMembership
    member = db.get(WorkspaceMembership, (ticket.workspace_id, body.assignee_user_id)); now = datetime.now(UTC)
    if not member or member.revoked_at or member.role != HumanRole.TEST_DATA_ENGINEER.value or member.valid_from > now or (member.valid_until and member.valid_until <= now):
        raise HTTPException(status_code=422, detail="Assignee must be an authorized TDE in this workspace")
    previous = ticket.assignee_user_id; ticket.assignee_user_id=body.assignee_user_id; ticket.row_version += 1; ticket.updated_at=datetime.now(UTC)
    previous_identity = db.get(UserIdentity, previous) if previous else None
    current_identity = db.get(UserIdentity, body.assignee_user_id)
    assignment_details = {"assigned_from": previous, "assigned_from_name": (previous_identity.display_name or previous_identity.email or previous) if previous_identity else previous,
        "assigned_to": body.assignee_user_id, "assigned_to_name": (current_identity.display_name or current_identity.email or body.assignee_user_id) if current_identity else body.assignee_user_id,
        "assignment_reason": body.reason}
    _event(db, ticket, principal, role, "assignment_changed", request, reason=body.reason,
        data={"before": previous, "after": body.assignee_user_id,
            "before_name": assignment_details["assigned_from_name"], "after_name": assignment_details["assigned_to_name"]})
    recipients = {ticket.requester_user_id, previous, body.assignee_user_id} - {principal.subject, None}
    for recipient in recipients:
        _notify(db, ticket, "ticket.assigned", request, recipient, assignment_details)
    db.commit(); return _out(db, ticket)


@router.post("/tickets/{ticket_id}/comments")
def add_comment(ticket_id: UUID, body: CommentInput, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket, role = _ticket(db, ticket_id, principal, lock=True)
    if ticket.status == "Draft" and role != HumanRole.QA_ENGINEER: raise HTTPException(status_code=409, detail="Draft is not visible to the queue")
    # Ticket participants may continue the conversation after closure; comments do not reopen the ticket.
    if role == HumanRole.TEST_DATA_ENGINEER and ticket.status in OPEN:
        _claim_ticket_for_actor(ticket, db, request, principal, role, "Automatically assigned to the TDE who responded")
    if role == HumanRole.QA_ENGINEER and ticket.status == "On Hold":
        clarification = db.scalar(select(TicketBlockPeriod).where(
            TicketBlockPeriod.ticket_id == ticket.id, TicketBlockPeriod.ended_at.is_(None)))
        if clarification:
            _set_ticket_assignee(ticket, db, request, principal, role, clarification.requested_by_user_id,
                "Automatically returned to the TDE after the requester responded to clarification")
    comment = TicketComment(id=uuid4(), ticket_id=ticket.id, author_user_id=principal.subject, author_role=role.value, body=body.body)
    db.add(comment); ticket.updated_at=datetime.now(UTC); ticket.row_version += 1
    _event(db, ticket, principal, role, "comment_added", request, data={"comment_id": str(comment.id)})
    author = db.get(UserIdentity, principal.subject)
    comment_details = {"comment_id": str(comment.id), "comment_body": body.body,
        "comment_author_user_id": principal.subject, "comment_author": (author.display_name or author.email or principal.subject) if author else (principal.display_name or principal.subject),
        "comment_author_role": role.value, "requires_action": True}
    if role == HumanRole.QA_ENGINEER:
        recipients = {ticket.assignee_user_id or "workspace:TDE"} - {principal.subject}
    else:
        recipients = {ticket.requester_user_id} - {principal.subject}
        if role == HumanRole.ADMIN and ticket.assignee_user_id:
            recipients.add(ticket.assignee_user_id)
    for recipient in recipients:
        _notify(db, ticket, "ticket.comment_action_required", request, recipient, comment_details)
    db.commit(); return {"id": str(comment.id), "created_at": comment.created_at}


@router.post("/tickets/{ticket_id}/start")
def start_ticket(ticket_id: UUID, body: Mutation, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    return _transition(ticket_id, "In Progress", body, request, db, principal,
        claim_assignee=True, claim_reason="Automatically assigned to the TDE who started work")


@router.post("/tickets/{ticket_id}/request-clarification")
def block_ticket(ticket_id: UUID, body: Mutation, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    if not body.reason: raise HTTPException(status_code=422, detail="Clarification question is required")
    return _transition(ticket_id, "On Hold", body, request, db, principal, reason=body.reason,
        claim_assignee=True, claim_reason="Automatically assigned to the TDE who requested clarification")


@router.post("/tickets/{ticket_id}/resume")
def resume_ticket(ticket_id: UUID, body: Mutation, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket, role = _ticket(db, ticket_id, principal, lock=True)
    if role != HumanRole.TEST_DATA_ENGINEER: raise HTTPException(status_code=403, detail="TDE access is required")
    if ticket.status != "On Hold": raise HTTPException(status_code=409, detail="Only On Hold tickets can resume")
    if ticket.row_version != body.row_version: raise HTTPException(status_code=409, detail="Ticket changed; refresh and retry")
    period = db.scalar(select(TicketBlockPeriod).where(TicketBlockPeriod.ticket_id == ticket.id, TicketBlockPeriod.ended_at.is_(None)))
    if period: period.ended_at=datetime.now(UTC); period.resolved_by_user_id=principal.subject
    return _transition(ticket_id, "In Progress", body, request, db, principal,
        claim_assignee=True, claim_reason="Automatically assigned to the TDE who resumed work")


@router.get("/tickets/{ticket_id}/share-options")
def ticket_share_options(ticket_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket, role = _ticket(db, ticket_id, principal)
    if role != HumanRole.TEST_DATA_ENGINEER: raise HTTPException(status_code=403, detail="TDE access is required")
    if ticket.status != "In Progress": raise HTTPException(status_code=409, detail="Only In Progress tickets can be shared")
    now = datetime.now(UTC)
    users = db.execute(select(WorkspaceMembership.subject, UserIdentity.display_name, UserIdentity.email)
        .join(UserIdentity, UserIdentity.subject == WorkspaceMembership.subject)
        .where(WorkspaceMembership.workspace_id == ticket.workspace_id,
            WorkspaceMembership.role == HumanRole.QA_ENGINEER.value, WorkspaceMembership.revoked_at.is_(None),
            WorkspaceMembership.valid_from <= now,
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now))
        .order_by(UserIdentity.display_name, UserIdentity.email, WorkspaceMembership.subject)).all()
    datasets = db.execute(select(DataSet, DataSetVersion)
        .join(DataSetVersion, and_(DataSetVersion.dataset_id == DataSet.id,
            DataSetVersion.version_number == DataSet.latest_version_number))
        .where(DataSet.workspace_id == ticket.workspace_id,
            DataSetVersion.lifecycle_state == "published", DataSetVersion.publication_state == "published",
            DataSetVersion.validation_state == "passed", DataSetVersion.privacy_state == "compliant",
            or_(DataSetVersion.expiry_at.is_(None), DataSetVersion.expiry_at > now))
        .order_by(DataSet.name, DataSet.reference_code)).all()
    return {
        "users": [{"subject": subject, "display_name": display_name or email or subject, "email": email}
            for subject, display_name, email in users],
        "datasets": [{"id": str(dataset.id), "name": dataset.name, "reference_code": dataset.reference_code}
            for dataset, _ in datasets],
    }


@router.get("/tickets/{ticket_id}/assignment-options")
def ticket_assignment_options(ticket_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket, role = _ticket(db, ticket_id, principal)
    if role not in (HumanRole.TEST_DATA_ENGINEER, HumanRole.ADMIN):
        raise HTTPException(status_code=403, detail="TDE access is required")
    now = datetime.now(UTC)
    users = db.execute(select(WorkspaceMembership.subject, UserIdentity.display_name, UserIdentity.email)
        .join(UserIdentity, UserIdentity.subject == WorkspaceMembership.subject)
        .where(WorkspaceMembership.workspace_id == ticket.workspace_id,
            WorkspaceMembership.role == HumanRole.TEST_DATA_ENGINEER.value, WorkspaceMembership.revoked_at.is_(None),
            WorkspaceMembership.valid_from <= now,
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now))
        .order_by(UserIdentity.display_name, UserIdentity.email, WorkspaceMembership.subject)).all()
    return [{"subject": subject, "display_name": display_name or email or subject, "email": email}
        for subject, display_name, email in users]


def _transition(ticket_id: UUID, new_status: str, body: Mutation, request: Request, db: Session, principal: Principal,
    reason: str | None = None, claim_assignee: bool = False, claim_reason: str | None = None):
    ticket, role = _ticket(db, ticket_id, principal, lock=True)
    if role != HumanRole.TEST_DATA_ENGINEER: raise HTTPException(status_code=403, detail="TDE access is required")
    allowed = {"Submitted": ("In Progress", "On Hold"), "In Progress": ("On Hold",), "On Hold": ("In Progress",)}
    if new_status not in allowed.get(ticket.status, ()): raise HTTPException(status_code=409, detail=f"Cannot move {ticket.status} to {new_status}")
    if new_status == "On Hold" and not reason: raise HTTPException(status_code=422, detail="Clarification question is required")
    if ticket.row_version != body.row_version: raise HTTPException(status_code=409, detail="Ticket changed; refresh and retry")
    old=ticket.status; ticket.status=new_status; ticket.updated_at=datetime.now(UTC); ticket.row_version += 1
    if new_status == "On Hold":
        _set_ticket_assignee(ticket, db, request, principal, role, ticket.requester_user_id,
            "Automatically assigned to the requester while clarification is pending")
    elif claim_assignee and claim_reason:
        _claim_ticket_for_actor(ticket, db, request, principal, role, claim_reason)
    if new_status == "On Hold": db.add(TicketBlockPeriod(id=uuid4(), ticket_id=ticket.id, started_at=ticket.updated_at, reason=reason, requested_by_user_id=principal.subject))
    _event(db, ticket, principal, role, "status_changed", request, old=old, new=new_status, reason=reason)
    if new_status == "In Progress": _notify(db, ticket, "ticket.started", request, ticket.requester_user_id)
    if new_status == "On Hold": _notify(db, ticket, "ticket.clarification_requested", request, ticket.requester_user_id)
    db.commit(); return _out(db, ticket)


@router.post("/tickets/{ticket_id}/link-dataset")
def link_dataset(ticket_id: UUID, body: LinkDataset, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket, role = _ticket(db, ticket_id, principal, lock=True)
    if role != HumanRole.TEST_DATA_ENGINEER: raise HTTPException(status_code=403, detail="TDE access is required")
    if ticket.row_version != body.row_version: raise HTTPException(status_code=409, detail="Ticket changed; refresh and retry")
    ds = db.get(DataSet, body.dataset_id); ver = db.get(DataSetVersion, body.version_id)
    if not ds or not ver or ds.workspace_id != ticket.workspace_id or ver.dataset_id != ds.id or ver.workspace_id != ticket.workspace_id:
        raise HTTPException(status_code=422, detail="Dataset version is unavailable in this workspace")
    if ver.lifecycle_state not in ("draft", "ready", "published"):
        raise HTTPException(status_code=409, detail="Only draft, ready, or published dataset versions can be linked")
    _claim_ticket_for_actor(ticket, db, request, principal, role, "Automatically assigned to the TDE who linked the dataset")
    ticket.linked_dataset_id=ds.id; ticket.linked_dataset_version_id=ver.id; ticket.updated_at=datetime.now(UTC); ticket.row_version += 1
    _event(db, ticket, principal, role, "dataset_linked", request, data={"dataset_id": str(ds.id), "version_id": str(ver.id)})
    db.commit(); return _out(db, ticket, detail=True)


@router.post("/tickets/{ticket_id}/close")
def close_ticket(ticket_id: UUID, body: CloseInput, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket, role = _ticket(db, ticket_id, principal, lock=True)
    if role != HumanRole.TEST_DATA_ENGINEER: raise HTTPException(status_code=403, detail="Only the assigned workspace TDE can close tickets")
    idempotency_key = request.headers.get("Idempotency-Key")
    if ticket.status == "Closed" and idempotency_key:
        prior = db.scalar(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id, TicketEvent.event_type == "closed",
            TicketEvent.event_data["idempotency_key"].as_string() == idempotency_key))
        if prior: return _out(db, ticket, detail=True)
    if ticket.status != "In Progress": raise HTTPException(status_code=409, detail="Only In Progress tickets can be closed")
    if ticket.row_version != body.row_version: raise HTTPException(status_code=409, detail="Ticket changed; refresh and retry")
    ds = None
    ver = None
    shared_with_user_id = None
    if not body.no_dataset:
        shared_with_user_id = body.shared_with_user_id or ticket.requester_user_id
        if not db.scalar(select(WorkspaceMembership.subject).where(WorkspaceMembership.workspace_id == ticket.workspace_id,
            WorkspaceMembership.subject == shared_with_user_id, WorkspaceMembership.role == HumanRole.QA_ENGINEER.value,
            WorkspaceMembership.revoked_at.is_(None), WorkspaceMembership.valid_from <= datetime.now(UTC),
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > datetime.now(UTC)))):
            raise HTTPException(status_code=422, detail="Share recipient must be an active QA user in this workspace")
        if body.dataset_id:
            ds = db.get(DataSet, body.dataset_id)
            ver = db.scalar(select(DataSetVersion).where(DataSetVersion.dataset_id == body.dataset_id,
                DataSetVersion.version_number == ds.latest_version_number)) if ds else None
        else:
            ds=db.get(DataSet,ticket.linked_dataset_id) if ticket.linked_dataset_id else None
            ver=db.get(DataSetVersion,ticket.linked_dataset_version_id) if ticket.linked_dataset_version_id else None
        if not ds or not ver or ds.workspace_id != ticket.workspace_id or ver.lifecycle_state != "published" or ver.publication_state != "published" or ver.privacy_state != "compliant" or ver.validation_state != "passed" or (ver.expiry_at and ver.expiry_at <= datetime.now(UTC)):
            raise HTTPException(status_code=409, detail="Linked dataset is not currently available to share")
        # QA access follows the platform's existing published-dataset policy.
        now_check = datetime.now(UTC)
        if not db.scalar(select(WorkspaceMembership.subject).where(WorkspaceMembership.workspace_id == ticket.workspace_id,
            WorkspaceMembership.subject == ticket.requester_user_id, WorkspaceMembership.role == HumanRole.QA_ENGINEER.value,
            WorkspaceMembership.revoked_at.is_(None), WorkspaceMembership.valid_from <= now_check,
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now_check))):
            raise HTTPException(status_code=409, detail="Requester access could not be verified")
    _claim_ticket_for_actor(ticket, db, request, principal, role, "Automatically assigned to the TDE who closed the ticket")
    ticket.linked_dataset_id=ds.id if ds else None; ticket.linked_dataset_version_id=ver.id if ver else None
    now=datetime.now(UTC); ticket.status="Closed"; ticket.closed_at=now; ticket.closure_actor_user_id=principal.subject
    ticket.fulfillment_summary=body.fulfillment_summary; ticket.updated_at=now; ticket.row_version += 1
    _event(db,ticket,principal,role,"closed",request,old="In Progress",new="Closed",reason=body.fulfillment_summary,
        data={"shared_with":shared_with_user_id,"dataset_id":str(ds.id) if ds else None,"version_id":str(ver.id) if ver else None,
            "no_dataset":body.no_dataset,"idempotency_key":idempotency_key,"closed_at":now.isoformat()},)
    for recipient in {ticket.requester_user_id, shared_with_user_id} - {None}:
        _notify(db,ticket,"ticket.closed",request,recipient,{"shared_with":shared_with_user_id,
            "dataset_id":str(ds.id) if ds else None,"dataset_name":ds.name if ds else None,"no_dataset":body.no_dataset})
    db.commit(); return _out(db,ticket,detail=True)


@router.post("/tickets/{ticket_id}/cancel")
def cancel_ticket(ticket_id: UUID, body: Mutation, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket, role=_ticket(db,ticket_id,principal,lock=True)
    if not body.reason: raise HTTPException(status_code=422,detail="Cancellation reason is required")
    if role == HumanRole.QA_ENGINEER:
        if ticket.requester_user_id != principal.subject or ticket.status not in ("Draft","Submitted"): raise HTTPException(status_code=403,detail="You can cancel only your Draft or unstarted Submitted ticket")
    elif role == HumanRole.TEST_DATA_ENGINEER:
        if ticket.status not in OPEN: raise HTTPException(status_code=409,detail="Only open tickets can be cancelled by a TDE")
    else: raise HTTPException(status_code=403,detail="TDE access is required")
    if ticket.row_version != body.row_version: raise HTTPException(status_code=409,detail="Ticket changed; refresh and retry")
    old=ticket.status; now=datetime.now(UTC); ticket.status="Cancelled"; ticket.cancelled_at=now; ticket.updated_at=now; ticket.row_version += 1
    _event(db,ticket,principal,role,"cancelled",request,old=old,new="Cancelled",reason=body.reason)
    if role != HumanRole.QA_ENGINEER: _notify(db,ticket,"ticket.cancelled",request,ticket.requester_user_id)
    db.commit(); return _out(db,ticket)


@router.post("/tickets/{ticket_id}/reopen")
def reopen_ticket(ticket_id: UUID, body: Mutation, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket,role=_ticket(db,ticket_id,principal,lock=True)
    if role != HumanRole.TEST_DATA_ENGINEER: raise HTTPException(status_code=403,detail="TDE access is required")
    if ticket.status != "Closed" or not body.reason: raise HTTPException(status_code=409,detail="A closed ticket and a reopen reason are required")
    now_check=datetime.now(UTC)
    if not db.scalar(select(WorkspaceMembership.subject).where(WorkspaceMembership.workspace_id == ticket.workspace_id,
        WorkspaceMembership.subject == ticket.requester_user_id, WorkspaceMembership.role == HumanRole.QA_ENGINEER.value,
        WorkspaceMembership.revoked_at.is_(None), WorkspaceMembership.valid_from <= now_check,
        or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now_check))):
        raise HTTPException(status_code=409,detail="Requester workspace access must be restored before reopening")
    if ticket.row_version != body.row_version: raise HTTPException(status_code=409,detail="Ticket changed; refresh and retry")
    _claim_ticket_for_actor(ticket, db, request, principal, role, "Automatically assigned to the TDE who reopened the ticket")
    previous=ticket.closed_at; now=datetime.now(UTC); ticket.status="In Progress"; ticket.closed_at=None; ticket.reopened_at=now; ticket.updated_at=now; ticket.row_version += 1
    _event(db,ticket,principal,role,"reopened",request,old="Closed",new="In Progress",reason=body.reason,data={"prior_closed_at":previous.isoformat() if previous else None})
    _notify(db,ticket,"ticket.reopened",request,ticket.requester_user_id,{"reason":body.reason}); db.commit(); return _out(db,ticket,detail=True)


@router.get("/tickets/{ticket_id}/events")
def ticket_events(ticket_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    ticket,_=_ticket(db,ticket_id,principal)
    return _out(db,ticket,detail=True)["events"]


@router.get("/notifications/tickets")
def ticket_notifications(workspace_id: UUID = Query(...), db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal)):
    _notification_workspace_access(db, principal, workspace_id)
    rows = list(db.scalars(select(TicketNotification).join(Ticket, Ticket.id == TicketNotification.ticket_id).where(
        TicketNotification.user_id == principal.subject, Ticket.workspace_id == workspace_id,
        TicketNotification.event_type.in_(('ticket.assigned', 'ticket.comment_action_required', 'ticket.closed', 'ticket.reopened')))
        .order_by(TicketNotification.created_at.desc()).limit(100)))
    visible = []
    for row in rows:
        ticket = db.get(Ticket, row.ticket_id)
        if ticket is None: continue
        try: _role(db, principal, ticket)
        except HTTPException: continue
        visible.append({"id": str(row.id), "ticket_id": str(row.ticket_id), "event_type": row.event_type,
            "payload": row.payload, "created_at": row.created_at, "read_at": row.read_at})
    return visible


@router.patch("/notifications/tickets/read-all")
def mark_all_ticket_notifications_read(workspace_id: UUID = Query(...), db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal)):
    _notification_workspace_access(db, principal, workspace_id)
    rows = list(db.scalars(select(TicketNotification).join(Ticket, Ticket.id == TicketNotification.ticket_id).where(
        TicketNotification.user_id == principal.subject, Ticket.workspace_id == workspace_id,
        TicketNotification.read_at.is_(None))))
    now = datetime.now(UTC)
    for row in rows: row.read_at = now
    db.commit()
    return {"updated": len(rows)}


@router.patch("/notifications/tickets/{notification_id}/read")
def mark_ticket_notification_read(notification_id: UUID, workspace_id: UUID = Query(...), db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal)):
    _notification_workspace_access(db, principal, workspace_id)
    row = db.scalar(select(TicketNotification).join(Ticket, Ticket.id == TicketNotification.ticket_id).where(
        TicketNotification.id == notification_id, TicketNotification.user_id == principal.subject,
        Ticket.workspace_id == workspace_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Ticket notification not found")
    ticket = db.get(Ticket, row.ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket notification not found")
    try: _role(db, principal, ticket)
    except HTTPException: raise HTTPException(status_code=404, detail="Ticket notification not found")
    if row.read_at is None:
        row.read_at = datetime.now(UTC)
        db.commit()
    return {"id": str(row.id), "read_at": row.read_at}


def _notification_workspace_access(db: Session, principal: Principal, workspace_id: UUID) -> None:
    if principal.actor_type != "human":
        raise HTTPException(status_code=403, detail="Human access is required")
    try:
        workspace_role(db, principal, workspace_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Workspace not found")


@router.get("/reports/tickets")
def ticket_report(workspace_id: UUID | None = None, status_filter: str | None = Query(None, alias="status"),
    priority: str | None = None, project_id: str | None = None, search: str | None = None,
    requester_user_id: str | None = None, assignee_user_id: str | None = None,
    submitted_after: datetime | None = None, submitted_before: datetime | None = None,
    closed_after: datetime | None = None, closed_before: datetime | None = None,
    needed_by_after: datetime | None = None, needed_by_before: datetime | None = None,
    age_bucket: str | None = None, date_field: Literal["submitted_at", "closed_at"] = "submitted_at",
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    rows=list_tickets(Request(scope={"type":"http","method":"GET","path":"/"}),workspace_id=workspace_id,
        status_filter=status_filter,priority=priority,project_id=project_id,search=search,assigned_to_me=False,
        requester_user_id=requester_user_id,assignee_user_id=assignee_user_id,
        submitted_after=submitted_after,submitted_before=submitted_before,closed_after=closed_after,closed_before=closed_before,
        needed_by_after=needed_by_after,needed_by_before=needed_by_before,age_bucket=age_bucket,sort=date_field,direction="desc",
        limit=500,offset=0,db=db,principal=principal)
    items=rows["items"]; counts={s:sum(1 for t in items if t["status"]==s) for s in (*OPEN,*TERMINAL,"Draft")}
    closed_durations=[t["final_duration_seconds"] for t in items if t["status"]=="Closed" and t["final_duration_seconds"] is not None]
    now=datetime.now(UTC)
    buckets={"under_1_day":0,"1_3_days":0,"4_7_days":0,"8_14_days":0,"over_14_days":0}
    for item in items:
        if item["status"] not in OPEN or item["submitted_at"] is None: continue
        hours=(now-item["submitted_at"]).total_seconds()/3600
        key="under_1_day" if hours<24 else "1_3_days" if hours<96 else "4_7_days" if hours<192 else "8_14_days" if hours<360 else "over_14_days"
        buckets[key]+=1
    return {"items":items,"counts":counts,"open_age_buckets":buckets,"selected_date_field":date_field,
        "average_closure_seconds":round(statistics.mean(closed_durations)) if closed_durations else None,
        "median_closure_seconds":round(statistics.median(closed_durations)) if closed_durations else None,
        "on_hold_duration_seconds":sum(t["on_hold_duration_seconds"] for t in items),
        "blocked_duration_seconds":sum(t["on_hold_duration_seconds"] for t in items),
        "overdue_needed_by_count":sum(1 for t in items if t["status"] in OPEN and t["needed_by"] and t["needed_by"] < now),
        "priority_distribution":{p:sum(1 for t in items if t["priority"]==p) for p in ("Urgent","Normal","Low")},
        "generated_at":now}


@router.get("/reports/tickets/export")
def export_tickets(request: Request, workspace_id: UUID | None = None, status_filter: str | None = Query(None, alias="status"),
    priority: str | None = None, project_id: str | None = None, search: str | None = None,
    assigned_to_me: bool = False,
    submitted_after: datetime | None = None, submitted_before: datetime | None = None,
    closed_after: datetime | None = None, closed_before: datetime | None = None,
    needed_by_after: datetime | None = None, needed_by_before: datetime | None = None, age_bucket: str | None = None,
    open_only: bool = False,
    sort: str = "submitted_at", direction: str = "desc",
    db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    rows=list_tickets(request,workspace_id=workspace_id,status_filter=status_filter,priority=priority,project_id=project_id,
        search=search,assigned_to_me=assigned_to_me,open_only=open_only,submitted_after=submitted_after,submitted_before=submitted_before,needed_by_before=needed_by_before,
        closed_after=closed_after,closed_before=closed_before,needed_by_after=needed_by_after,age_bucket=age_bucket,sort=sort,direction=direction,
        limit=500,offset=0,db=db,principal=principal)["items"]
    output=io.StringIO(); writer=csv.writer(output); fields=["display_id","title","workspace_id","project_name","requester_name","assignee_name","status","priority","submitted_at","current_age_seconds","final_duration_seconds","on_hold_duration_seconds","needed_by","closed_at","linked_dataset_id","linked_dataset_version_id","closure_actor_user_id"]
    writer.writerow([f"Generated at: {datetime.now(UTC).isoformat()}"])
    writer.writerow([f"Applied filters: workspace_id={workspace_id}; status={status_filter}; priority={priority}; search={search}; age_bucket={age_bucket}; assigned_to_me={assigned_to_me}; open_only={open_only}; sort={sort} {direction}"])
    writer.writerow(fields)
    for item in rows: writer.writerow([item.get(k) for k in fields])
    return StreamingResponse(iter([output.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=tickets.csv"})
