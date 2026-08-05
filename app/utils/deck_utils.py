"""
Card deck utilities for Least Count Rush game
"""
import random
from typing import List, Dict, Tuple, Optional
from enum import Enum


class Suit(str, Enum):
    """Card suits"""
    HEARTS = "hearts"
    DIAMONDS = "diamonds"
    CLUBS = "clubs"
    SPADES = "spades"
    JOKER = "joker"


class Rank(str, Enum):
    """Card ranks"""
    ACE = "A"
    TWO = "2"
    THREE = "3"
    FOUR = "4"
    FIVE = "5"
    SIX = "6"
    SEVEN = "7"
    EIGHT = "8"
    NINE = "9"
    TEN = "10"
    JACK = "J"
    QUEEN = "Q"
    KING = "K"
    JOKER = "JOKER"


class Card:
    """Represents a playing card"""
    
    def __init__(self, suit: Suit, rank: Rank):
        self.suit = suit
        self.rank = rank
        self.value = self._calculate_value()
    
    def _calculate_value(self) -> int:
        """Calculate card value according to game rules"""
        if self.rank == Rank.JOKER:
            return 0
        elif self.rank == Rank.ACE:
            return 1
        elif self.rank in [Rank.TWO, Rank.THREE, Rank.FOUR, Rank.FIVE, 
                           Rank.SIX, Rank.SEVEN, Rank.EIGHT, Rank.NINE]:
            return int(self.rank.value)
        else:  # TEN, JACK, QUEEN, KING
            return 10
    
    def to_dict(self) -> Dict:
        """Convert card to dictionary"""
        return {
            "suit": self.suit.value,
            "rank": self.rank.value,
            "value": self.value
        }
    
    def __repr__(self):
        if self.rank == Rank.JOKER:
            return "JOKER"
        return f"{self.rank.value}{self.suit.value[0].upper()}"
    
    def __eq__(self, other):
        if not isinstance(other, Card):
            return False
        return self.suit == other.suit and self.rank == other.rank


def create_standard_deck(include_jokers: bool = True) -> List[Card]:
    """Create a standard 52-card deck with optional jokers"""
    deck = []
    
    # Standard suits (excluding joker)
    suits = [Suit.HEARTS, Suit.DIAMONDS, Suit.CLUBS, Suit.SPADES]
    ranks = [
        Rank.ACE, Rank.TWO, Rank.THREE, Rank.FOUR, Rank.FIVE,
        Rank.SIX, Rank.SEVEN, Rank.EIGHT, Rank.NINE, Rank.TEN,
        Rank.JACK, Rank.QUEEN, Rank.KING
    ]
    
    # Create all cards
    for suit in suits:
        for rank in ranks:
            deck.append(Card(suit, rank))
    
    # Add jokers if requested
    if include_jokers:
        deck.append(Card(Suit.JOKER, Rank.JOKER))
    
    return deck


def create_game_deck(num_players: int) -> List[Card]:
    """
    Create deck(s) for the game based on number of players
    - 2 players: 1 deck (52 cards + 1 Joker)
    - 3-6 players: 2 decks (104 cards + 2 Jokers)
    """
    if num_players == 2:
        return create_standard_deck(include_jokers=True)
    else:  # 3-6 players
        deck1 = create_standard_deck(include_jokers=True)
        deck2 = create_standard_deck(include_jokers=True)
        return deck1 + deck2


def shuffle_deck(deck: List[Card]) -> List[Card]:
    """Shuffle a deck of cards"""
    shuffled = deck.copy()
    random.shuffle(shuffled)
    return shuffled


def deal_cards(deck: List[Card], num_players: int, cards_per_player: int = 7) -> Tuple[List[List[Card]], List[Card]]:
    """
    Deal cards to players
    Returns: (player_hands, remaining_deck)
    """
    if len(deck) < num_players * cards_per_player:
        raise ValueError(f"Not enough cards in deck to deal {num_players} players {cards_per_player} cards each")
    
    player_hands = [[] for _ in range(num_players)]
    remaining_deck = deck.copy()
    
    # Deal cards round-robin style
    for i in range(cards_per_player):
        for player_idx in range(num_players):
            if remaining_deck:
                card = remaining_deck.pop(0)
                player_hands[player_idx].append(card)
    
    return player_hands, remaining_deck


def calculate_hand_score(hand: List[Card], revealed_joker: Optional[Card] = None) -> int:
    """Calculate the total score of a hand, accounting for revealed joker.

    Rule: If the revealed open card is itself a Joker, all Aces count as 0
    (Ace becomes the effective joker for that round).
    """
    total_score = 0

    # Determine the effective joker rank for this round.
    # If the revealed card is a Joker, Ace becomes the wild rank.
    if revealed_joker is not None:
        if revealed_joker.rank == Rank.JOKER:
            effective_joker_rank = Rank.ACE
        else:
            effective_joker_rank = revealed_joker.rank
    else:
        effective_joker_rank = None

    for card in hand:
        # Joker cards in hand always score 0
        if card.rank == Rank.JOKER:
            continue
        # Cards matching the effective joker rank score 0
        if effective_joker_rank is not None and card.rank == effective_joker_rank:
            continue
        # Otherwise add the card's value
        total_score += card.value

    return total_score


def dict_to_card(card_dict: Dict) -> Card:
    """Convert a dictionary to a Card object"""
    suit = Suit(card_dict["suit"])
    rank = Rank(card_dict["rank"])
    return Card(suit, rank)


def cards_to_dict(cards: List[Card]) -> List[Dict]:
    """Convert a list of cards to list of dictionaries"""
    return [card.to_dict() for card in cards]

