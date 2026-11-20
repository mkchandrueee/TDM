# Phase 4 rollback snapshot

This snapshot captures the post-Phase-3 application, migrations, and tests before Phase 4 edits. Restore the copied `backend/tigr`, `backend/migrations/versions`, and `backend/tests` trees to roll back Phase 4 code. The database was at `0026_entity_claim_registry` when captured; Phase 4 adds no migration.

Rollback order:

1. Set `TIGR_ENTITY_LOCKING_ENABLED=false` and stop/drain source, masking, scenario, and delivery workers.
2. Reject or wait for in-flight feature jobs to finish; stale jobs are guarded by the current job status.
3. Restore the copied Phase 4 source trees and remove Phase 4-only files.
4. Keep migrations 0024–0026 and their lineage/claim tables unless separately rolling back earlier phases.

Existing dataset rows, artifacts, reservations, version locks, and Phase 3 claims remain intact. Application rollback should happen only after enforcement is disabled.
