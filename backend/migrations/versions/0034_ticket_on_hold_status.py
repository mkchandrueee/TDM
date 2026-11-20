"""Rename the ticket Blocked status to On Hold."""
from alembic import op

revision = "0034_ticket_on_hold_status"
down_revision = "0033_ticket_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_ticket_status", "test_data_tickets", schema="dataops", type_="check")
    op.execute("UPDATE dataops.test_data_tickets SET status = 'On Hold' WHERE status = 'Blocked'")
    op.create_check_constraint(
        "ck_ticket_status",
        "test_data_tickets",
        "status IN ('Draft','Submitted','In Progress','On Hold','Closed','Cancelled')",
        schema="dataops",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ticket_status", "test_data_tickets", schema="dataops", type_="check")
    op.execute("UPDATE dataops.test_data_tickets SET status = 'Blocked' WHERE status = 'On Hold'")
    op.create_check_constraint(
        "ck_ticket_status",
        "test_data_tickets",
        "status IN ('Draft','Submitted','In Progress','Blocked','Closed','Cancelled')",
        schema="dataops",
    )
