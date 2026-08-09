"""
Pydantic schemas for Room
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum


class RoomStatus(str, Enum):
    """Room status enumeration"""
    WAITING = "waiting"
    PLAYING = "playing"
    FINISHED = "finished"


class RoomCreate(BaseModel):
    """Schema for creating a room"""
    max_players: int = Field(default=6, ge=2, le=6, description="Maximum number of players")
    room_name: Optional[str] = Field(default=None, description="Optional room name")
    game_mode: Optional[str] = Field(
        default=None,
        description='Game mode: "Single Game" or "Tournament"',
    )
    score_limit: Optional[int] = Field(
        default=None,
        ge=1,
        description="Score limit for tournament mode",
    )
    creator_app_version: Optional[str] = Field(
        default=None,
        description="App version string of the room creator (e.g. '2.0.7')",
    )
    creator_build_number: Optional[str] = Field(
        default=None,
        description="Build number of the room creator (e.g. '22')",
    )


class PlayerInfo(BaseModel):
    """Player information"""
    player_id: str
    player_name: str
    avatar_seed: Optional[str] = Field(default=None, description="Seed used by RandomAvatar on clients")
    is_ready: bool = False
    is_connected: bool = Field(default=False, description="True if player has internet connection and WebSocket is active")
    is_admin: bool = Field(default=False, description="True if player is the room admin")
    is_in_game: bool = Field(default=False, description="True if player has entered the game view (in room or exited)")
    disconnect_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when player last disconnected; cleared to None on reconnect"
    )
    is_exited: bool = Field(
        default=False,
        description="True if player was offline for > 60 s and permanently exited this session"
    )
    exited_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when player was permanently marked as Exited"
    )


class RoomResponse(BaseModel):
    """Schema for room response"""
    room_id: str
    room_code: str = Field(description="Short 6-character code for easy sharing")
    room_name: Optional[str]
    players: List[PlayerInfo]
    waiting_players: List[PlayerInfo] = Field(default_factory=list)
    max_players: int
    game_mode: Optional[str] = Field(
        default=None,
        description='Game mode: "Single Game" or "Tournament"',
    )
    score_limit: Optional[int] = Field(
        default=None,
        description="Score limit for tournament mode",
    )
    creator_app_version: Optional[str] = Field(
        default=None,
        description="App version of the room creator (e.g. '2.0.7')",
    )
    creator_build_number: Optional[str] = Field(
        default=None,
        description="Build number of the room creator (e.g. '22')",
    )
    server_url: Optional[str] = Field(
        default=None,
        description="Public URL of the server where this room is hosted"
    )
    status: RoomStatus
    created_at: datetime

    class Config:
        from_attributes = True


class RoomListResponse(BaseModel):
    """Schema for listing rooms"""
    rooms: List[RoomResponse]
    total: int

