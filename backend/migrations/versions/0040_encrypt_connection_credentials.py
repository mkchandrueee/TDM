"""Allow encrypted connection credentials to be stored without truncation."""

import sqlalchemy as sa
from alembic import op

revision = "0040_encrypt_credentials"
down_revision = "0039_db_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "workspace_connections", "secret_reference",
        existing_type=sa.String(length=500), type_=sa.Text(),
        schema="dataops",
    )
    op.alter_column(
        "connection_database_credentials", "secret_reference",
        existing_type=sa.String(length=500), type_=sa.Text(),
        existing_nullable=False, schema="dataops",
    )


def downgrade() -> None:
    op.alter_column(
        "connection_database_credentials", "secret_reference",
        existing_type=sa.Text(), type_=sa.String(length=500),
        existing_nullable=False, schema="dataops",
    )
    op.alter_column(
        "workspace_connections", "secret_reference",
        existing_type=sa.Text(), type_=sa.String(length=500),
        schema="dataops",
    )
