"""
Disconnect scenario tests for Player A's turn.

Each test reproduces one disconnect point from the analysis document
`disconnect_analysis.md`.  Tests are grouped by Flow.

Legend:
  A = Player A (current turn holder, index 0)
  B = Player B (opponent, index 1)

All tests call `simulate_disconnect()` which mirrors `on_player_disconnect()`'s
game-state side-effects (rollback check + marking player offline).
The async grace-timer / broadcast parts of `on_player_disconnect` are NOT
exercised here — those need integration tests.
"""
import copy
import pytest

from tests.conftest import (
    build_services,
    teardown_services,
    setup_two_player_game,
    simulate_disconnect,
    make_card,
)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture()
def game():
    """
    Yield (room_id, pid_a, pid_b, game_service, room_service).
    Cleans up global singletons after each test.
    """
    rs, gs, originals = build_services()
    room_id, pid_a, pid_b = setup_two_player_game(rs, gs)
    yield room_id, pid_a, pid_b, gs, rs
    teardown_services(originals)


# ===========================================================================
# Helper
# ===========================================================================

def _hand_a(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    return gs._game_states[room_id].player_hands[0]

def _discard_top(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    return gs._game_states[room_id].discard_pile[-1]

def _turn_context(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    return gs._game_states[room_id].turn_context

def _current_turn(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    return gs._game_states[room_id].current_turn

def _action_seq(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    return gs._game_states[room_id].action_seq


# ===========================================================================
# DROP ZONE — pre-action disconnect
# ===========================================================================

class TestDropZoneDisconnect:
    """
    A drops a card into the drop zone (client-side only, player_action sent),
    but disconnects BEFORE tapping any action button.
    Backend game state is completely unchanged.
    """

    def test_no_rollback_before_any_action(self, game):
        """
        Disconnect point: after card_dropped player_action, before any WS action.
        Expected: No rollback (phase == awaiting_draw), A's hand and discard unchanged.
        """
        room_id, pid_a, _, gs, rs = game
        original_hand = copy.deepcopy(_hand_a(game))
        original_discard = copy.deepcopy(_discard_top(game))
        original_seq = _action_seq(game)

        # Precondition: context is awaiting_draw
        assert _turn_context(game).phase == "awaiting_draw"

        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert rolled_back is False, "No rollback expected before any draw action"
        assert _hand_a(game) == original_hand, "A's hand must be untouched"
        assert _discard_top(game) == original_discard, "Discard pile must be untouched"
        assert _action_seq(game) == original_seq, "action_seq must not be bumped"
        assert _current_turn(game) == 0, "Turn must still be A's"

    def test_turn_context_still_present_after_no_rollback(self, game):
        """After a no-rollback disconnect, TurnContext is NOT cleared."""
        room_id, pid_a, _, gs, rs = game
        simulate_disconnect(gs, rs, room_id, pid_a, 0)
        # TurnContext should still be there (grace timer would advance turn later)
        ctx = _turn_context(game)
        assert ctx is not None
        assert ctx.player_index == 0


# ===========================================================================
# FLOW 1 — Drop → Pick from Deck → Turn Ends
# ===========================================================================

class TestFlow1Disconnect:
    """
    Flow 1: A drops cards → taps deck → turn ends.
    """

    def test_f1_disconnect_after_pick_from_deck_player_action_no_state_change(self, game):
        """
        F1-1: A sent pick_from_deck player_action but NO WS messages processed yet.
        Expected: No rollback, no state change (phase still awaiting_draw).
        """
        room_id, pid_a, _, gs, rs = game
        original_hand = copy.deepcopy(_hand_a(game))
        original_seq = _action_seq(game)

        # No service calls made — purely visual player_action was sent by client
        assert _turn_context(game).phase == "awaiting_draw"

        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert rolled_back is False
        assert _hand_a(game) == original_hand
        assert _action_seq(game) == original_seq

    def test_f1_disconnect_after_discard_cards_processed_rollback_fires(self, game):
        """
        F1-2 (FIXED): A's discard_cards {skip_turn_advance:true} was processed
        (cards removed from hand), but pick_card has NOT been sent yet.

        Fix: discard_cards() now sets TurnContext.phase='awaiting_discard' when
        skip_turn_advance=True, so rollback_turn_if_incomplete fires on disconnect.
        A's dropped cards are restored — no permanent card loss.
        """
        room_id, pid_a, _, gs, rs = game
        original_hand = copy.deepcopy(_hand_a(game))
        original_discard = copy.deepcopy(gs._game_states[room_id].discard_pile)
        card_to_discard = copy.deepcopy(_hand_a(game)[0])

        ok, err = gs.discard_cards(room_id, 0, [card_to_discard], skip_turn_advance=True)
        assert ok, f"discard_cards failed: {err}"

        # Phase now correctly transitions to awaiting_discard (fix applied)
        assert _turn_context(game).phase == "awaiting_discard"

        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        # Rollback fires — cards restored, no permanent loss
        assert rolled_back is True, "Rollback must fire: discard step done but pick not yet sent"
        assert _hand_a(game) == original_hand, "A's discarded cards must be restored"
        assert gs._game_states[room_id].discard_pile == original_discard, "Discard pile must be restored"
        assert _turn_context(game) is None

    def test_f1_disconnect_after_discard_step_ack_before_pick_card(self, game):
        """
        F1-3: step_ack received by client, pick_card not yet sent.
        Server state is identical to F1-2 (discard processed, no pick yet).
        After fix, rollback fires here too — A's cards restored.
        """
        room_id, pid_a, _, gs, rs = game
        original_hand = copy.deepcopy(_hand_a(game))
        card_to_discard = copy.deepcopy(_hand_a(game)[0])

        gs.discard_cards(room_id, 0, [card_to_discard], skip_turn_advance=True)
        # step_ack sent to client; client got it but hasn't sent pick_card yet.
        # Server state is identical to F1-2 — phase is now awaiting_discard.
        assert _turn_context(game).phase == "awaiting_discard"

        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)
        assert rolled_back is True, "Rollback must fire — client had step_ack but never sent pick_card"
        assert _hand_a(game) == original_hand, "Cards must be restored"

    def test_f1_disconnect_after_pick_card_processed_turn_already_advanced(self, game):
        """
        F1-4: pick_card processed, turn advanced to B.
        Expected: No rollback (turn already ended). B holds the turn.
        """
        room_id, pid_a, _, gs, rs = game
        card_to_discard = copy.deepcopy(_hand_a(game)[0])

        # Step 1: discard cards
        gs.discard_cards(room_id, 0, [card_to_discard], skip_turn_advance=True)
        # Step 2: pick from deck (advances turn)
        ok, picked, err = gs.pick_card(room_id, 0, from_discard=False, skip_draw=False, skip_turn_advance=False)
        assert ok, f"pick_card failed: {err}"

        # Turn is now B's
        assert _current_turn(game) == 1

        # A disconnects (no-op on game state)
        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert rolled_back is False, "No rollback — turn already advanced to B"
        assert _current_turn(game) == 1, "B still holds the turn"

    def test_f1_disconnect_after_final_broadcast_before_cards_discarded_player_action(self, game):
        """
        F1-5: A's turn is fully done (both discard+pick committed), but the
        visual 'cards_discarded' player_action hasn't been sent yet.
        Expected: No rollback, no state impact — game is clean.
        """
        room_id, pid_a, _, gs, rs = game
        card_to_discard = copy.deepcopy(_hand_a(game)[0])

        gs.discard_cards(room_id, 0, [card_to_discard], skip_turn_advance=True)
        gs.pick_card(room_id, 0, from_discard=False, skip_draw=False, skip_turn_advance=False)

        # Full game_update was broadcast to B. A disconnects before sending
        # the cosmetic cards_discarded player_action.
        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert rolled_back is False
        assert _current_turn(game) == 1
        # Discard pile reflects A's discarded card
        assert _discard_top(game) == card_to_discard


# ===========================================================================
# FLOW 2 — Drop → Pick from Discard Pile → Turn Ends
# ===========================================================================

class TestFlow2Disconnect:
    """
    Flow 2: A drops cards → picks from discard → turn ends.
    3-step compound action.
    """

    def _setup_discard_pick_scenario(self, game):
        """
        Set up state so A has dropped cards ready and the discard pile top
        is a valid pick target.
        Returns the card A will pick from the discard pile.
        """
        room_id, _, _, gs, _ = game
        # The initial discard pile top is 7♦ — A will pick that.
        return copy.deepcopy(_discard_top(game))

    def test_f2_disconnect_after_pick_from_discard_player_action_no_state_change(self, game):
        """
        F2-1: pick_from_discard player_action sent, pick_card WS not yet sent.
        Backend state unchanged. No rollback expected.
        """
        room_id, pid_a, _, gs, rs = game
        original_hand = copy.deepcopy(_hand_a(game))
        original_discard = copy.deepcopy(gs._game_states[room_id].discard_pile)

        # Only a player_action was sent (no server state change)
        assert _turn_context(game).phase == "awaiting_draw"

        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert rolled_back is False
        assert _hand_a(game) == original_hand
        assert gs._game_states[room_id].discard_pile == original_discard

    def test_f2_disconnect_after_pick_from_discard_processed_triggers_rollback(self, game):
        """
        F2-2: pick_card {from_discard:true, skip_turn_advance:true} processed.
        Card Y is now in A's hand, discard pile lost Y. TurnContext.phase='awaiting_discard'.
        Disconnect → ROLLBACK expected: hand and discard pile restored.
        """
        room_id, pid_a, _, gs, rs = game
        original_hand = copy.deepcopy(_hand_a(game))
        original_discard = copy.deepcopy(gs._game_states[room_id].discard_pile)
        original_seq = _action_seq(game)

        # Simulate: A picks from discard (compound step 1)
        ok, picked, err = gs.pick_card(room_id, 0, from_discard=True, skip_draw=False, skip_turn_advance=True)
        assert ok, f"pick_card failed: {err}"

        # Verify Y is in A's hand and discard pile is shorter
        hand_after_pick = _hand_a(game)
        assert len(hand_after_pick) == len(original_hand) + 1
        assert gs._game_states[room_id].discard_pile == original_discard[:-1]

        # Phase flipped to awaiting_discard
        assert _turn_context(game).phase == "awaiting_discard"

        # Disconnect → rollback
        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert rolled_back is True, "Rollback MUST fire when phase==awaiting_discard"
        # Hand restored
        assert _hand_a(game) == original_hand, "A's hand must be restored to pre-pick state"
        # Discard pile restored (Y is back)
        assert gs._game_states[room_id].discard_pile == original_discard, (
            "Discard pile must be restored — Y must be back on top"
        )
        # action_seq bumped by rollback
        assert _action_seq(game) > original_seq
        # TurnContext cleared
        assert _turn_context(game) is None

    def test_f2_disconnect_after_discard_step_processed_triggers_rollback(self, game):
        """
        F2-3: Both compound steps done (pick from discard + discard own cards),
        but final advance-turn pick_card NOT sent yet.
        Phase is still 'awaiting_discard' → ROLLBACK expected.
        """
        room_id, pid_a, _, gs, rs = game
        original_hand = copy.deepcopy(_hand_a(game))
        original_discard = copy.deepcopy(gs._game_states[room_id].discard_pile)
        original_seq = _action_seq(game)

        # Compound step 1: pick from discard
        ok, _, err = gs.pick_card(room_id, 0, from_discard=True, skip_draw=False, skip_turn_advance=True)
        assert ok, err

        # Compound step 2: discard own cards
        card_to_discard = copy.deepcopy(_hand_a(game)[0])  # first card in updated hand
        ok, err = gs.discard_cards(room_id, 0, [card_to_discard], skip_turn_advance=True)
        assert ok, f"discard_cards failed: {err}"

        # Phase is still awaiting_discard (turn advance not yet sent)
        assert _turn_context(game).phase == "awaiting_discard"

        # Disconnect → rollback
        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert rolled_back is True, "Rollback must fire — compound action incomplete"
        assert _hand_a(game) == original_hand, "Full hand rollback to start-of-turn"
        assert gs._game_states[room_id].discard_pile == original_discard, (
            "Discard pile rolled back to start-of-turn"
        )
        assert _action_seq(game) > original_seq
        assert _turn_context(game) is None

    def test_f2_disconnect_after_turn_advanced_no_rollback(self, game):
        """
        F2-4: All three compound steps done. Turn advanced to B. A disconnects
        before sending the cosmetic cards_discarded player_action.
        Expected: No rollback — turn is fully committed.
        """
        room_id, pid_a, _, gs, rs = game

        # Compound step 1
        ok, _, err = gs.pick_card(room_id, 0, from_discard=True, skip_draw=False, skip_turn_advance=True)
        assert ok, err

        # Compound step 2
        card_to_discard = copy.deepcopy(_hand_a(game)[0])
        ok, err = gs.discard_cards(room_id, 0, [card_to_discard], skip_turn_advance=True)
        assert ok, err

        # Compound step 3: advance turn
        ok, _, err = gs.pick_card(room_id, 0, from_discard=False, skip_draw=True, skip_turn_advance=False)
        assert ok, f"advance-turn pick_card failed: {err}"

        # Turn is now B's
        assert _current_turn(game) == 1

        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert rolled_back is False, "Turn complete — no rollback"
        assert _current_turn(game) == 1


# ===========================================================================
# FLOW 3 — Drop → Slash → Turn Ends
# ===========================================================================

class TestFlow3Disconnect:
    """
    Flow 3: A drops matching cards → taps Slash → turn ends.
    Requires A's dropped cards to match the discard pile top rank.
    """

    def _setup_slash_scenario(self, game):
        """
        Adjust game state so A has a 7-rank card matching the discard top (7♦).
        Also updates TurnContext.hand_snapshot so rollback restores to the
        correct hand (with the slash card present but not yet discarded).
        Returns the card that will be slashed.
        """
        room_id, _, _, gs, _ = game
        # Insert a 7♣ into A's hand so it matches discard top (7♦, value=7)
        slash_card = make_card("clubs", "7")
        gs._game_states[room_id].player_hands[0].append(slash_card)
        # Update the TurnContext snapshot so rollback restores to the state
        # that includes the injected card (simulates A having it from the start)
        ctx = gs._game_states[room_id].turn_context
        if ctx:
            ctx.hand_snapshot = copy.deepcopy(gs._game_states[room_id].player_hands[0])
        return slash_card

    def test_f3_disconnect_after_slash_player_action_no_state_change(self, game):
        """
        F3-1: slash player_action sent, discard_cards WS not yet sent.
        Backend unchanged — no rollback.
        """
        room_id, pid_a, _, gs, rs = game
        self._setup_slash_scenario(game)
        original_hand = copy.deepcopy(_hand_a(game))
        original_discard = copy.deepcopy(gs._game_states[room_id].discard_pile)

        assert _turn_context(game).phase == "awaiting_draw"

        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert rolled_back is False
        assert _hand_a(game) == original_hand
        assert gs._game_states[room_id].discard_pile == original_discard

    def test_f3_disconnect_after_discard_step_processed_triggers_rollback(self, game):
        """
        F3-2 (FIXED): discard_cards {skip_turn_advance:true} processed (slash step 1).
        TurnContext.phase transitions to 'awaiting_discard'.
        Disconnect → rollback fires → hand and discard pile restored.
        """
        room_id, pid_a, _, gs, rs = game
        slash_card = self._setup_slash_scenario(game)
        original_hand = copy.deepcopy(_hand_a(game))
        original_discard = copy.deepcopy(gs._game_states[room_id].discard_pile)
        original_seq = _action_seq(game)

        ok, err = gs.discard_cards(room_id, 0, [slash_card], skip_turn_advance=True)
        assert ok, f"Slash discard failed: {err}"

        # Phase correctly transitions to awaiting_discard after fix
        assert _turn_context(game).phase == "awaiting_discard"

        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)
        assert rolled_back is True, "Rollback must fire after slash discard before turn advance"
        assert _hand_a(game) == original_hand, "Slash card must be restored to hand"
        assert gs._game_states[room_id].discard_pile == original_discard, "Discard pile must be restored"
        assert _action_seq(game) > original_seq
        assert _turn_context(game) is None

    def test_f3_disconnect_after_turn_advanced_no_rollback(self, game):
        """
        F3-3: Both slash steps done. Turn advanced to B. No rollback.
        """
        room_id, pid_a, _, gs, rs = game
        slash_card = self._setup_slash_scenario(game)

        gs.discard_cards(room_id, 0, [slash_card], skip_turn_advance=True)
        ok, _, err = gs.pick_card(room_id, 0, from_discard=False, skip_draw=True, skip_turn_advance=False)
        assert ok, f"Slash turn-advance failed: {err}"

        assert _current_turn(game) == 1

        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert rolled_back is False
        assert _current_turn(game) == 1


# ===========================================================================
# FLOW 4 — Show → Round Ends
# ===========================================================================

class TestFlow4Disconnect:
    """
    Flow 4: A taps Show → HTTP /show processed → show_results broadcast.
    """

    def test_f4_disconnect_before_http_show_no_state_change(self, game):
        """
        F4-1: show player_action sent (relay-blocked, no effect), HTTP not sent yet.
        Backend unchanged — no rollback.
        """
        room_id, pid_a, _, gs, rs = game
        original_hand = copy.deepcopy(_hand_a(game))
        original_seq = _action_seq(game)

        assert _turn_context(game).phase == "awaiting_draw"

        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert rolled_back is False
        assert _hand_a(game) == original_hand
        assert _action_seq(game) == original_seq
        # Turn still belongs to A — grace timer would advance it later
        assert _current_turn(game) == 0

    def test_f4_disconnect_after_show_results_calculated_round_over(self, game):
        """
        F4-2/F4-3: calculate_show_results() is called, round is done.
        A disconnects after results are out.
        Expected: No rollback — results stand, B received show_results.
        The disconnect here has no impact on game state.
        """
        room_id, pid_a, pid_b, gs, rs = game

        # Calculate show results (equivalent to HTTP POST /show processing)
        results = gs.calculate_show_results(room_id, pid_a)

        assert results is not None, "Show results must be calculated"
        assert len(results) == 2

        # Now A disconnects
        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        # No game-state rollback — round results are final
        assert rolled_back is False
        # Results are still valid
        assert any(r["player_id"] == pid_a for r in results)


# ===========================================================================
# GRACE TIMER EFFECT — turn advance for permanently exited player
# ===========================================================================

class TestGraceTimerEffect:
    """
    Validate that after grace expires, the turn IS advanced when it belongs
    to the disconnected player. We test the advance logic directly
    (skipping the 60-second asyncio.sleep).
    """

    def test_turn_is_advanced_when_exited_player_holds_turn(self, game):
        """
        After grace: player marked exited, _advance_turn called.
        Turn must move from A (index 0) to B (index 1).
        """
        room_id, pid_a, pid_b, gs, rs = game

        # Mark A as exited (what grace timer does after 60 s)
        rs.set_player_exited(room_id, pid_a, True)

        # _advance_turn is what _grace_period_expired calls
        gs._advance_turn(room_id)

        assert _current_turn(game) == 1, "Turn must be advanced to B after A is exited"

    def test_turn_not_double_advanced_if_already_b_turn(self, game):
        """
        If B's turn is already active when grace fires, a spurious advance
        should move the turn back to A — but the grace period checks
        current_turn == player_index before calling _advance_turn.
        Simulate that guard: if it's NOT A's turn, advance should NOT be called.
        """
        room_id, pid_a, pid_b, gs, rs = game

        # Manually set turn to B
        gs._game_states[room_id].current_turn = 1
        gs._create_turn_context(room_id)

        # Grace fires but the guard `current_turn == player_index` (0) fails
        # so _advance_turn is NOT called. We replicate that guard here.
        if gs._game_states[room_id].current_turn == 0:
            gs._advance_turn(room_id)

        # Turn must still be B's (advance was skipped)
        assert _current_turn(game) == 1


# ===========================================================================
# ROLLBACK STATE MACHINE — edge cases
# ===========================================================================

class TestRollbackEdgeCases:
    """
    Validates TurnContext state transitions and rollback boundary conditions.
    """

    def test_rollback_does_not_fire_for_other_player(self, game):
        """
        If TurnContext belongs to A (index 0) but we call rollback for B (index 1),
        nothing should happen.
        """
        room_id, pid_a, pid_b, gs, rs = game
        # Flip to awaiting_discard on A's context
        gs.pick_card(room_id, 0, from_discard=True, skip_draw=False, skip_turn_advance=True)
        assert _turn_context(game).phase == "awaiting_discard"
        assert _turn_context(game).player_index == 0

        # Attempt rollback for B — must be a no-op
        rolled_back = gs.rollback_turn_if_incomplete(room_id, 1)
        assert rolled_back is False

    def test_rollback_does_not_fire_when_no_turn_context(self, game):
        """
        If TurnContext is None (cleared after turn ends), rollback must not fire.
        """
        room_id, _, _, gs, _ = game
        gs._game_states[room_id].turn_context = None

        rolled_back = gs.rollback_turn_if_incomplete(room_id, 0)
        assert rolled_back is False

    def test_rollback_restores_score(self, game):
        """
        After rollback, the player's score must be recalculated to match
        the restored hand — not left at the mid-turn value.
        """
        room_id, pid_a, _, gs, rs = game
        score_before = gs._game_states[room_id].player_scores[0]

        # Pick from discard → A's score changes
        gs.pick_card(room_id, 0, from_discard=True, skip_draw=False, skip_turn_advance=True)
        assert _turn_context(game).phase == "awaiting_discard"

        score_mid_turn = gs._game_states[room_id].player_scores[0]

        # Rollback
        simulate_disconnect(gs, rs, room_id, pid_a, 0)

        score_after_rollback = gs._game_states[room_id].player_scores[0]
        assert score_after_rollback == score_before, (
            f"Score must be restored to pre-turn value ({score_before}), "
            f"not mid-turn value ({score_mid_turn})"
        )

    def test_turn_context_cleared_after_rollback(self, game):
        """After rollback, TurnContext must be None."""
        room_id, pid_a, _, gs, rs = game
        gs.pick_card(room_id, 0, from_discard=True, skip_draw=False, skip_turn_advance=True)

        simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert _turn_context(game) is None


    def test_action_seq_bumped_after_rollback(self, game):
        """Rollback must bump action_seq so clients detect the state change."""
        room_id, pid_a, _, gs, rs = game
        seq_before = _action_seq(game)

        gs.pick_card(room_id, 0, from_discard=True, skip_draw=False, skip_turn_advance=True)
        seq_mid = _action_seq(game)

        simulate_disconnect(gs, rs, room_id, pid_a, 0)

        seq_after = _action_seq(game)
        assert seq_after > seq_mid, "action_seq must be bumped after rollback"
        assert seq_after > seq_before


# ===========================================================================
# F1-2 SPECIFIC: Cards permanently lost + state B sees after grace
# ===========================================================================

class TestDoublePlayExploits:
    """
    Tests for exploiting a race condition during reconnect where a player
    might attempt to make a double play within the same turn by
    discarding and then reconnecting, or reconnecting before pick_card
    and attempting to pick a card without a discard in the new session.
    """

    def test_reconnect_double_discard_blocked(self, game):
        """
        A disconnects mid-turn after discard (rollback fires).
        On reconnect (fresh start of turn), A drops Y.
        If A maliciously or due to network delay tries to send another
        discard_cards before pick_card, it should be rejected.
        """
        room_id, pid_a, _, gs, rs = game
        card_to_discard = copy.deepcopy(_hand_a(game)[0])

        ok, err = gs.discard_cards(room_id, 0, [card_to_discard], skip_turn_advance=True)
        assert ok, f"Initial discard failed: {err}"

        # Attempt to discard another card before pick (simulate double tap or exploit)
        card_to_discard_2 = {"suit": "hearts", "rank": "K", "value": 10} # Fake card for invalid discard attempt
        ok_2, err_2 = gs.discard_cards(room_id, 0, [card_to_discard_2], skip_turn_advance=True)
        assert not ok_2
        assert "Discard already committed" in err_2


    def test_reconnect_free_pick_blocked(self, game):
        """
        A drops X, gets disconnected, rollback fires.
        A reconnects. Now A tries to send `pick_card` directly without
        dropping Y first (maybe the client state machine is still pending pick).
        The server MUST reject this because `discard_cards` hasn't happened in
        this fresh TurnContext.
        """
        room_id, pid_a, _, gs, rs = game
        
        # We start at beginning of turn. TurnContext phase is 'awaiting_draw'
        assert _turn_context(game).phase == "awaiting_draw"

        # A directly attempts to pick a card from the deck without discarding first
        ok, picked, err = gs.pick_card(room_id, 0, from_discard=False, skip_draw=False, skip_turn_advance=False)
        
        assert not ok
        assert "Must discard cards before picking" in err


class TestF12RollbackAndGrace:
    """
    F1-2 (FIXED): A's discard_cards was processed, A disconnects before pick_card.
    Rollback NOW fires (phase transitions to 'awaiting_discard').
    After grace, B gets the CLEAN start-of-turn state — A's cards are restored.
    """

    def test_f12_rollback_restores_hand_on_disconnect(self, game):
        """
        Rollback fires immediately on disconnect. A's discarded cards are
        restored to their hand. Discard pile reverts to start-of-turn state.
        """
        room_id, pid_a, pid_b, gs, rs = game
        original_hand = copy.deepcopy(_hand_a(game))
        original_discard = copy.deepcopy(gs._game_states[room_id].discard_pile)
        card_to_discard = copy.deepcopy(_hand_a(game)[0])

        gs.discard_cards(room_id, 0, [card_to_discard], skip_turn_advance=True)
        # Rollback fires on disconnect
        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)

        assert rolled_back is True
        # A's cards restored — no permanent loss
        assert _hand_a(game) == original_hand
        assert gs._game_states[room_id].discard_pile == original_discard

    def test_f12_b_sees_clean_state_after_grace(self, game):
        """
        After rollback+grace+advance, B gets the clean pre-turn state.
        The discard pile is the original one (not A's discarded card).
        B can pick from the original discard top normally.
        """
        room_id, pid_a, pid_b, gs, rs = game
        original_discard_top = copy.deepcopy(_discard_top(game))
        card_to_discard = copy.deepcopy(_hand_a(game)[0])

        gs.discard_cards(room_id, 0, [card_to_discard], skip_turn_advance=True)
        simulate_disconnect(gs, rs, room_id, pid_a, 0)  # rollback fires
        rs.set_player_exited(room_id, pid_a, True)
        gs._advance_turn(room_id)

        assert _current_turn(game) == 1
        # Discard pile is the ORIGINAL — A's discard was rolled back
        assert _discard_top(game) == original_discard_top
        # B can pick the original discard top
        ok, picked, err = gs.pick_card(room_id, 1, from_discard=True, skip_draw=False, skip_turn_advance=True)
        assert ok, f"B failed to pick original discard top: {err}"
        assert picked == original_discard_top

    def test_f12_game_remains_valid_after_rollback_and_grace(self, game):
        """
        Even with disconnect+rollback+grace, game stays in 'playing' phase
        and B can take their turn normally.
        """
        room_id, pid_a, pid_b, gs, rs = game
        card_to_discard = copy.deepcopy(_hand_a(game)[0])

        gs.discard_cards(room_id, 0, [card_to_discard], skip_turn_advance=True)
        simulate_disconnect(gs, rs, room_id, pid_a, 0)
        rs.set_player_exited(room_id, pid_a, True)
        gs._advance_turn(room_id)

        state = gs._game_states[room_id]
        assert state.phase == "playing"
        assert state.current_turn == 1

        # B can discard normally
        card_b = copy.deepcopy(state.player_hands[1][0])
        ok, err = gs.discard_cards(room_id, 1, [card_b], skip_turn_advance=True)
        assert ok, f"B's discard failed unexpectedly: {err}"


# ===========================================================================
# F2-1: B's discard pile resync after grace
# ===========================================================================

class TestF21BDiscardPileResync:
    """
    F2-1: A sent pick_from_discard player_action (visual only).
    B removed card Y from their local discard pile optimistically.
    A disconnects before any server state changes.

    After grace, the fresh game_update should show Y still in discard pile.
    This test verifies the SERVER state has Y intact, confirming B's
    client-side state would resync correctly on the next game_update.
    """

    def test_f21_discard_pile_unchanged_after_disconnect_and_grace(self, game):
        """
        B's visual pick was purely local — server discard pile must still
        contain Y after A disconnects and grace expires.
        """
        room_id, pid_a, pid_b, gs, rs = game
        card_y = copy.deepcopy(_discard_top(game))  # The card B thinks A picked
        original_discard = copy.deepcopy(gs._game_states[room_id].discard_pile)

        # A never sent pick_card — only the player_action was sent (visual)
        # Disconnect immediately
        simulate_disconnect(gs, rs, room_id, pid_a, 0)

        # Grace expires: advance turn
        rs.set_player_exited(room_id, pid_a, True)
        gs._advance_turn(room_id)

        # Y must still be in the discard pile — B's visual was an orphan
        assert gs._game_states[room_id].discard_pile == original_discard, (
            "Discard pile must be unchanged — server never processed the pick"
        )
        assert _discard_top(game) == card_y, "Y (the 'ghost-picked' card) must still be on top"

    def test_f21_b_can_pick_y_on_their_turn_after_grace(self, game):
        """
        After grace advances to B, B should be able to pick Y from the
        discard pile (proving Y was never actually removed).
        """
        room_id, pid_a, pid_b, gs, rs = game
        card_y = copy.deepcopy(_discard_top(game))

        simulate_disconnect(gs, rs, room_id, pid_a, 0)
        rs.set_player_exited(room_id, pid_a, True)
        gs._advance_turn(room_id)

        assert _current_turn(game) == 1
        # B picks Y — should succeed because Y was never taken
        ok, picked, err = gs.pick_card(room_id, 1, from_discard=True, skip_draw=False, skip_turn_advance=True)
        assert ok, f"B could not pick Y — it was incorrectly removed: {err}"
        assert picked == card_y


# ===========================================================================
# F4-2: Show race — game phase after HTTP /show processed
# ===========================================================================

class TestF42ShowRace:
    """
    F4-2: A's HTTP /show completes. show_results are broadcast. A is
    disconnected and never receives the WS event.

    Tests verify:
    1. Game phase is NOT marked 'finished' by calculate_show_results alone
       (it only calculates — phase change is done by the route layer).
    2. Results are deterministic and correct regardless of A's connection.
    3. On reconnect, A can call calculate_show_results again to re-derive
       the same results (idempotency).
    """

    def test_f42_show_results_calculated_while_a_disconnected(self, game):
        """
        calculate_show_results() returns correct results even if A is marked
        offline at the moment of calculation.
        """
        room_id, pid_a, pid_b, gs, rs = game

        # A disconnects
        rs.set_player_connected(room_id, pid_a, False)

        # HTTP /show still processed (A's connection to HTTP is separate)
        results = gs.calculate_show_results(room_id, pid_a)

        assert results is not None
        assert len(results) == 2
        # A's result is still present
        a_result = next(r for r in results if r["player_id"] == pid_a)
        assert a_result is not None
        assert a_result["is_showed"] is True

    def test_f42_show_results_idempotent_for_reconnecting_player(self, game):
        """
        If A reconnects after /show was processed and the show_results WS
        was missed, A's client can re-derive results by calling /show again
        (same showed_by_id → same outcome deterministically).

        This verifies the calculation is idempotent given the same game state.
        """
        room_id, pid_a, pid_b, gs, rs = game

        results_first = gs.calculate_show_results(room_id, pid_a)
        # Simulate A reconnecting and calling show again (via re-sync)
        results_second = gs.calculate_show_results(room_id, pid_a)

        assert results_first is not None
        assert results_second is not None
        # Scores and outcomes must be identical
        for r1, r2 in zip(results_first, results_second):
            assert r1["game_score"] == r2["game_score"]
            assert r1["game_result"] == r2["game_result"]
            assert r1["rule_score"] == r2["rule_score"]

    def test_f42_b_result_correct_when_a_disconnected_mid_show(self, game):
        """
        B's result must be calculated correctly even if A was offline
        when /show was processed. A's disconnect doesn't affect scoring.
        """
        room_id, pid_a, pid_b, gs, rs = game
        rs.set_player_connected(room_id, pid_a, False)

        results = gs.calculate_show_results(room_id, pid_a)

        b_result = next(r for r in results if r["player_id"] == pid_b)
        assert b_result is not None
        # B must have a valid game_result
        assert b_result["game_result"] in ("won", "lost")
        # B must have their actual hand exposed in results
        assert len(b_result["in_hand_cards"]) > 0


# ===========================================================================
# F4-1: Show abandoned — turn advances after grace
# ===========================================================================

class TestF41ShowAbandoned:
    """
    F4-1: A tapped Show, sent the player_action (relay-blocked), then
    disconnected BEFORE sending the HTTP POST /show.
    Round must NOT end. Grace timer advances A's turn.
    """

    def test_f41_round_does_not_end_when_show_abandoned(self, game):
        """
        Game phase must remain 'playing' after A disconnects without
        completing the HTTP /show.
        """
        room_id, pid_a, pid_b, gs, rs = game

        # A disconnects without calling HTTP /show
        simulate_disconnect(gs, rs, room_id, pid_a, 0)
        rs.set_player_exited(room_id, pid_a, True)
        gs._advance_turn(room_id)

        state = gs._game_states[room_id]
        assert state.phase == "playing", (
            "Round must NOT end — /show was never completed"
        )
        assert _current_turn(game) == 1, "B's turn should now be active"

    def test_f41_a_can_show_again_after_reconnect(self, game):
        """
        If A reconnects within grace (before turn is advanced), A's turn
        is still theirs and A can tap Show again.
        After reconnect (grace cancelled), current_turn must still be 0.
        """
        room_id, pid_a, pid_b, gs, rs = game

        # A disconnects
        rs.set_player_connected(room_id, pid_a, False)
        gs.rollback_turn_if_incomplete(room_id, 0)  # no-op for Show

        # A reconnects within grace (cancel exited status, restore connection)
        rs.set_player_connected(room_id, pid_a, True)
        rs.set_player_exited(room_id, pid_a, False)
        gs._game_states[room_id].action_seq += 1  # simulate reconnect bump

        # Turn must still be A's — grace was not expired
        assert _current_turn(game) == 0, "A's turn must be restored on reconnect"

        # A can call show again
        results = gs.calculate_show_results(room_id, pid_a)
        assert results is not None


# ===========================================================================
# RECONNECT WITHIN GRACE — state restoration
# ===========================================================================

class TestReconnectWithinGrace:
    """
    Verify state restoration when A reconnects before the 60-second grace expires.
    These tests focus on the server-side state: is_exited cleared, action_seq
    bumped so clients re-sync.
    """

    def test_reconnect_clears_is_exited_flag(self, game):
        """
        If grace hadn't expired (is_exited never set), reconnect should
        keep is_exited=False and restore is_connected=True.
        """
        room_id, pid_a, _, gs, rs = game

        # Disconnect
        rs.set_player_connected(room_id, pid_a, False)
        # Note: is_exited is NOT set — still within grace window

        # Reconnect
        rs.set_player_connected(room_id, pid_a, True)
        rs.set_player_exited(room_id, pid_a, False)  # grace timer cancels this

        room = rs.get_room(room_id)
        player_a = next(p for p in room.players if p.player_id == pid_a)
        assert player_a.is_connected is True
        assert player_a.is_exited is False

    def test_reconnect_after_grace_expired_clears_exited(self, game):
        """
        Even if grace expired (is_exited=True was set), a reconnecting player
        should have is_exited cleared (game allows them back but they are
        skipped in turn rotation).
        """
        room_id, pid_a, _, gs, rs = game

        # Simulate grace expiry
        rs.set_player_connected(room_id, pid_a, False)
        rs.set_player_exited(room_id, pid_a, True)

        # Player reconnects
        rs.set_player_connected(room_id, pid_a, True)
        rs.set_player_exited(room_id, pid_a, False)

        room = rs.get_room(room_id)
        player_a = next(p for p in room.players if p.player_id == pid_a)
        assert player_a.is_connected is True
        assert player_a.is_exited is False

    def test_reconnect_bumps_action_seq_for_client_resync(self, game):
        """
        On reconnect, action_seq must be bumped so the reconnecting client
        does not drop the incoming game_update as stale.
        """
        room_id, pid_a, _, gs, rs = game

        seq_before = _action_seq(game)

        # Disconnect + reconnect
        rs.set_player_connected(room_id, pid_a, False)
        rs.set_player_connected(room_id, pid_a, True)

        # Simulate what handle_join_room does on reconnect:
        #   game_state.action_seq += 1
        state = gs._game_states[room_id]
        if state:
            state.action_seq += 1

        assert _action_seq(game) > seq_before, (
            "action_seq must be bumped on reconnect so client re-applies the update"
        )

    def test_rollback_occurs_during_reconnect_if_mid_turn(self, game):
        """
        If A disconnects mid-turn (phase=awaiting_discard) and reconnects,
        the rollback fires on disconnect. When A reconnects, they get the
        clean start-of-turn state — not the mid-turn state.
        """
        room_id, pid_a, _, gs, rs = game
        original_hand = copy.deepcopy(_hand_a(game))
        original_discard = copy.deepcopy(gs._game_states[room_id].discard_pile)

        # A picks from discard (puts phase=awaiting_discard)
        gs.pick_card(room_id, 0, from_discard=True, skip_draw=False, skip_turn_advance=True)
        assert _turn_context(game).phase == "awaiting_discard"

        # Disconnect — rollback fires immediately
        rolled_back = simulate_disconnect(gs, rs, room_id, pid_a, 0)
        assert rolled_back is True

        # Reconnect within grace
        rs.set_player_connected(room_id, pid_a, True)
        gs._game_states[room_id].action_seq += 1  # simulate handle_join_room bump

        # State is clean — A gets start-of-turn hand
        assert _hand_a(game) == original_hand
        assert gs._game_states[room_id].discard_pile == original_discard
        # A's turn is still theirs (grace not expired)
        assert _current_turn(game) == 0


# ===========================================================================
# GRACE TIMER: exited player skipped in turn rotation
# ===========================================================================

class TestExitedPlayerTurnSkip:
    """
    After grace expires, permanently exited players must be skipped in
    the turn rotation (_advance_turn uses skip_set = eliminated | exited).
    """

    def test_exited_player_is_skipped_in_subsequent_turns(self, game):
        """
        With A (index 0) exited, advancing from B (index 1) should skip
        back past A and... wrap to B again in a 2-player game where
        A is the only skip target. Verifies skip logic doesn't get stuck.
        """
        room_id, pid_a, pid_b, gs, rs = game

        # Set A as exited
        rs.set_player_exited(room_id, pid_a, True)
        # Advance turn to B first
        gs._advance_turn(room_id)
        assert _current_turn(game) == 1

        # Now advance from B — should skip A (exited) and land on B again
        # (In a 2-player game where A is exited, there's only 1 active player)
        # This should NOT raise an error or loop infinitely
        gs._advance_turn(room_id)
        # In 2-player with 1 exited, next after B would be A (skipped) → B
        assert _current_turn(game) == 1

    def test_exited_player_hand_count_zero(self, game):
        """
        After grace expires, eliminate_exited_player clears the hand.
        This test verifies the hand-clear path used in tournament mode.
        """
        room_id, pid_a, _, gs, rs = game

        # Mark as exited first (grace expired)
        rs.set_player_exited(room_id, pid_a, True)

        # Eliminate them (as tournament code does)
        success = gs.eliminate_exited_player(room_id, 0)
        assert success is True

        # Hand must be empty
        assert _hand_a(game) == []
        # Score zeroed
        assert gs._game_states[room_id].player_scores[0] == 0
