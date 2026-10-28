"""Free online_bot accounts are reused. A new user is created only when all are busy."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot_server.pool import BotPool, OnlineBot, next_display_name


class FakeDirectory:
    def __init__(self, bots):
        self.bots = list(bots)

    async def list_online_bots(self):
        return list(self.bots)


class FakeCreator:
    def __init__(self):
        self.calls = 0

    async def __call__(self, existing_names):
        self.calls += 1
        name = next_display_name(set(existing_names))
        return OnlineBot(f"uid-{name}", name, f"{name}@leastcountrush.bot")


@pytest.mark.asyncio
async def test_reuses_a_free_bot_and_creates_only_when_all_are_busy():
    existing = OnlineBot("uid-obot0001", "obot0001", "obot0001@leastcountrush.bot")
    directory = FakeDirectory([existing])
    creator = FakeCreator()
    pool = BotPool(directory, creator)

    first = await pool.acquire()
    assert first.firebase_uid == "uid-obot0001"
    assert first.password == "obot0001"
    assert creator.calls == 0

    second = await pool.acquire()
    assert second.firebase_uid == "uid-obot0002"
    assert second.password == second.display_name
    assert creator.calls == 1
    assert pool.is_busy("uid-obot0001")
    assert pool.is_busy("uid-obot0002")

    await pool.release(first.firebase_uid)
    assert not pool.is_busy(first.firebase_uid)

    third = await pool.acquire()
    assert third.firebase_uid == "uid-obot0001"
    assert creator.calls == 1
