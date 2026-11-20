"""Add protected per-record entity lineage."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025_protected_record_lineage"
down_revision = "0024_lock_relationship_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_record_entity_lineage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_namespace", sa.String(300), nullable=False),
        sa.Column("source_object", sa.String(300), nullable=False),
        sa.Column("key_definition", postgresql.JSONB(), nullable=False),
        sa.Column("token_version", sa.String(40), nullable=False),
        sa.Column("entity_token", sa.String(80), nullable=False),
        sa.Column("structure_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lock_configuration_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("relationship_path", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_ordinal", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("ordinal >= 0", name="ck_record_entity_lineage_ordinal"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "dataset_id", "version_id"],
            ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"],
            name="fk_record_entity_lineage_version", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="dataops",
    )
    op.create_index("ix_record_entity_lineage_token", "dataset_record_entity_lineage",
                    ["source_namespace", "source_object", "token_version", "entity_token"], schema="dataops")
    op.create_index("ix_record_entity_lineage_version", "dataset_record_entity_lineage",
                    ["workspace_id", "dataset_id", "version_id", "ordinal"], schema="dataops")


def downgrade() -> None:
    op.drop_index("ix_record_entity_lineage_version", table_name="dataset_record_entity_lineage", schema="dataops")
    op.drop_index("ix_record_entity_lineage_token", table_name="dataset_record_entity_lineage", schema="dataops")
    op.drop_table("dataset_record_entity_lineage", schema="dataops")
