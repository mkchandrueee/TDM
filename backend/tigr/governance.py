from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tigr.db import Base


class PiiFinding(Base):
    __tablename__ = "pii_findings"
    __table_args__ = (
        UniqueConstraint("version_id", "field", name="uq_pii_finding_version_field"),
        CheckConstraint("classification IN ('sensitive', 'non_sensitive')", name="ck_pii_finding_classification"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.datasets.id", ondelete="CASCADE"), nullable=False)
    version_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.dataset_versions.id", ondelete="CASCADE"), nullable=False)
    field: Mapped[str] = mapped_column(String(180), nullable=False)
    suggested_type: Mapped[str] = mapped_column(String(40), nullable=False)
    suggested_transformation: Mapped[str | None] = mapped_column(String(32))
    classification: Mapped[str] = mapped_column(String(20), nullable=False, default="sensitive")
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.75)
    reviewed_by: Mapped[str | None] = mapped_column(String(180))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MaskingPolicy(Base):
    __tablename__ = "masking_policies"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", "version_number", name="uq_masking_policy_version"),
        CheckConstraint("version_number > 0", name="ck_masking_policy_version_positive"),
        CheckConstraint("status IN ('draft', 'approved', 'revoked')", name="ck_masking_policy_status"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    rules: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(180), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(180))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MaskingRun(Base):
    __tablename__ = "masking_runs"
    __table_args__ = (
        ForeignKeyConstraint(["dataset_id", "version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="CASCADE", name="fk_masking_run_dataset_version"),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_masking_run_status"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    policy_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.masking_policies.id", ondelete="RESTRICT"), nullable=False)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.jobs.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataSetReservation(Base):
    __tablename__ = "dataset_reservations"
    __table_args__ = (
        ForeignKeyConstraint(["dataset_id", "version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="CASCADE", name="fk_reservation_dataset_version"),
        CheckConstraint("ends_at > starts_at", name="ck_reservation_window"),
        CheckConstraint("status IN ('scheduled', 'active', 'completed', 'cancelled', 'expired')", name="ck_reservation_status"),
        Index("ix_reservations_workspace_window", "workspace_id", "starts_at", "ends_at"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    request_id: Mapped[UUID | None] = mapped_column(ForeignKey("dataops.data_use_requests.id", ondelete="RESTRICT"))
    actor_id: Mapped[str] = mapped_column(String(180), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DataUseRequest(Base):
    __tablename__ = "data_use_requests"
    __table_args__ = (
        ForeignKeyConstraint(["dataset_id", "version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="CASCADE", name="fk_data_use_request_dataset_version"),
        CheckConstraint("ends_at > starts_at", name="ck_data_use_request_window"),
        CheckConstraint("status IN ('pending', 'approved', 'denied', 'cancelled')", name="ck_data_use_request_status"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    requester_id: Mapped[str] = mapped_column(String(180), nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(180))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DataSetLock(Base):
    __tablename__ = "dataset_locks"
    __table_args__ = (
        ForeignKeyConstraint(["dataset_id", "version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"], ondelete="CASCADE", name="fk_lock_dataset_version"),
        CheckConstraint("status IN ('active', 'released', 'expired', 'force_released')", name="ck_dataset_lock_status"),
        Index("uq_dataset_lock_active", "version_id", unique=True, postgresql_where=text("status = 'active'")),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(180), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    release_reason: Mapped[str | None] = mapped_column(Text)


class EntityClaim(Base):
    """Active ownership of one protected source entity during a version lease."""
    __tablename__ = "dataset_entity_claims"
    __table_args__ = (
        ForeignKeyConstraint(["dataset_id", "version_id"], ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"],
                             ondelete="CASCADE", name="fk_entity_claim_dataset_version"),
        ForeignKeyConstraint(["lock_id"], ["dataops.dataset_locks.id"], ondelete="CASCADE", name="fk_entity_claim_lock"),
        CheckConstraint("status IN ('active', 'released', 'expired', 'force_released')", name="ck_entity_claim_status"),
        CheckConstraint("fencing_token > 0", name="ck_entity_claim_fencing_token"),
        Index("ix_entity_claim_active_identity", "source_namespace", "source_object", "token_version", "entity_token",
              postgresql_where=text("status = 'active'")),
        Index("ix_entity_claim_active_owner", "workspace_id", "actor_id", "expires_at",
              postgresql_where=text("status = 'active'")),
        Index("ix_entity_claim_dataset", "dataset_id", "version_id", "status"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    lock_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(180), nullable=False)
    source_namespace: Mapped[str] = mapped_column(String(300), nullable=False)
    source_object: Mapped[str] = mapped_column(String(300), nullable=False)
    key_definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    token_version: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_token: Mapped[str] = mapped_column(String(80), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    release_reason: Mapped[str | None] = mapped_column(Text)
