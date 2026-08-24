from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, func, desc
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
import logging
import httpx

from app.database import get_db_session
from app.api.dependencies import get_admin_api_key, get_current_db_user, _require_admin
from app.models.db_models import User, Message, MessageTypeEnum, UserDevice, Game, GamePlayer, GameResultEnum, GameTypeEnum, UserRoleEnum
from app.services import fcm_service
from app.services.redis_client import redis_client

logger = logging.getLogger(__name__)

# ── Existing router: protected by X-Admin-API-Key header ─────────────────────
router = APIRouter(prefix="/api/admin", tags=["Admin"], dependencies=[Depends(get_admin_api_key)])

# ── New router: protected by Firebase auth + admin role ───────────────────────
firebase_router = APIRouter(prefix="/api/admin", tags=["Admin Dashboard"])


class AdminStatsResponse(BaseModel):
    users_online: int
    games_online_today: int
    games_offline_today: int
    games_in_progress_online: int
    games_in_progress_offline: int
    unread_messages: int
    total_users: int
    active_users_today: int


@firebase_router.get("/dashboard/stats", response_model=AdminStatsResponse)
async def get_dashboard_stats(
    admin_user_idn: int,
    tz_offset: int = Query(0, description="Timezone offset in minutes"),
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Aggregate dashboard statistics for the admin app.
    Protected by Firebase authentication + admin role check.
    admin_user_idn is the DB idn of the admin, used to count unread messages.
    """
    from datetime import date, timedelta
    from sqlalchemy import cast, Date as SADate

    # Users online
    users_online_result = await db.execute(
        select(func.count()).where(User.is_online == True, User.entity_active == True)
    )
    users_online = users_online_result.scalar() or 0

    # Total users
    total_users_result = await db.execute(
        select(func.count()).where(User.entity_active == True)
    )
    total_users = total_users_result.scalar() or 0

    today = (datetime.now(timezone.utc) + timedelta(minutes=tz_offset)).date()

    active_users_today_result = await db.execute(
        select(func.count(func.distinct(GamePlayer.user_idn))).join(
            Game, GamePlayer.game_idn == Game.game_idn
        ).where(
            func.date(Game.crt_dt + timedelta(minutes=tz_offset)) == today,
            GamePlayer.entity_active == True,
            Game.entity_active == True
        )
    )
    active_users_today = active_users_today_result.scalar() or 0

    # Games In Progress Online
    games_in_progress_online_result = await db.execute(
        select(func.count()).where(
            Game.result == GameResultEnum.in_progress,
            Game.game_type == GameTypeEnum.online,
            Game.entity_active == True,
        )
    )
    games_in_progress_online = games_in_progress_online_result.scalar() or 0

    # Games In Progress Offline
    games_in_progress_offline_result = await db.execute(
        select(func.count()).where(
            Game.result == GameResultEnum.in_progress,
            Game.game_type == GameTypeEnum.offline,
            Game.entity_active == True,
        )
    )
    games_in_progress_offline = games_in_progress_offline_result.scalar() or 0

    # Games Online Today
    games_online_today_result = await db.execute(
        select(func.count()).where(
            Game.entity_active == True,
            Game.game_type == GameTypeEnum.online,
            func.date(Game.crt_dt + timedelta(minutes=tz_offset)) == today,
        )
    )
    games_online_today = games_online_today_result.scalar() or 0

    # Games Offline Today
    games_offline_today_result = await db.execute(
        select(func.count()).where(
            Game.entity_active == True,
            Game.game_type == GameTypeEnum.offline,
            func.date(Game.crt_dt + timedelta(minutes=tz_offset)) == today,
        )
    )
    games_offline_today = games_offline_today_result.scalar() or 0

    # Unread messages for admin
    unread_result = await db.execute(
        select(func.count()).where(
            Message.to_user_idn == admin_user_idn,
            Message.read_at.is_(None),
            Message.entity_active == True,
        )
    )
    unread_messages = unread_result.scalar() or 0

    return AdminStatsResponse(
        users_online=users_online,
        games_online_today=games_online_today,
        games_offline_today=games_offline_today,
        games_in_progress_online=games_in_progress_online,
        games_in_progress_offline=games_in_progress_offline,
        unread_messages=unread_messages,
        total_users=total_users,
        active_users_today=active_users_today,
    )

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

@firebase_router.get("/messages/conversations", response_model=List[ConversationResponse])
async def get_conversations(
    admin_user_idn: int,
    current_user: User = Depends(_require_admin),
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

@firebase_router.get("/messages/{user_idn}", response_model=List[MessageResponse])
async def get_chat_history(
    user_idn: int,
    admin_user_idn: int,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(_require_admin),
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
    
    return messages

@firebase_router.post("/messages/reply", response_model=MessageResponse)
async def reply_to_user(
    req: AdminReplyRequest,
    current_user: User = Depends(_require_admin),
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


class MarkReadResponse(BaseModel):
    success: bool
    marked_count: int

@firebase_router.post("/messages/{user_idn}/read", response_model=MarkReadResponse)
async def mark_messages_read(
    user_idn: int,
    admin_user_idn: int,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Mark all unread messages from a specific user as read by the admin.
    """
    from sqlalchemy import update
    stmt = update(Message).where(
        Message.from_user_idn == user_idn,
        Message.to_user_idn == admin_user_idn,
        Message.read_at.is_(None)
    ).values(read_at=datetime.now(timezone.utc))
    
    result = await db.execute(stmt)
    await db.commit()
    return MarkReadResponse(success=True, marked_count=result.rowcount)


from app.services.remote_config_service import remote_config_service

class RemoteConfigRequest(BaseModel):
    parameters: Dict[str, Any]
    etag: str

@firebase_router.get("/remote-config")
async def get_remote_config(current_user: User = Depends(_require_admin)):
    """Fetch Firebase remote config template."""
    template, etag = await remote_config_service.get_template()
    return {"parameters": template.get("parameters", {}), "etag": etag}

@firebase_router.post("/remote-config")
async def update_remote_config(
    req: RemoteConfigRequest,
    current_user: User = Depends(_require_admin)
):
    """Update Firebase remote config template."""
    template, current_etag = await remote_config_service.get_template()
    
    template["parameters"] = req.parameters
    
    try:
        updated_template = await remote_config_service.publish_template(template, req.etag)
        return {"success": True, "parameters": updated_template.get("parameters", {})}
    except httpx.HTTPStatusError as e:
        logger.error(f"Error updating remote config: {e.response.text}")
        raise HTTPException(status_code=400, detail="Failed to update remote config. Ensure you are editing the latest version.")
    except Exception as e:
        logger.error(f"Error updating remote config: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@firebase_router.get("/redis", response_model=list[dict])
async def get_redis_data(
    current_user: User = Depends(_require_admin)
):
    try:
        keys = await redis_client.keys("*")
        # Limit to 1000 to prevent overwhelming
        keys = list(keys)[:1000]
        
        data = []
        for k in keys:
            # We don't know the exact type (string, set, hash), so we try to get it.
            # `type` command in Redis returns type name. But aioredis handles it.
            # For simplicity, we just fetch string or members if it's a set.
            # In memory store, we don't have a robust type() method. 
            # We will just try to get string value, if it's a set it might fail or we can catch it.
            # Actually, `redis_client.get` returns None if it's a set in aioredis.
            # Let's try to get string value.
            val_str = None
            try:
                val = await redis_client.get(k)
                if val is not None:
                    val_str = str(val)
                else:
                    # Might be a set
                    members = await redis_client.smembers(k)
                    if members:
                        val_str = f"Set: {members}"
            except Exception:
                val_str = "<Complex Data Type>"
                
            data.append({"key": k, "value": val_str})
        return data
    except Exception as e:
        logger.error(f"Error fetching redis data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@firebase_router.delete("/redis")
async def clear_redis_data(
    current_user: User = Depends(_require_admin)
):
    try:
        await redis_client.flushdb()
        return {"status": "success", "message": "Redis database cleared."}
    except Exception as e:
        logger.error(f"Error clearing redis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class ActiveUserResponse(BaseModel):
    user_idn: int
    display_name: Optional[str] = None
    email: Optional[str] = None
    last_active_date: Optional[datetime] = None
    online_games: int
    offline_games: int
    date_online_games: int
    date_offline_games: int

@firebase_router.get("/users/all", response_model=List[ActiveUserResponse])
async def get_all_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_admin)
):
    try:
        from sqlalchemy import func
        from app.models.db_models import GamePlayer, Game, GameTypeEnum
        
        offset = (page - 1) * limit

        subq_online = select(func.count(GamePlayer.game_idn)).join(
            Game, GamePlayer.game_idn == Game.game_idn
        ).where(
            GamePlayer.user_idn == User.user_idn,
            Game.game_type == GameTypeEnum.online,
            GamePlayer.entity_active == True,
            Game.entity_active == True
        ).scalar_subquery()

        subq_offline = select(func.count(GamePlayer.game_idn)).join(
            Game, GamePlayer.game_idn == Game.game_idn
        ).where(
            GamePlayer.user_idn == User.user_idn,
            Game.game_type == GameTypeEnum.offline,
            GamePlayer.entity_active == True,
            Game.entity_active == True
        ).scalar_subquery()

        query = select(
            User,
            subq_online.label("online_games"),
            subq_offline.label("offline_games")
        ).where(
            User.entity_active == True
        ).order_by(
            User.crt_dt.desc()
        ).offset(offset).limit(limit)

        result = await db.execute(query)
        rows = result.all()

        response = []
        for user_obj, online_count, offline_count in rows:
            response.append({
                "user_idn": user_obj.user_idn,
                "display_name": user_obj.display_name,
                "email": user_obj.email,
                "last_active_date": user_obj.last_active_date,
                "online_games": online_count or 0,
                "offline_games": offline_count or 0,
                "date_online_games": 0,
                "date_offline_games": 0,
            })
            
        return response
    except Exception as e:
        logger.error(f"Error fetching all users: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@firebase_router.get("/users/active-by-date", response_model=List[ActiveUserResponse])
async def get_active_users_by_date(
    date: str = Query(None, description="Date in YYYY-MM-DD format. Defaults to today."),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    tz_offset: int = Query(0, description="Timezone offset in minutes"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_admin)
):
    try:
        from datetime import datetime, timezone, timedelta
        from sqlalchemy import func
        from app.models.db_models import GamePlayer, Game, GameTypeEnum
        
        target_date = (datetime.now(timezone.utc) + timedelta(minutes=tz_offset)).date()
        if date:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()

        offset = (page - 1) * limit

        subq_date_online = select(func.count(GamePlayer.game_idn)).join(
            Game, GamePlayer.game_idn == Game.game_idn
        ).where(
            GamePlayer.user_idn == User.user_idn,
            Game.game_type == GameTypeEnum.online,
            func.date(Game.crt_dt + timedelta(minutes=tz_offset)) == target_date,
            GamePlayer.entity_active == True,
            Game.entity_active == True
        ).scalar_subquery()

        subq_date_offline = select(func.count(GamePlayer.game_idn)).join(
            Game, GamePlayer.game_idn == Game.game_idn
        ).where(
            GamePlayer.user_idn == User.user_idn,
            Game.game_type == GameTypeEnum.offline,
            func.date(Game.crt_dt + timedelta(minutes=tz_offset)) == target_date,
            GamePlayer.entity_active == True,
            Game.entity_active == True
        ).scalar_subquery()

        query = select(
            User,
            subq_date_online.label("date_online_games"),
            subq_date_offline.label("date_offline_games")
        ).where(
            User.entity_active == True,
            User.user_idn.in_(
                select(GamePlayer.user_idn).join(Game).where(
                    func.date(Game.crt_dt + timedelta(minutes=tz_offset)) == target_date
                )
            )
        ).order_by(
            User.last_active_date.desc()
        ).offset(offset).limit(limit)

        result = await db.execute(query)
        rows = result.all()

        response = []
        for user_obj, date_online_count, date_offline_count in rows:
            response.append({
                "user_idn": user_obj.user_idn,
                "display_name": user_obj.display_name,
                "email": user_obj.email,
                "last_active_date": user_obj.last_active_date,
                "online_games": 0,
                "offline_games": 0,
                "date_online_games": date_online_count or 0,
                "date_offline_games": date_offline_count or 0,
            })
            
        return response
    except Exception as e:
        logger.error(f"Error fetching active users by date: {e}")
        raise HTTPException(status_code=500, detail=str(e))
