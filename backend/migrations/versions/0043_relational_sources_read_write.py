"""Default relational source connections to read and write for SSD."""

from alembic import op
import sqlalchemy as sa

revision = "0043_relational_sources_read_write"
down_revision = "0042_self_servicing_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE dataops.workspace_connections
        SET operation_scope = 'read_write'
        WHERE connector_type IN ('postgresql', 'mysql', 'oracle', 'mssql')
          AND direction IN ('source', 'both')
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        UPDATE dataops.workspace_connections
        SET operation_scope = 'read_only'
        WHERE connector_type IN ('postgresql', 'mysql', 'oracle', 'mssql')
          AND direction IN ('source', 'both')
    """))
