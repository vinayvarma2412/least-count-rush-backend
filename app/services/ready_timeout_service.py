"""Remove public-lobby players who do not tap Ready before their deadline."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.api.websocket.connection_manager import manager
from app.services.bot_fill_service import clear_room_safely, set_if_absent, sync_room_safely
from app.services.ready_timeout import (
    READY_TIMEOUT_INDEX,
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
    # Remove the seat first. Notifying the socket used to happen before this,
    # so a stuck send never reached the removal.
    removed = await room_service.force_remove_player(room_id, player_id)
    if removed:
        get_room_logger(room_id).info("player_removed_ready_timeout", {
            "player_id": player_id,
        })
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
        try:
            await manager.send_personal_message(message, conn)
        except Exception:
            logger.warning(
                "ready_timeout_notify_failed room_id=%s player_id=%s",
                room_id,
                player_id,
            )
        # Drop the socket from the room without closing it here. Closing while
        # this task holds the room lock can deadlock the receive loop, which
        # takes the same lock to finish leaving. The client disconnects after
        # the removal message.
        manager.disconnect(conn)
    return removed


async def evict_lone_offline_bots(room_id: str) -> bool:
    """Remove bots as soon as they are offline. Delete the room if it is empty.

    Caller holds the room lock. A connected human keeps the room. An offline
    bot does not, including when that bot is the only player left.
    """
    from app.services.bot_fill_service import _bot_player_ids
    from app.services.ready_timeout import _is_public, offline_bot_ids

    room = await room_service.get_room_raw(room_id)
    if not room or not _is_public(room):
        return False
    people = list(room.get("players") or []) + list(room.get("waiting_players") or [])
    active = [player for player in people if not player.get("is_exited")]
    if not active:
        return False

    flagged = {
        player.get("player_id")
        for player in active
        if player.get("is_bot") and player.get("player_id")
    }
    looked_up = await _bot_player_ids(
        [player.get("player_id") for player in active if player.get("player_id")]
    )
    bot_ids = set(flagged)
    if looked_up:
        bot_ids |= looked_up
    offline_ids = offline_bot_ids(people, bot_ids)
    if not offline_ids:
        return False

    for player_id in offline_ids:
        await room_service.force_remove_player(room_id, player_id)
    remaining = await room_service.get_room_raw(room_id)
    if remaining is None:
        await clear_room_safely(room_id)
    get_room_logger(room_id).info("offline_bots_removed", {
        "player_ids": offline_ids,
        "room_deleted": remaining is None,
    })
    return True


async def kick_expired_ready_players(room_id: str) -> bool:
    """Remove players whose ready countdown has finished. Caller holds the room lock."""
    room = await room_service.get_room_raw(room_id)
    if not room:
        return False
    now = datetime.now(timezone.utc)
    people = list(room.get("players") or []) + list(room.get("waiting_players") or [])
    kicked = False
    for player_id in expired_unready_player_ids(people, now):
        if await _kick_unready_player(room_id, player_id):
            kicked = True
    return kicked


async def _process_room(room_id: str) -> None:
    async with get_room_lock(room_id):
        # Kick from the deadlines already stored. Refresh used to clear an
        # expired deadline in this same turn, so the removal check saw nothing.
        kicked = await kick_expired_ready_players(room_id)
        changed = await room_service.refresh_public_ready_deadlines(room_id)
        if await kick_expired_ready_players(room_id):
            kicked = True

        if kicked:
            await room_service.refresh_public_ready_deadlines(room_id)

        evicted = await evict_lone_offline_bots(room_id)
        updated = await room_service.get_room(room_id)
        if updated and (kicked or changed or evicted):
            await manager.broadcast_to_room({
                "type": "room_update",
                "data": updated.model_dump(mode="json"),
            }, room_id)
            await sync_room_safely(updated)
        elif updated is None and (kicked or evicted):
            await clear_room_safely(room_id)


async def process_due() -> None:
    public_ids = await redis_client.smembers("rooms:public_index")
    watched_ids = await redis_client.smembers(READY_TIMEOUT_INDEX)
    room_ids = set(public_ids or []) | set(watched_ids or [])
    for room_id in room_ids:
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
