import asyncio
from redis.asyncio import Redis

async def main():
    try:
        r = Redis.from_url("redis://default:MueEcxqQoORWAYELWinnFtwLDkvKlqkL@altaria.proxy.rlwy.net:19301", socket_timeout=5)
        await r.ping()
        print("Redis is UP")
    except Exception as e:
        print(f"Redis error: {e}")

asyncio.run(main())
