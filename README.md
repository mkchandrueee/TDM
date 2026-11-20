# TIGR

TIGR is a test data generation and management platform. The application uses the `TIGR` database on LocalDBServer. The extracted sample source data remains in the separate `TIGR_SOURCE` database.

## Local development

Requirements: Python 3.13, Node.js 20.19+ (or 22.12+), PostgreSQL with the pgvector extension, and access to the local TIGR database.

1. Copy `.env.example` to `.env` and adjust `TIGR_DATABASE_URL` for your PostgreSQL account. The example uses the local Unix socket and contains no password.
2. Install and run the API:

   ```sh
   python3.13 -m venv .venv
   .venv/bin/python -m pip install -e '.[dev]'
   .venv/bin/alembic upgrade head
   PYTHONPATH=backend .venv/bin/python -m uvicorn tigr.main:app --app-dir backend --reload --port 8001
   ```

3. Install and run the web app in another terminal:

   ```sh
   cd frontend
   npm install
   npm run dev -- --port 5174
   ```

Open http://localhost:5174. The API is at http://localhost:8001; interactive API documentation is at http://localhost:8001/api/v1/docs.

## Test data tickets

The **Test Data Tickets** workspace module provides QA request drafts and submission, a workspace-scoped TDE queue, assignment, clarification and blocked-time tracking, comments, dataset-version linking, share-and-close, cancellation, reopen, role-scoped reporting, CSV export, and in-product notifications. Ticket records and append-only events are stored in PostgreSQL in the `dataops` schema. Ticket ages are calculated from UTC timestamps; the UI displays them in the browser's local timezone.

Apply the new Alembic migration with the normal `alembic upgrade head` step. Ticket API routes are under `/api/v1/tickets`; reports and CSV export are under `/api/v1/reports/tickets`. Project metadata is captured as optional project ID/name fields because the current TIGR workspace model does not define a project catalog.

## Identity and access

The API supports two explicit authentication modes:

- `TIGR_AUTH_MODE=development` is a local development fixture. It is accepted only with `TIGR_ENV=development`. Send `X-Dev-Role` as `Admin`, `Test Data Engineer`, or `QA Engineer`; optionally set `X-Dev-Subject`. Never enable this mode in a shared or production environment.
- `TIGR_AUTH_MODE=oidc` validates bearer access tokens against the configured issuer, audience, JWKS URL, and allowed signing algorithms. Set `TIGR_OIDC_ISSUER`, `TIGR_OIDC_AUDIENCE`, `TIGR_OIDC_JWKS_URL`, and `TIGR_OIDC_ALGORITHMS`. OIDC configuration is required when this mode is selected.

OIDC bearer validation is implemented, but a provider and browser client have not been configured. The current web app therefore uses the development role fixture when running locally; it does not perform an OIDC redirect or sign-in flow.

## Workspace connections

The Connections page supports PostgreSQL, MySQL, JSON, CSV, and delimited-text records. PostgreSQL and MySQL are read-only source connectors; MySQL database discovery is restricted to the configured database allowlist (or the default database in its secret URL). File paths are relative to `TIGR_FILE_ROOTS` (the example uses `Docs/source_data`); paths cannot escape those configured roots, and structure previews omit row values. Database credentials remain in a secret store. Local development can resolve `env://TIGR_SECRET_*` references from the matching process environment variable; do not put the actual connection URL in the connection form or application database. Shared environments need an approved secret-manager adapter.

For a local MySQL source, install/start MySQL, create a least-privilege read-only account, and set its URL in the private `.env`, for example `TIGR_SECRET_LOCAL_MYSQL=mysql+pymysql://tigr_reader:...@127.0.0.1:3306/example_db`. Add `env://TIGR_SECRET_LOCAL_MYSQL` in Connections and enter the allowed database names. Federated plans can execute as outbox jobs across PostgreSQL/MySQL source profiles. Source projections use database-specific read-only sessions, and TIGR performs reviewed inner/left joins in worker memory with per-source row, join-row, and 32 MiB staging caps. Privacy-enabled workspaces re-read the federated source set in the privacy worker and retain only masked output. Estimates and source filters are not available yet. Federated results use the existing data-set publication and target provisioning workflow; target delivery is not part of the generation job. Connection-scoped identity lineage is captured where approved key fields are selected, but shared Entity Domains, job cancellation, and configurable execution/TTL limits remain outstanding.

Connections move through draft, tested, and enabled states. Editing returns the record to draft, and enabling requires a fresh successful test. Disabled connections remain visible for review but are not eligible for use by later workflows.

Create the initial platform administrator after OIDC is configured:

```sh
PYTHONPATH=backend .venv/bin/python -m tigr.cli bootstrap-admin '<OIDC-subject>'
```

Subject identifiers must match the identity provider's stable `sub` claim. Platform administrators can create workspaces and manage membership, scoped grants, and service identities. Workspace roles are Admin, Test Data Engineer, and QA Engineer. Service identities are separate technical principals with explicit scopes.

Implemented endpoints include `/api/v1/me`, `/api/v1/workspaces`, workspace memberships and grants, `/api/v1/activities`, `/api/v1/dashboard`, workspace connection management, and the data-set catalog and lifecycle. Activity visibility is filtered in the API before search, pagination, and counts. Reservation and lock metrics remain unavailable until those workflow phases exist.

## Data sets and lineage

The Data sets page provides the shared catalog and version detail view. Admins and Test Data Engineers can create drafts, add versions, and record lineage; only Test Data Engineers can publish eligible versions. Admins and Test Data Engineers can expire versions. Version manifests and lineage events are immutable in PostgreSQL; lifecycle state changes are handled by the API. Publishing requires validation to pass and privacy to be compliant. Validation results can only be recorded by a workspace-scoped service identity with the `dataset:validate` scope, so a human editor cannot self-certify a draft. QA Engineers see only published versions covered by an active `data:read` grant.

Lineage is authoritative in PostgreSQL and is exposed as an ordered timeline with parent and child links. A Neo4j projection is not configured yet. The catalog and lifecycle are shared by Scenario to Data and Source to Data.

## Scenario to Data

The Scenario to Data page at `/scenario-to-data` implements the four steps: capture a requirement and expected states, review schema mappings and key relationships, compile a typed bounded query plan, then generate a data-set version. Scenario plans can be saved and resumed. Source-backed generation requires an enabled workspace source, uses only mapped fields and bound predicates, applies a 10,000-row cap and a five-second PostgreSQL statement timeout, and requires explicit confirmation before extraction. Generated records are stored in PostgreSQL in `dataops.dataset_records`; authorized downloads are streamed after release and access checks.

The configured assistant is a local metadata-only rule adapter. It suggests field matches from requirement words and approved schema names; no external model endpoint is configured, and no source records are sent to a model. Synthetic rows use a deterministic local generator and schema validation. Source-backed outputs remain in draft with `requires_masking` privacy status, so they cannot be published before the masking workflow. Scenario, source-to-data, and masking requests are persisted in PostgreSQL's outbox. Local development uses the durable worker by default. Start it in a separate terminal alongside the API:

```sh
PYTHONPATH=backend .venv/bin/python -m tigr.outbox_worker
```

For isolated development only, `TIGR_INLINE_JOBS=true` runs work in the API process. The durable worker uses expiring PostgreSQL claims and heartbeats, exposes job heartbeat timestamps, marks abandoned work terminal after a worker interruption, and rechecks workspace authorization and connection or policy state before dispatch. Masking job steps report safe elapsed times; source values are never included. Generated artifacts still use local storage; an encrypted object-store adapter is not configured.

## Source to Data

The Source to Data page at `/source-to-data` selects enabled workspace sources and immutable saved mapping versions, lets a Test Data Engineer review required fields and relationships, and accepts typed selection criteria, a bounded volume, an output format, and an optional writable target for preflight. Its preview is metadata-only; the API requires explicit confirmation before source extraction. Generation reuses the read-only parameterized source readers, creates the shared draft data-set/version/job, records mapping and source lineage, validates field and relationship coverage, and leaves source-backed output in `requires_masking` state. JSON and CSV artifacts are available to authorized engineers after validation. Physical target writes remain for the provisioning phase.

Mapping versions are stored in `dataops.source_data_mappings` by migration `0008_source_data_mapping`.

## PII discovery and masking

The PII & Masking route sends each output field name and its available schema metadata to the configured local Ollama model for sensitive/non-sensitive classification, confidence scoring, and a suggested masking transformation. It does not send dataset record values. All fields appear in the findings queue, and a Test Data Engineer reviews the classifications before masking. The scan uses the model selected by `TIGR_OLLAMA_MODEL` (or the latest installed supported model when set to `auto`). Supported transformations include stable HMAC tokenization, substitution, redaction, partial redaction, date shifting, numeric transformation, and generalization. Tokenization requires `TIGR_MASKING_SECRET`. A failed run quarantines the version and removes its unmasked artifact. Successful runs record masking lineage and set the version ready; only a Test Data Engineer can publish it.

## Reservations and locks

The Reservations & Locks route separates planned reservation windows from active exclusive lock leases. Reservation overlap is checked under a data-set-version transaction lock. Lock acquisition uses a monotonically increasing fencing token, and workers can validate that token before writing. Admin force release requires a reason and is audited. QA visibility is limited to authorized published versions and the QA user's own reservation/lock activity. TIGR enforces exclusivity only for work performed through TIGR.

Migrations through `0013_schema_design_records` are applied to the local TIGR database. The [schema implementation map](docs/1-TIGR_MVP1_Backend_Schema_Implementation_Map.md) documents how the baseline entities map to current tables and APIs.

## Provisioning and reports

The Data sets detail view can provision a published version to an enabled writable target and list its delivery runs. PostgreSQL deliveries create a unique `tigr_<run-id>` table in the configured schema; JSON and CSV deliveries create a run-specific file adjacent to the configured target path under `TIGR_FILE_ROOTS`. Cleanup and version expiry remove those owned target objects. PostgreSQL writes are transactional and compare inserted counts; file writes use a temporary file and atomic rename. Deliveries are capped at 10,000 rows and 5 MiB.

Authorized users can create five-minute CSV exports for catalog, connection, provisioning, activity, reservation, lock, masking, and job summaries. Spreadsheet formula-leading cells are neutralized; reports contain status and metadata only, never artifact row values. Export creation and download are audited. Expired files are removed on the next export request or when an expired link is requested.

The API routes are `POST /api/v1/datasets/{dataset_id}/versions/{version_number}/provision`, `GET /api/v1/datasets/{dataset_id}/provisioning-runs`, `DELETE /api/v1/provisioning-runs/{run_id}`, `POST /api/v1/workspaces/{workspace_id}/exports`, and `GET /api/v1/exports/{export_id}`. Provisioning currently runs in the request process; the outbox worker covers generation and masking jobs. Automatic background expiry sweeping remains operational follow-up work.

## Foundation interfaces

- API prefix: `/api/v1`; errors use `code`, `message`, `field_details`, `request_id`, and `retryable`.
- Correlation IDs are accepted or generated for every request and returned as `X-Request-ID`.
- `GET /api/v1/health/live` checks process liveness. `GET /api/v1/health/ready` checks PostgreSQL and reports Neo4j as not configured until an adapter is selected.
- Audit records are append-only. Store safe metadata only; do not put raw customer data, credentials, PII, model prompts, or generated SQL in logs, summaries, or outbox payloads.
- Secret references are an integration boundary. Local PostgreSQL and MySQL source connections can resolve `env://TIGR_SECRET_*`; no shared secret manager or Neo4j credentials are configured in this local foundation.

## Checks

- Frontend build and type check: `cd frontend && npm run build`
- Backend lint: `.venv/bin/ruff check backend`
- Backend tests: `.venv/bin/pytest backend/tests -q`
- Database migration status: `.venv/bin/alembic current`
