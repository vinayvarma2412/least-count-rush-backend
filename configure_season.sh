#!/bin/bash

# Get the directory of the script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
else
    echo "Virtual environment not found. Make sure you have created one (.venv)."
    exit 1
fi

# Add the current directory to PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Run the python script and pass all arguments
python scripts/configure_season.py "$@"
