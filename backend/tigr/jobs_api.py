"""Role-scoped job inventory and execution-step detail."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from tigr.access import ensure_user
from tigr.dashboard import visible_workspace_roles
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.datasets import DataSet, DataSetVersion
from tigr.jobs import Job, JobStatus, JobStep, OutboxEvent
from tigr.platform_access import is_tigr_admin

router = APIRouter(prefix="/api/v1", tags=["jobs"])


def scoped_job_query(db: Session, principal: Principal, workspace_id: UUID | None = None):
    query = select(Job).where(Job.status != JobStatus.ARCHIVED)
    if is_tigr_admin(db, principal):
        return query.where(Job.workspace_id == str(workspace_id)) if workspace_id else query
    roles = visible_workspace_roles(db, principal)
    if workspace_id is not None and workspace_id not in roles:
        return query.where(False)
    selected = {workspace_id: roles[workspace_id]} if workspace_id else roles
    scopes = []
    for selected_workspace, role in selected.items():
        scope = Job.workspace_id == str(selected_workspace)
        if role == HumanRole.TEST_DATA_ENGINEER:
            scope = and_(scope, or_(Job.actor_id == principal.subject, Job.actor_role == HumanRole.QA_ENGINEER.value))
        elif role == HumanRole.QA_ENGINEER:
            scope = and_(scope, Job.actor_id == principal.subject)
        scopes.append(scope)
    scopes.append(and_(Job.workspace_id.is_(None), Job.actor_id == principal.subject))
    return query.where(or_(*scopes))


def job_dataset_labels(db: Session, jobs: list[Job]) -> dict[str, dict[str, str]]:
    if not jobs:
        return {}
    job_ids = [str(job.id) for job in jobs]
    events = db.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.payload["job_id"].as_string().in_(job_ids))
        .order_by(OutboxEvent.created_at.desc())
    ).all()
    dataset_ids: dict[str, str] = {}
    version_ids: set[UUID] = set()
    for event in events:
        job_id = str(event.payload.get("job_id", ""))
        if not job_id or job_id in dataset_ids:
            continue
        dataset_id = event.payload.get("dataset_id")
        if dataset_id:
            dataset_ids[job_id] = str(dataset_id)
        elif event.aggregate_type == "dataset":
            dataset_ids[job_id] = event.aggregate_id
        elif event.aggregate_type == "dataset_version":
            try:
                version_ids.add(UUID(event.aggregate_id))
            except ValueError:
                continue
    if version_ids:
        versions = db.scalars(select(DataSetVersion).where(DataSetVersion.id.in_(version_ids))).all()
        for version in versions:
            for event in events:
                if event.aggregate_type == "dataset_version" and event.aggregate_id == str(version.id):
                    dataset_ids.setdefault(str(event.payload.get("job_id", "")), str(version.dataset_id))
    valid_dataset_ids: set[UUID] = set()
    for dataset_id in dataset_ids.values():
        try:
            valid_dataset_ids.add(UUID(dataset_id))
        except ValueError:
            continue
    datasets = db.scalars(select(DataSet).where(DataSet.id.in_(valid_dataset_ids))).all() if valid_dataset_ids else []
    by_id = {str(dataset.id): dataset for dataset in datasets}
    return {
        job_id: {"dataset_id": dataset_id, "dataset_reference_code": by_id[dataset_id].reference_code,
                 "dataset_name": by_id[dataset_id].name}
        for job_id, dataset_id in dataset_ids.items() if dataset_id in by_id
    }


def job_summary(job: Job, dataset_labels: dict[str, str] | None = None) -> dict:
    duration_seconds = None
    if job.ended_at and job.created_at:
        duration_seconds = max(0, round((job.ended_at - job.created_at).total_seconds()))
    return {"id": str(job.id), "job_type": job.job_type, "status": job.status, "progress": job.progress,
            "workspace_id": job.workspace_id, "actor_role": job.actor_role, "request_id": job.request_id,
            "safe_summary": job.safe_summary, "created_at": job.created_at.isoformat() if job.created_at else None,
            "ended_at": job.ended_at.isoformat() if job.ended_at else None,
            "duration_seconds": duration_seconds,
            "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None,
            **(dataset_labels or {})}


@router.get("/jobs")
def list_jobs(workspace_id: UUID | None = None, status: str | None = Query(default=None, max_length=32),
              search: str | None = Query(default=None, max_length=120), page: int = Query(default=1, ge=1),
              page_size: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db),
              principal: Principal = Depends(get_principal)) -> dict:
    ensure_user(db, principal)
    query = scoped_job_query(db, principal, workspace_id)
    if status:
        query = query.where(Job.status == status)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(or_(Job.job_type.ilike(term), Job.status.ilike(term), Job.safe_summary.ilike(term)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    jobs = db.scalars(query.order_by(Job.created_at.desc(), Job.id).offset((page - 1) * page_size).limit(page_size)).all()
    dataset_labels = job_dataset_labels(db, jobs)
    return {"items": [job_summary(job, dataset_labels.get(str(job.id))) for job in jobs], "page": page, "page_size": page_size,
            "total": total, "has_more": page * page_size < total}


@router.get("/jobs/{job_id}")
def get_job(job_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict:
    ensure_user(db, principal)
    job = db.scalar(scoped_job_query(db, principal).where(Job.id == job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    steps = db.scalars(select(JobStep).where(JobStep.job_id == job.id).order_by(JobStep.created_at, JobStep.id)).all()
    return {**job_summary(job, job_dataset_labels(db, [job]).get(str(job.id))), "steps": [{"id": str(step.id), "name": step.name, "status": step.status,
                                           "safe_summary": step.safe_summary,
                                           "created_at": step.created_at.isoformat() if step.created_at else None}
                                          for step in steps]}
