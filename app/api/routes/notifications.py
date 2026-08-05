"""
Admin-authenticated notification routes.

POST /api/admin/notifications/send       — immediate push (user or topic)
POST /api/admin/notifications/schedule   — store scheduled notification
GET  /api/admin/notifications            — list past notifications
POST /api/admin/notifications/{idn}/retry — retry failed notification
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel, model_validator
from datetime import datetime, timezone
from typing import Optional, List
import logging

from app.database import get_db_session, AsyncSessionLocal
from app.api.dependencies import get_admin_api_key
from app.models.db_models import Notification, NotifStatusEnum
from app.services import fcm_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/notifications",
    tags=["Admin Notifications"],
    dependencies=[Depends(get_admin_api_key)],
)


# ── Schemas ─────────────────────────────────────────────────────────────────

class SendNotificationRequest(BaseModel):
    title: str
    description: Optional[str] = None
    receiver_user_idn: Optional[int] = None
    receiver_user_topic: Optional[str] = None
    schedule_to: Optional[datetime] = None  # ISO 8601; None = immediate

    @model_validator(mode="after")
    def check_receiver(self):
        if self.receiver_user_idn is None and self.receiver_user_topic is None:
            raise ValueError(
                "Provide either receiver_user_idn or receiver_user_topic"
            )
        return self


class NotificationResponse(BaseModel):
    notification_idn: int
    receiver_user_idn: Optional[int]
    receiver_user_topic: Optional[str]
    title: str
    description: Optional[str]
    status: str
    schedule_to: Optional[datetime]
    crt_dt: datetime

    class Config:
        from_attributes = True


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/send", response_model=NotificationResponse)
async def send_notification(
    req: SendNotificationRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Send an immediate or scheduled push notification.

    - **Immediate** (schedule_to is null): fires FCM right away and records status.
    - **Scheduled** (schedule_to is set): stores with status=pending; the background
      worker picks it up at the right time.
    """
    notif = Notification(
        receiver_user_idn=req.receiver_user_idn,
        receiver_user_topic=req.receiver_user_topic,
        title=req.title,
        description=req.description,
        status=NotifStatusEnum.pending,
        schedule_to=req.schedule_to,
    )
    db.add(notif)
    await db.commit()
    await db.refresh(notif)

    # Fire immediately if no schedule
    if req.schedule_to is None:
        await fcm_service.dispatch_notification(notif.notification_idn, db)
        await db.refresh(notif)

    return notif


@router.get("", response_model=List[NotificationResponse])
async def list_notifications(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session),
):
    """List past notifications ordered by creation date (newest first)."""
    result = await db.execute(
        select(Notification)
        .order_by(desc(Notification.crt_dt))
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


@router.post("/{notification_idn}/retry", response_model=NotificationResponse)
async def retry_notification(
    notification_idn: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Retry a failed notification."""
    result = await db.execute(
        select(Notification).where(
            Notification.notification_idn == notification_idn
        )
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")

    if notif.status != NotifStatusEnum.failed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot retry notification with status '{notif.status}'",
        )

    # Reset to pending then dispatch
    notif.status = NotifStatusEnum.pending
    notif.upd_dt = datetime.now(timezone.utc)
    await db.commit()

    await fcm_service.dispatch_notification(notification_idn, db)
    await db.refresh(notif)
    return notif
