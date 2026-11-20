from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
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
    MYSQL = "mysql"
    ORACLE = "oracle"
    MSSQL = "mssql"
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


class ServerCredentialsInput(BaseModel):
    host: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    port: int = Field(ge=1, le=65535)
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    password: str = Field(default="", max_length=4096)
    initial_database: Annotated[str, StringConstraints(strip_whitespace=True, max_length=128)] | None = None


class DatabaseCredentialInput(BaseModel):
    database_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    credentials: ServerCredentialsInput | None = None
    # Kept for existing local profiles that use env:// references.
    secret_reference: SecretReference | None = None


class DatabaseDiscoveryRequest(BaseModel):
    connector_type: Literal["postgresql", "mysql", "oracle", "mssql"]
    credentials: ServerCredentialsInput | None = None
    secret_reference: SecretReference | None = None


class SchemaDiscoveryRequest(BaseModel):
    connector_type: Literal["postgresql", "mysql", "oracle", "mssql"]
    database_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    credentials: ServerCredentialsInput | None = None
    secret_reference: SecretReference | None = None

    @model_validator(mode="after")
    def require_credentials(self):
        if not self.credentials and not self.secret_reference:
            raise ValueError("Provide database credentials to discover schemas")
        return self


def validate_connector_fields(connector_type: ConnectorType, secret_reference: str | None, config: dict[str, Any],
                               database_credentials: list[DatabaseCredentialInput] | None = None) -> dict[str, Any]:
    if connector_type in (ConnectorType.POSTGRESQL, ConnectorType.MYSQL, ConnectorType.ORACLE, ConnectorType.MSSQL):
        if not secret_reference and not database_credentials:
            raise ValueError(f"{connector_type.value.title()} connections require a secret reference")
        if connector_type == ConnectorType.MYSQL:
            allowed = {"databases", "table_limit"}
            if set(config) - allowed:
                raise ValueError(f"{connector_type.value.upper()} config supports only databases and table_limit")
            databases = config.get("databases", [])
            if (not isinstance(databases, list) or len(databases) > 20
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in databases)
                    or len({value.casefold() for value in databases}) != len(databases)):
                raise ValueError("databases must contain up to 20 unique database names")
            normalized = {"databases": [value.strip() for value in databases]}
            if "table_limit" in config:
                limit = config["table_limit"]
                if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
                    raise ValueError("table_limit must be between 1 and 100")
                normalized["table_limit"] = limit
            return normalized
        if connector_type == ConnectorType.MSSQL:
            allowed = {"databases", "schemas_by_database", "table_limit"}
            if set(config) - allowed:
                raise ValueError("MSSQL config supports only databases, schemas_by_database, and table_limit")
            databases = config.get("databases", [])
            mapping = config.get("schemas_by_database", {})
            if (not isinstance(databases, list) or not databases or len(databases) > 20
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in databases)
                    or len({value.casefold() for value in databases}) != len(databases)):
                raise ValueError("Select up to 20 unique Microsoft SQL Server databases")
            if (not isinstance(mapping, dict) or {str(name).casefold() for name in mapping} != {name.casefold() for name in databases}
                    or any(not isinstance(values, list) or not values or len(values) > 20
                           or any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in values)
                           or len({value.casefold() for value in values}) != len(values) for values in mapping.values())):
                raise ValueError("Select one or more schemas for every selected database")
            normalized = {"databases": [value.strip() for value in databases],
                          "schemas_by_database": {name.strip(): [schema.strip() for schema in values]
                                                  for name, values in mapping.items()}}
            if "table_limit" in config:
                limit = config["table_limit"]
                if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
                    raise ValueError("table_limit must be between 1 and 100")
                normalized["table_limit"] = limit
            return normalized
        allowed = {"schema", "schemas", "schemas_by_database", "databases", "table_limit"}
        if set(config) - allowed:
            raise ValueError(f"{connector_type.value.upper()} config supports only schema, schemas_by_database, databases, and table_limit")
        if "schema" in config and (not isinstance(config["schema"], str) or len(config["schema"]) > 63):
            raise ValueError("schema must be a PostgreSQL schema name")
        databases = config.get("databases", [])
        for label, values in (("databases", databases),):
            if (not isinstance(values, list) or len(values) > 20
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in values)
                    or len({value.casefold() for value in values}) != len(values)):
                raise ValueError(f"{label} must contain up to 20 unique database names")

        schemas_by_database = config.get("schemas_by_database")
        if schemas_by_database is not None:
            if (not isinstance(schemas_by_database, dict) or not schemas_by_database or len(schemas_by_database) > 20
                    or len({str(database).casefold() for database in schemas_by_database}) != len(schemas_by_database)):
                raise ValueError("Select at least one schema for each selected database")
            normalized_mapping: dict[str, list[str]] = {}
            for database, selected_schemas in schemas_by_database.items():
                if (not isinstance(database, str) or not database.strip() or len(database.strip()) > 128
                        or not isinstance(selected_schemas, list) or not selected_schemas or len(selected_schemas) > 20
                        or any(not isinstance(schema, str) or not schema.strip() or len(schema.strip()) > 128 for schema in selected_schemas)
                        or len({schema.casefold() for schema in selected_schemas}) != len(selected_schemas)):
                    raise ValueError("Select up to 20 unique schemas for each selected database")
                normalized_mapping[database.strip()] = [schema.strip() for schema in selected_schemas]
            if databases and {name.casefold() for name in normalized_mapping} != {name.casefold() for name in databases}:
                raise ValueError("Select schemas for every configured database")
            flattened_schemas = list(dict.fromkeys(schema for selected in normalized_mapping.values() for schema in selected))
            normalized = {**config, "schema": flattened_schemas[0], "schemas": flattened_schemas,
                          "schemas_by_database": normalized_mapping,
                          "databases": [value.strip() for value in databases]}
        else:
            schemas = config.get("schemas")
            if schemas is None:
                schemas = [config["schema"]] if config.get("schema") else []
            if not schemas:
                raise ValueError("Select at least one schema")
            if (not isinstance(schemas, list) or len(schemas) > 20
                    or any(not isinstance(value, str) or not value.strip() or len(value) > 128 for value in schemas)
                    or len({value.casefold() for value in schemas}) != len(schemas)):
                raise ValueError("schemas must contain up to 20 unique names")
            normalized = {**config, "schema": schemas[0].strip(),
                          "schemas": [value.strip() for value in schemas],
                          "databases": [value.strip() for value in databases]}
        if "table_limit" in config and (not isinstance(config["table_limit"], int) or not 1 <= config["table_limit"] <= 100):
            raise ValueError("table_limit must be between 1 and 100")
        return normalized
    if secret_reference:
        raise ValueError("File connectors do not accept a secret reference")
    allowed = {"file_path", "delimiter", "has_header", "encoding", "record_limit"}
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
    record_limit = config.get("record_limit", 1000)
    if isinstance(record_limit, bool) or not isinstance(record_limit, int) or not 1 <= record_limit <= 10000:
        raise ValueError("record_limit must be between 1 and 10000")
    normalized = {"file_path": file_path.strip(), "delimiter": delimiter, "has_header": has_header, "encoding": encoding, "record_limit": record_limit}
    return normalized


class ConnectionCreate(BaseModel):
    name: Name
    connector_type: ConnectorType
    direction: ConnectionDirection
    environment: Environment
    purpose: str | None = Field(default=None, max_length=2000)
    owner: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)] | None = None
    operation_scope: OperationScope = OperationScope.READ_WRITE
    server_credentials: ServerCredentialsInput | None = None
    secret_reference: SecretReference | None = None
    database_credential_mode: Literal["shared", "per_database"] = "shared"
    database_credentials: list[DatabaseCredentialInput] = Field(default_factory=list)
    connector_config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_config(self):
        if self.server_credentials and self.secret_reference:
            raise ValueError("Use server credentials or a secret reference, not both")
        effective_reference = self.secret_reference or ("credentials" if self.server_credentials else None)
        self.connector_config = validate_connector_fields(self.connector_type, effective_reference, self.connector_config,
                                                          self.database_credentials)
        if self.connector_type in (ConnectorType.POSTGRESQL, ConnectorType.MYSQL, ConnectorType.ORACLE, ConnectorType.MSSQL):
            database_names = self.connector_config.get("databases", [])
            if self.database_credential_mode == "shared" and self.database_credentials:
                raise ValueError("Per-database credentials cannot be supplied in shared mode")
            if self.database_credential_mode == "per_database":
                supplied = {item.database_name.casefold(): item for item in self.database_credentials}
                if not database_names or {name.casefold() for name in database_names} != set(supplied):
                    raise ValueError("Provide one credential reference for each selected database")
                if any(not (item.credentials or item.secret_reference) for item in supplied.values()):
                    raise ValueError("Each selected database requires a credential reference")
                if self.secret_reference or self.server_credentials:
                    raise ValueError("Shared credentials cannot be supplied in per-database mode")
            elif not (self.secret_reference or self.server_credentials):
                raise ValueError("A shared database credential is required")
        else:
            if self.database_credentials or self.database_credential_mode != "shared":
                raise ValueError("Database credentials are supported only for database source connectors")
            if "operation_scope" not in self.model_fields_set:
                self.operation_scope = OperationScope.READ_ONLY
            elif self.operation_scope != OperationScope.READ_ONLY:
                raise ValueError("File connections must use read-only operation scope")
        return self


class ConnectionUpdate(BaseModel):
    name: Name | None = None
    connector_type: ConnectorType | None = None
    direction: ConnectionDirection | None = None
    environment: Environment | None = None
    purpose: str | None = Field(default=None, max_length=2000)
    owner: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)] | None = None
    operation_scope: OperationScope | None = None
    server_credentials: ServerCredentialsInput | None = None
    secret_reference: SecretReference | None = None
    database_credential_mode: Literal["shared", "per_database"] | None = None
    database_credentials: list[DatabaseCredentialInput] | None = None
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
    database_credential_mode: Literal["shared", "per_database"]
    database_credentials: list[dict[str, Any]] = Field(default_factory=list)
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
