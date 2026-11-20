"""Remove the built-in banking sample source tables.

Revision ID: 0029_remove_banking_samples
Revises: 0028_job_heartbeats
"""

from alembic import op

revision = "0029_remove_banking_samples"
down_revision = "0028_job_heartbeats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # These tables were created only to host TIGR's bundled banking demo data.
    # Keep this scoped to the four tables introduced by migration 0015.
    op.execute("DROP TABLE IF EXISTS source_data.loans CASCADE")
    op.execute("DROP TABLE IF EXISTS source_data.accounts CASCADE")
    op.execute("DROP TABLE IF EXISTS source_data.customers CASCADE")
    op.execute("DROP TABLE IF EXISTS source_data.branches CASCADE")
    op.execute("DROP SCHEMA IF EXISTS source_data")


def downgrade() -> None:
    # The sample schema and its data are intentionally not restored.
    pass
