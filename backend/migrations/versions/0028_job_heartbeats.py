"""Expose durable worker heartbeats for job progress and recovery."""

import sqlalchemy as sa
from alembic import op

revision = "0028_job_heartbeats"
down_revision = "0027_entity_lock_rollout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        schema="dataops",
    )
    op.execute("UPDATE dataops.jobs SET heartbeat_at = created_at WHERE heartbeat_at IS NULL")
    op.alter_column(
        "jobs",
        "heartbeat_at",
        schema="dataops",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def downgrade() -> None:
    op.drop_column("jobs", "heartbeat_at", schema="dataops")
