import asyncio
from app.database import AsyncSessionLocal
from app.services.pg_game_stats_service import pg_game_stats_service

async def test_save():
    async with AsyncSessionLocal() as db:
        stats = [{
            "gameMode": "Single Game",
            "playedAt": 1718872000000,
            "numberOfPlayers": 4,
            "roundsPlayed": 5,
            "isWinner": True,
            "roundsSurvived": 5,
            "userPosition": 1,
            "playerScore": 10,
            "status": "completed",
            "durationSeconds": 120
        }]
        success = await pg_game_stats_service.save_game_stats_batch(db, 8, stats)
        print("Success:", success)

asyncio.run(test_save())
