"""
📣 طبقة المشاركة والانتشار — engine النمو العضوي للمنصة
======================================================
لماذا هذا الملف موجود؟
----------------------
الإعلانات المبوبة تعيش أو تموت بحجم إعادة النشر. كل مشاركة على واتساب هي
إعلان مجاني للمنصة، وكل رابط يُفتح من زاحف فيسبوك/واتساب هو عميل محتمل.
لذلك هذه الطبقة تحل ثلاث مشاكل تقنية محددة:

1. **الزواحف لا تنفّذ JavaScript.** صفحة `/listing/{id}` في الواجهة عبارة عن
   SPA تُحمّل البيانات بعد الفتح، فتصل الزواحف إلى وسم `<og:title>` فارغ
   فيظهر الرابط كمربع رمادي بلا صورة ولا سعر. الحل: صفحة مُصيَّرة على الخادم
   بـ HTML كامل + وسوم Open Graph/Twitter + JSON-LD، تعمل حتى مع تعطيل JS.

2. **لا توجد أي حزمة خارجية مسموحة.** لا `qrcode` ولا `segno` ولا `Pillow`.
   لذلك `server/qr.py` (مُرمِّز QR مكتوب من الصفر) و`server/png.py`
   (مُرمِّز PNG + canvas) ينفّذان كل شيء بـ `zlib`/`struct` فقط.

3. **الخطوط العربية غير متوفرة للرسم.** لا يمكن تصيير نص عربي داخل الصورة
   بدون ملفات خطوط + تشكيل (shaping) + اتجاه من اليمين لليسار. لذلك بطاقة
   المشاركة تحمل **الأرقام والأشكال ورمز QR** فقط، والنص العربي يسافر عبر
   `og:title` و`og:description` حيث تعرضه المنصات بخطوطها الخاصة بشكل صحيح.
   هذا قرار مقصود وليس نقصاً.

الخصوصية
--------
🔒 لا تُسرَّب أرقام الهواتف أبداً في صفحة المشاركة أو البطاقة أو الـ QR —
   تماماً مثل `serialize_listing` في `main.py`. الزائر يصل واتساب عبر الرابط.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.parse
from typing import Optional

from . import qr
from .png import Canvas, draw_qr

# ─────────────────────────────────────────────────────────────
# إعدادات
# ─────────────────────────────────────────────────────────────

#: تجاوز صريح للنطاق العام (يُستخدم في الإنتاج خلف وكيل أو عند النشر).
#: بدونه نستنتج النطاق من ترويسات الطلب — وهذا ضروري في بيئة المعاينة حيث
#: يكون `Host` هو النطاق الوسيط وليس `localhost`.
PUBLIC_URL_ENV = "SHAMAA_PUBLIC_URL"

SITE_NAME = "منصة الشامل الذكية"
SITE_SHORT = "الشامل"
SITE_TAGLINE = "بيع واشترِ وقايض في الأردن"

#: وسائط التواصل التي نبني لها روابط مشاركة.
SHARE_NETWORKS = ("whatsapp", "facebook", "x", "telegram", "copy")

#: حجم بطاقة Open Graph — 1200×630 هو المعيار الذي تقصّه فيسبوك وواتساب
#: وX بنسبة 1.91:1 دون فقدان أطراف.
CARD_W, CARD_H = 1200, 630

# لوحة ألوان الهوية (مطابقة لـ static/style.css)
NAVY = (18, 33, 46)
NAVY_DEEP = (11, 21, 30)
GOLD = (245, 166, 35)
GOLD_LIGHT = (255, 216, 107)
CREAM = (255, 251, 240)
INK = (18, 33, 46)
MUTED = (122, 138, 152)
GREEN = (37, 211, 102)

# ─────────────────────────────────────────────────────────────
# ذاكرة مؤقتة
# ─────────────────────────────────────────────────────────────
# الزواحف تجلب البطاقة عند كل مشاركة، وتوليد PNG يكلف ~50 مللي ثانية.
# نُخزّن النتائج بمفتاح = بصمة كل ما يؤثر في الصورة، فلا تظهر بطاقة قديمة
# أبداً بعد تعديل السعر أو العنوان.
_CARD_CACHE: dict[str, bytes] = {}
_SVG_CACHE: dict[str, str] = {}
_CACHE_MAX = 64


def _cache_put(cache: dict, key: str, value) -> None:
    """إدخال مع حدّ أعلى بسيط — يفرّغ الأقدم عند الامتلاء."""
    if len(cache) >= _CACHE_MAX:
        cache.clear()
    cache[key] = value


# ─────────────────────────────────────────────────────────────
# 1) استنتاج النطاق العام
# ─────────────────────────────────────────────────────────────

def public_base_url(request=None) -> str:
    """
    يعيد النطاق العام بلا شرطة نهائية، مثل `https://shamaa.example`.

    الترتيب:
      1. متغيّر البيئة `SHAMAA_PUBLIC_URL` (الأوثق في الإنتاج).
      2. `X-Forwarded-Host` + `X-Forwarded-Proto` (خلف وكيل مثل بيئة المعاينة،
         حيث يصل الطلب عبر `https://{port}-{sandbox}.e2b.app`).
      3. `Host` + مخطط الطلب.

    ⚠️ هذا مهم لرمز QR: لو بنينا الرابط من `localhost` لأنتجنا رمزاً يفتح
    صفحة ميتة على هاتف المستخدم.
    """
    import os

    override = os.environ.get(PUBLIC_URL_ENV, "").strip()
    if override:
        return override.rstrip("/")

    if request is not None:
        headers = request.headers
        fwd_host = (headers.get("x-forwarded-host") or "").split(",")[0].strip()
        host = fwd_host or (headers.get("host") or "").strip()
        if host:
            proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip()
            if not proto:
                proto = getattr(request.url, "scheme", "") or "http"
            return f"{proto}://{host}"

    return "http://localhost:8000"


def canonical_url(request, listing_id: int) -> str:
    """الرابط القانوني الطويل — يُستخدم في `og:url` و`rel=canonical`."""
    return f"{public_base_url(request)}/listing/{listing_id}"


def short_url(request, listing_id: int) -> str:
    """
    الرابط القصير `/l/{id}` — يُرمَّز داخل QR ويظهر في رسالة واتساب.

    أقصر بـ 8 محارف من القانوني، وهذا يخفض إصدار رمز QR درجة كاملة
    (مثلاً من الإصدار 5 إلى 4)، أي وحدات أقل ورمز أنظف عند الطباعة.
    """
    return f"{public_base_url(request)}/l/{listing_id}"


# ─────────────────────────────────────────────────────────────
# 2) تنسيق النص والسعر
# ─────────────────────────────────────────────────────────────

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _to_latin_digits(s: str) -> str:
    """تحويل الأرقام العربية-الهندية إلى لاتينية (تُقرأ عالمياً وتُطبع بدقة)."""
    return (s or "").translate(_DIGITS)


def format_price(value) -> str:
    """
    سعر عربي للوسوم النصية: `8,900 دينار`.

    الأرقام الصحيحة بلا كسور (الأسعار في الأردن تُعرض هكذا في المبوبات)،
    ومع فواصل الآلاف لأن 12500 دينار أصعب قراءة من 12,500.
    """
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    if n <= 0:
        return "السعر عند التواصل"
    if abs(n - round(n)) < 0.005:
        return f"{int(round(n)):,} دينار"
    return f"{n:,.2f} دينار"


def format_price_card(value) -> str:
    """نسخة البطاقة: أرقام لاتينية فقط (الخط المدمج لا يملك حروفاً عربية)."""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    if n <= 0:
        return "CALL FOR PRICE"
    if abs(n - round(n)) < 0.005:
        return f"{int(round(n)):,}"
    return f"{n:,.2f}"


def _clip(text: str, limit: int) -> str:
    """قصّ عند حدّ المحارف على آخر مسافة، حتى لا تُبتر كلمة في المنتصف."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def og_title(d: dict) -> str:
    """
    عنوان المشاركة — أهم سطر يقرره الإنسان قبل النقر.

    الصيغة: `تويوتا كامري 2020 — 17,800 دينار · إربد`
    السعر في العنوان يرفع النقرات لأنه يجيب على السؤال الوحيد الذي يهمّ
    المتصفح الأردني قبل أن يفتح الرابط.
    """
    title = _clip(str(d.get("title", "")), 60) or "إعلان على منصة الشامل"
    parts = [title]
    price = format_price(d.get("price"))
    if price != "السعر عند التواصل":
        parts.append(price)
    city = str(d.get("city") or "").strip()
    if city:
        parts.append(city)
    return " — ".join(parts[:1]) + (" · " + " · ".join(parts[1:]) if len(parts) > 1 else "")


def og_description(d: dict) -> str:
    """
    وصف المشاركة — سطرا سياق + دعوة إجراء.

    نذكر حالة السلعة والمقايضة لأنها محددات قرار حقيقية في السوق الأردني،
    ونُنهي بجملة تحريك لأن وصفًا بلا فعل لا يُنقر.
    """
    bits: list[str] = []
    cat = str(d.get("category") or "").strip()
    if cat:
        bits.append(cat)
    cond = str(d.get("condition") or "").strip()
    if cond:
        bits.append(f"الحالة: {cond}")
    if d.get("accepts_barter"):
        bits.append("يقبل المقايضة 🔄")
    desc = _clip(str(d.get("description") or ""), 140)
    if desc:
        bits.append(desc)

    text = " · ".join(b for b in bits if b)
    tail = " افتح الإعلان للتفاصيل وتواصل مع البائع مباشرة عبر واتساب."
    if not text:
        return (f"{og_title(d)} على {SITE_NAME}.{tail}").strip()
    return _clip(text + tail, 200)


def share_text(d: dict, url: str) -> str:
    """
    نص رسالة واتساب الجاهز — ما يُلصَق فعلياً في المحادثة.

    ⚠️ هذا النص هو الذي ينتشر، لذلك يُصاغ كرسالة من إنسان لا كإعلان:
    سعر واضح، رابط قصير، ورمز تعبيري واحد. الطول مقصود تحت 160 محرفاً
    ليظهر كاملاً في معاينة واتساب دون "اقرأ المزيد".
    """
    title = _clip(str(d.get("title", "")), 50) or "إعلان"
    price = format_price(d.get("price"))
    city = str(d.get("city") or "").strip()
    where = f" · {city}" if city else ""
    barter = " 🔄 يقبل المقايضة" if d.get("accepts_barter") else ""
    return f"{title} — {price}{where}{barter}\n{url}"


def _esc(value) -> str:
    """تهريب لوضعه داخل وسم HTML أو خاصية."""
    return html.escape(str(value if value is not None else ""), quote=True)


def _js(value) -> str:
    """ترميز آمن لسلسلة داخل `<script>` — يمنع كسر الوسم أو حقن JS."""
    return json.dumps(str(value if value is not None else ""), ensure_ascii=False) \
        .replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


# ─────────────────────────────────────────────────────────────
# 3) وسوم Open Graph / Twitter / JSON-LD
# ─────────────────────────────────────────────────────────────

_IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg",
               "jpeg": "image/jpeg", "webp": "image/webp"}


def share_image(d: dict, base: str, listing_id: int) -> tuple[str, str, bool]:
    """
    يعيد (رابط صورة المشاركة المطلق، نوع MIME، هل هي البطاقة المولّدة؟).

    صورة البائع الحقيقية تتصدّر عند وجودها لأنها ما يجعل الإنسان ينقر —
    الوجه المألوف للسلعة أثقـل وقـعاً من أي تصميم. وعند غيابها تسدّ بطاقة
    المنصة (السعر + رمز QR) الفراغ فلا يظهر الرابط بلا صورة أبداً.
    """
    rel = str(d.get("image") or "").strip()
    if rel.startswith("/uploads/"):
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        return (base + rel, _IMAGE_MIME.get(ext, "image/jpeg"), False)
    return (f"{base}/listing/{listing_id}/card.png", "image/png", True)


def og_meta(d: dict, request=None, listing_id: Optional[int] = None) -> str:
    """
    كتلة الوسوم الكاملة التي تجعل الرابط يُعرض كبطاقة غنية.

    نضع `og:image` بحجم 1200×630 صريح لأن فيسبوك يتجاهل الصور التي لا
    يعرف أبعادها قبل جلبها، و`twitter:card=summary_large_image` لأن X
    يعرض الصورة الصغيرة افتراضياً بدون هذا الوسم.
    """
    lid = listing_id if listing_id is not None else int(d.get("id") or 0)
    base = public_base_url(request)
    url = f"{base}/listing/{lid}"
    image, mime, is_card = share_image(d, base, lid)
    title = og_title(d)
    desc = og_description(d)

    tags = [
        # ── Open Graph ──
        ("og:type", "website"),
        ("og:site_name", SITE_NAME),
        ("og:title", title),
        ("og:description", desc),
        ("og:url", url),
        ("og:image", image),
        ("og:image:secure_url", image),
        ("og:image:type", mime),
    ]
    if is_card:
        tags += [
            ("og:image:width", str(CARD_W)),
            ("og:image:height", str(CARD_H)),
        ]
    tags += [
        ("og:locale", "ar_JO"),
        # ── Twitter / X ──
        ("twitter:card", "summary_large_image"),
        ("twitter:title", title),
        ("twitter:description", desc),
        ("twitter:image", image),
        ("twitter:image:alt", title),
        # ── عام ──
        ("description", desc),
        ("theme-color", "#12212E"),
    ]
    out = [f'<meta property="{_esc(k)}" content="{_esc(v)}">' if k.startswith("og:")
           else f'<meta name="{_esc(k)}" content="{_esc(v)}">' for k, v in tags]
    out.append(f'<link rel="canonical" href="{_esc(url)}">')
    return "\n".join(out)


def json_ld(d: dict, request=None, listing_id: Optional[int] = None) -> str:
    """
    بيانات منظَّمة بصيغة `schema.org/Product`.

    الفائدة عملية: نتائج جوجل الغنية تُظهر السعر مباشرة في صفحة البحث،
    وهذا يجلب زواراً بلا إنفاق إعلاني — نفس هدف طبقة المشاركة.
    """
    lid = listing_id if listing_id is not None else int(d.get("id") or 0)
    base = public_base_url(request)
    try:
        price = float(d.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0

    data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": _clip(str(d.get("title", "")), 120) or f"إعلان رقم {lid}",
        "description": og_description(d),
        "url": f"{base}/listing/{lid}",
        "image": share_image(d, base, lid)[0],
        "productID": str(lid),
        "category": str(d.get("category") or ""),
    }
    if price > 0:
        data["offers"] = {
            "@type": "Offer",
            "price": f"{price:.2f}",
            "priceCurrency": "JOD",
            "availability": "https://schema.org/InStock",
            "url": f"{base}/listing/{lid}",
            "itemCondition": "https://schema.org/UsedCondition",
        }
    area = str(d.get("city") or "").strip()
    if area:
        data["areaServed"] = {"@type": "City", "name": area}

    return json.dumps(data, ensure_ascii=False, indent=None)


# ─────────────────────────────────────────────────────────────
# 4) بطاقة المشاركة (OG image) — PNG من الصفر
# ─────────────────────────────────────────────────────────────

def _rounded_panel(c: Canvas, x: int, y: int, w: int, h: int,
                   r: int, color) -> None:
    """مستطيل بزوايا دائرية — يُبنى من مستطيلين + 4 دوائر."""
    c.fill_rect(x + r, y, w - 2 * r, h, color)
    c.fill_rect(x, y + r, w, h - 2 * r, color)
    for cx, cy in ((x + r, y + r), (x + w - r - 1, y + r),
                   (x + r, y + h - r - 1), (x + w - r - 1, y + h - r - 1)):
        c.circle(cx, cy, r, color)


def build_card(d: dict, url: str) -> bytes:
    """
    يبني بطاقة Open Graph بمقاس 1200×630 ويعيدها كبايتات PNG.

    التصميم (من اليمين لليسار لأن العربية RTL):
      • خلفية بتدرج كحلي → ذهبي داكن، مع هالة دائرية ذهبية شفافة.
      • اليمين: شمعة الهوية + اسم المنصة، ثم السعر بخط ضخم (أكبر عنصر في
        البطاقة، لأنه ما يقرؤه المتصفح في أقل من ثانية).
      • اليسار: رمز QR على لوحة بيضاء — يُمسح بالهاتف فيفتح الإعلان فوراً
        حتى من شاشة حاسوب، وهذا يحوّل مشاركة فيسبوك إلى زيارة موبايل.

    النتيجة مخزّنة مؤقتاً بمفتاح = بصمة المحتوى + الرابط، فلا تُعاد الحساب
    عند كل جلب من الزاحف.
    """
    key_src = "|".join([
        _to_latin_digits(str(d.get("price", ""))),
        str(d.get("id", "")),
        str(d.get("is_featured", "")),
        url,
        "v1",  # يُرفع عند تغيير التصميم لإبطال كل المخزون
    ])
    key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()
    if key in _CARD_CACHE:
        return _CARD_CACHE[key]

    c = Canvas(CARD_W, CARD_H)
    c.dgradient(NAVY, (40, 33, 22))

    # هالات ذهبية شفافة تعطي عمقاً بدل خلفية مسطحة
    c.circle(1080, 90, 210, GOLD, 34)
    c.circle(140, 600, 170, GOLD, 22)
    c.circle(980, 560, 120, GOLD_LIGHT, 16)

    # شريط علوي رفيع بلون الهوية
    c.fill_rect(0, 0, CARD_W, 8, GOLD)

    # ── شمعة الهوية + اسم المنصة ──
    fx, fy = 70, 62
    c.circle(fx + 16, fy + 30, 16, GOLD)              # جسم الشمعة
    c.fill_rect(fx + 6, fy + 30, 21, 44, GOLD)
    c.circle(fx + 16, fy + 12, 9, GOLD_LIGHT)         # اللهب
    c.circle(fx + 16, fy + 13, 4, CREAM)
    c.text("ALSHAMIL", fx + 52, fy + 18, scale=5, color=(255, 255, 255))
    c.text("JORDAN MARKETPLACE", fx + 52, fy + 60, scale=2, color=GOLD_LIGHT)

    if int(d.get("is_featured") or 0):
        c.text("FEATURED", fx + 52, fy + 96, scale=3, color=GREEN)

    # ── لوحة رمز QR أولاً: موضعها يحدد المساحة المتاحة للسعر ──
    modules, size, mask = qr.make_matrix(url)
    scale_qr = 9
    border = 3
    panel_w = (size + border * 2) * scale_qr
    px = CARD_W - panel_w - 70
    py = CARD_H - panel_w - 64

    # ── السعر: العنصر الأضخم، وخطه يتقلص تلقائياً إن ضاقت المساحة ──
    # أسعار العقارات تصل 6 خانات (125,000) فيجب ألا تصطدم بلوحة الـ QR.
    price = format_price_card(d.get("price"))
    jod_scale = 7
    jod_w = c.text_width("JOD", scale=jod_scale) + 24   # يُحجز قبل حساب الخط
    avail = px - 70 - 80 - jod_w          # هامش يمين + فاصل أمان + وسمة JOD
    per_char = len(price) * 6             # 5 أعمدة للرمز + 1 فاصل
    price_scale = max(8, min(20, avail // max(1, per_char)))
    c.text(price, 70, 268, scale=price_scale, color=GOLD_LIGHT)
    price_bottom = 268 + 7 * price_scale
    c.text("JOD", 70 + c.text_width(price, scale=price_scale) + 24,
           price_bottom - 54, scale=jod_scale, color=(255, 255, 255))

    # ── معرّف الإعلان + تلميح المسح ──
    lid = _to_latin_digits(str(d.get("id", "")))
    if lid:
        c.text(f"LISTING #{lid}", 70, price_bottom + 34, scale=4,
               color=(190, 203, 216))
    c.text("SCAN TO OPEN", 70, price_bottom + 100, scale=4, color=GOLD)

    # شريط سفلي
    c.fill_rect(0, CARD_H - 8, CARD_W, 8, GOLD)
    # ── لوحة رمز QR ──
    _rounded_panel(c, px - 14, py - 14, panel_w + 28, panel_w + 28, 18, CREAM)
    draw_qr(c, modules, px, py, scale=scale_qr, border=border,
            dark=INK, light=(255, 255, 255))

    png = c.to_png()
    _cache_put(_CARD_CACHE, key, png)
    return png


def build_brand_card(base_url: str) -> bytes:
    """
    بطاقة Open Graph للصفحة الرئيسية — حتى مشاركة رابط المنصة نفسه
    تظهر كبطاقة غنية بهوية بصرية ورمز QR يفتح السوق على الهاتف.

    تُخزَّن مؤقتاً بمفتاح النطاق: تتغير فقط بتغيّر النطاق أو رفع إصدار
    التصميم.
    """
    key = hashlib.sha256(f"brand|{base_url}|v1".encode()).hexdigest()
    if key in _CARD_CACHE:
        return _CARD_CACHE[key]

    c = Canvas(CARD_W, CARD_H)
    c.dgradient(NAVY, (44, 35, 20))
    c.circle(150, 560, 190, GOLD, 26)
    c.circle(1060, 70, 230, GOLD, 30)
    c.fill_rect(0, 0, CARD_W, 8, GOLD)
    c.fill_rect(0, CARD_H - 8, CARD_W, 8, GOLD)

    # شمعة الهوية — أكبر هنا لأنها بطلة البطاقة
    fx, fy = 90, 150
    c.circle(fx + 26, fy + 52, 26, GOLD)
    c.fill_rect(fx + 10, fy + 52, 33, 74, GOLD)
    c.circle(fx + 26, fy + 20, 15, GOLD_LIGHT)
    c.circle(fx + 26, fy + 22, 7, CREAM)

    c.text("ALSHAMIL", fx + 90, fy + 30, scale=10, color=(255, 255, 255))
    c.text("JORDAN MARKETPLACE", fx + 90, fy + 110, scale=4, color=GOLD_LIGHT)
    c.text("BUY - SELL - BARTER", fx + 90, fy + 160, scale=4,
           color=(190, 203, 216))
    c.text("FAIR PRICES BY AI", fx + 90, fy + 210, scale=3, color=GREEN)

    # رمز QR يفتح السوق على هاتف من يرى البطاقة
    modules, size, mask = qr.make_matrix(base_url)
    scale, border = 8, 3
    panel_w = (size + border * 2) * scale
    px, py = CARD_W - panel_w - 80, CARD_H - panel_w - 80
    _rounded_panel(c, px - 14, py - 14, panel_w + 28, panel_w + 28, 18, CREAM)
    draw_qr(c, modules, px, py, scale=scale, border=border,
            dark=INK, light=(255, 255, 255))

    png = c.to_png()
    _cache_put(_CARD_CACHE, key, png)
    return png


# ─────────────────────────────────────────────────────────────
# 5) رمز QR كـ SVG (قابل للتكبير والطباعة)
# ─────────────────────────────────────────────────────────────

def qr_svg(url: str, border: int = 2, scale: int = 6) -> str:
    """SVG خالص بلا أي اعتماد خارجي — يُطبع على ملصق أو يُعرض في الصفحة."""
    key = f"{url}|{border}|{scale}|v1"
    if key in _SVG_CACHE:
        return _SVG_CACHE[key]
    svg = qr.to_svg(url, border=border, scale=scale)
    _cache_put(_SVG_CACHE, key, svg)
    return svg


# ─────────────────────────────────────────────────────────────
# 6) حزمة المشاركة للواجهة
# ─────────────────────────────────────────────────────────────

def share_links(d: dict, request=None, listing_id: Optional[int] = None) -> dict:
    """
    يعيد كل ما تحتاجه الواجهة لبناء أزرار المشاركة — جاهز كـ JSON.

    تُبنى الروابط على الخادم (لا في JS) لسببين: النطاق العام الصحيح يعرفه
    الخادم وحده، ونص الرسالة يجب أن يكون متطابقاً بين كل الشبكات.
    """
    lid = listing_id if listing_id is not None else int(d.get("id") or 0)
    long_url = canonical_url(request, lid)
    short = short_url(request, lid)
    text = share_text(d, short)
    quoted = urllib.parse.quote(text)
    title_q = urllib.parse.quote(og_title(d))
    url_q = urllib.parse.quote(long_url)

    return {
        "listing_id": lid,
        "url": long_url,
        "short_url": short,
        "text": text,
        "title": og_title(d),
        "description": og_description(d),
        "image": share_image(d, public_base_url(request), lid)[0],
        "qr_svg": f"/api/qr/{lid}.svg",
        "networks": {
            # واتساب: النص الكامل المنسّق — أهم قناة في الأردن بفارق كبير
            "whatsapp": f"https://wa.me/?text={quoted}",
            # فيسبوك: لا يقبل نصاً مخصصاً، يقرأ الوسوم من الصفحة
            "facebook": f"https://www.facebook.com/sharer/sharer.php?u={url_q}",
            "x": f"https://twitter.com/intent/tweet?text={quoted}",
            "telegram": f"https://t.me/share/url?url={url_q}&text={title_q}",
            # "copy" بلا رابط — الواجهة تنسخ `short_url` إلى الحافظة
            "copy": short,
        },
    }


# ─────────────────────────────────────────────────────────────
# 7) صفحة الإعلان المُصيَّرة على الخادم
# ─────────────────────────────────────────────────────────────

def render_listing_page(d: dict, request=None, listing_id: Optional[int] = None,
                        seller: Optional[dict] = None) -> str:
    """
    صفحة HTML مستقلة وكاملة لإعلان واحد.

    تعمل بلا JavaScript وتحتوي على كل الوسوم التي تحتاجها الزواحف، وفي نفس
    الوقت هي صفحة مفيدة للإنسان: صورة البطاقة، السعر، الوصف، زر واتساب،
    ورابط إلى المنصة. التصميم مضمّن (inline) عمداً حتى لا تعتمد على ملف
    CSS واحد قد يتغيّر — صفحة المشاركة يجب أن تبقى تعمل دائماً.

    🔒 لا يُطبع رقم الهاتف هنا إطلاقاً؛ التواصل عبر رابط واتساب فقط.
    """
    lid = listing_id if listing_id is not None else int(d.get("id") or 0)
    base = public_base_url(request)
    url = canonical_url(request, lid)
    links = share_links(d, request, lid)
    wa = str(d.get("whatsapp_url") or "").strip()

    title = og_title(d)
    desc = og_description(d)
    price = format_price(d.get("price"))
    featured = bool(int(d.get("is_featured") or 0))

    rows = []
    for label, value in (
        ("التصنيف", d.get("category")),
        ("الحالة", d.get("condition")),
        ("المدينة", d.get("city")),
        ("سنة الصنع", _to_latin_digits(str(d.get("year") or "")) or None),
    ):
        if value and str(value).strip():
            rows.append(
                f'<div class="kv"><span>{_esc(label)}</span>'
                f'<strong>{_esc(value)}</strong></div>'
            )
    if d.get("accepts_barter"):
        rows.append('<div class="kv"><span>المقايضة</span>'
                    '<strong>يقبل المقايضة 🔄</strong></div>')
    if featured:
        left = int(d.get("featured_hours_left") or 0)
        rows.append(f'<div class="kv"><span>الحالة</span>'
                    f'<strong>⭐ إعلان مثبّت · {left} ساعة متبقية</strong></div>')
    rows_html = "\n        ".join(rows)

    body_desc = _esc(str(d.get("description") or "").strip() or "لا يوجد وصف إضافي.")
    seller_name = _esc(str((seller or {}).get("name") or "بائع على المنصة"))
    verified = bool((seller or {}).get("is_verified"))
    badge = ('<span class="badge" title="بائع دفع رسوم التوثيق">🛡️ بائع موثّق</span>'
             if verified else "")

    wa_block = (
        f'<a class="cta wa" href="{_esc(wa)}" rel="noopener noreferrer" '
        f'target="_blank">💬 تواصل عبر واتساب</a>'
        if wa else
        '<span class="cta muted">التواصل متاح داخل المنصة</span>'
    )

    net = links["networks"]
    share_buttons = f'''
      <a class="chip wa" href="{_esc(net["whatsapp"])}" target="_blank" rel="noopener noreferrer">واتساب</a>
      <a class="chip" href="{_esc(net["facebook"])}" target="_blank" rel="noopener noreferrer">فيسبوك</a>
      <a class="chip" href="{_esc(net["x"])}" target="_blank" rel="noopener noreferrer">X</a>
      <a class="chip" href="{_esc(net["telegram"])}" target="_blank" rel="noopener noreferrer">تيليجرام</a>
      <button class="chip" type="button" data-copy="{_esc(links["short_url"])}">نسخ الرابط</button>'''

    hero, _mime, hero_is_card = share_image(d, base, lid)
    hero_attrs = (f' width="{CARD_W}" height="{CARD_H}"' if hero_is_card else "")
    hero_cls = "" if hero_is_card else ' class="photo"'

    return f'''<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)} · {_esc(SITE_NAME)}</title>
{og_meta(d, request, lid)}
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🌟</text></svg>">
<script type="application/ld+json">{json_ld(d, request, lid)}</script>
<style>
  :root {{ --navy:#12212E; --gold:#F5A623; --gold-l:#FFD86B; --cream:#FFFBF0; }}
  * {{ box-sizing:border-box; }}
  body {{
    margin:0; background:#F3F1EC; color:var(--navy);
    font-family:"Segoe UI",Tahoma,"Noto Sans Arabic",system-ui,sans-serif;
    line-height:1.7;
  }}
  .top {{ background:linear-gradient(135deg,var(--navy),#2A2416); color:#fff;
         padding:14px 18px; display:flex; align-items:center; gap:10px; }}
  .top a {{ color:var(--gold-l); text-decoration:none; font-weight:700; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:18px; }}
  .card {{ background:#fff; border-radius:18px; overflow:hidden;
           box-shadow:0 8px 30px rgba(18,33,46,.10); }}
  .card img {{ width:100%; display:block; }}
  .card img.photo {{ max-height:420px; object-fit:cover; }}
  .pad {{ padding:20px; }}
  h1 {{ font-size:1.35rem; margin:0 0 6px; line-height:1.5; }}
  .price {{ font-size:1.9rem; font-weight:800; color:var(--gold);
            margin:10px 0; letter-spacing:-.5px; }}
  .seller {{ font-size:.92rem; opacity:.8; margin-bottom:14px; }}
  .badge {{ display:inline-block; background:#E8F7EE; color:#1B7A3E;
            border-radius:999px; padding:2px 10px; font-size:.82rem;
            font-weight:700; margin-inline-start:6px; }}
  .kv {{ display:flex; justify-content:space-between; gap:12px;
         padding:9px 0; border-bottom:1px dashed #E4E0D6; font-size:.95rem; }}
  .kv span {{ opacity:.65; }}
  .desc {{ margin:16px 0; white-space:pre-wrap; font-size:1rem; }}
  .cta {{ display:block; text-align:center; padding:14px; border-radius:14px;
          font-weight:800; font-size:1.05rem; text-decoration:none; margin:14px 0; }}
  .cta.wa {{ background:#25D366; color:#06381C; }}
  .cta.muted {{ background:#EDEAE3; color:#6B7683; }}
  .share {{ background:#FAF8F3; border-radius:14px; padding:14px; margin-top:6px; }}
  .share h2 {{ font-size:.95rem; margin:0 0 10px; opacity:.7; font-weight:700; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:8px; }}
  .chip {{ background:#fff; border:1px solid #E0DACE; color:var(--navy);
           border-radius:999px; padding:7px 15px; font-size:.9rem; font-weight:700;
           text-decoration:none; cursor:pointer; font-family:inherit; }}
  .chip.wa {{ background:#25D366; border-color:#25D366; color:#06381C; }}
  .qr {{ text-align:center; margin-top:16px; }}
  .qr svg {{ width:180px; height:180px; }}
  .qr p {{ font-size:.85rem; opacity:.6; margin:8px 0 0; }}
  .foot {{ text-align:center; padding:20px; font-size:.9rem; opacity:.65; }}
  .foot a {{ color:var(--gold); font-weight:700; text-decoration:none; }}
  .toast {{ position:fixed; inset-block-end:22px; inset-inline-start:50%;
            transform:translateX(50%); background:var(--navy); color:#fff;
            padding:10px 20px; border-radius:999px; font-size:.9rem; opacity:0;
            transition:opacity .25s; pointer-events:none; }}
  .toast.on {{ opacity:1; }}
</style>
</head>
<body>

<div class="top">
  <span style="font-size:1.4rem">🌟</span>
  <a href="/">{_esc(SITE_NAME)}</a>
  <span style="opacity:.6;font-size:.85rem;margin-inline-start:auto">{_esc(SITE_TAGLINE)}</span>
</div>

<div class="wrap">
  <div class="card">
    <img src="{_esc(hero)}"{hero_cls} alt="{_esc(title)}"{hero_attrs}>
    <div class="pad">
      <h1>{_esc(title)}</h1>
      <div class="seller">البائع: {_esc(seller_name)}{badge}</div>
      <div class="price">{_esc(price)}</div>

      <div>
        {rows_html}
      </div>

      <p class="desc">{body_desc}</p>

      {wa_block}

      <div class="share">
        <h2>📣 شارك الإعلان — النشر أسرع طريقة للبيع</h2>
        <div class="chips">{share_buttons}
        </div>
        <div class="qr">
          <img src="/api/qr/{lid}.svg" alt="رمز QR للإعلان" width="180" height="180">
          <p>امسح الرمز بهاتفك لفتح الإعلان مباشرة</p>
        </div>
      </div>
    </div>
  </div>

  <div class="foot">
    <a href="/">← تصفّح كل الإعلانات على {_esc(SITE_SHORT)}</a>
  </div>
</div>

<div class="toast" id="toast">تم نسخ الرابط ✅</div>

<script>
// تحسين تدريجي فقط — الصفحة تعمل كاملة بدونه (وهذا ما تحتاجه الزواحف).
(function () {{
  var toast = document.getElementById('toast');
  function flash() {{
    toast.classList.add('on');
    setTimeout(function () {{ toast.classList.remove('on'); }}, 1800);
  }}
  document.querySelectorAll('[data-copy]').forEach(function (btn) {{
    btn.addEventListener('click', function () {{
      var link = btn.getAttribute('data-copy');
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(link).then(flash, flash);
      }} else {{
        // بديل للمتصفحات القديمة أو خارج HTTPS
        var ta = document.createElement('textarea');
        ta.value = link; ta.setAttribute('readonly', '');
        ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        try {{ document.execCommand('copy'); }} catch (e) {{}}
        document.body.removeChild(ta); flash();
      }}
    }});
  }});
}})();
</script>
</body>
</html>'''


def render_missing_page(listing_id, request=None) -> str:
    """
    صفحة 404 للإعلان المحذوف — بنفس وسوم المشاركة.

    مهم لأن الروابط المشتركة تبقى منتشرة بعد حذف الإعلان، فوصول الزائر إلى
    صفحة فارغة يعني خسارة ثقة. نحوّله إلى المنصة بدلاً من ذلك.
    """
    lid = _esc(_to_latin_digits(str(listing_id)))
    title = "هذا الإعلان لم يعد متاحاً"
    desc = (f"الإعلان رقم {lid} حُذف أو انتهت مدته على {SITE_NAME}. "
            f"تصفّح آلاف الإعلانات الأخرى المعروضة للبيع والمقايضة في الأردن.")
    return f'''<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)} · {_esc(SITE_NAME)}</title>
<meta property="og:type" content="website">
<meta property="og:site_name" content="{_esc(SITE_NAME)}">
<meta property="og:title" content="{_esc(title)}">
<meta property="og:description" content="{_esc(desc)}">
<meta name="description" content="{_esc(desc)}">
<meta name="robots" content="noindex">
<meta name="theme-color" content="#12212E">
<style>
  body {{ margin:0; min-height:100vh; display:flex; align-items:center;
         justify-content:center; background:#12212E; color:#fff; text-align:center;
         font-family:"Segoe UI",Tahoma,"Noto Sans Arabic",system-ui,sans-serif;
         padding:24px; }}
  .box {{ max-width:440px; }}
  .e {{ font-size:4rem; margin-bottom:8px; }}
  h1 {{ font-size:1.4rem; margin:0 0 10px; }}
  p {{ opacity:.75; line-height:1.8; margin:0 0 22px; }}
  a {{ display:inline-block; background:#F5A623; color:#12212E; font-weight:800;
       padding:13px 26px; border-radius:14px; text-decoration:none; }}
</style>
</head>
<body>
  <div class="box">
    <div class="e">🕯️</div>
    <h1>{_esc(title)}</h1>
    <p>{_esc(desc)}</p>
    <a href="/">تصفّح الإعلانات المتاحة</a>
  </div>
</body>
</html>'''


def cache_info() -> dict:
    """حالة الذاكرة المؤقتة — تُستخدم في الفحوصات والتشخيص."""
    return {
        "cards": len(_CARD_CACHE),
        "svgs": len(_SVG_CACHE),
        "max": _CACHE_MAX,
        "generated_at": time.time(),
    }
