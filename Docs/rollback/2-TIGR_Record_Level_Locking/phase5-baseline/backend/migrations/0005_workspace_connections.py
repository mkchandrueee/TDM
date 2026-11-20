"""Add workspace scoped source and target connections."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_workspace_connections"
down_revision = "0004_job_actor_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("connector_type", sa.String(24), nullable=False),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("environment", sa.String(40), nullable=False),
        sa.Column("purpose", sa.Text()),
        sa.Column("owner", sa.String(180), nullable=False),
        sa.Column("operation_scope", sa.String(16), nullable=False),
        sa.Column("secret_reference", sa.String(500)),
        sa.Column("connector_config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("tested_at", sa.DateTime(timezone=True)),
        sa.Column("tested_by", sa.String(180)),
        sa.Column("test_result", postgresql.JSONB()),
        sa.Column("structure_preview", postgresql.JSONB()),
        sa.Column("created_by", sa.String(180), nullable=False),
        sa.Column("updated_by", sa.String(180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("workspace_id", "name", name="uq_workspace_connections_name"),
        sa.CheckConstraint("connector_type IN ('postgresql', 'json', 'csv', 'delimited_text')", name="ck_workspace_connections_type"),
        sa.CheckConstraint("direction IN ('source', 'target', 'both')", name="ck_workspace_connections_direction"),
        sa.CheckConstraint("operation_scope IN ('read_only', 'write_only', 'read_write')", name="ck_workspace_connections_scope"),
        sa.CheckConstraint("status IN ('draft', 'tested', 'enabled', 'disabled')", name="ck_workspace_connections_status"),
        schema="dataops",
    )
    op.create_index("ix_workspace_connections_workspace_status", "workspace_connections", ["workspace_id", "status"], schema="dataops")


def downgrade() -> None:
    op.drop_index("ix_workspace_connections_workspace_status", table_name="workspace_connections", schema="dataops")
    op.drop_table("workspace_connections", schema="dataops")
