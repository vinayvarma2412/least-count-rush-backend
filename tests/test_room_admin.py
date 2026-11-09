"""Admin assignment and handoff for private and public rooms."""
from app.services.room_service import (
    apply_admin_transfer,
    newcomer_claims_admin,
    select_next_admin_id,
)


def _player(player_id, *, admin=False, connected=True, exited=False, bot=False):
    return {
        "player_id": player_id,
        "player_name": player_id,
        "is_admin": admin,
        "is_connected": connected,
        "is_exited": exited,
        "is_bot": bot,
    }


def _room(players, room_type="public"):
    return {
        "room_type": room_type,
        "players": players,
        "waiting_players": [],
    }


def test_first_player_in_a_public_room_becomes_admin():
    existing = []
    assert newcomer_claims_admin(existing) is True


def test_joining_player_does_not_replace_an_active_admin():
    admin = _player("alice", admin=True)
    assert newcomer_claims_admin([admin]) is False
    assert admin["is_admin"] is True


def test_joining_player_replaces_an_offline_admin():
    admin = _player("alice", admin=True, connected=False)
    assert newcomer_claims_admin([admin]) is True
    assert admin["is_admin"] is False


def test_offline_admin_hands_the_role_to_the_next_connected_human():
    players = [
        _player("alice", admin=True, connected=False),
        _player("bob"),
        _player("cara"),
    ]
    room = _room(players, room_type="public")
    chosen, changed = apply_admin_transfer(room, "alice")
    assert changed is True
    assert chosen == "bob"
    assert players[0]["is_admin"] is False
    assert players[1]["is_admin"] is True


def test_handoff_wraps_and_skips_bots_and_exited_players():
    players = [
        _player("alice"),
        _player("bot", bot=True),
        _player("bob", admin=True, connected=False),
        _player("cara", exited=True),
    ]
    assert select_next_admin_id(players, "bob") == "alice"


def test_exited_admin_does_not_keep_the_role():
    players = [
        _player("alice", admin=True, exited=True),
        _player("bob"),
    ]
    room = _room(players, room_type="private")
    chosen, _changed = apply_admin_transfer(room, "alice")
    assert chosen == "bob"
    assert players[0]["is_admin"] is False


def test_only_human_keeps_admin_when_everyone_else_is_a_bot():
    players = [
        _player("alice", admin=True, connected=False),
        _player("bot", bot=True),
    ]
    assert select_next_admin_id(players, "alice") == "alice"


def test_a_second_handoff_does_not_move_admin_again():
    players = [
        _player("alice", connected=False),
        _player("bob", admin=True),
    ]
    room = _room(players)
    chosen, changed = apply_admin_transfer(room, "alice")
    assert changed is False
    assert chosen == "bob"
    assert players[1]["is_admin"] is True
