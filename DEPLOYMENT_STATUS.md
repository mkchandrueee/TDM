# TIGR local Windows deployment

Deployment date: 30 October 2026 (Asia/Calcutta).

TIGR is installed and running locally. This is a development deployment with development authentication. It is not configured for shared or production access.

## Open TIGR

- Frontend: http://127.0.0.1:5174
- API documentation: http://127.0.0.1:8001/api/v1/docs
- Readiness: http://127.0.0.1:8001/api/v1/health/ready

## Installed components

| Component | Version / status |
|---|---|
| Host | Windows 11 Enterprise, x64 |
| Python | 3.13.16 |
| Node.js / npm | 22.23.4 / 10.9.9 |
| PostgreSQL | 17.11, private cluster in `.deployment/pgdata` |
| pgvector | 0.8.7, compiled from official source |
| MySQL | 8.4.9, private data directory in `.deployment/mysql-data` |
| C++ build tools | Visual Studio Build Tools 2022, 17.14.44 |
| Python dependencies | Installed in `.venv-win`; `pip check` passes |
| Frontend dependencies | Installed using `npm ci`; audit reported zero vulnerabilities |
| Migration | `0041_oracle_mssql` (head) |

Python, Node.js, MySQL, and C++ tools were installed through winget. PostgreSQL uses the [official EDB binary distribution](https://www.enterprisedb.com/download-postgresql-binaries), supported by the [PostgreSQL Windows download instructions](https://www.postgresql.org/download/windows/). pgvector was built using its [official Windows instructions](https://github.com/pgvector/pgvector#windows).

## Restored data and configuration

All four backup SHA-256 checksums matched `local-db-backups/SHA256SUMS` before restore.

- PostgreSQL: `TIGR`, `CORE_BANKING_SOURCE`, `CRM_SOURCE`.
- MySQL: `digitalbank`, including `digital_wallet_transactions`.
- Restored TIGR contains 2 workspaces, 17 datasets, and 32 original jobs.
- Original encryption and identity keys were preserved. All three saved source profiles decrypted successfully.
- Two PostgreSQL profiles and one MySQL profile now use the local restored sources with a dedicated read-only account. All three connection tests passed and profiles remain enabled.
- Original encrypted source-profile references are retained privately in `.deployment/original-source-profiles.json` and in the unmodified backup.
- Runtime database access uses `tigr_app`, not the PostgreSQL administrator.
- Secrets, backups, source files, artifacts, and deployment state are restricted to the deployment account. Deployment state and Windows environment are git-ignored.

The copied macOS `.venv` was preserved; Windows uses `.venv-win` instead. The macOS frontend dependencies were replaced with the Windows installation from the lockfile.

## Verification

- API liveness and readiness: HTTP 200; PostgreSQL reports ready.
- Frontend and its API proxy: HTTP 200.
- API, PostgreSQL, frontend, and MySQL bind only to `127.0.0.1`, on ports 8001, 5432, 5174, and 3306 respectively.
- Independent worker is running with inline jobs disabled. It claimed and acknowledged a harmless terminal-job smoke event on its first attempt.
- Frontend build passed; it reports a non-blocking bundle-size warning.
- Full backend test run on a clean, disposable migrated database: **31 passed, 15 failed**. The temporary database was removed. Failures include entity-lock expectations, connector mocks/error messages, dataset lifecycle expectations, and export interfaces. Full output is private at `.deployment/backend-tests.log`. Installation/health checks do not establish that all workflows are correct.

The deployment fixed migration 0041's invalid parent reference from `0040_encrypt_connection_credentials` to the actual revision `0040_encrypt_credentials`. Both the restored database upgrade and clean-schema migration run succeeded afterward.

## Restart after Windows restart

Services are background processes owned by the deployment account. Automatic startup and Windows service registration were not configured.

From this repository, run the reviewed launcher when TIGR's application processes are stopped:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-TIGR.ps1
```

This bypass applies only to the launcher process; the machine execution policy is unchanged. The launcher starts private database processes when needed and the API, worker, and frontend in hidden windows. It refuses to start the API/frontend if their ports are occupied. Logs and process IDs are in `.deployment/`; do not share that directory because it also holds private credentials and database files.

One-time installation helpers (`deploy_windows.py`, `deploy_mysql_windows.py`, `configure_local_sources.py`) refuse to repeat over their deployment state. Do not rerun them as routine startup commands. `verify_windows_deployment.py` runs tests against a temporary clean database.

## Remaining limitations

- Ollama and a model were not installed. Model-assisted metadata/PII paths are unavailable.
- No `TIGR_MASKING_SECRET` was supplied. Stable tokenization requires the approved original masking secret if continuity is needed; no replacement was generated.
- Neo4j is unconfigured, as expected by the runbook.
- OIDC browser login, TLS, reverse proxy hosting, automatic startup, and scheduled backups remain unconfigured. Configure them before shared/production use.
- Source configuration, health, credential decryption, and worker dispatch were verified. A full browser workflow, data generation, masking, and export acceptance exercise was not completed.
