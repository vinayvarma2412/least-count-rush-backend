import os
import time
import asyncio
from datetime import datetime, timezone, timedelta
import logging
from typing import Dict, Any, Optional, List, Tuple

try:
    from googleapiclient.discovery import build
    from google.oauth2.service_account import Credentials
except ImportError:
    pass

from app.config import settings

logger = logging.getLogger(__name__)

# The scope needed for AdMob reporting
SCOPES = ['https://www.googleapis.com/auth/admob.report']

# App IDs
ANDROID_APP_ID = "ca-app-pub-9959591108553606~2734505358"
IOS_APP_ID = "ca-app-pub-9959591108553606~1010360141"

# Cache TTLs (seconds)
# AdMob data updates only every 1-2 hours on Google's servers.
OVERVIEW_CACHE_TTL_SECONDS = 15 * 60   # 15 minutes
DETAILED_CACHE_TTL_SECONDS = 30 * 60   # 30 minutes
API_TIMEOUT_SECONDS = 15.0             # 15s max timeout for AdMob network calls

class AdMobReportingService:
    def __init__(self):
        self.credentials_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "serviceAccountKey.json")
        self.service = None
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._last_successful_overview: Dict[int, Dict[str, Any]] = {}
        self._last_successful_detailed: Dict[int, Dict[str, Any]] = {}
        self._overview_lock = asyncio.Lock()
        self._detailed_lock = asyncio.Lock()
        self._init_service()

    def _init_service(self):
        if not settings.admob_publisher_id:
            logger.warning("ADMOB_PUBLISHER_ID not set. AdMob reporting will be disabled.")
            return

        if not settings.admob_client_id or not settings.admob_refresh_token:
            logger.warning("OAuth credentials (ADMOB_CLIENT_ID or ADMOB_REFRESH_TOKEN) not set. AdMob reporting disabled.")
            return

        try:
            from google.oauth2.credentials import Credentials
            
            client_config = {
                "client_id": settings.admob_client_id,
                "client_secret": settings.admob_client_secret,
                "refresh_token": settings.admob_refresh_token,
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            
            credentials = Credentials.from_authorized_user_info(client_config, scopes=SCOPES)
            self.service = build('admob', 'v1', credentials=credentials, cache_discovery=False)
        except Exception as e:
            logger.error(f"Failed to initialize AdMob service: {e}")

    def _get_from_cache(self, key: str) -> Optional[Any]:
        if key in self._cache:
            expires_at, data = self._cache[key]
            if time.monotonic() < expires_at:
                return data
            # Expired
            self._cache.pop(key, None)
        return None

    def _set_in_cache(self, key: str, data: Any, ttl_seconds: float):
        self._cache[key] = (time.monotonic() + ttl_seconds, data)

    def _execute_admob_request(self, report_spec: dict) -> list:
        """Executes AdMob network report generate request with fast single-retry execution."""
        if not self.service or not settings.admob_publisher_id:
            return []

        parent = f"accounts/{settings.admob_publisher_id}"
        if not parent.startswith("accounts/pub-"):
            parent = f"accounts/pub-{settings.admob_publisher_id.replace('pub-', '')}"

        request = self.service.accounts().networkReport().generate(
            parent=parent,
            body={"reportSpec": report_spec}
        )
        response = request.execute(num_retries=1)
        return response or []

    def _generate_report_sync(self, start_date: datetime, end_date: datetime) -> dict:
        if not self.service or not settings.admob_publisher_id:
            return {"total": 0.0, "ios": 0.0, "android": 0.0}

        report_spec = {
            "dateRange": {
                "startDate": {"year": start_date.year, "month": start_date.month, "day": start_date.day},
                "endDate": {"year": end_date.year, "month": end_date.month, "day": end_date.day}
            },
            "dimensions": ["APP"],
            "metrics": ["ESTIMATED_EARNINGS"],
            "dimensionFilters": [
                {
                    "dimension": "APP",
                    "matchesAny": {
                        "values": [
                            ANDROID_APP_ID,
                            IOS_APP_ID
                        ]
                    }
                }
            ]
        }

        try:
            response = self._execute_admob_request(report_spec)
            result = {"total": 0.0, "ios": 0.0, "android": 0.0}
            for item in response:
                if 'row' in item and 'metricValues' in item['row']:
                    earnings_micros = item['row']['metricValues'].get('ESTIMATED_EARNINGS', {}).get('microsValue', '0')
                    val = float(earnings_micros) / 1_000_000.0
                    result["total"] += val
                    
                    app_id = item['row'].get('dimensionValues', {}).get('APP', {}).get('value', '')
                    if app_id == ANDROID_APP_ID:
                        result["android"] += val
                    elif app_id == IOS_APP_ID:
                        result["ios"] += val
                
            return result
        except Exception as e:
            logger.error(f"Error fetching AdMob report: {e}")
            return {"total": 0.0, "ios": 0.0, "android": 0.0}

    def _generate_combined_overview_sync(self, start_of_month: datetime, now_tz: datetime) -> Dict[str, Dict[str, float]]:
        """
        Fetches earnings for the entire month-to-date with ["DATE", "APP"] dimensions.
        Extracts BOTH today's and this month's earnings from ONE single AdMob network call.
        """
        today_default = {"total": 0.0, "ios": 0.0, "android": 0.0}
        month_default = {"total": 0.0, "ios": 0.0, "android": 0.0}

        if not self.service or not settings.admob_publisher_id:
            return {"today": today_default, "month": month_default}

        report_spec = {
            "dateRange": {
                "startDate": {"year": start_of_month.year, "month": start_of_month.month, "day": start_of_month.day},
                "endDate": {"year": now_tz.year, "month": now_tz.month, "day": now_tz.day}
            },
            "dimensions": ["DATE", "APP"],
            "metrics": ["ESTIMATED_EARNINGS"],
            "dimensionFilters": [
                {
                    "dimension": "APP",
                    "matchesAny": {
                        "values": [
                            ANDROID_APP_ID,
                            IOS_APP_ID
                        ]
                    }
                }
            ]
        }

        try:
            response = self._execute_admob_request(report_spec)
        except Exception as e:
            logger.warning(f"Combined AdMob report query failed ({e}), falling back to individual queries")
            today_res = self._generate_report_sync(now_tz, now_tz)
            month_res = self._generate_report_sync(start_of_month, now_tz)
            return {"today": today_res, "month": month_res}

        today_earnings = {"total": 0.0, "ios": 0.0, "android": 0.0}
        month_earnings = {"total": 0.0, "ios": 0.0, "android": 0.0}

        today_yyyymmdd = f"{now_tz.year:04d}{now_tz.month:02d}{now_tz.day:02d}"
        today_iso = f"{now_tz.year:04d}-{now_tz.month:02d}-{now_tz.day:02d}"

        for item in response:
            if 'row' in item and 'metricValues' in item['row']:
                earnings_micros = item['row']['metricValues'].get('ESTIMATED_EARNINGS', {}).get('microsValue', '0')
                val = float(earnings_micros) / 1_000_000.0

                dim_vals = item['row'].get('dimensionValues', {})
                app_id = dim_vals.get('APP', {}).get('value', '')
                date_val = str(dim_vals.get('DATE', {}).get('value', ''))

                # Accumulate for month
                month_earnings["total"] += val
                if app_id == ANDROID_APP_ID:
                    month_earnings["android"] += val
                elif app_id == IOS_APP_ID:
                    month_earnings["ios"] += val

                # Accumulate for today if row matches today's date
                if date_val in (today_yyyymmdd, today_iso) or date_val.replace("-", "") == today_yyyymmdd:
                    today_earnings["total"] += val
                    if app_id == ANDROID_APP_ID:
                        today_earnings["android"] += val
                    elif app_id == IOS_APP_ID:
                        today_earnings["ios"] += val

        return {
            "today": today_earnings,
            "month": month_earnings
        }

    def _generate_detailed_report_sync(self, start_date: datetime, end_date: datetime, dimension: str) -> list:
        if not self.service or not settings.admob_publisher_id:
            return []

        report_spec = {
            "dateRange": {
                "startDate": {"year": start_date.year, "month": start_date.month, "day": start_date.day},
                "endDate": {"year": end_date.year, "month": end_date.month, "day": end_date.day}
            },
            "dimensions": [dimension],
            "metrics": ["ESTIMATED_EARNINGS"],
            "dimensionFilters": [
                {
                    "dimension": "APP",
                    "matchesAny": {
                        "values": [
                            ANDROID_APP_ID,
                            IOS_APP_ID
                        ]
                    }
                }
            ]
        }

        try:
            response = self._execute_admob_request(report_spec)
            result = []
            for item in response:
                if 'row' in item and 'metricValues' in item['row']:
                    earnings_micros = item['row']['metricValues'].get('ESTIMATED_EARNINGS', {}).get('microsValue', '0')
                    val = float(earnings_micros) / 1_000_000.0
                    
                    dim_val = item['row'].get('dimensionValues', {}).get(dimension, {})
                    # For AD_UNIT, prefer displayLabel, fallback to value. For COUNTRY, use value.
                    name = dim_val.get('displayLabel') or dim_val.get('value', 'Unknown')
                    
                    result.append({"name": name, "earnings": val})
                    
            # Sort descending by earnings
            result.sort(key=lambda x: x["earnings"], reverse=True)
            return result
        except Exception as e:
            logger.error(f"Error fetching detailed AdMob report for {dimension}: {e}")
            return []

    async def get_earnings_overview(self, tz_offset_minutes: int = 0, force_refresh: bool = False) -> Dict[str, Dict[str, float]]:
        now_tz = datetime.now(timezone.utc) + timedelta(minutes=tz_offset_minutes)
        cache_key = f"overview:{now_tz.strftime('%Y-%m-%d')}:{tz_offset_minutes}"

        if not force_refresh:
            cached = self._get_from_cache(cache_key)
            if cached is not None:
                return cached

        async with self._overview_lock:
            if not force_refresh:
                cached = self._get_from_cache(cache_key)
                if cached is not None:
                    return cached

            start_of_month = now_tz.replace(day=1)

            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self._generate_combined_overview_sync, start_of_month, now_tz),
                    timeout=API_TIMEOUT_SECONDS
                )
                self._set_in_cache(cache_key, result, OVERVIEW_CACHE_TTL_SECONDS)
                self._last_successful_overview[tz_offset_minutes] = result
                return result
            except asyncio.TimeoutError:
                logger.warning("AdMob overview query timed out after %s seconds", API_TIMEOUT_SECONDS)
            except Exception as e:
                logger.error(f"Error fetching AdMob overview: {e}")

            if tz_offset_minutes in self._last_successful_overview:
                logger.info("Serving last successful AdMob overview from memory fallback")
                return self._last_successful_overview[tz_offset_minutes]

            return {
                "today": {"total": 0.0, "ios": 0.0, "android": 0.0},
                "month": {"total": 0.0, "ios": 0.0, "android": 0.0}
            }

    async def get_earnings_today(self, tz_offset_minutes: int = 0, force_refresh: bool = False) -> dict:
        overview = await self.get_earnings_overview(tz_offset_minutes, force_refresh=force_refresh)
        return overview.get("today", {"total": 0.0, "ios": 0.0, "android": 0.0})

    async def get_earnings_this_month(self, tz_offset_minutes: int = 0, force_refresh: bool = False) -> dict:
        overview = await self.get_earnings_overview(tz_offset_minutes, force_refresh=force_refresh)
        return overview.get("month", {"total": 0.0, "ios": 0.0, "android": 0.0})

    async def get_detailed_earnings_this_month(self, tz_offset_minutes: int = 0, force_refresh: bool = False) -> dict:
        now_tz = datetime.now(timezone.utc) + timedelta(minutes=tz_offset_minutes)
        cache_key = f"detailed:{now_tz.strftime('%Y-%m-%d')}:{tz_offset_minutes}"

        if not force_refresh:
            cached = self._get_from_cache(cache_key)
            if cached is not None:
                return cached

        async with self._detailed_lock:
            if not force_refresh:
                cached = self._get_from_cache(cache_key)
                if cached is not None:
                    return cached

            start_of_month = now_tz.replace(day=1)

            try:
                unit_wise, country_wise = await asyncio.wait_for(
                    asyncio.gather(
                        asyncio.to_thread(self._generate_detailed_report_sync, start_of_month, now_tz, "AD_UNIT"),
                        asyncio.to_thread(self._generate_detailed_report_sync, start_of_month, now_tz, "COUNTRY")
                    ),
                    timeout=API_TIMEOUT_SECONDS
                )
                result = {
                    "unit_wise": unit_wise,
                    "country_wise": country_wise
                }
                self._set_in_cache(cache_key, result, DETAILED_CACHE_TTL_SECONDS)
                self._last_successful_detailed[tz_offset_minutes] = result
                return result
            except asyncio.TimeoutError:
                logger.warning("AdMob detailed query timed out after %s seconds", API_TIMEOUT_SECONDS)
            except Exception as e:
                logger.error(f"Error fetching detailed AdMob report: {e}")

            if tz_offset_minutes in self._last_successful_detailed:
                logger.info("Serving last successful detailed AdMob report from memory fallback")
                return self._last_successful_detailed[tz_offset_minutes]

            return {
                "unit_wise": [],
                "country_wise": []
            }


admob_reporting_service = AdMobReportingService()
