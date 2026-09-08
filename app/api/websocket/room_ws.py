"""
WebSocket handlers for room management
"""
from datetime import datetime, timezone
from fastapi import WebSocket, WebSocketDisconnect, HTTPException
from typing import Dict
import asyncio
import json
import os
import uuid
from app.api.websocket.connection_manager import manager
from app.utils.lock import with_room_lock, get_room_lock
from app.services.room_service import room_service
from app.services.game_service import game_service
from app.schemas.room import RoomStatus
from app.api.websocket import game_ws
from app.utils.room_logger import get_room_logger, global_log
from app.utils.firebase_auth import verify_firebase_token
from app.utils.debug_log import log_to_file
from app.services.redis_client import KEY_TTL_SECONDS, redis_client

# Track running connection status check tasks per room
_connection_status_tasks: Dict[str, asyncio.Task] = {}

# Per-connection last-ping timestamp for heartbeat watchdog.
_last_ping: Dict[int, datetime] = {}  # key = id(websocket)

# Per-connection heartbeat watchdog tasks.
_heartbeat_watchdogs: Dict[int, asyncio.Task] = {}  # key = id(websocket)

# Server selection state
_room_latency_reports: Dict[str, Dict[str, Dict[str, int]]] = {} # room_id -> {player_id: {server_url: latency_ms}}
_server_selection_tasks: Dict[str, asyncio.Task] = {}
_server_switch_ack_locks: Dict[str, asyncio.Lock] = {}
_SERVER_SELECTION_REPORT_TIMEOUT_SECONDS = 10


async def _server_selection_urls() -> list[str]:
    """Return the canonical server list configured by the backend from Firebase config.
    """
    try:
        from app.services.remote_config_service import remote_config_service
        from app.config import settings
        
        template, _ = await remote_config_service.get_template()
        key = "servers_list_dev" if settings.develop_mode else "servers_list"
        configured = template.get("parameters", {}).get(key, {}).get("defaultValue", {}).get("value", "")
        
        if configured:
            import json
            values = json.loads(configured)
            urls: list[str] = []
            for value in values:
                if isinstance(value, dict) and "url" in value:
                    url = value["url"].strip().rstrip("/")
                    if url.startswith(("http://", "https://")) and url not in urls:
                        urls.append(url)
            if urls:
                return urls
    except Exception as e:
        from app.utils.room_logger import global_log
        global_log.error("server_selection_urls_fetch_failed", {"error": str(e)})
        
    return []


def _server_selection_key(room_id: str) -> str:
    return f"rooms:server_selection:{room_id}"

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
async def on_player_disconnect(room_id: str, player_id: str, websocket=None, connection_id=None):
    """Callback when a player disconnects from connection manager."""
    if player_id:
        # 1. Check if the player has any OTHER active connections on THIS server
        active_connections = manager.get_room_connections(room_id)
        player_still_connected_local = any(
            manager.connection_players.get(ws) == player_id
            for ws in active_connections
        )
        
        if player_still_connected_local:
            get_room_logger(room_id).info("player_disconnect_ignored_active_connection_exists_local", {
                "player_id": player_id,
            })
            return

        # 2. Check if a newer connection for this player has been established globally (e.g. on another server)
        # by checking if the connection_id in Redis matches the one that just disconnected.
        room = await room_service.get_room(room_id)
        if room:
            p = next((x for x in room.players + room.waiting_players if x.player_id == player_id), None)
            if p:
                current_connection_id = p.model_dump().get("connection_id") if hasattr(p, "model_dump") else p.connection_id if hasattr(p, "connection_id") else (p.get("connection_id") if isinstance(p, dict) else None)
                if connection_id and current_connection_id and connection_id != current_connection_id:
                    get_room_logger(room_id).info("player_disconnect_ignored_newer_connection_exists_global", {
                        "player_id": player_id,
                        "old_connection": connection_id,
                        "new_connection": current_connection_id,
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
        if room and room.room_type.value == "public" and room.status == RoomStatus.WAITING:
            async def _delayed_remove(r_id, p_id):
                await asyncio.sleep(30)
                async with get_room_lock(r_id):
                    current_r = await room_service.get_room(r_id)
                    if not current_r or current_r.status != RoomStatus.WAITING:
                        return
                    p = next((x for x in current_r.players + current_r.waiting_players if x.player_id == p_id), None)
                    if p and not p.is_connected:
                        get_room_logger(r_id).info("public_room_delayed_player_removal", {"player_id": p_id})
                        removed = await room_service.force_remove_player(r_id, p_id)
                        if removed:
                            updated_r = await room_service.get_room(r_id)
                            if updated_r:
                                await manager.broadcast_to_room({
                                    "type": "room_update",
                                    "data": updated_r.model_dump(mode='json')
                                }, r_id)
            asyncio.create_task(_delayed_remove(room_id, player_id))

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
            elif message_type == "latency_report":
                await handle_latency_report(websocket, room_id, data)
            elif message_type == "server_switch_ack":
                await handle_server_switch_ack(websocket, room_id, data)
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
    connection_id = str(uuid.uuid4())
    manager.connection_ids[websocket] = connection_id
    log_to_file("handle_join_room: checking player exists")
    player_exists = await room_service.player_exists(room_id, player_id)
    log_to_file(f"handle_join_room: player_exists={player_exists}")

    if not player_exists:
        success = await room_service.add_player(room_id, player_id, player_name, is_connected=True, avatar_seed=avatar_seed)

        if success:
            # Force is_connected=True if needed
            await room_service.update_player_fields(room_id, player_id, is_connected=True, connection_id=connection_id)
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
        await room_service.update_player_fields(room_id, player_id, is_connected=True, connection_id=connection_id)

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

        if room.status == RoomStatus.WAITING and len(room.players) >= 2:
            if room_id not in _server_selection_tasks or _server_selection_tasks[room_id].done():
                _server_selection_tasks[room_id] = asyncio.create_task(
                    _start_server_selection(room_id, room)
                )
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
        is_public = room.room_type.value == "public"
        
        # Debug printing

        if is_public:
            active_players = [p for p in room.players if p.is_connected]
            all_ready = len(active_players) >= 2 and all(player.is_ready for player in active_players)
        else:
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
            if is_public:
                all_in_game = all(player.is_in_game for player in active_players)
            else:
                all_in_game = all((player.is_in_game or not player.is_connected) for player in room.players)


            if all_ready and all_in_game:
                if is_public:
                    # Proceed straight to game if public, since public rooms might not need the server switch again
                    # Actually, we should find best server for public games too.
                    # But the requirement is that it finds the best server for *all* players.
                    pass
                log.info("triggering_server_selection", {
                    "player_count": len(room.players),
                })
                # Check if enough players (at least 2)
                if len(active_players if is_public else room.players) >= 2:
                    if room_id not in _server_selection_tasks or _server_selection_tasks[room_id].done():
                        _server_selection_tasks[room_id] = asyncio.create_task(
                            _start_server_selection(room_id, room)
                        )
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

async def handle_latency_report(websocket: WebSocket, room_id: str, data: Dict):
    """Handle latency report from a client"""
    player_id = manager.get_player_id(websocket)
    if not player_id:
        return
    
    latencies = data.get("latencies", {})
    if not isinstance(latencies, dict):
        get_room_logger(room_id).warning("invalid_latency_report", {
            "player_id": player_id,
        })
        return

    # Only retain numeric, non-negative measurements.  This prevents a
    # malformed client report from breaking or skewing server selection.
    latencies = {
        str(url): latency
        for url, latency in latencies.items()
        if isinstance(latency, (int, float)) and not isinstance(latency, bool)
        and latency >= 0
    }
    if room_id not in _room_latency_reports:
        _room_latency_reports[room_id] = {}
    
    _room_latency_reports[room_id][player_id] = latencies
    get_room_logger(room_id).info("received_latency_report", {
        "player_id": player_id,
        "latencies": latencies
    })

async def _start_server_selection(room_id: str, room):
    """Initiates server selection and then starts the game."""
    log = get_room_logger(room_id)
    log.info("start_server_selection", {"room_id": room_id})

    server_urls = await _server_selection_urls()
    if not server_urls:
        log.error("server_selection_no_servers_configured")
        await manager.broadcast_to_room({
            "type": "error",
            "message": "No game servers are configured.",
        }, room_id)
        return

    if len(server_urls) == 1:
        log.info("server_selection_single_server", {"server_url": server_urls[0]})
        await game_ws.start_game_for_room(room_id)
        return

    is_public = room.room_type.value == "public"
    expected_players = [
        p.player_id
        for p in room.players
        if not is_public or p.is_connected
    ]
    if len(expected_players) < 2:
        return
    
    # Reset the accumulator *before* notifying clients.  A client can reply as
    # soon as its WebSocket receives this broadcast; resetting it afterwards
    # loses those fast reports and makes selection appear to do nothing.
    _room_latency_reports[room_id] = {}

    # The backend is the source of truth for candidates. Every client measures
    # this exact list, so the final sums are comparable.
    await manager.broadcast_to_room({
        "type": "find_best_server",
        "data": {
            "servers": server_urls,
        }
    }, room_id)
    
    # Clients allow each parallel HTTP probe up to five seconds.  Keep the
    # collection window slightly longer, otherwise timeout reports routinely
    # arrive after selection has already completed.
    wait_time = 0.0
    while wait_time < _SERVER_SELECTION_REPORT_TIMEOUT_SECONDS:
        reports = _room_latency_reports.get(room_id, {})
        has_all = all(pid in reports for pid in expected_players)
        if has_all and len(expected_players) > 0:
            break
        await asyncio.sleep(0.1)
        wait_time += 0.1
        
    reports = _room_latency_reports.get(room_id, {})
    if not all(player_id in reports for player_id in expected_players):
        log.warning("server_selection_reports_timed_out", {
            "expected_players": expected_players,
            "reporting_players": list(reports),
        })
        _room_latency_reports.pop(room_id, None)
        await manager.broadcast_to_room({
            "type": "error",
            "message": "Server selection timed out. Please ready up again.",
        }, room_id)
        return

    log.info("server_selection_reports_gathered", {"reports": reports})
    
    best_server = None
    if reports:
        # Compare only servers measured by every responding player.  Summing a
        # URL reported by just one client biases selection toward a server that
        # other players did not measure (or cannot reach).
        common_servers = set.intersection(
            *(set(latencies) for latencies in reports.values())
        )
        server_sums = {
            url: sum(
                10000 if latencies[url] >= 9999 else latencies[url]
                for latencies in reports.values()
            )
            for url in common_servers
        }

        if server_sums:
            best_server = min(server_sums.keys(), key=lambda k: server_sums[k])
            log.info("best_server_selected", {"best_server": best_server, "server_sums": server_sums})
    
    if not best_server:
        log.error("server_selection_no_common_server", {"reports": reports})
        _room_latency_reports.pop(room_id, None)
        await manager.broadcast_to_room({
            "type": "error",
            "message": "No server is reachable by every player. Please try again.",
        }, room_id)
        return

    # This state is shared by every backend instance. Clients reconnect to the
    # selected server and acknowledge there; that server starts the game once
    # all expected players have arrived.
    await redis_client.set(
        _server_selection_key(room_id),
        json.dumps({
            "server_url": best_server,
            "expected_players": expected_players,
            "acknowledged_players": [],
            "started": False,
        }),
        ex=KEY_TTL_SECONDS,
    )

    await manager.broadcast_to_room({
        "type": "server_switch",
        "data": {
            "server_url": best_server,
            "server_region": "",
        },
    }, room_id)

    _room_latency_reports.pop(room_id, None)


async def handle_server_switch_ack(websocket: WebSocket, room_id: str, data: Dict):
    """Record a selected-server reconnection and start exactly once when ready."""
    player_id = manager.get_player_id(websocket)
    if not player_id:
        return

    key = _server_selection_key(room_id)
    lock = _server_switch_ack_locks.setdefault(room_id, asyncio.Lock())
    async with lock:
        raw_state = await redis_client.get(key)
        if not raw_state:
            return
        try:
            state = json.loads(raw_state)
        except (TypeError, json.JSONDecodeError):
            get_room_logger(room_id).error("invalid_server_selection_state")
            return

        expected_players = state.get("expected_players", [])
        if player_id not in expected_players or state.get("started"):
            return

        acknowledged_players = set(state.get("acknowledged_players", []))
        acknowledged_players.add(player_id)
        state["acknowledged_players"] = sorted(acknowledged_players)

        if set(expected_players).issubset(acknowledged_players):
            state["started"] = True

        await redis_client.set(key, json.dumps(state), ex=KEY_TTL_SECONDS)
        log = get_room_logger(room_id)
        log.info("server_switch_acknowledged", {
            "player_id": player_id,
            "acknowledged_count": len(acknowledged_players),
            "expected_count": len(expected_players),
        })

        if state["started"]:
            log.info("server_switch_all_acknowledged_waiting_for_frontend_countdown")
            # We explicitly do NOT call start_game_for_room here.
            # The frontend's 5-second countdown is still running. When it finishes,
            # the frontend will navigate to the game screen and send set_in_game=True.
            # handle_set_in_game will then call start_game_for_room.
