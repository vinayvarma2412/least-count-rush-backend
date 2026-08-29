import asyncio
from app.config import settings
from app.services.admob_reporting_service import admob_reporting_service
import logging

logging.basicConfig(level=logging.INFO)

async def main():
    print("Fetching detailed earnings...")
    res = await admob_reporting_service.get_detailed_earnings_this_month(0)
    print("Result:", res)

if __name__ == "__main__":
    asyncio.run(main())
