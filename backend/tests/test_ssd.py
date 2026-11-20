"""SSD integration tests use only the disposable database, including source DML."""

import os
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from tigr import ssd
from tigr.connection_models import WorkspaceConnection
from tigr.datasets import DataSet
from tigr.db import engine
from tigr.governance import MaskingPolicy
from tigr.iam import AccessGrant, UserIdentity, Workspace, WorkspaceMembership
from tigr.identity import HumanRole, Principal
from tigr.jobs import Job
from tigr.ssd_models import SsdDraft, SsdExecution

pytestmark = pytest.mark.skipif(
    os.environ.get("TIGR_SSD_DISPOSABLE_TEST") != "true",
    reason="Requires isolated disposable source database",
)


@pytest.fixture
def context(app_context, monkeypatch):
    client, db, current = app_context
    table = "ssd_accounts_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text(f'CREATE TABLE public."{table}" (id integer PRIMARY KEY, name text)')
        )
        connection.execute(
            text(
                f"INSERT INTO public.\"{table}\" SELECT i, 'Sensitive person' FROM generate_series(1,8) i"
            )
        )
    workspace = Workspace(
        id=uuid4(),
        slug="ssd-" + uuid4().hex,
        name="SSD tests",
        created_by="tests",
        pii_masking_enabled=False,
    )
    db.add(workspace)
    db.flush()
    owner = "ssd-qa-" + uuid4().hex
    admin = "ssd-admin-" + uuid4().hex
    db.add_all([UserIdentity(subject=owner), UserIdentity(subject=admin)])
    db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            subject=owner,
            role=HumanRole.QA_ENGINEER.value,
            valid_from=datetime.now(UTC) - timedelta(days=1),
            granted_by=admin,
        )
    )
    source = WorkspaceConnection(
        id=uuid4(),
        workspace_id=workspace.id,
        name="Test source",
        connector_type="postgresql",
        direction="both",
        environment="Disposable",
        owner=admin,
        operation_scope="read_write",
        secret_reference="env://TIGR_SECRET_SSD_TEST",
        connector_config={"databases": [engine.url.database]},
        status="enabled",
        created_by=admin,
        updated_by=admin,
        structure_preview={
            "objects": [{"database": engine.url.database, "schema": "public", "name": table}]
        },
    )
    db.add(source)
    db.flush()
    grant = AccessGrant(
        id=uuid4(),
        workspace_id=workspace.id,
        subject=owner,
        scope_type="connection",
        scope_id=str(source.id),
        permissions=["ssd:select", "ssd:insert", "ssd:update", "ssd:delete"],
        granted_by=admin,
        valid_from=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(grant)
    db.commit()
    monkeypatch.setenv("TIGR_SECRET_SSD_TEST", engine.url.render_as_string(hide_password=False))
    monkeypatch.setattr(ssd, "SessionLocal", lambda: nullcontext(db))
    current["principal"] = Principal(subject=owner, development_role=HumanRole.QA_ENGINEER)
    data = {
        "client": client,
        "db": db,
        "current": current,
        "workspace": workspace,
        "source": source,
        "grant": grant,
        "owner": owner,
        "admin": admin,
        "table": table,
    }
    yield data
    with engine.begin() as connection:
        connection.execute(text(f'DROP TABLE public."{table}"'))


def create(c, sql=None, parameters=None):
    response = c["client"].post(
        f"/api/v1/workspaces/{c['workspace'].id}/ssd/drafts",
        json={
            "name": "SSD dataset",
            "connection_id": str(c["source"].id),
            "database": engine.url.database,
            "sql": sql or f"SELECT * FROM public.{c['table']} ORDER BY id",
            "parameters": parameters or {},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def validate(c, draft):
    response = c["client"].post(
        f"/api/v1/ssd/drafts/{draft['id']}/validate",
        json={"expected_revision": draft["revision"], "idempotency_key": uuid4().hex},
    )
    assert response.status_code == 200, response.text
    return response.json()


def enqueue(c, draft, kind="execute", **overrides):
    response = c["client"].post(
        f"/api/v1/ssd/drafts/{draft['id']}/{kind}",
        json={"expected_revision": draft["revision"], "idempotency_key": uuid4().hex, **overrides},
    )
    assert response.status_code == 202, response.text
    return response.json()


def run(c, queued):
    ssd.run_job(UUID(queued["execution_id"]))
    job = c["db"].get(Job, UUID(queued["job_id"]))
    evidence = c["db"].get(SsdExecution, UUID(queued["execution_id"])).evidence
    assert job.status == "succeeded", (
        f"{job.safe_summary}: {evidence.get('failure_type')} {evidence.get('failure_cause')} {evidence.get('failure_location')}"
    )
    return job


def dataset(c):
    draft = create(c)
    validate(c, draft)
    run(c, enqueue(c, draft))
    run(c, enqueue(c, draft, "sample"))
    preview = c["client"].get(f"/api/v1/ssd/drafts/{draft['id']}/sample")
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["rows"]) == 5
    assert "disabled for workspace" in preview.json()["privacy"]
    run(c, enqueue(c, draft, "datasets"))
    d = c["db"].get(SsdDraft, UUID(draft["id"]))
    return d, c["db"].get(DataSet, d.dataset_id)


def test_select_sample_generate_publish_visibility(context):
    c = context
    _draft, ds = dataset(c)
    detail = c["client"].get(f"/api/v1/datasets/{ds.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["selected_version"]["record_count"] == 8
    publish = f"/api/v1/datasets/{ds.id}/versions/1/publish"
    assert c["client"].post(publish).status_code == 200
    assert c["client"].post(publish).status_code == 200
    assert (
        len(c["client"].get(f"/api/v1/workspaces/{c['workspace'].id}/viewable-datasets").json())
        == 1
    )
    grid = c["client"].get(f"/api/v1/datasets/{ds.id}/versions/1/view-data")
    assert grid.status_code == 200, grid.text
    assert c["client"].get(f"/api/v1/datasets/{ds.id}/versions/1/artifact").status_code == 200
    c["current"]["principal"] = Principal(
        subject="other-qa", development_role=HumanRole.QA_ENGINEER
    )
    assert c["client"].get(f"/api/v1/datasets/{ds.id}").status_code == 404
    listing = c["client"].get(f"/api/v1/datasets?workspace_id={c['workspace'].id}").json()
    assert listing["total"] == 0
    assert c["client"].get(f"/api/v1/datasets/{ds.id}/versions/1/artifact").status_code == 404
    c["current"]["principal"] = Principal(
        subject=c["owner"], development_role=HumanRole.QA_ENGINEER
    )
    assert (
        c["client"]
        .patch(f"/api/v1/datasets/{ds.id}/visibility", json={"visibility": "public"})
        .status_code
        == 200
    )
    c["current"]["principal"] = Principal(
        subject="other-qa", development_role=HumanRole.QA_ENGINEER
    )
    assert c["client"].get(f"/api/v1/datasets/{ds.id}").status_code == 200
    # Public consumers retain existing reservation and lock requirements.
    assert c["client"].get(f"/api/v1/datasets/{ds.id}/versions/1/view-data").status_code == 409
    assert c["client"].post(publish).status_code == 403
    c["current"]["principal"] = Principal(
        subject=c["owner"], development_role=HumanRole.QA_ENGINEER
    )
    c["client"].patch(f"/api/v1/datasets/{ds.id}/visibility", json={"visibility": "private"})
    c["current"]["principal"] = Principal(
        subject="other-qa", development_role=HumanRole.QA_ENGINEER
    )
    assert c["client"].get(f"/api/v1/datasets/{ds.id}").status_code == 404


def test_mutation_validates_without_executing_and_commits_once(context):
    c = context
    draft = create(
        c,
        f"INSERT INTO public.{c['table']}(id,name) VALUES (:id,:name)",
        {"id": 100, "name": "new"},
    )
    validate(c, draft)
    with engine.connect() as source:
        assert (
            source.execute(text(f"SELECT count(*) FROM public.{c['table']} WHERE id=100")).scalar()
            == 0
        )
    q = enqueue(c, draft, confirm_mutation=True)
    run(c, q)
    retry = enqueue(c, draft, confirm_mutation=True)
    assert retry["execution_id"] == q["execution_id"]
    ssd.run_job(UUID(q["execution_id"]))
    evidence = c["db"].get(SsdExecution, UUID(q["execution_id"]))
    assert evidence.outcome == "committed"
    assert evidence.evidence["affected_rows"] == 1
    assert (
        c["client"]
        .post(
            f"/api/v1/ssd/drafts/{draft['id']}/sample",
            json={"expected_revision": 1, "idempotency_key": "sample"},
        )
        .status_code
        == 409
    )
    with engine.connect() as source:
        assert (
            source.execute(text(f"SELECT count(*) FROM public.{c['table']} WHERE id=100")).scalar()
            == 1
        )


@pytest.mark.parametrize("change", ["revoke", "repoint", "edit", "cancel", "membership"])
def test_queued_work_rechecks_authority_and_revision(context, change):
    c = context
    draft = create(c)
    validate(c, draft)
    q = enqueue(c, draft)
    if change == "revoke":
        c["grant"].revoked_at = datetime.now(UTC)
    elif change == "membership":
        c["db"].get(WorkspaceMembership, (c["workspace"].id, c["owner"])).revoked_at = datetime.now(
            UTC
        )
    elif change == "repoint":
        c["source"].connector_config = {"databases": ["another_database"]}
    elif change == "edit":
        c["db"].get(SsdDraft, UUID(draft["id"])).revision = 2
    else:
        assert c["client"].post(f"/api/v1/ssd/jobs/{q['job_id']}/cancel").status_code == 202
    c["db"].commit()
    ssd.run_job(UUID(q["execution_id"]))
    job = c["db"].get(Job, UUID(q["job_id"]))
    assert job.status in ("failed", "cancelled")
    assert c["db"].get(SsdExecution, UUID(q["execution_id"])).outcome == "not_dispatched"


def test_unknown_mutation_outcome_never_replayed(context, monkeypatch):
    c = context
    draft = create(c, f"DELETE FROM public.{c['table']} WHERE id=1")
    validate(c, draft)
    q = enqueue(c, draft, confirm_mutation=True)
    calls = []

    def uncertain(*args):
        calls.append(1)
        raise OSError("connection lost around commit")

    monkeypatch.setattr(ssd, "run_source", uncertain)
    ssd.run_job(UUID(q["execution_id"]))
    ssd.run_job(UUID(q["execution_id"]))
    assert calls == [1]
    assert c["db"].get(SsdExecution, UUID(q["execution_id"])).outcome == "outcome_unknown"


def test_mutation_row_bound_rolls_back(context, monkeypatch):
    c = context
    monkeypatch.setattr(ssd, "ROW_LIMIT", 2)
    draft = create(c, f"DELETE FROM public.{c['table']}")
    validate(c, draft)
    q = enqueue(c, draft, confirm_mutation=True)
    ssd.run_job(UUID(q["execution_id"]))
    assert c["db"].get(SsdExecution, UUID(q["execution_id"])).outcome == "rolled_back"
    with engine.connect() as source:
        assert source.execute(text(f"SELECT count(*) FROM public.{c['table']}")).scalar() == 8


def test_privacy_policy_blocks_raw_results_then_applies_tde_rules(context, monkeypatch):
    c = context
    from tigr import privacy_api

    c["workspace"].pii_masking_enabled = True
    c["db"].commit()

    # An unavailable preconfigured classifier is a policy block, never raw output.
    def unavailable(*args):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(privacy_api, "_classify_schema_fields_with_ollama", unavailable)
    draft = create(c, f"SELECT id, name AS harmless FROM public.{c['table']} ORDER BY id")
    validate(c, draft)
    q = enqueue(c, draft)
    ssd.run_job(UUID(q["execution_id"]))
    failed = c["db"].get(SsdExecution, UUID(q["execution_id"]))
    assert failed.secured_output is None
    assert c["db"].get(Job, UUID(q["job_id"])).status == "failed"
    policy = MaskingPolicy(
        id=uuid4(),
        workspace_id=c["workspace"].id,
        name="SSD TDE rules",
        version_number=1,
        rules=[
            {"field": "id", "transformation": "numeric_transform", "offset": 100},
            {"field": "name", "transformation": "redaction", "replacement": "masked"},
        ],
        status="approved",
        created_by="tde",
        approved_by="tde",
    )
    c["db"].add(policy)
    c["db"].commit()
    q = enqueue(c, draft)
    run(c, q)
    run(c, enqueue(c, draft, "sample"))
    sample = c["client"].get(f"/api/v1/ssd/drafts/{draft['id']}/sample")
    assert sample.status_code == 200, sample.text
    assert sample.json()["rows"] == [{"id": i + 100, "harmless": "masked"} for i in range(1, 6)]
    policy.status = "revoked"
    c["db"].commit()
    assert c["client"].get(f"/api/v1/ssd/drafts/{draft['id']}/sample").status_code == 409


def test_policy_enabled_after_disabled_generation_withholds_dataset(context):
    c = context
    _, ds = dataset(c)
    c["workspace"].pii_masking_enabled = True
    c["db"].commit()
    assert c["client"].get(f"/api/v1/datasets/{ds.id}").status_code == 409
    assert c["client"].post(f"/api/v1/datasets/{ds.id}/versions/1/publish").status_code == 409


def test_granted_locked_connection_explains_unavailability_without_bypassing_locks(context):
    c = context
    c["source"].entity_locking_enabled = True
    c["source"].operation_scope = "read_only"
    c["db"].commit()
    url = f"/api/v1/workspaces/{c['workspace'].id}/ssd/connections"
    assert c["client"].get(url).json() == []
    response = c["client"].get(url + "?include_unavailable=true")
    assert response.status_code == 200
    item, = response.json()
    assert item["id"] == str(c["source"].id)
    assert item["available"] is False
    assert "SSD_LOCK_CONFIGURATION_REQUIRED" in item["unavailable_reasons"][0]
    assert item["operations"] == item["databases"] == []
    assert item["schemas"] == {}
    draft = create(c)
    assert c["client"].post(
        f"/api/v1/ssd/drafts/{draft['id']}/validate",
        json={"expected_revision": 1, "idempotency_key": uuid4().hex},
    ).status_code == 409
    c["source"].entity_locking_enabled = False
    c["db"].commit()
    item, = c["client"].get(url + "?include_unavailable=true").json()
    assert item["available"] is True
    assert item["operations"] == ["select"]
    c["grant"].revoked_at = datetime.now(UTC)
    c["db"].commit()
    assert c["client"].get(url + "?include_unavailable=true").json() == []


def test_empty_select_can_create_reusable_dataset(context):
    c = context
    draft = create(c, f"SELECT * FROM public.{c['table']} WHERE id<0")
    validate(c, draft)
    run(c, enqueue(c, draft))
    run(c, enqueue(c, draft, "sample"))
    q = enqueue(c, draft, "datasets")
    run(c, q)
    assert c["db"].get(SsdDraft, UUID(draft["id"])).dataset_id is not None


def test_no_grants_and_write_groups_are_independent(context):
    c = context
    c["grant"].permissions = []
    c["db"].commit()
    assert c["client"].get(f"/api/v1/workspaces/{c['workspace'].id}/ssd/connections").json() == []
    c["grant"].permissions = ["ssd:insert", "ssd:update"]
    c["db"].commit()
    draft = create(c, f"INSERT INTO public.{c['table']}(id) VALUES(100)")
    validate(c, draft)
    response = c["client"].post(
        f"/api/v1/workspaces/{c['workspace'].id}/ssd/drafts",
        json={
            "name": "Forbidden delete",
            "connection_id": str(c["source"].id),
            "database": engine.url.database,
            "sql": f"DELETE FROM public.{c['table']}",
        },
    )
    assert response.status_code == 201
    forbidden = response.json()
    rejected = c["client"].post(
        f"/api/v1/ssd/drafts/{forbidden['id']}/validate",
        json={"expected_revision": 1, "idempotency_key": "validate"},
    )
    assert rejected.status_code == 403


def test_private_job_inventory_and_admin_grants(context):
    c = context
    draft = create(c)
    validate(c, draft)
    q = enqueue(c, draft)
    c["current"]["principal"] = Principal(subject=c["admin"], development_role=HumanRole.ADMIN)
    assert c["client"].get(f"/api/v1/jobs/{q['job_id']}").status_code == 404
    granted = c["client"].post(
        f"/api/v1/workspaces/{c['workspace'].id}/grants",
        json={
            "subject": c["owner"],
            "scope_type": "connection",
            "scope_id": str(c["source"].id),
            "permissions": ["ssd:insert", "ssd:update"],
        },
    )
    assert granted.status_code == 201, granted.text
    assert (
        c["client"]
        .post(
            f"/api/v1/workspaces/{c['workspace'].id}/masking-policies",
            json={"name": "Admin blocked", "rules": []},
        )
        .status_code
        == 403
    )


def publish_saved(c, draft):
    saved = c["db"].get(SsdDraft, UUID(draft["id"]))
    response = c["client"].post(f"/api/v1/datasets/{saved.dataset_id}/versions/{saved.dataset_version_number}/publish")
    assert response.status_code == 200, response.text
    return saved


@pytest.mark.parametrize("operation", ["update", "insert"])
def test_mutation_save_publish_and_explicit_refresh(context, operation):
    c = context
    sql = (f"UPDATE public.{c['table']} SET name = 'Updated' WHERE id = 1" if operation == "update"
           else f"INSERT INTO public.{c['table']} (id, name) SELECT COALESCE(MAX(id), 0) + 1, 'Added' FROM public.{c['table']}")
    draft = create(c, sql)
    validate(c, draft)
    run(c, enqueue(c, draft, confirm_mutation=True))
    run(c, enqueue(c, draft, "datasets"))
    saved = publish_saved(c, draft)
    from tigr.datasets import DataSetVersion
    first = c["db"].scalar(ssd.select(DataSetVersion).where(DataSetVersion.dataset_id == saved.dataset_id))
    assert first.record_count == 0
    url = f"/api/v1/datasets/{saved.dataset_id}/refresh"
    payload = {"expected_version": 1, "idempotency_key": uuid4().hex}
    assert c["client"].post(url, json=payload).status_code == 422
    payload["confirm_mutation"] = True
    response = c["client"].post(url, json=payload)
    assert response.status_code == 202, response.text
    queued = response.json()
    assert c["client"].post(url, json=payload).json()["job_id"] == queued["job_id"]
    run(c, queued)
    latest = c["db"].scalar(ssd.select(DataSetVersion).where(DataSetVersion.dataset_id == saved.dataset_id, DataSetVersion.version_number == 2))
    assert latest.publication_state == "published"
    assert latest.schema_scope == first.schema_scope
    assert latest.record_count == 0
    assert c["db"].get(SsdExecution, UUID(queued["execution_id"])).outcome == "committed"
    # Re-dispatch does not execute the source twice.
    ssd.run_job(UUID(queued["execution_id"]))
    if operation == "insert":
        with engine.connect() as source:
            assert source.scalar(text(f"SELECT COUNT(*) FROM public.{c['table']}")) == 10


@pytest.mark.parametrize("role", [HumanRole.QA_ENGINEER, HumanRole.TEST_DATA_ENGINEER, HumanRole.ADMIN])
def test_refresh_saved_select_all_roles_preserves_structure(context, role):
    c = context
    draft = create(c)
    validate(c, draft)
    run(c, enqueue(c, draft))
    run(c, enqueue(c, draft, "sample"))
    run(c, enqueue(c, draft, "datasets"))
    saved = publish_saved(c, draft)
    ds = c["db"].get(DataSet, saved.dataset_id)
    ds.visibility = "public"
    actor = saved.owner_id
    membership = c["db"].scalar(ssd.select(WorkspaceMembership).where(WorkspaceMembership.workspace_id == c["workspace"].id, WorkspaceMembership.subject == actor))
    membership.role = role.value
    c["db"].commit()
    c["current"]["principal"] = Principal(subject=actor, development_role=role)
    with engine.begin() as source:
        source.execute(text(f"UPDATE public.{c['table']} SET name='Fresh' WHERE id=1"))
    response = c["client"].post(f"/api/v1/datasets/{saved.dataset_id}/refresh", json={"expected_version": 1, "idempotency_key": uuid4().hex})
    assert response.status_code == 202, response.text
    run(c, response.json())
    from tigr.datasets import DataSetVersion
    latest = c["db"].scalar(ssd.select(DataSetVersion).where(DataSetVersion.dataset_id == ds.id, DataSetVersion.version_number == 2))
    assert latest.record_count == 8
    assert latest.publication_state == "published"
    assert ssd.read_dataset_records(c["db"], latest)[0]["name"] == "Fresh"


def test_refresh_rechecks_grant_and_keeps_previous_version(context):
    c = context
    draft = create(c)
    validate(c, draft)
    run(c, enqueue(c, draft))
    run(c, enqueue(c, draft, "sample"))
    run(c, enqueue(c, draft, "datasets"))
    saved = publish_saved(c, draft)
    url = f"/api/v1/datasets/{saved.dataset_id}/refresh"
    payload = {"expected_version": 1, "idempotency_key": uuid4().hex, "sql": "DELETE FROM anything"}
    assert c["client"].post(url, json=payload).status_code == 422
    payload.pop("sql")
    queued = c["client"].post(url, json=payload)
    assert queued.status_code == 202
    c["grant"].revoked_at = datetime.now(UTC)
    c["db"].commit()
    ssd.run_job(UUID(queued.json()["execution_id"]))
    assert c["db"].get(Job, UUID(queued.json()["job_id"])).status == "failed"
    assert c["db"].get(DataSet, saved.dataset_id).latest_version_number == 1


def test_refresh_schema_change_keeps_previous_published_version(context):
    c = context
    draft = create(c)
    validate(c, draft)
    run(c, enqueue(c, draft))
    run(c, enqueue(c, draft, "sample"))
    run(c, enqueue(c, draft, "datasets"))
    saved = publish_saved(c, draft)
    with engine.begin() as source:
        source.execute(text(f"ALTER TABLE public.{c['table']} ADD COLUMN extra text"))
    queued = c["client"].post(f"/api/v1/datasets/{saved.dataset_id}/refresh", json={"expected_version": 1, "idempotency_key": uuid4().hex})
    assert queued.status_code == 202
    ssd.run_job(UUID(queued.json()["execution_id"]))
    job = c["db"].get(Job, UUID(queued.json()["job_id"]))
    assert job.status == "failed"
    assert "SSD_SCHEMA_CHANGED" in job.safe_summary
    assert c["db"].get(DataSet, saved.dataset_id).latest_version_number == 1
