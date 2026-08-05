#!/bin/bash
# Script to clear/wipe the database using the python clean script with backend virtualenv.

# Get the directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Determine which python interpreter to use
PYTHON_CMD="python3"

if [ -d "$SCRIPT_DIR/.venv" ]; then
    if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
        PYTHON_CMD="$SCRIPT_DIR/.venv/bin/python"
    fi
elif [ -d "$SCRIPT_DIR/venv" ]; then
    if [ -f "$SCRIPT_DIR/venv/bin/python" ]; then
        PYTHON_CMD="$SCRIPT_DIR/venv/bin/python"
    fi
fi

# Execute the clear_db.py script with all arguments passed to this script
"$PYTHON_CMD" "$SCRIPT_DIR/scripts/clear_db.py" "$@"
