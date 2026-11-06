"""A random-room bot readies only after a person does."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot_server.session import (
    READY_DELAY_MAX_SECONDS,
    READY_DELAY_MIN_SECONDS,
    other_player_is_ready,
    ready_delay_seconds,
)


def _player(player_id, *, ready=False, exited=False):
    return {
        "player_id": player_id,
        "is_ready": ready,
        "is_exited": exited,
    }


def test_bot_does_not_ready_while_the_person_is_waiting():
    players = [_player("bot-1"), _player("alice")]
    assert other_player_is_ready(players, "bot-1") is False


def test_bot_readies_once_a_person_has_tapped_ready():
    players = [_player("bot-1"), _player("alice", ready=True)]
    assert other_player_is_ready(players, "bot-1") is True


def test_an_exited_ready_player_does_not_start_the_bot():
    players = [_player("bot-1"), _player("alice", ready=True, exited=True)]
    assert other_player_is_ready(players, "bot-1") is False


def test_ready_delay_is_inside_the_random_window():
    for _ in range(30):
        delay = ready_delay_seconds()
        assert READY_DELAY_MIN_SECONDS <= delay <= READY_DELAY_MAX_SECONDS
