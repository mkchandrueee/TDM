"""Add Oracle and Microsoft SQL Server source connectors."""
from alembic import op
import sqlalchemy as sa

revision = "0041_oracle_mssql"
down_revision = "0040_encrypt_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_workspace_connections_type", "workspace_connections", schema="dataops", type_="check")
    op.create_check_constraint(
        "ck_workspace_connections_type", "workspace_connections",
        "connector_type IN ('postgresql', 'mysql', 'oracle', 'mssql', 'json', 'csv', 'delimited_text')",
        schema="dataops",
    )
    op.alter_column("connection_database_credentials", "database_name", schema="dataops",
                    existing_type=sa.String(length=64), type_=sa.String(length=128), existing_nullable=False)


def downgrade() -> None:
    op.alter_column("connection_database_credentials", "database_name", schema="dataops",
                    existing_type=sa.String(length=128), type_=sa.String(length=64), existing_nullable=False)
    op.drop_constraint("ck_workspace_connections_type", "workspace_connections", schema="dataops", type_="check")
    op.create_check_constraint(
        "ck_workspace_connections_type", "workspace_connections",
        "connector_type IN ('postgresql', 'mysql', 'json', 'csv', 'delimited_text')",
        schema="dataops",
    )
