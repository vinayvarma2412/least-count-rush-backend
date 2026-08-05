#!/bin/bash
# Startup script for Least Count Rush Backend

# Get current device network IP address and update api_config.dart
get_network_ip() {
    local ip=""
    
    # Try macOS method first
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # Try en0 (WiFi) first, then en1 (Ethernet)
        ip=$(ipconfig getifaddr en0 2>/dev/null)
        if [ -z "$ip" ]; then
            ip=$(ipconfig getifaddr en1 2>/dev/null)
        fi
    fi
    
    # Try Linux method if macOS didn't work or on Linux
    if [ -z "$ip" ]; then
        # Try hostname -I first (works on most Linux distros)
        ip=$(hostname -I 2>/dev/null | awk '{print $1}')
        # If that fails, try ip route method
        if [ -z "$ip" ]; then
            ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7}' | head -1)
        fi
        # If that fails, try ifconfig method
        if [ -z "$ip" ]; then
            ip=$(ifconfig 2>/dev/null | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | head -1 | sed 's/addr://')
        fi
    fi
    
    echo "$ip"
}

# Update API config with current IP
update_api_config() {
    local current_ip=$(get_network_ip)
    local api_config_file="../lib/app/config/api_config.dart"
    
    if [ -z "$current_ip" ]; then
        echo "Warning: Could not detect network IP address. Skipping api_config.dart update."
        return
    fi
    
    if [ ! -f "$api_config_file" ]; then
        echo "Warning: api_config.dart not found at $api_config_file. Skipping update."
        return
    fi
    
    # Use sed to replace the IP address in the return statement
    # Pattern: replace IP address in 'http://XXX.XXX.XXX.XXX:8000'
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS uses BSD sed, requires different syntax
        sed -i '' "s|return 'http://[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}:8000'|return 'http://${current_ip}:8000'|g" "$api_config_file"
    else
        # Linux uses GNU sed
        sed -i "s|return 'http://[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}:8000'|return 'http://${current_ip}:8000'|g" "$api_config_file"
    fi
    
    echo "Updated api_config.dart with IP address: $current_ip"
}

# Update the API config before starting the server
update_api_config

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

# Load .env variables first so command-line flags can override them
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Parse arguments
USE_TEST_DB="false"
for arg in "$@"; do
    if [ "$arg" = "--test-db" ]; then
        USE_TEST_DB="true"
    fi
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

# Run the FastAPI server
LOG_LEVEL_LOWER=$(echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')
"$UVICORN_CMD" app.main:app --reload --host 0.0.0.0 --port 8000 --log-level "$LOG_LEVEL_LOWER"
