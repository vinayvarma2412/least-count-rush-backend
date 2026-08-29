import os
import asyncio
from datetime import datetime, timezone
import logging

try:
    from googleapiclient.discovery import build
    from google.oauth2.service_account import Credentials
except ImportError:
    pass

from app.config import settings

logger = logging.getLogger(__name__)

# The scope needed for AdMob reporting
SCOPES = ['https://www.googleapis.com/auth/admob.report']

class AdMobReportingService:
    def __init__(self):
        self.credentials_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "serviceAccountKey.json")
        self.service = None
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
                            "ca-app-pub-9959591108553606~2734505358",  # LCR Android
                            "ca-app-pub-9959591108553606~1010360141"   # LCR iOS
                        ]
                    }
                }
            ]
        }

        try:
            parent = f"accounts/{settings.admob_publisher_id}"
            if not parent.startswith("accounts/pub-"):
                parent = f"accounts/pub-{settings.admob_publisher_id.replace('pub-', '')}"

            request = self.service.accounts().networkReport().generate(
                parent=parent,
                body={"reportSpec": report_spec}
            )
            import socket
            import ssl
            import time
            
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(120)
            
            response = None
            for attempt in range(3):
                try:
                    response = request.execute(num_retries=3)
                    break
                except Exception as e:
                    logger.warning(f"AdMob request attempt {attempt + 1} failed: {e}")
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)  # Exponential backoff
            
            socket.setdefaulttimeout(old_timeout)
            
            result = {"total": 0.0, "ios": 0.0, "android": 0.0}
            
            if not response:
                return result
                
            for item in response:
                if 'row' in item and 'metricValues' in item['row']:
                    earnings_micros = item['row']['metricValues'].get('ESTIMATED_EARNINGS', {}).get('microsValue', '0')
                    val = float(earnings_micros) / 1_000_000.0
                    result["total"] += val
                    
                    app_id = item['row'].get('dimensionValues', {}).get('APP', {}).get('value', '')
                    if app_id == "ca-app-pub-9959591108553606~2734505358":
                        result["android"] += val
                    elif app_id == "ca-app-pub-9959591108553606~1010360141":
                        result["ios"] += val
                
            return result
        except Exception as e:
            logger.error(f"Error fetching AdMob report: {e}")
            return {"total": 0.0, "ios": 0.0, "android": 0.0}

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
                            "ca-app-pub-9959591108553606~2734505358",
                            "ca-app-pub-9959591108553606~1010360141"
                        ]
                    }
                }
            ]
        }

        try:
            parent = f"accounts/{settings.admob_publisher_id}"
            if not parent.startswith("accounts/pub-"):
                parent = f"accounts/pub-{settings.admob_publisher_id.replace('pub-', '')}"

            request = self.service.accounts().networkReport().generate(
                parent=parent,
                body={"reportSpec": report_spec}
            )
            import socket
            import ssl
            import time
            
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(120)
            
            response = None
            for attempt in range(3):
                try:
                    response = request.execute(num_retries=3)
                    break
                except Exception as e:
                    logger.warning(f"AdMob request attempt {attempt + 1} failed: {e}")
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)  # Exponential backoff
            
            socket.setdefaulttimeout(old_timeout)
            
            result = []
            if not response:
                return result
                
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

    async def get_earnings_today(self, tz_offset_minutes: int = 0) -> dict:
        # Calculate today in the requested timezone
        from datetime import timedelta
        now_tz = datetime.now(timezone.utc) + timedelta(minutes=tz_offset_minutes)
        return await asyncio.to_thread(self._generate_report_sync, now_tz, now_tz)

    async def get_earnings_this_month(self, tz_offset_minutes: int = 0) -> dict:
        from datetime import timedelta
        now_tz = datetime.now(timezone.utc) + timedelta(minutes=tz_offset_minutes)
        start_of_month = now_tz.replace(day=1)
        return await asyncio.to_thread(self._generate_report_sync, start_of_month, now_tz)

    async def get_detailed_earnings_this_month(self, tz_offset_minutes: int = 0) -> dict:
        from datetime import timedelta
        now_tz = datetime.now(timezone.utc) + timedelta(minutes=tz_offset_minutes)
        start_of_month = now_tz.replace(day=1)
        
        unit_wise, country_wise = await asyncio.gather(
            asyncio.to_thread(self._generate_detailed_report_sync, start_of_month, now_tz, "AD_UNIT"),
            asyncio.to_thread(self._generate_detailed_report_sync, start_of_month, now_tz, "COUNTRY")
        )
        
        return {
            "unit_wise": unit_wise,
            "country_wise": country_wise
        }


admob_reporting_service = AdMobReportingService()
