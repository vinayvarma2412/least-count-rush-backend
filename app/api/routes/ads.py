"""
Ads API routes.

Endpoints:
  GET  /api/ads/status                         — user's ad-free flag + today's impression counts
  POST /api/ads/impression                      — log an ad impression
  PUT  /api/admin/ads/user/{user_idn}/ads-free  — admin: grant or revoke ad-free for a user
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.api.dependencies import get_current_db_user, get_admin_api_key
from app.models.db_models import AdPlacementEnum, User
from app.services import ads_service

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ads", tags=["Ads"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class AdsStatusResponse(BaseModel):
    ads_free: bool
    ads_free_until: Optional[datetime]
    today_counts: dict[str, int]


class RecordImpressionRequest(BaseModel):
    placement: AdPlacementEnum


class RecordImpressionResponse(BaseModel):
    placement: str
    today_count: int


class AdsFreeRequest(BaseModel):
    ads_free: bool
    ads_free_until: Optional[datetime] = None  # None = permanent


class AdsFreeResponse(BaseModel):
    user_idn: int
    ads_free: bool
    ads_free_until: Optional[datetime]

    class Config:
        from_attributes = True


# ── User endpoints ────────────────────────────────────────────────────────────

@router.get("/status", response_model=AdsStatusResponse)
async def get_ads_status(
    current_user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Returns the user's ad-free status and today's impression counts per placement.
    The Flutter client compares today_counts vs daily_cap from Firebase Remote Config
    to decide whether to show an ad.
    """
    free = await ads_service.is_ads_free(current_user.user_idn, db)
    counts = await ads_service.get_today_counts(current_user.user_idn, db)
    return AdsStatusResponse(
        ads_free=free,
        ads_free_until=current_user.ads_free_until,
        today_counts=counts,
    )


@router.post("/impression", response_model=RecordImpressionResponse)
async def record_impression(
    req: RecordImpressionRequest,
    current_user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Log one ad impression for the authenticated user.
    Call this AFTER an ad has been successfully shown/completed.
    Returns the updated count for today so the client can update its local state.
    """
    # If the user is ad-free we still accept the call but don't increment —
    # they shouldn't be seeing ads anyway; this is just a safety net.
    if await ads_service.is_ads_free(current_user.user_idn, db):
        counts = await ads_service.get_today_counts(current_user.user_idn, db)
        return RecordImpressionResponse(
            placement=req.placement.value,
            today_count=counts.get(req.placement.value, 0),
        )

    count = await ads_service.record_impression(current_user.user_idn, req.placement, db)
    return RecordImpressionResponse(placement=req.placement.value, today_count=count)


# ── Admin endpoints ───────────────────────────────────────────────────────────

admin_router = APIRouter(
    prefix="/api/admin/ads",
    tags=["Admin - Ads"],
    dependencies=[Depends(get_admin_api_key)],
)


@admin_router.put("/user/{user_idn}/ads-free", response_model=AdsFreeResponse)
async def set_ads_free(
    user_idn: int,
    req: AdsFreeRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Grant or revoke ad-free status for a user.

    - `ads_free: true, ads_free_until: null`  → permanent ad-free (subscription)
    - `ads_free: true, ads_free_until: <dt>`  → time-limited (e.g. trial)
    - `ads_free: false`                        → revoke, restore ads
    """
    try:
        if req.ads_free:
            user = await ads_service.grant_ads_free(user_idn, db, until=req.ads_free_until)
        else:
            user = await ads_service.revoke_ads_free(user_idn, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return AdsFreeResponse(
        user_idn=user.user_idn,
        ads_free=user.ads_free,
        ads_free_until=user.ads_free_until,
    )
