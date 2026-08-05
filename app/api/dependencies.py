from fastapi import Depends, HTTPException, Header, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db_session
from app.utils.firebase_auth import verify_firebase_token
from app.services.user_service import user_service
from app.models.db_models import User
import logging
import os

logger = logging.getLogger(__name__)

# Security scheme for Admin API Key in Swagger UI
api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=True)

async def get_admin_api_key(api_key: str = Depends(api_key_header)):
    expected_api_key = os.getenv("ADMIN_API_KEY", "your_super_secret_key")
    if api_key != expected_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )
    return api_key

async def get_current_firebase_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )
    
    token = authorization.removeprefix("Bearer ")
    try:
        decoded_token = verify_firebase_token(token)
        return decoded_token
    except Exception as e:
        logger.error(f"Token verification failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )

async def get_current_db_user(
    firebase_user: dict = Depends(get_current_firebase_user),
    db: AsyncSession = Depends(get_db_session)
) -> User:
    firebase_uid = firebase_user.get("uid")
    logger.info(f"[DEBUG] get_current_db_user checking firebase_uid={firebase_uid}")
    if not firebase_uid:
        logger.error("[DEBUG] Invalid token payload: no uid found")
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = await user_service.get_user_by_firebase_id(db, firebase_uid)
    if not user:
        logger.warning(f"[DEBUG] User {firebase_uid} not found in Postgres database. Returning 404.")
        raise HTTPException(status_code=404, detail="User not found in PostgreSQL database")
        
    logger.info(f"[DEBUG] User {firebase_uid} found in DB: user_idn={user.user_idn}")
    return user
