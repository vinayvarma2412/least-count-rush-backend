"""Online bot turn decisions."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot_server.strategy import (
    active_hand_counts,
    animation_script,
    decide,
    messages_for_step,
    plan_steps,
)


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


def test_show_bands_follow_opponent_hand_sizes():
    # A+3+5 = 9. Show at 9–10 only when every opponent has more than 5 cards.
    hand = [card("A"), card("3"), card("5")]
    decision = decide(
        hand, card("K", "spades"), joker=card("9", "clubs"), opponent_counts=[6, 7]
    )
    assert decision.action == "show"
    assert plan_steps(decision) == ["show"]

    held = decide(
        hand, card("K", "spades"), joker=card("9", "clubs"), opponent_counts=[6, 5]
    )
    assert held.action == "pick_from_deck"

    # A+3+4 = 8. Show at 6–8 only when every opponent has more than 3 cards.
    mid = [card("A"), card("3"), card("4")]
    assert decide(
        mid, card("K"), joker=card("9"), opponent_counts=[4]
    ).action == "show"
    assert decide(
        mid, card("K"), joker=card("9"), opponent_counts=[3, 8]
    ).action == "pick_from_deck"

    # Below 6, show no matter how few cards an opponent has.
    low = [card("A"), card("4")]
    assert decide(
        low, card("K"), joker=card("9"), opponent_counts=[1]
    ).action == "show"


def test_eliminated_opponents_do_not_block_a_show():
    counts = active_hand_counts([
        {"in_hand_cards_count": 2, "is_eliminated": True},
        {"in_hand_cards_count": 6, "is_exited": False},
    ])
    assert counts == [6]
    hand = [card("A"), card("3"), card("5")]
    assert decide(
        hand, card("K"), joker=card("9"), opponent_counts=counts
    ).action == "show"


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


def test_dump_drops_a_set_that_removes_more_than_the_highest_card():
    # King is the dearest single card (10), but the two 7s remove 14.
    hand = [
        card("7", "hearts"),
        card("7", "diamonds"),
        card("10", "hearts"),
        card("K", "diamonds"),
    ]
    decision = decide(hand, card("4", "clubs"), joker=card("3"))
    assert decision.action == "pick_from_deck"
    assert [c["rank"] for c in decision.discard_cards] == ["7", "7"]


def test_dump_keeps_the_highest_card_when_a_set_only_ties_it():
    hand = [card("5", "hearts"), card("5", "diamonds"), card("K", "spades")]
    decision = decide(hand, card("9", "clubs"), joker=card("3"))
    assert [c["rank"] for c in decision.discard_cards] == ["K"]


def test_empty_discard_draws_when_the_hand_is_not_showable():
    hand = [card("K"), card("Q")]
    decision = decide(hand, None, joker=None)
    assert decision.action == "pick_from_deck"
