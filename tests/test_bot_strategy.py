"""Online bot decisions match the offline Dart BotLogic rules."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot_server.strategy import animation_script, decide, messages_for_step, plan_steps


def card(rank, suit="hearts"):
    points = {
        "A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
        "10": 10, "J": 10, "Q": 10, "K": 10, "JOKER": 0,
    }
    return {
        "suit": "joker" if rank == "JOKER" else suit,
        "rank": rank,
        "value": points[rank],
    }


def test_show_when_score_is_under_11():
    hand = [card("A"), card("3"), card("5")]
    decision = decide(hand, card("K", "spades"), joker=card("9", "clubs"))
    assert decision.action == "show"
    assert plan_steps(decision) == ["show"]


def test_slash_matching_rank_before_drawing():
    hand = [card("7", "spades"), card("K"), card("9", "clubs")]
    decision = decide(hand, card("7", "diamonds"), joker=card("2", "clubs"))
    assert decision.action == "slash"
    assert [c["suit"] for c in decision.discard_cards] == ["spades"]
    assert plan_steps(decision) == ["discard", "skip_draw"]
    drops, follow = animation_script(decision, card("7", "diamonds"))
    assert drops[0]["action_type"] == "card_dropped"
    assert follow[0]["action_type"] == "slash"
    assert messages_for_step("discard", decision.discard_cards)[0]["type"] == "discard_cards"


def test_cheap_discard_is_picked_when_it_beats_the_highest_card():
    hand = [card("K"), card("9"), card("8")]
    decision = decide(hand, card("2", "spades"), joker=card("3", "clubs"))
    assert decision.action == "pick_from_discard"
    assert decision.discard_cards == [card("K")]
    assert plan_steps(decision) == ["pick_discard", "discard", "skip_draw"]
    _, follow = animation_script(decision, card("2", "spades"))
    assert [message["action_type"] for message in follow] == [
        "pick_from_discard",
        "cards_discarded",
    ]


def test_otherwise_dump_the_highest_card_and_draw():
    hand = [card("K"), card("Q", "spades"), card("9")]
    decision = decide(hand, card("8", "clubs"), joker=card("3"))
    assert decision.action == "pick_from_deck"
    # Equal point values keep the later card, then every card of that rank is dumped.
    assert [c["rank"] for c in decision.discard_cards] == ["Q"]
    assert plan_steps(decision) == ["discard", "pick_deck", "skip_draw"]
    drops, follow = animation_script(decision, card("8", "clubs"))
    assert [message["action_type"] for message in drops] == ["card_dropped"]
    assert [message["action_type"] for message in follow] == [
        "pick_from_deck",
        "cards_discarded",
    ]


def test_empty_discard_draws_when_the_hand_is_not_showable():
    hand = [card("K"), card("Q")]
    decision = decide(hand, None, joker=None)
    assert decision.action == "pick_from_deck"
