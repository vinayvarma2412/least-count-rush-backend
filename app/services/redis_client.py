"""
Cache backend — Redis or in-process memory, selected by USE_REDIS env var.

  USE_REDIS=true   (default) → real Redis/Valkey connection via aioredis
  USE_REDIS=false             → in-process dict store, zero network latency

Set REDIS_URL in .env when USE_REDIS=true:
  REDIS_URL=redis://localhost:6379               (local)
  REDIS_URL=rediss://:<password>@<host>:6380     (Upstash TLS)

⚠ Memory backend is single-process only. Do NOT use with multiple Uvicorn
  workers or multi-machine deployments — rooms will not be shared.
"""
import os
import time
import asyncio
from typing import Optional, Any

# ── Shared constant ──────────────────────────────────────────────────────────

# Default TTL for all room/game keys: 4 hours.
KEY_TTL_SECONDS: int = 4 * 3600

# ── Feature flag ─────────────────────────────────────────────────────────────

_use_redis_raw = os.getenv("USE_REDIS", "true").strip().lower()
USE_REDIS: bool = _use_redis_raw not in ("false", "0", "no", "off")

BACKEND_NAME: str = "redis" if USE_REDIS else "memory"

# ── Memory backend ────────────────────────────────────────────────────────────

class _MemoryPipeline:
    """Queued set of operations executed atomically (within asyncio single-thread)."""

    def __init__(self, store: "_MemoryStore"):
        self._store = store
        self._ops: list = []

    def setex(self, key: str, ttl: int, value: str):
        self._ops.append(("setex", key, ttl, value))
        return self

    def set(self, key: str, value: str):
        self._ops.append(("set", key, value))
        return self

    def delete(self, *keys: str):
        self._ops.append(("delete", keys))
        return self

    def sadd(self, key: str, *members: str):
        self._ops.append(("sadd", key, members))
        return self

    def srem(self, key: str, *members: str):
        self._ops.append(("srem", key, members))
        return self

    async def execute(self):
        async with self._store._lock:
            results = []
            for op in self._ops:
                if op[0] == "setex":
                    _, key, ttl, value = op
                    self._store._data[key] = (value, time.monotonic() + ttl)
                    results.append(True)
                elif op[0] == "set":
                    _, key, value = op
                    self._store._data[key] = (value, None)
                    results.append(True)
                elif op[0] == "delete":
                    _, keys = op
                    for k in keys:
                        self._store._data.pop(k, None)
                    results.append(len(keys))
                elif op[0] == "sadd":
                    _, key, members = op
                    if key not in self._store._sets:
                        self._store._sets[key] = set()
                    self._store._sets[key].update(members)
                    results.append(len(members))
                elif op[0] == "srem":
                    _, key, members = op
                    s = self._store._sets.get(key, set())
                    removed = 0
                    for m in members:
                        if m in s:
                            s.discard(m)
                            removed += 1
                    results.append(removed)
            return results


class _MemoryStore:
    """
    In-process key-value store mimicking the aioredis.Redis interface used by
    room_service and game_service.

    Thread-safety: asyncio.Lock (single-threaded event loop, no actual threads).
    TTL: enforced lazily on every read (no background eviction task needed).
    """

    def __init__(self):
        # key → (value_str, expires_at_monotonic | None)
        self._data: dict[str, tuple[str, Optional[float]]] = {}
        # key → set of str members (for SADD/SMEMBERS/SREM)
        self._sets: dict[str, set] = {}
        self._lock = asyncio.Lock()

    def _is_expired(self, key: str) -> bool:
        entry = self._data.get(key)
        if entry is None:
            return True
        _, expires_at = entry
        if expires_at is None:
            return False
        return time.monotonic() > expires_at

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            if self._is_expired(key):
                self._data.pop(key, None)
                return None
            return self._data[key][0]

    async def set(self, key: str, value: str, ex: Optional[int] = None):
        async with self._lock:
            expires_at = (time.monotonic() + ex) if ex else None
            self._data[key] = (value, expires_at)

    async def setex(self, key: str, ttl: int, value: str):
        async with self._lock:
            self._data[key] = (value, time.monotonic() + ttl)

    async def setnx(self, key: str, value: str) -> int:
        """Set key only if it doesn't exist. Returns 1 if set, 0 otherwise."""
        async with self._lock:
            if not self._is_expired(key) and key in self._data:
                return 0
            self._data[key] = (value, None)
            return 1

    async def delete(self, *keys: str) -> int:
        async with self._lock:
            removed = 0
            for k in keys:
                if k in self._data:
                    del self._data[k]
                    removed += 1
            return removed

    async def exists(self, *keys: str) -> int:
        async with self._lock:
            return sum(
                1 for k in keys
                if k in self._data and not self._is_expired(k)
            )

    async def sadd(self, key: str, *members: str) -> int:
        async with self._lock:
            if key not in self._sets:
                self._sets[key] = set()
            before = len(self._sets[key])
            self._sets[key].update(members)
            return len(self._sets[key]) - before

    async def smembers(self, key: str) -> set:
        async with self._lock:
            return set(self._sets.get(key, set()))

    async def srem(self, key: str, *members: str) -> int:
        async with self._lock:
            s = self._sets.get(key, set())
            removed = 0
            for m in members:
                if m in s:
                    s.discard(m)
                    removed += 1
            return removed

    async def keys(self, pattern: str = "*") -> list[str]:
        async with self._lock:
            # Basic wildcard support for * only, sufficient for simple matching
            import fnmatch
            return [
                k for k in self._data.keys()
                if not self._is_expired(k) and fnmatch.fnmatch(k, pattern)
            ]

    async def flushdb(self) -> None:
        async with self._lock:
            self._data.clear()
            self._sets.clear()

    def pipeline(self) -> "_MemoryPipeline":
        return _MemoryPipeline(self)

    async def aclose(self):
        """No-op — nothing to close for in-memory store."""
        pass


# ── Backend selection ─────────────────────────────────────────────────────────

if USE_REDIS:
    import redis.asyncio as aioredis

    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")

    ssl_kwargs = {}
    if REDIS_URL.startswith("rediss://"):
        ssl_kwargs["ssl_cert_reqs"] = "none"

    # decode_responses=True → all keys/values come back as str, not bytes.
    redis_client: Any = aioredis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        **ssl_kwargs,
    )
else:
    redis_client: Any = _MemoryStore()

# ── Dedicated Memory Store ──────────────────────────────────────────────────
# Used by game_service to store fast, zero-latency state regardless of USE_REDIS
memory_store: _MemoryStore = _MemoryStore()
