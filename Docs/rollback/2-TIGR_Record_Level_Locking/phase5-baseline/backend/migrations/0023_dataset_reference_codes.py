"""Add human-readable data set reference codes.

Revision ID: 0023_dataset_reference_codes
Revises: 0022_finalize_placeholder_drafts
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_dataset_reference_codes"
down_revision = "0022_finalize_placeholder_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE dataops.dataset_reference_sequence START WITH 1")
    op.add_column("datasets", sa.Column("reference_code", sa.String(length=64), nullable=True), schema="dataops")
    op.execute(
        """
        WITH latest_versions AS (
            SELECT DISTINCT ON (dataset_id)
                dataset_id,
                provenance,
                version_number
            FROM dataops.dataset_versions
            ORDER BY dataset_id, version_number DESC
        ), plan_modes AS (
            SELECT DISTINCT ON (dataset_id) dataset_id, generation_mode
            FROM (
                SELECT
                    sp.dataset_id,
                    sp.generation_mode,
                    sp.updated_at
                FROM dataops.scenario_plans sp
                WHERE sp.dataset_id IS NOT NULL
                UNION ALL
                SELECT
                    (sp.logical_plan->'draft_data'->>'draft_dataset_id')::uuid AS dataset_id,
                    sp.generation_mode,
                    sp.updated_at
                FROM dataops.scenario_plans sp
                WHERE sp.logical_plan->'draft_data'->>'draft_dataset_id' IS NOT NULL
            ) linked
            ORDER BY dataset_id, updated_at DESC
        ), codes AS (
            SELECT
                d.id,
                d.created_at,
                CASE d.creation_pathway
                    WHEN 'source_to_data' THEN 'SRC'
                    ELSE 'SCN'
                END AS pathway_code,
                CASE
                    WHEN lv.provenance->>'origin' = 'synthetic' THEN 'SY'
                    WHEN lv.provenance->>'origin' = 'source_backed' THEN 'SB'
                    WHEN d.creation_pathway = 'source_to_data' THEN 'SB'
                    WHEN pm.generation_mode = 'synthetic' THEN 'SY'
                    WHEN pm.generation_mode = 'source_backed' THEN 'SB'
                    ELSE 'NS'
                END AS mode_code
            FROM dataops.datasets d
            LEFT JOIN latest_versions lv ON lv.dataset_id = d.id
            LEFT JOIN plan_modes pm ON pm.dataset_id = d.id
        ), numbered AS (
            SELECT
                id,
                created_at,
                pathway_code,
                mode_code,
                row_number() OVER (ORDER BY created_at, id) AS sequence_number
            FROM codes
        )
        UPDATE dataops.datasets d
        SET reference_code =
            'DS-' || n.pathway_code || '-' || n.mode_code || '-'
            || to_char(n.created_at AT TIME ZONE 'UTC', 'YYMMDD"T"HH24MISS')
            || '-' || lpad(n.sequence_number::text, 5, '0')
        FROM numbered n
        WHERE d.id = n.id
        """
    )
    op.alter_column("datasets", "reference_code", nullable=False, schema="dataops")
    op.create_unique_constraint("uq_datasets_reference_code", "datasets", ["reference_code"], schema="dataops")
    op.execute(
        """
        SELECT setval(
            'dataops.dataset_reference_sequence',
            GREATEST((SELECT count(*) FROM dataops.datasets), 1),
            (SELECT count(*) > 0 FROM dataops.datasets)
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION dataops.assign_dataset_reference_code()
        RETURNS trigger AS $$
        DECLARE
            pathway_code text;
        BEGIN
            IF NEW.reference_code IS NULL THEN
                pathway_code := CASE NEW.creation_pathway
                    WHEN 'source_to_data' THEN 'SRC'
                    ELSE 'SCN'
                END;
                NEW.reference_code :=
                    'DS-' || pathway_code || '-NS-'
                    || to_char(COALESCE(NEW.created_at, now()) AT TIME ZONE 'UTC', 'YYMMDD"T"HH24MISS')
                    || '-' || lpad(nextval('dataops.dataset_reference_sequence')::text, 5, '0');
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_assign_dataset_reference_code
        BEFORE INSERT ON dataops.datasets
        FOR EACH ROW EXECUTE FUNCTION dataops.assign_dataset_reference_code()
        """
    )
    op.execute(
        """
        CREATE FUNCTION dataops.finalize_dataset_reference_mode()
        RETURNS trigger AS $$
        DECLARE
            mode_code text;
        BEGIN
            IF NEW.provenance->>'origin' IN ('synthetic', 'source_backed') THEN
                mode_code := CASE NEW.provenance->>'origin'
                    WHEN 'synthetic' THEN 'SY'
                    ELSE 'SB'
                END;
                UPDATE dataops.datasets
                SET reference_code = replace(reference_code, '-NS-', '-' || mode_code || '-')
                WHERE id = NEW.dataset_id AND reference_code LIKE 'DS-%-NS-%';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_finalize_dataset_reference_mode
        AFTER INSERT OR UPDATE OF provenance ON dataops.dataset_versions
        FOR EACH ROW EXECUTE FUNCTION dataops.finalize_dataset_reference_mode()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_finalize_dataset_reference_mode ON dataops.dataset_versions")
    op.execute("DROP FUNCTION IF EXISTS dataops.finalize_dataset_reference_mode()")
    op.execute("DROP TRIGGER IF EXISTS trg_assign_dataset_reference_code ON dataops.datasets")
    op.execute("DROP FUNCTION IF EXISTS dataops.assign_dataset_reference_code()")
    op.drop_constraint("uq_datasets_reference_code", "datasets", schema="dataops", type_="unique")
    op.drop_column("datasets", "reference_code", schema="dataops")
    op.execute("DROP SEQUENCE IF EXISTS dataops.dataset_reference_sequence")
