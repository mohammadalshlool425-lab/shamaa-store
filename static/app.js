/* ═══════════════════════════════════════════════════════════
   منصة الشامل الذكية — واجهة المستخدم
   SPA عربية (RTL) بدون أي اعتماديات خارجية
   ═══════════════════════════════════════════════════════════ */

const S = {
  boot: null,
  user: null,
  listings: [],
  totals: {},
  catCounts: {},
  filters: { q: "", category: "الكل", city: "كل الأردن", barter: false, sort: "recent" },
  lastPricing: null,
  view: "home",
  stats: null,
};

const CAT_EMOJI = {
  "إلكترونيات": "📱", "أجهزة كهربائية": "🔌", "أثاث منزلي": "🛋️", "سيارات": "🚗",
  "رياضة وترفيه": "🚴", "ملابس وأزياء": "👔", "أطفال وألعاب": "🧸", "عقارات": "🏠",
  "أخرى": "📦",
};
const CAT_CLASS = {
  "إلكترونيات": "th-إلكترونيات", "أجهزة كهربائية": "th-أجهزة", "أثاث منزلي": "th-أثاث",
  "سيارات": "th-سيارات", "رياضة وترفيه": "th-رياضة", "ملابس وأزياء": "th-ملابس",
  "أطفال وألعاب": "th-أطفال", "عقارات": "th-عقارات", "أخرى": "th-أخرى",
};

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const jd = (n) => `${Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;

/* ─────────────── API ─────────────── */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* استجابة فارغة */ }
  if (!res.ok) {
    const err = new Error(typeof data === "string" ? data : (data.message || "حدث خطأ غير متوقع"));
    err.status = res.status;
    err.payload = data;
    throw err;
  }
  return data;
}

/* ─────────────── التنبيهات ─────────────── */
function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = ".3s"; }, 3400);
  setTimeout(() => el.remove(), 3800);
}

/* ─────────────── النوافذ المنبثقة ─────────────── */
function modal(html, wide = false) {
  const root = $("#modalRoot");
  $(".modal", root).className = `modal${wide ? " wide" : ""}`;
  $("#modalBody").innerHTML = html;
  root.hidden = false;
  document.body.style.overflow = "hidden";
  const first = $("input,select,textarea", $("#modalBody"));
  if (first) setTimeout(() => first.focus(), 80);
}
function closeModal() {
  $("#modalRoot").hidden = true;
  $("#modalBody").innerHTML = "";
  document.body.style.overflow = "";
}
$("#modalRoot").addEventListener("click", (e) => {
  if (e.target.hasAttribute("data-close")) closeModal();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

/* ─────────────── التنقل بين الصفحات ─────────────── */
function go(view) {
  if ((view === "dashboard" || view === "admin") && !S.user) { openAuth("login", "لوحة التحكم تتطلب تسجيل الدخول"); return; }
  if (view === "admin" && (!S.user || !S.user.is_admin)) { toast("هذه الصفحة لمدير المنصة فقط", "err"); return; }
  S.view = view;
  ["home", "safety", "dashboard", "admin"].forEach(v => {
    $(`#view-${v}`).hidden = v !== view;
  });
  if (view === "home") renderHome();
  if (view === "safety") renderSafety();
  if (view === "dashboard") renderDashboard();
  if (view === "admin") renderAdmin();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ═══════════════════════════════════════════════════════════
   الصفحة الرئيسية
   ═══════════════════════════════════════════════════════════ */
async function loadListings() {
  const f = S.filters;
  const p = new URLSearchParams({
    q: f.q, category: f.category === "الكل" ? "" : f.category,
    city: f.city === "كل الأردن" ? "" : f.city,
    barter: f.barter ? "1" : "-1", sort: f.sort, limit: "80",
  });
  const data = await api(`/api/listings?${p}`);
  S.listings = data.listings;
  S.totals = data.totals;
  S.catCounts = data.category_counts;
}

function renderHome() {
  const cats = S.boot.categories;
  $("#view-home").innerHTML = `
    ${heroHTML()}
    <div class="layout">
      <aside class="sidebar">
        <div class="card">
          <h3>📂 الأقسام</h3>
          <div class="cat-list" id="catList"></div>
        </div>
        <div class="card">
          <h3>🎛️ تصفية النتائج</h3>
          <div class="field">
            <label>المدينة</label>
            <select id="fCity"></select>
          </div>
          <label class="switch ${S.filters.barter ? "on" : ""}" id="fBarter">
            <span class="sw-box">${S.filters.barter ? "✓" : ""}</span>
            <span class="switch-text"><strong>🔄 يقبل المقايضة فقط</strong><small>تبادل السلع بدون دفع نقدي</small></span>
          </label>
          <div class="field" style="margin-top:.9rem">
            <label>الترتيب</label>
            <select id="fSort">
              <option value="recent">الأحدث أولاً</option>
              <option value="price_asc">السعر: من الأقل للأعلى</option>
              <option value="price_desc">السعر: من الأعلى للأقل</option>
              <option value="popular">الأكثر مشاهدة</option>
            </select>
          </div>
          <button class="btn btn-outline btn-block btn-sm" id="fReset">↺ إعادة ضبط الفلاتر</button>
        </div>
        ${boostHTML()}
      </aside>
      <div>
        <div class="toolbar">
          <h2 id="resultsTitle">أحدث الإعلانات</h2>
          <div class="chips" id="sortChips"></div>
        </div>
        <div id="results"></div>
      </div>
    </div>`;

  // الأقسام
  $("#catList").innerHTML = [["الكل", S.totals.ads || 0], ...cats.map(c => [c, S.catCounts[c] || 0])]
    .map(([name, n]) => `
      <div class="cat-item ${S.filters.category === name ? "active" : ""}" data-cat="${esc(name)}">
        <span>${name === "الكل" ? "🌐" : CAT_EMOJI[name] || "📦"} ${esc(name)}</span>
        <span class="cat-count">${n}</span>
      </div>`).join("");
  $$("#catList .cat-item").forEach(el => el.onclick = () => {
    S.filters.category = el.dataset.cat; renderHome(); loadAndRenderResults();
  });

  // المدن
  $("#fCity").innerHTML = ["كل الأردن", ...S.boot.cities]
    .map(c => `<option ${S.filters.city === c ? "selected" : ""}>${esc(c)}</option>`).join("");
  $("#fCity").onchange = (e) => { S.filters.city = e.target.value; loadAndRenderResults(); };
  $("#fSort").value = S.filters.sort;
  $("#fSort").onchange = (e) => { S.filters.sort = e.target.value; loadAndRenderResults(); };
  $("#fBarter").onclick = () => { S.filters.barter = !S.filters.barter; renderHome(); loadAndRenderResults(); };
  $("#fReset").onclick = () => {
    S.filters = { q: "", category: "الكل", city: "كل الأردن", barter: false, sort: "recent" };
    $("#searchInput").value = ""; renderHome(); loadAndRenderResults();
  };

  loadAndRenderResults();
}

function heroHTML() {
  if (S.user) {
    return `<div class="hero">
      <h1>أهلاً ${esc(S.user.name)} 👋 <em>جاهز تبيع اليوم؟</em></h1>
      <p>لديك ${S.user.pricing_credits} تسعيرة ذكية في رصيدك. الإعلانات المثبّتة ⭐ تُشاهد بمعدل 4 أضعاف العادية.</p>
      <div class="hero-acts">
        <button class="btn btn-primary" onclick="openNewListing()">➕ أضف إعلاناً الآن</button>
        <button class="btn btn-star" onclick="openPricing()">🤖 احسب سعر سلعتي</button>
        <button class="btn btn-ghost" style="color:#fff" onclick="go('dashboard')">📊 لوحة التحكم</button>
      </div>
    </div>`;
  }
  return `<div class="hero">
    <h1>بيع واشترِ وقايض <em>بأذكى تسعير في الأردن</em></h1>
    <p>منصة مبوبة نظيفة وسريعة بدون إعلانات مزعجة — مع مساعد ذكاء اصطناعي يحسب لك السعر العادل قبل أن تنشر، ونظام توثيق يحمي حقوقك عند المقايضة.</p>
    <div class="hero-acts">
      <button class="btn btn-primary" onclick="openNewListing()">➕ أضف إعلانك مجاناً</button>
      <button class="btn btn-star" onclick="openPricing()">🤖 جرّب التسعير الذكي</button>
      <button class="btn btn-ghost" style="color:#fff;border-color:rgba(255,255,255,.25)" onclick="openAuth('login')">تسجيل الدخول</button>
    </div>
  </div>`;
}

function boostHTML() {
  return `<div class="card boost-card">
    <h3>⭐ هل تريد بيعاً أسرع؟</h3>
    <p>ثبّت إعلانك في أعلى نتائج البحث لمدة ${S.boot.pricing.feature_days} أيام.</p>
    <ul class="boost-list">
      <li>ظهور قبل كل الإعلانات في قسمك</li>
      <li>إطار ذهبي وشارة «مثبّت» تلفت الانتباه</li>
      <li>مشاهدات أكثر بمعدل 4 أضعاف</li>
      <li>رابط واتساب مباشر للمشتري</li>
    </ul>
    <div style="display:flex;align-items:center;gap:.6rem;margin-bottom:.7rem">
      <b style="font-size:1.5rem;color:#FFD86B">${S.boot.pricing.feature_price}</b>
      <span style="font-size:.8rem;opacity:.75">دينار / ${S.boot.pricing.feature_days} أيام<br>التجديد ${S.boot.pricing.renew_price} د.أ فقط</span>
    </div>
    <button class="btn btn-star btn-block" onclick="openBoost()">ثبّت إعلاني الآن</button>
  </div>`;
}

async function loadAndRenderResults() {
  const box = $("#results");
  if (!box) return;
  box.innerHTML = `<div class="empty"><span class="big">⏳</span>جارٍ تحميل الإعلانات…</div>`;
  try {
    await loadListings();
  } catch (e) {
    box.innerHTML = `<div class="empty"><span class="big">⚠️</span>${esc(e.message)}</div>`;
    return;
  }
  updateStats();
  const f = S.filters;
  $("#resultsTitle").textContent =
    f.category !== "الكل" ? `${CAT_EMOJI[f.category] || ""} ${f.category}` :
    f.q ? `نتائج البحث عن «${f.q}»` : "أحدث الإعلانات في الأردن";

  if (!S.listings.length) {
    box.innerHTML = `<div class="empty">
      <span class="big">🔍</span>
      <strong>لا توجد إعلانات مطابقة</strong>
      <p style="margin:.4rem 0 1rem;font-size:.88rem">جرّب توسيع البحث أو كن أول من ينشر في هذا القسم!</p>
      <button class="btn btn-primary" onclick="openNewListing()">➕ أضف إعلانك الآن</button>
    </div>`;
    return;
  }
  box.innerHTML = `<div class="grid">${S.listings.map(adCard).join("")}</div>`;
  $$("#results .ad").forEach(el => {
    el.onclick = (ev) => {
      if (ev.target.closest("[data-stop]")) return;
      openDetail(Number(el.dataset.id));
    };
  });
}

function adCard(a) {
  const emoji = CAT_EMOJI[a.category] || "📦";
  const cls = CAT_CLASS[a.category] || "th-أخرى";
  const thumb = a.image
    ? `<div class="ad-thumb photo"><img src="${esc(a.image)}" alt="${esc(a.title)}" loading="lazy"></div>`
    : `<div class="ad-thumb ${cls}">${emoji}</div>`;
  return `<article class="ad ${a.is_featured ? "featured" : ""}" data-id="${a.id}" style="cursor:pointer">
    ${thumb}
    <div class="ad-body">
      <h3 class="ad-title">${esc(a.title)}</h3>
      <div class="ad-price">${jd(a.price)} <small>${esc(S.boot.currency)}</small></div>
      <div class="ad-meta">
        <span class="tag tag-cat">${esc(a.category)}</span>
        <span class="tag">📍 ${esc(a.city)}</span>
        ${a.accepts_barter ? `<span class="tag tag-barter">🔄 مقايضة</span>` : ""}
        ${a.seller_verified ? `<span class="tag tag-verified">🛡️ موثّق</span>` : ""}
      </div>
      <div class="ad-actions">
        <a class="btn btn-green btn-sm" data-stop href="${esc(a.whatsapp_url)}" target="_blank" rel="noopener">💬 واتساب</a>
        <button class="btn btn-outline btn-sm" data-stop onclick="openDetail(${a.id})">التفاصيل</button>
      </div>
    </div>
    <div class="ad-foot">
      <span>👤 ${esc(a.seller_name)}</span>
      <span>👁 ${a.views} · ${esc(a.age_label)}</span>
    </div>
  </article>`;
}

function updateStats() {
  $("#statAds").textContent = jd(S.totals.ads || 0);
  $("#statBarter").textContent = jd(S.totals.barter || 0);
  $("#statSellers").textContent = jd(S.totals.sellers || 0);
}

/* ═══════════════════════════════════════════════════════════
   تفاصيل الإعلان
   ═══════════════════════════════════════════════════════════ */
async function openDetail(id) {
  try {
    const { listing: a } = await api(`/api/listings/${id}`);
    const emoji = CAT_EMOJI[a.category] || "📦";
    const cls = CAT_CLASS[a.category] || "th-أخرى";
    const mine = S.user && (S.user.id === a.user_id);
    modal(`
      ${a.image ? `<div class="detail-hero"><img src="${esc(a.image)}" alt="${esc(a.title)}"></div>` : ""}
      <div class="detail-head">
        <div class="detail-thumb ${cls}">${emoji}</div>
        <div style="flex:1;min-width:0">
          <h2>${a.is_featured ? "⭐ " : ""}${esc(a.title)}</h2>
          <div class="detail-price">${jd(a.price)} <small style="font-size:.9rem;color:var(--slate)">${esc(S.boot.currency)}</small></div>
          <div class="ad-meta" style="margin-top:.4rem">
            <span class="tag tag-cat">${esc(a.category)}</span>
            <span class="tag">📍 ${esc(a.city)}</span>
            ${a.accepts_barter ? `<span class="tag tag-barter">🔄 يقبل المقايضة</span>` : `<span class="tag">💵 بيع نقدي فقط</span>`}
          </div>
        </div>
      </div>
      <dl class="kv">
        <dt>البائع</dt><dd>${esc(a.seller_name)} ${a.seller_verified ? '<span class="tag tag-verified">🛡️ بائع موثّق</span>' : ""}</dd>
        <dt>عدد إعلاناته</dt><dd>${a.seller_ads}</dd>
        <dt>نُشر</dt><dd>${esc(a.age_label)} · ${a.views} مشاهدة</dd>
        ${a.is_featured ? `<dt>التثبيت</dt><dd>⭐ مثبّت — متبقٍ ${a.featured_hours_left} ساعة</dd>` : ""}
        ${mine && a.hits ? `<dt>أثر المشاركة</dt><dd>🔗 ${a.hits.scans} فتحة رابط مشاركة · 📄 ${a.hits.pages} زيارة للصفحة — كل واحدة فرصة بيع</dd>` : ""}
      </dl>
      <div class="desc">${esc(a.description || "لم يضف البائع وصفاً تفصيلياً.")}</div>
      <div class="pay-note">
        <span>🛡️</span>
        <span><strong>تذكير أمني:</strong> التَقِ البائع في مكان عام، عاين السلعة قبل الدفع، ووثّق أي اتفاق مقايضة برسالة واتساب مكتوبة.</span>
      </div>
      <div style="display:flex;gap:.55rem;flex-wrap:wrap;margin-top:.4rem">
        <a class="btn btn-green" style="flex:1" href="${esc(a.whatsapp_url)}" target="_blank" rel="noopener">💬 تواصل عبر واتساب</a>
        ${mine ? "" : `<button class="btn btn-outline" onclick="offerBarter(${a.id})">🔄 اعرض مقايضة</button>`}
        ${mine ? `<button class="btn btn-outline" onclick="pickImage(${a.id})">📷 ${a.image ? "تغيير الصورة" : "إضافة صورة"}</button>` : ""}
        ${mine && a.image ? `<button class="btn btn-outline" style="color:var(--red)" onclick="removeImage(${a.id})">🖼 إزالة</button>` : ""}
        ${mine ? `<button class="btn btn-star" onclick="closeModal();openBoost(${a.id})">⭐ ثبّت هذا الإعلان</button>
                 <button class="btn btn-outline" style="color:var(--red)" onclick="deleteListing(${a.id})">🗑 حذف</button>` : ""}
      </div>
      <div class="share-panel">
        <div class="share-title">📣 انشر الإعلان — كل مشاركة تقرّبه من البيع</div>
        <div class="share-row">
          <button class="btn btn-outline btn-sm" onclick="shareTo('whatsapp', ${a.id})">💬 واتساب</button>
          <button class="btn btn-outline btn-sm" onclick="shareTo('facebook', ${a.id})">📘 فيسبوك</button>
          <button class="btn btn-outline btn-sm" onclick="shareTo('x', ${a.id})">✖️ X</button>
          <button class="btn btn-outline btn-sm" onclick="copyShareLink(${a.id})">🔗 نسخ الرابط</button>
          <button class="btn btn-outline btn-sm" onclick="toggleQr(${a.id})">▦ رمز QR</button>
        </div>
        <div class="qr-box" id="qrBox" hidden></div>
      </div>
    `, true);
  } catch (e) { toast(e.message, "err"); }
}

/* ═══════════════════════════════════════════════════════════
   المشاركة والانتشار
   ═══════════════════════════════════════════════════════════ */
const SHARE_CACHE = {};
async function fetchShare(id) {
  if (!SHARE_CACHE[id]) SHARE_CACHE[id] = await api(`/api/listings/${id}/share`);
  return SHARE_CACHE[id];
}

async function shareTo(net, id) {
  try {
    const s = await fetchShare(id);
    window.open(s.networks[net], "_blank", "noopener");
  } catch (e) { toast(e.message, "err"); }
}

async function copyShareLink(id) {
  try {
    const s = await fetchShare(id);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(s.short_url);
    } else {
      const ta = document.createElement("textarea");
      ta.value = s.short_url; ta.setAttribute("readonly", "");
      ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      document.execCommand("copy"); ta.remove();
    }
    toast("تم نسخ الرابط القصير ✅");
  } catch (e) { toast(e.message, "err"); }
}

async function toggleQr(id) {
  const box = $("#qrBox");
  if (!box.hidden) { box.hidden = true; return; }
  try {
    const s = await fetchShare(id);
    box.innerHTML = `
      <img src="${esc(s.qr_svg)}?v=1" alt="رمز QR للإعلان" width="168" height="168">
      <div class="qr-meta">
        <b>امسح الرمز بهاتفك</b>
        <span>يفتح الإعلان مباشرة — مثالي للطباعة على ملصق أو إرساله صورة.</span>
        <code>${esc(s.short_url)}</code>
      </div>`;
    box.hidden = false;
  } catch (e) { toast(e.message, "err"); }
}

/* ─────────────── 📷 صور الإعلانات ─────────────── */
async function uploadListingImage(id, file, silent = false) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch(`/api/listings/${id}/image`, {
      method: "POST", body: fd, credentials: "same-origin",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.message || "فشل رفع الصورة");
    toast(data.message || "✅ رُفعت الصورة", "ok");
    if (!silent) {
      await loadAndRenderResults();
      if (!$("#modalRoot").hidden) openDetail(id);
    }
    return data;
  } catch (e) { toast(e.message, "err"); return null; }
}

function pickImage(id) {
  // يُبنى المدخل ديناميكياً في كل مرة حتى يعمل اختيار نفس الملف مرتين
  const inp = document.createElement("input");
  inp.type = "file";
  inp.accept = "image/png,image/jpeg,image/webp";
  inp.onchange = () => { if (inp.files[0]) uploadListingImage(id, inp.files[0]); };
  inp.click();
}

async function removeImage(id) {
  try {
    const r = await api(`/api/listings/${id}/image`, { method: "DELETE" });
    toast(r.message || "أُزيلت الصورة", "ok");
    await loadAndRenderResults();
    if (!$("#modalRoot").hidden) openDetail(id);
  } catch (e) { toast(e.message, "err"); }
}

async function offerBarter(id) {
  let title = "";
  try { title = (await api(`/api/listings/${id}`)).listing.title; } catch (_) { }
  modal(`
    <h3 class="form-title">🔄 اعرض مقايضة</h3>
    <p class="form-sub">على إعلان: <b>${esc(title)}</b><br>سيصل عرضك للبائع مباشرة على واتساب — وهذا يوثّق الاتفاق كتابياً لحمايتكما.</p>
    <div class="field"><label>ما الذي تعرضه بالمقابل؟ *</label>
      <textarea id="barterOffer" rows="3" placeholder="مثال: أعرض جهاز Xbox Series S بحالة ممتازة + 30 دينار فرق…"></textarea>
      <div class="hint">كن محدداً: السلعة + حالتها + أي فرق نقدي تعرضه أو تطلبه.</div></div>
    <div class="field"><label>رقم هاتفك (واتساب) *</label>
      <input id="barterPhone" type="tel" inputmode="numeric" placeholder="07XXXXXXXX" value="${esc(S.user?.phone || "")}"></div>
    <div id="barterMsg"></div>
    <button class="btn btn-primary btn-block" onclick="sendBarter(${id})">📤 أرسل عرض المقايضة</button>
  `);
}

async function sendBarter(id) {
  const offer = $("#barterOffer").value.trim();
  const phone = ($("#barterPhone").value || "").replace(/\D/g, "");
  if (offer.length < 5) {
    $("#barterMsg").innerHTML = `<div class="inline-msg err">اكتب وصفاً واضحاً لما تعرضه (5 أحرف على الأقل).</div>`;
    return;
  }
  if (!/^07[4-9]\d{7}$/.test(phone)) {
    $("#barterMsg").innerHTML = `<div class="inline-msg err">أدخل رقم هاتف أردني صحيح مثل 0791234567.</div>`;
    return;
  }
  try {
    const { listing } = await api(`/api/listings/${id}`);
    const lines = [
      `عرض مقايضة على إعلانك: «${listing.title}»`,
      ``,
      `أعرض بالمقابل: ${offer}`,
      `رقمي للتواصل: ${phone}`,
      ``,
      `— أُرسل عبر منصة الشامل الذكية 🌟`,
    ];
    window.open(`${listing.whatsapp_url.split("?")[0]}?text=${encodeURIComponent(lines.join("\n"))}`,
                "_blank", "noopener");
    closeModal();
    toast("✅ تم فتح واتساب لإرسال عرض المقايضة — احتفظ بالرسالة كتوثيق", "ok");
  } catch (e) {
    $("#barterMsg").innerHTML = `<div class="inline-msg err">${esc(e.message)}</div>`;
  }
}

async function deleteListing(id) {
  if (!confirm("هل أنت متأكد من حذف هذا الإعلان؟")) return;
  try {
    await api(`/api/listings/${id}`, { method: "DELETE" });
    closeModal(); toast("🗑 تم حذف الإعلان", "ok"); loadAndRenderResults();
    if (S.view === "dashboard") renderDashboard();
  } catch (e) { toast(e.message, "err"); }
}

/* ═══════════════════════════════════════════════════════════
   إضافة إعلان جديد (+ ترقية مدفوعة)
   ═══════════════════════════════════════════════════════════ */
function openNewListing() {
  if (!S.user) return openAuth("login", "سجّل الدخول أولاً لنشر إعلانك — يستغرق 20 ثانية فقط");
  const cats = S.boot.categories, cities = S.boot.cities;
  modal(`
    <h3 class="form-title">➕ أضف إعلانك الجديد</h3>
    <p class="form-sub">النشر مجاني تماماً. الإعلانات الواضحة والمسعّرة بعدالة تُباع أسرع بـ 3 مرات.</p>
    <div class="field"><label>عنوان الإعلان *</label>
      <input id="nlTitle" type="text" maxlength="120" placeholder="مثال: ايفون 13 - 128GB بحالة ممتازة">
      <div class="hint">اذكر النوع والموديل والحالة في العنوان — هذا ما يبحث عنه المشتري.</div></div>
    <div class="field-row">
      <div class="field"><label>السعر المطلوب (${esc(S.boot.currency)}) *</label>
        <input id="nlPrice" type="number" min="0" step="1" value="100"></div>
      <div class="field"><label>القسم *</label>
        <select id="nlCat">${cats.map(c => `<option>${esc(c)}</option>`).join("")}</select></div>
    </div>
    <div class="field-row">
      <div class="field"><label>المدينة *</label>
        <select id="nlCity">${cities.map(c => `<option ${c === S.user.city ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></div>
      <div class="field"><label>&nbsp;</label>
        <div style="display:flex;align-items:center;gap:.5rem;height:42px">
          <input type="checkbox" id="nlBarter" style="width:auto"> <label for="nlBarter" style="font-size:.85rem">🔄 أقبل المقايضة والتبادل</label>
        </div></div>
    </div>
    <div class="field"><label>وصف تفصيلي</label>
      <textarea id="nlDesc" rows="4" placeholder="المواصفات، الحالة، سبب البيع، ما يشمله السعر، إمكانية التوصيل…"></textarea></div>
    <div class="field"><label>📷 صورة السلعة (اختياري — لكن الإعلانات المصوّرة تُباع أسرع بكثير)</label>
      <input id="nlImage" type="file" accept="image/png,image/jpeg,image/webp" class="file-input">
      <div class="hint">PNG أو JPEG أو WebP حتى 2 ميغابايت. تظهر في البطاقة وفي بطاقة المشاركة. يمكنك إضافتها لاحقاً من صفحة الإعلان.</div></div>

    <label class="switch" id="nlFeatured">
      <span class="sw-box"></span>
      <span class="switch-text">
        <strong>⭐ ثبّت إعلاني في الأعلى — ${S.boot.pricing.feature_price} ${esc(S.boot.currency)}</strong>
        <small>ظهور مميز لمدة ${S.boot.pricing.feature_days} أيام · مشاهدات أكثر 4 أضعاف</small>
      </span>
    </label>

    <div class="pay-note"><span>💳</span>
      <span>الدفع حالياً في وضع <strong>المحاكاة التجريبية</strong> — عند الإطلاق الفعلي يُستبدل بـ Clicks / إي-فواتيركم / محفظة زين كاش. الميزة تُفعَّل فوراً بعد الدفع.</span>
    </div>
    <div id="nlMsg"></div>
    <button class="btn btn-primary btn-block" id="nlSubmit">🚀 نشر الإعلان</button>
    <div style="text-align:center;margin-top:.6rem">
      <button class="btn btn-ghost btn-sm" onclick="closeModal();openPricing()">🤖 لست متأكداً من السعر؟ احسبه أولاً</button>
    </div>
  `, true);

  let featured = false;
  $("#nlFeatured").onclick = () => {
    featured = !featured;
    $("#nlFeatured").classList.toggle("on", featured);
    $(".sw-box", $("#nlFeatured")).textContent = featured ? "✓" : "";
    $("#nlSubmit").textContent = featured
      ? `🚀 انشر وثبّت إعلاني — ${S.boot.pricing.feature_price} ${S.boot.currency}`
      : "🚀 نشر الإعلان";
  };

  $("#nlSubmit").onclick = async () => {
    const btn = $("#nlSubmit"); btn.disabled = true; btn.textContent = "جارٍ النشر…";
    try {
      const r = await api("/api/listings", {
        method: "POST",
        body: {
          title: $("#nlTitle").value, description: $("#nlDesc").value,
          price: Number($("#nlPrice").value || 0), category: $("#nlCat").value,
          city: $("#nlCity").value, accepts_barter: $("#nlBarter").checked, featured,
        },
      });
      const imgFile = ($("#nlImage") && $("#nlImage").files[0]) || null;
      if (imgFile) await uploadListingImage(r.listing.id, imgFile, true);
      closeModal();
      toast(r.message, "ok");
      if (r.charged_jd) toast(`💳 تم خصم ${r.charged_jd} ${S.boot.currency} (محاكاة)`, "info");
      await refreshMe(); await loadAndRenderResults();
      if (S.view !== "home") go("home");
    } catch (e) {
      btn.disabled = false; btn.textContent = "🚀 نشر الإعلان";
      $("#nlMsg").innerHTML = `<div class="inline-msg err">${esc(e.message)}</div>`;
      if (e.status === 402) {
        toast(e.message, "err");
        if (e.payload?.need_slots) {
          $("#nlMsg").insertAdjacentHTML("afterend", `
            <div class="card" style="margin-top:.8rem;border-color:#F6D9A5;background:var(--flame-soft)">
              <h4 style="color:#8A5B00">📦 وصلت لحدّك الأقصى من الإعلانات</h4>
              <p style="font-size:.8rem;color:#8A5B00;margin:.2rem 0 .7rem">وسّع حدك بـ ${S.boot.pricing.extra_listing_pack} إعلانات دائمة — التوسعة لا تضيع حتى لو حذفت إعلاناً.</p>
              <button class="btn btn-primary btn-block" onclick="buySlots()">وسّع حدّي بـ ${(S.boot.pricing.extra_listing_price * S.boot.pricing.extra_listing_pack).toFixed(2)} د.أ</button>
            </div>`);
        }
      }
    }
  };
}

/* ═══════════════════════════════════════════════════════════
   🤖 التسعير الذكي
   ═══════════════════════════════════════════════════════════ */
function openPricing() {
  const cats = S.boot.categories, cities = S.boot.cities;
  const credits = S.user ? S.user.pricing_credits : null;
  modal(`
    <h3 class="form-title">🤖 مساعد التسعير الذكي</h3>
    <p class="form-sub">أدخل تفاصيل سلعتك، وسيحلل المحرك السوق الأردني (أسعار الفئات، حالة المنتج، قوة الطلب في مدينتك) ويعطيك السعر العادل ونطاق المفاوضة.</p>
    ${credits !== null
      ? `<div class="inline-msg ok" style="margin-top:0">رصيدك الحالي: <strong>${credits}</strong> تسعيرة ذكية ${credits === 0 ? "— يمكنك استخدام " + S.boot.pricing.free_daily + " تسعيرات مجانية يومياً" : ""}</div>`
      : `<div class="inline-msg" style="background:var(--flame-soft);color:#8A5B00;border:1px solid #F6D9A5;margin-top:0">وضع الزائر: ${S.boot.pricing.free_daily} تسعيرات مجانية يومياً. سجّل الدخول للحصول على رصيد إضافي.</div>`}
    <div class="field"><label>اسم المنتج أو وصفه *</label>
      <input id="prName" type="text" placeholder="مثال: ايفون 12 - 64GB مع كرتونه">
      <div class="hint">كلما كان الوصف أدق (النوع + الموديل + الملحقات) كانت النتيجة أدق.</div></div>
    <div class="field-row">
      <div class="field"><label>القسم</label>
        <select id="prCat">${cats.map(c => `<option>${esc(c)}</option>`).join("")}</select></div>
      <div class="field"><label>المدينة</label>
        <select id="prCity">${cities.map(c => `<option ${c === (S.user?.city || "عمّان") ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></div>
    </div>
    <div class="field"><label>حالة المنتج: <b id="prCondLbl">8 / 10</b></label>
      <input id="prCond" type="range" min="1" max="10" value="8">
      <div class="range-labels"><span>1 = يحتاج صيانة</span><span>10 = جديد بالكرتون</span></div></div>
    <div class="field">
      <label class="switch" id="prBarter"><span class="sw-box"></span>
        <span class="switch-text"><strong>🔄 سأقبل المقايضة</strong><small>يوسّع دائرة المشترين لكنه يخفض السعر النقدي قليلاً</small></span></label>
    </div>
    <div id="prMsg"></div>
    <div id="prResult"></div>
    <button class="btn btn-primary btn-block" id="prGo">🚀 احسب السعر العادل</button>
  `, true);

  let barter = false;
  $("#prCond").oninput = (e) => $("#prCondLbl").textContent = `${e.target.value} / 10`;
  $("#prBarter").onclick = () => {
    barter = !barter;
    $("#prBarter").classList.toggle("on", barter);
    $(".sw-box", $("#prBarter")).textContent = barter ? "✓" : "";
  };
  $("#prGo").onclick = runPricing;
}

async function runPricing() {
  const btn = $("#prGo"); btn.disabled = true; btn.textContent = "⏳ جارٍ تحليل السوق…";
  try {
    const r = await api("/api/pricing", {
      method: "POST",
      body: {
        item_name: $("#prName").value, category: $("#prCat").value,
        condition: Number($("#prCond").value), city: $("#prCity").value, accepts_barter: barterFlag(),
      },
    });
    renderPricingResult(r);
    await refreshMe();
  } catch (e) {
    $("#prMsg").innerHTML = `<div class="inline-msg err">${esc(typeof e.message === "string" ? e.message : "خطأ")}</div>`;
    if (e.status === 402 && e.payload?.need_credits) renderUpsell(e.payload);
    if (e.status === 401) { closeModal(); openAuth("login", "سجّل الدخول لمتابعة استخدام التسعير الذكي"); }
  }
  btn.disabled = false; btn.textContent = "🚀 احسب السعر العادل";
}
function barterFlag() { return $("#prBarter")?.classList.contains("on") || false; }

function renderPricingResult(r) {
  S.lastPricing = {
    price: r.fair_price,
    name: $("#prName").value,
    category: r.category,
    city: r.city,
    barter: r.accepts_barter,
  };
  const span = r.high_price - r.low_price || 1;
  const pos = Math.min(100, Math.max(0, ((r.fair_price - r.low_price) / span) * 100));
  $("#prResult").innerHTML = `
    <div class="price-hero">
      <div class="lbl">💡 السعر العادل المقترح لسلعتك</div>
      <div class="big">${jd(r.fair_price)} <span style="font-size:1rem">${esc(r.currency)}</span></div>
      <div class="lbl">نطاق المفاوضة المتوقع في السوق الأردني</div>
    </div>
    <div class="range-bar">
      <div class="range-track"></div>
      <div class="range-dot" style="inset-inline-start:${100 - pos}%"></div>
    </div>
    <div class="range-labels"><span>أقل سعر ${jd(r.low_price)} د.أ</span><span>أعلى سعر ${jd(r.high_price)} د.أ</span></div>
    <div class="quick-cards">
      <div class="quick-card"><b>${jd(r.quick_sale_price)}</b><span>⚡ سعر البيع السريع (48 ساعة)</span></div>
      <div class="quick-card"><b>${jd(r.fair_price)}</b><span>⚖️ السعر العادل</span></div>
      <div class="quick-card"><b>${jd(r.high_price)}</b><span>🎯 ابدأ به ثم فاوض</span></div>
    </div>
    <div class="drivers">
      <h4>🔍 كيف وصل المحرك لهذا الرقم؟</h4>
      <ul>${r.drivers.map(d => `<li>${esc(d)}</li>`).join("")}</ul>
    </div>
    <div class="tip-box">💡 ${esc(r.tip)}</div>
    <div style="display:flex;gap:.5rem;margin-top:.9rem;flex-wrap:wrap">
      <button class="btn btn-primary" style="flex:1" onclick="usePriceInListing()">📝 انشر إعلاناً بهذا السعر</button>
      <button class="btn btn-outline" onclick="copyPricing()">📋 نسخ النتيجة</button>
    </div>
    ${r.remaining_credits !== undefined && S.user ? `<p style="font-size:.76rem;color:var(--slate);text-align:center;margin-top:.7rem">الرصيد المتبقي: ${r.remaining_credits} تسعيرة</p>` : ""}
  `;
  const ps = $("#statPricing"); if (ps) loadPricingStats();
}

function copyPricing() {
  const txt = $("#prResult").innerText.replace(/\n{2,}/g, "\n");
  navigator.clipboard?.writeText(`نتيجة التسعير الذكي — منصة الشامل 🌟\n${txt}`)
    .then(() => toast("📋 تم نسخ النتيجة", "ok"))
    .catch(() => toast("تعذّر النسخ — حدّد النص يدوياً", "err"));
}

function usePriceInListing() {
  const p = S.lastPricing;
  if (!p) return;
  closeModal();
  openNewListing();
  setTimeout(() => {
    if ($("#nlTitle")) $("#nlTitle").value = p.name;
    if ($("#nlPrice")) $("#nlPrice").value = p.price;
    if ($("#nlCat")) $("#nlCat").value = p.category;
    if ($("#nlCity")) $("#nlCity").value = p.city;
    if ($("#nlBarter")) $("#nlBarter").checked = p.barter;
    toast(`✨ تم تعبئة النموذج بالسعر العادل ${jd(p.price)} ${S.boot.currency}`, "ok");
  }, 140);
}

function renderUpsell(payload) {
  $("#prMsg").insertAdjacentHTML("afterend", `
    <div class="card" style="margin:.9rem 0;border-color:#F6D9A5;background:var(--flame-soft)">
      <h4 style="color:#8A5B00">🔓 افتح التسعير غير المحدود</h4>
      <div class="packs">${(payload.packages || S.boot.pricing.packages).map(packHTML).join("")}</div>
      ${S.user ? `<p style="font-size:.75rem;color:var(--slate);text-align:center">الدفع بالمحاكاة حالياً — يُفعَّل رصيدك فوراً</p>`
               : `<button class="btn btn-dark btn-block" onclick="closeModal();openAuth('register','سجّل واحصل على رصيد تسعير مجاني فوراً')">إنشاء حساب مجاني</button>`}
    </div>`);
  bindPacks();
}

function packHTML(p) {
  return `<div class="pack ${p.tag ? "best" : ""}" data-pack="${p.id}">
    ${p.tag ? `<span class="tagline">${esc(p.tag)}</span>` : ""}
    <b>${p.price_jd}</b><span class="label">د.أ</span>
    <div class="credits">${p.credits} تسعيرة ذكية</div>
    <div class="label">${esc(p.label)}</div>
    <button class="btn btn-primary btn-sm btn-block" style="margin-top:.55rem" onclick="buyPackage('${p.id}')">اشترِ</button>
  </div>`;
}
function bindPacks() { /* الأزرار مرتبطة مباشرة عبر onclick */ }

async function buyPackage(id) {
  if (!S.user) return openAuth("register", "أنشئ حساباً مجانياً لشراء حزمة التسعير");
  try {
    const r = await api("/api/buy/package", { method: "POST", body: { package_id: id } });
    toast(r.message, "ok");
    toast(`💳 تم خصم ${r.charged_jd} ${S.boot.currency} (محاكاة)`, "info");
    await refreshMe(); closeModal();
    if (S.view === "dashboard") renderDashboard(); else openPricing();
  } catch (e) { toast(e.message, "err"); }
}

async function loadPricingStats() {
  try {
    const r = await api("/api/pricing/stats");
    const el = $("#statPricing");
    if (el) el.textContent = jd(r.total);
  } catch (_) { /* غير حرج */ }
}

/* ═══════════════════════════════════════════════════════════
   المصادقة
   ═══════════════════════════════════════════════════════════ */
function openAuth(mode = "login", note = "") {
  const cities = S.boot.cities;
  modal(`
    <div style="display:flex;gap:.4rem;margin-bottom:1rem;background:#F1F4F8;padding:.25rem;border-radius:999px">
      <button class="btn btn-sm ${mode === "login" ? "btn-dark" : ""}" style="flex:1" onclick="openAuth('login')">تسجيل الدخول</button>
      <button class="btn btn-sm ${mode === "register" ? "btn-dark" : ""}" style="flex:1" onclick="openAuth('register')">حساب جديد</button>
    </div>
    ${note ? `<div class="inline-msg" style="background:var(--blue-soft);color:#1E5A96;border:1px solid #CFE1F6">${esc(note)}</div>` : ""}
    <div id="authForm">
      ${mode === "register" ? `<div class="field"><label>الاسم الكامل *</label><input id="aName" type="text" placeholder="مثال: محمد الشلول"></div>` : ""}
      <div class="field"><label>رقم الهاتف (واتساب) *</label>
        <input id="aPhone" type="tel" placeholder="07XXXXXXXX" inputmode="numeric">
        <div class="hint">سيظهر للمشترين كزر تواصل مباشر عبر واتساب.</div></div>
      ${mode === "register" ? `<div class="field"><label>المدينة</label>
        <select id="aCity">${cities.map(c => `<option>${esc(c)}</option>`).join("")}</select></div>` : ""}
      <div class="field"><label>كلمة المرور *</label><input id="aPass" type="password" placeholder="6 أحرف على الأقل">
        ${mode === "register" ? `<div class="hint">احصل على ${S.boot.pricing.free_daily} تسعيرات ذكية مجانية فور التسجيل 🎁</div>` : ""}</div>
      <div id="aMsg"></div>
      <button class="btn btn-primary btn-block" id="aGo">${mode === "register" ? "🚀 إنشاء الحساب" : "دخول"}</button>
      <div style="text-align:center;margin-top:.8rem;font-size:.78rem;color:var(--slate)">
        حساب تجريبي جاهز: <b>${esc(S.boot.demo_account.phone)}</b> / <b>${esc(S.boot.demo_account.password)}</b>
        <button class="btn btn-ghost btn-sm" onclick="fillDemo()">تعبئة تلقائية</button>
      </div>
    </div>
  `);

  $("#aGo").onclick = async () => {
    const btn = $("#aGo"); btn.disabled = true;
    const payload = {
      phone: $("#aPhone").value,
      password: $("#aPass").value,
    };
    if (mode === "register") { payload.name = $("#aName").value; payload.city = $("#aCity").value; }
    try {
      const r = await api(mode === "register" ? "/api/register" : "/api/login", { method: "POST", body: payload });
      S.user = r.user;
      renderAuthArea(); closeModal();
      toast(mode === "register" ? `🎉 أهلاً ${r.user.name}! تم إنشاء حسابك بنجاح` : `👋 أهلاً بعودتك ${r.user.name}`, "ok");
      if (S.view === "home") renderHome(); else go("dashboard");
      refreshMe();
    } catch (e) {
      btn.disabled = false;
      $("#aMsg").innerHTML = `<div class="inline-msg err">${esc(e.message)}</div>`;
    }
  };
}
function fillDemo() {
  $("#aPhone").value = S.boot.demo_account.phone;
  $("#aPass").value = S.boot.demo_account.password;
}

async function logout() {
  try { await api("/api/logout", { method: "POST" }); } catch (_) { }
  S.user = null; S.stats = null; renderAuthArea(); go("home"); toast("تم تسجيل الخروج 👋", "info");
}

function renderAuthArea() {
  const el = $("#authArea");
  if (!S.user) {
    el.innerHTML = `<button class="btn btn-ghost btn-sm" onclick="openAuth('login')">دخول</button>
                    <button class="btn btn-dark btn-sm" onclick="openAuth('register')">حساب جديد</button>`;
    return;
  }
  const initial = (S.user.name || "?").trim().charAt(0);
  el.innerHTML = `<div class="user-chip">
      <button class="btn btn-ghost btn-sm" style="padding:.2rem .5rem" onclick="go('dashboard')">📊</button>
      <span class="nm">${S.user.is_verified ? "🛡️ " : ""}${esc(S.user.name)}</span>
      ${S.user.pricing_credits > 0 ? `<span class="credits-pill">🤖 ${S.user.pricing_credits}</span>` : ""}
      <span class="avatar">${esc(initial)}</span>
      <button class="btn btn-ghost btn-sm" style="padding:.2rem .4rem" onclick="openNotifications()" title="إشعاراتي">🔔</button>
      <button class="btn btn-ghost btn-sm" style="padding:.2rem .4rem" onclick="logout()" title="خروج">⏻</button>
    </div>`;
}

async function openNotifications() {
  try {
    const r = await api("/api/notifications");
    const list = r.notifications || [];
    const ico = { renew_reminder: "⏳", renew_done: "✅", welcome: "🌟" };
    modal(`
      <h3 class="form-title">🔔 إشعاراتي</h3>
      <p class="form-sub">تذكيرات تجديد التثبيت وتأكيداتها — تصل واتساب عند الإطلاق الفعلي (محاكاة حالياً).</p>
      ${list.length ? list.map(n => `
        <div class="notif kind-${esc(n.kind)}">
          <span class="notif-ico">${ico[n.kind] || "🔔"}</span>
          <div>
            <div class="notif-body">${esc(n.body)}</div>
            <div class="notif-time">${new Date(n.created_at * 1000).toLocaleString("ar-JO")}</div>
          </div>
        </div>`).join("")
        : `<p style="opacity:.6;text-align:center;padding:1.4rem 0">لا إشعارات بعد — سيصلك تذكير قبل انتهاء تثبيت أي إعلان لك بـ 24 ساعة.</p>`}
    `, true);
  } catch (e) { toast(e.message, "err"); }
}

async function refreshMe() {
  try {
    const r = await api("/api/me");
    S.user = r.user; S.stats = r.stats; renderAuthArea();
  } catch (_) { }
}

/* ═══════════════════════════════════════════════════════════
   ⭐ شراء التثبيت
   ═══════════════════════════════════════════════════════════ */
async function openBoost(preselect) {
  if (!S.user) return openAuth("login", "سجّل الدخول لتثبيت إعلانك في الأعلى");
  try {
    const r = await api(`/api/listings?q=&limit=200`);
    const mine = r.listings.filter(a => a.seller_id === S.user.id);
    if (!mine.length) {
      modal(`<h3 class="form-title">⭐ تثبيت إعلان</h3>
        <p class="form-sub">لا تملك إعلانات نشطة بعد.</p>
        <button class="btn btn-primary btn-block" onclick="closeModal();openNewListing()">➕ أضف إعلانك الأول</button>`);
      return;
    }
    modal(`
      <h3 class="form-title">⭐ ثبّت إعلانك في الأعلى</h3>
      <p class="form-sub">اختر الإعلان — سيظهر بإطار ذهبي وشارة «مثبّت» قبل كل نتائج البحث لمدة ${S.boot.pricing.feature_days} أيام.</p>
      <div class="field"><label>الإعلان</label>
        <select id="bsListing">${mine.map(a =>
          `<option value="${a.id}" ${preselect === a.id ? "selected" : ""}>${a.is_featured ? "⭐ " : ""}${esc(a.title)} — ${jd(a.price)} ${esc(S.boot.currency)}</option>`).join("")}</select></div>
      <div class="quick-cards">
        <div class="quick-card"><b>${S.boot.pricing.feature_price}</b><span>د.أ تثبيت جديد</span></div>
        <div class="quick-card"><b>${S.boot.pricing.renew_price}</b><span>د.أ تجديد مثبّت</span></div>
        <div class="quick-card"><b>×4</b><span>زيادة المشاهدات</span></div>
      </div>
      <div class="pay-note"><span>💳</span><span>الدفع بالمحاكاة في النسخة التجريبية. يُستبدل ببوابة دفع أردنية حقيقية عند الإطلاق.</span></div>
      <div id="bsMsg"></div>
      <button class="btn btn-star btn-block" id="bsGo">⭐ ثبّت الآن</button>
    `);
    $("#bsGo").onclick = async () => {
      const btn = $("#bsGo"); btn.disabled = true; btn.textContent = "جارٍ المعالجة…";
      try {
        const res = await api("/api/feature", {
          method: "POST",
          body: { listing_id: Number($("#bsListing").value), renew: false },
        });
        closeModal(); toast(res.message, "ok");
        toast(`💳 تم خصم ${res.charged_jd} ${S.boot.currency} (محاكاة)`, "info");
        await refreshMe(); await loadAndRenderResults();
        if (S.view === "dashboard") renderDashboard();
      } catch (e) {
        btn.disabled = false; btn.textContent = "⭐ ثبّت الآن";
        $("#bsMsg").innerHTML = `<div class="inline-msg err">${esc(e.message)}</div>`;
      }
    };
  } catch (e) { toast(e.message, "err"); }
}

/* ═══════════════════════════════════════════════════════════
   📊 لوحة التحكم
   ═══════════════════════════════════════════════════════════ */
async function renderDashboard() {
  const box = $("#view-dashboard");
  box.innerHTML = `<div class="empty"><span class="big">⏳</span>جارٍ تحميل لوحتك…</div>`;
  await refreshMe();
  if (!S.user) { go("home"); return; }
  const st = S.stats || { listings: 0, featured: 0, views: 0, spent_jd: 0 };
  const r = await api(`/api/listings?limit=200`);
  const mine = r.listings.filter(a => a.seller_id === S.user.id);
  const packs = S.boot.pricing.packages;
  const freeUsed = S.boot.pricing.free_active_listings;

  box.innerHTML = `
    <div class="hero" style="margin-bottom:1rem">
      <h1>📊 لوحة التحكم ${S.user.is_verified ? '<span class="tag tag-verified" style="font-size:.7rem;vertical-align:middle">🛡️ بائع موثّق</span>' : ""}</h1>
      <p>${esc(S.user.name)} · ${esc(S.user.city)} · ${esc(S.user.phone)}</p>
    </div>
    <div class="stat-grid">
      <div class="stat-card"><b>${st.listings}</b><span>إعلاناتك النشطة</span>
        <div class="bar"><i style="width:${Math.min(100, (st.listings / (freeUsed + S.user.extra_listing_slots)) * 100)}%"></i></div>
        <span style="font-size:.68rem">الحد المتاح ${freeUsed + S.user.extra_listing_slots} (${freeUsed} مجاني + ${S.user.extra_listing_slots} مدفوع)</span></div>
      <div class="stat-card gold"><b>${st.featured}</b><span>⭐ إعلانات مثبّتة</span></div>
      <div class="stat-card"><b>${jd(st.views || 0)}</b><span>👁 إجمالي المشاهدات</span></div>
      <div class="stat-card green"><b>${S.user.pricing_credits}</b><span>🤖 رصيد التسعير</span></div>
      <div class="stat-card"><b>${freeUsed + S.user.extra_listing_slots}</b><span>📦 حدّك الأقصى للإعلانات</span></div>
      <div class="stat-card"><b>${st.spent_jd}</b><span>💳 إجمالي مدفوعاتك (د.أ)</span></div>
    </div>

    <div class="layout" style="grid-template-columns:1fr 300px">
      <div>
        <div class="card">
          <h3>📦 إعلاناتك</h3>
          ${mine.length ? mine.map(a => `
            <div class="my-ad">
              <div class="detail-thumb ${CAT_CLASS[a.category] || "th-أخرى"}" style="width:52px;height:52px;font-size:1.5rem;border-radius:11px">${CAT_EMOJI[a.category] || "📦"}</div>
              <div class="info">
                <strong>${a.is_featured ? "⭐ " : ""}${esc(a.title)}</strong>
                <small>${jd(a.price)} ${esc(S.boot.currency)} · ${esc(a.city)} · 👁 ${a.views} مشاهدة · ${esc(a.age_label)}
                ${a.is_featured ? ` · ينتهي التثبيت بعد ${a.featured_hours_left} ساعة` : ""}</small>
              </div>
              <div class="acts">
                <a class="btn btn-outline btn-sm" data-stop href="${esc(a.whatsapp_url)}" target="_blank" rel="noopener">💬</a>
                <button class="btn btn-sm ${a.is_featured ? "btn-outline" : "btn-star"}" onclick="openBoost(${a.id})">${a.is_featured ? "تجديد ⭐" : "⭐ ثبّت"}</button>
                <button class="btn btn-outline btn-sm" onclick="openDetail(${a.id})">عرض</button>
                <button class="btn btn-outline btn-sm" style="color:var(--red)" onclick="deleteListing(${a.id})">🗑</button>
              </div>
            </div>`).join("")
            : `<div class="empty" style="padding:2rem"><span class="big">📭</span>لا توجد إعلانات بعد
                <div style="margin-top:.8rem"><button class="btn btn-primary" onclick="openNewListing()">➕ أضف إعلانك الأول</button></div></div>`}
          <button class="btn btn-primary btn-block" style="margin-top:.8rem" onclick="openNewListing()">➕ إضافة إعلان جديد</button>
        </div>

        <div class="card">
          <h3>📦 فتحات الإعلانات الإضافية</h3>
          <p style="font-size:.83rem;color:var(--slate);margin:.2rem 0 .6rem">
            حدك المجاني ${freeUsed} إعلانات نشطة. تحتاج أكثر؟ وسّع حدك بـ ${S.boot.pricing.extra_listing_pack} إعلانات <b>دائمة</b> — لا تضيع حتى لو حذفت إعلاناً.</p>
          <div style="display:flex;gap:.7rem;align-items:center;flex-wrap:wrap">
            <div class="quick-card" style="flex:1;min-width:120px"><b>+${S.boot.pricing.extra_listing_pack}</b><span>إعلانات دائمة</span></div>
            <div class="quick-card" style="flex:1;min-width:120px"><b>${(S.boot.pricing.extra_listing_price * S.boot.pricing.extra_listing_pack).toFixed(2)}</b><span>د.أ للحزمة</span></div>
            <button class="btn btn-primary" onclick="buySlots()">وسّع حدّي</button>
          </div>
        </div>

        <div class="card">
          <h3>🤖 اشترِ رصيد التسعير الذكي</h3>
          <p style="font-size:.83rem;color:var(--slate);margin:.2rem 0 .6rem">
            رصيدك الحالي: <b>${S.user.pricing_credits}</b> تسعيرة. بدون رصيد تحصل على ${S.boot.pricing.free_daily} تسعيرات مجانية يومياً.</p>
          <div class="packs">${packs.map(packHTML).join("")}</div>
        </div>
      </div>

      <aside class="sidebar" style="position:static">
        ${S.user.is_verified ? `
          <div class="card" style="border-color:#CFE1F6;background:var(--blue-soft)">
            <h3>🛡️ حسابك موثّق</h3>
            <p style="font-size:.83rem;margin:.2rem 0 0;color:#1E5A96">إعلاناتك تحمل وسم الثقة — المشترين يتواصلون مع البائعين الموثّقين بنسبة أعلى 60%.</p>
          </div>` : `
          <div class="card boost-card">
            <h3>🛡️ احصل على وسم «بائع موثّق»</h3>
            <ul class="boost-list">
              <li>شارة زرقاء بجانب اسمك في كل إعلان</li>
              <li>ثقة أعلى = تواصل أكثر 60%</li>
              <li>أولوية في نتائج البحث</li>
              <li>دفعة واحدة مدى الحياة</li>
            </ul>
            <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.7rem">
              <b style="font-size:1.5rem;color:#FFD86B">${S.boot.pricing.verify_price}</b>
              <span style="font-size:.8rem;opacity:.75">دينار أردني<br>مرة واحدة فقط</span>
            </div>
            <button class="btn btn-star btn-block" onclick="buyVerification()">🛡️ وثّق حسابي</button>
          </div>`}
        ${boostHTML()}
        ${S.user.is_admin ? `<div class="card"><h3>🔐 أدوات المدير</h3>
          <button class="btn btn-dark btn-block" onclick="go('admin')">عرض إحصاءات وأرباح المنصة</button></div>` : ""}
      </aside>
    </div>`;
}

async function buySlots() {
  if (!S.user) return openAuth("login");
  try {
    const r = await api("/api/buy/extra-listing", { method: "POST" });
    toast(r.message, "ok");
    toast(`💳 تم خصم ${r.charged_jd} ${S.boot.currency} (محاكاة)`, "info");
    await refreshMe(); renderDashboard();
  } catch (e) { toast(e.message, "err"); }
}

async function buyVerification() {
  if (!S.user) return openAuth("login");
  try {
    const r = await api("/api/buy/verification", { method: "POST" });
    toast(r.message, "ok");
    toast(`💳 تم خصم ${r.charged_jd} ${S.boot.currency} (محاكاة)`, "info");
    await refreshMe(); renderDashboard();
  } catch (e) { toast(e.message, "err"); }
}

/* ═══════════════════════════════════════════════════════════
   🔐 لوحة المدير — أرباح المنصة
   ═══════════════════════════════════════════════════════════ */
async function renderAdmin() {
  const box = $("#view-admin");
  box.innerHTML = `<div class="empty"><span class="big">⏳</span>جارٍ التحميل…</div>`;
  try {
    const r = await api("/api/admin/stats");
    const kindLabel = {
      feature: "⭐ تثبيت إعلانات", renew: "🔁 تجديد تثبيت",
      verify: "🛡️ توثيق بائعين", credits: "🤖 حزم تسعير", slots: "📦 فتحات إعلانات",
    };
    const total = r.revenue_total_jd || 0;
    box.innerHTML = `
      <div class="hero"><h1>🔐 لوحة إدارة المنصة</h1>
        <p>نظرة فورية على الأداء والأرباح — كل الأرقام بالدينار الأردني.</p></div>
      <div class="stat-grid">
        <div class="stat-card gold"><b>${total}</b><span>💰 إجمالي الإيرادات (د.أ)</span></div>
        <div class="stat-card"><b>${r.users}</b><span>👥 مستخدم مسجّل</span></div>
        <div class="stat-card"><b>${r.listings}</b><span>📦 إعلان نشط</span></div>
        <div class="stat-card"><b>${r.featured_active}</b><span>⭐ مثبّت حالياً</span></div>
        <div class="stat-card green"><b>${jd(r.pricing_calls)}</b><span>🤖 عملية تسعير</span></div>
      </div>
      <div class="layout" style="grid-template-columns:1fr 1fr">
        <div class="card">
          <h3>💵 الإيرادات حسب المصدر</h3>
          <table class="data">
            <thead><tr><th>المصدر</th><th>عدد العمليات</th><th>الإيراد</th><th>النسبة</th></tr></thead>
            <tbody>${r.revenue_by_kind.map(k => `
              <tr><td>${kindLabel[k.kind] || esc(k.kind)}</td><td>${k.n}</td>
                  <td><b>${k.total.toFixed(2)}</b> د.أ</td>
                  <td style="min-width:90px"><div class="bar"><i style="width:${total ? (k.total / total * 100) : 0}%"></i></div></td></tr>`).join("")
              || `<tr><td colspan="4" style="text-align:center;color:var(--slate);padding:1.5rem">لا توجد إيرادات بعد</td></tr>`}
            </tbody>
          </table>
          <div class="pay-note" style="margin-top:1rem"><span>🔌</span>
            <span>كل العمليات مسجّلة بوضع <b>simulated</b>. لربط دفع حقيقي: أضف مزوّد Clicks أو إي-فواتيركم في <code>server/main.py</code> عند نقاط <code>POST /api/feature</code> و <code>/api/buy/*</code>.</span></div>
        </div>
        <div class="card">
          <h3>🏆 أعلى البائعين نشاطاً</h3>
          <table class="data">
            <thead><tr><th>البائع</th><th>إعلانات نشطة</th><th>الحالة</th></tr></thead>
            <tbody>${r.top_sellers.map(u => `
              <tr><td>${esc(u.name)}</td><td>${u.ads || 0}</td>
                  <td>${u.is_verified ? '<span class="tag tag-verified">🛡️ موثّق</span>' : '<span class="tag">عادي</span>'}</td></tr>`).join("")}
            </tbody>
          </table>
        </div>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty"><span class="big">🔒</span>${esc(e.message)}</div>`;
  }
}

/* ═══════════════════════════════════════════════════════════
   🛡️ صفحة الأمان والضمان
   ═══════════════════════════════════════════════════════════ */
function renderSafety() {
  $("#view-safety").innerHTML = `
    <div class="hero"><h1>🛡️ ضمان حقوق المستخدمين</h1>
      <p>بنينا المنصة على مبدأ واحد: لا أحد يخسر بسبب غموض. هذه آليات الحماية المتاحة لك.</p></div>
    <div class="safety-grid">
      <div class="safety-item"><span class="ico">🚫</span><h4>بيئة خالية من الإعلانات المزعجة</h4>
        <p>لا نوافذ منبثقة ولا إعلانات طرف ثالث تتعقبك. التصفح نظيف وسريع على أي جهاز.</p></div>
      <div class="safety-item"><span class="ico">🔄</span><h4>توثيق المقايضة</h4>
        <p>عند اختيار «اعرض مقايضة» يُولَّد عرض مكتوب يُرسل عبر واتساب ويوثّق السلعتين وقيمتهما — دليل لك إن حدث خلاف.</p></div>
      <div class="safety-item"><span class="ico">🛡️</span><h4>وسم البائع الموثّق</h4>
        <p>حسابات خضعت للتحقق تحمل شارة زرقاء. نسبة التواصل معها أعلى 60% لأن المشتري يعرف من يتعامل معه.</p></div>
      <div class="safety-item"><span class="ico">📱</span><h4>تواصل مباشر وآمن</h4>
        <p>لا وساطة ولا عمولة على البيع العادي. زر واتساب ينقلك للمحادثة مباشرة، ورقمك يبقى بيدك.</p></div>
      <div class="safety-item"><span class="ico">🤖</span><h4>تسعير عادل يمنع الاستغلال</h4>
        <p>المحرك يحسب السعر الحقيقي من بيانات السوق الأردني، فلا يُضحك على البائع بسعر متدنٍ ولا يُنفَّر المشتري بسعر مبالغ.</p></div>
      <div class="safety-item"><span class="ico">🔒</span><h4>حماية بياناتك</h4>
        <p>كلمات المرور مخزّنة بتجزئة bcrypt ولا تُقرأ أبداً. لا نبيع بياناتك ولا نشاركها مع أي طرف.</p></div>
    </div>

    <div class="card faq" style="margin-top:1.3rem">
      <h3>❓ الأسئلة الشائعة</h3>
      <details><summary>هل النشر مجاني فعلاً؟</summary>
        <p>نعم — لديك ${S.boot.pricing.free_active_listings} إعلانات نشطة مجاناً و ${S.boot.pricing.free_daily} تسعيرات ذكية يومياً. الدفع اختياري تماماً ولمن يريد تسريع البيع فقط.</p></details>
      <details><summary>ما الفرق بين الإعلان العادي والمثبّت ⭐؟</summary>
        <p>المثبّت يظهر أولاً في نتائج البحث بإطار ذهبي وشارة «مثبّت» لمدة ${S.boot.pricing.feature_days} أيام مقابل ${S.boot.pricing.feature_price} ${esc(S.boot.currency)}. التجديد أرخص (${S.boot.pricing.renew_price} ${esc(S.boot.currency)}).</p></details>
      <details><summary>كيف أحمي نفسي في عملية مقايضة؟</summary>
        <p>التقِ الطرف الآخر في مكان عام، عاين السلعتين معاً، ووقّع الاتفاق برسالة واتساب مكتوبة تذكر السلعتين وقيمتهما المتفق عليها. احتفظ بالرسالة.</p></details>
      <details><summary>هل تأخذون عمولة على البيع؟</summary>
        <p>لا. لا نقتطع أي نسبة من سعر سلعتك. دخلنا فقط من الخدمات الاختيارية: التثبيت، التوثيق، وحزم التسعير.</p></details>
      <details><summary>كيف يعمل محرك التسعير؟</summary>
        <p>يحلل: متوسط سعر الفئة في السوق الأردني + نوع السلعة من اسمها + حالتها من 1 إلى 10 + قوة الطلب في مدينتك + قبولك للمقايضة. ثم يعطيك السعر العادل ونطاق المفاوضة وسعر البيع السريع مع شرح كل عامل.</p></details>
      <details><summary>الدفع إلكتروني أم يدوي؟</summary>
        <p>النسخة الحالية تعمل بمحاكاة دفع لتجربة المنطق الكامل. عند الإطلاق تُربط ببوابة دفع أردنية (Clicks / إي-فواتيركم / زين كاش) دون تغيير في تجربة المستخدم.</p></details>
    </div>

    <div class="card" style="margin-top:1rem;background:var(--flame-soft);border-color:#F6D9A5">
      <h3 style="color:#8A5B00">💡 نصائح السلامة</h3>
      <ul style="margin:.3rem 0 0;padding-inline-start:1.2rem;font-size:.86rem;color:#7A5A1A">
        ${S.boot.safety_tips.map(t => `<li style="margin-bottom:.3rem">${esc(t)}</li>`).join("")}
      </ul>
    </div>`;
}

/* ═══════════════════════════════════════════════════════════
   الإقلاع والربط العام
   ═══════════════════════════════════════════════════════════ */
async function boot() {
  $("#year").textContent = new Date().getFullYear();
  try {
    S.boot = await api("/api/bootstrap");
    S.user = S.boot.user;
  } catch (e) {
    document.body.innerHTML = `<div class="empty" style="margin:4rem auto"><span class="big">⚠️</span>
      تعذّر تحميل المنصة: ${esc(e.message)}</div>`;
    return;
  }
  renderAuthArea();
  renderHome();
  loadPricingStats();

  // البحث
  let t;
  $("#searchInput").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { S.filters.q = e.target.value.trim(); if (S.view !== "home") go("home"); else loadAndRenderResults(); }, 260);
  });

  // أزرار التنقل
  $$("[data-view]").forEach(el => el.addEventListener("click", (e) => { e.preventDefault(); go(el.dataset.view); }));
  $("#navPricing").onclick = () => openPricing();
  $("#navNew").onclick = () => openNewListing();
  $("#footPricing").onclick = (e) => { e.preventDefault(); openPricing(); };
  $("#footNew").onclick = (e) => { e.preventDefault(); openNewListing(); };
  $("#footFeature").onclick = (e) => { e.preventDefault(); openBoost(); };
  $("#footDashboard").onclick = (e) => { e.preventDefault(); go("dashboard"); };
  $("#footVerify").onclick = (e) => { e.preventDefault(); S.user?.is_verified ? go("dashboard") : openAuth("register", "أنشئ حساباً ثم احصل على وسم بائع موثّق 🛡️"); };
}

// تحديث الإحصاءات دورياً
setInterval(() => { if (S.view === "home") loadPricingStats(); }, 25000);

boot();
