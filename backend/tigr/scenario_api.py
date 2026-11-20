import hashlib
import json
import re
from datetime import UTC, datetime
from time import perf_counter
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select, text
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
    create_version,
    dataset_records_privacy_ready,
    provenance_with_lock_configuration,
    record_generated_artifact,
    record_status_change,
    record_validation,
    read_dataset_records,
    serialize_dataset_records,
    store_dataset_records,
    workspace_pii_masking_enabled,
)
from tigr.datasets import DataSet, DataSetVersion
from tigr.db import SessionLocal
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.jobs import Job, JobStep, OutboxEvent
from tigr.schema_records import ArtifactRecord, DataSetRecord
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
    MappingField,
    ScenarioCreate,
    ScenarioPlanUpdate,
    ScenarioPreviewRequest,
    ScenarioQueryRequest,
    SchemaGenerationRequest,
)
from tigr.scenarios import ScenarioPlan
from tigr.ticket_linking import link_generation_to_ticket

router = APIRouter(prefix="/api/v1", tags=["scenario to data"])


def _identity_field_names(fields: list[MappingField]) -> list[str]:
    names = []
    for field in fields:
        parts = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", field.target_field)
        tokens = re.findall(r"[a-z0-9]+", parts.casefold())
        if "id" in tokens or "name" in tokens:
            names.append(field.target_field)
    return names


def _identity_value_key(value: object) -> str:
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip().casefold()
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


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
    """Require each source-backed criterion to be mapped or explicitly accepted."""
    if plan.generation_mode != "source_backed":
        return True, ""
    logical_plan = plan.logical_plan or {}
    intent = logical_plan.get("requirement_intent") or {}
    criteria = intent.get("criteria") or []
    coverage = logical_plan.get("criteria_coverage") or []
    accepted = {str(item) for item in (logical_plan.get("accepted_uncovered_criteria") or [])}
    if not criteria:
        return False, "Generate a requirement interpretation before continuing."
    coverage_by_id = {str(item.get("criterion_id")): item for item in coverage if isinstance(item, dict)}
    unresolved = [
        item for item in criteria
        if not coverage_by_id.get(str(item.get("id")), {}).get("covered")
        and str(item.get("id")) not in accepted
    ]
    if unresolved:
        return False, "Map each uncovered requirement criterion or accept its gap in Step 2 before continuing."
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
    has_records = bool(version and db.scalar(select(DataSetRecord.id).where(DataSetRecord.version_id == version.id).limit(1)))
    has_artifact = bool(version and db.scalar(select(ArtifactRecord.id).where(ArtifactRecord.version_id == version.id).limit(1)))
    blank_draft = bool(
        version and provenance.get("origin") == "not_set" and not version.schema_scope
        and not version.selected_fields and version.record_count is None and not version.validation_evidence
    )
    retryable_scenario_draft = bool(
        version and provenance.get("scenario_id") == payload.provenance.scenario_id
        and version.lifecycle_state in ("draft", "failed")
        and version.record_count is None
        and not has_records and not has_artifact
    )
    if version is None or has_records or has_artifact:
        raise HTTPException(status_code=409, detail="This draft already has generation data. Create a fresh draft to generate again.")

    dataset.name = payload.name
    dataset.description = payload.description
    if retryable_scenario_draft:
        # Existing version manifests are immutable. Keep the failed, empty
        # version as history and put the retry configuration in a new draft.
        return dataset, create_version(db, dataset, payload, principal, correlation_id)
    if version.version_number != 1 or not blank_draft:
        raise HTTPException(status_code=409, detail="This draft already has generation data. Create a fresh draft to generate again.")

    version.schema_scope = [field.model_dump() for field in payload.schema_scope]
    version.selected_fields = payload.selected_fields
    version.provenance = provenance_with_lock_configuration(
        db, payload.provenance.model_dump(mode="json", exclude_none=True), dataset.workspace_id,
    )
    version.generation_settings = {
        "target_use": payload.permitted_use[0] if payload.permitted_use else (payload.description or ""),
        "maximum_records": payload.maximum_records,
        "output_format": payload.output_format,
    }
    version.lifecycle_state = "draft"
    version.privacy_state = "not_assessed"
    version.validation_state = "not_run"
    version.record_count = None
    version.validation_evidence = {}
    version.expiry_at = payload.expiry_at
    version.permitted_use = payload.permitted_use
    version.fields = _dataset_fields(dataset, version, payload)
    db.flush()
    return dataset, version


def _failed_generation_is_retryable(db: Session, plan: ScenarioPlan, job: Job | None = None) -> bool:
    # The job is the authoritative execution state. A worker recovery path or
    # older runner may have committed the failed job before updating its plan.
    if (plan.state != "failed" and not (job and job.status == "failed")) or plan.dataset_id is None:
        return False
    logical_plan = plan.logical_plan or {}
    raw_version_id = logical_plan.get("generated_version_id")
    try:
        version_id = UUID(str(raw_version_id)) if raw_version_id else None
    except (TypeError, ValueError):
        version_id = None
    version = db.get(DataSetVersion, version_id) if version_id else None
    if version is None or version.dataset_id != plan.dataset_id:
        version = db.scalar(
            select(DataSetVersion).where(DataSetVersion.dataset_id == plan.dataset_id)
            .order_by(DataSetVersion.version_number.desc()).limit(1)
        )
    if (version is None or version.lifecycle_state not in ("draft", "failed")
            or version.record_count is not None
            or str((version.provenance or {}).get("scenario_id")) != str(plan.id)):
        return False
    has_records = db.scalar(
        select(DataSetRecord.id).where(DataSetRecord.version_id == version.id).limit(1)
    ) is not None
    has_artifact = db.scalar(
        select(ArtifactRecord.id).where(ArtifactRecord.version_id == version.id).limit(1)
    ) is not None
    return not has_records and not has_artifact


def plan_output(db: Session, plan: ScenarioPlan) -> dict:
    assistant = get_scenario_assistant()
    suggestions = (plan.logical_plan or {}).get("field_suggestions", [])
    logical_plan = plan.logical_plan or {}
    job_id = plan.job_id
    job = db.get(Job, job_id) if job_id else None
    dataset = db.get(DataSet, plan.dataset_id) if plan.dataset_id else None
    state = plan.state
    # Do not let a stale plan state keep the client in its running UI after the
    # execution record has reached a terminal state.
    if state == "running" and job and job.status == "failed":
        state = "failed"
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
        "state": state, "job": {"id": job.id, "status": job.status, "progress": job.progress, "safe_summary": job.safe_summary} if job else None,
        "dataset_id": plan.dataset_id,
        "dataset_reference_code": dataset.reference_code if dataset else None,
        "dataset_version_id": logical_plan.get("generated_version_id"),
        "dataset_version_number": logical_plan.get("generated_version_number"),
        "generation_retryable": _failed_generation_is_retryable(db, plan, job),
        "created_at": plan.created_at, "updated_at": plan.updated_at,
        "assistant": {"provider": assistant.provider_name, "model_version": (plan.logical_plan or {}).get("schema_model", assistant.model_version), "prompt_version": assistant.prompt_version},
    }


def _parse_draft_dataset_id(raw_id: object) -> UUID | None:
    try:
        return UUID(str(raw_id)) if raw_id else None
    except (TypeError, ValueError):
        return None


def _linked_draft_dataset_id(plan: ScenarioPlan) -> UUID | None:
    raw_id = ((plan.logical_plan or {}).get("draft_data") or {}).get("draft_dataset_id")
    return _parse_draft_dataset_id(raw_id) or _parse_draft_dataset_id(plan.dataset_id)


def _create_scenario_work_job(db: Session, plan: ScenarioPlan, principal: Principal, role: HumanRole,
                              request_id: str, job_type: str, generation_id: str,
                              step_name: str, summary: str) -> UUID:
    job_id = uuid4()
    job = Job(
        id=job_id, job_type=job_type, status="queued", workspace_id=str(plan.workspace_id),
        scope_key=f"workspace:{plan.workspace_id}",
        idempotency_key=f"scenario-plan:{plan.id}:{job_type}:{generation_id}",
        actor_type=principal.actor_type, actor_id=principal.subject, actor_role=role.value,
        request_id=request_id, progress=0, safe_summary=summary,
    )
    db.add(job)
    db.add(JobStep(id=uuid4(), job_id=job_id, name=step_name, status="queued", safe_summary=summary))
    db.flush()
    return job_id


def _set_scenario_work_job(db: Session, job_id: str | UUID | None, *, status: str,
                           progress: int, summary: str, step_name: str, step_status: str,
                           step_summary: str) -> None:
    if not job_id:
        return
    try:
        parsed_job_id = UUID(str(job_id))
    except ValueError:
        return
    job = db.get(Job, parsed_job_id)
    if job is None:
        return
    job.status = status
    job.progress = progress
    job.safe_summary = summary
    job.heartbeat_at = datetime.now(UTC)
    step = db.scalar(select(JobStep).where(JobStep.job_id == parsed_job_id, JobStep.name == step_name))
    if step is None:
        step = JobStep(id=uuid4(), job_id=parsed_job_id, name=step_name, status=step_status, safe_summary=step_summary)
        db.add(step)
    else:
        step.status = step_status
        step.safe_summary = step_summary


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
        job_status = current.get("status", "running")
        stage = str(current.get("stage") or "schema_generation")
        progress = (
            100 if job_status in {"complete", "failed"}
            else 10 if stage == "starting"
            else 25 if "requirement" in stage
            else 55 if "grounding" in stage
            else 80 if "validating" in stage
            else 90
        )
        final = job_status == "complete"
        _set_scenario_work_job(
            db, current.get("job_id"),
            status="succeeded" if final else "failed" if job_status == "failed" else "running",
            progress=progress,
            summary="Schema generation completed" if final else current.get("error") or f"Schema generation: {stage.replace('_', ' ')}",
            step_name="schema_generation",
            step_status="succeeded" if final else "failed" if job_status == "failed" else "running",
            step_summary="Schema suggestions are ready" if final else current.get("error") or f"Working on {stage.replace('_', ' ')}",
        )


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
            _set_scenario_work_job(
                db,
                current_status.get("job_id"),
                status="succeeded",
                progress=100,
                summary="Schema generation completed",
                step_name="schema_generation",
                step_status="succeeded",
                step_summary="Schema suggestions are ready",
            )
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
    raw_draft_dataset_id = payload.draft_data.get("draft_dataset_id")
    draft_dataset_id = _parse_draft_dataset_id(raw_draft_dataset_id)
    if raw_draft_dataset_id and draft_dataset_id is None:
        raise HTTPException(status_code=422, detail="Scenario draft data set ID is invalid")
    if draft_dataset_id:
        draft_dataset = db.get(DataSet, draft_dataset_id)
        if draft_dataset is None or draft_dataset.workspace_id != workspace_id or draft_dataset.creation_pathway != "scenario_to_data":
            raise HTTPException(status_code=404, detail="Scenario draft data set not found in this workspace")
        # Concurrent page effects or tabs can both attempt draft recovery.
        # Serialize those recoveries in PostgreSQL, then reuse the first plan.
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:draft_plan_key, 0))"),
                {"draft_plan_key": f"scenario-draft:{workspace_id}:{draft_dataset_id}"},
            )
        existing_plans = db.scalars(
            select(ScenarioPlan).where(ScenarioPlan.workspace_id == workspace_id)
            .order_by(ScenarioPlan.updated_at.desc())
        ).all()
        existing = next((item for item in existing_plans if _linked_draft_dataset_id(item) == draft_dataset_id), None)
        if existing is not None:
            return plan_output(db, existing)
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
    rows = db.scalars(select(ScenarioPlan).where(ScenarioPlan.workspace_id == workspace_id).order_by(ScenarioPlan.updated_at.desc())).all()
    draft_dataset_positions: dict[UUID, int] = {}
    unique_rows = []
    for row in rows:
        draft_dataset_id = _linked_draft_dataset_id(row)
        if draft_dataset_id is not None and draft_dataset_id in draft_dataset_positions:
            position = draft_dataset_positions[draft_dataset_id]
            # Prefer the plan that actually generated or is generating the data set
            # over a recovery-only draft plan if older duplicate records exist.
            if row.dataset_id is not None and unique_rows[position].dataset_id is None:
                unique_rows[position] = row
            continue
        if draft_dataset_id is not None:
            draft_dataset_positions[draft_dataset_id] = len(unique_rows)
        unique_rows.append(row)
        if len(unique_rows) == 50:
            break
    return [plan_output(db, row) for row in unique_rows]


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


@router.post("/scenario-plans/{plan_id}/quarantine")
def quarantine_scenario_draft(
    plan_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    plan = get_plan(db, plan_id)
    role = editable_role(db, principal, plan.workspace_id)
    logical_plan = plan.logical_plan or {}
    schema_status = logical_plan.get("schema_generation_status") or {}
    sample_status = logical_plan.get("sample_generation_status") or {}
    if schema_status.get("status") == "running" or sample_status.get("status") == "running":
        raise HTTPException(status_code=409, detail="Wait for the current schema or sample generation job to finish before quarantining this draft.")
    job = db.get(Job, plan.job_id) if plan.job_id else None
    if job and job.status in ("queued", "running", "validating"):
        raise HTTPException(status_code=409, detail="Wait for the full data set generation job to finish before quarantining this draft.")

    dataset_id = _linked_draft_dataset_id(plan) or plan.dataset_id
    if dataset_id is None:
        plan.state = "quarantined"
        plan.updated_at = datetime.now(UTC)
        record_scenario_audit(db, request, principal, role, plan, "scenario.draft_quarantined", {"dataset_version_present": False})
        db.commit()
        db.refresh(plan)
        return plan_output(db, plan)
    dataset = db.get(DataSet, dataset_id)
    if dataset is None or dataset.workspace_id != plan.workspace_id or dataset.creation_pathway != "scenario_to_data":
        raise HTTPException(status_code=404, detail="Scenario draft data set not found")
    version = db.scalar(select(DataSetVersion).where(
        DataSetVersion.dataset_id == dataset.id,
        DataSetVersion.version_number == dataset.latest_version_number,
    ).with_for_update())
    if (version is None or version.lifecycle_state not in ("draft", "ready", "failed")
            or version.publication_state == "published" or version.published_at is not None):
        raise HTTPException(status_code=409, detail="Only an unpublished Draft, Ready, or Failed version can be moved to Quarantined.")

    previous_state = version.lifecycle_state
    version.lifecycle_state = "quarantined"
    dataset.updated_at = datetime.now(UTC)
    plan.state = "quarantined"
    plan.updated_at = datetime.now(UTC)
    record_status_change(db, dataset, version, previous_state, "quarantined", principal, request.state.request_id,
                         "Unpublished version moved to quarantine by its owner")
    append_lineage_event(db, dataset, version, LineageEventCreate(
        event_type="draft_quarantined", stage="governance", outcome="warning",
        safe_evidence={"summary": "Draft version was moved to quarantine by its owner"},
    ), principal)
    record_scenario_audit(db, request, principal, role, plan, "scenario.draft_quarantined", {"dataset_id": str(dataset.id), "version_number": version.version_number})
    db.commit()
    db.refresh(plan)
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
    accepted_uncovered = changes.pop("accepted_uncovered_criteria", None)
    if accepted_uncovered is not None:
        logical_plan = dict(plan.logical_plan or {})
        intent = logical_plan.get("requirement_intent") or {}
        criteria = intent.get("criteria", [])
        criteria_ids = {str(item.get("id")) for item in criteria if isinstance(item, dict) and item.get("id")}
        unknown = set(accepted_uncovered) - criteria_ids
        if unknown:
            raise HTTPException(status_code=422, detail="Accepted requirement criteria must exist in the current requirement interpretation")
        coverage_by_id = {
            str(item.get("criterion_id")): item
            for item in logical_plan.get("criteria_coverage", [])
            if isinstance(item, dict)
        }
        uncovered_ids = {
            criterion_id for criterion_id in criteria_ids
            if not coverage_by_id.get(criterion_id, {}).get("covered")
        }
        logical_plan["accepted_uncovered_criteria"] = sorted(set(accepted_uncovered) & uncovered_ids)
        plan.logical_plan = logical_plan
    criteria_inputs_changed = (
        ("requirement" in changes and changes["requirement"] != plan.requirement)
        or ("expected_states" in changes and changes["expected_states"] != plan.expected_states)
        or ("exclusions" in changes and changes["exclusions"] != plan.exclusions)
        or ("generation_mode" in changes and changes["generation_mode"] != plan.generation_mode)
        or ("source_connection_id" in changes and changes["source_connection_id"] != plan.source_connection_id)
    )
    if criteria_inputs_changed:
        logical_plan = dict(plan.logical_plan or {})
        logical_plan.pop("accepted_uncovered_criteria", None)
        plan.logical_plan = logical_plan
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
    if plan.generation_mode == "synthetic" and plan.mapping:
        # Synthetic source_field values are aliases only. Normalize legacy
        # mappings and collapse repeated output names when saving any draft
        # update, so stale aliases cannot prevent a renamed draft from saving.
        normalized_mapping = []
        seen_targets = set()
        for raw_field in plan.mapping:
            field = MappingField.model_validate(raw_field)
            target_key = field.target_field.casefold()
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            field.source_field = field.target_field
            field.source_object = None
            normalized_mapping.append(field.model_dump(mode="json"))
        plan.mapping = normalized_mapping
    connection = plan_connection(db, plan)
    try:
        if plan.mapping:
            validate_mapping(plan, connection)
        if any(key in payload.model_fields_set for key in ("mapping", "predicates", "source_joins", "relationships")):
            plan.logical_plan = {key: value for key, value in (plan.logical_plan or {}).items() if key not in {"generated_query", "generated_query_fields", "generated_query_field_types"}}
        if set(payload.model_fields_set) - {"draft_data"}:
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
        objects = preview.get("objects", []) if connection.connector_type in ("postgresql", "mysql") else [{"name": (connection.connector_config or {}).get("file_path", connection.name), "fields": connection_fields(connection)}]
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
        if connection.connector_type in ("postgresql", "mysql"):
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
        if requested_objects and connection.connector_type not in ("postgresql", "mysql"):
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
        "accepted_uncovered_criteria",
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
    generation_id = str(uuid4())
    work_job_id = _create_scenario_work_job(
        db, plan, principal, role, request.state.request_id,
        "scenario_schema_generation", generation_id, "schema_generation", "Schema generation queued",
    )
    plan.generation_mode = payload.generation_mode.value
    plan.source_connection_id = connection.id if connection else None
    plan.source_object = None
    plan.mapping = []
    plan.logical_plan = {
        **retained_plan,
        "schema_generation_feedback": feedback,
        "selected_source_objects": selected_source_objects if payload.generation_mode.value == "source_backed" else [],
        "schema_generation_status": {
            "generation_id": generation_id,
            "job_id": str(work_job_id),
            "status": "running",
            "stage": "starting",
            "stage_started_at": datetime.now(UTC).isoformat(),
            "started_at": datetime.now(UTC).isoformat(),
            "reused_requirement_outline": bool(previous_intent),
            "timings_ms": {},
        },
    }
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


def run_synthetic_query_generation(plan_id: UUID, generation_id: str, query: str, include_sample: bool) -> None:
    started = perf_counter()
    db = SessionLocal()
    try:
        plan = db.get(ScenarioPlan, plan_id)
        if plan is None:
            return
        logical_plan = dict(plan.logical_plan or {})
        status_value = dict(logical_plan.get("sample_generation_status") or {})
        if status_value.get("generation_id") != generation_id:
            return
        work_job_id = status_value.get("job_id")
        _set_scenario_work_job(
            db, work_job_id, status="running", progress=5,
            summary="TIGR is reviewing the synthetic query",
            step_name="query_review", step_status="running", step_summary="SQL review started",
        )
        db.commit()
        mapping = validate_mapping(plan, None)
        reviewed_query, model, syntax_error, fix_summary, output_fields = get_scenario_assistant().review_synthetic_sql(
            query,
            [{"name": item.target_field, "data_type": item.data_type} for item in mapping],
        )
        logical_plan = dict(plan.logical_plan or {})
        logical_plan.update({
            "generated_query": reviewed_query,
            "generated_query_fields": [item["name"] for item in output_fields],
            "generated_query_field_types": {item["name"]: item["data_type"] for item in output_fields},
        })
        if syntax_error:
            logical_plan["query_validation_repair"] = {"original_error": syntax_error, "fix_summary": fix_summary or "Corrected the SQL syntax."}
        else:
            logical_plan.pop("query_validation_repair", None)
        status_value.update({"stage": "generating_sample" if include_sample else "saving_query"})
        logical_plan["sample_generation_status"] = status_value
        plan.logical_plan = logical_plan
        plan.state = "ready"
        plan.updated_at = datetime.now(UTC)
        _set_scenario_work_job(
            db, work_job_id, status="running", progress=45 if include_sample else 85,
            summary="SQL review completed; generating a sample" if include_sample else "SQL review completed",
            step_name="query_review", step_status="succeeded", step_summary="SQL was reviewed against the Step 2 fields",
        )
        if include_sample:
            _set_scenario_work_job(
                db, work_job_id, status="running", progress=45,
                summary="TIGR is generating sample rows",
                step_name="sample_generation", step_status="running", step_summary="Sample generation started",
            )
        db.commit()

        preview = _build_scenario_preview(db, plan) if include_sample else None
        status_value = dict((plan.logical_plan or {}).get("sample_generation_status") or {})
        if status_value.get("generation_id") != generation_id:
            return
        failed = bool(preview and preview.get("sample_error"))
        status_value.update({
            "status": "failed" if failed else "complete",
            "stage": "failed" if failed else "complete",
            "error": preview.get("sample_error") if failed and preview else None,
            "model": model,
            "elapsed_ms": int((perf_counter() - started) * 1000),
            "completed_at": datetime.now(UTC).isoformat(),
        })
        logical_plan = dict(plan.logical_plan or {})
        logical_plan["sample_generation_status"] = status_value
        if preview is not None:
            logical_plan["synthetic_preview"] = preview
        plan.logical_plan = logical_plan
        plan.updated_at = datetime.now(UTC)
        terminal_status = "failed" if failed else "succeeded"
        terminal_summary = status_value.get("error") or ("Synthetic sample generation completed" if include_sample else "Synthetic SQL review completed")
        _set_scenario_work_job(
            db, work_job_id, status=terminal_status, progress=100, summary=terminal_summary,
            step_name="sample_generation" if include_sample else "query_review",
            step_status="failed" if failed else "succeeded",
            step_summary=terminal_summary,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - keep background jobs from remaining stuck.
        db.rollback()
        plan = db.get(ScenarioPlan, plan_id)
        if plan is not None:
            logical_plan = dict(plan.logical_plan or {})
            status_value = dict(logical_plan.get("sample_generation_status") or {})
            if status_value.get("generation_id") == generation_id:
                status_value.update({
                    "status": "failed", "stage": "failed",
                    "error": str(exc)[:500] if isinstance(exc, ValueError) else "TIGR could not complete this step. Review the query and reviewed fields, then retry.",
                    "elapsed_ms": int((perf_counter() - started) * 1000),
                    "completed_at": datetime.now(UTC).isoformat(),
                })
                logical_plan["sample_generation_status"] = status_value
                plan.logical_plan = logical_plan
                plan.updated_at = datetime.now(UTC)
                error_summary = status_value["error"]
                _set_scenario_work_job(
                    db, status_value.get("job_id"), status="failed", progress=100,
                    summary=error_summary, step_name="query_review", step_status="failed",
                    step_summary=error_summary,
                )
                db.commit()
    finally:
        db.close()


@router.post("/scenario-plans/{plan_id}/query")
def generate_scenario_query(
    plan_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: ScenarioQueryRequest | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    plan = get_plan(db, plan_id)
    role = editable_role(db, principal, plan.workspace_id)
    require_source_coverage_review(plan)
    connection = plan_connection(db, plan)
    if plan.generation_mode == "synthetic":
        query = (payload.query if payload else "") or ""
        if not query.casefold().startswith(("select", "with")):
            raise HTTPException(status_code=422, detail="Synthetic generation accepts read-only SELECT or WITH queries")
        try:
            validate_mapping(plan, None)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        generation_id = uuid4().hex
        work_job_id = _create_scenario_work_job(
            db, plan, principal, role, request.state.request_id,
            "scenario_sample_generation", generation_id, "query_review", "TIGR sample generation queued",
        )
        if payload and payload.background_sample:
            db.add(JobStep(id=uuid4(), job_id=work_job_id, name="sample_generation", status="queued", safe_summary="Sample generation queued"))
        logical_plan = dict(plan.logical_plan or {})
        logical_plan.pop("synthetic_preview", None)
        correction_feedback = (payload.correction_feedback or "").strip() if payload else ""
        if correction_feedback:
            logical_plan["generation_correction_feedback"] = correction_feedback
        else:
            logical_plan.pop("generation_correction_feedback", None)
        logical_plan["sample_generation_status"] = {
            "generation_id": generation_id, "status": "running",
            "stage": "reviewing_sql", "started_at": datetime.now(UTC).isoformat(),
            "include_sample": bool(payload and payload.background_sample),
            "job_id": str(work_job_id),
        }
        plan.logical_plan = logical_plan
        plan.state = "ready"
        plan.updated_at = datetime.now(UTC)
        record_scenario_audit(db, request, principal, role, plan, "scenario.query_generation_started", {"include_sample": bool(payload and payload.background_sample)})
        db.commit()
        background_tasks.add_task(run_synthetic_query_generation, plan.id, generation_id, query, bool(payload and payload.background_sample))
        return {"status": "running", "generation_id": generation_id, "job_id": str(work_job_id)}
    if plan.generation_mode == "source_backed" and connection is not None:
        from tigr.entity_claims import assert_source_namespace_available
        assert_source_namespace_available(
            db, connection.id, principal.subject,
            allow_locked_entities=role in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER),
        )
    validation_repair = None
    assistant_model = "approved_schema_compiler"
    try:
        validated_mapping = validate_mapping(plan, connection)
        if connection is None or connection.connector_type != "postgresql":
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
            logical_plan = dict(plan.logical_plan or {})
            logical_plan["generated_query"] = review_query
            logical_plan["generated_query_fields"] = [item.target_field for item in validated_mapping]
            logical_plan["generated_query_field_types"] = {
                item.target_field: item.data_type for item in validated_mapping
            }
            plan.logical_plan = logical_plan
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    plan.state = "ready"
    plan.updated_at = datetime.now(UTC)
    record_scenario_audit(db, request, principal, role, plan, "scenario.query_generated", {"model": assistant_model, "query_mode": query_mode, "sql_repaired": validation_repair is not None})
    db.commit()
    db.refresh(plan)
    return {"plan": plan_output(db, plan), "query": review_query, "model": assistant_model, "validation_repair": validation_repair}


def _build_scenario_preview(db: Session, plan) -> dict:
    connection = plan_connection(db, plan)
    try:
        compiled = compile_query_plan(plan, connection)
    except ValueError as exc:
        raise exc
    sample_plan = SimpleNamespace(
        id=plan.id, requirement=plan.requirement, volume=min(plan.volume, 5), generation_mode=plan.generation_mode,
        logical_plan=plan.logical_plan, source_object=plan.source_object, mapping=plan.mapping,
    )
    sample_compiled = compile_query_plan(sample_plan, connection)
    sample_error = None
    sample_values_withheld = (
        plan.generation_mode == "source_backed"
        and workspace_pii_masking_enabled(db, plan.workspace_id)
    )
    try:
        sample_rows = generate_rows(sample_plan, connection, validate_mapping(sample_plan, connection), sample_compiled)[:5]
    except (RuntimeError, ValueError, SQLAlchemyError) as exc:
        # Keep the generated SQL available for review even when the source cannot
        # currently provide sample rows. Synthetic generation errors are safe,
        # user-facing validation messages; source errors stay generic.
        sample_rows = []
        if plan.generation_mode == "synthetic" and isinstance(exc, ValueError):
            sample_error = str(exc)[:500]
        elif plan.generation_mode == "synthetic":
            sample_error = "Sample generation could not complete with the reviewed fields and query. Review them, then retry."
        else:
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
        "fields": [item["target_field"] for item in plan.mapping],
        "sample_rows": sample_rows, "sample_count": sample_count,
        "sample_values_withheld": sample_values_withheld,
        "sample_error": sample_error,
    }


def run_scenario_preview_generation(plan_id: UUID, generation_id: str) -> None:
    started = perf_counter()
    db = SessionLocal()
    try:
        plan = db.get(ScenarioPlan, plan_id)
        if plan is None:
            return
        logical_plan = dict(plan.logical_plan or {})
        current = dict(logical_plan.get("sample_generation_status") or {})
        if current.get("generation_id") != generation_id:
            return
        work_job_id = current.get("job_id")
        _set_scenario_work_job(
            db, work_job_id, status="running", progress=20,
            summary="TIGR is generating preview rows",
            step_name="sample_generation", step_status="running", step_summary="Preview generation started",
        )
        db.commit()
        try:
            preview = _build_scenario_preview(db, plan)
            status_value = "failed" if preview.get("sample_error") else "complete"
            error = preview.get("sample_error")
        except Exception as exc:  # noqa: BLE001 - background errors are surfaced safely.
            preview = None
            status_value = "failed"
            error = str(exc)[:500] if isinstance(exc, (RuntimeError, ValueError)) else "Sample generation failed. Review the schema and generation requirements, then retry."
        logical_plan = dict(plan.logical_plan or {})
        current = dict(logical_plan.get("sample_generation_status") or {})
        if current.get("generation_id") != generation_id:
            return
        current.update({
            "status": status_value,
            "stage": "complete" if status_value == "complete" else "failed",
            "error": error,
            "elapsed_ms": int((perf_counter() - started) * 1000),
            "completed_at": datetime.now(UTC).isoformat(),
        })
        logical_plan["sample_generation_status"] = current
        if preview is not None:
            logical_plan["synthetic_preview"] = preview
        plan.logical_plan = logical_plan
        plan.updated_at = datetime.now(UTC)
        final_status = "failed" if status_value == "failed" else "succeeded"
        job_summary = error or "Synthetic preview generation completed"
        _set_scenario_work_job(
            db, work_job_id, status=final_status, progress=100, summary=job_summary,
            step_name="sample_generation", step_status="failed" if status_value == "failed" else "succeeded",
            step_summary=job_summary,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 - prevent preview work from being stranded as running.
        db.rollback()
        plan = db.get(ScenarioPlan, plan_id)
        if plan is not None:
            logical_plan = dict(plan.logical_plan or {})
            current = dict(logical_plan.get("sample_generation_status") or {})
            if current.get("generation_id") == generation_id:
                error = str(exc)[:500] if isinstance(exc, ValueError) else "Sample generation failed. Review the schema and generation requirements, then retry."
                current.update({"status": "failed", "stage": "failed", "error": error,
                                "elapsed_ms": int((perf_counter() - started) * 1000),
                                "completed_at": datetime.now(UTC).isoformat()})
                logical_plan["sample_generation_status"] = current
                plan.logical_plan = logical_plan
                plan.updated_at = datetime.now(UTC)
                _set_scenario_work_job(
                    db, current.get("job_id"), status="failed", progress=100, summary=error,
                    step_name="sample_generation", step_status="failed", step_summary=error,
                )
                db.commit()
    finally:
        db.close()


@router.post("/scenario-plans/{plan_id}/preview")
def preview_scenario_query(
    plan_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: ScenarioPreviewRequest | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    plan = get_plan(db, plan_id)
    workspace_role(db, principal, plan.workspace_id)
    if principal.actor_type != "human":
        raise HTTPException(status_code=404, detail="Scenario plan not found")
    require_source_coverage_review(plan)
    if payload is not None and plan.generation_mode == "synthetic":
        logical_plan = dict(plan.logical_plan or {})
        correction_feedback = (payload.correction_feedback or "").strip()
        if correction_feedback:
            logical_plan["generation_correction_feedback"] = correction_feedback
        else:
            logical_plan.pop("generation_correction_feedback", None)
        plan.logical_plan = logical_plan
    connection = plan_connection(db, plan)
    try:
        compile_query_plan(plan, connection)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if plan.generation_mode == "synthetic":
        generation_id = uuid4().hex
        work_job_id = _create_scenario_work_job(
            db, plan, principal, role, request.state.request_id, "scenario_sample_generation", generation_id,
            "sample_generation", "Synthetic sample generation queued",
        )
        logical_plan = dict(plan.logical_plan or {})
        logical_plan.pop("synthetic_preview", None)
        logical_plan["sample_generation_status"] = {
            "generation_id": generation_id,
            "status": "running",
            "stage": "generating_sample",
            "started_at": datetime.now(UTC).isoformat(),
            "job_id": str(work_job_id),
        }
        plan.logical_plan = logical_plan
        plan.updated_at = datetime.now(UTC)
        db.commit()
        background_tasks.add_task(run_scenario_preview_generation, plan.id, generation_id)
        return {"status": "running", "generation_id": generation_id, "job_id": str(work_job_id)}
    try:
        return _build_scenario_preview(db, plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
        selected_output_fields = {item.target_field for item in mapping}
        if plan.generation_mode == "source_backed" and connection is not None:
            from tigr.record_identity import add_hidden_anchor_fields
            mapping = add_hidden_anchor_fields(db, version, connection.id, mapping)
        compiled = compile_query_plan(plan, connection, mapping)
        generation_started = perf_counter()
        generation_sql = compiled.get("query") or (plan.logical_plan or {}).get("generated_query")
        if isinstance(generation_sql, str) and generation_sql.strip():
            version.provenance = {**(version.provenance or {}), "source_sql": generation_sql}
        job.status = "running"
        job.progress = 20
        job.safe_summary = "Generating data from the approved scenario plan"
        db.add(JobStep(id=uuid4(), job_id=job.id, name="generation", status="running", safe_summary="Generation started"))
        db.commit()
        if plan.generation_mode == "source_backed" and connection is not None:
            from tigr.entity_claims import assert_source_namespace_available
            assert_source_namespace_available(
                db, connection.id, job.actor_id,
                allow_locked_entities=job.actor_role in (HumanRole.ADMIN.value, HumanRole.TEST_DATA_ENGINEER.value),
            )
        output_mapping = synthetic_query_output_mapping(plan, mapping) if plan.generation_mode == "synthetic" else mapping
        identity_fields = _identity_field_names(output_mapping) if plan.generation_mode == "synthetic" else []
        prior_identity_values: dict[str, dict[str, object]] = {field: {} for field in identity_fields}
        if identity_fields and version.version_number > 1:
            prior_versions = db.scalars(select(DataSetVersion).where(
                DataSetVersion.dataset_id == dataset.id,
                DataSetVersion.version_number < version.version_number,
            ).order_by(DataSetVersion.version_number)).all()
            for prior_version in prior_versions:
                for prior_row in read_dataset_records(db, prior_version):
                    for field_name in identity_fields:
                        value = prior_row.get(field_name)
                        if value is not None:
                            prior_identity_values[field_name][_identity_value_key(value)] = value
        avoid_identity_values: dict[str, list[object]] = {}
        rows = generate_rows(plan, connection, mapping, compiled)
        for attempt in range(5):
            conflicts: dict[str, set[str]] = {}
            for field_name in identity_fields:
                prior_keys = prior_identity_values[field_name]
                used = {_identity_value_key(row.get(field_name)) for row in rows if row.get(field_name) is not None}
                overlap = used.intersection(prior_keys)
                if overlap:
                    conflicts[field_name] = overlap
            if not conflicts:
                break
            if attempt == 4:
                raise RuntimeError("Fresh version generation reused identity values from an earlier version. Retry generation to create distinct identifiers and names.")
            for field_name, overlap in conflicts.items():
                excluded = avoid_identity_values.setdefault(field_name, [])
                excluded_keys = {_identity_value_key(value) for value in excluded}
                for key in overlap:
                    value = prior_identity_values[field_name][key]
                    if key not in excluded_keys:
                        excluded.append(value)
                        excluded_keys.add(key)
            rows = generate_rows(plan, connection, mapping, compiled, avoid_identity_values)
        from tigr.entity_claims import assert_job_current
        assert_job_current(db, job.id)
        relationships = (plan.logical_plan or {}).get("relationships", [])
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
        pii_masking_enabled = workspace_pii_masking_enabled(db, dataset.workspace_id)
        persist_without_masking = valid and not is_synthetic and not pii_masking_enabled
        lineage_refs = []
        if persist_without_masking and connection is not None:
            from tigr.record_identity import capture_entity_lineage
            lineage_refs = capture_entity_lineage(db, dataset, version, rows, mapping, connection.id)
        for row in rows:
            for field_name in set(row) - selected_output_fields:
                row.pop(field_name, None)
        reference, digest = artifact_reference(dataset.id, rows) if is_synthetic or persist_without_masking else (None, None)
        artifact_size = store_dataset_records(
            db, dataset, version, rows,
            masking_complete=persist_without_masking,
            lineage_refs=lineage_refs,
        )[1] if is_synthetic or persist_without_masking else 0
        duration_ms = int((perf_counter() - generation_started) * 1000)
        output_format = str((version.generation_settings or {}).get("output_format", "json"))
        reference = f"artifact://{dataset.id}/v{version.version_number}/dataset.{output_format}"
        version.validation_state = "passed" if valid else "failed"
        version.record_count = len(rows)
        version.privacy_state = "compliant" if valid and (is_synthetic or persist_without_masking) else "requires_masking"
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
        if is_synthetic or persist_without_masking:
            version.validation_evidence.update({"artifact_ref": reference, "hash": digest})
        if not valid:
            version.lifecycle_state = "quarantined"
            version.privacy_state = "blocked"
            plan.state = "quarantined"
        elif is_synthetic or persist_without_masking:
            version.lifecycle_state = "ready"
            plan.state = "succeeded"
        else:
            version.lifecycle_state = "draft"
            plan.state = "succeeded"
        service_actor = Principal(subject="tigr-scenario-worker", actor_type="service")
        record_status_change(db, dataset, version, "draft", version.lifecycle_state, service_actor, job.request_id,
                             "Scenario generation completed" if valid else "Scenario output failed validation")
        record_validation(db, dataset, version, job.request_id, "scenario_schema")
        if valid and (is_synthetic or persist_without_masking):
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
    except Exception as exc:  # noqa: BLE001 - background failures must be reduced to safe job state.
        db.rollback()
        plan = db.get(ScenarioPlan, plan_id)
        job = db.get(Job, job_id)
        dataset = db.get(DataSet, dataset_id)
        version = db.get(DataSetVersion, version_id)
        if job and job.status not in ("queued", "running"):
            return
        if plan:
            plan.state = "failed"
            plan.updated_at = datetime.now(UTC)
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
            job.progress = 100
            job.heartbeat_at = datetime.now(UTC)
            if plan and plan.generation_mode == "synthetic" and isinstance(exc, (ValueError, RuntimeError)):
                job.safe_summary = f"Synthetic generation failed: {str(exc)[:400]}"
            else:
                job.safe_summary = "Generation failed before data was stored. Review the scenario settings and retry."
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
    linked_job = db.get(Job, plan.job_id) if plan.job_id else None
    failed_generation = plan.state == "failed" or bool(linked_job and linked_job.status == "failed")
    retrying_failed_empty_draft = bool(
        failed_generation and plan.dataset_id and payload.draft_dataset_id == plan.dataset_id
    )
    if clarifying_questions(plan):
        raise HTTPException(status_code=409, detail="Resolve the outstanding requirement clarifications before generating")
    reviewed_source_query = bool((plan.logical_plan or {}).get("generated_query"))
    stale_source_review_state = (
        plan.generation_mode == "source_backed"
        and reviewed_source_query
        and plan.state in ("draft", "mapped")
    )
    if plan.state != "ready" and not retrying_failed_empty_draft and not stale_source_review_state:
        raise HTTPException(status_code=409, detail="Complete schema mapping and generate or validate the SQL query in Step 3 before generating")
    plan.name = payload.dataset_name
    plan.target_use = payload.target_use
    plan.volume = payload.maximum_records
    connection = plan_connection(db, plan)
    if plan.generation_mode == "source_backed":
        if connection is None:
            raise HTTPException(status_code=422, detail="Choose an enabled source connection")
    try:
        mapping = validate_mapping(plan, connection)
        compiled = compile_query_plan(plan, connection)
        if plan.generation_mode == "source_backed" and not (plan.logical_plan or {}).get("generated_query"):
            reviewed_query = postgres_review_query(compiled)
            logical_plan = dict(plan.logical_plan or {})
            logical_plan["generated_query"] = reviewed_query
            logical_plan["generated_query_fields"] = [item.target_field for item in mapping]
            logical_plan["generated_query_field_types"] = {item.target_field: item.data_type for item in mapping}
            plan.logical_plan = logical_plan
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if stale_source_review_state:
        # Saving Step 3 progress updates draft_data through the shared plan
        # endpoint, which can leave a reviewed source query in the mapped state.
        # The persisted query proves Step 3 review completed; restore readiness.
        plan.state = "ready"
    assistant = get_scenario_assistant()
    job_id = uuid4()
    provenance = Provenance(
        origin="synthetic" if plan.generation_mode == "synthetic" else "source_backed",
        source_connection_id=connection.id if connection else None,
        source_object=plan.source_object if connection else None,
        source_schema_version="connection-structure-preview" if connection else None,
        mapping_version="scenario-mapping-v1", scenario_id=str(plan.id), scenario_version="1", job_id=job_id,
        test_data_request_id=payload.test_data_request_id,
    )
    output_mapping = synthetic_query_output_mapping(plan, mapping) if plan.generation_mode == "synthetic" else mapping
    schema_scope = [SchemaField(field=item.target_field, data_type=item.data_type, required=item.required, json_path=item.json_path) for item in output_mapping]
    draft = DraftDataSet(name=payload.dataset_name, description=plan.requirement[:2000], creation_pathway="scenario_to_data", schema_scope=schema_scope,
                         selected_fields=[item.target_field for item in output_mapping], provenance=provenance, permitted_use=[payload.target_use],
                         maximum_records=plan.volume, output_format=payload.output_format, expiry_at=payload.expiry_at)
    if payload.edit_dataset_id is not None:
        dataset = db.scalar(select(DataSet).where(
            DataSet.id == payload.edit_dataset_id,
            DataSet.workspace_id == plan.workspace_id,
            DataSet.creation_pathway == "scenario_to_data",
        ).with_for_update())
        base_version = db.scalar(select(DataSetVersion).where(
            DataSetVersion.dataset_id == payload.edit_dataset_id,
            DataSetVersion.version_number == payload.base_version_number,
        ))
        if dataset is None or base_version is None:
            raise HTTPException(status_code=404, detail="The data set version selected for editing is unavailable")
        if base_version.lifecycle_state != "published" or base_version.publication_state != "published":
            raise HTTPException(status_code=409, detail="Only a published version can be edited into a new scenario version")
        dataset.name = payload.dataset_name
        dataset.description = plan.requirement[:2000]
        version = create_version(db, dataset, draft, principal, request.state.request_id)
    else:
        dataset, version = _create_or_reuse_scenario_dataset(
            db, principal, plan.workspace_id, payload.draft_dataset_id, draft, request.state.request_id,
        )
    link_generation_to_ticket(db, payload.test_data_request_id, plan.workspace_id, dataset, version, principal, role)
    dataset_id = dataset.id
    job = Job(
        id=job_id, job_type="scenario_generation", status="queued", workspace_id=str(plan.workspace_id),
        scope_key=f"workspace:{plan.workspace_id}", idempotency_key=f"scenario:{plan.id}:{job_id}",
        actor_type=principal.actor_type, actor_id=principal.subject, actor_role=role.value,
        request_id=request.state.request_id, progress=0, safe_summary="Scenario generation queued",
    )
    db.add(job)
    db.flush()
    version_id = version.id
    plan.job_id = job_id
    plan.dataset_id = dataset_id
    plan.logical_plan = {
        **(plan.logical_plan or {}),
        "generated_version_id": str(version.id),
        "generated_version_number": version.version_number,
    }
    plan.state = "running"
    record_scenario_audit(db, request, principal, role, plan, "scenario.generation_requested", {"mode": plan.generation_mode, "volume": plan.volume})
    db.add(OutboxEvent(id=uuid4(), aggregate_type="scenario_plan", aggregate_id=str(plan.id), workspace_id=str(plan.workspace_id), event_type="scenario.generation_requested", payload={"plan_id": str(plan.id), "job_id": str(job_id), "dataset_id": str(dataset_id), "version_id": str(version_id), "assistant_provider": assistant.provider_name, "model_version": assistant.model_version, "prompt_version": assistant.prompt_version}, request_id=request.state.request_id))
    db.commit()
    if get_settings().inline_jobs:
        background_tasks.add_task(run_scenario_job, plan.id, job_id, dataset_id, version_id)
    return {"plan_id": str(plan.id), "job_id": str(job_id), "dataset_id": str(dataset_id), "dataset_reference_code": dataset.reference_code, "version_number": version.version_number, "status": "queued"}


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
    generated_version_id = (plan.logical_plan or {}).get("generated_version_id")
    try:
        parsed_version_id = UUID(str(generated_version_id)) if generated_version_id else None
    except (TypeError, ValueError):
        parsed_version_id = None
    version = db.get(DataSetVersion, parsed_version_id) if parsed_version_id else None
    if version is None and plan.dataset_id:
        # Older plans predate explicit generated-version tracking.
        version = db.scalar(select(DataSetVersion).where(
            DataSetVersion.dataset_id == plan.dataset_id,
            DataSetVersion.version_number == 1,
        ))
    if version is not None and version.dataset_id != plan.dataset_id:
        version = None
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
                    headers={"Content-Disposition": f'attachment; filename="{dataset.reference_code}.{output_format}"'})
