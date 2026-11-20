# TIGR MVP1 — Phase 0 Repository Assessment

**Date:** 2026-10-07  
**Phase:** 0 — Repository assessment  
**Status:** Complete

## Summary

The TIGR folder contains the MVP1 reference documents, a static HTML UI prototype, and banking CSV source files. It has no application source code, dependency manifests, migrations, tests, CI configuration, or deployment setup to extend. The folder is untracked inside the parent Git repository, which has no commits yet.

As requested, the local PostgreSQL server registered in pgAdmin as **LocalDBServer** now has a separate **`TIGR`** application database. It is owned by the configured local PostgreSQL role, and the available **pgvector 0.8.6** extension is enabled. The previously loaded **`TIGR_SOURCE`** database is retained as the source-data store; its `source_data` schema is not copied into the application database.

No product code, application schema, migration, or feature behavior was added in this phase.

## Repository and runtime findings

- Working folder: `TIGR` workspace.
- Git root: parent workspace; branch `main`, no commits. The complete `TIGR/` folder is currently untracked. No `AGENTS.md` or repository-specific instructions were found.
- TIGR currently contains only `Docs/`, including five Markdown documents, the HTML prototype, `archive.zip`, and two extracted CSV directories: `Docs/archive/` and `Docs/source_data/`. Both directories contain files with the same ten CSV names; exact content equivalence was not assessed.
- `Docs/source_data/load_source_data.sql` creates the relational source tables in `TIGR_SOURCE`. It is a one-time source-data loader, not an application migration system.
- No React/Vite frontend, FastAPI backend, routes, domain modules, worker, Alembic setup, tests, README, environment template, container configuration, or CI workflow exists yet.
- Local tools detected: Python 3.14.3, Node 26.7.0, npm 11.19.0, PostgreSQL 17.11, pgvector 0.8.6, and a Neo4j CLI. Docker and `uv` were not found. No project-level service configuration or credentials are present.

## Reference-document interpretation

The implementation plan names the baseline BRD, baseline TSD, and `1-TIGR_MVP1_UI_Prototype_baseline.html` as primary references; all are present and were reviewed. Both draft Markdown references were also reviewed.

Apply the plan’s precedence rules:

1. The baseline BRD governs business scope and behavior: exactly three human roles (Test Data Engineer, QA Engineer, Admin); workspaces define access scope; data sets are the primary managed objects; both Scenario to Data and Source to Data create governed data sets.
2. The baseline TSD governs technical design: React/TypeScript/Vite, FastAPI, PostgreSQL with pgvector, and Neo4j; PostgreSQL is authoritative; graph data is a derived metadata/lineage projection; raw customer values do not go into Neo4j or embeddings.
3. The prototype informs navigation and visual treatment. It is a static HTML/inline-JavaScript prototype with sample fixtures, not an existing application or source of authorization behavior. It shows a Dashboard landing page, workflow navigation, workspace context, and the specified purple palette.
4. The implementation plan controls sequencing and requires one phase at a time.

The draft BRD has a broader configurable role list and does not define the Source to Data creation path. These conflict with the baseline BRD and must not drive MVP1 implementation. The baseline TSD sometimes refers to both workspace and project scope; the baseline BRD and implementation plan use workspace as the primary operating/access boundary. Avoid creating a second scope hierarchy until the product owner resolves whether “project” is a separate concept.

## Existing data and database boundary

- `TIGR_SOURCE` holds the ten extracted banking source tables in schema `source_data` (about 5.87 million rows). Its customer data includes direct identifiers such as names, phone numbers, email addresses, and dates of birth. Treat the source as sensitive and keep it isolated from the TIGR metadata store.
- `TIGR` is a new, separate application database on LocalDBServer. It currently contains the `vector` extension only; TIGR application schemas and tables should be created through versioned migrations in a later phase.
- Keep raw source and generated files outside PostgreSQL by default. Store only protected artifact references and metadata in TIGR, as required by the TSD.

## Phase implementation map

| Plan phase | Intended work and repository prerequisite |
|---|---|
| 1 — Shared foundation and UI shell | Establish the frontend/backend boundaries, configuration, migration pattern, health/readiness and API conventions; define job/outbox, audit, and secret-reference seams; build the responsive application shell and required route placeholders. Reuse the static prototype for visual reference, not runtime code. |
| 2 — Identity, workspaces, access, Dashboard | Add OIDC integration when the bank identity-provider details are supplied; implement the three roles, scoped/time-bound grants, workspace administration, role-filtered Dashboard and audit/activity APIs. Keep any role-preview fixture development-only. |
| 3 — Workspace connections | Add workspace-scoped relational/file source and target profiles, secret references, structure discovery, connection tests, and explicit save/enable lifecycle. Do not use `TIGR_SOURCE` as an implicit approved connection until a workspace owner configures it. |
| 4 — Data-set core and lineage | Add the shared data-set/version lifecycle and PostgreSQL-authoritative lineage before either creation flow; keep lineage metadata safe and make publication depend on validation/privacy state. |
| 5 — Scenario to Data | Add text requirement, schema mapping, typed query planning, protected preview, and source-backed or synthetic job routing with lineage. Never execute model-generated SQL directly. |
| 6 — Source to Data | Add the second creation workflow using approved saved mappings and required-field selection, reusing the Phase 4 data-set and lineage services. |
| 7 — PII and masking | Add reviewed classifications and policy versions, masking/validation jobs, quarantine behavior, and publication gates. |
| 8 — Reservations and locks | Add planned reservations and exclusive leases, transactional conflict handling, expiry/fencing, time-bound authorization, and audited administrative override. |
| 9 — Provisioning and validation | Add target preflight, bounded provisioning, reconciliation, authorized exports, cleanup and expiry, with lineage events. |
| 10 — End-to-end readiness | Verify integrated role journeys, privacy/security controls, failure recovery and operations; resolve or record production dependencies and acceptance evidence. |

## Inputs needed before production integrations

The documents leave several bank/environment choices open: OIDC provider and claims, secret manager, broker/worker runtime, artifact storage, Neo4j endpoint and credentials, approved model/embedding gateway, source/target connector versions, approval policies, retention rules, file limits, and service objectives. Do not invent these or store credentials in TIGR. Local development can establish provider-neutral interfaces and clearly labeled non-production fixtures while these decisions are pending.

## Phase 0 exit gate

- Repository contents and existing implementation patterns have been assessed; no application implementation exists to preserve.
- The baseline references and conflicts with the drafts have been recorded above.
- `TIGR` and pgvector are prepared on LocalDBServer; `TIGR_SOURCE` remains separate.
- No product feature code or application schema was created. No tests were run in this assessment phase.

**Next prerequisite:** an explicit instruction to begin Phase 1 — Shared foundation and UI shell. Stop here until that instruction is given.
