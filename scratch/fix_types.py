import os
import re

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

    # Make sure Optional is imported
    if "from typing import" in content and "Optional" not in content:
        content = re.sub(r'from typing import (.+)', r'from typing import Optional, \1', content, count=1)
    elif "from typing import" not in content and "import typing" not in content:
        # just add to top
        lines = content.split('\n')
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.startswith("import") or line.startswith("from"):
                insert_idx = i
                break
        lines.insert(insert_idx, "from typing import Optional")
        content = '\n'.join(lines)

    # Replace type | None with Optional[type]
    content = re.sub(r'([a-zA-Z0-9_\[\]]+)\s*\|\s*None', r'Optional[\1]', content)
    
    with open(file_path, "w") as f:
        f.write(content)
    print(f"Fixed types in {file_path}")
