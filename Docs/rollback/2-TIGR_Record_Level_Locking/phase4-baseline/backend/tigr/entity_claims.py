"""Transactional helpers for cross-dataset protected entity claims."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from tigr.governance import DataSetLock, EntityClaim
from tigr.schema_records import DatasetRecordEntityLineage


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


def create_entity_claims(db: Session, dataset, version, lock: DataSetLock, actor_id: str, now: datetime) -> int:
    keys = lineage_claim_keys(db, version.id)
    if not keys and (version.provenance or {}).get("origin") == "source_backed":
        metadata = (version.provenance or {}).get("lock_metadata") or {}
        if metadata.get("configuration_status") == "approved":
            raise HTTPException(status_code=409, detail="Record identity is unavailable for this version; regenerate it before locking")
    db.add_all([EntityClaim(
        id=uuid4(), workspace_id=dataset.workspace_id, dataset_id=dataset.id, version_id=version.id,
        lock_id=lock.id, actor_id=actor_id, source_namespace=item["source_namespace"],
        source_object=item["source_object"], key_definition=item["key_definition"],
        token_version=item["token_version"], entity_token=item["entity_token"],
        fencing_token=lock.fencing_token, acquired_at=now, expires_at=lock.expires_at, status="active",
    ) for item in keys])
    return len(keys)


def expire_entity_claims(db: Session, lock_id: UUID, now: datetime) -> None:
    db.execute(EntityClaim.__table__.update().where(
        EntityClaim.lock_id == lock_id, EntityClaim.status == "active", EntityClaim.expires_at <= now,
    ).values(status="expired", released_at=now, release_reason="lease_expired"))


def close_entity_claims(db: Session, lock_id: UUID, status: str, now: datetime, reason: str | None = None) -> None:
    db.execute(EntityClaim.__table__.update().where(
        EntityClaim.lock_id == lock_id, EntityClaim.status == "active",
    ).values(status=status, released_at=now, release_reason=reason))


def conflicting_ordinals(db: Session, version_id: UUID, actor_id: str, now: datetime) -> set[int]:
    return set(db.scalars(select(DatasetRecordEntityLineage.ordinal).join(
        EntityClaim,
        (EntityClaim.source_namespace == DatasetRecordEntityLineage.source_namespace)
        & (EntityClaim.source_object == DatasetRecordEntityLineage.source_object)
        & (EntityClaim.token_version == DatasetRecordEntityLineage.token_version)
        & (EntityClaim.entity_token == DatasetRecordEntityLineage.entity_token),
    ).where(
        DatasetRecordEntityLineage.version_id == version_id,
        EntityClaim.status == "active", EntityClaim.expires_at > now,
        EntityClaim.actor_id != actor_id,
    )).all())


def assert_source_namespace_available(db: Session, connection_id: UUID, actor_id: str) -> None:
    """Fail closed before opaque source queries when claim predicates cannot be composed safely."""
    namespace = f"connection:{connection_id}"
    conflict = db.scalar(select(EntityClaim.id).where(
        EntityClaim.source_namespace == namespace, EntityClaim.status == "active",
        EntityClaim.expires_at > datetime.now(UTC), EntityClaim.actor_id != actor_id,
    ).limit(1))
    if conflict:
        raise HTTPException(status_code=409, detail="Source records are temporarily unavailable under an active entity lock")


def ensure_claims_current(db: Session, lock: DataSetLock, token: int, now: datetime) -> None:
    expire_entity_claims(db, lock.id, now)
    if lock.status != "active" or lock.expires_at <= now or lock.fencing_token != token:
        raise HTTPException(status_code=409, detail="Lock lease or fencing token is stale")
    stale = db.scalar(select(EntityClaim.id).where(
        EntityClaim.lock_id == lock.id, EntityClaim.status != "active",
    ).limit(1))
    if stale:
        raise HTTPException(status_code=409, detail="Entity claim is no longer active")
