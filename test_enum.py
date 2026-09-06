import json
from enum import Enum
class RoomType(str, Enum):
    PRIVATE = "private"
    PUBLIC = "public"
print(json.dumps(RoomType.PUBLIC))
