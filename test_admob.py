import asyncio
from app.config import settings
from app.services.admob_reporting_service import admob_reporting_service
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)

async def main():
    now = datetime.now(timezone.utc)
    report_spec = {
        "dateRange": {
            "startDate": {"year": now.year, "month": now.month, "day": now.day},
            "endDate": {"year": now.year, "month": now.month, "day": now.day}
        },
        "dimensions": ["APP"],
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

    request = admob_reporting_service.service.accounts().networkReport().generate(
        parent=parent,
        body={"reportSpec": report_spec}
    )
    
    response = request.execute()
    print("RAW RESPONSE WITH DIMENSIONS:", response)

if __name__ == "__main__":
    asyncio.run(main())
