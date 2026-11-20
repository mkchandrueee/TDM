from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tigr.db import Base


class SourceDataQuery(Base):
    """A validated, workspace-scoped PostgreSQL query definition."""

    __tablename__ = "source_data_queries"
    __table_args__ = ({"schema": "dataops"},)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.workspace_connections.id", ondelete="RESTRICT"), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    output_fields: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
