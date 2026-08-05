import argparse
import time
import os
import redis
from dotenv import load_dotenv

def test_redis_latency(url, num_requests=1000):
    print(f"🔌 Connecting to Redis at {url}...")
    try:
        r = redis.from_url(url)
        # Test connection
        r.ping()
        print("✅ Connected successfully.\n")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return

    # SET TEST
    print(f"🚀 Running {num_requests} SET operations...")
    set_latencies = []
    
    test_key = "latency_test_key"
    test_value = "x" * 256 # 256 bytes payload
    
    for i in range(num_requests):
        try:
            start_time = time.perf_counter()
            r.set(test_key, test_value)
            end_time = time.perf_counter()
            set_latencies.append((end_time - start_time) * 1000)
        except Exception as e:
            print(f"❌ SET failed at iteration {i}: {e}")
            break
            
    if set_latencies:
        print(f"📊 SET Results ({len(set_latencies)} successful):")
        print(f"  Average Latency: {sum(set_latencies)/len(set_latencies):.3f} ms")
        print(f"  Min Latency:     {min(set_latencies):.3f} ms")
        print(f"  Max Latency:     {max(set_latencies):.3f} ms")
    print()
    
    # GET TEST
    print(f"🚀 Running {num_requests} GET operations...")
    get_latencies = []
    
    for i in range(num_requests):
        try:
            start_time = time.perf_counter()
            r.get(test_key)
            end_time = time.perf_counter()
            get_latencies.append((end_time - start_time) * 1000)
        except Exception as e:
            print(f"❌ GET failed at iteration {i}: {e}")
            break
            
    if get_latencies:
        print(f"📊 GET Results ({len(get_latencies)} successful):")
        print(f"  Average Latency: {sum(get_latencies)/len(get_latencies):.3f} ms")
        print(f"  Min Latency:     {min(get_latencies):.3f} ms")
        print(f"  Max Latency:     {max(get_latencies):.3f} ms")

    # Cleanup
    try:
        r.delete(test_key)
    except:
        pass

if __name__ == "__main__":
    load_dotenv()
    default_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    
    parser = argparse.ArgumentParser(description="Test Redis/Valkey SET/GET Latency")
    parser.add_argument("--url", type=str, default=default_url, help=f"Redis URL (default: {default_url})")
    parser.add_argument("-n", "--num", type=int, default=1000, help="Number of requests (default: 1000)")
    
    args = parser.parse_args()
    
    test_redis_latency(args.url, args.num)
