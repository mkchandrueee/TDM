import hashlib
import json
from datetime import UTC, datetime
from time import perf_counter
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tigr.access import ensure_user, record_audit, workspace_role
from tigr.config import get_settings
from tigr.connection_models import WorkspaceConnection
from tigr.dataset_schemas import DraftDataSet, LineageEventCreate, Provenance, SchemaField
from tigr.dataset_services import (
    _dataset_fields,
    append_lineage_event,
    create_initial_version,
    dataset_records_privacy_ready,
    record_generated_artifact,
    record_status_change,
    record_validation,
    serialize_dataset_records,
    store_dataset_records,
)
from tigr.datasets import DataSet, DataSetVersion
from tigr.db import SessionLocal
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.jobs import Job, JobStep, OutboxEvent
from tigr.scenario_assistant import get_scenario_assistant
from tigr.scenario_generation import (
    MAX_REPAIR_ATTEMPTS,
    artifact_reference,
    clarifying_questions,
    compile_query_plan,
    connection_fields,
    generate_rows,
    postgres_review_query,
    repair_rows,
    synthetic_query_output_mapping,
    validate_mapping,
    validate_readonly_query,
    validate_rows,
)
from tigr.scenario_schemas import (
    GenerateRequest,
    ScenarioCreate,
    ScenarioPlanUpdate,
    ScenarioQueryRequest,
    SchemaGenerationRequest,
)
from tigr.scenarios import ScenarioPlan

router = APIRouter(prefix="/api/v1", tags=["scenario to data"])


def editable_role(db: Session, principal: Principal, workspace_id: UUID) -> HumanRole:
    role = workspace_role(db, principal, workspace_id)
    if principal.actor_type != "human" or role == HumanRole.QA_ENGINEER:
        raise HTTPException(status_code=403, detail="Scenario planning requires an Admin or Test Data Engineer")
    if role == HumanRole.ADMIN:
        raise HTTPException(status_code=403, detail="Scenario planning requires a Test Data Engineer")
    return role


def get_plan(db: Session, plan_id: UUID) -> ScenarioPlan:
    plan = db.get(ScenarioPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Scenario plan not found")
    return plan


def plan_connection(db: Session, plan: ScenarioPlan) -> WorkspaceConnection | None:
    # Synthetic workflows are isolated from every registered source, even if a
    # stale source id remains on a previously edited plan.
    if plan.generation_mode != "source_backed" or plan.source_connection_id is None:
        return None
    connection = db.get(WorkspaceConnection, plan.source_connection_id)
    if connection is None or connection.workspace_id != plan.workspace_id:
        raise HTTPException(status_code=404, detail="Scenario source connection not found")
    return connection


def source_coverage_ready(plan: ScenarioPlan) -> tuple[bool, str]:
    """Fail closed unless every extracted source-backed criterion is covered."""
    if plan.generation_mode != "source_backed":
        return True, ""
    logical_plan = plan.logical_plan or {}
    intent = logical_plan.get("requirement_intent") or {}
    criteria = intent.get("criteria") or []
    coverage = logical_plan.get("criteria_coverage") or []
    if not criteria:
        return False, "Generate a requirement interpretation before continuing."
    coverage_by_id = {str(item.get("criterion_id")): item for item in coverage if isinstance(item, dict)}
    unresolved = [item for item in criteria if not coverage_by_id.get(str(item.get("id")), {}).get("covered")]
    if unresolved:
        return False, "Resolve every uncovered requirement criterion in Step 2 before continuing. Use Corrections on schema mapping and regenerate."
    return True, ""


def require_source_coverage_review(plan: ScenarioPlan) -> None:
    ready, reason = source_coverage_ready(plan)
    if not ready:
        raise HTTPException(status_code=409, detail=reason)


def _create_or_reuse_scenario_dataset(
    db: Session,
    principal: Principal,
    workspace_id: UUID,
    draft_dataset_id: UUID | None,
    payload: DraftDataSet,
    correlation_id: str,
) -> tuple[DataSet, DataSetVersion]:
    """Populate the empty Create Draft record, avoiding a second dataset row."""
    if draft_dataset_id is None:
        dataset = DataSet(
            id=uuid4(), workspace_id=workspace_id, name=payload.name,
            description=payload.description, owner_id=principal.subject,
            creation_pathway="scenario_to_data", latest_version_number=1,
        )
        db.add(dataset)
        db.flush()
        return dataset, create_initial_version(db, dataset, payload, principal, correlation_id)

    dataset = db.scalar(select(DataSet).where(DataSet.id == draft_dataset_id).with_for_update())
    if (dataset is None or dataset.workspace_id != workspace_id
            or dataset.creation_pathway != "scenario_to_data" or dataset.owner_id != principal.subject):
        raise HTTPException(status_code=404, detail="Scenario to Data draft not found")
    version = db.scalar(
        select(DataSetVersion).where(DataSetVersion.dataset_id == dataset.id)
        .order_by(DataSetVersion.version_number.desc()).limit(1).with_for_update()
    )
    provenance = (version.provenance or {}) if version else {}
    if (version is None or version.version_number != 1 or version.lifecycle_state != "draft"
            or provenance.get("origin") != "not_set" or version.schema_scope or version.selected_fields
            or version.record_count is not None or version.validation_evidence):
        raise HTTPException(status_code=409, detail="This draft already has generation data. Create a fresh draft to generate again.")

    dataset.name = payload.name
    dataset.description = payload.description
    version.schema_scope = [field.model_dump() for field in payload.schema_scope]
    version.selected_fields = payload.selected_fields
    version.provenance = payload.provenance.model_dump(mode="json", exclude_none=True)
    version.generation_settings = {
        "target_use": payload.permitted_use[0] if payload.permitted_use else (payload.description or ""),
        "maximum_records": payload.maximum_records,
        "output_format": payload.output_format,
    }
    version.expiry_at = payload.expiry_at
    version.permitted_use = payload.permitted_use
    version.fields = _dataset_fields(dataset, version, payload)
    db.flush()
    return dataset, version


def plan_output(db: Session, plan: ScenarioPlan) -> dict:
    assistant = get_scenario_assistant()
    suggestions = (plan.logical_plan or {}).get("field_suggestions", [])
    job = db.get(Job, plan.job_id) if plan.job_id else None
    return {
        "id": plan.id, "workspace_id": plan.workspace_id, "actor_id": plan.actor_id,
        "name": plan.name, "requirement": plan.requirement,
        "expected_states": plan.expected_states, "exclusions": plan.exclusions,
        "target_use": plan.target_use, "volume": plan.volume,
        "clarifying_questions": clarifying_questions(plan),
        "generation_mode": plan.generation_mode,
        "source_connection_id": plan.source_connection_id,
        "source_object": plan.source_object, "mapping": plan.mapping,
        "suggested_mapping": suggestions, "logical_plan": plan.logical_plan,
        "state": plan.state, "job": {"id": job.id, "status": job.status, "progress": job.progress, "safe_summary": job.safe_summary} if job else None,
        "dataset_id": plan.dataset_id, "created_at": plan.created_at, "updated_at": plan.updated_at,
        "assistant": {"provider": assistant.provider_name, "model_version": (plan.logical_plan or {}).get("schema_model", assistant.model_version), "prompt_version": assistant.prompt_version},
    }


def record_scenario_audit(db: Session, request: Request, principal: Principal, role: HumanRole, plan: ScenarioPlan, action: str, metadata: dict | None = None) -> None:
    record_audit(db, principal, request.state.request_id, action, "scenario_plan", str(plan.id), plan.workspace_id, role, metadata or {})


def _schema_context_key(requirement: str, expected_states: list[str], exclusions: list[str], feedback: str) -> str:
    payload = json.dumps({"requirement": requirement, "expected_states": expected_states, "exclusions": exclusions, "feedback": feedback}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _set_schema_generation_status(plan_id: UUID, generation_id: str, **updates) -> None:
    with SessionLocal.begin() as db:
        plan = db.get(ScenarioPlan, plan_id)
        if plan is None:
            return
        logical_plan = dict(plan.logical_plan or {})
        current = dict(logical_plan.get("schema_generation_status") or {})
        if current.get("generation_id") != generation_id:
            return
        current.update(updates)
        current["updated_at"] = datetime.now(UTC).isoformat()
        logical_plan["schema_generation_status"] = current
        plan.logical_plan = logical_plan
        plan.updated_at = datetime.now(UTC)


def run_scenario_schema_generation(
    plan_id: UUID,
    generation_id: str,
    metadata: list[dict],
    mode: str,
    selected_source_objects: list[str],
    feedback: str,
    previous_response: dict | None,
    previous_intent: dict | None,
    intent_context_key: str,
    source_context_key: str,
    principal: Principal,
    role: HumanRole,
    request_id: str,
) -> None:
    from time import perf_counter

    started = perf_counter()
    timing_data: dict[str, int] = {}

    def update_stage(stage: str, **details) -> None:
        duration = details.get("duration_ms")
        if isinstance(duration, int):
            timing_data[stage.removesuffix("_complete")] = duration
        updates = {
            "status": "running",
            "stage": stage,
            "stage_started_at": datetime.now(UTC).isoformat(),
            "timings_ms": dict(timing_data),
        }
        if "reused_requirement_outline" in details:
            updates["reused_requirement_outline"] = details["reused_requirement_outline"]
        _set_schema_generation_status(
            plan_id,
            generation_id,
            **updates,
        )

    try:
        with SessionLocal() as db:
            plan = db.get(ScenarioPlan, plan_id)
            if plan is None:
                return
            requirement = plan.requirement
            expected_states = list(plan.expected_states or [])
            exclusions = list(plan.exclusions or [])
        assistant = get_scenario_assistant()
        mapping, suggested_joins, suggested_filters, schema_notes, model, requirement_intent, criteria_coverage = assistant.generate_schema(
            requirement,
            mode,
            metadata,
            expected_states=expected_states,
            exclusions=exclusions,
            feedback=feedback,
            previous_response=previous_response,
            selected_source_objects=selected_source_objects,
            previous_intent=previous_intent,
            on_stage=update_stage,
        )
        update_stage("saving_coverage_review")
        with SessionLocal.begin() as db:
            plan = db.get(ScenarioPlan, plan_id)
            if plan is None:
                return
            logical_plan = dict(plan.logical_plan or {})
            logical_plan.update({
                "field_suggestions": mapping,
                "suggested_source_joins": suggested_joins,
                "suggested_filters": suggested_filters,
                "schema_generation_notes": schema_notes,
                "requirement_intent": requirement_intent,
                "requirement_intent_context_key": intent_context_key,
                "requirement_source_context_key": source_context_key,
                "criteria_coverage": criteria_coverage,
                "source_joins": [],
                "predicates": [],
                "relationships": [],
                "schema_model": model,
                "schema_generation_feedback": feedback,
            })
            current_status = dict(logical_plan.get("schema_generation_status") or {})
            if current_status.get("generation_id") == generation_id:
                current_status.update({
                    "status": "complete",
                    "stage": "complete",
                    "completed_at": datetime.now(UTC).isoformat(),
                    "elapsed_ms": int((perf_counter() - started) * 1000),
                    "timings_ms": timing_data,
                })
            logical_plan["schema_generation_status"] = current_status
            plan.logical_plan = logical_plan
            plan.generation_mode = mode
            plan.mapping = [] if mode == "source_backed" else mapping
            plan.state = "needs_clarification" if clarifying_questions(plan) else "mapped"
            plan.updated_at = datetime.now(UTC)
            record_audit(db, principal, request_id, "scenario.schema_generated", "scenario_plan", str(plan.id), plan.workspace_id, role, {
                "mode": mode,
                "field_count": len(mapping),
                "model": model,
                "selected_source_object_count": len(selected_source_objects),
                "elapsed_ms": current_status.get("elapsed_ms"),
            })
    except Exception as exc:  # noqa: BLE001 - external model errors are sanitized below.
        safe_error = str(exc) if isinstance(exc, RuntimeError) else "Schema suggestion failed unexpectedly. Retry and review the approved source metadata."
        _set_schema_generation_status(
            plan_id,
            generation_id,
            status="failed",
            stage="failed",
            error=safe_error,
            elapsed_ms=int((perf_counter() - started) * 1000),
            timings_ms=timing_data,
        )


@router.post("/workspaces/{workspace_id}/scenario-plans", status_code=status.HTTP_201_CREATED)
def create_scenario_plan(
    workspace_id: UUID,
    payload: ScenarioCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    role = editable_role(db, principal, workspace_id)
    ensure_user(db, principal)
    plan = ScenarioPlan(
        id=uuid4(), workspace_id=workspace_id, actor_id=principal.subject,
        name=payload.name, requirement=payload.requirement,
        expected_states=payload.expected_states, exclusions=payload.exclusions,
        target_use=payload.target_use, volume=payload.volume, generation_mode="synthetic",
        logical_plan={"draft_data": payload.draft_data} if payload.draft_data else {},
        state="draft",
    )
    db.add(plan)
    db.flush()
    record_scenario_audit(db, request, principal, role, plan, "scenario.plan_created")
    db.commit()
    db.refresh(plan)
    return plan_output(db, plan)


@router.get("/workspaces/{workspace_id}/scenario-plans")
def list_scenario_plans(
    workspace_id: UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    role = workspace_role(db, principal, workspace_id)
    if principal.actor_type != "human" or role == HumanRole.QA_ENGINEER:
        raise HTTPException(status_code=403, detail="Scenario plans require an Admin or Test Data Engineer")
    rows = db.scalars(select(ScenarioPlan).where(ScenarioPlan.workspace_id == workspace_id).order_by(ScenarioPlan.updated_at.desc()).limit(50)).all()
    return [plan_output(db, row) for row in rows]


@router.get("/scenario-plans/{plan_id}")
def read_scenario_plan(
    plan_id: UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    plan = get_plan(db, plan_id)
    role = workspace_role(db, principal, plan.workspace_id)
    if role == HumanRole.QA_ENGINEER or principal.actor_type != "human":
        raise HTTPException(status_code=404, detail="Scenario plan not found")
    return plan_output(db, plan)


@router.patch("/scenario-plans/{plan_id}")
def update_scenario_plan(
    plan_id: UUID,
    payload: ScenarioPlanUpdate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    plan = get_plan(db, plan_id)
    role = editable_role(db, principal, plan.workspace_id)
    if plan.state in ("running", "succeeded", "failed", "quarantined"):
        raise HTTPException(status_code=409, detail="A started scenario plan cannot be edited")
    schema_status = (plan.logical_plan or {}).get("schema_generation_status") or {}
    if schema_status.get("status") == "running":
        raise HTTPException(status_code=409, detail="Schema generation is already running for this scenario")
    changes = payload.model_dump(exclude_unset=True)
    changes.pop("requirement_review_confirmed", None)
    if "draft_data" in changes:
        plan.logical_plan = {**(plan.logical_plan or {}), "draft_data": changes.pop("draft_data") or {}}
    if "generation_mode" in changes:
        changes["generation_mode"] = changes["generation_mode"].value
    if "mapping" in changes and changes["mapping"] is not None:
        # model_dump() converts nested Pydantic models to dictionaries, so use
        # the validated request models here rather than the dumped values.
        changes["mapping"] = [item.model_dump(mode="json") for item in payload.mapping or []]
    if "predicates" in changes and changes["predicates"] is not None:
        plan.logical_plan = {**(plan.logical_plan or {}), "predicates": [item.model_dump(mode="json") for item in payload.predicates]}
        changes.pop("predicates")
    if "relationships" in changes and changes["relationships"] is not None:
        plan.logical_plan = {**(plan.logical_plan or {}), "relationships": [item.model_dump(mode="json") for item in payload.relationships]}
        changes.pop("relationships")
    if "source_joins" in changes and changes["source_joins"] is not None:
        plan.logical_plan = {**(plan.logical_plan or {}), "source_joins": [item.model_dump(mode="json") for item in payload.source_joins]}
        changes.pop("source_joins")
    for key, value in changes.items():
        setattr(plan, key, value)
    connection = plan_connection(db, plan)
    try:
        if plan.mapping:
            validate_mapping(plan, connection)
        if any(key in payload.model_fields_set for key in ("mapping", "predicates", "source_joins", "relationships")):
            plan.logical_plan = {key: value for key, value in (plan.logical_plan or {}).items() if key not in {"generated_query", "generated_query_fields", "generated_query_field_types"}}
        plan.state = "needs_clarification" if clarifying_questions(plan) else ("mapped" if plan.mapping else "draft")
        if plan.mapping and not clarifying_questions(plan) and plan.generation_mode == "synthetic":
            compile_query_plan(plan, connection)
            plan.state = "ready"
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    plan.updated_at = datetime.now(UTC)
    record_scenario_audit(db, request, principal, role, plan, "scenario.plan_updated", {"changed_fields": sorted(payload.model_fields_set)})
    db.commit()
    db.refresh(plan)
    return plan_output(db, plan)


@router.get("/workspaces/{workspace_id}/scenario-sources")
def list_scenario_sources(
    workspace_id: UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    role = workspace_role(db, principal, workspace_id)
    if principal.actor_type != "human" or role == HumanRole.QA_ENGINEER:
        raise HTTPException(status_code=403, detail="Scenario metadata requires an Admin or Test Data Engineer")
    rows = db.scalars(select(WorkspaceConnection).where(
        WorkspaceConnection.workspace_id == workspace_id,
        WorkspaceConnection.status == "enabled",
        WorkspaceConnection.direction.in_(("source", "both")),
    ).order_by(WorkspaceConnection.name)).all()
    result = []
    for connection in rows:
        preview = connection.structure_preview or {}
        objects = preview.get("objects", []) if connection.connector_type == "postgresql" else [{"name": (connection.connector_config or {}).get("file_path", connection.name), "fields": connection_fields(connection)}]
        result.append({"id": connection.id, "name": connection.name, "connector_type": connection.connector_type, "objects": [{"name": item.get("name"), "fields": item.get("fields", [])} for item in objects]})
    return result


@router.post("/scenario-plans/{plan_id}/schema")
def generate_scenario_schema(
    plan_id: UUID,
    payload: SchemaGenerationRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    plan = get_plan(db, plan_id)
    role = editable_role(db, principal, plan.workspace_id)
    if plan.state in ("running", "succeeded", "failed", "quarantined"):
        raise HTTPException(status_code=409, detail="A started scenario plan cannot be edited")
    schema_status = (plan.logical_plan or {}).get("schema_generation_status") or {}
    if schema_status.get("status") == "running":
        raise HTTPException(status_code=409, detail="Schema generation is already running for this scenario")
    previous_generation_mode = plan.generation_mode
    if payload.generation_mode.value == "source_backed":
        connection = db.get(WorkspaceConnection, payload.source_connection_id)
        if connection is None or connection.workspace_id != plan.workspace_id or connection.status != "enabled":
            raise HTTPException(status_code=422, detail="Choose an enabled source connection in this workspace")
    else:
        connection = None
    metadata = []
    selected_source_objects: list[str] = []
    if connection:
        preview = connection.structure_preview or {}
        if connection.connector_type == "postgresql":
            objects = preview.get("objects", [])
            if objects and any(
                "foreign_keys" not in item
                or "unique_constraints" not in item
                or "check_constraints" not in item
                or any("nullable" not in field for field in item.get("fields", []))
                for item in objects
            ):
                raise HTTPException(status_code=409, detail="The saved schema metadata is incomplete. Re-test this source connection to refresh keys and constraints, then generate the schema again.")
            metadata = [{"name": str(item.get("name", "")), "fields": item.get("fields", []), "foreign_keys": item.get("foreign_keys", []), "unique_constraints": item.get("unique_constraints", []), "check_constraints": item.get("check_constraints", [])}
                        for item in objects]
        else:
            metadata = [{"name": "source_file", "fields": connection_fields(connection), "foreign_keys": []}]
        if not metadata or not any(item.get("fields") for item in metadata):
            raise HTTPException(status_code=422, detail="Test the source connection to load approved schema metadata")
        available_objects = {str(item.get("name", "")) for item in metadata if item.get("name")}
        requested_objects = payload.selected_source_objects
        if requested_objects and connection.connector_type != "postgresql":
            source_label = str((connection.connector_config or {}).get("file_path") or connection.name)
            if len(requested_objects) == 1 and requested_objects[0] in {source_label, connection.name, "source_file"}:
                requested_objects = ["source_file"]
        if requested_objects is None:
            # Older clients and saved plans without an explicit object scope retain
            # the existing behavior of using the complete approved catalog.
            selected_source_objects = sorted(available_objects)
        else:
            if not requested_objects:
                raise HTTPException(status_code=422, detail="Select at least one source object for schema mapping")
            unavailable = sorted(set(requested_objects) - available_objects)
            if unavailable:
                raise HTTPException(status_code=422, detail="One or more selected source objects are not in the approved connection catalog")
            selected_source_objects = list(requested_objects)
            selected_set = set(selected_source_objects)
            metadata = [
                {
                    **item,
                    "foreign_keys": [fk for fk in item.get("foreign_keys", []) if fk.get("references_object") in selected_set],
                }
                for item in metadata if item.get("name") in selected_set
            ]
    prior_plan = plan.logical_plan or {}
    feedback = payload.feedback.strip()
    intent_context_key = _schema_context_key(plan.requirement, list(plan.expected_states or []), list(plan.exclusions or []), feedback)
    source_context_key = _schema_context_key(plan.requirement, list(plan.expected_states or []), list(plan.exclusions or []), "")
    previous_intent = prior_plan.get("requirement_intent") if prior_plan.get("requirement_intent_context_key") == intent_context_key else None
    previous_response = {
        "fields": prior_plan.get("field_suggestions", []),
        "source_joins": prior_plan.get("suggested_source_joins", []),
        "filters": prior_plan.get("suggested_filters", []),
        "notes": prior_plan.get("schema_generation_notes", []),
    }
    if not any(previous_response.values()):
        previous_response = None
    if previous_generation_mode != payload.generation_mode.value:
        previous_response = None
    if prior_plan.get("requirement_source_context_key") != source_context_key:
        previous_response = None
    previous_selected_objects = prior_plan.get("selected_source_objects")
    if previous_response and payload.generation_mode.value == "source_backed":
        previous_scope = set(previous_selected_objects or available_objects)
        if previous_scope != set(selected_source_objects):
            previous_response = None
        else:
            previous_response = {
                **previous_response,
                "fields": [item for item in previous_response.get("fields", []) if item.get("source_object") in selected_source_objects],
                "source_joins": [item for item in previous_response.get("source_joins", []) if item.get("left_object") in selected_source_objects and item.get("right_object") in selected_source_objects],
                "filters": [item for item in previous_response.get("filters", []) if item.get("source_object") in selected_source_objects],
            }
    stale_schema_keys = {
        "field_suggestions", "suggested_source_joins", "suggested_filters", "criteria_coverage",
        "schema_generation_note", "schema_generation_notes", "schema_model",
        "generated_query", "generated_query_fields", "generated_query_field_types",
        "source_joins", "predicates", "relationships", "schema_generation_feedback",
        "schema_generation_status",
    }
    retained_plan = {key: value for key, value in prior_plan.items() if key not in stale_schema_keys}
    if previous_intent is None:
        retained_plan.pop("requirement_intent", None)
        retained_plan.pop("requirement_intent_context_key", None)
    if prior_plan.get("requirement_source_context_key") != source_context_key:
        retained_plan.pop("requirement_source_context_key", None)
    plan.generation_mode = payload.generation_mode.value
    plan.source_connection_id = connection.id if connection else None
    plan.source_object = None
    plan.mapping = []
    plan.logical_plan = {
        **retained_plan,
        "schema_generation_feedback": feedback,
        "selected_source_objects": selected_source_objects if payload.generation_mode.value == "source_backed" else [],
        "schema_generation_status": {
            "generation_id": str(uuid4()),
            "status": "running",
            "stage": "starting",
            "stage_started_at": datetime.now(UTC).isoformat(),
            "started_at": datetime.now(UTC).isoformat(),
            "reused_requirement_outline": bool(previous_intent),
            "timings_ms": {},
        },
    }
    generation_id = plan.logical_plan["schema_generation_status"]["generation_id"]
    plan.state = "draft"
    plan.updated_at = datetime.now(UTC)
    record_scenario_audit(db, request, principal, role, plan, "scenario.schema_generation_started", {"mode": plan.generation_mode, "has_feedback": bool(feedback), "has_previous_response": previous_response is not None, "selected_source_object_count": len(selected_source_objects)})
    db.commit()
    db.refresh(plan)
    background_tasks.add_task(
        run_scenario_schema_generation,
        plan.id,
        generation_id,
        metadata,
        payload.generation_mode.value,
        selected_source_objects,
        feedback,
        previous_response,
        previous_intent,
        intent_context_key,
        source_context_key,
        principal,
        role,
        request.state.request_id,
    )
    return plan_output(db, plan)


@router.post("/scenario-plans/{plan_id}/query")
def generate_scenario_query(
    plan_id: UUID,
    request: Request,
    payload: ScenarioQueryRequest | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    plan = get_plan(db, plan_id)
    role = editable_role(db, principal, plan.workspace_id)
    require_source_coverage_review(plan)
    connection = plan_connection(db, plan)
    if plan.generation_mode == "source_backed" and connection is not None:
        from tigr.entity_claims import assert_source_namespace_available
        assert_source_namespace_available(db, connection.id, principal.subject)
    validation_repair = None
    assistant_model = "approved_schema_compiler"
    try:
        validated_mapping = validate_mapping(plan, connection)
        if plan.generation_mode == "synthetic":
            if not payload or not payload.query:
                raise ValueError("Enter a read-only SELECT or WITH query to guide synthetic generation")
            source_query = payload.query.strip()
            if not source_query.casefold().startswith(("select", "with")):
                raise ValueError("Synthetic generation accepts read-only SELECT or WITH queries")
            try:
                review_query, assistant_model, syntax_error, fix_summary, sql_output_fields = get_scenario_assistant().review_synthetic_sql(
                    source_query,
                    [{"name": item.target_field, "data_type": item.data_type} for item in validated_mapping],
                )
            except RuntimeError as review_error:
                raise ValueError(f"Gemma could not review the synthetic SQL syntax: {review_error}") from review_error
            if syntax_error:
                validation_repair = {"original_error": syntax_error, "fix_summary": fix_summary or "Corrected the SQL syntax."}
            output_fields = [item["name"] for item in sql_output_fields]
            output_field_types = {item["name"]: item["data_type"] for item in sql_output_fields}
            plan.logical_plan = {
                **(plan.logical_plan or {}), "generated_query": review_query,
                "generated_query_fields": output_fields, "generated_query_field_types": output_field_types,
            }
            query_mode = "synthetic_sql_generation_guidance"
        elif connection is None or connection.connector_type != "postgresql":
            raise ValueError("SQL building requires a PostgreSQL source connection")
        elif payload and payload.query:
            validate_readonly_query(connection, payload.query, [item.target_field for item in validated_mapping])
            plan.logical_plan = {**(plan.logical_plan or {}), "generated_query": payload.query, "generated_query_fields": [item.target_field for item in validated_mapping]}
            review_query = payload.query
            query_mode = "user_edited"
        else:
            plan.logical_plan = {key: value for key, value in (plan.logical_plan or {}).items() if key not in {"generated_query", "generated_query_fields", "generated_query_field_types"}}
            review_query = None
            query_mode = "schema_compiled"
        compiled = compile_query_plan(plan, connection)
        if review_query is None and plan.generation_mode == "source_backed":
            review_query = postgres_review_query(compiled)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    plan.state = "ready"
    plan.updated_at = datetime.now(UTC)
    record_scenario_audit(db, request, principal, role, plan, "scenario.query_generated", {"model": assistant_model, "query_mode": query_mode, "sql_repaired": validation_repair is not None})
    db.commit()
    db.refresh(plan)
    return {"plan": plan_output(db, plan), "query": review_query, "model": assistant_model, "validation_repair": validation_repair}


@router.post("/scenario-plans/{plan_id}/preview")
def preview_scenario_query(
    plan_id: UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    plan = get_plan(db, plan_id)
    workspace_role(db, principal, plan.workspace_id)
    if principal.actor_type != "human":
        raise HTTPException(status_code=404, detail="Scenario plan not found")
    require_source_coverage_review(plan)
    connection = plan_connection(db, plan)
    try:
        compiled = compile_query_plan(plan, connection)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    sample_plan = SimpleNamespace(
        id=plan.id, requirement=plan.requirement, volume=min(plan.volume, 5), generation_mode=plan.generation_mode,
        logical_plan=plan.logical_plan, source_object=plan.source_object, mapping=plan.mapping,
    )
    sample_compiled = compile_query_plan(sample_plan, connection)
    sample_error = None
    sample_values_withheld = plan.generation_mode == "source_backed"
    try:
        sample_rows = generate_rows(sample_plan, connection, validate_mapping(sample_plan, connection), sample_compiled)[:5]
    except (RuntimeError, ValueError, SQLAlchemyError):
        # Keep the generated SQL available for review even when the source cannot
        # currently provide sample rows (for example, credentials or permissions).
        sample_rows = []
        sample_error = "The query was built, but sample rows could not be loaded. Check the source connection and selected objects."
    sample_count = len(sample_rows)
    if sample_values_withheld:
        sample_rows = []
    return {
        "source": connection.name if connection else "Synthetic data",
        "object": plan.source_object,
        "objects": list(dict.fromkeys(
            str(item.get("source_object") or plan.source_object)
            for item in plan.mapping
            if item.get("source_object") or plan.source_object
        )),
        "query_kind": compiled["query_kind"],
        "query": (plan.logical_plan or {}).get("generated_query") or postgres_review_query(compiled),
        "parameter_count": len([key for key in compiled.get("parameters", {}) if key != "tigr_limit"]),
        "row_limit": compiled["row_limit"], "estimated_count": compiled["estimated_count"],
        "exact_count": compiled["exact_count"],
        "fields": (plan.logical_plan or {}).get("generated_query_fields") or [item["target_field"] for item in plan.mapping],
        "sample_rows": sample_rows, "sample_count": sample_count,
        "sample_values_withheld": sample_values_withheld,
        "sample_error": sample_error,
    }


def run_scenario_job(plan_id: UUID, job_id: UUID, dataset_id: UUID, version_id: UUID, require_exact_count: bool = False) -> None:
    db = SessionLocal()
    try:
        plan = db.get(ScenarioPlan, plan_id)
        job = db.get(Job, job_id)
        dataset = db.get(DataSet, dataset_id)
        version = db.get(DataSetVersion, version_id)
        if not all((plan, job, dataset, version)):
            return
        connection = plan_connection(db, plan)
        mapping = validate_mapping(plan, connection)
        compiled = compile_query_plan(plan, connection)
        generation_started = perf_counter()
        job.status = "running"
        job.progress = 20
        job.safe_summary = "Generating data from the approved scenario plan"
        db.add(JobStep(id=uuid4(), job_id=job.id, name="generation", status="running", safe_summary="Generation started"))
        db.commit()
        if plan.generation_mode == "source_backed" and connection is not None:
            from tigr.entity_claims import assert_source_namespace_available
            assert_source_namespace_available(db, connection.id, job.actor_id)
        rows = generate_rows(plan, connection, mapping, compiled)
        from tigr.entity_claims import assert_job_current
        assert_job_current(db, job.id)
        relationships = (plan.logical_plan or {}).get("relationships", [])
        output_mapping = synthetic_query_output_mapping(plan, mapping) if plan.generation_mode == "synthetic" else mapping
        if plan.generation_mode == "synthetic" and (plan.logical_plan or {}).get("generated_query"):
            output_names = {item.target_field for item in output_mapping}
            relationships = [item for item in relationships if item.get("parent_field") in output_names and item.get("child_field") in output_names]
        schema_valid, warning_count = validate_rows(rows, output_mapping, relationships)
        exact_count_met = not require_exact_count or len(rows) == plan.volume
        valid = schema_valid and exact_count_met
        repair_count = 0
        while plan.generation_mode != "synthetic" and not schema_valid and repair_count < MAX_REPAIR_ATTEMPTS:
            repair_count += 1
            rows = repair_rows(rows, output_mapping)
            schema_valid, warning_count = validate_rows(rows, output_mapping, relationships)
            valid = schema_valid and exact_count_met
        is_synthetic = plan.generation_mode == "synthetic"
        reference, digest = artifact_reference(dataset.id, rows) if is_synthetic else (None, None)
        artifact_size = store_dataset_records(db, dataset, version, rows)[1] if is_synthetic else 0
        duration_ms = int((perf_counter() - generation_started) * 1000)
        output_format = str((version.generation_settings or {}).get("output_format", "json"))
        reference = f"artifact://{dataset.id}/v{version.version_number}/dataset.{output_format}"
        version.validation_state = "passed" if valid else "failed"
        version.record_count = len(rows)
        version.privacy_state = "compliant" if plan.generation_mode == "synthetic" and valid else "requires_masking"
        summary = (
            f"Generation returned {len(rows)} of {plan.volume} requested records"
            if require_exact_count and not exact_count_met
            else "Generated rows conform to the selected schema" if valid
            else "Generated rows failed schema validation"
        )
        version.validation_evidence = {
            "summary": summary,
            "record_count": len(rows), "field_count": len(output_mapping), "warning_count": warning_count,
            "validation_version": "scenario-schema-v1", "schema_version": "scenario-schema-v1",
            "duration_ms": duration_ms, "relationships": relationships,
        }
        if is_synthetic:
            version.validation_evidence.update({"artifact_ref": reference, "hash": digest})
        if not valid:
            version.lifecycle_state = "quarantined"
            version.privacy_state = "blocked"
            plan.state = "quarantined"
        elif plan.generation_mode == "synthetic":
            version.lifecycle_state = "ready"
            plan.state = "succeeded"
        else:
            version.lifecycle_state = "draft"
            plan.state = "succeeded"
        service_actor = Principal(subject="tigr-scenario-worker", actor_type="service")
        record_status_change(db, dataset, version, "draft", version.lifecycle_state, service_actor, job.request_id,
                             "Scenario generation completed" if valid else "Scenario output failed validation")
        record_validation(db, dataset, version, job.request_id, "scenario_schema")
        if valid and is_synthetic:
            record_generated_artifact(db, dataset, version, artifact_size, reference, "text/csv" if output_format == "csv" else "application/json", digest)
        event = LineageEventCreate(
            event_type="generation_completed", stage="generation", job_id=job.id,
            scenario_version=str(plan.id), source_reference=str(connection.id) if connection else None,
            artifact_reference=reference, outcome="succeeded" if valid else "failed",
            safe_evidence={"record_count": len(rows), "field_count": len(mapping), "provider": "Ollama" if plan.generation_mode == "synthetic" else connection.connector_type,
                           "model_version": (plan.logical_plan or {}).get("schema_model", "manual-schema") if plan.generation_mode == "synthetic" else "not_applicable", "prompt_version": "scenario-schema-v2" if plan.generation_mode == "synthetic" else "not_applicable",
                           "usage_count": len(rows), "duration_ms": duration_ms, **({"hash": digest} if digest else {})},
        )
        append_lineage_event(db, dataset, version, event, service_actor)
        plan.dataset_id = dataset.id
        job.progress = 100
        job.status = "succeeded" if valid else "failed"
        job.safe_summary = "Generation completed" if valid else summary
        db.add(JobStep(id=uuid4(), job_id=job.id, name="schema_validation", status="succeeded" if valid else "failed", safe_summary="Schema checks completed"))
        db.add(OutboxEvent(id=uuid4(), aggregate_type="dataset", aggregate_id=str(dataset.id), workspace_id=str(dataset.workspace_id), event_type="scenario.generation_completed", payload={"job_id": str(job.id), **({"artifact_reference": reference} if reference else {})}, request_id=job.request_id))
        record_audit(db, Principal(subject="tigr-scenario-worker", actor_type="service"), job.request_id, "scenario.generation_completed", "dataset_version", str(version.id), dataset.workspace_id, None, {"record_count": len(rows), "validation_state": version.validation_state, "privacy_state": version.privacy_state})
        db.commit()
    except Exception:  # noqa: BLE001 - background failures must be reduced to safe job state.
        db.rollback()
        plan = db.get(ScenarioPlan, plan_id)
        job = db.get(Job, job_id)
        dataset = db.get(DataSet, dataset_id)
        version = db.get(DataSetVersion, version_id)
        if job and job.status not in ("queued", "running"):
            return
        if plan:
            plan.state = "failed"
        if dataset and version:
            try:
                append_lineage_event(db, dataset, version, LineageEventCreate(
                    event_type="generation_failed", stage="generation", job_id=job_id,
                    scenario_version=str(plan_id), outcome="failed",
                    safe_evidence={"reason_code": "generation_failed", "summary": "Generation did not complete"},
                ), Principal(subject="tigr-scenario-worker", actor_type="service"))
            except SQLAlchemyError:
                db.rollback()
                plan = db.get(ScenarioPlan, plan_id)
                if plan:
                    plan.state = "failed"
        if job:
            job.status = "failed"
            job.safe_summary = "Generation failed; review the scenario configuration and connection"
            db.add(JobStep(id=uuid4(), job_id=job.id, name="generation", status="failed", safe_summary=job.safe_summary))
            db.commit()
    finally:
        db.close()


@router.post("/scenario-plans/{plan_id}/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_scenario(
    plan_id: UUID,
    payload: GenerateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    plan = get_plan(db, plan_id)
    role = editable_role(db, principal, plan.workspace_id)
    require_source_coverage_review(plan)
    if plan.state != "ready" or clarifying_questions(plan):
        raise HTTPException(status_code=409, detail="Complete clarifications, mapping, and query review before generating")
    plan.name = payload.dataset_name
    plan.target_use = payload.target_use
    plan.volume = payload.maximum_records
    connection = plan_connection(db, plan)
    if plan.generation_mode == "source_backed":
        if not payload.confirm_source_extraction:
            raise HTTPException(status_code=409, detail="Explicit confirmation is required before source extraction")
        if connection is None:
            raise HTTPException(status_code=422, detail="Choose an enabled source connection")
    try:
        mapping = validate_mapping(plan, connection)
        compile_query_plan(plan, connection)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assistant = get_scenario_assistant()
    job_id = uuid4()
    provenance = Provenance(
        origin="synthetic" if plan.generation_mode == "synthetic" else "source_backed",
        source_connection_id=connection.id if connection else None,
        source_object=plan.source_object if connection else None,
        source_schema_version="connection-structure-preview" if connection else None,
        mapping_version="scenario-mapping-v1", scenario_id=str(plan.id), scenario_version="1", job_id=job_id,
    )
    output_mapping = synthetic_query_output_mapping(plan, mapping) if plan.generation_mode == "synthetic" else mapping
    schema_scope = [SchemaField(field=item.target_field, data_type=item.data_type, required=item.required, json_path=item.json_path) for item in output_mapping]
    draft = DraftDataSet(name=payload.dataset_name, description=plan.requirement[:2000], creation_pathway="scenario_to_data", schema_scope=schema_scope,
                         selected_fields=[item.target_field for item in output_mapping], provenance=provenance, permitted_use=[payload.target_use],
                         maximum_records=plan.volume, output_format=payload.output_format, expiry_at=payload.expiry_at)
    dataset, version = _create_or_reuse_scenario_dataset(
        db, principal, plan.workspace_id, payload.draft_dataset_id, draft, request.state.request_id,
    )
    dataset_id = dataset.id
    job = Job(
        id=job_id, job_type="scenario_generation", status="queued", workspace_id=str(plan.workspace_id),
        scope_key=f"workspace:{plan.workspace_id}", idempotency_key=f"scenario:{plan.id}",
        actor_type=principal.actor_type, actor_id=principal.subject, actor_role=role.value,
        request_id=request.state.request_id, progress=0, safe_summary="Scenario generation queued",
    )
    db.add(job)
    db.flush()
    version_id = version.id
    plan.job_id = job_id
    plan.dataset_id = dataset_id
    plan.state = "running"
    record_scenario_audit(db, request, principal, role, plan, "scenario.generation_requested", {"mode": plan.generation_mode, "volume": plan.volume})
    db.add(OutboxEvent(id=uuid4(), aggregate_type="scenario_plan", aggregate_id=str(plan.id), workspace_id=str(plan.workspace_id), event_type="scenario.generation_requested", payload={"plan_id": str(plan.id), "job_id": str(job_id), "dataset_id": str(dataset_id), "version_id": str(version_id), "assistant_provider": assistant.provider_name, "model_version": assistant.model_version, "prompt_version": assistant.prompt_version}, request_id=request.state.request_id))
    db.commit()
    if get_settings().inline_jobs:
        background_tasks.add_task(run_scenario_job, plan.id, job_id, dataset_id, version_id)
    return {"plan_id": str(plan.id), "job_id": str(job_id), "dataset_id": str(dataset_id), "version_number": 1, "status": "queued"}


@router.get("/scenario-plans/{plan_id}/artifact")
def download_scenario_artifact(
    plan_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    plan = get_plan(db, plan_id)
    role = workspace_role(db, principal, plan.workspace_id)
    if role not in (HumanRole.TEST_DATA_ENGINEER, HumanRole.ADMIN):
        raise HTTPException(status_code=403, detail="Artifact access is not permitted")
    dataset = db.get(DataSet, plan.dataset_id) if plan.dataset_id else None
    version = db.scalar(select(DataSetVersion).where(DataSetVersion.dataset_id == plan.dataset_id, DataSetVersion.version_number == 1)) if plan.dataset_id else None
    if (plan.state != "succeeded" or not dataset or not version or version.privacy_state != "compliant"
            or version.lifecycle_state not in ("ready", "published") or not dataset_records_privacy_ready(version)):
        raise HTTPException(status_code=404, detail="Artifact not found")
    from tigr.entity_claims import filter_dataset_records
    rows, allowed_count, blocked_count = filter_dataset_records(db, version, principal.subject)
    if not rows:
        raise HTTPException(status_code=404, detail="Artifact not found")
    output_format = str((version.generation_settings or {}).get("output_format", "json"))
    append_lineage_event(db,dataset,version,LineageEventCreate(event_type="artifact_downloaded",stage="delivery",artifact_reference=f"artifact://{dataset.id}/dataset.{output_format}",
        safe_evidence={"format":output_format,"summary":"Authorized artifact download"}),principal)
    record_audit(db,principal,request.state.request_id,"dataset.artifact_downloaded","dataset_version",str(version.id),dataset.workspace_id,role,
        {"version_number":version.version_number,"format":output_format,"privacy_state":version.privacy_state,
         "record_count":allowed_count,"blocked_record_count":blocked_count})
    db.commit()
    return Response(serialize_dataset_records(rows, output_format), media_type="text/csv" if output_format == "csv" else "application/json",
                    headers={"Content-Disposition": f'attachment; filename="scenario-{plan.id}.{output_format}"'})
