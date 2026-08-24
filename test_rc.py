import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account
import json

creds = service_account.Credentials.from_service_account_file(
    'serviceAccountKey.json',
    scopes=['https://www.googleapis.com/auth/firebase.remoteconfig']
)
creds.refresh(Request())
token = creds.token
project_id = creds.project_id

headers = {'Authorization': f'Bearer {token}', 'Accept-Encoding': 'gzip'}
response = requests.get(f'https://firebaseremoteconfig.googleapis.com/v1/projects/{project_id}/remoteConfig', headers=headers)
print(json.dumps(response.json(), indent=2))
