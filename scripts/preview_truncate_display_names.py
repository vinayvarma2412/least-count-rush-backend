import asyncio
import os
import sys

# Add the project root to the python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.db_models import User

async def main():
    print("Connecting to database...")
    async with AsyncSessionLocal() as session:
        print("Fetching users...")
        result = await session.execute(select(User))
        users = result.scalars().all()
        
        would_update_count = 0
        print("\n--- DRY RUN: Previewing Display Name Changes ---")
        for user in users:
            if user.display_name and len(user.display_name) > 15:
                original_name = user.display_name
                new_name = original_name
                
                # Split by space and take first part
                if " " in new_name:
                    new_name = new_name.split(" ")[0]
                
                # Truncate to 15 chars
                if len(new_name) > 15:
                    new_name = new_name[:15]
                    
                if new_name != original_name:
                    print(f"[PREVIEW] User ID: {user.user_idn} | Before: '{original_name}' -> After: '{new_name}'")
                    would_update_count += 1
        
        print("-" * 50)
        if would_update_count > 0:
            print(f"Total {would_update_count} users WOULD be updated.")
            print("Note: This is a DRY RUN. No changes were made to the database.")
        else:
            print("No users need updating.")

if __name__ == "__main__":
    asyncio.run(main())
