"""Shared job, outbox, and audit foundation."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_shared_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS dataops")
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("scope_key", sa.String(180), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(180), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("safe_summary", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("scope_key", "idempotency_key", name="uq_jobs_idempotency"),
        schema="dataops",
    )
    op.create_table(
        "job_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("safe_summary", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="dataops",
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("aggregate_type", sa.String(80), nullable=False),
        sa.Column("aggregate_id", sa.String(180), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        schema="dataops",
    )
    op.create_index("ix_outbox_pending", "outbox_events", ["created_at"], schema="dataops", postgresql_where=sa.text("published_at IS NULL"))
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(180), nullable=False),
        sa.Column("workspace_id", sa.String(180)),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("object_type", sa.String(80), nullable=False),
        sa.Column("object_id", sa.String(180)),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="audit",
    )
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"], schema="audit")


def downgrade() -> None:
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events", schema="audit")
    op.drop_table("audit_events", schema="audit")
    op.drop_index("ix_outbox_pending", table_name="outbox_events", schema="dataops")
    op.drop_table("outbox_events", schema="dataops")
    op.drop_table("job_steps", schema="dataops")
    op.drop_table("jobs", schema="dataops")
    op.execute("DROP SCHEMA IF EXISTS audit")
    op.execute("DROP SCHEMA IF EXISTS dataops")
