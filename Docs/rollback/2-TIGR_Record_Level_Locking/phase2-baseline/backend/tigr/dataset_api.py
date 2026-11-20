import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from tigr.access import (
    ensure_user,
    record_audit,
    require_service_scope,
    workspace_role,
)
from tigr.config import get_settings
from tigr.connection_models import WorkspaceConnection
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
from tigr.governance import (
    DataSetLock,
    DataSetReservation,
    DataUseRequest,
    MaskingPolicy,
    MaskingRun,
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


def authorize_dataset_read(db: Session, principal: Principal, dataset: DataSet, version: DataSetVersion) -> HumanRole:
    role = workspace_role(db, principal, dataset.workspace_id)
    # QA workspace membership provides discoverability for every published
    # version. Artifact download remains gated separately by an approved
    # reservation request, an active reservation, and an active lock.
    if role == HumanRole.QA_ENGINEER and (
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


def summary(dataset: DataSet, version: DataSetVersion) -> DataSetSummary:
    return DataSetSummary(
        id=dataset.id, reference_code=dataset.reference_code, workspace_id=dataset.workspace_id, name=dataset.name, description=dataset.description,
        owner_id=dataset.owner_id, creation_pathway=dataset.creation_pathway,
        latest_version=version_out(version), created_at=dataset.created_at, updated_at=dataset.updated_at,
    )


def applied_masked_fields(db: Session, version: DataSetVersion) -> list[str]:
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
    qa_workspace_ids = [wid for wid, role in roles.items() if role == HumanRole.QA_ENGINEER]
    if qa_workspace_ids:
        # A QA member can only see published versions; object grants are checked below before count/search/paging.
        query = query.where(
            or_(DataSet.workspace_id.in_(qa_workspace_ids), DataSetVersion.version_number == DataSet.latest_version_number),
            or_(~DataSet.workspace_id.in_(qa_workspace_ids), (DataSetVersion.lifecycle_state == "published") & (DataSetVersion.publication_state == "published")),
        )
    elif lifecycle_state is None:
        query = query.where(DataSetVersion.version_number == DataSet.latest_version_number)
    if creation_pathway:
        query = query.where(DataSet.creation_pathway == creation_pathway.value)
    rows = db.execute(query.order_by(DataSet.updated_at.desc(), DataSet.id, DataSetVersion.version_number.desc())).all()
    visible: list[tuple[DataSet, DataSetVersion]] = []
    seen_datasets: set[UUID] = set()
    for dataset, version in rows:
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
    return {"items": [summary(dataset, version).model_dump(mode="json") for dataset, version in page_rows], "page": page, "page_size": page_size, "total": total, "has_more": page * page_size < total}


@router.post("/workspaces/{workspace_id}/datasets", status_code=status.HTTP_201_CREATED)
def create_dataset(
    workspace_id: UUID,
    payload: DraftDataSet,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    role = can_manage_dataset(db, principal, workspace_id)
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
    return summary(dataset, version).model_dump(mode="json")


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
    if role == HumanRole.QA_ENGINEER:
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
    visible_versions = versions if role != HumanRole.QA_ENGINEER else [row for row in versions if row.publication_state == "published"]
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
    return DataSetDetail(
        data_set=summary(dataset, summary_version), selected_version=version_out(version),
        versions=[version_out(row) for row in visible_versions], lineage=lineage,
        test_data_requirements=test_data_requirements,
        masked_fields=masked_fields,
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
        sample_records=read_dataset_records(db, version)[:preview_limit] if dataset_records_privacy_ready(version) else [],
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
    role = can_manage_dataset(db, principal, dataset.workspace_id)
    if payload.creation_pathway.value != dataset.creation_pathway:
        raise HTTPException(status_code=409, detail="Creation pathway is fixed for a data set")
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
            if any(previous.get(key) != updated.get(key) for key in ("field", "data_type", "entity", "json_path", "required")):
                raise HTTPException(status_code=422, detail="A copied version can rename output fields, but source fields and types must stay the same")

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
            # This version must pass privacy discovery again before it can be published.
            version.provenance = provenance
            for field, output_name in zip(version.fields, new_names):
                field.target_field_path = output_name
            version.validation_state = "passed"
            version.privacy_state = "not_assessed"
            version.lifecycle_state = "draft"
            version.record_count = len(copied_rows)
            version.generation_settings = {
                **(version.generation_settings or {}),
                "maximum_records": payload.maximum_records,
                "output_format": payload.output_format,
            }
            digest, _ = store_dataset_records(db, dataset, version, copied_rows)
            artifact_ref = f"artifact://{dataset.id}/v{version.version_number}/dataset.{payload.output_format}"
            artifact_bytes = serialize_dataset_records(copied_rows, payload.output_format, new_names)
            record_generated_artifact(
                db, dataset, version, len(artifact_bytes), artifact_ref,
                "text/csv" if payload.output_format == "csv" else "application/json", hashlib.sha256(artifact_bytes).hexdigest(),
            )
            version.validation_evidence = {
                "summary": f"Copied {len(copied_rows)} records from published version {base_version.version_number}; privacy discovery is required before publishing",
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
    role = can_manage_dataset(db, principal, dataset.workspace_id)
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
    role = can_manage_dataset(db, principal, dataset.workspace_id)
    if role != HumanRole.TEST_DATA_ENGINEER:
        raise HTTPException(status_code=403, detail="Only a Test Data Engineer can publish a data set version")
    version = get_version(db, dataset, version_number, for_update=True)
    if version.lifecycle_state != "ready" or version.validation_state != "passed" or version.privacy_state != "compliant":
        raise HTTPException(status_code=409, detail="Only validated, privacy-compliant ready versions can be published")
    if not dataset_records_privacy_ready(version):
        raise HTTPException(status_code=409, detail="Source-backed records require a completed PII masking run before publication")
    if version.expiry_at is not None and version.expiry_at <= datetime.now(UTC):
        raise HTTPException(status_code=409, detail="An expired version cannot be published")
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
    role = can_manage_dataset(db, principal, dataset.workspace_id)
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
    if version.lifecycle_state != "published" or version.publication_state != "published" or version.validation_state != "passed" or version.privacy_state != "compliant":
        raise HTTPException(status_code=409, detail="Only published, validated, privacy-compliant artifacts can be downloaded")
    if not dataset_records_privacy_ready(version):
        raise HTTPException(status_code=409, detail="Source-backed records are available only after PII masking is complete")
    if version.expiry_at is not None and version.expiry_at <= datetime.now(UTC):
        raise HTTPException(status_code=410, detail="The data set version has expired")
    if role == HumanRole.QA_ENGINEER:
        now = datetime.now(UTC)
        lock = db.scalar(select(DataSetLock).where(DataSetLock.version_id == version.id, DataSetLock.actor_id == principal.subject,
            DataSetLock.status == "active", DataSetLock.expires_at > now))
        reservation = db.scalar(select(DataSetReservation).where(DataSetReservation.version_id == version.id,
            DataSetReservation.actor_id == principal.subject, DataSetReservation.status == "active",
            DataSetReservation.starts_at <= now, DataSetReservation.ends_at > now))
        request_row = db.get(DataUseRequest, reservation.request_id) if reservation and reservation.request_id else None
        if not lock or not reservation or not request_row or request_row.status != "approved":
            raise HTTPException(status_code=409, detail="Download requires an approved reservation for this data set. After approval, download during the reserved time window while holding an active lock.")
    reference = str(version.validation_evidence.get("artifact_ref", ""))
    output_format = format or ("csv" if reference.endswith(".csv") else "json")
    extension = f".{output_format}"
    rows = read_dataset_records(db, version)
    # A generated artifact with zero rows is still downloadable. Keep the CSV
    # schema visible in its header so consumers can distinguish it from a
    # missing artifact; JSON naturally serializes as an empty array.
    field_names = [str(item.get("field")) for item in (version.schema_scope or []) if item.get("field")]
    record_audit(db, principal, request.state.request_id, "dataset.artifact_downloaded", "dataset_version", str(version.id), dataset.workspace_id, role,
        {"version_number": version_number, "format": extension.removeprefix("."), "privacy_state": version.privacy_state})
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
                    headers={"Content-Disposition": f'attachment; filename="dataset-{dataset.id}{extension}"'})
