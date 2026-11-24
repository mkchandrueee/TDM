import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from tigr.access import (
    ensure_user,
    membership_role,
    record_audit,
    require_service_scope,
    workspace_role,
)
from tigr.config import get_settings
from tigr.connection_models import ConnectionStructureSnapshot, LockPropagationConfiguration, WorkspaceConnection
from tigr.dataset_schemas import (
    CreationPathway,
    DataSetDetail,
    DataSetSummary,
    DataSetVersionOut,
    DraftDataSet,
    LifecycleState,
    LineageEventCreate,
    LineageEventOut,
    PrivacyState,
    ValidationState,
    ValidationUpdate,
)
from tigr.dataset_services import (
    append_lineage_event,
    create_initial_version,
    create_version,
    dataset_records_privacy_ready,
    expire_version,
    lineage_for_version,
    read_dataset_records,
    record_generated_artifact,
    record_status_change,
    serialize_dataset_records,
    store_dataset_records,
)
from tigr.dataset_services import (
    record_validation as record_validation_run,
)
from tigr.datasets import DataSet, DataSetLineageEvent, DataSetVersion
from tigr.delivery import ProvisioningRun
from tigr.entity_claims import filter_dataset_records, identity_unavailable_ordinals, protected_ordinals, require_dataset_derivation_access
from tigr.governance import (
    DataSetLock,
    DataSetReservation,
    DataUseRequest,
    MaskingPolicy,
    MaskingRun,
    PiiFinding,
)
from tigr.iam import Workspace, WorkspaceMembership
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.jobs import Job, OutboxEvent
from tigr.platform_access import is_tigr_admin
from tigr.scenarios import ScenarioPlan
from tigr.schema_records import (
    ArtifactRecord,
    ConsumptionEvent,
    DatasetPublication,
    DataSetRecord,
    DatasetStatusHistory,
    ValidationResult,
    ValidationRun,
)
from tigr.source_mappings import SourceDataMapping, SourceDataPlan

router = APIRouter(prefix="/api/v1", tags=["data sets and lineage"])


def workspace_ids_for_dataset_list(db: Session, principal: Principal) -> list[UUID]:
    if principal.development_role is not None or is_tigr_admin(db, principal):
        return list(db.scalars(select(Workspace.id).where(Workspace.status == "active")).all())
    now = datetime.now(UTC)
    return list(db.scalars(
        select(WorkspaceMembership.workspace_id)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.subject == principal.subject,
            WorkspaceMembership.revoked_at.is_(None),
            WorkspaceMembership.valid_from <= now,
            or_(WorkspaceMembership.valid_until.is_(None), WorkspaceMembership.valid_until > now),
            Workspace.status == "active",
        )
    ).all())


def can_manage_dataset(db: Session, principal: Principal, workspace_id: UUID) -> HumanRole:
    role = workspace_role(db, principal, workspace_id)
    if principal.actor_type == "service" or role == HumanRole.QA_ENGINEER:
        raise HTTPException(status_code=403, detail="Data set changes require an Admin or Test Data Engineer")
    return role


def require_tde_dataset_editor(db: Session, principal: Principal, workspace_id: UUID) -> HumanRole:
    """Authorize mutations to data sets; workspace Admins have catalog read access only."""
    role = workspace_role(db, principal, workspace_id)
    if principal.actor_type == "service" or role != HumanRole.TEST_DATA_ENGINEER:
        raise HTTPException(status_code=403, detail="Only a Test Data Engineer can change data sets")
    return role


def dataset_owner_direct_access(dataset: DataSet, principal: Principal) -> bool:
    return dataset.owner_id == principal.subject and dataset.visibility == "private"


def authorize_dataset_read(db: Session, principal: Principal, dataset: DataSet, version: DataSetVersion, *, refresh: bool = False) -> HumanRole:
    if dataset.visibility == "private" and dataset.owner_id != principal.subject:
        raise HTTPException(status_code=404, detail="Data set not found")
    from tigr.ssd import enforce_dataset
    enforce_dataset(db, principal, dataset, version, refresh=refresh)
    role = workspace_role(db, principal, dataset.workspace_id)
    owner_direct_access = dataset_owner_direct_access(dataset, principal)
    if not refresh and dataset.creation_pathway == "self_servicing_data" and dataset.owner_id == principal.subject:
        from tigr.ssd import qa
        qa(db, principal, dataset.workspace_id)
        role = HumanRole.QA_ENGINEER
    # QA workspace membership provides discoverability for published shared
    # versions. Private owners access their own data directly.
    if role == HumanRole.QA_ENGINEER and not owner_direct_access and (
        version.lifecycle_state != "published"
        or version.publication_state != "published"
        or (version.expiry_at is not None and version.expiry_at <= datetime.now(UTC))
    ):
        raise HTTPException(status_code=404, detail="Data set version not found")
    return role


def version_out(version: DataSetVersion) -> DataSetVersionOut:
    result = DataSetVersionOut.model_validate(version)
    if version.expiry_at is not None and version.expiry_at <= datetime.now(UTC):
        return result.model_copy(update={"lifecycle_state": LifecycleState.EXPIRED})
    return result


def expire_allocations_if_version_expired(
    db: Session, dataset: DataSet, version: DataSetVersion, principal: Principal, request_id: str,
) -> bool:
    if version.expiry_at is None or version.expiry_at > datetime.now(UTC):
        return False
    from tigr.dataset_access_lifecycle import expire_dataset_allocations
    changed = expire_dataset_allocations(db, {version.id}, principal, request_id)
    if changed:
        db.commit()
    return True


def dataset_failure_message(db: Session, dataset_id: UUID, version: DataSetVersion) -> str | None:
    if version.lifecycle_state not in ("failed", "quarantined"):
        return None
    event = db.scalar(select(DataSetLineageEvent).where(
        DataSetLineageEvent.dataset_id == dataset_id,
        DataSetLineageEvent.version_id == version.id,
        DataSetLineageEvent.outcome == "failed",
    ).order_by(DataSetLineageEvent.sequence_number.desc()).limit(1))
    job = db.get(Job, event.job_id) if event and event.job_id else None
    message = (job.safe_summary or "").strip() if job else ""
    evidence = version.validation_evidence or {}
    reason = str(evidence.get("reason_code") or (event.safe_evidence or {}).get("reason_code") or "")
    if reason == "approved_identity_key_not_mapped":
        return ("The approved entity key is not included in this source mapping. Add the source table's primary or unique key "
                "under Connections → Entity lock keys, then create and regenerate a new data set version.")
    if reason == "approved_identity_configuration_unavailable":
        return ("This version's approved entity-lock configuration is missing or stale. Review Connections → Entity lock keys, "
                "then create and regenerate a new data set version.")
    if message.lower() in {
        "source query generation failed; review the query and connection",
        "source query generation failed",
    }:
        return ("TIGR could not complete the saved read-only source query. The underlying database error was not retained. "
                "Re-run Execute Query to confirm the query still runs and returns its saved output fields; if it fails, review the query and source database access.")
    if message:
        return message
    if reason == "source_conflict":
        return "Masking could not refresh source records because active entity locks conflicted with the refresh. Retry after the conflicting locks expire or are released."
    if reason == "source_database_query_failed":
        return "Masking could not read the source database. Check source availability, permissions, and the approved query, then retry."
    summary = evidence.get("summary") or (event.safe_evidence or {}).get("summary")
    return str(summary) if isinstance(summary, str) and summary.strip() else "Generation or privacy processing failed. Review the failed job details before retrying."


def version_out_with_source_mapping(db: Session, version: DataSetVersion) -> DataSetVersionOut:
    """Fill source-query lineage for versions created before it was stored in schema_scope."""
    result = version_out(version)
    provenance = version.provenance or {}
    query_id = provenance.get("source_query_id")
    if (not query_id or provenance.get("origin") != "source_backed"
            or all(item.get("entity") and item.get("source_field") for item in result.schema_scope)):
        return result
    try:
        from tigr.source_queries import SourceDataQuery
        query = db.get(SourceDataQuery, UUID(str(query_id)))
    except (TypeError, ValueError):
        query = None
    if query is None:
        return result

    request_event = db.scalar(select(OutboxEvent).where(
        OutboxEvent.aggregate_id == str(version.dataset_id),
        OutboxEvent.event_type == "source_query.generation_requested",
        OutboxEvent.payload["version_id"].as_string() == str(version.id),
    ).order_by(OutboxEvent.created_at.desc()).limit(1))
    output_mappings = (request_event.payload or {}).get("output_fields", []) if request_event else []
    output_by_alias = {
        item.get("output_field"): item.get("source_field")
        for item in output_mappings
        if isinstance(item, dict) and item.get("output_field") and item.get("source_field")
    }
    origins = {
        item.get("name"): item
        for item in (query.output_fields or [])
        if isinstance(item, dict) and item.get("name")
    }
    enriched = []
    changed = False
    for raw in result.schema_scope:
        field = dict(raw)
        alias = field.get("field")
        source_alias = output_by_alias.get(alias)
        origin = origins.get(source_alias or alias)
        if origin:
            if not field.get("entity") and origin.get("source_object"):
                field["entity"] = origin["source_object"]
                changed = True
            if not field.get("source_field") and origin.get("source_field"):
                field["source_field"] = origin["source_field"]
                changed = True
        enriched.append(field)
    return result.model_copy(update={"schema_scope": enriched}) if changed else result


def summary(dataset: DataSet, version: DataSetVersion, db: Session | None = None) -> DataSetSummary:
    provenance = version.provenance or {}
    source_connection_name = None
    federated_plan_name = None
    if provenance.get("source_connection_id") and db is not None:
        try:
            source_connection_id = UUID(str(provenance["source_connection_id"]))
        except (TypeError, ValueError):
            source_connection_id = None
        if source_connection_id:
            connection = db.scalar(select(WorkspaceConnection).where(
                WorkspaceConnection.id == source_connection_id,
                WorkspaceConnection.workspace_id == dataset.workspace_id,
            ))
            if connection:
                source_connection_name = connection.name
    if provenance.get("federated_plan_hash") and provenance.get("source_plan_id"):
        federated_plan_name = provenance.get("federated_plan_name")
        if not federated_plan_name and db is not None:
            try:
                source_plan_id = UUID(str(provenance["source_plan_id"]))
            except (TypeError, ValueError):
                source_plan_id = None
            if source_plan_id:
                source_plan = db.scalar(select(SourceDataPlan).where(
                    SourceDataPlan.id == source_plan_id,
                    SourceDataPlan.workspace_id == dataset.workspace_id,
                ))
                if source_plan and isinstance(source_plan.plan_data.get("federatedPlan"), dict):
                    federated_plan_name = source_plan.name
    return DataSetSummary(
        id=dataset.id, reference_code=dataset.reference_code, workspace_id=dataset.workspace_id, name=dataset.name, description=dataset.description,
        owner_id=dataset.owner_id, creation_pathway=dataset.creation_pathway,
        visibility=dataset.visibility,
        source_connection_name=source_connection_name, federated_plan_name=federated_plan_name,
        latest_version=version_out(version), created_at=dataset.created_at, updated_at=dataset.updated_at,
    )


def applied_masked_fields(db: Session, version: DataSetVersion) -> list[str]:
    if (version.provenance or {}).get("ssd_policy_stamp"):
        return list((version.validation_evidence or {}).get("ssd_masked_fields", []))
    run_id = (version.validation_evidence or {}).get("masking_run_id")
    if not run_id:
        return []
    try:
        parsed_run_id = UUID(str(run_id))
    except (TypeError, ValueError):
        return []
    run = db.scalar(select(MaskingRun).where(
        MaskingRun.id == parsed_run_id,
        MaskingRun.version_id == version.id,
        MaskingRun.status == "succeeded",
    ))
    if run is None:
        return []
    policy = db.get(MaskingPolicy, run.policy_id)
    if policy is None or not isinstance(policy.rules, list):
        return []
    return sorted({
        str(rule["field"])
        for rule in policy.rules
        if isinstance(rule, dict) and isinstance(rule.get("field"), str) and rule["field"]
    })


def entity_key_output_fields(db: Session, version: DataSetVersion) -> list[str]:
    """Map the approved source entity keys to this version's visible output names."""
    metadata = (version.provenance or {}).get("lock_metadata") or {}
    connection_value = (version.provenance or {}).get("source_connection_id")
    try:
        configuration = db.get(LockPropagationConfiguration, UUID(str(metadata.get("lock_configuration_id"))))
        snapshot = db.get(ConnectionStructureSnapshot, UUID(str(metadata.get("structure_snapshot_id"))))
        connection_id = UUID(str(connection_value)) if connection_value else None
    except (TypeError, ValueError):
        return []
    if (configuration is None or snapshot is None or connection_id is None or configuration.status != "approved"
            or configuration.connection_id != connection_id or configuration.structure_snapshot_id != snapshot.id
            or snapshot.connection_id != connection_id):
        return []
    key_sources = {
        (str(anchor.get("object_name") or ""), str(field_name))
        for anchor in (configuration.configuration or {}).get("anchors", [])
        for field_name in (anchor.get("key_fields") or [])
    }
    selected_fields = list(version.selected_fields or [])
    output_fields = []
    for index, field in enumerate(version.schema_scope or []):
        if (str(field.get("entity") or ""), str(field.get("source_field") or "")) not in key_sources:
            continue
        output_name = selected_fields[index] if index < len(selected_fields) and selected_fields[index] else field.get("field")
        if output_name:
            output_fields.append(str(output_name))
    return sorted(set(output_fields))


def identity_unavailable_guidance(db: Session, version: DataSetVersion) -> str | None:
    """Explain the approved key gap using only version and schema metadata."""
    if (version.provenance or {}).get("origin") != "source_backed":
        return None
    metadata = (version.provenance or {}).get("lock_metadata") or {}
    try:
        snapshot = db.get(ConnectionStructureSnapshot, UUID(str(metadata.get("structure_snapshot_id"))))
        config = db.get(LockPropagationConfiguration, UUID(str(metadata.get("lock_configuration_id"))))
    except (TypeError, ValueError):
        snapshot = config = None

    mapped: dict[str, set[str]] = {}
    for field in version.schema_scope or []:
        obj, source = field.get("entity"), field.get("source_field")
        if obj and source:
            mapped.setdefault(str(obj), set()).add(str(source))
    approved = set()
    if config is not None and config.status == "approved":
        for anchor in (config.configuration or {}).get("anchors", []):
            approved.add((str(anchor.get("object_name") or ""), tuple(anchor.get("key_fields") or [])))

    if snapshot is not None:
        for obj in (snapshot.structure or {}).get("objects", []):
            if not isinstance(obj, dict):
                continue
            object_name = str(obj.get("name") or "")
            candidates = []
            primary = obj.get("primary_key")
            if isinstance(primary, dict) and primary.get("fields"):
                candidates.append(primary)
            candidates.extend(item for item in (obj.get("unique_constraints") or []) if isinstance(item, dict))
            candidates.extend(item for item in (obj.get("unique_indexes") or []) if isinstance(item, dict))
            for candidate in candidates:
                key_fields = tuple(str(item) for item in (candidate.get("fields") or []))
                if (key_fields and set(key_fields).issubset(mapped.get(object_name, set()))
                        and (object_name, key_fields) not in approved):
                    key_label = ", ".join(f"{object_name}.{name}" for name in key_fields)
                    return (f"The source fields map {key_label}, but this version's approved entity-lock keys do not include it. "
                            f"Approve the key in Connections → Entity lock keys, then create and regenerate a new data set version.")
    return ("The approved entity key is missing, stale, or not mapped to this version. "
            "Review Connections → Entity lock keys and the source field mapping, then create and regenerate a new data set version.")


def get_dataset(db: Session, dataset_id: UUID) -> DataSet:
    dataset = db.get(DataSet, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Data set not found")
    return dataset


def get_version(db: Session, dataset: DataSet, version_number: int, *, for_update: bool = False) -> DataSetVersion:
    query = select(DataSetVersion).where(DataSetVersion.dataset_id == dataset.id, DataSetVersion.version_number == version_number)
    if for_update:
        query = query.with_for_update()
    version = db.scalar(query)
    if version is None:
        raise HTTPException(status_code=404, detail="Data set version not found")
    return version


def audit_dataset(db: Session, request: Request, principal: Principal, role: HumanRole | None, dataset: DataSet, action: str, version: DataSetVersion | None = None, metadata: dict | None = None) -> None:
    evidence = {"version_number": version.version_number} if version else {}
    evidence.update(metadata or {})
    record_audit(db, principal, request.state.request_id, action, "dataset_version" if version else "dataset", str(version.id if version else dataset.id), dataset.workspace_id, role, evidence)


def latest_version(db: Session, dataset: DataSet) -> DataSetVersion:
    return get_version(db, dataset, dataset.latest_version_number)


@router.get("/workspaces/{workspace_id}/viewable-datasets")
def list_viewable_datasets(
    workspace_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[DataSetSummary]:
    """List datasets available in View Data for a workspace role."""
    role = workspace_role(db, principal, workspace_id)
    if role == HumanRole.ADMIN and membership_role(db, principal.subject, workspace_id) == HumanRole.QA_ENGINEER:
        role = HumanRole.QA_ENGINEER
    if role not in {HumanRole.QA_ENGINEER, HumanRole.TEST_DATA_ENGINEER}:
        raise HTTPException(status_code=403, detail="View Data is available to QA Engineers and Test Data Engineers")
    expired_versions = set(db.scalars(select(DataSetVersion.id).where(
        DataSetVersion.workspace_id == workspace_id,
        DataSetVersion.expiry_at.is_not(None),
        DataSetVersion.expiry_at <= datetime.now(UTC),
    )).all())
    if expired_versions:
        from tigr.dataset_access_lifecycle import expire_dataset_allocations
        if expire_dataset_allocations(db, expired_versions, principal, request.state.request_id):
            db.commit()
    eligible = (
        DataSet.workspace_id == workspace_id,
        or_(DataSet.visibility.is_(None), DataSet.visibility == "public", DataSet.owner_id == principal.subject),
        DataSetVersion.lifecycle_state == "published",
        DataSetVersion.publication_state == "published",
        DataSetVersion.validation_state == "passed",
        DataSetVersion.privacy_state == "compliant",
        or_(DataSetVersion.expiry_at.is_(None), DataSetVersion.expiry_at > datetime.now(UTC)),
    )
    if role == HumanRole.TEST_DATA_ENGINEER:
        rows = db.execute(
            select(DataSet, DataSetVersion)
            .join(DataSetVersion, DataSetVersion.dataset_id == DataSet.id)
            .where(*eligible)
            .order_by(DataSet.updated_at.desc(), DataSet.id, DataSetVersion.version_number.desc())
        ).all()
        seen: set[UUID] = set()
        result: list[DataSetSummary] = []
        for dataset, version in rows:
            if dataset.id in seen:
                continue
            if dataset_records_privacy_ready(version):
                seen.add(dataset.id)
                result.append(summary(dataset, version, db))
        return result

    # QA users can browse shared data sets they currently reserved and locked.
    # Private data-set owners and SSD owners have direct owner-only access.
    now = datetime.now(UTC)
    latest_published_version = select(func.max(DataSetVersion.version_number)).where(
        DataSetVersion.dataset_id == DataSet.id,
        DataSetVersion.lifecycle_state == "published",
        DataSetVersion.publication_state == "published",
    ).correlate(DataSet).scalar_subquery()
    rows = db.execute(
        select(DataSet, DataSetVersion)
        .join(DataSetVersion, DataSetVersion.dataset_id == DataSet.id)
        .join(DataSetReservation, DataSetReservation.version_id == DataSetVersion.id)
        .join(DataUseRequest, DataUseRequest.id == DataSetReservation.request_id)
        .join(DataSetLock, DataSetLock.version_id == DataSetVersion.id)
        .where(
            *eligible,
            DataSetVersion.version_number == latest_published_version,
            DataSetReservation.actor_id == principal.subject,
            DataSetReservation.status == "active",
            DataSetReservation.starts_at <= now,
            DataSetReservation.ends_at > now,
            DataUseRequest.requester_id == principal.subject,
            DataUseRequest.status == "approved",
            DataSetLock.actor_id == principal.subject,
            DataSetLock.status == "active",
            DataSetLock.expires_at > now,
            DataSetVersion.lifecycle_state == "published",
            DataSetVersion.publication_state == "published",
            DataSetVersion.validation_state == "passed",
            DataSetVersion.privacy_state == "compliant",
            or_(DataSetVersion.expiry_at.is_(None), DataSetVersion.expiry_at > now),
        )
        .order_by(DataSet.updated_at.desc(), DataSet.id)
    ).all()
    owned_direct = db.execute(select(DataSet, DataSetVersion).join(DataSetVersion, DataSetVersion.dataset_id == DataSet.id).where(
        *eligible, DataSetVersion.version_number == latest_published_version,
        DataSet.owner_id == principal.subject, DataSet.visibility == "private")).all()
    unique = {dataset.id: (dataset, version) for dataset, version in [*rows, *owned_direct]}
    return [summary(dataset, version, db) for dataset, version in unique.values() if dataset_records_privacy_ready(version)]


@router.get("/datasets")
def list_datasets(
    workspace_id: UUID | None = None,
    search: str | None = Query(default=None, max_length=180),
    lifecycle_state: LifecycleState | None = None,
    creation_pathway: CreationPathway | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    ensure_user(db, principal)
    if workspace_id:
        role = workspace_role(db, principal, workspace_id)
        workspace_ids = [workspace_id]
        roles = {workspace_id: role}
    else:
        workspace_ids = workspace_ids_for_dataset_list(db, principal)
        roles = {}
        for visible_id in workspace_ids:
            try:
                roles[visible_id] = workspace_role(db, principal, visible_id)
            except HTTPException:
                continue
        workspace_ids = list(roles)
    if not workspace_ids:
        return {"items": [], "page": page, "page_size": page_size, "total": 0, "has_more": False}

    query = select(DataSet, DataSetVersion).join(DataSetVersion, DataSetVersion.dataset_id == DataSet.id).where(DataSet.workspace_id.in_(workspace_ids))
    if lifecycle_state == LifecycleState.PUBLISHED:
        latest_published = select(func.max(DataSetVersion.version_number)).where(
            DataSetVersion.dataset_id == DataSet.id,
            DataSetVersion.lifecycle_state == "published",
            DataSetVersion.publication_state == "published",
        ).correlate(DataSet).scalar_subquery()
        query = query.where(DataSetVersion.version_number == latest_published)
    qa_workspace_ids = [wid for wid, role in roles.items() if role == HumanRole.QA_ENGINEER]
    if qa_workspace_ids:
        # A QA member can only see published versions; object grants are checked below before count/search/paging.
        query = query.where(
            or_(DataSet.workspace_id.in_(qa_workspace_ids), DataSetVersion.version_number == DataSet.latest_version_number),
            or_(
                ~DataSet.workspace_id.in_(qa_workspace_ids),
                (DataSet.creation_pathway == "self_servicing_data") & (DataSet.owner_id == principal.subject),
                (DataSet.visibility == "private") & (DataSet.owner_id == principal.subject),
                (DataSetVersion.lifecycle_state == "published") & (DataSetVersion.publication_state == "published"),
            ),
        )
    elif lifecycle_state is None:
        query = query.where(DataSetVersion.version_number == DataSet.latest_version_number)
    if creation_pathway:
        query = query.where(DataSet.creation_pathway == creation_pathway.value)
    rows = db.execute(query.order_by(DataSet.updated_at.desc(), DataSet.id, DataSetVersion.version_number.desc())).all()
    visible: list[tuple[DataSet, DataSetVersion]] = []
    seen_datasets: set[UUID] = set()
    for dataset, version in rows:
        if dataset.visibility == "private" and dataset.owner_id != principal.subject:
            continue
        if dataset.id in seen_datasets:
            continue
        role = roles[dataset.workspace_id]
        if role == HumanRole.QA_ENGINEER:
            try:
                authorize_dataset_read(db, principal, dataset, version)
            except HTTPException:
                continue
        if lifecycle_state and version.lifecycle_state != lifecycle_state.value:
            continue
        if search and search.casefold() not in f"{dataset.name} {dataset.reference_code} {dataset.description or ''} {dataset.owner_id}".casefold():
            continue
        seen_datasets.add(dataset.id)
        visible.append((dataset, version))
    total = len(visible)
    page_rows = visible[(page - 1) * page_size : page * page_size]
    items = []
    for dataset, version in page_rows:
        dataset_summary = summary(dataset, version, db)
        mapped_version = version_out_with_source_mapping(db, version)
        if mapped_version.schema_scope != dataset_summary.latest_version.schema_scope:
            dataset_summary = dataset_summary.model_copy(update={"latest_version": mapped_version})
        items.append(dataset_summary.model_dump(mode="json"))
    return {"items": items, "page": page, "page_size": page_size, "total": total, "has_more": page * page_size < total}


@router.post("/workspaces/{workspace_id}/datasets", status_code=status.HTTP_201_CREATED)
def create_dataset(
    workspace_id: UUID,
    payload: DraftDataSet,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    role = require_tde_dataset_editor(db, principal, workspace_id)
    if payload.creation_pathway.value == "self_servicing_data":
        raise HTTPException(422, "SSD datasets must be generated through their authorized draft jobs")
    ensure_user(db, principal)
    dataset = DataSet(
        id=uuid4(), workspace_id=workspace_id, name=payload.name, description=payload.description,
        owner_id=principal.subject, creation_pathway=payload.creation_pathway.value, latest_version_number=1,
    )
    db.add(dataset)
    db.flush()
    version = create_initial_version(db, dataset, payload, principal, request.state.request_id)
    audit_dataset(db, request, principal, role, dataset, "dataset.draft_created", version, {"creation_pathway": dataset.creation_pathway})
    db.commit()
    db.refresh(dataset)
    db.refresh(version)
    return summary(dataset, version, db).model_dump(mode="json")


@router.get("/datasets/{dataset_id}/generation-state")
def get_dataset_generation_state(
    dataset_id: UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    dataset = get_dataset(db, dataset_id)
    version = latest_version(db, dataset)
    authorize_dataset_read(db, principal, dataset, version)
    has_generation_data = (
        version.provenance.get("origin") != "not_set"
        or bool(version.schema_scope)
        or bool(version.selected_fields)
        or version.record_count is not None
        or bool(version.validation_evidence)
    )
    return {"dataset_id": str(dataset.id), "version_number": version.version_number, "has_generation_data": has_generation_data}


@router.get("/datasets/{dataset_id}", response_model=DataSetDetail)
def get_dataset_detail(
    dataset_id: UUID,
    version_number: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> DataSetDetail:
    dataset = get_dataset(db, dataset_id)
    role = workspace_role(db, principal, dataset.workspace_id)
    version_query = select(DataSetVersion).where(DataSetVersion.dataset_id == dataset.id)
    if role == HumanRole.QA_ENGINEER and not dataset_owner_direct_access(dataset, principal):
        version_query = version_query.where(DataSetVersion.lifecycle_state == "published", DataSetVersion.publication_state == "published",
            or_(DataSetVersion.expiry_at.is_(None), DataSetVersion.expiry_at > datetime.now(UTC)))
    versions = list(db.scalars(version_query.order_by(DataSetVersion.version_number.desc())).all())
    if not versions:
        raise HTTPException(status_code=404, detail="Data set not found")
    target_number = version_number or versions[0].version_number
    version = next((row for row in versions if row.version_number == target_number), None)
    if version is None:
        raise HTTPException(status_code=404, detail="Data set version not found")
    authorize_dataset_read(db, principal, dataset, version)
    version_expired = expire_allocations_if_version_expired(
        db, dataset, version, principal, "dataset-details-expiry-check",
    )
    visible_versions = versions if role != HumanRole.QA_ENGINEER or dataset_owner_direct_access(dataset, principal) else [row for row in versions if row.publication_state == "published"]
    lineage = []
    lineage_rows = {}
    for lineage_version in visible_versions:
        for event, parents, children in lineage_for_version(db, dataset.id, lineage_version):
            lineage_rows[event.id] = (event, parents, children)
    for event, parents, children in sorted(lineage_rows.values(), key=lambda row: row[0].sequence_number):
        event_version = get_version(db, dataset, db.scalar(select(DataSetVersion.version_number).where(DataSetVersion.id == event.version_id)))
        lineage.append(LineageEventOut(
            id=event.id, dataset_id=event.dataset_id, version_id=event.version_id, version_number=event_version.version_number,
            sequence_number=event.sequence_number, event_type=event.event_type, stage=event.stage,
            source_reference=event.source_reference, artifact_reference=event.artifact_reference,
            mapping_version=event.mapping_version, scenario_version=event.scenario_version, job_id=event.job_id,
            policy_version=event.policy_version, validation_version=event.validation_version,
            actor_id=event.actor_id, actor_type=event.actor_type, occurred_at=event.occurred_at,
            outcome=event.outcome, safe_evidence=event.safe_evidence, parent_event_ids=parents, child_event_ids=children,
        ))
    access = {"owner_id": dataset.owner_id, "permitted_use": version.permitted_use, "workspace_id": str(dataset.workspace_id)}
    summary_version = versions[0] if role == HumanRole.QA_ENGINEER else latest_version(db, dataset)
    status_history = db.scalars(
        select(DatasetStatusHistory).where(
            DatasetStatusHistory.workspace_id == dataset.workspace_id,
            DatasetStatusHistory.dataset_id == dataset.id,
            DatasetStatusHistory.version_id == version.id,
        ).order_by(DatasetStatusHistory.changed_at.desc()).limit(50)
    ).all()
    validation_runs = db.scalars(
        select(ValidationRun).where(
            ValidationRun.workspace_id == dataset.workspace_id,
            ValidationRun.dataset_id == dataset.id,
            ValidationRun.version_id == version.id,
        ).order_by(ValidationRun.started_at.desc()).limit(20)
    ).all()
    validation_run_ids = [run.id for run in validation_runs]
    validation_results = db.scalars(select(ValidationResult).where(
        ValidationResult.workspace_id == dataset.workspace_id,
        ValidationResult.validation_run_id.in_(validation_run_ids),
    )).all() if validation_run_ids else []
    results_by_run: dict[UUID, list[dict[str, Any]]] = {}
    for result in validation_results:
        results_by_run.setdefault(result.validation_run_id, []).append({
            "check_code": result.check_code, "severity": result.severity, "status": result.status,
            "field_path": result.field_path, "safe_message": result.safe_message,
            "observed_count": result.observed_count, "expected_summary": result.expected_summary,
        })
    masked_fields = applied_masked_fields(db, version)
    test_data_requirements = None
    provenance = version.provenance or {}
    source_sql = None
    if dataset.creation_pathway == "self_servicing_data":
        # SSD query text is encrypted inside its revision. Return it to the
        # owner or to workspace viewers of a public dataset; parameters and
        # other secured inputs stay hidden.
        revision_id = provenance.get("ssd_revision_id")
        can_view_ssd_query = (
            dataset.owner_id == principal.subject or dataset.visibility == "public"
        )
        if revision_id and can_view_ssd_query:
            from tigr.ssd import unseal
            from tigr.ssd_models import SsdDraft, SsdRevision

            try:
                parsed_revision_id = UUID(str(revision_id))
            except (TypeError, ValueError):
                parsed_revision_id = None
            revision = db.scalar(
                select(SsdRevision)
                .join(SsdDraft, SsdDraft.id == SsdRevision.draft_id)
                .where(
                    SsdRevision.id == parsed_revision_id,
                    SsdDraft.dataset_id == dataset.id,
                    SsdDraft.workspace_id == dataset.workspace_id,
                    SsdDraft.owner_id == dataset.owner_id,
                )
            ) if parsed_revision_id else None
            if revision is not None:
                try:
                    secured_inputs = unseal(revision.secured_inputs)
                    query_text = secured_inputs.get("sql")
                    source_sql = query_text if isinstance(query_text, str) else None
                except Exception:
                    source_sql = None
    else:
        source_sql = provenance.get("source_sql") or provenance.get("generated_query")
        source_query_id = provenance.get("source_query_id")
        if not source_sql and source_query_id:
            from tigr.source_queries import SourceDataQuery
            try:
                source_query = db.get(SourceDataQuery, UUID(str(source_query_id)))
            except (TypeError, ValueError):
                source_query = None
            if source_query is not None and source_query.workspace_id == dataset.workspace_id:
                source_sql = source_query.query_text
        scenario_id = provenance.get("scenario_id")
        if not source_sql and scenario_id:
            try:
                scenario_plan = db.get(ScenarioPlan, UUID(str(scenario_id)))
            except (TypeError, ValueError):
                scenario_plan = None
            if scenario_plan is not None and scenario_plan.workspace_id == dataset.workspace_id:
                source_sql = (scenario_plan.logical_plan or {}).get("generated_query")
    if dataset.creation_pathway == "source_to_data":
        source_plan = db.scalar(
            select(SourceDataPlan)
            .where(
                SourceDataPlan.workspace_id == dataset.workspace_id,
                SourceDataPlan.plan_data["draftDatasetId"].as_string() == str(dataset.id),
            )
            .order_by(SourceDataPlan.updated_at.desc())
            .limit(1)
        )
        if source_plan:
            value = source_plan.plan_data.get("testDataRequirements")
            test_data_requirements = value.strip() if isinstance(value, str) and value.strip() else None
    else:
        scenario_plan = db.scalar(
            select(ScenarioPlan)
            .where(
                ScenarioPlan.workspace_id == dataset.workspace_id,
                or_(
                    ScenarioPlan.dataset_id == dataset.id,
                    ScenarioPlan.logical_plan["draft_data"]["draft_dataset_id"].as_string() == str(dataset.id),
                ),
            )
            .order_by(ScenarioPlan.updated_at.desc())
            .limit(1)
        )
        if scenario_plan and scenario_plan.requirement.strip():
            test_data_requirements = scenario_plan.requirement.strip()
    preview_limit = 1 if role == HumanRole.QA_ENGINEER else 10
    filtered_records, _allowed_record_count, blocked_record_count = filter_dataset_records(db, version, principal.subject)
    identity_unavailable_count = len(identity_unavailable_ordinals(db, version))
    return DataSetDetail(
        data_set=summary(dataset, summary_version, db), selected_version=version_out_with_source_mapping(db, version),
        versions=[version_out_with_source_mapping(db, row) for row in visible_versions], lineage=lineage,
        test_data_requirements=test_data_requirements,
        source_sql=source_sql,
        masked_fields=masked_fields,
        entity_key_fields=entity_key_output_fields(db, version),
        status_history=[{"from_status": row.from_status, "to_status": row.to_status,
                         "changed_by": row.changed_by, "changed_at": row.changed_at,
                         "reason": row.reason} for row in status_history],
        validation_runs=[{"id": str(run.id), "validation_type": run.validation_type, "status": run.status,
                          "started_at": run.started_at, "completed_at": run.completed_at,
                          "summary": run.summary, "results": results_by_run.get(run.id, [])}
                         for run in validation_runs],
        access=access,
        # QA members may preview one row from a published version. For source-
        # backed data, dataset_records_privacy_ready ensures successful masking
        # and compliant validation before any stored values are returned.
        sample_records=filtered_records[:preview_limit] if not version_expired and dataset_records_privacy_ready(version) else [],
        blocked_record_count=blocked_record_count,
        identity_unavailable_record_count=identity_unavailable_count,
        identity_unavailable_guidance=(identity_unavailable_guidance(db, version)
                                       if identity_unavailable_count else None),
        failure_message=dataset_failure_message(db, dataset.id, version),
    )


@router.post("/datasets/{dataset_id}/versions", status_code=status.HTTP_201_CREATED, response_model=None)
def create_dataset_version(
    dataset_id: UUID,
    payload: DraftDataSet,
    request: Request,
    background_tasks: BackgroundTasks,
    base_version_number: int | None = Query(default=None, ge=1),
    force_regenerate: bool = Query(default=False),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> DataSetVersionOut | dict[str, Any]:
    dataset = get_dataset(db, dataset_id)
    role = require_tde_dataset_editor(db, principal, dataset.workspace_id)
    if payload.creation_pathway.value != dataset.creation_pathway:
        raise HTTPException(status_code=409, detail="Creation pathway is fixed for a data set")
    if dataset.creation_pathway == "self_servicing_data":
        raise HTTPException(422, "Edit the SSD draft and regenerate to create a governed version")
    generation_job_id: UUID | None = None
    base_version: DataSetVersion | None = None
    source_rows: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {}
    scenario_plan: ScenarioPlan | None = None
    source_replay: dict[str, Any] | None = None
    if base_version_number is not None:
        base_version = db.scalar(select(DataSetVersion).where(
            DataSetVersion.dataset_id == dataset.id,
            DataSetVersion.version_number == base_version_number,
        ))
        if base_version is None:
            raise HTTPException(status_code=404, detail="Base data set version not found")
        is_published = base_version.lifecycle_state == "published" and base_version.publication_state == "published"
        is_quarantined = base_version.lifecycle_state == "quarantined"
        is_ready = base_version.lifecycle_state == "ready"
        is_masked_draft = base_version.lifecycle_state == "draft" and base_version.validation_state == "passed" and bool((base_version.validation_evidence or {}).get("masking_run_id"))
        if role != HumanRole.TEST_DATA_ENGINEER and not (role == HumanRole.ADMIN and (is_quarantined or is_ready or is_masked_draft)):
            raise HTTPException(status_code=403, detail="Only a Test Data Engineer can edit a published version; an Admin or Test Data Engineer can replace a quarantined or masked version")
        if not is_published and not is_quarantined and not is_ready and not is_masked_draft:
            raise HTTPException(status_code=409, detail="A new editable version can only be based on a published, quarantined, or successfully masked version")
        if force_regenerate and not (is_ready or is_masked_draft):
            raise HTTPException(status_code=409, detail="Fresh data regeneration is available only for a ready or previously masked version")
        base_schema = base_version.schema_scope or []
        next_schema = [field.model_dump() for field in payload.schema_scope]
        if len(base_schema) != len(next_schema) or len(base_version.selected_fields or []) != len(payload.selected_fields):
            raise HTTPException(status_code=422, detail="A copied version must keep the published schema fields. Create a new data set through a generation workflow to add or remove fields.")
        for previous, updated in zip(base_schema, next_schema):
            if any(previous.get(key) != updated.get(key) for key in ("field", "data_type", "entity", "source_field", "json_path", "required")):
                raise HTTPException(status_code=422, detail="A copied version can rename output fields, but source fields and types must stay the same")

        require_dataset_derivation_access(db, base_version, principal.subject)
        source_rows = read_dataset_records(db, base_version)
        old_names = list(base_version.selected_fields or [])
        new_names = list(payload.selected_fields)
        provenance = dict(base_version.provenance or {})
        scenario_id = provenance.get("scenario_id")
        scenario_plan = db.get(ScenarioPlan, UUID(str(scenario_id))) if scenario_id else None
        if (is_quarantined or force_regenerate or provenance.get("origin") == "source_backed") and provenance.get("origin") == "source_backed" and scenario_plan is None:
            if provenance.get("source_query_id"):
                from tigr.source_queries import SourceDataQuery
                query_id = UUID(str(provenance["source_query_id"]))
                query = db.get(SourceDataQuery, query_id)
                original_request = db.scalar(select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == str(dataset.id),
                    OutboxEvent.event_type == "source_query.generation_requested",
                ).order_by(OutboxEvent.created_at.desc()).limit(1))
                original_payload = original_request.payload if original_request else {}
                replay_output_fields = list(original_payload.get("output_fields") or [])
                if query and not replay_output_fields:
                    replay_output_fields = [
                        {"source_field": item.get("name"), "output_field": base_version.selected_fields[index]}
                        for index, item in enumerate(query.output_fields or [])
                        if index < len(base_version.selected_fields or [])
                    ]
                replay_fields = [item.get("output_field") for item in replay_output_fields]
                if not query or query.workspace_id != dataset.workspace_id or not replay_fields:
                    raise HTTPException(status_code=409, detail="The original source query is unavailable. Start a new Source to Data query generation to create a replacement version.")
                source_replay = {
                    "kind": "query", "query_id": query.id,
                    "output_fields": replay_output_fields,
                    "target_connection_id": original_payload.get("target_connection_id"),
                }
            else:
                original_request = db.scalar(select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == str(dataset.id),
                    OutboxEvent.event_type == "source_data.generation_requested",
                ).order_by(OutboxEvent.created_at.desc()).limit(1))
                original_payload = original_request.payload if original_request else {}
                try:
                    mapping_id = UUID(str(original_payload.get("mapping_id") or provenance.get("source_mapping_id")))
                except (TypeError, ValueError):
                    mapping_id = None
                mapping = db.get(SourceDataMapping, mapping_id) if mapping_id else None
                mapping_ref = str(original_payload.get("mapping_version") or provenance.get("mapping_version") or "")
                if not mapping or mapping.workspace_id != dataset.workspace_id:
                    try:
                        mapping_name, version_text = mapping_ref.rsplit(":v", 1)
                        mapping_version_number = int(version_text)
                    except (TypeError, ValueError):
                        mapping_name, mapping_version_number = "", 0
                    if mapping_name and mapping_version_number > 0:
                        mapping = db.scalar(select(SourceDataMapping).where(
                            SourceDataMapping.workspace_id == dataset.workspace_id,
                            SourceDataMapping.name == mapping_name,
                            SourceDataMapping.version_number == mapping_version_number,
                        ))
                replay_fields = list(original_payload.get("selected_fields") or base_version.selected_fields or [])
                if not mapping or mapping.workspace_id != dataset.workspace_id:
                    raise HTTPException(status_code=409, detail=f"The saved source mapping {mapping_ref or '(version reference unavailable)'} is not present in this workspace. Restore that mapping version before creating a replacement.")
                mapped_outputs = {str(item.get("target_field")) for item in mapping.fields or []}
                replay_fields = [field for field in replay_fields if field in mapped_outputs]
                if not replay_fields:
                    raise HTTPException(status_code=409, detail=f"The saved source mapping {mapping_ref or '(version reference unavailable)'} has no fields in common with this data set version, so it cannot be replayed.")
                source_replay = {
                    "kind": "mapping", "mapping_id": mapping.id,
                    "selected_fields": replay_fields,
                    "predicates": list(original_payload.get("predicates") or provenance.get("source_predicates") or []),
                    "target_connection_id": original_payload.get("target_connection_id"),
                }
            replay_fields = [item.get("output_field") for item in source_replay["output_fields"]] if source_replay["kind"] == "query" else source_replay["selected_fields"]
            if list(payload.selected_fields) != replay_fields:
                raise HTTPException(status_code=422, detail="Keep output field names unchanged when regenerating a quarantined source-backed version.")
            generation_job_id = uuid4()
            payload = payload.model_copy(update={
                "provenance": payload.provenance.model_copy(update={"job_id": generation_job_id}),
            })
        source_backed = provenance.get("origin") == "source_backed"
        will_regenerate = bool(source_replay or (scenario_plan and (source_backed or is_quarantined or force_regenerate or payload.maximum_records != len(source_rows))))
        if will_regenerate:
            if new_names != old_names:
                raise HTTPException(status_code=422, detail="Keep output field names unchanged when regenerating a different record count. Create the new version first, then rename fields in a separate edit.")
            if scenario_plan and (scenario_plan.workspace_id != dataset.workspace_id or scenario_plan.dataset_id != dataset.id):
                raise HTTPException(status_code=409, detail="The original scenario is no longer linked to this data set")
            generation_job_id = uuid4()
            payload = payload.model_copy(update={
                "provenance": payload.provenance.model_copy(update={"job_id": generation_job_id}),
            })

    version = create_version(db, dataset, payload, principal, request.state.request_id)
    if base_version_number is not None and base_version is not None and not will_regenerate:
        # A record copy retains the schema/configuration interpretation of its
        # parent version. Never relabel old rows with a newer approval.
        copied_provenance = dict(version.provenance or {})
        parent_lock_metadata = (base_version.provenance or {}).get("lock_metadata")
        copied_provenance["lock_metadata"] = parent_lock_metadata or {"configuration_status": "missing_legacy"}
        version.provenance = copied_provenance
    if force_regenerate and base_version is not None:
        from tigr.governance import PiiFinding
        prior_findings = db.scalars(select(PiiFinding).where(PiiFinding.version_id == base_version.id)).all()
        for finding in prior_findings:
            db.add(PiiFinding(
                id=uuid4(), workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
                field=finding.field, suggested_type=finding.suggested_type,
                suggested_transformation=finding.suggested_transformation,
                classification=finding.classification, confidence=finding.confidence,
                reviewed_by=finding.reviewed_by, reviewed_at=finding.reviewed_at,
            ))
    if base_version_number is not None:
        if source_replay:
            request_id = request.state.request_id
            target_id = UUID(str(source_replay["target_connection_id"])) if source_replay.get("target_connection_id") else None
            query_replay = source_replay["kind"] == "query"
            replay_type = "source_query_generation" if query_replay else "source_data_generation"
            db.add(Job(
                id=generation_job_id, job_type=replay_type, status="queued",
                workspace_id=str(dataset.workspace_id), scope_key=f"workspace:{dataset.workspace_id}",
                idempotency_key=f"dataset-version:{dataset.id}:v{version.version_number}:{source_replay['kind']}",
                actor_type=principal.actor_type, actor_id=principal.subject, actor_role=role.value,
                request_id=request_id, progress=0,
                safe_summary=f"Re-running the approved source {'query' if query_replay else 'mapping'} for version {version.version_number}",
            ))
            db.flush()
            version.generation_settings = {
                **(version.generation_settings or {}),
                "maximum_records": payload.maximum_records,
                "output_format": payload.output_format,
            }
            version.validation_state = "not_run"
            version.privacy_state = "not_assessed"
            version.lifecycle_state = "draft"
            version.record_count = None
            base_lineage_event = db.scalar(select(DataSetLineageEvent.id).where(
                DataSetLineageEvent.dataset_id == dataset.id,
                DataSetLineageEvent.version_id == base_version.id,
            ).order_by(DataSetLineageEvent.sequence_number.desc()).limit(1))
            append_lineage_event(
                db, dataset, version,
                LineageEventCreate(
                    event_type="version_generation_requested", stage="generation", job_id=generation_job_id,
                    source_reference=str(provenance.get("source_connection_id") or ""),
                    mapping_version=f"query:{source_replay['query_id']}" if query_replay else provenance.get("mapping_version"),
                    parent_event_ids=[base_lineage_event] if base_lineage_event else [],
                    safe_evidence={"summary": f"Re-running the approved source {'query' if query_replay else 'mapping'} for version {version.version_number}", "field_count": len(replay_fields)},
                ), principal,
            )
            db.add(OutboxEvent(
                id=uuid4(), aggregate_type="dataset", aggregate_id=str(dataset.id),
                workspace_id=str(dataset.workspace_id),
                event_type="source_query.generation_requested" if query_replay else "source_data.generation_requested",
                payload=(
                    {"query_id": str(source_replay["query_id"]), "job_id": str(generation_job_id),
                     "dataset_id": str(dataset.id), "version_id": str(version.id), "volume": payload.maximum_records,
                     "output_format": payload.output_format, "output_fields": source_replay["output_fields"],
                     "target_connection_id": str(target_id) if target_id else None}
                    if query_replay else
                    {"job_id": str(generation_job_id), "mapping_id": str(source_replay["mapping_id"]),
                     "dataset_id": str(dataset.id), "version_id": str(version.id),
                     "mapping_version": provenance.get("mapping_version"),
                     "selected_fields": source_replay["selected_fields"], "volume": payload.maximum_records,
                     "output_format": payload.output_format, "predicates": source_replay["predicates"],
                     "target_connection_id": str(target_id) if target_id else None}
                ), request_id=request_id,
            ))
            if get_settings().inline_jobs:
                if query_replay:
                    from tigr.source_to_data import run_source_query_job
                    background_tasks.add_task(
                        run_source_query_job, source_replay["query_id"], generation_job_id, dataset.id, version.id,
                        payload.maximum_records, payload.output_format, source_replay["output_fields"], target_id,
                    )
                else:
                    from tigr.source_to_data import run_source_data_job
                    background_tasks.add_task(
                        run_source_data_job, source_replay["mapping_id"], generation_job_id, dataset.id, version.id,
                        source_replay["selected_fields"], payload.maximum_records, payload.output_format,
                        source_replay["predicates"], target_id,
                    )
        elif scenario_plan and (source_backed or is_quarantined or force_regenerate or payload.maximum_records != len(source_rows)):
            scenario_plan.volume = payload.maximum_records
            scenario_plan.target_use = payload.permitted_use[0] if payload.permitted_use else scenario_plan.target_use
            scenario_plan.state = "running"
            assert generation_job_id is not None
            scenario_plan.job_id = generation_job_id
            request_id = request.state.request_id
            db.add(Job(
                id=generation_job_id, job_type="scenario_generation", status="queued",
                workspace_id=str(dataset.workspace_id), scope_key=f"workspace:{dataset.workspace_id}",
                idempotency_key=f"dataset-version:{dataset.id}:v{version.version_number}",
                actor_type=principal.actor_type, actor_id=principal.subject, actor_role=role.value,
                request_id=request_id, progress=0,
                safe_summary=f"Generating {payload.maximum_records} records for version {version.version_number}",
            ))
            # Lineage events enforce a database FK to jobs. Sessions have autoflush
            # disabled, so persist the job explicitly before appending its event.
            db.flush()
            version.generation_settings = {
                **(version.generation_settings or {}),
                "maximum_records": payload.maximum_records,
                "output_format": payload.output_format,
            }
            version.validation_state = "not_run"
            version.privacy_state = "not_assessed"
            version.lifecycle_state = "draft"
            version.record_count = None
            base_lineage_event = db.scalar(select(DataSetLineageEvent.id).where(
                DataSetLineageEvent.dataset_id == dataset.id,
                DataSetLineageEvent.version_id == base_version.id,
            ).order_by(DataSetLineageEvent.sequence_number.desc()).limit(1))
            append_lineage_event(
                db, dataset, version,
                LineageEventCreate(
                    event_type="version_generation_requested", stage="generation", job_id=generation_job_id,
                    scenario_version=str(scenario_plan.id),
                    source_reference=str(scenario_plan.source_connection_id) if scenario_plan.source_connection_id else None,
                    parent_event_ids=[base_lineage_event] if base_lineage_event else [],
                    safe_evidence={"summary": f"Requested {payload.maximum_records} records for version {version.version_number}", "field_count": len(new_names)},
                ), principal,
            )
            db.add(OutboxEvent(
                id=uuid4(), aggregate_type="dataset_version", aggregate_id=str(version.id),
                workspace_id=str(dataset.workspace_id), event_type="scenario.generation_requested",
                payload={"plan_id": str(scenario_plan.id), "job_id": str(generation_job_id),
                         "dataset_id": str(dataset.id), "version_id": str(version.id), "require_exact_count": True},
                request_id=request_id,
            ))
            if get_settings().inline_jobs:
                from tigr.scenario_api import run_scenario_job
                background_tasks.add_task(run_scenario_job, scenario_plan.id, generation_job_id, dataset.id, version.id, True)
        else:
            if payload.maximum_records < len(source_rows):
                raise HTTPException(status_code=422, detail=f"Maximum records must be at least the {len(source_rows)} records being carried forward")
            copied_rows: list[dict[str, Any]] = []
            for row in source_rows:
                if any(name not in row for name in old_names):
                    raise HTTPException(status_code=409, detail="Published records do not match the saved output fields and cannot be copied safely")
                copied_rows.append({new: row[old] for old, new in zip(old_names, new_names)})
            # Preserve the published values, applying only the requested output-name mapping.
            # In workspaces without PII & Masking, validation alone gates publication.
            version.provenance = provenance
            pii_masking_required = version.provenance.get("pii_masking_required", True) is not False
            for field, output_name in zip(version.fields, new_names):
                field.target_field_path = output_name
            version.validation_state = "passed"
            version.privacy_state = "not_assessed" if pii_masking_required else "compliant"
            version.lifecycle_state = "draft" if pii_masking_required else "ready"
            version.record_count = len(copied_rows)
            version.generation_settings = {
                **(version.generation_settings or {}),
                "maximum_records": payload.maximum_records,
                "output_format": payload.output_format,
            }
            from tigr.record_identity import copy_entity_lineage
            copied_lineage = copy_entity_lineage(db, base_version.id, {ordinal: ordinal for ordinal in range(len(copied_rows))})
            digest, _ = store_dataset_records(db, dataset, version, copied_rows, lineage_refs=copied_lineage)
            artifact_ref = f"artifact://{dataset.id}/v{version.version_number}/dataset.{payload.output_format}"
            artifact_bytes = serialize_dataset_records(copied_rows, payload.output_format, new_names)
            record_generated_artifact(
                db, dataset, version, len(artifact_bytes), artifact_ref,
                "text/csv" if payload.output_format == "csv" else "application/json", hashlib.sha256(artifact_bytes).hexdigest(),
            )
            version.validation_evidence = {
                "summary": (f"Copied {len(copied_rows)} records from published version {base_version.version_number}; privacy discovery is required before publishing"
                            if pii_masking_required else f"Copied {len(copied_rows)} records from published version {base_version.version_number}; PII & Masking is disabled for this workspace"),
                "record_count": len(copied_rows), "field_count": len(new_names),
                "validation_version": "version-copy-v1", "schema_version": "version-copy-v1",
                "artifact_ref": artifact_ref, "hash": digest,
            }
            record_validation_run(db, dataset, version, request.state.request_id, "version_copy")
            base_lineage_event = db.scalar(select(DataSetLineageEvent.id).where(
                DataSetLineageEvent.dataset_id == dataset.id,
                DataSetLineageEvent.version_id == base_version.id,
            ).order_by(DataSetLineageEvent.sequence_number.desc()).limit(1))
            append_lineage_event(
                db, dataset, version,
                LineageEventCreate(
                    event_type="version_data_copied", stage="draft",
                    source_reference=f"dataset://{dataset.id}/versions/{base_version.version_number}",
                    artifact_reference=artifact_ref,
                    parent_event_ids=[base_lineage_event] if base_lineage_event else [],
                    safe_evidence={"summary": f"Copied {len(copied_rows)} records from version {base_version.version_number}", "record_count": len(copied_rows), "field_count": len(new_names)},
                ), principal,
            )
    dataset.name = payload.name
    dataset.description = payload.description
    dataset.updated_at = datetime.now(UTC)
    audit_dataset(db, request, principal, role, dataset, "dataset.version_created", version, {"creation_pathway": dataset.creation_pathway, "requested_records": payload.maximum_records, "job_id": str(generation_job_id) if generation_job_id else None})
    db.commit()
    db.refresh(version)
    result = version_out(version)
    if generation_job_id:
        return {**result.model_dump(mode="json"), "job_id": str(generation_job_id), "generation_status": "queued"}
    return result


@router.post("/datasets/{dataset_id}/versions/{version_number}/lineage", status_code=status.HTTP_201_CREATED)
def add_lineage_event(
    dataset_id: UUID,
    version_number: int,
    payload: LineageEventCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    dataset = get_dataset(db, dataset_id)
    role = require_tde_dataset_editor(db, principal, dataset.workspace_id)
    version = get_version(db, dataset, version_number)
    event = append_lineage_event(db, dataset, version, payload, principal)
    dataset.updated_at = datetime.now(UTC)
    audit_dataset(db, request, principal, role, dataset, "dataset.lineage_appended", version, {"event_type": event.event_type, "sequence_number": event.sequence_number})
    db.commit()
    return {"id": str(event.id), "version_id": str(version.id), "sequence_number": event.sequence_number, "event_type": event.event_type, "stage": event.stage, "outcome": event.outcome}


@router.post("/datasets/{dataset_id}/versions/{version_number}/validation", response_model=DataSetVersionOut)
def record_validation(
    dataset_id: UUID,
    version_number: int,
    payload: ValidationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> DataSetVersionOut:
    dataset = get_dataset(db, dataset_id)
    if principal.actor_type != "service":
        raise HTTPException(status_code=403, detail="Validation results can only be recorded by an authorized validation service")
    require_service_scope(principal, dataset.workspace_id, "dataset:validate")
    role = None
    version = get_version(db, dataset, version_number, for_update=True)
    if version.lifecycle_state != "draft":
        raise HTTPException(status_code=409, detail="Validation evidence can only be recorded for a draft version")
    previous_status = version.lifecycle_state
    version.validation_state = payload.validation_state.value
    version.privacy_state = payload.privacy_state.value
    version.validation_evidence = payload.evidence
    evidence_record_count = payload.evidence.get("record_count")
    version.record_count = evidence_record_count if isinstance(evidence_record_count, int) and evidence_record_count >= 0 else None
    if payload.validation_state == ValidationState.FAILED:
        version.lifecycle_state = "failed"
    elif payload.privacy_state == PrivacyState.BLOCKED:
        version.lifecycle_state = "quarantined"
    elif payload.validation_state == ValidationState.PASSED and payload.privacy_state == PrivacyState.COMPLIANT:
        version.lifecycle_state = "ready"
    else:
        version.lifecycle_state = "draft"
    record_status_change(db, dataset, version, previous_status, version.lifecycle_state, principal,
                         request.state.request_id, "Validation result changed readiness")
    record_validation_run(db, dataset, version, request.state.request_id, "service_validation")
    append_lineage_event(db, dataset, version, LineageEventCreate(
        event_type="validation_completed", stage="validation", outcome="succeeded" if version.lifecycle_state == "ready" else "failed" if version.lifecycle_state in ("failed", "quarantined") else "warning",
        validation_version=payload.evidence.get("validation_version"), safe_evidence=payload.evidence,
    ), principal)
    dataset.updated_at = datetime.now(UTC)
    audit_dataset(db, request, principal, role, dataset, "dataset.validation_recorded", version, {"validation_state": version.validation_state, "privacy_state": version.privacy_state, "lifecycle_state": version.lifecycle_state})
    db.commit()
    db.refresh(version)
    return version_out(version)


@router.post("/datasets/{dataset_id}/versions/{version_number}/publish", response_model=DataSetVersionOut)
def publish_version(
    dataset_id: UUID,
    version_number: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> DataSetVersionOut:
    dataset = get_dataset(db, dataset_id)
    role = workspace_role(db, principal, dataset.workspace_id)
    ssd_owner = dataset.creation_pathway == "self_servicing_data" and dataset.owner_id == principal.subject
    if ssd_owner:
        from tigr.ssd import qa
        qa(db, principal, dataset.workspace_id)
        role = HumanRole.QA_ENGINEER
    if role != HumanRole.TEST_DATA_ENGINEER and not ssd_owner:
        raise HTTPException(status_code=403, detail="Only a Test Data Engineer can publish a data set version")
    version = get_version(db, dataset, version_number, for_update=True)
    from tigr.ssd import enforce_dataset
    enforce_dataset(db, principal, dataset, version)
    if ssd_owner and version.publication_state == "published":
        return version_out(version)
    if version.lifecycle_state != "ready" or version.validation_state != "passed" or version.privacy_state != "compliant":
        raise HTTPException(status_code=409, detail="Only validated, privacy-compliant ready versions can be published")
    if not dataset_records_privacy_ready(version):
        raise HTTPException(status_code=409, detail="Source-backed records require a completed PII masking run before publication")
    if version.expiry_at is not None and version.expiry_at <= datetime.now(UTC):
        raise HTTPException(status_code=409, detail="An expired version cannot be published")
    if ssd_owner:
        from tigr.ssd_models import SsdDraft
        ssd_draft = db.scalar(select(SsdDraft).where(SsdDraft.dataset_id == dataset.id).with_for_update())
        if ssd_draft and ssd_draft.dataset_version_number == version_number and ssd_draft.generated_revision == ssd_draft.revision:
            ssd_draft.state = "published"
    version.lifecycle_state = "published"
    version.publication_state = "published"
    version.published_at = datetime.now(UTC)
    record_status_change(db, dataset, version, "ready", "published", principal, request.state.request_id, "Version published")
    db.add(DatasetPublication(
        id=uuid4(), workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
        published_by=principal.subject, published_at=version.published_at, state="published",
        readiness_snapshot={"validation_state": version.validation_state, "privacy_state": version.privacy_state,
                            "record_count": version.record_count, "permitted_use": version.permitted_use},
    ))
    dataset.updated_at = version.published_at
    append_lineage_event(db, dataset, version, LineageEventCreate(event_type="published", stage="publication", safe_evidence={"summary": "Version published after validation and privacy checks"}), principal)
    audit_dataset(db, request, principal, role, dataset, "dataset.version_published", version, {"publication_state": version.publication_state})
    db.commit()
    db.refresh(version)
    return version_out(version)


@router.post("/datasets/{dataset_id}/versions/{version_number}/expire", response_model=DataSetVersionOut)
def expire_dataset_version(
    dataset_id: UUID,
    version_number: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> DataSetVersionOut:
    dataset = get_dataset(db, dataset_id)
    role = require_tde_dataset_editor(db, principal, dataset.workspace_id)
    version = get_version(db, dataset, version_number, for_update=True)
    if version.lifecycle_state != "published" or version.publication_state != "published":
        raise HTTPException(status_code=409, detail="Only a published data set version can expire")
    from tigr.delivery_api import remove_provisioned_target
    active_runs = db.scalars(select(ProvisioningRun).where(ProvisioningRun.version_id == version.id, ProvisioningRun.status == "succeeded")).all()
    for run in active_runs:
        target = db.get(WorkspaceConnection, run.target_connection_id)
        if target is None or not run.target_object:
            raise HTTPException(status_code=409, detail="A target delivery cannot be reconciled; expiry was stopped")
        try:
            remove_provisioned_target(run, target)
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="A target delivery could not be cleaned up; expiry was stopped") from exc
        run.status = "cleaned"
        run.target_object = None
        run.evidence = {**run.evidence, "cleanup": "succeeded", "cleanup_reason": "version_expiry"}
        append_lineage_event(db, dataset, version, LineageEventCreate(event_type="target_cleaned", stage="cleanup",
            safe_evidence={"summary": "Provisioned target removed during version expiry"}), principal)
    db.execute(DataSetRecord.__table__.delete().where(DataSetRecord.version_id == version.id))
    artifact_record = db.scalar(select(ArtifactRecord).where(ArtifactRecord.version_id == version.id))
    if artifact_record:
        artifact_record.status = "expired"
        artifact_record.deleted_at = datetime.now(UTC)
    from tigr.dataset_access_lifecycle import expire_dataset_allocations
    expire_dataset_allocations(db, {version.id}, principal, request.state.request_id)
    previous_status = version.lifecycle_state
    expire_version(version)
    record_status_change(db, dataset, version, previous_status, version.lifecycle_state, principal,
                         request.state.request_id, "Version expired")
    dataset.updated_at = version.expired_at
    append_lineage_event(db, dataset, version, LineageEventCreate(event_type="expired", stage="lifecycle", safe_evidence={"summary": "Version expired"}), principal)
    audit_dataset(db, request, principal, role, dataset, "dataset.version_expired", version, {"lifecycle_state": version.lifecycle_state})
    db.commit()
    db.refresh(version)
    return version_out(version)


class ExpiryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expiry_at: AwareDatetime | None


@router.patch("/datasets/{dataset_id}/versions/{version_number}/expiry", response_model=DataSetVersionOut)
def update_dataset_expiry(
    dataset_id: UUID,
    version_number: int,
    payload: ExpiryUpdate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> DataSetVersionOut:
    dataset = get_dataset(db, dataset_id)
    role = require_tde_dataset_editor(db, principal, dataset.workspace_id)
    version = get_version(db, dataset, version_number, for_update=True)
    if version.lifecycle_state != "published" or version.publication_state != "published":
        raise HTTPException(status_code=409, detail="Expiry can only be changed for a published data set version")
    previous_expiry = version.expiry_at
    if previous_expiry is not None and previous_expiry <= datetime.now(UTC):
        from tigr.dataset_access_lifecycle import expire_dataset_allocations
        expire_dataset_allocations(db, {version.id}, principal, request.state.request_id)
    version.expiry_at = payload.expiry_at
    if version.expiry_at is not None and version.expiry_at <= datetime.now(UTC):
        from tigr.dataset_access_lifecycle import expire_dataset_allocations
        expire_dataset_allocations(db, {version.id}, principal, request.state.request_id)
    dataset.updated_at = datetime.now(UTC)
    updated_at = version.expiry_at.isoformat() if version.expiry_at else None
    previous_at = previous_expiry.isoformat() if previous_expiry else None
    append_lineage_event(db, dataset, version, LineageEventCreate(
        event_type="expiry_updated", stage="lifecycle",
        safe_evidence={"summary": "Published version expiry updated"}), principal)
    audit_dataset(db, request, principal, role, dataset, "dataset.expiry_updated", version,
                  {"previous_expiry_at": previous_at, "expiry_at": updated_at})
    db.commit()
    db.refresh(version)
    return version_out(version)


@router.get("/datasets/{dataset_id}/versions/{version_number}/lineage")
def get_version_lineage(
    dataset_id: UUID,
    version_number: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[LineageEventOut]:
    dataset = get_dataset(db, dataset_id)
    version = get_version(db, dataset, version_number)
    authorize_dataset_read(db, principal, dataset, version)
    return [LineageEventOut(
        id=event.id, dataset_id=event.dataset_id, version_id=event.version_id,
        version_number=db.scalar(select(DataSetVersion.version_number).where(DataSetVersion.id == event.version_id)),
        sequence_number=event.sequence_number, event_type=event.event_type, stage=event.stage,
        source_reference=event.source_reference, artifact_reference=event.artifact_reference,
        mapping_version=event.mapping_version, scenario_version=event.scenario_version, job_id=event.job_id,
        policy_version=event.policy_version, validation_version=event.validation_version,
        actor_id=event.actor_id, actor_type=event.actor_type, occurred_at=event.occurred_at,
        outcome=event.outcome, safe_evidence=event.safe_evidence, parent_event_ids=parents, child_event_ids=children,
    ) for event, parents, children in lineage_for_version(db, dataset.id, version)]


@router.get("/datasets/{dataset_id}/versions/{version_number}/download-access")
def dataset_download_access(
    dataset_id: UUID,
    version_number: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Return safe availability counts for the download confirmation step."""
    dataset = get_dataset(db, dataset_id)
    version = get_version(db, dataset, version_number)
    role = authorize_dataset_read(db, principal, dataset, version)
    if role == HumanRole.ADMIN:
        raise HTTPException(status_code=403, detail="Workspace Admins can view data set details but cannot download data set records")
    if version.lifecycle_state != "published" or version.publication_state != "published" or version.validation_state != "passed" or version.privacy_state != "compliant":
        raise HTTPException(status_code=409, detail="Only published, validated, privacy-compliant artifacts can be downloaded")
    if not dataset_records_privacy_ready(version):
        raise HTTPException(status_code=409, detail="Source-backed records are available only after PII masking is complete")
    if expire_allocations_if_version_expired(db, dataset, version, principal, "dataset-download-access-expiry-check"):
        raise HTTPException(status_code=410, detail="The data set version has expired")
    reservation_active = False
    lock_active = False
    if role == HumanRole.QA_ENGINEER and not dataset_owner_direct_access(dataset, principal):
        now = datetime.now(UTC)
        lock = db.scalar(select(DataSetLock).where(DataSetLock.version_id == version.id, DataSetLock.actor_id == principal.subject,
            DataSetLock.status == "active", DataSetLock.expires_at > now))
        reservation = db.scalar(select(DataSetReservation).where(DataSetReservation.version_id == version.id,
            DataSetReservation.actor_id == principal.subject, DataSetReservation.status == "active",
            DataSetReservation.starts_at <= now, DataSetReservation.ends_at > now))
        request_row = db.get(DataUseRequest, reservation.request_id) if reservation and reservation.request_id else None
        reservation_active = bool(reservation and request_row and request_row.status == "approved")
        lock_active = lock is not None
        if not lock_active or not reservation_active:
            raise HTTPException(status_code=409, detail="Reserve and lock this data set to download it. An approved reservation and active lock are required.")
    if role == HumanRole.TEST_DATA_ENGINEER:
        records = db.scalars(select(DataSetRecord).where(DataSetRecord.version_id == version.id).order_by(DataSetRecord.ordinal)).all()
        protected = protected_ordinals(db, version.id, datetime.now(UTC))
        blocked_count = sum(record.ordinal in protected for record in records)
        allowed_count = len(records) - blocked_count
    else:
        _rows, allowed_count, blocked_count = filter_dataset_records(db, version, principal.subject)
    total_count = allowed_count + blocked_count
    return {
        "can_download": total_count > 0 if role == HumanRole.TEST_DATA_ENGINEER else allowed_count > 0,
        "record_count": total_count,
        "available_record_count": allowed_count,
        "blocked_record_count": blocked_count,
        "reservation_active": reservation_active,
        "lock_active": lock_active,
    }


@router.get("/datasets/{dataset_id}/versions/{version_number}/view-data")
def view_dataset_records(
    dataset_id: UUID,
    version_number: int,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Return a paged grid of records available to a QA user or TDE."""
    dataset = get_dataset(db, dataset_id)
    version = get_version(db, dataset, version_number)
    role = authorize_dataset_read(db, principal, dataset, version)
    if role not in {HumanRole.QA_ENGINEER, HumanRole.TEST_DATA_ENGINEER}:
        raise HTTPException(status_code=403, detail="View Data is available to QA Engineers and Test Data Engineers")
    if version.lifecycle_state != "published" or version.publication_state != "published" or version.validation_state != "passed" or version.privacy_state != "compliant":
        raise HTTPException(status_code=409, detail="Only published, validated, privacy-compliant data can be viewed")
    if not dataset_records_privacy_ready(version):
        raise HTTPException(status_code=409, detail="Source-backed records are available only after PII masking is complete")
    if expire_allocations_if_version_expired(db, dataset, version, principal, request.state.request_id):
        raise HTTPException(status_code=410, detail="The data set version has expired")
    if role == HumanRole.QA_ENGINEER and not dataset_owner_direct_access(dataset, principal):
        now = datetime.now(UTC)
        lock = db.scalar(select(DataSetLock).where(
            DataSetLock.version_id == version.id, DataSetLock.actor_id == principal.subject,
            DataSetLock.status == "active", DataSetLock.expires_at > now,
        ))
        reservation = db.scalar(select(DataSetReservation).where(
            DataSetReservation.version_id == version.id, DataSetReservation.actor_id == principal.subject,
            DataSetReservation.status == "active", DataSetReservation.starts_at <= now,
            DataSetReservation.ends_at > now,
        ))
        request_row = db.get(DataUseRequest, reservation.request_id) if reservation and reservation.request_id else None
        if not lock or not reservation or not request_row or request_row.status != "approved":
            raise HTTPException(status_code=409, detail="View Data requires your approved active reservation and active lock")
        rows, available_count, blocked_count = filter_dataset_records(db, version, principal.subject)
        protected_row_indices: list[int] = []
    elif role == HumanRole.QA_ENGINEER:
        rows, available_count, blocked_count = filter_dataset_records(db, version, principal.subject)
        protected_row_indices = []
    else:
        records = db.scalars(select(DataSetRecord).where(DataSetRecord.version_id == version.id).order_by(DataSetRecord.ordinal)).all()
        rows = [record.values for record in records]
        locked = protected_ordinals(db, version.id, datetime.now(UTC))
        protected_row_indices = [index for index, record in enumerate(records) if record.ordinal in locked]
        blocked_count = len(protected_row_indices)
        available_count = len(records) - blocked_count
    record_count = available_count + blocked_count
    offset = (page - 1) * page_size
    page_rows = rows[offset:offset + page_size]
    page_protected_indices = [index - offset for index in protected_row_indices if offset <= index < offset + page_size] if role == HumanRole.TEST_DATA_ENGINEER else []
    fields = [item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item) for item in (version.schema_scope or [])]
    entity_key_fields = set(entity_key_output_fields(db, version))
    selected_fields = list(version.selected_fields or [])
    findings_by_field = {
        finding.field: finding
        for finding in db.scalars(select(PiiFinding).where(PiiFinding.version_id == version.id)).all()
    }
    masked_fields = set(applied_masked_fields(db, version))
    for index, field in enumerate(fields):
        if index < len(selected_fields) and selected_fields[index]:
            field["field"] = selected_fields[index]
        field["is_entity_key"] = str(field.get("field") or "") in entity_key_fields
        finding = findings_by_field.get(str(field.get("field") or ""))
        field["privacy_classification"] = finding.classification if finding else "not_assessed"
        field["is_masked"] = str(field.get("field") or "") in masked_fields
    response = {
        "dataset_id": str(dataset.id),
        "reference_code": dataset.reference_code,
        "dataset_name": dataset.name,
        "version_number": version.version_number,
        "fields": fields,
        "rows": page_rows,
        "record_count": record_count,
        "protected_row_indices": page_protected_indices,
        "available_record_count": available_count,
        "blocked_record_count": blocked_count,
        "page": page,
        "page_size": page_size,
        "has_more": offset + len(page_rows) < (record_count if role == HumanRole.TEST_DATA_ENGINEER else available_count),
    }
    record_audit(db, principal, request.state.request_id, "dataset.records_viewed", "dataset_version", str(version.id), dataset.workspace_id, role,
        {"version_number": version_number, "page": page, "page_size": page_size, "record_count": len(page_rows), "blocked_record_count": blocked_count})
    db.commit()
    return response


@router.get("/datasets/{dataset_id}/versions/{version_number}/record-availability")
def dataset_record_availability(
    dataset_id: UUID,
    version_number: int,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, int]:
    """Return record availability counts without returning record values."""
    dataset = get_dataset(db, dataset_id)
    version = get_version(db, dataset, version_number)
    authorize_dataset_read(db, principal, dataset, version)
    if expire_allocations_if_version_expired(db, dataset, version, principal, "dataset-record-availability-expiry-check"):
        raise HTTPException(status_code=410, detail="The data set version has expired")
    _rows, available_count, unavailable_count = filter_dataset_records(db, version, principal.subject)
    return {
        "record_count": available_count + unavailable_count,
        "available_record_count": available_count,
        "unavailable_record_count": unavailable_count,
    }


@router.get("/datasets/{dataset_id}/versions/{version_number}/artifact")
def download_dataset_artifact(
    dataset_id: UUID,
    version_number: int,
    request: Request,
    format: str | None = Query(default=None, pattern="^(csv|json)$"),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    dataset = get_dataset(db, dataset_id)
    version = get_version(db, dataset, version_number)
    role = authorize_dataset_read(db, principal, dataset, version)
    if role == HumanRole.ADMIN:
        raise HTTPException(status_code=403, detail="Workspace Admins can view data set details but cannot download data set records")
    if version.lifecycle_state != "published" or version.publication_state != "published" or version.validation_state != "passed" or version.privacy_state != "compliant":
        raise HTTPException(status_code=409, detail="Only published, validated, privacy-compliant artifacts can be downloaded")
    if not dataset_records_privacy_ready(version):
        raise HTTPException(status_code=409, detail="Source-backed records are available only after PII masking is complete")
    if expire_allocations_if_version_expired(db, dataset, version, principal, request.state.request_id):
        raise HTTPException(status_code=410, detail="The data set version has expired")
    if role == HumanRole.QA_ENGINEER and not dataset_owner_direct_access(dataset, principal):
        now = datetime.now(UTC)
        lock = db.scalar(select(DataSetLock).where(DataSetLock.version_id == version.id, DataSetLock.actor_id == principal.subject,
            DataSetLock.status == "active", DataSetLock.expires_at > now))
        reservation = db.scalar(select(DataSetReservation).where(DataSetReservation.version_id == version.id,
            DataSetReservation.actor_id == principal.subject, DataSetReservation.status == "active",
            DataSetReservation.starts_at <= now, DataSetReservation.ends_at > now))
        request_row = db.get(DataUseRequest, reservation.request_id) if reservation and reservation.request_id else None
        if not lock or not reservation or not request_row or request_row.status != "approved":
            raise HTTPException(status_code=409, detail="Reserve and lock this data set to download it. An approved reservation and active lock are required.")
    reference = str(version.validation_evidence.get("artifact_ref", ""))
    output_format = format or ("csv" if reference.endswith(".csv") else "json")
    extension = f".{output_format}"
    if role == HumanRole.TEST_DATA_ENGINEER:
        records = db.scalars(select(DataSetRecord).where(DataSetRecord.version_id == version.id).order_by(DataSetRecord.ordinal)).all()
        rows = [record.values for record in records]
        locked = protected_ordinals(db, version.id, datetime.now(UTC))
        blocked_count = sum(record.ordinal in locked for record in records)
        allowed_count = len(records) - blocked_count
    else:
        rows, allowed_count, blocked_count = filter_dataset_records(db, version, principal.subject)
    # A generated artifact with zero rows is still downloadable. Keep the CSV
    # schema visible in its header so consumers can distinguish it from a
    # missing artifact; JSON naturally serializes as an empty array.
    field_names = version.selected_fields or [str(item.get("field")) for item in (version.schema_scope or []) if item.get("field")]
    record_audit(db, principal, request.state.request_id, "dataset.artifact_downloaded", "dataset_version", str(version.id), dataset.workspace_id, role,
        {"version_number": version_number, "format": extension.removeprefix("."), "privacy_state": version.privacy_state,
         "record_count": len(rows), "blocked_record_count": blocked_count})
    append_lineage_event(db,dataset,version,LineageEventCreate(event_type="artifact_downloaded",stage="delivery",artifact_reference=reference,
        outcome="succeeded",safe_evidence={"format":extension.removeprefix("."),"summary":"Authorized artifact download"}),principal)
    artifact_record = db.scalar(select(ArtifactRecord).where(
        ArtifactRecord.workspace_id == dataset.workspace_id, ArtifactRecord.version_id == version.id,
        ArtifactRecord.storage_key == reference, ArtifactRecord.status == "available",
    ))
    db.add(ConsumptionEvent(
        workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
        principal_id=principal.subject, event_type="download", artifact_id=artifact_record.id if artifact_record else None,
        occurred_at=datetime.now(UTC), outcome="succeeded", correlation_id=request.state.request_id,
    ))
    db.commit()
    return Response(serialize_dataset_records(rows, output_format, field_names), media_type="text/csv" if output_format == "csv" else "application/json",
                    headers={"Content-Disposition": f'attachment; filename="{dataset.reference_code}{extension}"'})
