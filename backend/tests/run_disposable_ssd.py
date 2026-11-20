"""Windows verification against a new disposable database; never uses live source data."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from cryptography.fernet import Fernet
from psycopg import sql


def main():
    root = Path(__file__).resolve().parents[2]
    credentials = json.loads((root / ".deployment/database-private.json").read_text())
    name = "tigr_ssd_verify_" + uuid4().hex
    options = {
        "host": "127.0.0.1",
        "user": "postgres",
        "password": credentials["admin_password"],
        "autocommit": True,
    }
    with psycopg.connect(**options, dbname="postgres") as db:
        db.execute(sql.SQL("CREATE DATABASE {} OWNER tigr_app").format(sql.Identifier(name)))
    try:
        with psycopg.connect(**options, dbname=name) as db:
            db.execute("CREATE EXTENSION vector")
            db.execute("CREATE TABLE public.alembic_version (version_num varchar(128) NOT NULL PRIMARY KEY)")
            db.execute("ALTER TABLE public.alembic_version OWNER TO tigr_app")
        env = os.environ | {
            "TIGR_DATABASE_URL": f"postgresql+psycopg://tigr_app:{credentials['app_password']}@127.0.0.1:5432/{name}",
            "TIGR_CREDENTIAL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
            "TIGR_SSD_ENABLED": "true",
            "TIGR_SSD_DISPOSABLE_TEST": "true",
        }
        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        (root / "data/ssd-migration-check.log").write_text(
            migration.stdout + migration.stderr, encoding="utf-8"
        )
        if migration.returncode:
            print("Migration failed; see data/ssd-migration-check.log")
            return 1
        tests = sys.argv[1:] or ["backend/tests"]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *tests,
                "-q",
                "--tb=short",
                "--basetemp",
                str(root / "data" / ("ssd-pytest-" + uuid4().hex)),
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        (root / "data/ssd-test-results.log").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        print(result.stdout[-12000:])
        return result.returncode
    finally:
        with psycopg.connect(**options, dbname="postgres") as db:
            db.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        print("Disposable SSD verification database removed.")


if __name__ == "__main__":
    raise SystemExit(main())
