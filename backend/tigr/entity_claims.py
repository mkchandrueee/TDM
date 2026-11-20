"""Transactional helpers for cross-dataset protected entity claims."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tigr.config import get_settings
from tigr.connection_models import LockPropagationConfiguration, WorkspaceConnection
from tigr.governance import DataSetLock, EntityClaim
from tigr.jobs import Job
from tigr.schema_records import DataSetRecord, DatasetRecordEntityLineage


def lock_source_namespace(db: Session, namespace: str) -> None:
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(namespace, 0))))


def enabled_source_namespaces(db: Session, namespaces: list[str] | set[str]) -> set[str]:
    if not get_settings().entity_locking_enabled or not namespaces:
        return set()
    connection_ids = []
    for namespace in namespaces:
        prefix, _, raw_id = namespace.partition(":")
        if prefix == "connection":
            try:
                connection_ids.append(UUID(raw_id))
            except ValueError:
                continue
    enabled = set(db.scalars(select(WorkspaceConnection.id).where(
        WorkspaceConnection.id.in_(connection_ids),
        WorkspaceConnection.entity_locking_enabled.is_(True),
    )).all()) if connection_ids else set()
    return {f"connection:{connection_id}" for connection_id in enabled}


def _expire_namespace_claims(db: Session, namespace: str, now: datetime) -> None:
    db.execute(EntityClaim.__table__.update().where(
        EntityClaim.source_namespace == namespace, EntityClaim.status == "active", EntityClaim.expires_at <= now,
    ).values(status="expired", released_at=now, release_reason="lease_expired"))


def lineage_claim_keys(db: Session, version_id: UUID) -> list[dict]:
    rows = db.scalars(select(DatasetRecordEntityLineage).where(
        DatasetRecordEntityLineage.version_id == version_id,
    ).order_by(DatasetRecordEntityLineage.ordinal, DatasetRecordEntityLineage.id)).all()
    distinct = {}
    for row in rows:
        key = (row.source_namespace, row.source_object, row.token_version, row.entity_token)
        distinct[key] = {
            "source_namespace": row.source_namespace, "source_object": row.source_object,
            "key_definition": row.key_definition, "token_version": row.token_version,
            "entity_token": row.entity_token,
        }
    return list(distinct.values())


def create_entity_claims(db: Session, dataset, version, lock: DataSetLock, actor_id: str, now: datetime) -> tuple[int, int, int, int]:
    lineage_rows = db.scalars(select(DatasetRecordEntityLineage).where(
        DatasetRecordEntityLineage.version_id == version.id,
    ).order_by(DatasetRecordEntityLineage.ordinal, DatasetRecordEntityLineage.id)).all()
    keys = lineage_claim_keys(db, version.id)
    namespaces = {item["source_namespace"] for item in keys}
    metadata = (version.provenance or {}).get("lock_metadata") or {}
    source_connection_id = (version.provenance or {}).get("source_connection_id")
    source_namespace = metadata.get("source_identity_namespace") or (
        f"connection:{source_connection_id}" if source_connection_id else None
    )
    if source_namespace:
        namespaces.add(source_namespace)
    for namespace in sorted(namespaces):
        lock_source_namespace(db, namespace)
    enabled = enabled_source_namespaces(db, namespaces)
    if source_namespace in enabled and (version.provenance or {}).get("origin") == "source_backed":
        record_ordinals = set(db.scalars(select(DataSetRecord.ordinal).where(
            DataSetRecord.version_id == version.id,
        )).all())
        # Rows whose selected key could not be fingerprinted stay unavailable,
        # but must not prevent the identifiable rows from being locked. This
        # matches filter_dataset_records(), which already hides those rows from
        # QA preview and delivery.
        identity_unavailable = identity_unavailable_ordinals(db, version, record_ordinals)
        eligible_ordinals = record_ordinals - identity_unavailable
    else:
        record_ordinals = set(db.scalars(select(DataSetRecord.ordinal).where(
            DataSetRecord.version_id == version.id,
        )).all())
        identity_unavailable = set()
        eligible_ordinals = record_ordinals
    keys = [item for item in keys if item["source_namespace"] in enabled]
    for namespace in sorted(enabled):
        _expire_namespace_claims(db, namespace, now)
    # A conflict protects the whole output record. Keep the version lease, but
    # claim only entities from records that are currently available so the
    # holder can still use the disjoint portion of the data set.
    blocked_ordinals = conflicting_ordinals(db, version.id, actor_id, now)
    if eligible_ordinals and eligible_ordinals.issubset(blocked_ordinals):
        raise HTTPException(status_code=409, detail=(
            f"All {len(eligible_ordinals)} identifiable records in this data set are protected by another active lock. "
            "No records were made available; retry after the lease expires or is released."
        ))
    blocked_ordinals.update(identity_unavailable)
    total_ordinals = record_ordinals
    available_keys = {
        (row.source_namespace, row.source_object, row.token_version, row.entity_token)
        for row in lineage_rows if row.ordinal not in blocked_ordinals and row.source_namespace in enabled
    }
    keys = [item for item in keys if (
        item["source_namespace"], item["source_object"], item["token_version"], item["entity_token"]
    ) in available_keys]
    db.add_all([EntityClaim(
        id=uuid4(), workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
        lock_id=lock.id, actor_id=actor_id, source_namespace=item["source_namespace"],
        source_object=item["source_object"], key_definition=item["key_definition"],
        token_version=item["token_version"], entity_token=item["entity_token"],
        fencing_token=lock.fencing_token, acquired_at=now, expires_at=lock.expires_at, status="active",
    ) for item in keys])
    return (len(keys), len(blocked_ordinals & total_ordinals),
            len(total_ordinals - blocked_ordinals), len(identity_unavailable & total_ordinals))


def expire_entity_claims(db: Session, lock_id: UUID, now: datetime) -> None:
    db.execute(EntityClaim.__table__.update().where(
        EntityClaim.lock_id == lock_id, EntityClaim.status == "active", EntityClaim.expires_at <= now,
    ).values(status="expired", released_at=now, release_reason="lease_expired"))


def close_entity_claims(db: Session, lock_id: UUID, status: str, now: datetime, reason: str | None = None) -> None:
    db.execute(EntityClaim.__table__.update().where(
        EntityClaim.lock_id == lock_id, EntityClaim.status == "active",
    ).values(status=status, released_at=now, release_reason=reason))


def conflicting_ordinals(db: Session, version_id: UUID, actor_id: str, now: datetime) -> set[int]:
    namespaces = db.scalars(select(DatasetRecordEntityLineage.source_namespace).where(
        DatasetRecordEntityLineage.version_id == version_id,
    ).distinct()).all()
    for namespace in sorted(namespaces):
        lock_source_namespace(db, namespace)
    enabled = enabled_source_namespaces(db, set(namespaces))
    if not enabled:
        return set()
    for namespace in sorted(enabled):
        _expire_namespace_claims(db, namespace, now)
    return set(db.scalars(select(DatasetRecordEntityLineage.ordinal).distinct().join(
        EntityClaim,
        (EntityClaim.source_namespace == DatasetRecordEntityLineage.source_namespace)
        & (EntityClaim.source_object == DatasetRecordEntityLineage.source_object)
        & (EntityClaim.token_version == DatasetRecordEntityLineage.token_version)
        & (EntityClaim.entity_token == DatasetRecordEntityLineage.entity_token),
    ).where(
        DatasetRecordEntityLineage.version_id == version_id,
        EntityClaim.source_namespace.in_(enabled),
        EntityClaim.status == "active", EntityClaim.expires_at > now,
        EntityClaim.actor_id != actor_id,
    )).all())


def assert_source_namespace_available(
    db: Session,
    connection_id: UUID,
    actor_id: str,
    *,
    allow_locked_entities: bool = False,
) -> None:
    """Fail closed before opaque source queries when claim predicates cannot be composed safely."""
    namespace = f"connection:{connection_id}"
    lock_source_namespace(db, namespace)
    if namespace not in enabled_source_namespaces(db, {namespace}):
        return
    now = datetime.now(UTC)
    _expire_namespace_claims(db, namespace, now)
    # TDE/Admin source operations are governance workflows. They can inspect
    # and refresh identities held by QA leases; the resulting lineage still
    # marks those records protected for other QA users in every data set.
    if allow_locked_entities:
        return
    conflict = db.scalar(select(EntityClaim.id).where(
        EntityClaim.source_namespace == namespace, EntityClaim.status == "active",
        EntityClaim.expires_at > now, EntityClaim.actor_id != actor_id,
    ).limit(1))
    if conflict:
        raise HTTPException(status_code=409, detail="Source records are temporarily unavailable under another active lock. Retry after the lease expires or is released, or contact your workspace Admin or Test Data Engineer.")


def identity_unavailable_ordinals(db: Session, version, ordinals: list[int] | set[int] | None = None) -> set[int]:
    """Return source-backed rows missing lineage for an enabled entity-lock namespace."""
    provenance = getattr(version, "provenance", None) or {}
    if provenance.get("origin") != "source_backed":
        return set()
    metadata = provenance.get("lock_metadata") or {}
    source_connection_id = provenance.get("source_connection_id")
    namespace = metadata.get("source_identity_namespace") or (
        f"connection:{source_connection_id}" if source_connection_id else None
    )
    if not namespace or namespace not in enabled_source_namespaces(db, {namespace}):
        return set()
    # Locking applies only to source entities whose approved keys are both
    # selected in the version mapping and captured in its row lineage. If the
    # user deliberately omitted every entity key, don't hide all records as
    # "unavailable"; there was no identity selected for these rows to lock.
    try:
        snapshot_id = UUID(str(metadata.get("structure_snapshot_id")))
        config_id = UUID(str(metadata.get("lock_configuration_id")))
        connection_id = UUID(str(source_connection_id))
        configuration = db.get(LockPropagationConfiguration, config_id)
    except (TypeError, ValueError):
        configuration = None
        snapshot_id = None
    if (configuration is not None and configuration.status == "approved"
            and configuration.connection_id == connection_id
            and configuration.structure_snapshot_id == snapshot_id):
        mapped_objects = {
            str(field.get("entity") or "")
            for field in (getattr(version, "schema_scope", None) or [])
            if field.get("entity")
        }
        has_selected_key = any(
            anchor.get("key_fields")
            and str(anchor.get("object_name") or "") in mapped_objects
            for anchor in (configuration.configuration or {}).get("anchors", [])
        )
        if not has_selected_key:
            return set()
    record_ordinals = set(ordinals) if ordinals is not None else set(db.scalars(
        select(DataSetRecord.ordinal).where(DataSetRecord.version_id == version.id)
    ).all())
    known_ordinals = set(db.scalars(select(DatasetRecordEntityLineage.ordinal).where(
        DatasetRecordEntityLineage.version_id == version.id,
        DatasetRecordEntityLineage.source_namespace == namespace,
    ).distinct()).all())
    return record_ordinals - known_ordinals


def protected_ordinals(db: Session, version_id: UUID, now: datetime) -> set[int]:
    """Return rows matching any live entity claim in the same workspace.

    There is deliberately no dataset or actor filter on the claim side: an
    entity locked through one dataset must be visible as protected anywhere
    else that same configured source identity appears.
    """
    namespaces = db.scalars(select(DatasetRecordEntityLineage.source_namespace).where(
        DatasetRecordEntityLineage.version_id == version_id,
    ).distinct()).all()
    for namespace in sorted(namespaces):
        lock_source_namespace(db, namespace)
    enabled = enabled_source_namespaces(db, set(namespaces))
    if not enabled:
        return set()
    for namespace in sorted(enabled):
        _expire_namespace_claims(db, namespace, now)
    return set(db.scalars(select(DatasetRecordEntityLineage.ordinal).distinct().join(
        EntityClaim,
        (EntityClaim.source_namespace == DatasetRecordEntityLineage.source_namespace)
        & (EntityClaim.source_object == DatasetRecordEntityLineage.source_object)
        & (EntityClaim.token_version == DatasetRecordEntityLineage.token_version)
        & (EntityClaim.entity_token == DatasetRecordEntityLineage.entity_token)
        & (EntityClaim.workspace_id == DatasetRecordEntityLineage.workspace_id),
    ).join(
        DataSetLock,
        (DataSetLock.id == EntityClaim.lock_id)
        & (DataSetLock.workspace_id == EntityClaim.workspace_id)
        & (DataSetLock.dataset_id == EntityClaim.dataset_id)
        & (DataSetLock.version_id == EntityClaim.version_id)
        & (DataSetLock.fencing_token == EntityClaim.fencing_token)
        & (DataSetLock.status == "active")
        & (DataSetLock.expires_at > now),
    ).where(
        DatasetRecordEntityLineage.version_id == version_id,
        EntityClaim.source_namespace.in_(enabled),
        EntityClaim.status == "active", EntityClaim.expires_at > now,
    )).all())
def filter_dataset_records(db: Session, version, actor_id: str, rows: list[dict] | None = None) -> tuple[list[dict], int, int]:
    """Return allowed rows and safe counts without exposing protected tokens."""
    if rows is None:
        records = db.scalars(select(DataSetRecord).where(DataSetRecord.version_id == version.id).order_by(DataSetRecord.ordinal)).all()
        ordinals = [record.ordinal for record in records]
        values = [record.values for record in records]
    else:
        values = rows
        ordinals = list(range(len(rows)))
    blocked = conflicting_ordinals(db, version.id, actor_id, datetime.now(UTC))
    blocked.update(identity_unavailable_ordinals(db, version, ordinals))
    allowed_values = [value for ordinal, value in zip(ordinals, values) if ordinal not in blocked]
    return allowed_values, len(allowed_values), len(values) - len(allowed_values)


def require_dataset_derivation_access(db: Session, version, actor_id: str) -> None:
    if conflicting_ordinals(db, version.id, actor_id, datetime.now(UTC)):
        raise HTTPException(status_code=409, detail="Some records are temporarily unavailable under another active lock. Refresh after the lease expires or is released, or contact your workspace Admin or Test Data Engineer.")


def require_active_lock(db: Session, lock_id: UUID, fencing_token: int, actor_id: str) -> DataSetLock:
    now = datetime.now(UTC)
    lock = db.scalar(select(DataSetLock).where(DataSetLock.id == lock_id).with_for_update())
    if lock is None or lock.actor_id != actor_id:
        raise HTTPException(status_code=409, detail="Lock lease or fencing token is stale")
    ensure_claims_current(db, lock, fencing_token, now)
    return lock


def assert_job_current(db: Session, job_id: UUID) -> None:
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None or job.status != "running":
        raise HTTPException(status_code=409, detail="Background work is no longer current")


def ensure_claims_current(db: Session, lock: DataSetLock, token: int, now: datetime) -> None:
    expire_entity_claims(db, lock.id, now)
    if lock.status != "active" or lock.expires_at <= now or lock.fencing_token != token:
        raise HTTPException(status_code=409, detail="Lock lease or fencing token is stale")
    namespaces = set(db.scalars(select(EntityClaim.source_namespace).where(
        EntityClaim.lock_id == lock.id,
    ).distinct()).all())
    enabled = enabled_source_namespaces(db, namespaces)
    if not enabled:
        return
    stale = db.scalar(select(EntityClaim.id).where(
        EntityClaim.lock_id == lock.id, EntityClaim.source_namespace.in_(enabled), EntityClaim.status != "active",
    ).limit(1))
    if stale:
        raise HTTPException(status_code=409, detail="Entity claim is no longer active")
