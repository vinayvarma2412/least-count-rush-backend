import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import os
from dotenv import load_dotenv

load_dotenv()
db_url = os.environ.get("DATABASE_URL")

async def main():
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        try:
            # We must use commit to alter enum in postgres
            pass
        except Exception as e:
            pass
    # In postgres, ALTER TYPE ADD VALUE cannot be executed in a transaction block
    # so we must execute it using a driver-level connection or with engine in isolation level AUTOCOMMIT
    engine2 = create_async_engine(db_url, isolation_level="AUTOCOMMIT")
    async with engine2.connect() as conn:
        try:
            await conn.execute(text("ALTER TYPE game_result_enum ADD VALUE 'in_progress'"))
            print("Successfully added 'in_progress' to game_result_enum")
        except Exception as e:
            print(f"Skipped/Error: {e}")

asyncio.run(main())
