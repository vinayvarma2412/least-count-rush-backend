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

    def _generate_report_sync(self, start_date: datetime, end_date: datetime) -> float:
        if not self.service or not settings.admob_publisher_id:
            return 0.0

        report_spec = {
            "dateRange": {
                "startDate": {"year": start_date.year, "month": start_date.month, "day": start_date.day},
                "endDate": {"year": end_date.year, "month": end_date.month, "day": end_date.day}
            },
            "metrics": ["ESTIMATED_EARNINGS"]
        }

        try:
            parent = f"accounts/{settings.admob_publisher_id}"
            if not parent.startswith("accounts/pub-"):
                parent = f"accounts/pub-{settings.admob_publisher_id.replace('pub-', '')}"

            request = self.service.accounts().networkReport().generate(
                parent=parent,
                body={"reportSpec": report_spec}
            )
            response = request.execute()
            
            # The response is a list of lines. 
            # First line is usually headers, the rest are data, and the last line is a footer.
            # Look for the footer row which contains the totals.
            total_earnings = 0.0
            
            # Response is a list of dicts: [{'header': {...}}, {'row': {'metricValues': {'ESTIMATED_EARNINGS': {'microsValue': '...'}}}}, {'footer': {...}}]
            # Or the footer has the totals. Let's just sum all rows or use the footer.
            for item in response:
                if 'footer' in item and 'metricValues' in item['footer']:
                    earnings_micros = item['footer']['metricValues'].get('ESTIMATED_EARNINGS', {}).get('microsValue', '0')
                    total_earnings = float(earnings_micros) / 1_000_000.0
                    return total_earnings
                
            return total_earnings
        except Exception as e:
            logger.error(f"Error fetching AdMob report: {e}")
            return 0.0

    async def get_earnings_today(self, tz_offset_minutes: int = 0) -> float:
        # Calculate today in the requested timezone
        from datetime import timedelta
        now_tz = datetime.now(timezone.utc) + timedelta(minutes=tz_offset_minutes)
        return await asyncio.to_thread(self._generate_report_sync, now_tz, now_tz)

    async def get_earnings_this_month(self, tz_offset_minutes: int = 0) -> float:
        from datetime import timedelta
        now_tz = datetime.now(timezone.utc) + timedelta(minutes=tz_offset_minutes)
        start_of_month = now_tz.replace(day=1)
        return await asyncio.to_thread(self._generate_report_sync, start_of_month, now_tz)


admob_reporting_service = AdMobReportingService()
