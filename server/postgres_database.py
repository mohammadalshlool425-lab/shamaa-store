"""Psycopg 3 adapter for the private SHAM'A PostgreSQL schema.

Migrations are applied out-of-band. This adapter validates the installed
foundation and never creates roles, grants privileges, or changes RLS.
"""
from contextlib import contextmanager
import re
from typing import Any, Sequence

import psycopg

from .database import (
    DatabaseIntegrityError,
    DatabaseOperationalError,
    HybridRow,
    qmark_to_format,
)

SCHEMA_TABLES = (
    "users", "sessions", "auth_tokens", "rate_limits", "categories", "stores",
    "media", "products", "carts", "orders", "seller_orders", "order_items",
    "order_events", "inventory_movements", "ledger", "notifications",
    "audit_log", "settings", "mail_outbox", "product_images", "product_variants",
)


def _hybrid_row_factory(cursor):
    """Build HybridRow values from the result metadata for each executed query."""
    columns = tuple(column.name for column in (cursor.description or ()))

    def make_row(values: Sequence[Any]) -> HybridRow:
        return HybridRow(columns, values)

    return make_row


def _raise_database_error(exc: Exception) -> None:
    """Translate Psycopg's public DB-API exceptions into the shared app contract."""
    if isinstance(exc, psycopg.IntegrityError):
        raise DatabaseIntegrityError(str(exc)) from exc
    if isinstance(exc, psycopg.OperationalError):
        raise DatabaseOperationalError(str(exc)) from exc
    raise exc


class PostgresConnection:
    """Narrow connection wrapper: qmark SQL in, shared cursor contract out."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql: str, parameters: Sequence[object] = ()):
        try:
            return self._connection.execute(qmark_to_format(sql), tuple(parameters))
        except (psycopg.IntegrityError, psycopg.OperationalError) as exc:
            _raise_database_error(exc)


class PostgresDatabase:
    """One short-lived Psycopg connection per operation; no implicit migrations.

    The schema identifier is validated before it is interpolated into libpq's
    startup search_path option. Values in application SQL remain parameterized.
    """

    def __init__(self, conninfo: str, schema: str = "shamaa_app", connect_timeout: int = 10):
        if not isinstance(conninfo, str) or not conninfo.startswith("postgresql://"):
            raise ValueError("PostgreSQL connection info must use postgresql://")
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
            raise ValueError("Invalid PostgreSQL schema identifier")
        if not isinstance(connect_timeout, int) or not 1 <= connect_timeout <= 60:
            raise ValueError("connect_timeout must be between 1 and 60 seconds")
        self._conninfo = conninfo
        self.schema = schema
        self.connect_timeout = connect_timeout

    def _connect(self):
        try:
            return psycopg.connect(
                self._conninfo,
                autocommit=True,
                row_factory=_hybrid_row_factory,
                prepare_threshold=None,
                connect_timeout=self.connect_timeout,
                options=f"-c search_path={self.schema},pg_catalog",
            )
        except (psycopg.IntegrityError, psycopg.OperationalError) as exc:
            _raise_database_error(exc)

    @contextmanager
    def read(self):
        connection = self._connect()
        try:
            yield PostgresConnection(connection)
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        connection = self._connect()
        try:
            try:
                with connection.transaction():
                    yield PostgresConnection(connection)
            except (psycopg.IntegrityError, psycopg.OperationalError) as exc:
                _raise_database_error(exc)
        finally:
            connection.close()

    def schema_version(self) -> int:
        """Return 4 only when foundation, condition, gallery and variant columns exist."""
        with self.read() as connection:
            tables = connection.execute(
                """SELECT COUNT(*) FROM information_schema.tables
                   WHERE table_schema=? AND table_type='BASE TABLE' AND table_name = ANY(?)""",
                (self.schema, list(SCHEMA_TABLES)),
            ).fetchone()
            columns = connection.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema=? AND (
                     (table_name='products' AND column_name IN ('condition','condition_note'))
                     OR
                     (table_name='order_items' AND column_name IN ('condition','condition_note'))
                   )""",
                (self.schema,),
            ).fetchone()
            gallery = connection.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema=? AND table_name='product_images'
                     AND column_name IN ('product_id','media_id','position','created_at')""",
                (self.schema,),
            ).fetchone()
            variant_table = connection.execute(
                """SELECT COUNT(*) FROM information_schema.tables
                   WHERE table_schema=? AND table_name='product_variants' AND table_type='BASE TABLE'""",
                (self.schema,),
            ).fetchone()
            variant_columns = connection.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema=? AND table_name='carts'
                     AND column_name IN ('variant_id','variant_key')""",
                (self.schema,),
            ).fetchone()
        complete = (tables and int(tables[0]) == len(SCHEMA_TABLES)
                    and columns and int(columns[0]) == 4
                    and gallery and int(gallery[0]) == 4
                    and variant_table and int(variant_table[0]) == 1
                    and variant_columns and int(variant_columns[0]) == 2)
        return 4 if complete else 0

    def initialize(self) -> None:
        """Validate the pre-applied schema; never mutate production schema."""
        if self.schema_version() != 4:
            raise RuntimeError(
                f"PostgreSQL schema {self.schema!r} is missing or incomplete; "
                "apply all reviewed Supabase migrations before starting the app."
            )
