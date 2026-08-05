#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#   Least Count Rush — Production Load Test
#   Fill in the 4 variables below, then run:
#     chmod +x run_prod_load_test.sh
#     ./run_prod_load_test.sh
# ═══════════════════════════════════════════════════════════

# ── FILL THESE IN ──────────────────────────────────────────
FIREBASE_API_KEY="AIzaSyAtmW1z3onarz23gB7XAHLyxuc6x9l69Z0"
ADMIN_EMAIL="vinay@admin.com"
ADMIN_PASSWORD="1234567890"
SERVER_URL="https://leastcountrush.fly.dev"   # e.g. https://xxxx.run.app
# ──────────────────────────────────────────────────────────

set -e
cd "$(dirname "$0")"

# Validate filled in
if [[ "$FIREBASE_API_KEY" == "YOUR_FIREBASE_API_KEY" || \
      "$ADMIN_EMAIL"      == "YOUR_EMAIL"            || \
      "$ADMIN_PASSWORD"   == "YOUR_PASSWORD"         || \
      "$SERVER_URL"       == "https://YOUR_BACKEND_URL" ]]; then
  echo "❌  Please fill in FIREBASE_API_KEY, ADMIN_EMAIL, ADMIN_PASSWORD, and SERVER_URL"
  exit 1
fi

# Activate venv
source .venv/bin/activate 2>/dev/null || true

# Install load test deps if needed
python -c "import websockets, aiohttp" 2>/dev/null || {
  echo "📦 Installing deps..."
  pip install websockets aiohttp --quiet
}

# ── Get Firebase ID token ──────────────────────────────────
echo ""
echo "🔑 Fetching Firebase ID token..."
RESPONSE=$(curl -s -X POST \
  "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${FIREBASE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\",\"returnSecureToken\":true}")

TOKEN=$(echo "$RESPONSE" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'idToken' in d:
    print(d['idToken'])
else:
    err = d.get('error', {})
    print(f\"ERROR: {err.get('message', 'unknown')}\", file=sys.stderr)
    exit(1)
")

if [[ "$TOKEN" == ERROR* ]]; then
  echo "❌  Auth failed: $TOKEN"
  exit 1
fi

echo "✓  Token acquired (${#TOKEN} chars)"
echo ""

# ── Health check ───────────────────────────────────────────
echo "🩺 Checking server health at $SERVER_URL ..."
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "$SERVER_URL/health")
if [[ "$STATUS" != "200" ]]; then
  echo "❌  Server health check failed (HTTP $STATUS)"
  echo "    Make sure SERVER_URL is correct and the server is running."
  exit 1
fi
echo "✓  Server is healthy"
echo ""

# ── Run Tests ──────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════╗"
echo "║       Least Count Rush — Production Load Test        ║"
echo "╠══════════════════════════════════════════════════════╣"
printf  "║  Server  : %-41s║\n" "$SERVER_URL"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Light: 3 rooms × 2 players × 30s ──────────────────────
echo "▶  LIGHT — 3 rooms × 2 players × 30s"
FIREBASE_TOKEN="$TOKEN" python load_test.py \
  --rooms 3 --players 2 --duration 30 \
  --url "$SERVER_URL"

read -r -p "▶  Run MEDIUM test (10 rooms × 3 players × 30s)? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
  # Re-fetch token (expires after ~1 hour; safe to refresh)
  echo "🔑 Refreshing token..."
  TOKEN=$(curl -s -X POST \
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${FIREBASE_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\",\"returnSecureToken\":true}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['idToken'])")
  echo ""
  FIREBASE_TOKEN="$TOKEN" python load_test.py \
    --rooms 10 --players 3 --duration 30 \
    --url "$SERVER_URL"
fi

read -r -p "▶  Run HEAVY test (20 rooms × 4 players × 45s)? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
  echo "🔑 Refreshing token..."
  TOKEN=$(curl -s -X POST \
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${FIREBASE_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\",\"returnSecureToken\":true}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['idToken'])")
  echo ""
  FIREBASE_TOKEN="$TOKEN" python load_test.py \
    --rooms 20 --players 4 --duration 45 \
    --url "$SERVER_URL"
fi

echo ""
echo "🏁 Production load test complete."
