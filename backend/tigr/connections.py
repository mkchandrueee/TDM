import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tigr.access import ensure_user, record_audit, workspace_role
from tigr.config import get_settings
from tigr.connection_models import (
    ConnectionDatabaseCredential,
    ConnectionStructureSnapshot,
    LockPropagationConfiguration,
    WorkspaceConnection,
)
from tigr.connection_schemas import (
    ConnectionCreate,
    ConnectionOut,
    ConnectionUpdate,
    ConnectorType,
    DatabaseDiscoveryRequest,
    SchemaDiscoveryRequest,
    validate_connector_fields,
)
from tigr.connection_testing import (
    discover_databases,
    discover_databases_with_credentials,
    discover_schemas,
    discover_schemas_with_credentials,
    resolve_file,
    test_connection,
)
from tigr.datasets import DataSet, DataSetVersion
from tigr.governance import DataSetLock, EntityClaim
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.lock_configuration import LockConfigurationInput, validate_configuration
from tigr.schema_records import ConnectionTestRun, DatasetRecordEntityLineage
from tigr.secrets import encrypt_credentials

router = APIRouter(prefix="/api/v1", tags=["workspace connections"])
UPLOAD_CHUNK_BYTES = 5 * 1024 * 1024


def require_connection_editor(db: Session, principal: Principal, workspace_id: UUID) -> HumanRole:
    role = workspace_role(db, principal, workspace_id)
    if role not in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER):
        raise HTTPException(status_code=403, detail="Connection configuration requires an Admin or Test Data Engineer")
    return role


def audit_connection(db: Session, request: Request, principal: Principal, workspace_id: UUID, role: HumanRole, action: str, connection: WorkspaceConnection, metadata: dict[str, Any] | None = None) -> None:
    record_audit(db, principal, request.state.request_id, action, "workspace_connection", str(connection.id), workspace_id, role, metadata or {})


def connection_out(connection: WorkspaceConnection) -> ConnectionOut:
    database_credentials = list(connection.database_credentials or [])
    return ConnectionOut(
        id=connection.id, workspace_id=connection.workspace_id, name=connection.name,
        connector_type=connection.connector_type, direction=connection.direction,
        environment=connection.environment, purpose=connection.purpose, owner=connection.owner,
        operation_scope=connection.operation_scope,
        secret_configured=bool(connection.secret_reference or database_credentials),
        database_credential_mode="per_database" if database_credentials else "shared",
        database_credentials=[{"database_name": item.database_name, "configured": True} for item in database_credentials],
        connector_config=connection.connector_config, status=connection.status,
        tested_at=connection.tested_at, tested_by=connection.tested_by,
        test_result=connection.test_result, structure_preview=connection.structure_preview,
        created_by=connection.created_by, updated_by=connection.updated_by,
        created_at=connection.created_at, updated_at=connection.updated_at,
    )


def _replace_database_credentials(db: Session, connection: WorkspaceConnection, entries: list,
                                  actor: str) -> None:
    for existing in list(connection.database_credentials or []):
        db.delete(existing)
    connection.database_credentials = [ConnectionDatabaseCredential(
        id=uuid4(), workspace_id=connection.workspace_id, connection_id=connection.id,
        database_name=entry.database_name, secret_reference=entry.secret_reference,
        created_by=actor, updated_by=actor,
    ) for entry in entries]


def _credentials_payload(value) -> dict[str, object]:
    return value.model_dump() if hasattr(value, "model_dump") else dict(value)


def _encrypt_source_credentials(connector_type: str, credentials: dict[str, object]) -> str:
    try:
        return encrypt_credentials(connector_type, credentials)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Source credential encryption is not configured") from exc


def upload_job_directory(workspace_id: UUID, upload_id: UUID) -> Path:
    return resolve_file(f"upload_jobs/{workspace_id}/{upload_id}")


def read_upload_job(workspace_id: UUID, upload_id: UUID, principal: Principal) -> tuple[Path, dict[str, Any]]:
    directory = upload_job_directory(workspace_id, upload_id)
    try:
        job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail="Upload job was not found") from exc
    if job.get("workspace_id") != str(workspace_id) or job.get("requested_by") != principal.subject:
        raise HTTPException(status_code=404, detail="Upload job was not found")
    return directory, job


def write_upload_job(directory: Path, job: dict[str, Any]) -> None:
    temporary = directory / "job.json.tmp"
    temporary.write_text(json.dumps(job), encoding="utf-8")
    temporary.replace(directory / "job.json")


def finish_upload_job(workspace_id: UUID, upload_id: UUID) -> None:
    directory = upload_job_directory(workspace_id, upload_id)
    job_path = directory / "job.json"
    destination: Path | None = None
    assembling_path: Path | None = None
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        suffix = str(job["suffix"])
        total_chunks = int(job["total_chunks"])
        total_size = int(job["total_size"])
        final_relative_path = f"uploads/{workspace_id}/{upload_id.hex}{suffix}"
        destination = resolve_file(final_relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        assembling_path = destination.with_name(f"{destination.name}.assembling")
        written = 0
        with assembling_path.open("wb") as output:
            for index in range(total_chunks):
                part = directory / f"part-{index:08d}"
                with part.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
                        written += len(chunk)
        if written != total_size:
            raise ValueError("Uploaded parts do not match the expected file size")
        assembling_path.replace(destination)
        job.update(status="completed", file_path=final_relative_path, size_bytes=written, finished_at=datetime.now(UTC).isoformat())
        write_upload_job(directory, job)
        for index in range(total_chunks):
            (directory / f"part-{index:08d}").unlink(missing_ok=True)
    except Exception:
        if assembling_path:
            assembling_path.unlink(missing_ok=True)
        if destination:
            destination.unlink(missing_ok=True)
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
            job.update(status="failed", error="The uploaded file could not be assembled", finished_at=datetime.now(UTC).isoformat())
            write_upload_job(directory, job)
        except (OSError, json.JSONDecodeError):
            pass


@router.get("/workspaces/{workspace_id}/connections", response_model=list[ConnectionOut])
def list_connections(
    workspace_id: UUID,
    include_disabled: bool = False,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[ConnectionOut]:
    workspace_role(db, principal, workspace_id)
    query = select(WorkspaceConnection).where(WorkspaceConnection.workspace_id == workspace_id)
    if not include_disabled:
        query = query.where(WorkspaceConnection.status != "disabled")
    rows = db.scalars(query.order_by(WorkspaceConnection.created_at.desc(), WorkspaceConnection.id.desc())).all()
    return [connection_out(row) for row in rows]


@router.post("/workspaces/{workspace_id}/connections/available-databases")
def list_available_databases(
    workspace_id: UUID,
    payload: DatabaseDiscoveryRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    require_connection_editor(db, principal, workspace_id)
    try:
        if payload.credentials:
            databases = discover_databases_with_credentials(
                payload.connector_type, payload.credentials.model_dump(),
            )
        elif payload.secret_reference:
            databases = discover_databases(payload.connector_type, payload.secret_reference)
        else:
            raise ValueError("Database credentials are required")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Source credential encryption is not configured") from exc
    return {"databases": databases}


@router.post("/workspaces/{workspace_id}/connections/available-schemas")
def list_available_schemas(
    workspace_id: UUID,
    payload: SchemaDiscoveryRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    require_connection_editor(db, principal, workspace_id)
    try:
        if payload.credentials:
            schemas = discover_schemas_with_credentials(payload.connector_type, payload.database_name, payload.credentials.model_dump())
        elif payload.secret_reference:
            schemas = discover_schemas(payload.database_name, payload.secret_reference, payload.connector_type)
        else:
            raise ValueError("Database credentials are required")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Source credential encryption is not configured") from exc
    return {"database": payload.database_name, "schemas": schemas}


@router.post("/workspaces/{workspace_id}/connections/file-uploads", status_code=201)
def create_file_upload_job(
    workspace_id: UUID,
    connector_type: ConnectorType,
    filename: str = Query(min_length=1, max_length=255),
    total_size: int = Query(gt=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    require_connection_editor(db, principal, workspace_id)
    ensure_user(db, principal)
    suffix = Path(filename).suffix.casefold()
    allowed_suffixes = {
        ConnectorType.CSV: {".csv"},
        ConnectorType.JSON: {".json"},
        ConnectorType.DELIMITED_TEXT: {".txt", ".tsv", ".dat", ".csv", ".psv", ".pipe"},
    }
    if ("/" in filename or "\\" in filename or filename in {".", ".."}
            or suffix not in allowed_suffixes.get(connector_type, set())):
        raise HTTPException(status_code=422, detail="Choose a file with an extension supported by the selected connector type")
    upload_id = uuid4()
    directory = upload_job_directory(workspace_id, upload_id)
    try:
        directory.mkdir(parents=True, exist_ok=False)
        job = {
            "id": str(upload_id), "workspace_id": str(workspace_id), "requested_by": principal.subject,
            "filename": filename, "suffix": suffix, "connector_type": connector_type.value,
            "total_size": total_size, "chunk_size": UPLOAD_CHUNK_BYTES,
            "total_chunks": (total_size + UPLOAD_CHUNK_BYTES - 1) // UPLOAD_CHUNK_BYTES,
            "status": "uploading", "created_at": datetime.now(UTC).isoformat(),
        }
        write_upload_job(directory, job)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Unable to create the file upload job") from exc
    return {key: job[key] for key in ("id", "filename", "total_size", "chunk_size", "total_chunks", "status")}


@router.put("/workspaces/{workspace_id}/connections/file-uploads/{upload_id}/parts/{part_number}")
async def upload_file_part(
    workspace_id: UUID,
    upload_id: UUID,
    part_number: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    require_connection_editor(db, principal, workspace_id)
    directory, job = read_upload_job(workspace_id, upload_id, principal)
    if job.get("status") != "uploading":
        raise HTTPException(status_code=409, detail="This upload job is no longer accepting parts")
    total_chunks = int(job["total_chunks"])
    if part_number < 0 or part_number >= total_chunks:
        raise HTTPException(status_code=404, detail="Upload part was not found")
    expected_size = min(int(job["chunk_size"]), int(job["total_size"]) - part_number * int(job["chunk_size"]))
    part = directory / f"part-{part_number:08d}"
    temporary = directory / f"part-{part_number:08d}.tmp"
    received = 0
    try:
        with temporary.open("wb") as output:
            async for chunk in request.stream():
                received += len(chunk)
                if received > expected_size:
                    raise HTTPException(status_code=413, detail="Upload part is larger than expected")
                output.write(chunk)
        if received != expected_size:
            raise HTTPException(status_code=400, detail="Upload part is incomplete")
        temporary.replace(part)
    except HTTPException:
        temporary.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="The upload part could not be stored") from exc
    return {"part_number": part_number, "size_bytes": received, "status": "uploaded"}


@router.post("/workspaces/{workspace_id}/connections/file-uploads/{upload_id}/complete", status_code=202)
def complete_file_upload_job(
    workspace_id: UUID,
    upload_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    require_connection_editor(db, principal, workspace_id)
    directory, job = read_upload_job(workspace_id, upload_id, principal)
    if job.get("status") == "completed":
        return {"id": str(upload_id), "status": "completed", "file_path": job.get("file_path"), "filename": job.get("filename"), "size_bytes": job.get("size_bytes")}
    if job.get("status") != "uploading":
        raise HTTPException(status_code=409, detail="This upload job cannot be completed")
    missing = [index for index in range(int(job["total_chunks"])) if not (directory / f"part-{index:08d}").is_file()]
    if missing:
        raise HTTPException(status_code=409, detail=f"Upload is incomplete; {len(missing)} part(s) are missing")
    job["status"] = "assembling"
    write_upload_job(directory, job)
    background_tasks.add_task(finish_upload_job, workspace_id, upload_id)
    return {"id": str(upload_id), "status": "assembling", "filename": job["filename"]}


@router.get("/workspaces/{workspace_id}/connections/file-uploads/{upload_id}")
def get_file_upload_job(
    workspace_id: UUID,
    upload_id: UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    workspace_role(db, principal, workspace_id)
    _, job = read_upload_job(workspace_id, upload_id, principal)
    return {key: value for key, value in job.items() if key != "requested_by" and key != "suffix"}


@router.post("/workspaces/{workspace_id}/connections/file-upload", status_code=201)
async def upload_connection_file(
    workspace_id: UUID,
    request: Request,
    connector_type: ConnectorType,
    filename: str = Query(min_length=1, max_length=255),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    require_connection_editor(db, principal, workspace_id)
    ensure_user(db, principal)
    suffix = Path(filename).suffix.casefold()
    allowed_suffixes = {
        ConnectorType.CSV: {".csv"},
        ConnectorType.JSON: {".json"},
        ConnectorType.DELIMITED_TEXT: {".txt", ".tsv", ".dat", ".csv", ".psv", ".pipe"},
    }
    if ("/" in filename or "\\" in filename or filename in {".", ".."}
            or suffix not in allowed_suffixes.get(connector_type, set())):
        raise HTTPException(status_code=422, detail="Choose a file with an extension supported by the selected connector type")
    relative_path = f"uploads/{workspace_id}/{uuid4().hex}{suffix}"
    try:
        destination = resolve_file(relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="File upload storage is not configured") from exc
    written = 0
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output:
            async for chunk in request.stream():
                written += len(chunk)
                output.write(chunk)
        if written == 0:
            raise HTTPException(status_code=400, detail="The selected file is empty")
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="The selected file could not be stored") from exc
    return {"file_path": relative_path, "file_name": filename, "size_bytes": written}


@router.post("/workspaces/{workspace_id}/connections", response_model=ConnectionOut, status_code=201)
def create_connection(
    workspace_id: UUID,
    payload: ConnectionCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ConnectionOut:
    role = require_connection_editor(db, principal, workspace_id)
    ensure_user(db, principal)
    if payload.connector_type.value in ("mysql", "oracle", "mssql") and payload.direction.value != "source":
        raise HTTPException(status_code=422, detail=f"{payload.connector_type.value.upper()} is currently supported as a source connection")
    if payload.connector_type.value not in ("postgresql", "mysql", "oracle", "mssql") and payload.operation_scope.value != "read_only":
        raise HTTPException(status_code=422, detail="File connections must use read-only operation scope")
    if db.scalar(select(WorkspaceConnection.id).where(WorkspaceConnection.workspace_id == workspace_id, WorkspaceConnection.name == payload.name)):
        raise HTTPException(status_code=409, detail="A connection with this name already exists in the workspace")
    connection = WorkspaceConnection(
        id=uuid4(), workspace_id=workspace_id, name=payload.name, connector_type=payload.connector_type.value,
        direction=payload.direction.value, environment=payload.environment, purpose=payload.purpose,
        owner=payload.owner or principal.subject, operation_scope=payload.operation_scope.value,
        secret_reference=(_encrypt_source_credentials(payload.connector_type.value, payload.server_credentials.model_dump())
                          if payload.server_credentials else payload.secret_reference),
        connector_config=payload.connector_config,
        status="draft", created_by=principal.subject, updated_by=principal.subject,
    )
    db.add(connection)
    if payload.database_credential_mode == "per_database":
        entries = []
        for item in payload.database_credentials:
            reference = item.secret_reference
            if item.credentials:
                reference = _encrypt_source_credentials(payload.connector_type.value, item.credentials.model_dump())
            entries.append(type("Credential", (), {"database_name": item.database_name,
                                                    "secret_reference": reference})())
        _replace_database_credentials(db, connection, entries, principal.subject)
    audit_connection(db, request, principal, workspace_id, role, "connection.created", connection, {"connector_type": connection.connector_type, "direction": connection.direction})
    db.commit()
    db.refresh(connection)
    return connection_out(connection)


@router.patch("/workspaces/{workspace_id}/connections/{connection_id}", response_model=ConnectionOut)
def update_connection(
    workspace_id: UUID,
    connection_id: UUID,
    payload: ConnectionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ConnectionOut:
    role = require_connection_editor(db, principal, workspace_id)
    connection = db.scalar(select(WorkspaceConnection).where(
        WorkspaceConnection.id == connection_id,
        WorkspaceConnection.workspace_id == workspace_id,
    ).with_for_update())
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return connection_out(connection)
    if set(changes) == {"operation_scope"}:
        # Operation permissions do not change credentials, discovered tables or
        # approved entity keys. Keep the existing tested binding intact.
        scope = payload.operation_scope.value
        if connection.connector_type not in ("postgresql", "mysql", "oracle", "mssql"):
            raise HTTPException(422, "Operation-scope changes require a relational database connection")
        from tigr.entity_claims import lock_source_namespace

        lock_source_namespace(db, f"connection:{connection.id}")
        previous = connection.operation_scope
        connection.operation_scope = scope
        connection.updated_by = principal.subject
        connection.updated_at = datetime.now(UTC)
        audit_connection(db, request, principal, workspace_id, role, "connection.operation_scope_updated", connection,
                         {"previous": previous, "operation_scope": scope})
        db.commit()
        db.refresh(connection)
        return connection_out(connection)
    if "name" in changes and db.scalar(select(WorkspaceConnection.id).where(WorkspaceConnection.workspace_id == workspace_id, WorkspaceConnection.name == changes["name"], WorkspaceConnection.id != connection_id)):
        raise HTTPException(status_code=409, detail="A connection with this name already exists in the workspace")
    for enum_key in ("connector_type", "direction", "operation_scope"):
        if enum_key in changes and changes[enum_key] is not None:
            changes[enum_key] = changes[enum_key].value
    new_type = changes.get("connector_type", connection.connector_type)
    new_direction = changes.get("direction", connection.direction)
    if new_type in ("mysql", "oracle", "mssql") and new_direction != "source":
        raise HTTPException(status_code=422, detail=f"{new_type.upper()} is currently supported as a source connection")
    new_scope = changes.get("operation_scope", connection.operation_scope)
    if new_type not in ("postgresql", "mysql", "oracle", "mssql") and new_scope != "read_only":
        raise HTTPException(status_code=422, detail="File connections must use read-only operation scope")
    supplied_server_credentials = changes.pop("server_credentials", None)
    new_secret_reference = changes.get("secret_reference", connection.secret_reference)
    if supplied_server_credentials:
        new_secret_reference = _encrypt_source_credentials(new_type, _credentials_payload(supplied_server_credentials))
    new_config = changes.get("connector_config", connection.connector_config)
    credential_mode = changes.get("database_credential_mode") or ("per_database" if connection.database_credentials else "shared")
    submitted_credentials = changes.get("database_credentials")
    existing_credentials = {item.database_name.casefold(): item.secret_reference for item in connection.database_credentials}
    credential_entries = []
    try:
        changes["connector_config"] = validate_connector_fields(
            new_type, new_secret_reference, new_config,
            submitted_credentials if submitted_credentials is not None else ([object()] if existing_credentials else None),
        )
        if new_type in (ConnectorType.POSTGRESQL.value, ConnectorType.MYSQL.value, ConnectorType.ORACLE.value, ConnectorType.MSSQL.value):
            databases = changes["connector_config"].get("databases", [])
            if credential_mode == "per_database":
                supplied = {str(item["database_name"]).casefold(): item for item in (submitted_credentials or [])}
                if submitted_credentials is None:
                    supplied = {name: None for name in databases}
                if not databases or {name.casefold() for name in databases} != set(supplied):
                    raise ValueError("Provide one credential reference for each selected database")
                for database in databases:
                    item = supplied[database.casefold()]
                    reference = item.get("secret_reference") if isinstance(item, dict) else None
                    if isinstance(item, dict) and item.get("credentials"):
                        reference = _encrypt_source_credentials(new_type, item["credentials"])
                    reference = reference or existing_credentials.get(database.casefold())
                    if not reference:
                        raise ValueError("Each selected database requires a credential reference")
                    credential_entries.append(type("Credential", (), {"database_name": database, "secret_reference": reference})())
                new_secret_reference = None
                changes["secret_reference"] = None
            elif credential_mode == "shared":
                if not new_secret_reference:
                    raise ValueError("A shared credential reference is required")
                changes["secret_reference"] = new_secret_reference
            else:
                raise ValueError("Credential mode is invalid")
        else:
            credential_mode = "shared"
            changes["secret_reference"] = None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Connection configuration is invalid") from exc
    if new_type in (ConnectorType.POSTGRESQL.value, ConnectorType.MYSQL.value, ConnectorType.ORACLE.value, ConnectorType.MSSQL.value):
        _replace_database_credentials(db, connection, credential_entries if credential_mode == "per_database" else [], principal.subject)
    else:
        _replace_database_credentials(db, connection, [], principal.subject)
    changed_fields = sorted(changes)
    changes.pop("database_credential_mode", None)
    changes.pop("database_credentials", None)
    for key, value in changes.items():
        setattr(connection, key, value)
    connection.status = "draft"
    connection.tested_at = None
    connection.tested_by = None
    connection.test_result = None
    connection.structure_preview = None
    connection.updated_by = principal.subject
    connection.updated_at = datetime.now(UTC)
    audit_connection(db, request, principal, workspace_id, role, "connection.updated", connection, {"changed_fields": changed_fields, "status": "draft"})
    db.commit()
    db.refresh(connection)
    return connection_out(connection)


@router.delete(
    "/workspaces/{workspace_id}/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_connection(
    workspace_id: UUID,
    connection_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    role = require_connection_editor(db, principal, workspace_id)
    connection = db.get(WorkspaceConnection, connection_id)
    if connection is None or connection.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")

    # Test history belongs to the connection and is removed with it. Other data
    # workflow references are protected by foreign keys and prevent deletion.
    db.execute(
        delete(ConnectionTestRun).where(
            ConnectionTestRun.workspace_id == workspace_id,
            ConnectionTestRun.connection_id == connection_id,
        )
    )
    audit_connection(db, request, principal, workspace_id, role, "connection.deleted", connection)
    db.delete(connection)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This connection is referenced by a data workflow and cannot be deleted",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workspaces/{workspace_id}/connections/{connection_id}/test", response_model=ConnectionOut)
def run_connection_test(
    workspace_id: UUID,
    connection_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ConnectionOut:
    role = require_connection_editor(db, principal, workspace_id)
    connection = db.scalar(select(WorkspaceConnection).where(
        WorkspaceConnection.id == connection_id,
        WorkspaceConnection.workspace_id == workspace_id,
    ).with_for_update())
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    test_started_at = datetime.now(UTC)
    result = test_connection(connection)
    test_finished_at = datetime.now(UTC)
    connection.tested_at = test_finished_at
    connection.tested_by = principal.subject
    connection.test_result = {"succeeded": result.succeeded, "message": result.message}
    connection.structure_preview = result.structure if result.succeeded else None
    lock_configuration_notice = ""
    if (result.succeeded and result.structure is not None
            and connection.direction in ("source", "both")
            and connection.operation_scope in ("read_only", "read_write")):
        previous_configuration = db.scalar(select(LockPropagationConfiguration).where(
            LockPropagationConfiguration.connection_id == connection.id,
            LockPropagationConfiguration.workspace_id == workspace_id,
            LockPropagationConfiguration.status == "approved",
        ).order_by(LockPropagationConfiguration.version_number.desc()).limit(1))
        latest_snapshot = db.scalar(select(func.max(ConnectionStructureSnapshot.version_number)).where(
            ConnectionStructureSnapshot.connection_id == connection.id,
        )) or 0
        snapshot_structure = dict(result.structure)
        if connection.connector_type not in ("postgresql", "mysql", "oracle", "mssql"):
            from tigr.scenario_generation import connection_fields
            snapshot_structure = {
                "format": connection.connector_type,
                "objects": [{"name": "source_file", "type": "file", "fields": connection_fields(connection, "source_file")}],
                "values_included": False,
            }
        snapshot_structure["structure_snapshot_version"] = latest_snapshot + 1
        snapshot_structure["source_identity_namespace"] = f"connection:{connection.id}"
        if connection.connector_type in ("postgresql", "mysql", "oracle", "mssql"):
            connection.structure_preview = snapshot_structure
        snapshot = ConnectionStructureSnapshot(
            id=uuid4(), workspace_id=workspace_id, connection_id=connection.id,
            version_number=latest_snapshot + 1, structure=snapshot_structure,
            captured_by=principal.subject, captured_at=test_finished_at,
        )
        db.add(snapshot)
        db.flush()
        if previous_configuration is not None:
            try:
                carried_configuration = validate_configuration(
                    LockConfigurationInput(anchors=(previous_configuration.configuration or {}).get("anchors", [])),
                    snapshot_structure, connection.connector_type,
                )
            except ValueError:
                lock_configuration_notice = (
                    "Entity locking needs review: one or more saved entity keys no longer match the refreshed source structure. "
                    "Review and save Entity lock keys before generating new data sets."
                )
            else:
                latest_configuration = db.scalar(select(func.max(LockPropagationConfiguration.version_number)).where(
                    LockPropagationConfiguration.connection_id == connection.id,
                )) or 0
                refreshed_configuration = LockPropagationConfiguration(
                    id=uuid4(), workspace_id=workspace_id, connection_id=connection.id,
                    structure_snapshot_id=snapshot.id, version_number=latest_configuration + 1,
                    configuration=carried_configuration, status="approved", created_by=principal.subject,
                    approved_by=previous_configuration.approved_by, approved_at=test_finished_at,
                )
                db.add(refreshed_configuration)
                audit_connection(db, request, principal, workspace_id, role,
                                 "lock_configuration.auto_refreshed", connection, {
                                     "configuration_version": refreshed_configuration.version_number,
                                     "structure_snapshot_version": snapshot.version_number,
                                     "carried_from_configuration_version": previous_configuration.version_number,
                                     "anchor_count": len(carried_configuration["anchors"]),
                                 })
                lock_configuration_notice = (
                    f"Entity lock keys were automatically refreshed and saved for source structure v{snapshot.version_number}."
                )
        if lock_configuration_notice:
            connection.test_result = {
                "succeeded": True,
                "message": f"{result.message} {lock_configuration_notice}",
            }
    connection.status = "tested" if result.succeeded else ("disabled" if connection.status == "disabled" else "draft")
    connection.updated_by = principal.subject
    db.add(ConnectionTestRun(
        id=uuid4(), workspace_id=workspace_id, connection_id=connection.id,
        requested_by=principal.subject, started_at=test_started_at, finished_at=test_finished_at,
        outcome="succeeded" if result.succeeded else "failed",
        safe_error_code=None if result.succeeded else "connection_test_failed",
        safe_message=result.message[:500], correlation_id=request.state.request_id,
    ))
    audit_connection(db, request, principal, workspace_id, role, "connection.tested", connection, {"succeeded": result.succeeded, "connector_type": connection.connector_type})
    db.commit()
    db.refresh(connection)
    return connection_out(connection)


@router.get("/workspaces/{workspace_id}/connections/{connection_id}/lock-schema")
def read_lock_schema(
    workspace_id: UUID,
    connection_id: UUID,
    configuration_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    role = workspace_role(db, principal, workspace_id)
    connection = db.get(WorkspaceConnection, connection_id)
    if connection is None or connection.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")
    snapshot = db.scalar(select(ConnectionStructureSnapshot).where(
        ConnectionStructureSnapshot.connection_id == connection_id,
        ConnectionStructureSnapshot.workspace_id == workspace_id,
    ).order_by(ConnectionStructureSnapshot.version_number.desc()).limit(1))
    configuration_query = select(LockPropagationConfiguration).where(
        LockPropagationConfiguration.connection_id == connection_id,
        LockPropagationConfiguration.workspace_id == workspace_id,
        LockPropagationConfiguration.status == "approved",
    )
    if configuration_id is not None:
        configuration_query = configuration_query.where(LockPropagationConfiguration.id == configuration_id)
    else:
        configuration_query = configuration_query.order_by(LockPropagationConfiguration.version_number.desc())
    configuration = db.scalar(configuration_query.limit(1))
    result = {
        "connection_id": connection_id,
        "source_identity_namespace": f"connection:{connection_id}",
        "entity_locking_enabled": bool(connection.entity_locking_enabled and get_settings().entity_locking_enabled),
        "connection_entity_locking_enabled": bool(connection.entity_locking_enabled),
        "global_entity_locking_enabled": bool(get_settings().entity_locking_enabled),
        "snapshot": ({"id": snapshot.id, "version_number": snapshot.version_number,
                      "captured_by": snapshot.captured_by, "captured_at": snapshot.captured_at,
                      "structure": snapshot.structure} if snapshot else None),
        "configuration": ({"id": configuration.id, "version_number": configuration.version_number,
                           "structure_snapshot_id": configuration.structure_snapshot_id,
                           "matches_current_snapshot": bool(snapshot and configuration.structure_snapshot_id == snapshot.id),
                           "configuration": {"anchors": (configuration.configuration or {}).get("anchors", [])},
                           "approved_by": configuration.approved_by,
                           "approved_at": configuration.approved_at} if configuration else None),
    }
    if role in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER):
        now = datetime.now(UTC)
        impacts = db.execute(select(
            DataSet.id, DataSet.reference_code, DataSet.name, func.count(func.distinct(tuple_(
                DatasetRecordEntityLineage.version_id,
                DatasetRecordEntityLineage.ordinal,
            ))),
        ).select_from(EntityClaim).join(
            DatasetRecordEntityLineage,
            (DatasetRecordEntityLineage.source_namespace == EntityClaim.source_namespace)
            & (DatasetRecordEntityLineage.source_object == EntityClaim.source_object)
            & (DatasetRecordEntityLineage.token_version == EntityClaim.token_version)
            & (DatasetRecordEntityLineage.entity_token == EntityClaim.entity_token),
        ).join(DataSet, DataSet.id == DatasetRecordEntityLineage.dataset_id).where(
            EntityClaim.source_namespace == f"connection:{connection_id}",
            EntityClaim.status == "active", EntityClaim.expires_at > now,
            DatasetRecordEntityLineage.workspace_id == workspace_id,
            DatasetRecordEntityLineage.dataset_id != EntityClaim.dataset_id,
        ).group_by(DataSet.id, DataSet.reference_code, DataSet.name).order_by(DataSet.name)).all()
        result["operational_audit"] = {
            "active_claim_count": db.scalar(select(func.count(EntityClaim.id)).where(
                EntityClaim.workspace_id == workspace_id,
                EntityClaim.source_namespace == f"connection:{connection_id}",
                EntityClaim.status == "active", EntityClaim.expires_at > now,
            )) or 0,
            "affected_datasets": [{"dataset_id": str(row[0]), "reference_code": row[1],
                                   "name": row[2], "blocked_record_count": row[3]} for row in impacts],
        }
    return result


@router.put("/workspaces/{workspace_id}/connections/{connection_id}/entity-locking")
def set_entity_locking(
    workspace_id: UUID,
    connection_id: UUID,
    payload: dict[str, bool],
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    role = require_connection_editor(db, principal, workspace_id)
    if set(payload) != {"enabled"} or not isinstance(payload["enabled"], bool):
        raise HTTPException(status_code=422, detail="Provide only an enabled boolean")
    connection = db.scalar(select(WorkspaceConnection).where(
        WorkspaceConnection.id == connection_id,
        WorkspaceConnection.workspace_id == workspace_id,
    ).with_for_update())
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    if connection.connector_type not in ("postgresql", "mysql", "oracle", "mssql", "csv", "json", "delimited_text"):
        raise HTTPException(status_code=409, detail="Entity locking is not available for this connection type")
    if connection.connector_type not in ("postgresql", "mysql", "oracle", "mssql") and (
        connection.direction not in ("source", "both") or connection.operation_scope not in ("read_only", "read_write")
    ):
        raise HTTPException(status_code=409, detail="Entity locking requires a readable file source connection")
    enabled = payload["enabled"]
    from tigr.entity_claims import lock_source_namespace
    lock_source_namespace(db, f"connection:{connection_id}")
    if enabled:
        snapshot = db.scalar(select(ConnectionStructureSnapshot.id).where(
            ConnectionStructureSnapshot.connection_id == connection_id,
            ConnectionStructureSnapshot.workspace_id == workspace_id,
        ).order_by(ConnectionStructureSnapshot.version_number.desc()).limit(1))
        approved = db.scalar(select(LockPropagationConfiguration.id).where(
            LockPropagationConfiguration.connection_id == connection_id,
            LockPropagationConfiguration.workspace_id == workspace_id,
            LockPropagationConfiguration.structure_snapshot_id == snapshot,
            LockPropagationConfiguration.status == "approved",
        ).limit(1)) if snapshot else None
        if not approved:
            raise HTTPException(status_code=409, detail="Approve entity lock keys for the current structure snapshot before enabling entity locking")
        now = datetime.now(UTC)
        active_source_lock = db.scalar(select(DataSetLock.id).join(
            DataSetVersion, DataSetVersion.id == DataSetLock.version_id,
        ).join(
            DatasetRecordEntityLineage, DatasetRecordEntityLineage.version_id == DataSetLock.version_id,
        ).where(
            DatasetRecordEntityLineage.source_namespace == f"connection:{connection_id}",
            DataSetLock.status == "active", DataSetLock.expires_at > now,
        ).limit(1))
        if active_source_lock:
            raise HTTPException(status_code=409, detail="Release active version locks for this source before re-enabling entity locking")
    connection.entity_locking_enabled = enabled
    connection.updated_by = principal.subject
    revoked_count = 0
    if not enabled:
        now = datetime.now(UTC)
        revoked_count = db.execute(update(EntityClaim).where(
            EntityClaim.workspace_id == workspace_id,
            EntityClaim.source_namespace == f"connection:{connection_id}",
            EntityClaim.status == "active",
        ).values(status="released", released_at=now, release_reason="feature_disabled")).rowcount or 0
    audit_connection(db, request, principal, workspace_id, role,
                     "entity_locking.enabled" if enabled else "entity_locking.disabled", connection,
                     {"claims_revoked_count": int(revoked_count)})
    db.commit()
    return {"connection_id": str(connection_id), "enabled": enabled}


@router.post("/workspaces/{workspace_id}/connections/{connection_id}/lock-configurations", status_code=201)
def approve_lock_configuration(
    workspace_id: UUID,
    connection_id: UUID,
    payload: LockConfigurationInput,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    role = require_connection_editor(db, principal, workspace_id)
    if role != HumanRole.TEST_DATA_ENGINEER:
        raise HTTPException(status_code=403, detail="Only a Test Data Engineer can approve entity lock keys")
    connection = db.scalar(select(WorkspaceConnection).where(
        WorkspaceConnection.id == connection_id,
        WorkspaceConnection.workspace_id == workspace_id,
    ).with_for_update())
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    if connection.connector_type not in ("postgresql", "mysql", "oracle", "mssql", "csv", "json", "delimited_text"):
        raise HTTPException(status_code=409, detail="Entity lock key configuration is not available for this connection type")
    if connection.connector_type not in ("postgresql", "mysql", "oracle", "mssql") and (
        connection.direction not in ("source", "both") or connection.operation_scope not in ("read_only", "read_write")
    ):
        raise HTTPException(status_code=409, detail="Entity lock keys require a readable file source connection")
    snapshot = db.scalar(select(ConnectionStructureSnapshot).where(
        ConnectionStructureSnapshot.connection_id == connection_id,
        ConnectionStructureSnapshot.workspace_id == workspace_id,
    ).order_by(ConnectionStructureSnapshot.version_number.desc()).limit(1))
    if snapshot is None:
        raise HTTPException(status_code=409, detail="Test the source connection successfully to capture a structure snapshot first")
    try:
        normalized = validate_configuration(payload, snapshot.structure, connection.connector_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    latest_version = db.scalar(select(func.max(LockPropagationConfiguration.version_number)).where(
        LockPropagationConfiguration.connection_id == connection_id,
    )) or 0
    configuration = LockPropagationConfiguration(
        id=uuid4(), workspace_id=workspace_id, connection_id=connection_id,
        structure_snapshot_id=snapshot.id, version_number=latest_version + 1,
        configuration=normalized, status="approved", created_by=principal.subject,
        approved_by=principal.subject, approved_at=datetime.now(UTC),
    )
    db.add(configuration)
    audit_connection(db, request, principal, workspace_id, role, "lock_configuration.approved", connection, {
        "configuration_version": configuration.version_number,
        "structure_snapshot_version": snapshot.version_number,
        "anchor_count": len(normalized["anchors"]),
    })
    db.commit()
    db.refresh(configuration)
    return {
        "id": configuration.id,
        "connection_id": connection_id,
        "structure_snapshot_id": snapshot.id,
        "version_number": configuration.version_number,
        "configuration": configuration.configuration,
        "approved_by": configuration.approved_by,
        "approved_at": configuration.approved_at,
    }


@router.get("/workspaces/{workspace_id}/connections/{connection_id}/tests")
def list_connection_test_runs(
    workspace_id: UUID,
    connection_id: UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    workspace_role(db, principal, workspace_id)
    connection = db.get(WorkspaceConnection, connection_id)
    if connection is None or connection.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")
    runs = db.scalars(
        select(ConnectionTestRun)
        .where(ConnectionTestRun.workspace_id == workspace_id, ConnectionTestRun.connection_id == connection_id)
        .order_by(ConnectionTestRun.started_at.desc())
        .limit(50)
    ).all()
    return [{
        "id": run.id, "requested_by": run.requested_by, "started_at": run.started_at,
        "finished_at": run.finished_at, "outcome": run.outcome, "safe_error_code": run.safe_error_code,
        "safe_message": run.safe_message, "correlation_id": run.correlation_id,
    } for run in runs]


@router.post("/workspaces/{workspace_id}/connections/{connection_id}/enable", response_model=ConnectionOut)
def enable_connection(
    workspace_id: UUID,
    connection_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ConnectionOut:
    role = require_connection_editor(db, principal, workspace_id)
    connection = db.get(WorkspaceConnection, connection_id)
    if connection is None or connection.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")
    if connection.tested_at is None or not (connection.test_result or {}).get("succeeded"):
        raise HTTPException(status_code=409, detail="Run a successful connection test before enabling")
    connection.status = "enabled"
    connection.updated_by = principal.subject
    connection.updated_at = datetime.now(UTC)
    audit_connection(db, request, principal, workspace_id, role, "connection.enabled", connection, {"connector_type": connection.connector_type})
    db.commit()
    db.refresh(connection)
    return connection_out(connection)


@router.post("/workspaces/{workspace_id}/connections/{connection_id}/disable", response_model=ConnectionOut)
def disable_connection(
    workspace_id: UUID,
    connection_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> ConnectionOut:
    role = require_connection_editor(db, principal, workspace_id)
    connection = db.get(WorkspaceConnection, connection_id)
    if connection is None or connection.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")
    connection.status = "disabled"
    connection.updated_by = principal.subject
    connection.updated_at = datetime.now(UTC)
    audit_connection(db, request, principal, workspace_id, role, "connection.disabled", connection, {"connector_type": connection.connector_type})
    db.commit()
    db.refresh(connection)
    return connection_out(connection)
