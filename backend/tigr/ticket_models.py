from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tigr.db import Base


class Ticket(Base):
    __tablename__ = "test_data_tickets"
    __table_args__ = ({"schema": "dataops"},)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    display_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    title: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    request_type: Mapped[str | None] = mapped_column(String(64))
    environment: Mapped[str | None] = mapped_column(String(80))
    application: Mapped[str | None] = mapped_column(String(180))
    scenarios: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    schema_reference: Mapped[str | None] = mapped_column(Text)
    record_count: Mapped[int | None] = mapped_column(Integer)
    data_source_preference: Mapped[str | None] = mapped_column(String(24))
    data_date_from: Mapped[date | None] = mapped_column(Date)
    data_date_to: Mapped[date | None] = mapped_column(Date)
    required_until: Mapped[date | None] = mapped_column(Date)
    relationships: Mapped[str | None] = mapped_column(Text)
    masking_needs: Mapped[str | None] = mapped_column(Text)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text)
    needed_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    test_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    test_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    urgent_justification: Mapped[str | None] = mapped_column(Text)
    requester_user_id: Mapped[str] = mapped_column(ForeignKey("iam.users.subject", ondelete="RESTRICT"), nullable=False)
    assignee_user_id: Mapped[str | None] = mapped_column(ForeignKey("iam.users.subject", ondelete="RESTRICT"))
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(180))
    project_name: Mapped[str | None] = mapped_column(String(180))
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="Normal")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="Draft")
    linked_dataset_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    linked_dataset_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    requested_dataset_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("dataops.datasets.id", ondelete="RESTRICT"))
    fulfillment_summary: Mapped[str | None] = mapped_column(Text)
    closure_actor_user_id: Mapped[str | None] = mapped_column(String(180))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(180))


class TicketEvent(Base):
    __tablename__ = "ticket_events"
    __table_args__ = ({"schema": "dataops"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.test_data_tickets.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(String(180))
    actor_role: Mapped[str | None] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    event_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(180))
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)


class TicketComment(Base):
    __tablename__ = "ticket_comments"
    __table_args__ = ({"schema": "dataops"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.test_data_tickets.id", ondelete="RESTRICT"), nullable=False)
    author_user_id: Mapped[str] = mapped_column(String(180), nullable=False)
    author_role: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    visibility_scope: Mapped[str] = mapped_column(String(24), nullable=False, default="participants")


class TicketBlockPeriod(Base):
    __tablename__ = "ticket_block_periods"
    __table_args__ = ({"schema": "dataops"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.test_data_tickets.id", ondelete="RESTRICT"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by_user_id: Mapped[str] = mapped_column(String(180), nullable=False)
    resolved_by_user_id: Mapped[str | None] = mapped_column(String(180))


class TicketNotification(Base):
    __tablename__ = "ticket_notifications"
    __table_args__ = ({"schema": "dataops"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.test_data_tickets.id", ondelete="RESTRICT"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("iam.users.subject", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
