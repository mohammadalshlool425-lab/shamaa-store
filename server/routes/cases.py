"""Private buyer/seller cases scoped to a real shipment."""
from typing import Literal
from io import BytesIO
import hashlib
import os
import re
import secrets
import warnings
from threading import BoundedSemaphore

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError

from .. import schemas
from ..database import DatabaseIntegrityError, insert_id, now
from ..errors import fail
from ..security import admin_user, audit, recent_admin, throttle, verified_user

router = APIRouter(prefix="/api", tags=["Shipment cases"])
admin_router = APIRouter(prefix="/api/admin", tags=["Shipment case administration"])

CASE_WINDOW_SECONDS = 14 * 24 * 60 * 60
SELLER_RESPONSE_SECONDS = 48 * 60 * 60
ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024
ATTACHMENT_MAX_COUNT = 5
ATTACHMENT_CASE_QUOTA = 25 * 1024 * 1024
CASE_IMAGE_JOBS = BoundedSemaphore(2)
Image.MAX_IMAGE_PIXELS = 16_000_000


def _case_row(c, case_id):
    return c.execute(
        """SELECT c.*,o.number AS order_number,
                  s.name_ar AS store_name_ar,s.name_en AS store_name_en,s.slug AS store_slug,
                  buyer.name AS buyer_name,seller.name AS seller_name
             FROM order_cases c
             JOIN seller_orders so ON so.id=c.seller_order_id
             JOIN orders o ON o.id=so.order_id
             JOIN stores s ON s.id=so.store_id
             JOIN users buyer ON buyer.id=c.buyer_id
             JOIN users seller ON seller.id=c.seller_id
            WHERE c.id=?""",
        (case_id,),
    ).fetchone()


def _case_payload(c, row, user, include_private=False):
    events = c.execute(
        """SELECT id,actor_role,event_type,body,created_at
             FROM order_case_events WHERE case_id=? ORDER BY id LIMIT 500""",
        (row["id"],),
    ).fetchall()
    result = {
        key: row[key] for key in (
            "id","seller_order_id","order_number","store_name_ar","store_name_en","store_slug",
            "reason","description","status","opened_at","response_due_at","updated_at",
            "closed_at","resolution_note"
        )
    }
    result["viewer_role"] = (
        "admin" if user["role"] == "admin" else
        "buyer" if user["id"] == row["buyer_id"] else "seller"
    )
    result["events"] = [dict(event) for event in events]
    attachments = c.execute(
        """SELECT id,uploader_role,content_type,bytes,created_at
             FROM order_case_attachments WHERE case_id=? ORDER BY id""",
        (row["id"],),
    ).fetchall()
    attachment_prefix = (
        f"/api/admin/cases/{row['id']}/attachments/"
        if user["role"] == "admin"
        else f"/api/cases/{row['id']}/attachments/"
    )
    result["attachments"] = [
        {
            "id": item["id"],
            "uploader_role": item["uploader_role"],
            "content_type": item["content_type"],
            "bytes": item["bytes"],
            "created_at": item["created_at"],
            "download_url": attachment_prefix + str(item["id"]),
        }
        for item in attachments
    ]
    result["can_upload_attachment"] = (
        user["role"] != "admin" and row["status"] in ("open", "seller_responded")
    )
    if include_private:
        result.update({"buyer_name": row["buyer_name"], "seller_name": row["seller_name"]})
    return result


def _participant_case(c, case_id, user):
    row = _case_row(c, case_id)
    if not row or user["id"] not in (row["buyer_id"], row["seller_id"]):
        fail("not_found", 404)
    return row


def _case_attachment_directory(settings, create=False):
    root = settings.data_dir.resolve()
    folder = root / "case-attachments"
    if folder.is_symlink():
        fail("not_found", 404)
    if create:
        folder.mkdir(mode=0o700, exist_ok=True)
        if folder.is_symlink():
            fail("not_found", 404)
        folder.chmod(0o700)
    if not folder.is_dir() or folder.resolve().parent != root:
        fail("not_found", 404)
    return folder


def _case_attachment_path(settings, storage_key):
    if not re.fullmatch(r"[0-9a-f]{64}", storage_key):
        fail("not_found", 404)
    folder = _case_attachment_directory(settings)
    path = folder / f"{storage_key}.webp"
    if path.is_symlink() or not path.is_file() or path.resolve().parent != folder.resolve():
        fail("not_found", 404)
    return path


def _normalize_case_image(data):
    if len(data) > ATTACHMENT_MAX_BYTES:
        fail("media_size", 413)
    try:
        with CASE_IMAGE_JOBS, warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(BytesIO(data))
            if image.format not in ("JPEG", "PNG", "WEBP") or getattr(image, "is_animated", False):
                fail("media_type", 415)
            if image.width <= 0 or image.height <= 0 or image.width * image.height > 16_000_000:
                fail("media_size", 413)
            image.load()
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            normalized.thumbnail((2400, 2400))
            output = BytesIO()
            normalized.save(output, format="WEBP", quality=82, method=4)
            result = output.getvalue()
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        fail("media_size", 413)
    except (UnidentifiedImageError, OSError, ValueError):
        fail("media_type", 415)
    if not result or len(result) > ATTACHMENT_MAX_BYTES:
        fail("media_size", 413)
    return result


@router.get("/cases")
def list_cases(request: Request, user=Depends(verified_user)):
    with request.app.state.db.read() as c:
        rows = c.execute(
            "SELECT id FROM order_cases WHERE buyer_id=? OR seller_id=? ORDER BY updated_at DESC,id DESC LIMIT 100",
            (user["id"], user["id"]),
        ).fetchall()
        result = []
        for row in rows:
            case = _case_row(c, row["id"])
            result.append(_case_payload(c, case, user))
    return {"cases": result}


@router.post("/cases", status_code=201)
def open_case(body: schemas.CaseCreate, request: Request, user=Depends(verified_user)):
    throttle(request, "case_open", limit=5, window=3600, identifier=str(user["id"]))
    with request.app.state.db.transaction() as c:
        shipment = c.execute(
            """SELECT so.id AS seller_order_id,so.status,o.buyer_id,
                      s.owner_id AS seller_id,
                      (SELECT e.created_at FROM order_events e
                        WHERE e.seller_order_id=so.id
                          AND e.to_status=CASE so.status WHEN 'delivered' THEN 'delivered' ELSE 'shipped' END
                        ORDER BY e.id DESC LIMIT 1) AS eligibility_at
                 FROM seller_orders so
                 JOIN orders o ON o.id=so.order_id
                 JOIN stores s ON s.id=so.store_id
                WHERE so.id=?""",
            (body.seller_order_id,),
        ).fetchone()
        if not shipment or shipment["buyer_id"] != user["id"]:
            fail("not_found", 404)
        if shipment["status"] not in ("shipped", "delivered") or shipment["eligibility_at"] is None:
            fail("case_not_eligible", 409)
        if body.reason == "not_received":
            if shipment["status"] != "shipped":
                fail("invalid_input", 422)
        elif shipment["status"] != "delivered":
            fail("invalid_input", 422)
        opened = now()
        if opened < shipment["eligibility_at"] or opened - shipment["eligibility_at"] > CASE_WINDOW_SECONDS:
            fail("case_window_expired", 409)
        try:
            case_id = insert_id(
                c,
                """INSERT INTO order_cases(
                     seller_order_id,buyer_id,seller_id,reason,description,status,
                     opened_at,response_due_at,updated_at
                   ) VALUES(?,?,?,?,?,'open',?,?,?)""",
                (
                    shipment["seller_order_id"], shipment["buyer_id"], shipment["seller_id"],
                    body.reason, body.description, opened,
                    opened + SELLER_RESPONSE_SECONDS, opened,
                ),
            )
        except DatabaseIntegrityError:
            fail("case_exists", 409)
        event_id = insert_id(
            c,
            """INSERT INTO order_case_events(case_id,actor_id,actor_role,event_type,body,created_at)
               VALUES(?,?,'buyer','opened',?,?)""",
            (case_id, user["id"], body.description, opened),
        )
        c.execute(
            """INSERT INTO notifications(user_id,kind,body_ar,body_en,created_at)
               VALUES(?,'case',?,?,?)""",
            (
                shipment["seller_id"],
                "وصلتك شكوى جديدة بشأن إحدى شحنات متجرك.",
                "A buyer opened a case about one of your shipments.",
                opened,
            ),
        )
        audit(c, user["id"], "case_opened", "order_case", case_id, f"shipment={shipment['seller_order_id']};event={event_id};reason={body.reason}")
        row = _case_row(c, case_id)
        result = _case_payload(c, row, user)
    return {"case": result}


@router.get("/cases/{case_id}")
def get_case(case_id: int, request: Request, user=Depends(verified_user)):
    with request.app.state.db.read() as c:
        row = _participant_case(c, case_id, user)
        return {"case": _case_payload(c, row, user)}


@router.post("/cases/{case_id}/attachments", status_code=201)
def upload_case_attachment(
    case_id: int,
    request: Request,
    file: UploadFile = File(...),
    user=Depends(verified_user),
):
    throttle(
        request, "case_attachment", limit=12, window=3600,
        identifier=f"{user['id']}:{case_id}",
    )
    data = file.file.read(ATTACHMENT_MAX_BYTES + 1)
    image_data = _normalize_case_image(data)
    digest = hashlib.sha256(image_data).hexdigest()
    storage_key = secrets.token_hex(32)
    written_path = None
    try:
        with request.app.state.db.transaction() as c:
            row = _participant_case(c, case_id, user)
            if row["status"] in ("resolved", "rejected"):
                fail("case_closed", 409)
            quota = c.execute(
                """SELECT COUNT(*),COALESCE(SUM(bytes),0)
                     FROM order_case_attachments WHERE case_id=?""",
                (case_id,),
            ).fetchone()
            if quota[0] >= ATTACHMENT_MAX_COUNT or quota[1] + len(image_data) > ATTACHMENT_CASE_QUOTA:
                fail("media_quota", 413)
            if c.execute(
                "SELECT 1 FROM order_case_attachments WHERE case_id=? AND digest=?",
                (case_id, digest),
            ).fetchone():
                fail("invalid_input", 409)
            folder = _case_attachment_directory(request.app.state.settings, create=True)
            written_path = folder / f"{storage_key}.webp"
            fd = os.open(written_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(image_data)
            uploader_role = "buyer" if user["id"] == row["buyer_id"] else "seller"
            inserted = c.execute(
                """INSERT INTO order_case_attachments(
                     case_id,uploader_id,uploader_role,storage_key,content_type,bytes,digest,created_at
                   ) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(case_id,digest) DO NOTHING
                   RETURNING id""",
                (
                    case_id,user["id"],uploader_role,storage_key,"image/webp",
                    len(image_data),digest,now(),
                ),
            ).fetchone()
            if inserted is None:
                fail("invalid_input", 409)
            attachment_id = inserted["id"]
            recipient_id = row["seller_id"] if uploader_role == "buyer" else row["buyer_id"]
            created = now()
            c.execute(
                """INSERT INTO notifications(user_id,kind,body_ar,body_en,created_at)
                   VALUES(?,'case',?,?,?)""",
                (
                    recipient_id,
                    "أُضيفت صورة دليل إلى سجل الشكوى.",
                    "An evidence image was added to the case record.",
                    created,
                ),
            )
            audit(
                c,user["id"],"case_attachment_added","order_case",case_id,
                f"attachment={attachment_id};actor_role={uploader_role}",
            )
            updated = _case_row(c, case_id)
            result = _case_payload(c, updated, user)
    except BaseException:
        if written_path is not None:
            written_path.unlink(missing_ok=True)
        raise
    return {"case": result, "attachment_id": attachment_id}


@router.get("/cases/{case_id}/attachments/{attachment_id}")
def download_case_attachment(
    case_id: int, attachment_id: int, request: Request, user=Depends(verified_user)
):
    with request.app.state.db.read() as c:
        _participant_case(c, case_id, user)
        attachment = c.execute(
            """SELECT storage_key FROM order_case_attachments
                 WHERE id=? AND case_id=?""",
            (attachment_id, case_id),
        ).fetchone()
        if not attachment:
            fail("not_found", 404)
        path = _case_attachment_path(request.app.state.settings, attachment["storage_key"])
    return FileResponse(
        path, media_type="image/webp", filename=f"case-evidence-{attachment_id}.webp",
        headers={"Cache-Control":"private, no-store, max-age=0"},
    )


@router.post("/cases/{case_id}/messages", status_code=201)
def add_case_message(case_id: int, body: schemas.CaseMessage, request: Request,
                     user=Depends(verified_user)):
    throttle(
        request, "case_message", limit=20, window=3600,
        identifier=f"{user['id']}:{case_id}",
    )
    with request.app.state.db.transaction() as c:
        row = _participant_case(c, case_id, user)
        if row["status"] in ("resolved", "rejected"):
            fail("case_closed", 409)
        is_buyer = user["id"] == row["buyer_id"]
        actor_role = "buyer" if is_buyer else "seller"
        event_type = "buyer_message" if is_buyer else "seller_response"
        created = now()
        event_id = insert_id(
            c,
            """INSERT INTO order_case_events(case_id,actor_id,actor_role,event_type,body,created_at)
               VALUES(?,?,?,?,?,?)""",
            (case_id, user["id"], actor_role, event_type, body.body, created),
        )
        next_status = "open" if is_buyer else "seller_responded"
        c.execute(
            "UPDATE order_cases SET status=?,updated_at=? WHERE id=?",
            (next_status, created, case_id),
        )
        recipient_id = row["seller_id"] if is_buyer else row["buyer_id"]
        c.execute(
            """INSERT INTO notifications(user_id,kind,body_ar,body_en,created_at)
               VALUES(?,'case',?,?,?)""",
            (
                recipient_id,
                "لديك رد جديد بشأن شكوى شحنة.",
                "You have a new reply about a shipment case.",
                created,
            ),
        )
        audit(c, user["id"], "case_message", "order_case", case_id, f"event={event_id};actor_role={actor_role}")
        updated = _case_row(c, case_id)
        result = _case_payload(c, updated, user)
    return {"case": result}


@admin_router.get("/cases/{case_id}/attachments/{attachment_id}")
def admin_download_case_attachment(
    case_id: int, attachment_id: int, request: Request, user=Depends(admin_user)
):
    with request.app.state.db.read() as c:
        if not _case_row(c, case_id):
            fail("not_found", 404)
        attachment = c.execute(
            """SELECT storage_key FROM order_case_attachments
                 WHERE id=? AND case_id=?""",
            (attachment_id, case_id),
        ).fetchone()
        if not attachment:
            fail("not_found", 404)
        path = _case_attachment_path(request.app.state.settings, attachment["storage_key"])
    return FileResponse(
        path, media_type="image/webp", filename=f"case-evidence-{attachment_id}.webp",
        headers={"Cache-Control":"private, no-store, max-age=0"},
    )


@admin_router.get("/cases")
def admin_list_cases(
    request: Request,
    status: Literal["open","seller_responded","resolved","rejected"] | None = None,
    user=Depends(admin_user),
):
    with request.app.state.db.read() as c:
        if status is None:
            rows = c.execute("SELECT id FROM order_cases ORDER BY updated_at DESC,id DESC LIMIT 200").fetchall()
        else:
            rows = c.execute("SELECT id FROM order_cases WHERE status=? ORDER BY updated_at DESC,id DESC LIMIT 200",(status,)).fetchall()
        result = []
        for row in rows:
            case = _case_row(c, row["id"])
            result.append(_case_payload(c, case, user, include_private=True))
    return {"cases": result}


@admin_router.get("/cases/{case_id}")
def admin_get_case(case_id: int, request: Request, user=Depends(admin_user)):
    with request.app.state.db.read() as c:
        row = _case_row(c, case_id)
        if not row:
            fail("not_found", 404)
        return {"case": _case_payload(c, row, user, include_private=True)}


@admin_router.post("/cases/{case_id}/decision")
def admin_decide_case(case_id: int, body: schemas.CaseDecision, request: Request,
                      user=Depends(recent_admin)):
    with request.app.state.db.transaction() as c:
        row = _case_row(c, case_id)
        if not row:
            fail("not_found", 404)
        if row["status"] in ("resolved", "rejected"):
            fail("case_closed", 409)
        created = now()
        event_type = "admin_resolved" if body.status == "resolved" else "admin_rejected"
        event_id = insert_id(
            c,
            """INSERT INTO order_case_events(case_id,actor_id,actor_role,event_type,body,created_at)
               VALUES(?,?,'admin',?,?,?)""",
            (case_id, user["id"], event_type, body.note, created),
        )
        changed = c.execute(
            """UPDATE order_cases SET status=?,closed_at=?,closed_by=?,
                      resolution_note=?,updated_at=?
                WHERE id=? AND status IN ('open','seller_responded')""",
            (body.status, created, user["id"], body.note, created, case_id),
        )
        if changed.rowcount != 1:
            fail("case_closed", 409)
        audit(c, user["id"], "case_"+body.status, "order_case", case_id, f"event={event_id}")
        for recipient_id in {row["buyer_id"], row["seller_id"]}:
            c.execute(
                """INSERT INTO notifications(user_id,kind,body_ar,body_en,created_at)
                   VALUES(?,'case',?,?,?)""",
                (
                    recipient_id,
                    "اكتملت مراجعة الشكوى. راجع سجلها في حسابك.",
                    "The case review is complete. Check its history in your account.",
                    created,
                ),
            )
        updated = _case_row(c, case_id)
        result = _case_payload(c, updated, user, include_private=True)
    return {"case": result}
