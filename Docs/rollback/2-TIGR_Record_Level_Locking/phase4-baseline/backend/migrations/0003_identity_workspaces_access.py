"""Identity, workspaces, scoped access, and audit role context."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_identity_workspaces_access"
down_revision = "0002_job_workspace_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS iam")
    op.create_table(
        "users",
        sa.Column("subject", sa.String(180), primary_key=True),
        sa.Column("email", sa.String(320)),
        sa.Column("display_name", sa.String(240)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="iam",
    )
    op.create_table(
        "platform_admins",
        sa.Column("subject", sa.String(180), sa.ForeignKey("iam.users.subject", ondelete="CASCADE"), primary_key=True),
        sa.Column("granted_by", sa.String(180), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="iam",
    )
    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("purpose", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_workspaces_slug"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_workspaces_status"),
        schema="iam",
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("subject", sa.String(180), sa.ForeignKey("iam.users.subject", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("granted_by", sa.String(180), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by", sa.String(180)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('Test Data Engineer', 'QA Engineer', 'Admin')", name="ck_membership_human_role"),
        sa.CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="ck_membership_valid_period"),
        schema="iam",
    )
    op.create_index("ix_workspace_memberships_subject", "workspace_memberships", ["subject", "workspace_id"], schema="iam")
    op.create_table(
        "access_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject", sa.String(180), sa.ForeignKey("iam.users.subject", ondelete="CASCADE"), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_id", sa.String(180)),
        sa.Column("permissions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("granted_by", sa.String(180), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by", sa.String(180)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("scope_type IN ('workspace', 'dataset', 'reservation', 'lock')", name="ck_access_grants_scope_type"),
        sa.CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="ck_access_grants_valid_period"),
        schema="iam",
    )
    op.create_index("ix_access_grants_scope", "access_grants", ["subject", "workspace_id", "scope_type", "scope_id"], schema="iam")
    op.create_table(
        "service_identities",
        sa.Column("subject", sa.String(180), primary_key=True),
        sa.Column("display_name", sa.String(180), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="iam",
    )
    op.add_column("audit_events", sa.Column("actor_role", sa.String(32)), schema="audit")
    op.create_index("ix_audit_workspace_time", "audit_events", ["workspace_id", "occurred_at"], schema="audit")
    op.create_index("ix_audit_actor_time", "audit_events", ["actor_id", "occurred_at"], schema="audit")
    op.execute("""
        CREATE FUNCTION audit.prevent_audit_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit events are append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit.audit_events
        FOR EACH ROW EXECUTE FUNCTION audit.prevent_audit_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit.audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit.prevent_audit_mutation()")
    op.drop_index("ix_audit_actor_time", table_name="audit_events", schema="audit")
    op.drop_index("ix_audit_workspace_time", table_name="audit_events", schema="audit")
    op.drop_column("audit_events", "actor_role", schema="audit")
    for table in ("service_identities", "access_grants", "workspace_memberships", "workspaces", "platform_admins", "users"):
        op.drop_table(table, schema="iam")
    op.execute("DROP SCHEMA IF EXISTS iam")
