# TIGR — Technical Specification
## Banking Test Data Management Solution

**Solution name:** TIGR — Test-data Intelligence, Generation & Readiness  
**Tagline:** AI-powered data, ready for every test.  
**Document type:** Technical specification  
**Status:** Draft for architecture and engineering review  
**Companion document:** TIGR Business Requirements Document

---

## 1. Purpose

This document defines a reference technical design for TIGR based on the business requirements and the requested technology stack:

- **UI:** React, TypeScript, Vite
- **API:** Python, FastAPI
- **Operational database and vector search:** PostgreSQL with pgvector
- **Relationship graph:** Neo4j

It describes the technical design for exactly six MVP1 capabilities: Platform Access & Administration; Source Onboarding & Connectors; Scenario to Data Generation; Data Locking & Reservations; PII Data Discovery & Masking; and Data Provisioning & Validation. Cross-cutting services such as jobs, audit, exports and APIs support those capabilities and are not separate product scope. The four technologies specified by the product owner are required; supporting tools below are recommendations and may be replaced by bank-standard equivalents. This is a build specification for engineering teams, not a final infrastructure sizing or regulatory approval.

**Product model:** The data set is the primary managed unit. A workspace is the environment and access boundary in which source/target connections are configured, validated, saved, and used to create and consume data sets. MVP1 supports two data-set creation paths: AI-assisted Scenario to Data Generation with GenAI/LLM support, and **Source to Data**, where a user selects an approved source and mapping, chooses required fields, and generates/provisions a data set. All outputs converge on the same versioned data-set lifecycle, lineage, validation, publication, access, reservation/lock, and audit controls.

## 2. Technical objectives

1. Provide secure, role-scoped access for exactly three human roles: Test Data Engineer, QA Engineer, and Admin.
2. Support relational sources and file-based JSON, CSV, and structured delimited text in the MVP1 source and scenario workflows.
3. Keep PostgreSQL as the authoritative system of record for TIGR configuration, workflow state, audit, and lineage metadata.
4. Use Neo4j for connected metadata and dependency traversal, not as a repository for raw customer records.
5. Use pgvector to support schema-term mapping within the Scenario to Data Generation workflow.
6. Run long-running scans, masking, generation, and provisioning asynchronously.
7. Preserve traceability from workspace connection through transformations and output data set.
8. Support a user-requirement-to-data workflow that maps intent to approved schemas, builds safe source queries, and routes to source-backed data or GenAI synthetic generation.

## 3. Architecture decisions

### 3.1 MVP application shape

Start with a **modular FastAPI application plus separately runnable background workers**. Keep business modules independently testable and enforce clear interfaces, while deploying them as a small number of components. Split into microservices only when scale, isolation, or release ownership justifies it.

The browser UI communicates only with the FastAPI API. The API authorizes requests, validates workflow transitions, and creates jobs. Workers perform scans, masking, generation, and provisioning without holding an inbound browser session.

### 3.2 Workspace and data-set ownership model

- A workspace is the operating and authorization scope for connected sources/targets, users, mappings, policies, jobs, and the data sets created there. Admin can create, edit, activate, deactivate, and manage membership for workspaces. A user may belong to multiple workspaces under explicitly granted permissions.
- Connection setup is a stateful lifecycle: draft/configured → connection-tested → saved/enabled or disabled. Only saved, enabled connections can be referenced by a generation/provisioning job. Store credentials only as secret-manager references.
- A data set is the primary business object, not a synonym for a generation job or file. Its record identifies workspace, owner, creation pathway, schema and required fields, source/synthetic lineage, privacy state, validation evidence, publication state, immutable versions, expiry, and permitted usage.
- A scenario-generation job and a Source to Data job both create or update a draft data-set version. They use the same lineage, validation, and publication gate; a failed or partial job cannot produce a ready/published version.
- Publication is an explicit Test Data Engineer action after required validations. Only published versions appear as consumable data sets in the common catalogue/dashboard, and only for users with effective access.
- QA consumption is limited to an authorized published version. Reservations describe planned use; locks are active exclusive leases. Download/integration requests are checked against current access, privacy, and lock policies and audited.
- Every data-set version has a queryable lineage record and a user-facing lineage view. For source-backed/Source to Data outputs, lineage links workspace connection, source objects/schema version, mapping version, selected fields, query/criteria, generation or provisioning job, masking/transformation, validation, publication, and target/download events. For synthetic outputs, lineage links approved schema, scenario/prompt-template and model configuration versions, generation job, validation, publication, and delivery. Do not put raw sensitive values in lineage metadata.

### 3.3 System of record

- PostgreSQL is authoritative for users and project mappings, source registrations, model versions, policies, requests, approvals, jobs, reservations, data-set metadata, audit, and lineage.
- Neo4j is a derived graph projection of approved model and lineage metadata. PostgreSQL identifiers and versions are the source of truth.
- pgvector is an index over selected metadata descriptions, field descriptions, and scenario text. It must not be used as the authority for access control, policy decisions, or referential integrity.
- Raw files and generated artifacts are stored in encrypted object or file storage selected by the deployment. Do not store large source or output files as PostgreSQL blobs by default.
- Credentials are stored in an approved secret store. PostgreSQL stores only a secret reference and metadata needed to identify the connection.

### 3.4 Data minimization for graph and AI features

Neo4j and vector embeddings must not contain raw account numbers, card numbers, names, contact data, transaction details, authentication secrets, or other customer-level values. Use schema metadata, opaque object identifiers, approved descriptions, and non-sensitive scenario text. Do not embed sample values. Profiling examples shown to users must follow masking and redaction policy.

## 4. Logical architecture

### 4.1 Components

| Component | Technology | Responsibility |
|---|---|---|
| Web client | React + TypeScript + Vite | Role-scoped Dashboard plus six workflow areas, with workflow-specific pages, review steps, exports and dialogs |
| API application | FastAPI | Authentication integration, authorization, validation, business workflows, REST APIs, API documentation |
| Worker runtime | Python worker process using shared domain modules | Schema discovery, scenario/query execution, PII discovery and masking, synthetic generation, validation, provisioning, CSV export and cleanup |
| Operational store | PostgreSQL | Authoritative metadata, workflow state, audit events, configuration and job tracking |
| Vector index | pgvector extension in PostgreSQL | Similarity assistance for mapping scenario text to approved schema metadata |
| Relationship graph | Neo4j | Entity mappings, source dependencies, data lineage relationships and graph traversal |
| Artifact store | Encrypted object/file storage | Source files, intermediate artifacts and output files with lifecycle controls |
| Identity provider | Bank-selected OIDC/OAuth2-compatible provider | User authentication, group or claim integration and service identities |
| Secret manager | Bank-selected secret store | Database, file-transfer and model-provider credentials |
| AI model endpoint / gateway | Bank-approved embedding and generative model services | Schema mapping assistance and GenAI synthetic data within Scenario to Data Generation |

### 4.2 Request and job flow

1. The user signs in through the bank identity provider.
2. The UI calls the versioned API with an access token.
3. FastAPI validates the token, role, active workspace/object scope, request schema, and permitted state transition.
4. The API creates a durable job record in PostgreSQL and returns a job identifier.
5. A worker claims the job and retrieves connection secrets by reference.
6. The worker reads or writes through a source connector, applies approved controls, and records progress.
7. The worker writes generated files to encrypted artifact storage and saves result metadata and validation evidence.
8. The UI polls job status or receives a notification; the user can view the result subject to authorization.
9. Audit and lineage records connect the request, source version, model, policy, execution, and output.

### 4.3 Asynchronous execution

All potentially long-running or high-volume operations must be asynchronous: profiling, full scans, extraction, masking, GenAI generation, provisioning, graph synchronization, embedding creation, cleanup, and artifact deletion.

For MVP1, keep durable job and outbox state in PostgreSQL and use Celery workers with a bank-approved RabbitMQ-compatible broker for dispatch. PostgreSQL remains authoritative; broker messages are delivery signals. Use task acknowledgements, bounded retries, idempotency keys, dead-letter handling, source-specific rate limits, and operational visibility. Preserve the same public job contract if a bank-mandated task platform is used.

### 4.4 Supporting implementation toolchain

The following tools complement the required UI/API/database/graph stack. Treat them as recommended defaults, subject to the bank's approved software catalogue and security review.

| Capability | Recommended tool or standard | Use in TIGR |
|---|---|---|
| Python data validation and API schemas | Pydantic | Validate FastAPI requests/responses and define typed internal contracts |
| Relational persistence | SQLAlchemy | Manage PostgreSQL metadata and transactions using parameterized queries |
| Database migrations | Alembic | Version and deploy PostgreSQL schema changes |
| Dynamic schema validation | JSON Schema plus a Python validator such as jsonschema | Validate GenAI output against user-provided schemas |
| Graph connectivity | Neo4j Python driver | Execute scoped graph reads/writes |
| Background jobs | Celery with RabbitMQ broker | Execute profiling, GenAI generation, masking, provisioning, and cleanup |
| Artifact storage | Encrypted S3-compatible object storage; MinIO for local development if suitable | Store input files, generated batches, exports, and temporary artifacts outside PostgreSQL |
| Identity | OIDC/OAuth2-compatible identity provider | Browser and service authentication through the bank's provider |
| Secrets | Bank-approved secret manager | Store database, broker, storage, and model credentials |
| AI access | Bank-approved model gateway or endpoint | Provide embeddings and GenAI structured-output calls behind a provider-neutral adapter |
| Observability | OpenTelemetry SDK and collector; Prometheus/Grafana or bank-standard equivalent | Trace API-to-worker jobs, collect service/job metrics, and visualize health |
| Frontend testing | Vitest, React Testing Library, Playwright | Unit/component and browser end-to-end tests |
| Backend testing | pytest and HTTPX/FastAPI test client | Unit, API, worker, and connector-contract tests |
| Packaging and delivery | OCI-compatible containers and bank-approved CI/CD; Kubernetes/OpenShift where supported | Build, scan, deploy, configure, and promote services |

Pin compatible dependency versions in lockfiles and review them through the bank's security and software-supply-chain process. Do not expose model or database credentials to the browser.

## 5. Web application specification

### 5.1 Workflow navigation and page structure

Use React, TypeScript, and Vite. After Bank SSO and workspace selection, open the **Dashboard** as the landing page, then show workflow navigation for the six MVP1 capabilities:

1. Platform Access & Administration
2. Source Onboarding & Connectors
3. Scenario to Data Generation
4. Data Locking & Reservations
5. PII Data Discovery & Masking
6. Data Provisioning & Validation

Use separate route/page views for each capability, with step-based subpages for longer tasks. Avoid an all-in-one page containing every workflow. Keep the signed-in user's avatar, name, active workspace context, notifications and account controls at the top-right of the global header. Provide a Recent Activities view in Platform Access & Administration. The Dashboard is a summary landing page and does not replace the dedicated workflow pages.

**Dashboard requirements**

- Show role-scoped cards for recent activities, published/draft data sets as appropriate to the user, jobs by status, active/upcoming reservations, active locks, and count/status of configured workspace source connections.
- Admin activity view: all user and job activities across TIGR.
- Test Data Engineer activity view: own activities and QA Engineer activities within authorized workspace scope.
- QA Engineer activity view: own activities only.
- Apply authorization before aggregating counts and before serving drill-down rows; aggregates must not reveal otherwise inaccessible users, data sets, jobs, or connections.
- Make dashboard summaries refreshable and show the active workspace and time window used for each count.

The Scenario to Data Generation workflow must use these steps: **Text requirement → Schema mapping → Query development → Data generation**. Query development offers relational SQL and nested JSON query views. Data generation offers source-backed retrieval or GenAI synthetic generation from the same approved scenario plan.

### 5.2 Page requirements

- **Dashboard:** role-scoped activity, data-set, job, reservation, lock, and source-connection summaries with authorized drill-downs.
- **Platform Access & Administration:** SSO; workspace membership; exactly three MVP1 human roles and scoped/time-bounded grants; Admin workspace creation and lifecycle controls; service identity scopes; Recent Activities and searchable audit trail.
- **Source Onboarding & Connectors:** workspace source/target inventory; guided connection setup; secret reference selection; schema/file preview; connection/access test; explicit save/enable; connection lifecycle controls.
- **Scenario to Data Generation:** natural-language requirement entry; schema/entity/field/key/relationship mapping review; ambiguity clarification; read-only parameterized SQL and nested JSON query development; logical-plan, estimate and protected-result preview; relationship-complete subset selection and validation; source-backed or GenAI synthetic mode; versioned data-set creation, validation and lineage.
- **Data Locking & Reservations:** approved versioned data-set search and detail; data request and approval routing; planned-use reservation; exclusive user-specific lock acquire/inspect/renew/release; conflict and lease-expiry handling, stale-worker fencing, and reason-required audited administrator force-release.
- **PII Data Discovery & Masking:** classification findings; masking-policy upload/editor and preview; review; masking run results and release gate.
- **Data Provisioning & Validation:** **Source to Data** sub-workflow for source/mapping selection and required-field selection; database/file target and output-format selection; preflight and post-provision target/schema/count/relationship checks; job progress; versioned data-set creation, lineage, validation and publication; controlled rerun, cleanup and expiry actions.
- **Data set detail / lineage subpage:** open from every data-set row/version; show overview, status, validation, and an ordered lineage graph/timeline with drill-down evidence for every upstream and downstream step. For source-backed outputs show workspace connection, source objects/schema, mapping, selected fields, query/job, masking, validation, publish, and target/download. For synthetic outputs show approved schema, scenario and model/prompt configuration versions, generation job, validation, publish, and target/download.

### 5.3 CSV exports

Provide role-, workspace-, and object-scoped CSV downloads where tables or reports support review or downstream use: recent activities/audit results, source and target inventory, discovered metadata summaries, query-plan summaries and capped protected preview results, catalogue/reservation/lock reports, PII findings and policy coverage, masking validation, and provisioning/job validation reports. Export only fields the caller is authorized to see. Never export unmasked PII through reports or previews. Neutralize spreadsheet formula-leading cells, apply size limits and record export activity in the audit trail.

### 5.4 Client behavior and acceptance

- Keep server state and workflow state on the API; the browser is not authoritative.
- Use typed request/response contracts from FastAPI OpenAPI.
- Show asynchronous job state, stage, timestamps, progress and actionable errors.
- Keep database credentials and sensitive samples out of browser storage.
- Hide unauthorized actions in the UI and enforce authorization again in the API; dashboard aggregates and exports use the same policy.
- Support expired-session handling, accessible keyboard navigation, responsive desktop layouts, pagination and server-side filtering.
- Use explicit confirmation dialogs for source extraction, export, approval, lock release/override, provisioning, rerun and cleanup.
- A user can complete each of the six named workflows through its own pages and navigate between workflows without losing a saved draft.
- The Dashboard opens by default after authentication/workspace selection, and signed-in identity/workspace controls remain at the right side of the global top bar on every authenticated page.

## 6. FastAPI backend specification

### 6.1 Backend module boundaries

Organize product APIs and screens around the six MVP1 workflows; keep shared implementation modules internal:

- **platform_admin:** Bank SSO claims, workspace membership, the three human roles, scoped/time-bounded user/service identity grants, role-filtered dashboard summaries, recent activity and audit search.
- **connectors:** Workspace-scoped relational/file source and target profiles, credential references, schema/file discovery, connection tests, explicit save/enable and lifecycle.
- **scenario_to_data:** Natural-language requirement, schema mapping, ambiguity resolution, typed query plan, source/synthetic routing, GenAI synthesis, data-set version creation and subset validation.
- **locking_reservations:** Catalogue lookup, request/approval, reservation and exclusive lease/fencing behavior.
- **pii_masking:** PII classifications, masking policies, approvals, masking workers, validation and release gate.
- **provisioning_validation:** Field-mapping/required-field selection, target preflight, data-set version creation, publish/export, reconciliation, validation evidence, rerun, expiry and cleanup.
- **shared infrastructure:** Durable jobs/workers, audit event writing/search, CSV export, lineage/graph projection, secret references and notifications.

Modules communicate through service interfaces and domain events. Keep business rules in backend domain services rather than React components or connector-specific UI code. These modules implement the six approved capabilities and do not add standalone MVP1 product areas.

### 6.2 API conventions

- Use REST under a versioned prefix such as /api/v1.
- Publish OpenAPI documentation from FastAPI and use it as the contract source for the UI.
- Use UUIDs or equivalent opaque identifiers in public APIs; never expose database sequence IDs as security controls.
- Return standard error objects with machine-readable code, message, field details when safe, request identifier, and retryability.
- Use cursor or page-based pagination for catalogue and audit reads.
- Use optimistic concurrency with a version or ETag for model, policy, and scenario edits.
- Use idempotency keys for job creation and provisioning requests.
- Return HTTP 202 with a job identifier for asynchronous work.
- Keep API responses free of credentials, unmasked samples, internal stack traces, and raw SQL.
- Record request correlation IDs across API, worker, graph sync, and connector logs.

### 6.3 MVP1 API resource groups

These APIs back the six user-facing workflows; APIs are an implementation interface, not a seventh MVP1 capability.

| Workflow | Representative API operations |
|---|---|
| Dashboard | Read role-filtered activity, data-set, job, reservation, lock, and source-connection summaries for the active workspace; drill down only to authorized records |
| Platform Access & Administration | Read authenticated user/workspace context; Admin create/update/activate/deactivate workspace; manage workspace membership and three human roles; grant/revoke time-bounded workspace/data-set/reservation/lock access; search recent activities/audit under role visibility; export authorized activity CSV |
| Source Onboarding & Connectors | Create/update/disable workspace relational or file source/target; test connection and access; discover schema/file structure; save/enable validated connection; export authorized inventory metadata |
| Scenario to Data Generation | Submit text requirement; resolve schema mapping; clarify/edit scenario; create/validate query plan; preview capped results; run read-only SQL/JSON extraction or GenAI generation; create a draft data-set version and read its validation/publication state |
| Data Locking & Reservations | Search/inspect approved data sets; request/approve; reserve; acquire/inspect/renew/release exclusive lock; administrator force-release with reason; export approved report |
| PII Data Discovery & Masking | Review classification; configure/approve policy; submit masking run; inspect validation and release state; export authorized findings/coverage report |
| Data Provisioning & Validation | List workspace sources and mappings; select required fields; validate target; submit Source to Data generation/provisioning/rerun/cleanup job; create draft data-set version; publish after validation; retrieve per-version lineage, validation and expiry; download permitted CSV reports |
| Data-set lineage | Read lineage graph/timeline and supporting evidence for a data-set version; export only authorized lineage metadata |
| Shared job/audit/export services | Read authorized job status and audit evidence; create scoped CSV export job and download expiring artifact |

### 6.4 Job lifecycle

Use a controlled state machine:

- **queued**
- **validating**
- **running**
- **succeeded**
- **succeeded_with_warnings**
- **failed**
- **cancel_requested**
- **cancelled**
- **expired**

The job record must include job type, requester or service identity, project, input object versions, target reference, idempotency key, timestamps, stage, progress, result summary, retry count, and sanitized error details. Only valid transitions are allowed. A job cannot report success if required validation failed.

## 7. Connector and processing framework

### 7.1 Connector interface

Implement a connector registry with a common contract for:

- Validate source or target profile.
- Discover schemas, objects, fields, and types.
- Read data using a constrained selection plan.
- Write or export data using an approved target plan.
- Report capabilities, limits, supported data types, and safe batch sizes.
- Close resources and report failures without exposing secrets.

Each connector declares its support for profiling, streaming/batching, transactions, referential constraints, file formats, and target writes. The workflow planner must not assume that every connector supports every operation.

### 7.2 Relational source processing

- Use approved database drivers through a managed connector layer.
- Read only authorized schemas and objects.
- Use parameterized queries and allowlisted object names.
- Apply statement timeouts, row limits, fetch sizes, and source workload controls.
- Use consistent snapshots or source transaction isolation where supported and approved.
- Preserve source key values in a protected processing context only as required for referential consistency.
- Do not log query result values or bind parameters containing customer data.

### 7.3 JSON, CSV, and structured text

- JSON: inspect structure, object/array paths, nesting, optional fields, and data types; preserve paths when exporting.
- CSV and delimited text: configure delimiter, quoting, escape behavior, encoding, header presence, null representation, and line endings.
- Structured text: MVP supports configured delimited records. Fixed-width and free-form document text are later extensions.
- Validate malformed input, schema drift, duplicate headers, unsupported encodings, and records that cannot be parsed.
- Provide record-level error counts and safe line/field references without logging full sensitive records.
- Enforce file-size and decompression limits; reject unsafe archive paths and unsupported compression.

### 7.4 Data processing pipeline

A processing job should be composed from explicit steps:

1. Resolve and validate source, target, schema, and scenario references.
2. Validate actor permissions, approval status, policy, and environment.
3. Resolve immutable model, selection, masking, generation, prompt-template, and model-provider versions.
4. Read source data only when required; synthetic-only workflows must not read production records.
5. Select records and expand relationship dependencies for subsetting workflows.
6. For synthetic workflows, build a privacy-safe generation contract and prompt from schema and approved constraints, then call the approved GenAI endpoint in bounded batches.
7. Apply approved masking for production-derived workflows.
8. Validate schema, required fields, relationships, scenario conditions, prohibited values, and sensitive-data leakage.
9. Write validated output to the approved target or encrypted artifact storage; quarantine rejected batches.
10. Reconcile counts and target state.
11. Record lineage, AI invocation metadata, audit evidence, and cleanup schedule.

Each step must be restartable where safe and idempotent where possible. Partial results remain quarantined and are not catalogued as ready.

## 8. PostgreSQL and pgvector data design

### 8.1 PostgreSQL responsibilities

PostgreSQL stores durable application state and transactionally related metadata. Suggested logical schemas:

- **iam:** user identities, workspace memberships, role grants, object scopes and grant validity periods.
- **tdm:** source profiles, source objects, data models, model versions, entities, fields, relationships.
- **privacy:** classifications, findings, masking policies, masking rules, approvals.
- **dataops:** scenarios, generation plans, data sets, data-set versions, selection plans, jobs, reservations, provisioning records.
- **lineage:** immutable, version-scoped data-set lineage events, parent/child relationships, transformation references, and safe evidence metadata; PostgreSQL is authoritative, with optional graph projection for visual traversal.
- **audit:** append-only audit events and administrative activity.
- **semantic:** text chunks or searchable metadata and vector embeddings.

Use migrations with forward and rollback plans. Add tenant/workspace scope columns to records that are scoped, enforce scope in service queries, and consider PostgreSQL row-level security as defense in depth after the access model is validated.

### 8.2 Core entities

| Entity | Key fields and purpose |
|---|---|
| Workspace | Operating/security scope; owner/admin scope, members, role grants, connection inventory, mappings, policies, retention and status |
| WorkspaceConnection / ConnectionTest | Source or target type, endpoint/secret reference, discovered structure version, test outcome/time, enabled state and lifecycle; credentials remain external |
| WorkspaceFieldMapping | Workspace-scoped mapping from logical/required fields to source and target fields, with version, validation status and owner |
| SourceObject / SourceField | Discovered object metadata, types, labels and source version |
| ProfileRun / ProfileFinding | Scan configuration, metrics, classifier finding, evidence summary and review status |
| DataModel / DataModelVersion | Logical model, immutable version, author, validation status |
| Entity / Attribute / Relationship | Banking or application concepts, field metadata, keys and dependency semantics |
| MaskingPolicy / MaskingRule | Approved transformation intent, field scope, version, approval and effective status |
| ScenarioTemplate / GenerationPlan | Scenario conditions, constraints, volume, generation configuration and version |
| GenerationRun / GenerationBatch / GenerationValidation | Schema and prompt versions, provider/model reference, batch state, usage, validation findings, output hash and artifact reference |
| ScenarioRequest | Original user intent, actor/project, requested outcome, mode preference, limits and lifecycle status; sensitive values are minimized/redacted |
| ScenarioPlan | Normalized scenario conditions, entity/field references, expected states, constraints, relationship traversal, ambiguity/questions, selected schema/model versions and user-approved edits |
| QueryPlan / QueryPlanVersion | Source scope, typed relational or JSON query AST, parameter definitions, connector dialect, allow-list decisions, estimated cost/count, safety validation and approval status |
| QueryRun / QueryResultArtifact | Query-plan version, read-only execution identity, bounded counts, protected preview reference, masking/validation outcomes, job state, lineage and output artifact metadata |
| DataRequest / Approval | Requester, purpose, requested scope, risk context and approval state |
| DataSet / DataSetVersion | Primary managed object and immutable version; workspace, owner, creation pathway (scenario-assisted or Source to Data), selected fields/schema, source/synthetic lineage, privacy and validation state, publication/readiness, expiry and permitted-use scope |
| DataSetLineageEvent | Ordered, version-scoped provenance event with parent/child references, stage/type, source or artifact reference, mapping/scenario/job/policy/validation version, actor/service, timestamp, outcome and safe evidence metadata |
| Job / JobStep | Durable asynchronous execution, stage, attempts, progress, output and sanitized errors |
| Reservation | Data-set version, holder, purpose, validity window, granted access scope and status |
| DataLock | Data-set version, owner identity, workspace, purpose/test-run reference, scope, acquired/expiry timestamps, state, monotonically increasing fencing token and override/release metadata |
| AccessGrant | User/service identity, role, workspace/data-set/reservation/lock scope, valid-from/until, grantor, revocation and audit metadata |
| DataLockScopeKey | Lock ID and keyed HMAC fingerprint of a canonical logical entity key used to detect overlapping allocation scopes without persisting raw business identifiers |
| ProvisioningRun | Target, mapping version, pre/post validation, counts and outcome |
| AuditEvent | Actor and role, workspace, action, target object, result, timestamp, request/job correlation IDs |
| SemanticDocument | Approved non-sensitive text, metadata scope, embedding model/version and vector |

Use immutable version records for published models, policies, scenarios, and data sets. Draft edits create a new draft/version; published versions are not modified in place.

### 8.2.1 Exclusive test-data lock enforcement (MVP1)

Reservations represent planned or scheduled use. A data lock is an exclusive lease that prevents simultaneous conflicting TIGR-mediated use. Lock the immutable data-set version and, where supported, the logical allocation scope. For scoped subsets, derive canonical entity keys and compare keyed HMAC fingerprints; keep the HMAC secret in the approved secret store and rotate it under a documented migration strategy. If stable keys or safe overlap checks are unavailable, apply a whole-version lock.

Acquire and conflict-check inside one PostgreSQL transaction using a per-data-set advisory transaction lock or equivalent serializable strategy. Enforce unique active whole-version claims and reject overlapping scopes before committing. Each successful acquisition receives a monotonically increasing fencing token. Renewals extend the lease only for the owner (or authorized service identity) and only while the lease remains current. Expired locks are reclaimable; release and administrator force-release update state and append audit events. Force-release requires a reason and elevated permission.

The API/domain service checks the lock on every request that could mutate, provision, export, or otherwise claim a conflicting allocation. Workers revalidate immediately before target writes and attach the fencing token to each write operation where the target supports it. A stale token or expired lease causes the job to stop safely and report a conflict. Lock visibility is scoped to authorized project users and exposes metadata only. External manual access to a database is outside TIGR's enforcement boundary.

### 8.3 pgvector use

- Enable pgvector in PostgreSQL.
- Store embeddings only for approved schema descriptions and scenario text used to assist schema mapping in the Scenario to Data Generation workflow.
- Store embedding model identifier, model version, creation timestamp, content hash, workspace scope, and source object reference.
- Apply authorization filters before returning semantic results; vector similarity must never bypass project access.
- Re-embed when approved metadata changes or the embedding model version changes.
- Do not embed customer records, raw files, sensitive values, masked examples, or production-derived free text.
- Use structured scope and permission filters with vector similarity; similarity only proposes schema mappings and never authorizes access or execution.

## 9. Neo4j graph design

### 9.1 Graph purpose and content

Neo4j stores a navigable representation of structural relationships and lineage metadata. It is not the system of record and must not store customer-level data.

Suggested node labels:

- Workspace
- Project
- Source
- SourceObject
- SourceField
- DataModelVersion
- Entity
- Attribute
- Scenario
- MaskingPolicyVersion
- DataSetVersion
- Job

Suggested relationship types:

- CONTAINS
- HAS_FIELD
- MAPS_TO
- HAS_KEY
- REFERENCES
- DEPENDS_ON
- DERIVED_FROM
- PROFILED_BY
- MASKED_BY
- GENERATED_FROM
- PROVISIONED_BY
- VALIDATED_BY

### 9.2 Graph synchronization

- PostgreSQL commits are authoritative. Graph updates are asynchronous projections.
- Publish graph-sync events through a transactional outbox in PostgreSQL.
- Store the source object ID and version on graph nodes for idempotent upsert.
- Track graph-sync status and error details in PostgreSQL.
- Provide a reconciliation/rebuild job that recreates a project graph from authoritative records.
- Apply workspace scope to every graph query.
- Use graph traversal for dependencies, relationship expansion, lineage, and impact analysis; validate operations against PostgreSQL records before execution.

## 10. GenAI and intelligence services

### 10.1 Provider-neutral AI adapter

Create a backend AI adapter for embeddings and generative model calls. Domain services use typed internal operations; only the adapter communicates with model gateways. Store provider, model, deployment, and prompt-template configuration outside business logic. Support timeouts, rate limits, token budgets, transient retries, usage metering, and provider structured-output features where available.

### 10.2 MVP1 — GenAI synthetic data from a supplied schema

GenAI-based synthetic generation is included within the Scenario to Data Generation workflow. TIGR accepts a published relational/JSON schema, a user-provided JSON Schema, or a user-selected set of approved fields together with the scenario requirement; it generates candidate data through an approved model endpoint and validates the output independently before publication. When users provide only fields and a requirement, TIGR must resolve field types and constraints from approved schema metadata or request the missing definitions before generation.

**Generation flow**

1. **Register and normalize the data contract.** Accept user-provided JSON Schema, a published TIGR relational/JSON model, or selected approved fields. Normalize fields, types, required attributes, enumerations, ranges, patterns, keys, relationships, and approved business constraints into a versioned generation contract; do not invent missing schema constraints.
2. **Collect generation intent.** Capture record count, output format, scenario description, entity mix, constraints, and required positive, negative, boundary, or exception cases. Reference an immutable schema/model version.
3. **Build a privacy-safe prompt.** Supply only schema metadata, approved business-rule text, and scenario intent. Never put production samples, customer records, credentials, unmasked field examples, or raw extracts in a model prompt.
4. **Generate bounded batches.** Request structured output conforming to the contract. Use provider-supported schema-constrained output where available. Partition large requests by context/token limits and relationship requirements.
5. **Validate independently.** Validate output against JSON Schema or the relational model. Then run deterministic checks for types, required fields, ranges, formats, uniqueness, key relationships, referential integrity, scenario conditions, prohibited values, and sensitive-data leakage.
6. **Repair or quarantine.** On failure, allow a bounded repair attempt using only schema and error summaries. Do not send rejected sensitive values. After the retry limit, quarantine the batch; do not merge it into a ready data set.
7. **Publish with lineage.** Persist an immutable generation-run record, validation evidence, approved artifact reference, and data-set version. Publication follows approval and provisioning workflows.

**Generation requirements**

- Use GenAI to create varied, scenario-relevant synthetic values; do not simply copy or transform source records.
- Use deterministic generators or approved test-value providers for strict formats and reserved test ranges; combine with model-generated scenario content where appropriate.
- The model must not generate credentials, live payment credentials, or values presented as live financial instruments.
- Allow authorized users to preview schema and validation summaries before publication.
- Record model/provider ID, prompt-template version, schema/model version, generation settings, batch size, retries, usage, latency, validation outcome, and output hash.
- Do not claim exact repeatability for stochastic model output. Record configuration for run comparison; use a seed only when supported by the provider.
- Enforce per-project quotas, output-size and token limits, and cost/usage alerts.

### 10.3 MVP1 — Scenario-to-data planning and source-backed retrieval

The scenario planner accepts a tester's natural-language requirement and produces one typed scenario specification that can drive either existing-source retrieval or synthetic generation. The user selects the target project/application, approved schema/model version, environment, desired data mode (source-backed or synthetic), volume, and target use. The planner returns identified entities and attributes, scenario predicates, expected states, relationship traversal, exclusions, source scope, confidence/ambiguity notes, and clarification questions. User confirmation is required for material assumptions.

**Schema grounding and planning**

- Ground intent only against metadata the actor can access: approved relational tables/columns/keys/relationships and JSON object/array structures, types, and JSON paths. Use Neo4j for confirmed relationships/dependency traversal and PostgreSQL as the authority for schema/model versions, permissions, and plan state.
- Map intent to a connector-neutral logical query plan first. Show the mapped entities/fields and relationship path and allow authorized users to correct mappings. Keep natural-language terms and schema references in the plan for audit.
- Compile a typed relational plan to dialect-specific SQL using a SQL AST/query builder; support joins, nested subqueries and CTEs where connector capabilities permit. Compile nested JSON predicates and projection paths using the source connector's supported JSONPath/query capability. Represent multi-source joins only when the approved model provides stable, permitted join mappings.
- Do not execute SQL or query expressions returned directly by a generative model. The model may propose structured intent/plan fields; deterministic validation and the connector query compiler produce the executable expression. Edits to a query are parsed into the same restricted AST and revalidated.

**Query safety and preview**

- Permit read-only SELECT semantics only. Use read-only source credentials and transactions where available; reject mutation/DDL statements, multiple statements, unapproved tables/fields/functions, unbound literals, and unsupported expressions. Bind all user values as parameters.
- Apply actor/project authorization, schema/table/column allow-lists, row limits, execution timeouts, query-cost limits, connector rate limits, and approved maintenance windows. Provide explain/estimate where supported, with safe fallback estimates otherwise.
- Before execution, display source/environment, schema version, generated SQL or JSON path/query, bound parameter names and masked values, join/subquery explanation, expected record counts, cost/limit warnings, and privacy policy. Require explicit user confirmation for extraction.
- Preview only capped aggregates and policy-protected sample values. Never reveal unmasked sensitive values in the UI, model context, logs, or job errors. Apply approved masking within the controlled extraction pipeline before a derived data set is released.
- Store the immutable query-plan version, actor, approval, source/schema versions, normalized scenario, connector dialect, parameter metadata (not secrets or raw sensitive values), explain/estimate, row counts, execution outcome, masking/validation evidence, and lineage.

**Source-backed and synthetic routing**

After presenting a scenario plan, offer the user two authorized options: retrieve matching existing data or generate synthetic data using the same scenario specification. Source-backed data follows selection, read-only extraction, privacy transformation, relationship/integrity validation, data-set publication, and provisioning. Synthetic mode passes only schema and approved scenario constraints to the GenAI generation flow in Section 10.2. The mode chosen and user decision are audited. Hybrid merging of real and synthetic records is outside MVP1.

**UI and API flow**

1. Create scenario request from natural language and select scope, source/model, mode, volume, and target environment.
2. Return the typed scenario plan and schema matches; request clarification when needed.
3. Preview source/synthetic feasibility and the source query plan when applicable.
4. Let the user review/edit permitted criteria, preview protected sample/aggregate results, and confirm.
5. Submit an asynchronous source extraction or synthetic generation job; require all masking, integrity, validation, approval, lock, and provisioning gates for the selected operation.
6. Return dataset version, job status, warnings, lineage, validation evidence, and lock state.

### 10.4 MVP1 intelligence boundaries

- Use approved schema metadata to help map tester language to relational fields, JSON paths and confirmed relationships in the Scenario to Data Generation workflow.
- GenAI synthetic-data generation is available only as one data-production mode within that workflow.
- AI may propose mappings or values but cannot authorize a source query, approve a mask, acquire access for another user, or publish an unvalidated data set.

### 10.5 Later AI capabilities

- Beyond MVP1: broader autonomous scenario planning, complex multi-step banking journeys, temporal histories, distribution optimization, and recommendations that create or alter data policies.

### 10.6 AI safety and governance

- Treat AI output as untrusted until validation succeeds and an authorized workflow approves publication.
- AI cannot approve source access, activate a masking policy, or publish data by itself.
- Restrict model context to approved schema and policy metadata; apply input filtering and sensitive-data checks.
- Record model, prompt-template, schema, and configuration versions for every run.
- Log invocation metadata, latency, usage, validation status, and reviewer action without logging sensitive prompts or full raw responses.
- Test for prompt injection in schema descriptions and scenario text; treat both as untrusted user content.
- Maintain a non-AI fallback where configured deterministic rules can meet the scenario.
## 11. Security, privacy and audit

### 11.1 Authentication and authorization

- Use the bank’s selected OIDC/OAuth2 identity provider.
- Validate issuer, audience, signature, expiry, and required claims at the API boundary.
- Map external identities and groups to TIGR workspace role grants.
- Enforce least privilege at endpoint and object level.
- Use separate service identities for workers and pipeline clients.
- Require reauthorization when an action is initiated asynchronously; the job retains the requester and approved scope.

MVP1 human authorization is based on exactly three roles, with workspace and object grants constraining each role:

| Role | API-enforced capability boundary |
|---|---|
| Test Data Engineer | Configure/test/save workspace connections; manage Source to Data mappings; create draft data sets by either pathway; review PII findings and masking policies; run masking/validation; publish data sets after gates pass; view own and QA Engineer activity in authorized workspaces. |
| QA Engineer | Read authorized published data sets; create time-bounded reservations; acquire/renew/release own locks; download or integrate authorized data for test execution; view own activity only. |
| Admin | Create/manage workspaces; manage scoped/time-bounded access grants; inspect all user/job activity across TIGR; perform permitted lock/access overrides with reason and audit. |

Application/data-owner approval and privacy-review steps are permissions in the workflow and are assigned to one of these roles; do not add other human role values for MVP1. Every authorization decision evaluates identity, role, active workspace, object scope, grant validity period, data-set publication/privacy state, and requested action. Expired/revoked grants must fail closed.

**Dashboard authorization:** Filter activity rows by the role visibility above before pagination and aggregation. Compute cards using only records the current user can access. Do not return global counts that could reveal the existence or activity of unauthorized users or data sets. Dashboard endpoint policy is shared with audit/activity search and CSV export.

### 11.2 Sensitive data handling

- Encrypt network traffic and persisted data using bank-approved mechanisms.
- Use secret references, not inline credentials.
- Avoid raw values in application logs, traces, exception messages, telemetry, and support exports.
- Restrict raw samples to authorized data engineers and reviewers, with redaction by default.
- Mark production-derived, masked, and synthetic data sets distinctly.
- Treat schemas and scenario descriptions as untrusted input; exclude actual customer values and source extracts from GenAI prompts.
- Quarantine GenAI output until schema, relationship, business-rule, and sensitive-data checks pass.
- Require masking validation before general test-team access when policy requires masking.
- Quarantine failed or partially processed output; clean it up through a controlled workflow.
- Apply retention and deletion policies to source files, intermediates, exports, and job evidence.

### 11.3 Audit controls

Audit events should be append-only to ordinary application roles and include:

- Actor and actor type.
- Project and environment scope.
- Action and target entity/version.
- Timestamp and correlation ID.
- Approval reference where applicable.
- Result, validation state, and reason.
- Before/after metadata for configuration changes, excluding secrets and sensitive values.

## 12. Reliability, operations and observability

- Provide liveness and readiness endpoints for the API and workers.
- Emit structured logs with correlation ID, project ID, job ID, connector type, and stage; do not include raw data.
- Track API latency, job queue age, job duration, throughput, retries, connector failures, graph-sync lag, and cleanup backlog.
- Use bounded retries with backoff for transient errors; do not retry authorization or validation failures indefinitely.
- Support cancellation requests and safe cleanup of temporary resources.
- Add alerts for failed masking, stuck jobs, repeated connector failures, graph projection lag, audit write failures, and expired data not removed.
- Back up PostgreSQL and Neo4j according to the bank’s recovery design; test restore and graph rebuild procedures.
- Define operational targets for availability, recovery, job volume, retention, and processing time during pilot planning.

## 13. Deployment and environments

### 13.1 Deployable units

- Web client static bundle served behind the bank’s approved web tier.
- FastAPI API container/application.
- One or more worker processes with capability-specific queues or concurrency limits.
- PostgreSQL service with pgvector enabled.
- Neo4j service.
- Encrypted artifact storage.
- Identity provider and secret manager integrations.
- Optional approved model endpoint.

Container orchestration and cloud/on-premises placement are deployment decisions to confirm with the bank. The application should externalize configuration and secrets so it can run in approved development, test, staging, and production-like environments.

### 13.2 Environment separation

- Separate credentials, databases, graph instances or scopes, storage prefixes, and model endpoints by environment.
- Prevent source production credentials from being copied into lower environments.
- Promote versioned application configuration and migration artifacts through controlled release processes.
- Use synthetic fixtures for engineering tests.
- Gate deployment on schema migration checks, API contract checks, security scans, and smoke tests.

## 14. Testing strategy for TIGR

- **Unit tests:** domain rules, state transitions, policy validation, selection planning, idempotency.
- **API tests:** authentication, authorization, request validation, pagination, error contracts, job submission.
- **Connector contract tests:** capability declaration, schema discovery, safe query behavior, error handling.
- **Scenario/query-plan tests:** text-intent-to-schema mapping, ambiguity handling, nested SQL joins/subqueries/CTEs, nested JSON paths, query AST validation, read-only enforcement, parameterization, limits, and query-plan approval.
- **File tests:** nested JSON, malformed JSON, CSV quoting, encoding, nulls, oversized files, schema drift.
- **Data integrity tests:** referential integrity, uniqueness, cross-system mapping, business constraints.
- **Privacy tests:** masking consistency, no raw values in logs, access denial, failed-mask quarantine.
- **Graph tests:** projection idempotency, dependency traversal, project-scope filtering, rebuild and reconciliation.
- **Vector tests:** semantic retrieval quality, scope filtering, stale embedding replacement, exclusion of sensitive content.
- **Worker tests:** retries, cancellation, partial failure, duplicate submission, crash recovery.
- **Performance tests:** profile and process representative data volumes; establish baselines before setting service-level targets.
- **End-to-end tests:** register source, profile, model, classify, mask or generate, validate, publish, reserve, and retire.
- **Scenario-to-data end-to-end tests:** natural-language request → schema mapping → relational/JSON query plan → protected preview → masked source pull or synthetic generation → validation → versioned data set.
- **AI evaluation tests:** recommendation quality, unsafe output rejection, reviewer workflow, reproducibility metadata, and non-AI fallback.
- **GenAI synthetic-data tests:** schema-conformance rate, relationship and business-rule pass rate, uniqueness, sensitive-data leakage checks, prompt-injection resistance, repair limits, quarantine, and cost/latency by batch.

## 15. Phased technical implementation

### Phase 0 — Engineering foundation for the six MVP1 workflows

- Establish React/TypeScript/Vite, FastAPI, PostgreSQL/pgvector, Neo4j, migrations, jobs, audit, CSV export and CI.
- Implement Bank SSO, workspace scope, secret references and secure model-gateway configuration.

### Phase 1 — Platform administration and source onboarding

- Deliver workspace creation/lifecycle, workspace membership, the three human roles, scoped/time-bounded grants, role-filtered Dashboard, Recent Activities and audit search.
- Implement workspace-scoped relational and file source/target setup, schema preview, connection tests, explicit save/enable and lifecycle controls.

### Phase 2 — Scenario to Data Generation

- Implement natural-language requirements, schema mapping, query plan review, nested SQL/JSON query compilation and protected previews.
- Route an approved scenario either to bounded source extraction or GenAI synthetic generation; create a draft data-set version, validate it, and preserve scenario/query/run lineage through publication.

### Phase 3 — Locking, PII protection and delivery

- Implement catalogue/request/approval, reservations and exclusive user-specific lock leases with expiry and fencing.
- Implement Source to Data data-set creation from saved workspace sources/mappings and selected required fields.
- Implement PII discovery, masking-policy upload/review, masking workers and publication release gates.
- Implement target provisioning, data-set validation/publication, CSV reporting, reruns, cleanup and expiry.

## 16. MVP1 technical acceptance gates

The MVP1 pilot is ready when:

1. After SSO and workspace selection, Dashboard is the landing page and presents activity, data-set, job, reservation, lock, and source-connection summaries with authorized drill-downs.
2. Dashboard activity follows role rules: Admin sees all activity across TIGR; Test Data Engineer sees self and QA Engineer activities; QA Engineer sees self only. Aggregations, pagination, and exports use the same filters.
3. Bank SSO, three human roles, workspace grants, object-level access, and time-bounded permissions are enforced server-side; expired/revoked access fails closed.
4. Admin can create/manage workspaces. Relational, JSON, CSV and structured delimited-text connections can be configured in a workspace, schema-previewed, tested, then saved/enabled before use.
5. Scenario flow implements text requirement → schema mapping → query development → data generation, with source-backed and GenAI synthetic options, producing a versioned draft data set.
6. Source to Data lets an authorized user select a saved workspace source/mapping and required fields to create a data-set version; field, schema, relationship, privacy, and target checks run before publication.
7. Every successful run is traced to the primary data-set object and immutable version, including workspace, owner, creation pathway, lineage, status, validation evidence, publication, and expiry; each version has an accessible lineage view in the UI.
8. Test Data Engineers can review/upload masking policies, run masking, and publish only after applicable gates pass. QA Engineers can consume only authorized published versions and reserve, lock, download, or integrate within their grants.
9. Reservations and user-specific locks enforce conflicts, expiry, renewal/release, Admin override audit and stale-worker fencing.
10. CSV export is available at authorized review/report points; exports mask or exclude PII, neutralize formula-leading cells, obey row/size limits and produce audit events.
11. No failed, partial, unmasked, invalid, or unpublished data set can be marked ready for QA use.

## 17. Additional tool and service requirements

The named product stack does not cover all runtime and delivery needs. The following supporting capabilities are required; specific products may use bank-approved equivalents:

- Python runtime, dependency lockfiles, SQLAlchemy, Alembic, Pydantic, relational database drivers, Neo4j Python driver, and JSON Schema validation library.
- Durable asynchronous task execution using Celery and a supported message broker, with PostgreSQL job/outbox state.
- Encrypted S3-compatible object storage or an approved file service for uploaded schemas, generated batches, exports, and temporary artifacts.
- OIDC/OAuth2 identity provider, secret manager, encryption/key management, TLS termination, and network policy.
- Approved GenAI and embedding endpoint or model gateway with usage controls and structured-output support where available.
- Centralized logs, metrics, traces, alerting, and audit retention using OpenTelemetry-compatible instrumentation and bank-standard monitoring.
- Container build/runtime, CI/CD, dependency and image vulnerability scanning, infrastructure configuration, and backup/restore automation.
- Test tooling for Python, React, browser end-to-end, API contracts, connector contracts, and GenAI output evaluation.

Tools named in this document are implementation recommendations, not constraints where the bank provides equivalent capabilities.

## 18. Architecture decisions to confirm

The following decisions require bank or implementation-team input before production design is finalized:

- Initial relational database products and supported versions.
- Deployment environment, orchestration platform, network zones, and artifact storage.
- Identity provider, role/group mappings, and service-account controls.
- Approved secret manager and encryption/key management approach.
- Worker queue technology and expected concurrency/volume.
- Initial data retention, residency, audit retention, and approval policies.
- Whether sources may be queried live or must be staged through extracts.
- Supported file-size limits, encodings, compression, and JSON depth.
- Selected embedding and generative model endpoints, if enabled.
- Availability, recovery, throughput, and response-time objectives.

## 19. Glossary

| Term | Meaning |
|---|---|
| Data set | TIGR’s primary managed object: a versioned, governed set of test records/files with owner, workspace, creation pathway, lineage, validation, publication and access state |
| Data model | A versioned logical representation of entities, fields, keys, mappings, and relationships |
| Data lineage | Trace from source and transformation versions to a provisioned data set |
| Masking policy | Approved configuration describing how sensitive fields are transformed |
| Profile run | A scan that reports structural, statistical, relationship, and classification findings |
| Scenario template | A reusable description of required business states and data constraints |
| Workspace | The scoped environment containing users, connections, mappings, policies, jobs, and data sets |
| Worker | Background process that executes long-running TDM jobs |
