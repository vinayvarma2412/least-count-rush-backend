#!/bin/bash

# Exit on error
set -e

# Move to the root directory of the backend
cd "$(dirname "$0")/.."

# Load environment variables from .env
if [ -f .env ]; then
    # Parse .env ignoring comments and empty lines
    export $(grep -v '^#' .env | xargs)
fi

if [ -z "$DATABASE_URL" ]; then
    echo "Error: DATABASE_URL is not set in .env"
    exit 1
fi

# Convert postgresql+asyncpg:// to postgresql:// so pg_dump can use it
SYNC_DB_URL="${DATABASE_URL/+asyncpg/}"

# Create backups directory if it doesn't exist
mkdir -p backups

# Generate backup filename with current date and time
BACKUP_FILE="backups/db_backup_$(date +%Y-%m-%d_%H-%M-%S).sql"

echo "Starting database backup..."
# Create a logical backup
pg_dump "$SYNC_DB_URL" --no-owner --no-privileges --clean --if-exists > "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    echo "✅ Backup successfully created: $BACKUP_FILE"
else
    echo "❌ Backup failed!"
    rm -f "$BACKUP_FILE"
    exit 1
fi
