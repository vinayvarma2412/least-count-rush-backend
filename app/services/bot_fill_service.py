"""Ask the bot server to fill a public room that is still alone.

The wait length lives in app.config.PUBLIC_ROOM_BOT_WAIT_SECONDS.
A Redis deadline is stored when a public room is waiting with exactly one
connected player. A background loop fires due deadlines once.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Awaitable, Callable, Optional

from app.config import BOT_FILL_RETRY_SECONDS, PUBLIC_ROOM_BOT_WAIT_SECONDS, settings

logger = logging.getLogger(__name__)

INDEX_KEY = "botfill:index"
_ASSIGNED_TTL_SECONDS = 4 * 3600

LoadRoom = Callable[[str], Awaitable[Optional[dict]]]
Poster = Callable[[dict], Awaitable[bool]]
Clock = Callable[[], float]


def room_snapshot(room) -> dict:
    """Plain dict the scheduler can store and the bot server can be called with."""
    status = getattr(room.status, "value", room.status)
    room_type = getattr(room.room_type, "value", room.room_type)
    people = list(room.players) + list(getattr(room, "waiting_players", None) or [])
    connected = sum(1 for player in people if getattr(player, "is_connected", False))
    return {
        "room_id": room.room_id,
        "room_code": getattr(room, "room_code", "") or "",
        "status": status,
        "room_type": room_type,
        "server_url": getattr(room, "server_url", None) or os.getenv("PUBLIC_SERVER_URL") or "",
        "connected_players": connected,
    }


def _should_wait(snapshot: dict) -> bool:
    return (
        snapshot.get("room_type") == "public"
        and snapshot.get("status") == "waiting"
        and snapshot.get("connected_players") == 1
    )


async def set_if_absent(store, key: str, value: str, ex: int) -> bool:
    """SET NX with a TTL. Works for redis-py and the in-memory store."""
    try:
        result = await store.set(key, value, nx=True, ex=ex)
    except TypeError:
        current = await store.get(key)
        if current is not None:
            return False
        await store.set(key, value, ex=ex)
        return True
    return result is True or result == 1


class BotFillService:
    def __init__(
        self,
        store,
        poster: Poster,
        load_room: LoadRoom,
        wait_seconds: int = PUBLIC_ROOM_BOT_WAIT_SECONDS,
        retry_seconds: int = BOT_FILL_RETRY_SECONDS,
        clock: Optional[Clock] = None,
    ):
        self._store = store
        self._poster = poster
        self._load_room = load_room
        self.wait_seconds = wait_seconds
        self.retry_seconds = retry_seconds
        self._clock = clock or time.time

    def _wait_key(self, room_id: str) -> str:
        return f"botfill:wait:{room_id}"

    def _assigned_key(self, room_id: str) -> str:
        return f"botfill:assigned:{room_id}"

    def _lock_key(self, room_id: str) -> str:
        return f"botfill:lock:{room_id}"

    async def sync_snapshot(self, snapshot: dict) -> None:
        """Schedule, keep, or cancel the wait for this room."""
        room_id = snapshot["room_id"]
        if await self._is_assigned(room_id):
            await self._clear_wait(room_id)
            return
        if not _should_wait(snapshot):
            await self._clear_wait(room_id)
            return
        await self._ensure_wait(room_id)

    async def clear_room(self, room_id: str) -> None:
        await self._clear_wait(room_id)
        await self._store.delete(self._assigned_key(room_id))

    async def process_due(self) -> list[str]:
        """Fire every deadline that has passed. Returns room ids that were requested."""
        requested: list[str] = []
        room_ids = await self._store.smembers(INDEX_KEY)
        for room_id in list(room_ids):
            if await self._process_one(room_id):
                requested.append(room_id)
        return requested

    async def _process_one(self, room_id: str) -> bool:
        raw = await self._store.get(self._wait_key(room_id))
        if not raw:
            await self._store.srem(INDEX_KEY, room_id)
            return False
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            await self._clear_wait(room_id)
            return False
        if float(record.get("due_at", 0)) > self._clock():
            return False

        if await self._is_assigned(room_id):
            await self._clear_wait(room_id)
            return False

        snapshot = await self._load_room(room_id)
        if snapshot is None:
            await self.clear_room(room_id)
            return False
        if not _should_wait(snapshot):
            await self._clear_wait(room_id)
            return False

        locked = await set_if_absent(self._store, self._lock_key(room_id), "1", ex=30)
        if not locked:
            return False
        try:
            # Re-check after the lock so two loops cannot both call the bot server.
            if await self._is_assigned(room_id):
                await self._clear_wait(room_id)
                return False
            fresh = await self._load_room(room_id)
            if fresh is None or not _should_wait(fresh):
                await self._clear_wait(room_id)
                return False
            try:
                ok = await self._poster(fresh)
            except Exception as exc:
                logger.warning("bot_fill_request_failed", extra={"room_id": room_id, "error": str(exc)})
                ok = False
            if ok:
                await self._store.set(self._assigned_key(room_id), "1", ex=_ASSIGNED_TTL_SECONDS)
                await self._clear_wait(room_id)
                logger.info("bot_fill_requested room_id=%s", room_id)
                return True
            retries = int(record.get("retries", 0))
            if retries >= 1:
                logger.warning("bot_fill_gave_up room_id=%s", room_id)
                await self._clear_wait(room_id)
                return False
            record["retries"] = retries + 1
            record["due_at"] = self._clock() + self.retry_seconds
            await self._store.set(
                self._wait_key(room_id),
                json.dumps(record),
                ex=self.retry_seconds + 120,
            )
            return False
        finally:
            await self._store.delete(self._lock_key(room_id))

    async def _is_assigned(self, room_id: str) -> bool:
        return await self._store.get(self._assigned_key(room_id)) is not None

    async def _ensure_wait(self, room_id: str) -> None:
        payload = json.dumps({"due_at": self._clock() + self.wait_seconds, "retries": 0})
        created = await set_if_absent(
            self._store,
            self._wait_key(room_id),
            payload,
            ex=self.wait_seconds + 120,
        )
        await self._store.sadd(INDEX_KEY, room_id)
        if created:
            logger.info("bot_fill_scheduled room_id=%s wait_seconds=%s", room_id, self.wait_seconds)

    async def _clear_wait(self, room_id: str) -> None:
        await self._store.delete(self._wait_key(room_id))
        await self._store.srem(INDEX_KEY, room_id)


async def _post_join(snapshot: dict) -> bool:
    if not settings.bot_server_url or not settings.bot_server_secret:
        logger.warning("bot_fill_skipped_not_configured")
        return False
    import httpx

    url = settings.bot_server_url.rstrip("/") + "/internal/bots/join"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            url,
            json={
                "room_id": snapshot["room_id"],
                "room_code": snapshot.get("room_code") or "",
                "server_url": snapshot.get("server_url") or "",
            },
            headers={"X-Bot-Server-Secret": settings.bot_server_secret},
        )
    if response.status_code >= 300:
        logger.warning(
            "bot_fill_http_error status=%s body=%s",
            response.status_code,
            response.text[:300],
        )
        return False
    return True


async def _load_room(room_id: str) -> Optional[dict]:
    from app.services.room_service import room_service

    room = await room_service.get_room(room_id)
    if room is None:
        return None
    return room_snapshot(room)


_service: Optional[BotFillService] = None


def get_bot_fill_service() -> BotFillService:
    global _service
    if _service is None:
        from app.services.redis_client import redis_client

        _service = BotFillService(redis_client, _post_join, _load_room)
    return _service


async def sync_room_safely(room) -> None:
    if room is None:
        return
    try:
        await get_bot_fill_service().sync_snapshot(room_snapshot(room))
    except Exception as exc:
        logger.warning("bot_fill_sync_failed room_id=%s error=%s", getattr(room, "room_id", ""), exc)


async def clear_room_safely(room_id: str) -> None:
    try:
        await get_bot_fill_service().clear_room(room_id)
    except Exception as exc:
        logger.warning("bot_fill_clear_failed room_id=%s error=%s", room_id, exc)


async def bot_fill_loop() -> None:
    import asyncio

    while True:
        try:
            await asyncio.sleep(5)
            await get_bot_fill_service().process_due()
        except Exception as exc:
            logger.error("bot_fill_loop_error %s", exc)


async def ensure_online_bot_role() -> None:
    """Add the online_bot enum value if this database does not have it yet."""
    from sqlalchemy import text

    from app.database import engine
    from app.utils.room_logger import global_log

    try:
        async with engine.connect() as conn:
            autocommit = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await autocommit.execute(
                text("ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'online_bot'")
            )
        global_log.info("online_bot_role_ready", {})
    except Exception as exc:
        global_log.warning("online_bot_role_ensure_failed", {"error": str(exc)})
