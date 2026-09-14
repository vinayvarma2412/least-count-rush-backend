import sys
import os

sys.path.append(os.getcwd())
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy import desc, func
from app.models.db_models import UserLeaderboardStat, User

user_idn = 332
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

print(current_user_rank_stmt)
