"""Remove public-lobby players who do not tap Ready before their deadline."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.api.websocket.connection_manager import manager
from app.services.bot_fill_service import clear_room_safely, set_if_absent, sync_room_safely
from app.services.ready_timeout import (
    READY_TIMEOUT_MESSAGE,
    READY_TIMEOUT_REASON,
    expired_unready_player_ids,
)
from app.services.redis_client import redis_client
from app.services.room_service import room_service
from app.utils.lock import get_room_lock
from app.utils.room_logger import get_room_logger

logger = logging.getLogger(__name__)

_LOCK_TTL_SECONDS = 15


def _lock_key(room_id: str) -> str:
    return f"ready_timeout:lock:{room_id}"


async def _kick_unready_player(room_id: str, player_id: str) -> bool:
    await room_service.note_public_lobby_leave(room_id, player_id)
    message = {
        "type": "player_removed",
        "message": READY_TIMEOUT_MESSAGE,
        "data": {"reason": READY_TIMEOUT_REASON},
    }
    connections = [
        conn
        for conn, pid in list(manager.connection_players.items())
        if pid == player_id and manager.connection_rooms.get(conn) == room_id
    ]
    for conn in connections:
        await manager.send_personal_message(message, conn)

    removed = await room_service.force_remove_player(room_id, player_id)
    for conn in connections:
        # Drop the socket from the room without closing it here. Closing while
        # this task holds the room lock can deadlock the receive loop, which
        # takes the same lock to finish leaving. The client disconnects after
        # the removal message.
        manager.disconnect(conn)
    if removed:
        get_room_logger(room_id).info("player_removed_ready_timeout", {
            "player_id": player_id,
        })
    return removed


async def _process_room(room_id: str) -> None:
    async with get_room_lock(room_id):
        changed = await room_service.refresh_public_ready_deadlines(room_id)
        room = await room_service.get_room_raw(room_id)
        if not room:
            return

        now = datetime.now(timezone.utc)
        people = list(room.get("players") or []) + list(room.get("waiting_players") or [])
        kicked = False
        for player_id in expired_unready_player_ids(people, now):
            if await _kick_unready_player(room_id, player_id):
                kicked = True

        if kicked:
            await room_service.refresh_public_ready_deadlines(room_id)

        updated = await room_service.get_room(room_id)
        if updated and (kicked or changed):
            await manager.broadcast_to_room({
                "type": "room_update",
                "data": updated.model_dump(mode="json"),
            }, room_id)
            await sync_room_safely(updated)
        elif kicked and updated is None:
            await clear_room_safely(room_id)


async def process_due() -> None:
    room_ids = await redis_client.smembers("rooms:public_index")
    for room_id in list(room_ids):
        locked = await set_if_absent(redis_client, _lock_key(room_id), "1", ex=_LOCK_TTL_SECONDS)
        if not locked:
            continue
        try:
            await _process_room(room_id)
        except Exception as exc:
            logger.error("ready_timeout_room_failed room_id=%s error=%s", room_id, exc)
        finally:
            await redis_client.delete(_lock_key(room_id))


async def ready_timeout_loop() -> None:
    import asyncio

    while True:
        try:
            await asyncio.sleep(2)
            await process_due()
        except Exception as exc:
            logger.error("ready_timeout_loop_error %s", exc)
