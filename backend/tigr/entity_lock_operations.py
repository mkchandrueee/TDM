"""Safe, aggregate-only reconciliation for entity lock rollout state."""

from datetime import UTC, datetime

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from tigr.connection_models import WorkspaceConnection
from tigr.datasets import DataSetVersion
from tigr.governance import DataSetLock, EntityClaim
from tigr.schema_records import DatasetRecordEntityLineage


def reconcile_entity_locks(db: Session) -> dict[str, int]:
    now = datetime.now(UTC)
    expired = db.execute(update(EntityClaim).where(
        EntityClaim.status == "active", EntityClaim.expires_at <= now,
    ).values(status="expired", released_at=now, release_reason="lease_expired")).rowcount or 0

    stale_ids = select(EntityClaim.id).join(DataSetLock, DataSetLock.id == EntityClaim.lock_id).where(
        EntityClaim.status == "active",
        (DataSetLock.status != "active") | (DataSetLock.expires_at <= now)
        | (DataSetLock.fencing_token != EntityClaim.fencing_token),
    )
    stale = db.execute(update(EntityClaim).where(
        EntityClaim.id.in_(stale_ids), EntityClaim.status == "active",
    ).values(status="force_released", released_at=now, release_reason="reconciled_stale_lock")).rowcount or 0

    disabled_namespaces = {
        f"connection:{connection_id}"
        for connection_id in db.scalars(select(WorkspaceConnection.id).where(
            WorkspaceConnection.entity_locking_enabled.is_(False),
        )).all()
    }
    disabled = db.execute(update(EntityClaim).where(
        EntityClaim.status == "active",
        EntityClaim.source_namespace.in_(disabled_namespaces or {""}),
    ).values(status="released", released_at=now, release_reason="feature_disabled")).rowcount or 0

    missing_count = db.scalar(select(func.count()).select_from(DataSetVersion).where(
        DataSetVersion.provenance["origin"].as_string() == "source_backed",
        DataSetVersion.provenance["lock_metadata"]["configuration_status"].as_string() == "approved",
        ~exists(select(DatasetRecordEntityLineage.id).where(
            DatasetRecordEntityLineage.version_id == DataSetVersion.id,
        )),
    )) or 0
    active_count = db.scalar(select(func.count()).select_from(EntityClaim).where(
        EntityClaim.status == "active", EntityClaim.expires_at > now,
    )) or 0
    db.flush()
    return {
        "expired_claims_closed": int(expired),
        "stale_lock_claims_closed": int(stale),
        "disabled_connection_claims_closed": int(disabled),
        "active_claims_remaining": int(active_count),
        "source_versions_missing_trustworthy_lineage": int(missing_count),
    }
