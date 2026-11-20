from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tigr.access import ensure_user, record_audit, workspace_role
from tigr.connection_models import WorkspaceConnection
from tigr.connection_schemas import (
    ConnectionCreate,
    ConnectionOut,
    ConnectionUpdate,
    validate_connector_fields,
)
from tigr.connection_testing import test_connection
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.schema_records import ConnectionTestRun

router = APIRouter(prefix="/api/v1", tags=["workspace connections"])


def require_connection_editor(db: Session, principal: Principal, workspace_id: UUID) -> HumanRole:
    role = workspace_role(db, principal, workspace_id)
    if role not in (HumanRole.ADMIN, HumanRole.TEST_DATA_ENGINEER):
        raise HTTPException(status_code=403, detail="Connection configuration requires an Admin or Test Data Engineer")
    return role


def audit_connection(db: Session, request: Request, principal: Principal, workspace_id: UUID, role: HumanRole, action: str, connection: WorkspaceConnection, metadata: dict[str, Any] | None = None) -> None:
    record_audit(db, principal, request.state.request_id, action, "workspace_connection", str(connection.id), workspace_id, role, metadata or {})


def connection_out(connection: WorkspaceConnection) -> ConnectionOut:
    return ConnectionOut(
        id=connection.id, workspace_id=connection.workspace_id, name=connection.name,
        connector_type=connection.connector_type, direction=connection.direction,
        environment=connection.environment, purpose=connection.purpose, owner=connection.owner,
        operation_scope=connection.operation_scope, secret_configured=bool(connection.secret_reference),
        connector_config=connection.connector_config, status=connection.status,
        tested_at=connection.tested_at, tested_by=connection.tested_by,
        test_result=connection.test_result, structure_preview=connection.structure_preview,
        created_by=connection.created_by, updated_by=connection.updated_by,
        created_at=connection.created_at, updated_at=connection.updated_at,
    )


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
    if db.scalar(select(WorkspaceConnection.id).where(WorkspaceConnection.workspace_id == workspace_id, WorkspaceConnection.name == payload.name)):
        raise HTTPException(status_code=409, detail="A connection with this name already exists in the workspace")
    connection = WorkspaceConnection(
        id=uuid4(), workspace_id=workspace_id, name=payload.name, connector_type=payload.connector_type.value,
        direction=payload.direction.value, environment=payload.environment, purpose=payload.purpose,
        owner=payload.owner or principal.subject, operation_scope=payload.operation_scope.value,
        secret_reference=payload.secret_reference, connector_config=payload.connector_config,
        status="draft", created_by=principal.subject, updated_by=principal.subject,
    )
    db.add(connection)
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
    connection = db.get(WorkspaceConnection, connection_id)
    if connection is None or connection.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return connection_out(connection)
    if "name" in changes and db.scalar(select(WorkspaceConnection.id).where(WorkspaceConnection.workspace_id == workspace_id, WorkspaceConnection.name == changes["name"], WorkspaceConnection.id != connection_id)):
        raise HTTPException(status_code=409, detail="A connection with this name already exists in the workspace")
    for enum_key in ("connector_type", "direction", "operation_scope"):
        if enum_key in changes and changes[enum_key] is not None:
            changes[enum_key] = changes[enum_key].value
    new_type = changes.get("connector_type", connection.connector_type)
    new_secret_reference = changes.get("secret_reference", connection.secret_reference)
    new_config = changes.get("connector_config", connection.connector_config)
    try:
        changes["connector_config"] = validate_connector_fields(new_type, new_secret_reference, new_config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Connection configuration is invalid") from exc
    for key, value in changes.items():
        setattr(connection, key, value)
    connection.status = "draft"
    connection.tested_at = None
    connection.tested_by = None
    connection.test_result = None
    connection.structure_preview = None
    connection.updated_by = principal.subject
    connection.updated_at = datetime.now(UTC)
    audit_connection(db, request, principal, workspace_id, role, "connection.updated", connection, {"changed_fields": sorted(changes), "status": "draft"})
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
    connection = db.get(WorkspaceConnection, connection_id)
    if connection is None or connection.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")
    test_started_at = datetime.now(UTC)
    result = test_connection(connection)
    test_finished_at = datetime.now(UTC)
    connection.tested_at = test_finished_at
    connection.tested_by = principal.subject
    connection.test_result = {"succeeded": result.succeeded, "message": result.message}
    connection.structure_preview = result.structure if result.succeeded else None
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
