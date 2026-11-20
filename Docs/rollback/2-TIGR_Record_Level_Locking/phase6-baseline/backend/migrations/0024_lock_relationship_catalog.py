"""Persist immutable PostgreSQL structure snapshots and TDE lock paths."""

import json
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_lock_relationship_catalog"
down_revision = "0023_dataset_reference_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connection_structure_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("structure", postgresql.JSONB(), nullable=False),
        sa.Column("captured_by", sa.String(180), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version_number > 0", name="ck_structure_snapshot_version_positive"),
        sa.ForeignKeyConstraint(["workspace_id"], ["iam.workspaces.id"], name="fk_structure_snapshot_workspace", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["dataops.workspace_connections.workspace_id", "dataops.workspace_connections.id"],
            name="fk_structure_snapshot_connection", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "version_number", name="uq_structure_snapshot_version"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_structure_snapshot_workspace_id"),
        schema="dataops",
    )
    op.create_index(
        "ix_structure_snapshots_latest", "connection_structure_snapshots",
        ["workspace_id", "connection_id", "version_number"], schema="dataops",
    )

    op.create_table(
        "lock_propagation_configurations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("structure_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), server_default="approved", nullable=False),
        sa.Column("created_by", sa.String(180), nullable=False),
        sa.Column("approved_by", sa.String(180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version_number > 0", name="ck_lock_config_version_positive"),
        sa.CheckConstraint("status IN ('approved', 'revoked')", name="ck_lock_config_status"),
        sa.ForeignKeyConstraint(["workspace_id"], ["iam.workspaces.id"], name="fk_lock_config_workspace", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["dataops.workspace_connections.workspace_id", "dataops.workspace_connections.id"],
            name="fk_lock_config_connection", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "structure_snapshot_id"],
            ["dataops.connection_structure_snapshots.workspace_id", "dataops.connection_structure_snapshots.id"],
            name="fk_lock_config_structure_snapshot", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "version_number", name="uq_lock_config_version"),
        schema="dataops",
    )
    op.create_index(
        "ix_lock_configurations_latest", "lock_propagation_configurations",
        ["workspace_id", "connection_id", "version_number"], schema="dataops",
    )

    connection = op.get_bind()
    existing = connection.execute(sa.text("""
        SELECT id, workspace_id, structure_preview, tested_by, COALESCE(tested_at, CURRENT_TIMESTAMP) AS tested_at
        FROM dataops.workspace_connections
        WHERE connector_type = 'postgresql'
          AND test_result @> '{"succeeded": true}'::jsonb
          AND structure_preview IS NOT NULL
    """)).mappings().all()
    for row in existing:
        snapshot = dict(row["structure_preview"] or {})
        snapshot.setdefault("format", "postgresql")
        snapshot["structure_snapshot_version"] = 1
        snapshot["source_identity_namespace"] = f"connection:{row['id']}"
        snapshot["legacy_metadata_snapshot"] = True
        connection.execute(sa.text("""
            INSERT INTO dataops.connection_structure_snapshots
                (id, workspace_id, connection_id, version_number, structure, captured_by, captured_at)
            VALUES (:id, :workspace_id, :connection_id, 1, CAST(:structure AS jsonb), :captured_by, :captured_at)
        """), {
            "id": uuid4(), "workspace_id": row["workspace_id"], "connection_id": row["id"],
            "structure": json.dumps(snapshot), "captured_by": row["tested_by"] or "migration-0024",
            "captured_at": row["tested_at"],
        })


def downgrade() -> None:
    op.drop_index("ix_lock_configurations_latest", table_name="lock_propagation_configurations", schema="dataops")
    op.drop_table("lock_propagation_configurations", schema="dataops")
    op.drop_index("ix_structure_snapshots_latest", table_name="connection_structure_snapshots", schema="dataops")
    op.drop_table("connection_structure_snapshots", schema="dataops")
