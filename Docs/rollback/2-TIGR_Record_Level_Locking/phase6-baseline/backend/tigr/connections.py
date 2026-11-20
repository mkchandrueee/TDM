from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tigr.access import ensure_user, record_audit, workspace_role
from tigr.connection_models import (
    ConnectionStructureSnapshot,
    LockPropagationConfiguration,
    WorkspaceConnection,
)
from tigr.connection_schemas import (
    ConnectionCreate,
    ConnectionOut,
    ConnectionUpdate,
    validate_connector_fields,
)
from tigr.connection_testing import test_connection
from tigr.datasets import DataSet
from tigr.governance import EntityClaim
from tigr.identity import HumanRole, Principal, get_db, get_principal
from tigr.lock_configuration import LockConfigurationInput, validate_configuration
from tigr.schema_records import ConnectionTestRun, DatasetRecordEntityLineage

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
    connection = db.scalar(select(WorkspaceConnection).where(
        WorkspaceConnection.id == connection_id,
        WorkspaceConnection.workspace_id == workspace_id,
    ).with_for_update())
    if connection is None:
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
    if result.succeeded and connection.connector_type == "postgresql" and result.structure is not None:
        latest_snapshot = db.scalar(select(func.max(ConnectionStructureSnapshot.version_number)).where(
            ConnectionStructureSnapshot.connection_id == connection.id,
        )) or 0
        snapshot_structure = dict(result.structure)
        snapshot_structure["structure_snapshot_version"] = latest_snapshot + 1
        snapshot_structure["source_identity_namespace"] = f"connection:{connection.id}"
        connection.structure_preview = snapshot_structure
        db.add(ConnectionStructureSnapshot(
            id=uuid4(), workspace_id=workspace_id, connection_id=connection.id,
            version_number=latest_snapshot + 1, structure=snapshot_structure,
            captured_by=principal.subject, captured_at=test_finished_at,
        ))
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
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    role = workspace_role(db, principal, workspace_id)
    connection = db.get(WorkspaceConnection, connection_id)
    if connection is None or connection.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Connection not found")
    if connection.connector_type != "postgresql":
        raise HTTPException(status_code=409, detail="Lock relationship metadata is available for PostgreSQL connections")
    snapshot = db.scalar(select(ConnectionStructureSnapshot).where(
        ConnectionStructureSnapshot.connection_id == connection_id,
        ConnectionStructureSnapshot.workspace_id == workspace_id,
    ).order_by(ConnectionStructureSnapshot.version_number.desc()).limit(1))
    configuration = db.scalar(select(LockPropagationConfiguration).where(
        LockPropagationConfiguration.connection_id == connection_id,
        LockPropagationConfiguration.workspace_id == workspace_id,
        LockPropagationConfiguration.status == "approved",
    ).order_by(LockPropagationConfiguration.version_number.desc()).limit(1))
    result = {
        "connection_id": connection_id,
        "source_identity_namespace": f"connection:{connection_id}",
        "snapshot": ({"id": snapshot.id, "version_number": snapshot.version_number,
                      "captured_by": snapshot.captured_by, "captured_at": snapshot.captured_at,
                      "structure": snapshot.structure} if snapshot else None),
        "configuration": ({"id": configuration.id, "version_number": configuration.version_number,
                           "structure_snapshot_id": configuration.structure_snapshot_id,
                           "matches_current_snapshot": bool(snapshot and configuration.structure_snapshot_id == snapshot.id),
                           "configuration": configuration.configuration,
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
        raise HTTPException(status_code=403, detail="Only a Test Data Engineer can approve lock relationships")
    connection = db.scalar(select(WorkspaceConnection).where(
        WorkspaceConnection.id == connection_id,
        WorkspaceConnection.workspace_id == workspace_id,
    ).with_for_update())
    if connection is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    if connection.connector_type != "postgresql":
        raise HTTPException(status_code=409, detail="Lock relationship configuration requires a PostgreSQL connection")
    snapshot = db.scalar(select(ConnectionStructureSnapshot).where(
        ConnectionStructureSnapshot.connection_id == connection_id,
        ConnectionStructureSnapshot.workspace_id == workspace_id,
    ).order_by(ConnectionStructureSnapshot.version_number.desc()).limit(1))
    if snapshot is None:
        raise HTTPException(status_code=409, detail="Test the PostgreSQL connection successfully to capture a structure snapshot first")
    try:
        normalized = validate_configuration(payload, snapshot.structure)
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
        "path_count": len(normalized["paths"]),
        "manual_relationship_count": len(normalized["manual_relationships"]),
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
