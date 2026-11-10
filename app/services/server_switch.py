"""Best-server handoff.

Ready starts a latency check, then everyone reconnects to the chosen server.
The old socket closing during that move is not a leave. Treating it as one
removed the bot from public rooms and left the phone inactive.
"""
import json

REPLACED = "replaced"
SWITCHING = "switching"

# Long enough for the phone to drop its socket, open the new one, and join.
# The app waits about two seconds between those steps.
HANDOFF_GRACE_SECONDS = 20


def parse_server_list(configured) -> list[str]:
    """Absolute URLs from a Remote Config ``servers_list`` value.

    The value is usually a JSON string. A list that was already decoded is
    accepted too, so a non-string payload is not treated as "list missing".
    """
    if configured is None or configured == "":
        return []
    values = configured
    if isinstance(configured, str):
        try:
            values = json.loads(configured)
        except json.JSONDecodeError:
            return []
    if isinstance(values, dict):
        values = values.get("servers") or values.get("urls") or []
    if not isinstance(values, list):
        return []
    urls: list[str] = []
    for value in values:
        raw = value.get("url") if isinstance(value, dict) else value
        if not isinstance(raw, str):
            continue
        url = raw.strip().rstrip("/")
        if url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)
    return urls


def selection_candidates(configured_urls: list[str], current_server_url: str | None) -> list[str]:
    """Servers to measure, always including the one this room is already on.

    Remote Config can be missing, or it can list other regions only. The
    room's own URL stays in the set so Ready does not report that the
    selection list was not found and does not leave a reachable server behind.
    """
    urls: list[str] = []
    for url in [*configured_urls, current_server_url or ""]:
        cleaned = url.strip().rstrip("/")
        if cleaned.startswith(("http://", "https://")) and cleaned not in urls:
            urls.append(cleaned)
    return urls


def handoff_kind(
    player_id: str,
    closing_connection_id: str | None,
    current_connection_id: str | None,
    selection_state: dict | None,
) -> str | None:
    """Why this socket close should not change the seat, or None if it is a real leave.

    ``replaced`` means a newer socket already joined, so this close is the old one.
    ``switching`` means best-server selection is still waiting for this player.
    """
    if not player_id:
        return None
    if (
        closing_connection_id
        and current_connection_id
        and closing_connection_id != current_connection_id
    ):
        return REPLACED
    if not isinstance(selection_state, dict) or selection_state.get("started"):
        return None
    expected = selection_state.get("expected_players") or []
    if player_id in expected:
        return SWITCHING
    return None
