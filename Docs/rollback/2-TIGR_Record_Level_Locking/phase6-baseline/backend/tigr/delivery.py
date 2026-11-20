"""Phase 9 durable target delivery and expiring report export records."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tigr.db import Base


class ProvisioningRun(Base):
    __tablename__ = "provisioning_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "version_id"],
            ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"],
            ondelete="RESTRICT",
            name="fk_provisioning_dataset_version",
        ),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_provisioning_idempotency"),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cleaned')", name="ck_provisioning_status"),
        CheckConstraint("record_count >= 0", name="ck_provisioning_count"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    target_connection_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.workspace_connections.id", ondelete="RESTRICT"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(180), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    target_object: Mapped[str | None] = mapped_column(String(500))
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExportArtifact(Base):
    __tablename__ = "export_artifacts"
    __table_args__ = ({"schema": "dataops"},)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(180), nullable=False)
    report_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
