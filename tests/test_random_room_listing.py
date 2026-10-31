"""Random Rooms list includes only open public lobbies."""
from datetime import datetime, timedelta, timezone

from app.services.random_room_listing import is_open_random_room

NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)


def _player(player_id, *, connected=True, exited=False):
    return {
        "player_id": player_id,
        "player_name": player_id,
        "is_connected": connected,
        "is_exited": exited,
        "disconnect_at": None,
        "is_ready": False,
    }


def _room(**overrides):
    room = {
        "room_id": "room-1",
        "room_type": "public",
        "status": "waiting",
        "max_players": 4,
        "players": [_player("alice")],
        "waiting_players": [],
    }
    room.update(overrides)
    return room


def test_lists_a_waiting_public_room_with_a_free_seat():
    assert is_open_random_room(_room(), NOW) is True


def test_hides_private_full_playing_and_empty_rooms():
    assert is_open_random_room(_room(room_type="private"), NOW) is False
    assert is_open_random_room(_room(status="playing"), NOW) is False
    assert is_open_random_room(
        _room(players=[_player("a"), _player("b")], max_players=2),
        NOW,
    ) is False
    assert is_open_random_room(_room(players=[]), NOW) is False


def test_lists_a_room_after_players_leave():
    assert is_open_random_room(
        _room(players=[_player("alice", connected=False)]),
        NOW,
    ) is True
    assert is_open_random_room(
        _room(players=[_player("alice", exited=True)]),
        NOW,
    ) is True
    assert is_open_random_room(_room(), NOW, {"room-1"}) is True


def test_hides_a_room_with_an_expired_ready_deadline():
    alice = _player("alice")
    alice["ready_deadline_at"] = (NOW - timedelta(seconds=1)).isoformat()
    stalled = _room(players=[alice, _player("bob")])
    assert is_open_random_room(stalled, NOW) is False
