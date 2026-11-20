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
from tigr.connection_models import WorkspaceConnection
from tigr.connection_testing import secret_resolver
from tigr.dataset_schemas import DraftDataSet, LineageEventCreate, Provenance, SchemaField
from tigr.dataset_services import (
    _dataset_fields,
    append_lineage_event,
    create_initial_version,
    dataset_records_privacy_ready,
    record_status_change,
    record_validation,
    serialize_dataset_records,
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

router = APIRouter(prefix="/api/v1", tags=["source to data"])
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


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mapping_id: UUID
    dataset_name: Name
    selected_fields: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)]] = Field(min_length=1, max_length=200)
    target_use: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)] = "testing"
    volume: int = Field(default=100, ge=1, le=10000)
    format: str = Field(default="json", pattern="^(json|csv)$")
    expiry_at: AwareDatetime | None = None
    target_connection_id: UUID | None = None
    draft_dataset_id: UUID | None = None
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


class GenerateFromQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: UUID
    dataset_name: Name
    target_use: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
    volume: int = Field(ge=1, le=10000)
    format: str = Field(pattern="^(json|csv)$")
    output_fields: list[QueryOutputMapping] = Field(min_length=1, max_length=200)
    expiry_at: AwareDatetime | None = None
    target_connection_id: UUID | None = None
    draft_dataset_id: UUID | None = None
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
                           format('%I.%I', namespace.nspname, relation.relname) AS source_object,
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
                    "source_object": source_object, "source_field": source_field, "primary_key": bool(primary_key)}
                    for relation_oid, attribute_number, source_object, source_field, primary_key in origin_rows}
                for index, relation_oid, attribute_number in origins:
                    fields[index].update(origin_metadata.get((relation_oid, attribute_number), {}))
            rows = [{name: _json_safe(row.get(name)) for name in names} for row in raw_rows]
            return fields, rows
    finally:
        engine.dispose()


def _validate_query_syntax(connection: WorkspaceConnection, query_text: str) -> str:
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
            source_db.exec_driver_sql(f"EXPLAIN {query}").all()
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
    assert_source_namespace_available(db, connection.id, principal.subject)
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
    fields = [{"name": item.source_field, "output_name": item.output_field,
               "data_type": discovered_fields[index]["data_type"]}
              for index, item in enumerate(payload.output_fields)]
    job_id = uuid4()
    description = f"Source to Data · query {query.id.hex[:8]}"
    draft = DraftDataSet(
        name=payload.dataset_name, description=description, creation_pathway="source_to_data",
        schema_scope=[SchemaField(field=item["output_name"], data_type=item["data_type"]) for item in fields],
        selected_fields=[item["output_name"] for item in fields],
        provenance=Provenance(origin="source_backed", source_connection_id=connection.id, source_object="query",
            source_schema_version="source-query-v1", source_query_id=query.id, job_id=job_id),
        permitted_use=[payload.target_use], maximum_records=payload.volume, output_format=payload.format, expiry_at=payload.expiry_at,
    )
    job = Job(id=job_id, job_type="source_query_generation", status="queued", workspace_id=str(workspace_id),
        scope_key=f"workspace:{workspace_id}", idempotency_key="", actor_type=principal.actor_type,
        actor_id=principal.subject, actor_role=role.value, request_id=request.state.request_id, progress=0,
        safe_summary="Source query generation queued")
    dataset, version = _create_or_reuse_source_dataset(db, principal, workspace_id, payload.draft_dataset_id, draft, request.state.request_id)
    job.idempotency_key = f"source-query-draft:{dataset.id}" if payload.draft_dataset_id else f"source-query:{dataset.id}"
    db.add(job)
    db.flush()
    record_audit(db, principal, request.state.request_id, "source_query.generation_requested", "dataset", str(dataset.id), workspace_id, role,
        {"query_id": str(query.id), "selected_field_count": len(fields), "volume": payload.volume, "format": payload.format,
         "target_connection_id": str(payload.target_connection_id) if payload.target_connection_id else None})
    db.add(OutboxEvent(id=uuid4(), aggregate_type="dataset", aggregate_id=str(dataset.id), workspace_id=str(workspace_id),
        event_type="source_query.generation_requested", payload={"query_id": str(query.id), "job_id": str(job.id),
        "dataset_id": str(dataset.id), "version_id": str(version.id), "volume": payload.volume, "output_format": payload.format,
        "output_fields": [item.model_dump() for item in payload.output_fields],
        "target_connection_id": str(payload.target_connection_id) if payload.target_connection_id else None}, request_id=request.state.request_id))
    db.commit()
    if get_settings().inline_jobs:
        background_tasks.add_task(run_source_query_job, query.id, job.id, dataset.id, version.id, payload.volume,
                                  payload.format, [item.model_dump() for item in payload.output_fields], payload.target_connection_id)
    return {"dataset_id": str(dataset.id), "version_number": version.version_number, "job_id": str(job.id),
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
) -> tuple[DataSet, DataSetVersion]:
    """Use the draft opened by Create Draft, so generation does not fork a second data set."""
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
    version.provenance = payload.provenance.model_dump(mode="json", exclude_none=True)
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
    plan = SourceDataPlan(id=uuid4(), workspace_id=workspace_id, name=payload.name,
                          state=payload.state, plan_data=payload.plan_data, created_by=principal.subject)
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
    for field, value in changes.items():
        setattr(plan, field, value)
    record_audit(db, principal, request.state.request_id, "source_plan.updated", "source_data_plan",
                 str(plan.id), plan.workspace_id, role, {"name": plan.name, "state": plan.state})
    db.commit()
    db.refresh(plan)
    return source_plan_out(plan)


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
        objects = (connection.structure_preview or {}).get("objects", []) if connection.connector_type == "postgresql" else [
            {"name": (connection.connector_config or {}).get("file_path", connection.name), "fields": connection_fields(connection)}]
        output.append({"id": connection.id, "name": connection.name, "connector_type": connection.connector_type,
                       "objects": [{"name": item.get("name"), "type": item.get("type", "source object"), "fields": item.get("fields", [])} for item in objects]})
    return output


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
    structure_objects = (connection.structure_preview or {}).get("objects", []) if connection.connector_type == "postgresql" else [{"name": payload.source_object}]
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


def _source_generation_failure_summary(exc: Exception) -> str:
    """Return actionable failure guidance without exposing connection details."""
    if isinstance(exc, ValueError):
        messages = {
            "source_unavailable": "The selected source connection is disabled or unavailable. Enable it and test the connection before retrying.",
            "required_field_missing": "The mapping omits a required field. Add the required field in Map Fields and retry.",
            "relationship_dependency_missing": "A relationship key is not selected. Include both relationship fields or remove that relationship.",
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
    if isinstance(exc, SQLAlchemyError) or (isinstance(sqlstate, str) and sqlstate.startswith("08")):
        return "TIGR could not read from the source database. Check that the source is reachable and its credentials are valid, then test the connection."
    return "Source generation stopped unexpectedly. Review the selected objects, mapped fields, join keys, and source connection, then retry."


def run_source_data_job(mapping_id: UUID, job_id: UUID, dataset_id: UUID, version_id: UUID, selected_fields: list[str], volume: int, output_format: str, predicates: list[dict], target_connection_id: UUID | None) -> None:
    db = SessionLocal()
    try:
        saved = db.get(SourceDataMapping, mapping_id); job = db.get(Job, job_id); dataset = db.get(DataSet, dataset_id); version = db.get(DataSetVersion, version_id)
        if not all((saved, job, dataset, version)): return
        connection = db.get(WorkspaceConnection, saved.connection_id)
        if not connection or connection.workspace_id != saved.workspace_id or connection.status != "enabled": raise ValueError("source_unavailable")
        fields = [MappingField.model_validate(item) for item in saved.fields if item["target_field"] in selected_fields]
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
        assert_source_namespace_available(db, connection.id, job.actor_id)
        rows = generate_rows(plan, connection, fields, compiled)
        from tigr.entity_claims import assert_job_current
        assert_job_current(db, job.id)
        valid, warnings = validate_rows(rows, fields, [item.model_dump() for item in relationships])
        previous_state = version.lifecycle_state
        version.validation_state = "passed" if valid else "failed"
        version.record_count = len(rows)
        version.privacy_state = "requires_masking" if valid else "blocked"
        version.lifecycle_state = "draft" if valid else "quarantined"
        version.validation_evidence = {"summary": "Source output passed schema and relationship checks" if valid else "Source output failed validation", "record_count": len(rows), "field_count": len(fields), "warning_count": warnings, "validation_version": "source-data-v1", "mapping_version": f"{saved.name}:v{saved.version_number}", "relationships": [item.model_dump() for item in relationships]}
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
        db.rollback()
        job = db.get(Job, job_id); version = db.get(DataSetVersion, version_id); dataset = db.get(DataSet, dataset_id)
        if job and job.status not in ("queued", "running"):
            return
        if job:
            job.status="failed"; job.progress=100; job.safe_summary=_source_generation_failure_summary(exc)
            db.add(JobStep(id=uuid4(), job_id=job.id, name="generation", status="failed", safe_summary=job.safe_summary))
        if version: version.lifecycle_state="failed"; version.validation_state="failed"; version.privacy_state="blocked"
        if dataset and version:
            try: append_lineage_event(db, dataset, version, LineageEventCreate(event_type="source_generation_failed", stage="generation", job_id=job_id, outcome="failed", safe_evidence={"reason_code":"generation_failed","summary":"Source generation failed"}), Principal(subject="tigr-source-worker", actor_type="service"))
            except SQLAlchemyError: db.rollback()
        db.commit()
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
        fields = [MappingField(source_field=item["source_field"], target_field=item["output_field"],
                               data_type=query.output_fields[index]["data_type"], source_object="query_result")
                  for index, item in enumerate(output_field_mappings)]
        job.status = "running"
        job.progress = 20
        job.safe_summary = "Executing the approved source query"
        db.add(JobStep(id=uuid4(), job_id=job.id, name="source_query", status="running", safe_summary="Read-only query execution started"))
        db.commit()

        from tigr.entity_claims import assert_source_namespace_available
        assert_source_namespace_available(db, connection.id, job.actor_id)
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
        version.privacy_state = "requires_masking" if valid else "blocked"
        version.lifecycle_state = "draft" if valid else "quarantined"
        version.validation_evidence = {"summary": "Query output passed schema checks" if valid else "Query output failed schema checks",
            "record_count": len(rows), "field_count": len(fields), "warning_count": warnings,
            "validation_version": "source-query-v1",
            "source_query_id": str(query.id)}
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
        db.add(JobStep(id=uuid4(), job_id=job.id, name="validation", status="succeeded" if valid else "failed",
                       safe_summary="Query output schema checks completed"))
        db.add(OutboxEvent(id=uuid4(), aggregate_type="dataset", aggregate_id=str(dataset.id),
            workspace_id=str(dataset.workspace_id), event_type="source_query.generation_completed",
            payload={"job_id": str(job.id)}, request_id=job.request_id))
        record_audit(db, service_actor, job.request_id, "source_query.generation_completed", "dataset_version", str(version.id),
            dataset.workspace_id, None, {"validation_state": version.validation_state, "record_count": len(rows), "query_id": str(query.id)})
        db.commit()
    except Exception:  # noqa: BLE001 - background job failures expose only a safe status.
        db.rollback()
        job = db.get(Job, job_id)
        version = db.get(DataSetVersion, version_id)
        dataset = db.get(DataSet, dataset_id)
        if job and job.status not in ("queued", "running"):
            return
        if job:
            job.status = "failed"
            job.progress = 100
            job.safe_summary = "Source query generation failed; review the query and connection"
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
        schema_scope=[SchemaField(field=item.target_field, data_type=item.data_type, required=item.required, json_path=item.json_path) for item in selected_maps],
        selected_fields=payload.selected_fields, provenance=Provenance(origin="source_backed", source_connection_id=connection.id, source_object=mapping.source_object,
            source_schema_version="connection-structure-preview", source_mapping_id=mapping.id, source_predicates=predicates,
            mapping_version=f"{mapping.name}:v{mapping.version_number}", job_id=job_id), permitted_use=[payload.target_use],
        maximum_records=payload.volume, output_format=payload.format, expiry_at=payload.expiry_at)
    dataset, version = _create_or_reuse_source_dataset(db, principal, workspace_id, payload.draft_dataset_id, draft, request.state.request_id)
    job = Job(id=job_id, job_type="source_data_generation", status="queued", workspace_id=str(workspace_id), scope_key=f"workspace:{workspace_id}",
        idempotency_key=f"source-data-draft:{dataset.id}" if payload.draft_dataset_id else f"source-data:{dataset.id}",
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
    return {"dataset_id":str(dataset.id),"version_number":version.version_number,"job_id":str(job_id),"request_id":job.request_id,"status":"queued","pathway":"Source to Data","mapping_version":f"{mapping.name}:v{mapping.version_number}"}


@router.get("/source-data/jobs/{job_id}")
def read_source_data_job(job_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)) -> dict:
    job = db.get(Job, job_id)
    if not job or job.status == "archived" or job.job_type not in ("source_data_generation", "source_query_generation"): raise HTTPException(status_code=404, detail="Source job not found")
    workspace_id = UUID(job.workspace_id)
    role = workspace_role(db, principal, workspace_id)
    if principal.actor_type != "human" or role == HumanRole.QA_ENGINEER: raise HTTPException(status_code=404, detail="Source job not found")
    try:
        prefix = next((item for item in ("source-data-draft:", "source-query-draft:", "source-data:", "source-query:")
                       if job.idempotency_key.startswith(item)), None)
        dataset_id = UUID(job.idempotency_key.removeprefix(prefix)) if prefix else None
    except ValueError:
        dataset_id = None
    dataset = db.get(DataSet, dataset_id) if dataset_id else None
    return {"id":job.id,"status":job.status,"progress":job.progress,"safe_summary":job.safe_summary,"dataset_id":dataset.id if dataset else None}


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
                    headers={"Content-Disposition": f'attachment; filename="source-data-{dataset_id}{extension}"'})
