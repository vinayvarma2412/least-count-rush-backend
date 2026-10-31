"""Public-room bots join on a 1x, 2x, then 3x timer while one human is active."""
import pytest

from app.services.bot_fill_service import INDEX_KEY, BotFillService, bot_wait_seconds


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


def _room(
    active_humans=1,
    humans_present=None,
    bots_present=0,
    seated=None,
    status="waiting",
    room_type="public",
    max_players=6,
):
    if humans_present is None:
        humans_present = active_humans
    if seated is None:
        seated = humans_present + bots_present
    return {
        "room_id": "room-1",
        "room_code": "ABC123",
        "status": status,
        "room_type": room_type,
        "server_url": "http://game.example",
        "active_humans": active_humans,
        "humans_present": humans_present,
        "bots_present": bots_present,
        "seated": seated,
        "max_players": max_players,
        "roles_known": True,
    }


def _service(poster, rooms, wait_seconds=120):
    now = {"t": 1_000.0}

    async def load_room(room_id):
        return rooms.get(room_id)

    service = BotFillService(
        FakeStore(),
        poster,
        load_room,
        wait_seconds=wait_seconds,
        retry_seconds=30,
        clock=lambda: now["t"],
    )
    return service, now


def test_wait_grows_by_the_bot_number():
    assert bot_wait_seconds(1, 15) == 15
    assert bot_wait_seconds(2, 15) == 30
    assert bot_wait_seconds(3, 15) == 45


@pytest.mark.asyncio
async def test_three_bots_wait_one_two_then_three_times_the_timer():
    calls = []

    async def poster(snapshot):
        calls.append(snapshot["room_id"])
        return True

    rooms = {"room-1": _room()}
    service, now = _service(poster, rooms)

    await service.sync_snapshot(rooms["room-1"])
    assert await service.process_due() == []

    now["t"] += 120
    assert await service.process_due() == ["room-1"]

    now["t"] += 240
    assert await service.process_due() == ["room-1"]

    now["t"] += 360
    assert await service.process_due() == ["room-1"]
    assert calls == ["room-1", "room-1", "room-1"]

    now["t"] += 1000
    assert await service.process_due() == []
    assert calls == ["room-1", "room-1", "room-1"]


@pytest.mark.asyncio
async def test_no_bot_is_added_without_an_active_human():
    calls = []

    async def poster(snapshot):
        calls.append(snapshot)
        return True

    rooms = {"room-1": _room(active_humans=0, seated=0)}
    service, now = _service(poster, rooms)
    await service.sync_snapshot(rooms["room-1"])
    now["t"] += 120
    assert await service.process_due() == []
    assert calls == []

    rooms["room-1"] = _room(active_humans=1)
    now["t"] += 120
    assert await service.process_due() == ["room-1"]


@pytest.mark.asyncio
async def test_a_full_room_does_not_get_a_bot():
    calls = []

    async def poster(snapshot):
        calls.append(snapshot)
        return True

    rooms = {"room-1": _room(active_humans=2, seated=2, max_players=2)}
    service, now = _service(poster, rooms)
    await service.sync_snapshot(rooms["room-1"])
    now["t"] += 500
    assert await service.process_due() == []
    assert calls == []


@pytest.mark.asyncio
async def test_a_second_real_player_cancels_the_bot():
    async def poster(snapshot):
        raise AssertionError("should not post")

    rooms = {"room-1": _room()}
    service, now = _service(poster, rooms)
    await service.sync_snapshot(rooms["room-1"])
    rooms["room-1"] = _room(active_humans=2, humans_present=2)
    now["t"] += 500
    await service.sync_snapshot(rooms["room-1"])
    assert await service.process_due() == []
    assert await service._store.get(service._wait_key("room-1")) is None


@pytest.mark.asyncio
async def test_bots_already_seated_do_not_count_as_the_second_player():
    calls = []

    async def poster(snapshot):
        calls.append(snapshot["room_id"])
        return True

    rooms = {"room-1": _room(active_humans=1, humans_present=1, bots_present=1)}
    service, now = _service(poster, rooms)
    await service.sync_snapshot(rooms["room-1"])
    now["t"] += 240
    assert await service.process_due() == ["room-1"]
    assert calls == ["room-1"]


@pytest.mark.asyncio
async def test_failed_request_is_retried_once():
    calls = {"n": 0}

    async def poster(snapshot):
        calls["n"] += 1
        return calls["n"] == 2

    rooms = {"room-1": _room()}
    service, now = _service(poster, rooms)
    await service.sync_snapshot(rooms["room-1"])
    now["t"] += 120
    assert await service.process_due() == []
    assert calls["n"] == 1

    now["t"] += 30
    assert await service.process_due() == ["room-1"]
    assert calls["n"] == 2
