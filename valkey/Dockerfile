FROM valkey/valkey:7.2.5

# Start Valkey and bind to IPv6 (Fly's internal network uses IPv6)
# and use the password passed via the VALKEY_PASSWORD secret.
CMD ["sh", "-c", "exec valkey-server --bind :: --requirepass \"$VALKEY_PASSWORD\""]
