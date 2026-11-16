from datetime import datetime, timedelta, timezone
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import IapTransaction, User, UserRoleEnum
from app.api.routes.admin import (
    get_dashboard_stats_iap,
    get_admin_iap_subscriptions,
    _normalize_plan_id,
)


@pytest_asyncio.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        admin_user = User(
            user_idn=1,
            user_id="admin-uid",
            user_name="admin",
            display_name="Admin User",
            email="admin@example.com",
            role=UserRoleEnum.admin,
        )
        regular_user = User(
            user_idn=2,
            user_id="user-uid",
            user_name="player1",
            display_name="Player One",
            email="player1@example.com",
            role=UserRoleEnum.user,
        )
        session.add_all([admin_user, regular_user])
        await session.commit()

    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_normalize_plan_id():
    assert _normalize_plan_id("yearly") == "yearly"
    assert _normalize_plan_id("ad_free_yearly") == "yearly"
    assert _normalize_plan_id("halfyearly") == "halfyearly"
    assert _normalize_plan_id("half-yearly") == "halfyearly"
    assert _normalize_plan_id("ad_free_6months") == "halfyearly"
    assert _normalize_plan_id("quarterly") == "quarterly"
    assert _normalize_plan_id("ad_free_3months") == "quarterly"
    assert _normalize_plan_id("monthly") == "monthly"
    assert _normalize_plan_id("ad_free_monthly") == "monthly"
    assert _normalize_plan_id(None, "ad_free_yearly") == "yearly"


@pytest.mark.asyncio
async def test_dashboard_stats_iap(test_session: AsyncSession):
    now = datetime.now(timezone.utc)
    # Add subscriptions for this month
    txns = [
        IapTransaction(
            user_idn=2,
            platform="android",
            product_id="ad_free_monthly",
            plan_id="yearly",
            purchase_token="tok_1",
            store_transaction_id="txn_1",
            verification_status="verified",
            source="purchase",
            purchase_dt=now,
            expires_dt=now + timedelta(days=365),
            crt_dt=now,
        ),
        IapTransaction(
            user_idn=2,
            platform="ios",
            product_id="ad_free_6months",
            plan_id="halfyearly",
            purchase_token="tok_2",
            store_transaction_id="txn_2",
            verification_status="verified",
            source="purchase",
            purchase_dt=now,
            expires_dt=now + timedelta(days=180),
            crt_dt=now,
        ),
        IapTransaction(
            user_idn=2,
            platform="android",
            product_id="ad_free_3months",
            plan_id="quarterly",
            purchase_token="tok_3",
            store_transaction_id="txn_3",
            verification_status="verified",
            source="purchase",
            purchase_dt=now,
            expires_dt=now + timedelta(days=90),
            crt_dt=now,
        ),
        IapTransaction(
            user_idn=2,
            platform="ios",
            product_id="ad_free_monthly",
            plan_id="monthly",
            purchase_token="tok_4",
            store_transaction_id="txn_4",
            verification_status="verified",
            source="purchase",
            purchase_dt=now,
            expires_dt=now + timedelta(days=30),
            crt_dt=now,
        ),
        # An expired transaction from 2 months ago
        IapTransaction(
            user_idn=2,
            platform="android",
            product_id="ad_free_monthly",
            plan_id="monthly",
            purchase_token="tok_5",
            store_transaction_id="txn_5",
            verification_status="expired",
            source="purchase",
            purchase_dt=now - timedelta(days=60),
            expires_dt=now - timedelta(days=30),
            crt_dt=now - timedelta(days=60),
        ),
    ]
    test_session.add_all(txns)
    await test_session.commit()

    admin_user = await test_session.get(User, 1)

    stats = await get_dashboard_stats_iap(
        tz_offset=0,
        current_user=admin_user,
        db=test_session,
    )

    assert stats.total_this_month == 4
    assert stats.yearly == 1
    assert stats.half_yearly == 1
    assert stats.quarterly == 1
    assert stats.monthly == 1
    assert stats.android_count == 2
    assert stats.ios_count == 2
    assert stats.active_subscriptions == 4
    assert stats.total_revenue_this_month == pytest.approx(10.96, rel=1e-2)
    assert stats.total_revenue_this_month_inr > 0
    assert stats.revenue_android_this_month == pytest.approx(6.98, rel=1e-2)
    assert stats.revenue_ios_this_month == pytest.approx(3.98, rel=1e-2)
    assert len(stats.monthly_history) >= 1

    # Check detail listing
    details = await get_admin_iap_subscriptions(
        tz_offset=0,
        month="this_month",
        current_user=admin_user,
        db=test_session,
    )
    assert details.total_count == 4
    assert details.total_revenue == pytest.approx(10.96, rel=1e-2)
    assert len(details.subscriptions) == 4
    assert details.subscriptions[0].user is not None
    assert details.subscriptions[0].user.user_name == "player1"
    assert details.subscriptions[0].formatted_price is not None

    # Filter by plan
    yearly_details = await get_admin_iap_subscriptions(
        tz_offset=0,
        month="this_month",
        plan="yearly",
        current_user=admin_user,
        db=test_session,
    )
    assert yearly_details.filtered_count == 1
    assert yearly_details.filtered_revenue == pytest.approx(4.99, rel=1e-2)
    assert yearly_details.subscriptions[0].normalized_plan == "yearly"

    # Filter by platform
    ios_details = await get_admin_iap_subscriptions(
        tz_offset=0,
        month="this_month",
        platform="ios",
        current_user=admin_user,
        db=test_session,
    )
    assert ios_details.filtered_count == 2
    for s in ios_details.subscriptions:
        assert s.platform == "ios"

    # Filter by search (username or transaction id)
    search_details = await get_admin_iap_subscriptions(
        tz_offset=0,
        month="this_month",
        search="txn_3",
        current_user=admin_user,
        db=test_session,
    )
    assert search_details.filtered_count == 1
    assert search_details.subscriptions[0].store_transaction_id == "txn_3"

    # Month 'all' includes older transactions
    all_details = await get_admin_iap_subscriptions(
        tz_offset=0,
        month="all",
        current_user=admin_user,
        db=test_session,
    )
    assert all_details.total_count == 5
    assert len(all_details.subscriptions) == 5
