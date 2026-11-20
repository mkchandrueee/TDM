# Phase 6 rollback snapshot

This snapshot represents the post-Phase-5 application before Phase 6 rollout controls and reconciliation were added. Restore the copied `backend/tigr`, `backend/tests`, `backend/migrations`, and `frontend/src` trees to revert Phase 6 while retaining the Phase 5 user experience and operator impact view.

Phase 6 adds the default-off `entity_locking_enabled` source-connection flag, its API/UI control, migration `0027_entity_lock_rollout`, and `entity-lock-reconcile`. Restore the snapshot and downgrade only to `0026_entity_claim_registry` to remove the flag column. Phase 3 and 4 entity claims and enforcement remain available. Existing dataset records, artifacts, reservations, and version locks are not dropped or rewritten.

Rollback order:

1. Disable each connection flag through the API, which serializes with claim acquisition and releases active claims. For emergency-wide disable, set `TIGR_ENTITY_LOCKING_ENABLED=false` in API and worker processes.
2. Drain feature workers and run the aggregate reconciliation command. Confirm no active claims remain for disabled connections.
3. Restore the copied Phase 6 baseline trees.
4. After the restored code no longer reads the rollout column, run `.venv/bin/alembic -c alembic.ini downgrade 0026_entity_claim_registry`.

The copied requirement document records the phase scope. No source keys, tokens, credentials, or record samples are present in this snapshot.
