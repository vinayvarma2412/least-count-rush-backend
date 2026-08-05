import asyncio
from app.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(text("SELECT game_idn, result, winner_user_idn, created_user_idn FROM games ORDER BY game_idn DESC LIMIT 5"))
        for row in res:
            print(row)

asyncio.run(main())
