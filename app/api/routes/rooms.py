"""
Room management REST API endpoints
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional
from pydantic import BaseModel
from app.schemas.room import RoomCreate, RoomResponse, RoomListResponse, RoomStatus, RoomType
from app.services.room_service import room_service
from app.api.dependencies import get_current_firebase_user
from sqlalchemy import select, func
from app.models.db_models import User
from app.database import get_db_session
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


class MatchmakeRequest(BaseModel):
    """Request body for Play With Randoms matchmaking."""
    creator_app_version: Optional[str] = None
    creator_build_number: Optional[str] = None


class PublicRoomStatsResponse(BaseModel):
    online_players: int


@router.post("/matchmake", response_model=RoomResponse, status_code=200)
async def matchmake(
    request: MatchmakeRequest = MatchmakeRequest(),
    user: dict = Depends(get_current_firebase_user),
):
    """Join an open public room, or create a new one if none exists.

    Used by the 'Play With Randoms' feature. Returns a RoomResponse the
    client should then connect to via WebSocket.
    """
    # Try to find an existing open public room
    existing_room_id = await room_service.find_open_public_room()
    if existing_room_id:
        room = await room_service.get_room(existing_room_id)
        if room:
            return room

    # No open public room found — create one with Tournament defaults
    room_data = RoomCreate(
        max_players=6,
        room_name=None,
        game_mode="Tournament",
        score_limit=100,
        creator_app_version=request.creator_app_version,
        creator_build_number=request.creator_build_number,
        room_type=RoomType.PUBLIC,
    )
    room = await room_service.create_room(room_data)
    return room


@router.post("", response_model=RoomResponse, status_code=201)
async def create_room(room_data: RoomCreate, user: dict = Depends(get_current_firebase_user)):
    """Create a new game room"""
    room = await room_service.create_room(room_data)
    return room


@router.get("/public/stats", response_model=PublicRoomStatsResponse)
async def get_public_room_stats(
    user: dict = Depends(get_current_firebase_user),
    db: AsyncSession = Depends(get_db_session)
):
    """Get count of total online players from DB."""
    users_online_result = await db.execute(
        select(func.count()).where(User.is_online == True, User.entity_active == True)
    )
    count = users_online_result.scalar() or 0
    return PublicRoomStatsResponse(online_players=count)


@router.get("/{room_id}", response_model=RoomResponse)
async def get_room(room_id: str, user: dict = Depends(get_current_firebase_user)):
    """Get room details by ID or 6-character code"""
    if len(room_id) == 6 and room_id.isalnum():
        room = await room_service.get_room_by_code(room_id.upper())
    else:
        room = await room_service.get_room(room_id)

    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return room


@router.get("", response_model=RoomListResponse)
async def list_rooms(
    status: Optional[RoomStatus] = Query(None, description="Filter by room status"),
    user: dict = Depends(get_current_firebase_user),
):
    """List all rooms, optionally filtered by status"""
    rooms = await room_service.list_rooms(status)
    return RoomListResponse(rooms=rooms, total=len(rooms))

