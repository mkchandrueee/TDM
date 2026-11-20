"""Store requested existing dataset and required-until date on tickets."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_ticket_requested_dataset"
down_revision = "0034_ticket_on_hold_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("test_data_tickets", sa.Column("required_until", sa.Date(), nullable=True), schema="dataops")
    op.add_column("test_data_tickets", sa.Column("requested_dataset_id", postgresql.UUID(as_uuid=True), nullable=True), schema="dataops")
    op.create_foreign_key(
        "fk_ticket_requested_dataset",
        "test_data_tickets",
        "datasets",
        ["requested_dataset_id"],
        ["id"],
        source_schema="dataops",
        referent_schema="dataops",
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_ticket_requested_dataset", "test_data_tickets", schema="dataops", type_="foreignkey")
    op.drop_column("test_data_tickets", "requested_dataset_id", schema="dataops")
    op.drop_column("test_data_tickets", "required_until", schema="dataops")
