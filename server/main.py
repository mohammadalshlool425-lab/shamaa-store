"""
🌟 منصة الشامل الذكية — خادم API
=================================
متجر وإعلانات مبوبة أردنية مع مقايضة + تسعير ذكي + نموذج ربح فعلي.

التشغيل:
    python -m server.main            # يخدم على 0.0.0.0:8000
أو:
    uvicorn server.main:app --host 0.0.0.0 --port 8000

مصادر الدخل المفعّلة:
    1. ⭐ تثبيت الإعلان   — 3 دنانير / أسبوع (تجديد 2 دينار)
    2. 🛡️ وسم بائع موثّق  — 10 دنانير دفعة واحدة
    3. 🤖 حزم التسعير الذكي — 1.5 / 5 / 15 دينار
"""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
import time
import urllib.parse
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

import bcrypt
from fastapi import (Cookie, Depends, FastAPI, File, HTTPException, Request,
                    UploadFile)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                           RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import catalog, notify, seed, share
from .database import get_conn, init_db, now
from .pricing import CATEGORY_BASE, CITY_FACTOR, estimate_price

# ─────────────────────────────────────────────────────────────
# الإعدادات
# ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "data" / "uploads"   # صور الإعلانات — داخل data/ المُتجاهلة في Git
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SESSION_COOKIE = "shamaa_session"
DAY = 86400.0
JORDAN_DIALING = "962"

app = FastAPI(title="منصة الشامل الذكية", version="2.0.0", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

init_db()


# ─────────────────────────────────────────────────────────────
# الأمان: تجزئة كلمات المرور + الجلسات
# ─────────────────────────────────────────────────────────────
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn = get_conn()
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
        (token, user_id, now()),
    )
    conn.commit()
    return token


def current_user(session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)) -> Optional[dict]:
    """يعيد بيانات المستخدم الحالي أو None (بدون استثناء — بعض المسارات اختيارية)."""
    if not session:
        return None
    conn = get_conn()
    row = conn.execute(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ?""",
        (session,),
    ).fetchone()
    return dict(row) if row else None


def require_user(user=Depends(current_user)) -> dict:
    if not user:
        raise HTTPException(401, "يجب تسجيل الدخول أولاً لإتمام هذه العملية")
    return user


# ─────────────────────────────────────────────────────────────
# تحديد المعدل (Rate Limiting) — حماية بسيطة من الإساءة
# ─────────────────────────────────────────────────────────────
_buckets: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, max_calls: int, window: float = 60.0) -> None:
    q = _buckets[key]
    t = now()
    while q and t - q[0] > window:
        q.popleft()
    if len(q) >= max_calls:
        raise HTTPException(429, "عدد محاولات كبير خلال وقت قصير — انتظر دقيقة وأعد المحاولة")
    q.append(t)


# ─────────────────────────────────────────────────────────────
# أدوات مساعدة
# ─────────────────────────────────────────────────────────────
def normalize_phone(raw: str) -> str:
    """يحوّل الرقم إلى الصيغة الأردنية الموحدة 07XXXXXXXX."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("00" + JORDAN_DIALING):
        digits = "0" + digits[2 + len(JORDAN_DIALING):]
    elif digits.startswith(JORDAN_DIALING) and not digits.startswith("0"):
        digits = "0" + digits[len(JORDAN_DIALING):]
    if len(digits) == 9 and digits.startswith("7"):
        digits = "0" + digits
    if not re.fullmatch(r"07[4-9]\d{7}", digits):
        raise HTTPException(422, "رقم الهاتف غير صحيح — أدخل رقم هاتف أردني مثل 0791234567")
    return digits


def wa_link(phone: str, title: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0"):
        digits = JORDAN_DIALING + digits[1:]
    msg = f"السلام عليكم، أنا مهتم بإعلانك على منصة الشامل: «{title}». هل ما زال متوفراً؟"
    return f"https://wa.me/{digits}?text={urllib.parse.quote(msg)}"


def clean_featured(conn, t: float) -> None:
    """
    يُنزل الإعلانات التي انتهت مدة تثبيتها من المرتبة المميزة.

    ⚠️ يلتزم (commit) بنفسه دائماً: في وضع sqlite الافتراضي يفتح أي UPDATE
    معاملة كتابة حتى لو لم يطال صفوفاً، ومن بين مناداته مسارات قراءة لا
    تلتزم إطلاقاً — فكان قفل الكتابة يبقى مفتوحاً على اتصال الـ thread
    حتى تصادف معاملة أخرى على نفس الاتصال، فيعلق أي كاتب آخر 30 ثانية
    («database is locked»). الالتزام هنا يغلق المعاملة فوراً ويسدّ العلق.
    """
    conn.execute(
        "UPDATE listings SET is_featured = 0 WHERE is_featured = 1 AND featured_until < ?",
        (t,),
    )
    conn.commit()
    # تذكيرات التجديد: الإيراد المتكرر يموت بصمت إن نسي البائع، فالكنس هنا
    notify.sweep_expiring(conn, t)


def serialize_listing(row: sqlite3.Row, t: float, viewer: Optional[dict] = None) -> dict:
    """
    يجهّز الإعلان للواجهة.
    🔒 خصوصية: رابط واتساب يُبنى دائماً، لكن الرقم الخام يظهر فقط لصاحب
    الإعلان أو لمدير المنصة — الزوار يفتحون واتساب دون رؤية الرقم.
    """
    d = dict(row)
    featured = bool(d.get("is_featured")) and (d.get("featured_until") or 0) > t
    d["is_featured"] = 1 if featured else 0
    d["accepts_barter"] = bool(d.get("accepts_barter"))
    d["featured_hours_left"] = (
        max(0, round((d["featured_until"] - t) / 3600)) if featured else 0
    )
    d["image"] = d.pop("image_path", "") or ""
    d["whatsapp_url"] = wa_link(d.get("phone", ""), d.get("title", ""))
    d["age_label"] = _age_label(t - d["created_at"])
    d["seller_id"] = d.get("user_id")
    d.pop("password_hash", None)
    is_owner = bool(viewer) and viewer.get("id") == d.get("user_id")
    if not is_owner and not (viewer or {}).get("is_admin"):
        d.pop("phone", None)
    return d


def _age_label(seconds: float) -> str:
    if seconds < 3600:
        return f"قبل {max(1, int(seconds // 60))} دقيقة"
    if seconds < DAY:
        return f"قبل {int(seconds // 3600)} ساعة"
    days = int(seconds // DAY)
    return "اليوم" if days == 0 else (f"أمس" if days == 1 else f"قبل {days} أيام")


def public_user(u: dict) -> dict:
    return {
        "id": u["id"],
        "name": u["name"],
        "phone": u["phone"],
        "city": u["city"],
        "is_verified": bool(u["is_verified"]),
        "pricing_credits": u["pricing_credits"],
        "extra_listing_slots": u.get("extra_listing_slots", 0) if isinstance(u, dict) else u["extra_listing_slots"],
        "is_admin": bool(u["is_admin"]),
    }


# ─────────────────────────────────────────────────────────────
# نماذج الطلبات
# ─────────────────────────────────────────────────────────────
class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    phone: str = Field(min_length=8, max_length=20)
    city: str = Field(default="عمّان", max_length=40)
    password: str = Field(min_length=6, max_length=100)


class LoginIn(BaseModel):
    phone: str
    password: str


class ListingIn(BaseModel):
    title: str = Field(min_length=4, max_length=120)
    description: str = Field(default="", max_length=3000)
    price: float = Field(ge=0, le=5_000_000)
    category: str
    city: str = Field(default="عمّان", max_length=40)
    accepts_barter: bool = False
    featured: bool = False


class PricingIn(BaseModel):
    item_name: str = Field(min_length=2, max_length=200)
    category: str = "أخرى"
    condition: int = Field(default=8, ge=1, le=10)
    city: str = "عمّان"
    accepts_barter: bool = False


class FeatureIn(BaseModel):
    listing_id: int
    renew: bool = False


class PackageIn(BaseModel):
    package_id: str


# ─────────────────────────────────────────────────────────────
# مسارات: البيانات المرجعية
# ─────────────────────────────────────────────────────────────
@app.get("/api/bootstrap")
def bootstrap(user=Depends(current_user)):
    """كل ما تحتاجه الواجهة عند الإقلاع: أقسام، مدن، أسعار، ميزات."""
    return {
        "user": public_user(user) if user else None,
        "categories": list(CATEGORY_BASE.keys()),
        "cities": catalog.CITIES_DISPLAY,
        "currency": catalog.CURRENCY,
        "pricing": {
            "feature_days": catalog.FEATURE_DAYS,
            "feature_price": catalog.FEATURE_PRICE_JD,
            "renew_price": catalog.FEATURE_RENEW_PRICE_JD,
            "verify_price": catalog.VERIFY_PRICE_JD,
            "free_daily": catalog.FREE_DAILY_PRICING,
            "free_active_listings": catalog.FREE_ACTIVE_LISTINGS,
            "extra_listing_price": catalog.EXTRA_LISTING_PRICE_JD,
            "extra_listing_pack": catalog.EXTRA_LISTING_SLOTS_PACK,
            "rate_limits": {
                "listings": catalog.LISTING_RATE_LIMIT,
                "pricing": catalog.PRICING_RATE_LIMIT,
                "auth": catalog.AUTH_RATE_LIMIT,
            },
            "packages": catalog.PRICING_PACKAGES,
        },
        "safety_tips": catalog.SAFETY_TIPS,
        "demo_account": {"phone": catalog.DEMO_PHONE, "password": catalog.DEMO_PASSWORD},
    }


# ─────────────────────────────────────────────────────────────
# مسارات: الحساب
# ─────────────────────────────────────────────────────────────
@app.post("/api/register")
def register(body: RegisterIn, request: Request):
    rate_limit(f"reg:{request.client.host}", catalog.AUTH_RATE_LIMIT, 300)
    phone = normalize_phone(body.phone)
    conn = get_conn()
    if conn.execute("SELECT 1 FROM users WHERE phone = ?", (phone,)).fetchone():
        raise HTTPException(409, "هذا الرقم مسجّل مسبقاً — جرّب تسجيل الدخول")
    try:
        cur = conn.execute(
            """INSERT INTO users (name, phone, city, password_hash, pricing_credits, created_at)
               VALUES (?,?,?,?,?,?)""",
            (body.name.strip(), phone, body.city.strip() or "عمّان",
             hash_password(body.password), catalog.FREE_DAILY_PRICING, now()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "هذا الرقم مسجّل مسبقاً")
    token = create_session(cur.lastrowid)
    u = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    resp = JSONResponse({"ok": True, "user": public_user(dict(u))})
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                    max_age=30 * 24 * 3600)
    return resp


@app.post("/api/login")
def login(body: LoginIn, request: Request):
    rate_limit(f"login:{request.client.host}", catalog.AUTH_RATE_LIMIT, 300)
    phone = normalize_phone(body.phone)
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    if not row or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "رقم الهاتف أو كلمة المرور غير صحيحة")
    token = create_session(row["id"])
    resp = JSONResponse({"ok": True, "user": public_user(dict(row))})
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                    max_age=30 * 24 * 3600)
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/api/me")
def me(user=Depends(current_user)):
    if not user:
        return {"user": None}
    conn = get_conn()
    t = now()
    stats = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN is_featured=1 AND featured_until>? THEN 1 ELSE 0 END) AS featured,
                  SUM(views) AS views
           FROM listings WHERE user_id=? AND is_active=1""",
        (t, user["id"]),
    ).fetchone()
    revenue = conn.execute(
        "SELECT COALESCE(SUM(amount_jd),0) AS total FROM payments WHERE user_id=? AND status='paid'",
        (user["id"],),
    ).fetchone()["total"]
    return {
        "user": public_user(user),
        "stats": {
            "listings": stats["total"] or 0,
            "featured": stats["featured"] or 0,
            "views": stats["views"] or 0,
            "spent_jd": round(revenue, 2),
        },
    }


# ─────────────────────────────────────────────────────────────
# مسارات: الإعلانات (قلب المنصة)
# ─────────────────────────────────────────────────────────────
@app.get("/api/notifications")
def my_notifications(user=Depends(require_user)):
    """صندوق إشعارات البائع: تذكيرات التجديد وتأكيداتها (واتساب محاكاة)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, kind, body, status, created_at FROM notifications"
        " WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (user["id"],),
    ).fetchall()
    return {"notifications": [dict(r) for r in rows]}


@app.get("/api/listings")
def list_listings(
    q: str = "",
    category: str = "",
    city: str = "",
    barter: int = -1,
    sort: str = "recent",
    limit: int = 60,
    user=Depends(current_user),
):
    conn = get_conn()
    t = now()
    clean_featured(conn, t)

    where, params = ["l.is_active = 1"], []
    if q.strip():
        where.append("(l.title LIKE ? OR l.description LIKE ?)")
        params += [f"%{q.strip()}%", f"%{q.strip()}%"]
    if category and category != "الكل":
        where.append("l.category = ?")
        params.append(category)
    if city and city != "كل الأردن":
        where.append("l.city = ?")
        params.append(city)
    if barter == 1:
        where.append("l.accepts_barter = 1")

    order = {
        "recent": "l.is_featured DESC, l.created_at DESC",
        "price_asc": "l.is_featured DESC, l.price ASC",
        "price_desc": "l.is_featured DESC, l.price DESC",
        "popular": "l.is_featured DESC, l.views DESC",
    }.get(sort, "l.is_featured DESC, l.created_at DESC")

    rows = conn.execute(
        f"""SELECT l.*, u.name AS seller_name, u.phone, u.is_verified AS seller_verified
            FROM listings l JOIN users u ON u.id = l.user_id
            WHERE {' AND '.join(where)}
            ORDER BY {order}
            LIMIT ?""",
        params + [min(limit, 200)],
    ).fetchall()
    conn.commit()

    counts = conn.execute(
        """SELECT category, COUNT(*) AS c FROM listings
           WHERE is_active=1 GROUP BY category"""
    ).fetchall()
    totals = conn.execute(
        """SELECT COUNT(*) AS ads,
                  SUM(CASE WHEN accepts_barter=1 THEN 1 ELSE 0 END) AS barter,
                  COUNT(DISTINCT user_id) AS sellers FROM listings WHERE is_active=1"""
    ).fetchone()

    return {
        "listings": [serialize_listing(r, t, user) for r in rows],
        "category_counts": {r["category"]: r["c"] for r in counts},
        "totals": {
            "ads": totals["ads"] or 0,
            "barter": totals["barter"] or 0,
            "sellers": totals["sellers"] or 0,
        },
    }


@app.get("/api/listings/{listing_id}")
def get_listing(listing_id: int, user=Depends(current_user)):
    conn = get_conn()
    t = now()
    clean_featured(conn, t)
    row = conn.execute(
        """SELECT l.*, u.name AS seller_name, u.phone, u.city AS seller_city,
                  u.is_verified AS seller_verified,
                  (SELECT COUNT(*) FROM listings x WHERE x.user_id=u.id AND x.is_active=1) AS seller_ads
           FROM listings l JOIN users u ON u.id = l.user_id
           WHERE l.id = ? AND l.is_active = 1""",
        (listing_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "الإعلان غير موجود أو تم حذفه")
    conn.execute("UPDATE listings SET views = views + 1 WHERE id = ?", (listing_id,))
    conn.commit()
    d = serialize_listing(row, t, user)
    if user and (user["id"] == row["user_id"] or user.get("is_admin")):
        # 📊 عدّاد المشاركة: يرى البائع أثر نشره فيرجع ينشر أكثر
        d["hits"] = hit_counts(conn, listing_id)
    return {"listing": d}


@app.post("/api/listings")
def create_listing(body: ListingIn, user=Depends(require_user), request: Request = None):
    rate_limit(f"new:{user['id']}", catalog.LISTING_RATE_LIMIT, 300)
    conn = get_conn()
    t = now()

    if body.category not in CATEGORY_BASE:
        raise HTTPException(422, "القسم المختار غير معروف")

    # الحد المجاني = 5 إعلانات نشطة، وما فوقها يحتاج فتحات مدفوعة.
    active = conn.execute(
        "SELECT COUNT(*) AS c FROM listings WHERE user_id=? AND is_active=1",
        (user["id"],),
    ).fetchone()["c"]
    allowed = catalog.FREE_ACTIVE_LISTINGS + user.get("extra_listing_slots", 0)
    if active >= allowed:
        raise HTTPException(
            402,
            {
                "message": (f"وصلت لحدك الأقصى ({allowed} إعلانات نشطة = "
                              f"{catalog.FREE_ACTIVE_LISTINGS} مجانية + "
                              f"{user.get('extra_listing_slots', 0)} مدفوعة). "
                              f"وسّع حدك بـ {catalog.EXTRA_LISTING_SLOTS_PACK} إعلانات دائمة مقابل "
                              f"{catalog.EXTRA_LISTING_PRICE_JD * catalog.EXTRA_LISTING_SLOTS_PACK} "
                              f"{catalog.CURRENCY} — أو احذف إعلاناً قديماً."),
                "need_slots": True,
            },
        )

    # الدفع مقابل التثبيت (محاكاة — تُستبدل لاحقاً ببوابة دفع حقيقية)
    charged = 0.0
    if body.featured:
        charged = catalog.FEATURE_PRICE_JD

    featured_until = (t + catalog.FEATURE_DAYS * DAY) if body.featured else None
    cur = conn.execute(
        """INSERT INTO listings
           (user_id, title, description, price, category, city, accepts_barter,
            is_featured, featured_until, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (user["id"], body.title.strip(), body.description.strip(), body.price,
         body.category, body.city.strip() or "عمّان", int(body.accepts_barter),
         int(body.featured), featured_until, t),
    )
    listing_id = cur.lastrowid

    if charged:
        conn.execute(
            """INSERT INTO payments (user_id, kind, amount_jd, listing_id, status, method, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (user["id"], "feature", charged, listing_id, "paid", "simulated", t),
        )
    conn.commit()

    row = conn.execute(
        """SELECT l.*, u.name AS seller_name, u.phone, u.is_verified AS seller_verified
           FROM listings l JOIN users u ON u.id=l.user_id WHERE l.id=?""",
        (listing_id,),
    ).fetchone()
    return {
        "ok": True,
        "listing": serialize_listing(row, t, user),
        "charged_jd": charged,
        "message": (
            f"🎉 تم نشر إعلانك مثبّتاً في الأعلى لمدة {catalog.FEATURE_DAYS} أيام "
            f"مقابل {charged} {catalog.CURRENCY}"
            if body.featured else "🎉 تم نشر إعلانك بنجاح وأصبح مرئياً للجميع"
        ),
    }


@app.delete("/api/listings/{listing_id}")
def delete_listing(listing_id: int, user=Depends(require_user)):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM listings WHERE id=? AND is_active=1", (listing_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "الإعلان غير موجود")
    if row["user_id"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "لا تملك صلاحية حذف هذا الإعلان")
    conn.execute("UPDATE listings SET is_active=0 WHERE id=?", (listing_id,))
    conn.commit()
    return {"ok": True}


@app.post("/api/feature")
def feature_listing(body: FeatureIn, user=Depends(require_user)):
    """⭐ شراء/تجديد تثبيت إعلان — أهم مصدر دخل في المنصة."""
    conn = get_conn()
    t = now()
    row = conn.execute(
        "SELECT * FROM listings WHERE id=? AND is_active=1", (body.listing_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "الإعلان غير موجود")
    if row["user_id"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "هذا الإعلان ليس من إعلاناتك")

    already = row["is_featured"] == 1 and (row["featured_until"] or 0) > t
    price = catalog.FEATURE_RENEW_PRICE_JD if (already or body.renew) else catalog.FEATURE_PRICE_JD
    base = max(row["featured_until"] or 0, t) if already else t
    until = base + catalog.FEATURE_DAYS * DAY

    conn.execute(
        "UPDATE listings SET is_featured=1, featured_until=? WHERE id=?",
        (until, row["id"]),
    )
    conn.execute(
        """INSERT INTO payments (user_id, kind, amount_jd, listing_id, status, method, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (user["id"], "renew" if already else "feature", price, row["id"], "paid", "simulated", t),
    )
    conn.commit()
    if already:
        # تأكيد التجديد يصل واتساب — إغلاق حلقة الإيراد المتكرر
        notify.queue(
            conn, user["id"], row["id"], notify.KIND_RENEWED,
            f"✅ جدّدت تثبيت «{row['title']}» — إعلانك في الصدارة حتى "
            f"{time.strftime('%Y/%m/%d', time.localtime(until))}. "
            f"الإعلانات المثبّتة تُباع أسرع بأربعة أضعاف.",
        )
    return {
        "ok": True,
        "charged_jd": price,
        "featured_until": until,
        "message": f"⭐ تم تثبيت «{row['title']}» في أعلى النتائج حتى {time.strftime('%Y/%m/%d', time.localtime(until))} — الدفع {price} {catalog.CURRENCY}",
    }


# ─────────────────────────────────────────────────────────────
# مسارات: 🤖 التسعير الذكي (Freemium)
# ─────────────────────────────────────────────────────────────
@app.post("/api/pricing")
def pricing(body: PricingIn, request: Request, user=Depends(current_user)):
    """
    🤖 التسعير الذكي — منطق الاستهلاك:
      1) الرصيد المدفوع يُستهلك أولاً إن وُجد (لا يمسّ الحد المجاني).
      2) ثم تُحتسب الاستخدامات المجانية اليومية (للزائر بالعنوان، وللمسجّل بحسابه).
      3) عند نفاد الاثنين -> 402 مع عرض الحزم.
    """
    conn = get_conn()
    t = now()
    ip = request.client.host if request.client else "unknown"
    rate_limit(f"pricing:{user['id'] if user else ip}", catalog.PRICING_RATE_LIMIT, 300)
    credits = user["pricing_credits"] if user else 0

    uses_24h = conn.execute(
        """SELECT COUNT(*) AS c FROM pricing_uses
           WHERE created_at > ? AND (user_id = ? OR (user_id IS NULL AND visitor_ip = ?))""",
        (t - DAY, user["id"] if user else -1, ip),
    ).fetchone()["c"]

    free_left = max(0, catalog.FREE_DAILY_PRICING - uses_24h)
    if credits <= 0 and free_left <= 0:
        raise HTTPException(
            402,
            {
                "message": (
                    f"استنفدت التسعيرات المجانية ({catalog.FREE_DAILY_PRICING} يومياً)."
                    + (" اشترِ حزمة تسعير للمتابعة." if user
                       else " سجّل الدخول واحصل على رصيد مجاني، أو اشترِ حزمة تسعير.")
                ),
                "need_credits": True,
                "packages": catalog.PRICING_PACKAGES,
            },
        )

    result = estimate_price(body.item_name, body.category, body.condition,
                            body.city, body.accepts_barter)

    # الاستهلاك: الرصيد المدفوع أولاً
    used_credit = user is not None and credits > 0
    if used_credit:
        conn.execute("UPDATE users SET pricing_credits = pricing_credits - 1 WHERE id = ?",
                     (user["id"],))
        credits -= 1

    conn.execute(
        """INSERT INTO pricing_uses (user_id, visitor_ip, item_name, low_price, high_price, created_at)
           VALUES (?,?,?,?,?,?)""",
        (user["id"] if user else None, ip, body.item_name[:120],
         result["low_price"], result["high_price"], t),
    )
    conn.commit()

    result["used_credit"] = used_credit
    result["remaining_credits"] = credits
    result["remaining_free_today"] = free_left - (0 if used_credit else 1)
    return result


@app.get("/api/pricing/stats")
def pricing_stats():
    """إحصائية عامة تُظهر حجم استخدام الأداة (دليل اجتماعي)."""
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) AS c FROM pricing_uses").fetchone()["c"]
    today = conn.execute(
        "SELECT COUNT(*) AS c FROM pricing_uses WHERE created_at > ?", (now() - DAY,)
    ).fetchone()["c"]
    return {"total": total, "today": today}


# ─────────────────────────────────────────────────────────────
# مسارات: 💰 الشراء والترقية
# ─────────────────────────────────────────────────────────────
@app.post("/api/buy/package")
def buy_package(body: PackageIn, user=Depends(require_user)):
    pkg = catalog.package_by_id(body.package_id)
    if not pkg:
        raise HTTPException(404, "الحزمة غير موجودة")
    conn = get_conn()
    conn.execute(
        "UPDATE users SET pricing_credits = pricing_credits + ? WHERE id = ?",
        (pkg["credits"], user["id"]),
    )
    conn.execute(
        """INSERT INTO payments (user_id, kind, amount_jd, status, method, created_at)
           VALUES (?,?,?,?,?,?)""",
        (user["id"], "credits", pkg["price_jd"], "paid", "simulated", now()),
    )
    conn.commit()
    new_credits = conn.execute(
        "SELECT pricing_credits FROM users WHERE id=?", (user["id"],)
    ).fetchone()["pricing_credits"]
    return {
        "ok": True,
        "pricing_credits": new_credits,
        "charged_jd": pkg["price_jd"],
        "message": f"🤖 تمت إضافة {pkg['credits']} تسعيرة ذكية لرصيدك مقابل {pkg['price_jd']} {catalog.CURRENCY}",
    }


@app.post("/api/buy/extra-listing")
def buy_extra_listing(user=Depends(require_user)):
    """
    📦 شراء حزمة فتحات إعلانات — توسعة دائمة للحد الأقصى.
    الفتحات ليست رصيداً يُستهلك: كل فتحة ترفع حدّك بنشر واحد بشكل دائم،
    فلا تضيع قيمتها إذا حذفت إعلاناً (أبسط للمستخدم وأدق محاسبياً).
    """
    conn = get_conn()
    price = catalog.EXTRA_LISTING_PRICE_JD * catalog.EXTRA_LISTING_SLOTS_PACK
    conn.execute(
        "UPDATE users SET extra_listing_slots = extra_listing_slots + ? WHERE id = ?",
        (catalog.EXTRA_LISTING_SLOTS_PACK, user["id"]),
    )
    conn.execute(
        """INSERT INTO payments (user_id, kind, amount_jd, status, method, created_at)
           VALUES (?,?,?,?,?,?)""",
        (user["id"], "slots", price, "paid", "simulated", now()),
    )
    conn.commit()
    slots = conn.execute(
        "SELECT extra_listing_slots FROM users WHERE id=?", (user["id"],)
    ).fetchone()["extra_listing_slots"]
    return {
        "ok": True,
        "charged_jd": price,
        "extra_listing_slots": slots,
        "message": (f"📦 ارتفع حدّك الأقصى إلى "
                    f"{catalog.FREE_ACTIVE_LISTINGS + slots} إعلانات نشطة "
                    f"(+{catalog.EXTRA_LISTING_SLOTS_PACK} دائمة) مقابل {price} {catalog.CURRENCY}"),
    }


@app.post("/api/buy/verification")
def buy_verification(user=Depends(require_user)):
    conn = get_conn()
    if user["is_verified"]:
        raise HTTPException(409, "حسابك موثّق بالفعل 🛡️")
    conn.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user["id"],))
    conn.execute(
        """INSERT INTO payments (user_id, kind, amount_jd, status, method, created_at)
           VALUES (?,?,?,?,?,?)""",
        (user["id"], "verify", catalog.VERIFY_PRICE_JD, "paid", "simulated", now()),
    )
    conn.commit()
    return {
        "ok": True,
        "charged_jd": catalog.VERIFY_PRICE_JD,
        "message": f"🛡️ مبروك! أصبح حسابك «بائع موثّق» — الإعلانات الموثّقة تحصل على تواصل أكثر بنسبة 60%",
    }


# ─────────────────────────────────────────────────────────────
# مسارات: لوحة الإدارة (إحصاءات المنصة والأرباح)
# ─────────────────────────────────────────────────────────────
@app.get("/api/admin/stats")
def admin_stats(user=Depends(current_user)):
    if not user or not user["is_admin"]:
        raise HTTPException(403, "هذه البيانات متاحة لمدير المنصة فقط")
    conn = get_conn()
    t = now()
    revenue = conn.execute(
        """SELECT kind, COUNT(*) AS n, COALESCE(SUM(amount_jd),0) AS total
           FROM payments WHERE status='paid' GROUP BY kind"""
    ).fetchall()
    total_rev = conn.execute(
        "SELECT COALESCE(SUM(amount_jd),0) AS t FROM payments WHERE status='paid'"
    ).fetchone()["t"]
    top = conn.execute(
        """SELECT u.name, COUNT(l.id) AS ads, u.is_verified
           FROM users u LEFT JOIN listings l ON l.user_id=u.id AND l.is_active=1
           GROUP BY u.id ORDER BY ads DESC LIMIT 8"""
    ).fetchall()
    return {
        "revenue_total_jd": round(total_rev, 2),
        "revenue_by_kind": [dict(r) for r in revenue],
        "users": conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"],
        "listings": conn.execute(
            "SELECT COUNT(*) AS c FROM listings WHERE is_active=1").fetchone()["c"],
        "featured_active": conn.execute(
            "SELECT COUNT(*) AS c FROM listings WHERE is_featured=1 AND featured_until>?",
            (t,)).fetchone()["c"],
        "pricing_calls": conn.execute(
            "SELECT COUNT(*) AS c FROM pricing_uses").fetchone()["c"],
        "share_scans": int(conn.execute(
            "SELECT COUNT(*) AS c FROM link_hits WHERE kind='scan'").fetchone()["c"]),
        "share_pages": int(conn.execute(
            "SELECT COUNT(*) AS c FROM link_hits WHERE kind='page'").fetchone()["c"]),
        "top_sellers": [dict(r) for r in top],
    }


# ─────────────────────────────────────────────────────────────
# 📷 صور الإعلانات
# ─────────────────────────────────────────────────────────────
#: الحد الأقصى لحجم الصورة (2 ميغابايت تكفي صورة سلعة مضغوطة).
MAX_IMAGE_BYTES = 2 * 1024 * 1024

#: البصمات السحرية المقبولة — نفحص البايتات لا امتداد الملف ولا ترويسة
#: Content-Type، لأن كليهما ينتحل بسهولة فيطلب ملفاً خبيثاً بامتداد صورة.
IMAGE_TYPES = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
)


def _detect_image(data: bytes):
    """يعيد (الامتداد، نوع MIME) إن كانت البايتات صورة مدعومة، وإلا None."""
    for magic, ext, mime in IMAGE_TYPES:
        if data.startswith(magic):
            return ext, mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


@app.post("/api/listings/{listing_id}/image")
async def upload_listing_image(listing_id: int,
                               file: UploadFile = File(...),
                               user=Depends(require_user),
                               request: Request = None):
    """
    يرفع صورة إعلان (PNG/JPEG/WebP حتى 2 ميغابايت).

    الأمان: الملكية أولاً (403 لغير المالك)، ثم فحص البصمة السحرية (415)،
    ثم الحجم (413)، والاسم النهائي عشوائي بالكامل فلا يوجد مسار يدخله
    المستخدم إطلاقاً — وهذا يسدّ انتحال المسار (path traversal) من جذوره.
    """
    rate_limit(f"upload:{user['id']}", 12, 300)
    conn = get_conn()
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if row is None or not int(row["is_active"]):
        raise HTTPException(404, "الإعلان غير موجود أو تم حذفه")
    if row["user_id"] != user["id"] and not user.get("is_admin"):
        raise HTTPException(403, "لا يمكنك رفع صورة لإعلان لا تملكه")

    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "حجم الصورة كبير — الحد الأقصى 2 ميغابايت")
    detected = _detect_image(data)
    if detected is None:
        raise HTTPException(415, "نوع ملف غير مدعوم — يُقبل PNG أو JPEG أو WebP فقط")
    ext, _mime = detected

    old_path = row["image_path"] or ""
    name = f"{listing_id}-{secrets.token_hex(8)}.{ext}"
    (UPLOAD_DIR / name).write_bytes(data)
    if old_path:
        (UPLOAD_DIR / Path(old_path).name).unlink(missing_ok=True)

    conn.execute("UPDATE listings SET image_path = ? WHERE id = ?",
                 (f"/uploads/{name}", listing_id))
    conn.commit()
    return {"ok": True, "image": f"/uploads/{name}",
            "message": "✅ رُفعت الصورة — صارت بطاقة مشاركتك تعرضها"}


@app.delete("/api/listings/{listing_id}/image")
def delete_listing_image(listing_id: int, user=Depends(require_user)):
    """يزيل صورة الإعلان ويعيده إلى المصغّر الافتراضي."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if row is None or not int(row["is_active"]):
        raise HTTPException(404, "الإعلان غير موجود أو تم حذفه")
    if row["user_id"] != user["id"] and not user.get("is_admin"):
        raise HTTPException(403, "لا يمكنك تعديل صورة إعلان لا تملكه")
    old_path = row["image_path"] or ""
    if old_path:
        (UPLOAD_DIR / Path(old_path).name).unlink(missing_ok=True)
    conn.execute("UPDATE listings SET image_path = '' WHERE id = ?", (listing_id,))
    conn.commit()
    return {"ok": True, "image": "", "message": "أُزيلت الصورة"}


# ─────────────────────────────────────────────────────────────
# المشاركة والانتشار — صفحة إعلان مُصيَّرة على الخادم + بطاقة OG + QR
# ─────────────────────────────────────────────────────────────
#: بصمات الزواحف التي تجلب الرابط لبناء معاينة المشاركة — زياراتها ليست
#: بشراً فلا تُحتسب على البائع، وإلا لصارت معاينة فيسبوك الواحدة «مشاهدة».
CRAWLER_UA = ("facebookexternalhit", "whatsapp", "twitterbot", "telegrambot",
              "googlebot", "slackbot", "discordbot", "linkedinbot", "applebot",
              "bingbot", "embedly", "showyoubot", "quora")


def _is_crawler(request: Request) -> bool:
    ua = (request.headers.get("user-agent") or "").lower()
    return any(k in ua for k in CRAWLER_UA)


def record_hit(conn: sqlite3.Connection, listing_id: int, kind: str) -> None:
    conn.execute(
        "INSERT INTO link_hits(listing_id, kind, created_at) VALUES (?, ?, ?)",
        (listing_id, kind, now()),
    )
    conn.commit()


def hit_counts(conn: sqlite3.Connection, listing_id: int) -> dict:
    row = conn.execute(
        """SELECT SUM(kind = 'scan') AS scans, SUM(kind = 'page') AS pages
           FROM link_hits WHERE listing_id = ?""", (listing_id,),
    ).fetchone()
    return {"scans": int(row["scans"] or 0), "pages": int(row["pages"] or 0)}


def _listing_for_share(conn: sqlite3.Connection, listing_id: int,
                       t: float) -> Optional[dict]:
    """
    يجلب إعلاناً جاهزاً للمشاركة، أو None إن كان محذوفاً/موقوفاً.

    🔒 يستخدم `serialize_listing` بلا مُشاهِد: رقم الهاتف لا يخرج أبداً
    من هذه البوابة — صفحة المشاركة والبطاقة ورمز QR كلها تمر من هنا.
    """
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if row is None or not int(row["is_active"]):
        return None
    return serialize_listing(row, t)


@app.get("/listing/{listing_id}", response_class=HTMLResponse)
def listing_page(listing_id: int, request: Request):
    """
    صفحة إعلان كاملة تُصيَّر على الخادم.

    الزواحف (فيسبوك/واتساب/X/جوجل) لا تنفّذ JavaScript، لذا هذه الصفحة تحمل
    وسوم Open Graph وبطاقة الصورة والبيانات المنظَّمة مباشرة في الـ HTML —
    وهذا ما يحوّل أي رابط مشترك إلى بطاقة غنية بالسعر والصورة.
    """
    # ملاحظة: اتصال get_conn() حيّ لكل thread ولا يُغلق يدوياً أبداً —
    # إغلاقه هنا يكسر كل الطلبات التالية على نفس الـ thread.
    conn = get_conn()
    t = now()
    clean_featured(conn, t)
    d = _listing_for_share(conn, listing_id, t)
    if d is None:
        return HTMLResponse(share.render_missing_page(listing_id, request),
                            status_code=404)
    if not _is_crawler(request):
        record_hit(conn, listing_id, "page")
    seller = conn.execute(
        "SELECT name, is_verified FROM users WHERE id = ?",
        (d.get("user_id"),),
    ).fetchone()
    return HTMLResponse(
        share.render_listing_page(d, request, listing_id,
                                  dict(seller) if seller else None)
    )


@app.get("/l/{listing_id}")
def listing_short(listing_id: int):
    """
    الرابط القصير — ما يُرمَّز داخل رمز QR وما يُلصق في رسائل واتساب.

    302 لا 301: إن حُذف الإعلان فالصفحة القانونية تعرض البديل المناسب،
    ولا نريد للمتصفحات أن تخزّن تحويلاً دائماً نحو رابط ميت.
    """
    conn = get_conn()
    if conn.execute("SELECT 1 FROM listings WHERE id = ? AND is_active = 1",
                    (listing_id,)).fetchone():
        # كل فتحة للرابط القصير = نقرة من واتساب أو مسح QR: هذا ذهب المشاركة
        record_hit(conn, listing_id, "scan")
    return RedirectResponse(url=f"/listing/{listing_id}", status_code=302)


@app.get("/listing/{listing_id}/card.png")
def listing_card(listing_id: int, request: Request):
    """
    بطاقة المشاركة 1200×630 (صورة Open Graph).

    تُولَّد برمز PNG مكتوب داخلياً (`server/png.py`) بلا أي حزمة رسوميات،
    ومخزَّنة مؤقتاً بمفتاح محتوى — فتعديل السعر يُبطل المخزون تلقائياً.
    """
    conn = get_conn()
    d = _listing_for_share(conn, listing_id, now())
    if d is None:
        raise HTTPException(404, "الإعلان غير موجود")
    png = share.build_card(d, share.short_url(request, listing_id))
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/api/qr/{listing_id}.svg")
def listing_qr(listing_id: int, request: Request):
    """رمز QR متجهي للإعلان — للطباعة على ملصق أو العرض داخل الصفحة."""
    conn = get_conn()
    d = _listing_for_share(conn, listing_id, now())
    if d is None:
        raise HTTPException(404, "الإعلان غير موجود")
    svg = share.qr_svg(share.short_url(request, listing_id))
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/listings/{listing_id}/share")
def listing_share_links(listing_id: int, request: Request):
    """
    حزمة المشاركة للواجهة: روابط واتساب/فيسبوك/X/تيليجرام + نص الرسالة.

    تُبنى على الخادم لأن النطاق العام الصحيح (خلف وكيل المعاينة أو الإنتاج)
    لا تعرفه الواجهة، ولأن نص الرسالة يجب أن يطابق ما تفحصه الاختبارات.
    """
    conn = get_conn()
    d = _listing_for_share(conn, listing_id, now())
    if d is None:
        raise HTTPException(404, "الإعلان غير موجود")
    return share.share_links(d, request, listing_id)


# ─────────────────────────────────────────────────────────────
# الصفحات
# ─────────────────────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/healthz")
def health():
    return {"ok": True, "time": now()}


@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    """توحيد شكل الأخطاء حتى تفهمها الواجهة دائماً."""
    detail = exc.detail
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(status_code=exc.status_code,
                        content={"message": str(detail), "need_credits": exc.status_code == 402})


# ─────────────────────────────────────────────────────────────
# نقطة التشغيل
# ─────────────────────────────────────────────────────────────
seed.seed_if_empty(hash_password)


def main():
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("server.main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
