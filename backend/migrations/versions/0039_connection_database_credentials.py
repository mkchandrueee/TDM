"""Add optional per-database secret references to source connections."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0039_db_credentials"
down_revision = "0038_mysql_source_connector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connection_database_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("database_name", sa.String(length=64), nullable=False),
        sa.Column("secret_reference", sa.String(length=500), nullable=False),
        sa.Column("created_by", sa.String(length=180), nullable=False),
        sa.Column("updated_by", sa.String(length=180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["dataops.workspace_connections.workspace_id", "dataops.workspace_connections.id"],
            ondelete="CASCADE", name="fk_connection_database_credential_connection",
        ),
        sa.UniqueConstraint("connection_id", "database_name", name="uq_connection_database_credential"),
        schema="dataops",
    )


def downgrade() -> None:
    op.drop_table("connection_database_credentials", schema="dataops")
