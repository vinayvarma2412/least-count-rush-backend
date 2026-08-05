#!/bin/bash
# Google Cloud Run Deployment Script for Least Count Rush Backend
# This script builds and deploys the backend to Google Cloud Run

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration (update these as needed)
PROJECT_ID="${GCP_PROJECT_ID:-}"
SERVICE_NAME="${GCP_SERVICE_NAME:-least-count-rush-backend}"
REGION="${GCP_REGION:-us-central1}"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo -e "${RED}Error: gcloud CLI is not installed.${NC}"
    echo "Please install it from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed.${NC}"
    echo "Please install Docker from: https://docs.docker.com/get-docker/"
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

IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo -e "${GREEN}=== Google Cloud Run Deployment ===${NC}"
echo "Project ID: ${PROJECT_ID}"
echo "Service Name: ${SERVICE_NAME}"
echo "Region: ${REGION}"
echo "Image: ${IMAGE_NAME}"
echo ""

# Authenticate Docker with gcloud
echo -e "${YELLOW}Step 1: Configuring Docker authentication...${NC}"
gcloud auth configure-docker --quiet

# Build Docker image for linux/amd64 platform (required for Cloud Run)
echo -e "${YELLOW}Step 2: Building Docker image for linux/amd64 platform...${NC}"
docker build --platform linux/amd64 -t ${IMAGE_NAME}:latest .

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Docker build failed${NC}"
    exit 1
fi

# Push image to Google Container Registry
echo -e "${YELLOW}Step 3: Pushing image to Google Container Registry...${NC}"
docker push ${IMAGE_NAME}:latest

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Docker push failed${NC}"
    exit 1
fi

# Deploy to Cloud Run
echo -e "${YELLOW}Step 4: Deploying to Cloud Run...${NC}"
gcloud run deploy ${SERVICE_NAME} \
    --image ${IMAGE_NAME}:latest \
    --platform managed \
    --region ${REGION} \
    --allow-unauthenticated \
    --port 8080 \
    --memory 512Mi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 10 \
    --timeout 300 \
    --set-env-vars "DEBUG=false" \
    --quiet

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Cloud Run deployment failed${NC}"
    exit 1
fi

# Get service URL
echo -e "${YELLOW}Step 5: Getting service URL...${NC}"
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format 'value(status.url)')

echo ""
echo -e "${GREEN}=== Deployment Successful! ===${NC}"
echo "Service URL: ${SERVICE_URL}"
echo ""
echo "Test the deployment:"
echo "  curl ${SERVICE_URL}/health"
echo ""
echo "WebSocket endpoint:"
echo "  wss://$(echo ${SERVICE_URL} | sed 's|https://||')/ws/{room_id}"
echo ""
echo "Update your Flutter app with this URL!"

