"""Add a workspace-level PII discovery and masking switch."""
import sqlalchemy as sa
from alembic import op

revision = "0037_workspace_pii_setting"
down_revision = "0036_ticket_priority_choices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("pii_masking_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="iam",
    )


def downgrade() -> None:
    op.drop_column("workspaces", "pii_masking_enabled", schema="iam")
