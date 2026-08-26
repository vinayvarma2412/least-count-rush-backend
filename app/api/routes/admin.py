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
from app.models.db_models import User, Message, MessageTypeEnum, UserDevice, Game, GamePlayer, GameResultEnum, GameTypeEnum, GameModeEnum, UserRoleEnum, LeaderboardSeason
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
    # Active today means last_active_date is today in user's timezone
    user_now = datetime.now(timezone.utc) + timedelta(minutes=tz_offset)
    user_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = user_today_start - timedelta(minutes=tz_offset)
    active_users_today_result = await db.execute(
        select(func.count()).where(
            User.last_active_date >= today_start,
            User.entity_active == True
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
    is_online: bool = False

@firebase_router.get("/users", response_model=List[ActiveUserResponse])
async def get_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    active_today: bool = Query(False),
    sortBy: str = Query("createdAt"),
    sortOrder: str = Query("desc"),
    tz_offset: int = Query(0, description="Timezone offset in minutes"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_admin)
):
    try:
        from sqlalchemy import func, asc, desc, nullslast
        from app.models.db_models import GamePlayer, Game, GameTypeEnum
        from datetime import datetime, timezone, timedelta
        
        offset = (page - 1) * limit

        user_now = datetime.now(timezone.utc) + timedelta(minutes=tz_offset)
        user_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start = user_today_start - timedelta(minutes=tz_offset)
        
        subq_online_base = select(func.count(GamePlayer.game_idn)).join(
            Game, GamePlayer.game_idn == Game.game_idn
        ).where(
            GamePlayer.user_idn == User.user_idn,
            Game.game_type == GameTypeEnum.online,
            GamePlayer.entity_active == True,
            Game.entity_active == True
        )

        subq_offline_base = select(func.count(GamePlayer.game_idn)).join(
            Game, GamePlayer.game_idn == Game.game_idn
        ).where(
            GamePlayer.user_idn == User.user_idn,
            Game.game_type == GameTypeEnum.offline,
            GamePlayer.entity_active == True,
            Game.entity_active == True
        )
        
        if active_today:
            subq_online_base = subq_online_base.where(Game.crt_dt >= today_start)
            subq_offline_base = subq_offline_base.where(Game.crt_dt >= today_start)

        subq_online = subq_online_base.scalar_subquery()
        subq_offline = subq_offline_base.scalar_subquery()

        query = select(
            User,
            subq_online.label("online_games"),
            subq_offline.label("offline_games")
        ).where(
            User.entity_active == True
        )

        if active_today:
            # Active today in user's timezone
            query = query.where(User.last_active_date >= today_start)

        if sortBy == "onlineGames":
            order_col = subq_online
        elif sortBy == "offlineGames":
            order_col = subq_offline
        elif sortBy == "name":
            order_col = User.display_name
        elif sortBy == "lastActiveDate":
            order_col = User.last_active_date
        else:
            order_col = User.crt_dt

        if sortOrder == "asc":
            query = query.order_by(nullslast(asc(order_col)))
        else:
            query = query.order_by(nullslast(desc(order_col)))

        query = query.offset(offset).limit(limit)

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
                "is_online": user_obj.is_online or False,
            })
            
        return response
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class SeasonAdminResponse(BaseModel):
    season_idn: int
    season_name: str
    start_date: datetime
    end_date: datetime
    is_active: bool

    class Config:
        from_attributes = True

class SeasonCreateRequest(BaseModel):
    season_name: str
    start_date: datetime
    end_date: datetime
    is_active: bool = False

class SeasonUpdateRequest(BaseModel):
    season_name: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    is_active: Optional[bool] = None

@firebase_router.get("/seasons", response_model=List[SeasonAdminResponse])
async def get_all_seasons(
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Get all seasons."""
    try:
        result = await db.execute(
            select(LeaderboardSeason).order_by(desc(LeaderboardSeason.start_date))
        )
        return result.scalars().all()
    except Exception as e:
        logger.error(f"Error fetching seasons: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch seasons")

@firebase_router.post("/seasons", response_model=SeasonAdminResponse)
async def create_season(
    req: SeasonCreateRequest,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Create a new season."""
    try:
        if req.is_active:
            # Deactivate all other seasons if the new one is active
            await db.execute(
                LeaderboardSeason.__table__.update().values(is_active=False)
            )

        new_season = LeaderboardSeason(
            season_name=req.season_name,
            start_date=req.start_date,
            end_date=req.end_date,
            is_active=req.is_active
        )
        db.add(new_season)
        await db.commit()
        await db.refresh(new_season)
        return new_season
    except Exception as e:
        await db.rollback()
        logger.error(f"Error creating season: {e}")
        raise HTTPException(status_code=500, detail="Failed to create season")

@firebase_router.put("/seasons/{season_idn}", response_model=SeasonAdminResponse)
async def update_season(
    season_idn: int,
    req: SeasonUpdateRequest,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    """Update an existing season."""
    try:
        result = await db.execute(
            select(LeaderboardSeason).where(LeaderboardSeason.season_idn == season_idn)
        )
        season = result.scalar_one_or_none()
        
        if not season:
            raise HTTPException(status_code=404, detail="Season not found")

        if req.is_active is not None and req.is_active:
            # Deactivate all other seasons if this one is made active
            await db.execute(
                LeaderboardSeason.__table__.update().values(is_active=False).where(LeaderboardSeason.season_idn != season_idn)
            )

        if req.season_name is not None:
            season.season_name = req.season_name
        if req.start_date is not None:
            season.start_date = req.start_date
        if req.end_date is not None:
            season.end_date = req.end_date
        if req.is_active is not None:
            season.is_active = req.is_active

        await db.commit()
        await db.refresh(season)
        return season
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error updating season {season_idn}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update season")

import json

class AnnouncementModel(BaseModel):
    id: str
    title: str
    body: str
    display_type: str
    enabled: bool = False
    priority: int = 10
    target_min_version: str = "1.0.0"
    image_url: Optional[str] = ""
    cta_label: Optional[str] = ""
    cta_url: Optional[str] = ""
    cta_route: Optional[str] = ""
    start_time: Optional[str] = None
    end_time: Optional[str] = None

class AnnouncementsResponse(BaseModel):
    announcements: List[AnnouncementModel]
    etag: str

class UpdateAnnouncementsRequest(BaseModel):
    announcements: List[AnnouncementModel]
    etag: str

@firebase_router.get("/announcements", response_model=AnnouncementsResponse)
async def get_announcements(current_user: User = Depends(_require_admin)):
    """Fetch announcements from Firebase remote config."""
    try:
        template, etag = await remote_config_service.get_template()
        parameters = template.get("parameters", {})
        
        announcements_param = parameters.get("announcements", {})
        default_value = announcements_param.get("defaultValue", {}).get("value", "[]")
        
        try:
            announcements_list = json.loads(default_value)
        except json.JSONDecodeError:
            announcements_list = []

        return AnnouncementsResponse(announcements=announcements_list, etag=etag)
    except Exception as e:
        logger.error(f"Error fetching announcements: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch announcements")

@firebase_router.post("/announcements", response_model=AnnouncementsResponse)
async def update_announcements(
    req: UpdateAnnouncementsRequest,
    current_user: User = Depends(_require_admin)
):
    """Update announcements in Firebase remote config."""
    try:
        template, current_etag = await remote_config_service.get_template()
        
        # Serialize the announcements to JSON string
        announcements_json = json.dumps([a.dict(exclude_none=True) for a in req.announcements])
        
        if "parameters" not in template:
            template["parameters"] = {}
            
        template["parameters"]["announcements"] = {
            "defaultValue": {
                "value": announcements_json
            },
            "valueType": "JSON"
        }
        
        updated_template = await remote_config_service.publish_template(template, req.etag)
        
        return AnnouncementsResponse(announcements=req.announcements, etag=req.etag)
    except httpx.HTTPStatusError as e:
        logger.error(f"Error updating announcements: {e.response.text}")
        raise HTTPException(status_code=400, detail="Failed to update announcements. Ensure you are editing the latest version.")
    except Exception as e:
        logger.error(f"Error updating announcements: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

class AdminGameResponse(BaseModel):
    game_idn: int
    game_type: str
    game_mode: str
    result: str
    total_players: int
    winner_user_idn: Optional[int]
    created_user_idn: Optional[int]
    host_display_name: Optional[str] = None
    crt_dt: datetime
    started_at: Optional[datetime]
    ended_at: Optional[datetime]

    class Config:
        from_attributes = True

class AdminGamePlayerResponse(BaseModel):
    user_idn: int
    display_name: Optional[str] = None
    avatar_seed: Optional[str] = None
    rank_position: Optional[int] = None
    final_score: int
    rounds_survived: int
    lp_earned: int
    
class AdminGameDetailsResponse(BaseModel):
    game: AdminGameResponse
    players: List[AdminGamePlayerResponse]

@firebase_router.get("/games/{game_idn}/details", response_model=AdminGameDetailsResponse)
async def get_admin_game_details(
    game_idn: int,
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    from sqlalchemy.orm import selectinload
    # 1. Fetch game
    stmt = select(Game, User.display_name.label("host_display_name")).outerjoin(User, Game.created_user_idn == User.user_idn).where(Game.game_idn == game_idn)
    result = await db.execute(stmt)
    row = result.first()
    
    if not row:
        raise HTTPException(status_code=404, detail="Game not found")
        
    game_obj, host_name = row
    
    game_data = {
        "game_idn": game_obj.game_idn,
        "game_type": game_obj.game_type,
        "game_mode": game_obj.game_mode,
        "result": game_obj.result,
        "total_players": game_obj.total_players,
        "winner_user_idn": game_obj.winner_user_idn,
        "created_user_idn": game_obj.created_user_idn,
        "crt_dt": game_obj.crt_dt,
        "started_at": game_obj.started_at,
        "ended_at": game_obj.ended_at,
        "host_display_name": host_name
    }
    
    # 2. Fetch players
    from sqlalchemy import asc, nullslast
    player_stmt = select(GamePlayer, User).join(User, GamePlayer.user_idn == User.user_idn).where(GamePlayer.game_idn == game_idn).order_by(nullslast(asc(GamePlayer.rank_position)))
    player_result = await db.execute(player_stmt)
    player_rows = player_result.all()
    
    players_data = []
    for gp, user in player_rows:
        players_data.append({
            "user_idn": gp.user_idn,
            "display_name": user.display_name,
            "avatar_seed": user.avatar_seed,
            "rank_position": gp.rank_position,
            "final_score": gp.final_score,
            "rounds_survived": gp.rounds_survived,
            "lp_earned": gp.lp_earned
        })
        
    return {
        "game": game_data,
        "players": players_data
    }

@firebase_router.get("/games", response_model=List[AdminGameResponse])
async def get_admin_games(
    page: int = 1,
    limit: int = 20,
    status: Optional[str] = None,
    mode: Optional[str] = None,
    type: Optional[str] = None,
    startDate: Optional[str] = None,
    endDate: Optional[str] = None,
    sortBy: str = "createdAt",
    sortOrder: str = "desc",
    current_user: User = Depends(_require_admin),
    db: AsyncSession = Depends(get_db_session)
):
    from sqlalchemy import asc
    stmt = select(Game, User.display_name.label("host_display_name")).outerjoin(User, Game.created_user_idn == User.user_idn)

    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            valid_statuses = [s for s in statuses if s in [e.value for e in GameResultEnum]]
            if valid_statuses:
                stmt = stmt.where(Game.result.in_(valid_statuses))

    if mode:
        modes = [m.strip() for m in mode.split(",") if m.strip()]
        if modes:
            valid_modes = [m for m in modes if m in [e.value for e in GameModeEnum]]
            if valid_modes:
                stmt = stmt.where(Game.game_mode.in_(valid_modes))

    if type:
        types = [t.strip() for t in type.split(",") if t.strip()]
        if types:
            valid_types = [t for t in types if t in [e.value for e in GameTypeEnum]]
            if valid_types:
                stmt = stmt.where(Game.game_type.in_(valid_types))

    if startDate:
        try:
            start_dt = datetime.fromisoformat(startDate.replace('Z', '+00:00'))
            stmt = stmt.where(Game.crt_dt >= start_dt)
        except ValueError:
            pass

    if endDate:
        try:
            end_dt = datetime.fromisoformat(endDate.replace('Z', '+00:00'))
            stmt = stmt.where(Game.crt_dt <= end_dt)
        except ValueError:
            pass

    if sortBy == "playersCount":
        order_col = Game.total_players
    else:
        order_col = Game.crt_dt

    if sortOrder == "asc":
        stmt = stmt.order_by(asc(order_col))
    else:
        stmt = stmt.order_by(desc(order_col))

    stmt = stmt.offset((page - 1) * limit).limit(limit)

    result = await db.execute(stmt)
    rows = result.all()
    
    response = []
    for game_obj, host_name in rows:
        response.append({
            "game_idn": game_obj.game_idn,
            "game_type": game_obj.game_type,
            "game_mode": game_obj.game_mode,
            "result": game_obj.result,
            "total_players": game_obj.total_players,
            "winner_user_idn": game_obj.winner_user_idn,
            "created_user_idn": game_obj.created_user_idn,
            "crt_dt": game_obj.crt_dt,
            "started_at": game_obj.started_at,
            "ended_at": game_obj.ended_at,
            "host_display_name": host_name
        })
    
    return response
