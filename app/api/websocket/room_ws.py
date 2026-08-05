"""
WebSocket handlers for room management
"""
from datetime import datetime, timezone
from fastapi import WebSocket, WebSocketDisconnect, HTTPException
from typing import Dict
import asyncio
from app.api.websocket.connection_manager import manager
from app.utils.lock import with_room_lock, get_room_lock
from app.services.room_service import room_service
from app.services.game_service import game_service
from app.schemas.room import RoomStatus
from app.api.websocket import game_ws
from app.utils.room_logger import get_room_logger, global_log
from app.utils.firebase_auth import verify_firebase_token
from app.utils.debug_log import log_to_file

# Track running connection status check tasks per room
_connection_status_tasks: Dict[str, asyncio.Task] = {}

# Per-connection last-ping timestamp for heartbeat watchdog.
_last_ping: Dict[int, datetime] = {}  # key = id(websocket)

# Per-connection heartbeat watchdog tasks.
_heartbeat_watchdogs: Dict[int, asyncio.Task] = {}  # key = id(websocket)

# Empty room deletion timers
_empty_room_timers: Dict[str, asyncio.Task] = {}

async def _empty_room_timeout(room_id: str):
    """Wait 5 minutes, then delete the room if still empty."""
    try:
        await asyncio.sleep(300)  # 5 minutes
        async with get_room_lock(room_id):
            active_connections = manager.get_room_connections(room_id)
            if len(active_connections) == 0:
                get_room_logger(room_id).info("room_empty_timeout_deleting", {"room_id": room_id})
                if room_id in _connection_status_tasks:
                    _connection_status_tasks[room_id].cancel()
                    _connection_status_tasks.pop(room_id, None)
                from app.services.room_service import room_service
                await room_service.delete_room(room_id)
    except asyncio.CancelledError:
        pass

# Maximum seconds allowed between pings before the connection is declared dead.
# Flutter client sends a ping every 30 s; allow 2 missed pings + 10 s buffer → 70 s.
_PING_TIMEOUT_SECONDS = 70


async def _broadcast_room_update_on_disconnect(room_id: str):
    """Helper function to broadcast room update when player disconnects"""
    try:
        room = await room_service.get_room(room_id)
        if room:
            active_connections = manager.get_room_connections(room_id)
            if len(active_connections) > 0:
                await manager.broadcast_to_room({
                    "type": "room_update",
                    "data": room.model_dump(mode='json')
                }, room_id)
                get_room_logger(room_id).info("room_update_broadcast_on_disconnect", {
                    "active_connections": len(active_connections),
                })
    except Exception as e:
        get_room_logger(room_id).error("room_update_broadcast_on_disconnect_failed", {
            "error": str(e),
        }, exc_info=True)


@with_room_lock
async def on_player_disconnect(room_id: str, player_id: str, websocket=None):
    """Callback when a player disconnects from connection manager."""
    if player_id:
        # Check if the player has any OTHER active connections (e.g. they just reconnected)
        active_connections = manager.get_room_connections(room_id)
        player_still_connected = any(
            manager.connection_players.get(ws) == player_id
            for ws in active_connections
        )
        
        if player_still_connected:
            get_room_logger(room_id).info("player_disconnect_ignored_active_connection_exists", {
                "player_id": player_id,
            })
            return

        await room_service.set_player_connected(room_id, player_id, False)
        # Record disconnect timestamp for countdown badge
        await room_service.set_player_disconnected_at(
            room_id, player_id, datetime.now(timezone.utc)
        )

        if not await room_service.has_connected_players(room_id):
            get_room_logger(room_id).info("room_all_players_disconnected", {
                "player_id": player_id,
            })

        # Roll back mid-turn state if player disconnected after drawing but before discarding
        room = await room_service.get_room(room_id)
        reverted_intermediate_state = False
        turn_advanced_on_disconnect = False
        if room and room.status == RoomStatus.PLAYING:
            player_index = next((i for i, p in enumerate(room.players) if p.player_id == player_id), -1)
            if player_index >= 0:
                reverted_intermediate_state = await game_service.rollback_turn_if_incomplete(room_id, player_index)
                if reverted_intermediate_state:
                    get_room_logger(room_id).info("reverted_intermediate_state_for_disconnected_player")
                else:
                    # No rollback (either nothing to roll back, or compound action was fully
                    # committed). Check if this player is still current_turn with no active
                    # TurnContext — that means all compound-action steps completed on the
                    # backend but the final pick_card(skip_draw=True) was never sent
                    # (disconnect happened between step_ack and the turn-advance message).
                    # In this case advance the turn immediately instead of blocking for 60 s.
                    game_state = await game_service.get_game_state(room_id)
                    if (game_state is not None
                            and game_state.current_turn == player_index
                            and game_state.turn_context is None):
                        get_room_logger(room_id).info(
                            "disconnect_turn_context_none_advancing_turn_immediately",
                            {"player_id": player_id, "player_index": player_index},
                        )
                        await game_service._advance_turn(room_id)
                        turn_advanced_on_disconnect = True


        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                async def _ordered_disconnect_broadcast(r_id: str, is_playing: bool):
                    """Send room_update first, then game_update so clients always
                    receive is_connected=false before processing the new game state."""
                    await _broadcast_room_update_on_disconnect(r_id)
                    if is_playing:
                        await game_ws.broadcast_game_update(r_id)

                is_game_active = room is not None and room.status == RoomStatus.PLAYING
                asyncio.create_task(_ordered_disconnect_broadcast(room_id, is_game_active))
                # No grace timer — the turn timer handles idle/offline players.
            else:
                loop.run_until_complete(_broadcast_room_update_on_disconnect(room_id))
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                asyncio.create_task(_broadcast_room_update_on_disconnect(room_id))
            except Exception as e:
                get_room_logger(room_id).error("disconnect_broadcast_schedule_failed", {
                    "player_id": player_id,
                    "error": str(e),
                })


# Set disconnect callback
manager.set_disconnect_callback(on_player_disconnect)


async def check_connection_health(websocket: WebSocket, room_id: str, player_id: str):
    """Background task to periodically check if connection is alive"""
    while True:
        try:
            await asyncio.sleep(8)  # Check every 8 seconds (reduced from 30s)
            try:
                await websocket.send_json({"type": "connection_test"})
            except Exception as e:
                get_room_logger(room_id).warn("health_check_failed", {
                    "player_id": player_id,
                    "error": str(e),
                })
                await handle_leave_room(websocket, room_id, silent=True)
                break
        except asyncio.CancelledError:
            break
        except Exception as e:
            get_room_logger(room_id).error("health_check_error", {
                "player_id": player_id,
                "error": str(e),
            })
            await handle_leave_room(websocket, room_id, silent=True)
            break


async def _heartbeat_watchdog(websocket: WebSocket, room_id: str, player_id: str):
    """Per-connection watchdog: if no ping received within _PING_TIMEOUT_SECONDS,
    treat the connection as dead and trigger disconnect handling.

    The Flutter client sends {type: ping} every 30 seconds. If the client's
    internet drops (unclean disconnect), it stops sending pings. This watchdog
    detects the silence after _PING_TIMEOUT_SECONDS (70 s = 2 missed pings +
    10 s buffer) and immediately marks the player offline + broadcasts to all
    opponents.
    """
    log = get_room_logger(room_id)
    ws_key = id(websocket)
    # Give the client a full _PING_TIMEOUT_SECONDS before the first check.
    await asyncio.sleep(_PING_TIMEOUT_SECONDS)
    while True:
        try:
            last = _last_ping.get(ws_key)
            if last is None:
                # No ping ever received within the first timeout window.
                # Treat as a dead / unidentified connection and close it.
                log.warn("heartbeat_watchdog_no_ping_received_closing", {
                    "player_id": player_id,
                })
                await handle_leave_room(websocket, room_id, silent=True)
                break
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            if elapsed > _PING_TIMEOUT_SECONDS:
                log.warn("heartbeat_watchdog_timeout", {
                    "player_id": player_id,
                    "elapsed_seconds": elapsed,
                })
                await handle_leave_room(websocket, room_id, silent=True)
                break
            await asyncio.sleep(2)  # Re-check every 2 seconds
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("heartbeat_watchdog_error", {"player_id": player_id, "error": str(e)})
            break

    _last_ping.pop(ws_key, None)
    _heartbeat_watchdogs.pop(ws_key, None)





async def websocket_endpoint(websocket: WebSocket, room_id: str, token: str = ""):
    """WebSocket endpoint for room connections"""
    log = get_room_logger(room_id)
    
    # --- Auth ---
    try:
        if not token:
            await websocket.close(code=4001, reason="Missing token")
            return
        decoded = verify_firebase_token(token)
        firebase_uid = decoded.get("uid")
        if not firebase_uid:
            await websocket.close(code=4001, reason="Invalid token")
            return
    except Exception as e:
        log.warning("ws_token_verification_failed", {"error": str(e)})
        await websocket.close(code=4001, reason="Invalid token")
        return

    await manager.connect(websocket, room_id, "")
    health_check_task = None

    try:
        room = await room_service.get_room(room_id)
        if room:
            await manager.send_personal_message({
                "type": "room_update",
                "data": room.model_dump(mode='json')
            }, websocket)
        else:
            log.warn("ws_room_not_found_on_connect", {"room_id": room_id})
            await manager.send_personal_message({
                "type": "error",
                "message": "Room not found"
            }, websocket)
            await websocket.close()
            return

        player_id_for_health_check = None
        RECEIVE_TIMEOUT = 60  # Timeout for receiving messages (60 seconds)

        while True:
            try:
                try:
                    data = await asyncio.wait_for(websocket.receive_json(), timeout=RECEIVE_TIMEOUT)
                except asyncio.TimeoutError:
                    try:
                        await websocket.send_json({"type": "connection_test"})
                        continue
                    except Exception as connection_error:
                        log.warn("ws_timeout_dead_connection", {
                            "player_id": manager.get_player_id(websocket) or "unknown",
                            "error": str(connection_error),
                        })
                        await handle_leave_room(websocket, room_id, silent=True)
                        break
            except WebSocketDisconnect:
                raise
            except ConnectionError as e:
                log.warn("ws_connection_error_receiving", {
                    "player_id": manager.get_player_id(websocket) or "unknown",
                    "error": str(e),
                })
                await handle_leave_room(websocket, room_id, silent=True)
                break
            except RuntimeError as e:
                if "WebSocket is not connected" in str(e) or "Cannot call" in str(e):
                    log.info("ws_closed_externally", {
                        "player_id": manager.get_player_id(websocket) or "unknown",
                        "reason": str(e)
                    })
                else:
                    log.error("ws_runtime_error", {
                        "player_id": manager.get_player_id(websocket) or "unknown",
                        "error": str(e),
                    }, exc_info=True)
                await handle_leave_room(websocket, room_id, silent=True)
                break
            except Exception as e:
                log.error("ws_receive_error", {
                    "player_id": manager.get_player_id(websocket) or "unknown",
                    "error": str(e),
                }, exc_info=True)
                await handle_leave_room(websocket, room_id, silent=True)
                break

            message_type = data.get("type")
            player_id = manager.get_player_id(websocket) or "unknown"
            log.info("ws_message_received", {
                "msg_type": message_type,
                "player_id": player_id,
            })

            if message_type == "join_room":
                log_to_file(f"room_ws received join_room for {player_id}")
                await handle_join_room(websocket, room_id, data)
                player_id_for_health_check = manager.get_player_id(websocket)
                if player_id_for_health_check and health_check_task is None:
                    health_check_task = asyncio.create_task(
                        check_connection_health(websocket, room_id, player_id_for_health_check)
                    )
                    # Start heartbeat watchdog for this connection.
                    ws_key = id(websocket)
                    if ws_key not in _heartbeat_watchdogs:
                        _heartbeat_watchdogs[ws_key] = asyncio.create_task(
                            _heartbeat_watchdog(websocket, room_id, player_id_for_health_check)
                        )
            elif message_type == "leave_room":
                await handle_leave_room(websocket, room_id)
                break
            elif message_type == "exit_game":
                await game_ws.handle_exit_game(websocket, room_id, data)
            elif message_type == "player_ready":
                await handle_player_ready(websocket, room_id, data)
            elif message_type == "remove_player":
                await handle_remove_player(websocket, room_id, data)
            elif message_type == "set_in_game":
                await handle_set_in_game(websocket, room_id, data)
            elif message_type == "game_start":
                await game_ws.handle_game_start(websocket, room_id, data)
            elif message_type == "discard_cards":
                await game_ws.handle_discard_cards(websocket, room_id, data)
            elif message_type == "pick_card":
                await game_ws.handle_pick_card(websocket, room_id, data)
            elif message_type == "declare":
                await game_ws.handle_declare(websocket, room_id, data)
            elif message_type == "show":
                await game_ws.handle_show(websocket, room_id, data)
            elif message_type == "request_state":
                log_to_file(f"room_ws received request_state for {player_id}")
                await game_ws.handle_request_state(websocket, room_id, data)
            elif message_type == "player_action":
                await handle_player_action(websocket, room_id, data)
            elif message_type == "reset_game":
                await handle_reset_game(websocket, room_id, data)
            elif message_type == "update_room_status_to_waiting":
                await handle_update_room_status_to_waiting(websocket, room_id, data)
            elif message_type == "ping":
                # Heartbeat ping from Flutter client — record timestamp and reply.
                _last_ping[id(websocket)] = datetime.now(timezone.utc)
                try:
                    await websocket.send_json({"type": "pong"})
                except Exception:
                    pass  # Connection may already be closing
            elif message_type == "chat_message":
                await handle_chat_message(websocket, room_id, data)
            else:
                log.warn("ws_unknown_message_type", {"msg_type": message_type, "player_id": player_id})
                await manager.send_personal_message({
                    "type": "error",
                    "message": f"Unknown message type: {message_type}"
                }, websocket)

    except WebSocketDisconnect:
        log.info("ws_client_disconnected", {
            "player_id": manager.get_player_id(websocket) or "unknown",
        })
        if health_check_task:
            health_check_task.cancel()
        ws_key = id(websocket)
        if ws_key in _heartbeat_watchdogs:
            _heartbeat_watchdogs[ws_key].cancel()
            _heartbeat_watchdogs.pop(ws_key, None)
        _last_ping.pop(ws_key, None)
        await handle_leave_room(websocket, room_id, silent=True)
    except ConnectionError as e:
        log.warn("ws_connection_error", {
            "player_id": manager.get_player_id(websocket) or "unknown",
            "error": str(e),
        })
        if health_check_task:
            health_check_task.cancel()
        ws_key = id(websocket)
        if ws_key in _heartbeat_watchdogs:
            _heartbeat_watchdogs[ws_key].cancel()
            _heartbeat_watchdogs.pop(ws_key, None)
        _last_ping.pop(ws_key, None)
        await handle_leave_room(websocket, room_id, silent=True)
    except Exception as e:
        log.error("ws_unexpected_error", {
            "player_id": manager.get_player_id(websocket) or "unknown",
            "error": str(e),
        }, exc_info=True)
        if health_check_task:
            health_check_task.cancel()
        ws_key = id(websocket)
        if ws_key in _heartbeat_watchdogs:
            _heartbeat_watchdogs[ws_key].cancel()
            _heartbeat_watchdogs.pop(ws_key, None)
        _last_ping.pop(ws_key, None)
        await handle_leave_room(websocket, room_id, silent=True)
    finally:
        if health_check_task and not health_check_task.done():
            health_check_task.cancel()
            try:
                await health_check_task
            except asyncio.CancelledError:
                pass


@with_room_lock
async def handle_join_room(websocket: WebSocket, room_id: str, data: Dict):
    """Handle join room message"""
    log = get_room_logger(room_id)
    player_id = data.get("player_id", "")
    player_name = data.get("player_name", "")
    avatar_seed = data.get("avatar_seed")

    if not player_id or not player_name:
        await manager.send_personal_message({
            "type": "error",
            "message": "player_id and player_name are required"
        }, websocket)
        return

    # Update connection mapping
    manager.connection_players[websocket] = player_id
    log_to_file("handle_join_room: checking player exists")
    player_exists = await room_service.player_exists(room_id, player_id)
    log_to_file(f"handle_join_room: player_exists={player_exists}")

    if not player_exists:
        success = await room_service.add_player(room_id, player_id, player_name, is_connected=True, avatar_seed=avatar_seed)

        if success:
            # Force is_connected=True if needed
            await room_service.update_player_fields(room_id, player_id, is_connected=True)
    else:
        # Player already in room — reconnecting
        connected_result = await room_service.set_player_connected(room_id, player_id, True)

        updates = {}
        if player_name:
            updates["player_name"] = player_name
        if avatar_seed is not None:
            updates["avatar_seed"] = avatar_seed
        if updates:
            await room_service.update_player_fields(room_id, player_id, **updates)

        # Force is_connected=True if still not set
        await room_service.update_player_fields(room_id, player_id, is_connected=True)

        log.info("player_reconnected", {
            "player_id": player_id,
            "player_name": player_name,
            "avatar_seed": avatar_seed,
        })
        success = True

        # (Grace timer removed — turn timer now handles idle/offline players.)

        # Clear in_game status only when room is in WAITING state.
        # During an active game (PLAYING), an exited player keeps is_exited=True
        # so they spectate without re-entering active play.
        # is_in_game is reset so a returning lobby player shows as in_lobby.
        # Note: We NO LONGER clear is_exited here because exited players remain exited across tournament rounds.
        _room_for_status = await room_service.get_room(room_id)
        if _room_for_status and _room_for_status.status in (
            RoomStatus.WAITING, RoomStatus.WAITING.value
        ):
            await room_service.set_player_in_game(room_id, player_id, False)

        # Evict any stale connections for this player
        stale_connections = [
            ws for ws, pid in list(manager.connection_players.items())
            if pid == player_id and ws is not websocket
        ]
        for stale_ws in stale_connections:
            log.info("evicting_stale_connection_for_player", {
                "player_id": player_id,
                "stale_ws_id": id(stale_ws),
            })
            log_to_file(f"handle_join_room: evicting stale connection {id(stale_ws)}")
            manager.disconnect(stale_ws)
            try:
                await stale_ws.close()
            except Exception:
                pass  # Already closed, ignore

    if success:
        log_to_file("handle_join_room: success is True, getting room")
        room = await room_service.get_room(room_id)
        
        # Cancel the empty room deletion timer if it's running
        if room_id in _empty_room_timers:
            _empty_room_timers[room_id].cancel()
            _empty_room_timers.pop(room_id, None)
            log.info("empty_room_timer_cancelled_player_joined")

        log_to_file("handle_join_room: broadcasting room_update")
        await manager.broadcast_to_room({
            "type": "room_update",
            "data": room.model_dump(mode='json')
        }, room_id)
        log_to_file("handle_join_room: broadcasted room_update")
        log.info("room_update_broadcast_after_join", {
            "player_id": player_id,
            "player_name": player_name,
            "total_players": len(room.players),
        })

        # If the player is rejoining during an active game, broadcast the state to everyone
        # so all players immediately see that this player is no longer exited.
        log_to_file(f"handle_join_room: checking player_exists: {player_exists}")
        if player_exists:
            from app.services.game_service import game_service
            log_to_file("handle_join_room: getting game state")
            game_state = await game_service.get_game_state(room_id)
            log_to_file(f"handle_join_room: game state is {game_state is not None}")
            if game_state:
                game_state.action_seq += 1
                try:
                    log_to_file(f"handle_join_room broadcasting game update for {room_id}")
                    await game_ws.broadcast_game_update(room_id)
                    log_to_file(f"handle_join_room broadcast SUCCESS for {room_id}")
                except Exception as e:
                    import traceback
                    log_to_file(f"handle_join_room broadcast FAILED: {str(e)}\n{traceback.format_exc()}")
                    log.error("reconnect_game_state_broadcast_failed", {"error": str(e), "trace": traceback.format_exc()})
                    await manager.send_personal_message({"type": "error", "message": f"Broadcast failed: {str(e)}"}, websocket)
                log.info("reconnect_game_state_broadcasted", {
                    "player_id": player_id,
                    "player_name": player_name,
                })
    else:
        await manager.send_personal_message({
            "type": "error",
            "message": "Failed to join room (room full)"
        }, websocket)
        log.warn("join_room_failed", {"player_id": player_id, "reason": "room_full"})


@with_room_lock
async def handle_leave_room(websocket: WebSocket, room_id: str, silent: bool = False):
    """Handle leave room"""
    await _handle_leave_room_internal(websocket, room_id, silent)

async def _handle_leave_room_internal(websocket: WebSocket, room_id: str, silent: bool = False):
    """Internal leave room logic without lock"""
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)

    was_admin = False
    if player_id:
        room = await room_service.get_room(room_id)
        if room:
            player = next((p for p in room.players if p.player_id == player_id), None)
            if player and player.is_admin:
                was_admin = True

    manager.disconnect(websocket)

    if player_id:
        await room_service.set_player_connected(room_id, player_id, False)

        if was_admin:
            new_admin_id = await room_service.transfer_admin(room_id, player_id)

        active_connections = manager.get_room_connections(room_id)

        if len(active_connections) == 0:
            room = await room_service.get_room(room_id)
            log.info("room_empty_starting_deletion_timer", {
                "last_player_id": player_id,
            })
            # Start a 5-minute timer to delete the room if no one rejoins
            if room_id not in _empty_room_timers:
                _empty_room_timers[room_id] = asyncio.create_task(_empty_room_timeout(room_id))
            return

        room = await room_service.get_room(room_id)
        if room and not silent:
            try:
                await manager.broadcast_to_room({
                    "type": "room_update",
                    "data": room.model_dump(mode='json')
                }, room_id)
                log.info("room_update_broadcast_after_leave", {
                    "left_player_id": player_id,
                    "was_admin": was_admin,
                    "remaining_connections": len(active_connections),
                })
            except Exception as e:
                log.error("room_update_broadcast_after_leave_failed", {
                    "left_player_id": player_id,
                    "error": str(e),
                })


@with_room_lock
async def handle_player_ready(websocket: WebSocket, room_id: str, data: Dict):
    """Handle player ready status change"""
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)

    if not player_id:
        player_id = data.get("player_id", "")
        if player_id:
            manager.connection_players[websocket] = player_id

    is_ready = data.get("is_ready", False)

    if not player_id:
        await manager.send_personal_message({
            "type": "error",
            "message": "Player not identified. Join room first by sending a 'join_room' message."
        }, websocket)
        return

    success = await room_service.set_player_ready(room_id, player_id, is_ready)

    if success:
        room = await room_service.get_room(room_id)
        
        all_ready = len(room.players) >= 2 and all(player.is_ready for player in room.players)

        if all_ready and room.status != RoomStatus.WAITING:
            # All players are in the lobby and ready, but room is not WAITING.
            # This means they abandoned the previous game (or it finished) and want to start a new one.
            log.info("abandoned_game_reset_triggered", {"room_id": room_id})
            from app.api.routes.games import clear_tournament_scores
            await game_service.clear_game(room_id)
            clear_tournament_scores(room_id)
            await room_service.update_room_status(room_id, RoomStatus.WAITING)
            await room_service.reset_exited_players(room_id)
            room = await room_service.get_room(room_id)

        await manager.broadcast_to_room({
            "type": "room_update",
            "data": room.model_dump(mode='json')
        }, room_id)

        if room.status == RoomStatus.WAITING:
            all_in_game = all((player.is_in_game or not player.is_connected) for player in room.players)

            if all_ready and all_in_game:
                log.info("auto_game_start_triggered", {
                    "player_count": len(room.players),
                    "trigger": "player_ready",
                })
                game_started = await game_ws.start_game_for_room(room_id)
                if not game_started:
                    log.error("auto_game_start_failed", {"trigger": "player_ready"})
    else:
        await manager.send_personal_message({
            "type": "error",
            "message": "Failed to update ready status"
        }, websocket)


# Action types that must NOT be relayed to opponents.
# Per the turn-reference spec, 'show' is intentionally suppressed on the
# opponent side — they react only to the 'show_results' WS event that the
# server broadcasts after HTTP POST /api/games/{room_id}/show.
_RELAY_BLOCKED_ACTIONS: frozenset[str] = frozenset({"show"})


@with_room_lock
async def handle_player_action(websocket: WebSocket, room_id: str, data: Dict):
    """Handle player action (card drop, pick, etc.) and broadcast to other players.

    Certain action types (see _RELAY_BLOCKED_ACTIONS) are accepted from the
    sender but deliberately NOT forwarded to opponents.
    """
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)
    if not player_id:
        await manager.send_personal_message({"type": "error", "message": "Player not identified"}, websocket)
        return

    action_type = data.get("action_type", "")
    card_data = data.get("card")

    # Silently drop relay-blocked actions — they are for local UI use only.
    if action_type in _RELAY_BLOCKED_ACTIONS:
        log.info("player_action_relay_blocked", {
            "player_id": player_id,
            "action_type": action_type,
        })
        return

    log.info("player_action_broadcast", {
        "player_id": player_id,
        "action_type": action_type,
        "has_card": card_data is not None,
    })

    await manager.broadcast_to_room({
        "type": "player_action",
        "data": {
            "action_type": action_type,
            "player_id": player_id,
            "card": card_data,
        }
    }, room_id, exclude_player_id=player_id)


@with_room_lock
async def handle_set_in_game(websocket: WebSocket, room_id: str, data: Dict):
    """Handle set is_in_game status message"""
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)

    if not player_id:
        await manager.send_personal_message({
            "type": "error",
            "message": "Player not identified. Join room first."
        }, websocket)
        return

    is_in_game = data.get("is_in_game", data.get("in_game", True))

    success = await room_service.set_player_in_game(room_id, player_id, is_in_game)

    if success:
        room = await room_service.get_room(room_id)

        await manager.broadcast_to_room({
            "type": "room_update",
            "data": room.model_dump(mode='json')
        }, room_id)

        if room.status == RoomStatus.WAITING and is_in_game:
            # If AT LEAST ONE player reaches the game screen, it means the frontend countdown finished.
            # We don't wait for all players to send set_in_game because some might freeze or be unresponsive.
            # Start the game immediately. Other players will join in-progress when they load.
            has_enough_players = len(room.players) >= 2

            if has_enough_players:
                log.info("auto_game_start_triggered", {
                    "player_count": len(room.players),
                    "trigger": "set_in_game_single_player_reached",
                })
                game_started = await game_ws.start_game_for_room(room_id)
                if not game_started:
                    log.error("auto_game_start_failed", {"trigger": "set_in_game"})
    else:
        await manager.send_personal_message({
            "type": "error",
            "message": "Failed to update is_in_game status"
        }, websocket)


@with_room_lock
async def handle_remove_player(websocket: WebSocket, room_id: str, data: Dict):
    """Handle remove player request (admin only)"""
    log = get_room_logger(room_id)
    admin_player_id = manager.get_player_id(websocket)
    target_player_id = data.get("target_player_id", "")

    if not admin_player_id:
        await manager.send_personal_message({"type": "error", "message": "Player not identified. Join room first."}, websocket)
        return

    if not target_player_id:
        await manager.send_personal_message({"type": "error", "message": "target_player_id is required"}, websocket)
        return

    success = await room_service.remove_player(room_id, target_player_id, admin_player_id)

    if success:
        target_connections = [
            conn for conn, pid in manager.connection_players.items()
            if pid == target_player_id
        ]

        for conn in target_connections:
            try:
                await manager.send_personal_message({
                    "type": "player_removed",
                    "message": "You have been removed from the room by the admin."
                }, conn)
            except Exception as e:
                log.error("player_removed_notification_failed", {
                    "target_player_id": target_player_id,
                    "error": str(e),
                })

        room = await room_service.get_room(room_id)

        for conn in target_connections:
            await _handle_leave_room_internal(conn, room_id, silent=True)

        if room:
            await manager.broadcast_to_room({
                "type": "room_update",
                "data": room.model_dump(mode='json')
            }, room_id)
    else:
        await manager.send_personal_message({
            "type": "error",
            "message": "Failed to remove player. You may not be admin or player not found."
        }, websocket)


@with_room_lock
async def handle_reset_game(websocket: WebSocket, room_id: str, data: Dict):
    """Handle reset game request - clears game state and resets room to waiting"""
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)

    if not player_id:
        await manager.send_personal_message({"type": "error", "message": "Player not identified. Join room first."}, websocket)
        return

    room = await room_service.get_room(room_id)
    if not room:
        await manager.send_personal_message({"type": "error", "message": "Room not found"}, websocket)
        return

    player = next((p for p in room.players if p.player_id == player_id), None)
    if not player or not player.is_admin:
        await manager.send_personal_message({"type": "error", "message": "Only room admin can reset the game"}, websocket)
        return

    from app.api.routes.games import clear_tournament_scores
    await game_service.clear_game(room_id)
    clear_tournament_scores(room_id)
    await room_service.update_room_status(room_id, RoomStatus.WAITING)
    await room_service.merge_waiting_players(room_id)
    await room_service.reset_exited_players(room_id)  # Clear exited state for new game
    await room_service.reset_all_players_ready(room_id)

    log.info("game_reset_by_admin", {
        "admin_player_id": player_id,
        "player_count": len(room.players),
    })

    updated_room = await room_service.get_room(room_id)
    if updated_room:
        await manager.broadcast_to_room({
            "type": "room_update",
            "data": updated_room.model_dump(mode='json')
        }, room_id)


@with_room_lock
async def handle_update_room_status_to_waiting(websocket: WebSocket, room_id: str, data: Dict):
    """Handle update room status to waiting request"""
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)

    if not player_id:
        await manager.send_personal_message({"type": "error", "message": "Player not identified. Join room first."}, websocket)
        return

    room = await room_service.get_room(room_id)
    if not room:
        await manager.send_personal_message({"type": "error", "message": "Room not found"}, websocket)
        return

    player = next((p for p in room.players if p.player_id == player_id), None)
    if not player:
        await manager.send_personal_message({"type": "error", "message": "Player not found in room"}, websocket)
        return

    from app.api.routes.games import clear_tournament_scores
    await game_service.clear_game(room_id)
    clear_tournament_scores(room_id)
    await room_service.update_room_status(room_id, RoomStatus.WAITING)
    await room_service.merge_waiting_players(room_id)
    # When "Play Again" is clicked, we are starting a BRAND NEW game/tournament.
    # Therefore, we must reset all players who exited/eliminated in the previous game.
    await room_service.reset_exited_players(room_id)
    await room_service.reset_all_players_ready(room_id)

    log.info("room_status_reset_to_waiting", {
        "requested_by": player_id,
    })

    updated_room = await room_service.get_room(room_id)
    if updated_room:
        await manager.broadcast_to_room({
            "type": "room_update",
            "data": updated_room.model_dump(mode='json')
        }, room_id)


@with_room_lock
async def handle_chat_message(websocket: WebSocket, room_id: str, data: Dict):
    """Broadcast a chat message from one player to everyone in the room.

    The client sends::

        {
            "type": "chat_message",
            "sender_id":   "<uuid>",
            "sender_name": "Alice",
            "text":        "Hello!",
            "avatar_seed": "<optional seed string>"
        }

    The server re-broadcasts the exact same payload (with type ``chat_message``)
    to **all** connections in the room, including the sender, so the sender can
    confirm delivery.  The Flutter client ignores the echo for its own messages
    because it already inserted them optimistically (``isFromMe=true``).
    """
    log = get_room_logger(room_id)
    player_id = manager.get_player_id(websocket)

    if not player_id:
        await manager.send_personal_message({
            "type": "error",
            "message": "Player not identified. Join room first."
        }, websocket)
        return

    text = (data.get("text") or "").strip()
    if not text:
        # Silently ignore empty messages
        return

    sender_name = data.get("sender_name") or "Player"
    avatar_seed = data.get("avatar_seed")

    broadcast_payload: Dict = {
        "type": "chat_message",
        "data": {
            "sender_id":   player_id,     # always use server-known ID, not client-supplied
            "sender_name": sender_name,
            "text":        text,
        }
    }
    if avatar_seed:
        broadcast_payload["data"]["avatar_seed"] = avatar_seed

    log.info("chat_message_broadcast", {
        "sender_id":   player_id,
        "sender_name": sender_name,
        "text_length": len(text),
    })

    await manager.broadcast_to_room(broadcast_payload, room_id)
