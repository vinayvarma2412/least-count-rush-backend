import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from app.utils.room_logger import global_log
from app.config import settings

# Fetch database URL from environment
# Expecting postgresql+asyncpg:// format
DATABASE_URL = settings.database_url

if not DATABASE_URL:
    global_log.error("DATABASE_URL is not set. Cannot start without a database.")
    raise ValueError("DATABASE_URL environment variable is missing")

# Create the async engine
# pool_size and max_overflow can be adjusted based on load and Supabase PgBouncer limits
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True, # check connection before using
    pool_size=10,
    max_overflow=20
)

# Create a sessionmaker that uses AsyncSession
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

Base = declarative_base()

async def get_db_session():
    """Dependency for FastAPI to get a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
