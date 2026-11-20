# TIGR MVP1 Phase 4 Implementation Report

## Scope

Phase 4 delivers the shared data-set catalog, immutable version manifests, lifecycle gates, and versioned lineage foundation required by both creation workflows.

## Delivered

- Added migration `0006_datasets_lineage` with workspace-owned data sets, immutable version manifests, ordered lineage events, parent/child edges, constraints, and database triggers that reject edits to version manifests and lineage records.
- Added API services and routes to create a draft data set, create a new version, append lineage, record validation and privacy results, publish a version, expire an eligible version, and retrieve catalog/detail/lineage views.
- A data-set creation pathway is fixed at creation. New versions carry schema and selected field scope, provenance, permitted use, and optional expiry.
- Publishing is gated on a ready lifecycle state, passed validation, compliant privacy state, and an unexpired version. Only a service identity scoped to the workspace can record validation results. Admins and Test Data Engineers manage drafts and lineage; only Test Data Engineers can publish.
- QA visibility requires both a published version and an active object or workspace grant with `data:read`. Catalog and dashboard counts apply this authorization before exposing records.
- Added the Data sets catalog and detail modal with Overview, Lineage, Validation, and Access tabs, version selection, lifecycle filters, and an ordered lineage timeline. The Lineage action opens the detail modal on the Lineage tab.
- Updated Dashboard data-set metrics to reflect role and grant visibility.
- PostgreSQL remains authoritative for lineage. The schema records ordered lineage edges for later traversal projection; no Neo4j projection is configured in this phase.

## Verification

- Local TIGR database migration applied through `0006_datasets_lineage`.
- Backend suite: 13 passed. Phase 4 coverage exercises the publish gate, service-only validation, version creation and parent ordering, missing lineage parents, QA grants, immutable records, and expiry.
- Backend lint: passed. Frontend TypeScript check and production build: passed.
- API readiness check: ready; PostgreSQL ready; graph projection not configured.

## Integration boundary

This phase establishes the common catalog and lifecycle. Scenario generation, source-to-data extraction, privacy discovery and masking, artifact storage, reservations, and target provisioning remain in their respective later phases. A validation service must be configured with the `dataset:validate` scope before a data set can become ready; the current development foundation does not run a validation worker.
