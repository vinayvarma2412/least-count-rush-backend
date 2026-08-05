"""
Room management service — Redis-backed.

Key schema:
  room:{room_id}          → JSON blob of the room dict (TTL 4 h)
  room_code:{room_code}   → room_id string             (TTL 4 h)
  rooms:index             → Redis Set of all room_ids  (no TTL; cleaned lazily)
"""
import os
import uuid
import json
import random
from datetime import datetime, timezone
from typing import Dict, Optional, List
from app.schemas.room import RoomCreate, RoomResponse, RoomStatus, PlayerInfo
from app.utils.room_logger import get_room_logger, close_room_logger, global_log
from app.services.redis_client import redis_client, KEY_TTL_SECONDS


# ── Serialisation helpers ────────────────────────────────────────────────────

def _room_to_json(room: dict) -> str:
    """Serialise a room dict to JSON, converting non-serialisable types."""
    def default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, RoomStatus):
            return obj.value
        raise TypeError(f"Object of type {type(obj)} is not JSON serialisable")
    return json.dumps(room, default=default)


def _room_from_json(raw: str) -> dict:
    """Deserialise a room dict from JSON, reconstructing datetime fields."""
    room = json.loads(raw)
    # Reconstruct datetime fields
    for field in ("created_at",):
        if room.get(field):
            room[field] = datetime.fromisoformat(room[field])
    # Reconstruct player datetime fields
    for player_list_key in ("players", "waiting_players"):
        for p in room.get(player_list_key, []):
            for dt_field in ("disconnect_at", "exited_at"):
                if p.get(dt_field):
                    p[dt_field] = datetime.fromisoformat(p[dt_field])
    # status is stored as its .value string — RoomStatus is a str enum so it
    # round-trips fine; RoomResponse(status=...) accepts both.
    return room


# ── Service class ────────────────────────────────────────────────────────────

class RoomService:
    """Service for managing game rooms — all state in Redis."""

    # ── private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _room_key(room_id: str) -> str:
        return f"room:{room_id}"

    @staticmethod
    def _code_key(room_code: str) -> str:
        return f"room_code:{room_code}"

    async def _save_room(self, room: dict) -> None:
        """Persist room dict to Redis and refresh TTL."""
        room_id = room["room_id"]
        room_code = room.get("room_code", "")
        pipe = redis_client.pipeline()
        pipe.setex(self._room_key(room_id), KEY_TTL_SECONDS, _room_to_json(room))
        if room_code:
            pipe.setex(self._code_key(room_code), KEY_TTL_SECONDS, room_id)
        pipe.sadd("rooms:index", room_id)
        await pipe.execute()

    async def _load_room(self, room_id: str) -> Optional[dict]:
        """Load and deserialise room dict from Redis, or None if missing."""
        raw = await redis_client.get(self._room_key(room_id))
        if raw is None:
            return None
        return _room_from_json(raw)

    # ── internal room-code generation ────────────────────────────────────────

    async def _generate_room_code(self) -> str:
        """Generate a unique 6-character alphanumeric room code."""
        unambiguous_chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        for _ in range(1000):
            code = ''.join(random.choices(unambiguous_chars, k=6))
            # SETNX on the code key: if it returns 1 the code was free
            acquired = await redis_client.setnx(self._code_key(code), "__reserved__")
            if acquired:
                # Will be overwritten with real room_id once room is saved
                return code
        import time
        return f"{int(time.time()) % 1000000:06d}".replace('0', 'A').replace('1', 'B')

    # ── public API ───────────────────────────────────────────────────────────

    async def create_room(self, room_data: RoomCreate) -> RoomResponse:
        """Create a new room."""
        room_id = str(uuid.uuid4())
        room_code = await self._generate_room_code()

        room = {
            "room_id": room_id,
            "room_code": room_code,
            "room_name": room_data.room_name,
            "players": [],
            "waiting_players": [],
            "max_players": room_data.max_players,
            "game_mode": room_data.game_mode,
            "score_limit": room_data.score_limit,
            "creator_app_version": room_data.creator_app_version,
            "creator_build_number": room_data.creator_build_number,
            "host_machine_id": os.getenv("FLY_MACHINE_ID"),
            "status": RoomStatus.WAITING,
            "created_at": datetime.now(timezone.utc),
        }

        await self._save_room(room)

        log = get_room_logger(room_id)
        log.info("room_created", {
            "room_code": room_code,
            "room_name": room_data.room_name,
            "max_players": room_data.max_players,
            "game_mode": room_data.game_mode,
            "score_limit": room_data.score_limit,
            "creator_app_version": room_data.creator_app_version,
            "creator_build_number": room_data.creator_build_number,
        })

        return RoomResponse(**room)

    async def get_room(self, room_id: str) -> Optional[RoomResponse]:
        """Get room by ID."""
        room = await self._load_room(room_id)
        if room:
            return RoomResponse(**room)
        return None

    async def get_room_by_code(self, room_code: str) -> Optional[RoomResponse]:
        """Get room by 6-character code."""
        room_id = await redis_client.get(self._code_key(room_code.upper()))
        if room_id and room_id != "__reserved__":
            return await self.get_room(room_id)
        return None

    async def get_room_raw(self, room_id: str) -> Optional[dict]:
        """Return the raw room dict (for callers that need direct field access)."""
        return await self._load_room(room_id)

    async def player_exists(self, room_id: str, player_id: str) -> bool:
        """Check if a player exists in a room."""
        room = await self._load_room(room_id)
        if not room:
            return False
        in_players = any(p["player_id"] == player_id for p in room["players"])
        in_waiting = any(p["player_id"] == player_id for p in room.get("waiting_players", []))
        return in_players or in_waiting

    async def list_rooms(self, status: Optional[RoomStatus] = None) -> List[RoomResponse]:
        """List all rooms, optionally filtered by status."""
        room_ids = await redis_client.smembers("rooms:index")
        rooms = []
        for room_id in room_ids:
            room = await self._load_room(room_id)
            if room is None:
                # TTL expired — clean up the index lazily
                await redis_client.srem("rooms:index", room_id)
                continue
            if status and room.get("status") != status and room.get("status") != status.value:
                continue
            rooms.append(RoomResponse(**room))
        return rooms

    async def add_player(
        self,
        room_id: str,
        player_id: str,
        player_name: str,
        is_connected: bool = False,
        is_admin: bool = False,
        avatar_seed: Optional[str] = None,
    ) -> bool:
        """Add a player to a room."""
        room = await self._load_room(room_id)
        if not room:
            return False

        total_players = len(room["players"]) + len(room.get("waiting_players", []))
        if total_players >= room["max_players"]:
            get_room_logger(room_id).warn("player_join_rejected", {
                "player_id": player_id,
                "player_name": player_name,
                "reason": "room_full",
                "max_players": room["max_players"],
            })
            return False

        if any(p["player_id"] == player_id for p in room["players"]):
            return False

        if len(room["players"]) == 0 and len(room.get("waiting_players", [])) == 0:
            is_admin = True

        player = {
            "player_id": player_id,
            "player_name": player_name,
            "is_ready": False,
            "is_connected": is_connected,
            "is_admin": is_admin,
            "is_in_game": False,
            "avatar_seed": avatar_seed,
            "disconnect_at": None,
            "is_exited": False,
            "exited_at": None,
        }

        if room.get("status") not in (RoomStatus.WAITING, RoomStatus.WAITING.value):
            if "waiting_players" not in room:
                room["waiting_players"] = []
            room["waiting_players"].append(player)
            get_room_logger(room_id).info("player_joined_waiting", {
                "player_id": player_id,
                "player_name": player_name,
            })
        else:
            room["players"].append(player)
            get_room_logger(room_id).info("player_joined", {
                "player_id": player_id,
                "player_name": player_name,
                "is_admin": is_admin,
                "is_connected": is_connected,
                "avatar_seed": avatar_seed,
                "total_players": len(room["players"]),
            })

        await self._save_room(room)
        return True

    async def remove_player(self, room_id: str, target_player_id: str, admin_player_id: str) -> bool:
        """Remove a player from a room (admin only)."""
        room = await self._load_room(room_id)
        if not room:
            return False

        log = get_room_logger(room_id)

        admin_player = next((p for p in room["players"] if p["player_id"] == admin_player_id), None)
        if not admin_player or not admin_player.get("is_admin", False):
            log.warn("player_remove_rejected", {
                "target_player_id": target_player_id,
                "requested_by": admin_player_id,
                "reason": "not_admin",
            })
            return False

        if target_player_id == admin_player_id:
            log.warn("player_remove_rejected", {
                "target_player_id": target_player_id,
                "requested_by": admin_player_id,
                "reason": "cannot_remove_self",
            })
            return False

        target_player = next((p for p in room["players"] + room.get("waiting_players", []) if p["player_id"] == target_player_id), None)
        if not target_player:
            return False

        # Instead of completely removing the player if the game is active, just mark them exited.
        # This preserves their index in room["players"] so gs.player_hands[idx] still matches.
        is_playing = room.get("status") in ("playing", "finished")
        
        if is_playing:
            for p in room["players"]:
                if p["player_id"] == target_player_id:
                    p["is_exited"] = True
                    p["exited_at"] = datetime.now(timezone.utc).isoformat()
            
            # waiting_players can always be safely removed
            room["waiting_players"] = [p for p in room.get("waiting_players", []) if p["player_id"] != target_player_id]
        else:
            room["players"] = [p for p in room["players"] if p["player_id"] != target_player_id]
            room["waiting_players"] = [p for p in room.get("waiting_players", []) if p["player_id"] != target_player_id]

        if len([p for p in room["players"] if not p.get("is_exited")]) == 0:
            await self.delete_room(room_id)
            return True

        log.info("player_removed_by_admin", {
            "target_player_id": target_player_id,
            "target_player_name": target_player.get("player_name"),
            "admin_player_id": admin_player_id,
            "remaining_players": len(room["players"]),
        })

        await self._save_room(room)
        return True

    async def transfer_admin(self, room_id: str, old_admin_id: Optional[str] = None) -> Optional[str]:
        """Transfer admin to the next active player when admin leaves."""
        room = await self._load_room(room_id)
        if not room or not room["players"]:
            return None

        log = get_room_logger(room_id)

        for player in room["players"]:
            player["is_admin"] = False

        for player in room["players"]:
            if player.get("player_id") != old_admin_id and player.get("is_connected", False):
                player["is_admin"] = True
                log.info("admin_transferred", {
                    "from_player_id": old_admin_id,
                    "to_player_id": player["player_id"],
                    "to_player_name": player["player_name"],
                    "reason": "connected_player_found",
                })
                await self._save_room(room)
                return player["player_id"]

        for player in room["players"]:
            if player.get("player_id") != old_admin_id:
                player["is_admin"] = True
                log.info("admin_transferred", {
                    "from_player_id": old_admin_id,
                    "to_player_id": player["player_id"],
                    "to_player_name": player["player_name"],
                    "reason": "fallback_no_connected_players",
                })
                await self._save_room(room)
                return player["player_id"]

        for player in room["players"]:
            if player.get("player_id") == old_admin_id:
                player["is_admin"] = True
                log.info("admin_retained", {
                    "player_id": old_admin_id,
                    "reason": "only_player_in_room",
                })
                await self._save_room(room)
                return player["player_id"]

        return None

    async def set_player_ready(self, room_id: str, player_id: str, is_ready: bool) -> bool:
        """Set player ready status."""
        room = await self._load_room(room_id)
        if not room:
            return False

        all_players = room["players"] + room.get("waiting_players", [])
        for player in all_players:
            if player["player_id"] == player_id:
                old_status = player.get("is_ready", False)
                player["is_ready"] = is_ready
                if old_status != is_ready:
                    get_room_logger(room_id).info("player_ready_changed", {
                        "player_id": player_id,
                        "player_name": player.get("player_name"),
                        "is_ready": is_ready,
                    })
                await self._save_room(room)
                return True
        return False

    async def reset_all_players_ready(self, room_id: str) -> bool:
        """Set is_ready to False for all players in the room."""
        room = await self._load_room(room_id)
        if not room:
            return False

        for player in room["players"] + room.get("waiting_players", []):
            player["is_ready"] = False

        get_room_logger(room_id).info("all_players_ready_reset", {})
        await self._save_room(room)
        return True

    async def set_player_connected(self, room_id: str, player_id: str, is_connected: bool) -> bool:
        """Set player connection status."""
        room = await self._load_room(room_id)
        if not room:
            return False

        all_players = room["players"] + room.get("waiting_players", [])
        for player in all_players:
            if player["player_id"] == player_id:
                old_status = player.get("is_connected", False)
                player["is_connected"] = is_connected
                if is_connected:
                    player["disconnect_at"] = None
                if old_status != is_connected:
                    get_room_logger(room_id).info("player_connection_changed", {
                        "player_id": player_id,
                        "player_name": player.get("player_name"),
                        "was_connected": old_status,
                        "is_connected": is_connected,
                    })
                await self._save_room(room)
                return True

        get_room_logger(room_id).warn("player_not_found_for_connection_update", {
            "player_id": player_id,
            "is_connected": is_connected,
        })
        return False

    async def set_player_disconnected_at(self, room_id: str, player_id: str, ts) -> bool:
        """Record disconnect timestamp for a player (pass None to clear)."""
        room = await self._load_room(room_id)
        if not room:
            return False
        all_players = room["players"] + room.get("waiting_players", [])
        for player in all_players:
            if player["player_id"] == player_id:
                player["disconnect_at"] = ts
                await self._save_room(room)
                return True
        return False

    async def set_player_exited(self, room_id: str, player_id: str, is_exited: bool) -> bool:
        """Permanently mark a player as exited."""
        room = await self._load_room(room_id)
        if not room:
            return False
        all_players = room["players"] + room.get("waiting_players", [])
        for player in all_players:
            if player["player_id"] == player_id:
                player["is_exited"] = is_exited
                player["exited_at"] = datetime.now(timezone.utc) if is_exited else None
                get_room_logger(room_id).info("player_exited_status_changed", {
                    "player_id": player_id,
                    "player_name": player.get("player_name"),
                    "is_exited": is_exited,
                })
                await self._save_room(room)
                return True
        return False

    async def get_exited_indices(self, room_id: str) -> list:
        """Return list of player indices where is_exited = True."""
        room = await self._load_room(room_id)
        if not room:
            return []
        return [
            idx for idx, p in enumerate(room["players"])
            if p.get("is_exited", False)
        ]

    async def reset_exited_players(self, room_id: str) -> bool:
        """Clear is_exited / exited_at / is_in_game for all players.

        Called on full game reset (handle_reset_game) to ensure no stale
        is_in_game=True lingers into the next game's lobby phase.
        Note: handle_update_room_status_to_waiting (tournament round transition)
        intentionally does NOT call this so exited flags survive across rounds.
        """
        room = await self._load_room(room_id)
        if not room:
            return False
        # When a Single Game is reset (Play Again), completely remove any exited players
        # so they don't return as ghosts taking up slots.
        # Note: This is called by handle_reset_game (which is for Single Games or 
        # full tournament restarts). It is NOT called between tournament rounds.
        room["players"] = [p for p in room["players"] if not p.get("is_exited")]
        room["waiting_players"] = [p for p in room.get("waiting_players", []) if not p.get("is_exited")]

        for player in room["players"] + room.get("waiting_players", []):
            player["is_exited"] = False
            player["exited_at"] = None
            player["disconnect_at"] = None
            player["is_in_game"] = False  # Clear stale in_game flag from previous game
        get_room_logger(room_id).info("exited_players_reset", {})
        await self._save_room(room)
        return True

    async def set_player_in_game(self, room_id: str, player_id: str, is_in_game: bool) -> bool:
        """Set player is_in_game status."""
        room = await self._load_room(room_id)
        if not room:
            return False

        all_players = room["players"] + room.get("waiting_players", [])
        for player in all_players:
            if player["player_id"] == player_id:
                old_status = player.get("is_in_game", False)
                player["is_in_game"] = is_in_game
                if old_status != is_in_game:
                    get_room_logger(room_id).info("player_in_game_changed", {
                        "player_id": player_id,
                        "player_name": player.get("player_name"),
                        "was_in_game": old_status,
                        "is_in_game": is_in_game,
                    })
                await self._save_room(room)
                return True

        get_room_logger(room_id).warn("player_not_found_for_in_game_update", {
            "player_id": player_id,
            "is_in_game": is_in_game,
        })
        return False

    async def all_players_in_game(self, room_id: str) -> bool:
        """Check if all players have is_in_game=True."""
        room = await self._load_room(room_id)
        if not room or len(room["players"]) < 2:
            return False
        return all(player.get("is_in_game", False) for player in room["players"])

    async def update_room_status(self, room_id: str, status: RoomStatus) -> bool:
        """Update room status."""
        room = await self._load_room(room_id)
        if not room:
            return False

        old_status = room.get("status")
        room["status"] = status
        get_room_logger(room_id).info("room_status_changed", {
            "from_status": str(old_status),
            "to_status": str(status),
        })
        await self._save_room(room)
        return True

    async def has_connected_players(self, room_id: str) -> bool:
        """Check if room has any connected players."""
        room = await self._load_room(room_id)
        if not room:
            return False
        return any(p.get("is_connected", False) for p in room["players"])

    async def delete_room(self, room_id: str) -> bool:
        """Delete a room and its code mapping."""
        room = await self._load_room(room_id)
        if not room:
            return False

        room_code = room.get("room_code")
        player_count = len(room.get("players", []))

        get_room_logger(room_id).info("room_deleted", {
            "room_code": room_code,
            "total_players_at_deletion": player_count,
            "reason": "no_active_connections",
        })

        close_room_logger(room_id)

        pipe = redis_client.pipeline()
        pipe.delete(self._room_key(room_id))
        if room_code:
            pipe.delete(self._code_key(room_code))
        pipe.srem("rooms:index", room_id)
        await pipe.execute()

        return True

    async def merge_waiting_players(self, room_id: str) -> bool:
        """Merge waiting_players into main players list."""
        room = await self._load_room(room_id)
        if not room:
            return False

        if room.get("waiting_players"):
            room["players"].extend(room["waiting_players"])
            room["waiting_players"] = []
            get_room_logger(room_id).info("merged_waiting_players", {
                "total_players_now": len(room["players"]),
            })
            await self._save_room(room)
            return True
        return False

    async def update_player_fields(self, room_id: str, player_id: str, **fields) -> bool:
        """Update arbitrary fields on a player dict (used by join_room reconnect logic).
        
        Replaces the pattern: room_service._rooms.get(room_id) → player_dict["field"] = value
        """
        room = await self._load_room(room_id)
        if not room:
            return False
        all_players = room["players"] + room.get("waiting_players", [])
        player = next((p for p in all_players if p["player_id"] == player_id), None)
        if not player:
            return False
        for k, v in fields.items():
            player[k] = v
        await self._save_room(room)
        return True

    async def get_player_field(self, room_id: str, player_id: str, field: str):
        """Get a single field from a player dict."""
        room = await self._load_room(room_id)
        if not room:
            return None
        all_players = room["players"] + room.get("waiting_players", [])
        player = next((p for p in all_players if p["player_id"] == player_id), None)
        if not player:
            return None
        return player.get(field)

    async def cleanup_old_rooms(self, max_age_hours: float = 1.0) -> int:
        """Delete rooms that are older than max_age_hours.
        
        NOTE: With Redis TTL this is largely redundant, but kept for compatibility
        with the scheduled cleanup task.
        """
        now = datetime.now(timezone.utc)
        deleted_count = 0
        room_ids = await redis_client.smembers("rooms:index")
        for room_id in room_ids:
            room = await self._load_room(room_id)
            if room is None:
                await redis_client.srem("rooms:index", room_id)
                continue
            created_at = room.get("created_at")
            if created_at:
                if not created_at.tzinfo:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if (now - created_at).total_seconds() > max_age_hours * 3600:
                    await self.delete_room(room_id)
                    deleted_count += 1

        if deleted_count > 0:
            global_log.info("old_rooms_cleaned", {"deleted_count": deleted_count, "max_age_hours": max_age_hours})

        return deleted_count


# Global instance
room_service = RoomService()
