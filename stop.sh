#!/bin/bash
# Stop script for Least Count Rush Backend

echo "Stopping Least Count Rush Backend..."

# Find process running on port 8000
PIDS=$(lsof -t -i:8000)

if [ -z "$PIDS" ]; then
    echo "No process found running on port 8000."
    
    # Try finding by process name as a fallback
    PIDS=$(pgrep -f "uvicorn.*app.main:app")
    
    if [ -z "$PIDS" ]; then
        echo "Backend is already stopped."
        exit 0
    fi
fi

# Convert newlines to spaces for echo
PIDS_FORMATTED=$(echo $PIDS | tr '\n' ' ')
echo "Found backend process(es) with PID(s): $PIDS_FORMATTED"
echo "Stopping process(es)..."

# Terminate gracefully first
for PID in $PIDS; do
    kill -15 $PID 2>/dev/null
done

# Wait a moment for graceful shutdown
sleep 2

# Force kill any remaining processes
for PID in $PIDS; do
    if ps -p $PID > /dev/null; then
        echo "Force killing PID $PID..."
        kill -9 $PID 2>/dev/null
    fi
done

echo "Backend stopped successfully."
