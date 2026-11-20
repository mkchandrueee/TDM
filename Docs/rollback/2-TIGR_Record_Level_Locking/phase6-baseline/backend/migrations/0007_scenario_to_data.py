"""Scenario to Data workflow plan state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_scenario_to_data"
down_revision = "0006_datasets_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scenario_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(180), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("requirement", sa.Text(), nullable=False),
        sa.Column("expected_states", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("exclusions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("target_use", sa.String(500), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("clarifying_questions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("generation_mode", sa.String(20), nullable=False, server_default="synthetic"),
        sa.Column("source_connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.workspace_connections.id", ondelete="RESTRICT")),
        sa.Column("source_object", sa.String(180)),
        sa.Column("mapping", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("logical_plan", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("state", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.jobs.id", ondelete="SET NULL")),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.datasets.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("state IN ('draft', 'needs_clarification', 'mapped', 'ready', 'running', 'succeeded', 'failed', 'quarantined')", name="ck_scenario_plans_state"),
        sa.CheckConstraint("generation_mode IN ('synthetic', 'source_backed')", name="ck_scenario_plans_mode"),
        sa.CheckConstraint("volume >= 1 AND volume <= 10000", name="ck_scenario_plans_volume"),
        schema="dataops",
    )
    op.create_index("ix_scenario_plans_workspace_updated", "scenario_plans", ["workspace_id", "updated_at"], schema="dataops")


def downgrade() -> None:
    op.drop_index("ix_scenario_plans_workspace_updated", table_name="scenario_plans", schema="dataops")
    op.drop_table("scenario_plans", schema="dataops")
