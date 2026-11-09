"""A best-server reconnect must not look like the player or the bot left."""
from app.services.server_switch import REPLACED, SWITCHING, handoff_kind


def _selection(*player_ids, started=False):
    return {
        "server_url": "https://game.example",
        "expected_players": list(player_ids),
        "acknowledged_players": [],
        "started": started,
    }


def test_old_socket_is_ignored_once_the_new_one_has_joined():
    kind = handoff_kind("bot-1", "old-socket", "new-socket", _selection("bot-1", started=True))
    assert kind == REPLACED


def test_close_before_rejoin_is_a_switch_while_selection_is_open():
    kind = handoff_kind("alice", "socket-1", "socket-1", _selection("alice", "bot-1"))
    assert kind == SWITCHING


def test_a_real_leave_after_everyone_has_arrived_still_counts():
    assert handoff_kind("alice", "socket-1", "socket-1", _selection("alice", started=True)) is None


def test_someone_outside_the_move_is_a_real_leave():
    assert handoff_kind("carol", "socket-1", "socket-1", _selection("alice", "bot-1")) is None


def test_a_close_with_no_selection_is_a_real_leave():
    assert handoff_kind("alice", "socket-1", "socket-1", None) is None


def test_a_missing_player_is_not_a_handoff():
    assert handoff_kind("", "old-socket", "new-socket", _selection("alice")) is None
