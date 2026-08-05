from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func, update
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import List, Optional
import logging

from app.database import get_db_session
from app.api.dependencies import get_current_db_user
from app.models.db_models import User, Message, MessageTypeEnum, UserDevice
from app.services import fcm_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/messages", tags=["messages"])

class SendMessageRequest(BaseModel):
    to_user_idn: int
    content: str
    message_type: str = "text"

class MessageResponse(BaseModel):
    message_idn: int
    from_user_idn: int
    to_user_idn: int
    message_type: str
    content: str
    delivered_at: Optional[str] = None
    read_at: Optional[str] = None
    crt_dt: str

    class Config:
        from_attributes = True

@router.post("", response_model=MessageResponse)
async def send_message(
    req: SendMessageRequest,
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session)
):
    """Send a message to another user."""
    # Verify recipient exists
    result = await db.execute(select(User).where(User.user_idn == req.to_user_idn))
    recipient = result.scalar_one_or_none()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")

    try:
        msg_type = MessageTypeEnum(req.message_type)
    except ValueError:
        msg_type = MessageTypeEnum.text

    new_message = Message(
        from_user_idn=user.user_idn,
        to_user_idn=req.to_user_idn,
        message_type=msg_type,
        content=req.content,
        delivered_at=datetime.now(timezone.utc)
    )
    
    db.add(new_message)
    await db.commit()
    await db.refresh(new_message)
    
    # ── Push notification to recipient ──────────────────────────────────────
    try:
        tokens_result = await db.execute(
            select(UserDevice.fcm_token).where(
                UserDevice.user_idn == req.to_user_idn,
                UserDevice.entity_active == True,
                UserDevice.fcm_token.isnot(None),
            )
        )
        tokens = [row[0] for row in tokens_result.all() if row[0]]
        if tokens:
            sender_name = user.display_name or f"User #{user.user_idn}"
            preview = req.content[:80] + ("…" if len(req.content) > 80 else "")
            fcm_service.send_to_tokens(
                tokens,
                title=f"New message from {sender_name}",
                body=preview,
                data={"type": "chat", "from_user_idn": str(user.user_idn)},
            )
    except Exception as fcm_exc:
        logger.warning("send_message_fcm_failed", extra={"error": str(fcm_exc)})
    # ────────────────────────────────────────────────────────────────────────
    
    return MessageResponse(
        message_idn=new_message.message_idn,
        from_user_idn=new_message.from_user_idn,
        to_user_idn=new_message.to_user_idn,
        message_type=new_message.message_type.value,
        content=new_message.content,
        delivered_at=new_message.delivered_at.isoformat() if new_message.delivered_at else None,
        read_at=new_message.read_at.isoformat() if new_message.read_at else None,
        crt_dt=new_message.crt_dt.isoformat() if new_message.crt_dt else None
    )

@router.get("/unread/count")
async def get_unread_count(
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session)
):
    """Get the total unread messages count for the current user."""
    result = await db.execute(
        select(func.count(Message.message_idn))
        .where(
            and_(
                Message.to_user_idn == user.user_idn,
                Message.read_at.is_(None),
                Message.entity_active == True
            )
        )
    )
    count = result.scalar() or 0
    return {"unread_count": count}

@router.get("/{other_user_idn}", response_model=List[MessageResponse])
async def get_messages(
    other_user_idn: int,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session)
):
    """Get conversation history with another user."""
    result = await db.execute(
        select(Message)
        .where(
            and_(
                Message.entity_active == True,
                or_(
                    and_(Message.from_user_idn == user.user_idn, Message.to_user_idn == other_user_idn),
                    and_(Message.from_user_idn == other_user_idn, Message.to_user_idn == user.user_idn)
                )
            )
        )
        .order_by(Message.crt_dt.asc())
        .offset(offset)
        .limit(limit)
    )
    
    messages = result.scalars().all()
    
    return [
        MessageResponse(
            message_idn=msg.message_idn,
            from_user_idn=msg.from_user_idn,
            to_user_idn=msg.to_user_idn,
            message_type=msg.message_type.value,
            content=msg.content,
            delivered_at=msg.delivered_at.isoformat() if msg.delivered_at else None,
            read_at=msg.read_at.isoformat() if msg.read_at else None,
            crt_dt=msg.crt_dt.isoformat() if msg.crt_dt else None
        ) for msg in messages
    ]

@router.put("/{other_user_idn}/read")
async def mark_messages_read(
    other_user_idn: int,
    user: User = Depends(get_current_db_user),
    db: AsyncSession = Depends(get_db_session)
):
    """Mark all messages from other_user_idn to current user as read."""
    await db.execute(
        update(Message)
        .where(
            and_(
                Message.from_user_idn == other_user_idn,
                Message.to_user_idn == user.user_idn,
                Message.read_at.is_(None)
            )
        )
        .values(read_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"status": "success"}
