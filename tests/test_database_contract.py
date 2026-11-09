from server.application import create_app
import sqlite3

from server.database import (
    Database,
    DatabaseIntegrityError,
    DatabaseOperationalError,
    DatabaseProtocol,
    insert_id,
)
from server.config import Settings
from fastapi.testclient import TestClient


class RecordingDatabase:
    """Driver-neutral adapter wrapper used to prove application-level injection."""

    def __init__(self, inner):
        self.inner = inner
        self.initialized = False

    def read(self):
        return self.inner.read()

    def transaction(self):
        return self.inner.transaction()

    def initialize(self):
        self.inner.initialize()
        self.initialized = True

    def schema_version(self):
        return self.inner.schema_version()


def test_application_accepts_database_protocol_implementation(tmp_path):
    settings = Settings(
        env="development",
        db_path=tmp_path / "injected.sqlite3",
        data_dir=tmp_path,
        allowed_hosts=["testserver"],
        public_url="http://testserver",
        demo=False,
        email_mode="file",
        encryption_key="",
    )
    adapter = RecordingDatabase(Database(settings.db_path))
    assert isinstance(adapter, DatabaseProtocol)

    app = create_app(settings, database=adapter)

    assert adapter.initialized is True
    assert adapter.schema_version() == 1
    assert app.state.db is adapter
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["database"] == "ready"


def test_sqlite_exceptions_are_exposed_through_database_contract():
    assert DatabaseIntegrityError is sqlite3.IntegrityError
    assert DatabaseOperationalError is sqlite3.OperationalError



def test_insert_id_uses_returning_and_returns_generated_identifier(tmp_path):
    db = Database(tmp_path / "insert-returning.sqlite3")
    db.initialize()
    with db.transaction() as connection:
        connection.execute("CREATE TABLE generated_ids (id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL)")
        first = insert_id(connection, "INSERT INTO generated_ids(value) VALUES(?)", ("first",))
        second = insert_id(connection, "INSERT INTO generated_ids(value) VALUES(?)", ("second",))
        assert first == 1
        assert second == 2
        assert connection.execute("SELECT value FROM generated_ids WHERE id=?", (second,)).fetchone()[0] == "second"


def test_insert_id_rejects_non_insert_and_existing_returning_clause(tmp_path):
    db = Database(tmp_path / "insert-returning-invalid.sqlite3")
    db.initialize()
    with db.transaction() as connection:
        for statement in ("SELECT 1", "INSERT INTO settings(key,value) VALUES(?,?) RETURNING id"):
            try:
                insert_id(connection, statement, ("key", "value") if statement.startswith("INSERT") else ())
            except ValueError:
                pass
            else:
                raise AssertionError("insert_id must reject unsupported statements")
