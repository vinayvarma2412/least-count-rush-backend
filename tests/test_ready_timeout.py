"""Public lobby ready deadlines and matchmaking skips."""
from datetime import datetime, timedelta, timezone

from app.services.ready_timeout import (
    apply_ready_deadlines,
    expired_unready_player_ids,
    has_outstanding_ready_deadline,
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


def test_a_finished_deadline_stays_on_the_watch_list():
    past = (NOW - timedelta(seconds=1)).isoformat()
    assert has_outstanding_ready_deadline([
        _player("alice", ready=True),
        _player("bob", deadline=past),
    ]) is True
    assert has_outstanding_ready_deadline([
        _player("alice", ready=True),
        _player("bob", ready=True, deadline=past),
    ]) is False


def test_only_the_expired_unready_player_is_selected():
    past = (NOW - timedelta(seconds=1)).isoformat()
    future = (NOW + timedelta(seconds=10)).isoformat()
    players = [
        _player("alice", deadline=past),
        _player("bob", ready=True, deadline=past),
        _player("cara", deadline=future),
    ]
    assert expired_unready_player_ids(players, NOW) == ["alice"]


def test_offline_player_is_removed_when_the_ready_countdown_ends():
    past = (NOW - timedelta(seconds=1)).isoformat()
    player = _player("alice", connected=False, deadline=past)
    assert expired_unready_player_ids([player], NOW) == ["alice"]


def test_expired_deadline_is_not_cleared_when_the_clock_stops():
    past = (NOW - timedelta(seconds=1)).isoformat()
    room = _room([
        _player("alice"),
        _player("bob", deadline=past),
    ])
    assert apply_ready_deadlines(room, NOW, timeout_seconds=45) is False
    assert room["players"][1]["ready_deadline_at"] == past


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


def test_offline_bot_is_removed_as_soon_as_it_disconnects():
    from app.services.ready_timeout import offline_bot_ids

    bot = _player("bot-1", connected=False)
    assert offline_bot_ids([bot], {"bot-1"}) == ["bot-1"]


def test_offline_bot_without_a_disconnect_stamp_is_still_removed():
    from app.services.ready_timeout import offline_bot_ids

    bot = _player("bot-1", connected=False)
    assert "disconnect_at" not in bot or bot.get("disconnect_at") is None
    assert offline_bot_ids([bot], {"bot-1"}) == ["bot-1"]


def test_offline_bot_is_removed_while_a_human_is_still_seated():
    from app.services.ready_timeout import offline_bot_ids

    bot = _player("bot-1", connected=False)
    human = _player("alice")
    assert offline_bot_ids([human, bot], {"bot-1"}) == ["bot-1"]


def test_connected_bot_that_is_alone_is_not_kicked():
    from app.services.ready_timeout import offline_bot_ids

    assert offline_bot_ids([_player("bot-1")], {"bot-1"}) == []


def test_offline_bot_shown_disconnected_is_removed_even_if_the_socket_flag_is_stale():
    from app.services.ready_timeout import offline_bot_ids

    bot = _player("bot-1", connected=True)
    bot["disconnect_at"] = (NOW - timedelta(seconds=1)).isoformat()
    assert offline_bot_ids([bot], {"bot-1"}) == ["bot-1"]


def test_bot_flag_is_enough_when_the_role_lookup_is_missing():
    from app.services.ready_timeout import offline_bot_ids

    bot = _player("bot-1", connected=False)
    bot["is_bot"] = True
    assert offline_bot_ids([bot], set()) == ["bot-1"]
