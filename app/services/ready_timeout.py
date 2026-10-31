"""Public-lobby ready deadlines.

The clock starts only when at least half of the connected players are ready.
Each connected player who is still not ready then gets one deadline. It stays
until they tap Ready, fewer than half the room is ready, or the room is no
longer a public lobby. Disconnecting does not restart a deadline that is
already running.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import PUBLIC_MATCHMAKE_EXCLUDE_SECONDS, PUBLIC_READY_TIMEOUT_SECONDS
from app.schemas.room import RoomStatus, RoomType

READY_DEADLINE_FIELD = "ready_deadline_at"
READY_TIMEOUT_REASON = "ready_timeout"
READY_TIMEOUT_MESSAGE = (
    "You were removed because you did not tap Ready in time."
)


def _is_public(room: dict) -> bool:
    room_type = room.get("room_type")
    return room_type in (RoomType.PUBLIC, RoomType.PUBLIC.value, "public")


def _is_waiting(room: dict) -> bool:
    status = room.get("status")
    return status in (RoomStatus.WAITING, RoomStatus.WAITING.value, "waiting")


def _as_utc(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _half_the_players_are_ready(players: list) -> bool:
    """True when ready connected players are at least half of the connected seats."""
    connected = [
        player
        for player in players
        if player.get("is_connected") and not player.get("is_exited")
    ]
    if len(connected) < 2:
        return False
    ready = sum(1 for player in connected if player.get("is_ready"))
    return ready * 2 >= len(connected)


def apply_ready_deadlines(
    room: dict,
    now: datetime,
    timeout_seconds: int = PUBLIC_READY_TIMEOUT_SECONDS,
) -> bool:
    """Arm or clear ready_deadline_at on the room's players. Returns True if changed."""
    people = list(room.get("players") or []) + list(room.get("waiting_players") or [])
    half_ready = (
        _is_public(room)
        and _is_waiting(room)
        and _half_the_players_are_ready(people)
    )
    changed = False
    for key in ("players", "waiting_players"):
        if key not in room:
            continue
        updated = []
        for player in room.get(key) or []:
            new_player = dict(player)
            clock_running = (
                half_ready
                and not player.get("is_exited")
                and not player.get("is_ready")
            )
            should_arm = clock_running and player.get("is_connected")
            current = player.get(READY_DEADLINE_FIELD)
            if should_arm and not current:
                deadline = now + timedelta(seconds=timeout_seconds)
                new_player[READY_DEADLINE_FIELD] = deadline.isoformat()
                changed = True
            elif current and not clock_running:
                new_player[READY_DEADLINE_FIELD] = None
                changed = True
            updated.append(new_player)
        room[key] = updated
    return changed


def expired_unready_player_ids(players: list, now: datetime) -> list[str]:
    """Connected, unready players whose ready deadline has passed."""
    expired: list[str] = []
    for player in players:
        if player.get("is_ready") or not player.get("is_connected") or player.get("is_exited"):
            continue
        deadline = _as_utc(player.get(READY_DEADLINE_FIELD))
        if deadline is not None and now >= deadline:
            expired.append(player["player_id"])
    return expired


def matchmaking_block_reason(
    room: dict,
    now: datetime,
    exclude_room_ids: Optional[set] = None,
) -> Optional[str]:
    """Why this public room must not be offered, or None if it is still open."""
    excluded = exclude_room_ids or set()
    room_id = room.get("room_id")
    if room_id and room_id in excluded:
        return "recently_left"
    players = list(room.get("players") or []) + list(room.get("waiting_players") or [])
    if expired_unready_player_ids(players, now):
        return "ready_deadline_expired"
    return None


def exclude_ttl_seconds() -> int:
    return PUBLIC_MATCHMAKE_EXCLUDE_SECONDS
