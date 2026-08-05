import re
import os

def main():
    # 1. Update conftest.py
    with open("tests/conftest.py", "r") as f:
        conftest = f.read()

    # Replace setup_two_player_game
    setup_orig = """def setup_two_player_game(rs: RoomService, gs: GameService):
    \"\"\"
    Create a room with two players (A at index 0, B at index 1),
    initialize the game and force a known hand/discard state.

    Returns:
        room_id, player_a_id, player_b_id
    \"\"\"
    room = rs.create_room(RoomCreate(max_players=2))
    room_id = room.room_id
    pid_a = "player-a"
    pid_b = "player-b"

    rs.add_player(room_id, pid_a, "Alice", is_connected=True)
    rs.add_player(room_id, pid_b, "Bob", is_connected=True)

    # Manually initialize game state instead of going through
    # game_service.initialize_game() so we can control card hands exactly.
    rs.update_room_status(room_id, RoomStatus.PLAYING)"""

    setup_new = """async def setup_two_player_game(rs: RoomService, gs: GameService):
    \"\"\"
    Create a room with two players (A at index 0, B at index 1),
    initialize the game and force a known hand/discard state.

    Returns:
        room_id, player_a_id, player_b_id
    \"\"\"
    room = await rs.create_room(RoomCreate(max_players=2))
    room_id = room.room_id
    pid_a = "player-a"
    pid_b = "player-b"

    await rs.add_player(room_id, pid_a, "Alice", is_connected=True)
    await rs.add_player(room_id, pid_b, "Bob", is_connected=True)

    # Manually initialize game state instead of going through
    # game_service.initialize_game() so we can control card hands exactly.
    await rs.update_room_status(room_id, RoomStatus.PLAYING)"""
    
    conftest = conftest.replace(setup_orig, setup_new)

    gs_assign_orig = """    gs._game_states[room_id] = game_state
    # Create turn context for player A (index 0) — standard turn-start state
    gs._create_turn_context(room_id)"""
    
    gs_assign_new = """    await gs._save_game(room_id, game_state)
    # Create turn context for player A (index 0) — standard turn-start state
    await gs._create_turn_context(room_id)"""
    
    conftest = conftest.replace(gs_assign_orig, gs_assign_new)

    sim_orig = """def simulate_disconnect(gs: GameService, rs: RoomService, room_id: str, player_id: str, player_index: int):
    \"\"\"
    Mirror what on_player_disconnect() does for game-state concerns:
      1. Mark player offline.
      2. Call rollback_turn_if_incomplete().
    Returns True if rollback was performed.
    \"\"\"
    rs.set_player_connected(room_id, player_id, False)
    return gs.rollback_turn_if_incomplete(room_id, player_index)"""
    
    sim_new = """async def simulate_disconnect(gs: GameService, rs: RoomService, room_id: str, player_id: str, player_index: int):
    \"\"\"
    Mirror what on_player_disconnect() does for game-state concerns:
      1. Mark player offline.
      2. Call rollback_turn_if_incomplete().
    Returns True if rollback was performed.
    \"\"\"
    await rs.set_player_connected(room_id, player_id, False)
    return await gs.rollback_turn_if_incomplete(room_id, player_index)"""

    conftest = conftest.replace(sim_orig, sim_new)

    with open("tests/conftest.py", "w") as f:
        f.write(conftest)


    # 2. Update test_disconnect_scenarios.py
    with open("tests/test_disconnect_scenarios.py", "r") as f:
        tests = f.read()
        
    tests = tests.replace("import pytest", "import pytest\nimport pytest_asyncio")
        
    game_fix_orig = """@pytest.fixture()
def game():
    \"\"\"
    Yield (room_id, pid_a, pid_b, game_service, room_service).
    Cleans up global singletons after each test.
    \"\"\"
    rs, gs, originals = build_services()
    room_id, pid_a, pid_b = setup_two_player_game(rs, gs)
    yield room_id, pid_a, pid_b, gs, rs
    teardown_services(originals)"""
    
    game_fix_new = """@pytest_asyncio.fixture()
async def game():
    \"\"\"
    Yield (room_id, pid_a, pid_b, game_service, room_service).
    Cleans up global singletons after each test.
    \"\"\"
    from app.services.redis_client import redis_client
    # Connect to redis
    rs, gs, originals = build_services()
    room_id, pid_a, pid_b = await setup_two_player_game(rs, gs)
    yield room_id, pid_a, pid_b, gs, rs
    teardown_services(originals)"""
    
    tests = tests.replace(game_fix_orig, game_fix_new)
    
    # Update helpers
    h_orig = """def _hand_a(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    return gs._game_states[room_id].player_hands[0]

def _discard_top(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    return gs._game_states[room_id].discard_pile[-1]

def _turn_context(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    return gs._game_states[room_id].turn_context

def _current_turn(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    return gs._game_states[room_id].current_turn

def _action_seq(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    return gs._game_states[room_id].action_seq"""
    
    h_new = """async def _hand_a(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    state = await gs.get_game_state(room_id)
    return state.player_hands[0]

async def _discard_top(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    state = await gs.get_game_state(room_id)
    return state.discard_pile[-1]

async def _turn_context(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    state = await gs.get_game_state(room_id)
    return state.turn_context

async def _current_turn(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    state = await gs.get_game_state(room_id)
    return state.current_turn

async def _action_seq(game_fixture):
    room_id, _, _, gs, _ = game_fixture
    state = await gs.get_game_state(room_id)
    return state.action_seq"""
    
    tests = tests.replace(h_orig, h_new)
    
    # Add @pytest.mark.asyncio and make test functions async
    tests = re.sub(r"    def test_", "    @pytest.mark.asyncio\n    async def test_", tests)
    
    # Replace calls to helpers and service methods with awaits
    tests = re.sub(r"_hand_a\(game\)", "await _hand_a(game)", tests)
    tests = re.sub(r"_discard_top\(game\)", "await _discard_top(game)", tests)
    tests = re.sub(r"_turn_context\(game\)", "await _turn_context(game)", tests)
    tests = re.sub(r"_current_turn\(game\)", "await _current_turn(game)", tests)
    tests = re.sub(r"_action_seq\(game\)", "await _action_seq(game)", tests)
    tests = re.sub(r"simulate_disconnect\(", "await simulate_disconnect(", tests)
    
    tests = re.sub(r"gs\.pick_card\(", "await gs.pick_card(", tests)
    tests = re.sub(r"gs\.discard_cards\(", "await gs.discard_cards(", tests)
    tests = re.sub(r"gs\.step_ack\(", "await gs.step_ack(", tests)
    tests = re.sub(r"gs\.show_cards\(", "await gs.show_cards(", tests)
    tests = re.sub(r"gs\._advance_turn\(", "await gs._advance_turn(", tests)
    tests = re.sub(r"gs\.rollback_turn_if_incomplete\(", "await gs.rollback_turn_if_incomplete(", tests)
    
    with open("tests/test_disconnect_scenarios.py", "w") as f:
        f.write(tests)
        
    print("Test refactor script complete")

if __name__ == "__main__":
    main()
