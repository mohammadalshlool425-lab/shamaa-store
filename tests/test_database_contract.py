from server.application import create_app
import sqlite3

from server.database import (
    Database,
    DatabaseIntegrityError,
    DatabaseOperationalError,
    DatabaseProtocol,
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
    assert app.state.db is adapter
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["database"] == "ready"


def test_sqlite_exceptions_are_exposed_through_database_contract():
    assert DatabaseIntegrityError is sqlite3.IntegrityError
    assert DatabaseOperationalError is sqlite3.OperationalError
