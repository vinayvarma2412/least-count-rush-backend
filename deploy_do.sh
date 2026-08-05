#!/bin/bash
# One-click deployment script for Least Count Rush Backend on DigitalOcean Droplet
# This script sets up the backend to run continuously using systemd

set -e  # Exit on error

echo "========================================================="
echo "Least Count Rush Backend - DigitalOcean Droplet Deployment"
echo "========================================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BACKEND_DIR="$SCRIPT_DIR"
SERVICE_NAME="least-count-rush"
SERVICE_FILE="$BACKEND_DIR/least-count-rush.service"
SYSTEMD_DIR="/etc/systemd/system"

# Detect OS and package manager
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    OS="unknown"
fi

# Detect default user (DO often uses root or a created user)
if [ "$SUDO_USER" ]; then
    SERVICE_USER="$SUDO_USER"
elif id "ubuntu" &>/dev/null; then
    SERVICE_USER="ubuntu"
else
    SERVICE_USER=$(whoami)
fi

echo -e "${GREEN}Detected OS: $OS${NC}"
echo -e "${GREEN}Using user: $SERVICE_USER${NC}"

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then 
    echo -e "${YELLOW}Note: This script needs sudo privileges to install systemd service${NC}"
    echo "Please run: sudo $0"
    exit 1
fi

echo ""
echo "Step 1: Checking Python installation (3.10+ required)..."

# Function to get python minor version
get_python_minor() {
    python3 -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo "0"
}
get_python_major() {
    python3 -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo "0"
}

PYTHON_BIN="python3"
NEED_INSTALL=false

if ! command -v python3 &> /dev/null; then
    NEED_INSTALL=true
else
    PY_MAJOR=$(get_python_major)
    PY_MINOR=$(get_python_minor)
    echo -e "${GREEN}Python 3 found: $(python3 --version)${NC}"
    if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
        echo -e "${YELLOW}Python $PY_MAJOR.$PY_MINOR is too old (need 3.10+). Installing Python 3.11...${NC}"
        NEED_INSTALL=true
    fi
fi

if [ "$NEED_INSTALL" = true ]; then
    if [[ "$OS" == "ubuntu" ]] || [[ "$OS" == "debian" ]]; then
        apt-get update
        apt-get install -y software-properties-common || true
        add-apt-repository -y ppa:deadsnakes/ppa || true
        apt-get update
        apt-get install -y python3.11 python3.11-venv python3.11-distutils python3-pip
        PYTHON_BIN="python3.11"
    else
        echo -e "${RED}Unsupported OS for automatic install. Please install Python 3.10+ manually.${NC}"
        exit 1
    fi
    echo -e "${GREEN}Python 3.11 installed: $($PYTHON_BIN --version)${NC}"
else
    # Check if python3.11 is available and prefer it
    if command -v python3.11 &> /dev/null; then
        PYTHON_BIN="python3.11"
        echo -e "${GREEN}Using python3.11: $($PYTHON_BIN --version)${NC}"
    fi
fi

# Ensure python3-venv is installed on ubuntu
if [[ "$OS" == "ubuntu" ]] || [[ "$OS" == "debian" ]]; then
    echo "Ensuring venv package is installed..."
    apt-get install -y python3-venv || true
fi

echo ""
echo "Step 2: Setting up virtual environment..."
cd "$BACKEND_DIR"

# If venv exists but doesn't have the activate script, it might be an invalid or partially copied venv
if [ -d "venv" ] && [ ! -f "venv/bin/activate" ]; then
    echo "Found invalid venv directory, removing it..."
    rm -rf venv
fi

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON_BIN -m venv venv
    echo -e "${GREEN}Virtual environment created${NC}"
else
    echo -e "${GREEN}Virtual environment already exists${NC}"
fi

echo ""
echo "Step 3: Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
echo -e "${GREEN}Dependencies installed${NC}"

echo ""
echo "Step 4: Updating service file with correct paths..."
# Update the service file with the actual backend directory
sed -i "s|WorkingDirectory=.*|WorkingDirectory=$BACKEND_DIR|g" "$SERVICE_FILE"
sed -i "s|Environment=\"PATH=.*|Environment=\"PATH=$BACKEND_DIR/venv/bin\"|g" "$SERVICE_FILE"
sed -i "s|ExecStart=.*|ExecStart=$BACKEND_DIR/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000|g" "$SERVICE_FILE"

# Update service file with detected user
sed -i "s|User=.*|User=$SERVICE_USER|g" "$SERVICE_FILE"
echo -e "${GREEN}Service file updated${NC}"

echo ""
echo "Step 5: Installing systemd service..."
cp "$SERVICE_FILE" "$SYSTEMD_DIR/$SERVICE_NAME.service"
chmod 644 "$SYSTEMD_DIR/$SERVICE_NAME.service"
systemctl daemon-reload
echo -e "${GREEN}Systemd service installed${NC}"

echo ""
echo "Step 6: Enabling service to start on boot..."
systemctl enable "$SERVICE_NAME.service"
echo -e "${GREEN}Service enabled for auto-start${NC}"

echo ""
echo "Step 7: Starting service..."
systemctl restart "$SERVICE_NAME.service"
sleep 2

# Check if service is running
if systemctl is-active --quiet "$SERVICE_NAME.service"; then
    echo -e "${GREEN}Service started successfully!${NC}"
else
    echo -e "${RED}Service failed to start. Checking logs...${NC}"
    journalctl -u "$SERVICE_NAME.service" -n 20 --no-pager
    exit 1
fi

echo ""
echo "========================================================="
echo -e "${GREEN}Deployment completed successfully!${NC}"
echo "========================================================="
echo ""
echo "Service Status:"
systemctl status "$SERVICE_NAME.service" --no-pager -l | head -10
echo ""
echo "Useful commands:"
echo "  Check status:  sudo systemctl status $SERVICE_NAME"
echo "  View logs:     sudo journalctl -u $SERVICE_NAME -f"
echo "  Restart:       sudo systemctl restart $SERVICE_NAME"
echo "  Stop:          sudo systemctl stop $SERVICE_NAME"
echo "  Start:         sudo systemctl start $SERVICE_NAME"
echo ""
echo "The backend is now running continuously and will auto-start on reboot!"
echo ""
