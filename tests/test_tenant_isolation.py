import pytest
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from io import BytesIO

from PIL import Image


def _multi_vendor_order(market):
    factory = market["factory"]
    buyer = market["buyer"]
    factory.cart(buyer, market["p"], 1)
    quote = factory.cart(buyer, market["q"], 1)
    response, _, _ = factory.checkout(buyer, quote)
    assert response.status_code == 201, response.text
    return response.json()["order"]


def _advance_shipment(seller, shipment, final_status="shipped"):
    previous = "new"
    statuses = ["confirmed", "preparing", "shipped"]
    if final_status == "delivered":
        statuses.append("delivered")
    for status in statuses:
        response = seller["client"].patch(
            f"/api/seller/orders/{shipment['id']}/status",
            json={"status": status, "expected_status": previous},
        )
        assert response.status_code == 200, response.text
        previous = status


def _evidence_png():
    output = BytesIO()
    Image.new("RGB", (64, 48), (20, 80, 140)).save(output, format="PNG")
    return output.getvalue()


def test_cross_store_conversation_and_case_evidence_are_private(market):
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    other_buyer = factory.user()["client"]
    order = _multi_vendor_order(market)
    shipment_a = next(
        shipment for shipment in order["shipments"]
        if shipment["store_id"] == market["a"]["store_id"]
    )

    conversation = buyer.post("/api/conversations", json={
        "seller_order_id": shipment_a["id"],
        "body": "أريد الاستفسار عن الشحنة التابعة للمتجر الأول.",
        "client_key": secrets.token_urlsafe(24),
    })
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["conversation"]["id"]

    # A different store owner cannot use a guessed conversation or shipment id.
    assert market["b"]["client"].get(
        f"/api/conversations/{conversation_id}/messages"
    ).status_code == 404
    cross_store_start = market["b"]["client"].post("/api/conversations", json={
        "seller_order_id": shipment_a["id"],
        "body": "محاولة فتح محادثة على شحنة متجر آخر.",
        "client_key": secrets.token_urlsafe(24),
    })
    assert cross_store_start.status_code == 404
    assert other_buyer.get(
        f"/api/conversations/{conversation_id}/messages"
    ).status_code == 404

    _advance_shipment(market["a"], shipment_a)
    opened = buyer.post("/api/cases", json={
        "seller_order_id": shipment_a["id"],
        "reason": "not_received",
        "description": "لم تصل الشحنة وأحتاج إلى مراجعة حالة التسليم.",
    })
    assert opened.status_code == 201, opened.text
    case_id = opened.json()["case"]["id"]

    upload = buyer.post(
        f"/api/cases/{case_id}/attachments",
        files={"file": ("proof.png", _evidence_png(), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    attachment_id = upload.json()["attachment_id"]
    download_path = f"/api/cases/{case_id}/attachments/{attachment_id}"

    # Only the buyer and the owner of the linked shipment may see the case/evidence.
    assert market["a"]["client"].get(f"/api/cases/{case_id}").status_code == 200
    assert market["a"]["client"].get(download_path).status_code == 200
    for outsider in (market["b"]["client"], other_buyer):
        assert outsider.get(f"/api/cases/{case_id}").status_code == 404
        assert outsider.get(download_path).status_code == 404
        assert outsider.post(
            f"/api/cases/{case_id}/messages",
            json={"body": "رسالة من مستخدم غير مشارك."},
        ).status_code == 404
        assert outsider.post(
            f"/api/cases/{case_id}/attachments",
            files={"file": ("proof.png", _evidence_png(), "image/png")},
        ).status_code == 404

    # The second seller cannot claim the first seller's shipment when opening a case.
    forged_case = market["b"]["client"].post("/api/cases", json={
        "seller_order_id": shipment_a["id"],
        "reason": "not_received",
        "description": "محاولة إنشاء شكوى على شحنة لا تخص هذا المتجر.",
    })
    assert forged_case.status_code == 404


def test_return_detail_and_transitions_are_buyer_and_store_scoped(market):
    factory = market["factory"]
    buyer_user = market["buyer"]
    buyer = buyer_user["client"]
    other_buyer = factory.user()["client"]
    factory.cart(buyer_user, market["p"], 1)
    response, _, _ = factory.checkout(buyer_user)
    assert response.status_code == 201, response.text
    order = response.json()["order"]
    shipment = next(
        item for item in order["shipments"]
        if item["store_id"] == market["a"]["store_id"]
    )
    _advance_shipment(market["a"], shipment, final_status="delivered")

    with factory.db.read() as connection:
        item_id = connection.execute(
            "SELECT id FROM order_items WHERE seller_order_id=?",
            (shipment["id"],),
        ).fetchone()["id"]

    opened = buyer.post("/api/returns", json={
        "order_item_id": item_id,
        "quantity": 1,
        "reason": "damaged",
        "description": "وصل المنتج متضررًا وأرغب بتسجيل طلب إرجاع.",
    })
    assert opened.status_code == 201, opened.text
    return_id = opened.json()["return"]["id"]
    detail_path = f"/api/returns/{return_id}"

    assert buyer.get(detail_path).status_code == 200
    assert market["a"]["client"].get(detail_path).status_code == 200
    assert market["b"]["client"].get(detail_path).status_code == 404
    assert other_buyer.get(detail_path).status_code == 404

    denied_decision = market["b"]["client"].post(
        f"/api/returns/{return_id}/seller-decision",
        json={"status": "approved", "note": "محاولة قرار من متجر آخر."},
    )
    assert denied_decision.status_code == 404
    denied_cancel = other_buyer.post(f"{detail_path}/cancel")
    assert denied_cancel.status_code == 404

    assert all(
        row["id"] != return_id
        for row in market["b"]["client"].get("/api/returns").json()["returns"]
    )
    assert all(
        row["id"] != return_id
        for row in other_buyer.get("/api/returns").json()["returns"]
    )



def test_concurrent_shipment_delivery_creates_one_commission_due_entry(market):
    factory = market["factory"]
    order = _multi_vendor_order(market)
    shipment = next(
        item for item in order["shipments"]
        if item["store_id"] == market["a"]["store_id"]
    )
    _advance_shipment(market["a"], shipment, final_status="shipped")
    barrier = Barrier(2)

    def deliver():
        barrier.wait(timeout=10)
        return market["a"]["client"].patch(
            f"/api/seller/orders/{shipment['id']}/status",
            json={"status": "delivered", "expected_status": "shipped"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(deliver)
        second = pool.submit(deliver)
        responses = [first.result(timeout=30), second.result(timeout=30)]

    assert sorted(response.status_code for response in responses) == [200, 409]
    with factory.db.read() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM ledger WHERE seller_order_id=? AND kind='cod_commission_due'",
            (shipment["id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM order_events WHERE seller_order_id=? AND to_status='delivered'",
            (shipment["id"],),
        ).fetchone()[0] == 1

    seller_ledger = market["a"]["client"].get("/api/seller/ledger")
    assert seller_ledger.status_code == 200
    assert len(seller_ledger.json()["entries"]) == 1
    assert seller_ledger.json()["entries"][0]["amount_fils"] == shipment["commission_fils"]
    assert market["b"]["client"].get("/api/seller/ledger").json() == {
        "entries": [], "commission_due_fils": 0
    }


def _received_return_for_financial_gate(market):
    factory = market["factory"]
    buyer_user = market["buyer"]
    buyer = buyer_user["client"]
    factory.cart(buyer_user, market["p"], 1)
    response, _, _ = factory.checkout(buyer_user)
    assert response.status_code == 201, response.text
    shipment = next(
        item for item in response.json()["order"]["shipments"]
        if item["store_id"] == market["a"]["store_id"]
    )
    _advance_shipment(market["a"], shipment, final_status="delivered")
    with factory.db.read() as connection:
        item_id = connection.execute(
            "SELECT id FROM order_items WHERE seller_order_id=?",
            (shipment["id"],),
        ).fetchone()["id"]

    opened = buyer.post("/api/returns", json={
        "order_item_id": item_id,
        "quantity": 1,
        "reason": "damaged",
        "description": "وصل المنتج متضررًا وأحتاج إلى إتمام اختبار التسوية المالية.",
    })
    assert opened.status_code == 201, opened.text
    return_id = opened.json()["return"]["id"]

    approved = market["a"]["client"].post(
        f"/api/returns/{return_id}/seller-decision",
        json={"status": "approved", "note": "تمت الموافقة على الإرجاع للاختبار."},
    )
    assert approved.status_code == 200, approved.text
    shipped = buyer.post(
        f"/api/returns/{return_id}/shipment",
        json={"carrier": "Test carrier", "tracking_number": "TEST-RETURN-001"},
    )
    assert shipped.status_code == 200, shipped.text
    received = market["a"]["client"].post(
        f"/api/returns/{return_id}/received",
        json={"note": "تم استلام المرتجع في بيئة الاختبار."},
    )
    assert received.status_code == 200, received.text
    return return_id, shipment


def test_return_resolution_requires_recent_admin_mfa(market):
    return_id, _ = _received_return_for_financial_gate(market)
    admin = market["factory"].user("admin")

    # Expire only this isolated test session's re-authentication timestamp.
    with market["factory"].db.transaction() as connection:
        connection.execute(
            "UPDATE sessions SET mfa_at=? WHERE user_id=?",
            (1, admin["id"]),
        )

    denied = admin["client"].post(
        f"/api/admin/returns/{return_id}/decision",
        json={"status": "resolved", "note": "قرار اختبار دون إعادة تحقق حديثة."},
    )
    assert denied.status_code == 403, denied.text
    detail = admin["client"].get(f"/api/admin/returns/{return_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["return"]["status"] == "received"


def test_return_resolution_does_not_create_refund_or_change_commission_ledger(market):
    return_id, shipment = _received_return_for_financial_gate(market)
    factory = market["factory"]
    admin = factory.user("admin")

    with factory.db.read() as connection:
        before = [
            dict(row) for row in connection.execute(
                "SELECT seller_order_id,store_id,kind,amount_fils,reference_id "
                "FROM ledger WHERE seller_order_id=? ORDER BY id",
                (shipment["id"],),
            ).fetchall()
        ]

    resolved = admin["client"].post(
        f"/api/admin/returns/{return_id}/decision",
        json={"status": "resolved", "note": "إغلاق المرتجع في اختبار عدم إنشاء استرداد تلقائي."},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["return"]["status"] == "resolved"

    with factory.db.read() as connection:
        after = [
            dict(row) for row in connection.execute(
                "SELECT seller_order_id,store_id,kind,amount_fils,reference_id "
                "FROM ledger WHERE seller_order_id=? ORDER BY id",
                (shipment["id"],),
            ).fetchall()
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE entity='return_request' AND entity_id=? AND action='return_resolved'",
            (str(return_id),),
        ).fetchone()[0] == 1

    # Return workflow closure is not proof of a money transfer or commission reversal.
    assert after == before
    assert len(after) == 1
    assert after[0]["kind"] == "cod_commission_due"


def test_commission_ledger_rejects_update_and_delete(market):
    factory = market["factory"]
    order = _multi_vendor_order(market)
    shipment = next(
        item for item in order["shipments"]
        if item["store_id"] == market["a"]["store_id"]
    )
    _advance_shipment(market["a"], shipment, final_status="delivered")

    with factory.db.transaction() as connection:
        entry = connection.execute(
            "SELECT id,amount_fils FROM ledger WHERE seller_order_id=?",
            (shipment["id"],),
        ).fetchone()
        assert entry is not None
        with pytest.raises(Exception):
            connection.execute(
                "UPDATE ledger SET amount_fils=amount_fils+1 WHERE id=?",
                (entry["id"],),
            )
        with pytest.raises(Exception):
            connection.execute("DELETE FROM ledger WHERE id=?", (entry["id"],))

    with factory.db.read() as connection:
        unchanged = connection.execute(
            "SELECT amount_fils FROM ledger WHERE id=?",
            (entry["id"],),
        ).fetchone()
        assert unchanged["amount_fils"] == entry["amount_fils"]
