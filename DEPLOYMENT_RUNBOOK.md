# TIGR Deployment Runbook

This runbook is for deploying the TIGR application from a copy of this repository and its `local-db-backups/` directory onto a new machine. It covers software installation, database restore, application configuration, startup, and operational checks. It is written so a future Codex session can carry out the deployment from a clean host.

> **Data handling:** the backup files contain TIGR workspace, connection, dataset, and source data. Treat them as confidential. Keep them out of public repositories and transfer them only through an approved secure channel. Do not print or commit credentials, encryption keys, or database passwords.

## 1. What this deployment contains

| Component | Role | Required? |
|---|---|---|
| Python 3.13 | FastAPI backend, database migrations, durable job worker | Yes |
| PostgreSQL 17 | TIGR application database and optional restored PostgreSQL source databases | Yes |
| `pgvector` PostgreSQL extension | Not used by the current migrations. The Windows restore helper enables `vector`, so that helper needs the matching PostgreSQL 17 extension files. Manual restores need it only if the archive requires it. | Conditional |
| Node.js 20.19+ or 22.12+ and npm | React/Vite frontend | Yes |
| MySQL-compatible server | Restore/use `digitalbank` as a MySQL source | Only if this source is needed |
| Ollama plus a local model | Scenario assistance, synthetic row generation and PII field classification | Optional; required for those model-assisted paths |
| OIDC identity provider | API bearer-token authentication for shared/production | Required for shared/production; browser token acquisition still needs to be integrated |

TIGR has a FastAPI API and a PostgreSQL-backed outbox worker. In development, `TIGR_INLINE_JOBS=true` lets the API process background work inline; for durable queued processing, set it to `false` and run the independent worker. The frontend is a Vite development server locally. For shared use, serve the built frontend behind a web server or reverse proxy and configure TLS and API routing there; do not expose Vite's development server as a production web server.

The repository includes Windows helper scripts: `deploy_windows.py` provisions a private, loopback-only PostgreSQL 17 cluster and restores the three PostgreSQL backups; `deploy_mysql_windows.py` optionally provisions MySQL 8.4 and restores Digitalbank; `verify_windows_deployment.py` runs migrations and backend tests against a disposable database. These helpers do not install PostgreSQL or MySQL binaries. Follow the Windows steps in Section 4 before running them.

The database backup set currently contains:

- `local-db-backups/TIGR_2026-10-27.dump` — PostgreSQL application database.
- `local-db-backups/CORE_BANKING_SOURCE_2026-10-27.dump` — PostgreSQL source database. The local server did not contain the requested `CORE_BANK_SOURCE`; the actual name is `CORE_BANKING_SOURCE`.
- `local-db-backups/CRM_SOURCE_2026-10-27.dump` — PostgreSQL source database.
- `local-db-backups/digitalbank_2026-10-27.sql` — MySQL `digitalbank` database.
- `local-db-backups/SHA256SUMS` — SHA-256 checksums for the backup files.

The backup inventory and restore examples are also in [`local-db-backups/README.md`](local-db-backups/README.md).

## 2. Deployment rules for Codex

When using this document on the destination host, Codex should:

1. Inspect the operating system, CPU architecture, free disk space, repository contents, backup inventory, and existing database services before installing or restoring anything.
2. Preserve existing files and databases. Never drop, overwrite, or clean a non-empty destination database as an implicit step. If any target database already exists, inspect it and stop before restore until the owner has selected a safe strategy.
3. Confirm that the repository and all four backup files are present. Compare the backup checksums before restore.
4. Install only the required software that is missing. Use the package manager appropriate to the host and current official installation instructions when a package source or installer has changed.
5. Keep `.env`, database passwords, OIDC client secrets, and encryption keys out of git, logs, chat output, and shell history. Restrict `.env` permissions to the deployment account.
6. Ask before making a network service public, opening firewall ports, changing an existing machine's service configuration, or overwriting data.
7. Report any unavailable secret or unsupported source separately. A database restore cannot reconstruct secrets that were never included in the backup.

## 3. Required inputs and secrets

Before restoring the application database, locate the deployment's approved secure copy of these values from the original TIGR environment or its secret manager:

- `TIGR_CREDENTIAL_ENCRYPTION_KEY` — **required to decrypt the saved source connection credentials in the restored TIGR database.** The exact original value is required. A newly generated key cannot decrypt existing credentials.
- `TIGR_IDENTITY_SECRET` — required to preserve consistent protected entity identity hashes and record matching across source refreshes. Reuse the original value if entity locking/identity history already exists.
- `TIGR_MASKING_SECRET` — required if published masking configurations use stable tokenization. Reuse the original value for continuity.
- Source connection secrets — credentials used to connect to PostgreSQL, MySQL, Oracle, SQL Server, or file sources. Saved encrypted credentials in the TIGR database depend on the original encryption key above. Any legacy `env://TIGR_SECRET_*` source references also require their matching environment variables.
- OIDC issuer, audience, JWKS URL, algorithms, and any frontend identity configuration — needed for a shared or production login setup. The current browser UI is not configured to perform an OIDC redirect flow.

These secrets are not present in the database dumps or repository. If an original encryption key is unavailable, TIGR can still be deployed, but saved encrypted source credentials will not be usable. An administrator will need to edit/recreate and retest affected connections with fresh credentials. Do not silently generate a replacement key and assume old credentials still work.

For a brand-new environment with no encrypted credentials to preserve, generate and securely store a fresh Fernet key for `TIGR_CREDENTIAL_ENCRYPTION_KEY`. The backend expects a valid Fernet key. For example, after Python is installed:

```sh
python3.13 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
```

Capture the output directly in the secret manager or private `.env`; do not commit it or paste it into a public log. Generate a stable random `TIGR_IDENTITY_SECRET` and `TIGR_MASKING_SECRET` when the relevant features are enabled, then retain those exact values for the lifetime of the deployment.

## 4. Host preparation and software installation

Use a dedicated deployment account. Have enough free disk for the repository, restored PostgreSQL databases, MySQL data if used, generated artifacts, and temporary restore space. The core banking PostgreSQL dump is about 88 MB compressed; the restored database will use more space.

### macOS

If Homebrew is available, install the main local-development toolchain:

```sh
brew install python@3.13 node@22 postgresql@17
```

Add the Homebrew `postgresql@17` and `node@22` binary directories to the deployment account's `PATH` if Homebrew reports they are keg-only or not linked. Start PostgreSQL using the service mechanism shown by `brew services list` / `brew services start postgresql@17`, or use the organization's managed PostgreSQL service. Install MySQL only if the `digitalbank` source is required; use the official package for the host architecture, then start its service using the installer instructions.

Install Ollama from its official installer only if local model-assisted features are needed. Verify the Ollama service is reachable at the configured URL (default `http://127.0.0.1:11434`).

### Ubuntu/Debian Linux

Install build tools and PostgreSQL 17 server/client from the supported PostgreSQL package source for the OS release. The matching pgvector server extension is needed only for the Windows restore helper or if an inspected archive requires it. Package names and repositories vary by release; Codex should confirm exact package names from the official PostgreSQL packaging instructions. The typical base package set is:

```sh
sudo apt update
sudo apt install -y build-essential libpq-dev ca-certificates curl git
```

Install PostgreSQL 17, then enable/start the service with `systemctl`. Install Python 3.13 using the OS's supported source and ensure `python3.13`, `python3.13-venv`, and pip are available. Install Node.js 22 (or Node 20.19+) with npm from a supported Node.js package source; confirm both `node --version` and `npm --version`. Install MySQL only if the Digitalbank source is needed. Install Ollama only if model-assisted features are needed and follow its official installation instructions.

### Verify versions and services

Before proceeding, confirm:

```sh
python3.13 --version
node --version
npm --version
psql --version
pg_restore --version
```

PostgreSQL server tools should be major version 17 or compatible newer versions. Check that the database server is running and listening only on the intended interface. For local development, loopback (`127.0.0.1`) is sufficient. Do not expose database ports to the network unless required and protected by network controls.

The current migrations do not create or use the `vector` extension. The Windows PostgreSQL helper does run `CREATE EXTENSION vector` for each restored database, so its PostgreSQL 17 installation must include the matching extension files. For manual restores, install the server extension only if the archive's extension list requires it. Never install an extension package built for a different PostgreSQL major version.

### Windows local deployment

The checked-in Windows helpers expect Windows PostgreSQL 17 binaries at `.deployment/postgresql17/pgsql/bin/` and use private data under `.deployment/`. Install or stage a PostgreSQL 17 distribution that includes the `vector` extension, then:

1. Use a dedicated Windows account and restrict the repository's `.deployment/` directory and `.env` file to that account. The helpers store generated database passwords and logs there.
2. Verify the dump checksums using PowerShell before restore:

   ```powershell
   Get-Content local-db-backups/SHA256SUMS | ForEach-Object {
     $parts = $_ -split '\s+', 2
     $expected = $parts[0].ToLowerInvariant()
     $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path . $parts[1])).Hash.ToLowerInvariant()
     if ($actual -ne $expected) { throw "Checksum mismatch: $($parts[1])" }
     "OK $($parts[1])"
   }
   ```

3. Create `.env` from `.env.example`, then set the original `TIGR_CREDENTIAL_ENCRYPTION_KEY` before restoring if saved encrypted connection credentials must remain usable. Preserve the original identity and masking secrets when relevant.
4. Create the Python environment and install the project:

   ```powershell
   py -3.13 -m venv .venv-win
   .\.venv-win\Scripts\python.exe -m pip install --upgrade pip
   .\.venv-win\Scripts\python.exe -m pip install -e ".[dev]"
   ```

5. Run the PostgreSQL restore helper only when `.deployment/pgdata` and `.deployment/database-private.json` do not already exist:

   ```powershell
   .\.venv-win\Scripts\python.exe deploy_windows.py
   ```

   It creates `TIGR`, `CORE_BANKING_SOURCE`, and `CRM_SOURCE`, refuses pre-existing targets, binds PostgreSQL to loopback, restores the archive files, and writes local connection settings to `.env`. It requires the checked-in backup names and a configured encryption key; it does not deploy a shared/production identity service.

   This local helper restores all three PostgreSQL databases with `tigr_app` as owner for convenience. For shared deployments, use the manual source restore in Section 6.4 so source databases have separate owners and TIGR connects through a read-only source role.
6. Install Node.js 20.19+ or 22.12+ and npm, then run `npm ci` in `frontend`. If Digitalbank is required, install MySQL 8.4 at the path expected by `deploy_mysql_windows.py` (`C:\Program Files\MySQL\MySQL Server 8.4\bin`) and run that helper only if `.deployment/mysql-data` and `.deployment/mysql.ini` are absent.
7. Use the Windows-specific dependency, migration, startup and verification commands in Sections 9–13. These use `.venv-win\Scripts\python.exe` and PowerShell syntax.

## 5. Prepare the TIGR files

1. Put the repository, including the `backend/`, `frontend/`, `Docs/`, and `local-db-backups/` directories, in the chosen deployment directory. Preserve relative paths.
2. Confirm the four dump files and `SHA256SUMS` exist:

   ```sh
   ls -lh local-db-backups
   ```

3. From the repository root, verify checksums:

   ```sh
   shasum -a 256 -c local-db-backups/SHA256SUMS
   ```

   On Linux without `shasum`, use `sha256sum -c local-db-backups/SHA256SUMS` if the checksum file's paths resolve from the repository root. If they do not, run the verification from `local-db-backups` with the relative prefix adjusted. All four files must report `OK` before restore.

4. Confirm the archive formats without restoring:

   ```sh
   pg_restore --list local-db-backups/TIGR_2026-10-27.dump
   pg_restore --list local-db-backups/CORE_BANKING_SOURCE_2026-10-27.dump
   pg_restore --list local-db-backups/CRM_SOURCE_2026-10-27.dump
   ```

5. Keep `local-db-backups/` private. The existing dump files are mode-restricted on the original machine; set restrictive permissions again after copying if the transfer process changed them.

## 6. Set up PostgreSQL roles and restore the databases

Restore to an empty PostgreSQL 17 instance. The commands below use `postgres` as the database administrator and `tigr_app` as the owner of restored TIGR objects. Replace host, port, and administrator options for a managed server. Use a password prompt or secret manager; do not put a real password in a command line.

### 6.1 Check for name conflicts

Connect as a PostgreSQL administrator and inspect existing databases before creating anything:

```sh
psql --host=127.0.0.1 --port=5432 --username=postgres --dbname=postgres -c '\l'
```

If `TIGR`, `CORE_BANKING_SOURCE`, or `CRM_SOURCE` already exists, do not drop or replace it. Inspect its contents, confirm whether it is the intended target, and get an explicit restore plan. Restore only into empty targets.

### 6.2 Create the application owner

Create a role for the application and set its password interactively:

```sh
createuser --host=127.0.0.1 --port=5432 --username=postgres --login --pwprompt tigr_app
```

Keep this application password in the secret manager or private `.env`. Do not use the PostgreSQL superuser as the TIGR runtime user.

### 6.3 Create and restore TIGR

```sh
createdb --host=127.0.0.1 --port=5432 --username=postgres --owner=tigr_app TIGR
pg_restore --host=127.0.0.1 --port=5432 --username=tigr_app --no-owner --no-privileges --dbname=TIGR local-db-backups/TIGR_2026-10-27.dump
```

Restoring as `tigr_app` makes the restored objects belong to the runtime role. If the restore reports a missing extension, install the matching PostgreSQL 17 extension package, enable the extension using an administrator account in the `TIGR` database, then retry the restore only after confirming it remains empty or cleaning up only the partial restore that this deployment just created.

### 6.4 Restore PostgreSQL source databases (optional)

Restore these if the saved TIGR connections or workflows use them. TIGR itself does not require them to start. For shared deployments, keep source database ownership separate from the TIGR runtime role and connect TIGR through a dedicated read-only source account.

```sh
createdb --host=127.0.0.1 --port=5432 --username=postgres --owner=postgres CORE_BANKING_SOURCE
pg_restore --host=127.0.0.1 --port=5432 --username=postgres --no-owner --no-privileges --dbname=CORE_BANKING_SOURCE local-db-backups/CORE_BANKING_SOURCE_2026-10-27.dump

createdb --host=127.0.0.1 --port=5432 --username=postgres --owner=postgres CRM_SOURCE
pg_restore --host=127.0.0.1 --port=5432 --username=postgres --no-owner --no-privileges --dbname=CRM_SOURCE local-db-backups/CRM_SOURCE_2026-10-27.dump

createuser --host=127.0.0.1 --port=5432 --username=postgres --login --pwprompt tigr_source_reader
psql --host=127.0.0.1 --port=5432 --username=postgres --dbname=CORE_BANKING_SOURCE -c 'GRANT CONNECT ON DATABASE "CORE_BANKING_SOURCE" TO tigr_source_reader'
psql --host=127.0.0.1 --port=5432 --username=postgres --dbname=CORE_BANKING_SOURCE -c 'GRANT USAGE ON SCHEMA public TO tigr_source_reader'
psql --host=127.0.0.1 --port=5432 --username=postgres --dbname=CORE_BANKING_SOURCE -c 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO tigr_source_reader'
psql --host=127.0.0.1 --port=5432 --username=postgres --dbname=CRM_SOURCE -c 'GRANT CONNECT ON DATABASE "CRM_SOURCE" TO tigr_source_reader'
psql --host=127.0.0.1 --port=5432 --username=postgres --dbname=CRM_SOURCE -c 'GRANT USAGE ON SCHEMA public TO tigr_source_reader'
psql --host=127.0.0.1 --port=5432 --username=postgres --dbname=CRM_SOURCE -c 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO tigr_source_reader'
```

The example grants access to the `public` schema; inspect each restored source and adjust schema/table grants to the actual required objects. Apply default privileges for future source tables when appropriate. Do not use the TIGR application role or a PostgreSQL superuser as a source connection credential. Update/retest TIGR connection profiles with the reader account and destination host/port.

## 7. Restore MySQL Digitalbank (optional)

Install/start a MySQL-compatible server only if the Digitalbank source is needed. Before restore, inspect existing databases and stop if `digitalbank` already exists with data:

```sh
mysql --host=127.0.0.1 --port=3306 --user=root -e 'SHOW DATABASES;'
```

Restore the included dump using an account with create database, table, trigger, routine, and event privileges:

```sh
mysql --host=127.0.0.1 --port=3306 --user=root < local-db-backups/digitalbank_2026-10-27.sql
```

The SQL file includes the database creation and selection statements. For shared environments, create a separate least-privilege read-only account for TIGR's source connection and configure the profile with the new host and credentials. Never paste the password into shell history.

## 8. Configure TIGR

From the repository root:

1. Copy the template and restrict access:

   ```sh
   cp .env.example .env
   chmod 600 .env
   ```

2. Edit `.env` with the destination-specific values. At minimum configure:

   ```dotenv
   TIGR_ENV=development
   TIGR_DATABASE_URL=postgresql+psycopg://tigr_app:URL_ENCODED_PASSWORD@127.0.0.1:5432/TIGR
   TIGR_AUTH_MODE=development
   TIGR_CORS_ORIGINS=http://localhost:5174,http://127.0.0.1:5174
   TIGR_INLINE_JOBS=false
   TIGR_API_PREFIX=/api/v1
   TIGR_FILE_ROOTS=Docs/source_data
   TIGR_OLLAMA_URL=http://127.0.0.1:11434
   TIGR_OLLAMA_MODEL=gemma3:latest
   TIGR_OLLAMA_REQUEST_TIMEOUT_SECONDS=480
   TIGR_CREDENTIAL_ENCRYPTION_KEY=USE_ORIGINAL_KEY_FOR_RESTORED_CREDENTIALS
   TIGR_IDENTITY_SECRET=USE_ORIGINAL_STABLE_SECRET
   TIGR_MASKING_SECRET=USE_ORIGINAL_STABLE_SECRET_IF_TOKENIZATION_IS_USED
   ```

   URL-encode special characters in a PostgreSQL password. A random hexadecimal password avoids URL-reserved characters. Do not use the sample placeholders literally. Do not copy the original local socket URL (`postgresql+psycopg:///TIGR?host=/tmp`) to another OS; use a destination-specific host, port, username, and password or its managed database URL.

3. For a **shared/production** installation, set `TIGR_ENV=production` and `TIGR_AUTH_MODE=oidc`, and configure `TIGR_OIDC_ISSUER`, `TIGR_OIDC_AUDIENCE`, `TIGR_OIDC_JWKS_URL`, and `TIGR_OIDC_ALGORITHMS`. Configure the identity provider and a browser token-acquisition flow before opening user access. The backend validates bearer tokens, but the current browser UI only implements development sign-in; it has no OIDC redirect/token acquisition flow.

4. Keep Neo4j unset unless a compatible service and adapter have been configured. The current ready endpoint reports graph as not configured; PostgreSQL is TIGR's authoritative application database.

5. If any source uses legacy environment secret references, set the referenced names privately, for example `TIGR_SECRET_LOCAL_MYSQL`. New connection credentials are entered in the TIGR UI and encrypted in the app database using `TIGR_CREDENTIAL_ENCRYPTION_KEY`.

6. Ensure the configured file roots exist and are restricted to the deployment account:

   ```sh
   mkdir -p Docs/source_data
   ```

   File source paths are confined to `TIGR_FILE_ROOTS`. Generated dataset records and artifacts are stored in PostgreSQL by the current implementation; include the application database in backup/retention planning.

   The current `.env.example` still contains `TIGR_ARTIFACT_ROOT` as a legacy entry. It is not read by the current backend settings; generated artifacts are recorded in PostgreSQL, so no `Docs/generated_data` directory is required for those outputs.

## 9. Install Python and Node dependencies

Use the exact repository lock files where available. The backend requires Python `>=3.13,<3.14`; the frontend package lock is `frontend/package-lock.json`.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'

cd frontend
npm ci
cd ..
```

On Windows, use the environment prepared in Section 4:

```powershell
.\.venv-win\Scripts\python.exe -m pip install --upgrade pip
.\.venv-win\Scripts\python.exe -m pip install -e ".[dev]"
Push-Location frontend
npm ci
Pop-Location
```

The Python project installs FastAPI/Uvicorn, SQLAlchemy/psycopg, Alembic, the Python pgvector adapter, Pydantic settings, credential encryption, MySQL/Oracle/SQL Server source drivers, and TIGR's application dependencies. Current migrations do not use a vector column. The `dev` extra includes pytest, httpx, and Ruff for local maintenance. `npm ci` installs the React, TypeScript, Vite and icon libraries from `frontend/package-lock.json`.

For Oracle connections, the Python driver uses its thin mode and normally does not need Oracle Instant Client. For SQL Server, the `pymssql` dependency may require FreeTDS/native build prerequisites on some operating systems; install the host's supported FreeTDS development package if pip cannot install its wheel. These drivers are only necessary when those connectors are used.

## 10. Apply/verify migrations

The app backup contains its migration state. First inspect it:

```sh
.venv/bin/alembic current
```

On Windows:

```powershell
.\.venv-win\Scripts\python.exe -m alembic current
```

The current repository migration head is `0043_relational_sources_read_write`. Apply any migrations required by the checked-out code:

```sh
.venv/bin/alembic upgrade head
```

On Windows:

```powershell
.\.venv-win\Scripts\python.exe -m alembic upgrade head
```

Confirm the database is at the expected head:

```sh
.venv/bin/alembic current
```

On Windows:

```powershell
.\.venv-win\Scripts\python.exe -m alembic current
```

Do not downgrade the restored database to match an older checkout. If the database revision is newer than the repository's migration head, stop and obtain the matching code version. Run migrations with the configured `TIGR_DATABASE_URL`; do not point them at a source database.

## 11. Start TIGR services locally

Use separate terminal sessions or managed service units. Start PostgreSQL first.

### API

From the repository root:

```sh
PYTHONPATH=backend .venv/bin/python -m uvicorn tigr.main:app --app-dir backend --host 127.0.0.1 --port 8001
```

On Windows, start a PowerShell session from the repository root:

```powershell
$env:PYTHONPATH = 'backend'
.\.venv-win\Scripts\python.exe -m uvicorn tigr.main:app --app-dir backend --host 127.0.0.1 --port 8001
```

### Durable worker

In another terminal, from the repository root:

```sh
PYTHONPATH=backend .venv/bin/python -m tigr.outbox_worker
```

On Windows, start a separate PowerShell session and use:

```powershell
$env:PYTHONPATH = 'backend'
.\.venv-win\Scripts\python.exe -m tigr.outbox_worker
```

Keep `TIGR_INLINE_JOBS=false` when running this independent worker. Running the API without the worker allows the UI/API to load but queued generation and masking jobs will not be processed.

### Frontend

In another terminal:

```sh
cd frontend
npm run dev -- --host 127.0.0.1 --port 5174
```

Open `http://localhost:5174`. The Vite proxy forwards `/api` to `http://127.0.0.1:8001`. API documentation is at `http://127.0.0.1:8001/api/v1/docs`.

On Windows, run the frontend in another PowerShell session:

```powershell
Push-Location frontend
npm run dev -- --host 127.0.0.1 --port 5174
Pop-Location
```

### Local development authentication

With `TIGR_ENV=development` and `TIGR_AUTH_MODE=development`, the login page loads active user/workspace memberships from `/api/v1/dev-auth/options`, then signs in through `/api/v1/dev-auth/login`. Password is left blank. The UI sends the selected subject as `X-Dev-Subject`; the API resolves the active workspace role from PostgreSQL membership records. There is no `X-Dev-Role` authentication header. This passwordless fixture is local-development only and must not be exposed on a shared or production network.

## 12. Optional local model setup

If Scenario-to-Data assistance or PII field classification is required:

1. Install Ollama using the official installer for the destination OS.
2. Start its service and confirm `http://127.0.0.1:11434` is reachable from the TIGR API process.
3. Select and download a Gemma model supported by the configured Ollama version. The repository `.env.example` currently selects `gemma3:latest`. Set `TIGR_OLLAMA_MODEL` to that installed model name; `auto` searches installed model tags for a Gemma model.
4. Restart the API after changing the environment.

The current AI-assisted paths are Scenario-to-Data (requirement interpretation, source-schema mapping, join/filter suggestions, SQL assistance and synthetic row generation) and PII field classification. Scenario prompts include the user's requirement, schema/field metadata and reviewed query or mapping information. The PII classifier sends dataset name/description/pathway and field metadata, not dataset record values. Source-backed rows are read and handled by backend generation/masking code, not passed to Ollama. One exception to the metadata-only pattern is synthetic-version uniqueness handling: if a generated identity value conflicts with a prior version, the retry prompt includes the conflicting prior identity value so the model can avoid reusing it. Those values are from synthetic output, but still treat prompt traffic as potentially containing data-derived identifiers. Keep Ollama on a trusted local host unless a separate privacy/security review authorizes another endpoint. Masking transformations and dataset validation are applied by TIGR after human review/approval.

## 13. Verify the deployment

Run these checks from the destination host:

```sh
curl --fail http://127.0.0.1:8001/api/v1/health/live
curl --fail http://127.0.0.1:8001/api/v1/health/ready
```

On Windows, use:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/api/v1/health/live
Invoke-RestMethod http://127.0.0.1:8001/api/v1/health/ready
```

Then verify all of the following:

- The frontend loads at `http://localhost:5174` and can reach the API.
- The ready endpoint returns HTTP 200 and reports PostgreSQL as ready.
- Existing TIGR user/workspace, connection, dataset, and ticket records appear as expected.
- The outbox worker remains running and can claim a safe test job.
- Each restored source database is reachable using its intended, least-privilege source account; retest connections in TIGR if hostnames, ports, passwords, or TLS changed.
- Credential decryption succeeds for a saved profile if the original `TIGR_CREDENTIAL_ENCRYPTION_KEY` was recovered. Do not expose decrypted values during this check.
- File imports/exports use only the configured private file roots.
- If enabled, Ollama is reachable and the configured model is installed.
- For a production deployment, browser authentication, TLS, reverse proxy routes, CORS origins, secrets, backups, and logs are configured before user access is enabled.

Build the frontend to verify production assets compile:

```sh
cd frontend
npm run build
cd ..
```

On Windows:

```powershell
Push-Location frontend
npm run build
Pop-Location
```

The build output is in `frontend/dist`. Serve it using the deployment's web server/reverse proxy and route `/api` to the API service. Configure the actual browser origin in `TIGR_CORS_ORIGINS` when the frontend and API use different origins.

## 14. Production service operations

For a persistent/shared deployment, run API and worker as separate managed services (for example, systemd on Linux or the host's service manager). The service definitions should:

- Run as a dedicated unprivileged OS account.
- Use the repository's virtual environment and set the repository root as the working directory.
- Load secrets from the approved secret manager or a root-readable protected environment file.
- Restart on failure and write logs to the platform's protected logging service.
- Keep the worker separate from the API, with `TIGR_INLINE_JOBS=false`.
- Bind the API to loopback behind a TLS reverse proxy unless direct network binding is explicitly required and protected.
- Keep PostgreSQL/MySQL source ports private; allow only the TIGR host or approved network.

Do not use `--reload`, Vite's dev server, or development authentication in production. The repository does not provide a complete production browser login deployment or production web-server configuration; Codex must configure these for the chosen hosting environment before calling the deployment production-ready.

## 15. Backup, restore, and recovery notes

- Back up the TIGR application database (including dataset records/artifact metadata), source databases, private environment/secrets, and configured source files according to the deployment's retention policy.
- Database backups alone are not a complete recovery set. Securely escrow the original encryption, identity, and masking keys separately from database dumps.
- After deployment, verify backups by checksum and periodically rehearse restores into isolated, disposable databases.
- Never test restore procedures over the only working database.
- Preserve PostgreSQL major-version compatibility and install matching server extensions before a restore.
- Use `local-db-backups/README.md` for the compact archive inventory and basic restore commands; this document is the full application deployment procedure.

## 16. Troubleshooting

| Symptom | Check |
|---|---|
| API does not start or ready check returns 503 | Check `TIGR_DATABASE_URL`, PostgreSQL service/listener, database existence, password encoding, and API logs. Run `psql` using the same destination host/user/database. |
| `pg_restore` reports unknown version or archive error | Verify checksums and use PostgreSQL 17 or newer client tools. Confirm the dump was copied in binary mode and is non-empty. |
| Windows restore helper fails on `vector` extension | Install PostgreSQL 17 extension files matching the helper's PostgreSQL binaries. Current migrations do not need the extension; manual archive restores should install it only when the archive requires it. |
| Restored saved connections cannot decrypt | `TIGR_CREDENTIAL_ENCRYPTION_KEY` is missing, malformed, or not the original key. Recover the original key; otherwise recreate connection credentials. |
| Identity matching changes after migration | Restore the original stable `TIGR_IDENTITY_SECRET`; do not rotate it without a planned key migration. |
| Generation or masking jobs remain queued | Confirm the durable worker is running, points to the same `TIGR_DATABASE_URL`, and has access to the same required source secrets and files. |
| Browser shows API/network errors | Confirm API is on port 8001, Vite proxy is active, CORS includes the browser origin, and browser hostnames resolve to the configured API. |
| MySQL connection fails | Confirm MySQL service, host/port, account grants, authentication plugin compatibility, and the selected database allowlist. |
| Oracle or SQL Server driver install fails | Check Python 3.13 wheel/platform availability and native prerequisites; SQL Server may require FreeTDS build/runtime packages. |
| PII classification fails | Confirm Ollama is running, the configured URL is reachable, and the selected model is installed and responds locally. |
| Development auth is rejected | Confirm both `TIGR_ENV=development` and `TIGR_AUTH_MODE=development`; this fixture is not for shared production use. |

## 17. Completion report expected from Codex

At the end, report the host OS, installed runtime/database versions, restored database names, migration revision, API/worker/frontend service status, health-check results, and any unavailable secrets or optional integrations. Do not include passwords, encryption keys, or unredacted credential URLs in that report.
