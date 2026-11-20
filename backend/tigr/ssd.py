"""QA self servicing data, integrated with TIGR grants, jobs and datasets."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from tigr.access import ensure_user, membership_role, record_audit, workspace_role
from tigr.config import get_settings
from tigr.connection_models import (
    ConnectionStructureSnapshot,
    LockPropagationConfiguration,
    WorkspaceConnection,
    secret_reference_for_database,
)
from tigr.connection_testing import database_url, secret_resolver
from tigr.dataset_schemas import DraftDataSet, LineageEventCreate, Provenance, SchemaField
from tigr.dataset_services import (
    append_lineage_event,
    create_initial_version,
    create_version,
    read_dataset_records,
    record_generated_artifact,
    record_status_change,
    record_validation,
    serialize_dataset_records,
    store_dataset_records,
)
from tigr.datasets import DataSet, DataSetVersion
from tigr.db import SessionLocal
from tigr.iam import AccessGrant
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.jobs import Job, JobStep, OutboxEvent
from tigr.secrets import _fernet
from tigr.schema_records import DatasetPublication
from tigr.ssd_models import SsdDraft, SsdExecution, SsdRevision
from tigr.ssd_sql import CONNECTOR_DIALECTS, inspect_sql

router = APIRouter(prefix="/api/v1", tags=["self servicing data"])
TTL = timedelta(minutes=get_settings().ssd_temporary_ttl_minutes)
ROW_LIMIT = get_settings().ssd_max_rows


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def seal(value) -> str:
    return _fernet().encrypt(json.dumps(value, default=str).encode()).decode()


def unseal(value: str):
    return json.loads(_fernet().decrypt(value.encode()))


def qa(db, principal, workspace_id):
    workspace_role(db, principal, workspace_id)
    if (
        principal.actor_type != "human"
        or membership_role(db, principal.subject, workspace_id) != HumanRole.QA_ENGINEER
    ):
        raise HTTPException(403, "Self Servicing Data requires a QA Engineer workspace role")


def grants(db, principal, workspace_id, connection_id, *, refresh=False):
    if refresh:
        workspace_role(db, principal, workspace_id)
    else:
        qa(db, principal, workspace_id)
    now = datetime.now(UTC)
    return list(
        db.scalars(
            select(AccessGrant).where(
                AccessGrant.workspace_id == workspace_id,
                AccessGrant.subject == principal.subject,
                AccessGrant.scope_type == "connection",
                AccessGrant.scope_id == str(connection_id),
                AccessGrant.revoked_at.is_(None),
                AccessGrant.valid_from <= now,
                (AccessGrant.valid_until.is_(None) | (AccessGrant.valid_until > now)),
            )
        ).all()
    )


def connection_stamp(connection):
    return digest(
        {
            "config": connection.connector_config,
            "secret": connection.secret_reference,
            "bindings": [
                (b.database_name, b.secret_reference, str(b.updated_at))
                for b in connection.database_credentials
            ],
            "updated": connection.updated_at,
            "structure": connection.structure_preview,
            "locking": connection.entity_locking_enabled,
        }
    )


def source_lock_metadata(db, connection):
    if not connection.entity_locking_enabled or not get_settings().entity_locking_enabled:
        return {}
    snapshot = db.scalar(select(ConnectionStructureSnapshot).where(
        ConnectionStructureSnapshot.connection_id == connection.id,
    ).order_by(ConnectionStructureSnapshot.version_number.desc()).limit(1))
    configuration = db.scalar(select(LockPropagationConfiguration).where(
        LockPropagationConfiguration.connection_id == connection.id,
        LockPropagationConfiguration.structure_snapshot_id == snapshot.id,
        LockPropagationConfiguration.status == "approved",
    ).order_by(LockPropagationConfiguration.version_number.desc()).limit(1)) if snapshot else None
    if configuration is None:
        raise HTTPException(409, "SSD_LOCK_CONFIGURATION_REQUIRED: An Admin or TDE must approve entity keys for the current connection structure")
    namespace = (snapshot.structure or {}).get("source_identity_namespace") or f"connection:{connection.id}"
    if namespace != f"connection:{connection.id}":
        raise HTTPException(409, "SSD_LOCK_CONFIGURATION_REQUIRED: Unsupported source identity namespace")
    return {
        "source_identity_namespace": namespace,
        "structure_snapshot_id": str(snapshot.id),
        "lock_configuration_id": str(configuration.id),
    }


def guard_source(db, connection_id, actor_id):
    from tigr.entity_claims import assert_source_namespace_available

    try:
        # This transaction lock is shared with entity-claim acquisition. Keep it
        # until the source transaction has committed or rolled back.
        assert_source_namespace_available(db, connection_id, actor_id)
    except HTTPException as exc:
        raise HTTPException(409, "SSD_LOCK_CONFLICT: Another user holds an active source-entity lock on this connection. Retry after release or expiry; arbitrary SQL is checked conservatively across the connection.") from exc


def authorize(db, principal, draft, revision, *, for_draft=False, check_locks=True, refresh=False):
    connection = db.get(WorkspaceConnection, revision.connection_id)
    if (
        connection is None
        or connection.workspace_id != draft.workspace_id
        or connection.status != "enabled"
        or connection.connector_type not in CONNECTOR_DIALECTS
    ):
        raise HTTPException(
            403, "SSD_OPERATION_NOT_ALLOWED: Enabled relational database connection required"
        )
    config = connection.connector_config or {}
    databases = config.get("databases") or [
        config.get("database_name") or config.get("initial_database")
    ]
    if revision.database not in databases:
        raise HTTPException(403, "SSD_OPERATION_NOT_ALLOWED: Database binding is not configured")
    if for_draft:
        active = grants(db, principal, draft.workspace_id, connection.id, refresh=refresh)
        if not any(
            set(g.permissions) & {"ssd:select", "ssd:insert", "ssd:update", "ssd:delete"}
            for g in active
        ):
            raise HTTPException(
                403, "SSD_OPERATION_NOT_ALLOWED: No active connection operation grant"
            )
        return connection, {}
    inputs = unseal(revision.secured_inputs)
    try:
        info = inspect_sql(
            inputs["sql"],
            inputs["parameters"],
            connector_type=connection.connector_type,
            allow_database_select_functions=(connection.connector_type == "postgresql"),
        )
    except Exception as exc:
        message = (
            str(exc)
            if isinstance(exc, ValueError) and str(exc).startswith("SSD_")
            else "SSD_UNSUPPORTED_STATEMENT: SQL or parameters are outside the supported grammar"
        )
        error = HTTPException(422, message)
        diagnostics = getattr(exc, "errors", None)
        if isinstance(diagnostics, list) and diagnostics and isinstance(diagnostics[0], dict):
            error.ssd_location = (
                int(diagnostics[0].get("line", 1)),
                int(diagnostics[0].get("col", 1)),
            )
        raise error from exc
    active = grants(db, principal, draft.workspace_id, connection.id, refresh=refresh)
    permissions = {p for grant in active for p in grant.permissions}
    if refresh and workspace_role(db, principal, draft.workspace_id) in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER):
        permissions = {"ssd:select", "ssd:insert", "ssd:update", "ssd:delete"}
    if any(f"ssd:{op}" not in permissions for op in info["operations"]):
        raise HTTPException(
            403, "SSD_OPERATION_NOT_ALLOWED: Required connection operations are not granted"
        )
    if "select" in info["operations"] and (
        connection.operation_scope not in ("read_only", "read_write")
        or connection.direction not in ("source", "both")
    ):
        raise HTTPException(
            403, "SSD_OPERATION_NOT_ALLOWED: This query requires a readable source binding"
        )
    if info["operation"] == "select":
        if connection.operation_scope not in (
            "read_only",
            "read_write",
        ) or connection.direction not in ("source", "both"):
            raise HTTPException(403, "SSD_OPERATION_NOT_ALLOWED: Connection is not readable")
    elif connection.operation_scope not in ("write_only", "read_write"):
        raise HTTPException(403, "SSD_OPERATION_NOT_ALLOWED: Connection is not writable")
    if check_locks and info["operation"] in {"insert", "update", "delete"}:
        guard_source(db, connection.id, principal.subject)
    lock_metadata = source_lock_metadata(db, connection)
    discovered = (connection.structure_preview or {}).get("objects", [])
    for schema, table in info["objects"]:
        if not any(
            o.get("database") == revision.database
            and o.get("schema") == schema
            and o.get("name", "").split(".")[-1] == table
            for o in discovered
        ):
            raise HTTPException(
                403,
                "SSD_OPERATION_NOT_ALLOWED: Referenced object is outside the discovered binding",
            )
    return connection, {
        **info,
        "connection_stamp": connection_stamp(connection),
        "lock_metadata": lock_metadata,
        "grant_stamp": digest(
            sorted((str(g.id), g.permissions, str(g.valid_until)) for g in active)
        ),
    }


def validate_output(rows, fields):
    from types import SimpleNamespace

    from tigr.privacy_api import _validate_masked_rows

    version = SimpleNamespace(
        schema_scope=fields, selected_fields=[f["field"] for f in fields], validation_evidence={}
    )
    if _validate_masked_rows(rows, version):
        raise HTTPException(
            409, "SSD_OUTPUT_VALIDATION_FAILED: Query output does not satisfy its schema"
        )


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=2000)
    visibility: str = Field(default="private", pattern="^(private|public)$")
    connection_id: UUID
    database: str = Field(min_length=1, max_length=128)
    sql: str = Field(min_length=1, max_length=50000)
    parameters: dict = Field(default_factory=dict, max_length=100)
    expected_revision: int = Field(default=1, ge=1)


class ActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=180)
    confirm_mutation: bool = False
    acknowledge_unknown: bool = False
    reconciliation_reference: str | None = Field(
        default=None, max_length=180, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/#-]*$"
    )


def owned(db, principal, draft_id, expected=None):
    draft = db.scalar(select(SsdDraft).where(SsdDraft.id == draft_id).with_for_update())
    if not draft or draft.owner_id != principal.subject:
        raise HTTPException(404, "Draft not found")
    qa(db, principal, draft.workspace_id)
    if expected is not None and expected != draft.revision:
        raise HTTPException(409, "SSD_QUERY_STALE: Reload the current draft revision")
    revision = db.scalar(
        select(SsdRevision).where(
            SsdRevision.draft_id == draft.id, SsdRevision.number == draft.revision
        )
    )
    return draft, revision


def draft_out(db, draft, revision):
    executions = db.execute(
        select(SsdExecution, Job.status)
        .join(Job)
        .where(SsdExecution.revision_id == revision.id)
        .order_by(Job.created_at, Job.id)
    ).all()
    inputs = unseal(revision.secured_inputs)
    return {
        "id": str(draft.id),
        "workspace_id": str(draft.workspace_id),
        "name": draft.name,
        "description": draft.description,
        "visibility": draft.visibility,
        "revision": draft.revision,
        "state": draft.state,
        "generation_settings": draft.generation_settings,
        "connection_id": str(revision.connection_id),
        "database": revision.database,
        **inputs,
        "validation": revision.validation,
        "dataset_id": str(draft.dataset_id)
        if draft.dataset_id and draft.generated_revision == draft.revision
        else None,
        "dataset_version_number": draft.dataset_version_number
        if draft.generated_revision == draft.revision
        else None,
        "executions": [
            {
                "id": str(e.id),
                "job_id": str(e.job_id),
                "kind": e.kind,
                "job_status": job_status,
                "outcome": e.outcome,
                "evidence": e.evidence,
            }
            for e, job_status in executions
        ],
    }


@router.get("/workspaces/{workspace_id}/ssd/connections")
def connections(
    workspace_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal),
    include_unavailable: bool = False,
):
    qa(db, principal, workspace_id)
    result = []
    for c in db.scalars(
        select(WorkspaceConnection).where(
            WorkspaceConnection.workspace_id == workspace_id,
        )
    ).all():
        permissions = {p for g in grants(db, principal, workspace_id, c.id) for p in g.permissions}
        granted = [op for op in ("select", "insert", "update", "delete") if f"ssd:{op}" in permissions]
        if not granted:
            continue
        ops = [
            op
            for op in ("select", "insert", "update", "delete")
            if f"ssd:{op}" in permissions
            and (
                c.operation_scope in ("read_only", "read_write")
                and c.direction in ("source", "both")
                if op == "select"
                else c.operation_scope in ("write_only", "read_write")
            )
        ]
        reasons = []
        if c.status != "enabled":
            reasons.append("The connection is not enabled. Ask an Admin to review its status.")
        if c.connector_type not in CONNECTOR_DIALECTS:
            reasons.append("Self Servicing Data requires a transactional PostgreSQL, MySQL, Oracle or SQL Server connection.")
        try:
            source_lock_metadata(db, c)
        except HTTPException as exc:
            reasons.append(str(exc.detail))
        if not ops:
            reasons.append("The granted operations are incompatible with this connection's read/write configuration. Ask an Admin to review the connection and grant.")
        if reasons:
            if include_unavailable:
                result.append({
                    "id": str(c.id), "name": c.name, "environment": c.environment,
                    "connector": c.connector_type, "operations": [],
                    "granted_operations": granted, "available": False,
                    "unavailable_reasons": reasons, "databases": [], "schemas": {},
                })
            continue
        if ops:
            cfg = c.connector_config or {}
            result.append(
                {
                    "id": str(c.id),
                    "name": c.name,
                    "environment": c.environment,
                    "connector": c.connector_type,
                    "operations": ops,
                    "granted_operations": granted,
                    "available": True,
                    "unavailable_reasons": [],
                    "entity_locking_enabled": bool(c.entity_locking_enabled and get_settings().entity_locking_enabled),
                    "operation_scope": c.operation_scope,
                    "schemas": {
                        database: sorted(
                            {
                                o.get("schema")
                                for o in (c.structure_preview or {}).get("objects", [])
                                if o.get("database") == database and o.get("schema")
                            }
                        )
                        for database in (
                            cfg.get("databases")
                            or [cfg.get("database_name") or cfg.get("initial_database")]
                        )
                    },
                    "databases": cfg.get("databases")
                    or [cfg.get("database_name") or cfg.get("initial_database")],
                }
            )
    return result


@router.get("/workspaces/{workspace_id}/ssd/drafts")
def list_drafts(
    workspace_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
):
    qa(db, principal, workspace_id)
    return [
        {"id": str(d.id), "name": d.name, "state": d.state}
        for d in db.scalars(
            select(SsdDraft)
            .where(SsdDraft.workspace_id == workspace_id, SsdDraft.owner_id == principal.subject)
            .order_by(SsdDraft.created_at.desc())
        ).all()
    ]


def save_revision(db, draft, payload):
    inputs = {"sql": payload.sql, "parameters": payload.parameters}
    revision = SsdRevision(
        id=uuid4(),
        draft_id=draft.id,
        number=draft.revision,
        connection_id=payload.connection_id,
        database=payload.database,
        secured_inputs=seal(inputs),
        input_hash=digest(inputs),
        validation={},
    )
    db.add(revision)
    return revision


@router.post("/workspaces/{workspace_id}/ssd/drafts", status_code=201)
def create_draft(
    workspace_id: UUID,
    payload: DraftInput,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    qa(db, principal, workspace_id)
    ensure_user(db, principal)
    draft = SsdDraft(
        id=uuid4(),
        workspace_id=workspace_id,
        owner_id=principal.subject,
        name=payload.name,
        description=payload.description,
        visibility=payload.visibility,
        revision=1,
        state="draft",
        generation_settings={"maximum_records": ROW_LIMIT, "output_format": "json"},
    )
    db.add(draft)
    db.flush()
    revision = save_revision(db, draft, payload)
    authorize(db, principal, draft, revision, for_draft=True)
    record_audit(
        db,
        principal,
        request.state.request_id,
        "ssd.draft_created",
        "ssd_draft",
        str(draft.id),
        workspace_id,
        HumanRole.QA_ENGINEER,
        {"revision": 1, "visibility": draft.visibility},
    )
    db.commit()
    return draft_out(db, draft, revision)


@router.get("/ssd/drafts/{draft_id}")
def get_draft(
    draft_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
):
    draft, revision = owned(db, principal, draft_id)
    return draft_out(db, draft, revision)


@router.get("/ssd/drafts/{draft_id}/revisions")
def revisions(
    draft_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
):
    draft, _ = owned(db, principal, draft_id)
    result = []
    for revision in db.scalars(
        select(SsdRevision)
        .where(SsdRevision.draft_id == draft.id)
        .order_by(SsdRevision.number.desc())
    ).all():
        executions = db.scalars(
            select(SsdExecution).where(SsdExecution.revision_id == revision.id)
        ).all()
        result.append(
            {
                "revision": revision.number,
                "sql": unseal(revision.secured_inputs)["sql"],
                "query_hash": revision.input_hash,
                "operation": revision.validation.get("operation"),
                "executions": [
                    {"job_id": str(e.job_id), "outcome": e.outcome, "kind": e.kind}
                    for e in executions
                ],
            }
        )
    return result


@router.patch("/ssd/drafts/{draft_id}")
def edit_draft(
    draft_id: UUID,
    payload: DraftInput,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    draft, _ = owned(db, principal, draft_id, payload.expected_revision)
    draft.revision += 1
    draft.name, draft.description, draft.visibility = (
        payload.name,
        payload.description,
        payload.visibility,
    )
    draft.state = "draft"
    revision = save_revision(db, draft, payload)
    authorize(db, principal, draft, revision, for_draft=True)
    record_audit(
        db,
        principal,
        request.state.request_id,
        "ssd.revision_created",
        "ssd_draft",
        str(draft.id),
        draft.workspace_id,
        HumanRole.QA_ENGINEER,
        {"revision": draft.revision, "query_hash": revision.input_hash},
    )
    db.commit()
    return draft_out(db, draft, revision)


class MetadataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=2000)
    visibility: str = Field(pattern="^(private|public)$")

    maximum_records: int | None = Field(default=None, ge=1, le=10000)
    output_format: str | None = Field(default=None, pattern="^(json|csv)$")


@router.patch("/ssd/drafts/{draft_id}/metadata")
def metadata(
    draft_id: UUID,
    payload: MetadataInput,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    draft, _ = owned(db, principal, draft_id, payload.expected_revision)
    if payload.name is not None:
        draft.name = payload.name
    draft.description, draft.visibility = payload.description, payload.visibility
    settings = dict(draft.generation_settings or {})
    if payload.maximum_records is not None:
        if payload.maximum_records > ROW_LIMIT:
            raise HTTPException(422, "Requested count exceeds the current SSD row limit")
        settings["maximum_records"] = payload.maximum_records
    if payload.output_format is not None:
        settings["output_format"] = payload.output_format
    draft.generation_settings = settings
    record_audit(
        db,
        principal,
        request.state.request_id,
        "ssd.draft_metadata_updated",
        "ssd_draft",
        str(draft.id),
        draft.workspace_id,
        HumanRole.QA_ENGINEER,
        {"revision": draft.revision, "visibility": draft.visibility},
    )
    db.commit()
    return {"revision": draft.revision, "visibility": draft.visibility}


@router.post("/ssd/jobs/{job_id}/cancel", status_code=202)
def cancel(
    job_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job or not job.job_type.startswith("ssd_") or job.actor_id != principal.subject:
        raise HTTPException(404, "Job not found")
    qa(db, principal, UUID(job.workspace_id))
    if job.status == "queued":
        job.status, job.ended_at = "cancelled", datetime.now(UTC)
        job.safe_summary = "Cancelled before source dispatch"
    elif job.status == "running":
        job.status = "cancel_requested"
        job.safe_summary = "Cancellation requested; a committed source transaction cannot be undone"
    record_audit(
        db,
        principal,
        request.state.request_id,
        "ssd.cancellation_requested",
        "job",
        str(job.id),
        UUID(job.workspace_id),
        HumanRole.QA_ENGINEER,
    )
    db.commit()
    return {"job_id": str(job.id), "status": job.status}


@router.post("/ssd/drafts/{draft_id}/validate")
def validate(
    draft_id: UUID,
    payload: ActionInput,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    draft, revision = owned(db, principal, draft_id, payload.expected_revision)
    connection, info = authorize(db, principal, draft, revision)
    if info["operation"] == "select":
        inputs = unseal(revision.secured_inputs)
        try:
            _validate_read_source_sql(connection, revision, inputs)
        except ValueError as exc:
            raise HTTPException(422, f"SSD_UNSUPPORTED_STATEMENT: {exc}") from exc
        except Exception as exc:
            raise HTTPException(
                503,
                "SSD_SOURCE_UNAVAILABLE: Could not validate this query against the selected database connection",
            ) from exc
    revision.validation = {
        **info,
        "reference": str(uuid4()),
        "input_hash": revision.input_hash,
        "actor": principal.subject,
        "workspace": str(draft.workspace_id),
        "row_limit": ROW_LIMIT,
        "sql_hash": digest(unseal(revision.secured_inputs)["sql"]),
        "parameters_hash": digest(unseal(revision.secured_inputs)["parameters"]),
        "connection_id": str(revision.connection_id),
        "database": revision.database,
    }
    draft.state = "validated"
    db.commit()
    return revision.validation


def enqueue(db, principal, request, draft, revision, payload, kind):
    if not get_settings().ssd_enabled:
        raise HTTPException(503, "Self Servicing Data execution is disabled")
    _, current = authorize(db, principal, draft, revision)
    validation = revision.validation or {}
    if not validation or any(validation.get(k) != v for k, v in current.items()):
        raise HTTPException(
            409, "SSD_QUERY_STALE: Validate this query with current connection access"
        )
    existing = db.scalar(
        select(SsdExecution).where(
            SsdExecution.revision_id == revision.id,
            SsdExecution.kind == kind,
            SsdExecution.idempotency_key == payload.idempotency_key,
        )
    )
    if kind == "query" and current["operation"] != "select":
        if not payload.confirm_mutation:
            raise HTTPException(
                422, "Explicit confirmation of real source record changes is required"
            )
        # Even a new browser idempotency key cannot replay the same mutation revision.
        existing = db.scalar(
            select(SsdExecution).where(
                SsdExecution.revision_id == revision.id, SsdExecution.kind == "query"
            )
        )
    parent = None
    if kind != "query":
        parent = db.scalar(
            select(SsdExecution)
            .join(Job)
            .where(
                SsdExecution.revision_id == revision.id,
                SsdExecution.kind == ("query" if kind == "sample" or current["operation"] != "select" else "sample"),
                Job.status == "succeeded",
            )
            .order_by(Job.created_at.desc())
        )
        if parent is None or (kind == "sample" and current["operation"] != "select") or (current["operation"] != "select" and parent.outcome != "committed"):
            raise HTTPException(
                409,
                "Execute the query successfully before saving a dataset; only SELECT queries support samples",
            )
    if existing:
        return {
            "job_id": str(existing.job_id),
            "execution_id": str(existing.id),
            "revision": draft.revision,
            "status_url": f"/api/v1/jobs/{existing.job_id}",
            "state": draft.state,
        }
    prior_unknown = []
    if kind == "query" and current["operation"] != "select":
        prior_unknown = list(
            db.scalars(
                select(SsdExecution.id)
                .join(SsdRevision)
                .where(SsdRevision.draft_id == draft.id, SsdExecution.outcome == "outcome_unknown")
            ).all()
        )
        if prior_unknown and (
            not payload.acknowledge_unknown or not payload.reconciliation_reference
        ):
            raise HTTPException(
                409,
                "SSD_EXECUTION_OUTCOME_UNKNOWN: Investigate prior uncertain commits, supply a source evidence reference, and explicitly acknowledge this new mutation intent",
            )
    job = Job(
        id=uuid4(),
        job_type=f"ssd_{kind}",
        workspace_id=str(draft.workspace_id),
        scope_key=f"ssd:{revision.id}:{kind}",
        idempotency_key=payload.idempotency_key,
        actor_type="human",
        actor_id=principal.subject,
        actor_role=HumanRole.QA_ENGINEER.value,
        request_id=request.state.request_id,
        created_at=datetime.now(UTC),
        status="queued",
        safe_summary="SSD work queued",
    )
    db.add(job)
    db.flush()
    execution = SsdExecution(
        id=uuid4(),
        revision_id=revision.id,
        job_id=job.id,
        parent_id=parent.id if parent else None,
        kind=kind,
        idempotency_key=payload.idempotency_key,
        evidence={
            "validation": validation,
            "privacy_mode": "raw_preview",
            "prior_unknown_execution_ids": [str(e) for e in prior_unknown],
            "reconciliation_reference": payload.reconciliation_reference if prior_unknown else None,
        },
        outcome="not_dispatched",
    )
    db.add(execution)
    db.flush()
    db.add(
        OutboxEvent(
            id=uuid4(),
            aggregate_type="ssd",
            aggregate_id=str(draft.id),
            workspace_id=str(draft.workspace_id),
            event_type="ssd.work_requested",
            payload={"execution_id": str(execution.id), "job_id": str(job.id)},
            request_id=request.state.request_id,
        )
    )
    draft.state = {"query": "executing", "sample": "sampling", "dataset": "generating"}[kind]
    record_audit(
        db,
        principal,
        request.state.request_id,
        f"ssd.{kind}_requested",
        "ssd_draft",
        str(draft.id),
        draft.workspace_id,
        HumanRole.QA_ENGINEER,
        {"revision": draft.revision, "job_id": str(job.id)},
    )
    if prior_unknown:
        record_audit(
            db,
            principal,
            request.state.request_id,
            "ssd.new_intent_after_uncertain_commit",
            "ssd_execution",
            str(execution.id),
            draft.workspace_id,
            HumanRole.QA_ENGINEER,
            {
                "prior_execution_ids": [str(e) for e in prior_unknown],
                "source_evidence_reference": payload.reconciliation_reference,
            },
        )
    db.commit()
    return {
        "job_id": str(job.id),
        "execution_id": str(execution.id),
        "revision": draft.revision,
        "status_url": f"/api/v1/jobs/{job.id}",
        "state": draft.state,
    }


@router.post("/ssd/drafts/{draft_id}/execute", status_code=202)
def execute(
    draft_id: UUID,
    payload: ActionInput,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    return enqueue(
        db,
        principal,
        request,
        *owned(db, principal, draft_id, payload.expected_revision),
        payload,
        "query",
    )


@router.post("/ssd/drafts/{draft_id}/sample", status_code=202)
def sample(
    draft_id: UUID,
    payload: ActionInput,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    return enqueue(
        db,
        principal,
        request,
        *owned(db, principal, draft_id, payload.expected_revision),
        payload,
        "sample",
    )


@router.post("/ssd/drafts/{draft_id}/datasets", status_code=202)
def generate(
    draft_id: UUID,
    payload: ActionInput,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    return enqueue(
        db,
        principal,
        request,
        *owned(db, principal, draft_id, payload.expected_revision),
        payload,
        "dataset",
    )


def output(db, principal, draft, revision, execution):
    _, current = authorize(db, principal, draft, revision)
    if not execution or not execution.secured_output or execution.expires_at <= datetime.now(UTC):
        raise HTTPException(409, "SSD_SAMPLE_EXPIRED: Generate a fresh SELECT sample")
    result = unseal(execution.secured_output)
    if any(
        execution.evidence.get("output_validation", execution.evidence["validation"]).get(k) != v
        for k, v in current.items()
    ):
        raise HTTPException(409, "SSD_QUERY_STALE: Connection or authorization changed")
    return result


@router.get("/ssd/drafts/{draft_id}/sample")
def get_sample(
    draft_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
):
    draft, revision = owned(db, principal, draft_id)
    execution = db.scalar(
        select(SsdExecution)
        .join(Job)
        .where(
            SsdExecution.revision_id == revision.id,
            SsdExecution.kind == "sample",
            Job.status == "succeeded",
        )
        .order_by(Job.created_at.desc())
    )
    result = output(db, principal, draft, revision, execution)
    return {
        "fields": result["fields"],
        "privacy": result["privacy"],
        "rows": result["rows"][:5],
        "revision": draft.revision,
        "expires_at": execution.expires_at,
        "generated_at": execution.evidence.get("ended_at"),
    }


class SourceNotDispatched(Exception):
    pass


class SourceRolledBack(Exception):
    pass


def _source_engine(connection, database):
    reference = secret_reference_for_database(connection, database)
    dsn = secret_resolver().resolve(reference or "")
    url = make_url(dsn)
    expected_drivers = {
        "postgresql": {"postgresql", "postgresql+psycopg"},
        "mysql": {"mysql", "mysql+pymysql"},
        "oracle": {"oracle", "oracle+oracledb"},
        "mssql": {"mssql", "mssql+pymssql"},
    }
    if url.drivername not in expected_drivers.get(connection.connector_type, set()):
        raise ValueError("SSD_OPERATION_NOT_ALLOWED: Connection credentials do not match the connector type")
    connect_args = {
        "postgresql": {"connect_timeout": 5},
        "mysql": {"connect_timeout": 5, "read_timeout": 20, "write_timeout": 20},
        "oracle": {},
        "mssql": {"login_timeout": 5, "timeout": 20},
    }[connection.connector_type]
    return create_engine(
        database_url(url, connection.connector_type, database),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args=connect_args,
    )


def _begin_source_transaction(source, connector_type, operation):
    if connector_type == "mysql" and operation == "select":
        # MySQL applies this setting to the next transaction only.
        source.exec_driver_sql("SET TRANSACTION READ ONLY")
        source.commit()
    transaction = source.begin()
    if connector_type == "postgresql":
        if operation == "select":
            source.exec_driver_sql("SET TRANSACTION READ ONLY")
        source.exec_driver_sql("SET LOCAL statement_timeout = '10000ms'")
        source.exec_driver_sql("SET LOCAL lock_timeout = '2000ms'")
        source.exec_driver_sql("SET LOCAL search_path = pg_catalog")
        source.exec_driver_sql("SET LOCAL standard_conforming_strings = on")
    elif connector_type == "mysql":
        source.exec_driver_sql("SET SESSION innodb_lock_wait_timeout = 2")
    elif connector_type == "oracle":
        if operation == "select":
            source.exec_driver_sql("SET TRANSACTION READ ONLY")
        raw = source.connection.driver_connection
        if hasattr(raw, "call_timeout"):
            raw.call_timeout = 10000
    elif connector_type == "mssql":
        source.exec_driver_sql("SET LOCK_TIMEOUT 2000")
        source.exec_driver_sql("SET XACT_ABORT ON")
    return transaction


def _validate_read_source_sql(connection, revision, inputs):
    engine = _source_engine(connection, revision.database)
    try:
        with engine.connect() as source:
            if connection.connector_type == "oracle":
                cursor = source.connection.driver_connection.cursor()
                try:
                    cursor.prepare(inputs["sql"])
                    expected = {name.casefold() for name in inputs["parameters"]}
                    actual = {name.casefold() for name in cursor.bindnames()}
                    if expected != actual:
                        raise ValueError("SSD_UNSUPPORTED_STATEMENT: Bind scalar values to exactly the named parameters")
                finally:
                    cursor.close()
                return
            if connection.connector_type == "mssql":
                source.exec_driver_sql("SET SHOWPLAN_XML ON")
                try:
                    source.execute(text(inputs["sql"]), inputs["parameters"]).all()
                finally:
                    source.exec_driver_sql("SET SHOWPLAN_XML OFF")
                return
            transaction = _begin_source_transaction(source, connection.connector_type, "select")
            try:
                source.execute(text("EXPLAIN " + inputs["sql"]), inputs["parameters"]).all()
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def _validate_source_objects(source, connection, revision, info):
    if connection.connector_type == "postgresql":
        for schema, table in info["objects"]:
            metadata = source.execute(
                text("""SELECT c.relkind, c.relhasrules FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=:schema AND c.relname=:table"""),
                {"schema": schema, "table": table},
            ).first()
            if not metadata or metadata[0] not in ("r", "p") or metadata[1]:
                raise ValueError("SSD_UNSUPPORTED_STATEMENT: Views and foreign objects require verified lineage")
            custom_types = source.execute(
                text("""SELECT EXISTS (
                SELECT 1 FROM pg_catalog.pg_attribute a
                JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
                JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
                WHERE n.nspname=:schema AND c.relname=:table AND a.attnum>0
                  AND NOT a.attisdropped AND tn.nspname <> 'pg_catalog')"""),
                {"schema": schema, "table": table},
            ).scalar()
            if custom_types:
                raise ValueError("SSD_UNSUPPORTED_STATEMENT: Custom source types require verified function dependencies")
        return
    inspector = inspect(source)
    for schema, table in info["objects"]:
        names = inspector.get_table_names(schema=schema)
        if table.casefold() not in {name.casefold() for name in names}:
            raise ValueError("SSD_OPERATION_NOT_ALLOWED: Referenced object is not a discovered base table")
        if connection.connector_type == "mysql" and info["operation"] in {"insert", "update", "delete"}:
            storage_engine = source.execute(
                text("""SELECT ENGINE FROM information_schema.tables
                WHERE table_schema=:schema AND table_name=:table"""),
                {"schema": schema, "table": table},
            ).scalar()
            if str(storage_engine or "").casefold() != "innodb":
                raise ValueError("SSD_OPERATION_NOT_ALLOWED: MySQL mutations require InnoDB tables for transaction rollback")


def _fallback_source_field(connection, revision, info, field_name):
    matches = []
    for schema, table in info["objects"]:
        for obj in (connection.structure_preview or {}).get("objects", []):
            if (obj.get("database") != revision.database or obj.get("schema") != schema
                    or obj.get("name", "").split(".")[-1].casefold() != table.casefold()):
                continue
            if any(str(item.get("name", "")).casefold() == field_name.casefold() for item in obj.get("fields", [])):
                matches.append(f"{table}.{field_name}" if connection.connector_type == "mysql" else f"{schema}.{table}.{field_name}")
    return matches[0] if len(set(matches)) == 1 else None


def run_source(connection, revision, info, before_commit=None):
    """One external transaction. DML output is deliberately not a reusable recipe."""
    try:
        engine = _source_engine(connection, revision.database)
    except Exception as exc:
        raise SourceNotDispatched(
            "SSD_SOURCE_UNAVAILABLE: No statement was dispatched; review the connection and save a new query revision"
        ) from exc
    try:
        try:
            source_connection = engine.connect()
        except Exception as exc:
            raise SourceNotDispatched(
                "SSD_SOURCE_UNAVAILABLE: No statement was dispatched; review the connection and save a new query revision"
            ) from exc
        with source_connection as source:
            transaction = _begin_source_transaction(source, connection.connector_type, info["operation"])
            try:
                result = _source_statement(source, connection, revision, info)
                if before_commit:
                    before_commit()
            except Exception as exc:
                transaction.rollback()
                reason = (
                    str(exc)
                    if isinstance(exc, ValueError) and str(exc).startswith("SSD_")
                    else str(exc.detail)
                    if isinstance(exc, HTTPException) and str(exc.detail).startswith("SSD_")
                    else "Source transaction rolled back. Check SQL, source privileges and binding configuration"
                )
                raise SourceRolledBack(reason) from exc
            transaction.commit()  # Errors here cannot prove whether a commit happened.
            return result
    finally:
        engine.dispose()


def _source_statement(source, connection, revision, info):
    _validate_source_objects(source, connection, revision, info)
    inputs = unseal(revision.secured_inputs)
    result = source.execution_options(stream_results=info["operation"] == "select").execute(
        text(inputs["sql"]), inputs["parameters"]
    )
    affected = result.rowcount if info["operation"] != "select" else 0
    if affected < 0 or affected > ROW_LIMIT:
        raise ValueError("SSD_ROW_LIMIT: Mutation rolled back")
    rows, fields = [], []
    if info["operation"] == "select":
        names = list(result.keys())
        if (
            not names
            or len(names) > 200
            or len(set(names)) != len(names)
            or any(len(n) > 180 for n in names)
        ):
            raise ValueError("SSD_UNSUPPORTED_STATEMENT: Output requires unique column names")
        description = list(result.cursor.description or [])
        type_oids = [
            int(column.type_code if hasattr(column, "type_code") else column[1])
            for column in description
        ] if connection.connector_type == "postgresql" else []
        origins = []
        if connection.connector_type == "postgresql":
            origins = [
                (int(getattr(column, "table_oid", 0) or 0), int(getattr(column, "table_column", 0) or 0))
                for column in description
            ]
            pg_result = getattr(result.cursor, "pgresult", None)
            if pg_result is not None:
                origins = [(pg_result.ftable(index), pg_result.ftablecol(index)) for index in range(len(description))]
        byte_count = 0
        for row in result.mappings():
            values = dict(row)
            byte_count += len(json.dumps(values, default=str).encode())
            if byte_count > 5 * 1024 * 1024:
                raise ValueError("SSD_RESULT_LIMIT")
            rows.append(values)
            if len(rows) >= ROW_LIMIT:
                break
        result.close()
        fields = []
        if connection.connector_type == "postgresql":
            types = dict(source.execute(
                text("SELECT oid::int, format_type(oid, NULL) FROM pg_catalog.pg_type WHERE oid = ANY(CAST(:ids AS oid[]))"),
                {"ids": type_oids},
            ).all())
            fields = [{"field": name, "data_type": types.get(oid, "text")} for name, oid in zip(names, type_oids)]
        else:
            for name, column in zip(names, description):
                discovered_types = [
                    str(field.get("type") or "text")
                    for schema, table in info["objects"]
                    for obj in (connection.structure_preview or {}).get("objects", [])
                    if obj.get("database") == revision.database and obj.get("schema") == schema
                    and obj.get("name", "").split(".")[-1].casefold() == table.casefold()
                    for field in obj.get("fields", []) if str(field.get("name", "")).casefold() == name.casefold()
                ]
                type_code = column.type_code if hasattr(column, "type_code") else column[1]
                fallback_type = getattr(type_code, "__name__", None) or str(type_code)
                fields.append({"field": name, "data_type": discovered_types[0] if len(set(discovered_types)) == 1 else fallback_type})
        source_fields = {}
        if connection.connector_type == "postgresql" and any(oid for oid, _ in origins):
            origin_rows = source.execute(
                text("""SELECT a.attrelid::int, a.attnum,
                n.nspname || '.' || c.relname || '.' || a.attname
                FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE a.attrelid = ANY(CAST(:oids AS oid[])) AND a.attnum = ANY(CAST(:nums AS int[]))"""),
                {"oids": [oid for oid, _ in origins], "nums": [num for _, num in origins]},
            ).all()
            source_fields = {(oid, num): path for oid, num, path in origin_rows}
        dependencies = ",".join(info.get("columns", []))
        if len(dependencies) > 450:
            raise ValueError(
                "SSD_UNSUPPORTED_STATEMENT: Expression dependencies exceed supported metadata limits"
            )
        for index, field in enumerate(fields):
            if connection.connector_type == "postgresql":
                origin = origins[index]
                field["source_field"] = source_fields.get(origin, f"expression:{dependencies}")
            else:
                field["source_field"] = _fallback_source_field(connection, revision, info, field["field"]) or f"expression:{dependencies}"
    result.close()
    if len(json.dumps(rows, default=str).encode()) > 5 * 1024 * 1024:
        raise ValueError("SSD_RESULT_LIMIT")
    return {"rows": rows, "fields": fields, "affected_rows": affected}


def capture_read_identities(db, connection, revision, info, result):
    """Capture existing HMAC entity identities before masking changes key values."""
    from types import SimpleNamespace

    from tigr.record_identity import capture_entity_lineage

    metadata = info.get("lock_metadata") or {}
    if not metadata:
        return {"entity_lineage": [], "lock_metadata": {}}
    configuration = db.get(LockPropagationConfiguration, UUID(metadata["lock_configuration_id"]))
    objects = {f"{revision.database}.{schema}.{table}" for schema, table in info["objects"]}
    anchors = [a for a in configuration.configuration.get("anchors", []) if a["object_name"] in objects]
    mappings = []
    for field in result["fields"]:
        path = field.get("source_field", "")
        if path.startswith("expression:") or "." not in path:
            continue
        obj, column = path.rsplit(".", 1)
        mappings.append({"source_object": f"{revision.database}.{obj}", "source_field": column, "target_field": field["field"]})
    mapped = {(m["source_object"], m["source_field"]) for m in mappings}
    missing = any((a["object_name"], key) not in mapped for a in anchors for key in a["key_fields"])
    if missing:
        return {"entity_lineage": [], "lock_metadata": metadata,
                "identity_error": "Include the approved entity key columns directly in SELECT before creating a dataset from this locked source. Aggregates and expressions cannot substitute for entity keys."}
    version = SimpleNamespace(provenance={"lock_metadata": metadata})
    refs = capture_entity_lineage(db, None, version, result["rows"], mappings, connection.id)
    return {"entity_lineage": refs, "lock_metadata": metadata}


def run_guarded_source(db, principal, connection, revision, info, before_commit=None):
    if info["operation"] in {"insert", "update", "delete"}:
        try:
            guard_source(db, connection.id, principal.subject)
        except HTTPException as exc:
            raise SourceNotDispatched(str(exc.detail)) from exc
    return run_source(connection, revision, info, before_commit)


class RefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=180)
    confirm_mutation: bool = False


@router.get("/datasets/{dataset_id}/versions/{version_number}/query")
def dataset_query(dataset_id: UUID, version_number: int,
                  db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    from tigr.dataset_api import authorize_dataset_read

    dataset = db.get(DataSet, dataset_id)
    if dataset is None:
        raise HTTPException(404, "Saved source query not found")
    version = db.scalar(select(DataSetVersion).where(DataSetVersion.dataset_id == dataset.id,
                                                     DataSetVersion.version_number == version_number))
    if version is None:
        raise HTTPException(404, "Saved source query not found")
    provenance = version.provenance or {}
    if dataset.creation_pathway != "self_servicing_data" and provenance.get("origin") != "source_backed":
        raise HTTPException(404, "This data set has no saved source query")
    authorize_dataset_read(db, principal, dataset, version)
    if dataset.creation_pathway == "self_servicing_data":
        revision_id = provenance.get("ssd_revision_id")
        if not revision_id:
            raise HTTPException(404, "Saved SSD query not found")
        from tigr.ssd_models import SsdDraft, SsdRevision
        revision = db.scalar(select(SsdRevision).join(SsdDraft, SsdDraft.id == SsdRevision.draft_id).where(
            SsdRevision.id == UUID(str(revision_id)), SsdDraft.dataset_id == dataset.id,
            SsdDraft.workspace_id == dataset.workspace_id))
        if revision is None:
            raise HTTPException(404, "Saved SSD query not found")
        inputs = unseal(revision.secured_inputs)
        return {"query": inputs["sql"], "parameters": sorted(inputs["parameters"].keys()),
                "version_number": version.version_number, "kind": "sql"}
    query_text = provenance.get("source_sql") or provenance.get("generated_query")
    if not query_text and provenance.get("source_query_id"):
        from tigr.source_queries import SourceDataQuery
        try:
            saved = db.get(SourceDataQuery, UUID(str(provenance["source_query_id"])))
        except (ValueError, TypeError):
            saved = None
        if saved and saved.workspace_id == dataset.workspace_id:
            query_text = saved.query_text
    if not query_text and provenance.get("scenario_id"):
        try:
            scenario = db.get(ScenarioPlan, UUID(str(provenance["scenario_id"])))
        except (ValueError, TypeError):
            scenario = None
        if scenario and scenario.workspace_id == dataset.workspace_id:
            query_text = (scenario.logical_plan or {}).get("generated_query")
    return {"query": query_text if isinstance(query_text, str) else None,
            "parameters": [], "version_number": version.version_number,
            "kind": "sql" if isinstance(query_text, str) else "source_mapping"}


@router.post("/datasets/{dataset_id}/refresh", status_code=202)
def refresh_dataset(dataset_id: UUID, payload: RefreshInput, request: Request,
                    background_tasks: BackgroundTasks, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    from tigr.dataset_api import authorize_dataset_read

    dataset = db.scalar(select(DataSet).where(DataSet.id == dataset_id).with_for_update())
    if dataset is None:
        raise HTTPException(404, "Data set not found")
    version = db.scalar(select(DataSetVersion).where(DataSetVersion.dataset_id == dataset.id,
                                                    DataSetVersion.version_number == payload.expected_version))
    if version is None:
        raise HTTPException(404, "Data set version not found")
    authorize_dataset_read(db, principal, dataset, version, refresh=True)
    if principal.actor_type != "human":
        raise HTTPException(403, "Refresh requires a workspace user")
    if dataset.creation_pathway != "self_servicing_data":
        provenance = dict(version.provenance or {})
        source_backed = provenance.get("origin") == "source_backed"
        scenario_backed = bool(provenance.get("scenario_id"))
        if not source_backed and not scenario_backed:
            raise HTTPException(409, "This data set has no saved source query, mapping, or scenario to regenerate")
        from tigr.dataset_api import create_dataset_version
        from tigr.identity import HumanRole
        role = workspace_role(db, principal, dataset.workspace_id)
        if role != HumanRole.TEST_DATA_ENGINEER:
            raise HTTPException(403, "A Test Data Engineer must request source-backed regeneration")
        if dataset.latest_version_number != payload.expected_version or version.publication_state != "published":
            raise HTTPException(409, "Dataset changed; reload before refreshing")
        if provenance.get("federated_plan_hash") and provenance.get("source_plan_id"):
            from tigr.source_mappings import SourceDataPlan
            from tigr.source_to_data import validate_and_version_source_plan, run_federated_generation_job
            try:
                plan_id = UUID(str(provenance["source_plan_id"]))
            except (TypeError, ValueError):
                plan_id = None
            saved_plan = db.get(SourceDataPlan, plan_id) if plan_id else None
            if not saved_plan or saved_plan.workspace_id != dataset.workspace_id or saved_plan.state != "saved":
                raise HTTPException(409, "The saved federated plan is unavailable for refresh")
            plan = validate_and_version_source_plan(db, dataset.workspace_id, saved_plan.plan_data or {})
            if (plan.get("federatedPlan") or {}).get("execution_supported") is not True:
                raise HTTPException(409, "The federated plan is no longer executable")
            settings = version.generation_settings or {}
            volume = min(int(settings.get("maximum_records", 1000)), ROW_LIMIT)
            output_format = settings.get("output_format", "json")
            job_id = uuid4()
            provenance["job_id"] = str(job_id)
            draft_payload = DraftDataSet(
                name=dataset.name, description=dataset.description, creation_pathway=dataset.creation_pathway,
                maximum_records=volume, output_format=output_format,
                schema_scope=[SchemaField(**field) for field in version.schema_scope],
                selected_fields=list(version.selected_fields), provenance=Provenance(**{
                    key: value for key, value in provenance.items() if key in Provenance.model_fields
                }), expiry_at=version.expiry_at,
                permitted_use=list(version.permitted_use or ["testing"]),
            )
            new_version = create_version(db, dataset, draft_payload, principal, request.state.request_id,
                                         server_provenance=provenance)
            job = Job(id=job_id, job_type="federated_generation", status="queued",
                workspace_id=str(dataset.workspace_id), scope_key=f"workspace:{dataset.workspace_id}",
                idempotency_key=f"federated:{saved_plan.id}:{dataset.id}:v{new_version.version_number}",
                actor_type=principal.actor_type, actor_id=principal.subject, actor_role=role.value,
                request_id=request.state.request_id, progress=0, safe_summary="Federated refresh queued")
            db.add(job); db.flush()
            db.add(OutboxEvent(id=uuid4(), aggregate_type="dataset", aggregate_id=str(dataset.id),
                workspace_id=str(dataset.workspace_id), event_type="federated.generation_requested",
                payload={"source_plan_id": str(saved_plan.id), "job_id": str(job_id),
                    "dataset_id": str(dataset.id), "version_id": str(new_version.id),
                    "volume": volume, "output_format": output_format}, request_id=request.state.request_id))
            append_lineage_event(db, dataset, new_version, LineageEventCreate(
                event_type="federated_generation_requested", stage="generation", job_id=job_id,
                source_reference=str(saved_plan.id), safe_evidence={"summary": "Saved federated plan refresh requested"}), principal)
            record_audit(db, principal, request.state.request_id, "dataset.refresh_requested", "dataset",
                str(dataset.id), dataset.workspace_id, role, {"job_id": str(job_id), "version": new_version.version_number})
            db.commit()
            if get_settings().inline_jobs:
                background_tasks.add_task(run_federated_generation_job, saved_plan.id, job_id,
                                          dataset.id, new_version.id, volume, output_format)
            return {"job_id": str(job_id), "version_number": new_version.version_number, "requires_review": True}
        settings = version.generation_settings or {}
        allowed_provenance = Provenance.model_fields.keys()
        saved_provenance = Provenance(**{key: value for key, value in (version.provenance or {}).items() if key in allowed_provenance})
        draft_payload = DraftDataSet(
            name=dataset.name, description=dataset.description, creation_pathway=dataset.creation_pathway,
            maximum_records=min(int(settings.get("maximum_records", 1000)), ROW_LIMIT),
            output_format=settings.get("output_format", "json"),
            schema_scope=[SchemaField(**field) for field in version.schema_scope],
            selected_fields=list(version.selected_fields), provenance=saved_provenance,
            expiry_at=version.expiry_at, permitted_use=list(version.permitted_use or ["testing"]),
        )
        result = create_dataset_version(dataset_id, draft_payload, request, background_tasks,
                                        payload.expected_version, False, db, principal)
        if isinstance(result, dict) and result.get("job_id"):
            return {"job_id": result["job_id"], "version_number": result.get("version_number"),
                    "requires_review": True}
        raise HTTPException(409, "This source-backed version has no replayable saved query or mapping")
    if not get_settings().ssd_enabled:
        raise HTTPException(503, "Self Servicing Data execution is disabled")
    if version.publication_state != "published":
        raise HTTPException(409, "Publish the dataset before refreshing it")
    revision = db.get(SsdRevision, UUID(version.provenance["ssd_revision_id"]))
    draft = db.scalar(select(SsdDraft).where(SsdDraft.id == revision.draft_id).with_for_update())
    connection, info = authorize(db, principal, draft, revision, refresh=True)
    existing = db.scalar(select(SsdExecution).join(Job).where(
        SsdExecution.revision_id == revision.id, SsdExecution.kind == "refresh",
        SsdExecution.idempotency_key == payload.idempotency_key, Job.actor_id == principal.subject))
    if existing:
        return {"job_id": str(existing.job_id), "execution_id": str(existing.id)}
    if dataset.latest_version_number != payload.expected_version:
        raise HTTPException(409, "Dataset changed; reload before refreshing")
    pending = db.scalar(select(Job.id).join(SsdExecution).join(SsdRevision).where(
        SsdRevision.draft_id == draft.id, Job.status.in_(["queued", "running", "cancel_requested"])))
    if pending:
        raise HTTPException(409, "A dataset operation is already in progress")
    if info["operation"] != "select":
        if not payload.confirm_mutation:
            raise HTTPException(422, "Confirm that refresh will apply the saved source changes again")
        uncertain = db.scalar(select(SsdExecution.id).join(SsdRevision).where(
            SsdRevision.draft_id == draft.id, SsdExecution.outcome.in_(["outcome_unknown", "dispatched"])))
        if uncertain:
            raise HTTPException(409, "Reconcile the uncertain source execution before refreshing")
    job = Job(id=uuid4(), job_type="ssd_refresh", workspace_id=str(dataset.workspace_id),
              scope_key=f"ssd-refresh:{dataset.id}", idempotency_key=payload.idempotency_key,
              actor_type="human", actor_id=principal.subject,
              actor_role=workspace_role(db, principal, dataset.workspace_id).value,
              request_id=request.state.request_id, created_at=datetime.now(UTC), status="queued",
              safe_summary="Saved dataset query refresh queued")
    db.add(job)
    db.flush()
    execution = SsdExecution(id=uuid4(), revision_id=revision.id, job_id=job.id, kind="refresh",
                             idempotency_key=payload.idempotency_key, outcome="not_dispatched",
                             evidence={"validation": info, "base_version_id": str(version.id)})
    db.add(execution)
    db.flush()
    db.add(OutboxEvent(id=uuid4(), aggregate_type="ssd", aggregate_id=str(draft.id),
                       workspace_id=str(dataset.workspace_id), event_type="ssd.work_requested",
                       payload={"execution_id": str(execution.id), "job_id": str(job.id)},
                       request_id=request.state.request_id))
    record_audit(db, principal, request.state.request_id, "dataset.refresh_requested", "dataset",
                 str(dataset.id), dataset.workspace_id, workspace_role(db, principal, dataset.workspace_id),
                 {"job_id": str(job.id), "version": payload.expected_version})
    db.commit()
    return {"job_id": str(job.id), "execution_id": str(execution.id)}


def persist_ssd_dataset(db, principal, draft, revision, connection, job, source_execution, full_result, *, refresh_version=None):
    result = full_result
    settings = (refresh_version.generation_settings if refresh_version is not None else draft.generation_settings) or {}
    maximum_records = min(settings.get("maximum_records", ROW_LIMIT), ROW_LIMIT)
    output_format = settings.get("output_format", "json")
    dataset_rows = full_result["rows"][:maximum_records]
    if draft.dataset_id:
        dataset = db.get(DataSet, draft.dataset_id)
        if refresh_version is None:
            dataset.name, dataset.description, dataset.visibility = draft.name, draft.description, draft.visibility
    else:
        reference_sequence = db.scalar(
            text("SELECT nextval('dataops.dataset_reference_sequence')")
        )
        reference_code = (
            f"TDS-SRC-SB-{datetime.now(UTC).strftime('%y%m%dT%H%M%S')}"
            f"-{reference_sequence:05d}"
        )
        dataset = DataSet(
            id=uuid4(),
            reference_code=reference_code,
            workspace_id=draft.workspace_id,
            owner_id=draft.owner_id,
            name=draft.name,
            description=draft.description,
            creation_pathway="self_servicing_data",
            visibility=draft.visibility,
        )
        db.add(dataset)
        db.flush()
    if refresh_version is not None:
        if [(f["field"], f["data_type"]) for f in full_result["fields"]] != [(f["field"], f["data_type"]) for f in refresh_version.schema_scope]:
            raise HTTPException(409, "SSD_SCHEMA_CHANGED: Refresh cannot change dataset fields or types")
    payload = DraftDataSet(
        name=dataset.name,
        maximum_records=maximum_records,
        output_format=output_format,
        creation_pathway="self_servicing_data",
        schema_scope=[SchemaField(**f) for f in (refresh_version.schema_scope if refresh_version is not None else result["fields"])],
        selected_fields=refresh_version.selected_fields if refresh_version is not None else [f["field"] for f in result["fields"]],
        permitted_use=refresh_version.permitted_use if refresh_version is not None else ["testing"],
        expiry_at=refresh_version.expiry_at if refresh_version is not None else None,
    )
    provenance = {
        "origin": "source_backed",
        "ssd_operation": revision.validation.get("operation"),
        "ssd_execution_id": str(source_execution.id),
        "ssd_revision_id": str(revision.id),
        "ssd_draft_id": str(draft.id),
        "source_connection_id": str(connection.id),
        "lock_metadata": full_result.get("lock_metadata", {}),
        "ssd_pre_masked_source": True,
        "pii_masking_required": False,
        "ssd_privacy_reason": "Approved non-production source is already masked; SSD applies no additional PII masking.",
        "job_id": str(job.id),
    }
    create_dataset_version = (
        create_version if draft.dataset_id else create_initial_version
    )
    version = create_dataset_version(
        db,
        dataset,
        payload,
        principal,
        job.request_id,
        server_provenance=provenance,
    )
    version.validation_state, version.privacy_state, version.lifecycle_state = (
        "passed",
        "compliant",
        "ready",
    )
    version.record_count = len(dataset_rows)
    version.validation_evidence = {
        "summary": "SSD bounded output validated from an approved pre-masked non-production source; no additional PII masking applied",
        "record_count": version.record_count,
        "artifact_ref": f"postgresql://dataset/{dataset.id}/version/{version.id}/dataset.{output_format}",
        "ssd_pre_masked_source": True,
        "affected_rows": source_execution.evidence.get("affected_rows", 0),
    }
    store_dataset_records(db, dataset, version, dataset_rows)
    from tigr.record_identity import persist_entity_lineage

    lineage_refs = []
    for ref in full_result.get("entity_lineage", []):
        if ref["ordinal"] < len(dataset_rows):
            lineage_refs.append({**ref,
                "structure_snapshot_id": UUID(ref["structure_snapshot_id"]),
                "lock_configuration_id": UUID(ref["lock_configuration_id"]),
            })
    persist_entity_lineage(db, dataset, version, lineage_refs)
    db.flush()
    artifact_bytes = serialize_dataset_records(
        read_dataset_records(db, version),
        output_format,
        version.selected_fields,
    )
    record_generated_artifact(
        db,
        dataset,
        version,
        len(artifact_bytes),
        version.validation_evidence["artifact_ref"],
        "text/csv" if output_format == "csv" else "application/json",
        hashlib.sha256(artifact_bytes).hexdigest(),
    )
    record_validation(db, dataset, version, job.request_id, "ssd_output")
    record_status_change(
        db,
        dataset,
        version,
        "draft",
        "ready",
        principal,
        job.request_id,
        "SSD output validation and effective privacy checks passed",
    )
    mutations = db.scalars(
        select(SsdExecution)
        .join(SsdRevision)
        .where(
            SsdRevision.draft_id == draft.id,
            SsdExecution.outcome == "committed",
        )
    ).all()
    for mutation in mutations:
        append_lineage_event(
            db,
            dataset,
            version,
            LineageEventCreate(
                event_type="ssd_source_mutation",
                stage="source_execution",
                job_id=mutation.job_id,
                safe_evidence={
                    "summary": "Committed source mutation; saving does not replay source changes",
                    "artifact_ref": str(mutation.id),
                },
            ),
            principal,
        )
    append_lineage_event(
        db,
        dataset,
        version,
        LineageEventCreate(
            event_type="ssd_generated",
            stage="generation",
            job_id=job.id,
            safe_evidence={
                "hash": revision.input_hash,
                "artifact_ref": str(source_execution.id),
            },
        ),
        principal,
    )
    draft.dataset_id = dataset.id
    if draft.revision == revision.number:
        draft.generated_revision = revision.number
        draft.dataset_version_number = version.version_number
    return dataset, version


def run_job(execution_id):
    """A persisted dispatch marker prevents mutation replay after worker crashes."""
    with SessionLocal() as db:
        execution = db.scalar(
            select(SsdExecution).where(SsdExecution.id == execution_id).with_for_update()
        )
        job = db.get(Job, execution.job_id)
        if job.status in ("succeeded", "failed", "cancelled"):
            return
        revision = db.get(SsdRevision, execution.revision_id)
        draft = db.get(SsdDraft, revision.draft_id)
        principal = Principal(
            subject=job.actor_id,
            development_role=HumanRole(job.actor_role)
            if get_settings().env == "development"
            else None,
        )
        if execution.outcome != "not_dispatched":
            # A second dispatcher never enters the source transaction.
            job.status = "failed"
            if execution.outcome == "dispatched":
                execution.outcome = "outcome_unknown"
            job.safe_summary = "SSD_EXECUTION_OUTCOME_UNKNOWN: Reconcile source evidence; this execution will not be retried"
            job.ended_at = datetime.now(UTC)
            db.commit()
            return
        try:
            if not get_settings().ssd_enabled:
                raise HTTPException(503, "Self Servicing Data execution is disabled")
            if job.status == "cancel_requested":
                job.status = "cancelled"
                job.ended_at = datetime.now(UTC)
                db.commit()
                return
            if execution.kind == "refresh":
                from tigr.dataset_api import authorize_dataset_read
                base = db.get(DataSetVersion, UUID(execution.evidence["base_version_id"]))
                target = db.get(DataSet, base.dataset_id)
                authorize_dataset_read(db, principal, target, base, refresh=True)
                if target.latest_version_number != base.version_number or base.publication_state != "published":
                    raise HTTPException(409, "Dataset changed; refresh was not dispatched")
            connection, info = authorize(db, principal, draft, revision, refresh=execution.kind == "refresh")
            if (
                (execution.kind != "refresh" and draft.revision != revision.number)
                or (execution.kind != "refresh" and draft.owner_id != job.actor_id)
                or job.workspace_id != str(draft.workspace_id)
                or job.actor_type != "human"
            ):
                raise HTTPException(409, "SSD_QUERY_STALE")
            if any(execution.evidence["validation"].get(k) != v for k, v in info.items()):
                raise HTTPException(409, "SSD_QUERY_STALE")
            execution.outcome, job.status = "dispatched", "running"
            db.add(
                JobStep(
                    id=uuid4(),
                    job_id=job.id,
                    name="authorize",
                    status="succeeded",
                    safe_summary="Current role, operation grants, binding and configuration verified",
                )
            )
            execution.evidence = {**execution.evidence, "started_at": datetime.now(UTC).isoformat()}
            db.commit()  # Durable before any external statement, including DML.
            if execution.kind in ("query", "refresh"):

                def before_commit():
                    # Check current authority and cancellation before committing an external write.
                    db.execute(text("SELECT 1"))  # The registry guard connection must still be alive.
                    with SessionLocal() as check_db:
                        current_job = check_db.get(Job, job.id)
                        current_revision = check_db.get(SsdRevision, revision.id)
                        current_draft = check_db.get(SsdDraft, draft.id)
                        _, latest = authorize(check_db, principal, current_draft, current_revision, check_locks=False, refresh=execution.kind == "refresh")
                        if execution.kind == "refresh":
                            from tigr.dataset_api import authorize_dataset_read
                            saved_version = check_db.get(DataSetVersion, UUID(execution.evidence["base_version_id"]))
                            saved_dataset = check_db.get(DataSet, saved_version.dataset_id)
                            authorize_dataset_read(check_db, principal, saved_dataset, saved_version, refresh=True)
                            if saved_dataset.latest_version_number != saved_version.version_number:
                                raise HTTPException(409, "Dataset changed during refresh")
                        if (
                            current_job.status == "cancel_requested"
                            or latest != info
                            or (execution.kind != "refresh" and current_draft.revision != revision.number)
                        ):
                            raise HTTPException(
                                409, "SSD_QUERY_STALE: Cancelled or access changed during execution"
                            )

                result = run_guarded_source(db, principal, connection, revision, info, before_commit)
                execution.outcome = (
                    "read_complete" if info["operation"] == "select" else "committed"
                )
                # Persist commit outcome before sample work can fail.
                execution.evidence = {
                    **execution.evidence,
                    "affected_rows": result["affected_rows"],
                    "returned_rows": len(result["rows"]),
                }
                if info["operation"] != "select":
                    db.commit()
                if info["operation"] == "select":
                    identities = capture_read_identities(db, connection, revision, info, result)
                    execution.secured_output = seal(
                        {
                            "rows": result["rows"],
                            "fields": result["fields"],
                            "privacy": "Raw preview from the approved pre-masked non-production source; SSD applies no additional PII masking.",
                            "privacy_mode": "raw_preview",
                            **identities,
                        }
                    )
                    execution.expires_at = datetime.now(UTC) + TTL
                    execution.evidence = {**execution.evidence, "privacy_mode": "raw_preview"}
                next_state = "executed"
                if execution.kind == "refresh":
                    base = db.get(DataSetVersion, UUID(execution.evidence["base_version_id"]))
                    if info["operation"] == "select":
                        full_result = unseal(execution.secured_output)
                    else:
                        full_result = {"rows": [], "fields": [], "lock_metadata": info.get("lock_metadata", {})}
                    if full_result.get("identity_error"):
                        raise HTTPException(409, "SSD_IDENTITY_REQUIRED: " + full_result["identity_error"])
                    validate_output(full_result["rows"], full_result["fields"])
                    dataset, version = persist_ssd_dataset(db, principal, draft, revision, connection, job, execution, full_result, refresh_version=base)
                    version.lifecycle_state = version.publication_state = "published"
                    version.published_at = datetime.now(UTC)
                    record_status_change(db, dataset, version, "ready", "published", principal, job.request_id, "Saved query refreshed without changes")
                    dataset.updated_at = version.published_at
                    db.add(DatasetPublication(id=uuid4(), workspace_id=dataset.workspace_id,
                        dataset_id=dataset.id, version_id=version.id, published_by=principal.subject,
                        published_at=version.published_at, state="published",
                        readiness_snapshot={"validation_state": version.validation_state,
                            "privacy_state": version.privacy_state, "record_count": version.record_count,
                            "permitted_use": version.permitted_use}))
                    append_lineage_event(db, dataset, version, LineageEventCreate(
                        event_type="published", stage="publication",
                        safe_evidence={"summary": "Saved query refreshed with unchanged fields"}), principal)
                    execution.evidence = {**execution.evidence, "dataset_id": str(dataset.id), "version_number": version.version_number}
                    next_state = "published"
            else:
                db.refresh(draft, with_for_update=True)
                if draft.revision != revision.number:
                    raise HTTPException(409, "SSD_QUERY_STALE")
                parent = db.get(SsdExecution, execution.parent_id)
                if info["operation"] != "select":
                    if parent is None or parent.outcome != "committed":
                        raise HTTPException(409, "A confirmed committed execution is required")
                    source_execution = parent
                    result = {"rows": [], "fields": [], "lock_metadata": info.get("lock_metadata", {})}
                else:
                    try:
                        result = output(db, principal, draft, revision, parent)
                    except HTTPException as expired:
                        if (
                            execution.kind != "sample"
                            or info["operation"] != "select"
                            or expired.detail.split(":", 1)[0]
                            not in {
                                "SSD_SAMPLE_EXPIRED",
                                "SSD_QUERY_STALE",
                            }
                        ):
                            raise
                        fresh = run_guarded_source(db, principal, connection, revision, info)
                        identities = capture_read_identities(db, connection, revision, info, fresh)
                        result = {
                            "rows": fresh["rows"],
                            "fields": fresh["fields"],
                            "privacy": "Raw preview from the approved pre-masked non-production source; SSD applies no additional PII masking.",
                            "privacy_mode": "raw_preview",
                            **identities,
                        }
                        parent.secured_output = seal(result)
                        parent.expires_at = datetime.now(UTC) + TTL
                        parent.evidence = {
                            **parent.evidence,
                            "privacy_mode": "raw_preview",
                            "output_validation": execution.evidence["validation"],
                            "refreshed_at": datetime.now(UTC).isoformat(),
                        }
                if execution.kind == "sample":
                    execution.secured_output = seal({**result, "rows": result["rows"][:5]})
                    execution.expires_at = min(parent.expires_at, datetime.now(UTC) + TTL)
                    next_state = "sample_ready"
                else:
                    # Use the full bounded read, while the browser only receives five sample rows.
                    if info["operation"] == "select":
                        source_execution = db.get(SsdExecution, parent.parent_id)
                        full_result = output(db, principal, draft, revision, source_execution)
                    else:
                        full_result = result
                    if full_result.get("identity_error"):
                        raise HTTPException(409, "SSD_IDENTITY_REQUIRED: " + full_result["identity_error"])
                    if full_result.get("lock_metadata", {}) != info.get("lock_metadata", {}):
                        raise HTTPException(409, "SSD_QUERY_STALE: Regenerate the read under the current entity-lock configuration")
                    validate_output(full_result["rows"], full_result["fields"])
                    if draft.generated_revision != draft.revision:
                        persist_ssd_dataset(db, principal, draft, revision, connection, job, source_execution, full_result)
                    next_state = "ready_to_publish"
                execution.outcome = "complete"
            # Refresh after source work so an old revision cannot advance a newer draft.
            db.flush()
            db.refresh(draft, with_for_update=True)
            db.refresh(job, with_for_update=True)
            cancelled = job.status == "cancel_requested"
            if draft.revision == revision.number:
                draft.state = next_state
            job.status, job.progress, job.safe_summary = "succeeded", 100, "SSD work completed"
            if cancelled and execution.outcome != "committed":
                execution.secured_output, execution.expires_at = None, None
                job.status, job.safe_summary = "cancelled", "Cancelled; temporary output removed"
            elif cancelled:
                job.safe_summary = (
                    "Source transaction committed before cancellation could take effect"
                )
        except Exception as exc:  # noqa: BLE001 - sanitize errors and preserve source commit evidence.
            import traceback

            failure_frames = traceback.extract_tb(exc.__traceback__)
            failure_location = ",".join(
                f"{frame.name}:{frame.lineno}"
                for frame in failure_frames
                if "tigr" in frame.filename
            )
            failure_cause = type(getattr(exc, "orig", exc)).__name__
            db.rollback()
            db.refresh(execution)
            db.refresh(job)
            was_cancel_requested = job.status == "cancel_requested"
            db.refresh(draft)
            execution.evidence = {
                **execution.evidence,
                "failure_type": type(exc).__name__,
                "failure_cause": failure_cause,
                "failure_location": failure_location,
            }
            if execution.outcome == "dispatched":
                if isinstance(exc, SourceNotDispatched):
                    execution.outcome = "not_dispatched"
                elif isinstance(exc, SourceRolledBack):
                    execution.outcome = "rolled_back"
                elif (
                    execution.kind in ("query", "refresh")
                    and execution.evidence["validation"]["operation"] != "select"
                ):
                    execution.outcome = "outcome_unknown"
                else:
                    execution.outcome = "failed"
            job.status, job.progress = "failed", 100
            if was_cancel_requested and execution.outcome == "rolled_back":
                job.status = "cancelled"
            job.safe_summary = (
                str(exc.detail)
                if isinstance(exc, HTTPException)
                else str(exc)
                if isinstance(exc, (SourceRolledBack, SourceNotDispatched))
                else "SSD_EXECUTION_OUTCOME_UNKNOWN: Source outcome could not be proven; no automatic replay"
                if execution.outcome == "outcome_unknown"
                else "SSD work failed; check connection, SQL, limits and authorization"
            )
            if draft.revision == revision.number:
                draft.state = "blocked" if isinstance(exc, HTTPException) else "failed"
        job.ended_at = datetime.now(UTC)
        db.add(
            JobStep(
                id=uuid4(),
                job_id=job.id,
                name=f"ssd_{execution.kind}",
                status=job.status,
                safe_summary=f"Outcome: {execution.outcome}",
            )
        )
        execution.evidence = {**execution.evidence, "ended_at": job.ended_at.isoformat()}
        record_audit(
            db,
            principal,
            job.request_id,
            "ssd.execution_finished",
            "ssd_execution",
            str(execution.id),
            draft.workspace_id,
            HumanRole(job.actor_role),
            {"outcome": execution.outcome, "job_id": str(job.id)},
        )
        db.commit()


def enforce_dataset(db, principal, dataset, version=None, *, refresh=False):
    """Shared access gate used by catalog, privacy, reservations and delivery."""
    if dataset.creation_pathway != "self_servicing_data":
        return
    workspace_role(db, principal, dataset.workspace_id)
    if dataset.visibility == "private" and dataset.owner_id != principal.subject:
        raise HTTPException(404, "Data set not found")
    if version and refresh:
        if dataset.owner_id == principal.subject:
            revision = db.get(SsdRevision, UUID(version.provenance["ssd_revision_id"]))
            draft = db.get(SsdDraft, revision.draft_id)
            authorize(db, principal, draft, revision, refresh=refresh)


class VisibilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: str = Field(pattern="^(private|public)$")


@router.patch("/datasets/{dataset_id}/visibility")
def visibility(
    dataset_id: UUID,
    payload: VisibilityInput,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    dataset = db.scalar(select(DataSet).where(DataSet.id == dataset_id).with_for_update())
    if (
        dataset is None
        or dataset.creation_pathway != "self_servicing_data"
        or dataset.owner_id != principal.subject
    ):
        raise HTTPException(404, "Data set not found")
    qa(db, principal, dataset.workspace_id)
    previous = dataset.visibility
    dataset.visibility = payload.visibility
    record_audit(
        db,
        principal,
        request.state.request_id,
        "ssd.visibility_changed",
        "dataset",
        str(dataset.id),
        dataset.workspace_id,
        HumanRole.QA_ENGINEER,
        {"previous": previous, "visibility": payload.visibility},
    )
    db.commit()
    return {"visibility": dataset.visibility}


def cleanup(db):
    from sqlalchemy import update

    db.execute(
        update(SsdExecution)
        .where(
            SsdExecution.expires_at < datetime.now(UTC), SsdExecution.secured_output.is_not(None)
        )
        .values(secured_output=None)
    )
