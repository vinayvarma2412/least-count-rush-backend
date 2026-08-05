import asyncio
from dotenv import load_dotenv
load_dotenv()

from app.database import async_session
from sqlalchemy import text

async def check():
    async with async_session() as db:
        res = await db.execute(text("SELECT user_idn, email, user_type, user_id FROM users ORDER BY crt_dt DESC LIMIT 5"))
        for row in res:
            print(f"idn={row.user_idn} email={row.email} type={row.user_type} uid={row.user_id}")

asyncio.run(check())
