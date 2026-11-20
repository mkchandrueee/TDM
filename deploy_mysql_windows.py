"""Restore Digitalbank into a new private, loopback-only MySQL data directory."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

import pymysql

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / '.deployment'
BIN = Path('C:/Program Files/MySQL/MySQL Server 8.4/bin')
DATA = PRIVATE / 'mysql-data'
CONFIG = PRIVATE / 'mysql.ini'


def main():
    if DATA.exists() or CONFIG.exists():
        raise SystemExit('Existing MySQL deployment found; inspect before restore.')
    password = secrets.token_hex(32)
    (PRIVATE / 'mysql-private.json').write_text(json.dumps({'root_password': password}), encoding='utf-8')
    CONFIG.write_text(
        '[mysqld]\n' + f'basedir={BIN.parent.as_posix()}\ndatadir={DATA.as_posix()}\n'
        'bind-address=127.0.0.1\nport=3306\nmysqlx=0\n'
        f'log-error={(PRIVATE / "mysql-error.log").as_posix()}\n', encoding='utf-8')
    with (PRIVATE / 'mysql-init.log').open('w') as log:
        subprocess.run([str(BIN / 'mysqld.exe'), f'--defaults-file={CONFIG}', '--initialize-insecure'],
                       check=True, stdout=log, stderr=log)
    init = PRIVATE / 'mysql-set-password.sql'
    init.write_text(f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{password}';\n", encoding='ascii')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    process = subprocess.Popen([str(BIN / 'mysqld.exe'), f'--defaults-file={CONFIG}',
                                f'--init-file={init.as_posix()}'], creationflags=flags)
    (PRIVATE / 'mysql-process.json').write_text(json.dumps({'pid': process.pid}))
    db = None
    try:
        for _ in range(120):
            try:
                db = pymysql.connect(host='127.0.0.1', user='root', password=password, port=3306, autocommit=True)
                break
            except pymysql.Error:
                if process.poll() is not None:
                    raise SystemExit('MySQL exited during startup; inspect protected error log.')
                time.sleep(1)
        if db is None:
            raise SystemExit('MySQL startup timed out.')
    finally:
        init.unlink(missing_ok=True)
    with db.cursor() as cursor:
        cursor.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='digitalbank'")
        if cursor.fetchone():
            raise SystemExit('Digitalbank already exists; restore stopped.')
    client = PRIVATE / 'mysql-client.ini'
    client.write_text(f'[client]\nhost=127.0.0.1\nport=3306\nuser=root\npassword={password}\n', encoding='ascii')
    try:
        with (ROOT / 'local-db-backups/digitalbank_2026-10-27.sql').open('rb') as dump, (PRIVATE / 'restore-digitalbank.log').open('wb') as log:
            subprocess.run([str(BIN / 'mysql.exe'), f'--defaults-extra-file={client}'],
                           stdin=dump, stdout=log, stderr=log, check=True)
    finally:
        client.unlink(missing_ok=True)
    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='digitalbank'")
        print('Digitalbank restored; table count:', cursor.fetchone()[0])
    db.close()


if __name__ == '__main__':
    main()
