# TIGR Architecture Diagrams (C1–C3)

These diagrams describe the current repository structure and runtime behavior. C1 shows TIGR in its environment, C2 shows deployable/runtime containers, and C3 expands the FastAPI container into its main code components. Module names are included to make the diagrams traceable to the source.

## C1 — System Context

TIGR is the test-data workflow system used by workspace administrators, Test Data Engineers, and QA Engineers. It coordinates source-backed and synthetic data generation, privacy review, publication, reservations, record-level locks, viewing, and delivery.

```mermaid
flowchart LR
    admin["Workspace Admin<br/>Manage workspace access and operations"]
    tde["Test Data Engineer<br/>Configure sources, generate, mask, publish"]
    qa["QA Engineer<br/>Reserve, lock, view, download"]

    tigr["TIGR<br/>Test data generation and governance platform"]

    pg[("TIGR PostgreSQL<br/>Application data, jobs/outbox, audit, records, lineage")]
    sources["Configured source / target systems<br/>PostgreSQL, CSV, JSON and delimited files"]
    ollama["Ollama model service<br/>Schema assistance and privacy field classification"]
    oidc["OIDC identity provider<br/>Optional; validation is implemented, provider is deployment-configured"]
    secrets["Secret manager<br/>Integration boundary; production adapter is not configured in this repo"]
    files["Configured file roots<br/>File-based inputs and file delivery targets"]

    admin -->|Uses browser UI and API| tigr
    tde -->|Uses browser UI and API| tigr
    qa -->|Uses browser UI and API| tigr
    tigr -->|Reads and writes application state| pg
    tigr -->|Reads approved source data; may deliver to targets| sources
    tigr -->|Generation and classification requests; source records are excluded| ollama
    tigr -.->|Bearer token verification when OIDC mode is enabled| oidc
    tigr -.->|Resolves connection secret references in configured deployments| secrets
    tigr -->|Reads or writes configured file inputs and targets| files
```

Dashed relationships indicate optional deployment integrations. Neo4j is intentionally omitted from active connections: the repository reports the graph store as not configured, and PostgreSQL is authoritative for lineage.

## C2 — Container Diagram

The browser UI, API, and background worker are separate runtime processes. The API and worker share PostgreSQL; the worker claims durable outbox events and runs generation or masking jobs. In local development, Vite proxies `/api` to the API process.

```mermaid
flowchart LR
    user["Workspace users"]

    subgraph tigr["TIGR software system"]
        web["Web application<br/>React + TypeScript + Vite<br/>Browser SPA; role-scoped pages"]
        api["REST API<br/>FastAPI + Python<br/>/api/v1; auth, validation, workflow APIs"]
        worker["Outbox worker<br/>Python process<br/>Claims and executes generation/masking jobs"]
        db[("PostgreSQL<br/>SQLAlchemy persistence<br/>Workspaces, connections, datasets, records, audit, jobs, outbox")]
        artifactStore["Configured file roots<br/>File-backed sources and file delivery targets"]
    end

    sourceDb["Configured source/target databases<br/>PostgreSQL connectors"]
    model["Ollama<br/>Local model API"]
    oidc["OIDC provider<br/>Optional deployment integration"]
    secretStore["Secret manager<br/>Production adapter boundary"]

    user -->|HTTPS/HTTP in deployment; local Vite in development| web
    web -->|JSON REST and file downloads| api
    api -->|Read/write application data; enqueue outbox events| db
    worker -->|Poll and claim outbox; update jobs and domain data| db
    api -->|Metadata discovery and bounded source reads| sourceDb
    worker -->|Bounded source reads for queued generation| sourceDb
    api -->|Requirement/schema assistance and PII classification requests| model
    api -.->|Validate bearer tokens when enabled| oidc
    api -.->|Resolve configured connection secrets| secretStore
    api -->|File-backed source checks and synchronous file delivery| artifactStore
    worker -->|Reads configured file-backed sources for queued jobs| artifactStore
```

**Container notes**

- The API is started from `backend/tigr/main.py`; its routers share a SQLAlchemy session factory from `backend/tigr/db.py`.
- The worker runs as `python -m tigr.outbox_worker`. It consumes scenario generation, source generation, source-query generation, and masking events from PostgreSQL.
- The database stores domain state, generated dataset records/artifact metadata, and queued work. The repository does not use a separate message broker.
- Connection discovery supports PostgreSQL and configured file connectors. Secret resolution is environment-based in local development; a production secret-manager adapter remains deployment work.
- The Ollama client is implemented in `backend/tigr/scenario_assistant.py` and used by schema/query assistance, synthetic generation, and PII classification. Source record values are excluded; synthetic generation can include prior generated identity values so new versions avoid reusing them.
- File roots are configured through `TIGR_FILE_ROOTS`. The app database is separate from configured source/target PostgreSQL databases.

## C3 — REST API Components

This diagram expands the FastAPI API container. Components are logical Python modules, not independently deployed services. Several routers use SQLAlchemy models directly as well as shared domain helpers.

```mermaid
flowchart TB
    browser["React web application"]

    subgraph api["FastAPI REST API container — backend/tigr"]
        entry["Application entry<br/>main.py<br/>Router registration, CORS, error handlers"]
        middleware["Request middleware<br/>middleware.py<br/>Correlation/request IDs"]
        identity["Identity and authorization<br/>identity.py, access.py, access_schemas.py<br/>Development auth or OIDC; workspace roles and grants"]

        subgraph routers["HTTP routers"]
            platform["Platform access + dashboard<br/>platform_access.py, dashboard.py"]
            connectionApi["Connections<br/>connections.py"]
            scenarioApi["Scenario to Data<br/>scenario_api.py"]
            sourceApi["Source to Data<br/>source_to_data.py"]
            datasetApi["Data sets and lineage<br/>dataset_api.py"]
            privacyApi["PII discovery and masking<br/>privacy_api.py"]
            lockApi["Reservations and locks<br/>reservations_api.py"]
            jobDeliveryApi["Jobs, delivery, reports<br/>jobs_api.py, delivery_api.py"]
        end

        subgraph domain["Domain and workflow modules"]
            accessDomain["Access and audit<br/>iam.py, audit.py, access.py"]
            connectionDomain["Connection discovery and secrets<br/>connection_testing.py, secrets.py, lock_configuration.py"]
            scenarioDomain["Scenario planning and generation<br/>scenarios.py, scenario_generation.py, scenario_schemas.py, scenario_assistant.py"]
            sourceDomain["Source query and mappings<br/>source_queries.py, source_mappings.py"]
            datasetDomain["Dataset lifecycle and persistence<br/>datasets.py, dataset_services.py, dataset_schemas.py, schema_records.py"]
            privacyDomain["Masking and identity capture<br/>governance.py, record_identity.py"]
            lockDomain["Entity claims and lock operations<br/>entity_claims.py, entity_lock_operations.py"]
            jobDomain["Job and outbox records<br/>jobs.py"]
            deliveryDomain["Artifact/provisioning operations<br/>delivery.py"]
        end

        persistence["Persistence adapter<br/>db.py + SQLAlchemy ORM models"]
    end

    postgres[("PostgreSQL application database")]
    upstream["Configured source/target databases and files"]
    ollama["Ollama model API"]
    worker["Outbox worker<br/>outbox_worker.py"]

    browser -->|REST| entry
    entry --> middleware --> identity
    entry --> routers

    platform --> accessDomain
    connectionApi --> connectionDomain
    scenarioApi --> scenarioDomain
    scenarioApi --> datasetDomain
    scenarioApi --> jobDomain
    sourceApi --> sourceDomain
    sourceApi --> datasetDomain
    sourceApi --> jobDomain
    datasetApi --> datasetDomain
    privacyApi --> privacyDomain
    privacyApi --> datasetDomain
    privacyApi --> jobDomain
    lockApi --> lockDomain
    lockApi --> datasetDomain
    jobDeliveryApi --> jobDomain
    jobDeliveryApi --> deliveryDomain
    jobDeliveryApi --> datasetDomain

    accessDomain --> persistence
    connectionDomain --> persistence
    scenarioDomain --> persistence
    sourceDomain --> persistence
    datasetDomain --> persistence
    privacyDomain --> persistence
    lockDomain --> persistence
    jobDomain --> persistence
    deliveryDomain --> persistence

    persistence -->|SQLAlchemy sessions and ORM| postgres
    connectionDomain -->|Schema discovery and connection tests| upstream
    scenarioDomain -->|Source-backed reads when selected| upstream
    sourceDomain -->|Query validation and bounded extraction| upstream
    deliveryDomain -->|File/database delivery operations| upstream
    scenarioDomain -->|Requirement/schema assistance and synthetic generation| ollama
    privacyDomain -->|Field classification; record values excluded| ollama
    jobDomain -->|Durable outbox events in PostgreSQL| worker
    worker -->|Dispatches scenario/source/masking job handlers| scenarioDomain
    worker -->|Dispatches source extraction handlers| sourceDomain
    worker -->|Dispatches masking handler| privacyDomain
```

## Repository anchors

- Runtime and routers: `backend/tigr/main.py`
- Configuration and persistence: `backend/tigr/config.py`, `backend/tigr/db.py`
- Browser routes and API client: `frontend/src/App.tsx`, `frontend/src/api.ts`
- Background job execution: `backend/tigr/jobs.py`, `backend/tigr/outbox_worker.py`
- Current system behavior and integration limits: `README.md`

## Component Navigation and Workflow Flows

The diagrams below connect the architecture to the paths a user follows in the application. UI routes are registered in `frontend/src/App.tsx`; backend route groups are mounted by `backend/tigr/main.py`. Authorization is also enforced by the API, so hiding a navigation link is not the security boundary.

### Role-scoped application navigation

```mermaid
flowchart LR
    login["Sign in / select workspace"] --> home["Dashboard · /"]
    home --> datasets["Data sets · /datasets"]
    home --> reservations["Reservations & Locks · /reservations"]
    home --> privacy["PII Masking · /privacy"]
    home --> connections["Connections · /connections"]
    home --> activities["Activities · /activities"]

    datasets --> details["Data set details, versions, lineage"]
    details --> privacy
    details --> reservations
    reservations --> view["View Data · /view-data"]
    view --> download["Review/download available records"]

    subgraph qa["QA Engineer navigation"]
        qaHome["Dashboard"] --> qaData["Published Data sets"]
        qaData --> qaReserve["Request reservation"]
        qaReserve --> qaView["View Data"]
        qaView --> qaDownload["View, copy, or download allowed records"]
    end

    subgraph builders["Test Data Engineer navigation"]
        tdeHome["Dashboard"] --> scenario["Scenario to Data · /scenario-to-data"]
        tdeHome --> source["Source to Data · /source-to-data"]
        scenario --> jobs["Jobs · /jobs"]
        source --> jobs
        jobs --> datasets
        datasets --> privacy
        privacy --> publish["Publish eligible version"]
    end

    subgraph admin["Admin navigation"]
        adminHome["Dashboard"] --> workspaces["Workspace settings · /workspaces"]
        adminHome --> access["Access & Roles · /access"]
        adminHome --> adminActivities["Activities · /activities"]
    end
```

QA navigation focuses on published data sets, reservation/lock operations, and View Data. Scenario to Data, Source to Data, Jobs, and Configure navigation are hidden for QA Engineers; the View Data route is role-gated. Workspace and Access & Roles settings are Admin-only. TDEs use the generation and configuration routes. Dashboard activity and request links take users to the corresponding workflow pages.

### Source-backed data set lifecycle

```mermaid
flowchart TD
    tde["TDE: Connections"] --> inspect["Discover source schemas and test connection"]
    inspect --> plan["Source to Data: choose source and map output fields"]
    plan --> query["Review query and sample output"]
    query --> request["Start full data set generation"]
    request --> outbox["Persist job and outbox event in PostgreSQL"]
    outbox --> worker["Outbox worker claims and runs job"]
    worker --> read["Read approved source rows"]
    read --> version["Persist draft version, records, metadata, and lineage"]
    version --> review["PII Masking: scan/classify and run masking when required"]
    review --> publish["TDE publishes eligible version"]
    publish --> catalog["Published Data Sets catalog"]
    catalog --> reserve["QA requests reservation"]
    reserve --> approval["TDE/Admin approves or denies request"]
    approval --> lease["Approved window; QA acquires active entity lock"]
    lease --> access["View Data / download computes allowed records"]
    access --> release["Release lock or let lease expire"]
```

The generation request is durable: the API records job/outbox state in PostgreSQL and the worker performs the long-running work. The generated version moves through draft and privacy/release review before it becomes available in the published catalog. Reservations define an allowed time window; active entity locks control which records are available to another user.

### Synthetic Scenario to Data lifecycle

```mermaid
flowchart TD
    tde["TDE opens Scenario to Data"] --> req["Step 1: describe data requirements and choose generation mode"]
    req --> schema["Step 2: review/add schema fields and relationships"]
    schema --> sample["Step 3: review query and optionally generate sample rows"]
    sample --> full["Step 4: request full data set generation"]
    full --> job["Job/outbox event persisted in PostgreSQL"]
    job --> worker["Outbox worker executes synthetic generation"]
    worker --> records["Persist generated draft version and records"]
    records --> privacy["Apply applicable privacy review and release gates"]
    privacy --> publish["Publish version"]
    publish --> catalog["Published Data Sets"]
    catalog --> qa["QA reservation → active lock → View Data/download"]
```

Schema generation, sample generation, and full data set generation are distinct workflow jobs. Step 3 sample rows are a preview; Step 4 is the full data set generation. A saved draft resumes at its saved step/job state where applicable.

### Reservation, entity locking, and record access

```mermaid
sequenceDiagram
    actor QA1 as QA Engineer 1
    actor QA2 as QA Engineer 2
    participant UI as React UI
    participant API as FastAPI routers
    participant Claims as Entity claim / lock operations
    participant DB as PostgreSQL

    QA1->>UI: Request reservation for published data set
    UI->>API: Create reservation request
    API->>DB: Persist request and audit event
    Note over QA1,DB: Approval grants a time window; reservation alone does not reserve every record.
    QA1->>UI: Acquire lock during approved window
    UI->>API: Acquire entity lock
    API->>Claims: Resolve configured entity identity keys and claim records
    Claims->>DB: Check conflicts and persist active claims/lease
    DB-->>UI: Lock lease result

    QA2->>UI: Open View Data or download
    UI->>API: Request records and access summary
    API->>Claims: Filter records against other users' active claims
    Claims->>DB: Read lock claims and dataset records
    DB-->>UI: Allowed records plus blocked count/message
    Note over QA2,UI: If no records are available, the UI blocks CSV download.

    QA1->>UI: Release lock (or lease expires)
    UI->>API: Release lease
    API->>Claims: Close claims for the lease
    Claims->>DB: Persist release/expiry state
```

Entity keys determine record identity and lock conflicts. Approved propagation paths and manual relationships are not part of the current lock-configuration UI; access filtering uses the configured entity key fields. A user’s own active lock does not hide that user’s records from their own View Data/download flow, while another user’s active claims can hide overlapping records.

### API request and background job path

```mermaid
sequenceDiagram
    actor User
    participant Browser as React SPA
    participant API as FastAPI
    participant Auth as Identity/access checks
    participant DB as PostgreSQL
    participant Worker as Outbox worker
    participant Integration as Source DB / files / Ollama

    User->>Browser: Navigate or submit action
    Browser->>API: REST request or download request
    API->>Auth: Resolve identity, workspace, role, and scope
    Auth-->>API: Authorized context or denial
    API->>DB: Read/write domain state
    alt Long-running generation or masking
        API->>DB: Persist job and outbox event
        API-->>Browser: Return job reference/status
        Worker->>DB: Claim pending event
        Worker->>Integration: Perform bounded source/model/file operation
        Worker->>DB: Persist result and terminal job status
        Browser->>API: Poll/refresh job status
        API-->>Browser: Return current status and output reference
    else Synchronous read/action
        API-->>Browser: Return result or safe error details
    end
```

Use this sequence as the common operational path for tracing a screen action: identify the React page/API client, the registered router, its authorization and domain modules, persistence, and—when an outbox event is created—the worker handler.

These diagrams are a code-oriented snapshot. Deployment topology, production identity provider, production secret manager, and external database inventory must be confirmed against the deployment environment.
