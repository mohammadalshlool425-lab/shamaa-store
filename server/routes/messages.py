"""Private, text-only conversations scoped to an existing seller shipment."""
import hashlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .. import schemas
from ..database import now
from ..errors import fail
from ..security import audit, require_user, throttle, verified_user

router = APIRouter(prefix="/api", tags=["Private messages"])


def _conversation_select():
    return """
      SELECT c.id,c.seller_order_id,c.created_at,c.updated_at,
             so.status AS shipment_status,o.id AS order_id,o.number AS order_number,
             o.buyer_id,buyer.name AS buyer_name,s.owner_id AS seller_id,
             s.name_ar AS store_name_ar,s.name_en AS store_name_en,s.slug AS store_slug,
             (SELECT m.body FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_message,
             (SELECT m.created_at FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS last_message_at
        FROM conversations c
        JOIN seller_orders so ON so.id=c.seller_order_id
        JOIN orders o ON o.id=so.order_id
        JOIN users buyer ON buyer.id=o.buyer_id
        JOIN stores s ON s.id=so.store_id
    """


def _participant_row(c, conversation_id, user_id):
    row = c.execute(
        _conversation_select()
        + " WHERE c.id=? AND (o.buyer_id=? OR s.owner_id=?)",
        (conversation_id, user_id, user_id),
    ).fetchone()
    if not row:
        fail("not_found", 404)
    return row


def _summary(row, user):
    is_buyer = row["buyer_id"] == user["id"]
    return {
        "id": row["id"],
        "seller_order_id": row["seller_order_id"],
        "order_number": row["order_number"],
        "shipment_status": row["shipment_status"],
        "store_name_ar": row["store_name_ar"],
        "store_name_en": row["store_name_en"],
        "store_slug": row["store_slug"],
        "counterpart_name": row["store_name_ar"] if is_buyer else row["buyer_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _save_message(c, conversation, user, body, client_key):
    conversation_id = conversation["id"]
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

    # INSERT ... DO NOTHING makes a repeated idempotency key safe when two
    # requests race on PostgreSQL as well as on the serialized SQLite writer.
    inserted = c.execute(
        """INSERT INTO messages(
             conversation_id,sender_id,client_key,body,body_hash,created_at
           ) VALUES(?,?,?,?,?,?)
           ON CONFLICT(conversation_id,sender_id,client_key) DO NOTHING
           RETURNING id""",
        (conversation_id, user["id"], client_key, body, body_hash, now()),
    ).fetchone()

    if not inserted:
        existing = c.execute(
            """SELECT id,sender_id,body,body_hash,created_at
                 FROM messages
                WHERE conversation_id=? AND sender_id=? AND client_key=?""",
            (conversation_id, user["id"], client_key),
        ).fetchone()
        if not existing or existing["body_hash"] != body_hash:
            fail("idempotency", 409)
        return {
            "id": existing["id"],
            "sender_id": existing["sender_id"],
            "body": existing["body"],
            "created_at": existing["created_at"],
        }, True

    message_id = inserted["id"]
    created = c.execute(
        "SELECT created_at FROM messages WHERE id=?",
        (message_id,),
    ).fetchone()["created_at"]

    c.execute(
        "UPDATE conversations SET updated_at=? WHERE id=?",
        (created, conversation_id),
    )
    recipient_id = (
        conversation["seller_id"]
        if user["id"] == conversation["buyer_id"]
        else conversation["buyer_id"]
    )
    # Notification previews never contain user-authored message text.
    c.execute(
        """INSERT INTO notifications(user_id,kind,body_ar,body_en,created_at)
           VALUES(?,'message',?,?,?)""",
        (
            recipient_id,
            "لديك رسالة جديدة بشأن إحدى شحناتك.",
            "You have a new message about one of your shipments.",
            created,
        ),
    )
    audit(
        c, user["id"], "message_sent", "conversation", conversation_id,
        f"shipment={conversation['seller_order_id']};message={message_id}",
    )
    return {
        "id": message_id,
        "sender_id": user["id"],
        "body": body,
        "created_at": created,
    }, False


@router.get("/conversations")
def list_conversations(request: Request, user=Depends(require_user)):
    with request.app.state.db.read() as c:
        rows = c.execute(
            _conversation_select()
            + """ WHERE o.buyer_id=? OR s.owner_id=?
                 ORDER BY c.updated_at DESC,c.id DESC LIMIT 100""",
            (user["id"], user["id"]),
        ).fetchall()
        return {
            "conversations": [
                {
                    **_summary(row, user),
                    "last_message": row["last_message"] or "",
                    "last_message_at": row["last_message_at"] or row["created_at"],
                }
                for row in rows
            ]
        }


@router.post("/conversations")
def start_conversation(
    body: schemas.ConversationStart,
    request: Request,
    user=Depends(verified_user),
):
    throttle(request, "message_start", limit=10, window=3600, identifier=str(user["id"]))
    with request.app.state.db.transaction() as c:
        shipment = c.execute(
            """SELECT so.id AS seller_order_id,o.buyer_id,s.owner_id AS seller_id
                 FROM seller_orders so
                 JOIN orders o ON o.id=so.order_id
                 JOIN stores s ON s.id=so.store_id
                WHERE so.id=?""",
            (body.seller_order_id,),
        ).fetchone()
        if not shipment or user["id"] not in (
            shipment["buyer_id"], shipment["seller_id"]
        ):
            fail("not_found", 404)

        created = now()
        c.execute(
            """INSERT INTO conversations(seller_order_id,created_at,updated_at)
               VALUES(?,?,?) ON CONFLICT(seller_order_id) DO NOTHING""",
            (body.seller_order_id, created, created),
        )
        conversation_id = c.execute(
            "SELECT id FROM conversations WHERE seller_order_id=?",
            (body.seller_order_id,),
        ).fetchone()["id"]
        conversation = _participant_row(c, conversation_id, user["id"])
        message, replayed = _save_message(
            c, conversation, user, body.body, body.client_key
        )
        result = _summary(conversation, user)

    return JSONResponse(
        {"conversation": result, "message": message, "replayed": replayed},
        status_code=200 if replayed else 201,
    )


@router.get("/conversations/{conversation_id}/messages")
def get_messages(
    conversation_id: int,
    request: Request,
    user=Depends(require_user),
):
    with request.app.state.db.read() as c:
        conversation = _participant_row(c, conversation_id, user["id"])
        rows = c.execute(
            """SELECT id,sender_id,body,created_at FROM (
                 SELECT id,sender_id,body,created_at
                   FROM messages WHERE conversation_id=?
                   ORDER BY id DESC LIMIT 100
               ) recent ORDER BY id ASC""",
            (conversation_id,),
        ).fetchall()
        return {
            "conversation": _summary(conversation, user),
            "messages": [dict(row) for row in rows],
        }


@router.post("/conversations/{conversation_id}/messages")
def send_message(
    conversation_id: int,
    body: schemas.MessageSend,
    request: Request,
    user=Depends(verified_user),
):
    throttle(
        request, "message_send", limit=20, window=60,
        identifier=f"{user['id']}:{conversation_id}",
    )
    with request.app.state.db.transaction() as c:
        conversation = _participant_row(c, conversation_id, user["id"])
        message, replayed = _save_message(
            c, conversation, user, body.body, body.client_key
        )
        result = _summary(conversation, user)

    return JSONResponse(
        {"conversation": result, "message": message, "replayed": replayed},
        status_code=200,
    )
