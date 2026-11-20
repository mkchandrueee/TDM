from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from tigr.db import Base


class UserIdentity(Base):
    __tablename__ = "users"
    __table_args__ = ({"schema": "iam"},)

    subject: Mapped[str] = mapped_column(String(180), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PlatformAdmin(Base):
    __tablename__ = "platform_admins"
    __table_args__ = ({"schema": "iam"},)

    subject: Mapped[str] = mapped_column(ForeignKey("iam.users.subject", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[str] = mapped_column(String(180), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_workspaces_slug"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_workspaces_status"),
        {"schema": "iam"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text)
    pii_masking_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_by: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        CheckConstraint("role IN ('Test Data Engineer', 'QA Engineer', 'Admin')", name="ck_membership_human_role"),
        CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="ck_membership_valid_period"),
        {"schema": "iam"},
    )

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), primary_key=True)
    subject: Mapped[str] = mapped_column(ForeignKey("iam.users.subject", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_by: Mapped[str] = mapped_column(String(180), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(180))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AccessGrant(Base):
    __tablename__ = "access_grants"
    __table_args__ = (
        CheckConstraint("scope_type IN ('workspace', 'dataset', 'reservation', 'lock', 'connection')", name="ck_access_grants_scope_type"),
        CheckConstraint("valid_until IS NULL OR valid_until > valid_from", name="ck_access_grants_valid_period"),
        {"schema": "iam"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    subject: Mapped[str] = mapped_column(ForeignKey("iam.users.subject", ondelete="CASCADE"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(180))
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_by: Mapped[str] = mapped_column(String(180), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(180))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ServiceIdentity(Base):
    __tablename__ = "service_identities"
    __table_args__ = ({"schema": "iam"},)

    subject: Mapped[str] = mapped_column(String(180), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(180), nullable=False)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
