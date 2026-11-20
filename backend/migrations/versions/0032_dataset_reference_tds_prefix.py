"""Change human-readable data set references from DS- to TDS-."""

from alembic import op


revision = "0032_dataset_ref_tds"
down_revision = "0031_job_end_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE dataops.datasets "
        "SET reference_code = 'TDS-' || substring(reference_code FROM 4) "
        "WHERE reference_code LIKE 'DS-%'"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dataops.assign_dataset_reference_code()
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
                    'TDS-' || pathway_code || '-NS-'
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
        CREATE OR REPLACE FUNCTION dataops.finalize_dataset_reference_mode()
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
                WHERE id = NEW.dataset_id AND reference_code LIKE 'TDS-%-NS-%';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute(
        "UPDATE dataops.datasets "
        "SET reference_code = 'DS-' || substring(reference_code FROM 5) "
        "WHERE reference_code LIKE 'TDS-%'"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dataops.assign_dataset_reference_code()
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
        CREATE OR REPLACE FUNCTION dataops.finalize_dataset_reference_mode()
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
