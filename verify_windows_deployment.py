"""Run backend tests on a disposable empty migrated database, never the live DB."""
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / '.deployment'
BIN = PRIVATE / 'postgresql17/pgsql/bin'
PYTHON = ROOT / '.venv-win/Scripts/python.exe'


def main():
    credentials = json.loads((PRIVATE / 'database-private.json').read_text())
    name = 'tigr_deployment_verify'
    options = dict(host='127.0.0.1', user='postgres', password=credentials['admin_password'], autocommit=True)
    with psycopg.connect(**options, dbname='postgres') as db:
        if db.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone():
            raise SystemExit('Verification database already exists; inspect before reuse.')
        db.execute(sql.SQL('CREATE DATABASE {} OWNER tigr_app').format(sql.Identifier(name)))
    try:
        with psycopg.connect(**options, dbname=name) as db:
            db.execute('CREATE EXTENSION vector')
        env = os.environ | {'PGPASSWORD': credentials['app_password'],
            'TIGR_DATABASE_URL': f"postgresql+psycopg://tigr_app:{credentials['app_password']}@127.0.0.1:5432/{name}"}
        with (PRIVATE / 'verification-migrations.log').open('w') as log:
            subprocess.run([str(PYTHON), '-m', 'alembic', 'upgrade', 'head'], env=env,
                           stdout=log, stderr=log, check=True)
        result = subprocess.run([str(PYTHON), '-m', 'pytest', 'backend/tests', '-q', '--tb=short',
                                 '--basetemp', str(PRIVATE / ('pytest-' + uuid4().hex))],
                                env=env, capture_output=True, text=True)
        (PRIVATE / 'backend-tests.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        print('Backend test exit code:', result.returncode)
        for line in result.stdout.splitlines():
            if re.search(r'\d+ (passed|failed)', line):
                print(line)
    finally:
        with psycopg.connect(**options, dbname='postgres') as db:
            db.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        print('Disposable verification database removed.')


if __name__ == '__main__':
    main()
