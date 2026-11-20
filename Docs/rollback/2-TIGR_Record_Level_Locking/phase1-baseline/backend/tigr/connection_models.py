from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tigr.db import Base


class WorkspaceConnection(Base):
    __tablename__ = "workspace_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_workspace_connections_name"),
        UniqueConstraint("workspace_id", "id", name="uq_workspace_connections_scope_id"),
        CheckConstraint("connector_type IN ('postgresql', 'json', 'csv', 'delimited_text')", name="ck_workspace_connections_type"),
        CheckConstraint("direction IN ('source', 'target', 'both')", name="ck_workspace_connections_direction"),
        CheckConstraint("operation_scope IN ('read_only', 'write_only', 'read_write')", name="ck_workspace_connections_scope"),
        CheckConstraint("status IN ('draft', 'tested', 'enabled', 'disabled')", name="ck_workspace_connections_status"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(24), nullable=False)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    environment: Mapped[str] = mapped_column(String(40), nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(String(180), nullable=False)
    operation_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_reference: Mapped[str | None] = mapped_column(String(500))
    connector_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tested_by: Mapped[str | None] = mapped_column(String(180))
    test_result: Mapped[dict | None] = mapped_column(JSONB)
    structure_preview: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(180), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
