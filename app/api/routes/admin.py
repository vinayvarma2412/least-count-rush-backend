from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func, desc
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
import logging
import httpx

from app.database import get_db_session
from app.api.dependencies import get_admin_api_key, get_current_db_user, _require_admin
from app.models.db_models import User, Message, MessageTypeEnum, UserDevice, Game, GamePlayer, GameResultEnum, GameTypeEnum, GameModeEnum, UserRoleEnum, LeaderboardSeason, AdImpression, AdPlacementEnum, IapTransaction
from app.services.iap_catalog import canonicalize_plan_id
from app.services import fcm_service
from app.services.redis_client import redis_client
from app.services.admob_reporting_service import admob_reporting_service

logger = logging.getLogger(__name__)

# ── Existing router: protected by X-Admin-API-Key header ─────────────────────
router = APIRouter(prefix="/api/admin", tags=["Admin"], dependencies=[Depends(get_admin_api_key)])

# ── New router: protected by Firebase auth + admin role ───────────────────────
firebase_router = APIRouter(prefix="/api/admin", tags=["Admin Dashboard"])


class AdminStatsUsersResponse(BaseModel):
    users_online: int
    total_users: int
    active_users_today: int

class AdminStatsGamesResponse(BaseModel):
    games_online_today: int
    games_offline_today: int
    games_in_progress_online: int
    games_in_progress_offline: int
    games_public_today: int = 0
    games_private_today: int = 0
    games_in_progress_public: int = 0
    games_in_progress_private: int = 0

class AdminStatsMessagesResponse(BaseModel):
    unread_messages: int

class AdminStatsAdsResponse(BaseModel):
    ad_rewarded_today: int = 0
    ad_interstitial_today: int = 0
    ad_banner_today: int = 0

class AppEarnings(BaseModel):
    total: float = 0.0
    ios: float = 0.0
    android: float = 0.0

class AdminStatsEarningsResponse(BaseModel):
    estimated_earnings_today: AppEarnings = AppEarnings()
    estimated_earnings_month: AppEarnings = AppEarnings()

@firebase_router.get("/dashboard/stats/users", response_model=AdminStatsUsersResponse)
async def get_dashboard_stats_users(
    tz_offset: int = Query(0, description="Timezone offset in minutes"),
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    from datetime import datetime, timezone, timedelta
    
    users_online_result = await db.execute(
        select(func.count()).where(User.is_online == True, User.entity_active == True)
    )
    users_online = users_online_result.scalar() or 0

    total_users_result = await db.execute(
        select(func.count()).where(User.entity_active == True)
    )
    total_users = total_users_result.scalar() or 0

    user_now = datetime.now(timezone.utc) + timedelta(minutes=tz_offset)
    user_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = user_today_start - timedelta(minutes=tz_offset)
    
    active_users_today_result = await db.execute(
        select(func.count()).where(
            User.last_active_date >= today_start,
            User.entity_active == True
        )
    )
    active_users_today = active_users_today_result.scalar() or 0

    return AdminStatsUsersResponse(
        users_online=users_online,
        total_users=total_users,
        active_users_today=active_users_today,
    )


@firebase_router.get("/dashboard/stats/games", response_model=AdminStatsGamesResponse)
async def get_dashboard_stats_games(
    tz_offset: int = Query(0, description="Timezone offset in minutes"),
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    from datetime import datetime, timezone, timedelta
    
    games_in_progress_online_result = await db.execute(
        select(func.count()).where(
            Game.result == GameResultEnum.in_progress,
            Game.game_type == GameTypeEnum.online,
            Game.entity_active == True,
        )
    )
    games_in_progress_online = games_in_progress_online_result.scalar() or 0

    games_in_progress_public_result = await db.execute(
        select(func.count()).where(
            Game.result == GameResultEnum.in_progress,
            Game.game_type == GameTypeEnum.online,
            Game.room_type == "public",
            Game.entity_active == True,
        )
    )
    games_in_progress_public = games_in_progress_public_result.scalar() or 0
    games_in_progress_private = max(0, games_in_progress_online - games_in_progress_public)

    games_in_progress_offline_result = await db.execute(
        select(func.count()).where(
            Game.result == GameResultEnum.in_progress,
            Game.game_type == GameTypeEnum.offline,
            Game.entity_active == True,
        )
    )
    games_in_progress_offline = games_in_progress_offline_result.scalar() or 0

    user_now = datetime.now(timezone.utc) + timedelta(minutes=tz_offset)
    user_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = user_today_start - timedelta(minutes=tz_offset)
    tomorrow_start = today_start + timedelta(days=1)
    
    games_online_today_result = await db.execute(
        select(func.count()).where(
            Game.entity_active == True,
            Game.game_type == GameTypeEnum.online,
            Game.crt_dt >= today_start,
            Game.crt_dt < tomorrow_start,
        )
    )
    games_online_today = games_online_today_result.scalar() or 0

    games_public_today_result = await db.execute(
        select(func.count()).where(
            Game.entity_active == True,
            Game.game_type == GameTypeEnum.online,
            Game.room_type == "public",
            Game.crt_dt >= today_start,
            Game.crt_dt < tomorrow_start,
        )
    )
    games_public_today = games_public_today_result.scalar() or 0
    games_private_today = max(0, games_online_today - games_public_today)

    games_offline_today_result = await db.execute(
        select(func.count()).where(
            Game.entity_active == True,
            Game.game_type == GameTypeEnum.offline,
            Game.crt_dt >= today_start,
            Game.crt_dt < tomorrow_start,
        )
    )
    games_offline_today = games_offline_today_result.scalar() or 0

    return AdminStatsGamesResponse(
        games_online_today=games_online_today,
        games_offline_today=games_offline_today,
        games_in_progress_online=games_in_progress_online,
        games_in_progress_offline=games_in_progress_offline,
        games_public_today=games_public_today,
        games_private_today=games_private_today,
        games_in_progress_public=games_in_progress_public,
        games_in_progress_private=games_in_progress_private,
    )


@firebase_router.get("/dashboard/stats/messages", response_model=AdminStatsMessagesResponse)
async def get_dashboard_stats_messages(
    admin_user_idn: int,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    unread_result = await db.execute(
        select(func.count()).where(
            Message.to_user_idn == admin_user_idn,
            Message.read_at.is_(None),
            Message.entity_active == True,
        )
    )
    unread_messages = unread_result.scalar() or 0
    return AdminStatsMessagesResponse(unread_messages=unread_messages)


@firebase_router.get("/dashboard/stats/ads", response_model=AdminStatsAdsResponse)
async def get_dashboard_stats_ads(
    tz_offset: int = Query(0, description="Timezone offset in minutes"),
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    from datetime import datetime, timezone, timedelta
    
    user_now = datetime.now(timezone.utc) + timedelta(minutes=tz_offset)
    user_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = user_today_start - timedelta(minutes=tz_offset)
    tomorrow_start = today_start + timedelta(days=1)
    
    ad_impressions_result = await db.execute(
        select(AdImpression.placement, func.sum(AdImpression.impression_count)).where(
            AdImpression.upd_dt >= today_start,
            AdImpression.upd_dt < tomorrow_start
        ).group_by(AdImpression.placement)
    )
    
    ad_impressions_rows = ad_impressions_result.all()
    ad_rewarded_today = 0
    ad_interstitial_today = 0
    ad_banner_today = 0
    ad_play_with_randoms_rewarded_today = 0
    ad_play_with_bot_rewarded_interstitial_today = 0
    
    for placement, total_count in ad_impressions_rows:
        if placement == AdPlacementEnum.rewarded:
            ad_rewarded_today = total_count or 0
        elif placement == AdPlacementEnum.interstitial:
            ad_interstitial_today = total_count or 0
        elif placement == AdPlacementEnum.banner:
            ad_banner_today = total_count or 0
        elif placement == AdPlacementEnum.play_with_randoms_rewarded:
            ad_play_with_randoms_rewarded_today = total_count or 0
        elif placement == AdPlacementEnum.play_with_bot_rewarded_interstitial:
            ad_play_with_bot_rewarded_interstitial_today = total_count or 0

    return AdminStatsAdsResponse(
        ad_rewarded_today=ad_rewarded_today,
        ad_interstitial_today=ad_interstitial_today,
        ad_banner_today=ad_banner_today,
        ad_play_with_randoms_rewarded_today=ad_play_with_randoms_rewarded_today,
        ad_play_with_bot_rewarded_interstitial_today=ad_play_with_bot_rewarded_interstitial_today,
    )


@firebase_router.get("/dashboard/stats/earnings", response_model=AdminStatsEarningsResponse)
async def get_dashboard_stats_earnings(
    tz_offset: int = Query(0, description="Timezone offset in minutes"),
    force_refresh: bool = Query(False, description="Bypass cache and force fresh AdMob fetch"),
    current_user: User = Depends(_require_admin),
):
    try:
        overview = await admob_reporting_service.get_earnings_overview(tz_offset, force_refresh=force_refresh)
        earnings_today = overview.get("today", {"total": 0.0, "ios": 0.0, "android": 0.0})
        earnings_month = overview.get("month", {"total": 0.0, "ios": 0.0, "android": 0.0})
    except Exception as e:
        logger.error(f"Failed to fetch admob earnings: {e}")
        earnings_today, earnings_month = {"total": 0.0, "ios": 0.0, "android": 0.0}, {"total": 0.0, "ios": 0.0, "android": 0.0}

    return AdminStatsEarningsResponse(
        estimated_earnings_today=AppEarnings(**earnings_today),
        estimated_earnings_month=AppEarnings(**earnings_month),
    )

class EarningItem(BaseModel):
    name: str
    earnings: float

class AdminStatsDetailedEarningsResponse(BaseModel):
    unit_wise: List[EarningItem] = []
    country_wise: List[EarningItem] = []

@firebase_router.get("/dashboard/stats/earnings/detailed", response_model=AdminStatsDetailedEarningsResponse)
async def get_dashboard_stats_earnings_detailed(
    tz_offset: int = Query(0, description="Timezone offset in minutes"),
    force_refresh: bool = Query(False, description="Bypass cache and force fresh AdMob fetch"),
    current_user: User = Depends(_require_admin),
):
    try:
        detailed_data = await admob_reporting_service.get_detailed_earnings_this_month(
            tz_offset, force_refresh=force_refresh
        )
        return AdminStatsDetailedEarningsResponse(
            unit_wise=[EarningItem(**item) for item in detailed_data.get("unit_wise", [])],
            country_wise=[EarningItem(**item) for item in detailed_data.get("country_wise", [])]
        )
    except Exception as e:
        logger.error(f"Failed to fetch detailed admob earnings: {e}")
        return AdminStatsDetailedEarningsResponse()


# ── In-App Purchases / Subscriptions Dashboard & Detailed Listing ─────────────

DEFAULT_PLAN_PRICES = {
    "yearly": {"usd": 4.99, "inr": 399.0},
    "halfyearly": {"usd": 2.99, "inr": 249.0},
    "quarterly": {"usd": 1.99, "inr": 149.0},
    "monthly": {"usd": 0.99, "inr": 59.0},
}
USD_TO_INR_RATE = 84.0

def _extract_price_and_currency(
    platform: str,
    normalized_plan: str,
    store_payload: Optional[Dict[str, Any]] = None,
) -> tuple[float, str, float, str]:
    """
    Returns (price, currency, revenue_usd, formatted_price)
    """
    # 1. Try to extract from store_payload
    if store_payload and isinstance(store_payload, dict):
        # Apple payload (claims)
        claims = store_payload.get("claims") or (store_payload if "productId" in store_payload else {})
        if isinstance(claims, dict):
            raw_curr = claims.get("currency")
            raw_price = claims.get("price")
            if raw_price is not None:
                try:
                    p_num = float(raw_price)
                    if p_num > 100:  # Apple uses milliunits e.g. 4990 for $4.99 or 399000 for ₹399
                        p_num = p_num / 1000.0
                    curr = str(raw_curr).upper() if raw_curr else "USD"
                    if curr == "USD":
                        rev_usd = p_num
                        fmt = f"${p_num:.2f}"
                    elif curr == "INR":
                        rev_usd = p_num / USD_TO_INR_RATE
                        fmt = f"₹{p_num:.0f}"
                    else:
                        rev_usd = p_num
                        fmt = f"{curr} {p_num:.2f}"
                    return p_num, curr, rev_usd, fmt
                except (ValueError, TypeError):
                    pass

        # Google Play payload
        line_items = store_payload.get("lineItems") or []
        if isinstance(line_items, list) and line_items:
            first_item = line_items[0] if isinstance(line_items[0], dict) else {}
            offer = first_item.get("offerDetails") or {}
            pricing_phases = offer.get("pricingPhases") or []
            if isinstance(pricing_phases, list) and pricing_phases:
                phase = pricing_phases[-1] if isinstance(pricing_phases[-1], dict) else {}
                micros = phase.get("priceAmountMicros")
                curr = phase.get("priceCurrencyCode")
                if micros is not None:
                    try:
                        p_num = float(micros) / 1_000_000.0
                        c = str(curr).upper() if curr else "USD"
                        if c == "USD":
                            rev_usd = p_num
                            fmt = f"${p_num:.2f}"
                        elif c == "INR":
                            rev_usd = p_num / USD_TO_INR_RATE
                            fmt = f"₹{p_num:.0f}"
                        else:
                            rev_usd = p_num
                            fmt = f"{c} {p_num:.2f}"
                        return p_num, c, rev_usd, fmt
                    except (ValueError, TypeError):
                        pass

    # 2. Fallback to predefined catalog plan prices
    plan_info = DEFAULT_PLAN_PRICES.get(normalized_plan, DEFAULT_PLAN_PRICES["monthly"])
    usd_price = plan_info["usd"]
    return usd_price, "USD", usd_price, f"${usd_price:.2f}"

def _normalize_plan_id(plan_id: Optional[str], product_id: Optional[str] = None) -> str:
    canonical = canonicalize_plan_id(plan_id)
    if canonical:
        return canonical
    text = (plan_id or product_id or "").lower()
    if "year" in text or "12month" in text or "p1y" in text:
        return "yearly"
    if "half" in text or "6month" in text or "p6m" in text:
        return "halfyearly"
    if "quarter" in text or "3month" in text or "p3m" in text:
        return "quarterly"
    if "month" in text or "1month" in text or "p1m" in text:
        return "monthly"
    return "other"

def _get_month_bounds(tz_offset_minutes: int, year: Optional[int] = None, month: Optional[int] = None):
    from datetime import timedelta
    now_utc = datetime.now(timezone.utc)
    user_now = now_utc + timedelta(minutes=tz_offset_minutes)

    if year is not None and month is not None:
        user_month_start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        user_month_start = user_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_start_utc = user_month_start - timedelta(minutes=tz_offset_minutes)

    if user_month_start.month == 12:
        user_next_month_start = user_month_start.replace(year=user_month_start.year + 1, month=1)
    else:
        user_next_month_start = user_month_start.replace(month=user_month_start.month + 1)

    month_end_utc = user_next_month_start - timedelta(minutes=tz_offset_minutes)
    return month_start_utc, month_end_utc, user_month_start

class MonthlyIapPlanCount(BaseModel):
    month_key: str          # e.g. "2026-11"
    month_name: str         # e.g. "Nov 2026"
    yearly: int = 0
    half_yearly: int = 0
    quarterly: int = 0
    monthly: int = 0
    total: int = 0
    revenue: float = 0.0
    revenue_inr: float = 0.0

class AdminStatsIapResponse(BaseModel):
    total_this_month: int = 0
    yearly: int = 0
    half_yearly: int = 0
    quarterly: int = 0
    monthly: int = 0
    android_count: int = 0
    ios_count: int = 0
    active_subscriptions: int = 0
    total_revenue_this_month: float = 0.0
    total_revenue_this_month_inr: float = 0.0
    revenue_android_this_month: float = 0.0
    revenue_ios_this_month: float = 0.0
    monthly_history: List[MonthlyIapPlanCount] = []

class AdminIapSubscriptionUser(BaseModel):
    user_idn: int
    user_id: Optional[str] = None
    email: Optional[str] = None
    user_name: Optional[str] = None
    display_name: Optional[str] = None
    avatar_seed: Optional[str] = None

class AdminIapSubscriptionItem(BaseModel):
    iap_transaction_idn: int
    user: Optional[AdminIapSubscriptionUser] = None
    platform: str
    product_id: str
    plan_id: Optional[str] = None
    normalized_plan: str
    store_transaction_id: str
    original_transaction_id: Optional[str] = None
    verification_status: str
    source: str
    environment: Optional[str] = None
    purchase_dt: Optional[datetime] = None
    expires_dt: Optional[datetime] = None
    is_active: bool = False
    acknowledged: bool = False
    error_message: Optional[str] = None
    store_payload: Optional[Dict[str, Any]] = None
    crt_dt: Optional[datetime] = None
    upd_dt: Optional[datetime] = None
    price: float = 0.0
    currency: str = "USD"
    formatted_price: str = "$0.00"
    revenue_usd: float = 0.0

class AdminIapSubscriptionsResponse(BaseModel):
    total_count: int = 0
    filtered_count: int = 0
    yearly_count: int = 0
    half_yearly_count: int = 0
    quarterly_count: int = 0
    monthly_count: int = 0
    total_revenue: float = 0.0
    total_revenue_inr: float = 0.0
    filtered_revenue: float = 0.0
    filtered_revenue_inr: float = 0.0
    subscriptions: List[AdminIapSubscriptionItem] = []
    available_months: List[str] = []

@firebase_router.get("/dashboard/stats/iap", response_model=AdminStatsIapResponse)
async def get_dashboard_stats_iap(
    tz_offset: int = Query(0, description="Timezone offset in minutes"),
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    from datetime import timedelta
    now_utc = datetime.now(timezone.utc)
    month_start_utc, month_end_utc, user_month_start = _get_month_bounds(tz_offset)

    # Active subscriptions total
    active_stmt = select(func.count()).where(
        IapTransaction.verification_status == "verified",
        or_(
            IapTransaction.expires_dt == None,
            IapTransaction.expires_dt > now_utc,
        ),
    )
    active_subscriptions = (await db.execute(active_stmt)).scalar() or 0

    # Fetch last 6 months of transactions
    six_months_ago_utc = month_start_utc - timedelta(days=185)
    txn_date_expr = func.coalesce(IapTransaction.purchase_dt, IapTransaction.crt_dt)

    stmt = (
        select(
            IapTransaction.plan_id,
            IapTransaction.product_id,
            IapTransaction.platform,
            IapTransaction.verification_status,
            IapTransaction.store_payload,
            txn_date_expr.label("txn_dt"),
        )
        .where(
            txn_date_expr >= six_months_ago_utc,
            IapTransaction.verification_status.in_(["verified", "expired"]),
        )
    )
    res = await db.execute(stmt)
    rows = res.all()

    # Pre-build 6 months list
    monthly_buckets: Dict[str, MonthlyIapPlanCount] = {}
    month_keys_order = []
    temp_dt = user_month_start
    for _ in range(6):
        m_key = temp_dt.strftime("%Y-%m")
        m_name = temp_dt.strftime("%b %Y")
        month_keys_order.append(m_key)
        monthly_buckets[m_key] = MonthlyIapPlanCount(
            month_key=m_key,
            month_name=m_name,
        )
        if temp_dt.month == 1:
            temp_dt = temp_dt.replace(year=temp_dt.year - 1, month=12)
        else:
            temp_dt = temp_dt.replace(month=temp_dt.month - 1)

    this_month_key = user_month_start.strftime("%Y-%m")
    android_count = 0
    ios_count = 0
    total_revenue_this_month = 0.0
    total_revenue_this_month_inr = 0.0
    revenue_android_this_month = 0.0
    revenue_ios_this_month = 0.0

    for r in rows:
        r_dt = r.txn_dt
        if r_dt.tzinfo is None:
            r_dt = r_dt.replace(tzinfo=timezone.utc)
        r_user_dt = r_dt + timedelta(minutes=tz_offset)
        r_month_key = r_user_dt.strftime("%Y-%m")

        plan = _normalize_plan_id(r.plan_id, r.product_id)
        price, curr, rev_usd, fmt_price = _extract_price_and_currency(
            r.platform, plan, getattr(r, "store_payload", None)
        )

        if r_month_key in monthly_buckets:
            bucket = monthly_buckets[r_month_key]
            bucket.total += 1
            bucket.revenue = round(bucket.revenue + rev_usd, 2)
            bucket.revenue_inr = round(bucket.revenue_inr + (rev_usd * USD_TO_INR_RATE), 2)
            if plan == "yearly":
                bucket.yearly += 1
            elif plan == "halfyearly":
                bucket.half_yearly += 1
            elif plan == "quarterly":
                bucket.quarterly += 1
            elif plan == "monthly":
                bucket.monthly += 1

        if r_month_key == this_month_key:
            total_revenue_this_month += rev_usd
            total_revenue_this_month_inr += (rev_usd * USD_TO_INR_RATE)
            if r.platform == "android":
                android_count += 1
                revenue_android_this_month += rev_usd
            elif r.platform == "ios":
                ios_count += 1
                revenue_ios_this_month += rev_usd

    this_month_bucket = monthly_buckets.get(
        this_month_key,
        MonthlyIapPlanCount(month_key=this_month_key, month_name=user_month_start.strftime("%b %Y")),
    )

    return AdminStatsIapResponse(
        total_this_month=this_month_bucket.total,
        yearly=this_month_bucket.yearly,
        half_yearly=this_month_bucket.half_yearly,
        quarterly=this_month_bucket.quarterly,
        monthly=this_month_bucket.monthly,
        android_count=android_count,
        ios_count=ios_count,
        active_subscriptions=active_subscriptions,
        total_revenue_this_month=round(total_revenue_this_month, 2),
        total_revenue_this_month_inr=round(total_revenue_this_month_inr, 2),
        revenue_android_this_month=round(revenue_android_this_month, 2),
        revenue_ios_this_month=round(revenue_ios_this_month, 2),
        monthly_history=[monthly_buckets[k] for k in month_keys_order],
    )


@firebase_router.get("/dashboard/iap/subscriptions", response_model=AdminIapSubscriptionsResponse)
async def get_admin_iap_subscriptions(
    tz_offset: int = Query(0, description="Timezone offset in minutes"),
    month: Optional[str] = Query(None, description="'this_month', 'all', or 'YYYY-MM'"),
    plan: Optional[str] = Query(None, description="'all', 'yearly', 'halfyearly', 'quarterly', 'monthly'"),
    platform: Optional[str] = Query(None, description="'all', 'android', 'ios'"),
    status: Optional[str] = Query(None, description="'all', 'verified', 'expired', 'refunded', 'pending', 'rejected'"),
    search: Optional[str] = Query(None, description="Search in username, display name, email, or transaction id"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    now_utc = datetime.now(timezone.utc)
    txn_date_expr = func.coalesce(IapTransaction.purchase_dt, IapTransaction.crt_dt)

    # Normalize parameters if called directly in tests
    month_val = (month if isinstance(month, str) else "this_month") or "this_month"
    month_val = month_val.strip().lower()
    platform_val = platform if isinstance(platform, str) else None
    status_val = status if isinstance(status, str) else None
    search_val = search if isinstance(search, str) else None
    plan_filter = (plan if isinstance(plan, str) else "all") or "all"
    plan_filter = plan_filter.strip().lower()
    limit_val = limit if isinstance(limit, int) else 100
    offset_val = offset if isinstance(offset, int) else 0

    base_stmt = (
        select(IapTransaction, User)
        .outerjoin(User, IapTransaction.user_idn == User.user_idn)
    )

    if month_val == "this_month":
        month_start_utc, month_end_utc, _ = _get_month_bounds(tz_offset)
        base_stmt = base_stmt.where(txn_date_expr >= month_start_utc, txn_date_expr < month_end_utc)
    elif month_val != "all":
        try:
            parts = month_val.split("-")
            y, m = int(parts[0]), int(parts[1])
            month_start_utc, month_end_utc, _ = _get_month_bounds(tz_offset, year=y, month=m)
            base_stmt = base_stmt.where(txn_date_expr >= month_start_utc, txn_date_expr < month_end_utc)
        except Exception:
            pass

    if platform_val and platform_val.lower() not in ("all", ""):
        base_stmt = base_stmt.where(IapTransaction.platform == platform_val.lower())

    if status_val and status_val.lower() not in ("all", ""):
        base_stmt = base_stmt.where(IapTransaction.verification_status == status_val.lower())

    if search_val and search_val.strip():
        q = f"%{search_val.strip()}%"
        base_stmt = base_stmt.where(
            or_(
                User.user_name.ilike(q),
                User.display_name.ilike(q),
                User.email.ilike(q),
                IapTransaction.store_transaction_id.ilike(q),
                IapTransaction.original_transaction_id.ilike(q),
            )
        )

    base_stmt = base_stmt.order_by(txn_date_expr.desc())

    result = await db.execute(base_stmt)
    all_matched = result.all()

    total_count = len(all_matched)
    yearly_count = 0
    half_yearly_count = 0
    quarterly_count = 0
    monthly_count = 0
    total_revenue = 0.0
    total_revenue_inr = 0.0
    filtered_revenue = 0.0
    filtered_revenue_inr = 0.0

    filtered_items: List[AdminIapSubscriptionItem] = []

    for row in all_matched:
        txn: IapTransaction = row[0]
        user: Optional[User] = row[1]

        norm_plan = _normalize_plan_id(txn.plan_id, txn.product_id)
        price, curr, rev_usd, fmt_price = _extract_price_and_currency(
            txn.platform, norm_plan, txn.store_payload
        )

        if txn.verification_status in ("verified", "expired"):
            total_revenue += rev_usd
            total_revenue_inr += rev_usd * USD_TO_INR_RATE

        if norm_plan == "yearly":
            yearly_count += 1
        elif norm_plan == "halfyearly":
            half_yearly_count += 1
        elif norm_plan == "quarterly":
            quarterly_count += 1
        elif norm_plan == "monthly":
            monthly_count += 1

        if plan_filter != "all" and plan_filter != "":
            if norm_plan != plan_filter:
                continue

        if txn.verification_status in ("verified", "expired"):
            filtered_revenue += rev_usd
            filtered_revenue_inr += rev_usd * USD_TO_INR_RATE

        is_active = (
            txn.verification_status == "verified"
            and (txn.expires_dt is None or txn.expires_dt > now_utc)
        )

        user_info = None
        if user:
            user_info = AdminIapSubscriptionUser(
                user_idn=user.user_idn,
                user_id=user.user_id,
                email=user.email,
                user_name=user.user_name,
                display_name=user.display_name,
                avatar_seed=user.avatar_seed,
            )

        item = AdminIapSubscriptionItem(
            iap_transaction_idn=txn.iap_transaction_idn,
            user=user_info,
            platform=txn.platform,
            product_id=txn.product_id,
            plan_id=txn.plan_id,
            normalized_plan=norm_plan,
            store_transaction_id=txn.store_transaction_id,
            original_transaction_id=txn.original_transaction_id,
            verification_status=txn.verification_status,
            source=txn.source,
            environment=txn.environment,
            purchase_dt=txn.purchase_dt,
            expires_dt=txn.expires_dt,
            is_active=is_active,
            acknowledged=txn.acknowledged,
            error_message=txn.error_message,
            store_payload=txn.store_payload,
            crt_dt=txn.crt_dt,
            upd_dt=txn.upd_dt,
            price=price,
            currency=curr,
            formatted_price=fmt_price,
            revenue_usd=round(rev_usd, 2),
        )
        filtered_items.append(item)

    filtered_count = len(filtered_items)
    paginated_items = filtered_items[offset_val : offset_val + limit_val]

    _, _, cur_month = _get_month_bounds(tz_offset)
    available_months = []
    temp_m = cur_month
    for _ in range(12):
        available_months.append(temp_m.strftime("%Y-%m"))
        if temp_m.month == 1:
            temp_m = temp_m.replace(year=temp_m.year - 1, month=12)
        else:
            temp_m = temp_m.replace(month=temp_m.month - 1)

    return AdminIapSubscriptionsResponse(
        total_count=total_count,
        filtered_count=filtered_count,
        yearly_count=yearly_count,
        half_yearly_count=half_yearly_count,
        quarterly_count=quarterly_count,
        monthly_count=monthly_count,
        total_revenue=round(total_revenue, 2),
        total_revenue_inr=round(total_revenue_inr, 2),
        filtered_revenue=round(filtered_revenue, 2),
        filtered_revenue_inr=round(filtered_revenue_inr, 2),
        subscriptions=paginated_items,
        available_months=available_months,
    )


class AdminReplyRequest(BaseModel):
    admin_user_idn: int
    to_user_idn: int
    content: str

class MessageResponse(BaseModel):
    message_idn: int
    from_user_idn: int
    to_user_idn: int
    message_type: str
    content: Optional[str]
    read_at: Optional[datetime] = None
    crt_dt: datetime

    class Config:
        from_attributes = True

class ConversationResponse(BaseModel):
    user_idn: int
    display_name: Optional[str]
    last_message_dt: datetime
    unread_count: int

@firebase_router.get("/messages/conversations", response_model=List[ConversationResponse])
async def get_conversations(
    admin_user_idn: int,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get a list of users who have messaged the admin, along with unread counts.
    """
    # Find all users who have sent messages to the admin or received from the admin
    stmt = select(Message.from_user_idn, Message.to_user_idn, Message.crt_dt, Message.read_at).where(
        or_(
            Message.from_user_idn == admin_user_idn,
            Message.to_user_idn == admin_user_idn
        )
    ).order_by(desc(Message.crt_dt))
    
    result = await db.execute(stmt)
    messages = result.all()
    
    # Process into conversations
    conversations = {}
    
    for msg in messages:
        other_user_idn = msg.from_user_idn if msg.from_user_idn != admin_user_idn else msg.to_user_idn
        
        if other_user_idn not in conversations:
            conversations[other_user_idn] = {
                "user_idn": other_user_idn,
                "last_message_dt": msg.crt_dt,
                "unread_count": 0
            }
            
        # Count unread messages from them to us
        if msg.from_user_idn == other_user_idn and msg.read_at is None:
            conversations[other_user_idn]["unread_count"] += 1
            
    # Fetch user display names
    if conversations:
        user_idns = list(conversations.keys())
        user_stmt = select(User.user_idn, User.display_name).where(User.user_idn.in_(user_idns))
        user_result = await db.execute(user_stmt)
        users = user_result.all()
        
        for u in users:
            if u.user_idn in conversations:
                conversations[u.user_idn]["display_name"] = u.display_name
                
    # Sort by last message date
    conv_list = list(conversations.values())
    conv_list.sort(key=lambda x: x["last_message_dt"], reverse=True)
    
    return conv_list

@firebase_router.get("/messages/{user_idn}", response_model=List[MessageResponse])
async def get_chat_history(
    user_idn: int,
    admin_user_idn: int,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get chat history between the admin and a specific user.
    """
    stmt = select(Message).where(
        or_(
            and_(Message.from_user_idn == admin_user_idn, Message.to_user_idn == user_idn),
            and_(Message.from_user_idn == user_idn, Message.to_user_idn == admin_user_idn)
        )
    ).order_by(desc(Message.crt_dt)).limit(limit).offset(offset)
    
    result = await db.execute(stmt)
    messages = result.scalars().all()
    
    return messages

@firebase_router.post("/messages/reply", response_model=MessageResponse)
async def reply_to_user(
    req: AdminReplyRequest,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Send a message to a user as the admin.
    """
    # Verify the admin user exists
    admin_user = await db.get(User, req.admin_user_idn)
    if not admin_user:
        raise HTTPException(status_code=404, detail="Admin user not found")
        
    # Verify the target user exists
    target_user = await db.get(User, req.to_user_idn)
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")

    new_msg = Message(
        from_user_idn=req.admin_user_idn,
        to_user_idn=req.to_user_idn,
        message_type=MessageTypeEnum.text,
        content=req.content,
        read_at=None,
        crt_dt=datetime.now(timezone.utc)
    )
    
    db.add(new_msg)
    await db.commit()
    await db.refresh(new_msg)

    # ── Push notification to target user ────────────────────────────────────
    try:
        tokens_result = await db.execute(
            select(UserDevice.fcm_token).where(
                UserDevice.user_idn == req.to_user_idn,
                UserDevice.entity_active == True,
                UserDevice.fcm_token.isnot(None),
            )
        )
        tokens = [row[0] for row in tokens_result.all() if row[0]]
        if tokens:
            preview = req.content[:80] + ("…" if len(req.content) > 80 else "")
            fcm_service.send_to_tokens(
                tokens,
                title="💬 Support Reply",
                body=preview,
                data={"type": "admin_chat"},
            )
    except Exception as fcm_exc:
        logger.warning("admin_reply_fcm_failed", extra={"error": str(fcm_exc)})
    # ────────────────────────────────────────────────────────────────────────

    return new_msg


class MarkReadResponse(BaseModel):
    success: bool
    marked_count: int

@firebase_router.post("/messages/{user_idn}/read", response_model=MarkReadResponse)
async def mark_messages_read(
    user_idn: int,
    admin_user_idn: int,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Mark all unread messages from a specific user as read by the admin.
    """
    from sqlalchemy import update
    stmt = update(Message).where(
        Message.from_user_idn == user_idn,
        Message.to_user_idn == admin_user_idn,
        Message.read_at.is_(None)
    ).values(read_at=datetime.now(timezone.utc))
    
    result = await db.execute(stmt)
    await db.commit()
    return MarkReadResponse(success=True, marked_count=result.rowcount)


from app.services.remote_config_service import remote_config_service

class RemoteConfigRequest(BaseModel):
    parameters: Dict[str, Any]
    etag: str

@firebase_router.get("/remote-config")
async def get_remote_config(current_user: User = Depends(_require_admin)):
    """Fetch Firebase remote config template."""
    template, etag = await remote_config_service.get_template()
    return {"parameters": template.get("parameters", {}), "etag": etag}

@firebase_router.post("/remote-config")
async def update_remote_config(
    req: RemoteConfigRequest,
    current_user: User = Depends(_require_admin)
):
    """Update Firebase remote config template."""
    template, current_etag = await remote_config_service.get_template()
    
    template["parameters"] = req.parameters
    
    try:
        updated_template = await remote_config_service.publish_template(template, req.etag)
        return {"success": True, "parameters": updated_template.get("parameters", {})}
    except httpx.HTTPStatusError as e:
        logger.error(f"Error updating remote config: {e.response.text}")
        raise HTTPException(status_code=400, detail="Failed to update remote config. Ensure you are editing the latest version.")
    except Exception as e:
        logger.error(f"Error updating remote config: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@firebase_router.get("/redis", response_model=list[dict])
async def get_redis_data(
    current_user: User = Depends(_require_admin)
):
    try:
        keys = await redis_client.keys("*")
        # Limit to 1000 to prevent overwhelming
        keys = list(keys)[:1000]
        
        data = []
        for k in keys:
            # We don't know the exact type (string, set, hash), so we try to get it.
            # `type` command in Redis returns type name. But aioredis handles it.
            # For simplicity, we just fetch string or members if it's a set.
            # In memory store, we don't have a robust type() method. 
            # We will just try to get string value, if it's a set it might fail or we can catch it.
            # Actually, `redis_client.get` returns None if it's a set in aioredis.
            # Let's try to get string value.
            val_str = None
            try:
                val = await redis_client.get(k)
                if val is not None:
                    val_str = str(val)
                else:
                    # Might be a set
                    members = await redis_client.smembers(k)
                    if members:
                        val_str = f"Set: {members}"
            except Exception:
                val_str = "<Complex Data Type>"
                
            data.append({"key": k, "value": val_str})
        return data
    except Exception as e:
        logger.error(f"Error fetching redis data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@firebase_router.delete("/redis")
async def clear_redis_data(
    current_user: User = Depends(_require_admin)
):
    try:
        await redis_client.flushdb()
        return {"status": "success", "message": "Redis database cleared."}
    except Exception as e:
        logger.error(f"Error clearing redis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class ActiveUserResponse(BaseModel):
    user_idn: int
    display_name: Optional[str] = None
    email: Optional[str] = None
    last_active_date: Optional[datetime] = None
    online_games: int
    offline_games: int
    date_online_games: int
    date_offline_games: int
    is_online: bool = False

@firebase_router.get("/users", response_model=List[ActiveUserResponse])
async def get_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    active_today: bool = Query(False),
    sortBy: str = Query("createdAt"),
    sortOrder: str = Query("desc"),
    tz_offset: int = Query(0, description="Timezone offset in minutes"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_admin)
):
    try:
        from sqlalchemy import func, asc, desc, nullslast
        from app.models.db_models import GamePlayer, Game, GameTypeEnum
        from datetime import datetime, timezone, timedelta
        
        offset = (page - 1) * limit

        user_now = datetime.now(timezone.utc) + timedelta(minutes=tz_offset)
        user_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start = user_today_start - timedelta(minutes=tz_offset)
        
        subq_online_base = select(func.count(GamePlayer.game_idn)).join(
            Game, GamePlayer.game_idn == Game.game_idn
        ).where(
            GamePlayer.user_idn == User.user_idn,
            Game.game_type == GameTypeEnum.online,
            GamePlayer.entity_active == True,
            Game.entity_active == True
        )

        subq_offline_base = select(func.count(GamePlayer.game_idn)).join(
            Game, GamePlayer.game_idn == Game.game_idn
        ).where(
            GamePlayer.user_idn == User.user_idn,
            Game.game_type == GameTypeEnum.offline,
            GamePlayer.entity_active == True,
            Game.entity_active == True
        )
        
        if active_today:
            subq_online_base = subq_online_base.where(Game.crt_dt >= today_start)
            subq_offline_base = subq_offline_base.where(Game.crt_dt >= today_start)

        subq_online = subq_online_base.scalar_subquery()
        subq_offline = subq_offline_base.scalar_subquery()

        query = select(
            User,
            subq_online.label("online_games"),
            subq_offline.label("offline_games")
        ).where(
            User.entity_active == True
        )

        if active_today:
            # Active today in user's timezone
            query = query.where(User.last_active_date >= today_start)

        if sortBy == "onlineGames":
            order_col = subq_online
        elif sortBy == "offlineGames":
            order_col = subq_offline
        elif sortBy == "name":
            order_col = User.display_name
        elif sortBy == "lastActiveDate":
            order_col = User.last_active_date
        else:
            order_col = User.crt_dt

        if sortOrder == "asc":
            query = query.order_by(nullslast(asc(order_col)))
        else:
            query = query.order_by(nullslast(desc(order_col)))

        query = query.offset(offset).limit(limit)

        result = await db.execute(query)
        rows = result.all()

        response = []
        for user_obj, online_count, offline_count in rows:
            response.append({
                "user_idn": user_obj.user_idn,
                "display_name": user_obj.display_name,
                "email": user_obj.email,
                "last_active_date": user_obj.last_active_date,
                "online_games": online_count or 0,
                "offline_games": offline_count or 0,
                "date_online_games": 0,
                "date_offline_games": 0,
                "is_online": user_obj.is_online or False,
            })
            
        return response
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class SeasonAdminResponse(BaseModel):
    season_idn: int
    season_name: str
    start_date: datetime
    end_date: datetime
    is_active: bool

    class Config:
        from_attributes = True

class SeasonCreateRequest(BaseModel):
    season_name: str
    start_date: datetime
    end_date: datetime
    is_active: bool = False

class SeasonUpdateRequest(BaseModel):
    season_name: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    is_active: Optional[bool] = None

@firebase_router.get("/seasons", response_model=List[SeasonAdminResponse])
async def get_all_seasons(
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Get all seasons."""
    try:
        result = await db.execute(
            select(LeaderboardSeason).order_by(desc(LeaderboardSeason.start_date))
        )
        return result.scalars().all()
    except Exception as e:
        logger.error(f"Error fetching seasons: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch seasons")

@firebase_router.post("/seasons", response_model=SeasonAdminResponse)
async def create_season(
    req: SeasonCreateRequest,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Create a new season."""
    try:
        if req.is_active:
            # Deactivate all other seasons if the new one is active
            await db.execute(
                LeaderboardSeason.__table__.update().values(is_active=False)
            )

        new_season = LeaderboardSeason(
            season_name=req.season_name,
            start_date=req.start_date,
            end_date=req.end_date,
            is_active=req.is_active
        )
        db.add(new_season)
        await db.commit()
        await db.refresh(new_season)
        return new_season
    except Exception as e:
        await db.rollback()
        logger.error(f"Error creating season: {e}")
        raise HTTPException(status_code=500, detail="Failed to create season")

@firebase_router.put("/seasons/{season_idn}", response_model=SeasonAdminResponse)
async def update_season(
    season_idn: int,
    req: SeasonUpdateRequest,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Update an existing season."""
    try:
        result = await db.execute(
            select(LeaderboardSeason).where(LeaderboardSeason.season_idn == season_idn)
        )
        season = result.scalar_one_or_none()
        
        if not season:
            raise HTTPException(status_code=404, detail="Season not found")

        if req.is_active is not None and req.is_active:
            # Deactivate all other seasons if this one is made active
            await db.execute(
                LeaderboardSeason.__table__.update().values(is_active=False).where(LeaderboardSeason.season_idn != season_idn)
            )

        if req.season_name is not None:
            season.season_name = req.season_name
        if req.start_date is not None:
            season.start_date = req.start_date
        if req.end_date is not None:
            season.end_date = req.end_date
        if req.is_active is not None:
            season.is_active = req.is_active

        await db.commit()
        await db.refresh(season)
        return season
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error updating season {season_idn}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update season")

import json

class AnnouncementModel(BaseModel):
    id: str
    title: str
    body: str
    display_type: str
    enabled: bool = False
    priority: int = 10
    target_min_version: str = "1.0.0"
    image_url: Optional[str] = ""
    cta_label: Optional[str] = ""
    cta_url: Optional[str] = ""
    cta_route: Optional[str] = ""
    start_time: Optional[str] = None
    end_time: Optional[str] = None

class AnnouncementsResponse(BaseModel):
    announcements: List[AnnouncementModel]
    etag: str

class UpdateAnnouncementsRequest(BaseModel):
    announcements: List[AnnouncementModel]
    etag: str

@firebase_router.get("/announcements", response_model=AnnouncementsResponse)
async def get_announcements(current_user: User = Depends(_require_admin)):
    """Fetch announcements from Firebase remote config."""
    try:
        template, etag = await remote_config_service.get_template()
        parameters = template.get("parameters", {})
        
        announcements_param = parameters.get("announcements", {})
        default_value = announcements_param.get("defaultValue", {}).get("value", "[]")
        
        try:
            announcements_list = json.loads(default_value)
        except json.JSONDecodeError:
            announcements_list = []

        return AnnouncementsResponse(announcements=announcements_list, etag=etag)
    except Exception as e:
        logger.error(f"Error fetching announcements: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch announcements")

@firebase_router.post("/announcements", response_model=AnnouncementsResponse)
async def update_announcements(
    req: UpdateAnnouncementsRequest,
    current_user: User = Depends(_require_admin)
):
    """Update announcements in Firebase remote config."""
    try:
        template, current_etag = await remote_config_service.get_template()
        
        # Serialize the announcements to JSON string
        announcements_json = json.dumps([a.dict(exclude_none=True) for a in req.announcements])
        
        if "parameters" not in template:
            template["parameters"] = {}
            
        template["parameters"]["announcements"] = {
            "defaultValue": {
                "value": announcements_json
            },
            "valueType": "JSON"
        }
        
        updated_template = await remote_config_service.publish_template(template, req.etag)
        
        return AnnouncementsResponse(announcements=req.announcements, etag=req.etag)
    except httpx.HTTPStatusError as e:
        logger.error(f"Error updating announcements: {e.response.text}")
        raise HTTPException(status_code=400, detail="Failed to update announcements. Ensure you are editing the latest version.")
    except Exception as e:
        logger.error(f"Error updating announcements: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

class AdminGameResponse(BaseModel):
    game_idn: int
    game_type: str
    game_mode: str
    result: str
    total_players: int
    winner_user_idn: Optional[int]
    created_user_idn: Optional[int]
    host_display_name: Optional[str] = None
    crt_dt: datetime
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    score_limit: Optional[str] = None
    room_type: Optional[str] = None

    class Config:
        from_attributes = True

class AdminGamePlayerResponse(BaseModel):
    user_idn: int
    display_name: Optional[str] = None
    avatar_seed: Optional[str] = None
    rank_position: Optional[int] = None
    final_score: int
    rounds_survived: int
    lp_earned: int
    
class AdminGameDetailsResponse(BaseModel):
    game: AdminGameResponse
    players: List[AdminGamePlayerResponse]

@firebase_router.get("/games/{game_idn}/details", response_model=AdminGameDetailsResponse)
async def get_admin_game_details(
    game_idn: int,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    from sqlalchemy.orm import selectinload
    # 1. Fetch game
    stmt = select(Game, User.display_name.label("host_display_name")).outerjoin(User, Game.created_user_idn == User.user_idn).where(Game.game_idn == game_idn)
    result = await db.execute(stmt)
    row = result.first()
    
    if not row:
        raise HTTPException(status_code=404, detail="Game not found")
        
    game_obj, host_name = row
    
    game_data = {
        "game_idn": game_obj.game_idn,
        "game_type": game_obj.game_type,
        "game_mode": game_obj.game_mode,
        "result": game_obj.result,
        "total_players": game_obj.total_players,
        "winner_user_idn": game_obj.winner_user_idn,
        "created_user_idn": game_obj.created_user_idn,
        "crt_dt": game_obj.crt_dt,
        "started_at": game_obj.started_at,
        "ended_at": game_obj.ended_at,
        "host_display_name": host_name,
        "score_limit": game_obj.score_limit,
        "room_type": game_obj.room_type or ("private" if game_obj.game_type == GameTypeEnum.online or str(game_obj.game_type) == "online" else None),
    }
    
    # 2. Fetch players
    from sqlalchemy import asc, nullslast
    player_stmt = select(GamePlayer, User).join(User, GamePlayer.user_idn == User.user_idn).where(GamePlayer.game_idn == game_idn).order_by(nullslast(asc(GamePlayer.rank_position)))
    player_result = await db.execute(player_stmt)
    player_rows = player_result.all()
    
    players_data = []
    for gp, user in player_rows:
        players_data.append({
            "user_idn": gp.user_idn,
            "display_name": user.display_name,
            "avatar_seed": user.avatar_seed,
            "rank_position": gp.rank_position,
            "final_score": gp.final_score,
            "rounds_survived": gp.rounds_survived,
            "lp_earned": gp.lp_earned
        })
        
    return {
        "game": game_data,
        "players": players_data
    }

@firebase_router.get("/games", response_model=List[AdminGameResponse])
async def get_admin_games(
    page: int = 1,
    limit: int = 20,
    status: Optional[str] = None,
    mode: Optional[str] = None,
    type: Optional[str] = None,
    room_type: Optional[str] = None,
    startDate: Optional[str] = None,
    endDate: Optional[str] = None,
    sortBy: str = "createdAt",
    sortOrder: str = "desc",
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    from sqlalchemy import asc
    stmt = select(Game, User.display_name.label("host_display_name")).outerjoin(User, Game.created_user_idn == User.user_idn)

    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            valid_statuses = [s for s in statuses if s in [e.value for e in GameResultEnum]]
            if valid_statuses:
                stmt = stmt.where(Game.result.in_(valid_statuses))

    if mode:
        modes = [m.strip() for m in mode.split(",") if m.strip()]
        if modes:
            valid_modes = [m for m in modes if m in [e.value for e in GameModeEnum]]
            if valid_modes:
                stmt = stmt.where(Game.game_mode.in_(valid_modes))

    if type:
        types = [t.strip() for t in type.split(",") if t.strip()]
        if types:
            valid_types = [t for t in types if t in [e.value for e in GameTypeEnum]]
            if valid_types:
                stmt = stmt.where(Game.game_type.in_(valid_types))

    if room_type:
        room_types = [rt.strip().lower() for rt in room_type.split(",") if rt.strip()]
        if room_types:
            clauses = []
            if "public" in room_types:
                clauses.append(Game.room_type == "public")
            if "private" in room_types:
                clauses.append(
                    and_(
                        Game.game_type == GameTypeEnum.online,
                        or_(Game.room_type == "private", Game.room_type == None, Game.room_type != "public")
                    )
                )
            if clauses:
                stmt = stmt.where(or_(*clauses))

    if startDate:
        try:
            start_dt = datetime.fromisoformat(startDate.replace('Z', '+00:00'))
            stmt = stmt.where(Game.crt_dt >= start_dt)
        except ValueError:
            pass

    if endDate:
        try:
            end_dt = datetime.fromisoformat(endDate.replace('Z', '+00:00'))
            stmt = stmt.where(Game.crt_dt <= end_dt)
        except ValueError:
            pass

    if sortBy == "playersCount":
        order_col = Game.total_players
    else:
        order_col = Game.crt_dt

    if sortOrder == "asc":
        stmt = stmt.order_by(asc(order_col))
    else:
        stmt = stmt.order_by(desc(order_col))

    stmt = stmt.offset((page - 1) * limit).limit(limit)

    result = await db.execute(stmt)
    rows = result.all()
    
    response = []
    for game_obj, host_name in rows:
        response.append({
            "game_idn": game_obj.game_idn,
            "game_type": game_obj.game_type,
            "game_mode": game_obj.game_mode,
            "result": game_obj.result,
            "total_players": game_obj.total_players,
            "winner_user_idn": game_obj.winner_user_idn,
            "created_user_idn": game_obj.created_user_idn,
            "crt_dt": game_obj.crt_dt,
            "started_at": game_obj.started_at,
            "ended_at": game_obj.ended_at,
            "host_display_name": host_name,
            "score_limit": game_obj.score_limit,
            "room_type": game_obj.room_type or ("private" if game_obj.game_type == GameTypeEnum.online or str(game_obj.game_type) == "online" else None),
        })
    
    return response
