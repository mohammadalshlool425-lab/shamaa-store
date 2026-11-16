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
