"""Tournament boards stay readable after the room returns to waiting."""
from app.api.routes.games import (
    _pending_score_resets,
    _tournament_scores,
    clear_tournament_scores,
    request_tournament_score_reset,
    should_reset_scores_for_new_deal,
    tournament_still_in_progress,
)


ROOM = "room-score-reset-test"


def setup_function():
    _tournament_scores.pop(ROOM, None)
    _pending_score_resets.discard(ROOM)


def teardown_function():
    _tournament_scores.pop(ROOM, None)
    _pending_score_resets.discard(ROOM)


def _board(eliminated=None, exited=None):
    _tournament_scores[ROOM] = {
        "player_ids": ["human", "bot"],
        "player_names": ["You", "Bot"],
        "rounds": [[10, 22]],
        "eliminated": list(eliminated or []),
        "exited_rounds": dict(exited or {}),
    }


def test_finished_tournament_board_is_kept_until_the_next_deal():
    _board(eliminated=["bot"])
    assert tournament_still_in_progress(ROOM) is False
    # No reset has been requested yet, but a finished board is not "in progress".
    # The results screen can still read it; the next brand-new deal drops it.
    assert should_reset_scores_for_new_deal(ROOM) is True
    assert ROOM in _tournament_scores


def test_in_progress_tournament_is_not_reset_without_a_request():
    _board()
    assert tournament_still_in_progress(ROOM) is True
    assert should_reset_scores_for_new_deal(ROOM) is False


def test_lobby_reset_marks_a_fresh_board_without_deleting_rounds():
    _board(eliminated=["bot"])
    request_tournament_score_reset(ROOM)
    assert ROOM in _tournament_scores
    assert _tournament_scores[ROOM]["rounds"] == [[10, 22]]
    assert should_reset_scores_for_new_deal(ROOM) is True


def test_in_progress_reset_request_is_honored_on_the_next_new_deal():
    _board()
    request_tournament_score_reset(ROOM)
    assert tournament_still_in_progress(ROOM) is True
    assert should_reset_scores_for_new_deal(ROOM) is True


def test_clear_drops_the_pending_reset_flag():
    _board()
    request_tournament_score_reset(ROOM)
    clear_tournament_scores(ROOM)
    assert ROOM not in _tournament_scores
    assert ROOM not in _pending_score_resets
