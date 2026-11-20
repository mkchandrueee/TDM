# TIGR MVP1 acceptance alignment implementation

This note records how the MVP1 user stories in `1-TIGR_MVP1_Epics_Features_User_Stories_Acceptance_Criteria_baseline.md` are exposed through TIGR pages and PostgreSQL-backed APIs.

## User workflows

| Epic | UI entry points | Persisted workflow |
|---|---|---|
| Platform access and administration | Dashboard, Access & Roles, Activities & Audit | OIDC identity, workspace lifecycle, memberships, time-bounded grants, dashboard summaries, scoped activity search and CSV exports. |
| Source onboarding and connectors | Connections | Relational and structured-file connection metadata, secret references, structure snapshots, test history, lifecycle changes, and inventory exports. |
| Scenario to Data | Scenario to Data, Data sets, Jobs, PII & Masking | Saved scenario plans, schema mapping, typed query previews, bounded asynchronous generation, dataset versions, PostgreSQL records, validation, masking, publication, and lineage. |
| Data locking and reservations | Data sets, Reservations & Locks | Published catalog, version-scoped data-use requests and approval decisions, reservations, exclusive leases, fencing tokens, protected downloads, and usage reports. |
| PII discovery and masking | PII & Masking | Field findings and reviews, versioned policies, coverage previews, approved masking runs, leakage/schema gates, and privacy exports. |
| Provisioning and validation | Source to Data, Data sets, Jobs, Connections | Versioned source mappings, selected fields and criteria, durable jobs, target preflight/delivery, structured validation, publication, cleanup, lineage, and reports. |

The application already has pages for the six epics. The request and approval flow is now part of **Reservations & Locks**, so no separate approval page is needed. Report exports are available in the data-set catalog and the relevant workflow pages.

## PostgreSQL as the application data store

- Generated Scenario to Data and Source to Data records are persisted in `dataops.dataset_records`, linked to workspace, data set, version, and ordinal.
- Record reads for masking, target provisioning, and authorized downloads come from that table. Downloads and CSV exports are serialized in memory for the HTTP response.
- Short-lived CSV export content is stored in `dataops.export_artifacts.content`; export creation and download evidence remains auditable.
- Connection definitions, mappings, scenarios, policies, jobs, validations, lineage, reservations, requests, approvals, locks, and artifact metadata are stored in their PostgreSQL tables. PostgreSQL remains the authoritative lineage store.
- Source connectors can read configured source files. A configured file target remains an explicit delivery endpoint; the generated TIGR artifact and report are held in PostgreSQL.
- The bundled banking sample seeder and its source files have been removed. Workspaces, connections, and source data must be configured by the operator.

## New request and approval behavior

QA can submit a request for a specific published version and bounded time window. A Test Data Engineer or Admin can approve or deny it with a recorded decision. QA reservations must reference an approved request for that version and stay inside the approved window. Lock acquisition requires the requester’s active reservation, and QA artifact downloads require the approved request, active reservation, and active lock. These checks are enforced by the API.

## Environment-dependent acceptance items

- OIDC bearer-token validation is implemented in the API, but no identity provider or browser client is configured. The web app still uses its labeled development-role preview; bank SSO sign-in needs issuer, client, callback, and JWKS settings.
- Scenario mapping and synthetic generation currently use the deterministic, metadata-only local provider. No approved GenAI model endpoint or secret reference was supplied, so GenAI-backed mapping/generation remains an environment integration.
- A secret-manager adapter, production source credentials, and optional Neo4j projection are environment integrations. PostgreSQL lineage remains authoritative.

## Schema change

Alembic revision `0014_pg_records_requests` added `dataops.dataset_records`, `dataops.data_use_requests`, the request reference on reservations, and PostgreSQL content storage for temporary report exports. Revision `0029_remove_banking_sample_tables` removes the internal banking demo tables; apply `alembic upgrade head` to an existing TIGR database to remove them. The separate external `TIGR_SOURCE` database is not changed by this migration.
