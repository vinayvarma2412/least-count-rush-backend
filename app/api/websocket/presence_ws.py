"""
WebSocket handler for user presence tracking.

Flow:
  1. Client connects to /ws/presence?token=<firebase_id_token>
  2. Backend verifies token, looks up the user in DB, sets is_online=True
  3. Client sends periodic {type: ping} to keep alive (same as room WS)
  4. On any disconnect (graceful, network drop, app kill), backend sets
     is_online=False and last_seen_at=now()
"""

import asyncio
from datetime import datetime, timezone
from fastapi import WebSocket, WebSocketDisconnect

from app.database import AsyncSessionLocal
from app.utils.firebase_auth import verify_firebase_token
from app.services.user_service import user_service
import logging

logger = logging.getLogger(__name__)

# Per-connection last-ping timestamp for heartbeat watchdog.
_last_ping: dict[int, datetime] = {}  # key = id(websocket)
_watchdog_tasks: dict[int, asyncio.Task] = {}
_active_connections: dict[str, WebSocket] = {}  # key = firebase_uid

_PING_TIMEOUT_SECONDS = 30  # More lenient than game WS


async def _set_user_presence(firebase_uid: str, is_online: bool):
    """Update is_online and, when going offline, last_seen_at."""
    async with AsyncSessionLocal() as db:
        try:
            user = await user_service.get_user_by_firebase_id(db, firebase_uid)
            if not user:
                logger.warning(f"[presence_ws] User not found for uid={firebase_uid}")
                return
            user.is_online = is_online
            if not is_online:
                user.last_seen_at = datetime.now(timezone.utc)
            db.add(user)
            await db.commit()
            logger.info(f"[presence_ws] User {firebase_uid} is_online={is_online} saved to DB")
        except Exception as e:
            await db.rollback()
            logger.error(f"[presence_ws] DB error setting presence for {firebase_uid}: {e}")


async def _watchdog(websocket: WebSocket, firebase_uid: str):
    """Close the connection if no ping arrives within _PING_TIMEOUT_SECONDS."""
    ws_key = id(websocket)
    await asyncio.sleep(_PING_TIMEOUT_SECONDS)
    while True:
        try:
            last = _last_ping.get(ws_key)
            if last is None:
                logger.warning(f"[presence_ws] No ping from {firebase_uid}, closing")
                await websocket.close()
                break
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            if elapsed > _PING_TIMEOUT_SECONDS:
                logger.warning(f"[presence_ws] Ping timeout ({elapsed:.0f}s) for {firebase_uid}, closing")
                await websocket.close()
                break
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break
        except Exception:
            break

    _last_ping.pop(ws_key, None)
    _watchdog_tasks.pop(ws_key, None)


async def presence_endpoint(websocket: WebSocket, token: str):
    """
    Presence WebSocket endpoint.
    URL: /ws/presence?token=<firebase_id_token>
    """
    # --- Auth ---
    try:
        decoded = verify_firebase_token(token)
        firebase_uid = decoded.get("uid")
        if not firebase_uid:
            await websocket.close(code=4001, reason="Invalid token")
            return
    except Exception as e:
        logger.warning(f"[presence_ws] Token verification failed: {e}")
        await websocket.close(code=4001, reason="Invalid token")
        return

    await websocket.accept()
    logger.info(f"[presence_ws] User {firebase_uid} connected")

    # --- Conflict Resolution: Force logout existing connection ---
    old_ws = _active_connections.get(firebase_uid)
    if old_ws is not None:
        logger.info(f"[presence_ws] User {firebase_uid} logged in from another device. Forcing logout on old connection.")
        try:
            await old_ws.send_json({"type": "force_logout", "reason": "logged_in_elsewhere"})
            await asyncio.sleep(0.5) # Give it a moment to send before closing
            await old_ws.close(code=4000, reason="logged_in_elsewhere")
        except Exception as e:
            logger.warning(f"[presence_ws] Error closing old connection for {firebase_uid}: {e}")

    # --- Track New Connection ---
    _active_connections[firebase_uid] = websocket
    ws_key = id(websocket)

    # --- Mark online ---
    await _set_user_presence(firebase_uid, is_online=True)

    # --- Start watchdog ---
    _last_ping[ws_key] = datetime.now(timezone.utc)
    _watchdog_tasks[ws_key] = asyncio.create_task(_watchdog(websocket, firebase_uid))

    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=_PING_TIMEOUT_SECONDS + 5,
                )
                if data.get("type") == "ping":
                    _last_ping[ws_key] = datetime.now(timezone.utc)
                    try:
                        await websocket.send_json({"type": "pong"})
                    except Exception:
                        break
            except asyncio.TimeoutError:
                # No message at all — connection probably dead
                break
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.error(f"[presence_ws] Receive error for {firebase_uid}: {e}")
                break

    except WebSocketDisconnect:
        logger.info(f"[presence_ws] User {firebase_uid} disconnected (WebSocketDisconnect)")
    except Exception as e:
        logger.error(f"[presence_ws] Unexpected error for {firebase_uid}: {e}")
    finally:
        # Cancel watchdog
        task = _watchdog_tasks.pop(ws_key, None)
        if task:
            task.cancel()
        _last_ping.pop(ws_key, None)

        # Remove from active connections only if we are the currently tracked connection
        if _active_connections.get(firebase_uid) == websocket:
            _active_connections.pop(firebase_uid, None)
            # Mark offline
            await _set_user_presence(firebase_uid, is_online=False)
