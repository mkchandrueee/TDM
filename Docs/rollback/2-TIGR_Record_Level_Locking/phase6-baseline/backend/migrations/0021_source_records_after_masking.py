"""Remove pre-mask source-backed record values from PostgreSQL.

Revision ID: 0021_post_mask_records
Revises: 0020_pii_field_recommendations
"""

from alembic import op

revision = "0021_post_mask_records"
down_revision = "0020_pii_field_recommendations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only versions with a recorded successful masking run may retain rows.
    # Source rows can be re-extracted from their saved mapping/query/scenario
    # when the user runs masking, so deleting unmasked staging rows is safe.
    op.execute(
        """
        DELETE FROM dataops.dataset_records AS records
        USING dataops.dataset_versions AS versions
        WHERE records.version_id = versions.id
          AND versions.provenance ->> 'origin' = 'source_backed'
          AND COALESCE(versions.validation_evidence ->> 'masking_run_id', '') = ''
        """
    )
    op.execute(
        """
        UPDATE dataops.artifact_records AS artifacts
        SET status = 'deleted', deleted_at = CURRENT_TIMESTAMP
        FROM dataops.dataset_versions AS versions
        WHERE artifacts.version_id = versions.id
          AND versions.provenance ->> 'origin' = 'source_backed'
          AND COALESCE(versions.validation_evidence ->> 'masking_run_id', '') = ''
          AND artifacts.status = 'available'
        """
    )


def downgrade() -> None:
    # Deleted source values cannot be reconstructed safely from this database.
    pass
