import sys
import os

sys.path.append(os.getcwd())
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy import desc, func
from app.models.db_models import UserLeaderboardStat, User

async def main():
    engine = create_async_engine("postgresql+asyncpg://postgres:9014392979Kk!@db.hddxzsrgxkyudbdjabfj.supabase.co:5432/postgres")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        res = await db.execute(select(UserLeaderboardStat).where(UserLeaderboardStat.games_played >= 5).limit(1))
        stat = res.scalar()
        if not stat:
            print("No users")
            return
        
        user_idn = stat.user_idn
        print("Testing for user_idn:", user_idn)
        
        win_pct = (UserLeaderboardStat.games_won * 100.0) / func.nullif(UserLeaderboardStat.games_played, 0)
        
        subq = (
            select(
                UserLeaderboardStat.user_idn,
                func.row_number().over(
                    order_by=[
                        desc(UserLeaderboardStat.total_points),
                        desc(UserLeaderboardStat.games_won),
                        desc(win_pct),
                        desc(UserLeaderboardStat.top_3_finishes),
                        UserLeaderboardStat.games_played,
                        UserLeaderboardStat.user_idn
                    ]
                ).label('rank')
            )
            .where(UserLeaderboardStat.games_played >= 5)
            .subquery()
        )
        
        current_user_rank_stmt = (
            select(subq.c.rank)
            .select_from(subq)
            .where(subq.c.user_idn == user_idn)
        )
        
        res1 = await db.execute(current_user_rank_stmt)
        print("Rank for user_idn", user_idn, "is:", res1.scalar())

asyncio.run(main())
