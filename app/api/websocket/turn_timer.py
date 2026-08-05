"""
Turn timer — server-side per-room task that fires when a player exceeds
TURN_TIMEOUT_SECONDS without acting. Triggers:
  1. rollback_turn_if_incomplete  (revert partial move)
  2. decrement_life               (-1 heart; 0 hearts → eliminated)
  3. server_play_for_player       (bot plays full turn on clean state)
  4. broadcast_game_update        (all clients see new state)
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict

from app.config import TURN_TIMEOUT_SECONDS
from app.utils.lock import get_room_lock
from app.utils.room_logger import get_room_logger

# One running task per room_id
_turn_timer_tasks: Dict[str, asyncio.Task] = {}


def start_turn_timer(room_id: str) -> None:
    """Cancel any existing timer for the room and start a fresh one."""
    stop_turn_timer(room_id)
    task = asyncio.create_task(_turn_timer_loop(room_id))
    _turn_timer_tasks[room_id] = task
    get_room_logger(room_id).info("turn_timer_started", {"room_id": room_id})


def stop_turn_timer(room_id: str) -> None:
    """Cancel the running timer task for the room (if any)."""
    task = _turn_timer_tasks.pop(room_id, None)
    if task and not task.done():
        try:
            current = asyncio.current_task()
            if current != task:
                task.cancel()
        except RuntimeError:
            task.cancel()
        get_room_logger(room_id).info("turn_timer_stopped", {"room_id": room_id})


async def _turn_timer_loop(room_id: str) -> None:
    """Wait until the current turn's deadline, then handle the timeout."""
    log = get_room_logger(room_id)
    try:
        from app.services.game_service import game_service
        from app.api.websocket.game_ws import broadcast_game_update
        from app.api.websocket.connection_manager import manager
        from app.services.room_service import room_service

        # Poll until turn_started_at is set or game ends
        while True:
            await asyncio.sleep(1)

            gs = await game_service.get_game_state(room_id)
            if gs is None or gs.phase != "playing":
                log.info("turn_timer_game_ended", {"room_id": room_id})
                return

            if gs.turn_started_at is None:
                continue

            elapsed = (datetime.now(timezone.utc) - gs.turn_started_at).total_seconds()
            timeout = gs.turn_timeout_seconds or TURN_TIMEOUT_SECONDS

            if elapsed < timeout:
                # Sleep the remaining time (minus a small buffer to avoid busy-loop)
                remaining = timeout - elapsed
                await asyncio.sleep(max(0.0, remaining - 0.5))
                continue

            # ── Timer expired ─────────────────────────────────────────────────
            player_index = gs.current_turn
            log.info("turn_timer_expired", {
                "room_id": room_id,
                "player_index": player_index,
                "elapsed_seconds": elapsed,
            })

            # All state mutations happen inside the room lock.
            # Broadcast and timer restart happen OUTSIDE the lock so that
            # send_personal_message calls don't block under the lock.
            should_broadcast = False
            
            # Phase 1: Notify clients that the turn has expired
            async with get_room_lock(room_id):
                gs_fresh = await game_service.get_game_state(room_id)
                if gs_fresh is None or gs_fresh.phase != "playing":
                    return
                if gs_fresh.current_turn != player_index:
                    log.info("turn_timer_stale_turn_already_advanced", {"room_id": room_id})
                    return
                if gs_fresh.turn_started_at:
                    elapsed2 = (datetime.now(timezone.utc) - gs_fresh.turn_started_at).total_seconds()
                    if elapsed2 < timeout:
                        # Turn was reset between our checks; go back to polling
                        continue

                # Notify all clients to disable the timed-out player's turn UI
                # immediately (before server plays) so they see the slot is locked.
                await manager.broadcast_to_room({
                    "type": "turn_disabled",
                    "data": {
                        "player_index": player_index,
                    },
                }, room_id)
                log.info("turn_disabled_broadcast", {"player_index": player_index})

            # GAP: Release the lock and wait 1.5 seconds. 
            # This gives in-flight requests (like a user clicking just before receiving turn_disabled)
            # time to reach the server, acquire the lock, and process.
            await asyncio.sleep(1.5)

            # Phase 2: Server bot takes action if the user didn't play during the gap
            async with get_room_lock(room_id):
                # Re-check: did the user play during the gap?
                gs_fresh = await game_service.get_game_state(room_id)
                if gs_fresh is None or gs_fresh.phase != "playing":
                    return
                if gs_fresh.current_turn != player_index:
                    log.info("turn_timer_stale_turn_already_advanced_after_gap", {"room_id": room_id})
                    return
                # Check if the user initiated an action (like dropping a card) but hasn't finished the turn
                if gs_fresh.turn_context and gs_fresh.turn_context.actions:
                    log.info("turn_timer_user_played_during_gap", {"room_id": room_id})
                    # Give them more time to finish their compound action, restart timer
                    start_turn_timer(room_id)
                    return

                # 1. Rollback any partial move (although we just checked there shouldn't be any, safe to call)
                await game_service.rollback_turn_if_incomplete(room_id, player_index)

                # 2. Decrement life; exit if lives hit 0
                lives_left, is_now_exited = await game_service.mark_player_exited_by_timer(room_id, player_index)

                # 3. Notify all clients of the life change (small payload, safe inside lock)
                await manager.broadcast_to_room({
                    "type": "life_update",
                    "data": {
                        "player_index": player_index,
                        "lives_left": lives_left,
                        "is_bot_play": True,
                        "is_exited": is_now_exited,
                    },
                }, room_id)
                log.info("life_update_broadcast", {
                    "player_index": player_index,
                    "lives_left": lives_left,
                    "is_now_exited": is_now_exited,
                })

                if is_now_exited:
                    # Broadcast dedicated player_exited event so all clients
                    # immediately mark the opponent's avatar as exited.
                    room_for_exit = await room_service.get_room(room_id)
                    exit_player_id = None
                    if room_for_exit and player_index < len(room_for_exit.players):
                        exit_player_id = room_for_exit.players[player_index].player_id

                    await manager.broadcast_to_room({
                        "type": "player_exited",
                        "data": {
                            "player_index": player_index,
                            "player_id": exit_player_id,
                        },
                    }, room_id)

                    # Check active players AFTER set_player_exited is committed
                    active_players = await game_service.get_active_players(room_id)
                    if len(active_players) == 1:
                        survivor_index = active_players[0]
                        room = await room_service.get_room(room_id)
                        survivor_id = room.players[survivor_index].player_id if room and survivor_index < len(room.players) else None

                        if survivor_id:
                            log.info("winner_by_survival", {"room_id": room_id, "survivor_id": survivor_id})
                            results = await game_service.calculate_show_results(room_id, survivor_id, is_survival_win=True)
                            if results:
                                from app.api.routes.games import process_and_broadcast_show_results
                                await process_and_broadcast_show_results(room_id, survivor_id, results, is_survival_win=True)
                                
                                await broadcast_game_update(room_id)
                                return

                    # More than 1 active player — advance turn, skip bot play
                    await game_service._advance_turn(room_id)
                    log.info("turn_timer_player_exited_turn_advanced", {"player_index": player_index})
                    should_broadcast = True
                else:
                    # 4. Server plays the full turn (pick + discard) on the clean hand
                    played = await game_service.server_play_for_player(room_id, player_index)
                    log.info("server_play_result", {
                        "player_index": player_index,
                        "played": played,
                    })
                    if not played:
                        # server_play already advances the turn on failure — nothing more needed
                        pass
                    should_broadcast = True

            # 5. Broadcast updated game state to all players (outside lock)
            if should_broadcast:
                await broadcast_game_update(room_id)

            # 6. Restart timer for the new turn player (broadcast_game_update also
            #    does this, but calling it explicitly ensures it runs even if
            #    broadcast_game_update skips it due to game ending).
            start_turn_timer(room_id)
            return  # This task is done; the new task handles the next turn

    except asyncio.CancelledError:
        pass  # Normal — turn advanced legitimately or game ended
    except Exception as exc:
        get_room_logger(room_id).error("turn_timer_error", {"error": str(exc)}, exc_info=True)
