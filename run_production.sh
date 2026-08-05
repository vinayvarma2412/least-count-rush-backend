#!/bin/bash
# Production startup script for Least Count Rush Backend
# This script is used by systemd service (no --reload flag)

# Determine which uvicorn to use
UVICORN_CMD="uvicorn"

# Use virtual environment's uvicorn if it exists
if [ -d ".venv" ]; then
    if [ -f ".venv/bin/uvicorn" ]; then
        UVICORN_CMD=".venv/bin/uvicorn"
    else
        source .venv/bin/activate
    fi
elif [ -d "venv" ]; then
    if [ -f "venv/bin/uvicorn" ]; then
        UVICORN_CMD="venv/bin/uvicorn"
    else
        source venv/bin/activate
    fi
fi

# Check if uvicorn is available
if ! command -v "$UVICORN_CMD" &> /dev/null && [ ! -f "$UVICORN_CMD" ]; then
    echo "Error: uvicorn is not installed."
    echo "Please install dependencies by running:"
    if [ -d ".venv" ]; then
        echo "  .venv/bin/pip install -r requirements.txt"
    elif [ -d "venv" ]; then
        echo "  venv/bin/pip install -r requirements.txt"
    else
        echo "  pip install -r requirements.txt"
    fi
    exit 1
fi

# Get port from environment variable (Cloud Run uses PORT, default to 8000)
PORT=${PORT:-8000}

# Run the FastAPI server (production mode - no reload)
"$UVICORN_CMD" app.main:app --host 0.0.0.0 --port $PORT












