import csv
import hashlib
import json
from datetime import UTC, datetime
from io import StringIO
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tigr.connection_models import ConnectionStructureSnapshot, LockPropagationConfiguration
from tigr.dataset_schemas import DraftDataSet, LineageEventCreate
from tigr.datasets import (
    DataSet,
    DataSetField,
    DataSetLineageEdge,
    DataSetLineageEvent,
    DataSetVersion,
)
from tigr.identity import Principal
from tigr.schema_records import (
    ArtifactRecord,
    DataSetRecord,
    DatasetStatusHistory,
    ValidationResult,
    ValidationRun,
)


def _provenance_with_lock_configuration(db: Session, provenance: dict) -> dict:
    """Pin a new source-backed version to the schema/config approval it was planned against."""
    result = dict(provenance)
    if result.get("origin") != "source_backed" or result.get("lock_metadata"):
        return result
    try:
        connection_id = UUID(str(result.get("source_connection_id")))
    except (TypeError, ValueError):
        return result
    snapshot = db.scalar(select(ConnectionStructureSnapshot).where(
        ConnectionStructureSnapshot.connection_id == connection_id,
    ).order_by(ConnectionStructureSnapshot.version_number.desc()).limit(1))
    if snapshot is None:
        return result
    config = db.scalar(select(LockPropagationConfiguration).where(
        LockPropagationConfiguration.connection_id == connection_id,
        LockPropagationConfiguration.status == "approved",
    ).order_by(LockPropagationConfiguration.version_number.desc()).limit(1))
    config_matches_snapshot = config is not None and config.structure_snapshot_id == snapshot.id
    result["lock_metadata"] = {
        "source_identity_namespace": str((snapshot.structure or {}).get("source_identity_namespace") or f"connection:{connection_id}"),
        "structure_snapshot_id": str(snapshot.id),
        "structure_snapshot_version": snapshot.version_number,
        "lock_configuration_id": str(config.id) if config_matches_snapshot else None,
        "lock_configuration_version": config.version_number if config_matches_snapshot else None,
        "configuration_status": "approved" if config_matches_snapshot else "missing_or_stale",
    }
    return result


def append_lineage_event(
    db: Session,
    dataset: DataSet,
    version: DataSetVersion,
    payload: LineageEventCreate,
    principal: Principal,
) -> DataSetLineageEvent:
    locked_dataset = db.scalar(select(DataSet).where(DataSet.id == dataset.id).with_for_update())
    if locked_dataset is None:
        raise HTTPException(status_code=404, detail="Data set not found")
    sequence = (db.scalar(select(func.max(DataSetLineageEvent.sequence_number)).where(DataSetLineageEvent.dataset_id == dataset.id)) or 0) + 1
    parent_ids = payload.parent_event_ids
    if parent_ids is None:
        previous = db.scalar(
            select(DataSetLineageEvent.id)
            .where(DataSetLineageEvent.dataset_id == dataset.id, DataSetLineageEvent.version_id == version.id)
            .order_by(DataSetLineageEvent.sequence_number.desc())
            .limit(1)
        )
        parent_ids = [previous] if previous else []
    parent_events = list(db.scalars(select(DataSetLineageEvent).where(DataSetLineageEvent.id.in_(parent_ids))).all()) if parent_ids else []
    if len(parent_events) != len(parent_ids) or any(event.dataset_id != dataset.id for event in parent_events):
        raise HTTPException(status_code=404, detail="Lineage parent event not found")
    if any(event.sequence_number >= sequence for event in parent_events):
        raise HTTPException(status_code=422, detail="Lineage parents must precede the new event")
    event = DataSetLineageEvent(
        id=uuid4(), dataset_id=dataset.id, version_id=version.id, sequence_number=sequence,
        event_type=payload.event_type, stage=payload.stage, source_reference=payload.source_reference,
        artifact_reference=payload.artifact_reference, mapping_version=payload.mapping_version,
        scenario_version=payload.scenario_version, job_id=payload.job_id,
        policy_version=payload.policy_version, validation_version=payload.validation_version,
        actor_id=principal.subject, actor_type=principal.actor_type, outcome=payload.outcome,
        safe_evidence=payload.safe_evidence,
    )
    db.add(event)
    db.flush()
    for parent in parent_events:
        db.add(DataSetLineageEdge(dataset_id=dataset.id, parent_event_id=parent.id, child_event_id=event.id))
    return event


def record_status_change(
    db: Session, dataset: DataSet, version: DataSetVersion, from_status: str | None,
    to_status: str, principal: Principal, correlation_id: str, reason: str | None = None,
) -> None:
    if from_status == to_status:
        return
    db.add(DatasetStatusHistory(
        id=uuid4(), workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
        from_status=from_status, to_status=to_status, changed_by=principal.subject,
        changed_at=datetime.now(UTC), reason=reason, correlation_id=correlation_id,
    ))


def record_validation(
    db: Session, dataset: DataSet, version: DataSetVersion, correlation_id: str, validation_type: str,
) -> None:
    completed_at = datetime.now(UTC)
    status = "passed" if version.validation_state == "passed" else "failed" if version.validation_state == "failed" else "warning"
    evidence = version.validation_evidence or {}
    run = ValidationRun(
        id=uuid4(), workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
        validation_type=validation_type, status=status, started_at=completed_at, completed_at=completed_at,
        summary={key: value for key, value in evidence.items() if key in {
            "summary", "record_count", "field_count", "warning_count", "validation_version", "schema_version"
        }},
    )
    db.add(run)
    db.flush()
    db.add(ValidationResult(
        id=uuid4(), workspace_id=dataset.workspace_id, validation_run_id=run.id,
        check_code=f"{validation_type}_summary", severity="error" if status == "failed" else "info",
        status="failed" if status == "failed" else "passed" if status == "passed" else "warning",
        safe_message=str(evidence.get("summary", "Validation completed"))[:500],
        observed_count=version.record_count,
        expected_summary={"field_count": evidence.get("field_count"), "validation_version": evidence.get("validation_version")},
    ))


def store_dataset_records(
    db: Session, dataset: DataSet, version: DataSetVersion, rows: list[dict], *, masking_complete: bool = False,
    lineage_refs: list[dict] | None = None,
) -> tuple[str, int]:
    """Persist versioned rows, requiring a completed masking gate for source-backed values."""
    if (version.provenance or {}).get("origin") == "source_backed" and not masking_complete:
        raise ValueError("Source-backed records can be stored only after PII masking completes")
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    db.execute(DataSetRecord.__table__.delete().where(DataSetRecord.version_id == version.id))
    db.add_all([
        DataSetRecord(id=uuid4(), workspace_id=dataset.workspace_id, dataset_id=dataset.id,
                      version_id=version.id, ordinal=index, values=row)
        for index, row in enumerate(rows)
    ])
    from tigr.record_identity import persist_entity_lineage
    persist_entity_lineage(db, dataset, version, lineage_refs or [])
    return digest, len(canonical)


def read_dataset_records(db: Session, version: DataSetVersion) -> list[dict]:
    return [row.values for row in db.scalars(select(DataSetRecord).where(
        DataSetRecord.version_id == version.id
    ).order_by(DataSetRecord.ordinal)).all()]


def dataset_records_privacy_ready(version: DataSetVersion) -> bool:
    """Source-backed values are releasable only after a successful masking run."""
    provenance = version.provenance or {}
    if provenance.get("origin") != "source_backed":
        return True
    return (
        version.privacy_state == "compliant"
        and version.validation_state == "passed"
        and bool((version.validation_evidence or {}).get("masking_run_id"))
    )


def serialize_dataset_records(
    rows: list[dict], output_format: str, field_names: list[str] | None = None,
) -> bytes:
    if output_format == "csv":
        buffer = StringIO(newline="")
        fields = list(rows[0].keys()) if rows else list(field_names or [])
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: ("'" + str(value) if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else value) for key, value in row.items()})
        return buffer.getvalue().encode("utf-8")
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def record_generated_artifact(
    db: Session, dataset: DataSet, version: DataSetVersion, size_bytes: int, storage_key: str,
    content_type: str, checksum: str, status: str = "available",
) -> ArtifactRecord:
    artifact = db.scalar(select(ArtifactRecord).where(ArtifactRecord.storage_key == storage_key))
    if artifact is None:
        artifact = ArtifactRecord(
            id=uuid4(), workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
            artifact_type="generated_dataset", storage_key=storage_key, storage_provider="postgresql",
            content_type=content_type, size_bytes=size_bytes, checksum=checksum,
            classification="restricted", status=status,
        )
        db.add(artifact)
    else:
        artifact.size_bytes = size_bytes
        artifact.checksum = checksum
        artifact.status = status
    return artifact


def create_version(db: Session, dataset: DataSet, payload: DraftDataSet, principal: Principal, correlation_id: str) -> DataSetVersion:
    locked_dataset = db.scalar(select(DataSet).where(DataSet.id == dataset.id).with_for_update())
    if locked_dataset is None:
        raise HTTPException(status_code=404, detail="Data set not found")
    version_number = locked_dataset.latest_version_number + 1
    version = DataSetVersion(
        id=uuid4(), dataset_id=dataset.id, workspace_id=dataset.workspace_id,
        version_number=version_number, lifecycle_state="draft",
        schema_scope=[field.model_dump() for field in payload.schema_scope],
        selected_fields=payload.selected_fields, provenance=_provenance_with_lock_configuration(
            db, payload.provenance.model_dump(mode="json", exclude_none=True)),
        generation_settings={"target_use": payload.permitted_use[0] if payload.permitted_use else (payload.description or ""), "maximum_records": payload.maximum_records, "output_format": payload.output_format},
        privacy_state="not_assessed", validation_state="not_run", publication_state="unpublished",
        expiry_at=payload.expiry_at, permitted_use=payload.permitted_use,
        validation_evidence={}, created_by=principal.subject,
    )
    version.fields = _dataset_fields(dataset, version, payload)
    db.add(version)
    db.flush()
    record_status_change(db, dataset, version, None, "draft", principal, correlation_id, "New version created")
    locked_dataset.latest_version_number = version_number
    previous = db.scalar(
        select(DataSetLineageEvent.id).where(DataSetLineageEvent.dataset_id == dataset.id)
        .order_by(DataSetLineageEvent.sequence_number.desc()).limit(1)
    )
    append_lineage_event(
        db, dataset, version,
        LineageEventCreate(event_type="version_created", stage="draft", parent_event_ids=[previous] if previous else [], safe_evidence={"summary": f"Version {version_number} draft created"}),
        principal,
    )
    return version


def create_initial_version(db: Session, dataset: DataSet, payload: DraftDataSet, principal: Principal, correlation_id: str) -> DataSetVersion:
    version = DataSetVersion(
        id=uuid4(), dataset_id=dataset.id, workspace_id=dataset.workspace_id,
        version_number=1, lifecycle_state="draft",
        schema_scope=[field.model_dump() for field in payload.schema_scope],
        selected_fields=payload.selected_fields, provenance=_provenance_with_lock_configuration(
            db, payload.provenance.model_dump(mode="json", exclude_none=True)),
        generation_settings={"target_use": payload.permitted_use[0] if payload.permitted_use else (payload.description or ""), "maximum_records": payload.maximum_records, "output_format": payload.output_format},
        privacy_state="not_assessed", validation_state="not_run", publication_state="unpublished",
        expiry_at=payload.expiry_at, permitted_use=payload.permitted_use,
        validation_evidence={}, created_by=principal.subject,
    )
    version.fields = _dataset_fields(dataset, version, payload)
    db.add(version)
    db.flush()
    record_status_change(db, dataset, version, None, "draft", principal, correlation_id, "Initial version created")
    append_lineage_event(
        db, dataset, version,
        LineageEventCreate(event_type="draft_created", stage="draft", parent_event_ids=[], safe_evidence={"summary": "Initial data set draft created"}),
        principal,
    )
    return version


def _dataset_fields(dataset: DataSet, version: DataSetVersion, payload: DraftDataSet) -> list[DataSetField]:
    result = []
    for ordinal, field in enumerate(payload.schema_scope):
        field_path = field.json_path or (f"{field.entity}.{field.field}" if field.entity else field.field)
        result.append(DataSetField(
            id=uuid4(), workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
            field_name=field.field, field_path=field_path, data_type=field.data_type, required=field.required,
            target_field_path=field_path, constraint_spec={}, ordinal=ordinal,
        ))
    return result


def lineage_for_version(db: Session, dataset_id: UUID, version: DataSetVersion) -> list[tuple[DataSetLineageEvent, list[UUID], list[UUID]]]:
    selected_ids = set(db.scalars(select(DataSetLineageEvent.id).where(
        DataSetLineageEvent.dataset_id == dataset_id, DataSetLineageEvent.version_id == version.id,
    )).all())
    included = set(selected_ids)
    frontier = set(selected_ids)
    while frontier:
        parent_ids = set(db.scalars(select(DataSetLineageEdge.parent_event_id).where(DataSetLineageEdge.child_event_id.in_(frontier))).all())
        new_ids = parent_ids - included
        if not new_ids:
            break
        included.update(new_ids)
        frontier = new_ids
    events = db.scalars(select(DataSetLineageEvent).where(DataSetLineageEvent.id.in_(included)).order_by(DataSetLineageEvent.sequence_number)).all() if included else []
    edges = db.scalars(select(DataSetLineageEdge).where(
        DataSetLineageEdge.parent_event_id.in_(included), DataSetLineageEdge.child_event_id.in_(included),
    )).all() if included else []
    parent_map: dict[UUID, list[UUID]] = {}
    child_map: dict[UUID, list[UUID]] = {}
    for edge in edges:
        parent_map.setdefault(edge.child_event_id, []).append(edge.parent_event_id)
        child_map.setdefault(edge.parent_event_id, []).append(edge.child_event_id)
    return [(event, parent_map.get(event.id, []), child_map.get(event.id, [])) for event in events]


def expire_version(version: DataSetVersion, now: datetime | None = None) -> None:
    current_time = now or datetime.now(UTC)
    if version.lifecycle_state != "published" or version.publication_state != "published":
        raise HTTPException(status_code=409, detail="Only a published version can expire")
    if version.expiry_at is None or version.expiry_at > current_time:
        raise HTTPException(status_code=409, detail="The version has not reached its expiry time")
    version.lifecycle_state = "expired"
    version.expired_at = current_time
