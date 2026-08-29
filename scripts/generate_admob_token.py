#!/usr/bin/env python3
"""
Run this script locally to generate an OAuth 2.0 refresh token for AdMob API.
Requires `google-auth-oauthlib` to be installed.
"""
import os
import sys
import json

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("google-auth-oauthlib not installed. Please run: pip install google-auth-oauthlib")
    sys.exit(1)

# Ensure you have the AdMob Report scope
SCOPES = ['https://www.googleapis.com/auth/admob.report']

def main():
    print("=" * 60)
    print(" AdMob API OAuth 2.0 Token Generator ")
    print("=" * 60)
    print("\nBefore running this, ensure you have an OAuth 2.0 Client ID (Desktop app) created in Google Cloud Console.")
    print("You can create one here: https://console.cloud.google.com/apis/credentials\n")

    client_id = input("Enter your Client ID: ").strip()
    client_secret = input("Enter your Client Secret: ").strip()

    if not client_id or not client_secret:
        print("Client ID and Secret are required!")
        sys.exit(1)

    # We need to construct a temporary client_secrets.json in memory or file
    client_config = {
        "installed": {
            "client_id": client_id,
            "project_id": "least-count-rush",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": client_secret,
            "redirect_uris": ["http://localhost"]
        }
    }

    try:
        # Save temp file
        with open("temp_client_secret.json", "w") as f:
            json.dump(client_config, f)
            
        flow = InstalledAppFlow.from_client_secrets_file(
            'temp_client_secret.json', 
            SCOPES
        )
        
        print("\nOpening web browser to authorize access...")
        creds = flow.run_local_server(port=0)
        
        print("\n" + "=" * 60)
        print("SUCCESS! Authorization complete.")
        print("=" * 60)
        print("\nAdd the following lines to your .env file in the backend folder:\n")
        print(f"ADMOB_CLIENT_ID={client_id}")
        print(f"ADMOB_CLIENT_SECRET={client_secret}")
        print(f"ADMOB_REFRESH_TOKEN={creds.refresh_token}")
        print("\n" + "=" * 60)

    except Exception as e:
        print(f"\nError: {e}")
    finally:
        if os.path.exists("temp_client_secret.json"):
            os.remove("temp_client_secret.json")

if __name__ == '__main__':
    main()
