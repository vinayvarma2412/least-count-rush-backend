#!/bin/bash
# Startup script for Least Count Rush Backend on two ports

# Load .env variables first so command-line flags can override them
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

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

# Check if we need to start Redis locally
# Default to localhost if REDIS_URL isn't explicitly set to an external service
if [[ -z "$REDIS_URL" || "$REDIS_URL" == *"localhost"* || "$REDIS_URL" == *"127.0.0.1"* ]]; then
    echo "Ensuring local Redis container is running..."
    if docker ps -a --format '{{.Names}}' | grep -Eq "^least_count_redis\$"; then
        docker start least_count_redis > /dev/null
    else
        docker run -d --name least_count_redis -p 6379:6379 redis:7 > /dev/null
    fi
    export REDIS_URL="redis://localhost:6379"
fi

# Parse arguments
USE_TEST_DB="false"
POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
  case $1 in
    --test-db)
      USE_TEST_DB="true"
      shift
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done

if [ "$USE_TEST_DB" = "true" ]; then
    echo "Starting local PostgreSQL database for testing..."
    # Check if container exists
    if docker ps -a --format '{{.Names}}' | grep -Eq "^least_count_test_db\$"; then
        echo "Starting existing least_count_test_db container..."
        docker start least_count_test_db > /dev/null
    else
        echo "Creating new least_count_test_db container..."
        docker run -d --name least_count_test_db -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=postgres -e POSTGRES_DB=least_count_test postgres:15-alpine > /dev/null
    fi
    
    # Wait for DB to be ready
    echo "Waiting for database to be ready..."
    until docker exec least_count_test_db pg_isready -U postgres > /dev/null 2>&1; do
        sleep 1
    done
    sleep 2 # Extra buffer to ensure it is fully ready to accept connections
    
    export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/least_count_test"
    echo "Swapped to local test database: $DATABASE_URL"
fi

PORT1=${POSITIONAL_ARGS[0]:-8000}
PORT2=${POSITIONAL_ARGS[1]:-8001}

LOG_LEVEL_LOWER=$(echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')

echo "Starting Server 1 on port $PORT1..."
"$UVICORN_CMD" app.main:app --reload --host 0.0.0.0 --port $PORT1 --log-level "$LOG_LEVEL_LOWER" &
PID1=$!

echo "Starting Server 2 on port $PORT2..."
"$UVICORN_CMD" app.main:app --reload --host 0.0.0.0 --port $PORT2 --log-level "$LOG_LEVEL_LOWER" &
PID2=$!

# Function to handle termination
cleanup() {
    echo "Stopping servers..."
    kill $PID1 $PID2 2>/dev/null
    wait $PID1 $PID2 2>/dev/null
    echo "Servers stopped."
    exit 0
}

# Trap SIGINT and SIGTERM
trap cleanup SIGINT SIGTERM

echo "=================================================="
echo "Both servers are running:"
echo " - Server 1: http://localhost:$PORT1"
echo " - Server 2: http://localhost:$PORT2"
echo "Press Ctrl+C to stop both servers."
echo "=================================================="

wait
