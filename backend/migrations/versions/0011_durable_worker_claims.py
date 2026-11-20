"""Add expiring claims for independently runnable outbox workers."""

import sqlalchemy as sa
from alembic import op

revision = "0011_durable_worker_claims"
down_revision = "0010_provisioning_exports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("claimed_at", sa.DateTime(timezone=True)), schema="dataops")
    op.add_column("outbox_events", sa.Column("claim_token", sa.String(64)), schema="dataops")
    op.add_column("outbox_events", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"), schema="dataops")
    op.create_index("ix_outbox_claimed", "outbox_events", ["claimed_at", "created_at"], schema="dataops",
                    postgresql_where=sa.text("published_at IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_outbox_claimed", table_name="outbox_events", schema="dataops")
    op.drop_column("outbox_events", "attempt_count", schema="dataops")
    op.drop_column("outbox_events", "claim_token", schema="dataops")
    op.drop_column("outbox_events", "claimed_at", schema="dataops")
