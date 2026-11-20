from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from tigr.access import record_audit, workspace_role
from tigr.dataset_schemas import LineageEventCreate
from tigr.dataset_services import append_lineage_event
from tigr.datasets import DataSet, DataSetVersion
from tigr.governance import DataSetLock, DataSetReservation
from tigr.identity import HumanRole, Principal


def expire_dataset_allocations(
    db: Session,
    version_ids: list[UUID] | set[UUID],
    principal: Principal,
    request_id: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Close reservations, locks, and entity claims when versions are sunset."""
    ids = list(set(version_ids))
    if not ids:
        return False
    now = now or datetime.now(UTC)
    versions = {version.id: version for version in db.scalars(
        select(DataSetVersion).where(DataSetVersion.id.in_(ids))
    ).all()}
    changed = False
    reservations = db.scalars(select(DataSetReservation).where(
        DataSetReservation.version_id.in_(ids),
        DataSetReservation.status.in_(('scheduled', 'active')),
    ).with_for_update()).all()
    for reservation in reservations:
        reservation.status = 'expired'
        dataset = db.get(DataSet, reservation.dataset_id)
        role = workspace_role(db, principal, reservation.workspace_id) if principal.actor_type == 'human' else None
        record_audit(db, principal, request_id, 'reservation.expired', 'reservation', str(reservation.id),
                     reservation.workspace_id, role,
                     {'dataset_id': str(reservation.dataset_id), 'reason': 'dataset_expired'})
        version = versions.get(reservation.version_id)
        if dataset and version:
            append_lineage_event(db, dataset, version, LineageEventCreate(
                event_type='reservation_expired', stage='allocation',
                safe_evidence={'summary': 'Reservation ended because the data set version expired'}), principal)
        changed = True

    locks = db.scalars(select(DataSetLock).where(
        DataSetLock.version_id.in_(ids), DataSetLock.status == 'active',
    ).with_for_update()).all()
    for lock in locks:
        lock.status = 'expired'
        lock.released_at = now
        lock.release_reason = 'dataset_expired'
        from tigr.entity_claims import close_entity_claims
        close_entity_claims(db, lock.id, 'expired', now, 'dataset_expired')
        dataset = db.get(DataSet, lock.dataset_id)
        version = versions.get(lock.version_id)
        role = workspace_role(db, principal, lock.workspace_id) if principal.actor_type == 'human' else None
        record_audit(db, principal, request_id, 'lock.expired', 'lock', str(lock.id), lock.workspace_id, role,
                     {'dataset_id': str(lock.dataset_id), 'fencing_token': lock.fencing_token,
                      'reason': 'dataset_expired'})
        if dataset and version:
            append_lineage_event(db, dataset, version, LineageEventCreate(
                event_type='lock_expired', stage='allocation',
                safe_evidence={'summary': 'Lock released because the data set version expired'}), principal)
        changed = True
    return changed
