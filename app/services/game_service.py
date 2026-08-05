"""
Game service for managing game state and logic — Redis-backed.

Key schema:
  game:{room_id}  → JSON blob of GameState.dict()  (TTL 4 h)
"""
import copy
import json
import random
from datetime import datetime, timezone
from typing import Optional, List, Dict
from app.services.room_service import room_service
from app.schemas.room import RoomStatus
from app.schemas.game import GameState, TurnContext
from app.utils.room_logger import get_room_logger
from app.utils.deck_utils import (
    create_game_deck,
    shuffle_deck,
    deal_cards,
    calculate_hand_score,
    cards_to_dict,
    Card,
    Suit,
    Rank,
    dict_to_card,
)
from app.utils.game_rules import (
    validate_discard_cards,
    can_skip_draw,
    remove_cards_from_hand,
)
from app.services.redis_client import redis_client, KEY_TTL_SECONDS
from app.config import TURN_TIMEOUT_SECONDS, PLAYER_LIVES, BOT_PLAY_ANIMATION_DELAY_MS


# ── Serialisation helpers ────────────────────────────────────────────────────

def _game_state_to_json(gs: GameState) -> str:
    return gs.model_dump_json()


def _game_state_from_json(raw: str) -> GameState:
    return GameState.model_validate_json(raw)


# ── Service class ────────────────────────────────────────────────────────────

class GameService:
    """Service for managing game state — all state in Redis."""

    @staticmethod
    def _key(room_id: str) -> str:
        return f"game:{room_id}"

    async def _save(self, room_id: str, gs: GameState) -> None:
        await redis_client.setex(self._key(room_id), KEY_TTL_SECONDS, _game_state_to_json(gs))

    async def _load(self, room_id: str) -> Optional[GameState]:
        raw = await redis_client.get(self._key(room_id))
        if raw is None:
            return None
        return _game_state_from_json(raw)

    # ── public API ───────────────────────────────────────────────────────────

    async def initialize_game(
        self,
        room_id: str,
        eliminated_indices: list[int] | None = None,
        initial_turn_override: int | None = None,
    ) -> Optional[GameState]:
        """Initialize a new game for a room."""
        log = get_room_logger(room_id)
        room = await room_service.get_room(room_id)
        if not room:
            log.error("game_init_failed", {"reason": "room_not_found"})
            return None

        if eliminated_indices is None:
            eliminated_indices = []
        exited_indices = await room_service.get_exited_indices(room_id)
        inactive_indices = set(eliminated_indices) | set(exited_indices)

        num_players = len(room.players)
        active_player_count = num_players - len(inactive_indices)

        if active_player_count < 2:
            log.warn("game_init_failed", {
                "reason": "not_enough_active_players",
                "active_player_count": active_player_count,
                "eliminated_indices": eliminated_indices,
                "exited_indices": exited_indices,
            })
            return None

        deck = create_game_deck(active_player_count)
        shuffled_deck = shuffle_deck(deck)
        active_hands, remaining_deck = deal_cards(shuffled_deck, active_player_count, 7)

        player_hands: list[list[Card]] = []
        active_hand_idx = 0
        for i in range(num_players):
            if i in inactive_indices:
                player_hands.append([])
            else:
                player_hands.append(active_hands[active_hand_idx])
                active_hand_idx += 1

        face_up_card = None
        discard_pile = []
        if remaining_deck:
            face_up_card = remaining_deck.pop(0)
            discard_pile = [face_up_card.to_dict()]

        revealed_joker = None
        if remaining_deck:
            revealed_joker = remaining_deck.pop(-1)

        revealed_joker_card = revealed_joker
        player_scores = [calculate_hand_score(hand, revealed_joker_card) for hand in player_hands]

        active_player_indices = [i for i in range(num_players) if i not in inactive_indices]
        if initial_turn_override is not None and initial_turn_override in active_player_indices:
            initial_turn = initial_turn_override
            turn_source = "override"
        else:
            initial_turn = random.choice(active_player_indices) if active_player_indices else 0
            turn_source = "random"

        game_state = GameState(
            current_turn=initial_turn,
            initial_turn=initial_turn,
            discard_pile=discard_pile,
            revealed_joker=revealed_joker.to_dict() if revealed_joker else None,
            player_hands=[cards_to_dict(hand) for hand in player_hands],
            player_scores=player_scores,
            deck=cards_to_dict(remaining_deck),
            phase="playing",
            eliminated_indices=eliminated_indices,
            player_lives=[
                0 if i in inactive_indices else PLAYER_LIVES
                for i in range(num_players)
            ],
            turn_started_at=datetime.now(timezone.utc),
            turn_timeout_seconds=TURN_TIMEOUT_SECONDS,
        )

        await self._save(room_id, game_state)

        # Create TurnContext AFTER saving so _create_turn_context can load+re-save
        await self._create_turn_context(room_id)

        status_updated = await room_service.update_room_status(room_id, RoomStatus.PLAYING)
        if not status_updated:
            log.warn("room_status_update_failed", {"target_status": "PLAYING"})

        log.info("game_initialized", {
            "num_players": num_players,
            "active_players": active_player_count,
            "eliminated_indices": eliminated_indices,
            "exited_indices": exited_indices,
            "initial_turn": initial_turn,
            "turn_source": turn_source,
            "deck_size": len(remaining_deck),
            "revealed_joker": revealed_joker.to_dict() if revealed_joker else None,
            "face_up_card": face_up_card.to_dict() if face_up_card else None,
            "player_scores": player_scores,
        })

        # Return the freshly-loaded state (includes TurnContext)
        return await self._load(room_id)

    async def get_game_state(self, room_id: str) -> Optional[GameState]:
        """Get current game state for a room."""
        return await self._load(room_id)

    async def get_player_hand(self, room_id: str, player_index: int) -> Optional[List[Dict]]:
        """Get a specific player's hand."""
        gs = await self._load(room_id)
        if not gs:
            return None
        if player_index < 0 or player_index >= len(gs.player_hands):
            return None
        return gs.player_hands[player_index]

    async def get_public_game_state(self, room_id: str) -> Optional[Dict]:
        """Get public game state (without private hands)."""
        gs = await self._load(room_id)
        if not gs:
            return None
        public_state = gs.dict()
        public_state["player_hand_counts"] = [len(hand) for hand in gs.player_hands]
        public_state["player_hands"] = None
        return public_state

    async def get_player_state(self, room_id: str, player_index: int, room_players: List, preloaded_gs: Optional[GameState] = None) -> Optional[Dict]:
        """Build playerState for a specific player."""
        gs = preloaded_gs if preloaded_gs else await self._load(room_id)
        if not gs:
            return None
        if player_index < 0 or player_index >= len(gs.player_hands):
            return None

        player_hand = gs.player_hands[player_index]
        open_joker = gs.revealed_joker
        discard_pile = gs.discard_pile
        last_two_discard = discard_pile[-2:] if len(discard_pile) >= 2 else discard_pile
        current_turn = gs.current_turn
        is_playing = current_turn == player_index

        num_players = len(room_players)
        opponents = []
        eliminated_indices = gs.eliminated_indices or []
        
        # Compute exited indices from the already-loaded room_players list
        exited_indices = [
            idx for idx, p in enumerate(room_players) if getattr(p, "is_exited", False)
        ]
        
        eliminated_or_exited = set(eliminated_indices) | set(exited_indices)

        # Timer fields
        turn_started_at_iso = gs.turn_started_at.isoformat() if gs.turn_started_at else None
        turn_timeout_secs = gs.turn_timeout_seconds if gs.turn_timeout_seconds else TURN_TIMEOUT_SECONDS

        # Per-player lives visible to everyone (rotated like opponents)
        player_lives_list = gs.player_lives or []

        for i in range(1, num_players):
            opponent_index = (player_index + i) % num_players
            opponent_player = room_players[opponent_index] if opponent_index < len(room_players) else None
            opponent_name = opponent_player.player_name if opponent_player else f"Player {opponent_index + 1}"
            opponent_hand_count = len(gs.player_hands[opponent_index]) if opponent_index < len(gs.player_hands) else 0
            is_opponent_turn = current_turn == opponent_index
            is_opponent_eliminated = opponent_index in eliminated_or_exited
            is_opponent_exited = opponent_index in exited_indices
            opponent_avatar_seed = None
            opponent_disconnect_at = None
            opp_lives = player_lives_list[opponent_index] if opponent_index < len(player_lives_list) else PLAYER_LIVES

            if opponent_player:
                opponent_avatar_seed = opponent_player.avatar_seed or opponent_player.player_id
                raw_dc = opponent_player.disconnect_at
                if raw_dc is not None:
                    opponent_disconnect_at = raw_dc.isoformat() if hasattr(raw_dc, "isoformat") else str(raw_dc)

            opponents.append({
                "opponent_name": opponent_name,
                "in_hand_cards_count": opponent_hand_count,
                "is_playing": is_opponent_turn,
                "opponent_avatar_seed": opponent_avatar_seed,
                "is_eliminated": is_opponent_eliminated,
                "is_exited": is_opponent_exited,
                "disconnect_at": opponent_disconnect_at,
                "lives": opp_lives,
            })

        deck_card = None
        is_eliminated = player_index in eliminated_or_exited
        my_lives = player_lives_list[player_index] if player_index < len(player_lives_list) else PLAYER_LIVES

        return {
            "in_hand_cards": player_hand,
            "open_joker": open_joker,
            "discard_pile": last_two_discard,
            "is_playing": is_playing,
            "is_eliminated": is_eliminated,
            "opponents": opponents,
            "deck_card": deck_card,
            "my_lives": my_lives,
            "turn_started_at": turn_started_at_iso,
            "turn_timeout_seconds": turn_timeout_secs,
        }

    async def end_game(self, room_id: str, winner_index: Optional[int] = None):
        """End the game and determine winner."""
        log = get_room_logger(room_id)
        gs = await self._load(room_id)
        if not gs:
            return

        if winner_index is None:
            eliminated = set(gs.eliminated_indices or [])
            exited = set(await room_service.get_exited_indices(room_id))
            excluded = eliminated | exited

            active_candidates = [
                (idx, score)
                for idx, score in enumerate(gs.player_scores)
                if idx not in excluded
            ]
            if not active_candidates:
                return

            lowest_score = min(score for _, score in active_candidates)
            if lowest_score == 0:
                zero_score_players = [i for i, score in active_candidates if score == 0]
                if (
                    len(zero_score_players) > 1
                    and gs.declared_player is not None
                    and gs.declared_player not in excluded
                ):
                    winner_index = gs.declared_player
                else:
                    winner_index = zero_score_players[0] if zero_score_players else active_candidates[0][0]
            else:
                winner_index = next(i for i, score in active_candidates if score == lowest_score)

        gs.winner = winner_index
        gs.phase = "finished"

        log.info("game_ended", {
            "winner_index": winner_index,
            "final_scores": gs.player_scores,
            "total_turns_approx": gs.current_turn,
        })

        await self._save(room_id, gs)
        await room_service.update_room_status(room_id, RoomStatus.FINISHED)

    async def clear_game(self, room_id: str):
        """Clear game state for a room."""
        exists = await redis_client.exists(self._key(room_id))
        if exists:
            get_room_logger(room_id).info("game_state_cleared", {})
        await redis_client.delete(self._key(room_id))

    async def _create_turn_context(self, room_id: str):
        """Create a fresh TurnContext for the current player's turn."""
        gs = await self._load(room_id)
        if not gs:
            return
        player_index = gs.current_turn
        if player_index < 0 or player_index >= len(gs.player_hands):
            return
        gs.turn_context = TurnContext(
            player_index=player_index,
            phase="awaiting_draw",
            actions=[],
            hand_snapshot=copy.deepcopy(gs.player_hands[player_index]),
            discard_pile_snapshot=copy.deepcopy(gs.discard_pile),
        )
        await self._save(room_id, gs)

    async def _clear_turn_context(self, room_id: str):
        """Clear TurnContext when a turn ends."""
        gs = await self._load(room_id)
        if gs:
            gs.turn_context = None
            await self._save(room_id, gs)

    async def discard_cards(
        self,
        room_id: str,
        player_index: int,
        cards_to_discard: List[Dict],
        skip_turn_advance: bool = False,
    ) -> tuple[bool, str]:
        """Discard cards from player's hand. Returns (success, error_message)."""
        log = get_room_logger(room_id)
        gs = await self._load(room_id)
        if not gs:
            return False, "Game not found"

        if gs.phase != "playing":
            log.warn("discard_rejected", {
                "player_index": player_index,
                "reason": "wrong_phase",
                "phase": gs.phase,
            })
            return False, "Game is not in playing phase"

        if player_index != gs.current_turn:
            log.warn("discard_rejected", {
                "player_index": player_index,
                "reason": "not_your_turn",
                "current_turn": gs.current_turn,
            })
            return False, "Not your turn"

        if player_index < 0 or player_index >= len(gs.player_hands):
            return False, "Invalid player index"

        hand = [dict_to_card(card_dict) for card_dict in gs.player_hands[player_index]]

        ctx = gs.turn_context
        if ctx is not None and ctx.player_index == player_index and skip_turn_advance:
            if ctx.phase == "awaiting_discard" and any(a.startswith("discard_") for a in ctx.actions):
                log.warn("discard_rejected", {
                    "player_index": player_index,
                    "reason": "discard_already_committed_this_turn",
                    "turn_context_phase": ctx.phase,
                    "actions_taken": ctx.actions,
                })
                return False, "Discard already committed this turn — send pick_card to complete the turn"

        is_valid, error_msg = validate_discard_cards(hand, cards_to_discard)
        if not is_valid:
            log.warn("discard_rejected", {
                "player_index": player_index,
                "reason": "invalid_cards",
                "error": error_msg,
                "cards": cards_to_discard,
            })
            return False, error_msg

        updated_hand = remove_cards_from_hand(hand, cards_to_discard)
        gs.player_hands[player_index] = cards_to_dict(updated_hand)
        gs.discard_pile = cards_to_discard

        revealed_joker_card = dict_to_card(gs.revealed_joker) if gs.revealed_joker else None
        new_score = calculate_hand_score(updated_hand, revealed_joker_card)
        gs.player_scores[player_index] = new_score

        log.info("cards_discarded", {
            "player_index": player_index,
            "cards_discarded": cards_to_discard,
            "cards_count": len(cards_to_discard),
            "new_hand_size": len(updated_hand),
            "new_score": new_score,
            "skip_turn_advance": skip_turn_advance,
        })

        if gs.turn_context and gs.turn_context.player_index == player_index:
            gs.turn_context.actions.append(f"discard_{len(cards_to_discard)}_cards")
            if skip_turn_advance and gs.turn_context.phase == "awaiting_draw":
                gs.turn_context.phase = "awaiting_discard"

        if not skip_turn_advance:
            await self._save(room_id, gs)
            await self._advance_turn(room_id)
        else:
            gs.action_seq += 1
            await self._save(room_id, gs)

        return True, ""

    async def pick_card(
        self,
        room_id: str,
        player_index: int,
        from_discard: bool = False,
        skip_draw: bool = False,
        skip_turn_advance: bool = False,
    ) -> tuple[bool, Optional[Dict], str]:
        """Pick a card from deck or discard pile. Returns (success, picked_card, error_message)."""
        log = get_room_logger(room_id)
        gs = await self._load(room_id)
        if not gs:
            return False, None, "Game not found"

        if gs.phase != "playing":
            log.warn("pick_rejected", {
                "player_index": player_index,
                "reason": "wrong_phase",
                "phase": gs.phase,
            })
            return False, None, "Game is not in playing phase"

        if player_index != gs.current_turn:
            log.warn("pick_rejected", {
                "player_index": player_index,
                "reason": "not_your_turn",
                "current_turn": gs.current_turn,
            })
            return False, None, "Not your turn"

        if skip_draw:
            if not can_skip_draw(gs.discard_pile, gs.discard_pile):
                return False, None, "Cannot skip draw - discard doesn't match previous discard"
            ctx = gs.turn_context
            if ctx is not None and ctx.player_index != player_index:
                log.warn("pick_skip_draw_rejected", {
                    "player_index": player_index,
                    "reason": "turn_context_belongs_to_other_player",
                    "context_owner": ctx.player_index,
                })
                return False, None, "Compound action incomplete - another player has an active turn"
            await self._save(room_id, gs)
            await self._advance_turn(room_id)
            log.info("turn_skipped_draw", {"player_index": player_index})
            return True, None, ""

        picked_card = None

        if from_discard:
            if not gs.discard_pile:
                return False, None, "Discard pile is empty"
            picked_card = gs.discard_pile.pop()
            source = "discard_pile"
        else:
            if not gs.deck:
                return False, None, "Deck is empty"
            ctx = gs.turn_context
            if ctx is not None and ctx.player_index == player_index and not skip_turn_advance:
                if ctx.phase == "awaiting_draw":
                    log.warn("pick_rejected", {
                        "player_index": player_index,
                        "reason": "discard_step_not_yet_committed",
                        "turn_context_phase": ctx.phase,
                        "actions_taken": ctx.actions,
                    })
                    return False, None, "Must discard cards before picking from deck"
            picked_card = gs.deck.pop(0)
            source = "deck"

        hand = [dict_to_card(card_dict) for card_dict in gs.player_hands[player_index]]
        hand.append(dict_to_card(picked_card))
        gs.player_hands[player_index] = cards_to_dict(hand)

        revealed_joker_card = dict_to_card(gs.revealed_joker) if gs.revealed_joker else None
        new_score = calculate_hand_score(hand, revealed_joker_card)
        gs.player_scores[player_index] = new_score

        log.info("card_picked", {
            "player_index": player_index,
            "source": source,
            "picked_card": picked_card,
            "new_hand_size": len(hand),
            "new_score": new_score,
            "deck_remaining": len(gs.deck),
            "skip_turn_advance": skip_turn_advance,
        })

        if gs.turn_context and gs.turn_context.player_index == player_index:
            gs.turn_context.actions.append(
                "pick_from_discard" if from_discard else "pick_from_deck"
            )
            if skip_turn_advance:
                gs.turn_context.phase = "awaiting_discard"

        if not skip_turn_advance:
            await self._save(room_id, gs)
            await self._advance_turn(room_id)
        else:
            gs.action_seq += 1
            await self._save(room_id, gs)

        return True, picked_card, ""

    async def rollback_turn_if_incomplete(self, room_id: str, player_index: int) -> bool:
        """Revert mid-turn state if player disconnected after drawing but before discarding."""
        log = get_room_logger(room_id)
        gs = await self._load(room_id)
        if not gs or not gs.turn_context:
            return False

        ctx = gs.turn_context
        if ctx.player_index != player_index:
            return False

        if ctx.phase != "awaiting_discard":
            return False

        gs.player_hands[player_index] = copy.deepcopy(ctx.hand_snapshot)
        gs.discard_pile = copy.deepcopy(ctx.discard_pile_snapshot)

        revealed_joker_card = dict_to_card(gs.revealed_joker) if gs.revealed_joker else None
        hand = [dict_to_card(c) for c in gs.player_hands[player_index]]
        gs.player_scores[player_index] = calculate_hand_score(hand, revealed_joker_card)

        gs.turn_context = None
        gs.action_seq += 1

        log.info("turn_rolled_back", {
            "player_index": player_index,
            "phase_at_disconnect": ctx.phase,
            "actions_taken": ctx.actions,
        })

        await self._save(room_id, gs)
        return True

    async def declare(self, room_id: str, player_index: int) -> tuple[bool, str, bool]:
        """Player declares (score must be ≤ 10). Returns (success, message, is_wrong_declaration)."""
        log = get_room_logger(room_id)
        gs = await self._load(room_id)
        if not gs:
            return False, "Game not found", False

        if gs.phase != "playing":
            return False, "Game is not in playing phase", False

        if player_index < 0 or player_index >= len(gs.player_hands):
            return False, "Invalid player index", False

        hand = [dict_to_card(card_dict) for card_dict in gs.player_hands[player_index]]
        revealed_joker_card = dict_to_card(gs.revealed_joker) if gs.revealed_joker else None
        current_score = calculate_hand_score(hand, revealed_joker_card)

        if current_score > 10:
            gs.player_scores[player_index] += 40
            gs.phase = "declared"
            gs.declared_player = player_index
            log.warn("declaration_wrong", {
                "player_index": player_index,
                "score": current_score,
                "penalty": 40,
                "new_total_score": gs.player_scores[player_index],
            })
            await self._save(room_id, gs)
            return True, f"Wrong declaration! Score is {current_score}, not ≤ 10. 40 point penalty applied.", True

        gs.phase = "declared"
        gs.declared_player = player_index
        log.info("declaration_valid", {"player_index": player_index, "score": current_score})
        await self._save(room_id, gs)
        return True, f"Valid declaration! Your score is {current_score}.", False

    async def get_active_players(self, room_id: str) -> List[int]:
        """Return player indices that are still active (not exited via timer, not eliminated).
        
        'exited' = permanently left mid-game via missed turns (is_exited flag on PlayerInfo)
        'eliminated' = pre-excluded in tournament rounds (eliminated_indices in GameState)
        """
        gs = await self._load(room_id)
        if not gs:
            return []

        room = await room_service.get_room(room_id)
        if not room:
            return []

        eliminated_indices = gs.eliminated_indices or []
        exited_indices = await room_service.get_exited_indices(room_id)
        excluded_indices = set(eliminated_indices) | set(exited_indices)

        return [i for i in range(len(room.players)) if i not in excluded_indices]

    async def calculate_show_results(self, room_id: str, showed_by_id: str, is_survival_win: bool = False) -> Optional[List[Dict]]:
        """Calculate game results when a player shows.
        
        Exited players (is_exited=True) and tournament-eliminated players receive:
          - game_result = "lost"
          - rule_score  = 0          (no penalty added to tournament totals)
          - game_score  = their frozen score at exit time
          - is_exited_player = True  (so UI can show 'Exited' label)
        
        Exited players are placed at the end of the results list, ordered by:
          1. exited_at DESC (most-recently exited → better position)
          2. lower game_score → better position (tie-break)
        """
        log = get_room_logger(room_id)
        gs = await self._load(room_id)
        if not gs:
            return None

        room = await room_service.get_room(room_id)
        if not room:
            return None

        showed_by_index = None
        for idx, player in enumerate(room.players):
            if player.player_id == showed_by_id:
                showed_by_index = idx
                break

        if showed_by_index is None:
            return None

        revealed_joker_card = dict_to_card(gs.revealed_joker) if gs.revealed_joker else None

        player_scores = []
        for idx in range(len(room.players)):
            hand = [dict_to_card(card_dict) for card_dict in gs.player_hands[idx]]
            score = calculate_hand_score(hand, revealed_joker_card)
            player_scores.append(score)

        eliminated_indices = gs.eliminated_indices or []
        exited_indices = await room_service.get_exited_indices(room_id)
        # 'exited' = is_exited via timer; 'eliminated' = tournament pre-exclusion
        excluded_indices = set(eliminated_indices) | set(exited_indices)

        active_scores = [score for i, score in enumerate(player_scores) if i not in excluded_indices]
        min_score = min(active_scores) if active_scores else 0
        min_score_players = [i for i, score in enumerate(player_scores) if score == min_score and i not in excluded_indices]

        showed_by_has_min_score = player_scores[showed_by_index] == min_score

        # Fetch exited_at timestamps for ordering exited players
        room_raw = None
        try:
            from app.services.room_service import room_service as _rs
            raw = await _rs._load_room(room_id)
            room_raw = raw
        except Exception:
            pass

        def _exited_at(idx: int):
            """Return exited_at datetime for player at idx, or None."""
            if room_raw:
                players_raw = room_raw.get("players", [])
                if idx < len(players_raw):
                    ts = players_raw[idx].get("exited_at")
                    if ts is not None:
                        if hasattr(ts, "timestamp"):
                            return ts
                        try:
                            from datetime import datetime, timezone
                            return datetime.fromisoformat(str(ts)).replace(tzinfo=timezone.utc)
                        except Exception:
                            pass
            return None

        # Build active-player results first
        active_results = []
        for idx, player in enumerate(room.players):
            if idx in excluded_indices:
                continue
            hand = gs.player_hands[idx]
            game_score = player_scores[idx]

            if idx == showed_by_index:
                if game_score == min_score:
                    rule_score = 0
                    game_result = "won"
                else:
                    rule_score = 40
                    game_result = "lost"
            else:
                if game_score == min_score:
                    if showed_by_has_min_score:
                        # Tied with showed player: penalty of 1 (not full score)
                        rule_score = 1
                        game_result = "lost"
                    else:
                        rule_score = 0
                        game_result = "won"
                else:
                    rule_score = game_score
                    game_result = "lost"

            active_results.append({
                "player_id": player.player_id,
                "player_name": player.player_name,
                "in_hand_cards": hand,
                "game_score": game_score,
                "rule_score": rule_score,
                "game_result": game_result,
                "is_showed": (idx == showed_by_index) and not is_survival_win,
                "is_eliminated_player": False,
                "is_exited_player": False,
            })

        # Build exited/eliminated results, sorted for best position ordering:
        # most-recently exited → better (closer to last place winner)
        exited_results_raw = []
        for idx, player in enumerate(room.players):
            if idx not in excluded_indices:
                continue
            hand = gs.player_hands[idx]
            game_score = player_scores[idx]  # frozen score at exit time
            is_exited = idx in exited_indices
            exited_results_raw.append({
                "player_id": player.player_id,
                "player_name": player.player_name,
                "in_hand_cards": hand,
                "game_score": game_score,
                "rule_score": 0,
                "game_result": "lost",
                "is_showed": False,
                # They should NOT be marked eliminated if they just exited this round.
                # If they were eliminated in a PREV round, the frontend will hide them anyway.
                "is_eliminated_player": idx in eliminated_indices, 
                "is_exited_player": is_exited,
                # Sorting helpers (not sent to client)
                "_exited_at": _exited_at(idx) if is_exited else None,
                "_idx": idx,
            })

        # Sort exited players: most-recently exited = earlier in list (better position)
        # None exited_at (pre-eliminated) goes to the very end
        from datetime import datetime, timezone as _tz
        _epoch = datetime(1970, 1, 1, tzinfo=_tz.utc)

        def _exited_sort_key(r):
            ts = r.get("_exited_at")
            ts_val = ts if ts is not None else _epoch
            return (-ts_val.timestamp(), r.get("game_score", 0))

        exited_results_raw.sort(key=_exited_sort_key)

        # Strip internal sort helpers
        exited_results = []
        for r in exited_results_raw:
            clean = {k: v for k, v in r.items() if not k.startswith("_")}
            exited_results.append(clean)

        results = active_results + exited_results

        winner_index = next((i for i, r in enumerate(results) if r["game_result"] == "won"), None)
        log.info("show_results_calculated", {
            "showed_by_index": showed_by_index,
            "showed_by_id": showed_by_id,
            "showed_by_score": player_scores[showed_by_index],
            "min_score": min_score,
            "min_score_players": min_score_players,
            "winner_index": winner_index,
            "all_scores": player_scores,
            "rule_scores": [r["rule_score"] for r in results],
            "outcomes": [r["game_result"] for r in results],
        })

        return results

    async def eliminate_exited_player(self, room_id: str, player_index: int) -> bool:
        """Convert an exited player into eliminated game state."""
        gs = await self._load(room_id)
        if not gs:
            return False
        if player_index < 0 or player_index >= len(gs.player_hands):
            return False

        eliminated = list(gs.eliminated_indices or [])
        if player_index not in eliminated:
            eliminated.append(player_index)
            gs.eliminated_indices = eliminated

        gs.player_hands[player_index] = []
        if player_index < len(gs.player_scores):
            gs.player_scores[player_index] = 0

        ctx = gs.turn_context
        if ctx is not None and ctx.player_index == player_index:
            gs.turn_context = None
            get_room_logger(room_id).info("turn_context_cleared_for_eliminated_player", {
                "player_index": player_index,
                "phase_at_elimination": ctx.phase,
                "actions_taken": ctx.actions,
            })
            await self._save(room_id, gs)
            await self._advance_turn(room_id)
        else:
            gs.action_seq += 1
            await self._save(room_id, gs)

        return True

    async def _advance_turn(self, room_id: str):
        """Advance to next player's turn, skipping eliminated AND permanently-exited players."""
        log = get_room_logger(room_id)
        gs = await self._load(room_id)
        if not gs:
            return

        room = await room_service.get_room(room_id)
        if not room:
            return

        num_players = len(room.players)
        eliminated = gs.eliminated_indices or []
        exited = await room_service.get_exited_indices(room_id)
        skip_set = set(eliminated) | set(exited)

        prev_turn = gs.current_turn
        next_turn = (gs.current_turn + 1) % num_players
        attempts = 0
        skipped = []
        while next_turn in skip_set and attempts < num_players:
            skipped.append(next_turn)
            next_turn = (next_turn + 1) % num_players
            attempts += 1

        # Step 1: clear outgoing context
        gs.turn_context = None

        # Step 2: advance turn pointer
        gs.current_turn = next_turn
        gs.action_seq += 1
        gs.turn_started_at = datetime.now(timezone.utc)

        await self._save(room_id, gs)

        # Step 3: create context for incoming player AFTER saving current_turn
        await self._create_turn_context(room_id)

        # Reload for accurate seq in log
        gs = await self._load(room_id)
        log.info("turn_advanced", {
            "from_turn": prev_turn,
            "to_turn": next_turn,
            "skipped_eliminated": [s for s in skipped if s in eliminated],
            "skipped_exited": [s for s in skipped if s in exited],
            "phase": gs.phase if gs else None,
            "seq": gs.action_seq if gs else None,
        })

    async def mark_player_exited_by_timer(
        self, room_id: str, player_index: int
    ) -> tuple[int, bool]:
        """Called by the turn-timer when a player misses their turn.

        Decrements one life. If lives reach 0 the player is permanently exited
        (is_exited flag on PlayerInfo) so they are skipped in future turns.

        IMPORTANT DIFFERENCES from the old 'decrement_life':
          - We do NOT add the player to gs.eliminated_indices
            (that field is only for tournament-round pre-exclusions).
          - We do NOT clear the player's hand — their score is FROZEN at the
            current value so results can show it accurately.
          - We call room_service.set_player_exited() instead.

        Returns (remaining_lives, is_now_exited).
        """
        log = get_room_logger(room_id)
        gs = await self._load(room_id)
        if not gs:
            return 0, False

        # Ensure lives list is long enough
        while len(gs.player_lives) <= player_index:
            gs.player_lives.append(PLAYER_LIVES)

        lives = max(0, gs.player_lives[player_index] - 1)
        gs.player_lives[player_index] = lives
        is_now_exited = lives == 0

        if is_now_exited:
            # Clear any stale TurnContext for this player
            ctx = gs.turn_context
            if ctx is not None and ctx.player_index == player_index:
                gs.turn_context = None

        gs.action_seq += 1
        await self._save(room_id, gs)

        if is_now_exited:
            # Permanently mark in room data so _advance_turn skips them
            room = await room_service.get_room(room_id)
            if room and player_index < len(room.players):
                player_id = room.players[player_index].player_id
                await room_service.set_player_exited(room_id, player_id, True)

        log.info("player_life_decremented", {
            "player_index": player_index,
            "lives_remaining": lives,
            "is_now_exited": is_now_exited,
        })
        return lives, is_now_exited

    async def server_play_for_player(
        self, room_id: str, player_index: int
    ) -> bool:
        """Bot plays one full turn on behalf of player_index.

        Decision mirrors client-side BotLogic.processGameState():
          score < 11                          → show
          discard top value < 4 & < highest  → pick from discard pile, then discard highest
          slashable cards available           → slash (discard matching cards, no pick)
          else                               → pick from deck, then discard highest

        Emits player_action events so all clients see animations:
          1. card_dropped  (for each card to discard)
          2. pick_from_deck / pick_from_discard  (if picking)
          3. cards_discarded / slash  (after state mutation)

        Must be called after rollback_turn_if_incomplete so the hand is clean
        and the turn phase is 'awaiting_draw'.
        Returns True on success.
        """
        import asyncio as _asyncio
        log = get_room_logger(room_id)
        gs = await self._load(room_id)
        if not gs or gs.phase != "playing" or gs.current_turn != player_index:
            log.warn("server_play_skipped", {
                "player_index": player_index,
                "phase": gs.phase if gs else None,
                "current_turn": gs.current_turn if gs else None,
            })
            return False

        hand_dicts = gs.player_hands[player_index]
        if not hand_dicts:
            # Empty hand — just advance the turn
            await self._advance_turn(room_id)
            return True

        from app.api.websocket.connection_manager import manager

        # Get room and player_id for broadcasts
        room = await room_service.get_room(room_id)
        if not room or player_index >= len(room.players):
            await self._advance_turn(room_id)
            return False
        player_id = room.players[player_index].player_id

        revealed_joker_card = dict_to_card(gs.revealed_joker) if gs.revealed_joker else None
        hand = [dict_to_card(c) for c in hand_dicts]
        score = calculate_hand_score(hand, revealed_joker_card)

        discard_top_dict = gs.discard_pile[-1] if gs.discard_pile else None
        discard_top = dict_to_card(discard_top_dict) if discard_top_dict else None

        # ── Helper: card point value (mirrors Flutter BotLogic.getCardValue) ────────
        def _card_value(card: Card) -> int:
            if revealed_joker_card and card.rank == revealed_joker_card.rank:
                return 0
            rank_values = {
                "JOKER": 0, "A": 1, "2": 2, "3": 3, "4": 4, "5": 5,
                "6": 6, "7": 7, "8": 8, "9": 9,
                "10": 10, "J": 10, "Q": 10, "K": 10,
            }
            return rank_values.get(card.rank.value if hasattr(card.rank, 'value') else str(card.rank), 10)

        def _find_highest(cards: list) -> Optional[Card]:
            if not cards:
                return None
            return max(cards, key=lambda c: _card_value(c))

        def _find_all_same_value(cards: list, target: Card) -> list:
            """Return all cards whose face value matches target (for slash)."""
            return [c for c in cards if c.rank == target.rank]

        # ── Helper: emit animation delay ───────────────────────────────────
        async def _anim_delay():
            await _asyncio.sleep(BOT_PLAY_ANIMATION_DELAY_MS / 1000.0)

        # ── Helper: broadcast card_dropped events ───────────────────────────
        async def _emit_card_dropped(cards_to_drop: list):
            """Emit a card_dropped player_action for each card (with delay between)."""
            for card in cards_to_drop:
                card_dict = card.to_dict()
                await manager.broadcast_to_room({
                    "type": "player_action",
                    "data": {
                        "action_type": "card_dropped",
                        "player_id": player_id,
                        "card": card_dict,
                        "is_bot_play": True,
                    },
                }, room_id)
                log.info("server_play_emit_card_dropped", {
                    "player_index": player_index,
                    "card": card_dict,
                })
                await _anim_delay()

        # ── BotLogic decision tree (mirrors Flutter BotLogic.processGameState) ────
        highest = _find_highest(hand)

        # ACTION: show (score already low enough)
        if score < 11:
            log.info("server_play_show", {"player_index": player_index, "score": score})
            results = await self.calculate_show_results(room_id, player_id)
            if results is None:
                return False
            
            from app.api.routes.games import process_and_broadcast_show_results
            await process_and_broadcast_show_results(room_id, player_id, results, is_survival_win=False, is_bot_play=True)
            return True

        if highest is None:
            await self._advance_turn(room_id)
            return True

        highest_val = _card_value(highest)

        # ACTION: pick from discard pile (same condition as Flutter BotLogic)
        pick_from_discard = False
        if discard_top is not None:
            discard_val = _card_value(discard_top)
            if discard_val < 4 and discard_val < highest_val:
                pick_from_discard = True

        # ACTION: slash — if discard pile top card matches cards in hand (not taking discard)
        slashable = []
        if not pick_from_discard and discard_top is not None:
            slashable = _find_all_same_value(hand, discard_top)

        if slashable:
            # ── SLASH: drop matching cards, no pick ───────────────────────────────
            log.info("server_play_slash", {"player_index": player_index, "slash_cards": [c.to_dict() for c in slashable]})

            # Step 1: Emit card_dropped for each slashable card
            await _emit_card_dropped(slashable)

            # Step 2: Emit slash action (last card, with count)
            last_slash_card = slashable[-1].to_dict()
            if len(slashable) > 1:
                last_slash_card['count'] = len(slashable)
            await manager.broadcast_to_room({
                "type": "player_action",
                "data": {
                    "action_type": "slash",
                    "player_id": player_id,
                    "card": last_slash_card,
                    "is_bot_play": True,
                },
            }, room_id)

            # Step 3: State mutation — discard slashable cards, skip draw (slash = discard + no pick)
            slash_dicts = cards_to_dict(slashable)
            ok2, err2 = await self.discard_cards(room_id, player_index, slash_dicts, skip_turn_advance=True)
            if not ok2:
                log.warn("server_play_slash_discard_failed", {"error": err2})
                await self._advance_turn(room_id)
                return False
            # Slash: skip_draw=True, skip_turn_advance=False → advances turn
            ok3, _, err3 = await self.pick_card(room_id, player_index, from_discard=False, skip_draw=True, skip_turn_advance=False)
            if not ok3:
                log.warn("server_play_slash_pick_skip_failed", {"error": err3})
                await self._advance_turn(room_id)
                return False

            log.info("server_play_slash_completed", {"player_index": player_index})
            return True

        # ── PICK then DISCARD ─────────────────────────────────────────────
        # Cards to drop: all with same value as highest (same as Flutter findAllIndexes)
        cards_to_drop = _find_all_same_value(hand, highest)
        if not cards_to_drop:
            cards_to_drop = [highest]

        # Step 1: Emit card_dropped for each card to discard
        await _emit_card_dropped(cards_to_drop)

        # Step 2: Emit pick action (before mutation so clients see pick animation)
        if pick_from_discard:
            log.info("server_play_pick_discard", {"player_index": player_index})
            pick_card_data = discard_top.to_dict() if discard_top else None
            await manager.broadcast_to_room({
                "type": "player_action",
                "data": {
                    "action_type": "pick_from_discard",
                    "player_id": player_id,
                    "card": pick_card_data,
                    "is_bot_play": True,
                },
            }, room_id)
        else:
            log.info("server_play_pick_deck", {"player_index": player_index})
            await manager.broadcast_to_room({
                "type": "player_action",
                "data": {
                    "action_type": "pick_from_deck",
                    "player_id": player_id,
                    "card": None,
                    "is_bot_play": True,
                },
            }, room_id)

        await _anim_delay()

        discard_target = cards_to_dict(cards_to_drop)

        if pick_from_discard:
            # Drop+Pick-from-discard MUST happen in this exact order to avoid picking up the dropped card:
            # 1. pick_card(from_discard=True, skip_turn_advance=True)
            # 2. discard_cards(skip_turn_advance=True)
            # 3. pick_card(skip_draw=True, skip_turn_advance=False) - advances turn
            ok_pick1, _, err_pick1 = await self.pick_card(room_id, player_index, from_discard=True, skip_turn_advance=True)
            if not ok_pick1:
                log.warn("server_play_pick1_failed", {"error": err_pick1})
                await self._advance_turn(room_id)
                return False
                
            ok_discard, err_discard = await self.discard_cards(room_id, player_index, discard_target, skip_turn_advance=True)
            if not ok_discard:
                log.warn("server_play_discard_failed", {"error": err_discard})
                await self._advance_turn(room_id)
                return False
                
            ok_pick2, _, err_pick2 = await self.pick_card(room_id, player_index, from_discard=False, skip_draw=True, skip_turn_advance=False)
            if not ok_pick2:
                log.warn("server_play_pick2_failed", {"error": err_pick2})
                await self._advance_turn(room_id)
                return False
        else:
            # Drop+Pick-from-deck
            # 1. discard_cards(skip_turn_advance=True)
            # 2. pick_card(from_discard=False, skip_turn_advance=False) - advances turn
            ok_discard, err_discard = await self.discard_cards(room_id, player_index, discard_target, skip_turn_advance=True)
            if not ok_discard:
                log.warn("server_play_discard_failed", {"error": err_discard})
                await self._advance_turn(room_id)
                return False
                
            ok_pick, _, err_pick = await self.pick_card(room_id, player_index, from_discard=False, skip_turn_advance=False)
            if not ok_pick:
                log.warn("server_play_pick_failed", {"error": err_pick})
                await self._advance_turn(room_id)
                return False

        # Step 5: Broadcast cards_discarded so clients trigger discard-pile animation
        last_discard_card = discard_target[-1] if isinstance(discard_target, list) and discard_target else discard_target
        if len(discard_target) > 1:
            last_discard_card = dict(last_discard_card)
            last_discard_card['count'] = len(discard_target)
        await manager.broadcast_to_room({
            "type": "player_action",
            "data": {
                "action_type": "cards_discarded",
                "player_id": player_id,
                "card": last_discard_card,
                "is_bot_play": True,
            },
        }, room_id)

        log.info("server_play_completed_ok", {
            "player_index": player_index,
            "from_discard": pick_from_discard,
        })
        return True


# Global instance
game_service = GameService()
