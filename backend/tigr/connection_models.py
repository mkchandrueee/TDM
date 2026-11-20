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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tigr.db import Base


class WorkspaceConnection(Base):
    __tablename__ = "workspace_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_workspace_connections_name"),
        UniqueConstraint("workspace_id", "id", name="uq_workspace_connections_scope_id"),
        CheckConstraint("connector_type IN ('postgresql', 'mysql', 'oracle', 'mssql', 'json', 'csv', 'delimited_text')", name="ck_workspace_connections_type"),
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
    secret_reference: Mapped[str | None] = mapped_column(Text)
    connector_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    entity_locking_enabled: Mapped[bool] = mapped_column(default=False, nullable=False, server_default="false")
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tested_by: Mapped[str | None] = mapped_column(String(180))
    test_result: Mapped[dict | None] = mapped_column(JSONB)
    structure_preview: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(180), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    database_credentials: Mapped[list["ConnectionDatabaseCredential"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan", lazy="selectin",
    )


class ConnectionDatabaseCredential(Base):
    __tablename__ = "connection_database_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["dataops.workspace_connections.workspace_id", "dataops.workspace_connections.id"],
            ondelete="CASCADE", name="fk_connection_database_credential_connection",
        ),
        UniqueConstraint("connection_id", "database_name", name="uq_connection_database_credential"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    database_name: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_reference: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(180), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    connection: Mapped[WorkspaceConnection] = relationship(back_populates="database_credentials")


def secret_reference_for_database(connection: WorkspaceConnection, database_name: str | None) -> str | None:
    """Resolve a binding-specific credential reference, falling back to profile credentials."""
    if database_name:
        for item in getattr(connection, "database_credentials", ()) or ():
            if item.database_name.casefold() == database_name.casefold():
                return item.secret_reference
    return connection.secret_reference


class ConnectionStructureSnapshot(Base):
    __tablename__ = "connection_structure_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["dataops.workspace_connections.workspace_id", "dataops.workspace_connections.id"],
            ondelete="CASCADE", name="fk_structure_snapshot_connection",
        ),
        UniqueConstraint("connection_id", "version_number", name="uq_structure_snapshot_version"),
        UniqueConstraint("workspace_id", "id", name="uq_structure_snapshot_workspace_id"),
        CheckConstraint("version_number > 0", name="ck_structure_snapshot_version_positive"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    structure: Mapped[dict] = mapped_column(JSONB, nullable=False)
    captured_by: Mapped[str] = mapped_column(String(180), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LockPropagationConfiguration(Base):
    __tablename__ = "lock_propagation_configurations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["dataops.workspace_connections.workspace_id", "dataops.workspace_connections.id"],
            ondelete="CASCADE", name="fk_lock_config_connection",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "structure_snapshot_id"],
            ["dataops.connection_structure_snapshots.workspace_id", "dataops.connection_structure_snapshots.id"],
            ondelete="RESTRICT", name="fk_lock_config_structure_snapshot",
        ),
        UniqueConstraint("connection_id", "version_number", name="uq_lock_config_version"),
        CheckConstraint("version_number > 0", name="ck_lock_config_version_positive"),
        CheckConstraint("status IN ('approved', 'revoked')", name="ck_lock_config_status"),
        {"schema": "dataops"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("iam.workspaces.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    structure_snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="approved")
    created_by: Mapped[str] = mapped_column(String(180), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
