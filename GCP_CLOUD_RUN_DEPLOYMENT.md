# Google Cloud Run Deployment Guide - Least Count Rush Backend

This guide will help you deploy the Least Count Rush Backend to Google Cloud Run with WebSocket support.

## Prerequisites

- Google Cloud Platform account with billing enabled
- `gcloud` CLI installed and configured ([Installation Guide](https://cloud.google.com/sdk/docs/install))
- Docker installed ([Installation Guide](https://docs.docker.com/get-docker/))
- A Google Cloud Project created

## Quick Start

### Step 1: Install Prerequisites

**Install gcloud CLI:**
```bash
# macOS
brew install --cask google-cloud-sdk

# Or download from: https://cloud.google.com/sdk/docs/install
```

**Install Docker:**
```bash
# macOS
brew install --cask docker

# Or download from: https://docs.docker.com/get-docker/
```

### Step 2: Configure Google Cloud

1. **Create a Google Cloud Project** (if you don't have one):
   ```bash
   gcloud projects create YOUR_PROJECT_ID --name="Least Count Rush"
   ```

2. **Set your project:**
   ```bash
   gcloud config set project least-count-rush
   ```

3. **Enable required APIs:**
   ```bash
   gcloud services enable cloudbuild.googleapis.com
   gcloud services enable run.googleapis.com
   gcloud services enable containerregistry.googleapis.com
   ```

4. **Authenticate Docker:**
   ```bash
   gcloud auth configure-docker
   ```

### Step 3: Deploy Using the Script

**Option A: Using Local Docker (requires Docker Desktop running)**

1. **Start Docker Desktop** (if not already running)
   - Open Docker Desktop from Applications
   - Wait for it to fully start

2. **Navigate to the backend directory:**
   ```bash
   cd /Users/mrunknown/Desktop/Projects/Flutter/least_count_backend
   ```

3. **Make the deployment script executable:**
   ```bash
   chmod +x deploy_cloud_run.sh
   ```

4. **Set environment variables (optional):**
   ```bash
   export GCP_PROJECT_ID=least-count-rush
   export GCP_SERVICE_NAME=least-count-rush-backend
   export GCP_REGION=us-central1
   ```

5. **Run the deployment script:**
   ```bash
   ./deploy_cloud_run.sh
   ```

**Option B: Using Cloud Build (No Local Docker Required)**

If you don't have Docker installed or prefer not to use it locally:

1. **Navigate to the backend directory:**
   ```bash
   cd /Users/mrunknown/Desktop/Projects/Flutter/least_count_backend
   ```

2. **Make the Cloud Build deployment script executable:**
   ```bash
   chmod +x deploy_cloud_run_cloudbuild.sh
   ```

3. **Set environment variables (optional):**
   ```bash
   export GCP_PROJECT_ID=least-count-rush
   export GCP_SERVICE_NAME=least-count-rush-backend
   export GCP_REGION=us-central1
   ```

4. **Run the Cloud Build deployment script:**
   ```bash
   ./deploy_cloud_run_cloudbuild.sh
   ```

The scripts will:
- Build the Docker image (locally or via Cloud Build)
- Push it to Google Container Registry
- Deploy to Cloud Run
- Display your service URL

### Step 4: Get Your Service URL

After deployment, the script will display your service URL. It will look like:
```
https://least-count-rush-backend-xxxxx-uc.a.run.app
```

## Manual Deployment

If you prefer to deploy manually:

### Step 1: Build Docker Image

```bash
cd backend
docker build -t gcr.io/YOUR_PROJECT_ID/least-count-rush-backend:latest .
```

### Step 2: Push to Container Registry

```bash
docker push gcr.io/YOUR_PROJECT_ID/least-count-rush-backend:latest
```

### Step 3: Deploy to Cloud Run

```bash
gcloud run deploy least-count-rush-backend \
  --image gcr.io/YOUR_PROJECT_ID/least-count-rush-backend:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 10 \
  --timeout 300 \
  --set-env-vars "DEBUG=false"
```

## Testing the Deployment

### Test Health Endpoint

```bash
curl https://YOUR_SERVICE_URL/health
```

Expected response:
```json
{
  "status": "healthy",
  "service": "Least Count Rush Backend",
  "version": "1.0.0"
}
```

### Test WebSocket Connection

You can test WebSocket connections using a tool like `wscat`:

```bash
# Install wscat
npm install -g wscat

# Connect to WebSocket
wscat -c wss://YOUR_SERVICE_URL/ws/test-room-id
```

Or use the frontend testing UI:
```
https://YOUR_SERVICE_URL/frontend/index.html
```

## Configuration Options

### Environment Variables

Set environment variables during deployment:

```bash
gcloud run services update least-count-rush-backend \
  --update-env-vars "DEBUG=false,APP_NAME=Least Count Rush Backend" \
  --region us-central1
```

### Resource Limits

Adjust CPU and memory:

```bash
gcloud run services update least-count-rush-backend \
  --cpu 2 \
  --memory 1Gi \
  --region us-central1
```

### Scaling Configuration

Set minimum and maximum instances:

```bash
gcloud run services update least-count-rush-backend \
  --min-instances 1 \
  --max-instances 20 \
  --region us-central1
```

### Timeout Settings

Increase timeout for long-running requests:

```bash
gcloud run services update least-count-rush-backend \
  --timeout 600 \
  --region us-central1
```

## Updating the Deployment

### Method 1: Using the Script

Simply run the deployment script again:

```bash
./deploy_cloud_run.sh
```

### Method 2: Manual Update

1. **Build and push new image:**
   ```bash
   docker build -t gcr.io/YOUR_PROJECT_ID/least-count-rush-backend:latest .
   docker push gcr.io/YOUR_PROJECT_ID/least-count-rush-backend:latest
   ```

2. **Update Cloud Run service:**
   ```bash
   gcloud run services update least-count-rush-backend \
     --image gcr.io/YOUR_PROJECT_ID/least-count-rush-backend:latest \
     --region us-central1
   ```

## CI/CD with Cloud Build

To enable automatic deployments on git push:

1. **Create a Cloud Build trigger:**
   ```bash
   gcloud builds triggers create github \
     --repo-name=YOUR_REPO \
     --repo-owner=YOUR_GITHUB_USERNAME \
     --branch-pattern="^main$" \
     --build-config=backend/cloudbuild.yaml
   ```

2. **Or use the Cloud Console:**
   - Go to Cloud Build → Triggers
   - Create trigger
   - Connect your repository
   - Set build configuration file to `backend/cloudbuild.yaml`

## Custom Domain Setup

To use a custom domain (e.g., `api.leastcountrush.online`):

1. **Map your domain:**
   ```bash
   gcloud run domain-mappings create \
     --service least-count-rush-backend \
     --domain api.leastcountrush.online \
     --region us-central1
   ```

2. **Update DNS records** as instructed by the command output

3. **Update CORS** in `app/main.py` to include your custom domain

## Monitoring and Logs

### View Logs

```bash
# Stream logs
gcloud run services logs read least-count-rush-backend --region us-central1 --follow

# View recent logs
gcloud run services logs read least-count-rush-backend --region us-central1 --limit 50
```

### View Service Status

```bash
gcloud run services describe least-count-rush-backend --region us-central1
```

### Monitor in Cloud Console

- Go to Cloud Run → Services → least-count-rush-backend
- View metrics, logs, and revisions

## Troubleshooting

### Docker Daemon Not Running

If you see the error: `failed to connect to the docker API at unix:///var/run/docker.sock`

**On macOS:**
1. **Start Docker Desktop:**
   - Open Docker Desktop application from Applications folder
   - Wait for Docker to fully start (whale icon in menu bar should be steady)
   - Verify Docker is running:
     ```bash
     docker ps
     ```
   - If Docker Desktop is not installed, download it from: https://www.docker.com/products/docker-desktop

2. **Verify Docker is accessible:**
   ```bash
   docker --version
   docker info
   ```

**On Linux:**
1. **Start Docker service:**
   ```bash
   sudo systemctl start docker
   sudo systemctl enable docker  # Enable auto-start on boot
   ```

2. **Add your user to docker group (if needed):**
   ```bash
   sudo usermod -aG docker $USER
   # Log out and log back in for changes to take effect
   ```

**Alternative: Use Cloud Build (No Local Docker Required)**

If you don't want to use Docker locally, you can use Google Cloud Build instead:

```bash
# Build and deploy using Cloud Build (no local Docker needed)
gcloud builds submit --tag gcr.io/least-count-rush/least-count-rush-backend:latest backend/

# Then deploy to Cloud Run
gcloud run deploy least-count-rush-backend \
  --image gcr.io/least-count-rush/least-count-rush-backend:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080
```

### Platform/Architecture Error

If you see: `Container manifest type 'application/vnd.oci.image.index.v1+json' must support amd64/linux`

This happens when building Docker images on Apple Silicon (M1/M2 Macs) which create ARM64 images by default, but Cloud Run requires AMD64/Linux images.

**Solution:**

1. **If using local Docker build** (`deploy_cloud_run.sh`):
   - The script has been updated to use `--platform linux/amd64` flag automatically
   - Make sure you're using the latest version of the script
   - Rebuild and redeploy:
     ```bash
     ./deploy_cloud_run.sh
     ```

2. **If using Cloud Build** (`deploy_cloud_run_cloudbuild.sh`):
   - Cloud Build runs on Linux machines and builds AMD64 images by default
   - This method should work without issues
   - If you still see the error, ensure you're using the latest script

3. **Manual fix** (if scripts don't work):
   ```bash
   # Build with explicit platform
   docker build --platform linux/amd64 -t gcr.io/least-count-rush/least-count-rush-backend:latest .
   
   # Push the image
   docker push gcr.io/least-count-rush/least-count-rush-backend:latest
   
   # Deploy
   gcloud run deploy least-count-rush-backend \
     --image gcr.io/least-count-rush/least-count-rush-backend:latest \
     --platform managed \
     --region us-central1
   ```

### Deployment Fails

1. **Check build logs:**
   ```bash
   gcloud builds list --limit=5
   gcloud builds log BUILD_ID
   ```

2. **Verify Docker image:**
   ```bash
   docker images | grep least-count-rush-backend
   ```

3. **Test Docker image locally:**
   ```bash
   docker run --platform linux/amd64 -p 8080:8080 gcr.io/YOUR_PROJECT_ID/least-count-rush-backend:latest
   ```

### WebSocket Connection Issues

1. **Verify WebSocket support:**
   - Cloud Run supports WebSockets via HTTP/2
   - Ensure you're using `wss://` (not `ws://`) for secure connections

2. **Check CORS settings:**
   - Verify your Flutter app's origin is in the CORS allow list
   - Cloud Run URLs follow pattern: `https://*.run.app`

3. **Test WebSocket endpoint:**
   ```bash
   wscat -c wss://YOUR_SERVICE_URL/ws/test-room-id
   ```

### Service Not Responding

1. **Check service status:**
   ```bash
   gcloud run services describe least-count-rush-backend --region us-central1
   ```

2. **View logs for errors:**
   ```bash
   gcloud run services logs read least-count-rush-backend --region us-central1 --limit 100
   ```

3. **Verify health endpoint:**
   ```bash
   curl https://YOUR_SERVICE_URL/health
   ```

### Port Configuration Issues

- Cloud Run automatically sets the `PORT` environment variable
- The application reads this via `app/config.py`
- Default port is 8080 if `PORT` is not set

## Cost Optimization

### Free Tier

- Cloud Run offers a free tier: 2 million requests per month
- 360,000 GB-seconds of memory, 180,000 vCPU-seconds

### Cost-Saving Tips

1. **Set min-instances to 0** (default) - scales to zero when not in use
2. **Use appropriate memory** - Start with 512Mi, increase if needed
3. **Set max-instances** - Prevent runaway costs
4. **Monitor usage** - Use Cloud Console to track costs

## Security Best Practices

1. **Use IAM for access control** (if not using public access)
2. **Enable Cloud Armor** for DDoS protection
3. **Use Secret Manager** for sensitive environment variables
4. **Enable VPC connector** if accessing private resources
5. **Regularly update dependencies** in requirements.txt

## Next Steps

1. **Update Flutter app** with the Cloud Run service URL
2. **Set up monitoring** and alerts
3. **Configure custom domain** (optional)
4. **Set up CI/CD** for automatic deployments
5. **Review and optimize** resource allocation based on usage

## Support

For issues:
1. Check Cloud Run logs: `gcloud run services logs read`
2. Review Cloud Build logs if deployment fails
3. Test Docker image locally before deploying
4. Verify all prerequisites are installed and configured

## Additional Resources

- [Cloud Run Documentation](https://cloud.google.com/run/docs)
- [Cloud Run Pricing](https://cloud.google.com/run/pricing)
- [FastAPI Deployment Guide](https://fastapi.tiangolo.com/deployment/)
- [WebSocket Support in Cloud Run](https://cloud.google.com/run/docs/configuring/websockets)

