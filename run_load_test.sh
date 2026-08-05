#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# Terminal 2 — Run Load Test
# Make sure Terminal 1 is running: ./run_load_test_server.sh
# ─────────────────────────────────────────────────────────────────
set -e
cd "$(dirname "$0")"

# Activate venv
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "❌ .venv not found. Run: python -m venv .venv && pip install -r requirements.txt"
    exit 1
fi

# Install load test deps if missing
python -c "import websockets, aiohttp" 2>/dev/null || {
    echo "📦 Installing load test deps..."
    pip install websockets aiohttp --quiet
}

# ── Config (edit these) ───────────────────────────────────────────
ROOMS=5          # simultaneous rooms
PLAYERS=2        # players per room
DURATION=30      # seconds each room stays alive
URL="http://localhost:8000"   # change to prod URL if needed

# Uncomment for production run (paste a real Firebase ID token):
# export FIREBASE_TOKEN="your_firebase_id_token_here"
# URL="https://api.leastcountrush.online"

# ─────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║       Least Count Rush — Load Test Runner        ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║  Rooms    : $ROOMS                                    ║"
echo "║  Players  : $PLAYERS per room  →  $((ROOMS * PLAYERS)) total WS          ║"
echo "║  Duration : ${DURATION}s per room                       ║"
echo "║  Target   : $URL  ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# Wait for server to be ready
echo "⏳ Waiting for server at $URL ..."
for i in $(seq 1 10); do
    if curl -sf "$URL/health" > /dev/null 2>&1; then
        echo "✅ Server is up!"
        break
    fi
    if [ "$i" -eq 10 ]; then
        echo "❌ Server not reachable after 10s. Start Terminal 1 first: ./run_load_test_server.sh"
        exit 1
    fi
    sleep 1
done

echo ""

# ── Run tests (light → medium → heavy) ───────────────────────────
echo "▶ Running light test  (${ROOMS} rooms × ${PLAYERS} players × ${DURATION}s)..."
python load_test.py --rooms "$ROOMS" --players "$PLAYERS" --duration "$DURATION" --url "$URL"

read -r -p "▶ Run medium test (10 rooms × 3 players × 30s)? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    python load_test.py --rooms 10 --players 3 --duration 30 --url "$URL"
fi

read -r -p "▶ Run heavy test  (20 rooms × 4 players × 45s)? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    python load_test.py --rooms 20 --players 4 --duration 45 --url "$URL"
fi

echo ""
echo "🏁 Load test complete."
