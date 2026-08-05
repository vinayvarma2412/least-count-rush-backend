import asyncio
from app.database import AsyncSessionLocal
from app.services.pg_game_stats_service import pg_game_stats_service

async def test():
    async with AsyncSessionLocal() as db:
        try:
            stat = {
                "id": "test_id",
                "gameMode": "Single Game",
                "numberOfPlayers": 2,
                "scoreLimit": "250",
                "playedAt": 1717650000000,
                "playerScore": 50,
                "roundsPlayed": 2,
                "isWinner": True,
                "opponentNames": "",
                "roundsSurvived": 2,
                "userPosition": 1,
                "winner": "Zero Hero",
                "status": "completed",
                "durationSeconds": 60
            }
            success = await pg_game_stats_service.save_game_stats_batch(db, 6, [stat])
            print(f"Success: {success}")
        except Exception as e:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
