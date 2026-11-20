# TIGR MVP1 Backend Schema Implementation Map

## Database implementation

The schema baseline explicitly permits retaining an existing migration convention when its entities preserve the proposed logical model. TIGR keeps its current PostgreSQL namespaces (`iam`, `dataops`, and `audit`) and existing table names; no parallel `workspace`, `connector`, `dataset`, or `execution` schemas were introduced.

Migrations through `0014_pg_records_requests` are applied to the local `TIGR` database. Migration 0012 adds workspace-scoped data-set versions, a non-negative version record count, and `dataops.dataset_fields`. Migration 0013 adds normalized test history, status history, publication evidence, validation runs/results, artifact records, and consumption events. Migration 0014 adds version-bound PostgreSQL dataset records, data-use requests, request-linked reservations, and content storage for short-lived CSV exports.

## Logical entity mapping

| Baseline entity | TIGR PostgreSQL implementation |
| --- | --- |
| Principal, user, service identity, platform role | `iam.users`, `iam.service_identities`, `iam.platform_admins` |
| Workspace and membership | `iam.workspaces`, `iam.workspace_memberships` |
| Scoped object access | `iam.access_grants` with API scope checks |
| Connection registry and tests | `dataops.workspace_connections`, `dataops.connection_test_runs` |
| Discovered connection structure | Safe `structure_preview` metadata on `dataops.workspace_connections`; no row values |
| Data set and immutable versions | `dataops.datasets`, `dataops.dataset_versions`, `dataops.dataset_fields` |
| Generated data-set values | `dataops.dataset_records`, ordered and scoped by workspace, data set, and immutable version |
| Source mapping versions | Versioned rows in `dataops.source_data_mappings` |
| Scenario specifications | `dataops.scenario_plans` |
| Job, steps, durable dispatch | `dataops.jobs`, `dataops.job_steps`, `dataops.outbox_events` |
| Validation | `dataops.validation_runs`, `dataops.validation_results`, plus compatible safe evidence on each version |
| Publication and status history | `dataops.dataset_publications`, `dataops.dataset_status_history` |
| PII findings and masking | `dataops.pii_findings`, `dataops.masking_policies`, `dataops.masking_runs` |
| Reservations and fencing locks | `dataops.dataset_reservations`, `dataops.dataset_locks` |
| Data-use request and approval | `dataops.data_use_requests`; reservations retain their approved request reference |
| Provisioning and cleanup state | `dataops.provisioning_runs`; successful cleanup is recorded in run evidence and lineage |
| Version lineage | `dataops.dataset_lineage_events`, `dataops.dataset_lineage_edges` |
| Audit | `audit.audit_events` |
| Artifact metadata and access | `dataops.artifact_records`, `dataops.consumption_events` |
| Export content and metadata | `dataops.export_artifacts`; CSV content is stored in PostgreSQL and streamed on download |

## Code and UI wiring

- Data-set create/version services now write normalized field rows and retain the prior JSON field representation for compatible clients. Source-backed and scenario generation, masking, and service validation persist a version-level record count.
- Connection tests append a safe result to `connection_test_runs`. The Connections page loads and displays recent test history.
- Data-set creation, validation, publication, expiry, and worker failure write status history. Validation flows write a run and safe summary result; publication writes its readiness snapshot.
- Generation and masking persist generated values in `dataops.dataset_records` and an artifact record with an opaque reference, type, size, checksum, and restricted classification. Authorized downloads and target delivery/cleanup append consumption events.
- The Data Sets page displays record counts and relational version fields. Its Validation tab displays structured validation runs and lifecycle history.

## Follow-up model differences

The baseline remains the logical source of truth. Some entities use compact existing representations: schema discovery is stored as safe preview metadata rather than separate snapshot/object/field tables; masking rules remain in immutable policy records; provisioning cleanup remains attached to the provisioning run; and job progress history is represented by steps and outbox events. Data-use approval is enforced in PostgreSQL-backed APIs. Neo4j remains an optional, rebuildable lineage projection. Configured file connectors are supported as explicit source or delivery endpoints; TIGR-generated records and report content are persisted in PostgreSQL.
