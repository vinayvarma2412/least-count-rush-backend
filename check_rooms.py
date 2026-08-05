import requests
from test_api_latency import get_firebase_token

token = get_firebase_token()
res = requests.get("https://leastcountrush.fly.dev/api/rooms", headers={"Authorization": f"Bearer {token}"})
print(f"Status: {res.status_code}")
data = res.json()
print(f"Rooms count: {data.get('total', 'unknown')}")
print(f"Fly-Region: {res.headers.get('fly-region', 'unknown')}")
print(f"Via: {res.headers.get('via', 'unknown')}")
