import csv
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from tigr.connection_models import WorkspaceConnection
from tigr.connection_testing import MAX_FILE_BYTES, resolve_file, secret_resolver
from tigr.scenario_schemas import GenerationMode, MappingField, PredicateOperator, TypedPredicate

MAX_GENERATION_ROWS = 10_000
MAX_REPAIR_ATTEMPTS = 2


def connection_fields(connection: WorkspaceConnection, object_name: str | None = None) -> list[dict[str, Any]]:
    preview = connection.structure_preview or {}
    if connection.connector_type == "postgresql":
        objects = preview.get("objects", [])
        chosen = next((row for row in objects if row.get("name") == object_name), None)
        return list(chosen.get("fields", [])) if chosen else []
    fields = list(preview.get("fields", []))
    if connection.connector_type != "json":
        return fields
    flattened: list[dict[str, Any]] = []

    def visit(items: list[dict[str, Any]], parent: str = "$") -> None:
        for field in items:
            name = str(field.get("name", ""))
            path = f"{parent}.{name}" if parent != "$" else f"$.{name}"
            children = field.get("fields")
            if isinstance(children, list) and children:
                visit(children, path)
            else:
                flattened.append({"name": path, "type": field.get("type", "unknown"), "json_path": path})

    visit(fields)
    return flattened


def clarifying_questions(plan) -> list[str]:
    questions = []
    if not plan.requirement.strip():
        questions.append("What should the scenario cover?")
    return questions


def validate_mapping(plan, connection: WorkspaceConnection | None = None) -> list[MappingField]:
    mapping = [MappingField.model_validate(item) for item in plan.mapping]
    if not mapping:
        raise ValueError("Add at least one field in the schema mapping")
    source_names = [(item.source_object or getattr(plan, "source_object", None), item.source_field) for item in mapping]
    target_names = [item.target_field for item in mapping]
    if len(set(source_names)) != len(source_names) or len(set(target_names)) != len(target_names):
        raise ValueError("Source and target fields must be unique")
    if plan.generation_mode == GenerationMode.SOURCE_BACKED.value:
        if connection is None or connection.status != "enabled" or connection.direction not in ("source", "both"):
            raise ValueError("Choose an enabled source connection")
        if connection.operation_scope not in ("read_only", "read_write"):
            raise ValueError("The connection does not allow source reads")
        objects = ({str(row.get("name")) for row in (connection.structure_preview or {}).get("objects", [])}
                   if connection.connector_type == "postgresql" else {str(plan.source_object or "source_file")})
        selected_objects = set((plan.logical_plan or {}).get("selected_source_objects") or [])
        for item in mapping:
            object_name = item.source_object or plan.source_object
            if selected_objects and object_name not in selected_objects:
                raise ValueError("Mapped source objects must be selected in Step 1")
            metadata = connection_fields(connection, object_name)
            available = {str(field.get("name")) for field in metadata}
            if not metadata or object_name not in objects or item.source_field not in available:
                raise ValueError("Every mapped source field must exist in approved structure metadata")
        source_joins = (plan.logical_plan or {}).get("source_joins", [])
        if source_joins and connection.connector_type != "postgresql":
            raise ValueError("Source table relationships require a PostgreSQL connection")
        for join in source_joins:
            left_object, right_object = join.get("left_object"), join.get("right_object")
            if selected_objects and (left_object not in selected_objects or right_object not in selected_objects):
                raise ValueError("Source relationship objects must be selected in Step 1")
            if left_object not in objects or right_object not in objects or left_object == right_object:
                raise ValueError("Source relationship objects must exist in approved structure metadata")
            left_fields = {str(field.get("name")) for field in connection_fields(connection, left_object)}
            right_fields = {str(field.get("name")) for field in connection_fields(connection, right_object)}
            if join.get("left_field") not in left_fields or join.get("right_field") not in right_fields:
                raise ValueError("Source relationship fields must exist in approved structure metadata")
        approved_paths = {
            (item.source_object or plan.source_object, str(field.get("json_path")))
            for item in mapping
            for field in connection_fields(connection, item.source_object or plan.source_object)
            if field.get("json_path")
        }
        if any(item.json_path is not None and (item.source_object or plan.source_object, item.json_path) not in approved_paths for item in mapping):
            raise ValueError("JSON paths must come from the approved structure metadata")
    for predicate in (plan.logical_plan or {}).get("predicates", []):
        if predicate.get("field") not in {item.source_field for item in mapping} | {item.target_field for item in mapping}:
            raise ValueError("Query predicates must use a mapped source field")
    target_names_set = set(target_names)
    fields_by_target = {item.target_field: item for item in mapping}
    for relationship in (plan.logical_plan or {}).get("relationships", []):
        if relationship.get("parent_field") not in target_names_set or relationship.get("child_field") not in target_names_set:
            raise ValueError("Relationship fields must exist in the reviewed output mapping")
        parent = fields_by_target[relationship["parent_field"]]
        child = fields_by_target[relationship["child_field"]]
        if not parent.required or not child.required:
            raise ValueError("Parent and child relationship fields must both be required")
        def type_family(data_type: str) -> str:
            kind = data_type.casefold()
            if any(name in kind for name in ("int", "serial", "bigint")):
                return "integer"
            if any(name in kind for name in ("numeric", "decimal", "float", "double", "real")):
                return "number"
            if "bool" in kind:
                return "boolean"
            if "uuid" in kind:
                return "uuid"
            if "json" in kind or "object" in kind or "array" in kind:
                return "structured"
            return "string"
        if type_family(parent.data_type) != type_family(child.data_type):
            raise ValueError("Parent and child key fields must use compatible types")
    return mapping


def compile_query_plan(plan, connection: WorkspaceConnection | None) -> dict[str, Any]:
    mapping = validate_mapping(plan, connection)
    predicates = [TypedPredicate.model_validate(item) for item in (plan.logical_plan or {}).get("predicates", [])]
    if plan.generation_mode == GenerationMode.SYNTHETIC.value:
        generated_query = (plan.logical_plan or {}).get("generated_query")
        return {
            "query_kind": "synthetic_sql_select" if generated_query else "synthetic_schema_plan",
            "query": generated_query,
            "query_fields": (plan.logical_plan or {}).get("generated_query_fields", []),
            "fields": [item.model_dump() for item in mapping],
            "constraints": ["required_fields", "declared_types", "key_relationships"],
            "row_limit": min(plan.volume, MAX_GENERATION_ROWS),
            "estimated_count": min(plan.volume, MAX_GENERATION_ROWS),
            "exact_count": False,
        }
    if connection is None:
        raise ValueError("A source connection is required")
    if connection.connector_type == "postgresql":
        generated_query = (plan.logical_plan or {}).get("generated_query")
        if generated_query:
            return {
                "query_kind": "postgresql_select", "query": f"SELECT * FROM ({generated_query.rstrip().rstrip(';')}) AS tigr_generated LIMIT :tigr_limit",
                "parameters": {"tigr_limit": min(plan.volume, MAX_GENERATION_ROWS)},
                "row_limit": min(plan.volume, MAX_GENERATION_ROWS),
                "estimated_count": min(plan.volume, MAX_GENERATION_ROWS), "exact_count": False,
            }
        schema = str((connection.connector_config or {}).get("schema", "public"))
        objects = (connection.structure_preview or {}).get("objects", [])
        source = next((item for item in objects if item.get("name") == plan.source_object), None)
        if source is None:
            raise ValueError("Source object is not in the enabled connection preview")
        quote = lambda name: '"' + str(name).replace('"', '""') + '"'
        source_objects = {item.source_object or plan.source_object for item in mapping}
        source_objects.add(plan.source_object)
        joins = (plan.logical_plan or {}).get("source_joins", [])
        columns = ", ".join(f"{quote(item.source_object or plan.source_object)}.{quote(item.source_field)} AS {quote(item.target_field)}" for item in mapping)
        query = f"SELECT {columns} FROM {quote(schema)}.{quote(plan.source_object)}"
        joined = {plan.source_object}
        remaining = set(source_objects) - joined
        while remaining:
            selected_join = next((join for join in joins if (join["left_object"] in joined and join["right_object"] in remaining) or (join["right_object"] in joined and join["left_object"] in remaining)), None)
            if selected_join is None:
                raise ValueError("Add source join keys connecting every selected object")
            if selected_join["left_object"] in joined:
                existing, existing_field = selected_join["left_object"], selected_join["left_field"]
                new_object, new_field = selected_join["right_object"], selected_join["right_field"]
            else:
                existing, existing_field = selected_join["right_object"], selected_join["right_field"]
                new_object, new_field = selected_join["left_object"], selected_join["left_field"]
            query += f" INNER JOIN {quote(schema)}.{quote(new_object)} ON {quote(existing)}.{quote(existing_field)} = {quote(new_object)}.{quote(new_field)}"
            joined.add(new_object)
            remaining.remove(new_object)
        params: dict[str, Any] = {}
        clauses = []
        for index, predicate in enumerate(predicates):
            predicate_field = next((item for item in mapping if item.target_field == predicate.field), None)
            if predicate_field is None:
                predicate_field = next(item for item in mapping if item.source_field == predicate.field)
            field_name = f"{quote(predicate_field.source_object or plan.source_object)}.{quote(predicate_field.source_field)}"
            if predicate.operator == PredicateOperator.IS_NULL:
                clauses.append(f"{field_name} IS NULL")
            elif predicate.operator == PredicateOperator.IN:
                values = predicate.value if isinstance(predicate.value, list) else [predicate.value]
                names = []
                for item_index, value in enumerate(values):
                    key = f"p{index}_{item_index}"
                    names.append(f":{key}")
                    params[key] = value
                clauses.append(f"{field_name} IN ({', '.join(names)})")
            elif predicate.operator == PredicateOperator.BETWEEN:
                lower_key, upper_key = f"p{index}_lower", f"p{index}_upper"
                clauses.append(f"{field_name} BETWEEN :{lower_key} AND :{upper_key}")
                params[lower_key], params[upper_key] = predicate.value
            else:
                symbol = {PredicateOperator.EQ: "=", PredicateOperator.NE: "<>", PredicateOperator.LT: "<", PredicateOperator.LTE: "<=", PredicateOperator.GT: ">", PredicateOperator.GTE: ">="}[predicate.operator]
                key = f"p{index}"
                clauses.append(f"{field_name} {symbol} :{key}")
                params[key] = predicate.value
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " LIMIT :tigr_limit"
        params["tigr_limit"] = min(plan.volume, MAX_GENERATION_ROWS)
        return {"query_kind": "postgresql_select", "query": query, "parameters": params, "row_limit": params["tigr_limit"], "estimated_count": params["tigr_limit"], "exact_count": False}
    return {
        "query_kind": "file_filter_plan", "format": connection.connector_type,
        "object": plan.source_object, "fields": [item.model_dump() for item in mapping],
        "predicates": [item.model_dump(mode="json") for item in predicates],
        "row_limit": min(plan.volume, MAX_GENERATION_ROWS), "estimated_count": min(plan.volume, MAX_GENERATION_ROWS), "exact_count": False,
    }


def postgres_review_query(compiled: dict[str, Any]) -> str | None:
    """Render a PostgreSQL-native, copyable query while execution stays bound."""
    query = compiled.get("query")
    if not query:
        return None
    parameters = compiled.get("parameters", {})
    statement = text(query)
    if parameters:
        statement = statement.bindparams(**parameters)
    return str(statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))


def _get_path(row: dict[str, Any], path: str) -> Any:
    if path.startswith("$"):
        keys = re.findall(r"([A-Za-z_$][A-Za-z0-9_$]*)|\[(\d+)\]|\[['\"]([^'\"]+)['\"]\]", path)
        tokens = [next((part for part in item if part), "") for item in keys]
        tokens = [item for item in tokens if item and item != "$" ]

        def walk(value: Any, remaining: list[str]) -> Any:
            if not remaining:
                return value
            key, *tail = remaining
            if key == "$item" and isinstance(value, list):
                return [walk(item, tail) for item in value[:MAX_GENERATION_ROWS]]
            if isinstance(value, list) and key.isdigit() and int(key) < len(value):
                return walk(value[int(key)], tail)
            if isinstance(value, dict) and key in value:
                return walk(value[key], tail)
            return None

        return walk(row, tokens)
    return row.get(path)


def _matches(value: Any, predicate: TypedPredicate) -> bool:
    expected = predicate.value
    if predicate.operator == PredicateOperator.IS_NULL:
        return value is None
    if predicate.operator == PredicateOperator.IN:
        return value in (expected if isinstance(expected, list) else [expected])
    if predicate.operator == PredicateOperator.BETWEEN:
        try:
            return expected[0] <= value <= expected[1]
        except (TypeError, IndexError):
            return False
    try:
        return {
            PredicateOperator.EQ: lambda: value == expected,
            PredicateOperator.NE: lambda: value != expected,
            PredicateOperator.LT: lambda: value < expected,
            PredicateOperator.LTE: lambda: value <= expected,
            PredicateOperator.GT: lambda: value > expected,
            PredicateOperator.GTE: lambda: value >= expected,
        }[predicate.operator]()
    except (TypeError, KeyError):
        return False


def _read_file_rows(connection: WorkspaceConnection) -> list[dict[str, Any]]:
    path = resolve_file((connection.connector_config or {})["file_path"])
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Source file exceeds the configured 5 MiB generation limit")
    encoding = (connection.connector_config or {}).get("encoding", "utf-8-sig")
    if connection.connector_type == "json":
        data = json.loads(path.read_text(encoding=encoding))
        if isinstance(data, list):
            return [row for row in data[:MAX_GENERATION_ROWS * 10] if isinstance(row, dict)]
        if isinstance(data, dict):
            return [data]
        raise ValueError("JSON source must contain an object or array of objects")
    delimiter = (connection.connector_config or {}).get("delimiter", "\t" if connection.connector_type == "delimited_text" else ",")
    with path.open("r", encoding=encoding, newline="") as stream:
        reader = csv.DictReader(stream, delimiter=delimiter) if (connection.connector_config or {}).get("has_header", True) else None
        if reader is None:
            raise ValueError("Source-backed scenario generation requires a delimited file with a header row")
        return [row for _, row in zip(range(MAX_GENERATION_ROWS * 10), reader)]


def _read_postgres_rows(connection: WorkspaceConnection, plan, compiled: dict[str, Any]) -> list[dict[str, Any]]:
    dsn = secret_resolver().resolve(connection.secret_reference or "")
    if make_url(dsn).drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("Source secret must resolve to a PostgreSQL URL")
    engine = create_engine(dsn, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as source_db, source_db.begin():
            source_db.execute(text("SET TRANSACTION READ ONLY"))
            source_db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
            return [dict(row) for row in source_db.execute(text(compiled["query"]), compiled["parameters"]).mappings().all()]
    finally:
        engine.dispose()


def validate_readonly_query(connection: WorkspaceConnection, query: str, output_fields: list[str] | None = None) -> None:
    """Ask PostgreSQL to parse and plan a user-edited query read-only."""
    sql = query.strip()
    if not sql.casefold().startswith(("select", "with")) or ";" in sql.rstrip(";"):
        raise ValueError("Enter one read-only PostgreSQL SELECT or WITH query")
    dsn = secret_resolver().resolve(connection.secret_reference or "")
    if make_url(dsn).drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("Source secret must resolve to a PostgreSQL URL")
    engine = create_engine(dsn, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as source_db, source_db.begin():
            source_db.exec_driver_sql("SET TRANSACTION READ ONLY")
            source_db.exec_driver_sql("SET LOCAL statement_timeout = '10000ms'")
            source_db.exec_driver_sql(f"EXPLAIN {sql}").all()
            if output_fields:
                result = source_db.exec_driver_sql(f"SELECT * FROM ({sql.rstrip().rstrip(';')}) AS tigr_query_validation LIMIT 0")
                returned_fields = set(result.keys())
                missing = [field for field in output_fields if field not in returned_fields]
                if missing:
                    raise ValueError("Query output is missing mapped field(s): " + ", ".join(missing))
    except ValueError:
        raise
    except Exception as exc:  # Database drivers expose syntax diagnostics through driver-specific exception fields.
        original = getattr(exc, "orig", exc)
        sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
        diagnostic = getattr(original, "diag", None)
        primary = getattr(diagnostic, "message_primary", None)
        if sqlstate == "57014":
            raise ValueError("PostgreSQL query validation timed out after 10 seconds. Simplify the query or narrow its source scope.") from exc
        if primary:
            raw_position = getattr(diagnostic, "statement_position", None)
            try:
                position = int(raw_position)
            except (TypeError, ValueError):
                position = 0
            if position > 0:
                prefix = sql[:position - 1]
                primary = f"{primary} (line {prefix.count(chr(10)) + 1}, column {len(prefix.rsplit(chr(10), 1)[-1]) + 1})"
            raise ValueError(str(primary)) from exc
        if isinstance(exc, SQLAlchemyError):
            raise ValueError("Could not reach the selected PostgreSQL source. Check the connection and credentials.") from exc
        raise
    finally:
        engine.dispose()


def synthetic_query_output_mapping(plan, mapping: list[MappingField]) -> list[MappingField]:
    output_fields = (plan.logical_plan or {}).get("generated_query_fields")
    if not output_fields:
        return mapping
    source_by_name = {field.target_field: field for field in mapping}
    output_types = (plan.logical_plan or {}).get("generated_query_field_types", {})
    return [
        MappingField(
            source_field=name, target_field=name,
            data_type=output_types.get(name) or (source_by_name[name].data_type if name in source_by_name else "TEXT"),
            required=True,
        )
        for name in output_fields
    ]


def _generate_synthetic(plan, mapping: list[MappingField]) -> list[dict[str, Any]]:
    from tigr.scenario_assistant import get_scenario_assistant

    fields = [field.model_dump(mode="json") for field in mapping]
    output_names = {field.target_field for field in mapping}
    relationships = [
        relationship for relationship in (plan.logical_plan or {}).get("relationships", [])
        if relationship.get("parent_field") in output_names and relationship.get("child_field") in output_names
    ]
    rows, _ = get_scenario_assistant().generate_synthetic_rows(
        plan.requirement, fields, relationships, min(plan.volume, MAX_GENERATION_ROWS),
        (plan.logical_plan or {}).get("generated_query"),
    )
    return rows


def generate_rows(plan, connection: WorkspaceConnection | None, mapping: list[MappingField], compiled: dict[str, Any]) -> list[dict[str, Any]]:
    if plan.generation_mode == GenerationMode.SYNTHETIC.value:
        rows = _generate_synthetic(plan, synthetic_query_output_mapping(plan, mapping))
        return rows[:min(plan.volume, MAX_GENERATION_ROWS)]
    if connection.connector_type == "postgresql":
        rows = _read_postgres_rows(connection, plan, compiled)
        rows = [{item.target_field: _json_safe(row.get(item.target_field, row.get(item.source_field))) for item in mapping} for row in rows]
    else:
        predicates = [TypedPredicate.model_validate(item) for item in (plan.logical_plan or {}).get("predicates", [])]
        fields = connection_fields(connection, plan.source_object)
        rows = _read_file_rows(connection)
        mapped_predicates = []
        for predicate in predicates:
            mapped = next((item for item in mapping if item.target_field == predicate.field), None)
            path = (mapped.json_path or mapped.source_field) if mapped else predicate.field
            mapped_predicates.append((path, predicate))
        rows = [row for row in rows if all(_matches(_get_path(row, path), predicate) for path, predicate in mapped_predicates)]
        rows = rows[:min(plan.volume, MAX_GENERATION_ROWS)]
        if fields:
            rows = [{item.target_field: _get_path(row, item.json_path or item.source_field) for item in mapping} for row in rows]
    return [{key: _json_safe(value) for key, value in row.items()} for row in rows]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)[:500]


def validate_rows(rows: list[dict[str, Any]], mapping: list[MappingField], relationships: list[dict] | None = None) -> tuple[bool, int]:
    warnings = 0
    for row in rows:
        for field in mapping:
            value = row.get(field.target_field)
            if field.required and value is None:
                warnings += 1
            if value is not None and "int" in field.data_type.casefold() and not isinstance(value, int):
                warnings += 1
            if value is not None and "bool" in field.data_type.casefold() and not isinstance(value, bool):
                warnings += 1
            kind = field.data_type.casefold()
            if value is not None and any(name in kind for name in ("numeric", "decimal", "float", "double", "real")) and not isinstance(value, (int, float, Decimal)):
                warnings += 1
            if value is not None and ("json" in kind or "object" in kind) and not isinstance(value, (dict, list)):
                warnings += 1
            if value is not None and "uuid" in kind:
                try:
                    UUID(str(value))
                except ValueError:
                    warnings += 1
    for relationship in relationships or []:
        parent_values = {row.get(relationship["parent_field"]) for row in rows if row.get(relationship["parent_field"]) is not None}
        if any(row.get(relationship["child_field"]) is not None and row.get(relationship["child_field"]) not in parent_values for row in rows):
            warnings += 1
    return warnings == 0, warnings


def repair_rows(rows: list[dict[str, Any]], mapping: list[MappingField]) -> list[dict[str, Any]]:
    repaired = []
    for row in rows:
        new_row = dict(row)
        for field in mapping:
            value = new_row.get(field.target_field)
            kind = field.data_type.casefold()
            try:
                if value is not None and any(name in kind for name in ("int", "serial", "bigint")) and not isinstance(value, int):
                    new_row[field.target_field] = int(value)
                elif value is not None and any(name in kind for name in ("numeric", "decimal", "float", "double", "real")) and not isinstance(value, (int, float)):
                    new_row[field.target_field] = float(value)
                elif value is not None and "bool" in kind and not isinstance(value, bool) and str(value).casefold() in {"true", "false", "0", "1"}:
                    new_row[field.target_field] = str(value).casefold() in {"true", "1"}
            except (ValueError, TypeError, OverflowError):
                continue
        repaired.append(new_row)
    return repaired


def artifact_reference(reference_id: UUID, rows: list[dict[str, Any]]) -> tuple[str, str]:
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=_json_safe).encode()
    return f"artifact://{reference_id}/dataset.json", hashlib.sha256(payload).hexdigest()
