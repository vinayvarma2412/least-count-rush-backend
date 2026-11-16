import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest

from app.services.admob_reporting_service import (
    AdMobReportingService,
    ANDROID_APP_ID,
    IOS_APP_ID,
)
from app.api.routes.admin import (
    get_dashboard_stats_earnings,
    get_dashboard_stats_earnings_detailed,
    AdminStatsEarningsResponse,
    AdminStatsDetailedEarningsResponse,
)
from app.models.db_models import User, UserRoleEnum


@pytest.fixture
def mock_admin_user():
    return User(
        user_idn=1,
        user_id="admin-123",
        user_name="admin",
        role=UserRoleEnum.admin,
        entity_active=True,
    )


@pytest.mark.asyncio
async def test_combined_overview_parsing():
    service = AdMobReportingService()
    service.service = MagicMock()
    now_tz = datetime(2026, 11, 17, 12, 0, 0, tzinfo=timezone.utc)
    start_of_month = datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc)

    # Mock response with rows for earlier in month and rows for today
    mock_response = [
        # Earlier in month: 2026-11-10
        {
            "row": {
                "dimensionValues": {
                    "DATE": {"value": "20261110"},
                    "APP": {"value": ANDROID_APP_ID},
                },
                "metricValues": {
                    "ESTIMATED_EARNINGS": {"microsValue": "2000000"},  # $2.00
                },
            }
        },
        {
            "row": {
                "dimensionValues": {
                    "DATE": {"value": "20261110"},
                    "APP": {"value": IOS_APP_ID},
                },
                "metricValues": {
                    "ESTIMATED_EARNINGS": {"microsValue": "3000000"},  # $3.00
                },
            }
        },
        # Today: 2026-11-17
        {
            "row": {
                "dimensionValues": {
                    "DATE": {"value": "20261117"},
                    "APP": {"value": ANDROID_APP_ID},
                },
                "metricValues": {
                    "ESTIMATED_EARNINGS": {"microsValue": "1500000"},  # $1.50
                },
            }
        },
        {
            "row": {
                "dimensionValues": {
                    "DATE": {"value": "20261117"},
                    "APP": {"value": IOS_APP_ID},
                },
                "metricValues": {
                    "ESTIMATED_EARNINGS": {"microsValue": "2500000"},  # $2.50
                },
            }
        },
    ]

    with patch("app.services.admob_reporting_service.settings.admob_publisher_id", "pub-12345"):
        with patch.object(service, "_execute_admob_request", return_value=mock_response):
            overview = service._generate_combined_overview_sync(start_of_month, now_tz)

    # Month earnings: 2.0 + 3.0 + 1.5 + 2.5 = 9.0
    assert overview["month"]["total"] == pytest.approx(9.0, rel=1e-3)
    assert overview["month"]["android"] == pytest.approx(3.5, rel=1e-3)
    assert overview["month"]["ios"] == pytest.approx(5.5, rel=1e-3)

    # Today earnings: 1.5 + 2.5 = 4.0
    assert overview["today"]["total"] == pytest.approx(4.0, rel=1e-3)
    assert overview["today"]["android"] == pytest.approx(1.5, rel=1e-3)
    assert overview["today"]["ios"] == pytest.approx(2.5, rel=1e-3)


@pytest.mark.asyncio
async def test_overview_cache_and_force_refresh():
    service = AdMobReportingService()

    fake_overview_1 = {
        "today": {"total": 5.0, "ios": 3.0, "android": 2.0},
        "month": {"total": 20.0, "ios": 12.0, "android": 8.0},
    }
    fake_overview_2 = {
        "today": {"total": 6.0, "ios": 3.5, "android": 2.5},
        "month": {"total": 25.0, "ios": 15.0, "android": 10.0},
    }

    call_count = 0

    def mock_fetch(start, end):
        nonlocal call_count
        call_count += 1
        return fake_overview_1 if call_count == 1 else fake_overview_2

    with patch.object(service, "_generate_combined_overview_sync", side_effect=mock_fetch):
        # First call: cache miss, calls generator
        res1 = await service.get_earnings_overview(tz_offset_minutes=0, force_refresh=False)
        assert res1["today"]["total"] == 5.0
        assert call_count == 1

        # Second call: cache hit, does not call generator
        res2 = await service.get_earnings_overview(tz_offset_minutes=0, force_refresh=False)
        assert res2["today"]["total"] == 5.0
        assert call_count == 1

        # Third call: force_refresh=True, bypasses cache
        res3 = await service.get_earnings_overview(tz_offset_minutes=0, force_refresh=True)
        assert res3["today"]["total"] == 6.0
        assert call_count == 2


@pytest.mark.asyncio
async def test_fallback_to_last_successful():
    service = AdMobReportingService()

    good_overview = {
        "today": {"total": 10.0, "ios": 6.0, "android": 4.0},
        "month": {"total": 50.0, "ios": 30.0, "android": 20.0},
    }

    # Prime cache and last successful
    with patch.object(service, "_generate_combined_overview_sync", return_value=good_overview):
        await service.get_earnings_overview(tz_offset_minutes=0, force_refresh=True)

    # Next call with force_refresh fails with an exception
    with patch.object(service, "_generate_combined_overview_sync", side_effect=RuntimeError("Google API 503")):
        fallback_res = await service.get_earnings_overview(tz_offset_minutes=0, force_refresh=True)

    # Should gracefully return the last known good earnings
    assert fallback_res["today"]["total"] == 10.0
    assert fallback_res["month"]["total"] == 50.0


@pytest.mark.asyncio
async def test_admin_earnings_endpoint(mock_admin_user):
    with patch("app.api.routes.admin.admob_reporting_service.get_earnings_overview") as mock_overview:
        mock_overview.return_value = {
            "today": {"total": 3.25, "ios": 1.25, "android": 2.0},
            "month": {"total": 45.5, "ios": 20.5, "android": 25.0},
        }

        resp = await get_dashboard_stats_earnings(
            tz_offset=0,
            force_refresh=False,
            current_user=mock_admin_user,
        )

        assert isinstance(resp, AdminStatsEarningsResponse)
        assert resp.estimated_earnings_today.total == pytest.approx(3.25)
        assert resp.estimated_earnings_today.ios == pytest.approx(1.25)
        assert resp.estimated_earnings_today.android == pytest.approx(2.0)
        assert resp.estimated_earnings_month.total == pytest.approx(45.5)


@pytest.mark.asyncio
async def test_admin_detailed_earnings_endpoint(mock_admin_user):
    with patch("app.api.routes.admin.admob_reporting_service.get_detailed_earnings_this_month") as mock_detailed:
        mock_detailed.return_value = {
            "unit_wise": [{"name": "Banner 1", "earnings": 12.5}],
            "country_wise": [{"name": "IN", "earnings": 10.0}],
        }

        resp = await get_dashboard_stats_earnings_detailed(
            tz_offset=0,
            force_refresh=False,
            current_user=mock_admin_user,
        )

        assert isinstance(resp, AdminStatsDetailedEarningsResponse)
        assert len(resp.unit_wise) == 1
        assert resp.unit_wise[0].name == "Banner 1"
        assert resp.unit_wise[0].earnings == 12.5
        assert len(resp.country_wise) == 1
        assert resp.country_wise[0].name == "IN"
