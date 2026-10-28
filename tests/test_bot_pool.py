"""Free online_bot accounts are reused. A new user is created only when all are busy."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot_server.names import public_name
from bot_server.pool import BotPool, OnlineBot


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
        number = self.calls + 1
        name = f"obot{number:04d}"
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
    assert second.password == "obot0002"
    assert second.display_name == "obot0002"
    assert creator.calls == 1
    assert pool.is_busy("uid-obot0001")
    assert pool.is_busy("uid-obot0002")

    await pool.release(first.firebase_uid)
    assert not pool.is_busy(first.firebase_uid)

    third = await pool.acquire()
    assert third.firebase_uid == "uid-obot0001"
    assert creator.calls == 1


@pytest.mark.asyncio
async def test_free_bot_is_chosen_by_the_picker_not_list_order():
    directory = FakeDirectory([
        OnlineBot("uid-a", "Aarav", "obot0001@leastcountrush.bot"),
        OnlineBot("uid-b", "Meera", "obot0002@leastcountrush.bot"),
    ])
    pool = BotPool(directory, FakeCreator(), pick=lambda bots: bots[-1])

    chosen = await pool.acquire()
    assert chosen.firebase_uid == "uid-b"


def test_public_name_adds_a_trailing_number_when_taken():
    pick = lambda names: names[0]
    assert public_name(set(), ["Meera", "Arjun"], pick=pick) == "Meera"
    assert public_name({"Meera"}, ["Meera"], pick=pick) == "Meera2"
    assert public_name({"Meera", "Meera2"}, ["Meera"], pick=pick) == "Meera3"
