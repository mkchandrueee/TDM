"""Durable provisioning runs and short lived report exports."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_provisioning_exports"
down_revision = "0009_privacy_reservations_locks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provisioning_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.workspace_connections.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.String(180), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("target_object", sa.String(500)),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["dataset_id", "version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="RESTRICT", name="fk_provisioning_dataset_version"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_provisioning_idempotency"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cleaned')", name="ck_provisioning_status"),
        sa.CheckConstraint("record_count >= 0", name="ck_provisioning_count"),
        schema="dataops",
    )
    op.create_table(
        "export_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(180), nullable=False),
        sa.Column("report_type", sa.String(40), nullable=False),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("downloaded_at", sa.DateTime(timezone=True)),
        schema="dataops",
    )


def downgrade() -> None:
    op.drop_table("export_artifacts", schema="dataops")
    op.drop_table("provisioning_runs", schema="dataops")
