"""Add SSD metadata and connection operation grants without changing legacy access."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0042_self_servicing_data"
down_revision = "0041_oracle_mssql"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_access_grants_scope_type", "access_grants", schema="iam", type_="check")
    op.create_check_constraint(
        "ck_access_grants_scope_type",
        "access_grants",
        "scope_type IN ('workspace','dataset','reservation','lock','connection')",
        schema="iam",
    )
    op.drop_constraint("ck_datasets_pathway", "datasets", schema="dataops", type_="check")
    op.create_check_constraint(
        "ck_datasets_pathway",
        "datasets",
        "creation_pathway IN ('scenario_to_data','source_to_data','self_servicing_data')",
        schema="dataops",
    )
    op.add_column(
        "datasets", sa.Column("visibility", sa.String(16), nullable=True), schema="dataops"
    )
    op.create_check_constraint(
        "ck_dataset_visibility",
        "datasets",
        "visibility IS NULL OR visibility IN ('private','public')",
        schema="dataops",
    )
    op.create_table(
        "ssd_drafts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("iam.workspaces.id"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(180), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(48), nullable=False),
        sa.Column("dataset_id", pg.UUID(as_uuid=True), sa.ForeignKey("dataops.datasets.id")),
        sa.Column("generated_revision", sa.Integer()),
        sa.Column("dataset_version_number", sa.Integer()),
        sa.Column("generation_settings", pg.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="dataops",
    )
    op.create_index("ix_dataops_ssd_drafts_workspace_id", "ssd_drafts", ["workspace_id"], schema="dataops")
    op.create_index("ix_dataops_ssd_drafts_owner_id", "ssd_drafts", ["owner_id"], schema="dataops")
    op.create_table(
        "ssd_revisions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "draft_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("dataops.ssd_drafts.id"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column(
            "connection_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("dataops.workspace_connections.id"),
            nullable=False,
        ),
        sa.Column("database", sa.String(128), nullable=False),
        sa.Column("secured_inputs", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("validation", pg.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("draft_id", "number"),
        schema="dataops",
    )
    op.create_table(
        "ssd_executions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "revision_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("dataops.ssd_revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("dataops.jobs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("parent_id", pg.UUID(as_uuid=True), sa.ForeignKey("dataops.ssd_executions.id")),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column("evidence", pg.JSONB(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("secured_output", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("revision_id", "kind", "idempotency_key"),
        schema="dataops",
    )


    op.execute("""
        CREATE FUNCTION dataops.prevent_ssd_revision_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'SSD query revisions are retained';
            END IF;
            IF (to_jsonb(OLD) - 'validation') IS DISTINCT FROM (to_jsonb(NEW) - 'validation') THEN
                RAISE EXCEPTION 'SSD query revision inputs are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_ssd_revision_immutable BEFORE UPDATE OR DELETE ON dataops.ssd_revisions
        FOR EACH ROW EXECUTE FUNCTION dataops.prevent_ssd_revision_mutation();
    """)
    op.create_index("ix_dataops_ssd_executions_expires_at", "ssd_executions", ["expires_at"], schema="dataops")


def downgrade():
    # Never drop completed mutation evidence as part of an application rollback.
    raise RuntimeError(
        "SSD history is retained. Disable new SSD execution and roll back application code; use a reviewed archival migration to remove evidence."
    )
