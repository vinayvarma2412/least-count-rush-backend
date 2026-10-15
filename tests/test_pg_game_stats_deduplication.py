import pytest
import pytest_asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from app.database import Base
from app.models.db_models import User, Game, GamePlayer, GameTypeEnum, GameModeEnum, GameResultEnum
from app.services.pg_game_stats_service import PgGameStatsService

@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        # Create a test user
        user = User(
            user_idn=100,
            user_id="test_user_100",
            user_name="test_user",
            display_name="Test User",
            user_type="authenticated"
        )
        session.add(user)
        await session.commit()

    async with async_session() as session:
        yield session

    await engine.dispose()

@pytest.mark.asyncio
async def test_skip_in_progress_offline_stats(async_db):
    service = PgGameStatsService()
    user_idn = 100

    stats = [
        {
            "id": "stat_in_progress_1",
            "gameMode": "Single Game",
            "status": "in_progress",
            "playedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
            "numberOfPlayers": 2,
            "roundsPlayed": 0,
            "playerScore": 0,
            "isWinner": False,
        }
    ]

    success = await service.save_game_stats_batch(async_db, user_idn, stats)
    assert success is True

    # Check that no game was created in DB
    result = await async_db.execute(select(Game).where(Game.created_user_idn == user_idn))
    games = result.scalars().all()
    assert len(games) == 0

@pytest.mark.asyncio
async def test_idempotent_update_with_client_game_id(async_db):
    service = PgGameStatsService()
    user_idn = 100
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # First save
    stats1 = [
        {
            "id": "game_client_123",
            "gameMode": "Single Game",
            "status": "completed",
            "playedAt": now_ms,
            "numberOfPlayers": 2,
            "roundsPlayed": 1,
            "playerScore": 25,
            "isWinner": False,
            "userPosition": 2,
        }
    ]
    await service.save_game_stats_batch(async_db, user_idn, stats1)

    result = await async_db.execute(select(Game).where(Game.created_user_idn == user_idn))
    games = result.scalars().all()
    assert len(games) == 1
    assert games[0].client_game_id == "game_client_123"
    assert games[0].total_rounds == 1

    # Second save with same client_game_id (update)
    stats2 = [
        {
            "id": "game_client_123",
            "gameMode": "Single Game",
            "status": "completed",
            "playedAt": now_ms,
            "numberOfPlayers": 2,
            "roundsPlayed": 3,
            "playerScore": 5,
            "isWinner": True,
            "userPosition": 1,
        }
    ]
    await service.save_game_stats_batch(async_db, user_idn, stats2)

    result = await async_db.execute(select(Game).where(Game.created_user_idn == user_idn))
    games = result.scalars().all()
    # Must NOT create a second game
    assert len(games) == 1
    assert games[0].total_rounds == 3
    assert games[0].winner_user_idn == user_idn

    # Check GamePlayer was also updated
    result = await async_db.execute(select(GamePlayer).where(GamePlayer.game_idn == games[0].game_idn))
    players = result.scalars().all()
    assert len(players) == 1
    assert players[0].final_score == 5
    assert players[0].rank_position == 1

@pytest.mark.asyncio
async def test_fallback_deduplication_without_client_game_id(async_db):
    service = PgGameStatsService()
    user_idn = 100
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # First save without ID
    stats1 = [
        {
            "id": None,
            "gameMode": "Single Game",
            "status": "completed",
            "playedAt": now_ms,
            "numberOfPlayers": 2,
            "roundsPlayed": 1,
            "playerScore": 20,
            "isWinner": False,
        }
    ]
    await service.save_game_stats_batch(async_db, user_idn, stats1)

    result = await async_db.execute(select(Game).where(Game.created_user_idn == user_idn))
    games = result.scalars().all()
    assert len(games) == 1

    # Duplicate sync attempt 3 seconds later without ID
    stats2 = [
        {
            "id": None,
            "gameMode": "Single Game",
            "status": "completed",
            "playedAt": now_ms + 3000,
            "numberOfPlayers": 2,
            "roundsPlayed": 2,
            "playerScore": 10,
            "isWinner": True,
        }
    ]
    await service.save_game_stats_batch(async_db, user_idn, stats2)

    result = await async_db.execute(select(Game).where(Game.created_user_idn == user_idn))
    games = result.scalars().all()
    # Must be deduplicated into the same game
    assert len(games) == 1
    assert games[0].total_rounds == 2

@pytest.mark.asyncio
async def test_distinct_games_not_falsely_deduplicated(async_db):
    service = PgGameStatsService()
    user_idn = 100
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Game 1: 5 minutes ago
    stats1 = [
        {
            "id": "game_1",
            "gameMode": "Single Game",
            "status": "completed",
            "playedAt": now_ms - 300000,
            "numberOfPlayers": 2,
            "roundsPlayed": 1,
            "playerScore": 20,
            "isWinner": False,
        }
    ]
    # Game 2: now
    stats2 = [
        {
            "id": "game_2",
            "gameMode": "Single Game",
            "status": "completed",
            "playedAt": now_ms,
            "numberOfPlayers": 2,
            "roundsPlayed": 1,
            "playerScore": 15,
            "isWinner": True,
        }
    ]
    await service.save_game_stats_batch(async_db, user_idn, stats1)
    await service.save_game_stats_batch(async_db, user_idn, stats2)

    result = await async_db.execute(select(Game).where(Game.created_user_idn == user_idn))
    games = result.scalars().all()
    assert len(games) == 2
