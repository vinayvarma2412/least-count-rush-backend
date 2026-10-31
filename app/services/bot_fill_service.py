"""Ask the bot server to fill a public room on a growing timer.

The timer starts when the room is created. The first bot waits
PUBLIC_ROOM_BOT_WAIT_SECONDS, the second waits twice that, and the third
waits three times that. A bot is requested only when exactly one real
player is in the room, that player is connected, and a seat is free.
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
MAX_PUBLIC_ROOM_BOTS = 3

LoadRoom = Callable[[str], Awaitable[Optional[dict]]]
Poster = Callable[[dict], Awaitable[bool]]
Clock = Callable[[], float]


def bot_wait_seconds(bot_number: int, base_seconds: int) -> int:
    """1st bot waits 1x the configured timer, 2nd waits 2x, 3rd waits 3x."""
    return base_seconds * bot_number


def _player_dict(player) -> dict:
    if isinstance(player, dict):
        return player
    if hasattr(player, "model_dump"):
        return player.model_dump()
    return {
        "player_id": getattr(player, "player_id", ""),
        "is_connected": getattr(player, "is_connected", False),
        "is_exited": getattr(player, "is_exited", False),
        "disconnect_at": getattr(player, "disconnect_at", None),
    }


def room_snapshot(room, bot_ids: Optional[set] = None) -> dict:
    """Plain dict the scheduler can store and the bot server can be called with."""
    status = getattr(room.status, "value", room.status)
    room_type = getattr(room.room_type, "value", room.room_type)
    people = list(room.players) + list(getattr(room, "waiting_players", None) or [])
    bots = bot_ids or set()
    active_humans = 0
    humans_present = 0
    bots_present = 0
    seated = 0
    for player in people:
        data = _player_dict(player)
        if data.get("is_exited"):
            continue
        seated += 1
        player_id = data.get("player_id") or ""
        if player_id in bots:
            bots_present += 1
            continue
        humans_present += 1
        if data.get("is_connected") and not data.get("disconnect_at"):
            active_humans += 1
    return {
        "room_id": room.room_id,
        "room_code": getattr(room, "room_code", "") or "",
        "status": status,
        "room_type": room_type,
        "server_url": getattr(room, "server_url", None) or os.getenv("PUBLIC_SERVER_URL") or "",
        "active_humans": active_humans,
        "humans_present": humans_present,
        "bots_present": bots_present,
        "seated": seated,
        "max_players": getattr(room, "max_players", None) or 6,
        "roles_known": True,
    }


def _has_a_seat(snapshot: dict) -> bool:
    return int(snapshot.get("seated") or 0) < int(snapshot.get("max_players") or 6)


def _below_bot_cap(snapshot: dict) -> bool:
    return int(snapshot.get("bots_present") or 0) < MAX_PUBLIC_ROOM_BOTS


def _one_real_player(snapshot: dict) -> bool:
    return int(snapshot.get("humans_present") or 0) <= 1


def _should_schedule(snapshot: dict) -> bool:
    """Keep the timer while one real player, or nobody yet, can still be filled."""
    return (
        snapshot.get("room_type") == "public"
        and snapshot.get("status") == "waiting"
        and _has_a_seat(snapshot)
        and _below_bot_cap(snapshot)
        and _one_real_player(snapshot)
    )


def _can_add_bot(snapshot: dict) -> bool:
    """A bot joins only when the single real player is still connected."""
    if snapshot.get("roles_known") is False:
        return False
    return (
        _should_schedule(snapshot)
        and int(snapshot.get("humans_present") or 0) == 1
        and int(snapshot.get("active_humans") or 0) == 1
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

    def _lock_key(self, room_id: str) -> str:
        return f"botfill:lock:{room_id}"

    async def sync_snapshot(self, snapshot: dict) -> None:
        """Schedule, keep, or cancel the wait for this room."""
        room_id = snapshot["room_id"]
        if not _should_schedule(snapshot):
            await self._clear_wait(room_id)
            return
        await self._ensure_wait(room_id, int(snapshot.get("bots_present") or 0))

    async def clear_room(self, room_id: str) -> None:
        await self._clear_wait(room_id)

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

        snapshot = await self._load_room(room_id)
        if snapshot is None:
            await self.clear_room(room_id)
            return False
        if not _should_schedule(snapshot):
            await self._clear_wait(room_id)
            return False

        locked = await set_if_absent(self._store, self._lock_key(room_id), "1", ex=30)
        if not locked:
            return False
        try:
            fresh = await self._load_room(room_id)
            if fresh is None or not _should_schedule(fresh):
                await self._clear_wait(room_id)
                return False
            scheduled_for = max(
                int(record.get("scheduled_for") or 1),
                int(fresh.get("bots_present") or 0) + 1,
            )
            if scheduled_for > MAX_PUBLIC_ROOM_BOTS:
                await self._clear_wait(room_id)
                return False
            if not _can_add_bot(fresh):
                await self._postpone(room_id, record, scheduled_for)
                return False
            try:
                ok = await self._poster(fresh)
            except Exception as exc:
                logger.warning("bot_fill_request_failed room_id=%s error=%s", room_id, exc)
                ok = False
            if ok:
                logger.info(
                    "bot_fill_requested room_id=%s bot_number=%s",
                    room_id,
                    scheduled_for,
                )
                if scheduled_for >= MAX_PUBLIC_ROOM_BOTS:
                    await self._clear_wait(room_id)
                else:
                    await self._write_wait(room_id, scheduled_for + 1, retries=0)
                return True
            retries = int(record.get("retries", 0)) + 1
            if retries > 1:
                await self._postpone(room_id, record, scheduled_for)
                return False
            record["retries"] = retries
            record["scheduled_for"] = scheduled_for
            record["due_at"] = self._clock() + self.retry_seconds
            await self._store.set(
                self._wait_key(room_id),
                json.dumps(record),
                ex=self.retry_seconds + 120,
            )
            return False
        finally:
            await self._store.delete(self._lock_key(room_id))

    async def _postpone(self, room_id: str, record: dict, scheduled_for: int) -> None:
        """No active player, or the request failed twice. Wait this step again."""
        record["retries"] = 0
        record["scheduled_for"] = scheduled_for
        record["due_at"] = self._clock() + bot_wait_seconds(scheduled_for, self.wait_seconds)
        await self._store.set(
            self._wait_key(room_id),
            json.dumps(record),
            ex=bot_wait_seconds(scheduled_for, self.wait_seconds) + 120,
        )

    async def _ensure_wait(self, room_id: str, bots_present: int) -> None:
        scheduled_for = bots_present + 1
        if scheduled_for > MAX_PUBLIC_ROOM_BOTS:
            await self._clear_wait(room_id)
            return
        created = await self._write_wait(room_id, scheduled_for, retries=0, only_if_absent=True)
        await self._store.sadd(INDEX_KEY, room_id)
        if created:
            logger.info(
                "bot_fill_scheduled room_id=%s bot_number=%s wait_seconds=%s",
                room_id,
                scheduled_for,
                bot_wait_seconds(scheduled_for, self.wait_seconds),
            )

    async def _write_wait(
        self,
        room_id: str,
        scheduled_for: int,
        retries: int,
        only_if_absent: bool = False,
    ) -> bool:
        wait = bot_wait_seconds(scheduled_for, self.wait_seconds)
        payload = json.dumps({
            "due_at": self._clock() + wait,
            "retries": retries,
            "scheduled_for": scheduled_for,
        })
        if only_if_absent:
            return await set_if_absent(
                self._store,
                self._wait_key(room_id),
                payload,
                ex=wait + 120,
            )
        await self._store.set(self._wait_key(room_id), payload, ex=wait + 120)
        await self._store.sadd(INDEX_KEY, room_id)
        return True

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


async def _bot_player_ids(player_ids: list[str]) -> Optional[set]:
    """Firebase uids in this room that are bots. None when the lookup fails."""
    if not player_ids:
        return set()
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.db_models import User, UserRoleEnum

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User.user_id).where(
                    User.user_id.in_(player_ids),
                    User.role.in_([UserRoleEnum.bot, UserRoleEnum.online_bot]),
                )
            )
            return {row[0] for row in result.all() if row[0]}
    except Exception as exc:
        logger.warning("bot_fill_role_lookup_failed %s", exc)
        return None


async def snapshot_for_room(room) -> dict:
    people = list(room.players) + list(getattr(room, "waiting_players", None) or [])
    player_ids = []
    for player in people:
        data = _player_dict(player)
        if data.get("player_id"):
            player_ids.append(data["player_id"])
    bot_ids = await _bot_player_ids(player_ids)
    snapshot = room_snapshot(room, bot_ids or set())
    snapshot["roles_known"] = bot_ids is not None
    return snapshot


async def _load_room(room_id: str) -> Optional[dict]:
    from app.services.room_service import room_service

    room = await room_service.get_room(room_id)
    if room is None:
        return None
    return await snapshot_for_room(room)


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
        await get_bot_fill_service().sync_snapshot(await snapshot_for_room(room))
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
