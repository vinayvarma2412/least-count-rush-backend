#!/bin/bash
# Script to get EC2 instance Public DNS
# Run this on your EC2 instance

echo "=========================================="
echo "EC2 Public DNS Information"
echo "=========================================="
echo ""

# Get public DNS
PUBLIC_DNS=$(curl -s http://169.254.169.254/latest/meta-data/public-hostname 2>/dev/null)
PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null)

if [ -n "$PUBLIC_DNS" ]; then
    echo "Public DNS (Hostname):"
    echo "  $PUBLIC_DNS"
    echo ""
    echo "You can try using this for HTTPS setup:"
    echo "  sudo ./setup_https.sh $PUBLIC_DNS"
    echo ""
    echo "⚠️  Note: Let's Encrypt may reject AWS public DNS names."
    echo "   If certbot fails, use self-signed certificate instead."
else
    echo "Could not retrieve public DNS. You may be in a private subnet."
fi

echo ""
if [ -n "$PUBLIC_IP" ]; then
    echo "Public IPv4 Address: $PUBLIC_IP"
fi

echo ""
echo "To get this from AWS Console:"
echo "  EC2 → Instances → Your Instance → 'Public IPv4 DNS' column"
echo "=========================================="

