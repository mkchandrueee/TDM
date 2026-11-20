"""PII review, masking policies and runs, reservations, and lock leases."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_privacy_reservations_locks"
down_revision = "0008_source_data_mapping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pii_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.dataset_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field", sa.String(180), nullable=False),
        sa.Column("suggested_type", sa.String(40), nullable=False),
        sa.Column("classification", sa.String(20), nullable=False, server_default="sensitive"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.75"),
        sa.Column("reviewed_by", sa.String(180)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("version_id", "field", name="uq_pii_finding_version_field"),
        sa.CheckConstraint("classification IN ('sensitive', 'non_sensitive')", name="ck_pii_finding_classification"),
        schema="dataops",
    )
    op.create_table(
        "masking_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("rules", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(180), nullable=False),
        sa.Column("approved_by", sa.String(180)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "name", "version_number", name="uq_masking_policy_version"),
        sa.CheckConstraint("version_number > 0", name="ck_masking_policy_version_positive"),
        sa.CheckConstraint("status IN ('draft', 'approved', 'revoked')", name="ck_masking_policy_status"),
        schema="dataops",
    )
    op.create_table(
        "masking_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.masking_policies.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["dataset_id", "version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="CASCADE", name="fk_masking_run_dataset_version"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_masking_run_status"),
        schema="dataops",
    )
    op.create_table(
        "dataset_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", sa.String(180), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="scheduled"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["dataset_id", "version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="CASCADE", name="fk_reservation_dataset_version"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_reservation_window"),
        sa.CheckConstraint("status IN ('scheduled', 'active', 'completed', 'cancelled', 'expired')", name="ck_reservation_status"),
        schema="dataops",
    )
    op.create_index("ix_reservations_workspace_window", "dataset_reservations", ["workspace_id", "starts_at", "ends_at"], schema="dataops")
    op.create_table(
        "dataset_locks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", sa.String(180), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("release_reason", sa.Text()),
        sa.ForeignKeyConstraint(["dataset_id", "version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="CASCADE", name="fk_lock_dataset_version"),
        sa.CheckConstraint("status IN ('active', 'released', 'expired', 'force_released')", name="ck_dataset_lock_status"),
        schema="dataops",
    )
    op.create_index("uq_dataset_lock_active", "dataset_locks", ["version_id"], unique=True, postgresql_where=sa.text("status = 'active'"), schema="dataops")


def downgrade() -> None:
    op.drop_index("uq_dataset_lock_active", table_name="dataset_locks", schema="dataops")
    op.drop_table("dataset_locks", schema="dataops")
    op.drop_index("ix_reservations_workspace_window", table_name="dataset_reservations", schema="dataops")
    op.drop_table("dataset_reservations", schema="dataops")
    op.drop_table("masking_runs", schema="dataops")
    op.drop_table("masking_policies", schema="dataops")
    op.drop_table("pii_findings", schema="dataops")
