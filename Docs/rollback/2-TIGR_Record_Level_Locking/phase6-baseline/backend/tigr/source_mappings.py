from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tigr.db import Base


class SourceDataMapping(Base):
    __tablename__ = "source_data_mappings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", "version_number", name="uq_source_data_mapping_version"),
        CheckConstraint("version_number > 0", name="ck_source_data_mapping_version_positive"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.workspace_connections.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    source_object: Mapped[str] = mapped_column(String(180), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    fields: Mapped[list] = mapped_column(JSONB, nullable=False)
    relationships: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    source_joins: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SourceDataPlan(Base):
    __tablename__ = "source_data_plans"
    __table_args__ = (
        CheckConstraint("state IN ('draft', 'saved')", name="ck_source_data_plans_state"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    plan_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
