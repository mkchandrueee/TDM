import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from tigr.config import get_settings
from tigr.connection_models import WorkspaceConnection
from tigr.secrets import EnvironmentSecretResolver, UnconfiguredSecretResolver

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_COLUMNS = 200


@dataclass(frozen=True)
class ConnectionTestResult:
    succeeded: bool
    message: str
    structure: dict[str, Any] | None = None


def secret_resolver():
    settings = get_settings()
    if settings.env == "development":
        return EnvironmentSecretResolver()
    return UnconfiguredSecretResolver()


def resolve_file(file_path: str) -> Path:
    settings = get_settings()
    candidate = Path(file_path)
    for configured_root in settings.allowed_file_roots:
        root = Path(configured_root).expanduser().resolve()
        resolved = (root / candidate).resolve()
        if resolved == root or root in resolved.parents:
            return resolved
    raise ValueError("The file path is outside the configured file roots")


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "unknown"


def json_fields(value: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 8:
        return []
    if isinstance(value, list):
        objects = [item for item in value[:500] if isinstance(item, dict)]
        if objects:
            merged: dict[str, Any] = {}
            for row in objects:
                for key, item in row.items():
                    merged.setdefault(key, item)
            value = merged
        elif value:
            return [{"name": "$item", "type": f"array<{json_type(value[0])}>"}]
        else:
            return [{"name": "$item", "type": "unknown"}]
    if not isinstance(value, dict):
        return [{"name": "$value", "type": json_type(value)}]
    fields = []
    for name, item in list(value.items())[:MAX_COLUMNS]:
        field = {"name": str(name)[:180], "type": json_type(item)}
        if isinstance(item, (dict, list)):
            field["fields"] = json_fields(item, depth + 1)
        fields.append(field)
    return fields


def test_file(connection: WorkspaceConnection) -> ConnectionTestResult:
    config = connection.connector_config or {}
    path = resolve_file(config["file_path"])
    if not path.is_file():
        if connection.direction != "source" and connection.operation_scope != "read_only":
            parent = path.parent
            if not parent.is_dir() or not os.access(parent, os.W_OK):
                return ConnectionTestResult(False, "Target directory is unavailable or not writable")
            return ConnectionTestResult(True, "Target directory access test succeeded", {"format": connection.connector_type, "path": config["file_path"], "target_path_writable": True, "fields": [], "values_included": False})
        return ConnectionTestResult(False, "File not found under the configured data roots")
    encoding = config.get("encoding", "utf-8-sig")
    if connection.connector_type in ("csv", "delimited_text"):
        delimiter = config.get("delimiter", "\t" if connection.connector_type == "delimited_text" else ",")
        has_header = config.get("has_header", True)
        try:
            with path.open("r", encoding=encoding, newline="") as source:
                reader = csv.reader(source, delimiter=delimiter)
                first_row = next(reader, [])
        except (UnicodeError, csv.Error, OSError):
            return ConnectionTestResult(False, "File could not be decoded or parsed; check encoding and delimiter")
        if not first_row:
            return ConnectionTestResult(False, "File is empty")
        names = first_row if has_header else [f"column_{index + 1}" for index in range(len(first_row))]
        fields = [{"name": str(name)[:180] or f"column_{index + 1}", "type": "string"} for index, name in enumerate(names[:MAX_COLUMNS])]
        return ConnectionTestResult(True, "File structure test succeeded", {"format": connection.connector_type, "path": config["file_path"], "has_header": has_header, "delimiter": delimiter, "fields": fields, "values_included": False})
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ConnectionTestResult(False, "JSON file exceeds the 5 MiB structure preview limit")
        data = json.loads(path.read_text(encoding=encoding))
    except (UnicodeError, json.JSONDecodeError, OSError):
        return ConnectionTestResult(False, "File could not be decoded as JSON; check encoding and syntax")
    return ConnectionTestResult(True, "JSON structure test succeeded", {"format": "json", "path": config["file_path"], "root_type": json_type(data), "fields": json_fields(data), "values_included": False})


def test_postgresql(connection: WorkspaceConnection) -> ConnectionTestResult:
    settings = get_settings()
    if settings.env == "development":
        resolver = EnvironmentSecretResolver()
    else:
        resolver = UnconfiguredSecretResolver()
    try:
        dsn = resolver.resolve(connection.secret_reference or "")
        url = make_url(dsn)
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            return ConnectionTestResult(False, "Secret reference must resolve to a PostgreSQL connection URL")
        engine = create_engine(dsn, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 5})
        try:
            with engine.connect() as db:
                db.execute(text("SELECT 1"))
                schema = (connection.connector_config or {}).get("schema", "public")
                usage_allowed, create_allowed = db.execute(
                    text("SELECT has_schema_privilege(current_user, :schema, 'USAGE'), has_schema_privilege(current_user, :schema, 'CREATE')"),
                    {"schema": schema},
                ).one()
                needs_read = connection.operation_scope in ("read_only", "read_write")
                needs_write = connection.operation_scope in ("write_only", "read_write")
                if (needs_read and not usage_allowed) or (needs_write and not create_allowed):
                    return ConnectionTestResult(False, "Database connected, but requested schema access is not granted")
                inspector = inspect(db)
                tables = inspector.get_table_names(schema=schema)[: (connection.connector_config or {}).get("table_limit", 50)]
                objects = []
                for table in tables:
                    fields = inspector.get_columns(table, schema=schema)[:MAX_COLUMNS]
                    primary_key_columns = set(inspector.get_pk_constraint(table, schema=schema).get("constrained_columns") or [])
                    unique_constraints = []
                    for constraint in inspector.get_unique_constraints(table, schema=schema):
                        columns = constraint.get("column_names") or []
                        if columns:
                            unique_constraints.append({"name": constraint.get("name"), "fields": columns})
                    check_constraints = []
                    for constraint in inspector.get_check_constraints(table, schema=schema):
                        expression = constraint.get("sqltext")
                        if expression:
                            check_constraints.append({"name": constraint.get("name"), "expression": str(expression)[:1000]})
                    foreign_keys = []
                    for foreign_key in inspector.get_foreign_keys(table, schema=schema):
                        referenced_table = foreign_key.get("referred_table")
                        constrained_columns = foreign_key.get("constrained_columns") or []
                        referenced_columns = foreign_key.get("referred_columns") or []
                        for source_field, target_field in zip(constrained_columns, referenced_columns):
                            if referenced_table and source_field and target_field:
                                foreign_keys.append({
                                    "field": source_field,
                                    "references_object": referenced_table,
                                    "references_field": target_field,
                                })
                    objects.append({"name": table, "type": "table", "fields": [
                        {"name": field["name"], "type": str(field["type"]), "primary_key": field["name"] in primary_key_columns,
                         "nullable": bool(field.get("nullable", True)), "default": str(field["default"])[:300] if field.get("default") is not None else None}
                        for field in fields
                    ], "foreign_keys": foreign_keys, "unique_constraints": unique_constraints, "check_constraints": check_constraints})
        finally:
            engine.dispose()
    except (SQLAlchemyError, RuntimeError, ValueError, OSError):
        return ConnectionTestResult(False, "Connection failed. Check the secret reference, network access, and database permissions")
    return ConnectionTestResult(True, "PostgreSQL connection and schema access test succeeded", {"format": "postgresql", "schema": schema, "objects": objects, "values_included": False})


def test_connection(connection: WorkspaceConnection) -> ConnectionTestResult:
    try:
        if connection.connector_type == "postgresql":
            return test_postgresql(connection)
        return test_file(connection)
    except (KeyError, ValueError, OSError):
        return ConnectionTestResult(False, "Connection configuration is invalid or unavailable")
