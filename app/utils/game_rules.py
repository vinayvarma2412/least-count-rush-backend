"""
Game rules and validation utilities
"""
from typing import List, Dict
from app.utils.deck_utils import Card, Suit, Rank, dict_to_card


def validate_discard_cards(hand: List[Card], cards_to_discard: List[Dict]) -> tuple[bool, str]:
    """
    Validate if cards can be discarded
    Rules: All discarded cards must have the same value
    Returns: (is_valid, error_message)
    """
    if not cards_to_discard:
        return False, "No cards selected to discard"
    
    if len(cards_to_discard) == 0:
        return False, "Must discard at least one card"
    
    # Convert to Card objects
    try:
        discard_cards = [dict_to_card(card_dict) for card_dict in cards_to_discard]
    except Exception as e:
        return False, f"Invalid card format: {str(e)}"
    
    # Check if all cards have the same value
    first_value = discard_cards[0].value
    if not all(card.value == first_value for card in discard_cards):
        return False, "All discarded cards must have the same value"
    
    # Check if player has all these cards in hand
    hand_dicts = [card.to_dict() for card in hand]
    for discard_card in discard_cards:
        discard_dict = discard_card.to_dict()
        if discard_dict not in hand_dicts:
            return False, f"Card {discard_card} not in hand"
        # Remove from hand_dicts to handle duplicates
        hand_dicts.remove(discard_dict)
    
    return True, ""


def can_skip_draw(previous_discard: List[Dict], current_discard: List[Dict]) -> bool:
    """
    Check if player can skip drawing a card
    Rule: If discard matches previous discard value, can skip drawing
    """
    if not previous_discard or not current_discard:
        return False
    
    try:
        prev_cards = [dict_to_card(card_dict) for card_dict in previous_discard]
        curr_cards = [dict_to_card(card_dict) for card_dict in current_discard]
        
        if not prev_cards or not curr_cards:
            return False
        
        # Check if values match
        return prev_cards[0].value == curr_cards[0].value
    except Exception:
        return False


def remove_cards_from_hand(hand: List[Card], cards_to_remove: List[Dict]) -> List[Card]:
    """Remove cards from hand"""
    hand_dicts = [card.to_dict() for card in hand]
    remove_dicts = cards_to_remove.copy()
    
    for remove_dict in remove_dicts:
        if remove_dict in hand_dicts:
            hand_dicts.remove(remove_dict)
    
    # Convert back to Card objects
    return [dict_to_card(card_dict) for card_dict in hand_dicts]

