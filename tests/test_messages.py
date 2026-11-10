import secrets
import sqlite3


def make_multistore_order(market):
    factory = market["factory"]
    buyer = market["buyer"]
    factory.cart(buyer, market["p"], 1)
    quote = factory.cart(buyer, market["q"], 1)
    response, _, _ = factory.checkout(buyer, quote)
    assert response.status_code == 201, response.text
    return response.json()["order"]


def test_buyer_and_only_the_shipment_seller_can_exchange_private_messages(market):
    order = make_multistore_order(market)
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    seller_a = market["a"]["client"]
    seller_b = market["b"]["client"]
    shipment_a = next(s for s in order["shipments"] if s["store_id"] == market["a"]["store_id"])

    start = buyer.post("/api/conversations", json={
        "seller_order_id": shipment_a["id"],
        "body": "أهلًا، أريد الاستفسار عن الشحنة.",
        "client_key": secrets.token_urlsafe(24),
    })
    assert start.status_code == 201, start.text
    conversation_id = start.json()["conversation"]["id"]

    assert buyer.get("/api/conversations").json()["conversations"][0]["id"] == conversation_id
    seller_thread = seller_a.get(f"/api/conversations/{conversation_id}/messages")
    assert seller_thread.status_code == 200, seller_thread.text
    assert len(seller_thread.json()["messages"]) == 1

    assert seller_b.get(f"/api/conversations/{conversation_id}/messages").status_code == 404
    assert seller_b.post(f"/api/conversations/{conversation_id}/messages", json={
        "body": "محاولة غير مصرح بها",
        "client_key": secrets.token_urlsafe(24),
    }).status_code == 404
    other_buyer = factory.user()["client"]
    assert other_buyer.get(f"/api/conversations/{conversation_id}/messages").status_code == 404
    assert seller_b.get("/api/conversations").json()["conversations"] == []

    reply = seller_a.post(f"/api/conversations/{conversation_id}/messages", json={
        "body": "تم استلام استفسارك، شكرًا لك.",
        "client_key": secrets.token_urlsafe(24),
    })
    assert reply.status_code == 200, reply.text
    messages = buyer.get(f"/api/conversations/{conversation_id}/messages").json()
    assert [m["body"] for m in messages["messages"]] == [
        "أهلًا، أريد الاستفسار عن الشحنة.",
        "تم استلام استفسارك، شكرًا لك.",
    ]
    payload = str(messages)
    assert "0791234567" not in payload
    assert "Generated delivery address" not in payload
    assert "recipient" not in payload and "phone" not in payload


def test_message_idempotency_validation_notifications_and_append_only(market):
    order = make_multistore_order(market)
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    shipment = next(s for s in order["shipments"] if s["store_id"] == market["a"]["store_id"])
    key = secrets.token_urlsafe(24)
    payload = {"seller_order_id": shipment["id"], "body": "  رسالة أولى  ", "client_key": key}

    first = buyer.post("/api/conversations", json=payload)
    assert first.status_code == 201, first.text
    assert first.json()["message"]["body"] == "رسالة أولى"
    repeated = buyer.post("/api/conversations", json=payload)
    assert repeated.status_code == 200
    assert repeated.json()["replayed"] is True
    assert repeated.json()["message"]["id"] == first.json()["message"]["id"]

    changed = buyer.post("/api/conversations", json={**payload, "body": "محتوى مختلف بنفس المفتاح"})
    assert changed.status_code == 409
    assert changed.json()["code"] == "idempotency"
    invalid = buyer.post("/api/conversations", json={**payload, "body": "   ", "client_key": secrets.token_urlsafe(24)})
    assert invalid.status_code == 422

    conversation_id = first.json()["conversation"]["id"]
    with factory.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conversation_id,)).fetchone()[0] == 1
        notification = c.execute(
            "SELECT body_ar,body_en FROM notifications WHERE user_id=? AND kind='message'",
            (market["a"]["id"],),
        ).fetchone()
        assert notification is not None
        assert "رسالة أولى" not in notification["body_ar"]
        assert "رسالة أولى" not in notification["body_en"]
        message_id = first.json()["message"]["id"]

    with factory.db.transaction() as c:
        for statement in (
            "UPDATE messages SET body='tampered' WHERE id=?",
            "DELETE FROM messages WHERE id=?",
        ):
            try:
                c.execute(statement, (message_id,))
            except sqlite3.IntegrityError:
                continue
            raise AssertionError("Message records must be append-only")


def test_messages_require_verified_accounts_and_nonparticipant_cannot_start_thread(market):
    order = make_multistore_order(market)
    factory = market["factory"]
    shipment = order["shipments"][0]
    unverified = factory.user(verified=False)["client"]
    response = unverified.post("/api/conversations", json={
        "seller_order_id": shipment["id"],
        "body": "رسالة من حساب غير مؤكد",
        "client_key": secrets.token_urlsafe(24),
    })
    assert response.status_code == 403

    nonparticipant = factory.user()["client"]
    response = nonparticipant.post("/api/conversations", json={
        "seller_order_id": shipment["id"],
        "body": "رسالة غير مخوّلة",
        "client_key": secrets.token_urlsafe(24),
    })
    assert response.status_code == 404


def test_database_rejects_message_sender_who_is_not_a_shipment_party(market):
    order = make_multistore_order(market)
    factory = market["factory"]
    buyer = market["buyer"]["client"]
    shipment = order["shipments"][0]
    start = buyer.post("/api/conversations", json={
        "seller_order_id": shipment["id"],
        "body": "رسالة صحيحة",
        "client_key": secrets.token_urlsafe(24),
    })
    assert start.status_code == 201, start.text
    conversation_id = start.json()["conversation"]["id"]
    stranger = factory.user()
    with factory.db.transaction() as c:
        try:
            c.execute(
                """INSERT INTO messages(conversation_id,sender_id,client_key,body,body_hash,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (conversation_id, stranger["id"], secrets.token_urlsafe(24), "forged", "a" * 64, 1),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("Database must reject non-participant message senders")
