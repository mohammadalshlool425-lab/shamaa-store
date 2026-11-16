"""Psycopg 3 adapter for the private SHAM'A PostgreSQL schema.

Migrations are applied out-of-band. This adapter validates the installed
foundation and never creates roles, grants privileges, or changes RLS.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import re
from typing import Any, Sequence

import psycopg

from .database import (
    DatabaseIntegrityError,
    DatabaseOperationalError,
    HybridRow,
    qmark_to_format,
)

SYSTEM_OPERATIONS = frozenset({
    "auth.register",
    "auth.login",
    "auth.verify_email",
    "auth.forgot_password",
    "auth.reset_password",
    "rate_limit.consume",
    "mail.worker",
    "audit.append",
})


SCHEMA_TABLES = (
    "users", "sessions", "auth_tokens", "rate_limits", "categories", "stores",
    "media", "products", "carts", "orders", "seller_orders", "order_items",
    "order_events", "inventory_movements", "ledger", "notifications",
    "audit_log", "settings", "mail_outbox", "product_images", "product_variants",
    "conversations", "messages", "reviews", "review_reports", "order_cases", "order_case_events", "order_case_attachments", "return_requests", "return_request_events", "coupons", "coupon_redemptions",
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

    Reference catalog rows are installed by versioned migrations, never at web startup.
    The schema identifier is validated before it is interpolated into libpq's
    startup search_path option. Values in application SQL remain parameterized.
    """
    catalog_seeded_by_migrations = True

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
        self._request_identity: ContextVar[dict | None] = ContextVar(
            f"shamaa_pg_identity_{id(self)}", default=None
        )
        self._system_operation: ContextVar[str | None] = ContextVar(
            f"shamaa_pg_operation_{id(self)}", default=None
        )
        self._rate_limit_bucket: ContextVar[str | None] = ContextVar(
            f"shamaa_pg_rate_limit_bucket_{id(self)}", default=None
        )

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
            try:
                with self._request_operation(connection) as wrapped:
                    yield wrapped
            except (psycopg.IntegrityError, psycopg.OperationalError) as exc:
                _raise_database_error(exc)
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        connection = self._connect()
        try:
            try:
                with connection.transaction():
                    wrapped = PostgresConnection(connection)
                    self._apply_transaction_context(wrapped)
                    yield wrapped
            except (psycopg.IntegrityError, psycopg.OperationalError) as exc:
                _raise_database_error(exc)
        finally:
            connection.close()

    @contextmanager
    def system_operation(self, operation: str, *, bucket: str | None = None):
        """Enter a fixed server-selected operation context for narrow RLS policies.

        Operation names are internal constants, never request parameters. The
        rate-limit bucket is accepted only for the rate-limit operation and must
        be a server-computed SHA-256 digest. It is a policy input, not a secret
        or an independent authorization boundary.
        """
        if not isinstance(operation, str) or operation not in SYSTEM_OPERATIONS:
            raise ValueError("Unsupported PostgreSQL system operation")
        if bucket is not None and (
            operation != "rate_limit.consume"
            or not isinstance(bucket, str)
            or not re.fullmatch(r"[0-9a-f]{64}", bucket)
        ):
            raise ValueError("Rate-limit bucket must be a lowercase SHA-256 digest")
        active = self._system_operation.get()
        active_bucket = self._rate_limit_bucket.get()
        if active is not None and active != operation:
            raise RuntimeError("Cannot nest different PostgreSQL system operations")
        if active_bucket is not None and bucket not in (None, active_bucket):
            raise RuntimeError("Cannot nest different rate-limit buckets")
        operation_token = self._system_operation.set(operation)
        bucket_token = self._rate_limit_bucket.set(
            bucket if bucket is not None else active_bucket
        )
        identity_token = self._request_identity.set(None)
        try:
            yield
        finally:
            self._request_identity.reset(identity_token)
            self._rate_limit_bucket.reset(bucket_token)
            self._system_operation.reset(operation_token)

    def _apply_transaction_context(self, wrapped: PostgresConnection) -> None:
        identity = self._request_identity.get()
        if identity is not None:
            if identity["session_token_hash"] is not None:
                wrapped.execute(
                    "SELECT set_config('app.session_token_hash', ?, TRUE)",
                    (identity["session_token_hash"],),
                )
            if identity["user_id"] is not None:
                wrapped.execute(
                    "SELECT set_config('app.current_user_id', ?, TRUE)",
                    (str(identity["user_id"]),),
                )
        operation = self._system_operation.get()
        if operation is not None:
            wrapped.execute(
                "SELECT set_config('app.system_operation', ?, TRUE)",
                (operation,),
            )
        bucket = self._rate_limit_bucket.get()
        if bucket is not None:
            wrapped.execute(
                "SELECT set_config('app.rate_limit_bucket', ?, TRUE)",
                (bucket,),
            )

    @contextmanager
    def request_scope(self, session_token_hash: str | None):
        """Bind a verified-session lookup and subsequent user identity to one request context.

        The cookie itself must never be passed here: callers pass only its SHA-256
        digest. The context is reset even when request handling raises.
        """
        if session_token_hash is not None and (
            not isinstance(session_token_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", session_token_hash)
        ):
            raise ValueError("session_token_hash must be a lowercase SHA-256 digest")
        token = self._request_identity.set({
            "session_token_hash": session_token_hash,
            "user_id": None,
        })
        try:
            yield self.bind_request_user
        finally:
            self._request_identity.reset(token)

    def bind_request_user(self, user_id: int) -> None:
        """Bind the id returned by the server-validated session lookup."""
        identity = self._request_identity.get()
        if identity is None or identity["session_token_hash"] is None:
            raise RuntimeError("A verified session lookup is required before binding a user")
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        self._request_identity.set({
            "session_token_hash": identity["session_token_hash"],
            "user_id": user_id,
        })

    @contextmanager
    def _request_operation(self, connection):
        """Run an operation with transaction-local request identity, if present."""
        identity = self._request_identity.get()
        operation = self._system_operation.get()
        bucket = self._rate_limit_bucket.get()
        if (identity is None or not any(identity.values())) and operation is None and bucket is None:
            yield PostgresConnection(connection)
            return
        with connection.transaction():
            wrapped = PostgresConnection(connection)
            self._apply_transaction_context(wrapped)
            yield wrapped

    def for_user(self, user_id: int):
        """Return a request-scoped facade that sets transaction-local RLS identity.

        Call only with an id obtained from the server-validated application
        session, never with a value supplied directly by the client.
        """
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id must be a positive integer")
        return _UserScopedPostgresDatabase(self, user_id)

    def schema_version(self) -> int:
        """Return 10 when the foundation, commerce, cases, returns and coupons schema exist."""
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
                   WHERE table_schema=? AND (
                     (table_name='carts' AND column_name IN ('variant_id','variant_key'))
                     OR
                     (table_name='order_items' AND column_name IN ('variant_id','variant_key','variant_sku','variant_attributes'))
                   )""",
                (self.schema,),
            ).fetchone()
            messaging = connection.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema=? AND (
                     (table_name='conversations' AND column_name IN ('id','seller_order_id','created_at','updated_at'))
                     OR
                     (table_name='messages' AND column_name IN ('id','conversation_id','sender_id','client_key','body','body_hash','created_at'))
                   )""",
                (self.schema,),
            ).fetchone()
            reviews = connection.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema=? AND (
                     (table_name='reviews' AND column_name IN (
                       'id','order_item_id','buyer_id','product_id','seller_order_id',
                       'rating','body','status','moderation_note','moderated_by',
                       'moderated_at','created_at','updated_at'
                     ))
                     OR
                     (table_name='review_reports' AND column_name IN (
                       'id','review_id','reporter_id','reason','details','status',
                       'resolution_note','resolved_by','resolved_at','created_at'
                     ))
                   )""",
                (self.schema,),
            ).fetchone()
            cases = connection.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema=? AND (
                     (table_name='order_cases' AND column_name IN (
                       'id','seller_order_id','buyer_id','seller_id','reason','description','status',
                       'opened_at','response_due_at','updated_at','closed_at','closed_by','resolution_note'
                     ))
                     OR
                     (table_name='order_case_events' AND column_name IN (
                       'id','case_id','actor_id','actor_role','event_type','body','created_at'
                     ))
                   )""",
                (self.schema,),
            ).fetchone()
            attachments = connection.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema=? AND table_name='order_case_attachments'
                     AND column_name IN (
                       'id','case_id','uploader_id','uploader_role','storage_key',
                       'content_type','bytes','digest','created_at'
                     )""",
                (self.schema,),
            ).fetchone()
            returns = connection.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema=? AND (
                     (table_name='return_requests' AND column_name IN (
                       'id','order_item_id','seller_order_id','buyer_id','seller_id','quantity',
                       'reason','description','status','requested_at','response_due_at','updated_at',
                       'seller_note','return_carrier','return_tracking_number','closed_at','closed_by','decision_note'
                     ))
                     OR
                     (table_name='return_request_events' AND column_name IN (
                       'id','request_id','actor_id','actor_role','event_type','body','created_at'
                     ))
                   )""",
                (self.schema,),
            ).fetchone()
            coupons = connection.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema=? AND (
                     (table_name='coupons' AND column_name IN (
                       'id','store_id','code','discount_type','discount_value','minimum_subtotal_fils',
                       'maximum_discount_fils','starts_at','expires_at','max_uses','max_uses_per_buyer',
                       'used_count','active','created_at','updated_at'
                     ))
                     OR
                     (table_name='coupon_redemptions' AND column_name IN (
                       'id','coupon_id','order_id','seller_order_id','store_id','buyer_id',
                       'code','discount_fils','redeemed_at'
                     ))
                     OR (table_name='orders' AND column_name='discount_fils')
                     OR (table_name='seller_orders' AND column_name='discount_fils')
                   )""",
                (self.schema,),
            ).fetchone()
        complete = (tables and int(tables[0]) == len(SCHEMA_TABLES)
                    and columns and int(columns[0]) == 4
                    and gallery and int(gallery[0]) == 4
                    and variant_table and int(variant_table[0]) == 1
                    and variant_columns and int(variant_columns[0]) == 6
                    and messaging and int(messaging[0]) == 11
                    and reviews and int(reviews[0]) == 23
                    and cases and int(cases[0]) == 20
                    and attachments and int(attachments[0]) == 9
                    and returns and int(returns[0]) == 25
                    and coupons and int(coupons[0]) == 26)
        return 10 if complete else 0

    def initialize(self) -> None:
        """Validate the pre-applied schema; never mutate production schema."""
        if self.schema_version() != 10:
            raise RuntimeError(
                f"PostgreSQL schema {self.schema!r} is missing or incomplete; "
                "apply structural Supabase migrations 0001-0010 and reference-data migration 0011 before starting the app."
            )



class _UserScopedPostgresDatabase:
    """PostgreSQL facade whose every operation carries one verified user id.

    SET LOCAL semantics are provided by set_config(..., true) inside a fresh
    transaction. The setting is automatically cleared on commit/rollback,
    including exceptional exits, so a later request cannot inherit identity.
    """

    def __init__(self, database: PostgresDatabase, user_id: int):
        self._database = database
        self.user_id = user_id

    @contextmanager
    def _scope(self):
        connection = self._database._connect()
        try:
            try:
                with connection.transaction():
                    wrapped = PostgresConnection(connection)
                    wrapped.execute(
                        "SELECT set_config('app.current_user_id', ?, TRUE)",
                        (str(self.user_id),),
                    )
                    yield wrapped
            except (psycopg.IntegrityError, psycopg.OperationalError) as exc:
                _raise_database_error(exc)
        finally:
            connection.close()

    def read(self):
        return self._scope()

    def transaction(self):
        return self._scope()
