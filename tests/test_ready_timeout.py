"""Public lobby ready deadlines and matchmaking skips."""
from datetime import datetime, timedelta, timezone

from app.services.ready_timeout import (
    apply_ready_deadlines,
    expired_unready_player_ids,
    matchmaking_block_reason,
)


NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)


def _player(player_id, *, ready=False, connected=True, deadline=None):
    return {
        "player_id": player_id,
        "player_name": player_id,
        "is_ready": ready,
        "is_connected": connected,
        "is_exited": False,
        "ready_deadline_at": deadline,
    }


def _room(players):
    return {
        "room_id": "room-1",
        "room_type": "public",
        "status": "waiting",
        "players": players,
        "waiting_players": [],
    }


def test_one_connected_player_is_not_on_a_clock():
    room = _room([_player("alice")])
    assert apply_ready_deadlines(room, NOW, timeout_seconds=45) is False
    assert room["players"][0]["ready_deadline_at"] is None


def test_countdown_starts_only_when_half_the_players_are_ready():
    room = _room([
        _player("alice"),
        _player("bob"),
        _player("cara"),
        _player("dan"),
    ])
    assert apply_ready_deadlines(room, NOW, timeout_seconds=45) is False

    room["players"][0]["is_ready"] = True
    assert apply_ready_deadlines(room, NOW, timeout_seconds=45) is False
    assert room["players"][1]["ready_deadline_at"] is None

    room["players"][1]["is_ready"] = True
    assert apply_ready_deadlines(room, NOW, timeout_seconds=45) is True
    assert room["players"][0]["ready_deadline_at"] is None
    assert room["players"][2]["ready_deadline_at"] == (NOW + timedelta(seconds=45)).isoformat()
    assert room["players"][3]["ready_deadline_at"] == (NOW + timedelta(seconds=45)).isoformat()


def test_one_ready_player_in_a_pair_starts_the_other_deadline():
    room = _room([
        _player("alice", ready=True),
        _player("bob"),
    ])
    assert apply_ready_deadlines(room, NOW, timeout_seconds=45) is True
    assert room["players"][0]["ready_deadline_at"] is None
    assert room["players"][1]["ready_deadline_at"] == (NOW + timedelta(seconds=45)).isoformat()

    room["players"][0]["is_ready"] = False
    apply_ready_deadlines(room, NOW, timeout_seconds=45)
    assert room["players"][1]["ready_deadline_at"] is None


def test_a_disconnect_does_not_restart_the_deadline():
    existing = (NOW + timedelta(seconds=20)).isoformat()
    room = _room([
        _player("alice", ready=True),
        _player("bob", deadline=existing),
        _player("cara", connected=False, deadline=existing),
    ])
    assert apply_ready_deadlines(room, NOW, timeout_seconds=45) is False
    assert room["players"][1]["ready_deadline_at"] == existing
    assert room["players"][2]["ready_deadline_at"] == existing


def test_only_the_expired_unready_player_is_selected():
    past = (NOW - timedelta(seconds=1)).isoformat()
    future = (NOW + timedelta(seconds=10)).isoformat()
    players = [
        _player("alice", deadline=past),
        _player("bob", ready=True, deadline=past),
        _player("cara", deadline=future),
    ]
    assert expired_unready_player_ids(players, NOW) == ["alice"]


def test_matchmaking_skips_a_room_the_player_just_left_and_an_expired_stall():
    room = _room([_player("alice")])
    assert matchmaking_block_reason(room, NOW, {"room-1"}) == "recently_left"

    stalled = _room([
        _player("alice", deadline=(NOW - timedelta(seconds=1)).isoformat()),
        _player("bob", ready=True),
    ])
    assert matchmaking_block_reason(stalled, NOW) == "ready_deadline_expired"
    assert matchmaking_block_reason(room, NOW) is None


def test_private_rooms_never_get_a_deadline():
    room = _room([_player("alice"), _player("bob")])
    room["room_type"] = "private"
    assert apply_ready_deadlines(room, NOW, timeout_seconds=45) is False
    assert room["players"][0]["ready_deadline_at"] is None
