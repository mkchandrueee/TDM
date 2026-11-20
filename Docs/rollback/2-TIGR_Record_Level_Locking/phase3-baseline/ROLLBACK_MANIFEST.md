# Phase 3 rollback snapshot

This snapshot captures the post-Phase-2 source state before Phase 3 edits. Restore the copied files to roll back Phase 3 code. The database was at `0025_protected_record_lineage` when captured; it is currently at `0026_entity_claim_registry`.

Rollback order:

1. Set `TIGR_ENTITY_LOCKING_ENABLED=false` and drain source/data workers.
2. Restore the copied Phase 3 application files and remove new Phase 3 files.
3. Run `.venv/bin/alembic -c alembic.ini downgrade 0025_protected_record_lineage` to remove only Phase 3 claims and indexes.

This preserves Phase 2 lineage, records, artifacts, reservations, and version locks. Phase 3 adds `backend/tigr/entity_claims.py`, `backend/migrations/versions/0026_entity_claim_registry.py`, and `backend/tests/test_entity_claims.py`; the new files should be removed after the downgrade. Phase 3 modifies `governance.py` and `reservations_api.py`, both included in the snapshot.

Phase 3 adds `backend/tigr/entity_claims.py`, `backend/migrations/versions/0026_entity_claim_registry.py`, and `backend/tests/test_entity_claims.py`; remove these new files after downgrading 0026. Phase 3 modifies `governance.py` and `reservations_api.py`, both included in the snapshot.
