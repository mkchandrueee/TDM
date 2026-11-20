"""Versioned HMAC identities for source records; raw key values never leave memory."""

import hashlib
import hmac
import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from tigr.config import get_settings
from tigr.connection_models import (
    ConnectionStructureSnapshot,
    LockPropagationConfiguration,
    WorkspaceConnection,
)
from tigr.schema_records import DatasetRecordEntityLineage

TOKEN_ALGORITHM = "hmac-sha256"


def _typed(value) -> dict:
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, UUID):
        return {"type": "uuid", "value": str(value)}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if value is None:
        return {"type": "null", "value": None}
    return {"type": type(value).__name__, "value": value}


def entity_token(secret: str, namespace: str, source_object: str, key_definition: list[str], values: list,
                 key_version: str = "v1") -> str:
    """Build a deterministic, type-aware HMAC without retaining the source values."""
    if not secret or not key_version or len(key_definition) != len(values) or any(value is None for value in values):
        raise ValueError("A configured identity key and secret are required")
    token_version = f"{TOKEN_ALGORITHM}:{key_version}"
    material = json.dumps({
        "format": token_version, "namespace": namespace, "object": source_object,
        "key_definition": key_definition, "values": [_typed(value) for value in values],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


def capture_entity_lineage(db: Session, dataset, version, rows: list[dict], fields: list, connection_id: UUID | None) -> list[dict]:
    """Capture configured anchor tokens from pre-mask rows in worker memory."""
    if connection_id is None:
        return []
    if not _connection_entity_locking_enabled(db, connection_id):
        return []
    lock_metadata = (version.provenance or {}).get("lock_metadata") or {}
    try:
        snapshot = db.get(ConnectionStructureSnapshot, UUID(str(lock_metadata.get("structure_snapshot_id"))))
        config = db.get(LockPropagationConfiguration, UUID(str(lock_metadata.get("lock_configuration_id"))))
    except (TypeError, ValueError):
        raise ValueError("approved_identity_configuration_unavailable")
    if (snapshot is None or config is None or snapshot.connection_id != connection_id
            or config.connection_id != connection_id or config.structure_snapshot_id != snapshot.id
            or config.status != "approved"):
        raise ValueError("approved_identity_configuration_unavailable")
    namespace = str((snapshot.structure or {}).get("source_identity_namespace") or f"connection:{connection_id}")
    mapping = {}
    connection = db.get(WorkspaceConnection, connection_id)
    file_source = connection is not None and connection.connector_type not in ("postgresql", "mysql")
    for field in fields:
        data = field.model_dump() if hasattr(field, "model_dump") else dict(field)
        obj = "source_file" if file_source else data.get("source_object") or ""
        source = data.get("source_field")
        target = data.get("target_field")
        if obj and source and target:
            mapping.setdefault(str(obj), {})[str(source)] = str(target)
    anchors = (config.configuration or {}).get("anchors", [])
    participating_anchors = []
    for anchor in anchors:
        obj = str(anchor.get("object_name") or "")
        key_fields = list(anchor.get("key_fields") or [])
        direct = all(field in mapping.get(obj, {}) for field in key_fields)
        if direct:
            participating_anchors.append(anchor)
    if not participating_anchors:
        # Entity locking is opt-in per mapped source key. No selected key means
        # there is no identity to lock for this dataset; masking can continue.
        return []
    settings = get_settings()
    secret = settings.identity_secret
    if not secret:
        raise ValueError("identity_secret_unavailable")
    key_version = settings.identity_key_version
    result = []
    for ordinal, row in enumerate(rows):
        for anchor in participating_anchors:
            obj = str(anchor.get("object_name") or "")
            key_fields = list(anchor.get("key_fields") or [])
            field_map = mapping.get(obj, {})
            target_fields = [field_map.get(str(name)) for name in key_fields]
            values = [row[name] for name in target_fields] if target_fields and all(
                name is not None and name in row for name in target_fields
            ) else None
            if values is None:
                # A selected query row must map this anchor's key directly.
                continue
            if not key_fields or len(values) != len(key_fields):
                raise ValueError("Configured identity key fields must be included in the source mapping")
            if any(value is None for value in values):
                # A configured entity can be absent from an outer-joined row
                # (for example, a customer without a matching transaction).
                # There is no identity to claim for that entity on this row;
                # retain lineage for the other present entities and continue.
                continue
            if file_source and any(isinstance(value, str) and not value.strip() for value in values):
                # Blank file values are not entity identifiers. Avoid creating
                # one shared fingerprint for every row with an empty key.
                continue
            result.append({
                "ordinal": ordinal, "source_namespace": namespace, "source_object": obj,
                "key_definition": {"kind": anchor.get("key_kind"), "name": anchor.get("key_name"), "fields": key_fields},
                "token_version": f"{TOKEN_ALGORITHM}:{key_version}",
                "entity_token": entity_token(secret, namespace, obj, key_fields, values, key_version),
                "structure_snapshot_id": snapshot.id, "lock_configuration_id": config.id,
                "relationship_path": [],
            })
    return result


def add_hidden_anchor_fields(db: Session, version, connection_id: UUID | None, fields: list) -> list:
    """Select configured key columns into temporary worker-only aliases when needed."""
    if connection_id is None:
        return fields
    if not _connection_entity_locking_enabled(db, connection_id):
        return fields
    metadata = (version.provenance or {}).get("lock_metadata") or {}
    try:
        snapshot = db.get(ConnectionStructureSnapshot, UUID(str(metadata.get("structure_snapshot_id"))))
        config = db.get(LockPropagationConfiguration, UUID(str(metadata.get("lock_configuration_id"))))
    except (TypeError, ValueError):
        return fields
    if snapshot is None or config is None or config.status != "approved" or config.connection_id != connection_id:
        return fields
    connection = db.get(WorkspaceConnection, connection_id)
    file_source = connection is not None and connection.connector_type not in ("postgresql", "mysql")
    selected_objects = ({"source_file"} if file_source else {
        str((field.model_dump() if hasattr(field, "model_dump") else field).get("source_object") or "")
        for field in fields
    })
    existing = {}
    for field in fields:
        data = field.model_dump() if hasattr(field, "model_dump") else dict(field)
        object_name = "source_file" if file_source else str(data.get("source_object") or "")
        existing.setdefault(object_name, set()).add(str(data.get("source_field") or ""))
    objects = {str(item.get("name")): item for item in (snapshot.structure or {}).get("objects", []) if isinstance(item, dict)}
    occupied_targets = {
        str((field.model_dump() if hasattr(field, "model_dump") else field).get("target_field") or "")
        for field in fields
    }
    result = list(fields)
    from tigr.scenario_schemas import MappingField
    configuration = config.configuration or {}
    required_fields: dict[str, set[str]] = {}
    for anchor in configuration.get("anchors", []):
        obj_name = str(anchor.get("object_name") or "")
        if obj_name in selected_objects:
            required_fields.setdefault(obj_name, set()).update(map(str, anchor.get("key_fields") or []))
    for object_index, (obj_name, key_fields) in enumerate(required_fields.items()):
        obj = objects.get(obj_name) or {}
        source_metadata = {str(item.get("name")): item for item in obj.get("fields", []) if isinstance(item, dict)}
        for field_index, key_field in enumerate(sorted(key_fields)):
            if key_field in existing.get(obj_name, set()):
                continue
            metadata_field = source_metadata.get(key_field)
            if metadata_field is None:
                raise ValueError("Configured identity key is missing from the pinned source structure")
            target = f"__tigr_identity_{object_index}_{field_index}"
            while target in occupied_targets:
                target += "_"
            occupied_targets.add(target)
            result.append(MappingField(
                source_field=key_field, target_field=target,
                data_type=str(metadata_field.get("type") or "TEXT"), source_object=obj_name,
            ))
    return result


def copy_entity_lineage(db: Session, parent_version_id: UUID, ordinals: dict[int, int]) -> list[dict]:
    """Copy protected identities by row ordinal while recording version ancestry."""
    parent_rows = db.scalars(select(DatasetRecordEntityLineage).where(
        DatasetRecordEntityLineage.version_id == parent_version_id,
    )).all()
    namespaces = {row.source_namespace for row in parent_rows}
    from tigr.entity_claims import enabled_source_namespaces, lock_source_namespace
    for namespace in sorted(namespaces):
        lock_source_namespace(db, namespace)
    enabled = enabled_source_namespaces(db, namespaces)
    return [{
        "ordinal": ordinals[row.ordinal], "source_namespace": row.source_namespace,
        "source_object": row.source_object, "key_definition": row.key_definition,
        "token_version": row.token_version, "entity_token": row.entity_token,
        "structure_snapshot_id": row.structure_snapshot_id, "lock_configuration_id": row.lock_configuration_id,
        "relationship_path": row.relationship_path or [], "parent_version_id": parent_version_id,
        "parent_ordinal": row.ordinal,
    } for row in parent_rows if row.ordinal in ordinals and row.source_namespace in enabled]


def persist_entity_lineage(db: Session, dataset, version, lineage_refs: list[dict]) -> None:
    db.execute(DatasetRecordEntityLineage.__table__.delete().where(DatasetRecordEntityLineage.version_id == version.id))
    db.add_all([DatasetRecordEntityLineage(
        id=uuid4(), workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
        ordinal=item["ordinal"], source_namespace=item["source_namespace"], source_object=item["source_object"],
        key_definition=item["key_definition"], token_version=item["token_version"], entity_token=item["entity_token"],
        structure_snapshot_id=item.get("structure_snapshot_id"), lock_configuration_id=item.get("lock_configuration_id"),
        relationship_path=item.get("relationship_path") or [], parent_version_id=item.get("parent_version_id"),
        parent_ordinal=item.get("parent_ordinal"),
    ) for item in lineage_refs])


def _connection_entity_locking_enabled(db: Session, connection_id: UUID) -> bool:
    from tigr.entity_claims import enabled_source_namespaces, lock_source_namespace
    namespace = f"connection:{connection_id}"
    lock_source_namespace(db, namespace)
    return namespace in enabled_source_namespaces(db, {namespace}) and db.get(WorkspaceConnection, connection_id) is not None
