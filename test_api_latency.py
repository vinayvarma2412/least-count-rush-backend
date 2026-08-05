import argparse
import sys
import time
import requests

FIREBASE_API_KEY = "AIzaSyAtmW1z3onarz23gB7XAHLyxuc6x9l69Z0"
ADMIN_EMAIL = "vinay@admin.com"
ADMIN_PASSWORD = "1234567890"

def get_firebase_token():
    print("🔑 Fetching Firebase ID token...")
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"
    payload = {
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "returnSecureToken": True
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        return response.json()['idToken']
    except requests.exceptions.RequestException as e:
        print(f"❌ Auth failed: {e}")
        if getattr(e, 'response', None) is not None:
            print(e.response.text)
        sys.exit(1)

def test_latency(url, token, num_requests=10, method="GET"):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    print(f"\n🚀 Testing latency for {url} ({num_requests} requests)")
    latencies = []
    
    # Use Session for connection pooling (reuses TCP/TLS connections)
    session = requests.Session()
    session.headers.update(headers)
    
    for i in range(num_requests):
        try:
            start_time = time.time()
            response = session.request(method, url)
            
            # Consume the response content to ensure it's fully downloaded
            _ = response.content
            
            end_time = time.time()
            
            latency = (end_time - start_time) * 1000 # in ms
            latencies.append(latency)
            
            if response.ok:
                print(f"[{i+1:02d}/{num_requests}] ✅ HTTP {response.status_code} - Latency: {latency:6.2f} ms")
            else:
                print(f"[{i+1:02d}/{num_requests}] ❌ HTTP {response.status_code}: {response.reason} ({latency:6.2f} ms)")
                
        except requests.exceptions.RequestException as e:
            print(f"[{i+1:02d}/{num_requests}] ❌ Request failed: {e}")
            
        time.sleep(0.5) # small delay to prevent rate-limiting ourselves too aggressively
    
    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)
        print("\n📊 Results:")
        print(f"  Total successful: {len(latencies)}/{num_requests}")
        print(f"  Average Latency:  {avg_latency:.2f} ms")
        print(f"  Min Latency:      {min_latency:.2f} ms")
        print(f"  Max Latency:      {max_latency:.2f} ms")
    else:
        print("\n❌ No successful requests to calculate latency.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test API Latency with Firebase Auth (Uses Connection Pooling)")
    parser.add_argument("url", help="API URL to test (e.g., https://leastcountrush.fly.dev/api/users/me)")
    parser.add_argument("-n", "--num", type=int, default=10, help="Number of requests (default: 10)")
    parser.add_argument("-m", "--method", type=str, default="GET", choices=["GET", "POST", "PUT", "DELETE"], help="HTTP method to use")
    
    args = parser.parse_args()
    
    token = get_firebase_token()
    print("✓ Token acquired")
    
    test_latency(args.url, token, args.num, args.method)
