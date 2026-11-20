"""Persist LLM-recommended masking transformations for PII findings.

Revision ID: 0020_pii_field_recommendations
Revises: 0019_source_data_plans
"""

from alembic import op
import sqlalchemy as sa

revision = "0020_pii_field_recommendations"
down_revision = "0019_source_data_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pii_findings", sa.Column("suggested_transformation", sa.String(length=32), nullable=True), schema="dataops")


def downgrade() -> None:
    op.drop_column("pii_findings", "suggested_transformation", schema="dataops")
