"""Provision read-only local source roles and retest restored source profiles."""
import json
from pathlib import Path
import secrets
from datetime import UTC, datetime

from dotenv import load_dotenv
import psycopg
from psycopg import sql
import pymysql

load_dotenv()
from tigr.main import app  # Register all ORM models.
from tigr.db import SessionLocal
from tigr.connection_models import WorkspaceConnection
from tigr.connection_testing import test_connection
from tigr.secrets import decrypt_credentials, encrypt_credentials
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / '.deployment'


def main():
    backup = PRIVATE / 'original-source-profiles.json'
    if backup.exists():
        raise SystemExit('Source configuration backup exists; inspect before repeating.')
    admin = json.loads((PRIVATE / 'database-private.json').read_text())
    password = secrets.token_hex(32)
    (PRIVATE / 'source-reader-private.json').write_text(json.dumps({'username': 'tigr_source_reader', 'password': password}))
    connection = dict(host='127.0.0.1', user='postgres', password=admin['admin_password'], autocommit=True)
    with psycopg.connect(**connection, dbname='postgres') as db:
        db.execute(sql.SQL('CREATE ROLE tigr_source_reader LOGIN PASSWORD {}').format(sql.Literal(password)))
        db.execute('ALTER ROLE tigr_source_reader SET default_transaction_read_only = on')
    for name in ['CORE_BANKING_SOURCE', 'CRM_SOURCE']:
        with psycopg.connect(**connection, dbname=name) as db:
            db.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO tigr_source_reader').format(sql.Identifier(name)))
            schemas = db.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'").fetchall()
            for (schema,) in schemas:
                db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO tigr_source_reader').format(sql.Identifier(schema)))
                db.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA {} TO tigr_source_reader').format(sql.Identifier(schema)))
    mysql = json.loads((PRIVATE / 'mysql-private.json').read_text())
    with pymysql.connect(host='127.0.0.1', user='root', password=mysql['root_password'], autocommit=True) as db:
        with db.cursor() as cursor:
            cursor.execute("CREATE USER 'tigr_source_reader'@'127.0.0.1' IDENTIFIED BY %s", (password,))
            cursor.execute("GRANT SELECT, SHOW VIEW ON digitalbank.* TO 'tigr_source_reader'@'127.0.0.1'")
    with SessionLocal() as db:
        rows = db.scalars(select(WorkspaceConnection)).all()
        backup.write_text(json.dumps([{'id': str(r.id), 'reference': r.secret_reference, 'status': r.status} for r in rows]))
        for row in rows:
            if row.direction != 'source' or row.connector_type not in ('postgresql', 'mysql'):
                raise SystemExit('Unexpected profile; configuration stopped.')
            data = decrypt_credentials(row.secret_reference)
            data.update(host='127.0.0.1', port=5432 if row.connector_type == 'postgresql' else 3306,
                        username='tigr_source_reader', password=password)
            if row.connector_type == 'mysql':
                data['initial_database'] = 'digitalbank'
            row.secret_reference = encrypt_credentials(row.connector_type, data)
            old_status = row.status
            row.status = 'draft'
            row.updated_by = 'windows-deployment'
            result = test_connection(row)
            if not result.succeeded:
                raise SystemExit(f'{row.connector_type} source test failed; profile changes rolled back.')
            row.tested_at = datetime.now(UTC)
            row.tested_by = 'windows-deployment'
            row.test_result = {'succeeded': True, 'message': result.message}
            row.structure_preview = result.structure
            row.status = 'enabled' if old_status == 'enabled' else 'tested'
            print(row.connector_type, 'read-only source test: OK')
        db.commit()


if __name__ == '__main__':
    main()
