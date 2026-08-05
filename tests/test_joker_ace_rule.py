"""
Tests for the 'Ace-as-Joker' rule:
  When the revealed open card is a Joker, all Aces count as 0 points.
"""
import pytest
from app.utils.deck_utils import Card, Suit, Rank, calculate_hand_score


def make(suit: Suit, rank: Rank) -> Card:
    return Card(suit, rank)


class TestCalculateHandScore:
    # ------------------------------------------------------------------ #
    # Normal joker revealed (existing behaviour must be preserved)
    # ------------------------------------------------------------------ #

    def test_normal_joker_revealed_matching_card_scores_zero(self):
        """Card matching revealed rank → 0 (existing behaviour)."""
        hand = [make(Suit.HEARTS, Rank.FIVE), make(Suit.SPADES, Rank.KING)]
        revealed = make(Suit.DIAMONDS, Rank.FIVE)
        # 5♥ matches revealed 5 → 0; K♠ → 10
        assert calculate_hand_score(hand, revealed) == 10

    def test_normal_joker_revealed_no_match(self):
        """No card matches revealed rank → full sum."""
        hand = [make(Suit.HEARTS, Rank.THREE), make(Suit.CLUBS, Rank.SEVEN)]
        revealed = make(Suit.SPADES, Rank.KING)
        assert calculate_hand_score(hand, revealed) == 10  # 3 + 7

    def test_joker_in_hand_always_zero(self):
        """Joker card in hand always 0 regardless of revealed."""
        hand = [make(Suit.JOKER, Rank.JOKER), make(Suit.HEARTS, Rank.FOUR)]
        assert calculate_hand_score(hand, None) == 4

    # ------------------------------------------------------------------ #
    # NEW RULE: revealed card is a Joker → Ace becomes the wild rank
    # ------------------------------------------------------------------ #

    def test_revealed_joker_makes_ace_worth_zero(self):
        """When revealed card is a Joker, Ace scores 0."""
        hand = [make(Suit.HEARTS, Rank.ACE), make(Suit.CLUBS, Rank.SEVEN)]
        revealed_joker_card = make(Suit.JOKER, Rank.JOKER)
        # A♥ → 0 (Ace is the joker); 7♣ → 7
        assert calculate_hand_score(hand, revealed_joker_card) == 7

    def test_revealed_joker_all_aces_zero(self):
        """All Aces across all suits score 0 when revealed is a Joker."""
        hand = [
            make(Suit.HEARTS, Rank.ACE),
            make(Suit.DIAMONDS, Rank.ACE),
            make(Suit.CLUBS, Rank.ACE),
            make(Suit.SPADES, Rank.ACE),
        ]
        revealed_joker_card = make(Suit.JOKER, Rank.JOKER)
        assert calculate_hand_score(hand, revealed_joker_card) == 0

    def test_revealed_joker_mixed_hand(self):
        """Mixed hand: Aces = 0, Joker-in-hand = 0, others normal."""
        hand = [
            make(Suit.HEARTS, Rank.ACE),       # 0 (ace-as-joker)
            make(Suit.JOKER, Rank.JOKER),       # 0 (joker in hand)
            make(Suit.SPADES, Rank.KING),       # 10
            make(Suit.CLUBS, Rank.TWO),         # 2
        ]
        revealed_joker_card = make(Suit.JOKER, Rank.JOKER)
        assert calculate_hand_score(hand, revealed_joker_card) == 12

    def test_revealed_joker_no_ace_in_hand(self):
        """When revealed is Joker but hand has no Aces — only Joker-in-hand is 0."""
        hand = [make(Suit.HEARTS, Rank.TEN), make(Suit.SPADES, Rank.FIVE)]
        revealed_joker_card = make(Suit.JOKER, Rank.JOKER)
        assert calculate_hand_score(hand, revealed_joker_card) == 15  # 10 + 5

    def test_no_revealed_joker(self):
        """No revealed joker → Ace scores 1 (default)."""
        hand = [make(Suit.HEARTS, Rank.ACE), make(Suit.CLUBS, Rank.NINE)]
        assert calculate_hand_score(hand, None) == 10  # 1 + 9
