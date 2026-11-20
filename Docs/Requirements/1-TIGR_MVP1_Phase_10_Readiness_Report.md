# TIGR MVP1 Phase 10 Readiness Report

## Verification performed

- Applied the local PostgreSQL migrations through revision `0011_durable_worker_claims`.
- Built the frontend with TypeScript checking and Vite production bundling.
- Ran the full backend suite against local PostgreSQL: 19 passed, 1 Starlette/httpx deprecation warning.
- Backend lint passed: `.venv/bin/ruff check backend`.
- Frontend type check and production build passed: `cd frontend && npm run build`.
- `git diff --check` passed.

## MVP1 acceptance mapping

| BRD acceptance | Current evidence | Readiness |
| --- | --- | --- |
| 1. SSO and role/workspace/object scope | OIDC token validation and API scope tests; browser sign-in/provider not configured | Dependency remains |
| 2. Role-scoped dashboard/activity summaries | Dashboard/activity APIs and navigation; activity export shares the same visibility scope | Partial: browser visual E2E not exercised |
| 3. Connection configuration and checks | PostgreSQL/file connection lifecycle and authorization tests | Implemented baseline |
| 4. Scenario to Data lifecycle | Scenario workflow, masking gates, validation, publication, lineage implemented | Partial: no full browser E2E or configured GenAI service |
| 5. Source to Data lifecycle | Sources, mappings, bounded generation, masking/publication gates implemented | Partial: external connector contract matrix not run |
| 6. Versioned outputs and lineage | Versioned catalog, validation, masking, provisioning/download lineage | Partial: target connector integration tests remain |
| 7. PII review and masking | Human review, approved masking policies, fail-closed masking, publication gates | Implemented baseline; privacy red-team suite remains |
| 8. QA browse/reserve/lock/download | Object grants, reservations, fencing locks, authorized artifact download | Partial: browser automation E2E remains |
| 9. Time-bound access | Scoped grants and expiry authorization checks | Implemented baseline |
| 10. Safe CSV reports | Summary report types, limits, formula neutralization, expiring links, audit, and role-scoped job/activity exports | Partial: protected previews and detailed coverage exports remain |
| 11. Failed or unapproved data not ready | Lifecycle, privacy, validation, publication, and expiry gates | Implemented baseline; target failure/rollback coverage remains |

## Operational readiness gaps

- Local development uses the explicit development role header. OIDC browser login is not configured, so SSO acceptance cannot be demonstrated.
- Neo4j is not configured. PostgreSQL remains the authoritative lineage store; graph projection/rebuild cannot be verified.
- The independent PostgreSQL worker now recovers claimed generation/masking events after process interruption. Provisioning still executes synchronously in the API process; the worker recovery path needs dedicated fault-injection coverage.
- The worker covers queued scenario, source-to-data, and masking jobs. Cancellation and retry backoff are not implemented.
- Backup/restore was not run; no backup destination, retention policy, or restore environment was supplied.
- Representative load baselines were not run. The BRD defers performance targets until workload analysis, and no confirmed workload/SLO is configured.
- Accessibility/responsive checks and full browser end-to-end journeys were not run.
- Worker coverage verifies exclusive claim behavior and recovery after a claim lease expires; process-kill fault injection and multi-worker deployment were not run.

## Release gate

**MVP1 is not ready for pilot.** The six workflow areas have local implementations, but SSO, target connector integration, backup/restore, graph projection, workload baselines, and browser E2E remain unverified. No deployment or production rollout was performed.

## Test warning

The test run emitted a Starlette deprecation warning because the installed TestClient uses `httpx`; the suite still passed. JSON file provisioning is exercised against a temporary directory; real PostgreSQL target writes and cleanup failure recovery remain release checks.
