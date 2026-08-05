#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# Terminal 1 — Start server in LOAD TEST mode
# Firebase auth bypassed via LOAD_TEST_BYPASS_AUTH=1
# ─────────────────────────────────────────────────────────
cd "$(dirname "$0")"
source .venv/bin/activate 2>/dev/null || true

export LOAD_TEST_BYPASS_AUTH=1
export LOG_LEVEL=WARNING

echo "🔓 Firebase auth BYPASSED (LOAD_TEST_BYPASS_AUTH=1)"
echo "📡 Starting server → http://localhost:8000"
echo ""

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level warning
