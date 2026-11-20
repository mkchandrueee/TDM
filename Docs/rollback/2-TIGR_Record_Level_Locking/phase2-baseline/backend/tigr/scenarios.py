from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tigr.db import Base


class ScenarioPlan(Base):
    __tablename__ = "scenario_plans"
    __table_args__ = (
        CheckConstraint("state IN ('draft', 'needs_clarification', 'mapped', 'ready', 'running', 'succeeded', 'failed', 'quarantined')", name="ck_scenario_plans_state"),
        CheckConstraint("generation_mode IN ('synthetic', 'source_backed')", name="ck_scenario_plans_mode"),
        CheckConstraint("volume >= 1 AND volume <= 10000", name="ck_scenario_plans_volume"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(180), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    requirement: Mapped[str] = mapped_column(Text, nullable=False)
    expected_states: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    exclusions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    target_use: Mapped[str] = mapped_column(String(500), nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    clarifying_questions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    generation_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="synthetic")
    source_connection_id: Mapped[UUID | None] = mapped_column(ForeignKey("dataops.workspace_connections.id", ondelete="RESTRICT"))
    source_object: Mapped[str | None] = mapped_column(String(180))
    mapping: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    logical_plan: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("dataops.jobs.id", ondelete="SET NULL"))
    dataset_id: Mapped[UUID | None] = mapped_column(ForeignKey("dataops.datasets.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
