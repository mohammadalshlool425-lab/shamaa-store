#!/usr/bin/env bash
# يشغّل الاختبار الشامل من قاعدة نظيفة تلقائياً
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3
pkill -f "uvicorn server.main" 2>/dev/null || true
sleep 1
rm -f data/shamaa.db data/shamaa.db-shm data/shamaa.db-wal
$PY -m uvicorn server.main:app --host 0.0.0.0 --port 8000 >/tmp/shamaa-test.log 2>&1 &
SRV=$!
for i in $(seq 1 40); do curl -sf http://127.0.0.1:8000/healthz >/dev/null && break; sleep 0.5; done
$PY tests/test_e2e.py
RC=$?
kill $SRV 2>/dev/null || true
exit $RC
