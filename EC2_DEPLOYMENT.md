# EC2 Deployment Guide - Least Count Rush Backend

This guide will help you deploy the Least Count Rush Backend on Amazon EC2 with continuous running capability.

## Prerequisites

- An EC2 instance running Amazon Linux or Ubuntu
- SSH access to your EC2 instance
- Basic knowledge of Linux commands

## Quick Start (One-Click Deployment)

### Step 1: Upload Backend Files to EC2

You can use `scp` to upload your backend folder:

**For Amazon Linux:**
```bash
# From your local machine
scp -r backend/ ec2-user@your-ec2-ip:/home/ec2-user/least-count-rush-backend/
```

**For Ubuntu:**
```bash
# From your local machine
scp -r backend/ ubuntu@your-ec2-ip:/home/ubuntu/least-count-rush-backend/
```

Or use `rsync` for better efficiency:

**For Amazon Linux:**
```bash
rsync -avz backend/ ec2-user@your-ec2-ip:/home/ec2-user/least-count-rush-backend/backend/
```

**For Ubuntu:**
```bash
rsync -avz backend/ ubuntu@your-ec2-ip:/home/ubuntu/least-count-rush-backend/backend/
```

### Step 2: SSH into Your EC2 Instance

**For Amazon Linux:**
```bash
ssh ec2-user@your-ec2-ip
```

**For Ubuntu:**
```bash
ssh ubuntu@your-ec2-ip
```

### Step 3: Run the Deployment Script

**For Amazon Linux:**
```bash
cd /home/ec2-user/least-count-rush-backend/backend
sudo chmod +x deploy_ec2.sh
sudo ./deploy_ec2.sh
```

**For Ubuntu:**
```bash
cd /home/ubuntu/least-count-rush-backend/backend
sudo chmod +x deploy_ec2.sh
sudo ./deploy_ec2.sh
```

**Note:** The script automatically detects your OS (Amazon Linux or Ubuntu) and uses the appropriate package manager and user.

That's it! The backend will now:
- ✅ Run continuously in the background
- ✅ Auto-restart if it crashes
- ✅ Start automatically on server reboot
- ✅ Log all output to systemd journal

## What the Deployment Script Does

1. **Checks Python Installation** - Installs Python 3 if not present
2. **Creates Virtual Environment** - Sets up isolated Python environment
3. **Installs Dependencies** - Installs all packages from `requirements.txt`
4. **Configures Systemd Service** - Sets up service for continuous running
5. **Enables Auto-Start** - Configures service to start on boot
6. **Starts the Service** - Launches the backend immediately

## Managing the Service

Use the management script for easy control:

**For Amazon Linux:**
```bash
cd /home/ec2-user/least-count-rush-backend/backend
sudo chmod +x manage_service.sh
```

**For Ubuntu:**
```bash
cd /home/ubuntu/least-count-rush-backend/backend
sudo chmod +x manage_service.sh
```

# Start the service
sudo ./manage_service.sh start

# Stop the service
sudo ./manage_service.sh stop

# Restart the service
sudo ./manage_service.sh restart

# Check status
sudo ./manage_service.sh status

# View logs (live)
sudo ./manage_service.sh logs

# Enable auto-start on boot
sudo ./manage_service.sh enable

# Disable auto-start on boot
sudo ./manage_service.sh disable
```

## Manual Systemd Commands

You can also use systemd commands directly:

```bash
# Check status
sudo systemctl status least-count-rush

# Start service
sudo systemctl start least-count-rush

# Stop service
sudo systemctl stop least-count-rush

# Restart service
sudo systemctl restart least-count-rush

# View logs
sudo journalctl -u least-count-rush -f

# View last 50 lines of logs
sudo journalctl -u least-count-rush -n 50

# Enable on boot
sudo systemctl enable least-count-rush

# Disable on boot
sudo systemctl disable least-count-rush
```

## EC2 Security Group Configuration

Make sure your EC2 security group allows inbound traffic on port 8000:

1. Go to EC2 Console → Security Groups
2. Select your instance's security group
3. Add inbound rule:
   - Type: Custom TCP
   - Port: 8000
   - Source: 0.0.0.0/0 (or your specific IP for security)

## Testing the Deployment

After deployment, test the backend:

```bash
# From your local machine or EC2 instance
curl http://your-ec2-ip:8000/health

# Should return:
# {"status":"healthy","service":"Least Count Rush Backend","version":"1.0.0"}
```

## Updating the Backend

When you need to update the code:

1. **Upload new files** to EC2 (using scp/rsync)
2. **Restart the service**:
   ```bash
   sudo ./manage_service.sh restart
   ```

Or if you need to update dependencies:

**For Amazon Linux:**
```bash
cd /home/ec2-user/least-count-rush-backend/backend
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart least-count-rush
```

**For Ubuntu:**
```bash
cd /home/ubuntu/least-count-rush-backend/backend
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart least-count-rush
```

## Troubleshooting

### Service won't start

Check the logs:
```bash
sudo journalctl -u least-count-rush -n 50
```

Common issues:
- **Port already in use**: Another process is using port 8000
  ```bash
  sudo lsof -i :8000
  sudo kill -9 <PID>
  ```
- **Python dependencies missing**: Re-run deployment script
- **Permission issues**: Make sure service file has correct paths

### Service keeps restarting

Check logs for errors:
```bash
sudo journalctl -u least-count-rush -f
```

### Can't access from outside EC2

1. Check security group allows port 8000
2. Check EC2 instance firewall (if enabled):
   ```bash
   sudo ufw status
   sudo ufw allow 8000/tcp
   ```

## Production Recommendations

1. **Use a Reverse Proxy** (Nginx/Apache) for better security and SSL
2. **Set up SSL Certificate** (Let's Encrypt) for HTTPS
3. **Configure Firewall** to restrict access
4. **Set up Monitoring** (CloudWatch, etc.)
5. **Regular Backups** of your code and data
6. **Use Environment Variables** for sensitive configuration

## Example Nginx Configuration

If you want to use Nginx as a reverse proxy:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## File Structure on EC2

After deployment, your EC2 instance will have:

**For Amazon Linux:**
```
/home/ec2-user/least-count-rush-backend/
└── backend/
    ├── app/
    ├── frontend/
    ├── venv/              # Virtual environment
    ├── requirements.txt
    ├── deploy_ec2.sh      # Deployment script
    ├── manage_service.sh   # Management script
    └── least-count-rush.service  # Systemd service file
```

**For Ubuntu:**
```
/home/ubuntu/least-count-rush-backend/
└── backend/
    ├── app/
    ├── frontend/
    ├── venv/              # Virtual environment
    ├── requirements.txt
    ├── deploy_ec2.sh      # Deployment script
    ├── manage_service.sh   # Management script
    └── least-count-rush.service  # Systemd service file
```

## Support

If you encounter issues:
1. Check the logs: `sudo journalctl -u least-count-rush -f`
2. Verify service status: `sudo systemctl status least-count-rush`
3. Check network connectivity: `curl http://localhost:8000/health`

