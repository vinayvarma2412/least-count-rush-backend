import asyncio
import os
from redis.asyncio import Redis

async def main():
    r = Redis.from_url(os.getenv("REDIS_URL", "redis://default:MueEcxqQoORWAYELWinnFtwLDkvKlqkL@altaria.proxy.rlwy.net:19301"))
    await r.flushdb()
    print("Redis flushed successfully!")

asyncio.run(main())
