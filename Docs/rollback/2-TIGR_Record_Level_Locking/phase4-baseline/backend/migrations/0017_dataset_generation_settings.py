"""Persist dataset creation settings for draft defaults.

Revision ID: 0017_dataset_generation_settings
Revises: 0016_source_mapping_joins
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017_dataset_generation_settings"
down_revision = "0016_source_mapping_joins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dataset_versions",
        sa.Column("generation_settings", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        schema="dataops",
    )


def downgrade() -> None:
    op.drop_column("dataset_versions", "generation_settings", schema="dataops")
