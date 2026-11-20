"""Allow a user to lock the same protected records through multiple datasets.

Revision ID: 0030_same_actor_claims
Revises: 0029_remove_banking_samples
"""

from alembic import op
import sqlalchemy as sa

revision = "0030_same_actor_claims"
down_revision = "0029_remove_banking_samples"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Cross-user exclusivity is enforced transactionally by the source namespace
    # advisory lock and the claim conflict query. A global unique index also
    # rejected a user's own claims from a second active dataset lock.
    op.drop_index("uq_entity_claim_active_identity", table_name="dataset_entity_claims", schema="dataops")
    op.create_index(
        "ix_entity_claim_active_identity",
        "dataset_entity_claims",
        ["source_namespace", "source_object", "token_version", "entity_token"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
        schema="dataops",
    )


def downgrade() -> None:
    duplicates = op.get_bind().execute(sa.text(
        "SELECT 1 FROM dataops.dataset_entity_claims "
        "WHERE status = 'active' "
        "GROUP BY source_namespace, source_object, token_version, entity_token "
        "HAVING count(*) > 1 LIMIT 1"
    )).first()
    if duplicates:
        raise RuntimeError("Release overlapping same-user locks before restoring unique active entity claims")
    op.drop_index("ix_entity_claim_active_identity", table_name="dataset_entity_claims", schema="dataops")
    op.create_index(
        "uq_entity_claim_active_identity",
        "dataset_entity_claims",
        ["source_namespace", "source_object", "token_version", "entity_token"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        schema="dataops",
    )
