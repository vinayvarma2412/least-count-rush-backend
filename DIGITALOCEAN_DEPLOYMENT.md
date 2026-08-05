# DigitalOcean Deployment Guide - Least Count Rush Backend

This guide covers deploying the Least Count Rush Backend on DigitalOcean. You can choose to deploy it on a **Virtual Machine (Droplet)** or on the **App Platform (PaaS)**. 

Since this backend relies heavily on WebSockets for real-time game state, DigitalOcean is an excellent choice as it handles persistent connections very well without unexpected timeouts.

---

## Option 1: Droplet Deployment (Recommended for Cost)

A Droplet provides a dedicated Linux VM. A basic $4-$6/mo Droplet is plenty to run this game backend. 

### Prerequisites

- A DigitalOcean account
- A new Droplet running **Ubuntu 22.04** or **Ubuntu 24.04**
- SSH access to the Droplet

### Step 1: Upload Backend Files to your Droplet

Use `rsync` or `scp` to upload your backend folder to the Droplet. **Make sure to exclude the local virtual environment and cache files.**

```bash
# From your local machine (replace DROPLET_IP with your actual IP)
rsync -avz --exclude 'venv' --exclude '__pycache__' --exclude '.git' backend/ root@DROPLET_IP:/root/least-count-rush-backend/
```

### Step 2: SSH into Your Droplet

```bash
ssh root@DROPLET_IP
```

### Step 3: Run the Deployment Script

We've provided a dedicated deployment script for DigitalOcean:

```bash
cd /root/least-count-rush-backend/
sudo chmod +x deploy_do.sh
sudo ./deploy_do.sh
```

**What the script does:**
1. Installs Python 3.10+ if missing
2. Creates an isolated Python virtual environment (`venv`)
3. Installs packages from `requirements.txt`
4. Creates a `systemd` service (`least-count-rush.service`)
5. Starts the service in the background and enables it on boot.

### Step 4: Manage the Service

You can use standard `systemd` commands to manage the server:

```bash
# Check status
sudo systemctl status least-count-rush

# View live logs
sudo journalctl -u least-count-rush -f

# Restart the service (do this after uploading new code changes)
sudo systemctl restart least-count-rush
```

### Firewall Configuration

By default, DigitalOcean doesn't block ports unless you create a Cloud Firewall. However, Ubuntu has `ufw` built-in. Make sure port 8000 is open:

```bash
sudo ufw allow 8000/tcp
sudo ufw allow ssh
sudo ufw enable
```

---

## Option 2: DigitalOcean App Platform (Easiest)

If you don't want to manage a VM, DigitalOcean App Platform can build and deploy the `Dockerfile` directly from your GitHub repository. It automatically provides an SSL certificate (HTTPS/WSS).

### Step 1: Create an App

1. Go to the DigitalOcean Dashboard -> **Apps** -> **Create App**.
2. Select **GitHub** as the source and select your repository.
3. For the source directory, type `/backend`.
4. Ensure **Auto Deploy** is checked.

### Step 2: Configure the Service

DigitalOcean will automatically detect the `Dockerfile`.
1. Make sure the HTTP port is set to **8000**.
2. Set the Run Command (if it asks, but the Dockerfile `CMD` usually suffices): `uvicorn app.main:app --host 0.0.0.0 --port 8000`
3. Choose a plan. The basic $5/mo container is enough.

### Step 3: Deploy

Click **Review** and then **Create App**. DigitalOcean will build your Docker image and deploy it. 

Your backend will be available at a URL like:
`https://your-app-name-abcde.ondigitalocean.app`

For WebSockets in your frontend/app, connect to:
`wss://your-app-name-abcde.ondigitalocean.app/ws/{room_id}`

### Benefits of App Platform
- **Zero Server Config**: No SSH or bash scripts required.
- **Auto SSL**: It provides a secure `https://` and `wss://` endpoint immediately, which is required for secure WebSockets on modern platforms (iOS/Android/Web).
- **Auto Deployment**: When you push to the `main` branch, it deploys the update automatically.
