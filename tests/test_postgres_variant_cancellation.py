"""PostgreSQL concurrency contract for cancellation of a variant-backed shipment."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4


def test_postgres_concurrent_variant_cancellation_restores_both_stock_levels_once(tmp_path):
    conninfo = os.getenv("SHAMAA_TEST_DATABASE_URL")
    if not conninfo:
        import pytest
        pytest.skip("Variant cancellation PostgreSQL test requires SHAMAA_TEST_DATABASE_URL")

    from server.application import create_app
    from server.config import Settings
    from server.database import now
    from server.postgres_database import PostgresDatabase

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import Factory

    settings = Settings(
        db_path=tmp_path / "unused.sqlite3",
        data_dir=tmp_path / "variant-cancel-private-data",
        public_url="http://localhost:8000",
        allowed_hosts=["localhost", "127.0.0.1", "testserver"],
        demo=False,
        email_mode="file",
    )
    factory = Factory(create_app(settings=settings, database=PostgresDatabase(conninfo)))
    factory.counter = int(uuid4().hex[:8], 16)
    seller = factory.seller()
    buyer = factory.user()
    product = factory.product(seller, stock=3)

    with factory.db.transaction() as connection:
        variant = connection.execute(
            """INSERT INTO product_variants(
                   product_id,store_id,sku,attributes_json,price_fils,stock,
                   active,version,created_at,updated_at
               ) VALUES(?,?,?,?,?,3,TRUE,1,?,?) RETURNING id""",
            (product["id"], seller["store_id"], f"VAR-{uuid4().hex[:10]}",
             '{"color":"blue"}', product["price_fils"], now(), now()),
        ).fetchone()
        variant_id = variant["id"]

    cart = buyer["client"].put(
        "/api/cart",
        json={"product_id": product["id"], "variant_id": variant_id, "quantity": 2},
    )
    assert cart.status_code == 200, cart.text
    checkout, _, _ = factory.checkout(buyer, quote=cart.json())
    assert checkout.status_code == 201, checkout.text
    order = checkout.json()["order"]
    shipment = order["shipments"][0]
    assert shipment["items"][0]["variant_id"] == variant_id

    path = f"/api/orders/{order['id']}/shipments/{shipment['id']}/cancel"
    barrier = Barrier(2)

    def cancel():
        barrier.wait(timeout=10)
        return buyer["client"].post(path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cancel)
        second = pool.submit(cancel)
        responses = [first.result(timeout=30), second.result(timeout=30)]

    assert sorted(response.status_code for response in responses) == [200, 409]
    with factory.db.read() as connection:
        parent_stock = connection.execute(
            "SELECT stock FROM products WHERE id=?", (product["id"],)
        ).fetchone()["stock"]
        variant_stock = connection.execute(
            "SELECT stock FROM product_variants WHERE id=?", (variant_id,)
        ).fetchone()["stock"]
        movements = connection.execute(
            """SELECT reason,delta FROM inventory_movements
                 WHERE seller_order_id=? ORDER BY id""",
            (shipment["id"],),
        ).fetchall()
        cancelled_events = connection.execute(
            """SELECT COUNT(*) FROM order_events
                 WHERE seller_order_id=? AND to_status='cancelled'""",
            (shipment["id"],),
        ).fetchone()[0]

    assert parent_stock == 3
    assert variant_stock == 3
    assert len(movements) == 2
    assert {(row["reason"], row["delta"]) for row in movements} == {
        (f"sale_variant_{variant_id}", -2),
        (f"cancel_variant_{variant_id}", 2),
    }
    assert cancelled_events == 1
