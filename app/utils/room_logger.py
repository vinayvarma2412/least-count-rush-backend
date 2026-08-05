"""
Room-scoped structured logger for the Least Count Rush backend.

Usage:
    from app.utils.room_logger import get_room_logger, close_room_logger

    log = get_room_logger(room_id)
    log.info("player_joined", {"player_id": "abc", "name": "Alice"})
    log.warn("discard_invalid", {"reason": "not your turn"})
    log.error("ws_send_failed", {"error": str(e)})

Every log line format:
    2026-04-12T01:00:00.123Z [INFO] [ROOM:abc-123] [event_name] {"key": "value"}

Global logger (no room context):
    from app.utils.room_logger import global_log
    global_log.info("server_started", {"port": 8000})
"""

import logging
import logging.handlers
import json
import os
from datetime import datetime, timezone
from typing import Optional


# ─── Configuration ──────────────────────────────────────────────────────────

# Set to False or use env var to disable all room and global custom logging
ENABLE_LOGGING = os.getenv("ENABLE_ROOM_LOGGING", "false").lower() in ("true", "1", "yes")


# ─── Directories ────────────────────────────────────────────────────────────

_BASE_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "logs")
_ROOMS_LOG_DIR = os.path.join(_BASE_LOG_DIR, "rooms")

os.makedirs(_ROOMS_LOG_DIR, exist_ok=True)


# ─── Formatter ──────────────────────────────────────────────────────────────

class _StructuredFormatter(logging.Formatter):
    """Formats log records as a single structured line."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        level = record.levelname.ljust(5)
        room_tag = getattr(record, "room_tag", "[GLOBAL   ]")
        event = getattr(record, "event", "log")
        payload = getattr(record, "payload", None)

        line = f"{ts} [{level}] {room_tag} [{event}]"
        if payload:
            try:
                line += " " + json.dumps(payload, default=str, ensure_ascii=False)
            except Exception:
                line += " " + str(payload)
        elif record.msg:
            line += " " + record.getMessage()
            
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


_formatter = _StructuredFormatter()


# ─── Global file handler (all rooms combined) ────────────────────────────────

def _make_global_handler() -> logging.Handler:
    global_log_path = os.path.join(_BASE_LOG_DIR, "global.log")
    h = logging.handlers.RotatingFileHandler(
        global_log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    h.setFormatter(_formatter)
    return h


def _make_stderr_handler() -> logging.Handler:
    h = logging.StreamHandler()
    h.setFormatter(_formatter)
    return h


_global_file_handler = _make_global_handler()
_stderr_handler = _make_stderr_handler()

# ─── Root logger setup (call once at app startup) ────────────────────────────

def setup_logging(level: str = "INFO") -> None:
    """Configure the root logging system. Call this once in main.py at startup."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(log_level)

    # Remove any default handlers added by uvicorn/fastapi
    root.handlers.clear()
    
    _stderr_handler.setLevel(log_level)
    root.addHandler(_stderr_handler)
    if ENABLE_LOGGING:
        _global_file_handler.setLevel(log_level)
        root.addHandler(_global_file_handler)

    # Suppress noisy third-party loggers
    for noisy in ("uvicorn.access", "fastapi", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ─── RoomLogger wrapper ──────────────────────────────────────────────────────

class RoomLogger:
    """
    Structured logger scoped to a specific room.

    Every message is tagged with [ROOM:<room_id>] and written to:
      - logs/rooms/<room_id>.log  (room-specific rotating file)
      - logs/global.log           (all rooms combined)
      - stderr                    (visible in server console)
    """

    def __init__(self, room_id: str):
        self.room_id = room_id
        self._room_tag = f"[ROOM:{room_id[:8]}]"  # Short tag for readability
        self._logger = logging.getLogger(f"room.{room_id}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = True  # propagates to root → global.log + stderr

        self._file_handler = None
        if ENABLE_LOGGING:
            # Per-room rotating file handler
            room_log_path = os.path.join(_ROOMS_LOG_DIR, f"{room_id}.log")
            os.makedirs(_ROOMS_LOG_DIR, exist_ok=True)
            self._file_handler = logging.handlers.RotatingFileHandler(
                room_log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            self._file_handler.setFormatter(_formatter)
            self._logger.addHandler(self._file_handler)

    def _log(self, level: int, event: str, payload: Optional[dict] = None) -> None:
        if not ENABLE_LOGGING:
            return
        if payload is None:
            payload = {}
        payload["room_id"] = self.room_id
        self._logger.log(
            level,
            event,
            extra={"room_tag": self._room_tag, "event": event, "payload": payload},
        )

    def info(self, event: str, payload: Optional[dict] = None) -> None:
        self._log(logging.INFO, event, payload)

    def warn(self, event: str, payload: Optional[dict] = None) -> None:
        self._log(logging.WARNING, event, payload)

    def error(self, event: str, payload: Optional[dict] = None, exc_info: bool = False) -> None:
        if not ENABLE_LOGGING:
            return
        if payload is None:
            payload = {}
        payload["room_id"] = self.room_id
        self._logger.error(
            event,
            exc_info=exc_info,
            extra={"room_tag": self._room_tag, "event": event, "payload": payload},
        )

    def debug(self, event: str, payload: Optional[dict] = None) -> None:
        self._log(logging.DEBUG, event, payload)

    def close(self) -> None:
        """Flush and close the per-room file handler."""
        if getattr(self, "_file_handler", None):
            self._file_handler.flush()
            self._file_handler.close()
            self._logger.removeHandler(self._file_handler)


# ─── Registry ────────────────────────────────────────────────────────────────

_room_loggers: dict[str, RoomLogger] = {}


def get_room_logger(room_id: str) -> RoomLogger:
    """Get or create a RoomLogger for the given room_id."""
    if room_id not in _room_loggers:
        _room_loggers[room_id] = RoomLogger(room_id)
    return _room_loggers[room_id]


def close_room_logger(room_id: str) -> None:
    """Flush, close, and remove the logger for a deleted room."""
    logger = _room_loggers.pop(room_id, None)
    if logger:
        logger.info("room_logger_closed", {"reason": "room_deleted"})
        logger.close()


# ─── Global logger (no room context) ─────────────────────────────────────────

class _GlobalLogger:
    """Logger for server-level events not tied to a specific room."""

    def __init__(self):
        self._logger = logging.getLogger("global")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = True

    def _log(self, level: int, event: str, payload: Optional[dict] = None) -> None:
        self._logger.log(
            level,
            event,
            extra={"room_tag": "[GLOBAL   ]", "event": event, "payload": payload or {}},
        )

    def info(self, event: str, payload: Optional[dict] = None) -> None:
        self._log(logging.INFO, event, payload)

    def warn(self, event: str, payload: Optional[dict] = None) -> None:
        self._log(logging.WARNING, event, payload)

    def error(self, event: str, payload: Optional[dict] = None, exc_info: bool = False) -> None:
        self._logger.error(
            event,
            exc_info=exc_info,
            extra={"room_tag": "[GLOBAL   ]", "event": event, "payload": payload or {}},
        )
        
    def warning(self, event: str, payload: Optional[dict] = None) -> None:
        """Alias for warn"""
        self.warn(event, payload)


global_log = _GlobalLogger()
