"""Add atomic protected entity claims tied to version lock leases."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_entity_claim_registry"
down_revision = "0025_protected_record_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_entity_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lock_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", sa.String(180), nullable=False),
        sa.Column("source_namespace", sa.String(300), nullable=False),
        sa.Column("source_object", sa.String(300), nullable=False),
        sa.Column("key_definition", postgresql.JSONB(), nullable=False),
        sa.Column("token_version", sa.String(40), nullable=False),
        sa.Column("entity_token", sa.String(80), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("release_reason", sa.Text()),
        sa.CheckConstraint("status IN ('active', 'released', 'expired', 'force_released')", name="ck_entity_claim_status"),
        sa.CheckConstraint("fencing_token > 0", name="ck_entity_claim_fencing_token"),
        sa.ForeignKeyConstraint(["workspace_id"], ["iam.workspaces.id"], name="fk_entity_claim_workspace", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id", "version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], name="fk_entity_claim_dataset_version", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lock_id"], ["dataops.dataset_locks.id"], name="fk_entity_claim_lock", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"), schema="dataops",
    )
    op.create_index("uq_entity_claim_active_identity", "dataset_entity_claims",
                    ["source_namespace", "source_object", "token_version", "entity_token"], unique=True,
                    postgresql_where=sa.text("status = 'active'"), schema="dataops")
    op.create_index("ix_entity_claim_active_owner", "dataset_entity_claims",
                    ["workspace_id", "actor_id", "expires_at"], postgresql_where=sa.text("status = 'active'"), schema="dataops")
    op.create_index("ix_entity_claim_dataset", "dataset_entity_claims", ["dataset_id", "version_id", "status"], schema="dataops")


def downgrade() -> None:
    op.drop_index("ix_entity_claim_dataset", table_name="dataset_entity_claims", schema="dataops")
    op.drop_index("ix_entity_claim_active_owner", table_name="dataset_entity_claims", schema="dataops")
    op.drop_index("uq_entity_claim_active_identity", table_name="dataset_entity_claims", schema="dataops")
    op.drop_table("dataset_entity_claims", schema="dataops")
