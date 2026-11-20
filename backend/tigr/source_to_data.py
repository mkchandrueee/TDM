import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from tigr.access import ensure_user, record_audit, workspace_role
from tigr.config import get_settings
from tigr.connection_models import ConnectionStructureSnapshot, LockPropagationConfiguration, WorkspaceConnection
from tigr.connection_testing import secret_resolver
from tigr.dataset_schemas import DraftDataSet, LineageEventCreate, Provenance, SchemaField
from tigr.dataset_services import (
    _dataset_fields,
    append_lineage_event,
    create_initial_version,
    create_version,
    dataset_records_privacy_ready,
    provenance_with_lock_configuration,
    record_generated_artifact,
    record_status_change,
    record_validation,
    serialize_dataset_records,
    store_dataset_records,
    workspace_pii_masking_enabled,
)
from tigr.datasets import DataSet, DataSetVersion
from tigr.db import SessionLocal
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.jobs import Job, JobStep, OutboxEvent
from tigr.scenario_generation import (
    _json_safe,
    compile_query_plan,
    connection_fields,
    generate_rows,
    validate_mapping,
    validate_rows,
)
from tigr.scenario_schemas import MappingField, ScenarioRelationship, TypedPredicate
from tigr.schema_records import ArtifactRecord, ConsumptionEvent
from tigr.source_mappings import SourceDataMapping, SourceDataPlan
from tigr.source_queries import SourceDataQuery
from tigr.ticket_linking import link_generation_to_ticket

router = APIRouter(prefix="/api/v1", tags=["source to data"])
logger = logging.getLogger(__name__)
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=180)]


class SourceJoin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left_object: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    left_field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    right_object: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    right_field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]


class MappingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    connection_id: UUID
    source_object: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    fields: list[MappingField] = Field(min_length=1, max_length=200)
    relationships: list[ScenarioRelationship] = Field(default_factory=list, max_length=50)
    source_joins: list[SourceJoin] = Field(default_factory=list, max_length=50)


class SourcePlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    plan_data: dict[str, Any] = Field(default_factory=dict)
    state: Literal["draft", "saved"] = "draft"


class SourcePlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    plan_data: dict[str, Any] | None = None
    state: Literal["draft", "saved"] | None = None


class FederatedSource(BaseModel):
    """A selected object and projection from one authorized database binding."""
    model_config = ConfigDict(extra="forbid")
    alias: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    connection_id: UUID
    structure_version: int = Field(ge=1)
    database: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=63)]
    schema: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=63)]
    object_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    fields: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]] = Field(min_length=1, max_length=200)


class FederatedJoin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left_alias: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    left_field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    right_alias: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    right_field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    join_type: Literal["inner", "left"] = "inner"
    cardinality: Literal["one_to_one", "one_to_many", "many_to_one", "unknown"] = "unknown"


class FederatedPlanValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[FederatedSource] = Field(min_length=2, max_length=10)
    joins: list[FederatedJoin] = Field(min_length=1, max_length=20)
    row_limit: int = Field(default=1000, ge=1, le=10000)


class FederatedGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_plan_id: UUID
    dataset_name: Name
    target_use: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)] = "testing"
    volume: int = Field(default=100, ge=1, le=10000)
    format: str = Field(default="json", pattern="^(json|csv)$")
    expiry_at: AwareDatetime | None = None
    test_data_request_id: UUID | None = None
    draft_dataset_id: UUID | None = None
    base_dataset_id: UUID | None = None
    base_version_number: int | None = Field(default=None, ge=1)
    confirm_source_extraction: bool = False


def _federated_object(db: Session, connection: WorkspaceConnection, source: FederatedSource) -> dict[str, Any]:
    """Resolve a requested object against the current, permission-filtered snapshot."""
    config = connection.connector_config or {}
    allowed_databases = config.get("databases") or ([config["database_name"]] if config.get("database_name") else [])
    if allowed_databases and source.database not in allowed_databases:
        raise HTTPException(status_code=422, detail=f"Database {source.database!r} is not in this connection's configured allowlist")
    snapshot = db.scalar(select(ConnectionStructureSnapshot).where(
        ConnectionStructureSnapshot.connection_id == connection.id,
        ConnectionStructureSnapshot.workspace_id == connection.workspace_id,
        ConnectionStructureSnapshot.version_number == source.structure_version,
    ))
    structure = snapshot.structure if snapshot else (connection.structure_preview or {})
    if (structure or {}).get("structure_snapshot_version") != source.structure_version:
        raise HTTPException(status_code=409, detail=f"The discovered structure for source {source.alias!r} has changed. Refresh the source selection and review its fields.")
    objects = (structure or {}).get("objects", [])
    for item in objects:
        item_database = item.get("database")
        item_schema = item.get("schema")
        if (item_database == source.database and item_schema == source.schema
                and item.get("name", "").split(".")[-1] == source.object_name):
            return item
    raise HTTPException(status_code=422, detail=f"Object {source.database}.{source.schema}.{source.object_name} is not in the current discovered structure")


def _validate_federated_plan(db: Session, workspace_id: UUID, payload: FederatedPlanValidation) -> dict[str, Any]:
    aliases = [source.alias for source in payload.sources]
    if len(set(aliases)) != len(aliases):
        raise HTTPException(status_code=422, detail="Source aliases must be unique")
    resolved: dict[str, tuple[WorkspaceConnection, FederatedSource, dict[str, Any], dict[str, dict[str, Any]]]] = {}
    for source in payload.sources:
        connection = db.get(WorkspaceConnection, source.connection_id)
        if (connection is None or connection.workspace_id != workspace_id or connection.status != "enabled"
                or connection.direction not in ("source", "both")
                or connection.operation_scope not in ("read_only", "read_write")
                or connection.connector_type not in ("postgresql", "mysql")):
            raise HTTPException(status_code=422, detail=f"Source {source.alias!r} is not an enabled, readable relational connection in this workspace")
        item = _federated_object(db, connection, source)
        columns = {field.get("name"): field for field in item.get("fields", [])}
        if len(set(source.fields)) != len(source.fields) or not set(source.fields) <= columns.keys():
            raise HTTPException(status_code=422, detail=f"Selected fields for source {source.alias!r} must match its discovered object")
        if connection.entity_locking_enabled:
            snapshot = db.scalar(select(ConnectionStructureSnapshot).where(
                ConnectionStructureSnapshot.connection_id == connection.id,
                ConnectionStructureSnapshot.version_number == source.structure_version,
            ))
            lock_config = db.scalar(select(LockPropagationConfiguration).where(
                LockPropagationConfiguration.connection_id == connection.id,
                LockPropagationConfiguration.structure_snapshot_id == (snapshot.id if snapshot else None),
                LockPropagationConfiguration.status == "approved",
            ).order_by(LockPropagationConfiguration.version_number.desc()).limit(1))
            if lock_config is None:
                raise HTTPException(status_code=409, detail=f"Entity locking is enabled for {source.alias!r}, but its approved key configuration is missing or stale")
            anchors = [anchor for anchor in (lock_config.configuration or {}).get("anchors", [])
                       if str(anchor.get("object_name") or "") == str(item.get("name") or "")]
            missing_keys = sorted({str(key) for anchor in anchors for key in anchor.get("key_fields", [])} - set(source.fields))
            if missing_keys:
                raise HTTPException(status_code=422, detail=f"Include configured entity key fields for {source.alias!r}: {', '.join(missing_keys)}")
        resolved[source.alias] = (connection, source, item, columns)
    if len({(source.connection_id, source.database, source.schema, source.object_name) for source in payload.sources}) < 2:
        raise HTTPException(status_code=422, detail="Select at least two distinct source objects")
    join_edges: list[dict[str, Any]] = []
    warnings: list[str] = []
    if len(payload.joins) != len(payload.sources) - 1:
        raise HTTPException(status_code=422, detail="Define exactly one reviewed join for each additional source; cyclic or duplicate join edges are not supported")
    connected = {payload.sources[0].alias}
    pending = list(payload.joins)
    for join in pending:
        if join.left_alias not in resolved or join.right_alias not in resolved or join.left_alias == join.right_alias:
            raise HTTPException(status_code=422, detail="Each join must connect two different selected source aliases")
        left = resolved[join.left_alias]
        right = resolved[join.right_alias]
        if join.left_field not in left[1].fields or join.right_field not in right[1].fields:
            raise HTTPException(status_code=422, detail="Join keys must be included in the selected source fields")
        left_type = str(left[3][join.left_field].get("type", "")).lower()
        right_type = str(right[3][join.right_field].get("type", "")).lower()
        numeric = ("int", "numeric", "decimal", "real", "double", "float")
        text_types = ("char", "text", "string", "uuid")
        compatible = (left_type == right_type or
                      any(part in left_type for part in numeric) and any(part in right_type for part in numeric) or
                      any(part in left_type for part in text_types) and any(part in right_type for part in text_types))
        if not compatible:
            raise HTTPException(status_code=422, detail=f"Join key types are incompatible for {join.left_alias}.{join.left_field} and {join.right_alias}.{join.right_field}")
        def unique_field(source_object: dict[str, Any], field_name: str) -> bool:
            primary = source_object.get("primary_key") or {}
            if primary.get("fields") == [field_name]:
                return True
            return any(item.get("fields") == [field_name]
                       for item in [*(source_object.get("unique_constraints") or []), *(source_object.get("unique_indexes") or [])])
        left_unique = unique_field(left[2], join.left_field)
        right_unique = unique_field(right[2], join.right_field)
        if not left_unique and not right_unique:
            warnings.append(f"{join.left_alias}.{join.left_field} and {join.right_alias}.{join.right_field} are not declared unique; this join may expand many-to-many.")
        if join.cardinality == "unknown":
            warnings.append(f"Confirm expected cardinality for the join between {join.left_alias} and {join.right_alias}.")
        join_edges.append(join.model_dump())
    changed = True
    while changed:
        changed = False
        for join in pending:
            if join.left_alias in connected and join.right_alias not in connected:
                connected.add(join.right_alias); changed = True
            if join.right_alias in connected and join.left_alias not in connected:
                connected.add(join.left_alias); changed = True
    if connected != set(aliases):
        raise HTTPException(status_code=422, detail="Add reviewed joins connecting every selected source")
    reads = []
    for connection, source, _item, _columns in resolved.values():
        quote_char = "`" if connection.connector_type == "mysql" else '"'
        quote = lambda value, quote_char=quote_char: quote_char + value.replace(quote_char, quote_char * 2) + quote_char
        reads.append({
            "alias": source.alias, "connection_id": str(connection.id), "connection_name": connection.name,
            "database": source.database, "schema": source.schema, "object": source.object_name,
            "fields": source.fields,
            "read_template": f"SELECT {', '.join(quote(field) for field in source.fields)} FROM {quote(source.schema)}.{quote(source.object_name)} LIMIT {payload.row_limit}",
            "pushdown": ["projection", "row_limit"],
        })
    return {"valid": True, "execution_supported": True, "sources": reads, "joins": join_edges,
            "row_limit_per_source": payload.row_limit, "join_output_row_limit": payload.row_limit,
            "join_location": "TIGR federation layer", "estimates_available": False, "warnings": warnings,
            "disclosure": "Projections and row caps are pushed to each source. Joins run in a bounded, job-local TIGR worker; only approved output is retained after privacy checks. Estimates are unavailable."}


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mapping_id: UUID
    dataset_name: Name
    selected_fields: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]] = Field(min_length=1, max_length=200)
    target_use: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)] = "testing"
    volume: int = Field(default=100, ge=1, le=10000)
    format: str = Field(default="json", pattern="^(json|csv)$")
    expiry_at: AwareDatetime | None = None
    test_data_request_id: UUID | None = None
    target_connection_id: UUID | None = None
    draft_dataset_id: UUID | None = None
    base_dataset_id: UUID | None = None
    base_version_number: int | None = Field(default=None, ge=1)
    predicates: list[TypedPredicate] = Field(default_factory=list, max_length=30)
    confirm_source_extraction: bool = False


class ExecuteSourceQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: UUID
    query_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=6, max_length=20000)]


class ValidateSourceQueryRequest(ExecuteSourceQueryRequest):
    pass


class QueryOutputMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    output_field: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]
    source_object: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)] | None = None
    source_column: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)] | None = None


class GenerateFromQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: UUID
    dataset_name: Name
    target_use: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
    volume: int = Field(ge=1, le=10000)
    format: str = Field(pattern="^(json|csv)$")
    output_fields: list[QueryOutputMapping] = Field(min_length=1, max_length=200)
    expiry_at: AwareDatetime | None = None
    test_data_request_id: UUID | None = None
    target_connection_id: UUID | None = None
    draft_dataset_id: UUID | None = None
    base_dataset_id: UUID | None = None
    base_version_number: int | None = Field(default=None, ge=1)
    confirm_source_extraction: bool = False


def _first_sql_keyword(query: str) -> str:
    position = 0
    while position < len(query):
        if query[position].isspace():
            position += 1
        elif query.startswith("--", position):
            newline = query.find("\n", position + 2)
            position = len(query) if newline < 0 else newline + 1
        elif query.startswith("/*", position):
            end = query.find("*/", position + 2)
            if end < 0:
                raise ValueError("Query contains an unterminated comment")
            position = end + 2
        else:
            break
    start = position
    while position < len(query) and (query[position].isalpha() or query[position] == "_"):
        position += 1
    return query[start:position].upper()


def _validate_read_query(query: str) -> str:
    query = query.strip()
    if not query or len(query) > 20000:
        raise ValueError("Enter a query up to 20,000 characters")
    semicolons: list[int] = []
    position = 0
    quote: str | None = None
    dollar_quote: str | None = None
    block_depth = 0
    while position < len(query):
        char = query[position]
        pair = query[position:position + 2]
        if dollar_quote:
            if query.startswith(dollar_quote, position):
                position += len(dollar_quote)
                dollar_quote = None
                continue
        elif quote:
            if char == quote:
                if position + 1 < len(query) and query[position + 1] == quote:
                    position += 2
                    continue
                quote = None
            elif char == "\\" and quote == "'":
                position += 2
                continue
        elif block_depth:
            if pair == "/*":
                block_depth += 1
                position += 2
                continue
            if pair == "*/":
                block_depth -= 1
                position += 2
                continue
        elif pair == "--":
            newline = query.find("\n", position + 2)
            position = len(query) if newline < 0 else newline + 1
            continue
        elif pair == "/*":
            block_depth = 1
            position += 2
            continue
        elif char in ("'", '"'):
            quote = char
        elif char == "$":
            close = query.find("$", position + 1)
            if close >= 0 and (close == position + 1 or query[position + 1:close].replace("_", "a").isalnum()):
                dollar_quote = query[position:close + 1]
                position = close + 1
                continue
        elif char == ";":
            semicolons.append(position)
        position += 1
    if quote or dollar_quote or block_depth:
        raise ValueError("Query contains an unterminated quoted value or comment")
    if len(semicolons) > 1 or (semicolons and query[semicolons[0] + 1:].strip()):
        raise ValueError("Enter one read-only query or procedure call")
    if semicolons:
        query = query[:semicolons[0]].rstrip()
    if _first_sql_keyword(query) not in {"SELECT", "WITH", "CALL"}:
        raise ValueError("Only SELECT, WITH, or read-only CALL statements are supported")
    return query


def _execute_read_query(connection: WorkspaceConnection, query_text: str, row_limit: int) -> tuple[list[dict], list[dict]]:
    query = _validate_read_query(query_text)
    dsn = secret_resolver().resolve(connection.secret_reference or "")
    if make_url(dsn).drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("Source secret must resolve to a PostgreSQL URL")
    engine = create_engine(dsn, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as source_db, source_db.begin():
            source_db.exec_driver_sql("SET TRANSACTION READ ONLY")
            source_db.exec_driver_sql("SET LOCAL statement_timeout = '10000ms'")
            keyword = _first_sql_keyword(query)
            result = source_db.execution_options(stream_results=keyword in {"SELECT", "WITH"}).exec_driver_sql(query)
            if not result.returns_rows:
                raise ValueError("The query must return rows and output columns")
            names = list(result.keys())
            if not names or len(names) > 200 or any(not name or len(name) > 180 for name in names) or len(set(names)) != len(names):
                raise ValueError("Query output must contain 1–200 uniquely named columns, each 180 characters or less")
            description = result.cursor.description or []
            type_oids = [int(column.type_code) for column in description if getattr(column, "type_code", None) is not None]
            origins = [(index, int(getattr(column, "table_oid", 0) or 0), int(getattr(column, "table_column", 0) or 0))
                       for index, column in enumerate(description)
                       if getattr(column, "table_oid", 0) and getattr(column, "table_column", 0)]
            raw_rows = [dict(row) for row in result.mappings().fetchmany(max(1, min(row_limit, 10000)))]
            result.close()
            type_names = {}
            if type_oids:
                type_names = dict(source_db.execute(text(
                    "SELECT oid::int, format_type(oid, NULL) FROM pg_catalog.pg_type WHERE oid = ANY(CAST(:type_oids AS oid[]))"
                ), {"type_oids": type_oids}).all())
            fields = [{"name": name, "data_type": type_names.get(oid, "text")}
                      for name, oid in zip(names, type_oids)]
            fields.extend({"name": name, "data_type": "text"} for name in names[len(fields):])
            if origins:
                origin_rows = source_db.execute(text("""
                    WITH requested AS (
                        SELECT * FROM unnest(CAST(:relation_oids AS oid[]), CAST(:attribute_numbers AS int2[]))
                        AS origin(relid, attnum)
                    )
                    SELECT requested.relid::int, requested.attnum::int,
                           namespace.nspname AS source_schema,
                           relation.relname AS source_object,
                           attribute.attname AS source_field,
                           EXISTS (
                               SELECT 1 FROM pg_catalog.pg_index primary_index
                               WHERE primary_index.indrelid = relation.oid
                                 AND primary_index.indisprimary
                                 AND attribute.attnum = ANY(primary_index.indkey)
                           ) AS primary_key
                    FROM requested
                    JOIN pg_catalog.pg_class relation ON relation.oid = requested.relid
                    JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
                    JOIN pg_catalog.pg_attribute attribute
                      ON attribute.attrelid = relation.oid AND attribute.attnum = requested.attnum
                """), {"relation_oids": [relation_oid for _, relation_oid, _ in origins],
                     "attribute_numbers": [attribute_number for _, _, attribute_number in origins]}).all()
                origin_metadata = {(int(relation_oid), int(attribute_number)): {
                    "source_schema": source_schema, "source_object": source_object,
                    "source_field": source_field, "primary_key": bool(primary_key)}
                    for relation_oid, attribute_number, source_schema, source_object, source_field, primary_key in origin_rows}
                for index, relation_oid, attribute_number in origins:
                    fields[index].update(origin_metadata.get((relation_oid, attribute_number), {}))
            rows = [{name: _json_safe(row.get(name)) for name in names} for row in raw_rows]
            return fields, rows
    finally:
        engine.dispose()


def _sql_top_level_keyword(sql: str, keyword: str, start: int = 0) -> int | None:
    depth = 0
    quote = None
    index = start
    upper = sql.upper()
    while index < len(sql):
        char = sql[index]
        if quote:
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            elif quote == "'" and char == "\\":
                index += 2
                continue
        elif char in ("'", '"', "`"):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and upper.startswith(keyword.upper(), index):
            before = sql[index - 1] if index else " "
            after_index = index + len(keyword)
            after = sql[after_index] if after_index < len(sql) else " "
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                return index
        index += 1
    return None


def _sql_split_top_level(sql: str, delimiter: str = ",") -> list[str]:
    parts, start, depth, quote, index = [], 0, 0, None, 0
    while index < len(sql):
        char = sql[index]
        if quote:
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            elif quote == "'" and char == "\\":
                index += 2
                continue
        elif char in ("'", '"', "`"):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == delimiter and depth == 0:
            parts.append(sql[start:index].strip())
            start = index + 1
        index += 1
    parts.append(sql[start:].strip())
    return [part for part in parts if part]


def _sql_parenthesized_end(sql: str, opening: int) -> int | None:
    depth, quote, index = 0, None, opening
    while index < len(sql):
        char = sql[index]
        if quote:
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in ("'", '"', "`"):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _sql_identifier(value: str) -> str:
    return value.strip().strip('"`').split(".")[-1].strip('"`')


def _sql_ctes(sql: str) -> tuple[dict[str, str], str]:
    match = re.match(r"\s*WITH\s+(?:RECURSIVE\s+)?", sql, flags=re.IGNORECASE)
    if not match:
        return {}, sql
    ctes: dict[str, str] = {}
    index = match.end()
    while index < len(sql):
        name_match = re.match(r'\s*("(?:[^"]|"")+"|[A-Za-z_][\w$]*)', sql[index:])
        if not name_match:
            return ctes, sql[index:]
        name = _sql_identifier(name_match.group(1))
        index += name_match.end()
        # Optional CTE column list.
        rest = sql[index:].lstrip()
        index += len(sql[index:]) - len(rest)
        if rest.startswith("("):
            end = _sql_parenthesized_end(sql, index)
            if end is None:
                return ctes, sql[index:]
            index = end + 1
        as_match = re.match(r"\s+AS\s+", sql[index:], flags=re.IGNORECASE)
        if not as_match:
            return ctes, sql[index:]
        index += as_match.end()
        materialized = re.match(r"(?:NOT\s+)?MATERIALIZED\s+", sql[index:], flags=re.IGNORECASE)
        if materialized:
            index += materialized.end()
        while index < len(sql) and sql[index].isspace():
            index += 1
        if index >= len(sql) or sql[index] != "(":
            return ctes, sql[index:]
        end = _sql_parenthesized_end(sql, index)
        if end is None:
            return ctes, sql[index:]
        ctes[name.lower()] = sql[index + 1:end]
        index = end + 1
        while index < len(sql) and sql[index].isspace():
            index += 1
        if index < len(sql) and sql[index] == ",":
            index += 1
            continue
        return ctes, sql[index:]
    return ctes, ""


def _sql_projection(sql: str) -> tuple[list[tuple[str, str]], dict[str, str]]:
    select_at = _sql_top_level_keyword(sql, "SELECT")
    from_at = _sql_top_level_keyword(sql, "FROM", (select_at or 0) + len("SELECT")) if select_at is not None else None
    if select_at is None or from_at is None:
        return [], {}
    projection = sql[select_at + len("SELECT"):from_at].strip()
    projection = re.sub(r"^\s*(?:DISTINCT\s+|ALL\s+)", "", projection, flags=re.IGNORECASE)
    items: list[tuple[str, str]] = []
    for item in _sql_split_top_level(projection):
        alias_at = _sql_top_level_keyword(item, "AS")
        if alias_at is not None:
            expression, output_name = item[:alias_at].strip(), _sql_identifier(item[alias_at + 2:].strip())
        else:
            simple = re.fullmatch(r'\s*((?:[\w$]+\s*\.\s*)?"?(?:[\w$]+)"?)\s*', item)
            if not simple:
                continue
            expression = item.strip()
            output_name = _sql_identifier(expression)
        items.append((output_name, expression))
    tail = sql[from_at + len("FROM"):]
    relation_aliases: dict[str, str] = {}
    relation_pattern = re.compile(
        r'\b(?:FROM|JOIN)\s+((?:"?[\w$]+"?\s*\.\s*)?"?[\w$]+"?)(?:\s+(?:AS\s+)?("?[\w$]+"?))?',
        flags=re.IGNORECASE,
    )
    reserved = {"ON", "WHERE", "JOIN", "LEFT", "RIGHT", "FULL", "INNER", "OUTER", "CROSS", "LATERAL",
                "GROUP", "ORDER", "HAVING", "LIMIT", "OFFSET", "UNION", "FETCH"}
    for relation_match in relation_pattern.finditer("FROM " + tail):
        relation = re.sub(r"\s+", "", relation_match.group(1))
        base_name = _sql_identifier(relation)
        alias = relation_match.group(2)
        alias_name = _sql_identifier(alias) if alias and alias.strip('"').upper() not in reserved else base_name
        relation_aliases[alias_name.lower()] = relation
        relation_aliases.setdefault(base_name.lower(), relation)
    return items, relation_aliases


def _infer_query_origins(connection: WorkspaceConnection, query_text: str, fields: list[dict]) -> list[dict]:
    """Infer simple direct-column lineage through SELECT aliases and CTE projections."""
    structure_objects = (connection.structure_preview or {}).get("objects", [])
    objects_by_leaf: dict[str, list[dict]] = {}
    for source_object in structure_objects:
        objects_by_leaf.setdefault(_sql_identifier(str(source_object.get("name", ""))).lower(), []).append(source_object)
    ctes, main_sql = _sql_ctes(query_text)
    parsed_ctes = {name: _sql_projection(body) for name, body in ctes.items()}

    def resolve(sql: str, output_name: str, seen: set[tuple[str, str]] | None = None) -> tuple[str, str, bool] | None:
        seen = seen or set()
        marker = (sql, output_name.lower())
        if marker in seen:
            return None
        seen.add(marker)
        projections, relations = _sql_projection(sql)
        projection = next((expression for name, expression in projections if name.lower() == output_name.lower()), None)
        if projection is None:
            return None
        direct = re.fullmatch(r'\s*(?:((?:"?[\w$]+"?\s*\.\s*)?"?[\w$]+"?)\s*\.\s*)?("?[\w$]+"?)\s*', projection)
        if not direct:
            return None
        qualifier = _sql_identifier(direct.group(1)) if direct.group(1) else None
        column = _sql_identifier(direct.group(2))
        relation = relations.get(qualifier.lower()) if qualifier else None
        if relation:
            relation_leaf = _sql_identifier(relation).lower()
            if relation_leaf in parsed_ctes:
                cte_projections, _ = parsed_ctes[relation_leaf]
                return resolve(ctes[relation_leaf], column, seen) if any(name.lower() == column.lower() for name, _ in cte_projections) else None
            candidates = objects_by_leaf.get(relation_leaf, [])
            if len(candidates) == 1 and any(str(field.get("name", "")).lower() == column.lower() for field in candidates[0].get("fields", [])):
                return str(candidates[0]["name"]), column, any(
                    str(field.get("name", "")).lower() == column.lower() and bool(field.get("primary_key"))
                    for field in candidates[0].get("fields", [])
                )
            return None
        # Unqualified direct names are safe only when unique in the approved schema.
        candidates = [(obj, field) for obj in structure_objects for field in obj.get("fields", [])
                      if str(field.get("name", "")).lower() == column.lower()]
        if len(candidates) == 1:
            obj, field = candidates[0]
            return str(obj["name"]), column, bool(field.get("primary_key"))
        return None

    inferred = [dict(field) for field in fields]
    for field in inferred:
        if field.get("source_object") and field.get("source_field"):
            continue  # PostgreSQL's own origin metadata is authoritative.
        origin = resolve(main_sql, str(field.get("name", "")))
        if origin:
            field.update({"source_object": origin[0], "source_field": origin[1], "primary_key": origin[2]})
    return inferred


def _query_lineage_fields(connection: WorkspaceConnection, output_fields: list[dict],
                          source_names: list[str], target_names: list[str],
                          source_overrides: list[dict] | None = None) -> list[MappingField]:
    """Keep base-table column origins for query output fields used in entity locking."""
    if len(output_fields) != len(source_names) or len(source_names) != len(target_names):
        raise ValueError("query_output_mapping_changed")
    configured_schema = str((connection.connector_config or {}).get("schema") or "")
    fields = []
    overrides = source_overrides or [{} for _ in output_fields]
    if len(overrides) != len(output_fields):
        raise ValueError("query_output_mapping_changed")
    for metadata, source_name, target_name, override in zip(output_fields, source_names, target_names, overrides):
        source_object = metadata.get("source_object")
        source_schema = metadata.get("source_schema")
        # Older saved query metadata stored a quoted schema.table string.
        if source_object and not source_schema and "." in str(source_object):
            source_schema, source_object = str(source_object).rsplit(".", 1)
            source_schema = source_schema.strip('"')
            source_object = source_object.strip('"')
        # Lock anchors use object names from the connection's approved snapshot
        # (table names within the configured schema). Keep foreign-schema origins
        # qualified so they cannot accidentally match an anchor with the same name.
        if source_schema and source_schema != configured_schema:
            source_object = f"{source_schema}.{source_object}"
        source_field = metadata.get("source_field") or source_name
        if override.get("source_object") and override.get("source_column"):
            source_object = override["source_object"]
            source_field = override["source_column"]
        fields.append(MappingField(
            source_field=str(source_field),
            target_field=target_name,
            data_type=str(metadata.get("data_type") or "text"),
            source_object=str(source_object) if source_object else None,
            primary_key=bool(metadata.get("primary_key", False)),
        ))
    return fields


def _validate_query_syntax(
    connection: WorkspaceConnection,
    query_text: str,
    parameters: dict | None = None,
) -> str:
    query = _validate_read_query(query_text)
    keyword = _first_sql_keyword(query)
    if keyword == "CALL":
        return keyword
    dsn = secret_resolver().resolve(connection.secret_reference or "")
    if make_url(dsn).drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("Source secret must resolve to a PostgreSQL URL")
    engine = create_engine(dsn, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as source_db, source_db.begin():
            source_db.exec_driver_sql("SET TRANSACTION READ ONLY")
            source_db.exec_driver_sql("SET LOCAL statement_timeout = '10000ms'")
            if parameters is None:
                source_db.exec_driver_sql(f"EXPLAIN {query}").all()
            else:
                source_db.execute(text(f"EXPLAIN {query}"), parameters).all()
    finally:
        engine.dispose()
    return keyword


def _postgres_query_diagnostic(exc: Exception, query_text: str) -> dict | None:
    original = getattr(exc, "orig", exc)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    diagnostic = getattr(original, "diag", None)
    primary = getattr(diagnostic, "message_primary", None)
    if sqlstate == "57014":
        return {"message": "Validation timed out after 10 seconds. Simplify the query or narrow its source scope."}
    if not sqlstate or not primary or not (sqlstate.startswith(("42", "22", "23", "0A"))):
        return None
    details: dict = {"message": str(primary)}
    raw_position = getattr(diagnostic, "statement_position", None)
    try:
        position = int(raw_position)
    except (TypeError, ValueError):
        position = 0
    if position > 0:
        prefix = query_text[:position - 1]
        details.update({"position": position, "line": prefix.count("\n") + 1,
                        "column": len(prefix.rsplit("\n", 1)[-1]) + 1})
        details["message"] = f"{primary} (line {details['line']}, column {details['column']})"
    return details


@router.post("/workspaces/{workspace_id}/source-data/query/validate")
def validate_source_query(
    workspace_id: UUID,
    payload: ValidateSourceQueryRequest,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    role = require_editor(db, principal, workspace_id)
    connection = db.get(WorkspaceConnection, payload.connection_id)
    if (connection is None or connection.workspace_id != workspace_id or connection.connector_type != "postgresql"
            or connection.status != "enabled" or connection.direction not in ("source", "both")
            or connection.operation_scope not in ("read_only", "read_write")):
        raise HTTPException(status_code=422, detail="Choose an enabled, readable PostgreSQL source connection")
    from tigr.entity_claims import assert_source_namespace_available
    assert_source_namespace_available(
        db, connection.id, principal.subject,
        allow_locked_entities=role in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER),
    )
    try:
        statement_type = _validate_query_syntax(connection, payload.query_text)
    except ValueError as exc:
        record_audit(db, principal, request.state.request_id, "source_query.syntax_validation_failed", "source_connection",
            str(connection.id), workspace_id, role, {"reason": "query_rejected", "syntax_valid": False})
        db.commit()
        return {"valid": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - database errors may include SQL, identifiers, or connection details.
        diagnostic = _postgres_query_diagnostic(exc, payload.query_text)
        if diagnostic:
            record_audit(db, principal, request.state.request_id, "source_query.syntax_validation_failed", "source_connection",
                str(connection.id), workspace_id, role, {"reason": "postgresql_query_error", "syntax_valid": False,
                "line": diagnostic.get("line"), "column": diagnostic.get("column")})
            db.commit()
            return {"valid": False, **diagnostic}
        record_audit(db, principal, request.state.request_id, "source_query.syntax_validation_failed", "source_connection",
            str(connection.id), workspace_id, role, {"reason": "source_unavailable", "syntax_valid": False})
        db.commit()
        return {"valid": False, "message": "Could not reach the selected PostgreSQL source. Check the connection and credentials."}
    record_audit(db, principal, request.state.request_id, "source_query.syntax_validated", "source_connection", str(connection.id),
        workspace_id, role, {"statement_type": statement_type, "syntax_valid": True})
    db.commit()
    return {"valid": True, "statement_type": statement_type,
        "message": "SQL syntax and source references are valid" if statement_type != "CALL" else "CALL shape is valid; Execute Query will verify its signature and output"}


@router.post("/workspaces/{workspace_id}/source-data/query/execute", status_code=201)
def execute_source_query(
    workspace_id: UUID,
    payload: ExecuteSourceQueryRequest,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    role = require_editor(db, principal, workspace_id)
    ensure_user(db, principal)
    connection = db.get(WorkspaceConnection, payload.connection_id)
    if (connection is None or connection.workspace_id != workspace_id or connection.connector_type != "postgresql"
            or connection.status != "enabled" or connection.direction not in ("source", "both")
            or connection.operation_scope not in ("read_only", "read_write")):
        raise HTTPException(status_code=422, detail="Choose an enabled, readable PostgreSQL source connection")
    try:
        fields, rows = _execute_read_query(connection, payload.query_text, 1)
        fields = _infer_query_origins(connection, payload.query_text, fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Query execution failed. Review the query and source connection.") from exc
    query = SourceDataQuery(
        id=uuid4(), workspace_id=workspace_id, connection_id=connection.id,
        query_text=_validate_read_query(payload.query_text), output_fields=fields, created_by=principal.subject,
    )
    db.add(query)
    record_audit(db, principal, request.state.request_id, "source_query.executed", "source_data_query", str(query.id), workspace_id, role,
                 {"connection_id": str(connection.id), "field_count": len(fields), "has_rows": bool(rows)})
    db.commit()
    db.refresh(query)
    return {"query_id": str(query.id), "connection_id": str(connection.id), "output_fields": query.output_fields,
            "row_preview_available": bool(rows), "values_included": False, "executed_at": query.created_at}


@router.post("/workspaces/{workspace_id}/source-data/query/generate", status_code=202)
def generate_from_source_query(
    workspace_id: UUID,
    payload: GenerateFromQueryRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict:
    role = require_editor(db, principal, workspace_id)
    ensure_user(db, principal)
    if not payload.confirm_source_extraction:
        raise HTTPException(status_code=409, detail="Confirm the query and bounded source extraction before generation")
    query = db.get(SourceDataQuery, payload.query_id)
    if query is None or query.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Executed source query not found")
    connection = db.get(WorkspaceConnection, query.connection_id)
    if (connection is None or connection.workspace_id != workspace_id or connection.connector_type != "postgresql"
            or connection.status != "enabled" or connection.direction not in ("source", "both")
            or connection.operation_scope not in ("read_only", "read_write")):
        raise HTTPException(status_code=409, detail="The query source is no longer enabled for reading")
    if payload.target_connection_id:
        target = db.get(WorkspaceConnection, payload.target_connection_id)
        if (target is None or target.workspace_id != workspace_id or target.status != "enabled"
                or target.direction not in ("target", "both") or target.operation_scope not in ("write_only", "read_write")):
            raise HTTPException(status_code=422, detail="Choose an enabled writable target in this workspace")
    discovered_fields = query.output_fields
    if not discovered_fields or len(discovered_fields) > 200:
        raise HTTPException(status_code=409, detail="Execute the query again to discover its output fields")
    discovered_names = [item["name"] for item in discovered_fields]
    if ([item.source_field for item in payload.output_fields] != discovered_names
            or len({item.output_field for item in payload.output_fields}) != len(payload.output_fields)):
        raise HTTPException(status_code=422, detail="Output names must be unique and match the executed query fields")
    structure_objects = (connection.structure_preview or {}).get("objects", [])
    effective_discovered_fields = [dict(item) for item in discovered_fields]
    for index, mapping in enumerate(payload.output_fields):
        if bool(mapping.source_object) != bool(mapping.source_column):
            raise HTTPException(status_code=422, detail="Choose both a source object and source field for each query output, or leave both unmapped")
        if not mapping.source_object:
            continue
        candidates = [item for item in structure_objects
                      if item.get("name") == mapping.source_object
                      or str(item.get("name", "")).split(".")[-1] == mapping.source_object.split(".")[-1]]
        if len(candidates) != 1:
            raise HTTPException(status_code=422, detail=f"Source object for query output {mapping.source_field} is not in the approved connection schema")
        source_object = candidates[0]
        source_columns = [item for item in source_object.get("fields", []) if item.get("name") == mapping.source_column]
        if len(source_columns) != 1:
            raise HTTPException(status_code=422, detail=f"Source field for query output {mapping.source_field} is not in the approved connection schema")
        effective_discovered_fields[index].update({
            "source_object": source_object["name"], "source_field": mapping.source_column,
            "primary_key": bool(source_columns[0].get("primary_key", False)),
        })
    # Keep reviewed origins with the executed query so later replay and privacy
    # validation rebuild the same entity identity mapping.
    query.output_fields = effective_discovered_fields
    fields = [{"name": item.source_field, "output_name": item.output_field,
               "data_type": effective_discovered_fields[index]["data_type"]}
              for index, item in enumerate(payload.output_fields)]
    job_id = uuid4()
    description = f"Source to Data · query {query.id.hex[:8]}"
    draft = DraftDataSet(
        name=payload.dataset_name, description=description, creation_pathway="source_to_data",
        schema_scope=[SchemaField(
            field=item["output_name"], data_type=item["data_type"],
            entity=effective_discovered_fields[index].get("source_object"),
            source_field=effective_discovered_fields[index].get("source_field"),
        ) for index, item in enumerate(fields)],
        selected_fields=[item["output_name"] for item in fields],
        provenance=Provenance(origin="source_backed", source_connection_id=connection.id, source_object="query",
            source_schema_version="source-query-v1", source_query_id=query.id, job_id=job_id,
            test_data_request_id=payload.test_data_request_id),
        permitted_use=[payload.target_use], maximum_records=payload.volume, output_format=payload.format, expiry_at=payload.expiry_at,
    )
    job = Job(id=job_id, job_type="source_query_generation", status="queued", workspace_id=str(workspace_id),
        scope_key=f"workspace:{workspace_id}", idempotency_key="", actor_type=principal.actor_type,
        actor_id=principal.subject, actor_role=role.value, request_id=request.state.request_id, progress=0,
        safe_summary="Source query generation queued")
    dataset, version = _create_or_reuse_source_dataset(db, principal, workspace_id, payload.draft_dataset_id, draft,
                                                       request.state.request_id, payload.base_dataset_id,
                                                       payload.base_version_number)
    link_generation_to_ticket(db, payload.test_data_request_id, workspace_id, dataset, version, principal, role)
    job.idempotency_key = (f"source-query-edit:{dataset.id}:v{version.version_number}" if payload.base_dataset_id else
                           f"source-query-draft:{dataset.id}" if payload.draft_dataset_id else f"source-query:{dataset.id}")
    db.add(job)
    db.flush()
    record_audit(db, principal, request.state.request_id, "source_query.generation_requested", "dataset", str(dataset.id), workspace_id, role,
        {"query_id": str(query.id), "selected_field_count": len(fields), "volume": payload.volume, "format": payload.format,
         "target_connection_id": str(payload.target_connection_id) if payload.target_connection_id else None})
    outbox_event = OutboxEvent(id=uuid4(), aggregate_type="dataset", aggregate_id=str(dataset.id), workspace_id=str(workspace_id),
        event_type="source_query.generation_requested", payload={"query_id": str(query.id), "job_id": str(job.id),
        "dataset_id": str(dataset.id), "version_id": str(version.id), "volume": payload.volume, "output_format": payload.format,
        "output_fields": [item.model_dump() for item in payload.output_fields],
        "target_connection_id": str(payload.target_connection_id) if payload.target_connection_id else None}, request_id=request.state.request_id)
    if get_settings().inline_jobs:
        # Inline mode owns the job after commit; do not let the outbox worker race it.
        outbox_event.published_at = datetime.now(UTC)
    db.add(outbox_event)
    db.commit()
    if get_settings().inline_jobs:
        background_tasks.add_task(run_source_query_job, query.id, job.id, dataset.id, version.id, payload.volume,
                                  payload.format, [item.model_dump() for item in payload.output_fields], payload.target_connection_id)
    return {"dataset_id": str(dataset.id), "dataset_reference_code": dataset.reference_code, "version_number": version.version_number, "job_id": str(job.id),
        "request_id": job.request_id, "status": "queued", "pathway": "Source to Data · Query"}


def require_editor(db: Session, principal: Principal, workspace_id: UUID) -> HumanRole:
    role = workspace_role(db, principal, workspace_id)
    if principal.actor_type != "human" or role != HumanRole.TEST_DATA_ENGINEER:
        raise HTTPException(status_code=403, detail="Source to Data requires a Test Data Engineer")
    return role


def _create_or_reuse_source_dataset(
    db: Session,
    principal: Principal,
    workspace_id: UUID,
    draft_dataset_id: UUID | None,
    payload: DraftDataSet,
    correlation_id: str,
    base_dataset_id: UUID | None = None,
    base_version_number: int | None = None,
) -> tuple[DataSet, DataSetVersion]:
    """Use the draft opened by Create Draft, so generation does not fork a second data set."""
    if bool(base_dataset_id) != bool(base_version_number):
        raise HTTPException(status_code=422, detail="Choose both a base data set and version to generate an edited version")
    if base_dataset_id is not None:
        if draft_dataset_id is not None:
            raise HTTPException(status_code=422, detail="A new-version edit cannot also target an empty draft")
        dataset = db.scalar(select(DataSet).where(DataSet.id == base_dataset_id).with_for_update())
        if dataset is None or dataset.workspace_id != workspace_id or dataset.creation_pathway != "source_to_data":
            raise HTTPException(status_code=404, detail="Source to Data base data set not found")
        base_version = db.scalar(select(DataSetVersion).where(
            DataSetVersion.dataset_id == dataset.id,
            DataSetVersion.version_number == base_version_number,
        ))
        if base_version is None:
            raise HTTPException(status_code=404, detail="Source to Data base version not found")
        is_published = base_version.lifecycle_state == "published" and base_version.publication_state == "published"
        is_ready = base_version.lifecycle_state == "ready"
        is_quarantined = base_version.lifecycle_state == "quarantined"
        is_failed_source_version = (base_version.lifecycle_state == "failed"
                                    and (base_version.provenance or {}).get("origin") == "source_backed")
        is_masked_draft = (base_version.lifecycle_state == "draft" and base_version.validation_state == "passed"
                           and bool((base_version.validation_evidence or {}).get("masking_run_id")))
        if not (is_published or is_ready or is_quarantined or is_failed_source_version or is_masked_draft):
            raise HTTPException(status_code=409, detail="A new editable version must be based on a published, ready, quarantined, failed source-backed, or successfully masked version")
        if (base_version.provenance or {}).get("origin") != "source_backed":
            raise HTTPException(status_code=409, detail="The selected base version is not source-backed")
        dataset.name = payload.name
        dataset.description = payload.description
        return dataset, create_version(db, dataset, payload, principal, correlation_id)
    if draft_dataset_id is None:
        dataset = DataSet(
            id=uuid4(), workspace_id=workspace_id, name=payload.name,
            description=payload.description, owner_id=principal.subject,
            creation_pathway="source_to_data", latest_version_number=1,
        )
        db.add(dataset)
        db.flush()
        return dataset, create_initial_version(db, dataset, payload, principal, correlation_id)

    dataset = db.scalar(
        select(DataSet).where(DataSet.id == draft_dataset_id).with_for_update()
    )
    if (dataset is None or dataset.workspace_id != workspace_id
            or dataset.creation_pathway != "source_to_data" or dataset.owner_id != principal.subject):
        raise HTTPException(status_code=404, detail="Source to Data draft not found")
    version = db.scalar(
        select(DataSetVersion).where(DataSetVersion.dataset_id == dataset.id)
        .order_by(DataSetVersion.version_number.desc()).limit(1).with_for_update()
    )
    provenance = version.provenance or {} if version else {}
    if (version is None or version.version_number != 1 or version.lifecycle_state != "draft"
            or provenance.get("origin") != "not_set" or version.schema_scope or version.selected_fields
            or version.record_count is not None or version.validation_evidence):
        raise HTTPException(status_code=409, detail="This draft already has generation data. Create a fresh draft to generate again.")

    dataset.name = payload.name
    dataset.description = payload.description
    version.schema_scope = [field.model_dump() for field in payload.schema_scope]
    version.selected_fields = payload.selected_fields
    version.provenance = provenance_with_lock_configuration(
        db, payload.provenance.model_dump(mode="json", exclude_none=True), dataset.workspace_id,
    )
    version.generation_settings = {
        "target_use": payload.permitted_use[0] if payload.permitted_use else (payload.description or ""),
        "maximum_records": payload.maximum_records,
        "output_format": payload.output_format,
    }
    version.expiry_at = payload.expiry_at
    version.permitted_use = payload.permitted_use
    version.fields = _dataset_fields(dataset, version, payload)
    db.flush()
    return dataset, version


def source_plan_out(plan: SourceDataPlan) -> dict:
    return {"id": plan.id, "workspace_id": plan.workspace_id, "name": plan.name,
            "state": plan.state, "plan_data": plan.plan_data, "created_by": plan.created_by,
            "created_at": plan.created_at, "updated_at": plan.updated_at}


def validate_and_version_source_plan(db: Session, workspace_id: UUID, plan_data: dict[str, Any]) -> dict[str, Any]:
    """Reauthorize and snapshot federated plan metadata before it is persisted."""
    data = dict(plan_data)
    raw = data.get("federatedPlan")
    if not raw:
        return data
    try:
        # Saved plans include TIGR-generated versioning and execution metadata.
        # Validate only the editable plan fields so re-saving an existing plan works.
        federated = FederatedPlanValidation.model_validate(
            {key: raw[key] for key in ("sources", "joins", "row_limit") if key in raw}
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Federated source plan is invalid: {exc}") from exc
    validated = _validate_federated_plan(db, workspace_id, federated)
    canonical = federated.model_dump(mode="json")
    plan_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    data["federatedPlan"] = {**canonical, "plan_version": 1, "plan_hash": plan_hash,
        "source_reads": validated["sources"], "execution_supported": validated["execution_supported"],
        "limits": {"row_limit_per_source": validated["row_limit_per_source"],
                   "join_output_row_limit": validated["join_output_row_limit"]}}
    return data


@router.get("/workspaces/{workspace_id}/source-plans")
def list_source_plans(workspace_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> list[dict]:
    role = workspace_role(db, principal, workspace_id)
    if principal.actor_type != "human" or role not in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER):
        raise HTTPException(status_code=403, detail="Source plans require an Admin or Test Data Engineer")
    plans = db.scalars(select(SourceDataPlan).where(SourceDataPlan.workspace_id == workspace_id)
                       .order_by(SourceDataPlan.updated_at.desc()).limit(50)).all()
    return [source_plan_out(plan) for plan in plans]


@router.post("/workspaces/{workspace_id}/source-plans", status_code=201)
def create_source_plan(workspace_id: UUID, payload: SourcePlanCreate, request: Request,
                       db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict:
    role = require_editor(db, principal, workspace_id)
    ensure_user(db, principal)
    plan_data = validate_and_version_source_plan(db, workspace_id, payload.plan_data)
    plan = SourceDataPlan(id=uuid4(), workspace_id=workspace_id, name=payload.name,
                          state=payload.state, plan_data=plan_data, created_by=principal.subject)
    db.add(plan)
    db.flush()
    record_audit(db, principal, request.state.request_id, "source_plan.created", "source_data_plan",
                 str(plan.id), workspace_id, role, {"name": plan.name, "state": plan.state})
    db.commit()
    db.refresh(plan)
    return source_plan_out(plan)


@router.get("/source-plans/{plan_id}")
def read_source_plan(plan_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict:
    plan = db.get(SourceDataPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Source plan not found")
    role = workspace_role(db, principal, plan.workspace_id)
    if principal.actor_type != "human" or role not in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER):
        raise HTTPException(status_code=404, detail="Source plan not found")
    return source_plan_out(plan)


@router.patch("/source-plans/{plan_id}")
def update_source_plan(plan_id: UUID, payload: SourcePlanUpdate, request: Request,
                       db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict:
    plan = db.get(SourceDataPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Source plan not found")
    role = require_editor(db, principal, plan.workspace_id)
    changes = payload.model_dump(exclude_unset=True)
    if "plan_data" in changes:
        changes["plan_data"] = validate_and_version_source_plan(db, plan.workspace_id, changes["plan_data"] or {})
    for field, value in changes.items():
        setattr(plan, field, value)
    record_audit(db, principal, request.state.request_id, "source_plan.updated", "source_data_plan",
                 str(plan.id), plan.workspace_id, role, {"name": plan.name, "state": plan.state})
    db.commit()
    db.refresh(plan)
    return source_plan_out(plan)


@router.post("/source-plans/{plan_id}/quarantine")
def quarantine_source_draft(plan_id: UUID, request: Request,
                            db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict:
    plan = db.get(SourceDataPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Source plan not found")
    role = require_editor(db, principal, plan.workspace_id)
    plan_data = plan.plan_data or {}
    job_id = plan_data.get("generationJobId")
    if job_id:
        try:
            job = db.get(Job, UUID(str(job_id)))
        except (TypeError, ValueError):
            job = None
        if job and job.status in ("queued", "running", "validating"):
            raise HTTPException(status_code=409, detail="Wait for source generation to finish before quarantining this draft.")

    dataset_id = None
    if plan_data.get("draftDatasetId"):
        try:
            dataset_id = UUID(str(plan_data["draftDatasetId"]))
        except (TypeError, ValueError):
            raise HTTPException(status_code=409, detail="The linked draft data set reference is invalid.")

    if dataset_id is not None:
        dataset = db.get(DataSet, dataset_id)
        if dataset is None or dataset.workspace_id != plan.workspace_id or dataset.creation_pathway != "source_to_data":
            raise HTTPException(status_code=404, detail="Source draft data set not found")
        version = db.scalar(select(DataSetVersion).where(
            DataSetVersion.dataset_id == dataset.id,
            DataSetVersion.version_number == dataset.latest_version_number,
        ).with_for_update())
        if version is None or version.lifecycle_state != "draft":
            raise HTTPException(status_code=409, detail="Only a Draft version can be moved to Quarantined.")
        previous_state = version.lifecycle_state
        version.lifecycle_state = "quarantined"
        dataset.updated_at = datetime.now(UTC)
        record_status_change(db, dataset, version, previous_state, "quarantined", principal, request.state.request_id,
                             "Draft version moved to quarantine by its owner")
        append_lineage_event(db, dataset, version, LineageEventCreate(
            event_type="draft_quarantined", stage="governance", outcome="warning",
            safe_evidence={"summary": "Draft version was moved to quarantine by its owner"},
        ), principal)

    record_audit(db, principal, request.state.request_id, "source_plan.draft_quarantined", "source_data_plan",
                 str(plan.id), plan.workspace_id, role,
                 {"dataset_id": str(dataset_id) if dataset_id else None, "dataset_version_quarantined": dataset_id is not None})
    db.delete(plan)
    db.commit()
    return {"id": str(plan_id), "deleted": True, "dataset_id": str(dataset_id) if dataset_id else None,
            "dataset_quarantined": dataset_id is not None}


def mapping_out(row: SourceDataMapping) -> dict:
    return {"id": row.id, "workspace_id": row.workspace_id, "connection_id": row.connection_id,
            "name": row.name, "source_object": row.source_object, "version_number": row.version_number,
            "version": f"{row.name}:v{row.version_number}", "fields": row.fields,
            "relationships": row.relationships, "source_joins": row.source_joins or [],
            "created_by": row.created_by, "created_at": row.created_at}


@router.get("/workspaces/{workspace_id}/source-data-sources")
def source_data_sources(workspace_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> list[dict]:
    role = workspace_role(db, principal, workspace_id)
    if principal.actor_type != "human" or role == HumanRole.QA_ENGINEER:
        raise HTTPException(status_code=403, detail="Source metadata requires an Admin or Test Data Engineer")
    rows = db.scalars(select(WorkspaceConnection).where(WorkspaceConnection.workspace_id == workspace_id,
        WorkspaceConnection.status == "enabled", WorkspaceConnection.direction.in_(("source", "both")),
        WorkspaceConnection.operation_scope.in_(("read_only", "read_write"))).order_by(WorkspaceConnection.name)).all()
    output = []
    for connection in rows:
        objects = (connection.structure_preview or {}).get("objects", []) if connection.connector_type in ("postgresql", "mysql") else [
            {"name": (connection.connector_config or {}).get("file_path", connection.name), "fields": connection_fields(connection)}]
        by_database: dict[str, list[dict[str, Any]]] = {}
        for item in objects:
            database = item.get("database")
            schema = item.get("schema")
            by_database.setdefault(database or "", []).append({
                "name": item.get("name", "").split(".")[-1], "path": item.get("name"),
                "database": database, "schema": schema,
                "type": item.get("type", "source object"), "fields": item.get("fields", []),
            })
        output.append({"id": connection.id, "name": connection.name, "connector_type": connection.connector_type,
                       "environment": connection.environment, "status": connection.status,
                       "last_validated_at": connection.tested_at,
                       "structure_version": (connection.structure_preview or {}).get("structure_snapshot_version"),
                       "databases": [{"name": name, "objects": source_objects} for name, source_objects in sorted(by_database.items())],
                       "objects": [{"name": item.get("name"), "type": item.get("type", "source object"), "fields": item.get("fields", [])} for item in objects]})
    return output


@router.post("/workspaces/{workspace_id}/federated-plans/validate")
def validate_federated_plan(workspace_id: UUID, payload: FederatedPlanValidation, request: Request,
                            db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    role = require_editor(db, principal, workspace_id)
    try:
        plan = _validate_federated_plan(db, workspace_id, payload)
    except HTTPException:
        record_audit(db, principal, request.state.request_id, "federated_plan.validation_failed", "workspace",
                     str(workspace_id), workspace_id, role, {"reason": "plan_rejected"})
        db.commit()
        raise
    record_audit(db, principal, request.state.request_id, "federated_plan.validated", "workspace",
                 str(workspace_id), workspace_id, role,
                 {"source_count": len(plan["sources"]), "join_count": len(plan["joins"]),
                  "execution_supported": True, "row_limit_per_source": plan["row_limit_per_source"]})
    db.commit()
    return plan


@router.post("/workspaces/{workspace_id}/federated-plans/execute", status_code=202)
def execute_federated_plan(workspace_id: UUID, payload: FederatedGenerateRequest, request: Request,
                           background_tasks: BackgroundTasks, db: Session = Depends(get_db),
                           principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    """Queue a bounded federated source read against a saved, reviewed plan."""
    role = require_editor(db, principal, workspace_id)
    ensure_user(db, principal)
    if not payload.confirm_source_extraction:
        raise HTTPException(status_code=409, detail="Confirm the reviewed federated source set and bounded extraction before continuing")
    saved = db.get(SourceDataPlan, payload.source_plan_id)
    if saved is None or saved.workspace_id != workspace_id or saved.state != "saved":
        raise HTTPException(status_code=404, detail="Saved federated source plan not found")
    plan_data = validate_and_version_source_plan(db, workspace_id, saved.plan_data or {})
    raw = plan_data.get("federatedPlan") or {}
    if raw.get("execution_supported") is not True:
        raise HTTPException(status_code=409, detail="This saved plan is not executable. Validate and save it again before generating.")
    fed = FederatedPlanValidation.model_validate({key: raw[key] for key in ("sources", "joins", "row_limit") if key in raw})
    _validate_federated_plan(db, workspace_id, fed)
    schema_scope: list[SchemaField] = []
    selected_fields: list[str] = []
    for source in fed.sources:
        connection = db.get(WorkspaceConnection, source.connection_id)
        item = _federated_object(db, connection, source)
        columns = {str(field.get("name")): field for field in item.get("fields", [])}
        path = f"{connection.name}/{source.database}/{source.schema}/{source.object_name}"
        for source_field in source.fields:
            target_field = f"{source.alias}__{source_field}"
            if len(target_field) > 180 or target_field in selected_fields:
                raise HTTPException(status_code=422, detail="Federated output field names must be unique and no longer than 180 characters")
            selected_fields.append(target_field)
            schema_scope.append(SchemaField(field=target_field, data_type=str(columns[source_field].get("type") or "text")[:80],
                entity=path[:180], source_field=source_field, json_path=f"{source.alias}.{source_field}"))
    draft = DraftDataSet(name=payload.dataset_name, description=f"Federated source plan · {saved.name}",
        creation_pathway="source_to_data", maximum_records=payload.volume, output_format=payload.format,
        schema_scope=schema_scope, selected_fields=selected_fields,
        provenance=Provenance(origin="source_backed", source_schema_version=f"federated:{raw.get('plan_version', 1)}",
            source_plan_id=saved.id, federated_plan_hash=str(raw.get("plan_hash") or ""),
            job_id=uuid4(), test_data_request_id=payload.test_data_request_id),
        permitted_use=[payload.target_use], expiry_at=payload.expiry_at)
    dataset, version = _create_or_reuse_source_dataset(db, principal, workspace_id, payload.draft_dataset_id,
        draft, request.state.request_id, payload.base_dataset_id, payload.base_version_number)
    link_generation_to_ticket(db, payload.test_data_request_id, workspace_id, dataset, version, principal, role)
    job_id = UUID(str(version.provenance.get("job_id") or uuid4()))
    version.provenance = {**(version.provenance or {}), "job_id": str(job_id)}
    job = Job(id=job_id, job_type="federated_generation", status="queued", workspace_id=str(workspace_id),
        scope_key=f"workspace:{workspace_id}", idempotency_key=f"federated:{saved.id}:{dataset.id}:v{version.version_number}",
        actor_type=principal.actor_type, actor_id=principal.subject, actor_role=role.value,
        request_id=request.state.request_id, progress=0, safe_summary="Federated generation queued")
    db.add(job)
    db.add(OutboxEvent(id=uuid4(), aggregate_type="dataset", aggregate_id=str(dataset.id), workspace_id=str(workspace_id),
        event_type="federated.generation_requested", payload={"source_plan_id": str(saved.id), "job_id": str(job_id),
            "dataset_id": str(dataset.id), "version_id": str(version.id), "volume": payload.volume,
            "output_format": payload.format}, request_id=request.state.request_id))
    # The lineage row has a foreign key to jobs; flush the new job before
    # appending that event so PostgreSQL sees the referenced job first.
    db.flush()
    record_audit(db, principal, request.state.request_id, "federated_generation.requested", "dataset",
        str(dataset.id), workspace_id, role, {"source_plan_id": str(saved.id), "source_count": len(fed.sources),
        "join_count": len(fed.joins), "plan_hash": raw.get("plan_hash"), "volume": payload.volume,
        "source_connection_ids": sorted({str(source.connection_id) for source in fed.sources})})
    append_lineage_event(db, dataset, version, LineageEventCreate(event_type="federated_generation_requested",
            stage="generation", job_id=job_id, source_reference=str(saved.id), mapping_version=f"{saved.name}:v{raw.get('plan_version', 1)}"[:180],
        safe_evidence={"source_count": len(fed.sources), "join_count": len(fed.joins), "plan_hash": str(raw.get("plan_hash") or "")}), principal)
    db.commit()
    if get_settings().inline_jobs:
        background_tasks.add_task(run_federated_generation_job, saved.id, job_id, dataset.id, version.id,
                                  payload.volume, payload.format)
    return {"dataset_id": str(dataset.id), "dataset_reference_code": dataset.reference_code,
        "version_number": version.version_number, "job_id": str(job_id), "request_id": job.request_id,
        "status": "queued", "pathway": "Federated Source to Data", "source_plan_id": str(saved.id)}


@router.get("/workspaces/{workspace_id}/source-mappings")
def list_source_mappings(workspace_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> list[dict]:
    role = workspace_role(db, principal, workspace_id)
    if principal.actor_type != "human" or role == HumanRole.QA_ENGINEER:
        raise HTTPException(status_code=403, detail="Source mappings require an Admin or Test Data Engineer")
    rows = db.scalars(select(SourceDataMapping).where(SourceDataMapping.workspace_id == workspace_id).order_by(SourceDataMapping.name, SourceDataMapping.version_number.desc())).all()
    latest: dict[tuple[str, UUID, str], SourceDataMapping] = {}
    for row in rows:
        key = (row.name, row.connection_id, row.source_object)
        if key not in latest:
            latest[key] = row
    return [mapping_out(row) for row in latest.values()]


@router.post("/workspaces/{workspace_id}/source-mappings", status_code=201)
def create_source_mapping(workspace_id: UUID, payload: MappingCreate, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict:
    role = require_editor(db, principal, workspace_id)
    ensure_user(db, principal)
    connection = db.get(WorkspaceConnection, payload.connection_id)
    if not connection or connection.workspace_id != workspace_id or connection.status != "enabled" or connection.direction not in ("source", "both") or connection.operation_scope not in ("read_only", "read_write"):
        raise HTTPException(status_code=422, detail="Choose an enabled, readable source in this workspace")
    structure_objects = (connection.structure_preview or {}).get("objects", []) if connection.connector_type in ("postgresql", "mysql") else [{"name": payload.source_object}]
    available_objects = {str(item.get("name")) for item in structure_objects}
    source_objects = {payload.source_object, *(item.source_object or payload.source_object for item in payload.fields)}
    if not source_objects <= available_objects or not connection_fields(connection, payload.source_object):
        raise HTTPException(status_code=422, detail="Choose an object from the approved source structure")
    fields = [item.model_dump(exclude_none=True) for item in payload.fields]
    if len({(item.get("source_object", payload.source_object), item["source_field"]) for item in fields}) != len(fields) or len({item["target_field"] for item in fields}) != len(fields):
        raise HTTPException(status_code=422, detail="Mapped source and output fields must be unique")
    if any(item["source_field"] not in {str(field.get("name")) for field in connection_fields(connection, item.get("source_object", payload.source_object))} for item in fields):
        raise HTTPException(status_code=422, detail="Every mapping source must exist in approved structure metadata")
    joins = [item.model_dump() for item in payload.source_joins]
    for join in joins:
        if join["left_object"] not in source_objects or join["right_object"] not in source_objects or join["left_object"] == join["right_object"]:
            raise HTTPException(status_code=422, detail="Source joins must connect two selected source objects")
        left_fields = {str(field.get("name")) for field in connection_fields(connection, join["left_object"])}
        right_fields = {str(field.get("name")) for field in connection_fields(connection, join["right_object"])}
        if join["left_field"] not in left_fields or join["right_field"] not in right_fields:
            raise HTTPException(status_code=422, detail="Source join keys must exist in approved structure metadata")
    connected = {payload.source_object}
    while True:
        expanded = connected | {join["right_object"] if join["left_object"] in connected else join["left_object"] for join in joins if join["left_object"] in connected or join["right_object"] in connected}
        if expanded == connected:
            break
        connected = expanded
    if connected != source_objects:
        raise HTTPException(status_code=422, detail="Add join keys connecting every selected source object")
    field_names = {item["target_field"] for item in fields}
    relationships = [item.model_dump() for item in payload.relationships]
    if any(item["parent_field"] not in field_names or item["child_field"] not in field_names for item in relationships):
        raise HTTPException(status_code=422, detail="Relationship keys must be included in the output mapping")
    latest = db.scalar(select(SourceDataMapping).where(SourceDataMapping.workspace_id == workspace_id, SourceDataMapping.name == payload.name).order_by(SourceDataMapping.version_number.desc()).limit(1))
    if latest and (latest.connection_id != connection.id or latest.source_object != payload.source_object):
        raise HTTPException(status_code=409, detail="A mapping name stays attached to its original source object")
    row = SourceDataMapping(id=uuid4(), workspace_id=workspace_id, connection_id=connection.id, name=payload.name,
        source_object=payload.source_object, version_number=(latest.version_number + 1 if latest else 1), fields=fields,
        relationships=relationships, source_joins=joins, created_by=principal.subject)
    db.add(row)
    record_audit(db, principal, request.state.request_id, "source_mapping.version_created", "source_data_mapping", str(row.id), workspace_id, role,
                 {"name": row.name, "version_number": row.version_number, "field_count": len(fields), "source_object": row.source_object})
    db.commit(); db.refresh(row)
    return mapping_out(row)


def _persist_unmasked_source_rows(db: Session, dataset: DataSet, version: DataSetVersion,
                                  rows: list[dict], fields: list, connection: WorkspaceConnection,
                                  output_format: str) -> dict[str, Any]:
    """Persist validated source rows directly when this workspace has masking disabled."""
    from tigr.record_identity import capture_entity_lineage

    lineage_refs = capture_entity_lineage(db, dataset, version, rows, fields, connection.id)
    output_fields = set(version.selected_fields or [
        (item.model_dump() if hasattr(item, "model_dump") else item).get("target_field") for item in fields
    ])
    for row in rows:
        for field_name in set(row) - output_fields:
            row.pop(field_name, None)
    digest, _size_bytes = store_dataset_records(
        db, dataset, version, rows, masking_complete=True, lineage_refs=lineage_refs,
    )
    artifact_ref = f"artifact://{dataset.id}/v{version.version_number}/dataset.{output_format}"
    artifact_bytes = serialize_dataset_records(rows, output_format, version.selected_fields)
    record_generated_artifact(
        db, dataset, version, len(artifact_bytes), artifact_ref,
        "text/csv" if output_format == "csv" else "application/json", hashlib.sha256(artifact_bytes).hexdigest(),
    )
    return {"artifact_ref": artifact_ref, "hash": digest, "artifact_size_bytes": len(artifact_bytes)}


def _source_generation_failure_summary(exc: Exception) -> str:
    """Return actionable failure guidance without exposing connection details."""
    if isinstance(exc, ValueError):
        messages = {
            "source_unavailable": "The selected source connection is disabled or unavailable. Enable it and test the connection before retrying.",
            "required_field_missing": "The mapping omits a required field. Add the required field in Map Fields and retry.",
            "relationship_dependency_missing": "A relationship key is not selected. Include both relationship fields or remove that relationship.",
            "source_row_limit_exceeded": "A source returned more rows than the approved cap. Narrow the source selection or reduce the row limit and retry.",
            "source_byte_limit_exceeded": "Source results exceeded the staging byte cap. Select fewer fields or reduce the row limit and retry.",
            "staging_byte_limit_exceeded": "Federated staging exceeded the byte cap. Select fewer fields, reduce the row limit, or add source filters.",
            "join_output_row_limit_exceeded": "The join exceeded the output row cap. Review key uniqueness, add filters, or reduce the requested output size.",
            "federated_output_validation_failed": "Federated rows did not match the reviewed output schema. Refresh source structure and review the plan.",
            "fresh_federated_output_failed_validation": "Fresh federated rows did not match the reviewed output schema. Refresh source structure and review the plan.",
            "job_cancelled": "Federated generation was cancelled and temporary source rows were discarded.",
        }
        return messages.get(str(exc), str(exc)[:400])

    original = getattr(exc, "orig", exc)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate == "42P01":
        return "PostgreSQL could not find a source table used by this mapping. Check the table and schema names, then refresh the connection structure and mapping."
    if sqlstate == "42703":
        return "PostgreSQL could not find a mapped source field. Refresh the connection structure, update the field mapping, and retry."
    if sqlstate == "42501":
        return "The source database denied read access. Grant SELECT access to the configured database user, test the connection, and retry."
    if sqlstate == "57014":
        return "The source query timed out. Narrow the filters or reduce the requested record count, then retry."
    if isinstance(sqlstate, str) and sqlstate.startswith("08"):
        return "TIGR lost its database connection while reading source data. Check source availability, then retry generation."
    if isinstance(exc, SQLAlchemyError):
        return ("TIGR could not complete source data extraction. Review the selected objects and mapped fields, then retry. "
                "The underlying database detail is hidden to protect source data.")
    return "Source generation stopped unexpectedly. Review the selected objects, mapped fields, join keys, and source connection, then retry."


def _source_query_failure_summary(exc: Exception) -> str:
    """Describe query-worker failures without returning SQL, values, or raw driver details."""
    value = str(exc) if isinstance(exc, ValueError) else ""
    value_messages = {
        "query_source_unavailable": "The saved query's source connection is no longer enabled for read access. Enable and test the connection, then retry.",
        "query_output_mapping_changed": "The saved output-field mapping no longer matches the executed query. Re-run Execute Query, review the output fields, and regenerate.",
        "query_output_schema_changed": "The query now returns different output fields than the saved execution. Re-run Execute Query and review the output-field mapping.",
        "The query must return rows and output columns": "The query returned no output columns. Update it to return rows and columns, then run Execute Query again.",
        "Query output must contain 1–200 uniquely named columns, each 180 characters or less": "The query output must contain 1–200 uniquely named columns, each no longer than 180 characters. Update the query and re-run Execute Query.",
    }
    if value in value_messages:
        return value_messages[value]
    if value == "identity_secret_unavailable":
        return "Record identity locking is enabled, but TIGR's identity secret is not configured. Configure a stable TIGR_IDENTITY_SECRET and retry generation."
    if isinstance(exc, HTTPException) and exc.status_code == 409 and exc.detail == (
        "Source records are temporarily unavailable under another active lock. Retry after the lease expires or is released, or contact your workspace Admin or Test Data Engineer."
    ):
        return str(exc.detail)
    original = getattr(exc, "orig", exc)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate == "42P01":
        return "PostgreSQL could not find a table or view used by the query. Check the schema and object names, then re-run Execute Query."
    if sqlstate == "42703":
        return "PostgreSQL could not find a field used by the query. Check the source field names, then re-run Execute Query."
    if sqlstate == "42601":
        return "PostgreSQL rejected the query syntax. Review the SQL and re-run Execute Query before generating a version."
    if sqlstate == "42501":
        return "The source database denied read access to the query. Check SELECT permissions for the configured connection user."
    if sqlstate == "57014":
        return "The source query timed out. Narrow the query or reduce its workload, then retry."
    if isinstance(sqlstate, str) and sqlstate.startswith("08"):
        return "TIGR lost its database connection while running the source query. Check source availability, then re-run Execute Query."
    if isinstance(exc, SQLAlchemyError):
        return ("TIGR could not complete the source query. Re-run Execute Query to confirm the query and source access; "
                "the underlying database detail is hidden to protect query and record data.")
    if value in {
        "Enter one read-only query or procedure call", "Only SELECT, WITH, or read-only CALL statements are supported",
        "Query contains an unterminated quoted value or comment", "Query contains an unterminated comment",
        "Source secret must resolve to a PostgreSQL URL",
    }:
        return value
    return ("TIGR could not complete the saved read-only query. Re-run Execute Query to confirm the query and source access; "
            "the raw database error is hidden to avoid exposing query or record data.")


def run_federated_generation_job(source_plan_id: UUID, job_id: UUID, dataset_id: UUID, version_id: UUID,
                                 volume: int, output_format: str) -> None:
    """Execute a saved federation in worker memory and retain only approved output."""
    db = SessionLocal()
    rows: list[dict[str, Any]] = []
    try:
        from tigr.entity_claims import assert_job_current, assert_source_namespace_available
        from tigr.federation import execute_federated_reads

        saved = db.get(SourceDataPlan, source_plan_id)
        job = db.get(Job, job_id)
        dataset = db.get(DataSet, dataset_id)
        version = db.get(DataSetVersion, version_id)
        if not all((saved, job, dataset, version)):
            return
        if saved.workspace_id != dataset.workspace_id or saved.workspace_id != UUID(job.workspace_id):
            raise ValueError("federated_plan_scope_invalid")
        current = validate_and_version_source_plan(db, saved.workspace_id, saved.plan_data or {})
        raw = current.get("federatedPlan") or {}
        if raw.get("execution_supported") is not True:
            raise ValueError("federated_plan_not_executable")
        payload = FederatedPlanValidation.model_validate({key: raw[key] for key in ("sources", "joins", "row_limit") if key in raw})
        validated = _validate_federated_plan(db, saved.workspace_id, payload)
        connections: dict[str, WorkspaceConnection] = {}
        for source in payload.sources:
            connection = db.get(WorkspaceConnection, source.connection_id)
            if connection is None:
                raise ValueError("federated_source_unavailable")
            connections[source.alias] = connection
            assert_source_namespace_available(db, connection.id, job.actor_id,
                allow_locked_entities=job.actor_role in (HumanRole.ADMIN.value, HumanRole.TEST_DATA_ENGINEER.value))
        job.status = "running"
        job.progress = 10
        job.safe_summary = "Reading approved federated source fields"
        job.heartbeat_at = datetime.now(UTC)
        db.add(JobStep(id=uuid4(), job_id=job.id, name="source_reads", status="running",
            safe_summary=f"Reading {len(payload.sources)} bounded source projections"))
        db.commit()
        rows, source_counts = execute_federated_reads(connections, validated, validated["joins"],
            row_limit=min(payload.row_limit, volume))
        rows = rows[:volume]
        assert_job_current(db, job.id)
        from tigr.scenario_schemas import MappingField
        mapping = [MappingField(target_field=str(field["field"]), source_field=str(field.get("source_field") or field["field"]),
            data_type=str(field.get("data_type") or "text"), required=bool(field.get("required")),
            source_object=str(field.get("entity") or "federated_source")) for field in version.schema_scope]
        valid, warning_count = validate_rows(rows, mapping, [])
        if not valid:
            raise ValueError("federated_output_validation_failed")
        from tigr.record_identity import capture_entity_lineage
        original_provenance = dict(version.provenance or {})
        lineage_refs: list[dict[str, Any]] = []
        for source in payload.sources:
            connection = connections[source.alias]
            obj = _federated_object(db, connection, source)
            source_mapping = [MappingField(target_field=f"{source.alias}__{field}", source_field=field,
                data_type=str(next((column.get("type") for column in obj.get("fields", []) if column.get("name") == field), "text")),
                required=False, source_object=str(obj.get("name") or source.object_name)) for field in source.fields]
            snapshot = db.scalar(select(ConnectionStructureSnapshot).where(
                ConnectionStructureSnapshot.connection_id == connection.id,
                ConnectionStructureSnapshot.version_number == source.structure_version,
            ))
            lock_config = db.scalar(select(LockPropagationConfiguration).where(
                LockPropagationConfiguration.connection_id == connection.id,
                LockPropagationConfiguration.structure_snapshot_id == (snapshot.id if snapshot else None),
                LockPropagationConfiguration.status == "approved",
            ).order_by(LockPropagationConfiguration.version_number.desc()).limit(1))
            version.provenance = {**original_provenance, "source_connection_id": str(connection.id),
                "lock_metadata": {"source_identity_namespace": str((snapshot.structure or {}).get("source_identity_namespace") or f"connection:{connection.id}") if snapshot else f"connection:{connection.id}",
                    "structure_snapshot_id": str(snapshot.id) if snapshot else "",
                    "lock_configuration_id": str(lock_config.id) if lock_config else ""}}
            lineage_refs.extend(capture_entity_lineage(db, dataset, version, rows, source_mapping, connection.id))
        version.provenance = original_provenance
        pii_enabled = workspace_pii_masking_enabled(db, dataset.workspace_id)
        previous_state = version.lifecycle_state
        version.record_count = len(rows)
        version.validation_state = "passed"
        version.privacy_state = "requires_masking" if pii_enabled else "compliant"
        version.lifecycle_state = "draft" if pii_enabled else "ready"
        version.validation_evidence = {"summary": "Federated source output passed schema checks",
            "record_count": len(rows), "field_count": len(mapping), "warning_count": warning_count,
            "validation_version": "federated-source-v1", "source_plan_id": str(saved.id),
            "source_counts": source_counts}
        artifact_evidence: dict[str, Any] = {}
        if not pii_enabled:
            from tigr.dataset_services import store_dataset_records
            digest, _ = store_dataset_records(db, dataset, version, rows, masking_complete=True, lineage_refs=lineage_refs)
            artifact_ref = f"artifact://{dataset.id}/v{version.version_number}/dataset.{output_format}"
            artifact_bytes = serialize_dataset_records(rows, output_format, version.selected_fields)
            record_generated_artifact(db, dataset, version, len(artifact_bytes), artifact_ref,
                "text/csv" if output_format == "csv" else "application/json", hashlib.sha256(artifact_bytes).hexdigest())
            artifact_evidence = {"artifact_ref": artifact_ref, "hash": digest, "artifact_size_bytes": len(artifact_bytes)}
        service_actor = Principal(subject="tigr-federation-worker", actor_type="service")
        record_status_change(db, dataset, version, previous_state, version.lifecycle_state, service_actor,
            job.request_id, "Federated generation completed")
        record_validation(db, dataset, version, job.request_id, "federated_source_output")
        append_lineage_event(db, dataset, version, LineageEventCreate(event_type="federated_generation_completed",
            stage="generation", job_id=job.id, source_reference=str(saved.id), mapping_version=f"{saved.name}:v{raw.get('plan_version', 1)}"[:180],
            outcome="succeeded", safe_evidence={"record_count": len(rows), "field_count": len(mapping),
                "source_counts": json.dumps(source_counts, sort_keys=True),
                "source_count": len(payload.sources), "join_count": len(payload.joins),
                "plan_hash": str(raw.get("plan_hash") or ""), **artifact_evidence}), service_actor)
        job.progress = 100
        job.status = "succeeded"
        job.heartbeat_at = datetime.now(UTC)
        job.safe_summary = "Federated data set is ready for privacy review" if pii_enabled else "Federated data set generation completed"
        db.add(JobStep(id=uuid4(), job_id=job.id, name="source_reads", status="succeeded",
            safe_summary=f"All source reads and joins completed; {len(rows)} output records validated"))
        for alias, count in source_counts.items():
            db.add(JobStep(id=uuid4(), job_id=job.id, name=f"source_read:{alias}", status="succeeded",
                safe_summary=f"Source read completed; {count} rows staged in worker memory"))
        db.add(OutboxEvent(id=uuid4(), aggregate_type="dataset", aggregate_id=str(dataset.id),
            workspace_id=str(dataset.workspace_id), event_type="federated.generation_completed",
            payload={"job_id": str(job.id)}, request_id=job.request_id))
        record_audit(db, service_actor, job.request_id, "federated_generation.completed", "dataset_version",
            str(version.id), dataset.workspace_id, None, {"record_count": len(rows), "source_count": len(payload.sources),
            "join_count": len(payload.joins), "plan_hash": raw.get("plan_hash"), "pii_masking_required": pii_enabled})
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Federated generation failed for job %s", job_id)
        job = db.get(Job, job_id)
        version = db.get(DataSetVersion, version_id)
        dataset = db.get(DataSet, dataset_id)
        if job and job.status not in ("queued", "running"):
            return
        if job:
            job.status = "failed"
            job.progress = 100
            job.safe_summary = _source_generation_failure_summary(exc)
            db.add(JobStep(id=uuid4(), job_id=job.id, name="federated_generation", status="failed", safe_summary=job.safe_summary))
        if version:
            version.lifecycle_state = "failed"
            version.validation_state = "failed"
            version.privacy_state = "blocked"
        if dataset and version:
            try:
                append_lineage_event(db, dataset, version, LineageEventCreate(event_type="federated_generation_failed",
                    stage="generation", job_id=job_id, outcome="failed",
                    safe_evidence={"reason_code": "federated_generation_failed", "summary": "Federated generation failed; transient rows were discarded"}),
                    Principal(subject="tigr-federation-worker", actor_type="service"))
            except SQLAlchemyError:
                db.rollback()
        db.commit()
    finally:
        rows.clear()
        db.close()


def run_source_data_job(mapping_id: UUID, job_id: UUID, dataset_id: UUID, version_id: UUID, selected_fields: list[str], volume: int, output_format: str, predicates: list[dict], target_connection_id: UUID | None) -> None:
    db = SessionLocal()
    try:
        saved = db.get(SourceDataMapping, mapping_id); job = db.get(Job, job_id); dataset = db.get(DataSet, dataset_id); version = db.get(DataSetVersion, version_id)
        if not all((saved, job, dataset, version)): return
        connection = db.get(WorkspaceConnection, saved.connection_id)
        if not connection or connection.workspace_id != saved.workspace_id or connection.status != "enabled": raise ValueError("source_unavailable")
        fields = [MappingField.model_validate(item) for item in saved.fields if item["target_field"] in selected_fields]
        from tigr.record_identity import add_hidden_anchor_fields
        fields = add_hidden_anchor_fields(db, version, connection.id, fields)
        relationships = [ScenarioRelationship.model_validate(item) for item in saved.relationships]
        if any(item.required and item.target_field not in selected_fields for item in map(MappingField.model_validate, saved.fields)):
            raise ValueError("required_field_missing")
        if any(item.parent_field not in selected_fields or item.child_field not in selected_fields for item in relationships):
            raise ValueError("relationship_dependency_missing")
        plan = SimpleNamespace(mapping=[item.model_dump() for item in fields], generation_mode="source_backed", source_object=saved.source_object,
            logical_plan={"predicates": predicates, "relationships": [item.model_dump() for item in relationships], "source_joins": saved.source_joins or []}, volume=volume, id=dataset.id)
        validate_mapping(plan, connection); compiled = compile_query_plan(plan, connection)
        job.status="running"; job.progress=20; job.safe_summary="Reading the approved source fields"; db.add(JobStep(id=uuid4(), job_id=job.id, name="source_read", status="running", safe_summary="Bounded read started")); db.commit()
        from tigr.entity_claims import assert_source_namespace_available
        assert_source_namespace_available(
            db, connection.id, job.actor_id,
            allow_locked_entities=job.actor_role in (HumanRole.ADMIN.value, HumanRole.TEST_DATA_ENGINEER.value),
        )
        rows = generate_rows(plan, connection, fields, compiled)
        source_read_step = db.scalar(select(JobStep).where(
            JobStep.job_id == job.id,
            JobStep.name == "source_read",
            JobStep.status == "running",
        ))
        if source_read_step:
            source_read_step.status = "succeeded"
            source_read_step.safe_summary = "Bounded read completed"
        from tigr.entity_claims import assert_job_current
        assert_job_current(db, job.id)
        valid, warnings = validate_rows(rows, fields, [item.model_dump() for item in relationships])
        previous_state = version.lifecycle_state
        version.validation_state = "passed" if valid else "failed"
        version.record_count = len(rows)
        pii_masking_enabled = workspace_pii_masking_enabled(db, dataset.workspace_id)
        artifact_evidence = _persist_unmasked_source_rows(db, dataset, version, rows, fields, connection, output_format) if valid and not pii_masking_enabled else {}
        version.privacy_state = "requires_masking" if valid and pii_masking_enabled else "compliant" if valid else "blocked"
        version.lifecycle_state = "draft" if valid and pii_masking_enabled else "ready" if valid else "quarantined"
        version.validation_evidence = {"summary": "Source output passed schema and relationship checks" if valid else "Source output failed validation", "record_count": len(rows), "field_count": len(fields), "warning_count": warnings, "validation_version": "source-data-v1", "mapping_version": f"{saved.name}:v{saved.version_number}", "relationships": [item.model_dump() for item in relationships], **artifact_evidence}
        service_actor = Principal(subject="tigr-source-worker", actor_type="service")
        record_status_change(db, dataset, version, previous_state, version.lifecycle_state, service_actor, job.request_id,
                             "Source data generation completed" if valid else "Source output failed validation")
        record_validation(db, dataset, version, job.request_id, "source_schema_relationships")
        append_lineage_event(db, dataset, version, LineageEventCreate(event_type="source_generation_completed", stage="generation", job_id=job.id,
            source_reference=str(connection.id), mapping_version=f"{saved.name}:v{saved.version_number}",
            outcome="succeeded" if valid else "failed", safe_evidence={"record_count": len(rows), "field_count": len(fields), "warning_count": warnings,
                "format": output_format, "criteria_count": len(predicates),
                "target_connection_id": str(target_connection_id) if target_connection_id else None}),
            service_actor)
        job.progress=100; job.status="succeeded" if valid else "failed"; job.safe_summary="Source data generation completed" if valid else f"Output failed schema or relationship validation ({warnings} issue(s)). Check required values, field types, and relationship key values."
        db.add(JobStep(id=uuid4(), job_id=job.id, name="validation", status="succeeded" if valid else "failed", safe_summary="Schema, field and relationship checks completed"))
        db.add(OutboxEvent(id=uuid4(), aggregate_type="dataset", aggregate_id=str(dataset.id), workspace_id=str(dataset.workspace_id), event_type="source_data.generation_completed", payload={"job_id":str(job.id)}, request_id=job.request_id))
        record_audit(db, Principal(subject="tigr-source-worker", actor_type="service"), job.request_id, "source_data.generation_completed", "dataset_version", str(version.id), dataset.workspace_id, None, {"validation_state":version.validation_state,"record_count":len(rows)})
        db.commit()
    except Exception as exc:  # noqa: BLE001 - Background job failures expose only safe, actionable guidance.
        logger.exception("Source-backed generation failed for job %s", job_id)
        db.rollback()
        failure_db = SessionLocal()
        try:
            job = failure_db.get(Job, job_id)
            version = failure_db.get(DataSetVersion, version_id)
            dataset = failure_db.get(DataSet, dataset_id)
            if job and job.status not in ("queued", "running"):
                return
            if job:
                job.status = "failed"
                job.progress = 100
                job.safe_summary = _source_generation_failure_summary(exc)
                for step in failure_db.scalars(select(JobStep).where(JobStep.job_id == job.id, JobStep.status == "running")):
                    step.status = "failed"
                    step.safe_summary = "This step did not complete. See the Generation step for the failure summary."
                failure_db.add(JobStep(id=uuid4(), job_id=job.id, name="generation", status="failed", safe_summary=job.safe_summary))
            if version:
                version.lifecycle_state = "failed"
                version.validation_state = "failed"
                version.privacy_state = "blocked"
            if dataset and version:
                append_lineage_event(failure_db, dataset, version, LineageEventCreate(
                    event_type="source_generation_failed", stage="generation", job_id=job_id,
                    outcome="failed", safe_evidence={"reason_code": "generation_failed", "summary": "Source generation failed"},
                ), Principal(subject="tigr-source-worker", actor_type="service"))
            failure_db.commit()
        except Exception:
            failure_db.rollback()
            logger.exception("Could not persist source generation failure state for job %s", job_id)
        finally:
            failure_db.close()
    finally: db.close()


def run_source_query_job(query_id: UUID, job_id: UUID, dataset_id: UUID, version_id: UUID,
                         volume: int, output_format: str, output_field_mappings: list[dict],
                         target_connection_id: UUID | None) -> None:
    db = SessionLocal()
    try:
        query = db.get(SourceDataQuery, query_id)
        job = db.get(Job, job_id)
        dataset = db.get(DataSet, dataset_id)
        version = db.get(DataSetVersion, version_id)
        if not all((query, job, dataset, version)):
            return
        connection = db.get(WorkspaceConnection, query.connection_id)
        if (connection is None or connection.workspace_id != query.workspace_id or connection.status != "enabled"
                or connection.connector_type != "postgresql" or connection.operation_scope not in ("read_only", "read_write")):
            raise ValueError("query_source_unavailable")
        discovered_names = [item["name"] for item in query.output_fields]
        if ([item.get("source_field") for item in output_field_mappings] != discovered_names
                or len({item.get("output_field") for item in output_field_mappings}) != len(output_field_mappings)):
            raise ValueError("query_output_mapping_changed")
        fields = _query_lineage_fields(
            connection, query.output_fields,
            [item["source_field"] for item in output_field_mappings],
            [item["output_field"] for item in output_field_mappings],
            output_field_mappings,
        )
        job.status = "running"
        job.progress = 20
        job.safe_summary = "Executing the approved source query"
        db.add(JobStep(id=uuid4(), job_id=job.id, name="source_query", status="running", safe_summary="Read-only query execution started"))
        db.commit()

        from tigr.entity_claims import assert_source_namespace_available
        assert_source_namespace_available(
            db, connection.id, job.actor_id,
            allow_locked_entities=job.actor_role in (HumanRole.ADMIN.value, HumanRole.TEST_DATA_ENGINEER.value),
        )
        output_fields, rows = _execute_read_query(connection, query.query_text, volume)
        from tigr.entity_claims import assert_job_current
        assert_job_current(db, job.id)
        if [item["name"] for item in output_fields] != [item["name"] for item in query.output_fields]:
            raise ValueError("query_output_schema_changed")
        output_name_map = {item["source_field"]: item["output_field"] for item in output_field_mappings}
        rows = [{output_name_map[field]: value for field, value in row.items()} for row in rows]
        valid, warnings = validate_rows(rows, fields, [])
        previous_state = version.lifecycle_state
        version.validation_state = "passed" if valid else "failed"
        version.record_count = len(rows)
        pii_masking_enabled = workspace_pii_masking_enabled(db, dataset.workspace_id)
        artifact_evidence = _persist_unmasked_source_rows(db, dataset, version, rows, fields, connection, output_format) if valid and not pii_masking_enabled else {}
        version.privacy_state = "requires_masking" if valid and pii_masking_enabled else "compliant" if valid else "blocked"
        version.lifecycle_state = "draft" if valid and pii_masking_enabled else "ready" if valid else "quarantined"
        version.validation_evidence = {"summary": "Query output passed schema checks" if valid else "Query output failed schema checks",
            "record_count": len(rows), "field_count": len(fields), "warning_count": warnings,
            "validation_version": "source-query-v1",
            "source_query_id": str(query.id), **artifact_evidence}
        service_actor = Principal(subject="tigr-query-worker", actor_type="service")
        record_status_change(db, dataset, version, previous_state, version.lifecycle_state, service_actor, job.request_id,
            "Source query generation completed" if valid else "Source query output failed validation")
        record_validation(db, dataset, version, job.request_id, "source_query_output_schema")
        append_lineage_event(db, dataset, version, LineageEventCreate(event_type="source_query_generation_completed", stage="generation",
            job_id=job.id, source_reference=str(connection.id),
            mapping_version=f"query:{query.id}", outcome="succeeded" if valid else "failed",
            safe_evidence={"record_count": len(rows), "field_count": len(fields), "warning_count": warnings,
                           "format": output_format}), service_actor)
        job.progress = 100
        job.status = "succeeded" if valid else "failed"
        job.safe_summary = "Source query generation completed" if valid else "Query output quarantined after validation"
        for step in db.scalars(select(JobStep).where(JobStep.job_id == job.id, JobStep.name == "source_query", JobStep.status == "running")):
            step.status = "succeeded"
            step.safe_summary = "Read-only query completed"
        db.add(JobStep(id=uuid4(), job_id=job.id, name="validation", status="succeeded" if valid else "failed",
                       safe_summary="Query output schema checks completed"))
        db.add(OutboxEvent(id=uuid4(), aggregate_type="dataset", aggregate_id=str(dataset.id),
            workspace_id=str(dataset.workspace_id), event_type="source_query.generation_completed",
            payload={"job_id": str(job.id)}, request_id=job.request_id))
        record_audit(db, service_actor, job.request_id, "source_query.generation_completed", "dataset_version", str(version.id),
            dataset.workspace_id, None, {"validation_state": version.validation_state, "record_count": len(rows), "query_id": str(query.id)})
        db.commit()
    except Exception as exc:  # noqa: BLE001 - background job failures expose only a safe status.
        db.rollback()
        job = db.get(Job, job_id)
        version = db.get(DataSetVersion, version_id)
        dataset = db.get(DataSet, dataset_id)
        if job and job.status not in ("queued", "running"):
            return
        if job:
            job.status = "failed"
            job.progress = 100
            job.safe_summary = _source_query_failure_summary(exc)
            for step in db.scalars(select(JobStep).where(JobStep.job_id == job.id, JobStep.status == "running")):
                step.status = "failed"
                step.safe_summary = "This step did not complete. See the Generation step for the failure summary."
            db.add(JobStep(id=uuid4(), job_id=job.id, name="generation", status="failed", safe_summary=job.safe_summary))
        if version:
            version.lifecycle_state = "failed"
            version.validation_state = "failed"
            version.privacy_state = "blocked"
        if dataset and version:
            try:
                append_lineage_event(db, dataset, version, LineageEventCreate(event_type="source_query_generation_failed",
                    stage="generation", job_id=job_id, outcome="failed",
                    safe_evidence={"reason_code": "query_generation_failed", "summary": "Source query generation failed"}),
                    Principal(subject="tigr-query-worker", actor_type="service"))
            except SQLAlchemyError:
                db.rollback()
        db.commit()
    finally:
        db.close()


@router.post("/workspaces/{workspace_id}/source-data/generate", status_code=202)
def generate_source_data(workspace_id: UUID, payload: GenerateRequest, request: Request, background_tasks: BackgroundTasks,
                         db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict:
    role = require_editor(db, principal, workspace_id); ensure_user(db, principal)
    if not payload.confirm_source_extraction:
        raise HTTPException(status_code=409, detail="Confirm the source, selected fields, and bounded request before extraction")
    mapping = db.get(SourceDataMapping, payload.mapping_id)
    if not mapping or mapping.workspace_id != workspace_id: raise HTTPException(status_code=404, detail="Source mapping not found")
    if len(payload.selected_fields) != len(set(payload.selected_fields)): raise HTTPException(status_code=422, detail="Selected fields must be unique")
    available = {item["target_field"] for item in mapping.fields}
    if not set(payload.selected_fields) <= available: raise HTTPException(status_code=422, detail="Selected fields must come from the approved mapping")
    required = {item["target_field"] for item in mapping.fields if item.get("required")}
    if not required <= set(payload.selected_fields): raise HTTPException(status_code=422, detail="All required mapping fields must be selected")
    if any(rel["parent_field"] not in payload.selected_fields or rel["child_field"] not in payload.selected_fields for rel in mapping.relationships):
        raise HTTPException(status_code=422, detail="Selected fields must include all relationship dependencies")
    connection = db.get(WorkspaceConnection, mapping.connection_id)
    if not connection or connection.workspace_id != workspace_id or connection.status != "enabled" or connection.operation_scope not in ("read_only", "read_write"):
        raise HTTPException(status_code=409, detail="The mapped source is not enabled for reading")
    if payload.target_connection_id:
        target = db.get(WorkspaceConnection, payload.target_connection_id)
        if not target or target.workspace_id != workspace_id or target.status != "enabled" or target.direction not in ("target", "both") or target.operation_scope not in ("write_only", "read_write"):
            raise HTTPException(status_code=422, detail="Choose an enabled writable target in this workspace")
    predicates = [item.model_dump(mode="json") for item in payload.predicates]
    if any(item["field"] not in payload.selected_fields for item in predicates):
        raise HTTPException(status_code=422, detail="Criteria can reference selected mapped source fields only")
    job_id = uuid4()
    selected_maps = [MappingField.model_validate(item) for item in mapping.fields if item["target_field"] in payload.selected_fields]
    draft = DraftDataSet(name=payload.dataset_name, description=f"Source to Data · {mapping.name}:v{mapping.version_number}", creation_pathway="source_to_data",
        schema_scope=[SchemaField(field=item.target_field, data_type=item.data_type, entity=item.source_object,
                                  source_field=item.source_field, required=item.required, json_path=item.json_path) for item in selected_maps],
        selected_fields=payload.selected_fields, provenance=Provenance(origin="source_backed", source_connection_id=connection.id, source_object=mapping.source_object,
            source_schema_version="connection-structure-preview", source_mapping_id=mapping.id, source_predicates=predicates,
            mapping_version=f"{mapping.name}:v{mapping.version_number}", job_id=job_id,
            test_data_request_id=payload.test_data_request_id), permitted_use=[payload.target_use],
        maximum_records=payload.volume, output_format=payload.format, expiry_at=payload.expiry_at)
    dataset, version = _create_or_reuse_source_dataset(db, principal, workspace_id, payload.draft_dataset_id, draft,
                                                       request.state.request_id, payload.base_dataset_id,
                                                       payload.base_version_number)
    link_generation_to_ticket(db, payload.test_data_request_id, workspace_id, dataset, version, principal, role)
    job = Job(id=job_id, job_type="source_data_generation", status="queued", workspace_id=str(workspace_id), scope_key=f"workspace:{workspace_id}",
        idempotency_key=(f"source-data-edit:{dataset.id}:v{version.version_number}" if payload.base_dataset_id else
                         f"source-data-draft:{dataset.id}" if payload.draft_dataset_id else f"source-data:{dataset.id}"),
        actor_type=principal.actor_type, actor_id=principal.subject, actor_role=role.value,
        request_id=request.state.request_id, progress=0, safe_summary="Source data generation queued")
    db.add(job)
    record_audit(db, principal, request.state.request_id, "source_data.generation_requested", "dataset", str(dataset.id), workspace_id, role,
                 {"mapping_version":f"{mapping.name}:v{mapping.version_number}","selected_field_count":len(payload.selected_fields),"volume":payload.volume,"format":payload.format,"target_connection_id":str(payload.target_connection_id) if payload.target_connection_id else None})
    db.add(OutboxEvent(id=uuid4(), aggregate_type="dataset", aggregate_id=str(dataset.id), workspace_id=str(workspace_id), event_type="source_data.generation_requested",
        payload={"job_id":str(job_id),"mapping_id":str(mapping.id),"dataset_id":str(dataset.id),"version_id":str(version.id),
            "mapping_version":f"{mapping.name}:v{mapping.version_number}","selected_fields":payload.selected_fields,"volume":payload.volume,
            "output_format":payload.format,"predicates":predicates,"target_connection_id":str(payload.target_connection_id) if payload.target_connection_id else None}, request_id=request.state.request_id))
    db.commit()
    if get_settings().inline_jobs:
        background_tasks.add_task(run_source_data_job, mapping.id, job_id, dataset.id, version.id, payload.selected_fields,
                                  payload.volume, payload.format, predicates, payload.target_connection_id)
    return {"dataset_id":str(dataset.id),"dataset_reference_code":dataset.reference_code,"version_number":version.version_number,"job_id":str(job_id),"request_id":job.request_id,"status":"queued","pathway":"Source to Data","mapping_version":f"{mapping.name}:v{mapping.version_number}"}


@router.get("/source-data/jobs/{job_id}")
def read_source_data_job(job_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict:
    job = db.get(Job, job_id)
    if not job or job.status == "archived" or job.job_type not in ("source_data_generation", "source_query_generation", "federated_generation"): raise HTTPException(status_code=404, detail="Source job not found")
    workspace_id = UUID(job.workspace_id)
    role = workspace_role(db, principal, workspace_id)
    if principal.actor_type != "human" or role == HumanRole.QA_ENGINEER: raise HTTPException(status_code=404, detail="Source job not found")
    try:
        if job.job_type == "federated_generation":
            dataset_id = UUID(job.idempotency_key.split(":")[2])
            prefix = None
            edit_prefix = None
        else:
            edit_prefix = next((item for item in ("source-data-edit:", "source-query-edit:")
                            if job.idempotency_key.startswith(item)), None)
            if edit_prefix:
                # Edited source-backed versions encode the data set and version in
                # the idempotency key; the first segment is the stable data-set ID.
                dataset_id = UUID(job.idempotency_key.removeprefix(edit_prefix).split(":", 1)[0])
                prefix = None
            else:
                prefix = next((item for item in ("source-data-draft:", "source-query-draft:", "source-data:", "source-query:")
                           if job.idempotency_key.startswith(item)), None)
                dataset_id = UUID(job.idempotency_key.removeprefix(prefix)) if prefix else None
    except ValueError:
        dataset_id = None
    dataset = db.get(DataSet, dataset_id) if dataset_id else None
    source_steps = db.scalars(select(JobStep).where(JobStep.job_id == job.id, JobStep.name.like("source_read:%")).order_by(JobStep.created_at)).all()
    return {"id":job.id,"status":job.status,"progress":job.progress,"safe_summary":job.safe_summary,
        "dataset_id":dataset.id if dataset else None,"dataset_reference_code":dataset.reference_code if dataset else None,
        "source_statuses":[{"alias":step.name.removeprefix("source_read:"),"status":step.status,"summary":step.safe_summary} for step in source_steps]}


@router.get("/source-data/datasets/{dataset_id}/artifact")
def download_source_data_artifact(dataset_id: UUID, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> Response:
    dataset = db.get(DataSet, dataset_id)
    if not dataset or dataset.creation_pathway != "source_to_data": raise HTTPException(status_code=404, detail="Artifact not found")
    role = workspace_role(db, principal, dataset.workspace_id)
    if principal.actor_type != "human" or role not in (HumanRole.TEST_DATA_ENGINEER, HumanRole.ADMIN): raise HTTPException(status_code=403, detail="Artifact access is not permitted")
    version = db.scalar(select(DataSetVersion).where(DataSetVersion.dataset_id == dataset_id).order_by(DataSetVersion.version_number.desc()).limit(1))
    if (not version or version.validation_state != "passed" or version.privacy_state != "compliant"
            or version.lifecycle_state != "published" or version.publication_state != "published"
            or not dataset_records_privacy_ready(version)):
        raise HTTPException(status_code=404, detail="Artifact is available only after privacy completion and publication")
    if version.expiry_at is not None and version.expiry_at <= datetime.now(UTC):
        raise HTTPException(status_code=410, detail="The data set version has expired")
    artifact_ref = str(version.validation_evidence.get("artifact_ref", "dataset.json"))
    extension = ".csv" if artifact_ref.endswith(".csv") else ".json"
    from tigr.entity_claims import filter_dataset_records
    rows, allowed_count, blocked_count = filter_dataset_records(db, version, principal.subject)
    if not rows: raise HTTPException(status_code=404, detail="Artifact not found")
    media = "text/csv" if extension == ".csv" else "application/json"
    reference=str(version.validation_evidence.get("artifact_ref", ""))
    append_lineage_event(db,dataset,version,LineageEventCreate(event_type="artifact_downloaded",stage="delivery",artifact_reference=reference,
        safe_evidence={"format":extension.removeprefix("."),"summary":"Authorized artifact download"}),principal)
    artifact_record = db.scalar(select(ArtifactRecord).where(
        ArtifactRecord.workspace_id == dataset.workspace_id, ArtifactRecord.version_id == version.id,
        ArtifactRecord.storage_key == reference, ArtifactRecord.status == "available",
    ))
    db.add(ConsumptionEvent(
        workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
        principal_id=principal.subject, event_type="download", artifact_id=artifact_record.id if artifact_record else None,
        occurred_at=datetime.now(UTC), outcome="succeeded", correlation_id=request.state.request_id,
    ))
    record_audit(db,principal,request.state.request_id,"dataset.artifact_downloaded","dataset_version",str(version.id),dataset.workspace_id,role,
        {"version_number":version.version_number,"format":extension.removeprefix("."),"privacy_state":version.privacy_state,
         "record_count":allowed_count,"blocked_record_count":blocked_count})
    db.commit()
    return Response(serialize_dataset_records(rows, extension.removeprefix(".")), media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{dataset.reference_code}{extension}"'})
