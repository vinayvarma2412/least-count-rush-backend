import asyncio
from app.database import AsyncSessionLocal
from app.services.pg_game_stats_service import pg_game_stats_service

async def test():
    async with AsyncSessionLocal() as db:
        try:
            stats = await pg_game_stats_service.get_game_stats_for_user(db, 6) # Using an arbitrary ID that probably doesn't exist
            print(f"Success! {len(stats)} stats retrieved.")
        except Exception as e:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
