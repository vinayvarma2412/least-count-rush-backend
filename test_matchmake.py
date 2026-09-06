import asyncio
from app.services.room_service import room_service
from app.schemas.room import RoomCreate, RoomType
import json

async def main():
    room_data = RoomCreate(
        max_players=6,
        game_mode="Tournament",
        score_limit=100,
        room_type=RoomType.PUBLIC,
    )
    room = await room_service.create_room(room_data)
    print("room_type in RoomResponse:", type(room.room_type), room.room_type)
    print("room.model_dump():", json.dumps(room.model_dump(mode="json")))

asyncio.run(main())
