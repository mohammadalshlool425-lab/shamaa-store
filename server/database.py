"""SQLite, one connection per operation. BEGIN IMMEDIATE serializes stock writes.
This adapter supports a single-host deployment, NOT PostgreSQL by environment switch.
"""
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, ContextManager, Iterator, Protocol, Sequence, runtime_checkable
import sqlite3
import time
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name('schema.sql')
MIGRATION_DIR = Path(__file__).with_name('migrations')
LATEST_SCHEMA_VERSION = 7


class HybridRow(Mapping[str, Any]):
    """Immutable row supporting both SQLite-style indexes and mapping access.

    Existing application code uses row[0], row["column"], and dict(row).
    PostgreSQL drivers commonly expose mapping rows, so this small adapter
    establishes one explicit result-row contract for both database backends.
    """

    __slots__ = ("_columns", "_values", "_first_index")

    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        if len(columns) != len(values):
            raise ValueError("Row column and value counts must match")
        self._columns = tuple(columns)
        self._values = tuple(values)
        first_index: dict[str, int] = {}
        for index, column in enumerate(self._columns):
            first_index.setdefault(column, index)
        self._first_index = first_index

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        if isinstance(key, str):
            try:
                return self._values[self._first_index[key]]
            except KeyError:
                raise KeyError(key) from None
        raise TypeError("Row keys must be column names or integer indexes")

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)


def qmark_to_format(sql: str) -> str:
    """Translate DB-API qmark placeholders to psycopg %s placeholders.

    Question marks inside SQL strings, quoted identifiers, comments, and
    PostgreSQL dollar-quoted bodies are preserved. This deliberately handles
    only the application's qmark parameter style, not PostgreSQL JSON '?' ops.
    """
    output: list[str] = []
    i = 0
    length = len(sql)
    block_depth = 0
    dollar_tag: str | None = None
    quote: str | None = None

    while i < length:
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, i):
                output.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
            else:
                output.append(sql[i])
                i += 1
            continue

        if block_depth:
            if sql.startswith("/*", i):
                output.append("/*")
                i += 2
                block_depth += 1
            elif sql.startswith("*/", i):
                output.append("*/")
                i += 2
                block_depth -= 1
            else:
                output.append(sql[i])
                i += 1
            continue

        if quote is not None:
            char = sql[i]
            output.append(char)
            i += 1
            if char == "\\" and quote == "'":
                if i < length:
                    output.append(sql[i])
                    i += 1
            elif char == quote:
                if i < length and sql[i] == quote:
                    output.append(sql[i])
                    i += 1
                else:
                    quote = None
            continue

        if sql.startswith("--", i):
            end = sql.find("\n", i)
            if end == -1:
                output.append(sql[i:])
                break
            output.append(sql[i:end + 1])
            i = end + 1
            continue

        if sql.startswith("/*", i):
            output.append("/*")
            i += 2
            block_depth = 1
            continue

        char = sql[i]
        if char in ("'", '"'):
            quote = char
            output.append(char)
            i += 1
            continue

        if char == "$":
            end = sql.find("$", i + 1)
            if end != -1:
                tag_body = sql[i + 1:end]
                if tag_body == "" or (tag_body[0].isalpha() or tag_body[0] == "_") and all(
                    c.isalnum() or c == "_" for c in tag_body
                ):
                    dollar_tag = sql[i:end + 1]
                    output.append(dollar_tag)
                    i = end + 1
                    continue

        if char == "?":
            output.append("%s")
        else:
            output.append(char)
        i += 1

    return "".join(output)


class CursorProtocol(Protocol):
    """Small cursor surface consumed by marketplace repositories and routes."""
    @property
    def rowcount(self) -> int: ...
    def fetchone(self) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def __iter__(self) -> Iterator[Any]: ...


class ConnectionProtocol(Protocol):
    """Portable SQL connection surface; driver-specific APIs stay inside adapters."""
    def execute(self, sql: str, parameters: Sequence[object] = ()) -> CursorProtocol: ...


@runtime_checkable
class DatabaseProtocol(Protocol):
    """Contract required by the application, independent of a database driver."""

    def read(self) -> ContextManager[ConnectionProtocol]: ...
    def transaction(self) -> ContextManager[ConnectionProtocol]: ...
    def initialize(self) -> None: ...
    def schema_version(self) -> int: ...


# Stable exception names exposed by the database adapter contract. Drivers must map
# their native integrity/operational failures to these public exception types.
# SQLite aliases preserve existing behavior while PostgreSQL support is developed.
DatabaseIntegrityError = sqlite3.IntegrityError
DatabaseOperationalError = sqlite3.OperationalError


def now():
    return int(time.time())


def insert_id(connection: ConnectionProtocol, sql: str, parameters: Sequence[object] = ()) -> int:
    """Execute an INSERT and return its generated id using portable SQL RETURNING."""
    statement = sql.rstrip().removesuffix(";")
    if not statement.lstrip().upper().startswith("INSERT ") or " RETURNING " in statement.upper():
        raise ValueError("insert_id requires one INSERT statement without an existing RETURNING clause")
    row = connection.execute(statement + " RETURNING id", parameters).fetchone()
    if row is None:
        raise RuntimeError("INSERT did not return a generated id")
    return int(row[0])


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

    def schema_version(self) -> int:
        """Return the installed schema version without leaking driver SQL to callers."""
        with self.read() as c:
            return int(c.execute('PRAGMA user_version').fetchone()[0])

    def initialize(self):
        with self.read() as c:
            c.execute('PRAGMA journal_mode=WAL')
            version = c.execute('PRAGMA user_version').fetchone()[0]
            if version > LATEST_SCHEMA_VERSION:
                raise RuntimeError('Database is newer than this application. Do not downgrade the schema.')
            if version == 0:
                c.executescript('BEGIN IMMEDIATE;\n' + SCHEMA_PATH.read_text(encoding='utf-8') + '\nPRAGMA user_version=1; COMMIT;')
                version = 1
            if version == 1:
                migration = MIGRATION_DIR / '0002_product_condition.sql'
                try:
                    c.executescript('BEGIN IMMEDIATE;\n' + migration.read_text(encoding='utf-8')
                                    + '\nPRAGMA user_version=2; COMMIT;')
                except BaseException:
                    if c.in_transaction:
                        c.rollback()
                    raise
                version = 2
            if version == 2:
                migration = MIGRATION_DIR / '0003_product_images.sql'
                try:
                    c.executescript('BEGIN IMMEDIATE;\n' + migration.read_text(encoding='utf-8')
                                    + '\nPRAGMA user_version=3; COMMIT;')
                except BaseException:
                    if c.in_transaction:
                        c.rollback()
                    raise
                version = 3
            if version == 3:
                migration = MIGRATION_DIR / '0004_product_variants.sql'
                try:
                    c.executescript('BEGIN IMMEDIATE;\n' + migration.read_text(encoding='utf-8')
                                    + '\nPRAGMA user_version=4; COMMIT;')
                except BaseException:
                    if c.in_transaction:
                        c.rollback()
                    raise
                version = 4
            if version == 4:
                migration = MIGRATION_DIR / '0005_conversations_messages.sql'
                try:
                    c.executescript('BEGIN IMMEDIATE;\n' + migration.read_text(encoding='utf-8')
                                    + '\nPRAGMA user_version=5; COMMIT;')
                except BaseException:
                    if c.in_transaction:
                        c.rollback()
                    raise
                version = 5
            if version == 5:
                migration = MIGRATION_DIR / '0006_verified_reviews.sql'
                try:
                    c.executescript('BEGIN IMMEDIATE;\n' + migration.read_text(encoding='utf-8')
                                    + '\nPRAGMA user_version=6; COMMIT;')
                except BaseException:
                    if c.in_transaction:
                        c.rollback()
                    raise
                version = 6
            if version == 6:
                migration = MIGRATION_DIR / '0007_shipment_cases.sql'
                try:
                    c.executescript('BEGIN IMMEDIATE;\n' + migration.read_text(encoding='utf-8')
                                    + '\nPRAGMA user_version=7; COMMIT;')
                except BaseException:
                    if c.in_transaction:
                        c.rollback()
                    raise
                version = 7
            if version != LATEST_SCHEMA_VERSION:
                raise RuntimeError('Database schema is incomplete; apply all numbered migrations.')
        Path(self.path).chmod(0o600)
