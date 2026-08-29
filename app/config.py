"""
Configuration settings for the application
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings"""
    app_name: str = "Least Count Rush Backend"
    debug: bool = False
    host: str = "0.0.0.0"
    # Cloud Run uses PORT environment variable, default to 8080 for Cloud Run, 8000 for local
    port: int = int(os.getenv("PORT", 8000))
    database_url: str = ""
    firebase_project_id: str = ""
    admob_publisher_id: str = ""
    admob_client_id: str = ""
    admob_client_secret: str = ""
    admob_refresh_token: str = ""
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

# ── Turn Timer & Lives (configurable here) ──────────────────────────────────
# Seconds each player has to act on their turn before the server auto-plays.
TURN_TIMEOUT_SECONDS: int = 30
# Hearts every player starts each round with. 0 lives → eliminated.
PLAYER_LIVES: int = 3
# Delay (ms) between sequential animation events emitted by server_play_for_player.
# Matches the Flutter client's autoPlayMoveDelayMs so opponents see smooth animations.
BOT_PLAY_ANIMATION_DELAY_MS: int = 1000
