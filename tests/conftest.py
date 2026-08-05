"""
Shared fixtures and helpers for disconnect scenario tests.

Architecture:
  - All tests operate directly on GameService + RoomService instances.
  - No WebSocket / HTTP layer is involved — we test pure service logic.
  - `simulate_disconnect()` calls `game_service.rollback_turn_if_incomplete()`
    which is the same code path triggered by `on_player_disconnect` in room_ws.py.
"""
import copy
import pytest

from app.services.room_service import RoomService
from app.services.game_service import GameService
from app.schemas.room import RoomCreate, RoomStatus
from app.utils.deck_utils import Card, Suit, Rank, cards_to_dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_card(suit: str, rank: str) -> dict:
    """Return a card dict the same way Card.to_dict() does."""
    s = Suit(suit)
    r = Rank(rank)
    c = Card(s, r)
    return c.to_dict()


def build_services():
    """Return fresh, isolated RoomService + GameService instances."""
    rs = RoomService()
    gs = GameService()
    # Patch GameService's internal room_service reference so both share the
    # same in-memory store.
    import app.services.game_service as gsmod
    import app.services.room_service as rsmod
    _orig_rs = rsmod.room_service
    _orig_gs_rs = gsmod.room_service
    gsmod.room_service = rs
    rsmod.room_service = rs
    return rs, gs, (_orig_rs, _orig_gs_rs, rsmod, gsmod)


def teardown_services(originals):
    _orig_rs, _orig_gs_rs, rsmod, gsmod = originals
    rsmod.room_service = _orig_rs
    gsmod.room_service = _orig_gs_rs


def setup_two_player_game(rs: RoomService, gs: GameService):
    """
    Create a room with two players (A at index 0, B at index 1),
    initialize the game and force a known hand/discard state.

    Returns:
        room_id, player_a_id, player_b_id
    """
    room = rs.create_room(RoomCreate(max_players=2))
    room_id = room.room_id
    pid_a = "player-a"
    pid_b = "player-b"

    rs.add_player(room_id, pid_a, "Alice", is_connected=True)
    rs.add_player(room_id, pid_b, "Bob", is_connected=True)

    # Manually initialize game state instead of going through
    # game_service.initialize_game() so we can control card hands exactly.
    rs.update_room_status(room_id, RoomStatus.PLAYING)

    # ---- Craft deterministic game state ----
    # A's hand: 3♥ (3) and 5♠ (5) — score 8
    # B's hand: K♣ (10) and 2♦ (2) — score 12
    # Discard pile top: 7♦
    # Deck has a few cards
    hand_a = [make_card("hearts", "3"), make_card("spades", "5")]
    hand_b = [make_card("clubs", "K"), make_card("diamonds", "2")]
    discard = [make_card("diamonds", "7")]
    deck = [
        make_card("clubs", "A"),
        make_card("hearts", "9"),
        make_card("spades", "Q"),
    ]
    revealed_joker = make_card("hearts", "2")  # rank 2 is joker → 2♦ in B's hand = 0

    from app.schemas.game import GameState, TurnContext
    game_state = GameState(
        current_turn=0,
        initial_turn=0,
        discard_pile=copy.deepcopy(discard),
        revealed_joker=revealed_joker,
        player_hands=[copy.deepcopy(hand_a), copy.deepcopy(hand_b)],
        player_scores=[8, 10],
        deck=copy.deepcopy(deck),
        phase="playing",
        action_seq=1,
    )
    gs._game_states[room_id] = game_state
    # Create turn context for player A (index 0) — standard turn-start state
    gs._create_turn_context(room_id)

    return room_id, pid_a, pid_b


def simulate_disconnect(gs: GameService, rs: RoomService, room_id: str, player_id: str, player_index: int):
    """
    Mirror what on_player_disconnect() does for game-state concerns:
      1. Mark player offline.
      2. Call rollback_turn_if_incomplete().
    Returns True if rollback was performed.
    """
    rs.set_player_connected(room_id, player_id, False)
    return gs.rollback_turn_if_incomplete(room_id, player_index)
