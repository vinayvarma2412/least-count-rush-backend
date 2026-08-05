"""
WebSocket connection manager
"""
from typing import Dict, Set
from fastapi import WebSocket
import json
import asyncio
from app.utils.room_logger import global_log
from app.utils.debug_log import log_to_file


class ConnectionManager:
    """Manages WebSocket connections for rooms"""

    def __init__(self):
        # room_id -> Set of WebSocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # WebSocket -> room_id mapping
        self.connection_rooms: Dict[WebSocket, str] = {}
        # WebSocket -> player_id mapping
        self.connection_players: Dict[WebSocket, str] = {}
        # Per-websocket lock to prevent concurrent ASGI sends
        self.ws_locks: Dict[WebSocket, asyncio.Lock] = {}
        # Callback for when a player disconnects (room_id, player_id)
        self.on_disconnect_callback = None

    def set_disconnect_callback(self, callback):
        """Set callback function to call when a player disconnects"""
        self.on_disconnect_callback = callback

    async def connect(self, websocket: WebSocket, room_id: str, player_id: str):
        """Connect a WebSocket to a room"""
        await websocket.accept()

        if room_id not in self.active_connections:
            self.active_connections[room_id] = set()

        self.active_connections[room_id].add(websocket)
        self.connection_rooms[websocket] = room_id
        self.connection_players[websocket] = player_id
        self.ws_locks[websocket] = asyncio.Lock()

        global_log.info("ws_accepted", {
            "room_id": room_id,
            "player_id": player_id or "unknown",
            "active_connections": len(self.active_connections.get(room_id, set())),
        })

    def disconnect(self, websocket: WebSocket):
        """Disconnect a WebSocket from its room"""
        room_id = self.connection_rooms.get(websocket)
        player_id = self.connection_players.get(websocket, "")

        if room_id:
            self.active_connections[room_id].discard(websocket)
            if not self.active_connections[room_id]:
                del self.active_connections[room_id]

        self.connection_rooms.pop(websocket, None)
        self.connection_players.pop(websocket, None)
        self.ws_locks.pop(websocket, None)

        # Only call callback if we have both room_id and player_id (player was actually joined)
        if room_id and player_id:
            global_log.info("ws_disconnected", {
                "room_id": room_id,
                "player_id": player_id,
                "remaining_connections": len(self.active_connections.get(room_id, set())),
            })
            if self.on_disconnect_callback:
                import asyncio
                if asyncio.iscoroutinefunction(self.on_disconnect_callback):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self.on_disconnect_callback(room_id, player_id, websocket))
                    except RuntimeError:
                        asyncio.run(self.on_disconnect_callback(room_id, player_id, websocket))
                else:
                    self.on_disconnect_callback(room_id, player_id, websocket)

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        """Send a message to a specific WebSocket connection"""
        lock = self.ws_locks.get(websocket)
        log_to_file(f"send_personal_message called for {self.get_player_id(websocket)}. Lock exists: {lock is not None}")
        if lock is None:
            return

        try:
            async with lock:
                log_to_file(f"send_personal_message acquiring lock for {self.get_player_id(websocket)} SUCCESS. Sending...")
                await websocket.send_json(message)
                log_to_file(f"send_personal_message sent for {self.get_player_id(websocket)}")
        except Exception as e:
            from fastapi import WebSocketDisconnect
            import traceback
            log_to_file(f"send_personal_message exception for {self.get_player_id(websocket)}: {str(e)}\n{traceback.format_exc()}")
            room_id = self.connection_rooms.get(websocket, "unknown")
            player_id = self.connection_players.get(websocket, "unknown")
            
            error_msg = str(e) if str(e) else type(e).__name__
            if isinstance(e, (WebSocketDisconnect, RuntimeError)):
                global_log.warning("ws_send_failed_disconnected", {
                    "room_id": room_id,
                    "player_id": player_id,
                    "msg_type": message.get("type", "?"),
                    "error": error_msg,
                })
            else:
                global_log.error("ws_send_failed", {
                    "room_id": room_id,
                    "player_id": player_id,
                    "msg_type": message.get("type", "?"),
                    "error": error_msg,
                })
            # DO NOT call self.disconnect(websocket) here.
            # If the socket is truly dead, receive_json() in the endpoint loop
            # will fail and trigger handle_leave_room() for proper cleanup.

    async def send_personal_message_cross_node(self, room_id: str, player_id: str, message: dict):
        """Send a message to a specific player in a room locally"""
        connections = list(self.active_connections.get(room_id, set()))
        for conn in connections:
            if conn not in self.active_connections.get(room_id, set()):
                continue
            if self.connection_players.get(conn) == player_id:
                await self.send_personal_message(message, conn)
                break

    async def broadcast_to_room(self, message: dict, room_id: str, exclude_player_id: str = None):
        """Broadcast a message to all connections in a room locally"""
        connections = list(self.active_connections.get(room_id, set()))
        for conn in connections:
            if conn not in self.active_connections.get(room_id, set()):
                continue
            if exclude_player_id and self.connection_players.get(conn) == exclude_player_id:
                continue
            await self.send_personal_message(message, conn)

    def get_room_connections(self, room_id: str) -> Set[WebSocket]:
        """Get all active connections for a room"""
        return self.active_connections.get(room_id, set())

    def get_player_id(self, websocket: WebSocket) -> str:
        """Get player ID for a WebSocket connection"""
        return self.connection_players.get(websocket, "")


# Global connection manager instance
manager = ConnectionManager()
