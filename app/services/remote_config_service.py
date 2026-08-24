import os
import logging
from typing import Dict, Any, Tuple
import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

class RemoteConfigService:
    def __init__(self):
        sa_key_path = os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS",
            os.path.join(os.path.dirname(__file__), "..", "..", "serviceAccountKey.json"),
        )
        self.sa_key_path = os.path.normpath(sa_key_path)
        self._creds = None
        self.project_id = None

    def _get_credentials(self):
        if not self._creds:
            self._creds = service_account.Credentials.from_service_account_file(
                self.sa_key_path,
                scopes=['https://www.googleapis.com/auth/firebase.remoteconfig']
            )
            self.project_id = self._creds.project_id
        # Refresh if needed
        if not self._creds.valid:
            self._creds.refresh(GoogleAuthRequest())
        return self._creds

    async def get_template(self) -> Tuple[Dict[str, Any], str]:
        """
        Fetches the current Remote Config template.
        Returns a tuple: (template_dict, etag)
        """
        creds = self._get_credentials()
        token = creds.token

        url = f"https://firebaseremoteconfig.googleapis.com/v1/projects/{self.project_id}/remoteConfig"
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept-Encoding': 'gzip'
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
            etag = response.headers.get("ETag")
            return response.json(), etag

    async def publish_template(self, template_data: Dict[str, Any], etag: str) -> Dict[str, Any]:
        """
        Publishes a new Remote Config template.
        Returns the updated template.
        """
        creds = self._get_credentials()
        token = creds.token

        url = f"https://firebaseremoteconfig.googleapis.com/v1/projects/{self.project_id}/remoteConfig"
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json; UTF8',
            'If-Match': etag
        }

        async with httpx.AsyncClient() as client:
            response = await client.put(url, headers=headers, json=template_data)
            if response.status_code != 200:
                logger.error(f"Failed to publish remote config: {response.text}")
                response.raise_for_status()
            
            return response.json()

remote_config_service = RemoteConfigService()
