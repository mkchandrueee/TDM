"""Independent PostgreSQL outbox worker for generation and masking jobs."""

import argparse
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from tigr.access import record_audit, workspace_role
from tigr.config import get_settings
from tigr.connection_models import WorkspaceConnection
from tigr.dataset_schemas import LineageEventCreate
from tigr.dataset_services import append_lineage_event, record_status_change, record_validation
from tigr.datasets import DataSet, DataSetVersion
from tigr.db import SessionLocal
from tigr.governance import MaskingPolicy, MaskingRun
from tigr.identity import HumanRole, Principal
from tigr.jobs import Job, JobStep, OutboxEvent
from tigr.scenarios import ScenarioPlan
from tigr.schema_records import ArtifactRecord, DataSetRecord

logger = logging.getLogger("tigr.worker")
REQUEST_EVENTS = (
    "ssd.work_requested",
    "scenario.generation_requested",
    "source_data.generation_requested",
    "source_query.generation_requested",
    "federated.generation_requested",
    "privacy.masking_requested",
)
STALE_AFTER = timedelta(seconds=60)
MAX_ATTEMPTS = 5


def _as_uuid(value: UUID | str | None) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    return UUID(str(value))


def claim_next() -> dict | None:
    now = datetime.now(UTC)
    with SessionLocal.begin() as db:
        event = db.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.published_at.is_(None),
                OutboxEvent.event_type.in_(REQUEST_EVENTS),
                or_(OutboxEvent.claimed_at.is_(None), OutboxEvent.claimed_at < now - STALE_AFTER),
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if event is None:
            return None
        token = uuid4().hex
        event.claimed_at = now
        event.claim_token = token
        event.attempt_count += 1
        return {"id": event.id, "event_type": event.event_type, "payload": dict(event.payload),
                "claim_token": token, "attempt_count": event.attempt_count, "request_id": event.request_id}


class _Heartbeat:
    def __init__(self, event_id: UUID, claim_token: str, job_id: UUID | None):
        self.event_id = event_id
        self.claim_token = claim_token
        self.job_id = job_id
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        while not self.stopped.wait(15):
            try:
                now = datetime.now(UTC)
                with SessionLocal.begin() as db:
                    db.execute(update(OutboxEvent).where(
                        OutboxEvent.id == self.event_id,
                        OutboxEvent.claim_token == self.claim_token,
                        OutboxEvent.published_at.is_(None),
                    ).values(claimed_at=now))
                    if self.job_id:
                        db.execute(update(Job).where(
                            Job.id == self.job_id,
                            Job.status == "running",
                        ).values(heartbeat_at=now))
            except Exception:  # noqa: BLE001 - keep worker heartbeats independent and payload-free.
                logger.warning("worker heartbeat failed for event %s", self.event_id)


def _job_for_event(db: Session, event: dict) -> Job | None:
    payload = event["payload"]
    if event["event_type"] == "privacy.masking_requested":
        run = db.get(MaskingRun, UUID(payload["run_id"]))
        return db.get(Job, run.job_id) if run else None
    job_id = payload.get("job_id")
    return db.get(Job, UUID(job_id)) if job_id else None


def _job_scope(db: Session, job: Job) -> tuple[UUID, HumanRole]:
    if job.actor_type != "human" or not job.workspace_id or not job.actor_role:
        raise ValueError("worker_scope_invalid")
    workspace_id = UUID(job.workspace_id)
    if get_settings().env == "development":
        role = HumanRole(job.actor_role)
    else:
        role = workspace_role(db, Principal(subject=job.actor_id), workspace_id)
        if role != HumanRole(job.actor_role):
            raise ValueError("worker_role_changed")
    if role not in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER):
        raise ValueError("worker_role_not_allowed")
    return workspace_id, role


def _reauthorize_event(db: Session, event: dict, job: Job) -> None:
    workspace_id, _ = _job_scope(db, job)
    payload = event["payload"]
    if event["event_type"] == "scenario.generation_requested":
        plan = db.get(ScenarioPlan, UUID(payload["plan_id"]))
        if plan is None or plan.workspace_id != workspace_id or plan.job_id != job.id:
            raise ValueError("worker_plan_scope_invalid")
        if plan.generation_mode == "source_backed":
            connection = db.get(WorkspaceConnection, plan.source_connection_id)
            if (connection is None or connection.workspace_id != workspace_id or connection.status != "enabled"
                    or connection.direction not in ("source", "both")
                    or connection.operation_scope not in ("read_only", "read_write")):
                raise ValueError("worker_source_disabled")
    elif event["event_type"] == "source_data.generation_requested":
        from tigr.source_mappings import SourceDataMapping

        mapping = db.get(SourceDataMapping, UUID(payload["mapping_id"]))
        if mapping is None or mapping.workspace_id != workspace_id:
            raise ValueError("worker_mapping_scope_invalid")
        source = db.get(WorkspaceConnection, mapping.connection_id)
        if source is None or source.workspace_id != workspace_id or source.status != "enabled" or source.operation_scope not in ("read_only", "read_write"):
            raise ValueError("worker_source_disabled")
        target_id = payload.get("target_connection_id")
        if target_id:
            target = db.get(WorkspaceConnection, UUID(target_id))
            if target is None or target.workspace_id != workspace_id or target.status != "enabled" or target.direction not in ("target", "both") or target.operation_scope not in ("write_only", "read_write"):
                raise ValueError("worker_target_disabled")
    elif event["event_type"] == "source_query.generation_requested":
        from tigr.source_queries import SourceDataQuery

        query = db.get(SourceDataQuery, UUID(payload["query_id"]))
        source = db.get(WorkspaceConnection, query.connection_id) if query else None
        if (query is None or query.workspace_id != workspace_id or source is None or source.workspace_id != workspace_id
                or source.connector_type != "postgresql" or source.status != "enabled" or source.direction not in ("source", "both")
                or source.operation_scope not in ("read_only", "read_write")):
            raise ValueError("worker_source_query_scope_invalid")
        target_id = payload.get("target_connection_id")
        if target_id:
            target = db.get(WorkspaceConnection, UUID(target_id))
            if target is None or target.workspace_id != workspace_id or target.status != "enabled" or target.direction not in ("target", "both") or target.operation_scope not in ("write_only", "read_write"):
                raise ValueError("worker_target_disabled")
    elif event["event_type"] == "federated.generation_requested":
        from tigr.source_mappings import SourceDataPlan
        from tigr.source_to_data import FederatedPlanValidation, _validate_federated_plan

        saved = db.get(SourceDataPlan, UUID(payload["source_plan_id"]))
        if saved is None or saved.workspace_id != workspace_id:
            raise ValueError("worker_federated_plan_scope_invalid")
        raw = (saved.plan_data or {}).get("federatedPlan") or {}
        fed = FederatedPlanValidation.model_validate({key: raw[key] for key in ("sources", "joins", "row_limit") if key in raw})
        resolved = _validate_federated_plan(db, workspace_id, fed)
        if not resolved.get("valid") or raw.get("execution_supported") is not True:
            raise ValueError("worker_federated_plan_not_executable")
    else:
        run = db.get(MaskingRun, UUID(payload["run_id"]))
        policy = db.get(MaskingPolicy, run.policy_id) if run else None
        version = db.get(DataSetVersion, run.version_id) if run else None
        dataset = db.get(DataSet, run.dataset_id) if run else None
        if (run is None or run.workspace_id != workspace_id or run.job_id != job.id or policy is None
                or policy.workspace_id != workspace_id or policy.status != "approved" or version is None
                or dataset is None or dataset.workspace_id != workspace_id):
            raise ValueError("worker_masking_scope_invalid")


def _fail_job(db: Session, event: dict, job: Job | None, reason_code: str, failure_type: str | None = None) -> None:
    # A runner can finish (including a detailed, sanitized failure) before the
    # worker acknowledges the outbox event. Never replace that terminal result
    # with a generic worker-recovery failure if acknowledgement later fails.
    if job is None or job.status in {"succeeded", "succeeded_with_warnings", "failed", "cancelled", "expired"}:
        return
    job.status = "failed"
    job.progress = 100
    job.safe_summary = "Job stopped before completion; review its status and data-set release state"
    safe_reason = f"{reason_code}: {failure_type}" if failure_type else reason_code
    db.add(JobStep(id=uuid4(), job_id=job.id, name="worker_recovery", status="failed",
                   safe_summary=f"Worker could not complete dispatch ({safe_reason})"))
    payload = event["payload"]
    dataset_id = payload.get("dataset_id")
    version_id = payload.get("version_id")
    masking_run = None
    if event["event_type"] == "privacy.masking_requested":
        masking_run = db.get(MaskingRun, _as_uuid(payload["run_id"]))
        if masking_run:
            dataset_id, version_id = masking_run.dataset_id, masking_run.version_id
            masking_run.status = "failed"
            masking_run.completed_at = datetime.now(UTC)
            masking_run.evidence = {"reason_code": reason_code, "summary": "Masking job stopped and output was quarantined"}
    dataset = db.get(DataSet, _as_uuid(dataset_id)) if dataset_id else None
    version = db.get(DataSetVersion, _as_uuid(version_id)) if version_id else None
    if event["event_type"] == "scenario.generation_requested" and payload.get("plan_id"):
        plan = db.get(ScenarioPlan, _as_uuid(payload["plan_id"]))
        if plan:
            plan.state = "failed"
    if version:
        previous_status = version.lifecycle_state
        if event["event_type"] == "privacy.masking_requested":
            db.execute(DataSetRecord.__table__.delete().where(DataSetRecord.version_id == version.id))
        artifact = db.scalar(select(ArtifactRecord).where(ArtifactRecord.version_id == version.id))
        has_records = db.scalar(select(DataSetRecord.id).where(DataSetRecord.version_id == version.id).limit(1)) is not None
        recoverable_scenario_draft = (
            event["event_type"] == "scenario.generation_requested"
            and not has_records
            and artifact is None
        )
        version.lifecycle_state = (
            "quarantined" if event["event_type"] == "privacy.masking_requested"
            else "draft" if recoverable_scenario_draft
            else "failed"
        )
        version.validation_state = "not_run" if recoverable_scenario_draft else "failed"
        if recoverable_scenario_draft:
            version.privacy_state = "not_assessed"
            version.record_count = None
            version.validation_evidence = {}
            job.safe_summary = "Generation could not complete. The empty data set remains a draft and can be retried."
            if failure_type:
                job.safe_summary += f" Worker error type: {failure_type}."
        else:
            version.privacy_state = "blocked" if event["event_type"] == "privacy.masking_requested" else version.privacy_state
        if dataset:
            worker = Principal(subject="tigr-outbox-worker", actor_type="service")
            record_status_change(db, dataset, version, previous_status, version.lifecycle_state, worker,
                                 job.request_id, "Generation stopped; empty draft retained for retry" if recoverable_scenario_draft else "Worker stopped before job completion")
            if not recoverable_scenario_draft:
                record_validation(db, dataset, version, job.request_id, "worker_recovery")
        artifact = db.scalar(select(ArtifactRecord).where(ArtifactRecord.version_id == version.id))
        if artifact and not recoverable_scenario_draft:
            artifact.status = "quarantined"
            artifact.deleted_at = datetime.now(UTC)
    if dataset and version:
        try:
            worker = Principal(subject="tigr-outbox-worker", actor_type="service")
            append_lineage_event(db, dataset, version, LineageEventCreate(
                event_type="worker_job_failed", stage="worker", job_id=job.id, outcome="failed",
                safe_evidence={"summary": "Job stopped before completion", "reason_code": reason_code}), worker)
            record_audit(db, worker, job.request_id, "job.worker_failed", "dataset_version", str(version.id),
                         dataset.workspace_id, None, {"job_id": str(job.id), "reason_code": reason_code})
        except Exception:  # noqa: BLE001 - preserve the terminal failed state if evidence append fails.
            logger.warning("failed to append safe failure evidence for job %s", job.id)


def _execute_event(event: dict) -> None:
    if event["event_type"] == "ssd.work_requested":
        from tigr.ssd import run_job
        run_job(UUID(event["payload"]["execution_id"]))
        return
    with SessionLocal() as db:
        job = _job_for_event(db, event)
        if job is None:
            _fail_job(db, event, None, "job_not_found")
            db.commit()
            return
        if job.status in {"succeeded", "succeeded_with_warnings", "failed", "cancelled", "expired"}:
            return
        if job.status == "running":
            _fail_job(db, event, job, "worker_interrupted")
            db.commit()
            return
        if event["attempt_count"] > MAX_ATTEMPTS:
            _fail_job(db, event, job, "worker_attempt_limit")
            db.commit()
            return
        try:
            _reauthorize_event(db, event, job)
        except Exception:  # noqa: BLE001 - do not return provider or membership details.
            _fail_job(db, event, job, "worker_authorization_failed")
            db.commit()
            return

    payload = event["payload"]
    if event["event_type"] == "scenario.generation_requested":
        from tigr.scenario_api import run_scenario_job

        run_scenario_job(UUID(payload["plan_id"]), UUID(payload["job_id"]), UUID(payload["dataset_id"]), UUID(payload["version_id"]), bool(payload.get("require_exact_count", False)))
    elif event["event_type"] == "source_data.generation_requested":
        from tigr.source_to_data import run_source_data_job

        run_source_data_job(UUID(payload["mapping_id"]), UUID(payload["job_id"]), UUID(payload["dataset_id"]),
            UUID(payload["version_id"]), list(payload["selected_fields"]), int(payload["volume"]),
            str(payload["output_format"]), list(payload["predicates"]),
            UUID(payload["target_connection_id"]) if payload.get("target_connection_id") else None)
    elif event["event_type"] == "source_query.generation_requested":
        from tigr.source_to_data import run_source_query_job
        output_fields = list(payload.get("output_fields") or [])
        if not output_fields:
            from tigr.source_queries import SourceDataQuery

            with SessionLocal() as db:
                query = db.get(SourceDataQuery, UUID(payload["query_id"]))
                output_fields = ([{"source_field": item["name"], "output_field": item["name"]}
                                  for item in query.output_fields] if query else [])

        run_source_query_job(UUID(payload["query_id"]), UUID(payload["job_id"]), UUID(payload["dataset_id"]),
            UUID(payload["version_id"]), int(payload["volume"]), str(payload["output_format"]), output_fields,
            UUID(payload["target_connection_id"]) if payload.get("target_connection_id") else None)
    elif event["event_type"] == "federated.generation_requested":
        from tigr.source_to_data import run_federated_generation_job

        run_federated_generation_job(UUID(payload["source_plan_id"]), UUID(payload["job_id"]),
            UUID(payload["dataset_id"]), UUID(payload["version_id"]), int(payload["volume"]),
            str(payload["output_format"]))
    else:
        from tigr.governance import MaskingRun as MaskingRunModel
        from tigr.privacy_api import run_masking_job

        with SessionLocal() as db:
            run = db.get(MaskingRunModel, UUID(payload["run_id"]))
            if run is None:
                return
            run_args = (run.id, run.policy_id, run.dataset_id, run.version_id, run.job_id)
        run_masking_job(*run_args)


def _ack_event(event: dict) -> None:
    with SessionLocal.begin() as db:
        row = db.scalar(select(OutboxEvent).where(OutboxEvent.id == event["id"]).with_for_update())
        if row and row.claim_token == event["claim_token"] and row.published_at is None:
            row.published_at = datetime.now(UTC)
            row.claim_token = None
            row.claimed_at = None


def work_once() -> bool:
    from tigr.ssd import cleanup
    with SessionLocal.begin() as cleanup_db:
        cleanup(cleanup_db)
    event = claim_next()
    if event is None:
        return False
    try:
        job_id = UUID(str(event["payload"].get("job_id")))
    except (TypeError, ValueError):
        with SessionLocal() as db:
            job = _job_for_event(db, event)
            job_id = job.id if job else None
    heartbeat = _Heartbeat(event["id"], event["claim_token"], job_id)
    heartbeat.start()
    try:
        _execute_event(event)
    except Exception as exc:  # noqa: BLE001 - recover through the durable claim and sanitized job state.
        logger.exception("outbox event dispatch failed for %s", event["id"])
        with SessionLocal() as db:
            job = _job_for_event(db, event)
            _fail_job(db, event, job, "worker_dispatch_failed", type(exc).__name__)
            db.commit()
    try:
        _ack_event(event)
    except Exception:  # noqa: BLE001 - an ack failure must not rewrite a completed job.
        # The claim lease will expire and another worker can observe the
        # already-terminal job, skip execution, and acknowledge the event.
        logger.exception("outbox event acknowledgement failed for %s", event["id"])
    finally:
        heartbeat.stop()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(prog="tigr-worker")
    parser.add_argument("--once", action="store_true", help="Process at most one pending event, then exit")
    parser.add_argument("--poll-seconds", type=float, default=1.0, help="Idle wait between outbox polls")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    while True:
        if not work_once():
            if args.once:
                return
            time.sleep(max(0.1, args.poll_seconds))
        elif args.once:
            return


if __name__ == "__main__":
    main()
