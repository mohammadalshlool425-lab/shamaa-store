"""Application-level cross-account order and shipment isolation tests."""


def test_buyer_and_seller_cannot_access_another_accounts_order_or_shipment(market):
    factory = market["factory"]
    buyer_a = market["buyer"]
    buyer_b = factory.user()
    seller_a = market["a"]
    seller_b = market["b"]
    product = market["p"]

    factory.cart(buyer_a, product, quantity=1)
    checkout, _, _ = factory.checkout(buyer_a)
    assert checkout.status_code == 201, checkout.text
    order = checkout.json()["order"]
    shipment = next(
        row for row in order["shipments"]
        if row["store_id"] == seller_a["store_id"]
    )

    # The owning buyer can read the order; another authenticated buyer cannot.
    own_order = buyer_a["client"].get(f"/api/orders/{order['id']}")
    assert own_order.status_code == 200, own_order.text
    assert buyer_b["client"].get(f"/api/orders/{order['id']}").status_code == 404

    # A guessed order/shipment identifier cannot be used to cancel another
    # buyer's shipment or to read its order through the cancellation response.
    foreign_cancel = buyer_b["client"].post(
        f"/api/orders/{order['id']}/shipments/{shipment['id']}/cancel"
    )
    assert foreign_cancel.status_code == 404

    # The store owner can transition its shipment. A different store owner
    # cannot read the seller queue entry or mutate the shipment by guessed id.
    seller_queue = seller_a["client"].get("/api/seller/orders")
    assert seller_queue.status_code == 200, seller_queue.text
    assert any(row["id"] == shipment["id"] for row in seller_queue.json()["orders"])

    foreign_queue = seller_b["client"].get("/api/seller/orders")
    assert foreign_queue.status_code == 200, foreign_queue.text
    assert all(row["id"] != shipment["id"] for row in foreign_queue.json()["orders"])

    foreign_transition = seller_b["client"].patch(
        f"/api/seller/orders/{shipment['id']}/status",
        json={"status": "confirmed", "expected_status": "new"},
    )
    assert foreign_transition.status_code == 404

    valid_transition = seller_a["client"].patch(
        f"/api/seller/orders/{shipment['id']}/status",
        json={"status": "confirmed", "expected_status": "new"},
    )
    assert valid_transition.status_code == 200, valid_transition.text

    # Unauthorized attempts must not have changed the shipment state.
    with factory.db.read() as connection:
        status = connection.execute(
            "SELECT status FROM seller_orders WHERE id=?",
            (shipment["id"],),
        ).fetchone()["status"]
    assert status == "confirmed"
