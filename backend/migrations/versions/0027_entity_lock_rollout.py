"""Add default-off per-connection entity-lock rollout control."""

import sqlalchemy as sa
from alembic import op

revision = "0027_entity_lock_rollout"
down_revision = "0026_entity_claim_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_connections",
        sa.Column("entity_locking_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema="dataops",
    )


def downgrade() -> None:
    op.drop_column("workspace_connections", "entity_locking_enabled", schema="dataops")
