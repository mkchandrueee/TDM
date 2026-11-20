"""Banking source tables loaded from the repository CSV files into TIGR PostgreSQL."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from tigr.db import Base


class SourceBranch(Base):
    __tablename__ = "branches"
    __table_args__ = ({"schema": "source_data"},)

    branch_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_name: Mapped[str] = mapped_column(String, nullable=False)
    city: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    opened_date: Mapped[date] = mapped_column(Date, nullable=False)
    ifsc_code: Mapped[str] = mapped_column(String, nullable=False)


class SourceCustomer(Base):
    __tablename__ = "customers"
    __table_args__ = ({"schema": "source_data"},)

    customer_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    gender: Mapped[str] = mapped_column(String, nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    city: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    occupation: Mapped[str] = mapped_column(String, nullable=False)
    annual_income: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    join_date: Mapped[date] = mapped_column(Date, nullable=False)
    credit_score: Mapped[int] = mapped_column(Integer, nullable=False)


class SourceAccount(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        Index("ix_source_accounts_customer_id", "customer_id"),
        Index("ix_source_accounts_branch_id", "branch_id"),
        {"schema": "source_data"},
    )

    account_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("source_data.customers.customer_id"), nullable=False)
    branch_id: Mapped[int] = mapped_column(ForeignKey("source_data.branches.branch_id"), nullable=False)
    account_type: Mapped[str] = mapped_column(String, nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    open_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)


class SourceLoan(Base):
    __tablename__ = "loans"
    __table_args__ = (
        Index("ix_source_loans_customer_id", "customer_id"),
        Index("ix_source_loans_branch_id", "branch_id"),
        {"schema": "source_data"},
    )

    loan_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("source_data.customers.customer_id"), nullable=False)
    branch_id: Mapped[int] = mapped_column(ForeignKey("source_data.branches.branch_id"), nullable=False)
    loan_type: Mapped[str] = mapped_column(String, nullable=False)
    loan_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    interest_rate: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
