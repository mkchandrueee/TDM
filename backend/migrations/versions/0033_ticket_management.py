"""Add scoped test data request tickets and immutable ticket history."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033_ticket_management"
down_revision = "0032_dataset_ref_tds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE dataops.ticket_display_sequence START WITH 1")
    op.create_table(
        "test_data_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("display_id", sa.String(32), nullable=False, unique=True),
        sa.Column("title", sa.String(160)), sa.Column("description", sa.Text()),
        sa.Column("request_type", sa.String(64)), sa.Column("environment", sa.String(80)),
        sa.Column("application", sa.String(180)), sa.Column("scenarios", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("schema_reference", sa.Text()), sa.Column("record_count", sa.Integer()),
        sa.Column("data_source_preference", sa.String(24)), sa.Column("data_date_from", sa.Date()), sa.Column("data_date_to", sa.Date()),
        sa.Column("relationships", sa.Text()), sa.Column("masking_needs", sa.Text()), sa.Column("acceptance_criteria", sa.Text()),
        sa.Column("needed_by", sa.DateTime(timezone=True)), sa.Column("test_window_start", sa.DateTime(timezone=True)), sa.Column("test_window_end", sa.DateTime(timezone=True)),
        sa.Column("urgent_justification", sa.Text()), sa.Column("requester_user_id", sa.String(180), sa.ForeignKey("iam.users.subject", ondelete="RESTRICT"), nullable=False),
        sa.Column("assignee_user_id", sa.String(180), sa.ForeignKey("iam.users.subject", ondelete="RESTRICT")),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.String(180)), sa.Column("project_name", sa.String(180)),
        sa.Column("priority", sa.String(16), nullable=False, server_default="Normal"), sa.Column("status", sa.String(16), nullable=False, server_default="Draft"),
        sa.Column("linked_dataset_id", postgresql.UUID(as_uuid=True)), sa.Column("linked_dataset_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("fulfillment_summary", sa.Text()), sa.Column("closure_actor_user_id", sa.String(180)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True)), sa.Column("cancelled_at", sa.DateTime(timezone=True)), sa.Column("reopened_at", sa.DateTime(timezone=True)),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"), sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(180)),
        sa.CheckConstraint("status IN ('Draft','Submitted','In Progress','Blocked','Closed','Cancelled')", name="ck_ticket_status"),
        sa.CheckConstraint("priority IN ('Low','Normal','High','Urgent')", name="ck_ticket_priority"),
        sa.CheckConstraint("record_count IS NULL OR record_count > 0", name="ck_ticket_record_count"),
        sa.CheckConstraint("data_date_to IS NULL OR data_date_from IS NULL OR data_date_to >= data_date_from", name="ck_ticket_date_range"),
        sa.CheckConstraint("test_window_end IS NULL OR test_window_start IS NULL OR test_window_end > test_window_start", name="ck_ticket_window"),
        sa.UniqueConstraint("requester_user_id", "idempotency_key", name="uq_ticket_request_idempotency"),
        sa.ForeignKeyConstraint(["linked_dataset_id"], ["dataops.datasets.id"], ondelete="RESTRICT", name="fk_ticket_dataset"),
        sa.ForeignKeyConstraint(["linked_dataset_id", "linked_dataset_version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="RESTRICT", name="fk_ticket_dataset_version"),
        schema="dataops",
    )
    for name, columns in [
        ("ix_ticket_workspace_status", ["workspace_id", "status"]), ("ix_ticket_requester_status", ["requester_user_id", "status"]),
        ("ix_ticket_assignee_status", ["assignee_user_id", "status"]), ("ix_ticket_submitted", ["submitted_at"]),
        ("ix_ticket_needed_by", ["needed_by"]), ("ix_ticket_closed", ["closed_at"]), ("ix_ticket_priority", ["priority"]),
        ("ix_ticket_dataset", ["linked_dataset_id"]), ("ix_ticket_project_status", ["workspace_id", "project_id", "status"]),
    ]:
        op.create_index(name, "test_data_tickets", columns, schema="dataops")
    op.create_table(
        "ticket_events", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.test_data_tickets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False), sa.Column("actor_user_id", sa.String(180)), sa.Column("actor_role", sa.String(32)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("from_status", sa.String(16)), sa.Column("to_status", sa.String(16)), sa.Column("reason", sa.Text()),
        sa.Column("event_data", postgresql.JSONB(), nullable=False, server_default="{}"), sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", sa.String(180)), sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("related_dataset_id", postgresql.UUID(as_uuid=True)), sa.Column("related_dataset_version_id", postgresql.UUID(as_uuid=True)), schema="dataops")
    op.create_index("ix_ticket_events_time", "ticket_events", ["ticket_id", "occurred_at"], schema="dataops")
    op.create_index("ix_ticket_events_scope", "ticket_events", ["workspace_id", "occurred_at"], schema="dataops")
    op.create_table(
        "ticket_comments", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.test_data_tickets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("author_user_id", sa.String(180), nullable=False), sa.Column("author_role", sa.String(32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("visibility_scope", sa.String(24), nullable=False, server_default="participants"), schema="dataops")
    op.create_index("ix_ticket_comments_time", "ticket_comments", ["ticket_id", "created_at"], schema="dataops")
    op.create_table(
        "ticket_block_periods", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.test_data_tickets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.Text(), nullable=False), sa.Column("requested_by_user_id", sa.String(180), nullable=False),
        sa.Column("resolved_by_user_id", sa.String(180)), schema="dataops")
    op.create_index("ix_ticket_blocks_ticket", "ticket_block_periods", ["ticket_id", "started_at"], schema="dataops")
    op.create_table(
        "ticket_notifications", sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.test_data_tickets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.String(180), sa.ForeignKey("iam.users.subject", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False), sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", sa.DateTime(timezone=True)), schema="dataops")
    op.create_index("ix_ticket_notifications_user", "ticket_notifications", ["user_id", "created_at"], schema="dataops")
    op.execute("""CREATE FUNCTION dataops.prevent_ticket_history_mutation() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'ticket history is append-only'; END; $$ LANGUAGE plpgsql""")
    for table in ("ticket_events", "ticket_comments"):
        op.execute(f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON dataops.{table} FOR EACH ROW EXECUTE FUNCTION dataops.prevent_ticket_history_mutation()")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_ticket_events_append_only ON dataops.ticket_events")
    op.execute("DROP TRIGGER IF EXISTS trg_ticket_comments_append_only ON dataops.ticket_comments")
    op.execute("DROP FUNCTION IF EXISTS dataops.prevent_ticket_history_mutation()")
    op.drop_table("ticket_block_periods", schema="dataops")
    op.drop_table("ticket_notifications", schema="dataops")
    op.drop_table("ticket_comments", schema="dataops")
    op.drop_table("ticket_events", schema="dataops")
    op.drop_table("test_data_tickets", schema="dataops")
    op.execute("DROP SEQUENCE IF EXISTS dataops.ticket_display_sequence")
