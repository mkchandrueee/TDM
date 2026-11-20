"""Persist resumable Source to Data drafts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0019_source_data_plans"
down_revision = "0018_source_data_queries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_data_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("plan_data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("state IN ('draft', 'saved')", name="ck_source_data_plans_state"),
        schema="dataops",
    )
    op.create_index("ix_source_data_plans_workspace_updated", "source_data_plans", ["workspace_id", "updated_at"], schema="dataops")


def downgrade() -> None:
    op.drop_index("ix_source_data_plans_workspace_updated", table_name="source_data_plans", schema="dataops")
    op.drop_table("source_data_plans", schema="dataops")
