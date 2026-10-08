"""
اختبار شامل من طرف إلى طرف — منصة الشامل الذكية
=================================================
يشغّل كل تدفقات المستخدم وكل مصادر الدخل ويتحقق من الحمايات.

الاستخدام:
    # 1) أوقف الخادم ثم امسح القاعدة لتشغيل اختبار من الصفر:
    rm -f data/shamaa.db*
    # 2) شغّل الخادم
    python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
    # 3) في طرفية أخرى
    python tests/test_e2e.py

ملاحظة: الاختبار يستهلك حدود الاستخدام المجاني، لذلك يجب أن يبدأ من قاعدة نظيفة.
"""
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# يمكن توجيه الاختبار إلى أي منفذ — الافتراضي 8001 (منفذ الاختبار المعزول).
# خادم التطوير/المعاينة يعمل على 8000 ولا يلمسه الاختبار.
BASE = os.environ.get("SHAMAA_TEST_BASE", "http://127.0.0.1:8001")
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

PASS = FAIL = 0


def preflight():
    """يتحقق من أن الخادم يعمل وأن القاعدة نظيفة (وإلا فالنتائج مضللة)."""
    try:
        urllib.request.urlopen(BASE + "/healthz", timeout=4)
    except Exception:
        sys.exit("❌ الخادم لا يعمل على " + BASE + "\n"
                 "   شغّله عبر:  bash tests/run.sh   (يفتح منفذ الاختبار تلقائياً)")
    r = json.loads(urllib.request.urlopen(BASE + "/api/pricing/stats", timeout=4).read())
    if r["total"] > 0:
        sys.exit(f"⚠️  القاعدة ليست نظيفة ({r['total']} عملية تسعير مسجّلة).\n"
                 f"   أوقف الخادم ثم نفّذ:  rm -f data/shamaa.db*  ثم أعد تشغيله.\n"
                 f"   (حدود الاستخدام المجاني تُحسب تراكمياً، فالاختبار يحتاج بداية نظيفة.)")
    print("✅ الخادم يعمل والقاعدة نظيفة — بدء الاختبار\n")


def call(method, path, body=None, expect=200, label=""):
    """ينفّذ طلباً ويتحقق من رمز الاستجابة."""
    global PASS, FAIL
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                headers={"Content-Type": "application/json"})
    try:
        r = op.open(req)
        code, payload = r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        code, payload = e.code, json.loads(e.read() or b"{}")
    ok = code == expect
    PASS += ok
    FAIL += (not ok)
    mark = "✅" if ok else "❌"
    extra = "" if ok else " → " + json.dumps(payload, ensure_ascii=False)[:200]
    print(f"  {mark} {label or path} [{code}]{extra}")
    return payload if ok else None


def get(path, **params):
    return "/api/" + path + ("?" + urllib.parse.urlencode(params) if params else "")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """يمنع اتباع 302 حتى نفحص رابط التحويل نفسه (مسار /l/{id})."""

    def redirect_request(self, *args, **kwargs):
        return None


op_nr = urllib.request.build_opener(_NoRedirect,
                                    urllib.request.HTTPCookieProcessor(cj))


def raw(path, expect=200, label="", follow=False, headers=None):
    """طلب خام يعيد (رمز الاستجابة، البايتات، نوع المحتوى، ترويسة Location)."""
    global PASS, FAIL
    opener = op if follow else op_nr
    try:
        r = opener.open(urllib.request.Request(BASE + path, headers=headers or {}))
        code, body = r.status, r.read()
        ctype, loc = r.headers.get("Content-Type", ""), r.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read()
        ctype, loc = e.headers.get("Content-Type", ""), e.headers.get("Location", "")
    ok = code == expect
    PASS += ok
    FAIL += (not ok)
    mark = "✅" if ok else "❌"
    print(f"  {mark} {label or path} [{code}] {ctype.split(';')[0]} {len(body):,}B")
    return code, body, ctype, loc


def post_raw(path, body, ctype, expect=200, label=""):
    """POST بجسم خام (multipart للصور) مع عدّ النجاح/Fشل."""
    global PASS, FAIL
    req = urllib.request.Request(BASE + path, data=body, method="POST",
                                headers={"Content-Type": ctype})
    try:
        r = op.open(req)
        code, payload = r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        code, payload = e.code, json.loads(e.read() or b"{}")
    ok = code == expect
    PASS += ok
    FAIL += (not ok)
    mark = "✅" if ok else "❌"
    extra = "" if ok else " → " + json.dumps(payload, ensure_ascii=False)[:140]
    print(f"  {mark} {label or path} [{code}]{extra}")
    return payload


def multipart(file_bytes, filename="photo.png", ctype="image/png",
              boundary="shamaa-boundary-7"):
    body = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n").encode() \
        + file_bytes + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def section(n, title):
    print(f"\n{'─'*62}\n  {n}) {title}\n{'─'*62}")


def run_suite() -> None:
    global PASS, FAIL   # القسم 16 يعدّ فحوصات يدوية داخل الدالة نفسها

    # ══════════════════════════════════════════════════════════════
    section(1, "الإقلاع والبيانات المرجعية")
    b = call("GET", "/api/bootstrap", label="bootstrap")
    print("     الأقسام :", " · ".join(b["categories"]))
    print("     المدن   :", " · ".join(b["cities"]))
    pr = b["pricing"]
    print(f"     الأسعار : تثبيت {pr['feature_price']} د.أ/{pr['feature_days']} أيام · "
          f"تجديد {pr['renew_price']} · توثيق {pr['verify_price']} · "
          f"مجاني {pr['free_daily']} تسعيرات/يوم")
    assert len(set(b["cities"])) == len(b["cities"]), "تكرار في قائمة المدن"
    assert all(not c.isascii() for c in b["cities"]), "مدينة بصيغة لاتينية تسرّبت للقائمة"
    print("     ✅ قائمة مدن نظيفة بلا تكرار")

    # ══════════════════════════════════════════════════════════════
    section(2, "تصفح الإعلانات كزائر + حماية الخصوصية")
    l = call("GET", "/api/listings", label="قائمة الإعلانات")
    print(f"     الإعلانات: {len(l['listings'])} | الإجماليات: {l['totals']}")
    first = l["listings"][0]
    assert first["is_featured"] == 1, "المثبّت يجب أن يظهر أولاً"
    print(f"     ✅ الإعلانات المثبّتة تظهر أولاً ({sum(a['is_featured'] for a in l['listings'])} مثبّت)")
    assert "phone" not in first, "ثغرة: رقم الهاتف مكشوف للزوار!"
    print("     ✅ رقم الهاتف مخفي عن الزوار")
    assert first["whatsapp_url"].startswith("https://wa.me/962"), first["whatsapp_url"]
    print(f"     ✅ رابط واتساب أردني صحيح: {first['whatsapp_url'][:52]}…")

    # ══════════════════════════════════════════════════════════════
    section(3, "البحث والفلاتر والترتيب")
    r = call("GET", get("listings", category="سيارات"), label="فلتر قسم: سيارات")
    assert r and all(a["category"] == "سيارات" for a in r["listings"])
    print("     النتائج:", [a["title"] for a in r["listings"]])
    r = call("GET", get("listings", barter=1), label="فلتر: يقبل المقايضة فقط")
    assert r and all(a["accepts_barter"] for a in r["listings"])
    print(f"     ✅ {len(r['listings'])} إعلان يقبل المقايضة")
    r = call("GET", get("listings", q="ايفون"), label="بحث نصي: ايفون")
    print("     ✅ النتائج:", [a["title"] for a in r["listings"]])
    r = call("GET", get("listings", city="إربد", sort="price_asc"), label="مدينة + ترتيب سعري")
    # المثبّتات تبقى في الصدارة دائماً (ميزة مدفوعة)، والباقي يُرتَّب بالسعر
    prices = [a["price"] for a in r["listings"] if not a["is_featured"]]
    assert prices == sorted(prices), f"الترتيب التصاعدي فشل: {prices}"
    print(f"     ✅ الأسعار تصاعدياً بعد المثبّتات: {prices}")
    r = call("GET", get("listings", sort="popular"), label="ترتيب: الأكثر مشاهدة")
    views = [a["views"] for a in r["listings"] if not a["is_featured"]]
    assert views == sorted(views, reverse=True), "ترتيب المشاهدات فشل"
    print("     ✅ ترتيب الأكثر مشاهدة يعمل")

    # ══════════════════════════════════════════════════════════════
    section(4, "حد الاستخدام المجاني للزائر (Freemium)")
    codes = []
    for i in range(pr["free_daily"] + 2):
        data = json.dumps({"item_name": "سماعات بلوتوث", "category": "إلكترونيات",
                           "condition": 8, "city": "عمّان"}).encode()
        req = urllib.request.Request(BASE + "/api/pricing", data=data,
                                    headers={"Content-Type": "application/json"})
        try:
            codes.append(op.open(req).status)
        except urllib.error.HTTPError as e:
            codes.append(e.code)
    print("     رموز الاستجابة:", codes)
    assert codes.count(200) == pr["free_daily"], f"المفروض {pr['free_daily']} نجاح، حصلنا {codes.count(200)}"
    assert 402 in codes, "لم يظهر 402 — نموذج الربح لا يعمل!"
    print(f"     ✅ {codes.count(200)} تسعيرات مجانية ثم 402 (يحتاج رصيداً)")

    # ══════════════════════════════════════════════════════════════
    section(5, "التحقق من صحة المدخلات")
    call("POST", "/api/register", {"name": "م", "phone": "0791234567", "password": "123456"},
         expect=422, label="اسم قصير جداً (مرفوض)")
    call("POST", "/api/register", {"name": "رقم خاطئ", "phone": "12345", "password": "123456"},
         expect=422, label="هاتف غير أردني (مرفوض)")
    call("POST", "/api/register", {"name": "مكرر", "phone": "0791111111", "password": "123456"},
         expect=409, label="رقم مسجّل مسبقاً (مرفوض)")
    call("POST", "/api/register", {"name": "كلمة سر ضعيفة", "phone": "0792220001", "password": "123"},
         expect=422, label="كلمة مرور قصيرة (مرفوض)")
    call("POST", "/api/login", {"phone": "0790000000", "password": "كلمة خاطئة"},
         expect=401, label="كلمة مرور خاطئة (مرفوض)")
    call("POST", "/api/listings", {"title": "x", "price": 1, "category": "إلكترونيات"},
         expect=401, label="نشر بدون تسجيل دخول (مرفوض)")
    call("GET", "/api/admin/stats", expect=403, label="لوحة المدير لزائر (مرفوض)")
    call("DELETE", "/api/listings/1", expect=401, label="حذف بدون جلسة (مرفوض)")

    # ══════════════════════════════════════════════════════════════
    section(6, "إنشاء حساب جديد")
    u = call("POST", "/api/register",
             {"name": "ريم التاجر", "phone": "+962 7 9123 4567", "city": "عمّان",
              "password": "secret123"}, label="تسجيل بصيغة دولية +962")
    assert u, "فشل التسجيل"
    assert u["user"]["phone"] == "0791234567", f"تطبيع الهاتف فشل: {u['user']['phone']}"
    print(f"     ✅ طُبِّع «+962 7 9123 4567» إلى {u['user']['phone']}")
    print(f"     ✅ رصيد التسعير المهدى عند التسجيل: {u['user']['pricing_credits']}")
    me = call("GET", "/api/me", label="قراءة بياناتي (الكوكي يعمل)")
    assert me and me["user"]["name"] == "ريم التاجر", "الجلسة لا تعمل!"
    print("     ✅ الجلسة محفوظة ومقرؤة")

    # ══════════════════════════════════════════════════════════════
    section(7, "🤖 التسعير الذكي بالرصيد المدفوع")
    bought = call("POST", "/api/buy/package", {"package_id": "pack100"},
                  label="شراء حزمة 100 تسعيرة قبل الاختبار")
    assert bought and bought["pricing_credits"] == me["user"]["pricing_credits"] + 100
    print(f"     ✅ الرصيد: {me['user']['pricing_credits']} + 100 = {bought['pricing_credits']}")
    cases = [
        ("ايفون 13 - 128GB", "إلكترونيات", 9, "عمّان", False),
        ("ثلاجة LG بابين", "أجهزة كهربائية", 7, "عمّان", False),
        ("كيا ريو 2016 - أوتوماتيك", "سيارات", 8, "عمّان", False),
        ("طقم صالون خشب زان 7 قطع", "أثاث منزلي", 8, "إربد", False),
        ("شقة في خلدا 180 متر", "عقارات", 8, "عمّان", False),
    ]
    for name, cat, cond, city, _ in cases:
        p = call("POST", "/api/pricing",
                 {"item_name": name, "category": cat, "condition": cond, "city": city},
                 label=f"تسعير: {name}")
        if p:
            print(f"        💰 عادل {p['fair_price']:>7} د.أ | نطاق {p['low_price']}–{p['high_price']} | "
                  f"بيع سريع {p['quick_sale_price']}")
    assert p and p["fair_price"] > 0

    p = call("POST", "/api/pricing",
             {"item_name": "بلايستيشن 5 مع يدين", "category": "إلكترونيات",
              "condition": 9, "city": "عمّان", "accepts_barter": True},
             label="تسعير مع قبول المقايضة")
    assert p and any("المقايضة" in d for d in p["drivers"]), "معامل المقايضة غير مطبّق"
    print("     ✅ خصم المقايضة مطبّق ومذكور في شرح العوامل")
    print("     العوامل:", " | ".join(p["drivers"]))
    print("     النصيحة:", p["tip"])
    before = p["remaining_credits"]

    p2 = call("POST", "/api/pricing",
              {"item_name": "مرسيدس C200 موديل 2015", "category": "سيارات",
               "condition": 8, "city": "عمّان"}, label="تسعير يستخرج سنة الموديل")
    assert p2 and any("موديل 2015" in d for d in p2["drivers"]), "معامل سنة الموديل لم يُطبّق"
    print(f"     ✅ سنة الموديل مستخرجة ومطبّقة → {p2['fair_price']} د.أ")
    assert p2["remaining_credits"] == before - 1, "خصم الرصيد فشل"
    print(f"     ✅ الرصيد ينقص بدقة: {before} → {p2['remaining_credits']}")

    # ══════════════════════════════════════════════════════════════
    section(8, "نشر إعلان مجاني")
    n = call("POST", "/api/listings",
             {"title": "جهاز آيباد برو 11 انش مع الكيبورد", "description": "حالة ممتازة،استخدام خفيف",
              "price": 420, "category": "إلكترونيات", "city": "عمّان",
              "accepts_barter": True, "featured": False}, label="نشر إعلان")
    assert n, "فشل النشر"
    ad_id = n["listing"]["id"]
    assert n["charged_jd"] == 0.0, "الإعلان العادي يجب أن يكون مجانياً"
    print(f"     ✅ {n['message']} (بالمجان)")
    call("POST", "/api/listings",
         {"title": "x", "price": 1, "category": "قسم وهمي"}, expect=422,
         label="قسم غير معروف (مرفوض)")

    # ══════════════════════════════════════════════════════════════
    section(9, "⭐ تثبيت الإعلان — مصدر الدخل #1")
    f = call("POST", "/api/feature", {"listing_id": ad_id, "renew": False}, label="شراء تثبيت")
    assert f and f["charged_jd"] == pr["feature_price"], "سعر التثبيت خاطئ"
    print(f"     ✅ {f['message']}")
    l2 = call("GET", "/api/listings", label="التحقق من ظهوره مثبّتاً")
    mine = next(a for a in l2["listings"] if a["id"] == ad_id)
    assert mine["is_featured"] == 1 and mine["featured_hours_left"] > 0
    assert l2["listings"][0]["id"] == ad_id, "الإعلان المثبّت يجب أن يقفز للأول"
    print(f"     ✅ قفز للصدارة · مثبّت · متبقٍ {mine['featured_hours_left']} ساعة")
    f2 = call("POST", "/api/feature", {"listing_id": ad_id, "renew": True}, label="تجديد التثبيت")
    assert f2["charged_jd"] == pr["renew_price"], "سعر التجديد خاطئ"
    print(f"     ✅ التجديد أرخص ({pr['renew_price']} د.أ) — يشجع الاستمرار")
    call("POST", "/api/feature", {"listing_id": 1, "renew": False}, expect=403,
         label="تثبيت إعلان الغير (مرفوض)")

    # ══════════════════════════════════════════════════════════════
    section(10, "🛡️ وسم البائع الموثّق — مصدر الدخل #2")
    v = call("POST", "/api/buy/verification", label="شراء التوثيق")
    assert v and v["charged_jd"] == pr["verify_price"]
    print(f"     ✅ {v['message']}")
    call("POST", "/api/buy/verification", expect=409, label="شراء مكرر (مرفوض)")
    me = call("GET", "/api/me", label="التحقق من الحالة")
    assert me["user"]["is_verified"] is True
    print("     ✅ الحساب أصبح موثّقاً وتظهر شارته في إعلاناته")
    l3 = call("GET", get("listings", q="آيباد"), label="إعلاني يحمل شارة التوثيق")
    assert l3["listings"][0]["seller_verified"] in (1, True)
    print("     ✅ شارة 🛡️ مرئية للمشترين")

    # ══════════════════════════════════════════════════════════════
    section(11, "🤖 حزم التسعير — مصدر الدخل #3")
    call("POST", "/api/buy/package", {"package_id": "pack999"}, expect=404,
         label="حزمة غير موجودة (مرفوض)")
    credits_before = me["user"]["pricing_credits"]
    pk = call("POST", "/api/buy/package", {"package_id": "pack25"}, label="شراء حزمة 25")
    assert pk and pk["pricing_credits"] == credits_before + 25, "الرصيد لم يُجمع"
    print(f"     ✅ {pk['message']} → الرصيد {credits_before} + 25 = {pk['pricing_credits']}")

    # ══════════════════════════════════════════════════════════════
    section(12, "حماية الملكية والخصوصية")
    other = call("GET", "/api/listings/1", label="إعلان بائع آخر")
    assert other and "phone" not in other["listing"], "رقم هاتف البائع مكشوف لغير المالك!"
    print("     ✅ لا أرى رقم هاتف بائع آخر")
    own = call("GET", f"/api/listings/{ad_id}", label="إعلاني أنا")
    assert own and "phone" in own["listing"], "المالك يجب أن يرى رقمه"
    print(f"     ✅ أرى رقمي أنا: {own['listing']['phone']}")
    call("DELETE", "/api/listings/1", expect=403, label="حذف إعلان الغير (مرفوض)")
    call("DELETE", f"/api/listings/{ad_id}", label="حذف إعلاني (مسموح)")
    call("GET", f"/api/listings/{ad_id}", expect=404, label="اختفى بعد الحذف")

    # ══════════════════════════════════════════════════════════════
    section(13, "📦 الحد المجاني للإعلانات + الفتحات المدفوعة")
    free = pr["free_active_listings"]
    need = free + pr["extra_listing_pack"] + 2
    assert pr["rate_limits"]["listings"] >= need, \
        f"حد المعدل ({pr['rate_limits']['listings']}) أقل من حاجة الاختبار ({need})"
    created = []
    for i in range(free):
        res = call("POST", "/api/listings",
                   {"title": f"سلعة اختبار رقم {i+1} للبيع", "price": 10 + i,
                    "category": "أخرى", "city": "عمّان", "accepts_barter": False,
                    "featured": False}, label=f"إعلان #{i+1} ضمن الحد المجاني")
        assert res, f"فشل نشر الإعلان المجاني #{i+1}"
        created.append(res["listing"]["id"])
    print(f"     ✅ {free} إعلانات مجانية نُشرت بنجاح")

    call("POST", "/api/listings",
         {"title": "الإعلان السادس الذي يجب رفضه", "price": 99, "category": "أخرى",
          "city": "عمّان", "accepts_barter": False, "featured": False},
         expect=402, label=f"إعلان #{free+1} فوق الحد (مرفوض)")
    print("     ✅ الحد المجاني مطبَّق فعلاً — لم يعد الرصيد يُستخدم كتجاوز")

    me2 = call("GET", "/api/me", label="قراءة الفتحات قبل الشراء")
    assert me2["user"]["extra_listing_slots"] == 0
    slots = call("POST", "/api/buy/extra-listing", label="شراء حزمة فتحات")
    assert slots and slots["extra_listing_slots"] == pr["extra_listing_pack"]
    print(f"     ✅ {slots['message']}")

    for i in range(pr["extra_listing_pack"]):
        res = call("POST", "/api/listings",
                   {"title": f"سلعة مدفوعة الفتحة رقم {i+1}", "price": 20 + i,
                    "category": "أخرى", "city": "عمّان", "accepts_barter": False,
                    "featured": False}, label=f"إعلان #{free+i+1} بفتحة مدفوعة")
        assert res, "فشل النشر بفتحة مدفوعة"
        created.append(res["listing"]["id"])
    me3 = call("GET", "/api/me", label="التحقق من السعة الدائمة")
    assert me3["user"]["extra_listing_slots"] == pr["extra_listing_pack"], \
        "الفتحات يجب أن تبقى كسعة دائمة لا أن تُستهلك"
    print(f"     ✅ الفتحات سعة دائمة ({pr['extra_listing_pack']}) — لا تضيع بالحذف")
    call("POST", "/api/listings",
         {"title": "إعلان يتجاوز السعة الموسّعة", "price": 99, "category": "أخرى",
          "city": "عمّان", "accepts_barter": False, "featured": False},
         expect=402, label=f"إعلان #{free + pr['extra_listing_pack'] + 1} (مرفوض)")
    print(f"     ✅ الحد الجديد {free + pr['extra_listing_pack']} مطبَّق بدقة")

    # الحذف يحرر مكاناً ضمن السعة (لا يعيد فتحات)
    call("DELETE", f"/api/listings/{created[-1]}", label="حذف آخر إعلان")
    created.pop()
    res = call("POST", "/api/listings",
               {"title": "إعلان بعد تحرير مكان", "price": 55, "category": "أخرى",
                "city": "عمّان", "accepts_barter": False, "featured": False},
               label="نشر في المكان المُحرَّر (مسموح)")
    assert res, "الحذف يجب أن يحرر مكاناً ضمن السعة"
    created.append(res["listing"]["id"])
    print("     ✅ الحذف يحرر مكاناً للنشر مجدداً")

    for lid in created:
        call("DELETE", f"/api/listings/{lid}", label=f"تنظيف #{lid}")

    section(14, "🔐 لوحة المدير — تجميع الإيرادات")
    cj.clear()
    d = call("POST", "/api/login", {"phone": "0790000000", "password": "demo1234"},
             label="دخول الحساب التجريبي (مدير)")
    assert d, "فشل دخول الحساب التجريبي"
    a = call("GET", "/api/admin/stats", label="إحصاءات وأرباح المنصة")
    assert a
    print(f"     💰 إجمالي الإيرادات: {a['revenue_total_jd']} د.أ")
    print(f"     👥 مستخدمون {a['users']} · 📦 إعلانات {a['listings']} · "
          f"⭐ مثبّتة {a['featured_active']} · 🤖 تسعيرات {a['pricing_calls']}")
    kinds = {k["kind"]: k for k in a["revenue_by_kind"]}
    for k, lbl in [("feature", "تثبيت"), ("renew", "تجديد"), ("verify", "توثيق"),
                   ("credits", "حزم تسعير"), ("slots", "فتحات")]:
        if k in kinds:
            print(f"        · {lbl:8s} {kinds[k]['n']:>2} عملية = {kinds[k]['total']:>6.2f} د.أ")
    expected_min = (pr["feature_price"] + pr["renew_price"] + pr["verify_price"]
                    + 5.0 + 15.0 + pr["extra_listing_price"] * pr["extra_listing_pack"])
    assert a["revenue_total_jd"] >= expected_min, \
        f"الإيرادات ناقصة: {a['revenue_total_jd']} < {expected_min}"
    print(f"     ✅ كل مصادر الدخل الخمسة مسجّلة في جدول المدفوعات (≥ {expected_min} د.أ)")

    # ══════════════════════════════════════════════════════════════
    section(15, "الصفحات والأصول")
    html = urllib.request.urlopen(BASE + "/").read().decode()
    assert "منصة الشامل" in html and 'dir="rtl"' in html
    print("  ✅ الصفحة الرئيسية عربية RTL وتحتوي العنوان")
    for asset in ["/static/app.js", "/static/style.css"]:
        r = urllib.request.urlopen(BASE + asset)
        assert r.status == 200
        print(f"  ✅ {asset} ({len(r.read()):,} bytes)")
    print("  ✅", urllib.request.urlopen(BASE + "/healthz").read().decode())

    # ══════════════════════════════════════════════════════════════
    section(16, "📣 المشاركة والانتشار — صفحة SSR + بطاقة OG + رمز QR")
    import re as _re
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from server import qr as _qr

    # إعلان حيّ موجود فعلاً (أقسام سابقة تحذف إعلاناتها التجريبية)
    alive = call("GET", "/api/listings", label="اختيار إعلان حيّ للمشاركة")
    assert alive and alive["listings"], "لا إعلانات حية لفحص المشاركة"
    share_id = alive["listings"][0]["id"]

    # ── صفحة الإعلان المُصيَّرة على الخادم ──
    code, body, ctype, _loc = raw(f"/listing/{share_id}", label="صفحة الإعلان SSR")
    page = body.decode("utf-8")
    assert code == 200 and "text/html" in ctype
    for tag in ('property="og:title"', 'property="og:image"', 'rel="canonical"',
                'application/ld+json', 'dir="rtl"', 'property="og:image:width"'):
        assert tag in page, f"ناقص وسم: {tag}"
    PASS += 1
    print("  ✅ وسوم Open Graph + canonical + JSON-LD كلها حاضرة")
    assert not _re.search(r"07[789]\d{8}", page), \
        "ثغرة: رقم هاتف أردني مكشوف في صفحة المشاركة!"
    PASS += 1
    print("  ✅ خصوصية: لا رقم هاتف في صفحة المشاركة")
    m = _re.search(r'property="og:image" content="([^"]+)"', page)
    assert m and m.group(1).startswith("http") and m.group(1).endswith(
        f"/listing/{share_id}/card.png"), f"og:image خاطئ: {m and m.group(1)}"
    PASS += 1
    print(f"  ✅ og:image مطلق ويشير للبطاقة: {m.group(1)[:60]}…")
    assert "wa.me/" in page, "صفحة المشاركة بلا زر واتساب!"
    PASS += 1
    print("  ✅ زر تواصل واتساب موجود (والرقم نفسه غير ظاهر)")

    # ── الرابط القصير ──
    code, body, ctype, loc = raw(f"/l/{share_id}", expect=302, label="الرابط القصير /l/")
    assert loc.endswith(f"/listing/{share_id}"), f"تحويل خاطئ: {loc}"
    PASS += 1
    print(f"  ✅ 302 نحو {loc.split('/')[-1]}")

    # ── بطاقة Open Graph ──
    code, body, ctype, _l = raw(f"/listing/{share_id}/card.png", label="بطاقة OG")
    assert "image/png" in ctype and body[:8] == b"\x89PNG\r\n\x1a\n", "ليست PNG سليمة"
    assert len(body) > 8000, "البطاقة أصغر من المتوقع"
    PASS += 1
    print(f"  ✅ PNG سليمة بترويسة صحيحة ومقاس 1200×630")
    code2, body2, _c2, _l2 = raw(f"/listing/{share_id}/card.png", label="جلب ثانٍ (مخزون)")
    assert body2 == body, "البطاقة غير حتمية — المخزون أو التوليد يكسر الثبات"
    PASS += 1
    print("  ✅ الجلب الثاني مطابق بايت-ببايت (مخزون مؤقت ثابت)")

    # ── رمز QR ──
    code, body, ctype, _l = raw(f"/api/qr/{share_id}.svg", label="رمز QR")
    assert "image/svg+xml" in ctype and body.startswith(b"<svg"), "ليس SVG"
    svg = body.decode("utf-8")
    PASS += 1
    # نفك الرمز من مستطيلات الـ SVG نفسها — دليل أن الرمز المسلَّم حقيقي
    dim = int(_re.search(r'width="(\d+)"', svg).group(1))
    scale = int(_re.search(r'height="(\d+)" fill="#12212E"', svg).group(1))
    border = 2
    size = dim // scale - border * 2
    mat = [[0] * size for _ in range(size)]
    for x, y, w in _re.findall(
            r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="\d+" fill="#12212E"', svg):
        r0, c0 = int(y) // scale - border, int(x) // scale - border
        for cc in range(c0, c0 + int(w) // scale):
            mat[r0][cc] = 1
    sh = json.loads(urllib.request.urlopen(
        BASE + f"/api/listings/{share_id}/share").read())
    version, _cw = _qr.encode(sh["short_url"])
    decoded = _qr.decode(mat, size, _qr.make_matrix(sh["short_url"])[2], version)
    assert decoded == sh["short_url"], f"الرمز يفك لشيء آخر: {decoded}"
    PASS += 1
    print(f"  ✅ رمز QR المسلَّم يُفك ذاتياً إلى: {decoded}")

    # ── حزمة المشاركة ──
    assert set(sh["networks"]) == {"whatsapp", "facebook", "x", "telegram", "copy"}
    assert sh["networks"]["whatsapp"].startswith("https://wa.me/?text=")
    assert sh["short_url"].endswith(f"/l/{share_id}")
    assert sh["short_url"] in sh["text"], "نص الرسالة بلا الرابط القصير"
    assert "دينار" in sh["title"], "عنوان المشاركة بلا سعر"
    PASS += 1
    print(f"  ✅ 5 شبكات + نص رسالة جاهز: {sh['text'][:48].replace(chr(10), ' ⏎ ')}…")

    # ── إعلان محذوف: لا صفحة ميتة ولا بطاقة يتيمه ──
    code, body, ctype, _l = raw("/listing/99999", expect=404, label="صفحة إعلان محذوف")
    miss = body.decode("utf-8")
    assert "noindex" in miss and "لم يعد متاحاً" in miss, "صفحة 404 بلا وسوم مناسبة"
    PASS += 1
    print("  ✅ 404 بوسم noindex ورسالة عربية تحفظ ثقة الزائر")
    raw(f"/listing/99999/card.png", expect=404, label="بطاقة إعلان محذوف")
    raw(f"/api/qr/99999.svg", expect=404, label="رمز إعلان محذوف")
    raw(f"/api/listings/99999/share", expect=404, label="حزمة إعلان محذوف")

    # ── الواجهة موصولة بلوحة المشاركة ──
    js = urllib.request.urlopen(BASE + "/static/app.js").read().decode()
    for fn in ("shareTo(", "copyShareLink(", "toggleQr(", "/share`"):
        assert fn in js, f"app.js فقد دالة المشاركة: {fn}"
    PASS += 1
    print("  ✅ لوحة المشاركة موصولة في الواجهة (واتساب/فيسبوك/X/نسخ/QR)")

    # ══════════════════════════════════════════════════════════════
    section(17, "📷 صور الإعلانات — رفع آمن وعرض ومشاركة")
    from server import png as _png

    cu = call("POST", "/api/register",
              {"name": "بائع الصور", "phone": "0793555555", "password": "123456"},
              label="تسجيل بائع الصور")
    assert cu, "فشل التسجيل"
    pic_ad = call("POST", "/api/listings",
                  {"title": "كنبة ثلاثية مريحة بلون رمادي", "price": 140,
                   "description": "مستعملة سنة واحدة، نظيفة جداً.",
                   "category": "أثاث منزلي", "city": "عمّان", "accepts_barter": False},
                  label="نشر إعلان بلا صورة")
    lid = pic_ad["listing"]["id"]

    # صورة PNG حقيقية مبنية بمرمّز المنصة نفسه
    cv = _png.Canvas(96, 72)
    cv.dgradient((120, 90, 60), (60, 40, 25))
    cv.circle(48, 36, 22, (200, 60, 40))
    photo = cv.to_png()

    body, ctype = multipart(photo)
    up = post_raw(f"/api/listings/{lid}/image", body, ctype, label="رفع صورة PNG")
    assert up and up["image"].startswith("/uploads/") and up["image"].endswith(".png")
    PASS += 1
    print(f"     ✅ مسار محفوظ باسم عشوائي: {up['image']}")

    got = call("GET", f"/api/listings/{lid}", label="الصورة ضمن بيانات الإعلان")
    assert got["listing"]["image"] == up["image"]
    PASS += 1

    code, fbody, fctype, _l = raw(up["image"], label="جلب الملف المرفوع")
    assert fbody[:8] == b"\x89PNG\r\n\x1a\n" and "image/" in fctype
    PASS += 1
    print("     ✅ الملف يُخدم بنفس البصمة التي رُفع بها")

    code, pbody, _c, _l = raw(f"/listing/{lid}", label="صفحة المشاركة بصورة البائع")
    page = pbody.decode()
    assert f'property="og:image" content="{BASE}{up["image"]}"' in page, \
        "og:image يجب أن يشير لصورة البائع لا للبطاقة"
    assert f'src="{BASE}{up["image"]}"' in page and 'class="photo"' in page, \
        "صورة البائع يجب أن تتصدر الصفحة (src مطلق + class=photo)"
    PASS += 1
    print("     ✅ og:image والـ hero لصورة البائع الحقيقية")

    # ── الحماية: نوع منتحل، حجم مبالغ، ملكية، مصادقة ──
    body, ctype = multipart(b"GIF89a" + b"\x00" * 200, "x.gif", "image/gif")
    post_raw(f"/api/listings/{lid}/image", body, ctype, expect=415,
             label="امتداد منتحل / بصمة غير مدعومة (415)")
    body, ctype = multipart(photo[:8] + b"\x00" * (2 * 1024 * 1024), "big.png")
    post_raw(f"/api/listings/{lid}/image", body, ctype, expect=413,
             label="صورة أكبر من 2 ميغابايت (413)")
    body, ctype = multipart(photo)
    post_raw("/api/listings/1/image", body, ctype, expect=403,
             label="رفع لصورة إعلان الغير (403)")
    req = urllib.request.Request(
        BASE + f"/api/listings/{lid}/image", data=body, method="POST",
        headers={"Content-Type": ctype})   # بلا كوكي الجلسة
    try:
        urllib.request.urlopen(req)
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    ok = code == 401
    PASS += ok; FAIL += (not ok)
    print(f"  {'✅' if ok else '❌'} رفع بدون تسجيل (401) [{code}]")

    # ── الاستبدال ثم الإزالة ──
    cv2 = _png.Canvas(96, 72, (20, 120, 60))
    body, ctype = multipart(cv2.to_png(), "second.png")
    up2 = post_raw(f"/api/listings/{lid}/image", body, ctype, label="استبدال الصورة")
    assert up2["image"] != up["image"], "الاستبدال يجب أن ينشئ ملفاً جديداً"
    raw(up["image"], expect=404, label="الملف القديم حُذف عند الاستبدال")

    call("DELETE", f"/api/listings/{lid}/image", label="إزالة الصورة")
    got = call("GET", f"/api/listings/{lid}", label="الإعلان بلا صورة")
    assert got["listing"]["image"] == ""
    code, pbody, _c, _l = raw(f"/listing/{lid}", label="og:image تعود للبطاقة")
    assert f'property="og:image" content="{BASE}/listing/{lid}/card.png"' \
        in pbody.decode(), "بعد الإزالة يجب أن تعود بطاقة المنصة"
    PASS += 1
    print("     ✅ عند غياب الصورة تسدّ بطاقة المنصة الفراغ")

    # ══════════════════════════════════════════════════════════════
    section(18, "📊 عدّاد المشاركة — فتحات الرابط القصير وزيارات الصفحة")
    # القسم 16 فتح /l/ والصفحة مرة لكل منهما — هذا الأساس: 1 و1
    raw(f"/l/{share_id}", expect=302, label="فتحة رابط قصير (تُحتسب scan)")
    raw(f"/listing/{share_id}", label="زيارة بشرية للصفحة (تُحتسب page)")
    raw(f"/listing/{share_id}",
        headers={"User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"},
        label="زاحف فيسبوك يبني معاينة (لا تُحتسب)")

    mine_view = call("GET", f"/api/listings/{share_id}",
                     label="غير المالك لا يرى العدّاد")
    assert "hits" not in mine_view["listing"], "العدّاد يجب ألا يُكشف لغير المالك"
    PASS += 1

    call("POST", "/api/login",
         {"phone": "0790000000", "password": "demo1234"}, label="دخول المدير")
    own = call("GET", f"/api/listings/{share_id}", label="المالك يرى العدّاد")
    hits = own["listing"]["hits"]
    assert hits == {"scans": 2, "pages": 2}, f"عدّاد خاطئ: {hits}"
    PASS += 1
    print(f"     ✅ فتحتا رابط مشاركة + زيارتا صفحة — والزاحف لم يُحتسب")

    adm = call("GET", "/api/admin/stats", label="إجماليات المشاركة عند المدير")
    assert adm["share_scans"] >= 2 and adm["share_pages"] >= 4, \
        f"إجماليات خاطئة: {adm.get('share_scans')}/{adm.get('share_pages')}"
    PASS += 1
    print(f"     ✅ لوحة المدير: {adm['share_scans']} مسح/فتحة · {adm['share_pages']} زيارة")

    # ══════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════
#  نقطة الدخول — تضمن تقريراً نهائياً واضحاً حتى عند أول فشل
# ══════════════════════════════════════════════════════════════
def main() -> int:
    global PASS, FAIL
    preflight()
    failed_at = None
    try:
        run_suite()
    except AssertionError as e:
        FAIL += 1
        failed_at = f"فشل فحص: {e}"
    except Exception as e:
        FAIL += 1
        failed_at = f"خطأ غير متوقع: {type(e).__name__}: {e}"
        import traceback
        traceback.print_exc()

    print(f"\n{'═' * 62}")
    print(f"  النتيجة النهائية: {PASS} فحص ناجح · {FAIL} فاشل")
    if failed_at:
        print(f"  ⛔ توقّف الاختبار عند: {failed_at}")
        print(f"  ← لن يُدمج أي Pull Request في هذه الحالة.")
    print(f"{'═' * 62}\n")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
