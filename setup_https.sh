#!/bin/bash
# HTTPS Setup Script for EC2
# This script helps set up HTTPS using nginx and Let's Encrypt

set -e  # Exit on error

echo "=========================================="
echo "HTTPS Setup Script for EC2"
echo "=========================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

# Check for domain name argument
if [ -z "$1" ]; then
    echo "Usage: sudo ./setup_https.sh <your-domain.com>"
    echo ""
    echo "Example:"
    echo "  sudo ./setup_https.sh api.leastcountrush.online"
    echo ""
    echo "Note: Your domain must point to this server's IP address via DNS A record"
    exit 1
fi

DOMAIN=$1
NGINX_CONFIG="/etc/nginx/sites-available/least-count-rush"

echo "Domain: $DOMAIN"
echo ""

# Detect OS and package manager
if command -v apt &> /dev/null; then
    # Ubuntu/Debian
    PKG_MANAGER="apt"
    UPDATE_CMD="apt update"
    INSTALL_CMD="apt install -y"
    NGINX_DIR="/etc/nginx/sites-available"
elif command -v yum &> /dev/null; then
    # Amazon Linux / CentOS / RHEL
    PKG_MANAGER="yum"
    UPDATE_CMD="yum update -y"
    INSTALL_CMD="yum install -y"
    NGINX_DIR="/etc/nginx/conf.d"
elif command -v dnf &> /dev/null; then
    # Fedora / Amazon Linux 2023
    PKG_MANAGER="dnf"
    UPDATE_CMD="dnf update -y"
    INSTALL_CMD="dnf install -y"
    NGINX_DIR="/etc/nginx/conf.d"
else
    echo "Error: Unsupported OS. This script supports Ubuntu, Debian, Amazon Linux, CentOS, and Fedora."
    exit 1
fi

echo "Detected OS: $PKG_MANAGER"
echo ""

# Step 1: Install nginx
echo "Step 1: Installing nginx..."
$UPDATE_CMD
$INSTALL_CMD nginx
systemctl start nginx
systemctl enable nginx
echo "✓ nginx installed"

# Step 2: Create initial nginx configuration
echo ""
echo "Step 2: Creating nginx configuration..."

# Create nginx config directory if it doesn't exist (for Amazon Linux)
if [ "$PKG_MANAGER" != "apt" ]; then
    NGINX_CONFIG="/etc/nginx/conf.d/least-count-rush.conf"
    mkdir -p /etc/nginx/conf.d
else
    NGINX_CONFIG="/etc/nginx/sites-available/least-count-rush"
    mkdir -p /etc/nginx/sites-available
    mkdir -p /etc/nginx/sites-enabled
fi

cat > "$NGINX_CONFIG" <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    # For Let's Encrypt verification
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    # Proxy to backend
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        
        # WebSocket support
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
}
EOF

# Enable the site (only for Ubuntu/Debian)
if [ "$PKG_MANAGER" = "apt" ]; then
    ln -sf "$NGINX_CONFIG" /etc/nginx/sites-enabled/least-count-rush
    rm -f /etc/nginx/sites-enabled/default
fi

# Test and reload nginx
nginx -t
systemctl reload nginx
echo "✓ nginx configured"

# Step 3: Install certbot
echo ""
echo "Step 3: Installing certbot..."

if [ "$PKG_MANAGER" = "apt" ]; then
    $INSTALL_CMD certbot python3-certbot-nginx
elif [ "$PKG_MANAGER" = "yum" ] || [ "$PKG_MANAGER" = "dnf" ]; then
    # For Amazon Linux, install certbot via pip (most reliable)
    echo "Installing certbot via pip for Amazon Linux..."
    
    # Install Python and pip
    $INSTALL_CMD python3 python3-pip python3-devel gcc openssl-devel libffi-devel || \
    $INSTALL_CMD python3 python3-pip || \
    $INSTALL_CMD python3
    
    # Upgrade pip
    python3 -m pip install --upgrade pip || pip3 install --upgrade pip || true
    
    # Install certbot and nginx plugin via pip
    pip3 install certbot certbot-nginx || python3 -m pip install certbot certbot-nginx
    
    # Verify installation
    if command -v certbot &> /dev/null || [ -f /usr/local/bin/certbot ]; then
        # Create symlink if certbot is in /usr/local/bin
        if [ -f /usr/local/bin/certbot ] && [ ! -f /usr/bin/certbot ]; then
            ln -sf /usr/local/bin/certbot /usr/bin/certbot
        fi
        echo "✓ certbot installed successfully"
        certbot --version || /usr/local/bin/certbot --version
    else
        echo "⚠️  Warning: certbot installation may have issues"
        echo "Trying to locate certbot..."
        find /usr -name certbot 2>/dev/null || echo "Certbot not found in standard locations"
    fi
fi

# Step 4: Obtain SSL certificate
echo ""
echo "Step 4: Obtaining SSL certificate from Let's Encrypt..."
echo "You will be prompted for:"
echo "  - Email address"
echo "  - Agreement to terms"
echo "  - Redirect HTTP to HTTPS (recommended: Yes)"
echo ""
read -p "Press Enter to continue with certbot..."

certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email admin@"$DOMAIN" --redirect || {
    echo ""
    echo "Certbot needs interactive mode. Running interactively..."
    certbot --nginx -d "$DOMAIN"
}

# Step 5: Test certificate renewal
echo ""
echo "Step 5: Testing certificate auto-renewal..."
certbot renew --dry-run
echo "✓ Certificate auto-renewal configured"

# Step 6: Final nginx reload
echo ""
echo "Step 6: Reloading nginx..."
nginx -t
systemctl reload nginx
echo "✓ nginx reloaded"

echo ""
echo "=========================================="
echo "HTTPS Setup Complete!"
echo "=========================================="
echo ""
echo "Your backend is now available at:"
echo "  https://$DOMAIN"
echo ""
echo "Test it with:"
echo "  curl https://$DOMAIN/health"
echo ""
echo "Next steps:"
echo "1. Update lib/app/config/api_config.dart to use: https://$DOMAIN"
echo "2. Update CORS settings in backend/app/main.py"
echo "3. Test WebSocket connections (wss://$DOMAIN/ws/{room_id})"
echo ""

