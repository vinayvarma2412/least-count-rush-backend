import asyncio
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from datetime import datetime, timezone
import os

from app.models.db_models import DeletedUser

DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/least_count_test"
engine = create_async_engine(DATABASE_URL)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def test():
    async with async_session() as db:
        now = datetime.now(timezone.utc)
        email = "indirectconvey@gmail.com"
        print(f"Checking for email: {email}, now: {now}")
        try:
            result = await db.execute(
                select(DeletedUser)
                .where(DeletedUser.email == email)
                .where(DeletedUser.blocked_until > now)
            )
            record = result.scalars().first()
            if record:
                print(f"FOUND BLOCKED: {record.blocked_until}")
            else:
                print("NOT FOUND")
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")

asyncio.run(test())
