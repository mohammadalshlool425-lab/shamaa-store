# شمعة · SHAM’A

سوق أردني متعدد البائعين، بواجهة **عربية/إنجليزية** وRTL/LTR، وعملة **JOD**، ودفع عند الاستلام في المرحلة الأولى.

**مشروع يعمل بقاعدة بيانات فعلية، وليس قالب عرض.** الحسابات والمتاجر والمنتجات والسلة والطلبات تُحفظ في SQLite. كتالوج العرض اختياري وموسوم بأنه توضيحي؛ لا حساب مدير افتراضي، ولا شارات ثقة مدفوعة، ولا دفع إلكتروني محاكى.

> **الحالة:** نُفّذ الأساس التقني للمرحلة الأولى واختُبرت تدفقاته محليًا. ليست هذه شهادة أمان/إتاحة أو إعلان نشر إنتاجي. SMTP حقيقي، النطاق وHTTPS، إعداد الجدولة والنسخة الخارجية، والمراجعات القانونية/التشغيلية تتطلب إعدادًا قبل استقبال عملاء حقيقيين. [نتائج التحقق وحدودها](docs/VERIFICATION.md).

## ابدأ من هنا

- **للمبتدئ، Windows والهاتف:** [خطوات مصوغة خطوة بخطوة](docs/BEGINNER_AR.md).
- **ما نُفّذ وما أُجّل:** [المراحل ومعايير القبول](docs/PHASES.md).
- **للمطوّر:** [البنية والبيانات والصلاحيات](docs/ARCHITECTURE.md).
- **للنشر والنسخ والاستعادة:** [دليل التشغيل](docs/OPERATIONS.md).
- **التكاليف والخدمات الخارجية:** [ميزانية البداية وحدود المجاني](docs/COSTS.md).
- **الخصوصية والأمان قبل الإطلاق:** [قائمة المراجعة](docs/SECURITY_PRIVACY.md).

## تشغيل سريع — Linux / macOS

المتطلبات: **Python 3.11 أو أحدث**؛ بيئة التحقق الحالية Python 3.11 على Linux. Node 22 مطلوب لفحص JavaScript واختبارات المتصفح فقط، **وليس لتشغيل المتجر**. استعمل إصدار نظام ومفسّر بتحديثات الأمان الحالية.

من داخل مجلد المشروع:

```bash
python3 -m venv .venv
# Linux + Python 3.11: قفل شامل مع بصمات الحزم
.venv/bin/python -m pip install --require-hashes -r requirements.lock
# على macOS أو Python مختلف: استخدم requirements.txt ثم أعد الاختبارات
# .venv/bin/python -m pip install -r requirements.txt
```

إذا لم يكن لديك `.env` محلي، انسخ `.env.example` إليه، **ولا تستبدل ملف إعداداتك الموجود**. الإعداد الافتراضي تطوير محلي وبريد ملفات وكتالوج فارغ. لتجربة الشكل بالمنتجات التوضيحية، غيّر داخل `.env`:

```dotenv
SHAMAA_DEMO_DATA=1
```

ثم شغّل:

```bash
.venv/bin/python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --no-access-log
```

افتح **http://localhost:8000**. اترك الطرفية مفتوحة؛ `Ctrl+C` يوقف الخادم. جميع طلبات الواجهة إلى `/api/...` على النطاق نفسه؛ لا عناوين localhost مزروعة في كود المتصفح.

**البيانات:** `data/marketplace.sqlite3` والصور الخاصة داخل `data/media/`. لا تستخدم قاعدة النسخة القديمة بدلها؛ مخطط هذه النسخة مختلف ولا يوجد ترحيل آلي للبيانات القديمة. احتفظ بنسخة منها أولًا. لا تُرفع `data/` أو `.env` أو مفاتيحك إلى Git.

### إنشاء حساب وتأكيد البريد محليًا

1. اختر «دخول / تسجيل» ثم «إنشاء حساب» في الموقع.
2. في وضع `file`، الرسالة محفوظة خصوصيًا وليست مرسلة إلى صندوق بريدك. في طرفية ثانية:

   ```bash
   .venv/bin/python -m server.cli dev-mail
   ```

3. أدخل بريد الحساب لعرض أحدث رسالة محلية، وافتح رابطها ثم اضغط تأكيد البريد. لا تنشر الرابط/الرسالة. إن لم تُكتب الرسالة بعد، شغّل `mail-worker --once`.
4. الحساب المؤكد يستطيع فتح متجر وإرساله للمراجعة. تأكيد البريد **ليس** توثيق هوية أو ترخيصًا تجاريًا.

### إنشاء مدير حقيقي

من طرفية خاصة يملكها مشغّل الخادم، ببريد منفصل غير مسجّل مسبقًا:

```bash
.venv/bin/python -m server.cli create-admin
```

يسألك الأمر عن الاسم والبريد وكلمة مرور تختارها أنت، ثم ينشئ مفتاح إعداد/QR خاصًا لتطبيق مصادقة TOTP ويطلب الرمز الحالي. لا تُنشأ الإدارة دون إتمام المصادقة. احتفظ بمفتاح الاستعادة في خزنة خاصة، ولا تصوّر شاشة الإعداد أو ترسله في المحادثة. بعد الدخول بالحساب وكلمة المرور **ورمز المصادقة** تظهر لوحة الإدارة؛ راجع المتاجر ثم المنتجات منها.

## اختبارات قابلة للإعادة

على Linux / Python 3.11:

```bash
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
npm ci
bash tests/run.sh

# Chromium العادي: يتطلب تنزيل المتصفح أول مرة
npx playwright install chromium
npm run test:browser

# بديل Linux x86_64 من حزم npm فقط؛ هو المستخدم في بيئة التحقق هنا
SHAMAA_BUNDLED_BROWSER=1 npm run test:browser -- --reporter=line
```

اختبارات الخادم تستخدم قواعد مؤقتة. اختبارات المتصفح تدير خادمًا معزولًا على **8011** وتعيد إنشاء **`data/browser-tests/` فقط**. هذا المجلد خاص بالاختبار ويُحذف عند بدئه؛ لا تحفظ فيه أي شيء. أوقف أي نسخة اختبار قديمة على 8011 قبل إعادة المجموعة كاملة. لا تلمس الاختبارات قاعدة المتجر على 8000.

```bash
.venv/bin/python -m pip install -r requirements-tools.txt
.venv/bin/pip-audit --require-hashes -r requirements-dev.lock --progress-spinner off
npm audit --audit-level=moderate
```

ملفات `requirements*.lock` مولّدة ومُتحقَّق منها على Linux/Python 3.11، وتتضمن `uvloop` دون شرط Windows؛ **لا تستخدمها على Windows**. تعليمات Windows تستخدم الملفات المباشرة مع إعادة الاختبارات. تدقيق الحزم ليس اختبار اختراق ولا يغطي نظام التشغيل.

ملف `.github/workflows/tests.yml` يهيئ فحوص الخادم والمتصفح والتدقيق عند الدفع/طلب السحب، مع حد تغطية 90%. لا ننسب إليه تشغيلًا ناجحًا على GitHub قبل رؤية نتيجة فعلية هناك. لا تُرفع قواعد أو كلمات مرور أو رسائل أو traces كـ artifacts.

## حدود النسخة الأولى المهمة

- منتجات **مادية جديدة** فقط؛ حتى 10 منتجات لكل متجر في البداية، وصورة واحدة للمنتج، ومخزون إجمالي بلا متغيرات.
- متجر واحد مستقل لكل بائع، ومراجعة إدارية قبل النشر، وشحن ثابت لكل متجر داخل الأردن. لا ربط بناقل ولا حجز توصيل حقيقي.
- الطلب يُقسّم إلى شحنات البائعين ويخصم المخزون ذريًا، مع مفتاح منع تكرار وسجل حالات. السلة ليست حجز مخزون.
- عمولة افتراضية 5% قابلة للتعديل؛ المتجر ثم الفئة ثم الفئة الأم ثم العام. تحفظ الطلبات نسبة إنشائها. الخصومات حاليًا سعر مخفّض يدخله البائع؛ لا كوبونات بعد.
- الدفع **COD فقط**. عمولة الشحنة المسلّمة تُسجّل كمستحق للمنصة، **لا كأموال محصّلة أو رصيد قابل للسحب**. لا محفظة ولا تحويلات ولا صرف آلي أو إثبات قبض.
- PWA للتثبيت والتصفح العام دون اتصال عند توفر HTTPS/localhost. الحساب والطلبات ووسائط البائعين ليست مخزنة في Service Worker، ولا توجد قائمة عمليات مالية/مخزون مؤجلة للعمل دون إنترنت.
- نسخ مشفرة واستعادة مختبرة، وأداة تدوير وقالب جدولة يومية؛ **لم تُفعّل جدولة إنتاج أو نسخة خارجية نيابة عنك**.
- SQLite على قرص دائم وخادم واحد. ليست نسخة متعددة الخوادم، ولا تحويل إلى PostgreSQL بمجرد تغيير رابط.
- سياسات الاستخدام/الخصوصية في الواجهة مسودات توضيحية؛ لا تُعدّ مراجعة قانونية أردنية أو فواتير رسمية.

## خريطة الملفات

```text
server/application.py      مصنع التطبيق، الحماية، الوسائط، صفحات الواجهة وSEO الأساسي
server/main.py             مدخل Uvicorn (main.py وapp.py للتوافق)
server/schema.sql          مخطط v1 وقيود البيانات والسجلات append-only
server/routes/             auth / catalog / seller / orders / admin
server/commerce.py         الفلس، السلة، أولوية العمولة وحسابها
server/security.py         scrypt، الجلسات، MFA، الأدوار والتدقيق
server/mail.py             صندوق إرسال معاملاتي، ملفات تطوير أو SMTP/TLS
server/backup.py           AES-GCM، اللقطة المتسقة، الاستعادة وتدوير النسخ
server/cli.py              أدوات المدير والبريد والنسخ
static/                    واجهتان، خطوط وصور محلية، manifest وService Worker
scripts/browser_test_server.py  خادم وبيانات اختبار مولّدة ومعزولة
tests/                     اختبارات الخادم والمتصفح والإتاحة وoffline
deploy/systemd/            قوالب تشغيل وبريد وجدولة نسخ؛ تحتاج إعدادًا فعليًا
docs/                      القرارات والمراحل والتشغيل والتكاليف والتحقق
```

استُبدلت وحدات التسعير المفترض والدفع المحاكى وبيع شارات التوثيق والمشاركة القديمة؛ تاريخ Git يحتفظ بالنسخة السابقة، لكن واجهاتها ليست وظائف مفعّلة في هذه النسخة. [نموذج الربح الصادق الحالي](docs/افكار-الربح.md).

---

### English overview

SHAM’A is a Jordan-first, bilingual Arabic/English multi-vendor marketplace. Phase one uses FastAPI, same-origin vanilla JavaScript, persistent SQLite, integer fils, atomic seller-split orders and COD. There is **no default administrator, simulated online payment, paid verification badge, or automatic payout**. Local illustrative data is opt-in. Use Python 3.11 and the Linux hashed locks for the tested environment; see the Arabic beginner guide for Windows/phone setup. Tests and deployment templates are provided, but production deployment, real SMTP delivery, scheduled offsite backups, legal review and a complete accessibility/security audit have not been performed on an external production service.
