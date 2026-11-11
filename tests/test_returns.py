import sqlite3

import pytest

import server.routes.returns as return_routes


def _delivered_item(factory, seller, buyer, product, quantity=3):
    cart = factory.cart(buyer, product, quantity)
    response, _, _ = factory.checkout(buyer, quote=cart)
    assert response.status_code == 201, response.text
    shipment = response.json()["order"]["shipments"][0]
    previous = "new"
    for status in ("confirmed", "preparing", "shipped", "delivered"):
        changed = seller["client"].patch(
            f"/api/seller/orders/{shipment['id']}/status",
            json={"status": status, "expected_status": previous},
        )
        assert changed.status_code == 200, changed.text
        previous = status
    with factory.db.read() as c:
        item = c.execute(
            "SELECT id,quantity FROM order_items WHERE seller_order_id=?",
            (shipment["id"],),
        ).fetchone()
    return shipment, dict(item)


def test_return_workflow_requires_delivery_and_is_scoped_to_order_item(market):
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    shipment, item = _delivered_item(factory, market["a"], market["buyer"], market["p"], 3)
    created = buyer.post("/api/returns", json={
        "order_item_id": item["id"],
        "quantity": 2,
        "reason": "damaged",
        "description": "وصل المنتج متضررًا وأطلب إرجاع قطعتين من هذا السطر.",
    })
    assert created.status_code == 201, created.text
    request = created.json()["return"]
    assert request["status"] == "requested"
    assert request["quantity"] == 2
    assert request["response_due_at"] - request["requested_at"] == 48 * 60 * 60
    assert request["viewer_role"] == "buyer"
    assert request["events"][0]["event_type"] == "opened"

    seller_list = market["a"]["client"].get("/api/returns")
    assert seller_list.status_code == 200
    assert seller_list.headers["cache-control"] == "no-store"
    assert seller_list.json()["returns"][0]["id"] == request["id"]
    buyer_list = buyer.get("/api/returns")
    assert buyer_list.status_code == 200
    assert buyer_list.headers["cache-control"] == "no-store"
    assert buyer.get(f"/api/returns/{request['id']}").status_code == 200
    stranger = factory.user()["client"]
    assert stranger.get(f"/api/returns/{request['id']}").status_code == 404
    assert stranger.post("/api/returns", json={
        "order_item_id": item["id"], "quantity": 1, "reason": "other",
        "description": "هذا الحساب لا يملك البند الذي يحاول إرجاعه.",
    }).status_code == 404

    active_duplicate = buyer.post("/api/returns", json={
        "order_item_id": item["id"], "quantity": 1, "reason": "other",
        "description": "محاولة إنشاء طلب إرجاع آخر قبل إغلاق الطلب الحالي.",
    })
    assert active_duplicate.status_code == 409
    assert active_duplicate.json()["code"] == "return_active"

    with factory.db.read() as c:
        assert c.execute(
            "SELECT COUNT(*) FROM return_request_events WHERE request_id=?",
            (request["id"],),
        ).fetchone()[0] == 1
        assert c.execute(
            "SELECT COUNT(*) FROM ledger WHERE seller_order_id=?",
            (shipment["id"],),
        ).fetchone()[0] == 1  # Only the normal COD commission event from delivery.

    admin = factory.user("admin")["client"]
    admin_list = admin.get("/api/admin/returns")
    assert admin_list.status_code == 200
    assert admin_list.json()["returns"][0]["buyer_name"] == market["buyer"]["email"].split("@")[0].replace("account-", "Generated user ")


def test_return_lifecycle_seller_accepts_buyer_ships_and_seller_confirms(market):
    factory = market["factory"]
    buyer_user = market["buyer"]
    buyer = buyer_user["client"]
    seller = market["a"]["client"]
    shipment, item = _delivered_item(factory, market["a"], market["buyer"], market["p"], 3)
    opened = buyer.post("/api/returns", json={
        "order_item_id": item["id"], "quantity": 1, "reason": "not_as_described",
        "description": "وصل المنتج ولا يطابق الوصف المنشور وأرغب بإرجاع قطعة واحدة.",
    })
    assert opened.status_code == 201, opened.text
    return_id = opened.json()["return"]["id"]

    decision = seller.post(
        f"/api/returns/{return_id}/seller-decision",
        json={"status": "approved", "note": "تم قبول الإرجاع وفق حالة المنتج الموضحة."},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["return"]["status"] == "approved"

    shipped = buyer.post(
        f"/api/returns/{return_id}/shipment",
        json={"carrier": "شركة توصيل محلية", "tracking_number": "RET-2026-001"},
    )
    assert shipped.status_code == 200, shipped.text
    assert shipped.json()["return"]["status"] == "return_shipped"
    assert shipped.json()["return"]["return_tracking_number"] == "RET-2026-001"

    received = seller.post(
        f"/api/returns/{return_id}/received",
        json={"note": "تم استلام القطعة المرتجعة وفحص حالتها."},
    )
    assert received.status_code == 200, received.text
    assert received.json()["return"]["status"] == "received"

    admin_user = factory.user("admin")
    admin = admin_user["client"]
    decision = admin.post(
        f"/api/admin/returns/{return_id}/decision",
        json={"status": "resolved", "note": "تم استلام المرتجع ومراجعة سجل الإرجاع."},
    )
    assert decision.status_code == 200, decision.text
    resolved = decision.json()["return"]
    assert resolved["status"] == "resolved"
    assert [event["event_type"] for event in resolved["events"]] == [
        "opened", "seller_approved", "buyer_return_shipped", "seller_received", "admin_resolved"
    ]

    with factory.db.read() as c:
        assert c.execute(
            "SELECT COUNT(*) FROM ledger WHERE seller_order_id=?",
            (shipment["id"],),
        ).fetchone()[0] == 1
        assert c.execute(
            "SELECT COUNT(*) FROM return_request_events WHERE request_id=?",
            (return_id,),
        ).fetchone()[0] == 5
        assert c.execute(
            "SELECT status FROM seller_orders WHERE id=?",
            (shipment["id"],),
        ).fetchone()["status"] == "delivered"


def test_return_window_quantity_and_ownership_rules(market, monkeypatch):
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    _, item = _delivered_item(factory, market["a"], market["buyer"], market["p"], 2)
    with factory.db.read() as c:
        delivered_at = c.execute(
            "SELECT created_at FROM order_events WHERE seller_order_id=? AND to_status='delivered'",
            (c.execute("SELECT seller_order_id FROM order_items WHERE id=?", (item["id"],)).fetchone()[0],),
        ).fetchone()["created_at"]

    payload = {
        "order_item_id": item["id"], "quantity": 1,
        "reason": "changed_mind",
        "description": "أرغب بإرجاع المنتج ضمن مدة الإرجاع التشغيلية المحددة.",
    }
    monkeypatch.setattr(return_routes, "now", lambda: delivered_at + return_routes.RETURN_WINDOW_SECONDS + 1)
    expired = buyer.post("/api/returns", json=payload)
    assert expired.status_code == 409
    assert expired.json()["code"] == "return_not_eligible"

    monkeypatch.setattr(return_routes, "now", lambda: delivered_at + 2 * 24 * 60 * 60)
    too_many = buyer.post("/api/returns", json={**payload, "quantity": 3})
    assert too_many.status_code == 409
    assert too_many.json()["code"] == "return_quantity"

    accepted = buyer.post("/api/returns", json={**payload, "quantity": 2})
    assert accepted.status_code == 201, accepted.text
    seller = market["a"]["client"]
    rejected = seller.post(
        f"/api/returns/{accepted.json()['return']['id']}/seller-decision",
        json={"status": "rejected", "note": "لا تنطبق شروط الإرجاع التشغيلية على وصف الحالة."},
    )
    assert rejected.status_code == 200
    assert rejected.json()["return"]["status"] == "rejected"

    # A rejected request releases the reserved quantity; the buyer can retry once.
    retry = buyer.post("/api/returns", json={**payload, "quantity": 2})
    assert retry.status_code == 201, retry.text


def test_return_cannot_open_before_delivery_or_for_unverified_buyer(market):
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    factory.cart(market["buyer"], market["p"], 1)
    checkout, _, _ = factory.checkout(market["buyer"])
    assert checkout.status_code == 201
    shipment = checkout.json()["order"]["shipments"][0]
    with factory.db.read() as c:
        item_id = c.execute(
            "SELECT id FROM order_items WHERE seller_order_id=?",
            (shipment["id"],),
        ).fetchone()["id"]
    response = buyer.post("/api/returns", json={
        "order_item_id": item_id, "quantity": 1, "reason": "other",
        "description": "أحاول إرجاع المنتج قبل أن يتم تسجيل استلام الشحنة.",
    })
    assert response.status_code == 409
    assert response.json()["code"] == "return_not_eligible"

    unverified = factory.user(verified=False)["client"]
    assert unverified.post("/api/returns", json={
        "order_item_id": item_id, "quantity": 1, "reason": "other",
        "description": "حساب غير موثق يحاول فتح طلب إرجاع.",
    }).status_code == 403


def test_return_events_and_identity_are_immutable(market):
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    _, item = _delivered_item(factory, market["a"], market["buyer"], market["p"], 1)
    opened = buyer.post("/api/returns", json={
        "order_item_id": item["id"], "quantity": 1, "reason": "other",
        "description": "أريد إرجاع المنتج ضمن المدة التشغيلية المتاحة.",
    })
    assert opened.status_code == 201
    request_id = opened.json()["return"]["id"]
    stranger = factory.user()

    with factory.db.transaction() as c:
        event_id = c.execute(
            "SELECT id FROM return_request_events WHERE request_id=?",
            (request_id,),
        ).fetchone()["id"]
        for sql, parameters in (
            ("UPDATE return_request_events SET body='tampered' WHERE id=?", (event_id,)),
            ("DELETE FROM return_request_events WHERE id=?", (event_id,)),
            ("UPDATE return_requests SET quantity=99 WHERE id=?", (request_id,)),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(sql, parameters)
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                """INSERT INTO return_request_events(request_id,actor_id,actor_role,event_type,body,created_at)
                   VALUES(?,?,'buyer','buyer_cancelled','unauthorized',1)""",
                (request_id,stranger["id"]),
            )

def test_return_database_guards_reject_forged_owner_quantity_and_initial_status(market):
    factory = market["factory"]
    _, item = _delivered_item(factory, market["a"], market["buyer"], market["p"], 2)
    admin = factory.user("admin")
    stranger = factory.user()
    with factory.db.read() as c:
        shipment_id = c.execute(
            "SELECT seller_order_id FROM order_items WHERE id=?",
            (item["id"],),
        ).fetchone()["seller_order_id"]
        delivered_at = c.execute(
            "SELECT created_at FROM order_events WHERE seller_order_id=? AND to_status='delivered'",
            (shipment_id,),
        ).fetchone()["created_at"]

    requested_at = delivered_at + 10
    response_due_at = requested_at + return_routes.SELLER_RESPONSE_SECONDS
    common = (
        item["id"], shipment_id, market["buyer"]["id"], market["a"]["id"],
        "other", "Direct database guard test with a delivered purchased item.",
        requested_at, response_due_at,
    )
    with factory.db.transaction() as c:
        # A user cannot replace the actual buyer identity in the row.
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                """INSERT INTO return_requests(
                     order_item_id,seller_order_id,buyer_id,seller_id,quantity,reason,description,
                     status,requested_at,response_due_at,updated_at
                   ) VALUES(?,?,?,?,1,?,?,'requested',?,?,?)""",
                (item["id"],shipment_id,stranger["id"],market["a"]["id"],"other",common[5],requested_at,response_due_at,requested_at),
            )
        # Quantity is constrained by the purchased order-item row, not by caller input.
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                """INSERT INTO return_requests(
                     order_item_id,seller_order_id,buyer_id,seller_id,quantity,reason,description,
                     status,requested_at,response_due_at,updated_at
                   ) VALUES(?,?,?,?,3,?,?,'requested',?,?,?)""",
                (item["id"],shipment_id,market["buyer"]["id"],market["a"]["id"],"other",common[5],requested_at,response_due_at,requested_at),
            )
        # A row cannot be inserted directly as already resolved, even with closure fields filled.
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                """INSERT INTO return_requests(
                     order_item_id,seller_order_id,buyer_id,seller_id,quantity,reason,description,
                     status,requested_at,response_due_at,updated_at,return_carrier,
                     return_tracking_number,closed_at,closed_by,decision_note
                   ) VALUES(?,?,?,?,1,?,?,'resolved',?,?,?,'Carrier','TRACK-001',?,?,'Forged closure')""",
                (item["id"],shipment_id,market["buyer"]["id"],market["a"]["id"],"other",common[5],requested_at,response_due_at,requested_at,requested_at,admin["id"]),
            )

