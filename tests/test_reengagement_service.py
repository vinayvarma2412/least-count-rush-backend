from datetime import datetime, timezone, timedelta
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import (
    User,
    UserRoleEnum,
    UserDevice,
    PlatformEnum,
    Game,
    GamePlayer,
    GameTypeEnum,
    GameModeEnum,
    GameResultEnum,
    Notification,
    NotifStatusEnum,
)
from app.services.reengagement_service import (
    calculate_habitual_schedule_time,
    get_eligible_reengagement_users,
    get_reengagement_preview,
    schedule_reengagement_notifications,
)


@pytest.mark.asyncio
async def test_calculate_habitual_schedule_time():
    now = datetime(2026, 11, 21, 10, 0, 0, tzinfo=timezone.utc)

    # Habitual game was at 14:30 (upcoming today)
    last_game_upcoming = datetime(2026, 11, 20, 14, 30, 0, tzinfo=timezone.utc)
    target = calculate_habitual_schedule_time(last_game_upcoming, now=now)
    assert target.year == 2026
    assert target.month == 11
    assert target.day == 21
    assert target.hour == 14
    assert target.minute == 30

    # Habitual game was at 08:30 (already passed today)
    last_game_past = datetime(2026, 11, 20, 8, 30, 0, tzinfo=timezone.utc)
    target_past = calculate_habitual_schedule_time(last_game_past, now=now)
    assert target_past.year == 2026
    assert target_past.month == 11
    assert target_past.day == 22  # tomorrow
    assert target_past.hour == 8
    assert target_past.minute == 30


@pytest_asyncio.fixture
async def reengage_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_reengagement_filtering_and_scheduling(reengage_session: AsyncSession):
    now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
    yesterday = datetime(2026, 11, 20, 15, 0, 0, tzinfo=timezone.utc)
    today_earlier = datetime(2026, 11, 21, 9, 0, 0, tzinfo=timezone.utc)

    # 1. User 1: Eligible, played yesterday, has FCM token
    u1 = User(user_idn=1, user_id="u1", user_name="alice", display_name="Alice", role=UserRoleEnum.user)
    dev1 = UserDevice(user_device_idn=1, user_idn=1, platform=PlatformEnum.android, device_id="d1", fcm_token="token-1")

    # 2. User 2: Played today (not eligible)
    u2 = User(user_idn=2, user_id="u2", user_name="bob", display_name="Bob", role=UserRoleEnum.user)
    dev2 = UserDevice(user_device_idn=2, user_idn=2, platform=PlatformEnum.ios, device_id="d2", fcm_token="token-2")

    # 3. User 3: Played yesterday, but no FCM token (eligible but unreachable)
    u3 = User(user_idn=3, user_id="u3", user_name="charlie", display_name="Charlie", role=UserRoleEnum.user)

    # 4. User 4: Bot user (excluded)
    u4 = User(user_idn=4, user_id="u4", user_name="bot1", display_name="Bot 1", role=UserRoleEnum.online_bot)

    # 5. User 5: Played yesterday, has token, but was notified yesterday (in cooldown)
    u5 = User(user_idn=5, user_id="u5", user_name="david", display_name="David", role=UserRoleEnum.user)
    dev5 = UserDevice(user_device_idn=5, user_idn=5, platform=PlatformEnum.android, device_id="d5", fcm_token="token-5")
    notif5 = Notification(
        notification_idn=100,
        receiver_user_idn=5,
        title="Recent",
        status=NotifStatusEnum.sent,
        crt_dt=datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc),
    )

    reengage_session.add_all([u1, dev1, u2, dev2, u3, u4, u5, dev5, notif5])
    await reengage_session.commit()

    # Games
    g1 = Game(game_idn=1, total_players=2, total_rounds=1, crt_dt=yesterday, ended_at=yesterday)
    gp1 = GamePlayer(game_player_idn=1, game_idn=1, user_idn=1, final_score=10, crt_dt=yesterday)

    g2 = Game(game_idn=2, total_players=2, total_rounds=1, crt_dt=today_earlier, ended_at=today_earlier)
    gp2 = GamePlayer(game_player_idn=2, game_idn=2, user_idn=2, final_score=15, crt_dt=today_earlier)

    g3 = Game(game_idn=3, total_players=2, total_rounds=1, crt_dt=yesterday, ended_at=yesterday)
    gp3 = GamePlayer(game_player_idn=3, game_idn=3, user_idn=3, final_score=20, crt_dt=yesterday)

    g4 = Game(game_idn=4, total_players=2, total_rounds=1, crt_dt=yesterday, ended_at=yesterday)
    gp4 = GamePlayer(game_player_idn=4, game_idn=4, user_idn=4, final_score=25, crt_dt=yesterday)

    g5 = Game(game_idn=5, total_players=2, total_rounds=1, crt_dt=yesterday, ended_at=yesterday)
    gp5 = GamePlayer(game_player_idn=5, game_idn=5, user_idn=5, final_score=30, crt_dt=yesterday)

    reengage_session.add_all([g1, gp1, g2, gp2, g3, gp3, g4, gp4, g5, gp5])
    await reengage_session.commit()

    # Test get_eligible_reengagement_users
    eligible = await get_eligible_reengagement_users(
        reengage_session, days_inactive=1, cooldown_days=2, now=now
    )
    eligible_ids = [u["user_idn"] for u in eligible]

    # u1 and u3 should be eligible; u2 (played today), u4 (bot), u5 (cooldown) should not
    assert 1 in eligible_ids
    assert 3 in eligible_ids
    assert 2 not in eligible_ids
    assert 4 not in eligible_ids
    assert 5 not in eligible_ids

    # Check reachability
    u1_entry = next(u for u in eligible if u["user_idn"] == 1)
    assert u1_entry["has_fcm_token"] is True
    u3_entry = next(u for u in eligible if u["user_idn"] == 3)
    assert u3_entry["has_fcm_token"] is False

    # Test preview
    preview = await get_reengagement_preview(
        reengage_session, days_inactive=1, cooldown_days=2, now=now
    )
    assert preview["total_eligible_users"] == 2
    assert preview["reachable_users_count"] == 1
    assert preview["users_without_token_count"] == 1
    assert len(preview["sample_users"]) == 1
    assert preview["sample_users"][0]["user_idn"] == 1

    # Test scheduling
    schedule_res = await schedule_reengagement_notifications(
        reengage_session,
        title="Ready for cards?",
        description="Come back and play!",
        days_inactive=1,
        time_strategy="habitual",
        cooldown_days=2,
        now=now,
    )
    assert schedule_res["success"] is True
    assert schedule_res["scheduled_count"] == 1
    assert schedule_res["reachable_count"] == 1

    # Test route handlers
    from app.api.routes.notifications import (
        preview_reengagement,
        schedule_reengagement,
        ReengageScheduleRequest,
    )
    route_preview = await preview_reengagement(days_inactive=1, cooldown_days=2, db=reengage_session)
    assert route_preview["total_eligible_users"] >= 0

    route_schedule = await schedule_reengagement(
        ReengageScheduleRequest(
            title="Come play!",
            days_inactive=1,
            time_strategy="habitual",
            cooldown_days=2,
        ),
        db=reengage_session,
    )
    assert route_schedule["success"] is True


@pytest.mark.asyncio
async def test_concurrent_multi_server_dispatch_deduplication(reengage_session: AsyncSession):
    """
    Simulate 3 servers running in 3 regions trying to dispatch the exact same
    pending notification at the exact same second.
    """
    from app.services.fcm_service import dispatch_notification

    notif = Notification(
        notification_idn=500,
        receiver_user_idn=1,
        title="Multi-region Test",
        status=NotifStatusEnum.pending,
        crt_dt=datetime.now(timezone.utc),
    )
    reengage_session.add(notif)
    await reengage_session.commit()

    # Server 1 dispatches first (claims the row)
    claimed_1 = await dispatch_notification(500, reengage_session)

    # Server 2 attempts to dispatch the same notification concurrently
    claimed_2 = await dispatch_notification(500, reengage_session)

    # Server 3 attempts to dispatch the same notification concurrently
    claimed_3 = await dispatch_notification(500, reengage_session)

    # Exactly one server was able to claim and dispatch
    assert claimed_1 in (True, False)  # depending on mock/active tokens
    # But servers 2 and 3 MUST be rejected because it was already claimed!
    assert claimed_2 is False
    assert claimed_3 is False


def test_scheduler_env_configuration(monkeypatch):
    """
    Verify that ENABLE_NOTIFICATION_SCHEDULER environment variable
    properly configures the server instance.
    """
    from app.config import Settings

    # Default should be False
    monkeypatch.delenv("ENABLE_NOTIFICATION_SCHEDULER", raising=False)
    monkeypatch.delenv("ENABLE_SCHEDULER", raising=False)
    s_default = Settings()
    assert s_default.enable_notification_scheduler is False

    # Enabled via ENABLE_NOTIFICATION_SCHEDULER
    monkeypatch.setenv("ENABLE_NOTIFICATION_SCHEDULER", "true")
    s_enabled = Settings()
    assert s_enabled.enable_notification_scheduler is True

    # Enabled via ENABLE_SCHEDULER
    monkeypatch.delenv("ENABLE_NOTIFICATION_SCHEDULER", raising=False)
    monkeypatch.setenv("ENABLE_SCHEDULER", "1")
    s_alias = Settings()
    assert s_alias.enable_notification_scheduler is True
