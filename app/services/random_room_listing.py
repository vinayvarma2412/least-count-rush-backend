"""Which public rooms belong on the Random Rooms screen."""
from __future__ import annotations

from datetime import datetime

from app.schemas.room import RoomStatus, RoomType
from app.services.ready_timeout import expired_unready_player_ids


def _is_public(room: dict) -> bool:
    room_type = room.get("room_type")
    return room_type in (RoomType.PUBLIC, RoomType.PUBLIC.value, "public")


def _is_waiting(room: dict) -> bool:
    status = room.get("status")
    return status in (RoomStatus.WAITING, RoomStatus.WAITING.value, "waiting")


def _all_players(room: dict) -> list:
    return list(room.get("players") or []) + list(room.get("waiting_players") or [])


def is_open_random_room(room: dict, now: datetime, exclude_room_ids=None) -> bool:
    """A waiting public room with a free seat.

    Rooms stay listed after someone leaves, including when the remaining
    players are disconnected or marked exited. ``exclude_room_ids`` is ignored.
    """
    del exclude_room_ids
    if not _is_public(room) or not _is_waiting(room):
        return False
    people = _all_players(room)
    if not people:
        return False
    if expired_unready_player_ids(people, now):
        return False
    active = [player for player in people if not player.get("is_exited")]
    return len(active) < room.get("max_players", 6)
