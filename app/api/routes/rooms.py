"""
Room management REST API endpoints
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional
from app.schemas.room import RoomCreate, RoomResponse, RoomListResponse, RoomStatus
from app.services.room_service import room_service
from app.api.dependencies import get_current_firebase_user

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


@router.post("", response_model=RoomResponse, status_code=201)
async def create_room(room_data: RoomCreate, user: dict = Depends(get_current_firebase_user)):
    """Create a new game room"""
    room = await room_service.create_room(room_data)
    return room


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
