from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tigr.db import Base


class DataSet(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        CheckConstraint("creation_pathway IN ('scenario_to_data', 'source_to_data', 'self_servicing_data')", name="ck_datasets_pathway"),
        CheckConstraint("visibility IS NULL OR visibility IN ('private', 'public')", name="ck_dataset_visibility"),
        UniqueConstraint("id", "workspace_id", name="uq_datasets_id_workspace"),
        UniqueConstraint("workspace_id", "id", name="uq_datasets_workspace_id"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    reference_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(String(180), nullable=False)
    visibility: Mapped[str | None] = mapped_column(String(16))
    creation_pathway: Mapped[str] = mapped_column(String(32), nullable=False)
    latest_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class DataSetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_number", name="uq_dataset_versions_number"),
        UniqueConstraint("dataset_id", "id", name="uq_dataset_versions_dataset_id"),
        UniqueConstraint("workspace_id", "dataset_id", "id", name="uq_dataset_versions_scope_id"),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_id"], ["dataops.datasets.workspace_id", "dataops.datasets.id"],
            ondelete="RESTRICT", name="fk_dataset_versions_workspace_dataset",
        ),
        CheckConstraint("version_number > 0", name="ck_dataset_versions_number_positive"),
        CheckConstraint("lifecycle_state IN ('draft', 'ready', 'published', 'failed', 'quarantined', 'expired')", name="ck_dataset_versions_lifecycle"),
        CheckConstraint("privacy_state IN ('not_assessed', 'compliant', 'requires_masking', 'blocked')", name="ck_dataset_versions_privacy"),
        CheckConstraint("validation_state IN ('not_run', 'passed', 'failed')", name="ck_dataset_versions_validation"),
        CheckConstraint("publication_state IN ('unpublished', 'published', 'withdrawn')", name="ck_dataset_versions_publication"),
        CheckConstraint("expiry_at IS NULL OR expiry_at > created_at", name="ck_dataset_versions_expiry_after_creation"),
        CheckConstraint("record_count IS NULL OR record_count >= 0", name="ck_dataset_versions_record_count"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.datasets.id", ondelete="RESTRICT"), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    schema_scope: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    selected_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    generation_settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    privacy_state: Mapped[str] = mapped_column(String(24), nullable=False, default="not_assessed")
    validation_state: Mapped[str] = mapped_column(String(16), nullable=False, default="not_run")
    publication_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unpublished")
    record_count: Mapped[int | None] = mapped_column(BigInteger)
    expiry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    permitted_use: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    validation_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    fields: Mapped[list["DataSetField"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="DataSetField.ordinal", lazy="selectin"
    )


class DataSetField(Base):
    __tablename__ = "dataset_fields"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_id", "field_path", name="uq_dataset_fields_version_path"),
        ForeignKeyConstraint(
            ["workspace_id", "dataset_id", "version_id"],
            ["dataops.dataset_versions.workspace_id", "dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"],
            ondelete="CASCADE",
            name="fk_dataset_fields_scoped_version",
        ),
        CheckConstraint("ordinal >= 0", name="ck_dataset_fields_ordinal"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.workspaces.id", ondelete="RESTRICT"), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    field_name: Mapped[str] = mapped_column(String(180), nullable=False)
    field_path: Mapped[str] = mapped_column(String(500), nullable=False)
    data_type: Mapped[str] = mapped_column(String(80), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_schema_field_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    target_field_path: Mapped[str | None] = mapped_column(String(500))
    constraint_spec: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    version: Mapped[DataSetVersion] = relationship(back_populates="fields")


class DataSetLineageEvent(Base):
    __tablename__ = "dataset_lineage_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "version_id"],
            ["dataops.dataset_versions.dataset_id", "dataops.dataset_versions.id"],
            ondelete="RESTRICT",
            name="fk_lineage_dataset_version",
        ),
        UniqueConstraint("dataset_id", "sequence_number", name="uq_dataset_lineage_sequence"),
        CheckConstraint("sequence_number > 0", name="ck_dataset_lineage_sequence_positive"),
        CheckConstraint("outcome IN ('succeeded', 'failed', 'warning')", name="ck_dataset_lineage_outcome"),
        UniqueConstraint("dataset_id", "id", name="uq_dataset_lineage_dataset_id"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("dataops.datasets.id", ondelete="RESTRICT"), nullable=False)
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    stage: Mapped[str] = mapped_column(String(80), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(500))
    artifact_reference: Mapped[str | None] = mapped_column(String(500))
    mapping_version: Mapped[str | None] = mapped_column(String(180))
    scenario_version: Mapped[str | None] = mapped_column(String(180))
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("dataops.jobs.id", ondelete="RESTRICT"))
    policy_version: Mapped[str | None] = mapped_column(String(180))
    validation_version: Mapped[str | None] = mapped_column(String(180))
    actor_id: Mapped[str] = mapped_column(String(180), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    safe_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class DataSetLineageEdge(Base):
    __tablename__ = "dataset_lineage_edges"
    __table_args__ = (
        ForeignKeyConstraint(["dataset_id", "parent_event_id"], ["dataops.dataset_lineage_events.dataset_id", "dataops.dataset_lineage_events.id"], ondelete="RESTRICT", name="fk_lineage_edge_parent_same_dataset"),
        ForeignKeyConstraint(["dataset_id", "child_event_id"], ["dataops.dataset_lineage_events.dataset_id", "dataops.dataset_lineage_events.id"], ondelete="RESTRICT", name="fk_lineage_edge_child_same_dataset"),
        CheckConstraint("parent_event_id <> child_event_id", name="ck_dataset_lineage_edge_not_self"),
        {"schema": "dataops"},
    )

    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    parent_event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    child_event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
