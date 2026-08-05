import os
import firebase_admin
from firebase_admin import credentials, auth
from app.utils.room_logger import global_log

# Initialize Firebase Admin SDK
# Try to get credentials from JSON file or let the environment handle it (e.g. Fly.io secret)
try:
    if not firebase_admin._apps:
        # Check if GOOGLE_APPLICATION_CREDENTIALS is set in env
        cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            global_log.info("firebase_admin_initialized", {"source": "certificate_file"})
        else:
            # Fallback to default which uses GOOGLE_APPLICATION_CREDENTIALS automatically
            # Or if GOOGLE_APPLICATION_CREDENTIALS_BASE64 or _JSON is set, write to a temp file and use it
            cred_b64 = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_BASE64")
            cred_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
            
            if cred_b64 or cred_json:
                import json
                import tempfile
                import base64
                
                content = base64.b64decode(cred_b64).decode('utf-8') if cred_b64 else (cred_json or "")
                
                with tempfile.NamedTemporaryFile("w", delete=False) as f:
                    f.write(content)
                    temp_path = f.name
                cred = credentials.Certificate(temp_path)
                firebase_admin.initialize_app(cred)
                # Cleanup temp file securely later, or keep alive for container lifetime
                global_log.info("firebase_admin_initialized", {"source": "base64_or_json_env_var"})
            else:
                firebase_admin.initialize_app()
                global_log.info("firebase_admin_initialized", {"source": "default"})
except Exception as e:
    global_log.error("firebase_admin_init_failed", {"error": str(e)})


def verify_firebase_token(id_token: str) -> dict:
    """
    Verify Firebase ID token and return decoded payload.
    Raises firebase_admin.auth.InvalidIdTokenError if invalid.

    Set LOAD_TEST_BYPASS_AUTH=1 to skip real Firebase verification
    (load testing / local dev only — never enable in production).
    """
    if os.getenv("LOAD_TEST_BYPASS_AUTH", "0") == "1":
        return {"uid": f"load_test_{id_token[:16]}"}
    try:
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token
    except Exception as e:
        global_log.warning("firebase_token_verification_failed", {"error": str(e)})
        raise
