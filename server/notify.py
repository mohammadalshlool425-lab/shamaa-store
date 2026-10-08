"""
🔔 الإشعارات — تذكيرات واتساب محاكاة تحمي الإيراد المتكرر
=========================================================
الإيراد المتكرر (تجديد التثبيت 2 د.أ) يموت بصمت إن نسي البائع أن ثبتيته
تنتهي. لذلك كنسٌ دوري يبحث عن التثبيتات التي تنتهي خلال 24 ساعة ويضع
تذكيراً واتساب لصاحبها — مرة واحدة لكل دورة تثبيت، لا إزعاج مكرر.

النظام حالياً **محاكاة**: الرسائل تُحفظ في جدول `notifications` بحالة
`simulated` لأن ربط WhatsApp Business API الحقيقي يحتاج اعتماداً من ميتا
ورقماً مسجلاً. نقطة الاستبدال الوحيدة هي `deliver()` أدناه.
"""
from __future__ import annotations

import sqlite3

from . import catalog
from .database import now

#: نافذة التذكير: تثبيت ينتهي خلال هذه المدة يستحق تذكيراً واحداً.
REMINDER_WINDOW = 24 * 3600

KIND_REMINDER = "renew_reminder"
KIND_RENEWED = "renew_done"
KIND_WELCOME = "welcome"


def queue(conn: sqlite3.Connection, user_id: int, listing_id, kind: str,
          body: str) -> int:
    """يسجّل إشعاراً (ويحاكي إرساله واتساب) ويعيد معرّفه."""
    cur = conn.execute(
        "INSERT INTO notifications(user_id, listing_id, kind, body, status, created_at)"
        " VALUES (?, ?, ?, ?, 'simulated', ?)",
        (user_id, listing_id, kind, body, now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def deliver(phone: str, body: str) -> str:
    """
    نقطة الاستبدال الوحيدة عند الإطلاق الفعلي:
    هنا يُستدعى WhatsApp Business Cloud API ‏(POST /{phone_id}/messages)
    بقالب معتمد. حالياً الاكتفاء بتسجيل المحاكاة.
    """
    return "simulated"


def sweep_expiring(conn: sqlite3.Connection, t: float) -> int:
    """
    يبحث عن تثبيتات تنتهي خلال نافذة التذكير ويُرسل تذكيراً واحداً لكل دورة.

    إلغاء التكرار: لا تذكير ثانياً لنفس الإعلان داخل دورة التثبيت الحالية
    (بدايتها = نهاية المدة − مدة التثبيت الأصلية)، فيبقى الجرس صديقاً
    لا مزعجاً.
    """
    rows = conn.execute(
        """SELECT l.id, l.title, l.user_id, l.featured_until
           FROM listings l
           WHERE l.is_featured = 1 AND l.is_active = 1
             AND l.featured_until > ? AND l.featured_until <= ?""",
        (t, t + REMINDER_WINDOW),
    ).fetchall()

    sent = 0
    for r in rows:
        cycle_start = (r["featured_until"] or 0) - catalog.FEATURE_DAYS * 86400 - 60
        dup = conn.execute(
            "SELECT 1 FROM notifications WHERE kind = ? AND listing_id = ?"
            " AND created_at >= ? LIMIT 1",
            (KIND_REMINDER, r["id"], cycle_start),
        ).fetchone()
        if dup:
            continue
        hours = max(1, int(((r["featured_until"] or 0) - t) / 3600))
        body = (f"⭐ تثبيت إعلانك «{_clip(r['title'])}» ينتهي بعد {hours} ساعة "
                f"وسيختفي من الصدارة. جدّده الآن بـ {catalog.FEATURE_RENEW_PRICE_JD} "
                f"{catalog.CURRENCY} فقط ليبقى أولاً أسبوعاً آخر.")
        queue(conn, r["user_id"], r["id"], KIND_REMINDER, body)
        sent += 1
    return sent


def _clip(text: str, limit: int = 40) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"
