import os

files_to_fix = [
    "app/services/game_service.py",
    "app/services/pg_game_stats_service.py",
    "app/api/websocket/game_ws.py",
    "app/api/routes/users.py",
    "app/api/routes/user_game_stats.py"
]

for file_path in files_to_fix:
    with open(file_path, "r") as f:
        content = f.read()
    
    if "from __future__ import annotations" not in content:
        # insert after docstring or at top
        lines = content.split("\n")
        insert_idx = 0
        in_docstring = False
        for i, line in enumerate(lines):
            if line.startswith('"""') or line.startswith("'''"):
                if not in_docstring:
                    in_docstring = True
                    if line.count('"""') == 2 or line.count("'''") == 2:
                        in_docstring = False
                        insert_idx = i + 1
                        break
                else:
                    in_docstring = False
                    insert_idx = i + 1
                    break
            elif not in_docstring and line.strip() != "":
                insert_idx = i
                break
        
        lines.insert(insert_idx, "from __future__ import annotations")
        
        with open(file_path, "w") as f:
            f.write("\n".join(lines))
        print(f"Fixed {file_path}")

