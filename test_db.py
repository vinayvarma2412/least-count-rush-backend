import asyncio
from app.database import AsyncSessionLocal
from app.services.online_game_stats_service import online_game_stats_service

async def run():
    async with AsyncSessionLocal() as db:
        print("Got db session")
        firebase_uids = ["nsl3m6k5ICU4KqaaPmqhRYVjHdU2", "UxaKfsMwqSe7aosoxgtzB3QKDNw2"]
        res = await online_game_stats_service.resolve_user_idns(db, firebase_uids)
        print("Result:", res)

asyncio.run(run())
