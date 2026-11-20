"""Add human role snapshot to job ownership context."""
import sqlalchemy as sa
from alembic import op

revision = "0004_job_actor_role"
down_revision = "0003_identity_workspaces_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("actor_role", sa.String(32)), schema="dataops")


def downgrade() -> None:
    op.drop_column("jobs", "actor_role", schema="dataops")
