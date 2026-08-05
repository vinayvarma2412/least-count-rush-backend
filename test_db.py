import asyncio
from app.database import async_session_maker
from sqlalchemy import text

async def main():
    async with async_session_maker() as session:
        result = await session.execute(text("SELECT user_idn, platform, fcm_token FROM user_devices"))
        for row in result.all():
            print(f"User: {row[0]}, Platform: {row[1]}, Token: {row[2][:15]}...")

asyncio.run(main())
