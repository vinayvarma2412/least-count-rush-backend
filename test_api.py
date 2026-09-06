import asyncio
from httpx import AsyncClient

async def test():
    async with AsyncClient() as client:
        # First we need to simulate the request. But wait, it needs authentication.
        pass
