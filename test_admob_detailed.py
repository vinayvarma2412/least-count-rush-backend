import asyncio
from app.config import settings
from app.services.admob_reporting_service import admob_reporting_service
import logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO)

async def main():
    now = datetime.now(timezone.utc)
    start_of_month = now.replace(day=1)
    
    # Unit wise
    report_spec_unit = {
        "dateRange": {
            "startDate": {"year": start_of_month.year, "month": start_of_month.month, "day": start_of_month.day},
            "endDate": {"year": now.year, "month": now.month, "day": now.day}
        },
        "dimensions": ["AD_UNIT"],
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
    
    parent = f"accounts/pub-{settings.admob_publisher_id.replace('pub-', '')}"

    request_unit = admob_reporting_service.service.accounts().networkReport().generate(
        parent=parent,
        body={"reportSpec": report_spec_unit}
    )
    
    print("Fetching unit wise...")
    response_unit = request_unit.execute()
    
    # Country wise
    report_spec_country = {
        "dateRange": {
            "startDate": {"year": start_of_month.year, "month": start_of_month.month, "day": start_of_month.day},
            "endDate": {"year": now.year, "month": now.month, "day": now.day}
        },
        "dimensions": ["COUNTRY"],
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
    
    request_country = admob_reporting_service.service.accounts().networkReport().generate(
        parent=parent,
        body={"reportSpec": report_spec_country}
    )
    
    print("Fetching country wise...")
    response_country = request_country.execute()
    
    print("UNIT RESPONSE:", response_unit)
    print("COUNTRY RESPONSE:", response_country)

if __name__ == "__main__":
    asyncio.run(main())
