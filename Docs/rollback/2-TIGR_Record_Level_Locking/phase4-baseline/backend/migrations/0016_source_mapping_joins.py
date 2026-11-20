"""Persist explicit source object joins for Source to Data mappings.

Revision ID: 0016_source_mapping_joins
Revises: 0015_banking_sources
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016_source_mapping_joins"
down_revision = "0015_banking_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_data_mappings",
        sa.Column("source_joins", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        schema="dataops",
    )


def downgrade() -> None:
    op.drop_column("source_data_mappings", "source_joins", schema="dataops")
