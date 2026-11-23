import secrets
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
