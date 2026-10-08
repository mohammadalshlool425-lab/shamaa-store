#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  يشغّل الاختبار الشامل من قاعدة بيانات نظيفة تلقائياً
#
#  الاستخدام:  bash tests/run.sh
#
#  ملاحظة: حدود الاستخدام المجاني تُحسب تراكمياً، لذلك يجب أن يبدأ
#  الاختبار من قاعدة نظيفة وإلا فشلت فحوصات الـ Freemium.
# ══════════════════════════════════════════════════════════════
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# البيئة الافتراضية داخل المستودع إن وُجدت، وإلا python3
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

PORT="${PORT:-8000}"
LOG="/tmp/shamaa-test.log"

# ── إيقاف أي خادم سابق وتصفير القاعدة ──
pkill -f "uvicorn server.main" 2>/dev/null || true
sleep 1
rm -f data/shamaa.db data/shamaa.db-shm data/shamaa.db-wal

# ── تشغيل الخادم في الخلفية ──
"$PY" -m uvicorn server.main:app --host 0.0.0.0 --port "$PORT" >"$LOG" 2>&1 &
SRV=$!
trap 'kill "$SRV" 2>/dev/null || true' EXIT

# ── انتظار الجاهزية (حتى 20 ثانية) ──
for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
  sleep 0.5
done
if ! curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
  echo "❌ الخادم لم يستجب — سجل التشغيل:" >&2
  cat "$LOG" >&2
  exit 1
fi

# ── تنفيذ الاختبار ──
# لا نمرر المخرجات عبر أنبوب هنا: الأنبوب يخفي رمز الخروج الحقيقي
# (pipefail مضبوط، لكن التشغيل المباشر أوضح وأأمن).
set +e
"$PY" tests/test_e2e.py
RC=$?
set -e

if [ "$RC" -ne 0 ]; then
  echo "" >&2
  echo "──────── سجل الخادم عند الفشل ────────" >&2
  tail -40 "$LOG" >&2
fi

exit "$RC"
