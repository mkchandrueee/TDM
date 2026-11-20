from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


class ConnectorType(StrEnum):
    POSTGRESQL = "postgresql"
    JSON = "json"
    CSV = "csv"
    DELIMITED_TEXT = "delimited_text"


class ConnectionDirection(StrEnum):
    SOURCE = "source"
    TARGET = "target"
    BOTH = "both"


class OperationScope(StrEnum):
    READ_ONLY = "read_only"
    WRITE_ONLY = "write_only"
    READ_WRITE = "read_write"


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=180)]
Environment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=40)]
SecretReference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


def validate_connector_fields(connector_type: ConnectorType, secret_reference: str | None, config: dict[str, Any]) -> dict[str, Any]:
    if connector_type == ConnectorType.POSTGRESQL:
        if not secret_reference:
            raise ValueError("PostgreSQL connections require a secret reference")
        allowed = {"schema", "table_limit"}
        if set(config) - allowed:
            raise ValueError("PostgreSQL config supports only schema and table_limit")
        if "schema" in config and (not isinstance(config["schema"], str) or len(config["schema"]) > 63):
            raise ValueError("schema must be a PostgreSQL schema name")
        if "table_limit" in config and (not isinstance(config["table_limit"], int) or not 1 <= config["table_limit"] <= 100):
            raise ValueError("table_limit must be between 1 and 100")
        return config
    if secret_reference:
        raise ValueError("File connectors do not accept a secret reference")
    allowed = {"file_path", "delimiter", "has_header", "encoding"}
    if set(config) - allowed:
        raise ValueError("File connector config contains unsupported settings")
    file_path = config.get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        raise ValueError("file_path is required for file connectors")
    path = PurePosixPath(file_path)
    if path.is_absolute() or ".." in path.parts or "\\" in file_path or "\x00" in file_path:
        raise ValueError("file_path must be a relative path within the configured data root")
    delimiter = config.get("delimiter", "\t" if connector_type == ConnectorType.DELIMITED_TEXT else ",")
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in "\r\n\x00":
        raise ValueError("delimiter must be exactly one printable character")
    has_header = config.get("has_header", True)
    if not isinstance(has_header, bool):
        raise TypeError("has_header must be a boolean")
    encoding = config.get("encoding", "utf-8-sig")
    if encoding not in {"utf-8", "utf-8-sig", "latin-1"}:
        raise ValueError("encoding must be utf-8, utf-8-sig, or latin-1")
    normalized = {"file_path": file_path.strip(), "delimiter": delimiter, "has_header": has_header, "encoding": encoding}
    return normalized


class ConnectionCreate(BaseModel):
    name: Name
    connector_type: ConnectorType
    direction: ConnectionDirection
    environment: Environment
    purpose: str | None = Field(default=None, max_length=2000)
    owner: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)] | None = None
    operation_scope: OperationScope = OperationScope.READ_ONLY
    secret_reference: SecretReference | None = None
    connector_config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_config(self):
        self.connector_config = validate_connector_fields(self.connector_type, self.secret_reference, self.connector_config)
        return self


class ConnectionUpdate(BaseModel):
    name: Name | None = None
    connector_type: ConnectorType | None = None
    direction: ConnectionDirection | None = None
    environment: Environment | None = None
    purpose: str | None = Field(default=None, max_length=2000)
    owner: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)] | None = None
    operation_scope: OperationScope | None = None
    secret_reference: SecretReference | None = None
    connector_config: dict[str, Any] | None = None

    @field_validator("name", "connector_type", "direction", "environment", "owner", "operation_scope", "connector_config")
    @classmethod
    def required_when_supplied(cls, value):
        if value is None:
            raise ValueError("This field cannot be cleared")
        return value

    @model_validator(mode="after")
    def validate_config(self):
        if self.secret_reference is not None or self.connector_config is not None or self.connector_type is not None:
            # The route validates the merged configuration against the stored record.
            return self
        return self


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    connector_type: ConnectorType
    direction: ConnectionDirection
    environment: str
    purpose: str | None
    owner: str
    operation_scope: OperationScope
    secret_configured: bool
    connector_config: dict[str, Any]
    status: str
    tested_at: datetime | None
    tested_by: str | None
    test_result: dict[str, Any] | None
    structure_preview: dict[str, Any] | None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime
