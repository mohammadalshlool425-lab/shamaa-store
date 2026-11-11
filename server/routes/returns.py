"""Private item-level return requests with an append-only workflow."""
from typing import Literal

from fastapi import APIRouter, Depends, Request

from .. import schemas
from ..database import DatabaseIntegrityError, insert_id, now
from ..errors import fail
from ..security import admin_user, audit, recent_admin, throttle, verified_user

router = APIRouter(prefix="/api", tags=["Returns"])
admin_router = APIRouter(prefix="/api/admin", tags=["Return administration"])

RETURN_WINDOW_SECONDS = 14 * 24 * 60 * 60
SELLER_RESPONSE_SECONDS = 48 * 60 * 60
ACTIVE_STATUSES = ("requested", "approved", "return_shipped", "received")
RETURNABLE_STATUSES = (*ACTIVE_STATUSES, "resolved")


def _return_row(c, request_id):
    return c.execute(
        """SELECT r.*,o.number AS order_number,
                  oi.title_ar,oi.title_en,oi.unit_price_fils,oi.quantity AS purchased_quantity,
                  oi.variant_sku,oi.variant_attributes,oi.image_url,
                  s.name_ar AS store_name_ar,s.name_en AS store_name_en,s.slug AS store_slug,
                  buyer.name AS buyer_name,seller.name AS seller_name
             FROM return_requests r
             JOIN order_items oi ON oi.id=r.order_item_id
             JOIN seller_orders so ON so.id=r.seller_order_id
             JOIN orders o ON o.id=so.order_id
             JOIN stores s ON s.id=so.store_id
             JOIN users buyer ON buyer.id=r.buyer_id
             JOIN users seller ON seller.id=r.seller_id
            WHERE r.id=?""",
        (request_id,),
    ).fetchone()


def _return_payload(c, row, user, include_private=False):
    events = c.execute(
        """SELECT id,actor_role,event_type,body,created_at
             FROM return_request_events WHERE request_id=? ORDER BY id LIMIT 500""",
        (row["id"],),
    ).fetchall()
    result = {
        key: row[key] for key in (
            "id","order_item_id","seller_order_id","order_number","title_ar","title_en",
            "unit_price_fils","quantity","purchased_quantity","variant_sku","variant_attributes",
            "image_url","store_name_ar","store_name_en","store_slug","reason","description",
            "status","requested_at","response_due_at","updated_at","seller_note",
            "return_carrier","return_tracking_number","closed_at","decision_note"
        )
    }
    result["viewer_role"] = (
        "admin" if user["role"] == "admin" else
        "buyer" if user["id"] == row["buyer_id"] else "seller"
    )
    result["events"] = [dict(event) for event in events]
    result["can_seller_decide"] = (
        user["id"] == row["seller_id"] and row["status"] == "requested"
    )
    result["can_buyer_cancel"] = (
        user["id"] == row["buyer_id"] and row["status"] in ("requested", "approved")
    )
    result["can_buyer_ship"] = (
        user["id"] == row["buyer_id"] and row["status"] == "approved"
    )
    result["can_seller_receive"] = (
        user["id"] == row["seller_id"] and row["status"] == "return_shipped"
    )
    if include_private:
        result.update({"buyer_name": row["buyer_name"], "seller_name": row["seller_name"]})
    return result


def _participant_return(c, request_id, user):
    row = _return_row(c, request_id)
    if not row or user["id"] not in (row["buyer_id"], row["seller_id"]):
        fail("not_found", 404)
    return row


def _add_event(c, request_id, actor_id, actor_role, event_type, body, created):
    return insert_id(
        c,
        """INSERT INTO return_request_events(request_id,actor_id,actor_role,event_type,body,created_at)
           VALUES(?,?,?,?,?,?)""",
        (request_id, actor_id, actor_role, event_type, body, created),
    )


def _notify(c, user_ids, body_ar, body_en, created):
    for user_id in set(user_ids):
        c.execute(
            """INSERT INTO notifications(user_id,kind,body_ar,body_en,created_at)
               VALUES(?,'return',?,?,?)""",
            (user_id, body_ar, body_en, created),
        )


@router.get("/returns")
def list_returns(request: Request, user=Depends(verified_user)):
    with request.app.state.db.read() as c:
        rows = c.execute(
            """SELECT id FROM return_requests
                 WHERE buyer_id=? OR seller_id=?
                 ORDER BY updated_at DESC,id DESC LIMIT 100""",
            (user["id"], user["id"]),
        ).fetchall()
        return {
            "returns": [
                _return_payload(c, _return_row(c, row["id"]), user)
                for row in rows
            ]
        }


@router.post("/returns", status_code=201)
def create_return(body: schemas.ReturnCreate, request: Request, user=Depends(verified_user)):
    throttle(request, "return_open", limit=5, window=3600, identifier=str(user["id"]))
    with request.app.state.db.transaction() as c:
        # The no-op UPDATE acquires a row lock in PostgreSQL; SQLite's
        # BEGIN IMMEDIATE already serializes writers. This protects the
        # remaining-quantity calculation against concurrent return requests.
        item_exists = c.execute(
            "UPDATE order_items SET quantity=quantity WHERE id=?",
            (body.order_item_id,),
        )
        if item_exists.rowcount != 1:
            fail("not_found", 404)
        item = c.execute(
            """SELECT oi.id AS order_item_id,oi.quantity AS purchased_quantity,
                      so.id AS seller_order_id,so.status AS shipment_status,
                      o.buyer_id,s.owner_id AS seller_id,
                      (SELECT e.created_at FROM order_events e
                        WHERE e.seller_order_id=so.id AND e.to_status='delivered'
                        ORDER BY e.id DESC LIMIT 1) AS delivered_at
                 FROM order_items oi
                 JOIN seller_orders so ON so.id=oi.seller_order_id
                 JOIN orders o ON o.id=so.order_id
                 JOIN stores s ON s.id=so.store_id
                WHERE oi.id=? AND o.buyer_id=?""",
            (body.order_item_id, user["id"]),
        ).fetchone()
        if not item:
            fail("not_found", 404)
        opened = now()
        if (
            item["shipment_status"] != "delivered"
            or item["delivered_at"] is None
            or opened < item["delivered_at"]
            or opened - item["delivered_at"] > RETURN_WINDOW_SECONDS
        ):
            fail("return_not_eligible", 409)
        returned = c.execute(
            """SELECT COALESCE(SUM(quantity),0) FROM return_requests
                 WHERE order_item_id=? AND status IN
                   ('requested','approved','return_shipped','received','resolved')""",
            (body.order_item_id,),
        ).fetchone()[0]
        if int(returned) + body.quantity > int(item["purchased_quantity"]):
            fail("return_quantity", 409)
        active = c.execute(
            """SELECT 1 FROM return_requests
                 WHERE order_item_id=? AND status IN
                   ('requested','approved','return_shipped','received')""",
            (body.order_item_id,),
        ).fetchone()
        if active:
            fail("return_active", 409)
        try:
            request_id = insert_id(
                c,
                """INSERT INTO return_requests(
                     order_item_id,seller_order_id,buyer_id,seller_id,quantity,reason,description,
                     status,requested_at,response_due_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,'requested',?,?,?)""",
                (
                    item["order_item_id"],item["seller_order_id"],item["buyer_id"],item["seller_id"],
                    body.quantity,body.reason,body.description,opened,
                    opened + SELLER_RESPONSE_SECONDS,opened,
                ),
            )
        except DatabaseIntegrityError:
            fail("return_active", 409)
        event_id = _add_event(
            c, request_id, user["id"], "buyer", "opened", body.description, opened
        )
        _notify(
            c, [item["seller_id"]],
            "وصلك طلب إرجاع جديد لأحد بنود الطلب. راجع التفاصيل والمدة التشغيلية للرد.",
            "A new return request was submitted for one of your order items. Review it and the response target.",
            opened,
        )
        audit(
            c,user["id"],"return_opened","return_request",request_id,
            f"order_item={item['order_item_id']};quantity={body.quantity};event={event_id}",
        )
        row = _return_row(c, request_id)
        result = _return_payload(c, row, user)
    return {"return": result}


@router.get("/returns/{request_id}")
def get_return(request_id: int, request: Request, user=Depends(verified_user)):
    with request.app.state.db.read() as c:
        row = _participant_return(c, request_id, user)
        return {"return": _return_payload(c, row, user)}


@router.post("/returns/{request_id}/seller-decision")
def seller_decision(
    request_id: int, body: schemas.ReturnSellerDecision,
    request: Request, user=Depends(verified_user)
):
    throttle(request, "return_decision", limit=20, window=3600, identifier=str(user["id"]))
    if body.status == "rejected" and len(body.note) < 5:
        fail("invalid_input", 422)
    with request.app.state.db.transaction() as c:
        row = _participant_return(c, request_id, user)
        if row["seller_id"] != user["id"]:
            fail("not_found", 404)
        if row["status"] != "requested":
            fail("return_closed", 409)
        created = now()
        event_type = "seller_approved" if body.status == "approved" else "seller_rejected"
        note = body.note or "وافق البائع على طلب الإرجاع. Seller approved the return."
        event_id = _add_event(c, request_id, user["id"], "seller", event_type, note, created)
        if body.status == "approved":
            c.execute(
                """UPDATE return_requests SET status='approved',seller_note=?,updated_at=?
                     WHERE id=? AND status='requested'""",
                (body.note,created,request_id),
            )
        else:
            c.execute(
                """UPDATE return_requests SET status='rejected',seller_note=?,closed_at=?,
                          closed_by=?,decision_note=?,updated_at=?
                     WHERE id=? AND status='requested'""",
                (body.note,created,user["id"],body.note,created,request_id),
            )
        _notify(
            c,[row["buyer_id"]],
            "ردّ البائع على طلب الإرجاع. راجع الحالة والتفاصيل في حسابك.",
            "The seller responded to your return request. Review the status and details in your account.",
            created,
        )
        audit(c,user["id"],"return_"+body.status,"return_request",request_id,f"event={event_id}")
        result = _return_payload(c,_return_row(c,request_id),user)
    return {"return": result}


@router.post("/returns/{request_id}/cancel")
def cancel_return(request_id: int, request: Request, user=Depends(verified_user)):
    throttle(request,"return_cancel",limit=10,window=3600,identifier=str(user["id"]))
    with request.app.state.db.transaction() as c:
        row = _participant_return(c, request_id, user)
        if row["buyer_id"] != user["id"] or row["status"] not in ("requested","approved"):
            fail("return_closed", 409)
        created = now()
        note = "ألغى المشتري طلب الإرجاع قبل شحن المنتج المرتجع. Buyer cancelled before return shipment."
        event_id = _add_event(c,request_id,user["id"],"buyer","buyer_cancelled",note,created)
        c.execute(
            """UPDATE return_requests SET status='cancelled',closed_at=?,closed_by=?,
                      decision_note=?,updated_at=? WHERE id=?""",
            (created,user["id"],note,created,request_id),
        )
        _notify(c,[row["seller_id"]],"ألغى المشتري طلب الإرجاع.","The buyer cancelled the return request.",created)
        audit(c,user["id"],"return_cancelled","return_request",request_id,f"event={event_id}")
        result = _return_payload(c,_return_row(c,request_id),user)
    return {"return": result}


@router.post("/returns/{request_id}/shipment")
def ship_return(
    request_id: int, body: schemas.ReturnShipment,
    request: Request, user=Depends(verified_user)
):
    throttle(request,"return_ship",limit=10,window=3600,identifier=str(user["id"]))
    with request.app.state.db.transaction() as c:
        row = _participant_return(c,request_id,user)
        if row["buyer_id"] != user["id"] or row["status"] != "approved":
            fail("return_closed",409)
        created = now()
        note = f"carrier={body.carrier};tracking={body.tracking_number}"
        event_id = _add_event(c,request_id,user["id"],"buyer","buyer_return_shipped",note,created)
        c.execute(
            """UPDATE return_requests SET status='return_shipped',return_carrier=?,
                      return_tracking_number=?,updated_at=? WHERE id=? AND status='approved'""",
            (body.carrier,body.tracking_number,created,request_id),
        )
        _notify(c,[row["seller_id"]],"أرسل المشتري المرتجع وسجّل بيانات التتبع.","The buyer shipped the return and provided tracking details.",created)
        audit(c,user["id"],"return_shipped","return_request",request_id,f"event={event_id}")
        result = _return_payload(c,_return_row(c,request_id),user)
    return {"return": result}


@router.post("/returns/{request_id}/received")
def confirm_return_received(
    request_id: int, body: schemas.ReturnReceived,
    request: Request, user=Depends(verified_user)
):
    throttle(request,"return_receive",limit=20,window=3600,identifier=str(user["id"]))
    with request.app.state.db.transaction() as c:
        row = _participant_return(c,request_id,user)
        if row["seller_id"] != user["id"] or row["status"] != "return_shipped":
            fail("return_closed",409)
        created = now()
        event_id = _add_event(c,request_id,user["id"],"seller","seller_received",body.note,created)
        c.execute(
            """UPDATE return_requests SET status='received',updated_at=?
                 WHERE id=? AND status='return_shipped'""",
            (created,request_id),
        )
        _notify(c,[row["buyer_id"]],"أكد البائع استلام المنتج المرتجع. تنتظر العملية المراجعة النهائية.","The seller confirmed receipt of the returned item. Final review is pending.",created)
        audit(c,user["id"],"return_received","return_request",request_id,f"event={event_id}")
        result = _return_payload(c,_return_row(c,request_id),user)
    return {"return": result}


@admin_router.get("/returns")
def admin_list_returns(
    request: Request,
    status: Literal['requested','approved','rejected','return_shipped','received','resolved','cancelled'] | None = None,
    user=Depends(admin_user),
):
    with request.app.state.db.read() as c:
        if status is None:
            rows = c.execute("SELECT id FROM return_requests ORDER BY updated_at DESC,id DESC LIMIT 200").fetchall()
        else:
            rows = c.execute(
                "SELECT id FROM return_requests WHERE status=? ORDER BY updated_at DESC,id DESC LIMIT 200",
                (status,),
            ).fetchall()
        return {
            "returns":[
                _return_payload(c,_return_row(c,row["id"]),user,include_private=True)
                for row in rows
            ]
        }


@admin_router.get("/returns/{request_id}")
def admin_get_return(request_id: int, request: Request, user=Depends(admin_user)):
    with request.app.state.db.read() as c:
        row = _return_row(c,request_id)
        if not row:
            fail("not_found",404)
        return {"return":_return_payload(c,row,user,include_private=True)}


@admin_router.post("/returns/{request_id}/decision")
def admin_decide_return(
    request_id: int, body: schemas.ReturnAdminDecision,
    request: Request, user=Depends(recent_admin)
):
    throttle(request,"return_admin_decision",limit=30,window=3600,identifier=str(user["id"]))
    with request.app.state.db.transaction() as c:
        row = _return_row(c,request_id)
        if not row:
            fail("not_found",404)
        if row["status"] not in ("requested","approved","return_shipped","received"):
            fail("return_closed",409)
        if body.status == "resolved" and row["status"] != "received":
            fail("invalid_status",409)
        created = now()
        event_type = "admin_resolved" if body.status=="resolved" else "admin_rejected"
        event_id = _add_event(c,request_id,user["id"],"admin",event_type,body.note,created)
        c.execute(
            """UPDATE return_requests SET status=?,closed_at=?,closed_by=?,
                      decision_note=?,updated_at=? WHERE id=?""",
            (body.status,created,user["id"],body.note,created,request_id),
        )
        _notify(
            c,[row["buyer_id"],row["seller_id"]],
            "اتخذت الإدارة قرارًا نهائيًا بشأن طلب الإرجاع. راجع السجل.",
            "An administrator recorded a final decision for the return request. Review its history.",
            created,
        )
        audit(c,user["id"],"return_"+body.status,"return_request",request_id,f"event={event_id}")
        result = _return_payload(c,_return_row(c,request_id),user,include_private=True)
    return {"return":result}
