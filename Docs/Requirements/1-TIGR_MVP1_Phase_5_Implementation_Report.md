# TIGR MVP1 Phase 5 Implementation Report

## Scope

Phase 5 adds the four-step Scenario to Data workflow on top of the shared Phase 4 data-set and lineage model.

## Delivered

- Added migration `0007_scenario_to_data` and the durable `dataops.scenario_plans` record for requirement text, expected states, exclusions, intended use, volume, mapping, typed query plan, source/mode selection, job, and resulting data set.
- Added clarifying prompts for missing expected outcomes and intended use, plus vague requirement terms. Scenario plans can be saved, listed, reopened, and edited before generation.
- Added workspace-scoped source metadata selection from enabled connections. The local provider-neutral assistant boundary currently uses a metadata-only rule adapter and returns suggested field matches; it receives no source rows.
- Added editable field mappings and parent/foreign-key relationship declarations. Query filters use an allowlisted operator set and typed bound values. Relational query compilation quotes only identifiers from the approved connection structure and always emits a bounded `SELECT`; it does not execute user-supplied SQL.
- Added query preview with non-exact estimated counts and a hard 10,000-row cap. PostgreSQL extraction runs read-only with a five-second statement timeout; file inputs remain capped at 5 MiB.
- Added synthetic and source-backed generation jobs, schema/key validation with up to two bounded repair passes, data-set creation before the job runs, lineage and audit metadata, artifact hashes, and authorized artifact downloads.
- Source-backed generation requires explicit confirmation and leaves its data-set version in draft with privacy state `requires_masking`. Synthetic output that passes schema checks becomes ready for Test Data Engineer publication review. Invalid output is quarantined.
- Added the four-step React workflow with Back/Continue, Save draft, resume, editable mappings and relationships, query preview, source confirmation, job status, artifact download, and data-set catalog navigation.

## Verification

- Local TIGR migration is at `0007_scenario_to_data`.
- Backend lint passed.
- Frontend TypeScript check and production build passed.
- API readiness returned `ready` with PostgreSQL ready.
- The backend test suite was not run during this implementation turn.

## Integration boundaries

- No approved external model provider is configured. The provider-neutral interface currently uses deterministic metadata suggestions and a local synthetic generator, not an external LLM.
- Local development jobs execute through FastAPI background tasks. A separate production worker must consume the durable job/outbox records before shared deployment.
- Artifacts use local owner-only file permissions under `TIGR_ARTIFACT_ROOT`; an encrypted object store is not configured. Source-backed outputs remain unpublished until masking and privacy review are implemented in the later privacy phase.
- Relationship validation supports declared parent-key coverage within the generated output. Cross-table joins, richer business constraints, and scenario-specific natural-language rule validation need the approved mapping/query and validation services from later integration work.
