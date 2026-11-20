"""
Service for finding inactive players and scheduling re-engagement push notifications.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
import logging
from sqlalchemy import select, func, and_, or_, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import (
    User,
    UserDevice,
    Game,
    GamePlayer,
    Notification,
    NotifStatusEnum,
    UserRoleEnum,
)
from app.services import fcm_service

logger = logging.getLogger(__name__)


def calculate_habitual_schedule_time(last_game_time: datetime, now: Optional[datetime] = None) -> datetime:
    """
    Given a user's last game played timestamp (in UTC), return the next occurrence
    of that exact hour:minute in UTC.

    Because adding 24-hour periods preserves local time-of-day regardless of timezone,
    scheduling at the UTC hour:minute will fire at the user's exact habitual local time.

    If the time today has not passed yet (with a 5-minute buffer), schedule for today.
    Otherwise, schedule for tomorrow.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Candidate for today
    candidate = now.replace(
        hour=last_game_time.hour,
        minute=last_game_time.minute,
        second=0,
        microsecond=0,
    )

    # If it is at least 5 minutes in the future today, schedule today; else tomorrow
    if candidate > now + timedelta(minutes=5):
        return candidate
    else:
        return candidate + timedelta(days=1)


async def get_eligible_reengagement_users(
    db: AsyncSession,
    days_inactive: int = 1,
    cooldown_days: int = 2,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Find users whose latest game was played yesterday or earlier (>= days_inactive days ago),
    who have not played today, who are active real users, and who have not received a
    notification within the cooldown window.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # If days_inactive == 1, cutoff is today_start (i.e. played before today)
    cutoff = today_start - timedelta(days=max(0, days_inactive - 1))
    cooldown_cutoff = now - timedelta(days=cooldown_days)

    # 1. Subquery: latest game timestamp per user across all completed/active games
    game_time_expr = func.coalesce(Game.ended_at, Game.started_at, Game.crt_dt)
    latest_game_subq = (
        select(
            GamePlayer.user_idn.label("user_idn"),
            func.max(game_time_expr).label("last_game_played_at"),
        )
        .join(Game, GamePlayer.game_idn == Game.game_idn)
        .where(
            GamePlayer.entity_active == True,
            Game.entity_active == True,
        )
        .group_by(GamePlayer.user_idn)
        .subquery()
    )

    # 2. Subquery: users who were notified recently (cooldown)
    recent_notif_subq = (
        select(Notification.receiver_user_idn.label("receiver_user_idn"))
        .distinct()
        .where(
            Notification.receiver_user_idn.isnot(None),
            Notification.entity_active == True,
            Notification.status.in_([NotifStatusEnum.pending, NotifStatusEnum.sent]),
            or_(
                Notification.crt_dt >= cooldown_cutoff,
                and_(
                    Notification.schedule_to.isnot(None),
                    Notification.schedule_to >= cooldown_cutoff,
                ),
            ),
        )
        .subquery()
    )

    # 3. Query eligible users
    query = (
        select(
            User.user_idn,
            User.display_name,
            User.email,
            latest_game_subq.c.last_game_played_at,
        )
        .join(latest_game_subq, User.user_idn == latest_game_subq.c.user_idn)
        .where(
            User.entity_active == True,
            User.role == UserRoleEnum.user,
            latest_game_subq.c.last_game_played_at < cutoff,
            User.user_idn.notin_(select(recent_notif_subq.c.receiver_user_idn)),
        )
        .order_by(latest_game_subq.c.last_game_played_at.desc())
    )

    result = await db.execute(query)
    user_rows = result.all()
    if not user_rows:
        return []

    eligible_user_ids = [r.user_idn for r in user_rows]

    # 4. Check active device tokens for these users
    devices_query = (
        select(
            UserDevice.user_idn,
            func.count(UserDevice.user_device_idn).label("token_count"),
        )
        .where(
            UserDevice.user_idn.in_(eligible_user_ids),
            UserDevice.entity_active == True,
            UserDevice.fcm_token.isnot(None),
            UserDevice.fcm_token != "",
        )
        .group_by(UserDevice.user_idn)
    )
    dev_res = await db.execute(devices_query)
    user_token_counts = {row.user_idn: row.token_count for row in dev_res.all()}

    output = []
    for row in user_rows:
        token_count = user_token_counts.get(row.user_idn, 0)
        output.append({
            "user_idn": row.user_idn,
            "display_name": row.display_name or f"Player #{row.user_idn}",
            "email": row.email,
            "last_game_played_at": row.last_game_played_at,
            "has_fcm_token": token_count > 0,
            "fcm_token_count": token_count,
        })

    return output


async def get_reengagement_preview(
    db: AsyncSession,
    days_inactive: int = 1,
    cooldown_days: int = 2,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Return statistics and sample users for preview before scheduling.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    users = await get_eligible_reengagement_users(
        db, days_inactive=days_inactive, cooldown_days=cooldown_days, now=now
    )

    total_eligible = len(users)
    reachable_users = [u for u in users if u["has_fcm_token"]]
    reachable_count = len(reachable_users)
    users_without_token = total_eligible - reachable_count

    # Sample preview of first 10 users with habitual schedule time
    sample_users = []
    for u in reachable_users[:10]:
        habitual_time = calculate_habitual_schedule_time(u["last_game_played_at"], now=now)
        sample_users.append({
            "user_idn": u["user_idn"],
            "display_name": u["display_name"],
            "last_game_played_at": u["last_game_played_at"].isoformat() if u["last_game_played_at"] else None,
            "habitual_schedule_to": habitual_time.isoformat(),
        })

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = today_start - timedelta(days=max(0, days_inactive - 1))

    return {
        "total_eligible_users": total_eligible,
        "reachable_users_count": reachable_count,
        "users_without_token_count": users_without_token,
        "days_inactive": days_inactive,
        "cooldown_days": cooldown_days,
        "cutoff_time": cutoff.isoformat(),
        "sample_users": sample_users,
    }


async def schedule_reengagement_notifications(
    db: AsyncSession,
    title: str,
    description: Optional[str] = None,
    days_inactive: int = 1,
    time_strategy: str = "habitual",
    fixed_schedule_to: Optional[datetime] = None,
    cooldown_days: int = 2,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Schedule re-engagement notifications for all eligible reachable users.

    - time_strategy:
      - 'habitual': computes each user's next occurrence matching their last game's hour:minute
      - 'fixed_time': schedules all users at fixed_schedule_to
      - 'immediate': sends immediately
    """
    if now is None:
        now = datetime.now(timezone.utc)
    users = await get_eligible_reengagement_users(
        db, days_inactive=days_inactive, cooldown_days=cooldown_days, now=now
    )

    reachable_users = [u for u in users if u["has_fcm_token"]]
    if not reachable_users:
        return {
            "success": True,
            "scheduled_count": 0,
            "reachable_count": 0,
            "total_eligible_count": len(users),
            "message": "No reachable users found with active FCM tokens.",
        }

    scheduled_notifs: List[Notification] = []

    for u in reachable_users:
        target_schedule: Optional[datetime] = None

        if time_strategy == "habitual":
            target_schedule = calculate_habitual_schedule_time(u["last_game_played_at"], now=now)
        elif time_strategy == "fixed_time":
            target_schedule = fixed_schedule_to or now
        elif time_strategy == "immediate":
            target_schedule = None

        notif = Notification(
            receiver_user_idn=u["user_idn"],
            title=title,
            description=description,
            status=NotifStatusEnum.pending,
            schedule_to=target_schedule,
        )
        db.add(notif)
        scheduled_notifs.append(notif)

    await db.commit()

    # For immediate strategy, dispatch right away
    if time_strategy == "immediate":
        for notif in scheduled_notifs:
            try:
                await fcm_service.dispatch_notification(notif.notification_idn, db)
            except Exception as exc:
                logger.error(
                    "immediate_reengagement_dispatch_failed",
                    extra={"notification_idn": notif.notification_idn, "error": str(exc)},
                )

    return {
        "success": True,
        "scheduled_count": len(scheduled_notifs),
        "reachable_count": len(reachable_users),
        "total_eligible_count": len(users),
        "time_strategy": time_strategy,
        "message": f"Successfully scheduled {len(scheduled_notifs)} re-engagement notifications.",
    }
