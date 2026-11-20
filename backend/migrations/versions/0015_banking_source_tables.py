"""Create relational banking source tables inside TIGR for CSV imports."""

import sqlalchemy as sa
from alembic import op

revision = "0015_banking_sources"
down_revision = "0014_pg_records_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS source_data")
    op.create_table(
        "branches",
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("branch_name", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("opened_date", sa.Date(), nullable=False),
        sa.Column("ifsc_code", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("branch_id", name="pk_source_branches"),
        schema="source_data",
    )
    op.create_table(
        "customers",
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("gender", sa.Text(), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("occupation", sa.Text(), nullable=False),
        sa.Column("annual_income", sa.Numeric(14, 2), nullable=False),
        sa.Column("join_date", sa.Date(), nullable=False),
        sa.Column("credit_score", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("customer_id", name="pk_source_customers"),
        schema="source_data",
    )
    op.create_table(
        "accounts",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("account_type", sa.Text(), nullable=False),
        sa.Column("balance", sa.Numeric(16, 2), nullable=False),
        sa.Column("open_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["source_data.branches.branch_id"], name="fk_source_accounts_branch"),
        sa.ForeignKeyConstraint(["customer_id"], ["source_data.customers.customer_id"], name="fk_source_accounts_customer"),
        sa.PrimaryKeyConstraint("account_id", name="pk_source_accounts"),
        schema="source_data",
    )
    op.create_table(
        "loans",
        sa.Column("loan_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("branch_id", sa.Integer(), nullable=False),
        sa.Column("loan_type", sa.Text(), nullable=False),
        sa.Column("loan_amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("interest_rate", sa.Numeric(6, 3), nullable=False),
        sa.Column("term_months", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["source_data.branches.branch_id"], name="fk_source_loans_branch"),
        sa.ForeignKeyConstraint(["customer_id"], ["source_data.customers.customer_id"], name="fk_source_loans_customer"),
        sa.PrimaryKeyConstraint("loan_id", name="pk_source_loans"),
        schema="source_data",
    )
    op.create_index("ix_source_accounts_customer_id", "accounts", ["customer_id"], schema="source_data")
    op.create_index("ix_source_accounts_branch_id", "accounts", ["branch_id"], schema="source_data")
    op.create_index("ix_source_loans_customer_id", "loans", ["customer_id"], schema="source_data")
    op.create_index("ix_source_loans_branch_id", "loans", ["branch_id"], schema="source_data")


def downgrade() -> None:
    op.drop_index("ix_source_loans_branch_id", table_name="loans", schema="source_data")
    op.drop_index("ix_source_loans_customer_id", table_name="loans", schema="source_data")
    op.drop_index("ix_source_accounts_branch_id", table_name="accounts", schema="source_data")
    op.drop_index("ix_source_accounts_customer_id", table_name="accounts", schema="source_data")
    op.drop_table("loans", schema="source_data")
    op.drop_table("accounts", schema="source_data")
    op.drop_table("customers", schema="source_data")
    op.drop_table("branches", schema="source_data")
    op.execute("DROP SCHEMA IF EXISTS source_data")
