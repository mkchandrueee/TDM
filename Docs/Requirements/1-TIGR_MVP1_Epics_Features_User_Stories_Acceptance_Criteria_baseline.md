# TIGR MVP1 — Epics, Features, User Stories, and Acceptance Criteria

**Purpose:** A concise, testable MVP1 backlog derived from the Business Requirements Document (BRD) and aligned to the Technical Specification (TSD).  
**Sources:** `1-TIGR_MVP1_Business_Requirements_baseline.md`; `1-TIGR_MVP1_Technical_Specification_baseline.md`  
**Scope:** The six MVP1 epics only. Later-development epics are out of scope.  
**Product invariant:** A versioned **data set** is TIGR’s primary business object. **Scenario to Data** and **Source to Data** create versions that use common lineage, privacy, validation, publication, and consumption controls.

## 1. Roles and conventions

- **Test Data Engineer (TDE):** Configures workspace connections, creates datasets, reviews PII, applies masking, validates and publishes ready versions.
- **QA Engineer (QA):** Finds authorized published datasets, requests/reserves/locks them, and downloads or integrates authorized data for testing.
- **Admin:** Creates and manages workspaces, grants time-bounded access, sees all TIGR activities, and performs permitted audited overrides.
- **Service identity:** A non-human principal with explicit scope. It is not a fourth human role.

Acceptance criteria below are brief outcome checks. API authorization, asynchronous job safety, persistence, audit, privacy and operational controls remain governed by the TSD. “Authorized” always means access is verified server-side for the actor, workspace, object, action, and validity period.

## 2. Backlog

## Epic 1 — Platform Access & Administration

**Outcome:** Bank users access TIGR through SSO, work in authorized workspaces, and see role-appropriate activity and operational summaries.

### Feature 1.1 — SSO, user identity, and role context

#### TIGR-E1-F1-US01 — Sign in through bank SSO

**Story:** As a TIGR user, I want to sign in through bank SSO so that TIGR uses my enterprise identity.

**Acceptance criteria:**
- Given valid identity-provider authentication, when I open TIGR, then I reach the authenticated application with my verified identity.
- Given an invalid, expired, or revoked identity, when I access TIGR, then access is denied and no protected data is returned.
- Given a signed-in user, then role and workspace scope are resolved server-side, not trusted from client-supplied values.

#### TIGR-E1-F1-US02 — Use the three MVP1 human roles

**Story:** As an Admin, I want users to have explicit TDE, QA, or Admin role scope so that capabilities match their responsibilities.

**Acceptance criteria:**
- Given a user, when an Admin assigns access, then the role is one of Test Data Engineer, QA Engineer, or Admin and is scoped as defined by the access model.
- Given a service identity, then it has explicit technical scopes and is not assigned a human role.
- Given an unauthorized role/action combination, when the API is called directly, then the operation is denied.

### Feature 1.2 — Workspace lifecycle and membership

#### TIGR-E1-F2-US01 — Create a workspace

**Story:** As an Admin, I want to create a workspace with its purpose and initial access so that teams have a controlled environment for connections and datasets.

**Acceptance criteria:**
- Given an Admin, when they submit a valid workspace name, purpose, status, and initial grants, then the workspace is created and audited.
- Given a duplicate name within the configured naming scope or invalid grant, then creation is rejected with an actionable, non-sensitive message.
- Given a non-Admin, when they attempt workspace creation, then the request is denied.

#### TIGR-E1-F2-US02 — Manage workspace lifecycle and membership

**Story:** As an Admin, I want to edit, activate, deactivate, and manage workspace membership so that access reflects current team needs.

**Acceptance criteria:**
- Given an Admin, when they update workspace metadata or membership, then the change is persisted with actor, time, outcome, and audit evidence.
- Given an inactive workspace, then new workflows cannot use its connections or create consumable datasets.
- Given a user without workspace access, then workspace records and counts are not exposed.

### Feature 1.3 — Scoped, time-bounded access

#### TIGR-E1-F3-US01 — Grant and revoke access

**Story:** As an Admin, I want to grant or revoke user and service access to workspaces, datasets, reservations, and locks so that permissions are limited to the required scope.

**Acceptance criteria:**
- Given an Admin with grant authority, when they create a grant, then its principal, scope, permissions, validity period, and grantor are recorded.
- Given a revoked or expired grant, when the principal requests the protected action, then access fails closed in both APIs and background jobs.
- Given a grant change, then the change is auditable and does not silently broaden other scopes.

#### TIGR-E1-F3-US02 — Enforce grant validity during asynchronous work

**Story:** As a data owner, I want jobs to re-check access at execution and release time so that queued work cannot outlive authorization.

**Acceptance criteria:**
- Given a job submitted under a valid grant, when the grant expires or is revoked before execution or release, then the job is blocked or safely stopped.
- Given a blocked job, then status and a sanitized reason are visible and the event is audited.

### Feature 1.4 — Role-aware Dashboard

#### TIGR-E1-F4-US01 — View scoped Dashboard summaries

**Story:** As a user, I want a Dashboard after sign-in and workspace selection so that I can see my permitted activity and work at a glance.

**Acceptance criteria:**
- Given an authorized workspace, then the Dashboard shows recent activities, datasets, jobs by status, reservations, locks, and configured source count.
- Given a role, then cards and drill-downs include only authorized records; counts do not reveal inaccessible objects.
- Given no accessible records, then the Dashboard displays an empty state rather than an error or unauthorized count.

#### TIGR-E1-F4-US02 — Apply role-specific activity visibility

**Story:** As a user, I want the Dashboard activity feed to follow my role’s visibility rules so that activity data is appropriately scoped.

**Acceptance criteria:**
- Given an Admin, then activity visibility covers all TIGR users and jobs.
- Given a TDE, then visibility covers the TDE’s own activity and QA activity within authorized workspaces.
- Given a QA Engineer, then visibility covers only that QA Engineer’s activity.
- Given pagination, search, aggregation, or export, then the same visibility filter is applied before results are calculated.

### Feature 1.5 — Recent Activities, audit search, and export

#### TIGR-E1-F5-US01 — Search activity and audit records

**Story:** As an authorized user, I want to search recent activities and audit records so that I can investigate actions and outcomes.

**Acceptance criteria:**
- Given an authorized search, then results include actor, role, workspace, action, object, outcome, time, and correlation reference where available.
- Given a search beyond the caller’s role/workspace scope, then inaccessible records are excluded before filtering and pagination.
- Given a record, then secrets and raw unmasked PII are not exposed in its details.

#### TIGR-E1-F5-US02 — Export authorized activity results

**Story:** As an authorized user, I want to export activity and audit results to CSV so that I can review or share permitted evidence.

**Acceptance criteria:**
- Given a permitted export, then the CSV contains only authorized columns and rows and is recorded as an audit event.
- Given spreadsheet-formula-leading content, then it is neutralized; configured row and file-size limits are enforced.
- Given a user outside the record scope, then export is denied or returns no unauthorized rows.

## Epic 2 — Source Onboarding & Connectors

**Outcome:** TDEs register approved source and target connections, inspect structure, validate access, and enable connections for workflows.

### Feature 2.1 — Configure workspace source and target connections

#### TIGR-E2-F1-US01 — Register a supported source or target

**Story:** As a TDE, I want to configure a relational or supported file-based source/target in a workspace so that TIGR can discover and process approved data.

**Acceptance criteria:**
- Given an authorized TDE, then they can register relational database, JSON, CSV, and structured delimited-text endpoints supported by the connector.
- Given a connection, then workspace, owner, environment, purpose, permitted operations, and lifecycle status are recorded.
- Given an unsupported format or operation, then configuration is rejected with a safe explanation.

#### TIGR-E2-F1-US02 — Reference connection secrets securely

**Story:** As a TDE, I want to select a secret reference rather than enter credentials into TIGR records so that connection credentials remain protected.

**Acceptance criteria:**
- Given connection setup, then TIGR persists only a secret-manager reference and safe connection metadata.
- Given a connection display, export, log, or error, then credentials, tokens, and secret values are never exposed.
- Given an unavailable or unauthorized secret reference, then the connection cannot be enabled.

### Feature 2.2 — Discover and preview schema or file structure

#### TIGR-E2-F2-US01 — Preview discovered structure

**Story:** As a TDE, I want to preview tables, fields, keys, and file structure so that I can select valid mappings and required fields.

**Acceptance criteria:**
- Given a readable source, then TIGR displays discovered objects and field metadata with a versioned snapshot.
- Given nested JSON, then the preview represents nested objects, arrays, and field paths.
- Given a preview, then it contains no raw sensitive sample values unless separately authorized and protected; schema metadata remains traceable to its connection and snapshot.

### Feature 2.3 — Test, save, and manage the connection lifecycle

#### TIGR-E2-F3-US01 — Test connection and access

**Story:** As a TDE, I want to test connectivity and permitted access before saving a connection so that invalid connections cannot be used in workflows.

**Acceptance criteria:**
- Given configured connection details, when I run a test, then TIGR records the outcome, time, actor, and safe error reference.
- Given a failed test, then the connection remains unavailable and the user receives a non-sensitive actionable message.
- Given a successful test, then the connection is not enabled until explicitly saved/enabled.

#### TIGR-E2-F3-US02 — Enable, disable, or update a connection

**Story:** As a TDE, I want to save or disable a tested connection so that workflow use follows its current approved lifecycle.

**Acceptance criteria:**
- Given a successfully tested connection, when I explicitly save/enable it, then authorized workflows may select it.
- Given a connection configuration change, then its previous test is invalidated until retested.
- Given a disabled connection, then new jobs cannot use it; prior jobs retain historical connection/snapshot references.

### Feature 2.4 — Export source inventory and metadata summary

#### TIGR-E2-F4-US01 — Export authorized connection inventory

**Story:** As an authorized user, I want to export source/target inventory and discovered metadata summaries so that I can review workspace connectivity.

**Acceptance criteria:**
- Given export permission, then the CSV includes only authorized connections and safe metadata.
- Given secret references or sensitive fields, then secrets and raw values are omitted or masked.
- Given an export, then it is size-limited and audited.

## Epic 3 — Scenario to Data Generation

**Outcome:** TDEs transform a natural-language test requirement into a reviewed source-backed or synthetic data set with validation and lineage.

### Feature 3.1 — Capture a scenario and resolve ambiguity

#### TIGR-E3-F1-US01 — Submit a natural-language requirement

**Story:** As a TDE, I want to describe test conditions, expected states, volume, exclusions, and target use in natural language so that TIGR can help plan a dataset.

**Acceptance criteria:**
- Given an authorized workspace, then I can submit and save a scenario request with its workspace and requested use.
- Given missing material intent, then TIGR asks for clarification rather than silently assuming a value.
- Given sensitive content in the requirement, then it is handled according to approved privacy and retention controls and is not placed in unsafe logs or prompts.

### Feature 3.2 — Map scenario to approved schemas and relationships

#### TIGR-E3-F2-US01 — Review and correct schema mapping

**Story:** As a TDE, I want AI-assisted schema and field mapping suggestions grounded in approved metadata so that the scenario uses the correct data model.

**Acceptance criteria:**
- Given an authorized scenario and schema snapshot, then TIGR proposes relevant entities, fields, keys, and relationships with traceable schema versions.
- Given a proposed mapping, then the TDE can review and correct it before query or generation.
- Given material ambiguity or unsupported mapping, then TIGR requests clarification or blocks progression; it does not invent schema facts.

### Feature 3.3 — Build and approve a safe source query plan

#### TIGR-E3-F3-US01 — Review a connector-neutral query plan

**Story:** As a TDE, I want to review the logical source plan and relationship path so that I can verify which approved data is selected.

**Acceptance criteria:**
- Given a source-backed scenario, then TIGR displays source/environment, schema version, fields, joins/paths, criteria, limits, and estimated matches where supported.
- Given query planning, then executable plans are built from validated typed intent and approved schema metadata; model-generated SQL is not executed directly.
- Given a query plan, then the TDE can correct permitted criteria and must confirm before extraction.

#### TIGR-E3-F3-US02 — Preview protected results and execute read-only queries

**Story:** As a TDE, I want capped, privacy-protected previews and safe query execution so that I can confirm result suitability without exposing data.

**Acceptance criteria:**
- Given relational/JSON sources, then only connector-supported read-only, parameterized queries over authorized schemas/fields are executed.
- Given a preview, then result count/limits and protected sample or aggregate output are shown; raw/unmasked values are not exported.
- Given an invalid, unsafe, over-limit, or unauthorized plan, then it is rejected before source execution and the failure is safely reported.

#### TIGR-E3-F3-US03 — Select relationship-complete subsets

**Story:** As a TDE, I want TIGR to preserve approved keys and relationships when selecting subsets so that the resulting test data remains coherent.

**Acceptance criteria:**
- Given a selected subset, then required dependent records are included or the validation reports the missing relationship.
- Given unsupported or ambiguous relationships, then the system identifies the limitation before publishing.
- Given extraction completion, then selected source/schema/mapping/query references are retained in lineage.

### Feature 3.4 — Choose source-backed or GenAI synthetic generation

#### TIGR-E3-F4-US01 — Route a reviewed scenario to a generation mode

**Story:** As a TDE, I want to choose an approved source-backed or synthetic mode using the same scenario specification so that the mode is explicit and auditable.

**Acceptance criteria:**
- Given a reviewed plan, then the user can select a permitted source-backed or synthetic path.
- Given synthetic generation, then only schema metadata and approved scenario constraints are passed to the model; production records, secrets, and unmasked samples are excluded.
- Given the selected mode, then the job and dataset version record that mode and its immutable input references.

#### TIGR-E3-F4-US02 — Generate synthetic data from a schema

**Story:** As a TDE, I want synthetic records generated from an approved schema and scenario so that test conditions can be covered without copying production records.

**Acceptance criteria:**
- Given a schema contract and scenario, then generation produces bounded structured batches using an approved model/provider configuration.
- Given missing field types or constraints, then TIGR requests definitions or uses only approved schema metadata; it does not invent constraints.
- Given generated output, then it is independently validated for schema, fields, relationships, scenario rules, prohibited values, and sensitive-data leakage before release.
- Given validation failure, then the batch is retried only within configured limits or quarantined; failed output is not marked ready.

### Feature 3.5 — Create a versioned dataset, validate, publish, and inspect lineage

#### TIGR-E3-F5-US01 — Track scenario runs as dataset versions

**Story:** As a TDE, I want each successful scenario run represented as a version under a dataset so that results, evidence, and reruns remain traceable.

**Acceptance criteria:**
- Given a completed successful run, then TIGR creates or updates the dataset record with a new immutable version, owner, workspace, pathway, status, and artifact reference.
- Given a failed or partial run, then it remains non-ready/quarantined and cannot appear as consumable.
- Given a rerun or change to the mapping/scenario, then prior versions and evidence remain unchanged.

#### TIGR-E3-F5-US02 — View per-version validation and lineage

**Story:** As a TDE or authorized QA Engineer, I want to inspect the lineage and validation for a dataset version so that I can understand its origin and release state.

**Acceptance criteria:**
- Given any dataset version, then its ordered lineage shows applicable source/schema/mapping/scenario/query/model/job/masking/validation/publication/delivery steps.
- Given a lineage step, then authorized users can inspect safe evidence and version references; sensitive values and secrets are excluded.
- Given PostgreSQL as system of record, then the graph projection (if enabled) does not become the only copy of lineage evidence.

#### TIGR-E3-F5-US03 — Publish only a ready version

**Story:** As a TDE, I want to publish a validated version explicitly so that only approved, usable data appears in the catalogue.

**Acceptance criteria:**
- Given required integrity, privacy, and validation checks pass, then an authorized TDE can publish the version and the decision is audited.
- Given failed, partial, unmasked-when-required, or invalid data, then publication is blocked.
- Given a published version, then authorized users can discover it from the common catalogue; unpublished versions remain unavailable to QA consumption.

### Feature 3.6 — Export protected scenario and validation reports

#### TIGR-E3-F6-US01 — Export authorized query and scenario summaries

**Story:** As an authorized user, I want scenario, query-plan, protected-preview, and validation summaries in CSV so that I can review evidence outside TIGR.

**Acceptance criteria:**
- Given a permitted export, then it contains only authorized summary fields and capped/protected preview data.
- Given raw or unmasked sensitive values, then they are excluded from the CSV.
- Given an export, then it is size-limited, formula-neutralized, and audited.

## Epic 4 — Data Locking & Reservations

**Outcome:** QA users discover authorized published datasets, reserve planned use, lock versions during execution, and release or integrate them safely.

### Feature 4.1 — Browse published dataset catalogue

#### TIGR-E4-F1-US01 — Find an authorized published version

**Story:** As a QA Engineer, I want to search the common catalogue for published datasets available to me so that I can select data for testing.

**Acceptance criteria:**
- Given a QA Engineer, then only published, unexpired versions with effective access are returned.
- Given a dataset row, then version, fields, validation state, availability, and safe lineage are visible subject to access.
- Given an unpublished or unauthorized version, then it is omitted from results and counts.

### Feature 4.2 — Request and approve data use

#### TIGR-E4-F2-US01 — Submit a data request

**Story:** As a QA Engineer, I want to request a dataset for a planned test window so that configured approvals and access checks can be completed.

**Acceptance criteria:**
- Given a selected dataset/version, then the request records purpose, requested time window, requester, and requested version.
- Given a configured approval path, then the request is routed to an authorized approver and status is visible.
- Given a denied or pending request, then reservation/lock use is blocked as required by policy.

#### TIGR-E4-F2-US02 — Record an approval decision

**Story:** As an authorized approver, I want to approve or deny a data request so that use follows workspace governance.

**Acceptance criteria:**
- Given an authorized approver, then the decision, time, scope, and permitted comment/evidence are recorded.
- Given an unauthorized approver or invalid request state, then the decision is rejected.
- Given an approved request, then only the requested authorized dataset/version and time scope are enabled.

### Feature 4.3 — Reserve a version for future use

#### TIGR-E4-F3-US01 — Create, inspect, and release a reservation

**Story:** As a QA Engineer, I want to reserve a published version for a planned time window so that its availability is visible before test execution.

**Acceptance criteria:**
- Given valid access and time window, then the reservation is created against the exact dataset version.
- Given a reservation, then owner, start/end, status, and related request are visible to authorized users.
- Given an invalid window, expired grant, or policy conflict, then the reservation is rejected with a safe reason.
- Given release or expiry, then reservation status is updated and audited.

### Feature 4.4 — Acquire and manage exclusive locks

#### TIGR-E4-F4-US01 — Acquire, inspect, renew, and release a lock

**Story:** As a QA Engineer, I want an exclusive lock while using a version so that conflicting TIGR-mediated use is prevented.

**Acceptance criteria:**
- Given an available authorized version and valid reservation/policy, then the QA Engineer can acquire one active lock.
- Given an active lock by another user, then acquisition fails and the current holder/expiry is disclosed only as permitted.
- Given a lock holder, then they can inspect, renew within policy, or release their lock; each transition is audited.

#### TIGR-E4-F4-US02 — Expire leases and fence stale workers

**Story:** As a platform operator, I want expired locks to stop authorizing stale work so that abandoned sessions cannot continue using data.

**Acceptance criteria:**
- Given an expired lease, then it no longer grants use and can be reclaimed under concurrency control.
- Given a stale worker with an old fencing token, then its write/use operation is rejected.
- Given lock expiry or conflict, then the event is visible in authorized activity and job status.

#### TIGR-E4-F4-US03 — Perform an audited Admin override

**Story:** As an Admin, I want to force-release a lock when permitted so that an operational conflict can be resolved with evidence.

**Acceptance criteria:**
- Given an Admin with override authority and a reason, then the lock is overridden/released and the actor, time, reason, and prior holder are audited.
- Given no reason or insufficient authority, then override is rejected.
- Given the override, then stale lock holders/workers cannot retain access through the old lease.

### Feature 4.5 — Download or integrate dataset with automation

#### TIGR-E4-F5-US01 — Download authorized data for testing

**Story:** As a QA Engineer, I want to download an authorized dataset artifact so that I can use it in a test run.

**Acceptance criteria:**
- Given a published version, valid grant, and required reservation/lock state, then an authorized download is issued through a controlled, short-lived mechanism.
- Given expired access, failed privacy status, or an invalid lock, then download is denied.
- Given a download, then the action, artifact/version, actor, result, and time are audited; the storage key is not exposed.

#### TIGR-E4-F5-US02 — Integrate data with an automation suite

**Story:** As a QA Engineer, I want to use an approved export/integration format so that dataset delivery can support an automation test suite.

**Acceptance criteria:**
- Given an authorized target/format, then TIGR delivers only the selected published version under configured masking and access controls.
- Given failed target preflight or access checks, then delivery is blocked and the job reports a safe failure.
- Given a completed or partial delivery, then outcome, counts, lineage, and audit evidence are available.

### Feature 4.6 — Export catalogue, reservation, and lock reports

#### TIGR-E4-F6-US01 — Export authorized usage reports

**Story:** As an authorized user, I want catalogue, reservation, and lock reports so that I can coordinate test-data use.

**Acceptance criteria:**
- Given report access, then only in-scope published datasets and usage records are included.
- Given sensitive identifiers, then they are omitted or masked according to policy.
- Given the report, then CSV limits, formula neutralization, and export audit are applied.

## Epic 5 — PII Data Discovery & Masking

**Outcome:** TDEs identify and review sensitive fields, manage masking policies, apply approved transformations, and block release when privacy checks fail.

### Feature 5.1 — Discover, classify, and review sensitive fields

#### TIGR-E5-F1-US01 — Run field classification

**Story:** As a TDE, I want to discover likely PII and sensitive fields in approved schemas so that required protection can be planned.

**Acceptance criteria:**
- Given an authorized schema snapshot, then TIGR produces field-level classification findings with method/confidence and schema version.
- Given discovery, then raw customer values are not stored in findings, logs, lineage, or exports.
- Given a schema change, then findings remain linked to the snapshot and can be reviewed for the new version.

#### TIGR-E5-F1-US02 — Review or correct classifications

**Story:** As a TDE, I want to review and correct proposed classifications so that masking requirements reflect the data owner’s decision.

**Acceptance criteria:**
- Given a finding, then an authorized TDE can accept, reject, or correct the classification with actor and time recorded.
- Given a review, then its evidence and reason are retained for audit.
- Given an unreviewed field requiring protection, then release remains blocked when policy requires review.

### Feature 5.2 — Create, upload, review, and approve policies

#### TIGR-E5-F2-US01 — Upload or define a masking policy

**Story:** As a TDE, I want to upload or create a field-level masking policy so that transformation rules can be applied consistently.

**Acceptance criteria:**
- Given an authorized workspace, then the policy is stored as a versioned configuration or protected artifact reference.
- Given invalid or unsupported policy content, then it is rejected without enabling enforcement.
- Given a saved policy, then its owner, version, scope, status, and uploaded file reference are visible to authorized reviewers.

#### TIGR-E5-F2-US02 — Preview policy coverage and request approval

**Story:** As a TDE, I want to preview policy coverage and route changes for review so that gaps are known before enforcement.

**Acceptance criteria:**
- Given a policy and schema, then TIGR shows covered/uncovered fields and safe findings before a masking run.
- Given a policy change, then approval status and reviewer evidence are recorded before use when required.
- Given an unapproved policy, then it cannot be enforced on a release-bound dataset.

### Feature 5.3 — Apply consistent masking methods

#### TIGR-E5-F3-US01 — Apply an approved policy to a dataset version

**Story:** As a TDE, I want to mask a dataset using approved field rules so that protected data is safe for test consumption.

**Acceptance criteria:**
- Given an approved policy version, then authorized masking applies configured tokenization, substitution, redaction, date shifting, or numeric transformation.
- Given related fields or records that require consistent substitution, then the configured consistency group is preserved.
- Given a masking run, then its dataset version, policy version, job, outcome, and safe counts are traceable; raw values are not logged.

### Feature 5.4 — Validate masking and enforce release gates

#### TIGR-E5-F4-US01 — Validate masking before release

**Story:** As a TDE, I want masking and privacy validation results so that unsafe data cannot be published.

**Acceptance criteria:**
- Given a masking run, then validation checks applicable coverage and leakage requirements and records field-level safe results.
- Given failed or incomplete validation, then publication and QA availability are blocked.
- Given successful required checks, then the dataset can progress to authorized review/publication.

### Feature 5.5 — Export PII and masking summaries

#### TIGR-E5-F5-US01 — Export authorized privacy reports

**Story:** As an authorized TDE or Admin, I want classification, policy-coverage, and masking-validation summaries so that I can review control status.

**Acceptance criteria:**
- Given export permission, then the report includes authorized findings and aggregate evidence only.
- Given original PII or raw samples, then they are never included in the report.
- Given an export, then it is access-controlled, formula-neutralized, size-limited, and audited.

## Epic 6 — Data Provisioning & Validation

**Outcome:** TDEs create data using the Source to Data workflow, validate delivery to controlled targets, and publish ready versions with complete lineage.

### Feature 6.1 — Select source, mapping, required fields, criteria, and target

#### TIGR-E6-F1-US01 — Start a Source to Data workflow

**Story:** As a TDE, I want to select an approved workspace source and saved mapping so that I can define a dataset from trusted metadata.

**Acceptance criteria:**
- Given an active workspace, then only tested/enabled sources and approved saved mapping versions are selectable.
- Given a mapping from another workspace, disabled connection, or stale schema version, then it is rejected or requires an explicit remap.
- Given selections, then the dataset version retains exact source, schema snapshot, and mapping version references.

#### TIGR-E6-F1-US02 — Choose required fields and criteria

**Story:** As a TDE, I want to select required fields and permitted criteria/volume so that the output meets the test need.

**Acceptance criteria:**
- Given a selected mapping, then available source fields and target fields are shown with required/mapped status.
- Given an omitted required field or incompatible mapping, then submission is blocked with field-level guidance.
- Given selection criteria, then values are parameterized, bounded, and validated against permissions and connector capabilities.

#### TIGR-E6-F1-US03 — Choose an approved target and output format

**Story:** As a TDE, I want to select an approved database or file target and output format so that the dataset is delivered to the required test environment.

**Acceptance criteria:**
- Given an enabled target connection, then only supported and authorized formats/operations are offered.
- Given a target, then credentials remain in the secret manager and the chosen target is retained in the job and lineage.
- Given an untested, disabled, or unauthorized target, then preflight/submission is denied.

### Feature 6.2 — Generate/provision with safe asynchronous jobs

#### TIGR-E6-F2-US01 — Submit and monitor a provisioning job

**Story:** As a TDE, I want to submit an asynchronous Source to Data job and see its progress so that larger operations do not block the application.

**Acceptance criteria:**
- Given validated inputs and permission, then TIGR creates a durable job and returns its identifier/status.
- Given a duplicate retry with the same idempotency key, then TIGR does not create unintended duplicate output.
- Given job steps, then status, safe progress, retry outcome, and correlation reference are visible without exposing secrets/PII.
- Given partial/failing output, then it is quarantined or marked failed and not catalogued as ready.

#### TIGR-E6-F2-US02 — Re-run a controlled job

**Story:** As a TDE, I want to rerun a failed or changed workflow under current controls so that I can correct the dataset safely.

**Acceptance criteria:**
- Given a rerun, then current authorization, connection, mapping, policy, and validation requirements are checked again.
- Given a successful rerun, then a new dataset version and job lineage are created; previous evidence is retained.
- Given a retry limit or non-retryable error, then no unsafe repeated execution occurs and the user receives a safe status.

### Feature 6.3 — Validate source fields, dataset, and target

#### TIGR-E6-F3-US01 — Run preflight and post-provision validations

**Story:** As a TDE, I want validation of connection, target schema, counts, required fields, constraints, relationships, and privacy so that only complete data is released.

**Acceptance criteria:**
- Given submission, then target connectivity/schema preflight runs before writing.
- Given output, then selected/required fields, data types, constraints, counts, relationships, and applicable privacy checks are validated.
- Given failed checks, then failures are associated with the job/version and block publication.
- Given successful checks, then evidence and summary are retained and visible to authorized reviewers.

### Feature 6.4 — Publish validated version and inspect lineage

#### TIGR-E6-F4-US01 — Publish a ready provisioned version

**Story:** As a TDE, I want to confirm readiness and publish a validated version so that authorized QA users can consume it.

**Acceptance criteria:**
- Given all required checks pass, then a TDE can explicitly publish and the publication actor/time/readiness state is recorded.
- Given invalid, partial, unmasked-when-required, or failed output, then publish is blocked.
- Given publication, then only authorized users can see the version in the common catalogue and Dashboard.

#### TIGR-E6-F4-US02 — Inspect complete Source to Data lineage

**Story:** As a TDE or authorized QA Engineer, I want to view the dataset’s upstream and downstream lineage so that I can verify its provenance and processing.

**Acceptance criteria:**
- Given a dataset version, then lineage shows source connection/object/schema, mapping, selected fields, criteria/query, job, masking, validation, publication, and target/download events as applicable.
- Given each step, then evidence is ordered, version-bound, access-controlled, and free of raw sensitive values.
- Given a missing graph projection, then authoritative PostgreSQL lineage remains available; graph failure does not erase committed lineage.

### Feature 6.5 — Cleanup, expiry, and provisioning reports

#### TIGR-E6-F5-US01 — Expire and clean up provisioned data

**Story:** As an authorized TDE or Admin, I want datasets and target artifacts to expire and be cleaned up under policy so that stale test data is not retained indefinitely.

**Acceptance criteria:**
- Given an expiry date or cleanup instruction, then an authorized cleanup job is scheduled and tracked.
- Given cleanup failure or partial completion, then the status and safe evidence remain visible and the artifact is not falsely marked deleted.
- Given successful cleanup, then completion time/evidence is retained and lineage/audit records remain.

#### TIGR-E6-F5-US02 — Export provisioning and validation reports

**Story:** As an authorized user, I want provisioning summaries, validation results, and job reports in CSV so that I can review delivery evidence.

**Acceptance criteria:**
- Given export permission, then report rows are limited to authorized jobs, dataset versions, and fields.
- Given sensitive data, then only masked or aggregate evidence is exported.
- Given a CSV export, then formula-leading cells are neutralized, size limits apply, and the export is audited.

## 3. Cross-epic acceptance rules

These rules apply to all six epics and do not add a seventh user-facing epic.

1. **Workspace scope:** Every action and query is constrained to workspaces and objects the caller can access; cross-workspace references are rejected.
2. **Dataset lifecycle:** Jobs and artifacts are attached to a dataset version; a failed or partial job never creates a consumable ready version.
3. **Versioning:** Published versions and the schema, mapping, policy, scenario, query, and model references used by them are immutable and traceable.
4. **Lineage:** Every dataset version has a user-visible, ordered lineage view; PostgreSQL is authoritative and Neo4j, if enabled, is a rebuildable projection.
5. **Privacy:** Secrets and unmasked PII are excluded from logs, telemetry, model prompts, ordinary exports, and lineage metadata. Protected artifacts are accessed only through authorization checks.
6. **Jobs:** Long-running work is asynchronous, durable, observable, idempotent where applicable, and safely retryable; partial results are quarantined.
7. **Audit:** Access grants, connection tests, mapping changes, generation, masking, validation, publication, reservations, locks, downloads, overrides, exports, and cleanup record actor, action, time, outcome, and correlation reference.
8. **CSV safety:** Every export uses the same authorization scope as the UI/API, masks or omits sensitive values, neutralizes formula-leading cells, applies size limits, and creates an audit record.
9. **Navigation:** The authenticated Dashboard is the landing page; direct links and subpages enforce the same permissions as primary navigation.
10. **No MVP scope expansion:** Do not implement later-development capabilities as MVP1 acceptance requirements.

## 4. Traceability to source documents

| Backlog area | BRD source | TSD alignment |
|---|---|---|
| Roles, workspaces, Dashboard, audit, grants | Epic 1; Sections 5, 6, 8 | Web pages 5.2; API groups 6.3; authorization/audit 11 |
| Connections and schema discovery | Epic 2 | Web pages 5.2; connectors 7; API groups 6.3 |
| Scenario to Data | Epic 3 | Scenario workflow 5.2; API groups 6.3; processing/AI 7 and 10 |
| Reservations, locks, download | Epic 4 | Web pages 5.2; API groups 6.3; locking, security, and audit 8 and 11 |
| PII discovery and masking | Epic 5 | Web pages 5.2; API groups 6.3; security/privacy 11 |
| Source to Data, provisioning, validation, lineage, expiry | Epic 6 | Web pages 5.2; API groups 6.3; processing 7.4; lineage 9; acceptance 16 |
| Cross-cutting technical gates | BRD Sections 7–8 | TSD Sections 6–16, especially security, reliability, testing, and MVP1 acceptance |

## 5. Notes for backlog grooming

- Preserve the source requirement language when decomposing into sprint tasks; do not treat this document as a replacement for BRD/TSD details.
- Product decisions still requiring confirmation—such as approval routing, reservation overlap policy, retention periods, and exact connector versions—should become explicit configuration or decision records, not hidden assumptions.
- Convert each story’s criteria into automated API, authorization, job, data-integrity, privacy, and UI tests where applicable.
