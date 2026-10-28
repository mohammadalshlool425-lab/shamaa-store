"""SQLite, one connection per operation. BEGIN IMMEDIATE serializes stock writes.
This adapter supports a single-host deployment, NOT PostgreSQL by environment switch.
"""
from contextlib import contextmanager
import sqlite3
import time
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name('schema.sql')


def now():
    return int(time.time())


class Database:
    def __init__(self, path):
        self.path = str(path)

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
            c.execute('PRAGMA journal_mode=WAL')
            version = c.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise RuntimeError('Database is newer than this application. Do not downgrade the schema.')
            if version == 0:
                c.executescript('BEGIN IMMEDIATE;\n' + SCHEMA_PATH.read_text(encoding='utf-8') + '\nPRAGMA user_version=1; COMMIT;')
        Path(self.path).chmod(0o600)
