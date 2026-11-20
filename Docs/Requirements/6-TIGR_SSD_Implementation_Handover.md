# Self Servicing Data implementation handover

Date: 2 November 2026

SSD adds the QA route `/self-servicing-data`, Admin connection-operation grants, encrypted drafts and immutable query revisions, background execution/sampling/generation, and explicit owner publication. It uses the existing PostgreSQL outbox, Jobs, dataset/version/lineage/artifact services, masking transformations, reservations, entity-claim controls, and View Data.

## Storage and compatibility

The inspected branch **materializes dataset records in PostgreSQL**. SSD preserves this model. Successful reads temporarily retain an encrypted bounded result; browser samples contain at most five protected records. Generation materializes the requested bounded record count through the standard record/artifact lifecycle. It does not introduce metadata-only storage or replay SQL during download/provisioning.

Legacy datasets retain nullable visibility and their previous access semantics. Migration 0042 does not grant connection operations, disable privacy, change existing dataset visibility, or delete existing data. Legacy SQL validators remain separate and unchanged.

## Deployment

1. Install the updated Python dependencies, including `sqlglot>=28,<29`.
2. Apply `python -m alembic upgrade head` before restarting the API and outbox worker.
3. Build the frontend with `npm run build` from `frontend`.
4. Keep the existing `TIGR_CREDENTIAL_ENCRYPTION_KEY` configured and stable. SSD encrypts query inputs and temporary outputs with that key.
5. In Access & Roles, grant a QA member Select, Insert / Update, and/or Delete Records on an explicitly selected connection. Insert / Update stores two independent capabilities; writes do not grant Select.

Initial implementation verification used newly created disposable databases. On 2 November 2026, the local TIGR application database was upgraded from `0041_oracle_mssql` to `0042_self_servicing_data` and the API and idle outbox worker were restarted to resolve the old API rejecting `scope_type: connection`. The running OpenAPI schema and database constraint now include `connection`; API readiness and the frontend proxy returned healthy responses. Source databases and user grants were not changed by this deployment fix.

| Setting | Default | Meaning |
| --- | --- | --- |
| `TIGR_SSD_ENABLED` | `true` | Disables new submissions and worker execution when false; retains datasets and historical evidence. |
| `TIGR_SSD_TEMPORARY_TTL_MINUTES` | `60` | Temporary encrypted output retention, configurable from 1 to 1,440 minutes. |
| `TIGR_SSD_MAX_ROWS` | `1000` | Bounded reads and mandatory affected-row limit; configurable up to the existing 10,000-record cap. |

Source execution has a five-second connect timeout, ten-second statement timeout, two-second lock timeout, and five-MiB result limit. Admin-granted operations also require compatible connection direction/scope and actual database privileges.

## Supported SQL and connector capabilities

| Capability | Behavior |
| --- | --- |
| PostgreSQL | Supported through configured database credentials and discovered objects. |
| SELECT | Schema-qualified tables, joins, unions, subqueries and read-only CTEs; server-side streaming and bounded results. |
| INSERT | VALUES and authorized INSERT…SELECT; one directly targeted table. |
| UPDATE / DELETE | One directly targeted table, including supported scalar subqueries and predicates. Multi-table UPDATE FROM / DELETE USING are rejected. |
| RETURNING | Requires Select access. Returned values are discarded; use a separate validated SELECT revision to form a dataset. |
| Functions | A conservative allowlist of aggregates, text/numeric functions, casts and EXISTS. User-defined functions/types and schema-qualified function calls are rejected. |
| Unsupported | DDL, scripts, procedures, MERGE/upsert, writable CTEs, locking SELECTs, custom operators, views, foreign tables and rewrite-rule tables. |
| Other connectors / federation | Not exposed by this SSD release. Existing non-SSD capabilities remain available through their original workflows. |
| Entity-lock-enabled connections | SELECT remains available when another user has an active source-entity claim. INSERT, UPDATE and DELETE use the existing source-namespace guard and are blocked when another user holds a claim on the connection, because arbitrary SQL cannot safely identify the affected entities before execution. Generated datasets fingerprint selected approved entity keys with the existing HMAC engine and store normal row lineage; generation explains when a required key is missing. Granted connections with an unavailable lock configuration remain visible with an explanation; the optional `include_unavailable=true` response exposes no database/schema discovery. |

Database triggers, cascades, sequences and constraints retain their database-defined behavior. The affected-row bound applies to the directly targeted statement; it does not count independent trigger/cascade effects. Use restricted test-data credentials and understand those effects when configuring writable connections.

## Authorization, privacy and execution evidence

Active QA membership and explicit connection operations are rechecked at validation, submission, worker execution, result retrieval, generation and owner publication. Workers pin connection configuration and grant fingerprints. Invalid or incomplete SQL can be saved but cannot be validated or executed. SQL edits create new immutable revisions; later generation creates a standard new dataset version and preserves prior published versions.

Private datasets are owner-only through request-scoped inventory, count, related-record and job filters, plus direct dataset access checks. Public means eligible users in the same workspace. Existing public-consumer reservation and lock requirements remain. Owners can view/download their own eligible published SSD dataset without a new approval request, while record/entity protection filters remain active. API responses prohibit caching; open previews and View Data periodically reauthorize and recheck on focus.

Only TDE can configure dataset privacy and masking, including policy approval. Enabled workspaces apply approved TDE rules with the existing masking engine; uncovered fields use the existing metadata-only classifier. Source-column metadata carries known TDE rules through SQL aliases; expressions combining incompatible source policies are rejected. Sensitive fields without a compatible approved rule, unavailable classification, invalid output, and masking failures block exposure. Real findings and successful automatic masking runs are recorded on generated versions. Disabled workspaces explicitly record the skip and do not invent masking runs. Enabling privacy withholds stale unprotected output and publication until regenerated under current policy.

Mutation dispatch is durably recorded before the source statement. Duplicate submissions for a mutation revision reuse its existing execution. Sampling, regeneration, generation, publication and consumption never execute mutation SQL. Successful commits are persisted before downstream work. Rollback is reported only when confirmed; interrupted or uncertain commits remain `outcome_unknown` and are never automatically retried. A new mutation revision after an uncertain outcome requires explicit acknowledgment and a source-investigation evidence reference, with a separate audit event. Such acknowledgment does not relabel the original unknown outcome as proven.

Cancellation is cooperative: it is checked before dispatch and before commit, together with current authorization. A transaction already committed is reported as committed. Application rollback cannot undo source mutations. Disable new SSD work before rolling back application processes; migration downgrade intentionally refuses to drop execution history.

## Changed integration points

- Backend additions: `ssd.py`, `ssd_sql.py`, `ssd_models.py`, `ssd_access.py`.
- Schema: `0042_self_servicing_data.py`; expanded existing connection grant scope, nullable dataset visibility, SSD draft/revision/execution metadata, immutable-input trigger and indexes.
- Shared backend integration: access schemas/grant validation, dataset models/schemas/API/services, TDE privacy authority, outbox dispatch/cleanup, delivery checks, API error diagnostics and cache controls, configuration and migration model registration.
- Frontend: `SelfServicingDataPage.tsx`, `SsdGrantForm.tsx`, `ssd.css`, QA navigation, dataset origin/visibility labels, and View Data reauthorization.
- Tests: `test_ssd.py`, `test_ssd_sql.py`, `test_ssd_safety.py`, and the disposable Windows test runner `run_disposable_ssd.py`.

## API notes

All paths use `/api/v1`. Draft actions require `expected_revision`; async actions also require `idempotency_key` and return `job_id`, `execution_id`, `revision`, `state`, and the existing job status URL. Mutations require `confirm_mutation`.

| Routes | Purpose |
| --- | --- |
| `GET /workspaces/{id}/ssd/connections` | Effective permitted PostgreSQL connections, operations, databases and schema names. |
| Existing `POST /workspaces/{id}/grants` and revoke route | `scope_type: connection`, explicit connection UUID and `ssd:select`, `ssd:insert`, `ssd:update`, `ssd:delete`. Existing expiry/revocation apply. |
| `GET/POST /workspaces/{id}/ssd/drafts` | Owner draft inventory and creation. |
| `GET/PATCH /ssd/drafts/{id}` | Resume or create a new query revision. |
| `GET /ssd/drafts/{id}/revisions` | Owner-only query and execution history. |
| `PATCH /ssd/drafts/{id}/metadata` | Name, description, visibility, maximum record count and output format. |
| `POST /ssd/drafts/{id}/validate` | Static dialect/statement/parameter/access validation without executing DML. |
| `POST /ssd/drafts/{id}/execute`, `/sample`, `/datasets` | Separate outbox-backed jobs. |
| `GET /ssd/drafts/{id}/sample` | Current authorized protected preview, schema and timestamps. |
| `POST /ssd/jobs/{id}/cancel` | Cooperative cancellation through the existing job record. |
| `PATCH /datasets/{id}/visibility` | Audited SSD-owner Private/Public changes. |
| Existing dataset detail, publish, View Data, artifact and Jobs routes | Shared lifecycle and consumption, with SSD visibility/privacy/owner checks. |

## Verification and remaining limits

The full regression run completed with **77 passed and 15 baseline failures**, including **46 passing SSD checks**. Clean-database upgrade through 0042, focused Ruff checks, Python compilation, and the TypeScript/Vite production build passed. The backend regression run preserves the same 15 failing tests recorded before SSD implementation; these involve existing lock configuration/entity claims, connector mocks, legacy publication expectations, and delivery/export interfaces. Detailed current output is in `data/ssd-test-results.log`; migration output is in `data/ssd-migration-check.log`.

SSD tests cover real disposable PostgreSQL writes, validation without writes, bounded rollback, uncertain-outcome replay suppression, cancellation, grant/configuration/revision changes, privacy blocking and masking, expiry/regeneration, direct publication, Private/Public access, immutable version creation, and outbox dispatch. No destructive verification used live source data. Automated browser interaction was not run; frontend validation comprises type/build checks and API workflow tests. Existing bundle-size and dependency deprecation warnings remain.

The SQL/connector and entity-lineage restrictions above are explicit release limitations. Automatic source-side commit reconciliation and exact entity resolution for arbitrary SQL require additional connector support; no outcome proof is fabricated.

## Connection visibility follow-up (2 November 2026)

John's active grant for `Core_Banking_Postgre` in `Customer and Accounts` was hidden because that connection has entity locking enabled. Its read-only configuration also restricts effective operations to Select. SSD now displays granted but unavailable connections with the blocking reason and refreshes connection access on focus and every 15 seconds. Revoked, expired, ungranted and cross-workspace connections remain excluded. Existing locking and read/write configuration were not changed.

Follow-up verification: 47 SSD tests passed, including locked-connection visibility and revocation coverage; frontend TypeScript/Vite build passed. The local API was restarted, and a request as John through the frontend proxy returned the granted connection with its entity-locking explanation.

## SSD locked-source and write configuration follow-up (2 November 2026)

The Connections form and detail view now support Admin/TDE selection of PostgreSQL `read_only`, `read_write`, or `write_only` operation scope. A scope-only update preserves the tested database binding and current approved key configuration. SSD applies TIGR's existing advisory source-namespace guard to INSERT, UPDATE, and DELETE while preserving SELECT access during another actor's active source lock. Generated datasets capture configured source entity keys before masking so they participate in the existing entity-claim registry. Query output intended for generated datasets must select every configured key for each referenced anchored table directly; otherwise generation stops.

TIGR connection-operation grants and PostgreSQL role permissions are separate gates. John's existing source login is `tigr_source_reader`: inspection confirmed it can read 10 source tables and has no INSERT, UPDATE, or DELETE privileges. A Read and write scope plus SSD grants will enable the UI and authorization path, but actual mutations require the Admin to supply a PostgreSQL login with those privileges. The application does not silently upgrade the read-only source role.

John's connection was changed to `read_write` through the audited Admin API. His SSD inventory now shows the connection as available with Select/Insert/Update/Delete. The current saved credentials are still `tigr_source_reader`; database inspection confirmed its read-only permissions, and no PostgreSQL data or grants were changed. The Admin must update this connection to credentials for a writer role and retest it before SSD mutations can succeed. A requested automated creation of that role and DML grants was rejected by automatic approval review because it would create a persistent login and grant DML across the connection's discovered tables/sequences without explicit authorization for that broad database privilege change.

## Entity-lock query behavior refinement (2 November 2026)

Active source-entity claims no longer block SSD SELECT statements. The shared source-namespace lock check applies only to INSERT, UPDATE, and DELETE. The QA wizard shows the entity-lock warning only for a validated mutation; SELECT output can still be used for sampling and dataset generation, with approved source keys retained in generated dataset lineage where selected.
