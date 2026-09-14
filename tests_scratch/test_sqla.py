import asyncio
from sqlalchemy import select, desc, func
from app.models.db_models import UserLeaderboardStat, User, SeasonLeaderboardStat
from app.database import get_db_session

async def test():
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
                    UserLeaderboardStat.games_played
                ]
            ).label('rank')
        )
        .where(UserLeaderboardStat.games_played >= 5)
        .subquery()
    )

    current_user_rank_stmt = (
        select(subq.c.rank, UserLeaderboardStat, User, win_pct.label('win_percentage'))
        .select_from(subq)
        .join(UserLeaderboardStat, UserLeaderboardStat.user_idn == subq.c.user_idn)
        .join(User, User.user_idn == UserLeaderboardStat.user_idn)
        .where(subq.c.user_idn == 1)
    )
    print(current_user_rank_stmt)

asyncio.run(test())
