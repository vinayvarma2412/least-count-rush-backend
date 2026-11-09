"""Best-server handoff.

Ready starts a latency check, then everyone reconnects to the chosen server.
The old socket closing during that move is not a leave. Treating it as one
removed the bot from public rooms and left the phone inactive.
"""

REPLACED = "replaced"
SWITCHING = "switching"

# Long enough for the phone to drop its socket, open the new one, and join.
# The app waits about two seconds between those steps.
HANDOFF_GRACE_SECONDS = 20


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
