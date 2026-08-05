from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc, func
from typing import List, Optional

from app.database import get_db_session
from app.api.dependencies import get_current_firebase_user
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
    user: dict = Depends(get_current_firebase_user)
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
            UserLeaderboardStat.games_played
        )
        .limit(limit)
        .offset(offset)
    )
    
    result = await db.execute(stmt)
    rows = result.all()
    
    leaderboard = []
    for stat, user, win_p in rows:
        leaderboard.append({
            "user_id": user.user_id,
            "display_name": user.display_name,
            "avatar_seed": user.avatar_seed,
            "total_points": stat.total_points,
            "games_played": stat.games_played,
            "games_won": stat.games_won,
            "top_3_finishes": stat.top_3_finishes,
            "win_percentage": float(win_p) if win_p else 0.0,
            "current_streak": stat.current_streak,
            "longest_win_streak": stat.longest_win_streak,
            "best_tournament_win_limit": stat.best_tournament_win_limit
        })
        
    return {"leaderboard": leaderboard}

@router.get("/season/{season_idn}")
async def get_season_leaderboard(
    season_idn: int,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    user: dict = Depends(get_current_firebase_user)
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
            SeasonLeaderboardStat.games_played
        )
        .limit(limit)
        .offset(offset)
    )
    
    result = await db.execute(stmt)
    rows = result.all()
    
    leaderboard = []
    for stat, user, win_p in rows:
        leaderboard.append({
            "user_id": user.user_id,
            "display_name": user.display_name,
            "avatar_seed": user.avatar_seed,
            "total_points": stat.total_points,
            "games_played": stat.games_played,
            "games_won": stat.games_won,
            "top_3_finishes": stat.top_3_finishes,
            "win_percentage": float(win_p) if win_p else 0.0,
            "current_streak": stat.current_streak,
            "longest_win_streak": stat.longest_win_streak,
            "best_tournament_win_limit": stat.best_tournament_win_limit
        })
        
    return {"leaderboard": leaderboard}
