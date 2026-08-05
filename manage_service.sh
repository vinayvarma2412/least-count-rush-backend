#!/bin/bash
# Management script for Least Count Rush Backend service
# Usage: ./manage_service.sh [start|stop|restart|status|logs|enable|disable]

SERVICE_NAME="least-count-rush"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then 
    echo -e "${YELLOW}Note: This script needs sudo privileges${NC}"
    echo "Please run: sudo $0 [command]"
    exit 1
fi

case "$1" in
    start)
        echo -e "${BLUE}Starting $SERVICE_NAME service...${NC}"
        systemctl start "$SERVICE_NAME.service"
        sleep 1
        if systemctl is-active --quiet "$SERVICE_NAME.service"; then
            echo -e "${GREEN}Service started successfully!${NC}"
        else
            echo -e "${RED}Failed to start service${NC}"
            systemctl status "$SERVICE_NAME.service" --no-pager -l | head -10
        fi
        ;;
    stop)
        echo -e "${BLUE}Stopping $SERVICE_NAME service...${NC}"
        systemctl stop "$SERVICE_NAME.service"
        echo -e "${GREEN}Service stopped${NC}"
        ;;
    restart)
        echo -e "${BLUE}Restarting $SERVICE_NAME service...${NC}"
        systemctl restart "$SERVICE_NAME.service"
        sleep 1
        if systemctl is-active --quiet "$SERVICE_NAME.service"; then
            echo -e "${GREEN}Service restarted successfully!${NC}"
        else
            echo -e "${RED}Failed to restart service${NC}"
            systemctl status "$SERVICE_NAME.service" --no-pager -l | head -10
        fi
        ;;
    status)
        echo -e "${BLUE}Service Status:${NC}"
        systemctl status "$SERVICE_NAME.service" --no-pager -l
        ;;
    logs)
        echo -e "${BLUE}Showing logs (Press Ctrl+C to exit):${NC}"
        journalctl -u "$SERVICE_NAME.service" -f
        ;;
    enable)
        echo -e "${BLUE}Enabling $SERVICE_NAME to start on boot...${NC}"
        systemctl enable "$SERVICE_NAME.service"
        echo -e "${GREEN}Service enabled${NC}"
        ;;
    disable)
        echo -e "${BLUE}Disabling $SERVICE_NAME from starting on boot...${NC}"
        systemctl disable "$SERVICE_NAME.service"
        echo -e "${GREEN}Service disabled${NC}"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs|enable|disable}"
        echo ""
        echo "Commands:"
        echo "  start    - Start the service"
        echo "  stop     - Stop the service"
        echo "  restart  - Restart the service"
        echo "  status   - Show service status"
        echo "  logs     - Show and follow service logs"
        echo "  enable   - Enable service to start on boot"
        echo "  disable  - Disable service from starting on boot"
        exit 1
        ;;
esac




















