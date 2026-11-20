# TIGR Record-Level Locking — Phase 1 rollback point

This directory is the restorable pre-implementation snapshot for Phase 1. It was captured before editing Phase 1 application files because the project files are untracked in the parent Git repository and Git revert is not a reliable recovery path.

At snapshot capture, the local TIGR database was at Alembic revision `0023_dataset_reference_codes`. Phase 1 migration `0024_lock_relationship_catalog` has since been applied; downgrade to `0023_dataset_reference_codes` to remove only the Phase 1 catalog/configuration tables after reverting the application code.

The snapshot preserves these existing files:

- `backend/tigr/connection_testing.py`
- `backend/tigr/connection_models.py`
- `backend/tigr/connection_schemas.py`
- `backend/tigr/connections.py`
- `backend/tigr/schema_records.py`
- `backend/tigr/datasets.py`
- `backend/tigr/dataset_services.py` (snapshotted before adding structure/configuration references to new data-set version provenance)
- `backend/tigr/dataset_api.py` (snapshotted before preserving pinned lock metadata when a version is copied without regeneration)
- `backend/tigr/config.py`
- `frontend/src/ConnectionsPage.tsx`
- `frontend/src/api.ts`
- `backend/migrations/versions/0023_dataset_reference_codes.py`
- `Docs/2-TIGR_Record_Level_Locking_Change_Requirements.md`

Phase 1 changes to any other existing file must be added to this manifest and snapshotted before that file is edited. New Phase 1 files are rolled back by removing those files and downgrading the Phase 1 migration after confirming the application no longer references it.

To restore an individual baseline file, copy the corresponding path from this directory over the project path. For a complete application rollback, follow Section 15 of the requirements document: disable the feature, stop/drain workers, revert all implementation files, then downgrade only feature-specific migrations. Do not remove data-set records, generated artifacts, reservations, or version locks.
