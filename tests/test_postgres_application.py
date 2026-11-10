"""FastAPI application smoke tests on isolated PostgreSQL 17 CI.

The CI database owner is a superuser, so these tests validate application SQL
compatibility only. They do NOT validate RLS isolation or runtime-role grants.
"""
import os
from uuid import uuid4

import pytest


def test_fastapi_application_smoke_on_postgresql(tmp_path):
    conninfo = os.getenv("SHAMAA_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Application PostgreSQL smoke test requires SHAMAA_TEST_DATABASE_URL")

    from fastapi.testclient import TestClient
    from server.application import create_app
    from server.config import Settings
    from server.postgres_database import PostgresDatabase

    settings = Settings(
        db_path=tmp_path / "unused.sqlite3",
        data_dir=tmp_path / "private-data",
        public_url="http://localhost:8000",
        allowed_hosts=["localhost", "127.0.0.1"],
        demo=False,
        email_mode="file",
    )
    database = PostgresDatabase(conninfo)
    app = create_app(settings=settings, database=database)
    email = f"pg-smoke-{uuid4().hex}@example.invalid"
    payload = {
        "name": "PostgreSQL Smoke Test",
        "email": email,
        "password": "SmokeTest2026Secure",
        "locale": "en",
    }

    with TestClient(app, base_url="http://localhost") as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["database"] == "ready"

        bootstrap = client.get("/api/bootstrap")
        assert bootstrap.status_code == 200
        assert bootstrap.json()["currency"] == "JOD"
        assert len(bootstrap.json()["categories"]) >= 8

        catalog = client.get("/api/products")
        assert catalog.status_code == 200
        assert catalog.json()["products"] == []

        registered = client.post(
            "/api/auth/register",
            json=payload,
            headers={"x-requested-with": "shamaa"},
        )
        assert registered.status_code == 201
        body = registered.json()
        assert body["user"]["email"] == email
        assert body["user"]["locale"] == "en"
        assert body["csrf_token"]
        assert "shamaa_session" in client.cookies

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["email"] == email
        assert me.json()["csrf_token"] == body["csrf_token"]

        # Exercise PostgreSQL unique-constraint translation through the real route.
        duplicate = client.post(
            "/api/auth/register",
            json=payload,
            headers={
                "x-requested-with": "shamaa",
                "x-csrf-token": body["csrf_token"],
            },
        )
        assert duplicate.status_code == 409

    # Registration's transactional outbox is delivered through the private,
    # temporary file transport; no external SMTP service or live account is used.
    mail_files = list((settings.data_dir / "mail").glob("*.eml"))
    assert len(mail_files) == 1
    assert mail_files[0].stat().st_size > 0
