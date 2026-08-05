#!/bin/bash
# Script to update the Least Count Rush Backend on a DigitalOcean Droplet

if [ -z "$1" ]; then
    echo "Usage: ./update_do.sh <DROPLET_IP>"
    echo "Example: ./update_do.sh 203.0.113.50"
    echo ""
    echo "Note: Make sure your Droplet is already set up and running the service."
    exit 1
fi

DROPLET_IP=$1
USER="root"
TARGET_DIR="/root/least-count-rush-backend/"

echo "🚀 Updating backend on DigitalOcean Droplet at $DROPLET_IP..."

echo "📦 Syncing files via rsync..."
# Sync files, excluding unnecessary directories (like venv, git, caches)
# We also exclude .env so we don't accidentally overwrite the production environment variables
rsync -avz --exclude 'venv' --exclude '.venv' --exclude '__pycache__' --exclude '.git' --exclude '.env' --exclude 'update_do.sh' ./ ${USER}@${DROPLET_IP}:${TARGET_DIR}

if [ $? -ne 0 ]; then
    echo "❌ Failed to sync files. Please check your SSH connection and IP address."
    exit 1
fi

echo "🔄 Restarting the systemd service..."
# SSH into the server and restart the backend service
ssh ${USER}@${DROPLET_IP} "sudo systemctl restart least-count-rush && sudo systemctl status least-count-rush --no-pager | head -n 10"

echo "✅ Update complete!"
