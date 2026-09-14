import sys
import os
import asyncio

sys.path.append(os.getcwd())
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from app.models.db_models import User
from app.api.routes.leaderboard import get_all_time_leaderboard

async def main():
    engine = create_async_engine("postgresql+asyncpg://postgres:9014392979Kk!@db.hddxzsrgxkyudbdjabfj.supabase.co:5432/postgres")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        user = await db.execute(select(User).limit(1))
        u = user.scalar()
        
        # Test offset 0
        res1 = await get_all_time_leaderboard(limit=20, offset=0, db=db, user=u)
        print("Offset 0 rank:", res1['current_user']['rank'] if res1['current_user'] else "None")
        
        # Test offset 20
        res2 = await get_all_time_leaderboard(limit=20, offset=20, db=db, user=u)
        print("Offset 20 rank:", res2['current_user']['rank'] if res2['current_user'] else "None")

asyncio.run(main())
