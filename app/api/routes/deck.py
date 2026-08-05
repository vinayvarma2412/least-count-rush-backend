"""
Deck utilities API endpoints for testing
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict
from app.utils.deck_utils import (
    create_standard_deck,
    create_game_deck,
    shuffle_deck,
    deal_cards,
    calculate_hand_score,
    cards_to_dict,
    Card,
    Suit,
    Rank
)

router = APIRouter(prefix="/api/deck", tags=["deck"])


class CreateStandardDeckRequest(BaseModel):
    include_jokers: bool = True


class CreateGameDeckRequest(BaseModel):
    num_players: int


class ShuffleDeckRequest(BaseModel):
    deck: List[Dict]


class DealCardsRequest(BaseModel):
    deck: List[Dict]
    num_players: int
    cards_per_player: int = 7


class CalculateScoreRequest(BaseModel):
    hands: List[List[Dict]]


def dict_to_card(card_dict: Dict) -> Card:
    """Convert dictionary to Card object"""
    suit = Suit(card_dict["suit"])
    rank = Rank(card_dict["rank"])
    return Card(suit, rank)


@router.post("/create-standard")
async def create_standard_deck_endpoint(request: CreateStandardDeckRequest):
    """Create a standard deck"""
    deck = create_standard_deck(request.include_jokers)
    return {"deck": cards_to_dict(deck), "count": len(deck)}


@router.post("/create-game")
async def create_game_deck_endpoint(request: CreateGameDeckRequest):
    """Create a game deck based on number of players"""
    if request.num_players < 2 or request.num_players > 6:
        raise HTTPException(status_code=400, detail="Number of players must be between 2 and 6")
    
    deck = create_game_deck(request.num_players)
    return {"deck": cards_to_dict(deck), "count": len(deck)}


@router.post("/shuffle")
async def shuffle_deck_endpoint(request: ShuffleDeckRequest):
    """Shuffle a deck"""
    try:
        deck = [dict_to_card(card_dict) for card_dict in request.deck]
        shuffled = shuffle_deck(deck)
        return {"deck": cards_to_dict(shuffled), "count": len(shuffled)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid deck: {str(e)}")


@router.post("/deal")
async def deal_cards_endpoint(request: DealCardsRequest):
    """Deal cards to players"""
    try:
        deck = [dict_to_card(card_dict) for card_dict in request.deck]
        player_hands, remaining_deck = deal_cards(
            deck,
            request.num_players,
            request.cards_per_player
        )
        return {
            "player_hands": [cards_to_dict(hand) for hand in player_hands],
            "remaining_deck": cards_to_dict(remaining_deck)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/calculate-score")
async def calculate_score_endpoint(request: CalculateScoreRequest):
    """Calculate scores for multiple hands"""
    try:
        hands = [[dict_to_card(card_dict) for card_dict in hand] for hand in request.hands]
        scores = [calculate_hand_score(hand, None) for hand in hands]  # No joker consideration for deck calculation
        lowest_score = min(scores)
        lowest_score_player = scores.index(lowest_score)
        
        return {
            "scores": scores,
            "lowest_score": lowest_score,
            "lowest_score_player": lowest_score_player
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

