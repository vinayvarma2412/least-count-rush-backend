from app.schemas.room import RoomResponse, RoomStatus, RoomType
from datetime import datetime, timezone
r = RoomResponse(
    room_id="1", room_code="A", room_name=None, players=[], max_players=6, status=RoomStatus.WAITING,
    created_at=datetime.now(timezone.utc), room_type=RoomType.PUBLIC
)
print(r.model_dump(mode="json"))
