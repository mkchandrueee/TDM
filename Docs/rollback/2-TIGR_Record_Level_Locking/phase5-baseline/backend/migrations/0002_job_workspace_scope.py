"""Add workspace scope to shared asynchronous job records."""
import sqlalchemy as sa
from alembic import op

revision = "0002_job_workspace_scope"
down_revision = "0001_shared_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("workspace_id", sa.String(180)), schema="dataops")
    op.add_column("outbox_events", sa.Column("workspace_id", sa.String(180)), schema="dataops")


def downgrade() -> None:
    op.drop_column("outbox_events", "workspace_id", schema="dataops")
    op.drop_column("jobs", "workspace_id", schema="dataops")
