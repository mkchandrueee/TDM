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
from tigr.connection_models import ConnectionStructureSnapshot, LockPropagationConfiguration
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
    lock_metadata = (version.provenance or {}).get("lock_metadata") or {}
    try:
        snapshot = db.get(ConnectionStructureSnapshot, UUID(str(lock_metadata.get("structure_snapshot_id"))))
        config = db.get(LockPropagationConfiguration, UUID(str(lock_metadata.get("lock_configuration_id"))))
    except (TypeError, ValueError):
        return []
    if (snapshot is None or config is None or snapshot.connection_id != connection_id
            or config.connection_id != connection_id or config.structure_snapshot_id != snapshot.id
            or config.status != "approved"):
        return []
    namespace = str((snapshot.structure or {}).get("source_identity_namespace") or f"connection:{connection_id}")
    mapping = {}
    for field in fields:
        data = field.model_dump() if hasattr(field, "model_dump") else dict(field)
        obj = data.get("source_object") or ""
        source = data.get("source_field")
        target = data.get("target_field")
        if obj and source and target:
            mapping.setdefault(str(obj), {})[str(source)] = str(target)
    anchors = (config.configuration or {}).get("anchors", [])
    participating_anchors = [anchor for anchor in anchors if str(anchor.get("object_name") or "") in mapping]
    if not participating_anchors:
        return []
    settings = get_settings()
    secret = settings.identity_secret
    if not secret:
        raise ValueError("Protected record identity secret is not configured")
    key_version = settings.identity_key_version
    result = []
    for ordinal, row in enumerate(rows):
        for anchor in participating_anchors:
            obj = str(anchor.get("object_name") or "")
            key_fields = list(anchor.get("key_fields") or [])
            field_map = mapping.get(obj, {})
            # An anchor for an object absent from this extraction is unrelated to
            # this row shape. For a participating object, never silently emit
            # rows without a complete configured identity.
            if obj not in mapping:
                continue
            target_fields = [field_map.get(str(name)) for name in key_fields]
            if not key_fields or any(name is None or name not in row for name in target_fields):
                raise ValueError("Configured identity key fields must be included in the source mapping")
            values = [row[name] for name in target_fields]
            if any(value is None for value in values):
                raise ValueError("Configured identity key values are missing from a source row")
            result.append({
                "ordinal": ordinal, "source_namespace": namespace, "source_object": obj,
                "key_definition": {"kind": anchor.get("key_kind"), "name": anchor.get("key_name"), "fields": key_fields},
                "token_version": f"{TOKEN_ALGORITHM}:{key_version}",
                "entity_token": entity_token(secret, namespace, obj, key_fields, values, key_version),
                "structure_snapshot_id": snapshot.id, "lock_configuration_id": config.id,
                "relationship_path": [
                    {"name": path.get("name"), "steps": list(path.get("steps") or [])}
                    for path in (config.configuration or {}).get("paths", [])
                    if path.get("anchor_object") == obj
                ],
            })
    return result


def add_hidden_anchor_fields(db: Session, version, connection_id: UUID | None, fields: list) -> list:
    """Select configured key columns into temporary worker-only aliases when needed."""
    if connection_id is None:
        return fields
    metadata = (version.provenance or {}).get("lock_metadata") or {}
    try:
        snapshot = db.get(ConnectionStructureSnapshot, UUID(str(metadata.get("structure_snapshot_id"))))
        config = db.get(LockPropagationConfiguration, UUID(str(metadata.get("lock_configuration_id"))))
    except (TypeError, ValueError):
        return fields
    if snapshot is None or config is None or config.status != "approved" or config.connection_id != connection_id:
        return fields
    selected_objects = {
        str((field.model_dump() if hasattr(field, "model_dump") else field).get("source_object") or "")
        for field in fields
    }
    existing = {}
    for field in fields:
        data = field.model_dump() if hasattr(field, "model_dump") else dict(field)
        existing.setdefault(str(data.get("source_object") or ""), set()).add(str(data.get("source_field") or ""))
    objects = {str(item.get("name")): item for item in (snapshot.structure or {}).get("objects", []) if isinstance(item, dict)}
    occupied_targets = {
        str((field.model_dump() if hasattr(field, "model_dump") else field).get("target_field") or "")
        for field in fields
    }
    result = list(fields)
    from tigr.scenario_schemas import MappingField
    for anchor_index, anchor in enumerate((config.configuration or {}).get("anchors", [])):
        obj_name = str(anchor.get("object_name") or "")
        if obj_name not in selected_objects:
            continue
        obj = objects.get(obj_name) or {}
        source_metadata = {str(item.get("name")): item for item in obj.get("fields", []) if isinstance(item, dict)}
        for field_index, key_field in enumerate(anchor.get("key_fields") or []):
            key_field = str(key_field)
            if key_field in existing.get(obj_name, set()):
                continue
            metadata_field = source_metadata.get(key_field)
            if metadata_field is None:
                raise ValueError("Configured identity key is missing from the pinned source structure")
            target = f"__tigr_identity_{anchor_index}_{field_index}"
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
    return [{
        "ordinal": ordinals[row.ordinal], "source_namespace": row.source_namespace,
        "source_object": row.source_object, "key_definition": row.key_definition,
        "token_version": row.token_version, "entity_token": row.entity_token,
        "structure_snapshot_id": row.structure_snapshot_id, "lock_configuration_id": row.lock_configuration_id,
        "relationship_path": row.relationship_path or [], "parent_version_id": parent_version_id,
        "parent_ordinal": row.ordinal,
    } for row in parent_rows if row.ordinal in ordinals]


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
