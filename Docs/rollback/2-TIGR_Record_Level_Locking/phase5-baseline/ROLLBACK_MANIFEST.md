# Phase 5 rollback snapshot

This snapshot captures the post-Phase-4 application and UI before Phase 5 edits. Restore `backend/tigr`, `backend/tests`, `backend/migrations`, and `frontend/src` from the corresponding copied directories to roll back Phase 5. No Phase 5 migration was added.

Phase 5 changes the lock relationship panel in `frontend/src/ConnectionsPage.tsx`, adds safe blocked-row counts to data-set detail responses and views, and adds the privileged lock impact view to the connection lock-schema response. Restore the copied source trees to remove those experience and operator-view changes. Phase 3 and 4 claim tables and enforcement remain in place.

Rollback order:

1. Hide the entity-lock connection panel or restore the Phase 5 UI snapshot.
2. Keep Phase 3 and 4 enforcement, claims, reservations, and version locks active; this phase adds no feature schema.
3. Restore the copied application trees. Existing reservation and version-lock endpoints remain from the Phase 4 baseline.

The snapshot also includes the requirement document used for this phase. It contains no source row values, identity tokens, or secrets.
