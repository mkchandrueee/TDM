# TIGR Multi-Source Connections and Federated Dataset Generation

**Document type:** Requirements and implementation guide for Codex  
**Product:** TIGR — Test-data Intelligence, Generation & Readiness  
**Scope:** Extend source discovery, connection management, query planning, dataset generation, and entity locking to support multiple databases and multiple source connections.

## 1. Purpose

Implement two capabilities for Test Data Engineers (TDEs):

1. Use one logical connection profile to select and query multiple separate databases on the same supported server/platform, including schemas and fields in each database.
2. Select multiple source connections, databases, schemas, tables, and fields in one dataset-generation plan, including joins and filters across them.

Redesign the connection page so users can select and manage connections through a list-of-values (LOV) control, inspect connection overview/history, discovered structure, and entity-locking setup, and access the relevant actions on the right side of the page.

This change must preserve TIGR's data-minimization behavior: persist source metadata, schema, mappings, query plans, audit evidence, and one approved sample record for display. Do not persist complete source extracts as TIGR datasets. Full requested data is read and processed for a provisioning job and delivered to the configured target or export destination.

## 2. Current baseline

| Current behavior | Current limitation |
|---|---|
| A PostgreSQL connection points to one database. It can query multiple tables and schemas in that database, subject to configured-schema discovery and database-user grants. | It cannot natively address another database through the same database session. |
| Each source mapping or generation plan uses one source connection. | A dataset cannot combine data from multiple connection profiles. A target connection is for delivery and is not a query source. |
| Entity-lock identity is scoped to a TIGR connection. | Equal customer/entity values from different connections are not treated as the same entity. |

## 3. Terminology and design model

Use these terms consistently in code and UI:

- **Connection profile:** TIGR-managed endpoint and credentials. Users enter host, port, username, and password in the connection UI; TIGR encrypts credential material before storing it and never returns it through profile APIs. Keep the encryption key outside the database and stable across restarts/backups. Legacy secret references may be supported during migration.
- **Database binding:** A database/catalog reachable through a profile and authorized for TIGR. For PostgreSQL, each database binding requires a database-specific session, even when host, port, and credentials are shared.
- **Source object:** A discovered schema, table, view, JSON structure, or file structure within a database binding.
- **Source set:** The set of connection profiles and database bindings selected by a dataset scenario.
- **Federated plan:** A TIGR plan that partitions source reads by connection/database, then combines the results under one logical dataset plan.
- **Entity domain:** An optional, explicitly configured identity namespace used to correlate entity keys across source bindings, such as `CUSTOMER_ID` across a core banking database and a CRM connection.

### 3.1 Required architecture behavior

Do not imply that a relational database session can directly query arbitrary separate databases. TIGR must implement logical federation:

1. Build a logical plan for selected fields, filters, joins, and requested record count.
2. Partition the plan into source-specific read plans, each executed with the correct connection profile and database binding.
3. Push supported projections and filters down to source systems.
4. Transfer only required columns/rows to an isolated, short-lived job workspace for cross-source joins and final shaping. Prefer streaming or bounded batches; do not copy full source tables.
5. Apply configured PII policies before data is persisted to a target or made available for download.
6. Delete transient data and temporary artifacts on job completion, failure, cancellation, and TTL expiry. Record cleanup outcomes in job audit evidence.

The implementation may use an existing query engine if present. If none exists, Codex should add a small federation execution abstraction and an isolated job-local execution engine (for example, an embedded analytical engine) rather than coupling federation logic to the API or UI. Confirm supported source dialects and pushdown behavior against the existing connectors before selecting an engine.

## 4. Goals and non-goals

### 4.1 Goals

- Allow a TDE to select more than one database reachable through a single connection profile.
- Allow a TDE to combine data from two or more source connection profiles in one dataset plan.
- Discover and browse schemas, tables, columns, JSON paths, and supported file structures across selected bindings.
- Support explicit, reviewable join mappings across schemas, databases, and connection profiles.
- Preserve source-level permissions, read-only execution, auditability, reservations, locking, and PII policies.
- Keep existing single-connection datasets functional without requiring users to recreate them.

### 4.2 Non-goals for this change

- Arbitrary cross-database writes or source mutation.
- Replacing the target connection/delivery model.
- Automatically treating equal values from different connections as the same entity without an approved identity mapping.
- Persisting full source datasets in TIGR's application database.
- General-purpose, unrestricted user SQL execution across all registered connections.
- Distributed transactions across source systems.

## 5. Functional requirements

### 5.1 Connection profile and database selection

**FR-01. Connection LOV**  
The connection page and dataset source-selection flow must provide a searchable LOV of connections the signed-in user is permitted to view/use. Show a clear display name, connector type, environment/tag, status, and last validation time. Do not display secrets or sensitive connection properties.

**FR-02. Multiple database bindings per profile**  
A profile may expose one or more configured/authorized databases. TDEs can select multiple databases from the profile when the connector supports them. For PostgreSQL, each selected database is discovered and queried through its own database-specific session. Selecting multiple databases does not bypass database grants or network restrictions.

**FR-03. Database enumeration modes**  
Support controlled database selection using either:
- an administrator/TDE-configured allowlist of database names; or
- an explicit database entered/selected by an authorized user when server-side enumeration is unavailable.

Do not enumerate or expose databases the configured service identity is not allowed to access. Never expose unrestricted server catalog discovery by default.

**FR-04. Cross-schema discovery**  
For every selected database binding, discover only schemas/objects allowed by the profile's discovery configuration and database permissions. Allow a user to browse across discovered schemas, tables/views, columns, types, and keys. Show the full object path: `connection / database / schema / object / field`.

**FR-05. Discovery lifecycle**  
Support validate, discover/refresh, and disable/archive operations per profile and database binding. A refresh must version the discovered structure and retain prior versions referenced by saved datasets. Show last refresh time and errors without overwriting saved scenario mappings.

**FR-06. Profile-level access enforcement**  
Workspace permissions, source-use permissions, and the database service identity's grants must all be enforced. A user must not gain access to a source by selecting it in a LOV or editing a request payload.

### 5.2 Multi-source dataset planning

**FR-07. Multiple source selection**  
A TDE can add multiple permitted connection profiles to a single Scenario-to-Data or Source-to-Data plan. Each profile can contribute one or more database bindings. The user can remove a source before saving the plan.

**FR-08. Source/object/field selection**  
After adding a source, the user can select database, schema, object, and fields. Fields must retain their originating binding and object identity. Duplicate field labels must be disambiguated with their full path.

**FR-09. Requirement-to-schema mapping**  
For natural-language requirements, TIGR can recommend fields across all selected source bindings. Each recommendation must display its full source path and confidence/reason where supported. The TDE must review and confirm mappings before a plan can be saved or executed. No hidden automatic source selection.

**FR-10. Cross-source relationships and joins**  
Allow a TDE to define and review joins across objects in the same database, separate databases under one profile, and separate profiles. A join definition must include:
- left and right source paths and fields;
- join type (inner/left; other types only if supported safely by the existing engine);
- key comparison and compatible types/casts, if required;
- cardinality expectation (one-to-one, one-to-many, many-to-one, unknown);
- optional null handling and trim/case normalization, if supported;
- whether the relationship is discovered metadata or a user-confirmed mapping.

Warn about missing keys, incompatible data types, likely many-to-many joins, non-unique keys, and estimated high row expansion. Block execution if the plan cannot be executed safely within configured limits.

**FR-11. Filters and parameterization**  
Support read-only, parameterized filters and joins. Values must be bound as parameters, not interpolated into SQL. Field/table/schema/database identifiers must be validated against the selected user's discovered metadata and safely quoted for the source dialect. Reject DDL/DML and multiple-statement execution.

**FR-12. Logical query plan**  
Before execution, show a plan preview with source reads, selected fields, joins, filters, estimated matches/rows when available, configured row caps, and data-handling steps. Indicate which operations are pushed down and which are evaluated by TIGR's federation layer. The UI must disclose when estimates are unavailable.

**FR-13. Source-specific read execution**  
Execute each source read with the correct profile and database binding. Use read-only transactions/credentials where supported, statement timeouts, row/byte limits, concurrency limits, and cancellation. A failure in one source must fail the combined job with a clear per-source error; do not silently produce a partial dataset unless a future feature explicitly supports partial results.

**FR-14. Bounded federation**  
The executor must enforce configurable limits for source rows, bytes, join output rows, memory, execution time, concurrency, and temporary storage. Support streaming/batch reads and early filtering. Reject plans that exceed hard limits; provide actionable remediation such as adding filters, reducing requested count, or narrowing columns.

**FR-15. Dataset versions and lineage**  
Save the source set, database bindings, structure versions, selected fields, relationships, filters, parameters (without secrets), logical plan, query-plan versions, creator, and audit events with the dataset/scenario version. Display lineage as `connection(s) → database(s) → schema/object/field mapping → federated plan → job → masking → target/export`.

**FR-16. Existing dataset compatibility**  
Existing single-source datasets must load as a source set with one profile and one database binding. Existing query plans and audit evidence remain readable. Migration must not cause previously saved datasets to point to a different database if a profile's default database changes.

### 5.3 PII, target delivery, reservations, and audit

**FR-17. PII policy across sources**  
Apply classification and masking consistently to fields from every selected source. Resolve policy by canonical field/entity mapping and source path. If a required field has no applicable policy, follow the workspace's current policy behavior; for protected data, fail closed and explain the missing policy. Record policy version and per-field actions in job evidence.

**FR-18. Temporary data protection**  
Federation staging must be isolated per job, encrypted at rest where disk is used, access-limited to the job service identity, excluded from backups, and deleted on all terminal paths and TTL expiry. Secrets must be fetched from the existing secret store and never copied into plans, logs, UI responses, or temporary data metadata.

**FR-19. Target delivery**  
Continue to treat target connection as a delivery destination, not a source by implication. The plan may have multiple sources and one selected target/export destination. Validate target permissions separately. Do not write generated/extracted results until validation and masking succeed.

**FR-20. Audit**  
Record actor, workspace, dataset/scenario/version, source profile IDs and database binding IDs, structure versions, plan version/hash, execution start/end, per-source counts/status, limits applied, masking policy versions, target/export, cancellation/failure, and staging cleanup status. Do not log source row values or secrets.

**FR-21. Entity locking across connections**  
Extend entity-lock configuration to support an optional shared Entity Domain. A TDE/Admin may map different source fields to a canonical entity key in that domain, for example `CoreBanking.Customer.CUSTOMER_ID` and `CRM.Contact.BANK_CUSTOMER_ID` → `Customer`. Lock identity is `(workspace, entity_domain, canonical_key)` when an approved cross-source mapping exists; otherwise retain the current `(workspace, connection, entity_key)` behavior. Normalize only using explicitly configured rules. Hash/tokenize lock keys in storage where feasible. Prevent accidental collisions between unrelated identifiers and provide conflict reporting when different source rows map to one canonical identity.

## 6. Connection page redesign

### 6.1 Page layout

Use a two-region layout:

- **Left/main region:** connection LOV/list and selected connection content.
- **Right action rail:** primary and contextual actions, consistently aligned to the right side of the page. On narrow viewports, collapse into an overflow menu while preserving action labels and keyboard access.

The page should not hide actions below a long discovered-structure list. Keep actions visible and contextual to the selected profile or database binding.

### 6.2 Connection LOV/list

- Search by display name, connector type, database, environment, or status.
- Filter by workspace/project, connector type, and lifecycle status where applicable.
- Show connection health/status, last validation, and number of configured databases.
- Selecting a profile loads its tabs. Where a profile has multiple database bindings, provide a database selector within the selected connection view; tabs should make clear whether they show profile-wide or selected-database content.
- Support add connection, subject to existing roles/permissions.

### 6.3 Tabs

**Overview**
- Display non-secret endpoint metadata, connector type, workspace/project, status, owner, enabled database bindings, default/allowed schemas, read-only status, last validation, last discovery, and usage references.
- Include **Connection History**: chronological events for creation, edits, validation, database binding additions/removals, discovery refresh, disable/archive, and failures. Each event shows timestamp, actor/service identity, action, outcome, and a sanitized detail. Include pagination/filtering for long history.
- Clearly distinguish profile-level events from database-binding events.

**Discovered Structure**
- Database selector, then schema/object/field explorer for that binding.
- Search/filter object and field names; display type, nullable, primary/foreign-key metadata when available, and structure version/last discovered time.
- Support refresh and show current/previous discovery status. Preserve saved-plan references to older structure versions.
- Permit selection/inspection across schemas and databases without implying they can be queried through a single database session.

**Entity Locking**
- Show current connection-scoped entity keys and lock usage.
- Configure entity key fields and optional mappings to a shared Entity Domain; show source path and canonical key mapping.
- Preview collision/duplicate statistics before enabling a shared domain, where source metadata can safely provide the estimate.
- Show active locks/reservations and related datasets, subject to permissions.
- Explain that a shared domain is required to recognize an entity across different connection profiles; otherwise locks remain connection-scoped.

### 6.4 Right-side actions

Contextual actions in the right rail (show only what role and profile state permit):

- **Add Connection** (page-level primary action)
- **Edit Connection**
- **Validate Connection**
- **Manage Databases** / **Add Database Binding**
- **Discover / Refresh Structure**
- **Disable / Archive Connection**
- **View Audit/History** (if not fully visible in Overview)
- **Save** / **Cancel** for edit/configuration states

Use confirmation for destructive or impactful actions, such as disabling a profile with active datasets or removing a database binding. Explain affected saved datasets/jobs and block removal when it would invalidate active jobs. Do not place routine inspect actions in destructive styling.

## 7. Dataset-generation UI changes

### 7.1 Source selection step

Add a **Sources** step to Scenario-to-Data and Source-to-Data flows:

1. Search/select one or more connection profiles from the permitted LOV.
2. For each profile, select one or more authorized database bindings.
3. Browse/select schemas, objects, and fields.
4. Add another profile or database binding as needed.
5. Review selected source paths and remove any before proceeding.

Represent each binding as a source card/tree with clear full-path labels. Do not merge identical schema/table names from separate databases into one ambiguous item.

### 7.2 Mapping and relationship step

- Show requirement fields mapped to source fields, with full paths and source badges.
- Allow correction of mapping, entity, key, and relationship details.
- Provide a relationship canvas/table for joins; each edge shows left/right key, join type, cardinality, and status.
- Offer discovered relationships as suggestions only. The user confirms or edits them.
- Show warnings inline and summarize blockers before allowing plan save or execution.

### 7.3 Plan preview and execution

- Show federated plan and source-specific reads, filters, row/byte bounds, estimate and cap.
- Show where data is joined and masked, plus the selected target/export.
- Execution status must include per-source phases (queued, connecting, reading, staged, joined, masked, delivered, cleanup) while presenting one overall job state.
- Failure detail should identify the failing source binding without exposing endpoint secrets or row content.

## 8. Data and API implementation guidance

Codex must inspect the current TIGR repository and adapt names/migrations to existing conventions. The following is a logical model, not a mandate to duplicate existing tables.

### 8.1 Logical entities

- `connection_profile`: existing profile; endpoint, connector type, secret reference, workspace, lifecycle/status, owner, configuration version.
- `connection_database_binding`: profile ID, database/catalog identifier, display name, allowed schemas, enabled status, validation/discovery timestamps, structure version, capability metadata. Store no credential material.
- `discovery_snapshot`: binding ID, version, discovered schemas/objects/fields/keys, status, created by/time, sanitized error metadata.
- `dataset_source_binding`: dataset/scenario version ID, profile ID, database binding ID, discovery snapshot/version, alias, selected object/field references.
- `dataset_relationship`: scenario version ID, left/right binding and field refs, join type, cardinality, normalization/cast config, origin, review status.
- `federated_query_plan`: scenario version ID, logical plan JSON, source subplans, hash/version, validation/estimate results, limits, creator/time.
- `entity_domain` and `entity_key_mapping`: workspace/domain, binding/object/field path, canonical entity type/key, normalization rule, approval status/version.
- `connection_activity`: profile/binding ID, actor, event type, outcome, sanitized metadata, timestamp.
- `federated_job_source_status`: job ID, binding ID, state, counts, timing, error code/details, cleanup state.

Use foreign keys, workspace scoping, soft lifecycle state where appropriate, and indexes for profile, workspace, dataset version, binding, status, and audit-time queries. Store plan/config JSON only for versioned, validated configuration; keep query values parameterized and secrets outside the database.

### 8.2 API capabilities

Align routes with existing API style; expected capabilities include:

- List/search permitted connection profiles and their database bindings.
- Add/edit/validate a profile and add/disable a database binding.
- Discover/refresh a specific binding and fetch paginated structure snapshots.
- Read paginated profile/binding connection history.
- Create/update entity domains and key mappings; validate/preflight collision reports.
- Validate a multi-source scenario plan, return field/relationship issues, estimates, and execution limits.
- Save/version a plan and execute/cancel a federated job.
- Read aggregate job status and per-source status.

Every endpoint must enforce workspace membership, role/action permission, profile use permission, database-binding allowlist, and object/field access. The backend must re-resolve submitted object and field IDs against the approved discovery snapshot; UI validation is not a security boundary.

### 8.3 Query planning and execution

Separate concerns behind interfaces similar to:

- `SourceConnector`: connect, discover, validate, and execute a parameterized read for a specific database binding.
- `DialectAdapter`: quote identifiers, bind parameters, describe capabilities, and generate source-specific read SQL.
- `FederatedPlanCompiler`: validate logical mappings, partition source reads, determine pushdown, estimate bounds, and create an immutable execution plan.
- `FederatedJobExecutor`: execute source subplans with bounded batches, join/shape results in the isolated job workspace, apply policy, deliver, and clean up.

Use a plan compiler rather than concatenating user-provided SQL. If legacy SQL is supported, parse it into a safe AST, allow only SELECT, validate every source reference against the selected source set, and prohibit unsupported constructs. Do not route credentials through the query engine in user-visible plan data.

## 9. Non-functional requirements

- **Security:** least-privilege source identities; read-only access; secret store; TLS; workspace isolation; no privilege escalation via database selection.
- **Privacy:** no source-row values in logs/traces/errors; redact sensitive query parameter values; masking before delivery; job-local transient staging with deterministic cleanup.
- **Performance:** pushdown projection/filter; bounded parallelism; configurable query and job timeouts; support pagination/streaming; report source-level timings.
- **Reliability:** retries only for safe idempotent reads and transient failures; do not duplicate target delivery on retry; job state transitions and cleanup are idempotent.
- **Compatibility:** existing one-source plans and datasets continue to behave; API additions should preserve old request formats during migration or provide a safe adapter.
- **Observability:** metrics for validations, discovery duration/failures, per-source read time/rows/bytes, join time/output, limit rejections, masking, delivery, cleanup, and job failures.
- **Accessibility:** LOV, tabs, right-side actions, relationship editor, and warnings usable by keyboard and screen reader.

## 10. Acceptance criteria

1. A TDE with permission can select two authorized databases available through one PostgreSQL profile, discover structures in both, and create a plan referencing fields from each.
2. Execution opens database-specific source sessions and combines data through TIGR's federation layer; it does not rely on an unsupported cross-database SQL reference.
3. A TDE can select two distinct connection profiles and create a single plan with a reviewed join across them.
4. The plan preview identifies each source, database, schema/object/field, join, filter, estimated/capped volume, pushdown decision, and masking/delivery step.
5. Cross-source joins validate key existence, type compatibility, cardinality warnings, and configured row/byte/runtime bounds before execution.
6. All database/object/field selections are re-authorized server-side against workspace permissions and discovery metadata; tampered request payloads cannot access unselected or unauthorized sources.
7. Full source rows exist only in the bounded job execution context and are removed from temporary storage after success, failure, cancellation, and TTL cleanup. No full extract is stored in TIGR's persistent dataset metadata store.
8. PII policies are evaluated for fields from all sources before target delivery/export; job evidence records policy versions and actions without row values.
9. Existing single-connection datasets load and execute as one-source plans; existing locks remain connection-scoped unless explicitly mapped to a shared Entity Domain.
10. With an approved Entity Domain mapping, the same canonical entity key from two profiles resolves to the same lock identity. Without it, equal raw IDs from different profiles remain distinct.
11. Connection page provides LOV selection and Overview, Discovered Structure, and Entity Locking tabs. Overview includes connection history with actor/time/outcome.
12. Right-side actions are visible/contextual and permission-aware; removing or disabling a binding with active use is blocked or clearly handled.
13. Duplicate labels across databases/connections are distinguishable by full source path in selection, mapping, plan preview, and lineage.
14. Per-source job status and sanitized errors are visible to authorized users, and job/audit records identify cleanup completion.

## 11. Suggested implementation sequence

1. **Repository inspection and compatibility map:** locate connection models, connectors, discovery, dataset/scenario plans, SQL execution, PII, reservation/lock, UI routes/components, and migrations. Document existing constraints in code comments or implementation notes.
2. **Persistence and migration:** add database bindings and versioned structure snapshots; adapt single-source records to one binding without changing their effective database identity; add activity history.
3. **Connector and discovery updates:** add binding-aware connection/validation/discovery APIs; database-specific connection sessions; cross-schema structure browse with permission filtering.
4. **Federated plan model:** introduce source set, full-path field refs, relationship mappings, plan validation, safe query compilation, and plan preview. Preserve single-source behavior through an adapter.
5. **Federated execution:** partition reads, push down projections/filters, bounded batch staging, join/shape, PII application, target delivery, cancellation, per-source status, and cleanup.
6. **Entity locking:** retain legacy scope and add opt-in entity domains/key mappings, validation, collision handling, and audit.
7. **Connection page UI:** LOV/list, database selector, three tabs, activity history, right-side action rail, empty/loading/error states, and responsive/accessibility behavior.
8. **Dataset UI:** multi-source selector, database/schema/object/field picker, mapping editor, join editor, plan preview, per-source execution status, and lineage.
9. **Operational controls:** configure row/byte/time/memory/concurrency/TTL limits; add metrics and sanitized logs; document supported connector capabilities and limitations.
10. **Verification:** add or update unit, integration, migration, authorization, UI, and end-to-end coverage for the acceptance criteria. Include tests for one-profile/two-database and two-profile federation, failures, cancellation, cleanup, PII, and legacy dataset compatibility.

## 12. Delivery checklist for Codex

- Update backend models, migrations, connector interfaces, services, API schemas/routes, authorization, and job execution.
- Update the connection page and dataset-generation flows described above.
- Update TIGR technical/business documentation and API/schema diagrams affected by federation.
- Add configuration for safe execution limits with conservative defaults and workspace/admin control.
- Include a migration/rollback note and list supported source connector combinations/capabilities.
- Do not mark the feature complete if the UI only lets users select multiple sources but the backend still executes against one source, or if temporary cross-source data is retained after the job.

## 13. Implementation handoff — current status and remaining work

**Status: partially implemented; do not report this feature as complete.** This section is the handoff for the next Codex session. Re-check the code before changing it, since this document records the known state as of 2026-10-25.

### 13.1 Implemented foundation

- MySQL and PostgreSQL are available as read-only source connectors. MySQL database selection is restricted by configured allowed databases (or its configured default database); database reads use separate sessions.
- The Source-to-Data UI can construct a reviewed plan with multiple source profiles, selected fields, and inner/left joins.
- Backend plan validation resolves source objects against workspace connections and saved structure metadata. A valid plan reports `execution_supported: true` and can be saved.
- A federated generation route queues a job. Its worker revalidates the saved plan, reads projected source fields, joins in worker memory, enforces fixed row/join/byte caps, records source job steps and lineage, and discards transient in-memory rows on terminal paths.
- Privacy-enabled generation re-reads the saved federated plan through the privacy worker and persists only the masked result. Existing publication and provisioning remain separate workflows.
- The current federation implementation is intentionally narrow: relational PostgreSQL/MySQL sources, reviewed inner/left equijoins, hard-coded limits, and no user-supplied SQL.

Useful starting points: `backend/tigr/source_to_data.py` (plan validation, execute route, worker, job status), `backend/tigr/federation.py` (bounded read/join execution), `backend/tigr/privacy_api.py` (fresh federated reads for masking), `backend/tigr/outbox_worker.py` (job dispatch), `frontend/src/FederatedPlanBuilder.tsx`, and `frontend/src/SourceToDataPage.tsx`.

### 13.2 Required work still pending

Implement and verify these against the functional requirements and acceptance criteria above:

1. **Shared Entity Domains and cross-profile identity matching (FR-21, acceptance 9–10).** Add workspace-scoped domain and approved key-mapping persistence, APIs, UI, normalization/version rules, collision/conflict preflight, and audit. Integrate canonical domain keys into reservation/lock acquisition so approved mappings for different profiles resolve to the same lock. Existing connection-scoped locking must remain the fallback. Current connection-scoped HMAC lineage does not provide cross-profile identity matching.
2. **Parameterized source filters and useful estimates (FR-11–12).** Add a reviewed filter model and UI; bind values as parameters and validate identifiers against the selected discovery snapshot. Compile supported predicates into each source read. Provide estimates when safely available, otherwise explicitly report unavailable; always show caps and pushdown details. Current plans have no source-filter capability and previews report `estimates_available: false`.
3. **User-visible cancellation and configurable execution limits (FR-13–14, non-functional requirements).** Add an authorized cancel endpoint/action and connect it to job state and a cancellation signal checked during source reads and joins. The executor currently accepts an internal cancellation callback, but there is no complete federated-job cancel flow. Move hard-coded row/join/byte limits and connector/job timeouts into validated configuration with conservative defaults and appropriate admin/workspace controls. Define cleanup/TTL settings and make cancellation and cleanup outcomes auditable. Do not imply that checking between operations can interrupt a currently blocked database read; configure and enforce driver statement timeouts too.
4. **Target selection and delivery from the generation job (FR-19–20, acceptance 4, 8, 14).** Extend plan/job request and validation to choose an authorized target or export destination. After output validation and masking, deliver idempotently from the job, track a per-target delivery phase/result, and record target/export lineage and audit. Do not write before masking succeeds. Today generation creates the dataset through TIGR's existing publication/provisioning flow; it does not automatically deliver to a target from the federated job.
5. **Complete execution lifecycle and status.** Expand per-source/job progress to the specified phases (queued, connecting, reading, staged, joined, masked, delivered, cleanup); expose sanitized errors, timings, counts, applied limits, masking evidence, delivery result, and cleanup status to authorized users. Ensure failure, cancellation, retry, and duplicate outbox delivery are idempotent and never produce a partial dataset silently.
6. **Connection management page requirements (sections 6 and 12).** Audit the current Connections page against the searchable permitted connection LOV, database-binding selector, Overview/History, Discovered Structure and Entity Locking tabs, contextual right action rail, responsive/accessibility behavior, and safe disable/removal with active-use checks. Implement the missing pieces; current federated plan UI alone does not satisfy this page redesign.
7. **Versioning, cleanup, and operational hardening (FR-05, FR-15, FR-18, FR-20).** Ensure saved plans pin immutable discovery/plan versions and cannot silently change when a profile's default database or structure changes. Persist cleanup outcomes and audit without row values or secrets. Review process-local memory staging against worker concurrency, crash/restart, TTL, memory exhaustion, and privacy-worker behavior; implement isolated encrypted job staging and expiry cleanup if the selected deployment model requires disk/shared staging.
8. **Acceptance coverage and docs.** Add migration, authorization, connector, executor, cancellation, limit, masking, delivery, cleanup, lineage, and UI coverage, including one-profile/two-database and two-profile plans plus legacy single-source compatibility. Update API/configuration/deployment docs with supported combinations, actual guarantees, operational limits, and rollback notes.

### 13.3 Recommended next-session order

1. Inspect the current models, migrations, lock/reservation services, job state transitions, target provisioning APIs, configuration conventions, and Connections page before editing.
2. Implement shared Entity Domains and locking as a vertical slice (schema/migration → authorization/API → lock integration → UI → collision reporting).
3. Add filter/estimate plan support, then configurable limits and a real cancel lifecycle; update previews and job status together with backend behavior.
4. Implement masked, idempotent target delivery from the federated job, including lineage/audit and cleanup status.
5. Finish the Connections page redesign and lifecycle/versioning gaps.
6. Run the relevant automated and end-to-end checks for the acceptance criteria, then revise this status section based on verified behavior. Only mark complete when every required acceptance criterion is met or explicitly descoped by product.

### 13.4 Constraints and safety notes for follow-up work

- Preserve server-side authorization and revalidation for every source, database, object, field, and target; never rely on the browser's validation as an access boundary.
- Keep source row values and secrets out of plans, logs, errors, and audit records. Keep raw federated rows transient and persist only policy-compliant output.
- Do not make shared identity matching implicit. A canonical cross-source lock requires an approved, versioned Entity Domain mapping.
- Do not call generation complete just because the job can read/join multiple sources. Delivery, cleanup, lineage, authorization, cancellation/limits, and the outstanding UI requirements above are part of this feature.
