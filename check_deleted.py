import asyncio
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import select, create_engine
from sqlalchemy.orm import sessionmaker
from app.models.db_models import DeletedUser
import os
from datetime import datetime, timezone

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def check():
    db = SessionLocal()
    records = db.query(DeletedUser).all()
    print(f"Total deleted_users: {len(records)}")
    now = datetime.now(timezone.utc)
    for r in records:
        print(f"Email: {r.email}, Blocked Until: {r.blocked_until}, Is Active Block: {r.blocked_until > now}")
    db.close()

check()
