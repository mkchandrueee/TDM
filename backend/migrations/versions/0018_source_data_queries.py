"""Store validated source queries for query-backed generation.

Revision ID: 0018_source_data_queries
Revises: 0017_dataset_generation_settings
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0018_source_data_queries"
down_revision = "0017_dataset_generation_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_data_queries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.workspace_connections.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("output_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="dataops",
    )
    op.create_index("ix_source_data_queries_workspace_created", "source_data_queries", ["workspace_id", "created_at"], schema="dataops")


def downgrade() -> None:
    op.drop_index("ix_source_data_queries_workspace_created", table_name="source_data_queries", schema="dataops")
    op.drop_table("source_data_queries", schema="dataops")
