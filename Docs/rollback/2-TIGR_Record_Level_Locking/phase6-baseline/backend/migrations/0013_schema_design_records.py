"""Add normalized test, publication, validation, and artifact metadata records."""

import json
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_schema_design_records"
down_revision = "0012_relational_dataset_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_workspace_connections_scope_id", "workspace_connections", ["workspace_id", "id"], schema="dataops")
    op.create_table(
        "connection_test_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", sa.String(180), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("safe_error_code", sa.String(80)),
        sa.Column("safe_message", sa.String(500), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.CheckConstraint("outcome IN ('succeeded', 'failed')", name="ck_connection_test_outcome"),
        sa.ForeignKeyConstraint(["workspace_id"], ["iam.workspaces.id"], name="fk_connection_test_runs_workspace", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id", "connection_id"], ["dataops.workspace_connections.workspace_id", "dataops.workspace_connections.id"], name="fk_connection_test_scoped_connection", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema="dataops",
    )
    op.create_index("ix_connection_test_runs_latest", "connection_test_runs", ["workspace_id", "connection_id", "started_at"], schema="dataops")

    op.create_table(
        "dataset_status_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(24)),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("changed_by", sa.String(180), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["iam.workspaces.id"], name="fk_dataset_status_history_workspace", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id", "dataset_id", "version_id"], ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], name="fk_dataset_status_history_version", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema="dataops",
    )
    op.create_index("ix_dataset_status_history_version", "dataset_status_history", ["dataset_id", "version_id", "changed_at"], schema="dataops")

    op.create_table(
        "dataset_publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("published_by", sa.String(180), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), server_default="published", nullable=False),
        sa.Column("readiness_snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("withdrawn_by", sa.String(180)),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.String(500)),
        sa.CheckConstraint("state IN ('published', 'withdrawn')", name="ck_dataset_publication_state"),
        sa.ForeignKeyConstraint(["workspace_id"], ["iam.workspaces.id"], name="fk_dataset_publications_workspace", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id", "dataset_id", "version_id"], ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], name="fk_dataset_publication_version", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "version_id", name="uq_dataset_publication_version"),
        schema="dataops",
    )

    op.create_table(
        "validation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True)),
        sa.Column("validation_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint("status IN ('running', 'passed', 'failed', 'warning')", name="ck_validation_run_status"),
        sa.ForeignKeyConstraint(["workspace_id"], ["iam.workspaces.id"], name="fk_validation_runs_workspace", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id", "dataset_id", "version_id"], ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], name="fk_validation_run_version", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["dataops.jobs.id"], name="fk_validation_runs_job", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_validation_runs_scope_id"),
        schema="dataops",
    )
    op.create_index("ix_validation_runs_version", "validation_runs", ["workspace_id", "dataset_id", "version_id", "started_at"], schema="dataops")
    op.create_table(
        "validation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("validation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("check_code", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("field_path", sa.String(500)),
        sa.Column("safe_message", sa.String(500), nullable=False),
        sa.Column("observed_count", sa.BigInteger()),
        sa.Column("expected_summary", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint("severity IN ('info', 'warning', 'error')", name="ck_validation_result_severity"),
        sa.CheckConstraint("status IN ('passed', 'failed', 'warning', 'skipped')", name="ck_validation_result_status"),
        sa.ForeignKeyConstraint(["workspace_id"], ["iam.workspaces.id"], name="fk_validation_results_workspace", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id", "validation_run_id"], ["dataops.validation_runs.workspace_id", "dataops.validation_runs.id"], name="fk_validation_result_run", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="dataops",
    )

    op.create_table(
        "artifact_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True)),
        sa.Column("version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("artifact_type", sa.String(40), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=False),
        sa.Column("storage_provider", sa.String(40), server_default="local", nullable=False),
        sa.Column("encryption_key_ref", sa.String(500)),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(128)),
        sa.Column("classification", sa.String(32), server_default="restricted", nullable=False),
        sa.Column("status", sa.String(16), server_default="available", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("(dataset_id IS NULL) = (version_id IS NULL)", name="ck_artifact_record_dataset_version_pair"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_artifact_record_size"),
        sa.CheckConstraint("status IN ('available', 'quarantined', 'expired', 'deleted')", name="ck_artifact_record_status"),
        sa.ForeignKeyConstraint(["workspace_id"], ["iam.workspaces.id"], name="fk_artifact_records_workspace", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id", "dataset_id", "version_id"], ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], name="fk_artifact_record_version", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_artifact_storage_key"),
        schema="dataops",
    )
    op.create_index("ix_artifact_records_version", "artifact_records", ["workspace_id", "dataset_id", "version_id", "status"], schema="dataops")

    connection = op.get_bind()
    legacy_tests = connection.execute(sa.text("""
        SELECT id, workspace_id, tested_by, tested_at, test_result
        FROM dataops.workspace_connections WHERE tested_at IS NOT NULL
    """)).mappings().all()
    for test in legacy_tests:
        result = test["test_result"] or {}
        succeeded = bool(result.get("succeeded"))
        message = str(result.get("message") or ("Connection test passed" if succeeded else "Connection test failed"))[:500]
        connection.execute(sa.text("""
            INSERT INTO dataops.connection_test_runs
                (id, workspace_id, connection_id, requested_by, started_at, finished_at, outcome,
                 safe_error_code, safe_message, correlation_id)
            VALUES (:id, :workspace_id, :connection_id, :requested_by, :tested_at, :tested_at, :outcome,
                    :error_code, :message, 'migration-0013')
        """), {"id": uuid4(), "workspace_id": test["workspace_id"], "connection_id": test["id"],
              "requested_by": test["tested_by"] or "unknown", "tested_at": test["tested_at"],
              "outcome": "succeeded" if succeeded else "failed",
              "error_code": None if succeeded else "connection_test_failed", "message": message})

    versions = connection.execute(sa.text("""
        SELECT version.id, version.dataset_id, version.workspace_id, version.lifecycle_state,
               version.created_by, version.created_at, version.published_at, version.validation_state,
               version.validation_evidence, dataset.name
        FROM dataops.dataset_versions AS version
        JOIN dataops.datasets AS dataset ON dataset.id = version.dataset_id
    """)).mappings().all()
    for version in versions:
        connection.execute(sa.text("""
            INSERT INTO dataops.dataset_status_history
                (id, workspace_id, dataset_id, version_id, from_status, to_status, changed_by, changed_at,
                 reason, correlation_id)
            VALUES (:id, :workspace_id, :dataset_id, :version_id, NULL, :state, :actor, :changed_at,
                    'Initial state captured during schema alignment', 'migration-0013')
        """), {"id": uuid4(), "workspace_id": version["workspace_id"], "dataset_id": version["dataset_id"],
              "version_id": version["id"], "state": version["lifecycle_state"], "actor": version["created_by"],
              "changed_at": version["created_at"]})
        if version["published_at"] is not None:
            connection.execute(sa.text("""
                INSERT INTO dataops.dataset_publications
                    (id, workspace_id, dataset_id, version_id, published_by, published_at, state, readiness_snapshot)
                VALUES (:id, :workspace_id, :dataset_id, :version_id, :actor, :published_at, 'published', CAST(:snapshot AS jsonb))
            """), {"id": uuid4(), "workspace_id": version["workspace_id"], "dataset_id": version["dataset_id"],
                  "version_id": version["id"], "actor": version["created_by"], "published_at": version["published_at"],
                  "snapshot": '{"source":"existing published version"}'})
        if version["validation_state"] != "not_run":
            status = "passed" if version["validation_state"] == "passed" else "failed"
            evidence = version["validation_evidence"] or {}
            completed = version["published_at"] or version["created_at"]
            run_id = uuid4()
            connection.execute(sa.text("""
                INSERT INTO dataops.validation_runs
                    (id, workspace_id, dataset_id, version_id, validation_type, status, started_at, completed_at, summary)
                VALUES (:id, :workspace_id, :dataset_id, :version_id, 'legacy_evidence', :status, :started_at, :completed_at, CAST(:summary AS jsonb))
            """), {"id": run_id, "workspace_id": version["workspace_id"], "dataset_id": version["dataset_id"],
                  "version_id": version["id"], "status": status, "started_at": completed, "completed_at": completed,
                  "summary": json.dumps({"summary": evidence.get("summary", "Existing validation evidence")})})

    op.create_table(
        "consumption_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_id", sa.String(180), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("target_connection_id", postgresql.UUID(as_uuid=True)),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["iam.workspaces.id"], name="fk_consumption_events_workspace", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id", "dataset_id", "version_id"], ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], name="fk_consumption_event_version", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_connection_id"], ["dataops.workspace_connections.id"], name="fk_consumption_events_target", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["artifact_id"], ["dataops.artifact_records.id"], name="fk_consumption_events_artifact", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema="dataops",
    )
    op.create_index("ix_consumption_events_version", "consumption_events", ["workspace_id", "dataset_id", "version_id", "occurred_at"], schema="dataops")


def downgrade() -> None:
    op.drop_index("ix_consumption_events_version", table_name="consumption_events", schema="dataops")
    op.drop_table("consumption_events", schema="dataops")
    op.drop_index("ix_artifact_records_version", table_name="artifact_records", schema="dataops")
    op.drop_table("artifact_records", schema="dataops")
    op.drop_table("validation_results", schema="dataops")
    op.drop_index("ix_validation_runs_version", table_name="validation_runs", schema="dataops")
    op.drop_table("validation_runs", schema="dataops")
    op.drop_table("dataset_publications", schema="dataops")
    op.drop_index("ix_dataset_status_history_version", table_name="dataset_status_history", schema="dataops")
    op.drop_table("dataset_status_history", schema="dataops")
    op.drop_index("ix_connection_test_runs_latest", table_name="connection_test_runs", schema="dataops")
    op.drop_table("connection_test_runs", schema="dataops")
    op.drop_constraint("uq_workspace_connections_scope_id", "workspace_connections", schema="dataops", type_="unique")
