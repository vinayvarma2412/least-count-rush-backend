"""
Rate Us service: tracks when to show the Rate Us popup to users.

Rules:
  - rated_at IS NOT NULL  → never show again (user already rated)
  - wins_since_dismissed >= threshold (configured in Firebase Remote Config
    key `rate_us_wins_threshold`, default 5) → show popup on next win
  - "Rate Now" → set rated_at = NOW(), reset wins_since_dismissed = 0
  - "Not Now"  → reset wins_since_dismissed = 0 (re-triggers after threshold more wins)
  - Only online game wins count (offline games have no server-side user account)

Note: the wins threshold comparison is done by the Flutter client using
Firebase Remote Config so it can be changed without a backend deploy.
This service only persists and returns the raw counters.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import User
from app.utils.room_logger import global_log


async def get_rate_us_status(user_idn: int, db: AsyncSession) -> dict:
    """
    Returns the rate-us raw counters for a user.
    The Flutter client compares wins_since_dismissed vs the Firebase Remote
    Config key `rate_us_wins_threshold` (default 5) to decide whether to show
    the popup — same pattern as ads daily cap.

    Response: { wins_since_dismissed: int, rated_at: datetime | None }
    """
    user = await db.get(User, user_idn)
    if not user:
        return {"wins_since_dismissed": 0, "rated_at": None}

    return {
        "wins_since_dismissed": user.wins_since_dismissed,
        "rated_at": user.rated_at,
    }


async def record_rate_now(user_idn: int, db: AsyncSession) -> bool:
    """
    User tapped "Rate Now". Set rated_at and reset wins counter.
    Returns True on success.
    """
    try:
        user = await db.get(User, user_idn)
        if not user:
            return False
        user.rated_at = datetime.now(timezone.utc)
        user.wins_since_dismissed = 0
        user.upd_dt = datetime.now(timezone.utc)
        await db.commit()
        global_log.info("rate_us_rated", {"user_idn": user_idn})
        return True
    except Exception as exc:
        await db.rollback()
        global_log.error("rate_us_rate_error", {"user_idn": user_idn, "error": str(exc)})
        return False


async def record_dismiss(user_idn: int, db: AsyncSession) -> bool:
    """
    User tapped "Not Now". Reset wins counter so popup shows again after 5 wins.
    No-op if user has already rated.
    Returns True on success.
    """
    try:
        user = await db.get(User, user_idn)
        if not user or user.rated_at is not None:
            return True  # Already rated — nothing to do
        user.wins_since_dismissed = 0
        user.upd_dt = datetime.now(timezone.utc)
        await db.commit()
        global_log.info("rate_us_dismissed", {"user_idn": user_idn})
        return True
    except Exception as exc:
        await db.rollback()
        global_log.error("rate_us_dismiss_error", {"user_idn": user_idn, "error": str(exc)})
        return False


async def increment_wins_since_dismissed(user_idn: int, db: AsyncSession) -> None:
    """
    Increment wins_since_dismissed for a user.
    Called after every online win (only for unrated users).
    Non-fatal — failure must not crash the game.
    """
    try:
        user = await db.get(User, user_idn)
        if not user or user.rated_at is not None:
            return  # Already rated — no need to track
        user.wins_since_dismissed += 1
        user.upd_dt = datetime.now(timezone.utc)
        await db.commit()
        global_log.info(
            "rate_us_win_incremented",
            {"user_idn": user_idn, "wins_since_dismissed": user.wins_since_dismissed},
        )
    except Exception as exc:
        await db.rollback()
        global_log.error(
            "rate_us_increment_error", {"user_idn": user_idn, "error": str(exc)}
        )
