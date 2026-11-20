# TIGR — Banking Test Data Management Solution
## Business Requirements Document

**Solution name:** TIGR — Test-data Intelligence, Generation & Readiness  
**Tagline:** AI-powered data, ready for every test.  
**Document status:** Draft for product and architecture review  
**Product scope:** An AI-enabled banking platform for discovering, protecting, generating, managing, and provisioning test data  
**Delivery approach:** MVP followed by capability expansion

---

## 1. Purpose

This document defines business requirements for TIGR, a Test Data Management (TDM) solution for banking application delivery and testing. It converts the agreed epic roadmap into functional requirements, operating expectations, controls, and acceptance outcomes.

The solution will help testing, engineering, data, and security teams obtain reliable, privacy-protected data for functional, integration, regression, performance, migration, and AI-enabled application testing.

## 2. Business problem

Banking test teams often depend on manual data searches, extracts, masking, scripts, and approvals. Data may be incomplete, stale, inconsistent across connected systems, or difficult to obtain within a sprint. Production-derived information also creates privacy and access risks when handling is not controlled.

The solution will provide a controlled, reusable service that supports both production-derived and synthetic data, preserves relationships across banking systems, and makes approved data available through repeatable workflows.

## 3. Objectives

- Reduce the time required to find, prepare, approve, and provision test data.
- Protect sensitive customer and financial information throughout the data lifecycle.
- Preserve referential integrity and business validity across connected applications.
- Improve scenario coverage, including boundary, negative, and exception cases.
- Enable controlled self-service and automation for delivery teams.
- Provide traceable evidence of data discovery, transformation, access, use, and retirement.

## 4. Scope and delivery principles

### 4.1 MVP scope

MVP1 is limited to six capabilities: Platform Access & Administration; Source Onboarding & Connectors; Scenario to Data Generation; Data Locking & Reservations; PII Data Discovery & Masking; and Data Provisioning & Validation. A **data set is TIGR’s primary managed unit**: every generated or provisioned output is tracked, versioned, validated, published, reserved/locked, and consumed as a data set. A data set can be created through either (1) AI-assisted Scenario to Data Generation using GenAI/LLMs, or (2) **Source to Data**, where a user selects an approved workspace source and mapping, chooses required fields, and generates/provisions a data set. Work is performed in a workspace, which provides the access and operating scope and holds the configured source/target connections used for generation and delivery.

### 4.2 Later scope

Later releases may add broader enterprise connectors, mainframe-native processing, advanced AI-assisted schema interpretation and scenario optimization, database virtualization, expanded environment orchestration, and higher-scale distributed processing.

### 4.3 Design principles

- Use synthetic data where it can meet the test need; use production-derived data only through approved controls.
- Keep sensitive data protected during discovery, processing, storage, and delivery.
- Make data lineage and transformations traceable.
- Preserve business relationships and valid states across systems.
- Require human review for uncertain discovery or AI-generated recommendations.
- Treat data sets as owned, versioned products with defined access and expiry.

## 5. Users, roles and product concepts

MVP1 has three human roles. Access is assigned within one or more workspaces and can be limited to specific data sets, connections, reservations, locks, and time periods. Non-human service identities are separately scoped technical principals, not a fourth user role.

| Role | Responsibilities and access |
|---|---|
| **Test Data Engineer** | Creates and manages workspace source/target connections; tests and saves connection configurations; creates data sets through either generation pathway; discovers PII; uploads or configures masking policies, reviews findings, and runs/enforces masking; validates and publishes ready data sets for use. |
| **QA Engineer** | Views published data sets available to them; reserves data for planned future use; locks a data set while it is in use; releases or lets the lock expire; downloads an authorized data set or integrates it with an automation test suite. |
| **Admin** | Creates and manages workspaces; manages users and workspace access; grants or revokes time-bounded access to workspaces, data sets, reservations, and locks; oversees all user and job activity across TIGR; performs permitted administrative overrides with an audit reason. |

Application/data-owner approval and privacy-review duties are permissions or approval steps assigned to these three roles according to bank governance; they do not create additional MVP1 personas.

### 5.1 Core operating model

- **Workspace:** The environment in which users configure connections and create, manage, and use data sets. It groups approved source and target connections across supported relational and file-based stores, along with its users, permissions, jobs, policies, and data-set catalogue.
- **Connection lifecycle:** An authorized Test Data Engineer configures a source or target, references its secret securely, previews discovered structure, tests connectivity/access, and saves the connection for workspace use. Only validated, enabled connections can be selected for work.
- **Data set:** The primary TIGR business object and lifecycle unit. A data set has an owner, workspace, creation pathway, status, version, schema/field scope, source or synthetic lineage, privacy state, validation evidence, publication state, expiry, and usage controls.
- **Scenario-to-data pathway:** User describes the need in natural language; GenAI/LLM assists with scenario interpretation and schema/field mapping; user reviews the mapping and generation plan; TIGR generates source-backed or synthetic content, validates it, and creates a data-set version.
- **Source to Data pathway:** User selects an approved workspace source and saved mapping, chooses required fields, supplies generation/selection criteria, then generates or provisions the requested records/files. TIGR validates selected fields, constraints, relationships, and privacy controls before creating the data-set version.
- **Publication and consumption:** A Test Data Engineer publishes a validated data set. Published data sets appear in the common catalogue/dashboard for authorized users. QA Engineers consume only data sets and versions for which they have access, with reservation and lock controls applied.
- **Data-set lineage:** Every data-set version has an inspectable lineage view showing its origin and processing history, including workspace, source or synthetic generation inputs, mapping/scenario, selected fields, jobs, masking/transformation steps, validation results, publication, and target/download events as applicable.

### 5.2 Dashboard and activity visibility

The Dashboard is the authenticated landing page after sign-in and workspace selection. It presents role-scoped summaries for recent activities, data sets, job status, reservations, locks, and the number of configured data sources/connections. Counts and links must be calculated only from objects the current user is authorized to access.

| Role | Dashboard activity visibility |
|---|---|
| **Admin** | All user and job activities across TIGR. |
| **Test Data Engineer** | The Test Data Engineer’s own activities and QA Engineer activities within the authorized workspace scope. |
| **QA Engineer** | The signed-in QA Engineer’s own activities only. |

Dashboard cards and activity feeds must honor the same scope: no unauthorized data-set names, user identities, connection details, or sensitive values may be exposed through totals, drill-downs, or notifications.

### 5.3 MVP1 role journeys

| Role | Main journey |
|---|---|
| **Test Data Engineer** | Sign in and select a workspace → configure a source/target connection → preview its structure → test and save/enable the connection → create a data set using AI-assisted Scenario to Data Generation or Source to Data → review PII findings and upload/configure masking policy where required → run validation → publish the ready data-set version → inspect its lineage and monitor jobs/QA usage from the Dashboard. |
| **QA Engineer** | Sign in and select a workspace → find an authorized published data set → inspect its fields, version, validation state, and availability → reserve it for a future test window → acquire a lock when execution begins → download/integrate it with the automation suite → release the lock and reservation when done. |
| **Admin** | Sign in → create or select a workspace → review the Dashboard and all TIGR user/job activities → grant or revoke user access to workspaces, data sets, reservations, or locks for specified periods → monitor access and usage → perform an authorized override when needed, recording the reason and audit evidence. |

---

# 6. Functional Requirements

## MVP1 — Six in-scope capabilities

MVP1 includes only the six epics below. Supporting infrastructure and internal APIs are implementation components, not additional user-facing MVP capabilities.

### Epic 1 — Platform Access & Administration

**Business outcome:** Provide controlled bank-user access and clear activity oversight.

**Features**
- Bank SSO authentication.
- Workspaces and role-based access for Test Data Engineer, QA Engineer, and Admin.
- Admin can create, edit, activate, and deactivate workspaces; creation captures workspace name, purpose, status, and initial member/access grants.
- Scoped, optionally time-bounded permissions for users and service identities across workspaces, data sets, reservations, and locks.
- Role-aware Dashboard as the authenticated landing page, with summaries for recent activities, data sets, jobs by status, reservations, locks, and connected source count.
- Dashboard activity visibility: Admin sees all activities across TIGR; Test Data Engineer sees own and QA Engineer activities; QA Engineer sees only own activities.
- Recent Activities and searchable audit trail with actor, role, workspace, action, object, outcome, time and correlation reference; enforce the same visibility rules in APIs and exports.
- Searchable audit trail with actor, action, object, outcome, time and correlation reference.
- CSV export for authorized recent-activity and audit results.

### Epic 2 — Source Onboarding & Connectors

**Business outcome:** Register approved sources and targets for scenario-driven data preparation.

**Features**
- Relational database, JSON, CSV and structured delimited-text sources.
- Workspace-scoped source and target connection configuration with secret references.
- Schema and file-structure preview, including nested JSON objects and arrays.
- Connection/access validation, followed by explicit save/enable for workspace use, with actionable, non-sensitive errors.
- Source ownership, environment, purpose, permitted-operation and lifecycle status.
- CSV download of authorized source/target inventory and discovered metadata summaries.

### Epic 3 — Scenario to Data Generation

**Business outcome:** Create a governed, versioned data set from a tester’s requirement using AI-assisted scenario interpretation and generation.

**Required workflow:** **Text requirement → schema mapping → query development → data generation**

**Features**
- Capture test requirements in natural language, including scenario conditions, expected states, volume, exclusions and target use.
- Map the requirement to approved relational and JSON schemas, fields, keys and relationships; let users review and correct mappings.
- Ask for clarification when material intent or schema mappings are ambiguous.
- Choose approved source-backed retrieval or synthetic generation using the same scenario specification.
- Build nested, read-only, parameterized SQL queries across related tables, including joins, subqueries and CTEs when supported.
- Build connector-supported queries for nested JSON objects and arrays.
- Preview the logical query plan, source scope, estimated matches, limits and capped, privacy-protected results.
- Let authorized users review and approve query criteria before source extraction.
- Select and validate relationship-complete subsets.
- Generate synthetic data with GenAI from user-provided fields, schemas or natural-language requirements.
- Validate generated data for schema, field constraints, relationships, scenario rules and sensitive/prohibited values before release.
- Create a data-set record and version for every successful run, retaining the scenario, schema mapping, query plan, generation settings, validation and audit evidence.
- Present lineage from the data-set detail view for every version, including source/schema or synthetic inputs, scenario, query/generation job, masking, validation, publication, and downstream delivery events as applicable.
- Keep generated data sets in draft/processing state until required privacy, integrity, and validation gates pass; publication is a Test Data Engineer action.
- CSV download for authorized query summaries, protected preview results and scenario/validation reports. Raw or unmasked values must never be exported.

### Epic 4 — Data Locking & Reservations

**Business outcome:** Make approved data discoverable while preventing conflicting test use.

**Features**
- Browse the common catalogue/dashboard of published, versioned data sets, subject to workspace and data-set access.
- Submit data requests and route configured approvals.
- Reserve data for planned use.
- Acquire, inspect, renew and release exclusive user-specific locks on a data-set version or allocation.
- Download authorized data sets or use an approved export/integration format for an automation test suite; audit the action and apply masking/access controls.
- Prevent conflicting TIGR-mediated use; expire leases and fence stale workers.
- Allow audited administrator force-release with a required reason.
- CSV download for authorized catalogue and reservation/lock reports, with sensitive identifiers omitted or masked.

### Epic 5 — PII Data Discovery & Masking

**Business outcome:** Identify and protect personal and sensitive data before test use.

**Features**
- Test Data Engineers discover and classify likely PII and sensitive fields; review or correct classifications.
- Upload or create field-level masking policies; review policy coverage and findings before enforcing a policy on a data set.
- Apply consistent tokenization, substitution, redaction, date shifting and numeric transformation.
- Preview policies and route changes for approval.
- Validate masking and block release when required privacy checks fail.
- CSV download for authorized classification findings, policy coverage and masking-validation summaries; never export original PII.

### Epic 6 — Data Provisioning & Validation

**Business outcome:** Deliver approved masked or synthetic test data to controlled targets with evidence.

**Features**
- Provision approved masked or synthetic data to database and file targets.
- Support the **Source to Data** pathway: a user selects an approved workspace source and saved mapping, selects required fields, supplies permitted criteria/volume, then generates or provisions the data set.
- Validate target connectivity, target schema, record counts, relationships and required constraints.
- Validate that requested fields are present and correctly mapped, and that field constraints, relationships, privacy controls, and target schema are satisfied.
- Track data-set versions, lineage, job status and validation evidence; publish only after required checks pass and the Test Data Engineer confirms readiness.
- Provide an inspectable lineage view from each data-set version showing upstream inputs, mapping, processing/masking steps, validation evidence, publication state, and downstream target or authorized download events.
- Support controlled reruns, cleanup and expiry.
- Provide CSV downloads for authorized provisioning summaries, validation results and job reports; apply the same masking and export controls as the underlying data.

---

## Later Development — Expansion releases

### Epic 11 — Expanded Source Ecosystem

**Features and requirements**

- Add connectors for selected NoSQL databases, mainframe data stores, cloud warehouses, data lakes, and message or event sources.
- Support API-based ingestion and approved data exchange patterns.
- Extend metadata discovery and profiling to connector-specific data types and structures.
- Support source-specific execution options, including in-place processing where technically and operationally approved.
- Retain common source registration, policy, audit, and provisioning concepts across connectors.
- Deliver connectors in prioritized waves based on application coverage, risk, and reuse.

### Epic 12 — Advanced File and Semi-Structured Data Management

**Features and requirements**

- Improve handling of deeply nested JSON, optional fields, changing schemas, large files, and multi-file data sets.
- Add support for additional structured file formats and fixed-width text where required.
- Support schema version comparison and controlled schema evolution.
- Extend discovery to sensitive data in free-form text and selected document fields.
- Preserve document structure and required formatting during masking.
- Provide validation for file encoding, delimiters, field counts, and schema conformance.

### Epic 13 — Advanced AI-Assisted Modelling and Scenario Optimization

**Features and requirements**

- Extend beyond MVP1 schema-driven generation with AI suggestions for entity relationships, field meanings, sensitivity labels, and masking-rule drafts.
- Explain recommendations with evidence and confidence; require review before they change a published model or policy.
- Extend MVP1 scenario planning to richer multi-step lifecycle journeys, cross-product scenarios, temporal constraints, and schema inference across additional source types.
- Connect approved AI models through a governed integration layer and compare configurations using controlled evaluations.
- Monitor recommendation quality, schema-conformance rates, policy violations, reviewer acceptance, cost, and failure patterns.
- Prevent prompts, model context, and outputs from exposing unmasked sensitive data.
### Epic 14 — Advanced Synthetic Data and Banking Scenario Library

**Features and requirements**

- Expand scenario libraries across payments, cards, deposits, lending, onboarding, AML operations, trade finance, and customer service.
- Support more complex dependencies, temporal sequences, transaction histories, and lifecycle states.
- Generate configurable distributions and rare or exceptional conditions while maintaining business validity.
- Allow teams to create, validate, version, and share scenario packs through governed workflows.
- Measure scenario coverage against defined test conditions and business rules.
- Support reusable data packs for regression, migration, performance, and resilience testing.

### Epic 15 — Database Virtualization and Snapshot Management

**Features and requirements**

- Create storage-efficient snapshots or virtual copies where the target platform supports them.
- Provision parallel data environments from a known baseline.
- Support refresh, reset, rollback, and defect-reproduction workflows.
- Track snapshot lineage, source version, masking state, owner, and expiry.
- Enforce isolation between teams and environments.
- Provide capacity and lifecycle controls to prevent stale or unused copies from accumulating.

### Epic 16 — Environment and Data Lifecycle Orchestration

**Features and requirements**

- Coordinate data provisioning with environment readiness and availability.
- Support request, reserve, prepare, provision, execute, release, and cleanup workflows.
- Show environment and data dependencies in a common calendar or operational view.
- Manage conflicts and prioritization through configurable policies.
- Integrate with environment management, test management, and deployment tools.
- Retain a traceable association between environment, data set, release, and test execution.

### Epic 17 — Scale, Resilience and Distributed Processing

**Features and requirements**

- Scale profiling, masking, generation, and provisioning workloads according to approved capacity policies.
- Process large datasets in parallel while maintaining consistent masking and relationship behavior.
- Support workload scheduling, prioritization, retries, and recovery from partial failures.
- Provide operational metrics for duration, throughput, queue depth, resource use, and failure rates.
- Protect source and target systems through workload limits and maintenance windows.
- Support recovery, restart, and reconciliation for interrupted processing.

### Epic 18 — Governance, Compliance and Risk Analytics

**Features and requirements**

- Provide dashboards for sensitive-data findings, masking coverage, unmasked exceptions, access, approvals, expiry, and cleanup.
- Track policy compliance by source, project, environment, and data set.
- Support evidence exports for internal reviews and approved audit processes.
- Manage policy versions, approval history, exceptions, remediation actions, and due dates.
- Report data lineage and transformation history across source, model, job, and target.
- Support configurable retention and regional or legal restrictions as determined by the bank.
- Alert owners to overdue reviews, policy changes, and unresolved risk findings.

### Epic 19 — Advanced Test-Data Analytics and Optimization

**Features and requirements**

- Measure request turnaround, reuse, provisioning success, data-quality failures, and data-related test delays.
- Identify underused data sets and stale catalogue entries.
- Report storage and processing consumption by project or application.
- Recommend reusable data packs based on request patterns, subject to owner review.
- Compare defect reproduction outcomes across data-set versions.
- Provide trend reporting to support CoE capacity planning and service improvement.

---

# 7. Cross-Cutting Business and Non-Functional Requirements

- **Security:** Apply least privilege, secure authentication, encryption in transit and at rest, and protected secret handling.
- **Privacy:** Minimize source data, restrict samples, apply approved masking before broad test use, and govern access to any original data.
- **Auditability:** Record user and service activity, policy versions, approvals, transformations, provisioning, and cleanup.
- **Integrity:** Preserve configured keys, relationships, constraints, and business states through every processing stage.
- **Reliability:** Make job status visible; support safe retries and avoid silent partial success.
- **Performance:** Define measurable service targets for profiling, masking, generation, and provisioning after baseline workload analysis.
- **Scalability:** Support modular addition of connectors and processing capacity without redesigning the catalogue and governance model.
- **Usability:** Make common data discovery and request workflows understandable to testers without requiring direct database expertise.
- **Interoperability:** Use documented APIs and formats that can integrate with bank-approved testing, delivery, and environment tools.
- **Configurability:** Keep source types, policies, approval paths, retention, and limits configurable rather than embedded in application logic.

# 8. MVP1 Acceptance Criteria

MVP1 is ready for pilot when:

1. Bank users can sign in through SSO and access only permitted workspaces and objects under one of the three MVP1 human roles; Admin can create workspaces, and access grants/expiry are auditable.
2. After sign-in and workspace selection, the Dashboard shows role-scoped activity, data-set, job, reservation, lock, and connected-source summaries. Admin sees all activities across TIGR, Test Data Engineer sees own and QA Engineer activities, and QA Engineer sees own activities only.
3. Authorized Test Data Engineers can configure relational, JSON, CSV and structured delimited-text source/target connections, preview structure, test access, and save/enable validated connections in a workspace.
4. A Test Data Engineer can create a data set through AI-assisted scenario-to-data generation, review its field/schema mapping, use source-backed or GenAI synthetic generation, and inspect validation and lineage before publishing.
5. A Test Data Engineer can create a data set through Source to Data by selecting an approved source, saved mapping, and required fields; the result passes schema, field, relationship, privacy, and target checks before publication.
6. Every successful generation/provisioning output is represented by a versioned data set with owner, workspace, pathway, status, lineage, validation evidence, and access/expiry metadata; users can open a lineage view for every version.
7. A Test Data Engineer can upload/review masking policy and enforce masking; data sets requiring masking cannot be published or made available to QA until masking validation passes.
8. QA Engineers can see only authorized published data sets, reserve them for a defined period, lock them while in use, and download/use an authorized artifact for automation testing; reservation/lock conflicts, expiry, renewal, release, and administrative overrides are auditable.
9. Admin can grant time-bounded access to workspaces, data sets, reservations, and locks, and dashboard/API access stops when the grant expires.
10. CSV downloads are available for authorized reports and activity views; no export reveals unmasked PII or circumvents role/workspace/data-set access.
11. Failed, partial, unmasked, invalid, or unpublished data cannot be represented as ready for QA consumption.

# 9. Success Measures

Establish a baseline during the pilot, then track:

- Median time from data request to usable data.
- Percentage of requests fulfilled from reusable data sets or scenario packs.
- Provisioning success rate and job completion time.
- Percentage of registered sources with completed profiling.
- Percentage of sensitive fields classified and covered by approved masking rules.
- Number of test failures or delays caused by missing, stale, or inconsistent data.
- Reservation conflicts and expired data sets not cleaned up within policy.
- Scenario-to-data request completion time, selected source-versus-synthetic mode, and query-plan approval rate.
- Data reuse and processing consumption by application or project.
- GenAI synthetic-data schema-conformance rate, relationship-validation pass rate, and rejected/quarantined batch rate.

# 10. Assumptions and Dependencies

- Application and data owners will identify approved sources, targets, and data stewards.
- The bank will provide approved identity, secrets, infrastructure, and audit capabilities.
- Connector priorities and supported database versions will be agreed during technical design.
- Privacy, security, and compliance teams will define permitted source use, masking policy, retention, and approval requirements.
- Environment, test management, and CI/CD integrations will be selected with delivery teams.
- Synthetic rules and banking scenario definitions will be reviewed by domain and application SMEs.

# 11. Out of Scope for the Initial MVP

- Replacing the bank’s enterprise data governance or production data platforms.
- Unrestricted production-data access for test users.
- Automatic activation of AI-generated rules without human approval.
- Direct changes to production systems.
- Full environment orchestration or database virtualization before the core data lifecycle is established.
- A universal connector for every database, file type, or third-party platform.
- Delivery-pipeline integrations and standalone automation APIs as user-facing MVP1 capabilities.
