"""FastAPI application smoke tests on isolated PostgreSQL 17 CI.

The CI database owner is a superuser, so these tests validate application SQL
compatibility only. They do NOT validate RLS isolation or runtime-role grants.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
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

    # A test-only route proves the FastAPI middleware resolves the cookie
    # before sync handlers execute and carries only the verified identity.
    from fastapi import Depends, Request
    from server.security import digest, require_user

    def request_identity_probe(request: Request, user=Depends(require_user)):
        with request.app.state.db.read() as connection:
            row = connection.execute(
                "SELECT current_setting('app.session_token_hash', TRUE), "
                "current_setting('app.current_user_id', TRUE)"
            ).fetchone()
        return {"user_id": user["id"], "session_hash": row[0], "db_user_id": row[1]}

    app.add_api_route(
        "/api/test/request-identity",
        request_identity_probe,
        methods=["GET"],
    )
    # The application intentionally registers a final /api/{path:path}
    # not-found route; place this test-only route ahead of that fallback.
    probe_route = app.router.routes.pop()
    fallback_index = next(
        index for index, route in enumerate(app.router.routes)
        if getattr(route, "path", None) == "/api/{path:path}"
    )
    app.router.routes.insert(fallback_index, probe_route)
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

        identity = client.get("/api/test/request-identity")
        assert identity.status_code == 200, identity.text
        assert identity.json()["user_id"] == body["user"]["id"]
        assert identity.json()["db_user_id"] == str(body["user"]["id"])
        assert identity.json()["session_hash"] == digest(client.cookies.get("shamaa_session"))

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

    status_barrier = Barrier(2)

    def confirm_shipment():
        status_barrier.wait(timeout=10)
        return seller["client"].patch(
            f"/api/seller/orders/{shipment['id']}/status",
            json={"status": "confirmed", "expected_status": "new"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(confirm_shipment)
        second = pool.submit(confirm_shipment)
        confirmation_results = [first.result(timeout=30), second.result(timeout=30)]
    assert sorted(response.status_code for response in confirmation_results) == [200, 409]

    previous = "confirmed"
    for status in ("preparing", "shipped", "delivered"):
        changed = seller["client"].patch(
            f"/api/seller/orders/{shipment['id']}/status",
            json={"status": status, "expected_status": previous},
        )
        assert changed.status_code == 200, changed.text
        previous = status

    with factory.db.read() as c:
        expected_snapshot = [
            dict(row) for row in c.execute(
                """SELECT id AS order_item_id,product_id,title_ar,title_en,quantity,
                          unit_price_fils,commission_bps,commission_fils
                     FROM order_items WHERE seller_order_id=? ORDER BY id""",
                (shipment["id"],),
            ).fetchall()
        ]
        item_id = expected_snapshot[0]["order_item_id"]
        original_ledger_count = c.execute(
            "SELECT COUNT(*) FROM ledger WHERE seller_order_id=?",
            (shipment["id"],),
        ).fetchone()[0]

    ledger_response = seller["client"].get("/api/seller/ledger")
    assert ledger_response.status_code == 200, ledger_response.text
    ledger_payload = ledger_response.json()
    assert len(ledger_payload["entries"]) == 1
    ledger_entry = ledger_payload["entries"][0]
    assert ledger_entry["source"] == "cod_delivery"
    assert ledger_entry["reference_type"] == "seller_order"
    assert ledger_entry["reference_id"] == shipment["id"]
    assert ledger_entry["amount_fils"] == shipment["commission_fils"]
    expected_basis = sum(item["unit_price_fils"] * item["quantity"] for item in expected_snapshot)
    assert ledger_entry["basis_fils"] == expected_basis
    assert ledger_entry["commission_snapshot"] == expected_snapshot

    with pytest.raises(Exception, match="delivered shipment snapshot"):
        with factory.db.transaction() as c:
            c.execute(
                """INSERT INTO ledger(
                       seller_order_id,store_id,kind,amount_fils,source,reference_type,
                       reference_id,basis_fils,snapshot_json,created_at
                   ) VALUES(%s,%s,'cod_commission_due',%s,'cod_delivery','seller_order',%s,%s,%s,%s)""",
                (shipment["id"], shipment["store_id"], shipment["commission_fils"],
                 shipment["id"], expected_basis, "[]", now()),
            )

    opened = buyer["client"].post("/api/returns", json={
        "order_item_id": item_id,
        "quantity": 1,
        "reason": "damaged",
        "description": "وصل المنتج متضررًا وأطلب إرجاع قطعة واحدة من الطلب.",
    })
    assert opened.status_code == 201, opened.text
    return_id = opened.json()["return"]["id"]

    return_barrier = Barrier(2)

    def approve_return():
        return_barrier.wait(timeout=10)
        return seller["client"].post(
            f"/api/returns/{return_id}/seller-decision",
            json={"status": "approved", "note": "تم قبول الإرجاع بعد مراجعة الوصف."},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(approve_return)
        second = pool.submit(approve_return)
        approval_results = [first.result(timeout=30), second.result(timeout=30)]
    assert sorted(response.status_code for response in approval_results) == [200, 409]
    approved = next(response for response in approval_results if response.status_code == 200)
    assert approved.json()["return"]["status"] == "approved"
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



def test_postgres_catalog_startup_does_not_open_a_write_transaction():
    from types import SimpleNamespace

    from server.seed import initialize_catalog

    class MigrationManagedDatabase:
        catalog_seeded_by_migrations = True

        def transaction(self):
            raise AssertionError("PostgreSQL startup must not write reference categories")

    initialize_catalog(MigrationManagedDatabase(), SimpleNamespace(demo=False))


def test_postgres_demo_seed_is_rejected_before_any_database_write():
    from types import SimpleNamespace

    from server.seed import initialize_catalog

    class MigrationManagedDatabase:
        catalog_seeded_by_migrations = True

        def transaction(self):
            raise AssertionError("Demo seeding must not run through PostgreSQL web startup")

    with pytest.raises(RuntimeError, match="separate development setup path"):
        initialize_catalog(MigrationManagedDatabase(), SimpleNamespace(demo=True))



def test_postgres_coupon_checkout_uses_discount_aware_order_totals(tmp_path):
    conninfo = os.getenv("SHAMAA_TEST_DATABASE_URL")
    if not conninfo:
        pytest.skip("Coupon checkout PostgreSQL test requires SHAMAA_TEST_DATABASE_URL")

    from server.application import create_app
    from server.config import Settings
    from server.postgres_database import PostgresDatabase

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import Factory

    settings = Settings(
        db_path=tmp_path / "unused.sqlite3",
        data_dir=tmp_path / "coupon-private-data",
        public_url="http://localhost:8000",
        allowed_hosts=["localhost", "127.0.0.1", "testserver"],
        demo=False,
        email_mode="file",
    )
    factory = Factory(create_app(settings=settings, database=PostgresDatabase(conninfo)))
    factory.counter = int(uuid4().hex[:8], 16)
    seller = factory.seller()
    buyer = factory.user()
    product = factory.product(seller, price=10000, stock=3)
    code = f"PG{uuid4().hex[:8].upper()}"
    created = seller["client"].post("/api/seller/coupons", json={
        "code": code,
        "discount_type": "percent",
        "discount_value": 1000,
        "minimum_subtotal_fils": 0,
        "maximum_discount_fils": 2000,
        "starts_at": None,
        "expires_at": None,
        "max_uses": 1,
        "max_uses_per_buyer": 1,
        "active": True,
    })
    assert created.status_code == 201, created.text

    quote = factory.cart(buyer, product, quantity=1)
    preview = buyer["client"].post("/api/coupon-quote", json={"code": code.lower()})
    assert preview.status_code == 200, preview.text
    assert preview.json()["discount_fils"] == 1000

    response, _, _ = factory.checkout(
        buyer,
        quote=quote,
        expected_total_fils=preview.json()["total_fils"],
        coupon_code=code.lower(),
    )
    assert response.status_code == 201, response.text
    order = response.json()["order"]
    shipment = order["shipments"][0]
    assert order["discount_fils"] == 1000
    assert order["total_fils"] == (
        order["subtotal_fils"] + order["shipping_fils"] - order["discount_fils"]
    )
    assert shipment["discount_fils"] == 1000
    assert shipment["net_fils"] == (
        shipment["subtotal_fils"] + shipment["shipping_fils"]
        - shipment["discount_fils"] - shipment["commission_fils"]
    )

    with factory.db.read() as connection:
        redemption = connection.execute(
            "SELECT discount_fils FROM coupon_redemptions WHERE order_id=?",
            (order["id"],),
        ).fetchone()
        usage = connection.execute(
            "SELECT used_count FROM coupons WHERE code=?",
            (code,),
        ).fetchone()
    assert redemption["discount_fils"] == 1000
    assert usage["used_count"] == 1
