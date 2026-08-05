#!/bin/bash
# Script to get EC2 instance IP address
# Run this on your EC2 instance

echo "=========================================="
echo "EC2 IP Address Information"
echo "=========================================="
echo ""

# Get public IP
PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null)
if [ -n "$PUBLIC_IP" ]; then
    echo "Public IPv4 Address: $PUBLIC_IP"
    echo ""
    echo "Use this in your Flutter app:"
    echo "  http://$PUBLIC_IP:8000"
    echo ""
    echo "Or update api_config.dart line 13 to:"
    echo "  return 'http://$PUBLIC_IP:8000';"
else
    echo "Could not retrieve public IP. You may be in a private subnet."
fi

echo ""
echo "Private IPv4 Address:"
PRIVATE_IP=$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4 2>/dev/null)
echo "$PRIVATE_IP"
echo ""

echo "Instance ID:"
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null)
echo "$INSTANCE_ID"
echo ""

echo "=========================================="
echo "To get Elastic IP (if allocated):"
echo "  aws ec2 describe-addresses --filters \"Name=instance-id,Values=$INSTANCE_ID\""
echo "=========================================="

