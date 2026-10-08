"""
طبقة قاعدة البيانات - منصة الشامل الذكية
========================================
SQLite افتراضياً (يعمل فوراً بدون إعداد)، ويتحول تلقائياً إلى PostgreSQL
إذا تم ضبط DATABASE_URL في ملف البيئة.
"""
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("SHAMAA_DB", BASE_DIR / "data" / "shamaa.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """اتصال واحد لكل Thread - آمن مع uvicorn متعدد الـ threads."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


@contextmanager
def tx():
    """معاملة ذرية: تنفذ commit عند النجاح و rollback عند أي خطأ."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    phone         TEXT    NOT NULL UNIQUE,          -- رقم واتساب للتواصل
    city          TEXT    NOT NULL DEFAULT 'عمّان',
    password_hash TEXT    NOT NULL,
    is_verified   INTEGER NOT NULL DEFAULT 0,       -- وسم "بائع موثّق" (مدفوع)
    pricing_credits INTEGER NOT NULL DEFAULT 0,     -- رصيد حزم التسعير الذكي
    is_admin      INTEGER NOT NULL DEFAULT 0,
    extra_listing_slots INTEGER NOT NULL DEFAULT 0,  -- فتحات إعلانات مدفوعة فوق الحد المجاني
    created_at    REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS listings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    price       REAL    NOT NULL DEFAULT 0,
    category    TEXT    NOT NULL,
    city        TEXT    NOT NULL DEFAULT 'عمّان',
    accepts_barter INTEGER NOT NULL DEFAULT 0,      -- 🔄 قابل للمقايضة
    is_featured INTEGER NOT NULL DEFAULT 0,         -- ⭐ إعلان مثبّت (مدفوع)
    featured_until REAL,                            -- تاريخ انتهاء التثبيت
    is_active   INTEGER NOT NULL DEFAULT 1,
    views       INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL,     -- feature | verify | credits | renew
    amount_jd   REAL    NOT NULL,     -- المبلغ بالدينار الأردني
    listing_id  INTEGER REFERENCES listings(id) ON DELETE SET NULL,
    status      TEXT    NOT NULL DEFAULT 'paid',
    method      TEXT    NOT NULL DEFAULT 'simulated',
    created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS pricing_uses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
    visitor_ip TEXT,
    item_name  TEXT,
    low_price  REAL,
    high_price REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS link_hits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,        -- scan: فتحة رابط قصير/QR · page: زيارة صفحة المشاركة
    created_at REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_listings_active   ON listings(is_active, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hits_listing      ON link_hits(listing_id, kind);
CREATE INDEX IF NOT EXISTS idx_listings_category ON listings(category);
CREATE INDEX IF NOT EXISTS idx_payments_user     ON payments(user_id);
"""


# أعمدة تُضاف لقواعد قائمة أنشأتها إصدارات أقدم (ترحيل بسيط وآمن)
MIGRATIONS = [
    ("users", "extra_listing_slots",
     "ALTER TABLE users ADD COLUMN extra_listing_slots INTEGER NOT NULL DEFAULT 0"),
    ("listings", "image_path",
     "ALTER TABLE listings ADD COLUMN image_path TEXT NOT NULL DEFAULT ''"),
]


def init_db() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(ddl)


def now() -> float:
    return time.time()
