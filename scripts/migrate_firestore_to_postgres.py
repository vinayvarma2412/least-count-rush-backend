import os
import json
import asyncio
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, firestore
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models.db_models import User, Game, GamePlayer, GameTypeEnum, GameModeEnum, GameResultEnum

# Requires GOOGLE_APPLICATION_CREDENTIALS and DATABASE_URL in env
# e.g., run with: python -m scripts.migrate_firestore_to_postgres

async def run_migration():
    print("Starting Firestore to PostgreSQL migration...")
    
    # 1. Init Firebase Admin
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    db_fs = firestore.client()
    
    # 2. Init PostgreSQL
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set!")
        return
        
    engine = create_async_engine(db_url, echo=False)
    SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    
    # Track stats
    stats = {"users_migrated": 0, "games_migrated": 0, "errors": 0}
    
    async with SessionLocal() as session:
        # Fetch all users from Firestore
        users_ref = db_fs.collection("users")
        docs = users_ref.stream()
        
        for doc in docs:
            user_data = doc.to_dict()
            uid = doc.id
            
            try:
                # Check if user exists in Postgres
                from sqlalchemy import select
                result = await session.execute(select(User).where(User.user_id == uid))
                pg_user = result.scalar_one_or_none()
                
                if not pg_user:
                    # Create user
                    pg_user = User(
                        user_id=uid,
                        user_name=user_data.get("username", f"user_{uid[:8]}"),
                        display_name=user_data.get("displayName", ""),
                        avatar_seed=user_data.get("avatarSeed", ""),
                        crt_dt=user_data.get("createdAt", datetime.now(timezone.utc)),
                        user_type="authenticated"
                    )
                    session.add(pg_user)
                    await session.flush()
                    stats["users_migrated"] += 1
                
                # Fetch game stats for this user
                game_stats_ref = users_ref.document(uid).collection("game_stats")
                game_stats_docs = game_stats_ref.stream()
                
                for stat_doc in game_stats_docs:
                    stat = stat_doc.to_dict()
                    
                    # Convert to our Postgres models
                    game_mode_str = stat.get("gameMode", "Single Game")
                    game_mode = GameModeEnum.tournament if "Tournament" in game_mode_str else GameModeEnum.single
                    
                    started_at = datetime.fromtimestamp(stat.get("playedAt", 0) / 1000.0, tz=timezone.utc)
                    is_winner = stat.get("isWinner", False)
                    rank = stat.get("userPosition", 1 if is_winner else 2)
                    
                    # Create Game
                    game = Game(
                        game_type=GameTypeEnum.online,
                        game_mode=game_mode,
                        result=GameResultEnum.completed,
                        total_players=stat.get("numberOfPlayers", 1),
                        total_rounds=stat.get("roundsPlayed", 0),
                        created_user_idn=pg_user.user_idn,
                        winner_user_idn=pg_user.user_idn if is_winner else None,
                        started_at=started_at,
                        game_settings={"scoreLimit": stat.get("scoreLimit", "")}
                    )
                    session.add(game)
                    await session.flush()
                    
                    # Create GamePlayer
                    game_player = GamePlayer(
                        game_idn=game.game_idn,
                        user_idn=pg_user.user_idn,
                        final_score=stat.get("playerScore", 0),
                        rank_position=rank
                    )
                    session.add(game_player)
                    stats["games_migrated"] += 1
                
                await session.commit()
                print(f"Migrated user {uid}")
                
            except Exception as e:
                print(f"Error migrating user {uid}: {str(e)}")
                await session.rollback()
                stats["errors"] += 1

    await engine.dispose()
    print("Migration complete!")
    print(json.dumps(stats, indent=2))

if __name__ == "__main__":
    asyncio.run(run_migration())
