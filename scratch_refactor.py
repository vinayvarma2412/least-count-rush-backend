import re
import sys

def main():
    file_path = "/Volumes/ssd/Projects/Flutter/least_count_backend/app/api/websocket/room_ws.py"
    with open(file_path, "r") as f:
        content = f.read()

    # Replacements for room_service and game_service method calls
    methods_to_await = [
        r"room_service\.get_room\(",
        r"room_service\.delete_room\(",
        r"room_service\.set_player_exited\(",
        r"room_service\.set_player_connected\(",
        r"room_service\.set_player_disconnected_at\(",
        r"room_service\.has_connected_players\(",
        r"room_service\.player_exists\(",
        r"room_service\.add_player\(",
        r"room_service\.set_player_ready\(",
        r"room_service\.update_room_status\(",
        r"room_service\.reset_exited_players\(",
        r"room_service\.set_player_in_game\(",
        r"room_service\.remove_player\(",
        r"room_service\.transfer_admin\(",
        r"room_service\.merge_waiting_players\(",
        r"room_service\.reset_all_players_ready\(",
        r"game_service\.get_game_state\(",
        r"game_service\._advance_turn\(",
        r"game_service\.rollback_turn_if_incomplete\(",
        r"game_service\.clear_game\(",
    ]

    for method in methods_to_await:
        # Avoid double await
        content = re.sub(r"(?<!await\s)" + method, "await " + method.replace("\\", ""), content)

    # Manual fixes for direct dict accesses
    # In handle_join_room:
    
    dict_access_1 = """            # Double-check raw dict and force is_connected=True if needed
            room_dict = room_service._rooms.get(room_id)
            if room_dict:
                player_dict = next((p for p in room_dict["players"] if p["player_id"] == player_id), None)
                if player_dict and not player_dict.get('is_connected'):
                    log.warn("join_room_is_connected_forced_true", {"player_id": player_id})
                    player_dict['is_connected'] = True"""

    replacement_1 = """            # Force is_connected=True if needed
            await room_service.update_player_fields(room_id, player_id, is_connected=True)"""

    content = content.replace(dict_access_1, replacement_1)

    dict_access_2 = """        room_dict = room_service._rooms.get(room_id)
        if room_dict:
            all_players = room_dict.get("players", []) + room_dict.get("waiting_players", [])
            player_dict = next((p for p in all_players if p["player_id"] == player_id), None)
            if player_dict:
                if player_name:
                    player_dict["player_name"] = player_name
                if avatar_seed is not None:
                    player_dict["avatar_seed"] = avatar_seed"""
                    
    replacement_2 = """        updates = {}
        if player_name:
            updates["player_name"] = player_name
        if avatar_seed is not None:
            updates["avatar_seed"] = avatar_seed
        if updates:
            await room_service.update_player_fields(room_id, player_id, **updates)"""

    content = content.replace(dict_access_2, replacement_2)
    
    dict_access_3 = """        # Force is_connected=True if still not set
        room_dict = room_service._rooms.get(room_id)
        if room_dict:
            all_players = room_dict.get("players", []) + room_dict.get("waiting_players", [])
            player_dict = next((p for p in all_players if p["player_id"] == player_id), None)
            if player_dict and not player_dict.get('is_connected'):
                log.warn("join_room_reconnect_is_connected_forced_true", {"player_id": player_id})
                player_dict['is_connected'] = True"""
                
    replacement_3 = """        # Force is_connected=True if still not set
        await room_service.update_player_fields(room_id, player_id, is_connected=True)"""
        
    content = content.replace(dict_access_3, replacement_3)
    
    # In check_all_players_connection_status:
    #                 updated_room = room_service.get_room(room_id) -> already replaced by regex
    
    # In handle_join_room: room_service.add_player is now awaited
    # connected_result = room_service.set_player_connected... -> already replaced by regex
    
    with open(file_path, "w") as f:
        f.write(content)

    print("Success")

if __name__ == "__main__":
    main()
