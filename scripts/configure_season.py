import argparse
import asyncio
import sys
import os
from datetime import datetime, timedelta, timezone

# Add the backend directory to sys.path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import AsyncSessionLocal
from app.models.db_models import LeaderboardSeason
from sqlalchemy import update

async def configure_season(name: str = None, duration_days: int = 7, freeze: bool = False):
    """
    Deactivates any currently active seasons and optionally creates a new one.
    """
    async with AsyncSessionLocal() as db:
        try:
            # 1. Deactivate currently active seasons
            print("Deactivating currently active seasons...")
            await db.execute(
                update(LeaderboardSeason)
                .where(LeaderboardSeason.is_active == True)
                .values(is_active=False)
            )
            
            if freeze:
                await db.commit()
                print("✅ Successfully froze the active season. No active season is currently running.")
                return

            now = datetime.now(timezone.utc)
            start_date = now
            end_date = start_date + timedelta(days=duration_days)
            
            season_name = name
            if not season_name:
                week_num = start_date.isocalendar()[1]
                year = start_date.year
                season_name = f"Week {week_num} - {year}"

            # 2. Create new active season
            new_season = LeaderboardSeason(
                season_name=season_name,
                start_date=start_date,
                end_date=end_date,
                is_active=True
            )
            db.add(new_season)
            
            await db.commit()
            await db.refresh(new_season)
            
            print(f"✅ Successfully created new active season:")
            print(f"   ID: {new_season.season_idn}")
            print(f"   Name: {new_season.season_name}")
            print(f"   Start: {new_season.start_date}")
            print(f"   End: {new_season.end_date}")
            
        except Exception as e:
            await db.rollback()
            print(f"❌ Error configuring season: {e}")

def main():
    parser = argparse.ArgumentParser(description="Configure a new leaderboard season.")
    parser.add_argument("--name", type=str, help="Name of the season (e.g., 'Week 42 - 2026'). If not provided, it generates one based on the current week.")
    parser.add_argument("--days", type=int, default=7, help="Duration of the season in days (default 7).")
    parser.add_argument("--freeze", action="store_true", help="Freeze the active season (deactivate it without starting a new one).")
    
    args = parser.parse_args()
    
    asyncio.run(configure_season(name=args.name, duration_days=args.days, freeze=args.freeze))

if __name__ == "__main__":
    main()
