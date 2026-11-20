"""Versioned source-to-data field mappings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_source_data_mapping"
down_revision = "0007_scenario_to_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_data_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.workspace_connections.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("source_object", sa.String(180), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("relationships", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "name", "version_number", name="uq_source_data_mapping_version"),
        sa.CheckConstraint("version_number > 0", name="ck_source_data_mapping_version_positive"),
        schema="dataops",
    )
    op.create_index("ix_source_data_mappings_workspace", "source_data_mappings", ["workspace_id", "name", "version_number"], schema="dataops")


def downgrade() -> None:
    op.drop_index("ix_source_data_mappings_workspace", table_name="source_data_mappings", schema="dataops")
    op.drop_table("source_data_mappings", schema="dataops")
