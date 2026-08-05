from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func, desc
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import List, Optional
import logging

from app.database import get_db_session
from app.api.dependencies import get_admin_api_key
from app.models.db_models import User, Message, MessageTypeEnum, UserDevice
from app.services import fcm_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["Admin"], dependencies=[Depends(get_admin_api_key)])

class AdminReplyRequest(BaseModel):
    admin_user_idn: int
    to_user_idn: int
    content: str

class MessageResponse(BaseModel):
    message_idn: int
    from_user_idn: int
    to_user_idn: int
    message_type: str
    content: Optional[str]
    read_at: Optional[datetime] = None
    crt_dt: datetime

    class Config:
        from_attributes = True

class ConversationResponse(BaseModel):
    user_idn: int
    display_name: Optional[str]
    last_message_dt: datetime
    unread_count: int

@router.get("/messages/conversations", response_model=List[ConversationResponse])
async def get_conversations(
    admin_user_idn: int,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get a list of users who have messaged the admin, along with unread counts.
    """
    # Find all users who have sent messages to the admin or received from the admin
    stmt = select(Message.from_user_idn, Message.to_user_idn, Message.crt_dt, Message.read_at).where(
        or_(
            Message.from_user_idn == admin_user_idn,
            Message.to_user_idn == admin_user_idn
        )
    ).order_by(desc(Message.crt_dt))
    
    result = await db.execute(stmt)
    messages = result.all()
    
    # Process into conversations
    conversations = {}
    
    for msg in messages:
        other_user_idn = msg.from_user_idn if msg.from_user_idn != admin_user_idn else msg.to_user_idn
        
        if other_user_idn not in conversations:
            conversations[other_user_idn] = {
                "user_idn": other_user_idn,
                "last_message_dt": msg.crt_dt,
                "unread_count": 0
            }
            
        # Count unread messages from them to us
        if msg.from_user_idn == other_user_idn and msg.read_at is None:
            conversations[other_user_idn]["unread_count"] += 1
            
    # Fetch user display names
    if conversations:
        user_idns = list(conversations.keys())
        user_stmt = select(User.user_idn, User.display_name).where(User.user_idn.in_(user_idns))
        user_result = await db.execute(user_stmt)
        users = user_result.all()
        
        for u in users:
            if u.user_idn in conversations:
                conversations[u.user_idn]["display_name"] = u.display_name
                
    # Sort by last message date
    conv_list = list(conversations.values())
    conv_list.sort(key=lambda x: x["last_message_dt"], reverse=True)
    
    return conv_list

@router.get("/messages/{user_idn}", response_model=List[MessageResponse])
async def get_chat_history(
    user_idn: int,
    admin_user_idn: int,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get chat history between the admin and a specific user.
    """
    stmt = select(Message).where(
        or_(
            and_(Message.from_user_idn == admin_user_idn, Message.to_user_idn == user_idn),
            and_(Message.from_user_idn == user_idn, Message.to_user_idn == admin_user_idn)
        )
    ).order_by(desc(Message.crt_dt)).limit(limit).offset(offset)
    
    result = await db.execute(stmt)
    messages = result.scalars().all()
    
    # Return in chronological order
    return messages[::-1]

@router.post("/messages/reply", response_model=MessageResponse)
async def reply_to_user(
    req: AdminReplyRequest,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Send a message to a user as the admin.
    """
    # Verify the admin user exists
    admin_user = await db.get(User, req.admin_user_idn)
    if not admin_user:
        raise HTTPException(status_code=404, detail="Admin user not found")
        
    # Verify the target user exists
    target_user = await db.get(User, req.to_user_idn)
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")

    new_msg = Message(
        from_user_idn=req.admin_user_idn,
        to_user_idn=req.to_user_idn,
        message_type=MessageTypeEnum.text,
        content=req.content,
        read_at=None,
        crt_dt=datetime.now(timezone.utc)
    )
    
    db.add(new_msg)
    await db.commit()
    await db.refresh(new_msg)

    # ── Push notification to target user ────────────────────────────────────
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
            preview = req.content[:80] + ("…" if len(req.content) > 80 else "")
            fcm_service.send_to_tokens(
                tokens,
                title="💬 Support Reply",
                body=preview,
                data={"type": "admin_chat"},
            )
    except Exception as fcm_exc:
        logger.warning("admin_reply_fcm_failed", extra={"error": str(fcm_exc)})
    # ────────────────────────────────────────────────────────────────────────

    return new_msg
