"""Authorized target provisioning and CSV reporting endpoints."""

import csv
import json
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    create_engine,
    insert,
    or_,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from tigr.access import record_audit, workspace_role
from tigr.audit import AuditEvent
from tigr.config import get_settings
from tigr.connection_models import WorkspaceConnection
from tigr.connection_testing import resolve_file
from tigr.dashboard import activity_query, visible_workspace_roles
from tigr.dataset_api import audit_dataset, can_manage_dataset, get_dataset, get_version
from tigr.dataset_schemas import LineageEventCreate
from tigr.dataset_services import (
    append_lineage_event,
    dataset_records_privacy_ready,
    read_dataset_records,
)
from tigr.datasets import DataSet, DataSetVersion
from tigr.delivery import ExportArtifact, ProvisioningRun
from tigr.governance import DataSetLock, DataSetReservation, MaskingRun
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.jobs import Job
from tigr.schema_records import ConsumptionEvent
from tigr.secrets import EnvironmentSecretResolver, UnconfiguredSecretResolver

router = APIRouter(prefix="/api/v1", tags=["provisioning and exports"])
MAX_ROWS = 10_000
MAX_EXPORT_BYTES = 5 * 1024 * 1024
MAX_DATA_BYTES = 5 * 1024 * 1024
EXPORT_TTL = timedelta(minutes=5)


class ProvisionRequest(BaseModel):
    target_connection_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=180, pattern=r"^[A-Za-z0-9._:-]+$")


def _safe_cell(value: Any) -> str:
    cell = "" if value is None else str(value)
    if cell.lstrip(" ").startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + cell
    return cell


def _load_records(db: Session, version: DataSetVersion) -> list[dict[str, Any]]:
    if not dataset_records_privacy_ready(version):
        raise ValueError("Source-backed records are available only after PII masking is complete")
    records = read_dataset_records(db, version)
    if not isinstance(records, list) or len(records) > MAX_ROWS or any(not isinstance(row, dict) for row in records):
        raise ValueError("Artifact must contain at most 10,000 object records")
    return records


def _pg_type(value: str):
    normalized = value.casefold()
    if any(part in normalized for part in ("int", "serial")):
        return Integer()
    if any(part in normalized for part in ("numeric", "decimal", "float", "double", "real")):
        return Numeric()
    if "bool" in normalized:
        return Boolean()
    if any(part in normalized for part in ("char", "text", "string", "uuid", "date", "time")):
        return String(2000)
    return Text()


def _provision(db: Session, run: ProvisioningRun, connection: WorkspaceConnection, dataset: DataSet, version: DataSetVersion) -> None:
    records = _load_records(db, version)
    fields = version.schema_scope or []
    if not fields:
        raise ValueError("The published version has no target schema fields")
    names = [str(row.get("field", "")) for row in fields]
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name) for name in names) or len(set(names)) != len(names):
        raise ValueError("Target schema field names are invalid")
    if any(not set(record).issubset(set(names)) for record in records):
        raise ValueError("Artifact contains fields outside the published schema")
    required_fields = {str(field["field"]) for field in fields if field.get("required")}
    if any(not required_fields.issubset(record) for record in records):
        raise ValueError("Artifact is missing one or more required target fields")
    if connection.connector_type in {"csv", "json"}:
        destination = resolve_file(connection.connector_config["file_path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        suffix = ".csv" if connection.connector_type == "csv" else ".json"
        final = destination.with_name(f"{destination.stem}-{run.id.hex}{suffix}")
        if connection.connector_type == "json":
            payload = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        else:
            from io import StringIO
            buffer = StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=names, extrasaction="raise")
            writer.writeheader()
            for record in records:
                writer.writerow({name: _safe_cell(record.get(name)) for name in names})
            payload = buffer.getvalue().encode("utf-8")
        if len(payload) > MAX_DATA_BYTES:
            raise ValueError("Target artifact exceeds the 5 MiB delivery limit")
        fd, temporary = tempfile.mkstemp(prefix=".tigr-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, final)
        finally:
            Path(temporary).unlink(missing_ok=True)
        run.target_object = str(final)
        run.record_count = len(records)
        if final.stat().st_size != len(payload):
            final.unlink(missing_ok=True)
            raise ValueError("Target file post-write check failed")
        return
    resolver = EnvironmentSecretResolver() if get_settings().env == "development" else UnconfiguredSecretResolver()
    dsn = resolver.resolve(connection.secret_reference or "")
    url = make_url(dsn)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("Target secret must resolve to a PostgreSQL URL")
    schema = str((connection.connector_config or {}).get("schema", "public"))
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", schema):
        raise ValueError("Target schema is invalid")
    table_name = f"tigr_{run.id.hex}"
    metadata = MetaData()
    target = Table(table_name, metadata, *[
        Column(name, _pg_type(str(field.get("data_type", "text"))), nullable=not bool(field.get("required")))
        for name, field in zip(names, fields, strict=True)
    ], schema=schema)
    engine = create_engine(dsn, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with engine.begin() as target_db:
            target.create(target_db)
            if records:
                target_db.execute(insert(target), [{name: record.get(name) for name in names} for record in records])
            count = target_db.execute(select(text("count(*)")).select_from(target)).scalar_one()
            if count != len(records):
                raise ValueError("Target record count check failed")
        run.target_object = f"{schema}.{table_name}"
        run.record_count = len(records)
    finally:
        engine.dispose()


def _run_out(run: ProvisioningRun, *, include_target: bool = True) -> dict[str, Any]:
    return {"id": str(run.id), "dataset_id": str(run.dataset_id), "version_id": str(run.version_id),
            "target_connection_id": str(run.target_connection_id), "status": run.status,
            "target_object": run.target_object if include_target else None, "record_count": run.record_count,
            "evidence": run.evidence, "created_at": run.created_at, "completed_at": run.completed_at}


def remove_provisioned_target(run: ProvisioningRun, connection: WorkspaceConnection) -> None:
    if connection.connector_type == "postgresql":
        resolver = EnvironmentSecretResolver() if get_settings().env == "development" else UnconfiguredSecretResolver()
        dsn = resolver.resolve(connection.secret_reference or "")
        schema, table_name = (run.target_object or "").split(".", 1)
        if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", part) for part in (schema, table_name)) or not table_name.startswith(f"tigr_{run.id.hex}"):
            raise ValueError("Provisioned table reference is invalid")
        engine = create_engine(dsn, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 5})
        try:
            with engine.begin() as target_db:
                target_db.execute(text(f'DROP TABLE IF EXISTS "{schema}"."{table_name}"'))
        finally:
            engine.dispose()
    else:
        path = Path(run.target_object or "").resolve()
        base = resolve_file(connection.connector_config["file_path"])
        expected = base.with_name(f"{base.stem}-{run.id.hex}{'.csv' if connection.connector_type == 'csv' else '.json'}").resolve()
        if path != expected:
            raise ValueError("Provisioned file reference is unavailable")
        path.unlink(missing_ok=True)


@router.post("/datasets/{dataset_id}/versions/{version_number}/provision", status_code=201)
def provision_dataset(dataset_id: UUID, version_number: int, payload: ProvisionRequest, request: Request, response: Response,
                      db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict:
    dataset = get_dataset(db, dataset_id)
    role = can_manage_dataset(db, principal, dataset.workspace_id)
    if role not in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER):
        raise HTTPException(status_code=403, detail="Provisioning requires an Admin or Test Data Engineer")
    version = get_version(db, dataset, version_number, for_update=True)
    if (version.lifecycle_state != "published" or version.publication_state != "published"
            or version.privacy_state != "compliant" or version.validation_state != "passed"
            or not dataset_records_privacy_ready(version)
            or (version.expiry_at and version.expiry_at <= datetime.now(UTC))):
        raise HTTPException(status_code=409, detail="Only active, published, validated, privacy-compliant versions can be provisioned")
    from tigr.entity_claims import require_dataset_derivation_access
    require_dataset_derivation_access(db, version, principal.subject)
    target = db.scalar(select(WorkspaceConnection).where(WorkspaceConnection.id == payload.target_connection_id).with_for_update())
    if (target is None or target.workspace_id != dataset.workspace_id or target.status != "enabled"
            or target.direction not in {"target", "both"} or target.operation_scope not in {"write_only", "read_write"}):
        raise HTTPException(status_code=404, detail="Enabled target connection not found")
    existing = db.scalar(select(ProvisioningRun).where(ProvisioningRun.workspace_id == dataset.workspace_id,
                                                        ProvisioningRun.idempotency_key == payload.idempotency_key))
    if existing:
        if existing.dataset_id != dataset.id or existing.version_id != version.id or existing.target_connection_id != target.id:
            raise HTTPException(status_code=409, detail="Idempotency key was already used for a different delivery")
        response.status_code = 200
        return _run_out(existing)
    run = ProvisioningRun(id=uuid4(), workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
                          target_connection_id=target.id, actor_id=principal.subject, idempotency_key=payload.idempotency_key,
                          status="running", evidence={"preflight": "passed"})
    db.add(run)
    db.flush()
    try:
        _provision(db, run, target, dataset, version)
        run.status = "succeeded"
        run.evidence = {"preflight": "passed", "postflight": "passed", "verified_record_count": run.record_count}
        outcome = "succeeded"
    except Exception:  # noqa: BLE001 - persist a sanitized failure for any connector error.
        run.status = "failed"
        run.evidence = {"preflight": "passed", "postflight": "failed", "summary": "Target delivery failed; no row values or connection details were retained"}
        run.target_object = None
        run.record_count = 0
        outcome = "failed"
    run.completed_at = datetime.now(UTC)
    if outcome == "succeeded":
        db.add(ConsumptionEvent(
            workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
            principal_id=principal.subject, event_type="target_delivery", target_connection_id=target.id,
            occurred_at=run.completed_at, outcome="succeeded", correlation_id=request.state.request_id,
        ))
    append_lineage_event(db, dataset, version, LineageEventCreate(event_type="target_provisioned", stage="delivery",
        outcome=outcome, safe_evidence={"target_connection_id": str(target.id), "summary": f"Target provisioning {run.status}",
                                       "record_count": run.record_count}), principal)
    audit_dataset(db, request, principal, role, dataset, "dataset.target_provisioned", version,
                  {"run_id": str(run.id), "target_connection_id": str(target.id), "status": run.status})
    db.commit()
    db.refresh(run)
    return _run_out(run)


@router.get("/datasets/{dataset_id}/provisioning-runs")
def list_provisioning_runs(dataset_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> list[dict]:
    dataset = get_dataset(db, dataset_id)
    role = workspace_role(db, principal, dataset.workspace_id)
    if role == HumanRole.QA_ENGINEER:
        published = db.scalar(select(DataSetVersion.id).where(DataSetVersion.dataset_id == dataset_id,
            DataSetVersion.lifecycle_state == "published", DataSetVersion.publication_state == "published",
            DataSetVersion.privacy_state == "compliant", DataSetVersion.validation_state == "passed",
            or_(DataSetVersion.expiry_at.is_(None), DataSetVersion.expiry_at > datetime.now(UTC))).limit(1))
        if published is None:
            raise HTTPException(status_code=404, detail="Data set not found")
    return [_run_out(run, include_target=role != HumanRole.QA_ENGINEER) for run in db.scalars(select(ProvisioningRun).where(ProvisioningRun.dataset_id == dataset_id)
                                                                                             .order_by(ProvisioningRun.created_at.desc()).limit(100)).all()]


@router.delete("/provisioning-runs/{run_id}")
def cleanup_provisioning_run(run_id: UUID, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict:
    run = db.get(ProvisioningRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Provisioning run not found")
    role = can_manage_dataset(db, principal, run.workspace_id)
    if run.status == "cleaned":
        return _run_out(run)
    if run.status != "succeeded" or not run.target_object:
        raise HTTPException(status_code=409, detail="Only a successful delivery can be cleaned up")
    target = db.get(WorkspaceConnection, run.target_connection_id)
    try:
        if target is None:
            raise ValueError("Target connection is unavailable")
        remove_provisioned_target(run, target)
    except (ValueError, OSError, SQLAlchemyError, RuntimeError):
        raise HTTPException(status_code=409, detail="Target cleanup failed; the run remains available for reconciliation")
    run.status = "cleaned"
    run.evidence = {**run.evidence, "cleanup": "succeeded"}
    run.target_object = None
    dataset = get_dataset(db, run.dataset_id)
    version = db.get(DataSetVersion, run.version_id)
    append_lineage_event(db, dataset, version, LineageEventCreate(event_type="target_cleaned", stage="cleanup",
        safe_evidence={"summary": "Provisioned target was removed"}), principal)
    record_audit(db, principal, request.state.request_id, "dataset.target_cleaned", "dataset_version", str(version.id),
                 dataset.workspace_id, role, {"run_id": str(run.id)})
    db.add(ConsumptionEvent(
        workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
        principal_id=principal.subject, event_type="target_cleanup", target_connection_id=target.id,
        occurred_at=datetime.now(UTC), outcome="succeeded", correlation_id=request.state.request_id,
    ))
    db.commit()
    return _run_out(run)


def _purge_expired_exports(db: Session) -> None:
    now = datetime.now(UTC)
    rows = db.scalars(select(ExportArtifact).where(ExportArtifact.expires_at <= now)).all()
    for row in rows:
        db.delete(row)


@router.post("/workspaces/{workspace_id}/exports", status_code=201)
def create_csv_export(workspace_id: UUID, report_type: str, request: Request, db: Session = Depends(get_db),
                      principal: Principal = Depends(get_principal)) -> dict:
    role = workspace_role(db, principal, workspace_id)
    if role == HumanRole.QA_ENGINEER:
        report_type = "datasets"
    supported = {"datasets", "connections", "provisioning", "activities", "reservations", "locks", "masking", "jobs"}
    if report_type not in supported:
        raise HTTPException(status_code=422, detail="Report type is not supported")
    _purge_expired_exports(db)
    rows: list[list[Any]] = []
    headers: list[str]
    if report_type == "datasets":
        headers = ["dataset_id", "version_id", "name", "version", "lifecycle_state", "privacy_state", "validation_state", "publication_state", "created_at"]
        query = select(DataSet, DataSetVersion).join(DataSetVersion, DataSetVersion.dataset_id == DataSet.id).where(DataSet.workspace_id == workspace_id)
        if role == HumanRole.QA_ENGINEER:
            query = query.where(DataSetVersion.lifecycle_state == "published", DataSetVersion.publication_state == "published",
                                DataSetVersion.privacy_state == "compliant", DataSetVersion.validation_state == "passed",
                                or_(DataSetVersion.expiry_at.is_(None), DataSetVersion.expiry_at > datetime.now(UTC)))
        else:
            query = query.where(DataSetVersion.version_number == DataSet.latest_version_number)
        if role == HumanRole.QA_ENGINEER:
            query = query.where(DataSetVersion.version_number == DataSet.latest_version_number)
        for dataset, version in db.execute(query.order_by(DataSet.name).limit(MAX_ROWS)).all():
            lifecycle_state = "expired" if version.expiry_at and version.expiry_at <= datetime.now(UTC) else version.lifecycle_state
            rows.append([dataset.id, version.id, dataset.name, version.version_number, lifecycle_state, version.privacy_state,
                         version.validation_state, version.publication_state, version.created_at.isoformat()])
    elif report_type == "connections":
        if role == HumanRole.QA_ENGINEER:
            raise HTTPException(status_code=403, detail="Connection inventory exports require an Admin or Test Data Engineer")
        headers = ["connection_id", "name", "connector_type", "direction", "environment", "status", "operation_scope"]
        for connection in db.scalars(select(WorkspaceConnection).where(WorkspaceConnection.workspace_id == workspace_id).order_by(WorkspaceConnection.name).limit(MAX_ROWS)).all():
            rows.append([connection.id, connection.name, connection.connector_type, connection.direction, connection.environment, connection.status, connection.operation_scope])
    elif report_type == "provisioning":
        if role == HumanRole.QA_ENGINEER:
            raise HTTPException(status_code=403, detail="Provisioning exports require an Admin or Test Data Engineer")
        headers = ["run_id", "dataset_id", "version_id", "target_connection_id", "status", "record_count", "created_at", "completed_at"]
        for run in db.scalars(select(ProvisioningRun).where(ProvisioningRun.workspace_id == workspace_id).order_by(ProvisioningRun.created_at.desc()).limit(MAX_ROWS)).all():
            rows.append([run.id, run.dataset_id, run.version_id, run.target_connection_id, run.status, run.record_count, run.created_at.isoformat(), run.completed_at.isoformat() if run.completed_at else ""])
    elif report_type == "activities":
        headers = ["event_id", "action", "object_type", "object_id", "outcome", "occurred_at"]
        visibility_roles = visible_workspace_roles(db, principal)
        visible_events = activity_query(db, principal, visibility_roles, workspace_id)
        events = db.scalars(visible_events.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id).limit(MAX_ROWS)).all()
        rows.extend([[event.id, event.action, event.object_type, event.object_id, event.outcome, event.occurred_at.isoformat()] for event in events])
    elif report_type in {"reservations", "locks"}:
        if role == HumanRole.QA_ENGINEER:
            raise HTTPException(status_code=403, detail="Reservation and lock exports require an Admin or Test Data Engineer")
        if report_type == "reservations":
            headers = ["reservation_id", "dataset_id", "version_id", "starts_at", "ends_at", "purpose", "status"]
            records = db.scalars(select(DataSetReservation).where(DataSetReservation.workspace_id == workspace_id).order_by(DataSetReservation.starts_at.desc()).limit(MAX_ROWS)).all()
            rows.extend([[record.id, record.dataset_id, record.version_id, record.starts_at.isoformat(), record.ends_at.isoformat(), record.purpose, record.status] for record in records])
        else:
            headers = ["lock_id", "dataset_id", "version_id", "fencing_token", "acquired_at", "expires_at", "released_at", "status"]
            records = db.scalars(select(DataSetLock).where(DataSetLock.workspace_id == workspace_id).order_by(DataSetLock.acquired_at.desc()).limit(MAX_ROWS)).all()
            rows.extend([[record.id, record.dataset_id, record.version_id, record.fencing_token, record.acquired_at.isoformat(), record.expires_at.isoformat(), record.released_at.isoformat() if record.released_at else "", record.status] for record in records])
    elif report_type == "masking":
        if role == HumanRole.QA_ENGINEER:
            raise HTTPException(status_code=403, detail="Masking exports require an Admin or Test Data Engineer")
        headers = ["run_id", "dataset_id", "version_id", "policy_id", "status", "created_at", "completed_at"]
        records = db.scalars(select(MaskingRun).where(MaskingRun.workspace_id == workspace_id).order_by(MaskingRun.created_at.desc()).limit(MAX_ROWS)).all()
        rows.extend([[record.id, record.dataset_id, record.version_id, record.policy_id, record.status, record.created_at.isoformat(), record.completed_at.isoformat() if record.completed_at else ""] for record in records])
    else:
        if role == HumanRole.QA_ENGINEER:
            raise HTTPException(status_code=403, detail="Job exports require an Admin or Test Data Engineer")
        headers = ["job_id", "job_type", "status", "progress", "workspace_id", "created_at"]
        records = db.scalars(select(Job).where(Job.workspace_id == str(workspace_id)).order_by(Job.created_at.desc()).limit(MAX_ROWS)).all()
        rows.extend([[record.id, record.job_type, record.status, record.progress, record.workspace_id, record.created_at.isoformat()] for record in records])
    from io import StringIO
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_safe_cell(value) for value in row])
    payload = buffer.getvalue().encode("utf-8")
    if len(payload) > MAX_EXPORT_BYTES:
        raise HTTPException(status_code=413, detail="Report exceeds the 5 MiB export limit")
    export_id = uuid4()
    expires_at = datetime.now(UTC) + EXPORT_TTL
    artifact = ExportArtifact(id=export_id, workspace_id=workspace_id, actor_id=principal.subject, report_type=report_type,
                              content=payload.decode("utf-8"), expires_at=expires_at)
    db.add(artifact)
    record_audit(db, principal, request.state.request_id, "report.export_created", "export_artifact", str(export_id),
                 workspace_id, role, {"report_type": report_type, "row_count": len(rows), "expires_at": expires_at.isoformat()})
    db.commit()
    return {"id": str(export_id), "report_type": report_type, "row_count": len(rows), "expires_at": expires_at.isoformat(),
            "download_url": f"/api/v1/exports/{export_id}"}


@router.get("/exports/{export_id}")
def download_csv_export(export_id: UUID, request: Request, db: Session = Depends(get_db),
                        principal: Principal = Depends(get_principal)) -> Response:
    artifact = db.get(ExportArtifact, export_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Export artifact not found")
    role = workspace_role(db, principal, artifact.workspace_id)
    if artifact.expires_at <= datetime.now(UTC):
        db.delete(artifact)
        db.commit()
        raise HTTPException(status_code=404, detail="Export artifact not found")
    artifact.downloaded_at = datetime.now(UTC)
    record_audit(db, principal, request.state.request_id, "report.export_downloaded", "export_artifact", str(export_id),
                 artifact.workspace_id, role, {"report_type": artifact.report_type})
    db.commit()
    return Response(artifact.content.encode("utf-8"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="tigr-{artifact.report_type}.csv"'})
