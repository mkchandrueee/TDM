import csv
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from tigr.config import get_settings
from tigr.connection_models import WorkspaceConnection, secret_reference_for_database
from tigr.secrets import EnvironmentSecretResolver, UnconfiguredSecretResolver, decrypt_credentials, encrypt_credentials

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


def database_url(url, connector_type: str, database: str | None = None):
    if connector_type == "oracle":
        query = dict(url.query)
        if database:
            query["service_name"] = database
        return url.set(drivername="oracle+oracledb", database=None, query=query)
    if connector_type == "mssql":
        return url.set(drivername="mssql+pymssql", database=database)
    if connector_type == "mysql":
        return url.set(drivername="mysql+pymysql", database=database)
    return url.set(database=database) if database else url


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
        with path.open("r", encoding=encoding) as source:
            data = json.load(source)
    except (UnicodeError, json.JSONDecodeError, OSError):
        return ConnectionTestResult(False, "File could not be decoded as JSON; check encoding and syntax")
    return ConnectionTestResult(True, "JSON structure test succeeded", {"format": "json", "path": config["file_path"], "root_type": json_type(data), "fields": json_fields(data), "values_included": False})


def test_postgresql(connection: WorkspaceConnection) -> ConnectionTestResult:
    config = connection.connector_config or {}
    databases = config.get("databases") or []
    schemas_by_database = config.get("schemas_by_database")
    if schemas_by_database:
        schema_pairs = [(database, schema) for database, selected in schemas_by_database.items()
                        for schema in selected]
        schemas = list(dict.fromkeys(schema for _, schema in schema_pairs))
        databases = databases or list(schemas_by_database)
    else:
        schemas = config.get("schemas") or ([config["schema"]] if config.get("schema") else [])
        schema_pairs = [(database, schema) for database in (databases or [None]) for schema in schemas]
    if not schema_pairs:
        return ConnectionTestResult(False, "Select at least one PostgreSQL schema before testing this connection")
    if len(schema_pairs) > 1 or databases:
        objects: list[dict[str, Any]] = []
        for database, schema in schema_pairs:
            scoped = SimpleNamespace(
                connector_type=connection.connector_type,
                operation_scope=connection.operation_scope,
                secret_reference=secret_reference_for_database(connection, database),
                connector_config={"schema": schema, "database_name": database},
            )
            result = test_postgresql(scoped)
            if not result.succeeded:
                return ConnectionTestResult(False, f"Could not discover {database + '.' if database else ''}{schema}: {result.message}")
            for item in (result.structure or {}).get("objects", []):
                item = dict(item)
                item["name"] = ".".join(part for part in (database, schema if len(schema_pairs) > 1 or databases else None, item["name"]) if part)
                item["database"] = database or (result.structure or {}).get("database")
                item["schema"] = schema
                objects.append(item)
        return ConnectionTestResult(True, "PostgreSQL databases and schemas discovered successfully", {
            "format": "postgresql", "databases": databases, "schemas": schemas,
            "schemas_by_database": schemas_by_database,
            "objects": objects, "values_included": False,
        })
    settings = get_settings()
    if settings.env == "development":
        resolver = EnvironmentSecretResolver()
    else:
        resolver = UnconfiguredSecretResolver()
    database_name = config.get("database_name")
    try:
        reference = secret_reference_for_database(connection, database_name) if database_name else connection.secret_reference
        dsn = resolver.resolve(reference or "")
        url = make_url(dsn)
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            return ConnectionTestResult(False, "Secret reference must resolve to a PostgreSQL connection URL")
        database_name = database_name or url.database
        if not database_name:
            return ConnectionTestResult(False, "Select a PostgreSQL source database before testing this connection")
        url = url.set(database=database_name)
        dsn = url.render_as_string(hide_password=False)
        engine = create_engine(dsn, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 5})
        try:
            with engine.connect() as db:
                db.execute(text("SELECT 1"))
                database_name = db.execute(text("SELECT current_database()")).scalar_one()
                schema = (connection.connector_config or {}).get("schema")
                if not schema:
                    return ConnectionTestResult(False, "Select a PostgreSQL schema before testing this connection")
                schema_exists = db.execute(text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = :schema)"
                ), {"schema": schema}).scalar_one()
                if not schema_exists:
                    return ConnectionTestResult(False, f"Schema '{schema}' was not found in database '{database_name}'. Select an existing schema.")
                usage_allowed = db.execute(
                    text("SELECT has_schema_privilege(current_user, :schema, 'USAGE')"),
                    {"schema": schema},
                ).scalar_one()
                # SSD is scoped to the configured database/schema. PostgreSQL
                # enforces table DML privileges when each query executes;
                # schema CREATE is unrelated to INSERT/UPDATE/DELETE rights.
                if not usage_allowed:
                    return ConnectionTestResult(False, "Database connected, but requested schema access is not granted")
                inspector = inspect(db)
                tables = inspector.get_table_names(schema=schema)[: (connection.connector_config or {}).get("table_limit", 50)]
                objects = []
                for table in tables:
                    fields = inspector.get_columns(table, schema=schema)[:MAX_COLUMNS]
                    primary_key = inspector.get_pk_constraint(table, schema=schema)
                    primary_key_columns = set(primary_key.get("constrained_columns") or [])
                    primary_key_metadata = {
                        "name": primary_key.get("name") or "PRIMARY",
                        "fields": list(primary_key.get("constrained_columns") or []),
                        "evidence": "database_primary_key",
                        "validated": True,
                        "enforced": True,
                    } if primary_key_columns else None
                    unique_constraints = []
                    unique_indexes = []
                    for constraint in inspector.get_unique_constraints(table, schema=schema):
                        columns = constraint.get("column_names") or []
                        if columns:
                            unique_constraints.append({
                                "name": constraint.get("name") or "UNIQUE",
                                "fields": columns,
                                "evidence": "database_unique_constraint",
                                "validated": True,
                                "enforced": True,
                            })
                    unique_constraint_names = {item["name"] for item in unique_constraints}
                    for index in inspector.get_indexes(table, schema=schema):
                        columns = index.get("column_names") or []
                        index_name = index.get("name") or ""
                        dialect_options = index.get("dialect_options") or {}
                        if (index.get("unique") and columns and all(isinstance(name, str) for name in columns)
                                and index_name not in unique_constraint_names
                                and not index.get("duplicates_constraint")
                                and not dialect_options.get("postgresql_where")):
                            unique_indexes.append({
                                "name": index_name or "UNIQUE INDEX",
                                "fields": columns,
                                "evidence": "database_unique_index",
                                "validated": True,
                                "enforced": True,
                            })
                    check_constraints = []
                    for constraint in inspector.get_check_constraints(table, schema=schema):
                        expression = constraint.get("sqltext")
                        if expression:
                            check_constraints.append({"name": constraint.get("name"), "expression": str(expression)[:1000]})
                    foreign_keys = []
                    foreign_key_edges = []
                    constraint_states = {
                        name: {"validated": bool(validated), "deferrable": bool(deferrable), "initially_deferred": bool(initially_deferred)}
                        for name, validated, deferrable, initially_deferred in db.execute(text("""
                            SELECT constraint_row.conname, constraint_row.convalidated,
                                   constraint_row.condeferrable, constraint_row.condeferred
                            FROM pg_catalog.pg_constraint AS constraint_row
                            JOIN pg_catalog.pg_class AS relation ON relation.oid = constraint_row.conrelid
                            JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                            WHERE namespace.nspname = :schema AND relation.relname = :table
                              AND constraint_row.contype = 'f'
                        """), {"schema": schema, "table": table}).all()
                    }
                    for foreign_key in inspector.get_foreign_keys(table, schema=schema):
                        referenced_table = foreign_key.get("referred_table")
                        constrained_columns = foreign_key.get("constrained_columns") or []
                        referenced_columns = foreign_key.get("referred_columns") or []
                        if referenced_table and constrained_columns and referenced_columns and len(constrained_columns) == len(referenced_columns):
                            constraint_name = foreign_key.get("name")
                            state = constraint_states.get(constraint_name, {"validated": None, "deferrable": None, "initially_deferred": None})
                            target_schema = foreign_key.get("referred_schema") or schema
                            stable_identity = json.dumps(
                                [schema, table, constraint_name, constrained_columns, target_schema, referenced_table, referenced_columns],
                                separators=(",", ":"), ensure_ascii=False,
                            )
                            relation_id = "fk_" + hashlib.sha256(stable_identity.encode("utf-8")).hexdigest()[:24]
                            foreign_key_edges.append({
                                "id": relation_id,
                                "name": constraint_name,
                                "source_object": table,
                                "source_fields": list(constrained_columns),
                                "target_schema": target_schema,
                                "target_object": referenced_table,
                                "target_fields": list(referenced_columns),
                                "evidence": "database_foreign_key",
                                **state,
                                "enforced": True,
                            })
                        for source_field, target_field in zip(constrained_columns, referenced_columns):
                            if referenced_table and source_field and target_field:
                                foreign_keys.append({
                                    "field": source_field,
                                    "references_object": referenced_table,
                                    "references_field": target_field,
                                })
                    objects.append({"name": table, "table_name": table, "database": database_name,
                        "schema": schema, "type": "table", "fields": [
                        {"name": field["name"], "type": str(field["type"]), "primary_key": field["name"] in primary_key_columns,
                         "nullable": bool(field.get("nullable", True)), "default": str(field["default"])[:300] if field.get("default") is not None else None}
                        for field in fields
                    ], "primary_key": primary_key_metadata, "foreign_keys": foreign_keys,
                        "foreign_key_edges": foreign_key_edges, "unique_constraints": unique_constraints,
                        "unique_indexes": unique_indexes, "check_constraints": check_constraints})
        finally:
            engine.dispose()
    except (SQLAlchemyError, RuntimeError, ValueError, OSError):
        if database_name:
            return ConnectionTestResult(False, f"Database '{database_name}' is unavailable or access was denied. Check that it exists, the server is reachable, and the account has CONNECT permission.")
        return ConnectionTestResult(False, "Connection failed. Check the server address, credentials, network access, and database permissions.")
    return ConnectionTestResult(True, "PostgreSQL connection and schema access test succeeded", {"format": "postgresql", "database": database_name, "schema": schema, "objects": objects, "values_included": False})


def test_mysql(connection: WorkspaceConnection) -> ConnectionTestResult:
    """Validate and discover only explicitly allowed MySQL databases."""
    config = connection.connector_config or {}
    settings = get_settings()
    resolver = EnvironmentSecretResolver() if settings.env == "development" else UnconfiguredSecretResolver()
    engines = []
    try:
        configured_databases = list(config.get("databases") or [])
        base_url = make_url(resolver.resolve(connection.secret_reference or "")) if connection.secret_reference else None
        databases = configured_databases or ([base_url.database] if base_url and base_url.database else [])
        if not databases:
            return ConnectionTestResult(False, "Select at least one MySQL source database before testing this connection")
        table_limit = min(int(config.get("table_limit", 50)), 100)
        objects: list[dict[str, Any]] = []
        for database in databases:
            database_reference = secret_reference_for_database(connection, database)
            if not database_reference:
                return ConnectionTestResult(False, f"No credential reference is configured for database {database}")
            url = make_url(resolver.resolve(database_reference))
            if url.drivername not in {"mysql", "mysql+pymysql"}:
                return ConnectionTestResult(False, "Secret reference must resolve to a MySQL connection URL")
            url = url.set(drivername="mysql+pymysql")
            engine = create_engine(url.set(database=database), poolclass=NullPool, hide_parameters=True,
                                   connect_args={"connect_timeout": 5})
            engines.append(engine)
            with engine.connect() as source_db:
                source_db.execute(text("SELECT 1"))
                inspector = inspect(source_db)
                names = [(name, "table") for name in inspector.get_table_names()]
                names.extend((name, "view") for name in inspector.get_view_names())
                for table, object_type in names[:table_limit]:
                    columns = inspector.get_columns(table)[:MAX_COLUMNS]
                    primary = inspector.get_pk_constraint(table)
                    primary_fields = list(primary.get("constrained_columns") or [])
                    unique_constraints = [{"name": item.get("name") or "UNIQUE", "fields": item.get("column_names") or [],
                                          "evidence": "database_unique_constraint", "validated": True, "enforced": True}
                                         for item in inspector.get_unique_constraints(table) if item.get("column_names")]
                    constraint_names = {item["name"] for item in unique_constraints}
                    unique_indexes = [{"name": item.get("name") or "UNIQUE INDEX", "fields": item.get("column_names") or [],
                                       "evidence": "database_unique_index", "validated": True, "enforced": True}
                                      for item in inspector.get_indexes(table)
                                      if item.get("unique") and item.get("column_names") and item.get("name") not in constraint_names]
                    foreign_key_edges = []
                    foreign_keys = []
                    for item in inspector.get_foreign_keys(table):
                        local_fields = item.get("constrained_columns") or []
                        remote_fields = item.get("referred_columns") or []
                        target = item.get("referred_table")
                        if target and local_fields and len(local_fields) == len(remote_fields):
                            foreign_key_edges.append({"id": f"{database}.{table}.{item.get('name') or target}",
                                "name": item.get("name"), "source_object": table, "source_fields": local_fields,
                                "target_schema": database, "target_object": target, "target_fields": remote_fields,
                                "evidence": "database_foreign_key", "validated": True, "enforced": True})
                        foreign_keys.extend({"field": local, "references_object": target, "references_field": remote}
                                            for local, remote in zip(local_fields, remote_fields) if target)
                    objects.append({"name": f"{database}.{table}", "table_name": table,
                        "database": database, "schema": database, "type": object_type,
                        "fields": [{"name": column["name"], "type": str(column["type"]),
                                    "primary_key": column["name"] in primary_fields,
                                    "nullable": bool(column.get("nullable", True)),
                                    "default": str(column["default"])[:300] if column.get("default") is not None else None}
                                   for column in columns],
                        "primary_key": ({"name": primary.get("name") or "PRIMARY", "fields": primary_fields,
                                         "evidence": "database_primary_key", "validated": True, "enforced": True}
                                        if primary_fields else None),
                        "unique_constraints": unique_constraints, "unique_indexes": unique_indexes,
                        "foreign_keys": foreign_keys, "foreign_key_edges": foreign_key_edges,
                        "check_constraints": []})
        return ConnectionTestResult(True, "MySQL connection and allowlisted database discovery succeeded",
                                    {"format": "mysql", "databases": databases, "objects": objects, "values_included": False})
    except (SQLAlchemyError, RuntimeError, ValueError, OSError):
        return ConnectionTestResult(False, "MySQL connection failed. Check the secret reference, database allowlist, network access, and read permissions")
    finally:
        for engine in engines:
            engine.dispose()


def test_oracle_mssql(connection: WorkspaceConnection) -> ConnectionTestResult:
    connector_type = connection.connector_type
    config = connection.connector_config or {}
    databases = list(config.get("databases") or [])
    schemas_by_database = config.get("schemas_by_database") or {}
    if not databases:
        return ConnectionTestResult(False, f"Select at least one {connector_type.upper()} source database before testing this connection")
    if connector_type == "oracle" and not schemas_by_database:
        return ConnectionTestResult(False, "Select at least one Oracle schema before testing this connection")
    settings = get_settings()
    resolver = EnvironmentSecretResolver() if settings.env == "development" else UnconfiguredSecretResolver()
    engines = []
    objects: list[dict[str, Any]] = []
    try:
        table_limit = min(int(config.get("table_limit", 50)), 100)
        for database in databases:
            reference = secret_reference_for_database(connection, database)
            if not reference:
                return ConnectionTestResult(False, f"No credential reference is configured for database {database}")
            url = make_url(resolver.resolve(reference))
            valid_drivers = {"oracle", "oracle+oracledb"} if connector_type == "oracle" else {"mssql", "mssql+pymssql"}
            if url.drivername not in valid_drivers:
                return ConnectionTestResult(False, f"Secret reference must resolve to a {connector_type.upper()} connection URL")
            engine_url = database_url(url, connector_type, database)
            engine = create_engine(engine_url, poolclass=NullPool, hide_parameters=True)
            engines.append(engine)
            selected_schemas = schemas_by_database.get(database) or (["dbo"] if connector_type == "mssql" else [])
            if not selected_schemas:
                return ConnectionTestResult(False, f"Select at least one Oracle schema for {database}")
            with engine.connect() as source_db:
                source_db.execute(text("SELECT 1 FROM DUAL" if connector_type == "oracle" else "SELECT 1"))
                inspector = inspect(source_db)
                for schema in selected_schemas:
                    names = [(name, "table") for name in inspector.get_table_names(schema=schema)]
                    names.extend((name, "view") for name in inspector.get_view_names(schema=schema))
                    for table, object_type in names[:table_limit]:
                        columns = inspector.get_columns(table, schema=schema)[:MAX_COLUMNS]
                        primary = inspector.get_pk_constraint(table, schema=schema)
                        primary_fields = list(primary.get("constrained_columns") or [])
                        unique_constraints = [{"name": item.get("name") or "UNIQUE", "fields": item.get("column_names") or [],
                                              "evidence": "database_unique_constraint", "validated": True, "enforced": True}
                                             for item in inspector.get_unique_constraints(table, schema=schema) if item.get("column_names")]
                        unique_names = {item["name"] for item in unique_constraints}
                        unique_indexes = [{"name": item.get("name") or "UNIQUE INDEX", "fields": item.get("column_names") or [],
                                           "evidence": "database_unique_index", "validated": True, "enforced": True}
                                          for item in inspector.get_indexes(table, schema=schema)
                                          if item.get("unique") and item.get("column_names") and item.get("name") not in unique_names]
                        foreign_keys = []
                        edges = []
                        for item in inspector.get_foreign_keys(table, schema=schema):
                            local = item.get("constrained_columns") or []
                            remote = item.get("referred_columns") or []
                            target = item.get("referred_table")
                            if target and local and len(local) == len(remote):
                                edges.append({"id": f"{database}.{schema}.{table}.{item.get('name') or target}",
                                    "name": item.get("name"), "source_object": table, "source_fields": local,
                                    "target_schema": item.get("referred_schema") or schema, "target_object": target,
                                    "target_fields": remote, "evidence": "database_foreign_key", "validated": True, "enforced": True})
                            foreign_keys.extend({"field": source, "references_object": target, "references_field": target_field}
                                                for source, target_field in zip(local, remote) if target)
                        objects.append({"name": f"{database}.{schema}.{table}", "table_name": table,
                            "database": database, "schema": schema, "type": object_type,
                            "fields": [{"name": column["name"], "type": str(column["type"]),
                                        "primary_key": column["name"] in primary_fields,
                                        "nullable": bool(column.get("nullable", True)),
                                        "default": str(column["default"])[:300] if column.get("default") is not None else None}
                                       for column in columns],
                            "primary_key": ({"name": primary.get("name") or "PRIMARY", "fields": primary_fields,
                                             "evidence": "database_primary_key", "validated": True, "enforced": True}
                                            if primary_fields else None),
                            "unique_constraints": unique_constraints, "unique_indexes": unique_indexes,
                            "foreign_keys": foreign_keys, "foreign_key_edges": edges, "check_constraints": []})
        return ConnectionTestResult(True, f"{connector_type.upper()} connection and selected schema discovery succeeded",
            {"format": connector_type, "databases": databases, "schemas_by_database": schemas_by_database,
             "objects": objects, "values_included": False})
    except (SQLAlchemyError, RuntimeError, ValueError, OSError):
        return ConnectionTestResult(False, f"{connector_type.upper()} connection failed. Check the server, credentials, selected databases and schemas, network access, and read permissions")
    finally:
        for engine in engines:
            engine.dispose()


def discover_databases(connector_type: str, secret_reference: str) -> list[str]:
    """List databases visible to the supplied secret without returning endpoint details."""
    settings = get_settings()
    resolver = EnvironmentSecretResolver() if settings.env == "development" else UnconfiguredSecretResolver()
    url = make_url(resolver.resolve(secret_reference))
    engine = None
    try:
        if connector_type == "postgresql":
            if url.drivername not in {"postgresql", "postgresql+psycopg"}:
                raise ValueError("invalid_postgresql_secret")
            if not url.database:
                raise ValueError("Enter a starting database before discovering PostgreSQL databases")
            engine = create_engine(url, poolclass=NullPool, hide_parameters=True,
                                   connect_args={"connect_timeout": 5})
            with engine.connect() as source_db:
                rows = source_db.execute(text(
                    "SELECT datname FROM pg_database "
                    "WHERE datallowconn AND NOT datistemplate "
                    "AND has_database_privilege(current_user, datname, 'CONNECT') "
                    "ORDER BY datname"
                )).scalars().all()
                return [str(name) for name in rows]
        if connector_type == "mysql":
            if url.drivername not in {"mysql", "mysql+pymysql"}:
                raise ValueError("invalid_mysql_secret")
            engine = create_engine(url.set(drivername="mysql+pymysql", database=None), poolclass=NullPool,
                                   hide_parameters=True, connect_args={"connect_timeout": 5})
            with engine.connect() as source_db:
                rows = source_db.exec_driver_sql("SHOW DATABASES").scalars().all()
                system_databases = {"information_schema", "mysql", "performance_schema", "sys"}
                return sorted(str(name) for name in rows if str(name).casefold() not in system_databases)
        if connector_type == "mssql":
            if url.drivername not in {"mssql", "mssql+pymssql"}:
                raise ValueError("invalid_mssql_secret")
            engine = create_engine(database_url(url, connector_type, "master"), poolclass=NullPool,
                                   hide_parameters=True, connect_args={"timeout": 5})
            with engine.connect() as source_db:
                rows = source_db.execute(text(
                    "SELECT name FROM sys.databases WHERE database_id > 4 AND state = 0 ORDER BY name"
                )).scalars().all()
                return [str(name) for name in rows]
        if connector_type == "oracle":
            if url.drivername not in {"oracle", "oracle+oracledb"}:
                raise ValueError("invalid_oracle_secret")
            service_name = url.query.get("service_name") or url.database
            if not service_name:
                raise ValueError("Enter an Oracle service name before discovering databases")
            return [str(service_name)]
        raise ValueError("unsupported_database_connector")
    except (SQLAlchemyError, RuntimeError, ValueError, OSError) as exc:
        if connector_type == "postgresql" and url.database:
            raise ValueError(
                f"Could not connect to starting database '{url.database}'. Confirm it exists, the server is reachable, and the account has CONNECT permission."
            ) from exc
        raise ValueError("Could not list databases for this connection. Check the server, credentials, network access, and database permissions.") from exc
    finally:
        if engine is not None:
            engine.dispose()


def discover_databases_with_credentials(connector_type: str, credentials: dict[str, object]) -> list[str]:
    """Discover databases using credentials supplied in the current request."""
    return discover_databases(connector_type, encrypt_credentials(connector_type, credentials))


def discover_schemas(database_name: str, secret_reference: str, requested_connector_type: str | None = None) -> list[str]:
    """List accessible non-system schemas in one explicitly selected database/service."""
    settings = get_settings()
    resolver = EnvironmentSecretResolver() if settings.env == "development" else UnconfiguredSecretResolver()
    url = make_url(resolver.resolve(secret_reference))
    connector_type = str(decrypt_credentials(secret_reference).get("connector_type", requested_connector_type or "postgresql")) if secret_reference.startswith("enc:v1:") else requested_connector_type or "postgresql"
    if connector_type not in {"postgresql", "mysql", "oracle", "mssql"}:
        raise ValueError("Credential must resolve to a database connection URL")
    if connector_type == "postgresql" and url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("Credential must resolve to a PostgreSQL connection URL")
    engine = create_engine(database_url(url, connector_type, database_name), poolclass=NullPool,
                           hide_parameters=True, connect_args={"connect_timeout": 5} if connector_type in {"postgresql", "mysql"} else {})
    try:
        with engine.connect() as source_db:
            if connector_type == "postgresql":
                schemas = source_db.execute(text(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name NOT IN ('information_schema', 'pg_catalog') "
                    "AND schema_name NOT LIKE 'pg_toast%' "
                    "AND has_schema_privilege(current_user, schema_name, 'USAGE') ORDER BY schema_name"
                )).scalars().all()
            elif connector_type == "oracle":
                schemas = source_db.execute(text(
                    "SELECT username FROM all_users WHERE username = SYS_CONTEXT('USERENV','CURRENT_SCHEMA') "
                    "OR username IN (SELECT owner FROM all_tables UNION SELECT owner FROM all_views) ORDER BY username"
                )).scalars().all()
                schemas = [name for name in schemas if str(name).upper() not in {"SYS", "SYSTEM", "XDB", "CTXSYS", "MDSYS", "ORDSYS", "OUTLN", "DBSNMP"}]
            else:
                schemas = inspect(source_db).get_schema_names()
                excluded = {"information_schema", "sys", "guest"} if connector_type == "mssql" else {"information_schema", "mysql", "performance_schema", "sys"}
                schemas = [name for name in schemas if str(name).casefold() not in excluded]
            return [str(schema) for schema in schemas]
    except SQLAlchemyError as exc:
        raise ValueError(
            f"Could not list schemas in database '{database_name}'. Confirm the database exists, is reachable, and the account can connect."
        ) from exc
    finally:
        engine.dispose()


def discover_schemas_with_credentials(connector_type: str, database_name: str, credentials: dict[str, object]) -> list[str]:
    """Discover schemas from credentials supplied for this database selection."""
    return discover_schemas(database_name, encrypt_credentials(connector_type, credentials), connector_type)


def test_connection(connection: WorkspaceConnection) -> ConnectionTestResult:
    try:
        if connection.connector_type == "postgresql":
            return test_postgresql(connection)
        if connection.connector_type == "mysql":
            return test_mysql(connection)
        if connection.connector_type in ("oracle", "mssql"):
            return test_oracle_mssql(connection)
        return test_file(connection)
    except (KeyError, ValueError, OSError):
        return ConnectionTestResult(False, "Connection configuration is invalid or unavailable")
