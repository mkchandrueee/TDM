# TIGR MVP1 Phase 9 Implementation Report

## Phase result

Phase 9 delivery and reporting foundations are implemented. Migration `0010_provisioning_exports` provides provisioning and export records; `0011_durable_worker_claims` adds independent, recoverable dispatch for generation and masking jobs. Target writes run synchronously in the API process, with durable run records, idempotency keys, pre/postflight evidence, safe failure summaries, lineage, and audit records.

## Delivered

- Provisioning requires an enabled writable target in the same workspace and a published, validated, privacy-compliant, unexpired exact version.
- PostgreSQL output creates a run-owned table in the configured schema inside a transaction. Identifiers are validated and values are bound parameters; post-write row counts are checked.
- JSON and CSV output uses run-specific filenames constrained to configured file roots, bounded input/output size and row count, temporary file writes, and atomic rename.
- Idempotency keys prevent duplicate work and reject key reuse for a different dataset/version/target.
- Provisioning runs are visible in dataset detail. Cleanup removes only the generated table or run-specific file. Expiring a version cleans provisioned targets and its artifact before changing lifecycle state; an external cleanup error stops expiry and retains the run for reconciliation.
- Catalog, connection, provisioning, activity, reservation, lock, masking, and job CSV reports enforce workspace roles. QA report access is limited to granted published compliant catalog rows.
- CSV reports cap rows and bytes, neutralize formula-leading cells, use opaque five-minute links, and audit creation and download. Expired artifacts are purged lazily on a new export or expired-link request.
- Dataset artifact download and QA catalog visibility reject elapsed expiry timestamps even before lifecycle state is updated.
- The Jobs route lists authorized jobs and execution steps with status filtering, search, and pagination. Job report export uses the same role-filtered inventory.
- Activity CSV export applies the same activity visibility scope as the activity page and API, including the TDE own-activity and QA visibility rules.
- The independent `tigr-worker` claims pending scenario, source-to-data, and masking events with `FOR UPDATE SKIP LOCKED`, claim leases, heartbeats, attempt limits, authorization rechecks, and terminal failure/quarantine handling.

## Main files

- `backend/tigr/delivery_api.py`: provisioning, cleanup, reports, and expiring exports.
- `backend/tigr/delivery.py`: provisioning and export records.
- `backend/migrations/versions/0010_provisioning_exports.py`: Phase 9 schema.
- `backend/migrations/versions/0011_durable_worker_claims.py` and `backend/tigr/outbox_worker.py`: durable worker claims and dispatch.
- `backend/tigr/jobs_api.py` and `frontend/src/JobsPage.tsx`: authorized job inventory and detail steps.
- `backend/tigr/dataset_api.py`: expiry cleanup and expired-version visibility/download gate.
- `frontend/src/DataSetsPage.tsx`: export action, target selection, delivery history, and cleanup action.
- `backend/tests/test_phase9_delivery.py`: formula defense, idempotency/lineage, and expired-link checks.

## Remaining delivery gates

- Provisioning currently executes in-process and is not dispatched through the worker. The worker covers scenario, source-to-data, and masking requests. Cancellation, retry backoff, and a provisioning reconciliation scheduler are not implemented.
- Automated tests exercise actual bounded JSON file provisioning, schema mismatch rejection, idempotent rerun, successful cleanup, QA grant filtering, export expiry, and formula neutralization. Live PostgreSQL target writes, target rollback, cleanup failure/retry, and expiry cleanup still need connector integration coverage.
- Report types are safe operational summaries. Protected query previews, detailed PII finding exports, policy coverage reports, and per-object activity filtering are not exposed yet.
- There is no background expiry/export janitor; cleanup runs when an export is created or an expired link is accessed.

## Verification

The full backend, lint, migration, and frontend build results are recorded in the Phase 10 readiness report.
