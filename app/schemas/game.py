"""
Pydantic schemas for Game state
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, timezone


class TurnContext(BaseModel):
    """Tracks the full lifecycle of the current player's turn as an explicit state machine.

    Created when a turn starts (in _advance_turn / initialize_game) and cleared
    when the turn ends. GameState is still mutated immediately on every action —
    TurnContext is used only for rollback safety and request validation.
    """
    player_index: int = Field(description="Index of the player whose turn this is")
    phase: str = Field(
        description=(
            "State machine phase for the current turn: "
            "'awaiting_draw' (turn started, no irreversible action taken yet), "
            "'awaiting_discard' (player is mid-compound-action — either drew a card and must discard, "
            "or discarded via skip_turn_advance and must complete the turn advance). "
            "rollback_turn_if_incomplete fires whenever phase == 'awaiting_discard'."
        )
    )
    actions: List[str] = Field(
        default_factory=list,
        description="Ordered log of sub-actions taken this turn, e.g. ['pick_from_deck', 'discard_2_cards']"
    )
    hand_snapshot: List[Dict] = Field(
        description="Deep copy of the player's hand at the START of the turn — used for rollback on mid-turn disconnect"
    )
    discard_pile_snapshot: List[Dict] = Field(
        description="Deep copy of the discard pile at the START of the turn — used for rollback on mid-turn disconnect"
    )
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when this turn started"
    )


class GameState(BaseModel):
    """Game state schema"""
    current_turn: int = Field(description="Index of current player's turn")
    initial_turn: Optional[int] = Field(default=None, description="Index of the player who started this round (used for tournament turn rotation)")
    discard_pile: List[Dict] = Field(default_factory=list, description="Last discarded cards (first item is face-up card)")
    revealed_joker: Optional[Dict] = Field(default=None, description="Revealed joker card (last card from deck)")
    player_hands: List[List[Dict]] = Field(default_factory=list, description="Player hands (private)")
    player_scores: List[int] = Field(default_factory=list, description="Current player scores")
    deck: List[Dict] = Field(default_factory=list, description="Remaining deck")
    phase: str = Field(default="playing", description="Game phase: playing, declared, finished")
    declared_player: Optional[int] = Field(default=None, description="Player who declared")
    winner: Optional[int] = Field(default=None, description="Winner player index")
    eliminated_indices: List[int] = Field(default_factory=list, description="Indices of eliminated players in tournament mode")
    action_seq: int = Field(default=0, description="Monotonically increasing counter incremented on every state-changing action. Never reset, even across Play Again rounds.")
    player_lives: List[int] = Field(default_factory=list, description="Remaining lives per player index. Seeded to PLAYER_LIVES at game start; 0 = eliminated via timer.")
    turn_started_at: Optional[datetime] = Field(default=None, description="UTC timestamp when the current turn started. Reset every _advance_turn.")
    turn_timeout_seconds: int = Field(default=30, description="Seconds per turn (mirrors TURN_TIMEOUT_SECONDS config).")
    turn_context: Optional[TurnContext] = Field(
        default=None,
        description="Tracks the current turn's state machine phase and rollback snapshots. Created on turn start, cleared on turn end."
    )



class GameStartRequest(BaseModel):
    """Request to start a game"""
    room_id: str


class GameUpdateResponse(BaseModel):
    """Game state update response"""
    game_state: GameState
    message: str


class ShowRequest(BaseModel):
    """Request to show cards and calculate results"""
    showed_by_id: str


class PlayerGameResult(BaseModel):
    """Single player's game result"""
    player_id: str
    player_name: str
    in_hand_cards: List[Dict]
    game_score: int  # Count
    rule_score: int  # Score (0 for winner, count for others, 40 for penalty)
    game_result: str  # "won", "lost", or "penalty"
    is_showed: bool = Field(default=False, description="True if this player showed their cards")


class ShowResponse(BaseModel):
    """Response with game results after show"""
    players: List[PlayerGameResult]
    tournament: Optional[Dict] = Field(default=None, description="Tournament metadata (only for tournament mode)")


class TournamentRoundScores(BaseModel):
    """Single tournament round scores"""
    round: int
    scores: List[int]  # Per-player scores in player order


class TournamentScoresResponse(BaseModel):
    """Aggregated tournament scores for a room"""
    player_ids: List[str]
    player_names: List[str]
    rounds: List[TournamentRoundScores]
    totals: List[int]
    elimination_rounds: Dict[str, int] = Field(default_factory=dict, description="Map of player_id to round they were eliminated (1-indexed)")
    exited_rounds: Dict[str, int] = Field(default_factory=dict, description="Map of player_id to round they were marked exited (1-indexed)")

