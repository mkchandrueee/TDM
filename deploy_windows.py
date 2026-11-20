"""Install the verified TIGR backups into a new private Windows PG cluster.

Run with .venv-win/Scripts/python.exe. Never overwrites an existing cluster/database.
Secrets are written only beneath the account-restricted .deployment/ and .env.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess

import psycopg
from psycopg import sql
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
PRIVATE = ROOT / '.deployment'
BIN = PRIVATE / 'postgresql17/pgsql/bin'
DATA = PRIVATE / 'pgdata'
STATE = PRIVATE / 'database-private.json'


def run(name, *args, **kwargs):
    return subprocess.run([str(BIN / (name + '.exe')), *map(str, args)], check=True, **kwargs)


def main():
    if DATA.exists() or STATE.exists():
        raise SystemExit('Existing cluster or deployment credentials found; inspect before continuing.')
    state = {'admin_password': secrets.token_hex(32), 'app_password': secrets.token_hex(32)}
    STATE.write_text(json.dumps(state), encoding='utf-8')
    pwfile = PRIVATE / 'init-password'
    pwfile.write_text(state['admin_password'], encoding='ascii')
    try:
        run('initdb', '-D', DATA, '-U', 'postgres', '--encoding=UTF8', '--locale=C',
            '--auth=scram-sha-256', '--pwfile', pwfile)
    finally:
        pwfile.unlink(missing_ok=True)
    with (DATA / 'postgresql.conf').open('a', encoding='utf-8') as f:
        f.write("\n# TIGR private local deployment\nlisten_addresses = '127.0.0.1'\nport = 5432\n")
    run('pg_ctl', '-D', DATA, '-l', PRIVATE / 'postgresql.log', '-w', 'start')
    connection = dict(host='127.0.0.1', port=5432, user='postgres',
                      password=state['admin_password'], dbname='postgres', autocommit=True)
    names = ['TIGR', 'CORE_BANKING_SOURCE', 'CRM_SOURCE']
    with psycopg.connect(**connection) as db:
        existing = {r[0] for r in db.execute('SELECT datname FROM pg_database')}
        if existing.intersection(names):
            raise SystemExit('Target database already exists. Restore stopped.')
        db.execute(sql.SQL('CREATE ROLE tigr_app LOGIN PASSWORD {}').format(sql.Literal(state['app_password'])))
        for name in names:
            db.execute(sql.SQL('CREATE DATABASE {} OWNER tigr_app').format(sql.Identifier(name)))
    for name in names:
        with psycopg.connect(**(connection | {'dbname': name})) as db:
            db.execute('CREATE EXTENSION vector')
        env = os.environ | {'PGPASSWORD': state['app_password']}
        with (PRIVATE / f'restore-{name}.log').open('w', encoding='utf-8') as log:
            run('pg_restore', '--host=127.0.0.1', '--port=5432', '--username=tigr_app',
                '--no-owner', '--no-privileges', '--no-comments', '--exit-on-error', f'--dbname={name}',
                ROOT / f'local-db-backups/{name}_2026-10-27.dump',
                env=env, stdout=log, stderr=log)
        print(f'Restored {name}')
    values = dotenv_values(ROOT / '.env')
    if not values.get('TIGR_CREDENTIAL_ENCRYPTION_KEY'):
        raise SystemExit('Original credential key unavailable; .env not changed.')
    settings = {
        'TIGR_DATABASE_URL': f"postgresql+psycopg://tigr_app:{state['app_password']}@127.0.0.1:5432/TIGR",
        'TIGR_ENV': 'development', 'TIGR_AUTH_MODE': 'development',
        'TIGR_CORS_ORIGINS': 'http://localhost:5174,http://127.0.0.1:5174',
        'TIGR_INLINE_JOBS': 'false', 'TIGR_API_PREFIX': '/api/v1',
        'TIGR_FILE_ROOTS': 'Docs/source_data', 'TIGR_ARTIFACT_ROOT': 'Docs/generated_data',
    }
    for key, value in settings.items():
        set_key(ROOT / '.env', key, value)
    for folder in ['Docs/source_data', 'Docs/generated_data']:
        (ROOT / folder).mkdir(parents=True, exist_ok=True)
    print('Local settings saved; original encryption and identity keys preserved.')


if __name__ == '__main__':
    main()
