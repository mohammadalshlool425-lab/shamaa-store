"""Unit tests for PostgreSQL adapter boundaries that do not require a live database."""
from types import SimpleNamespace

import psycopg
import pytest

from server.database import DatabaseIntegrityError, DatabaseOperationalError
from server.postgres_database import (
    PostgresConnection,
    PostgresDatabase,
    _hybrid_row_factory,
    _raise_database_error,
)


@pytest.mark.parametrize(
    ("conninfo", "schema", "timeout"),
    [
        ("sqlite:///not-postgres", "shamaa_app", 10),
        ("postgresql://localhost/db", "shamaa_app; DROP SCHEMA public", 10),
        ("postgresql://localhost/db", "shamaa_app", 0),
        ("postgresql://localhost/db", "shamaa_app", 61),
    ],
)
def test_postgres_adapter_rejects_unsafe_connection_configuration(conninfo, schema, timeout):
    with pytest.raises(ValueError):
        PostgresDatabase(conninfo, schema=schema, connect_timeout=timeout)


def test_postgres_adapter_translates_driver_exceptions_to_shared_contract():
    with pytest.raises(DatabaseIntegrityError):
        _raise_database_error(psycopg.IntegrityError("duplicate"))
    with pytest.raises(DatabaseOperationalError):
        _raise_database_error(psycopg.OperationalError("connection lost"))
    original = ValueError("unmapped")
    with pytest.raises(ValueError) as caught:
        _raise_database_error(original)
    assert caught.value is original


def test_postgres_hybrid_row_factory_preserves_named_and_positional_access():
    cursor = SimpleNamespace(
        description=(SimpleNamespace(name="id"), SimpleNamespace(name="value"))
    )
    make_row = _hybrid_row_factory(cursor)
    row = make_row((7, "ready"))
    assert row["id"] == 7
    assert row[1] == "ready"
    assert dict(row) == {"id": 7, "value": "ready"}


def test_postgres_connection_translates_qmark_parameters():
    class FakeConnection:
        def __init__(self):
            self.call = None

        def execute(self, sql, parameters):
            self.call = (sql, parameters)
            return "cursor"

    driver = FakeConnection()
    connection = PostgresConnection(driver)
    assert connection.execute("SELECT ? AS value", [23]) == "cursor"
    assert driver.call == ("SELECT %s AS value", (23,))



@pytest.mark.parametrize("user_id", [0, -1, True, False, "7", None])
def test_postgres_user_scope_rejects_untrusted_or_invalid_identity(user_id):
    database = PostgresDatabase("postgresql://localhost/db")
    with pytest.raises(ValueError, match="positive integer"):
        database.for_user(user_id)


def test_postgres_user_scope_accepts_positive_integer_identity():
    database = PostgresDatabase("postgresql://localhost/db")
    scoped = database.for_user(17)
    assert scoped.user_id == 17



@pytest.mark.parametrize("session_hash", ["not-a-digest", "A" * 64, "a" * 63, 123, True])
def test_postgres_request_scope_rejects_invalid_session_digest(session_hash):
    database = PostgresDatabase("postgresql://localhost/db")
    with pytest.raises(ValueError, match="SHA-256 digest"):
        with database.request_scope(session_hash):
            pass


def test_postgres_request_scope_requires_verified_session_before_binding_user():
    database = PostgresDatabase("postgresql://localhost/db")
    with database.request_scope(None):
        with pytest.raises(RuntimeError, match="verified session"):
            database.bind_request_user(17)
        with pytest.raises(ValueError, match="positive integer"):
            with database.request_scope("a" * 64) as bind_user:
                bind_user(True)


def test_postgres_request_scope_binds_only_positive_integer_user_ids():
    database = PostgresDatabase("postgresql://localhost/db")
    with database.request_scope("a" * 64) as bind_user:
        bind_user(17)
        assert database._request_identity.get() == {
            "session_token_hash": "a" * 64,
            "user_id": 17,
        }
