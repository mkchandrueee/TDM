# TIGR — Self Servicing Data (SSD)

**Document type:** Change requirements and implementation specification for Codex  
**Version:** 1.0  
**Date:** 1 November 2026  
**Target:** Existing TIGR code base  
**Primary user:** QA Engineer  
**Status:** Ready for implementation; verify repository integration points before coding.

## 1. Objective

Introduce **Self Servicing Data (SSD)** so QA Engineers can independently select workspace-authorized connections, validate and execute SQL, inspect up to five sample records, create a dataset, and publish it as Private or Public. Support SELECT, INSERT, UPDATE, and DELETE according to Admin-assigned connection privileges. Query execution and sample generation must run through TIGR's existing background Jobs capability.

QA-created SSD datasets must be available in **View Data** after publication without a TDE or Admin approval. Reuse TIGR's existing dataset lifecycle, versioning, lineage, jobs, validation, reservations, locks, provisioning, downloads, and audit capabilities. Only the explicitly described authorization and privacy-policy changes may affect shared flows.

This document requests implementation of a feature, not a replacement application. Do not create an independent SSD catalog, job scheduler, masking engine, or authorization system.

## 2. Baseline and implementation evidence

The following existing documents were reviewed for integration context:

- `TIGR_Architecture_and_Codebase_Documentation.md`, current retrieved version 1, based on the October 2026 repository snapshot.
- `TIGR_Multi_Source_Connections_Requirements_Implementation.md`, dated by its creation on 25 October 2026; this is a change specification, not proof that federation is implemented.

The architecture guide identifies React/TypeScript/Vite, FastAPI/Python, SQLAlchemy/Alembic, PostgreSQL, and a PostgreSQL outbox worker. It describes source queries, datasets, PII/masking, locks, jobs, and delivery as shared backend services. These are candidate integration points, not a verified map of the repository supplied to the implementing Codex session.

**Storage discrepancy to resolve during repository inspection:** The older code-aligned guide describes persisted dataset records. The later multi-source specification requires schema/plan metadata and one approved sample record, with extraction during provisioning. Preserve the behavior actually implemented in the target branch. SSD must not silently reverse an implemented metadata-only design or introduce a platform-wide storage migration. Under metadata-only behavior, SSD's five-row preview is temporary; persist at most the existing permitted sample count. Under existing materialized behavior, reuse the established record/artifact lifecycle and caps. Record which mode is present in the implementation handover.

The latest user requirements in this document take precedence for SSD. Existing read-only restrictions remain in Scenario to Data, Source to Data, and other legacy query paths; authorized mutations are added only through the SSD execution path.

## 3. Product decisions and boundaries

These decisions resolve details needed for implementation without adding approval steps.

| Area | Required interpretation |
| --- | --- |
| Name and navigation | Display `Self Servicing Data` in the QA sidebar; use `SSD` as the internal feature abbreviation. |
| Private | Owner-only dataset discovery/use, subject to existing explicitly authorized support/governance access. It is not visible to other ordinary QA users. |
| Public | Available to eligible users inside the same workspace/project scope. Never internet-public or automatically shared across workspaces. |
| Default visibility | Private; QA may select Public before publication or change visibility later with audit and existing ownership checks. |
| Connection grants | Admin controls QA's SELECT, INSERT/UPDATE, and DELETE privileges per workspace and connection, with binding/object restrictions where supported. |
| Database writes | INSERT, UPDATE, and DELETE execute against the explicitly selected external test-data connection. They are real writes, not synthetic generation. |
| Query scope | One SQL statement per execution. Joins, subqueries, and supported read-only CTEs are allowed. No arbitrary scripts or database administration commands. |
| Supported mutations | INSERT, UPDATE, DELETE. `etc.` does not implicitly authorize DROP, TRUNCATE, ALTER, CREATE, GRANT, stored procedure execution, or unrestricted MERGE/upsert. |
| Query execution | An explicit Execute Query action starts a job. Validation, sampling, dataset creation, publication, provisioning, and downloads must never implicitly replay mutation SQL. |
| SSD source privacy | SSD uses approved non-production connections whose data is masked before it reaches the source. SSD does not classify or additionally mask query results. |
| Workspace PII policy | Workspace masking settings and TDE policy coverage do not gate SSD query execution, previews, datasets, or publication. Other workflows retain their existing policy behavior. |
| Dataset privacy state | Record SSD's pre-masked-source assumption in provenance and require normal query validation, authorization, and dataset lifecycle checks. Do not create synthetic findings or masking-run records. |
| Publication | QA can publish their eligible SSD dataset directly. No TDE/Admin approval, data-use request, or manual status manipulation is needed for the creator to use it. |
| Other consumers | Public visibility grants discovery under workspace scope; existing consumption permissions, reservations, and locks still apply. It does not grant source SQL write privileges. |
| Scope of change | Add the QA pathway and SSD-specific pre-masked-source assumption. Preserve existing privacy behavior for all other paths. |

### 3.1 Scope exclusions

Do not add connection creation for QA, an AI SQL assistant, new federation engines, cross-source distributed writes, new ticket approvals, schema administration, a separate record-locking model, or a new deployment stack. Reuse federation for SELECT only if already implemented and exposed through the existing SQL-to-Data capability; each underlying source must be authorized. Mutations target exactly one writable database binding and one directly targeted table per statement. Triggers, cascades, and database constraints retain their database-defined behavior and must be considered in connector capability documentation.

## 4. Actors and authorization

### 4.1 Role matrix

| Action | QA Engineer | TDE | Admin |
| --- | --- | --- | --- |
| Open SSD | Yes, in an active workspace | Only if also QA or already authorized by an explicit role grant | Only if also QA or already authorized by an explicit role grant |
| Select SSD connection | Only granted connections and operations | Existing role behavior | Existing role behavior |
| Validate/execute SSD SQL | According to operation grants | No automatic new rights from this change | No automatic SQL execution rights from administration alone |
| Create/publish own SSD dataset | Yes, after automatic gates | Existing creation/publication rights | Existing administration rights |
| Set own SSD visibility | Yes | Existing ownership/management rights | Existing audited management rights |
| Enable/configure dataset PII and masking | No | Yes, within scope | Only when also authorized as TDE |
| Apply preconfigured masking through a job (non-SSD workflows) | System action on QA's behalf | System action or existing TDE action | No implied manual policy rights |
| Grant/revoke QA connection operations | No | No new grant-management right | Yes, within administered workspace |
| Read another user's Private rows | No | Only explicitly scoped privacy/support authority | Only existing explicit governance authority; admin role alone must not introduce unrestricted row access |

### 4.2 Admin connection access management

Add an SSD access section to existing workspace user/group grants or connection permissions. Prefer extending existing grants rather than a parallel ACL table.

Each grant must identify workspace, QA user or existing supported group, connection/profile, database binding if applicable, allowed operations, any existing object/field scope, enabled state, issuer, timestamps, and grant revision. Reuse existing expiry/revocation mechanisms.

The UI must show three operation groups: **Select**, **Insert / Update**, **Delete Records**. Internally store distinct `select`, `insert`, `update`, and `delete` capabilities; the Insert / Update group grants both. Do not implicitly grant SELECT with write privileges. Explain when a query requires SELECT in addition to a write privilege, such as INSERT…SELECT, UPDATE predicates reading protected fields, or RETURNING data.

Effective access is the intersection of active membership, role/action rights, connection grants, connector capabilities, configured binding/object scope, and underlying database privileges. A connection's writable service credential must not allow an unauthorized QA operation. No new QA write grants are assigned by migration.

Authorize every referenced object and operation, including subqueries, joins, source reads in INSERT…SELECT, RETURNING expressions, and any supported view lineage. Reject ambiguous constructs whose access cannot be established. Use existing database RLS/column restrictions where configured; do not widen them.

Recheck authorization at request validation, queue submission, worker start, result retrieval, dataset creation/publication, and later live extraction. Revoked access must prevent queued work from executing and must not expose stored results. On revocation during running work, request cancellation where supported; report a transaction already committed as committed, not rolled back. Before dispatch, bind the job to the selected connection configuration revision; never silently run against a newly repointed database.

## 5. QA experience and stepwise workflow

### 5.1 Entry and common controls

- Add the QA sidebar link and an authorized route, suggested `/self-servicing-data`.
- Retain the current TIGR visual design and reuse the SQL-to-Data editor, grid, stepper, job status, and dataset actions where present.
- Show active workspace/project, page title, three-step progress, draft name, and privacy status.
- Show a useful empty state when no connection is granted: `No connections are available for Self Servicing Data in this workspace.`
- Save and resume a draft without losing query versions or job references. Changing workspace must reset the displayed source/results and reauthorize the draft.
- Provide keyboard access, labeled controls, inline validation messages, loading/empty/error states, and accessible job-status updates.

### 5.2 Step 1 — Connection selection, query validation, execution

**Inputs and controls**

1. Connection selector showing only authorized connections, environment, connector, and granted operations.
2. Database/schema selector where supported by that connection's existing capability.
3. SQL editor similar to SQL-to-Data, with syntax highlighting, bound parameter inputs, and errors with line/column where available.
4. **Validate Query** and **Execute Query** buttons.
5. Query type, resolved sources/target, applicable limits, execution status, and existing Jobs link.

**Validation**

- Validate on the server; client-side hints are supplementary.
- Check SQL syntax/dialect, one-statement rule, operation permissions, object scope, connection capability, parameter types, limits, and output schema when derivable.
- Do not execute user DML during validation, including in a rollback-only transaction. Do not use EXPLAIN ANALYZE or invoke side-effecting functions for validation.
- Return a server-issued validation record tied to the canonical SQL hash, parameters hash, connection/binding, actor/workspace, configuration revision, and resolved operations.
- Any edit to SQL, parameters, connection, or binding invalidates validation and dependent samples. Revalidate before execution; stale browser validation cannot authorize a job.

**Execution**

- Execute Query submits a background job and immediately shows queued/running state; use the existing status polling or event mechanism.
- For SELECT, execute a bounded read and store only the allowed temporary result/schema and metrics. Step 2 presents an independently tracked sample job using that execution where feasible.
- For mutations, the button/action must clearly state that it changes records in the selected environment. Submit only from the explicit user action and record the execution intent. No TDE/Admin approval is introduced.
- Show statement type, affected-row count, start/end time, result availability, commit outcome, and sanitized errors. Affected-row count is not a grid of data records.
- Execution limits must not silently convert the user's query into a different mutation. Roll back when a mandatory affected-row bound is breached on a supported transactional connector.

### 5.3 Step 2 — Sample generation

- Enable **Generate Sample** after successful query execution and creation of an eligible read result/recipe.
- Submit sample generation as a background job linked to the draft, query revision, and execution job. Do not block the browser while waiting.
- Display **up to five records** in a grid with column names/types, nulls, long-value expansion, and `Showing N sample records`.
- For fewer than five records, display the available rows. For zero records, show schema when known and an empty result message; allow saving the draft but block dataset generation until a non-empty result is obtained for this release.
- SSD sources are approved non-production sources whose data is already masked before connection. Return the actual query values for the first five rows; SSD does not classify or apply additional PII masking, and workspace TDE masking policy coverage is not an execution prerequisite.
- Provide **Regenerate Sample**, **Back**, and **Next**. Regeneration may repeat a safe read; it must not repeat INSERT/UPDATE/DELETE.
- Require the sample job to succeed before advancing. The QA user verifies the grid through the stepwise flow; no TDE approval is needed.
- Record sample generation time and query revision. Live-source previews are illustrative; later provisioning can differ as source data changes.

### 5.4 Step 3 — Dataset generation, open, publish

**Inputs:** dataset name, description, existing project/environment context, visibility (Private/Public), and existing generation settings such as requested count/format where relevant to the current storage model.

**Actions:** **Generate Data Set**, **Open Data Set**, **Publish Data Set**.

1. Generate Data Set creates the standard TIGR dataset/version, attaches the validated read recipe or supported result artifact, schema, permitted preview, source/query/job lineage, owner, and selected visibility. Use the shared generation job path where already asynchronous; if generation is long-running, enqueue it through the same Jobs capability.
2. Automatic dataset validation and SSD pre-masked-source provenance checks run through existing services. QA cannot mark validation as passed or change source privacy assumptions in request payloads.
3. Open Data Set becomes available after creation and opens the existing dataset detail page. Show owner, origin `Self Servicing Data`, visibility, connection/environment, version, job links, privacy state, and lineage.
4. Publish Data Set becomes enabled when automatic gates pass. The API must allow the QA owner to perform this transition for SSD without broadening QA publication rights for unrelated TDE datasets.
5. Publication writes the normal publication/audit records and makes the dataset available in View Data to its authorized audience. No pending-approval state or TDE/Admin request is created.
6. Publication is explicit via the button; generation alone does not silently publish. Repeated publish requests are idempotent.

Once published, the creator can view/use the dataset through existing authorized operations without a new data-use approval. Private data sets are owner-only; their QA owner can view and download published versions without a reservation or dataset-wide lock. Source-entity lock protections still filter records currently held by another QA user's lock. Public consumers retain the existing approved-reservation and active-lock requirements, along with validation and current workspace policy.

## 6. SQL behavior and mutation-to-dataset semantics

### 6.1 Operation matrix

| Statement | Required privileges | Step 1 result | How a reusable dataset is formed |
| --- | --- | --- | --- |
| SELECT / supported read-only WITH | SELECT on every referenced source | Bounded rows/schema and read metrics | Save the validated read recipe; generate/provision through existing lifecycle. |
| INSERT…VALUES | INSERT; SELECT if returning/reading fields requires it | Commit outcome and affected count; RETURNING only if supported/authorized | Use authorized returned rows under existing artifact storage, or a separately validated SELECT over inserted records. |
| INSERT…SELECT | INSERT on target and SELECT on all sources | Commit outcome and affected count | Same rules as INSERT; a single writable binding, no distributed writes. |
| UPDATE | UPDATE plus all read privileges required by expressions/predicates/RETURNING | Commit outcome and affected count | Separately validated read of the intended resulting records, or a supported immutable returned-row artifact. |
| DELETE | DELETE plus applicable read/RETURNING privileges | Commit outcome and affected count | A separate read of remaining/intended records, or a supported immutable deleted-row artifact explicitly identified as a snapshot. |

**A successful mutation is not automatically a reusable dataset.** Never treat an affected-row count as customer/test-data rows. Never save DML as the query that View Data, preview, download, or provisioning will execute.

### 6.2 Follow-up result selection

When a mutation does not provide a reusable row result, keep its execution as completed and show an inline **Dataset Result Query (SELECT)** editor within Step 1. The QA user selects the intended resulting data with an independently validated, authorized SELECT. This produces the read recipe used by Steps 2–3; both mutation and read jobs remain in lineage. A write-only user can complete permitted mutations and see their sanitized status/count but cannot create a record-based dataset until they have authorized readable output.

Do not reconstruct UPDATE results using the original WHERE clause automatically: an UPDATE may change the predicate values. Capture stable permitted identities where the existing connector supports it, or require an explicit user-authored result SELECT. DELETE'd records cannot be re-extracted later; do not silently rerun DELETE or return unrelated rows to recreate them.

If the branch supports only metadata-plus-sample datasets, support DML followed by SELECT and clearly explain that a deleted-row snapshot is unavailable unless the existing approved artifact mechanism supports it. Do not invent a hidden permanent full-row store. If a supported artifact is used, record immutable artifact provenance, privacy state, expiry, and snapshot semantics. Publishing an artifact must never make its DML replayable.

### 6.3 Execution controls

- Use a dialect-aware parser and an operation allowlist. Checking the first word or a regex alone is insufficient.
- Inspect nested statements/CTEs and reject data-modifying CTEs, multiple statements, dynamic SQL, session changes, file/network export commands, and procedural calls. For SELECT/WITH, reuse SQL to Data's PostgreSQL syntax/reference validator and bounded read-only execution; database function resolution and privileges determine which functions are accepted. Do not use EXPLAIN ANALYZE or execute query functions during validation. Keep the SSD safe-function allowlist for mutation expressions.
- Scope user SQL to configured external test-data connections. Prevent access to TIGR's application database, credentials, and internal schemas through SSD.
- Bind parameter values; validate and safely quote identifiers. Raw SQL may contain literals, so redact logs/errors. Show saved SQL to the owner of a private SSD dataset and to authorized workspace viewers of a public SSD dataset; never expose saved parameter values.
- Reuse existing time/row/byte/concurrency limits and expose effective values. Where SSD needs a new mutation limit, set a configurable initial default of **1,000 directly affected rows**, enforced before commit. This is an implementation default, not a claimed existing TIGR limit.
- For this release, require a filter for UPDATE/DELETE and reject obvious all-row predicates. Do not claim a WHERE clause alone guarantees a small operation; enforce the row bound transactionally as well.
- Use only transactional connectors for mutations in the initial implementation. Keep rollback/cancel behavior honest about database triggers, cascades, sequence changes, and external effects. Document unsupported cases rather than promising universal rollback.
- Mutations must consult existing protected entity claims where enabled. Do not update/delete another user's locked source entities through SSD. If the existing engine cannot reliably resolve and protect the affected identity set, reject that mutation with an actionable reason; do not bypass locks or disable the global lock feature.
- Dataset locks protect later dataset use. SSD's new-draft SQL cannot infer every dataset lock solely from a table name; enforce source-entity protections where configured, and preserve the distinction in UI/documentation.

### 6.4 Idempotency, retries, and uncertain commits

Create a stable client request/idempotency key and a server-side execution ID. A retry, double-click, page refresh, or duplicate outbox delivery must reuse the existing execution rather than commit the mutation again. A deliberate new execution requires a new execution intent.

Persist a mutation dispatch marker before contacting the source, with a unique execution constraint and serialized worker ownership. Once dispatch may have reached the source, disable automatic mutation retries unless connector-supported source-side idempotency proves they are safe. A local outbox key alone does not provide exactly-once commits across the application and source databases.

Use separate execution outcomes: `NOT_STARTED`, `COMMITTED`, `ROLLED_BACK`, and `UNKNOWN`. If a worker crashes or disconnects around source commit and cannot establish the outcome, mark the execution `UNKNOWN` and surface `Execution outcome unknown — do not rerun until reconciled`. Keep it out of normal automatic retries. Reconcile using authoritative source evidence when available; never infer success or rollback merely from a job timeout. A manual reconciliation action must be authorized and audited.

Cancellation before commit attempts rollback; cancellation after commit cannot undo the write. A sample failure after successful DML must show the mutation as committed and the dataset as blocked/failed separately. No compensation mutation is automatically generated.

## 7. Source privacy assumptions for SSD

### 7.1 Non-production source requirement

SSD is intended for approved non-production connections whose source records have already been masked. The connection administrator is responsible for selecting and maintaining an appropriate source. SSD does not run PII discovery, consult TDE masking coverage, or apply another masking transformation. A missing workspace masking policy must not block SSD query validation, execution, five-row preview, dataset creation, publication, or access to an SSD dataset.

Show a read-only UI note that the source must already be masked and that SSD does not apply additional PII masking. Return actual query values for the five-row preview and persist the actual bounded query output. Retain the existing authorization, workspace, dataset visibility, entity-lock, validation, and audit checks. Mark SSD provenance as pre-masked source data; do not create fabricated PII findings or masking-run records. This SSD exception must not change privacy behavior for datasets created by other workflows.

### 7.2 Policy changes

Workspace PII/masking policy changes do not invalidate SSD previews or datasets because SSD does not apply that policy. Existing privacy gates and policy revision behavior remain unchanged for non-SSD workflows. Keep authorization and dataset visibility checks active for private and public SSD datasets.
- Existing workspaces keep their current policy setting on migration. Do not disable protection globally as a shortcut for SSD.

## 8. Dataset visibility and existing lifecycle integration

Store `origin = SELF_SERVICING_DATA`, `owner_user_id`, workspace/project scope, and explicit visibility on the standard dataset. Scope QA-specific edit/publish privileges to ownership and origin. All object endpoints must enforce visibility, including direct IDs, search, dashboards/counts, Jobs, artifacts, samples, lineage, notifications, download links, and exports. Private dataset titles and query text must not leak through other users' job inventories or aggregates.

Changing Public to Private removes future ordinary non-owner access immediately, including cached UI data and newly requested download links. Already downloaded files cannot be recalled. Do not silently drop active jobs or reservations: deny new unauthorized access, cancel pending unauthorized deliveries where possible, and preserve their audit/lock history using existing lifecycle rules. Changes of dataset query/schema create a new draft version and rerun relevant gates; they do not modify published query behavior in place.

Publication does not grant consumers the creator's connection credentials or SQL mutation privileges. For metadata-only live extraction, use the existing explicitly authorized dataset-service execution model if present. If absent, require the consuming user to have current SELECT access to every source. Do not execute arbitrary consumer-provided SQL under the creator's identity. Public datasets can remain discoverable while a user lacking consumption/source rights sees an access explanation under existing rules.

Preserve normal reservation, lock, job, provisioning, download, lineage, and ticket relationships. SSD creation does not require a ticket. A published dataset may be linked to an existing ticket through existing behavior without changing ticket routing/closure.

## 9. Background jobs and draft state

### 9.1 Job integration

Extend the existing Jobs/outbox dispatcher with logical job types such as `SSD_QUERY_EXECUTION`, `SSD_SAMPLE_GENERATION`, and, if needed, `SSD_DATASET_GENERATION`. Adapt names to repository conventions. Persist job and outbox creation atomically. Use existing polling/events, retry policies for safe reads, claim leases, heartbeats, progress, timestamps, cancellation, and audit.

Every job carries actor, workspace, draft ID/revision, query hash, connection/binding/configuration revision, permission revision, policy revision, idempotency key, and correlated parent job/dataset version where applicable. Store secure server-side query references rather than raw SQL/parameter values in public job summaries.

Show job steps appropriate to the operation: authorize, resolve connection, validate, execute, apply output policy, prepare sample/create dataset, validate output, cleanup. Report returned rows and affected rows separately. Do not expose staged raw records via generic job-result endpoints.

### 9.2 Draft and execution state

Suggested draft states, mapped to existing conventions where possible:

`DRAFT → VALIDATED → EXECUTING → EXECUTED → SAMPLING → SAMPLE_READY → GENERATING → READY_TO_PUBLISH → PUBLISHED`

Failures/blocks are explicit with retryable reasons; use `BLOCKED_POLICY_CONFIGURATION`, stale-input markers, or existing equivalents rather than representing every outcome as a generic error. Mutation commit state is independent of these wizard states.

Changing query inputs creates a new immutable revision, invalidates validation/sample readiness, and marks earlier outputs stale without deleting their audit history. Workers completing an old revision must not advance the new draft. Use optimistic concurrency for edits and unique constraints for dataset-generation/publication idempotency. Navigating away and back resumes the correct revision and jobs.

### 9.3 Temporary output lifecycle

Reuse existing temporary storage protection, TTL, isolation, and cleanup. Apply a configurable **60-minute default TTL** only if no equivalent policy exists. Clean up sample/raw staging on expiry, cancellation, failure, or when no longer required by downstream jobs. Protect dependencies with existing leases/references so cleanup cannot remove an active sample's input.

A five-record browser preview does not authorize persistence of five records in a metadata-only dataset. Expired SELECT samples can be regenerated through a read job. Expired mutation output must not trigger DML replay; use its saved SELECT recipe or explain that a snapshot is unavailable.

## 10. Logical data model changes

The model below is additive guidance. Extend existing entities instead of duplicating equivalent structures.

| Entity/change | Minimum fields or behavior |
| --- | --- |
| Existing connection grants | Workspace/principal/connection/binding, operation flags, scope, enabled/expiry, issuer, revision; no implicit grants. |
| SSD draft | ID, workspace/project, owner, name/description, selected sources/binding, environment, visibility, current revision/state, timestamps. |
| Immutable SSD query revision | Draft/revision, secured SQL and typed parameters, canonical hash, statement type, object/operation dependencies, output schema, validation evidence, config revision. |
| SSD execution | Execution/job IDs, query revision, actor, idempotency key, dispatch marker, mutation outcome, affected/returned counts, source evidence reference, safe errors/timestamps. |
| SSD sample reference | Draft/query revision, execution/sample job, safe output reference, row count ≤5, policy revision, status, expiry; reuse artifact metadata. |
| Dataset additions | Origin, owner, visibility and SSD draft/read-query/execution references; use current fields if already present. |
| Dataset version/lineage | Read recipe or artifact, source bindings, schema, query revision, preview, privacy evidence, mutation provenance and job links. |
| Jobs/audit additions | SSD event types, correlation/revision data, cleanup outcome, privacy skip reason; existing tables. |

Use foreign keys and workspace-consistent relationships, indexes for owner/workspace/status/visibility and job correlation, unique idempotency constraints, and optimistic concurrency/version columns. Do not cascade-delete execution evidence when a draft is removed. Protect SQL literals/parameters as potentially sensitive metadata; sanitize general audit events and job errors.

## 11. API contract guidance

Suggested routes under the existing `/api/v1` prefix; use repository route conventions where different.

| Method and route | Purpose and key checks |
| --- | --- |
| `GET /workspaces/{id}/ssd/connections` | List only effective authorized connections/bindings/capabilities; no credentials. |
| Existing Admin grant routes | Add/revoke SSD operation scope; Admin/workspace checks and audit. |
| `POST /workspaces/{id}/ssd/drafts` | Create QA-owned draft in current scope. |
| `GET/PATCH /ssd/drafts/{id}` | Load/edit own draft; concurrency and immutable revisions. |
| `POST /ssd/drafts/{id}/validate` | Validate exact revision and return operations, schema/issues, limits, and validation reference. |
| `POST /ssd/drafts/{id}/execute` | Verify revision/validation, reauthorize, enqueue job; return HTTP 202 and job/execution references. |
| `POST /ssd/drafts/{id}/sample` | Enqueue sample job for eligible read/result; never dispatch mutation SQL. |
| `GET /ssd/drafts/{id}/sample` | Return authorized, policy-compliant ≤5 rows/schema or pending/expired/blocked status. |
| `POST /ssd/drafts/{id}/datasets` | Generate normal dataset/version using eligible sample/recipe; async response when queued. |
| Existing dataset detail/publish/visibility routes | Extend owner/origin rules for QA SSD; automatic gates, no approval record. |
| Existing job get/list/cancel routes | Reuse lifecycle; enforce workspace, ownership, privacy and mutation outcome semantics. |
| Existing View Data/download/provision routes | Enforce visibility and existing consumption/lock/policy rules. |

Async responses include `job_id`, current state, draft/query revision, and existing status URL. Requests include expected revision and idempotency key where needed. The backend derives actor, workspace rights, effective policy, grant revision, and allowed operations; never trust these as client assertions.

Use the existing error envelope with stable codes such as `SSD_OPERATION_NOT_ALLOWED`, `SSD_QUERY_STALE`, `SSD_UNSUPPORTED_STATEMENT`, `SSD_SAMPLE_EXPIRED`, `SSD_EMPTY_RESULT`, `SSD_EXECUTION_OUTCOME_UNKNOWN`, and `SSD_LOCK_CONFLICT`. Return 403 for known forbidden actions and the existing not-found pattern where required to avoid disclosing inaccessible objects.

## 12. Repository implementation plan

### Phase 1 — Inspect and map

Inspect the current branch and its repository instructions. Locate current role/grant checks, SQL-to-Data UI/query validation, connector adapters, background worker, dataset publication predicates, privacy settings, storage semantics, View Data, entity locking, and tests. Record changed integration points and existing behavior before editing.

Historical candidates from the architecture guide include:

- Frontend: `frontend/src/App.tsx`, `frontend/src/api.ts`, existing source/query pages, dataset detail, Jobs, View Data, Admin grant components.
- Backend access/connections: `backend/tigr/access.py`, `iam.py`, `platform_access.py`, `connections.py`, `connection_models.py`.
- Dataset/query: `source_queries.py`, `source_to_data.py`, `datasets.py`, `dataset_api.py`, `dataset_services.py`, `dataset_schemas.py`.
- Privacy/jobs/locks: `privacy_api.py`, `governance.py`, `jobs.py`, `jobs_api.py`, `outbox_worker.py`, entity-claim modules.
- Database/tests: `backend/migrations/`, `backend/tests/`, existing frontend test locations.

These paths must be verified in the current repository. Do not add Neo4j, a broker, an LLM, or a second ORM just for SSD.

### Phase 2 — Shared domain and migration

1. Add operation grants and dataset origin/visibility only where absent.
2. Add draft/query/execution metadata and necessary uniqueness/concurrency controls.
3. Extract a shared effective privacy decision function for QA/TDE paths, including workspace-disabled skip.
4. Add narrow QA-owner permissions for SSD creation/publication; preserve legacy publication and consumption behavior otherwise.
5. Keep old records at their current visibility/access semantics. Do not bulk-change existing datasets to Public or Private blindly.

### Phase 3 — Execution and background work

1. Keep legacy read-only validators and source execution unchanged; add an explicit SSD mutation service with its own authorization/transaction contract.
2. Add dialect-aware validation, dependency authorization, row bounds, source-entity lock checks, and mutation outcome handling.
3. Integrate jobs/outbox, sample creation, privacy processing, read recipe/artifact selection, and cleanup.
4. Reuse standard dataset generation/publication/lineage services; isolate mutations from reusable dataset recipes.

### Phase 4 — UI and integration

Implement QA navigation, Admin grant controls, the three-step SSD page, background progress/resume behavior, five-record grid, privacy read-only summary, visibility controls, and existing dataset actions. Add origin/visibility filters or badges where they fit existing dataset lists. Preserve other menus and role behavior.

### Phase 5 — Verify and hand over

Run the tests below, relevant existing regression suites, frontend type/build checks, and migration checks. Deliver changed code, migrations, API notes, configuration defaults, a supported-SQL capability matrix, test results, and known limitations. Report any code behavior that conflicts with the storage/policy assumptions explicitly.

## 13. Acceptance criteria and verification

| ID | Scenario | Expected result |
| --- | --- | --- |
| SSD-01 | QA opens sidebar/route | Self Servicing Data is present and authorized; three steps render. |
| SSD-02 | QA has no connection grant | Empty state; no source data or credentials disclosed. |
| SSD-03 | Admin grants SELECT on one workspace connection | Only permitted binding appears; SELECT works; write attempts fail server-side. |
| SSD-04 | Admin grants Insert / Update but not Delete | INSERT/UPDATE work subject to read dependencies; DELETE denied. |
| SSD-05 | QA tampers with workspace, connection, database, or object IDs | Request and worker reject access outside effective scope. |
| SSD-06 | Grant revoked or connection repointed after queueing | Queued execution does not run with stale authority or against a new database. |
| SSD-07 | Valid SELECT with join/CTE | Authorized dependencies resolve; validation and bounded background read succeed. |
| SSD-08 | Validation receives mutation SQL | Validates metadata only; source records remain unchanged. |
| SSD-09 | Multiple statements, DDL, hidden DML CTE, procedural call, or mutation expression outside the SSD safe-function allowlist | Rejected; validation never executes the query. SELECT/WITH functions are checked by PostgreSQL and run only within a bounded read-only transaction. |
| SSD-10 | Query or parameter edited after validation | Execute blocked until current revision is validated; old jobs cannot advance new revision. |
| SSD-11 | Query execution runs slowly | HTTP returns job reference; Jobs/page show progress and final results; navigation remains usable. |
| SSD-12 | Sample query matches more than five rows | Background sample job returns at most five actual records from the approved pre-masked source. |
| SSD-13 | Sample returns 1–4 or zero records | Correct count/grid or empty state; zero-row draft retained and dataset generation blocked. |
| SSD-14 | SSD payload attempts to change source privacy assumption | No editable UI; server records the pre-masked-source assumption and does not expose masking controls. |
| SSD-15 | Workspace privacy enabled with or without TDE policy | SSD samples and output remain available without additional masking; other workflows keep existing policy behavior. |
| SSD-16 | SSD source has no TDE masking policy coverage | Query, preview, dataset creation, and publication still work; no policy block or masking run is created. |
| SSD-17 | Workspace privacy setting changes | SSD preview and datasets remain available; non-SSD privacy behavior is unchanged. |
| SSD-19 | QA generates and opens dataset | Standard dataset/version/detail and lineage created; correct storage mode retained. |
| SSD-20 | QA publishes own eligible SSD dataset | Direct publication; View Data availability for creator; no TDE/Admin/data-use approval. |
| SSD-21 | QA tries publishing another user's or unrelated TDE dataset | Existing ownership/role policy enforced; SSD rights do not escalate access. |
| SSD-22 | Private SSD dataset accessed by another ordinary QA user | Excluded from lists/counts/results; direct detail/job/sample/export requests denied. |
| SSD-23 | Public SSD dataset viewed in same vs different workspace | Same-scope eligible users can discover/use under existing rights; other workspace denied. |
| SSD-24 | Public becomes Private | Future unauthorized access removed; caches/links rechecked; audit/reservations handled without silent deletion. |
| SSD-24a | QA owner uses a private data set | Owner can view/download published versions without a reservation; non-owners remain denied and source-entity lock protections still apply. |
| SSD-25 | INSERT/UPDATE completes | Source change committed once; count/outcome visible; read result selected independently for dataset. |
| SSD-26 | Sample/regenerate/generate/publish/download/provision after DML | No mutation SQL re-executes; only validated reads or supported artifacts are used. |
| SSD-27 | DELETE has no retained readable result | Mutation success retained; user can provide a SELECT; no fabricated deleted-row dataset. |
| SSD-28 | Double click, request retry, duplicate outbox dispatch | Existing mutation execution reused; no second source commit. |
| SSD-29 | Worker dies or source connection drops around commit | Outcome unknown if not provable; automatic mutation retry suppressed; reconciliation path audited. |
| SSD-30 | Cancel before/after commit or sample fails after commit | Accurate rollback/committed state; no claim that a committed mutation was undone. |
| SSD-31 | UPDATE/DELETE exceeds row cap or targets protected entities | Transaction rejected/rolled back where supported; locks respected; no partial success hidden. |
| SSD-32 | Sample expires or browser refreshes | Draft resumes with jobs; safe read regeneration offered; DML not replayed. |
| SSD-33 | Dataset is locked/reserved by another actor | Existing usage restrictions remain effective for SSD datasets. |
| SSD-34 | Public consumer lacks source rights for live extraction | Existing explicitly authorized dataset service or required user SELECT enforced; creator credentials not delegated implicitly. |
| SSD-35 | SQL literals, sensitive rows, failures inspected in logs | No raw sensitive values/secrets in generic jobs/audit/error output. |
| SSD-36 | Existing flows run after migration | Scenario to Data, Source/SQL to Data, PII, jobs, locks, delivery, tickets, and existing datasets preserve behavior except explicit shared privacy rule changes. |
| SSD-37 | Legacy validator receives INSERT/UPDATE/DELETE | Remains read-only; SSD write support does not relax legacy endpoints. |
| SSD-38 | New deployment/migration applied to existing data | No new QA grants, policy disablement, visibility widening, or destructive data migration. |

Use unit tests for authorization, SQL classification, policy decisions, state transitions, and visibility predicates. Use a disposable transactional database for mutation integration tests, rollback bounds, source-lock conflicts, and commit uncertainty/crash injection. Add end-to-end coverage for QA SELECT→sample→Private/Public publish and Admin grant→QA write→SELECT result→publish. Do not run destructive verification against real test environments or production data.

## 14. Release, rollback, and completion criteria

- Use additive migrations and the repository's feature-availability mechanism where present. Feature disablement prevents new SSD execution without deleting existing SSD datasets, completed mutation evidence, or audit history.
- Preserve current workspace privacy settings and existing dataset access during deployment. Backfill owner/origin conservatively from authoritative records.
- Keep changes compatible with existing APIs; do not rename or remove shared routes solely for SSD.
- Document that application rollback cannot reverse committed source mutations. Preserve execution evidence and disable new SSD writes before rolling back workers/routes; do not drop tables containing active jobs or historical commit records.
- The feature is complete only when SQL executes through authorized backend jobs, samples are real and bounded, privacy rules work for both creator roles, QA can publish without approval, visibility is enforced on all access paths, mutation retries cannot silently duplicate changes, and the regression suite demonstrates existing workflows still work.

## 15. Direct implementation instruction for Codex

Implement Self Servicing Data as an additive QA workflow in the existing TIGR repository using this specification. First inspect repository instructions and current services; then reuse the existing SQL editor, connection authorization, datasets, privacy, Jobs/outbox, and View Data capabilities. Add only the schema/API/UI extensions required for SSD. Keep SQL mutations isolated from read-only legacy paths and from dataset provisioning. Enforce Admin-granted operations, Private/Public scope, TDE-only privacy configuration, workspace-disabled privacy skip, and approval-free QA-owned SSD publication. Run the acceptance and regression checks, and provide a concise handover identifying changed files, migrations/configuration, supported connectors/statements, verified storage behavior, tests performed, and any remaining limitations.


## Reusable SQL datasets and refresh ? 6 November 2026

- A successful SELECT, INSERT, UPDATE or DELETE can be saved and published as an SSD dataset even when no records are returned. Saving a mutation uses its committed execution evidence and never reruns it.
- All human workspace roles (Admin, Test Data Engineer and QA Engineer) can refresh accessible published SSD datasets. Existing workspace and private dataset access rules apply. QA users must retain the required SSD connection operation grants; Admin and TDE refreshes use their workspace management authority. Connection operation scope, source privileges and entity locks continue to apply.
- Refresh reexecutes the encrypted SQL and parameters from the published version, without accepting query or field edits. It preserves schema, selected fields, output format, record limit, permitted use and expiry. Changed output column names/types block refresh and leave the previous published version available.
- A successful refresh stores a new version of the same dataset, including zero-row executions, and publishes it with execution, publication and lineage evidence. Earlier versions remain intact. Mutation datasets store affected-row evidence; they do not invent result records when SQL returns none.
- INSERT, UPDATE and DELETE refreshes require explicit confirmation on each new intent. Repeated requests with the same idempotency key reuse the job, and worker redelivery never repeats a dispatched mutation. Uncertain source outcomes block another mutation refresh pending reconciliation.
- Refresh dataset is available in Data Sets, dataset details, View Data and the published SSD editor. Reload view only reloads the stored records and does not execute source SQL.
- API: POST /api/v1/datasets/{dataset_id}/refresh accepts expected_version, idempotency_key and confirm_mutation only; returns HTTP 202 with job/execution IDs. Refresh is asynchronous and rechecks access before source dispatch and before committing mutations.
