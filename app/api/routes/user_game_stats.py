from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any
from app.database import get_db_session
from app.api.dependencies import get_current_db_user
from app.services.pg_game_stats_service import pg_game_stats_service
from app.models.db_models import User, GameTypeEnum

router = APIRouter(prefix="/api/users/me/game-stats", tags=["game_stats"])

@router.post("")
async def save_game_stats(
    stats: List[Dict[str, Any]],
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Batch save game stats for the current user.
    Called from the Flutter app to sync local SQLite stats.
    """
    if not stats:
        return {"status": "success", "message": "No stats provided", "count": 0}
        
    success = await pg_game_stats_service.save_game_stats_batch(db, user.user_idn, stats)
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save game stats")
        
    return {"status": "success", "count": len(stats)}

@router.get("")
async def get_game_stats(
    limit: int = 10,
    offset: int = 0,
    game_type: GameTypeEnum | None = None,
    year: int | None = None,
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get all game stats for the current user.
    Used for syncing back to the client.
    """
    stats = await pg_game_stats_service.get_game_stats_for_user(db, user.user_idn, limit, offset, game_type, year)
    return stats

@router.get("/summary")
async def get_game_stats_summary(
    game_type: GameTypeEnum | None = None,
    year: int | None = None,
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get aggregated summary game stats for the current user.
    """
    summary = await pg_game_stats_service.get_game_stats_summary_for_user(db, user.user_idn, game_type, year)
    return summary

@router.get("/achievements")
async def get_achievements(
    game_type: GameTypeEnum | None = None,
    year: int | None = None,
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get achievement progress counters for the current user.
    """
    achievements = await pg_game_stats_service.get_achievements_for_user(db, user.user_idn, game_type, year)
    return achievements

@router.delete("")
async def delete_game_stats(
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Delete all game stats for the current user.
    """
    success = await pg_game_stats_service.delete_game_stats_for_user(db, user.user_idn)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete game stats")
        
    return {"status": "success"}
