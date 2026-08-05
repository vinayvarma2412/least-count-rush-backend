from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Optional
from app.database import get_db_session
from app.api.dependencies import get_current_firebase_user, get_current_db_user
from app.services.user_service import user_service
from app.models.db_models import User, UserDevice, PlatformEnum

import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])

class ProfileUpdateRequest(BaseModel):
    display_name: str | None = None
    avatar_seed: str | None = None

class SyncUserRequest(BaseModel):
    user_type: str = "authenticated"

@router.post("/sync", status_code=status.HTTP_200_OK)
async def sync_user(
    req: SyncUserRequest | None = None,
    firebase_user: dict = Depends(get_current_firebase_user),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Called after successful Firebase login.
    Creates or updates the user record in PostgreSQL.
    """
    user_type = req.user_type if req else "authenticated"
    firebase_uid = firebase_user.get("uid")
    email = firebase_user.get("email")
    display_name = firebase_user.get("name")
    
    logger.info(f"[DEBUG] sync_user called: firebase_uid={firebase_uid}, email={email}, display_name={display_name}, user_type={user_type}")

    if email:
        from app.models.db_models import DeletedUser
        from fastapi.responses import JSONResponse
        
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(DeletedUser)
            .where(DeletedUser.email == email)
            .where(DeletedUser.blocked_until > now)
        )
        blocked_record = result.scalars().first()
        if blocked_record:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "account_deleted",
                    "blocked_until": blocked_record.blocked_until.isoformat()
                }
            )

    user = await user_service.upsert_user(
        db=db, 
        firebase_uid=firebase_uid, 
        email=email, 
        display_name=display_name,
        user_type=user_type
    )

    return {
        "user_idn": user.user_idn,
        "user_id": user.user_id,
        "user_name": user.user_name,
        "displayName": user.display_name,
        "avatarSeed": user.avatar_seed,
    }

@router.get("/me")
async def get_my_profile(user: User = Depends(get_current_db_user)):
    """Get the current user's profile."""
    return {
        "user_idn": user.user_idn,
        "user_name": user.user_name,
        "displayName": user.display_name,
        "avatarSeed": user.avatar_seed,
        "email": user.email,
        "createdAt": user.crt_dt.isoformat() if user.crt_dt else None,
        "updatedAt": user.upd_dt.isoformat() if user.upd_dt else None,
        "userType": user.user_type,
    }

@router.put("/me")
async def update_my_profile(
    req: ProfileUpdateRequest,
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session)
):
    """Update current user profile (display name, avatar)."""
    logger.info(f"[DEBUG] update_my_profile called: user_idn={user.user_idn}, req={req}")
    # If no fields are provided, it acts as a last_active update 
    # (handled implicitly by Depends(get_current_db_user) because upsert_user updates last_active_date)
    updated_user = await user_service.update_user_profile(
        db=db,
        user_idn=user.user_idn,
        display_name=req.display_name,
        avatar_seed=req.avatar_seed
    )
    return {"status": "success", "display_name": updated_user.display_name}

@router.delete("/me")
async def delete_my_account(
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session)
):
    """Delete current user and all their data."""
    success = await user_service.delete_user(db, user.user_idn)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete user data")
    return {"status": "success", "message": "User data deleted"}

@router.get("/check-username")
async def check_username(name: str, db: AsyncSession = Depends(get_db_session)):
    """Check if a username is available."""
    is_available = await user_service.check_username_available(db, name)
    return {"available": is_available}


# ── Device Token (FCM) ────────────────────────────────────────────────────────

class RegisterDeviceRequest(BaseModel):
    device_id: str          # stable unique device identifier (e.g. Android ID / IDFV)
    fcm_token: str          # Firebase Cloud Messaging registration token
    platform: str           # "ios" | "android" | "web"


@router.post("/device", status_code=status.HTTP_200_OK)
async def register_device(
    req: RegisterDeviceRequest,
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Register or update the FCM token for the current user's device.
    Called on app start / when FCM token refreshes.
    """
    try:
        platform = PlatformEnum(req.platform)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid platform '{req.platform}'")

    # Upsert: update token if device already registered, otherwise insert
    result = await db.execute(
        select(UserDevice).where(
            UserDevice.user_idn == user.user_idn,
            UserDevice.device_id == req.device_id,
        )
    )
    device = result.scalar_one_or_none()

    if device:
        device.fcm_token = req.fcm_token
        device.platform = platform
        device.last_active_at = datetime.now(timezone.utc)
        device.entity_active = True
    else:
        device = UserDevice(
            user_idn=user.user_idn,
            platform=platform,
            device_id=req.device_id,
            fcm_token=req.fcm_token,
            last_active_at=datetime.now(timezone.utc),
        )
        db.add(device)

    await db.commit()
    return {"status": "success", "device_id": req.device_id}


@router.delete("/device/{device_id}", status_code=status.HTTP_200_OK)
async def deregister_device(
    device_id: str,
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Soft-deactivate a device (clears FCM token).
    Called on logout so stale tokens are not used for push notifications.
    """
    await db.execute(
        update(UserDevice)
        .where(
            UserDevice.user_idn == user.user_idn,
            UserDevice.device_id == device_id,
        )
        .values(fcm_token=None, entity_active=False, upd_dt=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"status": "success"}
