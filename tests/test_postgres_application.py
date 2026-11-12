"""FastAPI application smoke tests on isolated PostgreSQL 17 CI.

The CI database owner is a superuser, so these tests validate application SQL
compatibility only. They do NOT validate RLS isolation or runtime-role grants.
"""
import os
import sys
from pathlib import Path
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
    email = f"pg-smoke-{uuid4().hex}@example.com"
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
        assert registered.status_code == 201, registered.text
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

def test_postgres_return_lifecycle_and_database_receipt_guard(tmp_path):
    conninfo = os.getenv("SHAMAA_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Return workflow PostgreSQL test requires SHAMAA_TEST_DATABASE_URL")

    import psycopg
    from fastapi.testclient import TestClient
    from server.application import create_app
    from server.config import Settings
    from server.database import now
    from server.postgres_database import PostgresDatabase

    # Factory exercises the same real FastAPI endpoints as the SQLite suite,
    # but every query in this test runs through the PostgreSQL adapter.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import Factory

    settings = Settings(
        db_path=tmp_path / "unused.sqlite3",
        data_dir=tmp_path / "return-private-data",
        public_url="http://localhost:8000",
        allowed_hosts=["localhost", "127.0.0.1", "testserver"],
        demo=False,
        email_mode="file",
    )
    app = create_app(settings=settings, database=PostgresDatabase(conninfo))
    factory = Factory(app)
    seller = factory.seller()
    buyer = factory.user()
    product = factory.product(seller, stock=3)
    quote = factory.cart(buyer, product, quantity=2)
    checkout, _, _ = factory.checkout(buyer, quote=quote)
    assert checkout.status_code == 201, checkout.text
    shipment = checkout.json()["order"]["shipments"][0]

    previous = "new"
    for status in ("confirmed", "preparing", "shipped", "delivered"):
        changed = seller["client"].patch(
            f"/api/seller/orders/{shipment['id']}/status",
            json={"status": status, "expected_status": previous},
        )
        assert changed.status_code == 200, changed.text
        previous = status

    with factory.db.read() as c:
        item_id = c.execute(
            "SELECT id FROM order_items WHERE seller_order_id=?",
            (shipment["id"],),
        ).fetchone()["id"]
        original_ledger_count = c.execute(
            "SELECT COUNT(*) FROM ledger WHERE seller_order_id=?",
            (shipment["id"],),
        ).fetchone()[0]

    opened = buyer["client"].post("/api/returns", json={
        "order_item_id": item_id,
        "quantity": 1,
        "reason": "damaged",
        "description": "وصل المنتج متضررًا وأطلب إرجاع قطعة واحدة من الطلب.",
    })
    assert opened.status_code == 201, opened.text
    return_id = opened.json()["return"]["id"]

    approved = seller["client"].post(
        f"/api/returns/{return_id}/seller-decision",
        json={"status": "approved", "note": "تم قبول الإرجاع بعد مراجعة الوصف."},
    )
    assert approved.status_code == 200, approved.text
    shipped = buyer["client"].post(
        f"/api/returns/{return_id}/shipment",
        json={"carrier": "CI return carrier", "tracking_number": "PG-RETURN-001"},
    )
    assert shipped.status_code == 200, shipped.text

    admin = factory.user("admin")
    stamp = now()
    forged_note = "قرار إداري مباشر قبل تأكيد وصول المرتجع."
    # The event writer is an active admin, but DB status guard still refuses
    # resolution until the seller has moved the return to received.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with factory.db.transaction() as c:
            c.execute(
                """INSERT INTO return_request_events(
                     request_id,actor_id,actor_role,event_type,body,created_at
                   ) VALUES(?,?,'admin','admin_resolved',?,?)""",
                (return_id, admin["id"], forged_note, stamp),
            )
            c.execute(
                """UPDATE return_requests SET status='resolved',closed_at=?,closed_by=?,
                          decision_note=?,updated_at=? WHERE id=?""",
                (stamp, admin["id"], forged_note, stamp, return_id),
            )

    early = admin["client"].post(
        f"/api/admin/returns/{return_id}/decision",
        json={"status": "resolved", "note": "لا يمكن إغلاقه قبل الاستلام."},
    )
    assert early.status_code == 409
    assert early.json()["code"] == "invalid_status"

    received = seller["client"].post(
        f"/api/returns/{return_id}/received",
        json={"note": "تم استلام المرتجع في اختبار PostgreSQL."},
    )
    assert received.status_code == 200, received.text
    resolved = admin["client"].post(
        f"/api/admin/returns/{return_id}/decision",
        json={"status": "resolved", "note": "تم استلام المرتجع ومراجعة سجل العملية."},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["return"]["status"] == "resolved"

    with factory.db.read() as c:
        assert c.execute(
            "SELECT COUNT(*) FROM return_request_events WHERE request_id=?",
            (return_id,),
        ).fetchone()[0] == 5
        assert c.execute(
            "SELECT COUNT(*) FROM ledger WHERE seller_order_id=?",
            (shipment["id"],),
        ).fetchone()[0] == original_ledger_count
        assert c.execute(
            "SELECT status FROM seller_orders WHERE id=?",
            (shipment["id"],),
        ).fetchone()["status"] == "delivered"

