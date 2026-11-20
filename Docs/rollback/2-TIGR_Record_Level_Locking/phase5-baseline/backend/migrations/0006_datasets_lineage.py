"""Add versioned data sets and immutable lineage."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_datasets_lineage"
down_revision = "0005_workspace_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("owner_id", sa.String(180), nullable=False),
        sa.Column("creation_pathway", sa.String(32), nullable=False),
        sa.Column("latest_version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("creation_pathway IN ('scenario_to_data', 'source_to_data')", name="ck_datasets_pathway"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_datasets_id_workspace"),
        schema="dataops",
    )
    op.create_index("ix_datasets_workspace_updated", "datasets", ["workspace_id", "updated_at"], schema="dataops")

    op.create_table(
        "dataset_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("lifecycle_state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("schema_scope", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("selected_fields", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("privacy_state", sa.String(24), nullable=False, server_default="not_assessed"),
        sa.Column("validation_state", sa.String(16), nullable=False, server_default="not_run"),
        sa.Column("publication_state", sa.String(16), nullable=False, server_default="unpublished"),
        sa.Column("expiry_at", sa.DateTime(timezone=True)),
        sa.Column("permitted_use", postgresql.JSONB(), nullable=False, server_default='["testing"]'),
        sa.Column("validation_evidence", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.String(180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("dataset_id", "version_number", name="uq_dataset_versions_number"),
        sa.UniqueConstraint("dataset_id", "id", name="uq_dataset_versions_dataset_id"),
        sa.CheckConstraint("version_number > 0", name="ck_dataset_versions_number_positive"),
        sa.CheckConstraint("lifecycle_state IN ('draft', 'ready', 'published', 'failed', 'quarantined', 'expired')", name="ck_dataset_versions_lifecycle"),
        sa.CheckConstraint("privacy_state IN ('not_assessed', 'compliant', 'requires_masking', 'blocked')", name="ck_dataset_versions_privacy"),
        sa.CheckConstraint("validation_state IN ('not_run', 'passed', 'failed')", name="ck_dataset_versions_validation"),
        sa.CheckConstraint("publication_state IN ('unpublished', 'published', 'withdrawn')", name="ck_dataset_versions_publication"),
        sa.CheckConstraint("expiry_at IS NULL OR expiry_at > created_at", name="ck_dataset_versions_expiry_after_creation"),
        schema="dataops",
    )
    op.create_index("ix_dataset_versions_dataset_state", "dataset_versions", ["dataset_id", "lifecycle_state", "version_number"], schema="dataops")

    op.create_table(
        "dataset_lineage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("stage", sa.String(80), nullable=False),
        sa.Column("source_reference", sa.String(500)),
        sa.Column("artifact_reference", sa.String(500)),
        sa.Column("mapping_version", sa.String(180)),
        sa.Column("scenario_version", sa.String(180)),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dataops.jobs.id", ondelete="SET NULL")),
        sa.Column("policy_version", sa.String(180)),
        sa.Column("validation_version", sa.String(180)),
        sa.Column("actor_id", sa.String(180), nullable=False),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="succeeded"),
        sa.Column("safe_evidence", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["dataset_id", "version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="RESTRICT", name="fk_lineage_dataset_version"),
        sa.UniqueConstraint("dataset_id", "id", name="uq_dataset_lineage_dataset_id"),
        sa.UniqueConstraint("dataset_id", "sequence_number", name="uq_dataset_lineage_sequence"),
        sa.CheckConstraint("sequence_number > 0", name="ck_dataset_lineage_sequence_positive"),
        sa.CheckConstraint("outcome IN ('succeeded', 'failed', 'warning')", name="ck_dataset_lineage_outcome"),
        schema="dataops",
    )
    op.create_index("ix_dataset_lineage_version_sequence", "dataset_lineage_events", ["dataset_id", "version_id", "sequence_number"], schema="dataops")

    op.create_table(
        "dataset_lineage_edges",
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id", "parent_event_id"], ["dataops.dataset_lineage_events.dataset_id", "dataops.dataset_lineage_events.id"], ondelete="RESTRICT", name="fk_lineage_edge_parent_same_dataset"),
        sa.ForeignKeyConstraint(["dataset_id", "child_event_id"], ["dataops.dataset_lineage_events.dataset_id", "dataops.dataset_lineage_events.id"], ondelete="RESTRICT", name="fk_lineage_edge_child_same_dataset"),
        sa.CheckConstraint("parent_event_id <> child_event_id", name="ck_dataset_lineage_edge_not_self"),
        schema="dataops",
    )
    op.create_index("ix_dataset_lineage_edges_child", "dataset_lineage_edges", ["child_event_id"], schema="dataops")

    op.execute("""
        CREATE FUNCTION dataops.prevent_dataset_version_manifest_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'data set versions cannot be deleted';
            END IF;
            IF OLD.dataset_id IS DISTINCT FROM NEW.dataset_id
                OR OLD.version_number IS DISTINCT FROM NEW.version_number
                OR OLD.schema_scope IS DISTINCT FROM NEW.schema_scope
                OR OLD.selected_fields IS DISTINCT FROM NEW.selected_fields
                OR OLD.provenance IS DISTINCT FROM NEW.provenance
                OR OLD.permitted_use IS DISTINCT FROM NEW.permitted_use
                OR OLD.created_by IS DISTINCT FROM NEW.created_by
                OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
                RAISE EXCEPTION 'data set version manifests are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_dataset_version_manifest_immutable
        BEFORE UPDATE OR DELETE ON dataops.dataset_versions
        FOR EACH ROW EXECUTE FUNCTION dataops.prevent_dataset_version_manifest_mutation()
    """)
    op.execute("""
        CREATE FUNCTION dataops.prevent_dataset_lineage_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'data set lineage is append-only';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_dataset_lineage_events_append_only
        BEFORE UPDATE OR DELETE ON dataops.dataset_lineage_events
        FOR EACH ROW EXECUTE FUNCTION dataops.prevent_dataset_lineage_mutation()
    """)
    op.execute("""
        CREATE TRIGGER trg_dataset_lineage_edges_append_only
        BEFORE UPDATE OR DELETE ON dataops.dataset_lineage_edges
        FOR EACH ROW EXECUTE FUNCTION dataops.prevent_dataset_lineage_mutation()
    """)
    op.execute("""
        CREATE FUNCTION dataops.validate_dataset_lineage_edge_order()
        RETURNS trigger AS $$
        DECLARE parent_sequence integer;
        DECLARE child_sequence integer;
        BEGIN
            SELECT sequence_number INTO parent_sequence FROM dataops.dataset_lineage_events
              WHERE id = NEW.parent_event_id AND dataset_id = NEW.dataset_id;
            SELECT sequence_number INTO child_sequence FROM dataops.dataset_lineage_events
              WHERE id = NEW.child_event_id AND dataset_id = NEW.dataset_id;
            IF parent_sequence IS NULL OR child_sequence IS NULL OR parent_sequence >= child_sequence THEN
                RAISE EXCEPTION 'lineage parent must precede child in the same data set';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_dataset_lineage_edge_order
        BEFORE INSERT ON dataops.dataset_lineage_edges
        FOR EACH ROW EXECUTE FUNCTION dataops.validate_dataset_lineage_edge_order()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_dataset_lineage_edges_append_only ON dataops.dataset_lineage_edges")
    op.execute("DROP TRIGGER IF EXISTS trg_dataset_lineage_edge_order ON dataops.dataset_lineage_edges")
    op.execute("DROP TRIGGER IF EXISTS trg_dataset_lineage_events_append_only ON dataops.dataset_lineage_events")
    op.execute("DROP TRIGGER IF EXISTS trg_dataset_version_manifest_immutable ON dataops.dataset_versions")
    op.execute("DROP FUNCTION IF EXISTS dataops.prevent_dataset_lineage_mutation()")
    op.execute("DROP FUNCTION IF EXISTS dataops.validate_dataset_lineage_edge_order()")
    op.execute("DROP FUNCTION IF EXISTS dataops.prevent_dataset_version_manifest_mutation()")
    op.drop_index("ix_dataset_lineage_edges_child", table_name="dataset_lineage_edges", schema="dataops")
    op.drop_table("dataset_lineage_edges", schema="dataops")
    op.drop_index("ix_dataset_lineage_version_sequence", table_name="dataset_lineage_events", schema="dataops")
    op.drop_table("dataset_lineage_events", schema="dataops")
    op.drop_index("ix_dataset_versions_dataset_state", table_name="dataset_versions", schema="dataops")
    op.drop_table("dataset_versions", schema="dataops")
    op.drop_index("ix_datasets_workspace_updated", table_name="datasets", schema="dataops")
    op.drop_table("datasets", schema="dataops")
