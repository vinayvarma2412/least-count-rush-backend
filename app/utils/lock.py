import asyncio
from collections import defaultdict
import functools
import inspect

_room_locks = defaultdict(asyncio.Lock)

def get_room_lock(room_id: str) -> asyncio.Lock:
    """Get an asyncio.Lock for the given room_id to serialize concurrent access on a single machine."""
    return _room_locks[room_id]

def with_room_lock(func):
    """Decorator to acquire the room lock for the duration of the function."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        room_id = bound.arguments.get('room_id')
        
        if not room_id:
            raise ValueError(f"room_id is required to use @with_room_lock on {func.__name__}")
            
        from app.utils.debug_log import log_to_file
        import time
        
        lock = get_room_lock(room_id)
        log_to_file(f"LOCK: {func.__name__} waiting for room {room_id}")
        t0 = time.time()
        async with lock:
            log_to_file(f"LOCK: {func.__name__} ACQUIRED room {room_id} after {time.time()-t0:.3f}s")
            try:
                return await func(*args, **kwargs)
            finally:
                log_to_file(f"LOCK: {func.__name__} RELEASING room {room_id}")
                
    return wrapper
