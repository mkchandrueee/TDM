# Phase 2 rollback snapshot

This snapshot captures the post-Phase-1 source state before Phase 2 edits. Restore copied files to roll back Phase 2 code and migration changes. The database was at Alembic revision `0024_lock_relationship_catalog` when captured. If migration 0025 has been applied, first run its downgrade to remove only the Phase 2 lineage table/indexes. Dataset rows, artifacts, and reservation/version-lock data are preserved. Any new Phase 2 files absent from this snapshot should be removed when rolling back.

Snapshot file list is recorded by the directory tree. The requirements document is included for reference. Phase 2 adds `backend/tigr/record_identity.py`, `backend/migrations/versions/0025_protected_record_lineage.py`, and `backend/tests/test_record_identity.py`; remove those new files after restoring the snapshot and downgrading the database to 0024.
