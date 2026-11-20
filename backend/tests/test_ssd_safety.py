from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text
from test_ssd import (
    context as context,  # noqa: PLC0414 - shared pytest fixture
)
from test_ssd import (
    create,
    dataset,
    enqueue,
    run,
    validate,
)
from test_ssd import (
    pytestmark as pytestmark,  # noqa: PLC0414 - disposable-only module mark
)
from tigr import ssd
from tigr.datasets import DataSet, DataSetVersion
from tigr.db import engine
from tigr.jobs import Job
from tigr.ssd_models import SsdDraft, SsdExecution, SsdRevision


def test_expired_sample_is_cleaned_and_regenerated_by_safe_read(context):
    c = context
    draft = create(c)
    validate(c, draft)
    run(c, enqueue(c, draft))
    run(c, enqueue(c, draft, "sample"))
    revision = c["db"].scalar(select(SsdRevision).where(SsdRevision.draft_id == UUID(draft["id"])))
    for execution in (
        c["db"].scalars(select(SsdExecution).where(SsdExecution.revision_id == revision.id)).all()
    ):
        execution.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    c["db"].commit()
    ssd.cleanup(c["db"])
    c["db"].commit()
    c["db"].expire_all()
    assert c["client"].get(f"/api/v1/ssd/drafts/{draft['id']}/sample").status_code == 409
    run(c, enqueue(c, draft, "sample"))
    assert len(c["client"].get(f"/api/v1/ssd/drafts/{draft['id']}/sample").json()["rows"]) == 5


def test_running_mutation_rechecks_cancellation_before_commit(context, monkeypatch):
    c = context
    draft = create(c, f"UPDATE public.{c['table']} SET name=:name WHERE id=1", {"name": "changed"})
    validate(c, draft)
    q = enqueue(c, draft, confirm_mutation=True)
    source_runner = ssd.run_source

    def cancelled_source(connection, revision, info, before_commit):
        def cancel_then_check():
            job = c["db"].get(Job, UUID(q["job_id"]))
            job.status = "cancel_requested"
            c["db"].commit()
            before_commit()

        return source_runner(connection, revision, info, cancel_then_check)

    monkeypatch.setattr(ssd, "run_source", cancelled_source)
    ssd.run_job(UUID(q["execution_id"]))
    assert c["db"].get(SsdExecution, UUID(q["execution_id"])).outcome == "rolled_back"
    assert c["db"].get(Job, UUID(q["job_id"])).status == "cancelled"
    with engine.connect() as connection:
        assert (
            connection.execute(text(f"SELECT name FROM public.{c['table']} WHERE id=1")).scalar()
            == "Sensitive person"
        )


def test_dataset_gates_cannot_be_overridden_by_qa_payload(context):
    c = context
    payload = {
        "name": "Tampered",
        "connection_id": str(c["source"].id),
        "database": engine.url.database,
        "sql": f"SELECT * FROM public.{c['table']}",
        "pii_masking_required": False,
    }
    response = c["client"].post(f"/api/v1/workspaces/{c['workspace'].id}/ssd/drafts", json=payload)
    assert response.status_code == 422
    _, ds = dataset(c)
    response = c["client"].post(
        f"/api/v1/datasets/{ds.id}/versions/1/validation",
        json={"validation_state": "passed", "privacy_state": "compliant"},
    )
    assert response.status_code == 403


def test_worker_recovery_does_not_replay_dispatched_mutation(context, monkeypatch):
    c = context
    draft = create(c, f"DELETE FROM public.{c['table']} WHERE id=1")
    validate(c, draft)
    q = enqueue(c, draft, confirm_mutation=True)
    execution = c["db"].get(SsdExecution, UUID(q["execution_id"]))
    execution.outcome = "dispatched"
    c["db"].get(Job, UUID(q["job_id"])).status = "running"
    c["db"].commit()

    def forbidden(*args):
        raise AssertionError("A dispatched mutation must never be retried")

    monkeypatch.setattr(ssd, "run_source", forbidden)
    ssd.run_job(execution.id)
    assert execution.outcome == "outcome_unknown"


def test_mutation_then_select_publish_and_edit_as_new_dataset_version(context):
    c = context
    draft = create(c, f"INSERT INTO public.{c['table']}(id,name) VALUES(100,'new')")
    validate(c, draft)
    run(c, enqueue(c, draft, confirm_mutation=True))
    from tigr.outbox_worker import _execute_event

    dataset_id = None
    for expected_revision, where, count in [(1, "", 9), (2, "WHERE id<3", 2)]:
        response = c["client"].patch(
            f"/api/v1/ssd/drafts/{draft['id']}",
            json={
                "name": "Versioned SSD",
                "connection_id": str(c["source"].id),
                "database": engine.url.database,
                "sql": f"SELECT * FROM public.{c['table']} {where} ORDER BY id",
                "expected_revision": expected_revision,
                "visibility": "public",
            },
        )
        assert response.status_code == 200, response.text
        draft = response.json()
        assert draft["dataset_id"] is None
        validate(c, draft)
        query = enqueue(c, draft)
        _execute_event(
            {
                "event_type": "ssd.work_requested",
                "payload": {"execution_id": query["execution_id"], "job_id": query["job_id"]},
            }
        )
        assert c["db"].get(Job, UUID(query["job_id"])).status == "succeeded"
        run(c, enqueue(c, draft, "sample"))
        run(c, enqueue(c, draft, "datasets"))
        stored = c["db"].get(SsdDraft, UUID(draft["id"]))
        if dataset_id is None:
            dataset_id = stored.dataset_id
        assert stored.dataset_id == dataset_id
        version_number = expected_revision
        assert stored.dataset_version_number == version_number
        version = c["db"].scalar(
            select(DataSetVersion).where(
                DataSetVersion.dataset_id == dataset_id,
                DataSetVersion.version_number == version_number,
            )
        )
        assert version.record_count == count
        assert (
            c["client"]
            .post(f"/api/v1/datasets/{dataset_id}/versions/{version_number}/publish")
            .status_code
            == 200
        )
    assert c["db"].get(DataSet, dataset_id).latest_version_number == 2
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(f"SELECT count(*) FROM public.{c['table']} WHERE id=100")
            ).scalar()
            == 1
        )


def test_incomplete_sql_draft_can_be_saved_but_not_executed(context):
    c = context
    draft = create(c, "SELECT unfinished")
    response = c["client"].post(
        f"/api/v1/ssd/drafts/{draft['id']}/validate",
        json={"expected_revision": 1, "idempotency_key": "validation"},
    )
    assert response.status_code == 422

    response = c["client"].post(
        f"/api/v1/ssd/drafts/{draft['id']}/execute",
        json={"expected_revision": 1, "idempotency_key": "execution"},
    )
    assert response.status_code == 422


def test_requested_count_and_csv_artifact_match_stored_output(context):
    import hashlib

    from tigr.schema_records import ArtifactRecord

    c = context
    draft = create(c)
    validate(c, draft)
    run(c, enqueue(c, draft))
    run(c, enqueue(c, draft, "sample"))
    saved = c["client"].patch(
        f"/api/v1/ssd/drafts/{draft['id']}/metadata",
        json={
            "expected_revision": 1,
            "name": "Three CSV records",
            "description": "Requested output",
            "visibility": "private",
            "maximum_records": 3,
            "output_format": "csv",
        },
    )
    assert saved.status_code == 200, saved.text
    run(c, enqueue(c, draft, "datasets"))
    stored = c["db"].get(SsdDraft, UUID(draft["id"]))
    assert (
        c["client"].post(f"/api/v1/datasets/{stored.dataset_id}/versions/1/publish").status_code
        == 200
    )
    version = c["db"].scalar(
        select(DataSetVersion).where(DataSetVersion.dataset_id == stored.dataset_id)
    )
    assert version.record_count == 3
    downloaded = c["client"].get(f"/api/v1/datasets/{stored.dataset_id}/versions/1/artifact")
    assert downloaded.status_code == 200, downloaded.text
    assert "text/csv" in downloaded.headers["content-type"]
    assert len(downloaded.text.splitlines()) == 4
    artifact = c["db"].scalar(select(ArtifactRecord).where(ArtifactRecord.version_id == version.id))
    assert artifact.checksum == hashlib.sha256(downloaded.content).hexdigest()
