import sqlite3
import server.routes.cases as case_routes


def _shipment(factory, seller, buyer, product, delivered=False):
    factory.cart(buyer, product, 1)
    response, _, _ = factory.checkout(buyer)
    assert response.status_code == 201, response.text
    order = response.json()["order"]
    shipment = next(item for item in order["shipments"] if item["store_id"] == seller["store_id"])
    previous = "new"
    statuses = ["confirmed", "preparing", "shipped"]
    if delivered:
        statuses.append("delivered")
    for status in statuses:
        changed = seller["client"].patch(
            f"/api/seller/orders/{shipment['id']}/status",
            json={"status": status, "expected_status": previous},
        )
        assert changed.status_code == 200, changed.text
        previous = status
    return order, shipment


def test_case_creation_is_shipment_scoped_private_and_has_no_financial_side_effects(market):
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    order, shipment = _shipment(factory, market["a"], market["buyer"], market["p"])
    payload = {
        "seller_order_id": shipment["id"],
        "reason": "not_received",
        "description": "لم أستلم الشحنة بعد وأحتاج إلى معرفة موعد التسليم.",
    }
    created = buyer.post("/api/cases", json=payload)
    assert created.status_code == 201, created.text
    case = created.json()["case"]
    assert case["status"] == "open"
    assert case["response_due_at"] - case["opened_at"] == 48 * 60 * 60
    assert case["viewer_role"] == "buyer"
    assert case["events"][0]["event_type"] == "opened"
    assert case["events"][0]["body"] == payload["description"]

    seller_cases = market["a"]["client"].get("/api/cases")
    assert seller_cases.status_code == 200
    assert seller_cases.json()["cases"][0]["id"] == case["id"]
    assert seller_cases.json()["cases"][0]["viewer_role"] == "seller"
    assert buyer.get(f"/api/cases/{case['id']}").status_code == 200
    stranger = factory.user()["client"]
    assert stranger.get(f"/api/cases/{case['id']}").status_code == 404

    duplicate = buyer.post("/api/cases", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "case_exists"

    with factory.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM ledger WHERE seller_order_id=?", (shipment["id"],)).fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM order_case_events WHERE case_id=?", (case["id"],)).fetchone()[0] == 1
        notification = c.execute(
            "SELECT body_ar,body_en FROM notifications WHERE user_id=? AND kind='case'",
            (market["a"]["id"],),
        ).fetchone()
        assert notification is not None
        assert payload["description"] not in notification["body_ar"]
        assert payload["description"] not in notification["body_en"]
        assert c.execute("SELECT status FROM seller_orders WHERE id=?", (shipment["id"],)).fetchone()["status"] == "shipped"


def test_case_replies_alternate_participants_and_admin_decision_is_audited(market):
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    _, shipment = _shipment(factory, market["a"], market["buyer"], market["p"])
    opened = buyer.post("/api/cases", json={
        "seller_order_id": shipment["id"],
        "reason": "not_received",
        "description": "لم يصلني الطلب حتى الآن، وأرجو التحقق من حالة الشحنة.",
    })
    assert opened.status_code == 201, opened.text
    case_id = opened.json()["case"]["id"]

    seller_reply = market["a"]["client"].post(
        f"/api/cases/{case_id}/messages",
        json={"body": "تواصلنا مع مندوب التوصيل وسنوافيك بالتحديث."},
    )
    assert seller_reply.status_code == 201, seller_reply.text
    assert seller_reply.json()["case"]["status"] == "seller_responded"

    buyer_reply = buyer.post(
        f"/api/cases/{case_id}/messages",
        json={"body": "شكرًا، سأنتظر تحديثكم."},
    )
    assert buyer_reply.status_code == 201, buyer_reply.text
    assert buyer_reply.json()["case"]["status"] == "open"

    admin = factory.user("admin")["client"]
    queue = admin.get("/api/admin/cases?status=open")
    assert queue.status_code == 200, queue.text
    admin_case = next(row for row in queue.json()["cases"] if row["id"] == case_id)
    assert admin_case["buyer_name"] == market["buyer"]["email"].split("@")[0].replace("account-", "Generated user ")
    assert admin_case["events"][-1]["event_type"] == "buyer_message"
    all_cases = admin.get("/api/admin/cases")
    assert all_cases.status_code == 200 and len(all_cases.json()["cases"]) == 1
    admin_detail = admin.get(f"/api/admin/cases/{case_id}")
    assert admin_detail.status_code == 200
    assert admin.get("/api/admin/cases/999999").status_code == 404
    missing_decision = admin.post(
        "/api/admin/cases/999999/decision",
        json={"status": "resolved", "note": "لا توجد شكوى بهذا الرقم."},
    )
    assert missing_decision.status_code == 404

    decision = admin.post(
        f"/api/admin/cases/{case_id}/decision",
        json={"status": "resolved", "note": "تمت مراجعة الشكوى والتأكد من معالجة سببها."},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["case"]["status"] == "resolved"
    assert decision.json()["case"]["events"][-1]["event_type"] == "admin_resolved"
    repeated_decision = admin.post(
        f"/api/admin/cases/{case_id}/decision",
        json={"status": "rejected", "note": "محاولة قرار ثانٍ بعد الإغلاق."},
    )
    assert repeated_decision.status_code == 409
    closed = buyer.post(
        f"/api/cases/{case_id}/messages",
        json={"body": "رسالة بعد الإغلاق."},
    )
    assert closed.status_code == 409
    assert closed.json()["code"] == "case_closed"

    with factory.db.read() as c:
        assert c.execute(
            "SELECT COUNT(*) FROM audit_log WHERE entity='order_case' AND entity_id=?",
            (str(case_id),),
        ).fetchone()[0] >= 4
        assert c.execute(
            "SELECT COUNT(*) FROM notifications WHERE kind='case' AND user_id IN (?,?)",
            (market["buyer"]["id"], market["a"]["id"]),
        ).fetchone()[0] >= 3
        assert c.execute("SELECT COUNT(*) FROM ledger WHERE seller_order_id=?", (shipment["id"],)).fetchone()[0] == 0


def test_case_reason_and_delivery_window_are_enforced_from_shipment_events(market, monkeypatch):
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    _, shipment = _shipment(factory, market["a"], market["buyer"], market["p"], delivered=True)
    with factory.db.read() as c:
        delivered_at = c.execute(
            "SELECT created_at FROM order_events WHERE seller_order_id=? AND to_status='delivered'",
            (shipment["id"],),
        ).fetchone()["created_at"]

    too_late = delivered_at + case_routes.CASE_WINDOW_SECONDS + 1
    monkeypatch.setattr(case_routes, "now", lambda: too_late)
    too_early = delivered_at - 1
    monkeypatch.setattr(case_routes, "now", lambda: too_early)
    early = buyer.post("/api/cases", json={
        "seller_order_id": shipment["id"],
        "reason": "damaged",
        "description": "أحاول فتح الشكوى قبل تسجيل حدث التسليم النهائي.",
    })
    assert early.status_code == 409
    assert early.json()["code"] == "case_window_expired"

    expired = buyer.post("/api/cases", json={
        "seller_order_id": shipment["id"],
        "reason": "damaged",
        "description": "وصل المنتج متضررًا وأريد تسجيل الحالة ومراجعتها.",
    })
    assert expired.status_code == 409
    assert expired.json()["code"] == "case_window_expired"

    monkeypatch.setattr(case_routes, "now", lambda: delivered_at + 2 * 24 * 60 * 60)
    invalid_reason = buyer.post("/api/cases", json={
        "seller_order_id": shipment["id"],
        "reason": "not_received",
        "description": "وصلني الطلب ولكنني اخترت سببًا لا يطابق حالة الشحنة.",
    })
    assert invalid_reason.status_code == 422

    accepted = buyer.post("/api/cases", json={
        "seller_order_id": shipment["id"],
        "reason": "damaged",
        "description": "وصل المنتج متضررًا وأرفق وصفًا تفصيليًا للمشكلة هنا.",
    })
    assert accepted.status_code == 201, accepted.text
    admin = factory.user("admin")["client"]
    detail = admin.get(f"/api/admin/cases/{accepted.json()['case']['id']}")
    assert detail.status_code == 200
    rejected = admin.post(
        f"/api/admin/cases/{accepted.json()['case']['id']}/decision",
        json={"status": "rejected", "note": "لم يثبت سبب الشكوى ضمن المعلومات المتاحة."},
    )
    assert rejected.status_code == 200
    assert rejected.json()["case"]["status"] == "rejected"


def test_case_opening_rejects_wrong_owner_unshipped_and_unverified_accounts(market):
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    _, shipment = _shipment(factory, market["a"], market["buyer"], market["p"])
    stranger = factory.user()["client"]
    payload = {
        "seller_order_id": shipment["id"],
        "reason": "not_received",
        "description": "أريد الاستفسار عن شحنة لا تخص حسابي.",
    }
    assert stranger.post("/api/cases", json=payload).status_code == 404
    assert market["b"]["client"].post("/api/cases", json=payload).status_code == 404
    wrong_reason = buyer.post("/api/cases", json={**payload, "reason": "damaged"})
    assert wrong_reason.status_code == 422

    # A fresh order remains in the new state and cannot enter the case workflow.
    second_product = factory.product(market["a"], stock=2)
    factory.cart(market["buyer"], second_product, 1)
    response, _, _ = factory.checkout(market["buyer"])
    fresh_shipment = response.json()["order"]["shipments"][0]
    not_shipped = buyer.post("/api/cases", json={
        **payload,
        "seller_order_id": fresh_shipment["id"],
    })
    assert not_shipped.status_code == 409
    assert not_shipped.json()["code"] == "case_not_eligible"

    unverified = factory.user(verified=False)["client"]
    assert unverified.post("/api/cases", json=payload).status_code == 403


def test_case_event_and_status_guards_reject_forgery_and_history_changes(market):
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    _, shipment = _shipment(factory, market["a"], market["buyer"], market["p"])
    opened = buyer.post("/api/cases", json={
        "seller_order_id": shipment["id"],
        "reason": "not_received",
        "description": "لم تصل الشحنة وأحتاج إلى فتح سجل متابعة رسمي داخل المنصة.",
    })
    assert opened.status_code == 201, opened.text
    case_id = opened.json()["case"]["id"]
    stranger = factory.user()

    with factory.db.transaction() as c:
        try:
            c.execute(
                """INSERT INTO order_case_events(case_id,actor_id,actor_role,event_type,body,created_at)
                   VALUES(?,?,'seller','seller_response','محاولة مزورة',1)""",
                (case_id, stranger["id"]),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("A non-participant must not append a case event")

        event_id = c.execute(
            "SELECT id FROM order_case_events WHERE case_id=? ORDER BY id LIMIT 1",
            (case_id,),
        ).fetchone()["id"]
        for statement in (
            "UPDATE order_case_events SET body='tampered' WHERE id=?",
            "DELETE FROM order_case_events WHERE id=?",
        ):
            try:
                c.execute(statement, (event_id,))
            except sqlite3.IntegrityError:
                continue
            raise AssertionError("Case event history must be append-only")

        try:
            c.execute("UPDATE order_cases SET description='tampered opening evidence' WHERE id=?", (case_id,))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("Opening evidence must be immutable")

        try:
            c.execute(
                """UPDATE order_cases SET status='resolved',closed_at=2,closed_by=?,resolution_note='forged decision',
                       updated_at=2 WHERE id=?""",
                (stranger["id"], case_id),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("Case status cannot be changed outside the authorized workflow")
