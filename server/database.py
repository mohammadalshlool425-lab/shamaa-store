"""SQLite, one connection per operation. BEGIN IMMEDIATE serializes stock writes.
This adapter supports a single-host deployment, NOT PostgreSQL by environment switch.
WAL is the default; DELETE is available for restricted/shared-file WSGI previews.
"""
from contextlib import contextmanager
import sqlite3
import time
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name('schema.sql')


def now():
    return int(time.time())


class Database:
    def __init__(self, path, journal_mode='WAL'):
        if journal_mode not in ('WAL', 'DELETE'):
            raise ValueError('Unsupported SQLite journal mode')
        self.path = str(path)
        self.journal_mode = journal_mode

    def connect(self):
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA busy_timeout=15000')
        return c

    @contextmanager
    def read(self):
        c = self.connect()
        try:
            yield c
        finally:
            c.close()

    @contextmanager
    def transaction(self):
        c = self.connect()
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def initialize(self):
        with self.read() as c:
            # Values are strictly whitelisted above; PRAGMA cannot bind this parameter.
            actual = c.execute('PRAGMA journal_mode='+self.journal_mode).fetchone()[0]
            if actual.upper()!=self.journal_mode:
                raise RuntimeError('SQLite journal mode could not be applied')
            version = c.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise RuntimeError('Database is newer than this application. Do not downgrade the schema.')
            if version == 0:
                c.executescript('BEGIN IMMEDIATE;\n' + SCHEMA_PATH.read_text(encoding='utf-8') + '\nPRAGMA user_version=1; COMMIT;')
        Path(self.path).chmod(0o600)
