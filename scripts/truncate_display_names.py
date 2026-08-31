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
        
        updated_count = 0
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
                    print(f"Updating user {user.user_idn}: '{original_name}' -> '{new_name}'")
                    user.display_name = new_name
                    updated_count += 1
        
        if updated_count > 0:
            print(f"Committing {updated_count} updates...")
            await session.commit()
            print("Successfully updated database!")
        else:
            print("No users needed updating.")

if __name__ == "__main__":
    asyncio.run(main())
