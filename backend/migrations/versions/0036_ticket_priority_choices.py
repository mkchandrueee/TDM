"""Restrict ticket priority to Urgent, Normal, and Low."""
from alembic import op

revision = "0036_ticket_priority_choices"
down_revision = "0035_ticket_requested_dataset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Normalize legacy High tickets before tightening the database constraint.
    op.execute("UPDATE dataops.test_data_tickets SET priority = 'Normal' WHERE priority = 'High'")
    op.drop_constraint("ck_ticket_priority", "test_data_tickets", schema="dataops", type_="check")
    op.create_check_constraint(
        "ck_ticket_priority",
        "test_data_tickets",
        "priority IN ('Low','Normal','Urgent')",
        schema="dataops",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ticket_priority", "test_data_tickets", schema="dataops", type_="check")
    op.create_check_constraint(
        "ck_ticket_priority",
        "test_data_tickets",
        "priority IN ('Low','Normal','High','Urgent')",
        schema="dataops",
    )
