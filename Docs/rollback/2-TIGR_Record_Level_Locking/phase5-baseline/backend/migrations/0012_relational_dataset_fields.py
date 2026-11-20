"""Normalize version field metadata and expose workspace-scoped data-set versions."""

import json
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_relational_dataset_fields"
down_revision = "0011_durable_worker_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_datasets_workspace_id", "datasets", ["workspace_id", "id"], schema="dataops"
    )
    op.add_column("dataset_versions", sa.Column("workspace_id", postgresql.UUID(as_uuid=True)), schema="dataops")
    op.add_column("dataset_versions", sa.Column("record_count", sa.BigInteger()), schema="dataops")
    op.create_check_constraint(
        "ck_dataset_versions_record_count", "dataset_versions",
        "record_count IS NULL OR record_count >= 0", schema="dataops",
    )
    op.execute(sa.text("""
        UPDATE dataops.dataset_versions AS version
        SET workspace_id = dataset.workspace_id,
            record_count = CASE
                WHEN version.validation_evidence ->> 'record_count' ~ '^[0-9]+$'
                THEN (version.validation_evidence ->> 'record_count')::bigint
                ELSE NULL
            END
        FROM dataops.datasets AS dataset
        WHERE dataset.id = version.dataset_id
    """))
    op.alter_column("dataset_versions", "workspace_id", nullable=False, schema="dataops")
    op.create_foreign_key(
        "fk_dataset_versions_workspace", "dataset_versions", "workspaces",
        ["workspace_id"], ["id"], source_schema="dataops", referent_schema="iam", ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_dataset_versions_workspace_dataset", "dataset_versions", "datasets",
        ["workspace_id", "dataset_id"], ["workspace_id", "id"],
        source_schema="dataops", referent_schema="dataops", ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_dataset_versions_scope_id", "dataset_versions",
        ["workspace_id", "dataset_id", "id"], schema="dataops",
    )
    op.create_table(
        "dataset_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(length=180), nullable=False),
        sa.Column("field_path", sa.String(length=500), nullable=False),
        sa.Column("data_type", sa.String(length=80), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_schema_field_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_field_path", sa.String(length=500), nullable=True),
        sa.Column("constraint_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_dataset_fields_ordinal"),
        sa.ForeignKeyConstraint(["workspace_id"], ["iam.workspaces.id"], name="fk_dataset_fields_workspace", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "dataset_id", "version_id"],
            ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"],
            name="fk_dataset_fields_scoped_version", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "version_id", "field_path", name="uq_dataset_fields_version_path"),
        schema="dataops",
    )

    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, dataset_id, workspace_id, schema_scope FROM dataops.dataset_versions"
    )).mappings().all()
    insert_field = sa.text("""
        INSERT INTO dataops.dataset_fields
            (id, workspace_id, dataset_id, version_id, field_name, field_path, data_type,
             required, target_field_path, constraint_spec, ordinal)
        VALUES
            (:id, :workspace_id, :dataset_id, :version_id, :field_name, :field_path, :data_type,
             :required, :target_field_path, CAST(:constraint_spec AS jsonb), :ordinal)
    """)
    for row in rows:
        used_paths: set[str] = set()
        for ordinal, field in enumerate(row["schema_scope"] or []):
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("field", "field"))[:180]
            field_path = str(field.get("json_path") or (
                f"{field['entity']}.{field_name}" if field.get("entity") else field_name
            ))[:500]
            if field_path in used_paths:
                field_path = f"{field_path}#{ordinal}"[:500]
            used_paths.add(field_path)
            connection.execute(insert_field, {
                "id": uuid4(), "workspace_id": row["workspace_id"], "dataset_id": row["dataset_id"],
                "version_id": row["id"], "field_name": field_name, "field_path": field_path,
                "data_type": str(field.get("data_type", "unknown"))[:80],
                "required": bool(field.get("required", False)), "target_field_path": field_path,
                "constraint_spec": json.dumps({}), "ordinal": ordinal,
            })


def downgrade() -> None:
    op.drop_table("dataset_fields", schema="dataops")
    op.drop_constraint("uq_dataset_versions_scope_id", "dataset_versions", schema="dataops", type_="unique")
    op.drop_constraint("fk_dataset_versions_workspace_dataset", "dataset_versions", schema="dataops", type_="foreignkey")
    op.drop_constraint("fk_dataset_versions_workspace", "dataset_versions", schema="dataops", type_="foreignkey")
    op.drop_column("dataset_versions", "record_count", schema="dataops")
    op.drop_column("dataset_versions", "workspace_id", schema="dataops")
    op.drop_constraint("uq_datasets_workspace_id", "datasets", schema="dataops", type_="unique")
