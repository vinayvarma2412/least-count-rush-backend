"""
Configuration settings for the application
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings"""
    app_name: str = "Least Count Rush Backend"
    debug: bool = False
    develop_mode: bool = False
    host: str = "0.0.0.0"
    # Cloud Run uses PORT environment variable, default to 8080 for Cloud Run, 8000 for local
    port: int = int(os.getenv("PORT", 8000))
    database_url: str = ""
    firebase_project_id: str = ""
    admob_publisher_id: str = ""
    admob_client_id: str = ""
    admob_client_secret: str = ""
    admob_refresh_token: str = ""
    iap_google_package_name: str = "com.mrunknown.least_count_game"
    iap_google_service_account_json: str = ""
    iap_google_service_account_file: str = ""
    iap_apple_bundle_id: str = "com.mrunknown.leastCountGame"
    iap_apple_issuer_id: str = ""
    iap_apple_key_id: str = ""
    iap_apple_private_key: str = ""
    iap_apple_private_key_file: str = ""
    iap_apple_shared_secret: str = ""
    bot_server_url: str = ""
    bot_server_secret: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

# ── Turn Timer & Lives (configurable here) ──────────────────────────────────
# Seconds each player has to act on their turn before the server auto-plays.
TURN_TIMEOUT_SECONDS: int = 30
# Base wait before the first bot joins a public room. The second bot waits
# twice this long, and the third waits three times this long.
PUBLIC_ROOM_BOT_WAIT_SECONDS: int = 15
# Once a public lobby has two connected players, each player who has not
# tapped Ready is removed after this many seconds.
PUBLIC_READY_TIMEOUT_SECONDS: int = 15
# After leaving a public lobby, matchmaking will not put that player back
# into the same room for this long.
PUBLIC_MATCHMAKE_EXCLUDE_SECONDS: int = 180
# One extra attempt if that request fails, this many seconds later.
BOT_FILL_RETRY_SECONDS: int = 15
# Hearts every player starts each round with. 0 lives → eliminated.
PLAYER_LIVES: int = 3
# Delay (ms) between sequential animation events emitted by server_play_for_player.
# Matches the Flutter client's autoPlayMoveDelayMs so opponents see smooth animations.
BOT_PLAY_ANIMATION_DELAY_MS: int = 1000
