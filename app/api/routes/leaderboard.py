from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc, func
from typing import List, Optional

from app.database import get_db_session
from app.api.dependencies import get_current_firebase_user, get_current_db_user
from app.models.db_models import (
    UserLeaderboardStat, 
    SeasonLeaderboardStat, 
    LeaderboardSeason,
    User
)

router = APIRouter()

@router.get("/seasons")
async def get_seasons(db: AsyncSession = Depends(get_db_session), user: dict = Depends(get_current_firebase_user)):
    """Fetch all leaderboard seasons."""
    result = await db.execute(
        select(LeaderboardSeason).order_by(desc(LeaderboardSeason.start_date))
    )
    seasons = result.scalars().all()
    return seasons

@router.get("/all-time")
async def get_all_time_leaderboard(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_db_user)
):
    """Fetch the all-time global leaderboard."""
    # Using float cast for win percentage logic
    win_pct = (UserLeaderboardStat.games_won * 100.0) / func.nullif(UserLeaderboardStat.games_played, 0)
    
    stmt = (
        select(UserLeaderboardStat, User, win_pct.label('win_percentage'))
        .join(User, UserLeaderboardStat.user_idn == User.user_idn)
        .where(UserLeaderboardStat.games_played >= 5)
        .order_by(
            desc(UserLeaderboardStat.total_points),
            desc(UserLeaderboardStat.games_won),
            desc('win_percentage'),
            desc(UserLeaderboardStat.top_3_finishes),
            UserLeaderboardStat.games_played,
            UserLeaderboardStat.user_idn
        )
        .limit(limit)
        .offset(offset)
    )
    
    result = await db.execute(stmt)
    rows = result.all()
    
    leaderboard = []
    for stat, row_user, win_p in rows:
        leaderboard.append({
            "user_id": row_user.user_id,
            "display_name": row_user.display_name,
            "avatar_seed": row_user.avatar_seed,
            "total_points": stat.total_points,
            "games_played": stat.games_played,
            "games_won": stat.games_won,
            "top_3_finishes": stat.top_3_finishes,
            "win_percentage": float(win_p) if win_p else 0.0,
            "current_streak": stat.current_streak,
            "longest_win_streak": stat.longest_win_streak,
            "best_tournament_win_limit": stat.best_tournament_win_limit
        })
        
    
    # Calculate current user rank
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
        select(subq.c.rank, UserLeaderboardStat, User, win_pct.label('win_percentage'))
        .select_from(subq)
        .join(UserLeaderboardStat, UserLeaderboardStat.user_idn == subq.c.user_idn)
        .join(User, User.user_idn == UserLeaderboardStat.user_idn)
        .where(subq.c.user_idn == user.user_idn)
    )

    current_user_result = await db.execute(current_user_rank_stmt)
    current_user_row = current_user_result.first()
    
    current_user_data = None
    if current_user_row:
        rank, stat, u, win_p = current_user_row
        current_user_data = {
            "rank": rank,
            "entry": {
                "user_id": u.user_id,
                "display_name": u.display_name,
                "avatar_seed": u.avatar_seed,
                "total_points": stat.total_points,
                "games_played": stat.games_played,
                "games_won": stat.games_won,
                "top_3_finishes": stat.top_3_finishes,
                "win_percentage": float(win_p) if win_p else 0.0,
                "current_streak": stat.current_streak,
                "longest_win_streak": stat.longest_win_streak,
                "best_tournament_win_limit": stat.best_tournament_win_limit
            }
        }
        
    return {"leaderboard": leaderboard, "current_user": current_user_data}

@router.get("/season/{season_idn}")
async def get_season_leaderboard(
    season_idn: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_db_user)
):
    """Fetch the leaderboard for a specific season."""
    win_pct = (SeasonLeaderboardStat.games_won * 100.0) / func.nullif(SeasonLeaderboardStat.games_played, 0)
    
    stmt = (
        select(SeasonLeaderboardStat, User, win_pct.label('win_percentage'))
        .join(User, SeasonLeaderboardStat.user_idn == User.user_idn)
        .where(SeasonLeaderboardStat.season_idn == season_idn)
        .where(SeasonLeaderboardStat.games_played >= 5)
        .order_by(
            desc(SeasonLeaderboardStat.total_points),
            desc(SeasonLeaderboardStat.games_won),
            desc('win_percentage'),
            desc(SeasonLeaderboardStat.top_3_finishes),
            SeasonLeaderboardStat.games_played,
            SeasonLeaderboardStat.user_idn
        )
        .limit(limit)
        .offset(offset)
    )
    
    result = await db.execute(stmt)
    rows = result.all()
    
    leaderboard = []
    for stat, row_user, win_p in rows:
        leaderboard.append({
            "user_id": row_user.user_id,
            "display_name": row_user.display_name,
            "avatar_seed": row_user.avatar_seed,
            "total_points": stat.total_points,
            "games_played": stat.games_played,
            "games_won": stat.games_won,
            "top_3_finishes": stat.top_3_finishes,
            "win_percentage": float(win_p) if win_p else 0.0,
            "current_streak": stat.current_streak,
            "longest_win_streak": stat.longest_win_streak,
            "best_tournament_win_limit": stat.best_tournament_win_limit
        })
        
    # Calculate current user rank
    subq = (
        select(
            SeasonLeaderboardStat.user_idn,
            func.row_number().over(
                order_by=[
                    desc(SeasonLeaderboardStat.total_points),
                    desc(SeasonLeaderboardStat.games_won),
                    desc(win_pct),
                    desc(SeasonLeaderboardStat.top_3_finishes),
                    SeasonLeaderboardStat.games_played,
                    SeasonLeaderboardStat.user_idn
                ]
            ).label('rank')
        )
        .where(SeasonLeaderboardStat.season_idn == season_idn)
        .where(SeasonLeaderboardStat.games_played >= 5)
        .subquery()
    )

    current_user_rank_stmt = (
        select(subq.c.rank, SeasonLeaderboardStat, User, win_pct.label('win_percentage'))
        .select_from(subq)
        .join(SeasonLeaderboardStat, SeasonLeaderboardStat.user_idn == subq.c.user_idn)
        .join(User, User.user_idn == SeasonLeaderboardStat.user_idn)
        .where(SeasonLeaderboardStat.season_idn == season_idn)
        .where(subq.c.user_idn == user.user_idn)
    )

    current_user_result = await db.execute(current_user_rank_stmt)
    current_user_row = current_user_result.first()
    
    current_user_data = None
    if current_user_row:
        rank, stat, u, win_p = current_user_row
        current_user_data = {
            "rank": rank,
            "entry": {
                "user_id": u.user_id,
                "display_name": u.display_name,
                "avatar_seed": u.avatar_seed,
                "total_points": stat.total_points,
                "games_played": stat.games_played,
                "games_won": stat.games_won,
                "top_3_finishes": stat.top_3_finishes,
                "win_percentage": float(win_p) if win_p else 0.0,
                "current_streak": stat.current_streak,
                "longest_win_streak": stat.longest_win_streak,
                "best_tournament_win_limit": stat.best_tournament_win_limit
            }
        }
        
    return {"leaderboard": leaderboard, "current_user": current_user_data}
