#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║          Least Count Rush — WebSocket Load Tester               ║
║                                                                  ║
║  Simulates N concurrent rooms, each with M players.              ║
║  Tests: WS connect, join, ping heartbeat, game messages.         ║
║                                                                  ║
║  PREREQUISITES                                                   ║
║    pip install websockets aiohttp                                ║
║                                                                  ║
║  RUN (local server with auth bypass):                            ║
║    LOAD_TEST_BYPASS_AUTH=1 python load_test.py                   ║
║                                                                  ║
║  RUN (against real server with a real Firebase token):           ║
║    FIREBASE_TOKEN=<id_token> python load_test.py                 ║
║                                                                  ║
║  ARGS                                                            ║
║    --rooms N      simultaneous rooms  (default: 5)               ║
║    --players N    players per room    (default: 2)               ║
║    --duration N   seconds per room   (default: 30)              ║
║    --url URL      server base URL    (default: http://localhost:8000) ║
╚══════════════════════════════════════════════════════════════════╝
"""

import argparse
import asyncio
import json
import os
import time
import uuid
import random
import statistics
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import websockets
    import aiohttp
except ImportError:
    print("Missing deps. Run: pip install websockets aiohttp")
    raise

# ── ANSI colours ──────────────────────────────────────────────────────────────
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

# ── Config (overridable via env or --url arg) ──────────────────────────────────
BASE_URL    = os.getenv("BASE_URL", "http://localhost:8000")
WS_BASE_URL = os.getenv("WS_BASE_URL", "ws://localhost:8000")
FIREBASE_TOKEN = os.getenv("FIREBASE_TOKEN", "load_test_mock_token")
BYPASS_AUTH    = os.getenv("LOAD_TEST_BYPASS_AUTH", "0") == "1"

# ── Result dataclasses ─────────────────────────────────────────────────────────
@dataclass
class PlayerResult:
    player_id: str
    room_index: int
    connected: bool = False
    joined: bool = False
    pings_sent: int = 0
    pongs_received: int = 0
    messages_received: int = 0
    errors: List[str] = field(default_factory=list)
    connect_latency_ms: float = 0.0
    join_latency_ms: float = 0.0
    disconnect_clean: bool = False

@dataclass
class RoomResult:
    room_index: int
    room_id: Optional[str] = None
    room_code: Optional[str] = None
    created: bool = False
    errors: List[str] = field(default_factory=list)
    player_results: List[PlayerResult] = field(default_factory=list)
    creation_latency_ms: float = 0.0


# ── HTTP helpers ──────────────────────────────────────────────────────────────
async def create_room_http(session: aiohttp.ClientSession, room_index: int) -> RoomResult:
    result = RoomResult(room_index=room_index)
    payload = {
        "room_name": f"LoadTest Room {room_index}",
        "max_players": 4,
        "game_mode": "classic",
        "score_limit": 100,
        "creator_app_version": "load_test",
        "creator_build_number": "0",
    }
    headers = {"Authorization": f"Bearer {FIREBASE_TOKEN}"}
    t0 = time.perf_counter()
    try:
        async with session.post(
            f"{BASE_URL}/api/rooms",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            result.creation_latency_ms = (time.perf_counter() - t0) * 1000
            if resp.status == 201:
                body = await resp.json()
                result.room_id   = body["room_id"]
                result.room_code = body.get("room_code", "")
                result.created   = True
            else:
                text = await resp.text()
                result.errors.append(f"HTTP {resp.status}: {text[:200]}")
    except Exception as e:
        result.errors.append(f"create_room_error: {e}")
    return result


# ── WebSocket player simulation ────────────────────────────────────────────────
async def simulate_player(
    room_id: str,
    room_index: int,
    player_index: int,
    duration_seconds: int,
) -> PlayerResult:
    player_id   = str(uuid.uuid4())
    player_name = f"Bot_{room_index}_{player_index}"
    result      = PlayerResult(player_id=player_id, room_index=room_index)

    ws_url = f"{WS_BASE_URL}/ws/{room_id}?token={FIREBASE_TOKEN}"

    t_connect = time.perf_counter()
    try:
        async with websockets.connect(
            ws_url,
            ping_interval=None,   # manual pings
            ping_timeout=None,
            open_timeout=10,
            close_timeout=5,
        ) as ws:
            result.connect_latency_ms = (time.perf_counter() - t_connect) * 1000
            result.connected = True

            # ── Join the room ─────────────────────────────────────────────
            t_join = time.perf_counter()
            await ws.send(json.dumps({
                "type": "join_room",
                "player_id": player_id,
                "player_name": player_name,
                "avatar_seed": str(random.randint(1, 999)),
            }))

            end_time = time.perf_counter() + duration_seconds

            async def send_pings():
                while time.perf_counter() < end_time:
                    await asyncio.sleep(5)
                    if time.perf_counter() >= end_time:
                        break
                    try:
                        await ws.send(json.dumps({"type": "ping"}))
                        result.pings_sent += 1
                    except Exception:
                        break

            async def receive_messages():
                joined_ack = False
                try:
                    while time.perf_counter() < end_time:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                            msg = json.loads(raw)
                            result.messages_received += 1
                            mtype = msg.get("type")
                            if mtype == "room_update" and not joined_ack:
                                joined_ack = True
                                result.join_latency_ms = (time.perf_counter() - t_join) * 1000
                                result.joined = True
                            elif mtype == "pong":
                                result.pongs_received += 1
                            elif mtype == "error":
                                result.errors.append(f"server_error: {msg.get('message','?')}")
                        except asyncio.TimeoutError:
                            continue
                        except websockets.exceptions.ConnectionClosed as cc:
                            # Code 4001 = auth rejected by server
                            if hasattr(cc, 'rcvd') and cc.rcvd and cc.rcvd.code == 4001:
                                result.errors.append("auth_rejected_4001: token invalid (use LOAD_TEST_BYPASS_AUTH=1 on server)")
                            break
                except Exception as e:
                    result.errors.append(f"recv_error: {e}")

            await asyncio.gather(send_pings(), receive_messages(), return_exceptions=True)

            # Graceful leave
            try:
                await ws.send(json.dumps({"type": "leave_room"}))
                result.disconnect_clean = True
            except Exception:
                pass

    except websockets.exceptions.InvalidHandshake as e:
        result.errors.append(f"handshake_failed: {e}")
    except websockets.exceptions.ConnectionClosedError as e:
        # Server closed connection without proper close frame (e.g. auth rejection)
        code = e.rcvd.code if (hasattr(e, 'rcvd') and e.rcvd) else '?'
        if code == 4001:
            result.errors.append("auth_rejected_4001: token invalid — start server with LOAD_TEST_BYPASS_AUTH=1")
        else:
            result.errors.append(f"connection_closed_abruptly: code={code}")
    except OSError as e:
        result.errors.append(f"os_error: {e}")
    except Exception as e:
        result.errors.append(f"ws_error: {e}")

    return result


# ── Room simulation ────────────────────────────────────────────────────────────
async def simulate_room(
    session: aiohttp.ClientSession,
    room_index: int,
    num_players: int,
    duration_seconds: int,
) -> RoomResult:
    room_result = await create_room_http(session, room_index)
    if not room_result.created:
        return room_result

    async def staggered_player(i):
        await asyncio.sleep(i * 0.1)
        return await simulate_player(room_result.room_id, room_index, i, duration_seconds)

    tasks = [staggered_player(i) for i in range(num_players)]
    player_results = await asyncio.gather(*tasks, return_exceptions=True)

    for pr in player_results:
        if isinstance(pr, Exception):
            room_result.errors.append(f"player_task_exception: {pr}")
        else:
            room_result.player_results.append(pr)

    return room_result


# ── Report ─────────────────────────────────────────────────────────────────────
def print_report(room_results: List[RoomResult], wall_seconds: float, args):
    print()
    print(f"{BOLD}{'═'*65}{RESET}")
    print(f"{BOLD}  LOAD TEST RESULTS — {args.rooms} rooms × {args.players} players × {args.duration}s{RESET}")
    print(f"{BOLD}{'═'*65}{RESET}")

    total_rooms       = len(room_results)
    created_rooms     = sum(1 for r in room_results if r.created)
    total_target      = args.rooms * args.players
    connected_players = sum(p.connected for r in room_results for p in r.player_results)
    joined_players    = sum(p.joined for r in room_results for p in r.player_results)
    total_pings       = sum(p.pings_sent for r in room_results for p in r.player_results)
    total_pongs       = sum(p.pongs_received for r in room_results for p in r.player_results)
    total_msgs        = sum(p.messages_received for r in room_results for p in r.player_results)
    clean_disconnects = sum(p.disconnect_clean for r in room_results for p in r.player_results)
    all_errors: List[str] = []
    for r in room_results:
        all_errors.extend(r.errors)
        for p in r.player_results:
            all_errors.extend(p.errors)

    connect_lats = [p.connect_latency_ms for r in room_results for p in r.player_results if p.connected]
    join_lats    = [p.join_latency_ms for r in room_results for p in r.player_results if p.joined]
    room_lats    = [r.creation_latency_ms for r in room_results if r.created]

    def fmt_lat(data):
        if not data: return "N/A"
        s = sorted(data)
        return f"avg={statistics.mean(data):.0f}ms  p95={s[int(len(s)*0.95)]:.0f}ms  max={max(data):.0f}ms"

    pong_ratio  = (total_pongs / total_pings * 100) if total_pings else 0
    error_count = len(all_errors)

    rows = [
        ("Wall time",            f"{wall_seconds:.1f}s",                    True),
        ("Rooms created",        f"{created_rooms}/{total_rooms}",           created_rooms == total_rooms),
        ("Players connected",    f"{connected_players}/{total_target}",      connected_players == total_target),
        ("Players joined",       f"{joined_players}/{total_target}",         joined_players == total_target),
        ("Ping/Pong ratio",      f"{pong_ratio:.1f}%  ({total_pings}→{total_pongs})",  pong_ratio >= 90),
        ("Total messages rx",    f"{total_msgs}",                            True),
        ("Clean disconnects",    f"{clean_disconnects}/{connected_players}", clean_disconnects == connected_players),
        ("Errors",               f"{error_count}",                          error_count == 0),
    ]

    for label, value, ok in rows:
        colour = GREEN if ok else (RED if label == "Errors" and error_count > 0 else YELLOW)
        print(f"  {label:<25} {colour}{value}{RESET}")

    print()
    print(f"  {'Room create latency':<25} {fmt_lat(room_lats)}")
    print(f"  {'WS connect latency':<25} {fmt_lat(connect_lats)}")
    print(f"  {'Join (room_update) lat':<25} {fmt_lat(join_lats)}")

    print()
    print(f"  {'Rm':<4} {'OK':<4} {'Plrs':<6} {'Jnd':<5} {'Pings':<7} {'Pongs':<7} {'Errs'}")
    print(f"  {'──':<4} {'──':<4} {'────':<6} {'───':<5} {'─────':<7} {'─────':<7} {'────'}")
    for r in room_results:
        pps  = len(r.player_results)
        jps  = sum(p.joined for p in r.player_results)
        ps   = sum(p.pings_sent for p in r.player_results)
        po   = sum(p.pongs_received for p in r.player_results)
        errs = len(r.errors) + sum(len(p.errors) for p in r.player_results)
        icon = f"{GREEN}✓{RESET}" if r.created else f"{RED}✗{RESET}"
        print(f"  {r.room_index:<4} {icon}    {pps:<6} {jps:<5} {ps:<7} {po:<7} {errs}")

    if all_errors:
        print()
        print(f"  {RED}── Errors ────────────────────────────────────────────────{RESET}")
        for e in all_errors[:30]:
            print(f"    {RED}• {e}{RESET}")
        if len(all_errors) > 30:
            print(f"    ... and {len(all_errors)-30} more")

    # Verdict
    print()
    if joined_players == total_target and error_count == 0 and pong_ratio >= 90:
        v = f"{GREEN}{BOLD}✅  PASS — Server handled load cleanly{RESET}"
    elif joined_players == 0:
        v = f"{RED}{BOLD}❌  FAIL — No players joined (auth issue? server down?){RESET}"
    elif error_count > total_target * 0.1:
        v = f"{RED}{BOLD}❌  FAIL — >10% error rate{RESET}"
    else:
        v = f"{YELLOW}{BOLD}⚠️   PARTIAL — Some issues, review errors above{RESET}"

    print(f"  {v}")
    print(f"{BOLD}{'═'*65}{RESET}")
    print()


# ── Main ───────────────────────────────────────────────────────────────────────
async def main(args):
    global BASE_URL, WS_BASE_URL
    if args.url:
        BASE_URL    = args.url
        WS_BASE_URL = args.url.replace("https://", "wss://").replace("http://", "ws://")

    print(f"\n{CYAN}{BOLD}  Least Count Rush — Load Test{RESET}")
    print(f"  Target   : {BOLD}{BASE_URL}{RESET}")
    print(f"  Rooms    : {args.rooms}")
    print(f"  Players  : {args.players}/room  →  {args.rooms * args.players} total WS connections")
    print(f"  Duration : {args.duration}s per room")
    print(f"  Auth     : {'BYPASS (mock token)' if BYPASS_AUTH else 'Real Firebase token'}")
    print()

    if BYPASS_AUTH:
        print(f"  {YELLOW}⚠️  Bypass mode — ensure server started with LOAD_TEST_BYPASS_AUTH=1{RESET}\n")

    connector = aiohttp.TCPConnector(limit=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Health check
        try:
            async with session.get(f"{BASE_URL}/health", timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    print(f"  {GREEN}✓ Server healthy{RESET}")
                else:
                    print(f"  {RED}✗ /health → {r.status}{RESET}")
                    return
        except Exception as e:
            print(f"  {RED}✗ Cannot reach server: {e}{RESET}")
            return

        print(f"  Launching {args.rooms} rooms in parallel...\n")
        t0 = time.perf_counter()

        async def staggered_room(i):
            await asyncio.sleep(i * 0.2)
            return await simulate_room(session, i, args.players, args.duration)

        tasks = [staggered_room(i) for i in range(args.rooms)]
        room_results = await asyncio.gather(*tasks, return_exceptions=True)

    wall_time = time.perf_counter() - t0

    final = []
    for i, r in enumerate(room_results):
        if isinstance(r, Exception):
            rr = RoomResult(room_index=i)
            rr.errors.append(f"room_task_exception: {r}")
            final.append(rr)
        else:
            final.append(r)

    print_report(final, wall_time, args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Least Count Rush Load Tester")
    parser.add_argument("--rooms",    type=int, default=5,  help="Simultaneous rooms")
    parser.add_argument("--players",  type=int, default=2,  help="Players per room")
    parser.add_argument("--duration", type=int, default=30, help="Seconds per room")
    parser.add_argument("--url",      type=str,             help="Override server URL (http://...)")
    args = parser.parse_args()
    asyncio.run(main(args))
