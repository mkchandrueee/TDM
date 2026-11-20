"""Store generated dataset values and short-lived exports in PostgreSQL; add approval requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_pg_records_requests"
down_revision = "0013_schema_design_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("values", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id", "dataset_id", "version_id"], ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="CASCADE", name="fk_dataset_records_version"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "ordinal", name="uq_dataset_records_version_ordinal"),
        schema="dataops",
    )
    op.create_index("ix_dataset_records_version", "dataset_records", ["version_id", "ordinal"], schema="dataops")
    op.create_table(
        "data_use_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_id", sa.String(180), nullable=False),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("decided_by", sa.String(180)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_comment", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name="ck_data_use_request_window"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'denied', 'cancelled')", name="ck_data_use_request_status"),
        sa.ForeignKeyConstraint(["workspace_id"], ["iam.workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id", "version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="CASCADE", name="fk_data_use_request_dataset_version"),
        sa.PrimaryKeyConstraint("id"),
        schema="dataops",
    )
    op.create_index("ix_data_use_requests_workspace_status", "data_use_requests", ["workspace_id", "status", "created_at"], schema="dataops")
    op.add_column("dataset_reservations", sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True), schema="dataops")
    op.create_foreign_key("fk_reservation_data_use_request", "dataset_reservations", "data_use_requests", ["request_id"], ["id"], source_schema="dataops", referent_schema="dataops", ondelete="RESTRICT")
    op.alter_column("export_artifacts", "file_path", new_column_name="content", existing_type=sa.String(1000), type_=sa.Text(), schema="dataops")


def downgrade() -> None:
    op.alter_column("export_artifacts", "content", new_column_name="file_path", existing_type=sa.Text(), type_=sa.String(1000), schema="dataops")
    op.drop_constraint("fk_reservation_data_use_request", "dataset_reservations", schema="dataops", type_="foreignkey")
    op.drop_column("dataset_reservations", "request_id", schema="dataops")
    op.drop_index("ix_data_use_requests_workspace_status", table_name="data_use_requests", schema="dataops")
    op.drop_table("data_use_requests", schema="dataops")
    op.drop_index("ix_dataset_records_version", table_name="dataset_records", schema="dataops")
    op.drop_table("dataset_records", schema="dataops")
