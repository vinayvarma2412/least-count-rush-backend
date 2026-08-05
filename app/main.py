"""
FastAPI application entry point for Least Count Rush Backend
"""
import os
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import rooms, deck, games, users, user_game_stats, leaderboard, messages, admin, notifications, ads, rate_us
from app.api.websocket import room_ws, presence_ws
from app.utils.room_logger import setup_logging, global_log
from app.services.room_service import room_service
from app.database import engine
from contextlib import asynccontextmanager
import asyncio

# ── Logging bootstrap (must run before any logger is used) ──────────────────
_log_level = os.getenv("LOG_LEVEL", "INFO")
setup_logging(level=_log_level)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    global_log.info("server_started", {"log_level": _log_level, "version": "1.0.0"})
    
    # We can create tables here if we don't want to use Alembic for local testing
    # but since Supabase is being used, it's expected tables are created externally.
    if "localhost" in str(engine.url) or "127.0.0.1" in str(engine.url):
        global_log.info("Local testing database detected, creating tables if they don't exist...")
        async with engine.begin() as conn:
            from app.database import Base
            # Import models to ensure they are registered with Base
            import app.models.db_models
            await conn.run_sync(Base.metadata.create_all)
            
    asyncio.create_task(room_cleanup_task())
    asyncio.create_task(notification_dispatch_task())
    yield
    # Shutdown
    await engine.dispose()
    global_log.info("server_stopped")

app = FastAPI(
    title="Least Count Rush Backend",
    description="Real-time multiplayer backend for Least Count Rush card game",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
# Using allow_origin_regex to support Cloud Run URLs, EC2 IPs, and local development
# Cloud Run URLs follow pattern: https://SERVICE-REGION-HASH.run.app
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # Production domains
        "https://leastcountrush.online",
        "https://www.leastcountrush.online",
        "https://api.leastcountrush.online",
        # EC2 IP addresses (add your specific IP here)
        "http://54.234.122.15",
        "http://ec2-54-234-122-15.compute-1.amazonaws.com",
        "http://ec2-54-234-122-15.compute-1.amazonaws.com:8000",
        # Development/local (exact matches)
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://192.168.1.7:8000",
    ],
    # Regex pattern for:
    # - Cloud Run URLs: https://*.run.app
    # - Localhost with any port: http://localhost:*
    # - Local IPs with any port: http://127.0.0.1:* or http://192.168.*.*:*
    # - EC2 public IPs (no port or port 80): http://[0-9.]+ or http://[0-9.]+:80
    # - EC2 public IPs with any port: http://[0-9.]+:\d+
    allow_origin_regex=r".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(rooms.router)
app.include_router(deck.router)
app.include_router(games.router)
app.include_router(users.router)
app.include_router(user_game_stats.router)
app.include_router(leaderboard.router, prefix="/leaderboard", tags=["Leaderboard"])
app.include_router(messages.router)
app.include_router(admin.router)
app.include_router(admin.firebase_router)  # Firebase-auth protected admin routes
app.include_router(notifications.router)
app.include_router(ads.router)
app.include_router(ads.admin_router)
app.include_router(rate_us.router)

# WebSocket endpoints
# NOTE: /presence must be declared BEFORE /ws/{room_id} to avoid the
# wildcard path swallowing the literal "presence" segment.
@app.websocket("/presence")
async def websocket_presence(websocket: WebSocket, token: str = ""):
    """WebSocket endpoint for user presence tracking."""
    await presence_ws.presence_endpoint(websocket, token)

from fastapi import Depends, HTTPException

async def check_machine_routing(room_id: str):
    """Check if the room is hosted on another machine and replay if necessary."""
    room = await room_service.get_room(room_id)
    if room and room.host_machine_id:
        current_machine = os.environ.get("FLY_MACHINE_ID")
        if current_machine and room.host_machine_id != current_machine:
            global_log.info("fly_replay_triggered", {
                "room_id": room_id,
                "target_machine": room.host_machine_id,
                "current_machine": current_machine
            })
            raise HTTPException(
                status_code=409,
                headers={"fly-replay": f"instance={room.host_machine_id}"}
            )

@app.websocket("/ws/{room_id}", dependencies=[Depends(check_machine_routing)])
async def websocket_room(websocket: WebSocket, room_id: str, token: str = ""):
    """WebSocket endpoint for room connections"""
    await room_ws.websocket_endpoint(websocket, room_id, token)

# Mount frontend static files
# app.mount("/frontend", StaticFiles(directory="frontend", html=True), name="frontend")


async def room_cleanup_task():
    """Background task to clean up old rooms periodically"""
    while True:
        try:
            # Run cleanup every 30 minutes (1800 seconds)
            await asyncio.sleep(1800)
            await room_service.cleanup_old_rooms(max_age_hours=3.0)
            
            from app.database import AsyncSessionLocal
            from app.services.online_game_stats_service import online_game_stats_service
            async with AsyncSessionLocal() as db:
                await online_game_stats_service.cleanup_abandoned_games(db, max_age_hours=3.0)
                
        except Exception as e:
            global_log.error("room_cleanup_error", {"error": str(e)})


async def notification_dispatch_task():
    """Background task: dispatch scheduled/pending notifications every 60 seconds."""
    from app.database import AsyncSessionLocal
    from app.models.db_models import Notification, NotifStatusEnum
    from app.services import fcm_service
    from sqlalchemy import select, and_
    from datetime import datetime, timezone

    while True:
        try:
            await asyncio.sleep(60)
            async with AsyncSessionLocal() as db:
                now = datetime.now(timezone.utc)
                result = await db.execute(
                    select(Notification.notification_idn).where(
                        and_(
                            Notification.status == NotifStatusEnum.pending,
                            Notification.entity_active == True,
                            Notification.schedule_to.isnot(None),
                            Notification.schedule_to <= now,
                        )
                    )
                )
                pending_ids = [row[0] for row in result.all()]
                for notif_idn in pending_ids:
                    try:
                        await fcm_service.dispatch_notification(notif_idn, db)
                    except Exception as exc:
                        global_log.error(
                            "notification_dispatch_error",
                            {"notification_idn": notif_idn, "error": str(exc)},
                        )
        except Exception as e:
            global_log.error("notification_dispatch_task_error", {"error": str(e)})



# Startup event logic moved to lifespan



@app.get("/", response_class=HTMLResponse)
async def root():
    """Root endpoint"""
    return """
    <html>
        <head>
            <title>Least Count Rush Backend</title>
        </head>
        <body>
            <h1>Least Count Rush Backend API</h1>
            <p>The backend is running successfully. API documentation is available at <a href="/docs">/docs</a>.</p>
        </body>
    </html>
    """


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    from app.services.redis_client import BACKEND_NAME, USE_REDIS
    return {
        "status": "healthy",
        "service": "Least Count Rush Backend",
        "version": "1.0.0",
        "cache_backend": BACKEND_NAME,
        "redis_enabled": USE_REDIS,
    }


if __name__ == "__main__":
    import uvicorn
    # Cloud Run uses PORT environment variable, default to 8000 for local development
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level=_log_level.lower())
