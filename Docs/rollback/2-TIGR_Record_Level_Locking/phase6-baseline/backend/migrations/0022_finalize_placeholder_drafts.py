"""Allow one-time population of empty Create Draft manifests.

Revision ID: 0022_finalize_placeholder_drafts
Revises: 0021_post_mask_records
"""

from alembic import op

revision = "0022_finalize_placeholder_drafts"
down_revision = "0021_post_mask_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dataops.prevent_dataset_version_manifest_mutation()
        RETURNS trigger AS $$
        DECLARE
            manifest_changed boolean;
            placeholder_finalization boolean;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'data set versions cannot be deleted';
            END IF;

            IF OLD.dataset_id IS DISTINCT FROM NEW.dataset_id
                OR OLD.version_number IS DISTINCT FROM NEW.version_number
                OR OLD.created_by IS DISTINCT FROM NEW.created_by
                OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
                RAISE EXCEPTION 'data set version manifests are immutable';
            END IF;

            manifest_changed := OLD.schema_scope IS DISTINCT FROM NEW.schema_scope
                OR OLD.selected_fields IS DISTINCT FROM NEW.selected_fields
                OR OLD.provenance IS DISTINCT FROM NEW.provenance
                OR OLD.permitted_use IS DISTINCT FROM NEW.permitted_use;

            IF manifest_changed THEN
                placeholder_finalization := OLD.lifecycle_state = 'draft'
                    AND OLD.provenance ->> 'origin' = 'not_set'
                    AND OLD.schema_scope = '[]'::jsonb
                    AND OLD.selected_fields = '[]'::jsonb
                    AND OLD.record_count IS NULL
                    AND OLD.validation_evidence = '{}'::jsonb
                    AND NEW.lifecycle_state = 'draft'
                    AND NEW.provenance ->> 'origin' IN ('source_backed', 'synthetic')
                    AND jsonb_typeof(NEW.schema_scope) = 'array'
                    AND jsonb_array_length(NEW.schema_scope) > 0
                    AND jsonb_typeof(NEW.selected_fields) = 'array'
                    AND jsonb_array_length(NEW.selected_fields) > 0
                    AND NEW.record_count IS NULL
                    AND NEW.validation_evidence = '{}'::jsonb;

                IF NOT placeholder_finalization THEN
                    RAISE EXCEPTION 'data set version manifests are immutable';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION dataops.prevent_dataset_version_manifest_mutation()
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
        """
    )
