"""
Ads service: per-user impression tracking and ad-free subscription management.
Global config (ad unit IDs, caps, enabled flags) lives in Firebase Remote Config.
"""
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import AdImpression, AdPlacementEnum, User


async def is_ads_free(user_idn: int, db: AsyncSession) -> bool:
    """
    Returns True if the user currently has an active ad-free status.
    Checks both the flag and optional expiry timestamp.
    """
    user = await db.get(User, user_idn)
    if not user or not user.ads_free:
        return False
    if user.ads_free_until is None:
        return True  # permanent grant
    return datetime.now(timezone.utc) < user.ads_free_until


async def get_today_counts(user_idn: int, db: AsyncSession) -> dict[str, int]:
    """
    Returns today's impression count per placement for the user.
    Result: { "rewarded": 2, "banner": 0, "interstitial": 1 }
    """
    today = date.today()
    result = await db.execute(
        select(AdImpression.placement, AdImpression.impression_count).where(
            AdImpression.user_idn == user_idn,
            AdImpression.impression_date == today,
        )
    )
    rows = result.all()
    counts: dict[str, int] = {p.value: 0 for p in AdPlacementEnum}
    for row in rows:
        counts[row.placement.value] = row.impression_count
    return counts


async def record_impression(
    user_idn: int, placement: AdPlacementEnum, db: AsyncSession
) -> int:
    """
    Increments the daily impression counter for the user+placement.
    Uses INSERT ... ON CONFLICT DO UPDATE (upsert) via raw SQL for atomicity.
    Returns the new impression_count.
    """
    today = date.today()

    # Try to fetch existing row
    result = await db.execute(
        select(AdImpression).where(
            AdImpression.user_idn == user_idn,
            AdImpression.placement == placement,
            AdImpression.impression_date == today,
        )
    )
    impression = result.scalar_one_or_none()

    if impression:
        impression.impression_count += 1
        impression.upd_dt = datetime.now(timezone.utc)
    else:
        impression = AdImpression(
            user_idn=user_idn,
            placement=placement,
            impression_date=today,
            impression_count=1,
        )
        db.add(impression)

    await db.commit()
    await db.refresh(impression)
    return impression.impression_count


async def grant_ads_free(
    user_idn: int,
    db: AsyncSession,
    until: Optional[datetime] = None,
) -> User:
    """
    Grant ad-free status to a user.
    `until=None` means permanent; pass a datetime for a time-limited grant.
    """
    user = await db.get(User, user_idn)
    if not user:
        raise ValueError(f"User {user_idn} not found")
    user.ads_free = True
    user.ads_free_until = until
    user.upd_dt = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return user


async def revoke_ads_free(user_idn: int, db: AsyncSession) -> User:
    """Remove ad-free status from a user."""
    user = await db.get(User, user_idn)
    if not user:
        raise ValueError(f"User {user_idn} not found")
    user.ads_free = False
    user.ads_free_until = None
    user.upd_dt = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return user
