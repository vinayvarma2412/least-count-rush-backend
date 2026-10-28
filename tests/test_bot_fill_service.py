"""Public-room bot fill waits for one connected player, then asks once."""
import json

import pytest

from app.config import PUBLIC_ROOM_BOT_WAIT_SECONDS
from app.services.bot_fill_service import INDEX_KEY, BotFillService


class FakeStore:
    def __init__(self):
        self.data = {}
        self.sets = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)

    async def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    async def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))


def _room(connected, status="waiting", room_type="public"):
    return {
        "room_id": "room-1",
        "room_code": "ABC123",
        "status": status,
        "room_type": room_type,
        "server_url": "http://game.example",
        "connected_players": connected,
    }


def _service(poster, rooms):
    now = {"t": 1_000.0}

    async def load_room(room_id):
        return rooms.get(room_id)

    service = BotFillService(
        FakeStore(),
        poster,
        load_room,
        wait_seconds=120,
        retry_seconds=30,
        clock=lambda: now["t"],
    )
    return service, now


@pytest.mark.asyncio
async def test_requests_one_bot_when_the_room_is_still_alone():
    calls = []

    async def poster(snapshot):
        calls.append(snapshot["room_id"])
        return True

    rooms = {"room-1": _room(1)}
    service, now = _service(poster, rooms)

    await service.sync_snapshot(rooms["room-1"])
    assert await service.process_due() == []

    now["t"] += 120
    assert await service.process_due() == ["room-1"]
    assert calls == ["room-1"]

    # The assignment sticks, so a still-alone room does not get a second bot.
    await service.sync_snapshot(rooms["room-1"])
    now["t"] += 500
    assert await service.process_due() == []
    assert calls == ["room-1"]


@pytest.mark.asyncio
async def test_second_human_before_the_deadline_cancels_the_request():
    calls = []

    async def poster(snapshot):
        calls.append(snapshot)
        return True

    rooms = {"room-1": _room(1)}
    service, now = _service(poster, rooms)

    await service.sync_snapshot(rooms["room-1"])
    rooms["room-1"] = _room(2)
    await service.sync_snapshot(rooms["room-1"])
    now["t"] += 120
    assert await service.process_due() == []
    assert calls == []


@pytest.mark.asyncio
async def test_failed_request_is_retried_once():
    calls = {"n": 0}

    async def poster(snapshot):
        calls["n"] += 1
        return calls["n"] == 2

    rooms = {"room-1": _room(1)}
    service, now = _service(poster, rooms)
    await service.sync_snapshot(rooms["room-1"])
    now["t"] += 120
    assert await service.process_due() == []
    assert calls["n"] == 1

    now["t"] += 30
    assert await service.process_due() == ["room-1"]
    assert calls["n"] == 2

    raw = await service._store.get(service._wait_key("room-1"))
    assert raw is None
    assert await service._store.get(service._assigned_key("room-1")) == "1"


def test_wait_constant_is_the_single_config_value():
    assert PUBLIC_ROOM_BOT_WAIT_SECONDS == 120


@pytest.mark.asyncio
async def test_wait_record_is_not_reset_while_it_is_pending():
    """Joining traffic must not push the original deadline later."""

    async def poster(snapshot):
        raise AssertionError("should not post")

    rooms = {"room-1": _room(1)}
    service, now = _service(poster, rooms)
    await service.sync_snapshot(rooms["room-1"])
    first = json.loads(await service._store.get(service._wait_key("room-1")))
    now["t"] += 10
    await service.sync_snapshot(rooms["room-1"])
    second = json.loads(await service._store.get(service._wait_key("room-1")))
    assert second["due_at"] == first["due_at"]
    assert "room-1" in await service._store.smembers(INDEX_KEY)
