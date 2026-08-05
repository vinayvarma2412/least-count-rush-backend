#!/bin/bash
# Google Cloud Run Deployment Script using Cloud Build (No Local Docker Required)
# This script builds and deploys the backend to Google Cloud Run using Cloud Build

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

echo -e "${GREEN}=== Google Cloud Run Deployment (Cloud Build) ===${NC}"
echo "Project ID: ${PROJECT_ID}"
echo "Service Name: ${SERVICE_NAME}"
echo "Region: ${REGION}"
echo "Image: ${IMAGE_NAME}"
echo ""
echo -e "${YELLOW}Note: This method uses Cloud Build and does not require local Docker.${NC}"
echo ""

# Enable required APIs
echo -e "${YELLOW}Step 1: Enabling required APIs...${NC}"
gcloud services enable cloudbuild.googleapis.com --quiet
gcloud services enable run.googleapis.com --quiet
gcloud services enable containerregistry.googleapis.com --quiet

# Build using Cloud Build
# Note: Cloud Build runs on Linux machines, so it builds AMD64 images by default
echo -e "${YELLOW}Step 2: Building Docker image using Cloud Build...${NC}"
echo "This may take a few minutes..."
echo "Note: Cloud Build automatically builds for linux/amd64 platform"
gcloud builds submit --tag ${IMAGE_NAME}:latest .

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Cloud Build failed${NC}"
    exit 1
fi

# Deploy to Cloud Run
echo -e "${YELLOW}Step 3: Deploying to Cloud Run...${NC}"
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
echo -e "${YELLOW}Step 4: Getting service URL...${NC}"
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

