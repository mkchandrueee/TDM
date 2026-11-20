# TIGR MVP1 Phase 6 Implementation Report

## Scope delivered

Phase 6 adds the Source to Data creation pathway and keeps its outputs in the shared TIGR data-set, version, job, audit, and lineage model.

- Added immutable, workspace-scoped source mapping versions in `dataops.source_data_mappings`.
- Added authorized endpoints to list enabled readable source metadata, list and create mappings, queue generation, read job progress, and download validated artifacts.
- Added a four-step Source to Data screen for source selection, field and relationship mapping, criteria/volume/format/target selection, and confirmation plus validation status.
- Source reads reuse the bounded Phase 5 source query and file readers. PostgreSQL reads are read-only and parameterized; extraction is capped at 10,000 rows. The pre-generation preview contains metadata only.
- Generation creates a `source_to_data` data set and initial version, records source and mapping provenance, stores selected fields and validation evidence, and appends lineage and audit events.
- Source-backed outputs remain unpublished drafts with `requires_masking`; QA publication remains unavailable until later privacy gates.

## Migration

Revision `0008_source_data_mapping` adds the mapping version table and workspace/name/version index. It has been applied to the local TIGR database.

## Current boundaries

- Target selection performs an enabled/writable connection preflight. Physical target provisioning and post-write reconciliation remain in the later provisioning phase.
- Masking and publication are not performed in Phase 6. The workflow labels the resulting source data as requiring masking.
- There is no external worker or object store in the current local MVP; jobs use the existing in-process background task and local protected artifact directory.
- The backend test suite was not run. Ruff lint, the frontend production build, and an API module import check passed.
