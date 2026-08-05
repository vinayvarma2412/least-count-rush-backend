#!/bin/bash
# Management script for Google Cloud Run service
# Usage: ./manage_cloud_run.sh [start|stop|force-stop|restart|status]

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration (update these as needed)
PROJECT_ID="${GCP_PROJECT_ID:-}"
SERVICE_NAME="${GCP_SERVICE_NAME:-least-count-rush-backend}"
REGION="${GCP_REGION:-us-central1}"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo -e "${RED}Error: gcloud CLI is not installed.${NC}"
    echo "Please install it from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Check if PROJECT_ID is set
if [ -z "$PROJECT_ID" ]; then
    echo -e "${YELLOW}GCP_PROJECT_ID not set. Attempting to get from gcloud...${NC}"
    PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
    
    if [ -z "$PROJECT_ID" ]; then
        echo -e "${RED}Error: GCP_PROJECT_ID is not set and could not be determined.${NC}"
        echo "Please set it: export GCP_PROJECT_ID=your-project-id"
        echo "Or run: gcloud config set project YOUR_PROJECT_ID"
        exit 1
    fi
    echo -e "${GREEN}Using project: ${PROJECT_ID}${NC}"
fi

# Function to check if service exists
service_exists() {
    gcloud run services describe ${SERVICE_NAME} \
        --region ${REGION} \
        --format 'value(status.url)' 2>/dev/null | grep -q .
}

# Function to get service URL
get_service_url() {
    gcloud run services describe ${SERVICE_NAME} \
        --region ${REGION} \
        --format 'value(status.url)' 2>/dev/null
}

case "$1" in
    start)
        echo -e "${BLUE}Starting Cloud Run service: ${SERVICE_NAME}...${NC}"
        echo "Project: ${PROJECT_ID}"
        echo "Region: ${REGION}"
        echo ""
        
        # Get current max-instances, or use default
        CURRENT_MAX=$(gcloud run services describe ${SERVICE_NAME} \
            --region ${REGION} \
            --format 'value(spec.template.metadata.annotations."autoscaling.knative.dev/maxScale")' 2>/dev/null)
        
        if [ -z "$CURRENT_MAX" ] || [ "$CURRENT_MAX" = "0" ]; then
            CURRENT_MAX="10"
        fi
        
        # Update service to have minimum 1 instance and restore max-instances
        gcloud run services update ${SERVICE_NAME} \
            --region ${REGION} \
            --min-instances 1 \
            --max-instances ${CURRENT_MAX} \
            --quiet
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}Service started successfully!${NC}"
            echo ""
            echo "The service will now keep at least 1 instance running."
            echo "Max instances: ${CURRENT_MAX}"
            echo "Service URL: $(get_service_url)"
        else
            echo -e "${RED}Failed to start service${NC}"
            exit 1
        fi
        ;;
    stop)
        echo -e "${BLUE}Stopping Cloud Run service: ${SERVICE_NAME}...${NC}"
        echo "Project: ${PROJECT_ID}"
        echo "Region: ${REGION}"
        echo ""
        
        # Update service to scale to zero
        gcloud run services update ${SERVICE_NAME} \
            --region ${REGION} \
            --min-instances 0 \
            --quiet
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}Service configuration updated!${NC}"
            echo ""
            echo -e "${YELLOW}Note: Cloud Run scales to zero automatically when there's no traffic.${NC}"
            echo "If there's active traffic, instances will continue running."
            echo "The service will scale to zero after a few minutes of inactivity."
            echo ""
            echo "To check if instances are currently running, use:"
            echo "  ./manage_cloud_run.sh status"
        else
            echo -e "${RED}Failed to update service${NC}"
            exit 1
        fi
        ;;
    force-stop)
        echo -e "${BLUE}Force stopping Cloud Run service: ${SERVICE_NAME}...${NC}"
        echo "Project: ${PROJECT_ID}"
        echo "Region: ${REGION}"
        echo ""
        echo -e "${YELLOW}Note: Cloud Run requires max-instances to be at least 1.${NC}"
        echo "This will limit the service to maximum 1 instance and allow scaling to zero."
        echo ""
        
        # Set min-instances to 0 and max-instances to 1 (minimum allowed)
        # This limits scaling but doesn't prevent instances completely
        gcloud run services update ${SERVICE_NAME} \
            --region ${REGION} \
            --min-instances 0 \
            --max-instances 1 \
            --quiet
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}Service configuration updated!${NC}"
            echo ""
            echo -e "${YELLOW}Service is now limited to maximum 1 instance.${NC}"
            echo "It will scale to zero when there's no traffic."
            echo "Note: Cloud Run doesn't support true 'force stop' - instances may still run if there's active traffic."
            echo ""
            echo "To restore normal operation, use:"
            echo "  ./manage_cloud_run.sh start"
        else
            echo -e "${RED}Failed to update service${NC}"
            exit 1
        fi
        ;;
    restart)
        echo -e "${BLUE}Restarting Cloud Run service: ${SERVICE_NAME}...${NC}"
        echo "Project: ${PROJECT_ID}"
        echo "Region: ${REGION}"
        echo ""
        
        # Get current min-instances to preserve it
        CURRENT_MIN=$(gcloud run services describe ${SERVICE_NAME} \
            --region ${REGION} \
            --format 'value(spec.template.metadata.annotations."autoscaling.knative.dev/minScale")' 2>/dev/null || echo "0")
        
        if [ -z "$CURRENT_MIN" ]; then
            CURRENT_MIN="0"
        fi
        
        echo "Forcing new revision deployment to restart all instances..."
        
        # Force a new revision by updating a label with timestamp
        RESTART_TIMESTAMP=$(date +%s)
        gcloud run services update ${SERVICE_NAME} \
            --region ${REGION} \
            --update-labels "restarted=${RESTART_TIMESTAMP}" \
            --min-instances ${CURRENT_MIN} \
            --quiet
        
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}Service restarted successfully!${NC}"
            echo ""
            echo "A new revision has been deployed and all instances have been restarted."
            echo "Service URL: $(get_service_url)"
            echo ""
            echo -e "${YELLOW}Note: It may take a few moments for the new revision to be fully active.${NC}"
        else
            echo -e "${RED}Failed to restart service${NC}"
            exit 1
        fi
        ;;
    status)
        echo -e "${BLUE}Cloud Run Service Status:${NC}"
        echo "Project: ${PROJECT_ID}"
        echo "Service: ${SERVICE_NAME}"
        echo "Region: ${REGION}"
        echo ""
        
        # Check if service exists
        if ! service_exists; then
            echo -e "${RED}Error: Service not found or not accessible${NC}"
            exit 1
        fi
        
        # Get service details
        SERVICE_URL=$(get_service_url)
        
        # Get min-instances (annotation might not exist if it's 0, so handle empty string)
        MIN_INSTANCES_RAW=$(gcloud run services describe ${SERVICE_NAME} \
            --region ${REGION} \
            --format 'value(spec.template.metadata.annotations."autoscaling.knative.dev/minScale")' 2>/dev/null)
        
        if [ -z "$MIN_INSTANCES_RAW" ]; then
            MIN_INSTANCES="0"
        else
            MIN_INSTANCES="$MIN_INSTANCES_RAW"
        fi
        
        # Get max-instances
        MAX_INSTANCES_RAW=$(gcloud run services describe ${SERVICE_NAME} \
            --region ${REGION} \
            --format 'value(spec.template.metadata.annotations."autoscaling.knative.dev/maxScale")' 2>/dev/null)
        
        if [ -z "$MAX_INSTANCES_RAW" ]; then
            MAX_INSTANCES="10"
        else
            MAX_INSTANCES="$MAX_INSTANCES_RAW"
        fi
        
        echo -e "${GREEN}Status: Ready${NC}"
        echo "Service URL: ${SERVICE_URL}"
        echo "Min Instances: ${MIN_INSTANCES}"
        echo "Max Instances: ${MAX_INSTANCES}"
        echo ""
        
        # Check if service is currently responding (indicates instances are running)
        echo -e "${BLUE}Checking if service is currently running...${NC}"
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "${SERVICE_URL}/health" 2>/dev/null || echo "000")
        
        if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "404" ]; then
            echo -e "${GREEN}Service is currently responding (instances are running)${NC}"
        elif [ "$HTTP_CODE" = "000" ]; then
            echo -e "${YELLOW}Service is not responding (likely scaled to zero)${NC}"
        else
            echo -e "${YELLOW}Service returned HTTP ${HTTP_CODE}${NC}"
        fi
        echo ""
        
        if [ "$MAX_INSTANCES" = "1" ]; then
            echo -e "${YELLOW}Configuration: Service is LIMITED (max-instances = 1)${NC}"
            echo "Service is limited to maximum 1 instance and can scale to zero."
            echo "Run './manage_cloud_run.sh start' to restore normal operation"
        elif [ "$MIN_INSTANCES" = "0" ] || [ -z "$MIN_INSTANCES" ]; then
            echo -e "${YELLOW}Configuration: Service is set to scale to zero${NC}"
            echo "Instances will stop automatically after a few minutes of no traffic."
            echo "Run './manage_cloud_run.sh start' to keep it always running"
        else
            echo -e "${GREEN}Configuration: Service will keep at least ${MIN_INSTANCES} instance(s) running${NC}"
            echo "Run './manage_cloud_run.sh stop' to allow scaling to zero"
        fi
        ;;
    *)
        echo "Usage: $0 {start|stop|force-stop|restart|status}"
        echo ""
        echo "Commands:"
        echo "  start       - Start the service (set min-instances to 1, restore max-instances)"
        echo "  stop        - Stop the service (set min-instances to 0, allows scaling to zero)"
        echo "  force-stop  - Limit service (set max-instances to 1, allows scaling to zero)"
        echo "  restart     - Restart the service (force new revision deployment)"
        echo "  status      - Show service status and configuration"
        echo ""
        echo "Environment Variables (optional):"
        echo "  GCP_PROJECT_ID   - Google Cloud Project ID"
        echo "  GCP_SERVICE_NAME - Cloud Run service name (default: least-count-rush-backend)"
        echo "  GCP_REGION       - Cloud Run region (default: us-central1)"
        echo ""
        echo "Examples:"
        echo "  ./manage_cloud_run.sh start"
        echo "  ./manage_cloud_run.sh stop"
        echo "  ./manage_cloud_run.sh force-stop"
        echo "  ./manage_cloud_run.sh restart"
        echo "  ./manage_cloud_run.sh status"
        echo ""
        echo "Or with environment variables:"
        echo "  export GCP_PROJECT_ID=my-project"
        echo "  ./manage_cloud_run.sh start"
        exit 1
        ;;
esac

