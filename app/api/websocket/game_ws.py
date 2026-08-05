"""
WebSocket handlers for game events
"""
from fastapi import WebSocket
from typing import Dict
from app.api.websocket.connection_manager import manager
from app.utils.lock import with_room_lock
from app.services.game_service import game_service
from app.services.room_service import room_service
from app.schemas.room import RoomStatus
from app.utils.deck_utils import dict_to_card, calculate_hand_score
from app.utils.room_logger import get_room_logger
from app.utils.debug_log import log_to_file
from app.services.online_game_stats_service import online_game_stats_service
from app.database import AsyncSessionLocal
from app.api.websocket.turn_timer import start_turn_timer, stop_turn_timer

# Stores the DB game_idn for each active online room so it can be used
# at round-increment and game-finalization time.
# Also stores the game started_at time for duration calculation.
_online_game_ids: dict[str, int] = {}        # room_id -> game_idn
_online_game_started_at: dict[str, object] = {}  # room_id -> datetime


async def start_game_for_room(room_id: str, eliminated_indices: list[int] | None = None, initial_turn_override: int | None = None) -> bool:
    """
    Start game for a room if all conditions are met.
    Returns True if game was started, False otherwise.
    """
    log = get_room_logger(room_id)
    room = await room_service.get_room(room_id)
    if not room:
        log.error("start_game_failed", {"reason": "room_not_found"})
        return False

    if room.status == RoomStatus.PLAYING:
        log.warn("start_game_skipped", {"reason": "already_playing"})
        return False

    if len(room.players) < 2:
        log.warn("start_game_failed", {
            "reason": "not_enough_players",
            "player_count": len(room.players),
        })
        return False

    active_players = []
    elim_set = set(eliminated_indices) if eliminated_indices else set()
    for idx, p in enumerate(room.players):
        if not getattr(p, "is_exited", False) and idx not in elim_set:
            active_players.append(p)

    all_ready = all(player.is_ready for player in active_players)
    if not all_ready:
        not_ready = [p.player_name for p in active_players if not p.is_ready]
        log.warn("start_game_failed", {
            "reason": "players_not_ready",
            "not_ready_players": not_ready,
        })
        return False

    log.info("game_initializing", {
        "player_count": len(room.players),
        "eliminated_indices": eliminated_indices or [],
        "initial_turn_override": initial_turn_override,
    })
    game_state = await game_service.initialize_game(room_id, eliminated_indices=eliminated_indices, initial_turn_override=initial_turn_override)
    if not game_state:
        log.error("game_init_failed", {"reason": "initialize_game_returned_none"})
        return False

    # For tournament rooms: record who starts this round
    try:
        from app.api.routes.games import _tournament_scores
        room_for_tournament = await room_service.get_room(room_id)
        if room_for_tournament and getattr(room_for_tournament, "game_mode", None) == "Tournament":
            if room_id not in _tournament_scores:
                _tournament_scores[room_id] = {
                    "player_ids": [p.player_id for p in room_for_tournament.players],
                    "player_names": [p.player_name for p in room_for_tournament.players],
                    "rounds": [],
                    "eliminated": [],
                    "elimination_rounds": {},
                    "last_starting_player_index": None,
                }
            if _tournament_scores[room_id].get("last_starting_player_index") is None:
                _tournament_scores[room_id]["last_starting_player_index"] = game_state.initial_turn
                log.info("tournament_initial_turn_recorded", {"initial_turn": game_state.initial_turn})
    except Exception as e:
        log.error("tournament_initial_turn_record_failed", {"error": str(e)})

    await room_service.reset_all_players_ready(room_id)

    updated_room = await room_service.get_room(room_id)
    if not updated_room:
        log.error("start_game_failed", {"reason": "room_not_found_after_init"})
        return False

    player_id_to_index = {player.player_id: idx for idx, player in enumerate(updated_room.players)}

    connections = list(manager.get_room_connections(room_id))
    log.info("game_start_sending", {
        "connection_count": len(connections),
        "player_count": len(updated_room.players),
        "current_turn": game_state.current_turn,
        "phase": game_state.phase,
    })

    for idx, player in enumerate(updated_room.players):
        player_state = await game_service.get_player_state(room_id, idx, updated_room.players, game_state)

        if player_state:
            await manager.send_personal_message_cross_node(room_id, player.player_id, {
                "type": "game_start",
                "data": {
                    "player_state": player_state,
                    "seq": game_state.action_seq,
                }
            })
            log.info("game_start_sent_cross_node", {
                "player_index": idx,
                "player_id": player.player_id,
                "hand_size": len(player_state.get("in_hand_cards", [])),
                "is_playing": player_state.get("is_playing"),
                "seq": game_state.action_seq,
            })
        else:
            log.warn("game_start_player_state_missing", {"player_index": idx})

    room_data = updated_room.model_dump(mode='json')
    await manager.broadcast_to_room({"type": "room_update", "data": room_data}, room_id)
    log.info("game_start_room_update_broadcast", {"room_status": room_data.get("status")})

    # ── Persist game start to the database (fire-and-forget, non-blocking) ──
    try:
        already_tracked = room_id in _online_game_ids

        if not already_tracked:
            firebase_uids = [p.player_id for p in updated_room.players]
            async with AsyncSessionLocal() as db:
                uid_to_idn = await online_game_stats_service.resolve_user_idns(db, firebase_uids)

            player_user_idns = [
                (uid_to_idn.get(p.player_id), idx)
                for idx, p in enumerate(updated_room.players)
            ]

            creator_idn: int | None = None
            for p in updated_room.players:
                if getattr(p, "is_admin", False):
                    creator_idn = uid_to_idn.get(p.player_id)
                    break

            game_mode = getattr(updated_room, "game_mode", None) or "Single Game"
            score_limit = getattr(updated_room, "score_limit", None)

            async with AsyncSessionLocal() as db:
                game_idn = await online_game_stats_service.create_online_game(
                    db,
                    game_mode=game_mode,
                    total_players=len(updated_room.players),
                    score_limit=score_limit,
                    created_user_idn=creator_idn,
                    player_user_idns=player_user_idns,
                )

            if game_idn is not None:
                from datetime import datetime, timezone
                _online_game_ids[room_id] = game_idn
                _online_game_started_at[room_id] = datetime.now(timezone.utc)
                log.info("online_game_db_created", {"game_idn": game_idn})
            else:
                log.error("online_game_db_create_failed", {})
        else:
            game_idn = _online_game_ids[room_id]
            async with AsyncSessionLocal() as db:
                await online_game_stats_service.increment_round_count(db, game_idn)
            log.info("online_game_round_incremented", {"game_idn": game_idn})
    except Exception as exc:
        log.error("online_game_db_start_error", {"error": str(exc)})

    # Launch the per-room turn timer so the first player's countdown begins.
    start_turn_timer(room_id)
    log.info("turn_timer_launched", {"room_id": room_id})

    return True


@with_room_lock
async def handle_game_start(websocket: WebSocket, room_id: str, data: Dict):
    """Handle game start request (manual trigger)"""
    log = get_room_logger(room_id)
    sender_player_id = data.get("player_id", "")
    if sender_player_id:
        if not manager.get_player_id(websocket):
            manager.connection_players[websocket] = sender_player_id

    log.info("ws_game_start_requested", {"sender_player_id": sender_player_id})

    success = await start_game_for_room(room_id)

    if not success:
        room = await room_service.get_room(room_id)
        if not room:
            await manager.send_personal_message({"type": "error", "message": "Room not found"}, websocket)
        elif len(room.players) < 2:
            await manager.send_personal_message({"type": "error", "message": "Need at least 2 players to start"}, websocket)
        elif not all(player.is_ready for player in room.players):
            await manager.send_personal_message({"type": "error", "message": "All players must be ready to start"}, websocket)
        else:
            await manager.send_personal_message({"type": "error", "message": "Failed to initialize game"}, websocket)


@with_room_lock
async def handle_discard_cards(websocket: WebSocket, room_id: str, data: Dict):
    """Handle discard cards action"""
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)
    room = await room_service.get_room(room_id)
    if not room:
        await manager.send_personal_message({"type": "error", "message": "Room not found"}, websocket)
        return

    player_index = None
    for idx, player in enumerate(room.players):
        if player.player_id == player_id:
            player_index = idx
            break

    if player_index is None:
        await manager.send_personal_message({"type": "error", "message": "Player not found in room"}, websocket)
        return

    cards_to_discard = data.get("cards", [])
    if not cards_to_discard:
        await manager.send_personal_message({"type": "error", "message": "No cards specified to discard"}, websocket)
        return

    skip_turn_advance = data.get("skip_turn_advance", False)

    log.info("ws_discard_cards_received", {
        "player_index": player_index,
        "player_id": player_id,
        "cards_count": len(cards_to_discard),
        "cards": cards_to_discard,
        "skip_turn_advance": skip_turn_advance,
    })

    success, error_msg = await game_service.discard_cards(room_id, player_index, cards_to_discard, skip_turn_advance)

    if not success:
        await manager.send_personal_message({"type": "error", "message": error_msg}, websocket)
        return

    if not skip_turn_advance:
        await broadcast_game_update(room_id)
    else:
        await push_game_state_to_player_by_index(room_id, player_index, room, step_ack=True)


@with_room_lock
async def handle_pick_card(websocket: WebSocket, room_id: str, data: Dict):
    """Handle pick card action"""
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)
    room = await room_service.get_room(room_id)
    if not room:
        await manager.send_personal_message({"type": "error", "message": "Room not found"}, websocket)
        return

    player_index = None
    for idx, player in enumerate(room.players):
        if player.player_id == player_id:
            player_index = idx
            break

    if player_index is None:
        await manager.send_personal_message({"type": "error", "message": "Player not found in room"}, websocket)
        return

    from_discard = data.get("from_discard", False)
    skip_draw = data.get("skip_draw", False)
    skip_turn_advance = data.get("skip_turn_advance", False)

    log.info("ws_pick_card_received", {
        "player_index": player_index,
        "player_id": player_id,
        "from_discard": from_discard,
        "skip_draw": skip_draw,
        "skip_turn_advance": skip_turn_advance,
    })

    success, picked_card, error_msg = await game_service.pick_card(room_id, player_index, from_discard, skip_draw, skip_turn_advance)

    if not success:
        await manager.send_personal_message({"type": "error", "message": error_msg}, websocket)
        return

    if not skip_turn_advance or skip_draw:
        await broadcast_game_update(room_id, picked_card=picked_card)
    else:
        await push_game_state_to_player_by_index(room_id, player_index, room, step_ack=True)


@with_room_lock
async def handle_declare(websocket: WebSocket, room_id: str, data: Dict):
    """Handle player declaration"""
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)
    room = await room_service.get_room(room_id)
    if not room:
        await manager.send_personal_message({"type": "error", "message": "Room not found"}, websocket)
        return

    player_index = None
    for idx, player in enumerate(room.players):
        if player.player_id == player_id:
            player_index = idx
            break

    if player_index is None:
        await manager.send_personal_message({"type": "error", "message": "Player not found in room"}, websocket)
        return

    log.info("ws_declare_received", {
        "player_index": player_index,
        "player_id": player_id,
        "player_name": room.players[player_index].player_name,
    })

    success, message, is_wrong = await game_service.declare(room_id, player_index)

    if not success:
        await manager.send_personal_message({"type": "error", "message": message}, websocket)
        return

    await manager.broadcast_to_room({
        "type": "declaration",
        "data": {
            "player_index": player_index,
            "player_name": room.players[player_index].player_name,
            "message": message,
            "is_wrong": is_wrong,
        }
    }, room_id)

    if not is_wrong:
        await game_service.end_game(room_id, player_index)
        game_state = await game_service.get_game_state(room_id)

        final_scores = []
        for idx in range(len(room.players)):
            hand = await game_service.get_player_hand(room_id, idx)
            if hand:
                cards = [dict_to_card(card_dict) for card_dict in hand]
                final_scores.append({
                    "player_index": idx,
                    "player_name": room.players[idx].player_name,
                    "score": calculate_hand_score(cards, None),
                })

        await broadcast_game_update(room_id)
        await manager.broadcast_to_room({
            "type": "game_end",
            "data": {
                "winner_index": game_state.winner,
                "winner_name": room.players[game_state.winner].player_name if game_state.winner is not None else None,
                "final_scores": final_scores,
            }
        }, room_id)
        log.info("ws_game_end_broadcast", {
            "winner_index": game_state.winner,
            "final_scores": final_scores,
        })
    else:
        # Wrong declaration - continue game with phase reset
        game_state = await game_service.get_game_state(room_id)
        if game_state:
            game_state.phase = "playing"
            game_state.declared_player = None
            # Persist the phase reset
            await game_service._save(room_id, game_state)
        await broadcast_game_update(room_id)


@with_room_lock
async def handle_show(websocket: WebSocket, room_id: str, data: Dict):
    """Handle player show action"""
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)
    room = await room_service.get_room(room_id)
    if not room:
        await manager.send_personal_message({"type": "error", "message": "Room not found"}, websocket)
        return

    player_index = None
    for idx, player in enumerate(room.players):
        if player.player_id == player_id:
            player_index = idx
            break

    if player_index is None:
        await manager.send_personal_message({"type": "error", "message": "Player not found in room"}, websocket)
        return

    log.info("ws_show_received", {
        "player_index": player_index,
        "player_id": player_id,
        "player_name": room.players[player_index].player_name,
    })

    # We delegate the actual calculation and broadcast to the REST API /show endpoint.
    # The REST API properly calculates tournament scores and broadcasts `show_results`.
    log.info("ws_show_ignored_delegated_to_rest", {
        "player_id": player_id,
        "player_index": player_index,
    })


@with_room_lock
async def handle_request_state(websocket: WebSocket, room_id: str, data: Dict):
    """Handle a client explicitly requesting a fresh copy of its own player_state."""
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)
    from app.utils.debug_log import log_to_file
    log_to_file(f"handle_request_state started for {player_id}")
    log.info("request_state_received", {"player_id": player_id})
    room = await room_service.get_room(room_id)

    if not room:
        log_to_file("handle_request_state: Room not found")
        await manager.send_personal_message({"type": "error", "message": "Room not found"}, websocket)
        return

    player_index = None
    for idx, player in enumerate(room.players):
        if player.player_id == player_id:
            player_index = idx
            break

    if player_index is None:
        log_to_file(f"handle_request_state: player_index is None for {player_id}")
        log.info("request_state_player_not_in_game_yet", {"player_id": player_id})
        await manager.send_personal_message({"type": "game_update", "data": {"error": "player_index_none"}}, websocket)
        return

    game_state = await game_service.get_game_state(room_id)
    if not game_state:
        log_to_file(f"handle_request_state: game_state is None for {player_id}")
        log.info("request_state_no_active_game", {
            "player_index": player_index,
            "player_id": player_id,
        })
        await manager.send_personal_message({"type": "game_update", "data": {"error": "game_state_none"}}, websocket)
        return

    try:
        player_state = await game_service.get_player_state(room_id, player_index, room.players, game_state)
        if not player_state:
            log_to_file(f"handle_request_state: Failed to build player state for {player_id}")
            await manager.send_personal_message({"type": "error", "message": "Failed to build player state"}, websocket)
            return

        log_to_file(f"handle_request_state: sending game_update to {player_id}")

        await manager.send_personal_message(
            {
                "type": "game_update",
                "data": {
                    "player_state": player_state,
                    "seq": game_state.action_seq,
                },
            },
            websocket,
        )
    except Exception as e:
        import traceback
        log.error("request_state_failed", {"error": str(e), "trace": traceback.format_exc()})
        await manager.send_personal_message({"type": "game_update", "data": {"error": f"Request failed: {str(e)}"}}, websocket)
        return
    log.info("request_state_served", {
        "player_index": player_index,
        "player_id": player_id,
        "hand_size": len(player_state.get("in_hand_cards", [])),
        "is_playing": player_state.get("is_playing"),
    })


async def push_game_state_to_player(websocket: WebSocket, room_id: str, player_id: str) -> bool:
    """Push the current game_update to a single player's websocket."""
    log = get_room_logger(room_id)
    room = await room_service.get_room(room_id)
    if not room:
        return False

    game_state = await game_service.get_game_state(room_id)
    if not game_state:
        return False

    player_index = None
    for idx, player in enumerate(room.players):
        if player.player_id == player_id:
            player_index = idx
            break

    if player_index is None:
        return False

    player_state = await game_service.get_player_state(room_id, player_index, room.players, game_state)
    if not player_state:
        return False

    await manager.send_personal_message(
        {
            "type": "game_update",
            "data": {
                "player_state": player_state,
                "seq": game_state.action_seq,
            },
        },
        websocket,
    )
    log.info("reconnect_game_state_pushed", {
        "player_index": player_index,
        "player_id": player_id,
        "hand_size": len(player_state.get("in_hand_cards", [])),
        "is_playing": player_state.get("is_playing"),
    })
    return True


async def push_game_state_to_player_by_index(room_id: str, player_index: int, room, step_ack: bool = False) -> bool:
    """Send a game_update only to the player at player_index."""
    log = get_room_logger(room_id)
    game_state = await game_service.get_game_state(room_id)
    if not game_state:
        return False

    player_state = await game_service.get_player_state(room_id, player_index, room.players, game_state)
    if not player_state:
        return False

    target_player_id = room.players[player_index].player_id if player_index < len(room.players) else None
    if not target_player_id:
        return False

    payload: dict = {
        "player_state": player_state,
        "seq": game_state.action_seq,
    }
    if step_ack:
        payload["step_ack"] = True
        
    await manager.send_personal_message_cross_node(room_id, target_player_id, {"type": "game_update", "data": payload})
    log.info("intermediate_game_state_pushed_to_acting_player", {
        "player_index": player_index,
        "player_id": target_player_id,
        "hand_size": len(player_state.get("in_hand_cards", [])),
        "is_playing": player_state.get("is_playing"),
        "step_ack": step_ack,
    })
    return True


@with_room_lock
async def handle_exit_game(websocket: WebSocket, room_id: str, data: Dict):
    """Handle explicit mid-game exit by player"""
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)
    if not player_id:
        return

    room = await room_service.get_room(room_id)
    if not room:
        return

    player_index = next((i for i, p in enumerate(room.players) if p.player_id == player_id), None)
    if player_index is None:
        return

    # Set player exited
    await room_service.set_player_exited(room_id, player_id, True)

    # Clean up turn context if it's their turn
    gs = await game_service.get_game_state(room_id)
    if gs and gs.current_turn == player_index:
        if gs.turn_context and gs.turn_context.player_index == player_index:
            gs.turn_context = None
        await game_service._save(room_id, gs)

    # Broadcast player_exited
    await manager.broadcast_to_room({
        "type": "player_exited",
        "data": {
            "player_index": player_index,
            "player_id": player_id,
        },
    }, room_id)

    # Check for survival win
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

    # If game isn't over and it was their turn, advance turn
    if gs and gs.current_turn == player_index:
        await game_service._advance_turn(room_id)
        await broadcast_game_update(room_id)
        start_turn_timer(room_id)
    else:
        # Just broadcast game update so everyone sees them marked exited
        await broadcast_game_update(room_id)


async def broadcast_game_update(room_id: str, picked_card: Dict = None):
    """Broadcast game state update to all players in room"""
    log = get_room_logger(room_id)
    room = await room_service.get_room(room_id)
    if not room:
        return

    game_state = await game_service.get_game_state(room_id)
    connections = list(manager.get_room_connections(room_id))

    log.info("game_update_broadcasting", {
        "current_turn": game_state.current_turn if game_state else None,
        "phase": game_state.phase if game_state else None,
        "player_count": len(room.players),
        "has_picked_card": picked_card is not None,
    })

    for idx, player in enumerate(room.players):
        player_state = await game_service.get_player_state(room_id, idx, room.players, game_state)

        if player_state:
            update_data = {
                "type": "game_update",
                "data": {
                    "player_state": player_state,
                    "seq": game_state.action_seq if game_state else 0,
                }
            }

            if picked_card:
                update_data["data"]["picked_card"] = picked_card

            log_to_file(f"broadcast_game_update: sending to {player.player_id}")
            await manager.send_personal_message_cross_node(room_id, player.player_id, update_data)
        else:
            log.warn("game_update_player_state_missing", {
                "player_index": idx,
                "player_id": player.player_id,
            })

    # Restart the turn timer for the new turn player (unless the game ended)
    if game_state and game_state.phase == "playing":
        start_turn_timer(room_id)
    else:
        stop_turn_timer(room_id)
