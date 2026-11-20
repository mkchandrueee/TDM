"""Normalized records that complete the PostgreSQL-first MVP schema."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tigr.db import Base


class ConnectionTestRun(Base):
    __tablename__ = "connection_test_runs"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "connection_id"], ["dataops.workspace_connections.workspace_id", "dataops.workspace_connections.id"], ondelete="RESTRICT", name="fk_connection_test_scoped_connection"),
        CheckConstraint("outcome IN ('succeeded', 'failed')", name="ck_connection_test_outcome"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(180), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    safe_error_code: Mapped[str | None] = mapped_column(String(80))
    safe_message: Mapped[str] = mapped_column(String(500), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)


class DatasetStatusHistory(Base):
    __tablename__ = "dataset_status_history"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "dataset_id", "version_id"], ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="RESTRICT", name="fk_dataset_status_history_version"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    changed_by: Mapped[str] = mapped_column(String(180), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)


class DatasetPublication(Base):
    __tablename__ = "dataset_publications"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "dataset_id", "version_id"], ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="RESTRICT", name="fk_dataset_publication_version"),
        CheckConstraint("state IN ('published', 'withdrawn')", name="ck_dataset_publication_state"),
        UniqueConstraint("dataset_id", "version_id", name="uq_dataset_publication_version"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    published_by: Mapped[str] = mapped_column(String(180), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="published")
    readiness_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    withdrawn_by: Mapped[str | None] = mapped_column(String(180))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(500))


class ValidationRun(Base):
    __tablename__ = "validation_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_validation_runs_scope_id"),
        ForeignKeyConstraint(["workspace_id", "dataset_id", "version_id"], ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="RESTRICT", name="fk_validation_run_version"),
        CheckConstraint("status IN ('running', 'passed', 'failed', 'warning')", name="ck_validation_run_status"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("dataops.jobs.id", ondelete="RESTRICT"))
    validation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class ValidationResult(Base):
    __tablename__ = "validation_results"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "validation_run_id"], ["dataops.validation_runs.workspace_id", "dataops.validation_runs.id"], ondelete="CASCADE", name="fk_validation_result_run"),
        CheckConstraint("severity IN ('info', 'warning', 'error')", name="ck_validation_result_severity"),
        CheckConstraint("status IN ('passed', 'failed', 'warning', 'skipped')", name="ck_validation_result_status"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False)
    validation_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    check_code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    field_path: Mapped[str | None] = mapped_column(String(500))
    safe_message: Mapped[str] = mapped_column(String(500), nullable=False)
    observed_count: Mapped[int | None] = mapped_column(BigInteger)
    expected_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class ArtifactRecord(Base):
    __tablename__ = "artifact_records"
    __table_args__ = (
        CheckConstraint("(dataset_id IS NULL) = (version_id IS NULL)", name="ck_artifact_record_dataset_version_pair"),
        ForeignKeyConstraint(["workspace_id", "dataset_id", "version_id"], ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="RESTRICT", name="fk_artifact_record_version"),
        CheckConstraint("size_bytes >= 0", name="ck_artifact_record_size"),
        CheckConstraint("status IN ('available', 'quarantined', 'expired', 'deleted')", name="ck_artifact_record_status"),
        UniqueConstraint("storage_key", name="uq_artifact_storage_key"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False)
    dataset_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    artifact_type: Mapped[str] = mapped_column(String(40), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(40), nullable=False, default="local")
    encryption_key_ref: Mapped[str | None] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    classification: Mapped[str] = mapped_column(String(32), nullable=False, default="restricted")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="available")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataSetRecord(Base):
    """Generated record values live in PostgreSQL, bound to an immutable dataset version."""
    __tablename__ = "dataset_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "dataset_id", "version_id"],
            ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"],
            ondelete="CASCADE", name="fk_dataset_records_version",
        ),
        UniqueConstraint("version_id", "ordinal", name="uq_dataset_records_version_ordinal"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    values: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ConsumptionEvent(Base):
    __tablename__ = "consumption_events"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id", "dataset_id", "version_id"], ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="RESTRICT", name="fk_consumption_event_version"),
        {"schema": "dataops"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(180), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_connection_id: Mapped[UUID | None] = mapped_column(ForeignKey("dataops.workspace_connections.id", ondelete="RESTRICT"))
    artifact_id: Mapped[UUID | None] = mapped_column(ForeignKey("dataops.artifact_records.id", ondelete="RESTRICT"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
