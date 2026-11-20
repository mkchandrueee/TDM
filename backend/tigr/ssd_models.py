"""SSD metadata; sensitive query inputs and temporary outputs are encrypted."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tigr.db import Base


class SsdDraft(Base):
    __tablename__ = "ssd_drafts"
    __table_args__ = ({"schema": "dataops"},)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(180), index=True)
    name: Mapped[str] = mapped_column(String(180))
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(48), default="draft")
    dataset_id: Mapped[UUID | None] = mapped_column(ForeignKey("dataops.datasets.id"))
    generated_revision: Mapped[int | None] = mapped_column(Integer)
    dataset_version_number: Mapped[int | None] = mapped_column(Integer)
    generation_settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SsdRevision(Base):
    __tablename__ = "ssd_revisions"
    __table_args__ = (UniqueConstraint("draft_id", "number"), {"schema": "dataops"})
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    draft_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.ssd_drafts.id"))
    number: Mapped[int] = mapped_column(Integer)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.workspace_connections.id"))
    database: Mapped[str] = mapped_column(String(128))
    secured_inputs: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str] = mapped_column(String(64))
    validation: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SsdExecution(Base):
    __tablename__ = "ssd_executions"
    __table_args__ = (
        UniqueConstraint("revision_id", "kind", "idempotency_key"),
        {"schema": "dataops"},
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.ssd_revisions.id"))
    job_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.jobs.id"), unique=True)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey("dataops.ssd_executions.id"))
    kind: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(180))
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    outcome: Mapped[str] = mapped_column(String(32), default="not_dispatched")
    secured_output: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
