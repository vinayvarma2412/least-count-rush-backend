"""
Rate Us API routes.

Endpoints:
  GET  /api/rate-us/status    — check if popup should be shown
  POST /api/rate-us/rate-now  — user tapped "Rate Now"
  POST /api/rate-us/dismiss   — user tapped "Not Now"
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.api.dependencies import get_current_db_user
from app.models.db_models import User
from app.services import rate_us_service

router = APIRouter(prefix="/api/rate-us", tags=["Rate Us"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class RateUsStatusResponse(BaseModel):
    wins_since_dismissed: int
    rated_at: Optional[datetime]


class RateUsActionResponse(BaseModel):
    success: bool


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status", response_model=RateUsStatusResponse)
async def get_rate_us_status(
    current_user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Returns the user's Rate Us counters.

    The Flutter client reads `rate_us_wins_threshold` from Firebase Remote
    Config (default 5) and computes whether to show the popup:
      should_show = (rated_at IS NULL) AND (wins_since_dismissed >= threshold)

    This mirrors how ads daily cap works: backend returns counts, RC has cap,
    client decides.
    """
    status = await rate_us_service.get_rate_us_status(current_user.user_idn, db)
    return RateUsStatusResponse(**status)


@router.post("/rate-now", response_model=RateUsActionResponse)
async def rate_now(
    current_user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Called when user taps "Rate Now".
    Sets rated_at = NOW() and resets wins_since_dismissed = 0.
    The popup will never be shown again for this user.
    """
    success = await rate_us_service.record_rate_now(current_user.user_idn, db)
    return RateUsActionResponse(success=success)


@router.post("/dismiss", response_model=RateUsActionResponse)
async def dismiss(
    current_user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Called when user taps "Not Now".
    Resets wins_since_dismissed = 0.
    The popup will show again after the next 5 wins.
    """
    success = await rate_us_service.record_dismiss(current_user.user_idn, db)
    return RateUsActionResponse(success=success)
