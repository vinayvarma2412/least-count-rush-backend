"""
Script to preview and remove duplicate offline game records in PostgreSQL.

Usage:
  python -m scripts.deduplicate_offline_games --dry-run
  python -m scripts.deduplicate_offline_games --apply
"""

import os
import sys
import asyncio
from datetime import timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy import delete
from app.models.db_models import Game, GamePlayer, GameTypeEnum, GameResultEnum

async def run_deduplication(apply_changes: bool = False):
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL environment variable is not set!")
        sys.exit(1)

    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    print(f"--- Offline Game Deduplication {'[APPLY MODE]' if apply_changes else '[DRY RUN MODE]'} ---")

    async with async_session() as session:
        # Fetch all offline games ordered by user and start time
        result = await session.execute(
            select(Game)
            .where(Game.game_type == GameTypeEnum.offline)
            .order_by(Game.created_user_idn, Game.started_at.asc(), Game.game_idn.asc())
        )
        games = result.scalars().all()

        print(f"Total offline games found: {len(games)}")

        # Group duplicate candidates by user
        user_games: dict[int, list[Game]] = {}
        for g in games:
            if g.created_user_idn:
                user_games.setdefault(g.created_user_idn, []).append(g)

        games_to_delete: list[int] = []

        for user_idn, user_game_list in user_games.items():
            # Check for duplicates
            n = len(user_game_list)
            visited = set()

            for i in range(n):
                if i in visited:
                    continue
                cluster = [user_game_list[i]]
                visited.add(i)

                for j in range(i + 1, n):
                    if j in visited:
                        continue
                    g1 = user_game_list[i]
                    g2 = user_game_list[j]

                    # Match condition:
                    # 1. Matching non-empty client_game_id
                    # 2. OR same game_mode and started_at within 15 seconds
                    is_match = False
                    if g1.client_game_id and g2.client_game_id and g1.client_game_id == g2.client_game_id:
                        is_match = True
                    elif g1.game_mode == g2.game_mode and g1.started_at and g2.started_at:
                        diff = abs((g1.started_at - g2.started_at).total_seconds())
                        if diff <= 15:
                            is_match = True

                    if is_match:
                        cluster.append(g2)
                        visited.add(j)

                if len(cluster) > 1:
                    # Sort cluster by quality:
                    # Prefer completed over cancelled/in_progress,
                    # then prefer higher total_rounds,
                    # then prefer non-null duration_seconds,
                    # then prefer highest game_idn
                    def score_game(g: Game):
                        result_score = 2 if g.result == GameResultEnum.completed else (1 if g.result == GameResultEnum.cancelled else 0)
                        rounds = g.total_rounds or 0
                        has_duration = 1 if g.duration_seconds is not None else 0
                        has_client_id = 1 if g.client_game_id is not None else 0
                        return (result_score, rounds, has_duration, has_client_id, g.game_idn)

                    cluster.sort(key=score_game, reverse=True)
                    winner = cluster[0]
                    duplicates = cluster[1:]

                    print(f"\nUser {user_idn}: Keeping game_idn={winner.game_idn} (mode={winner.game_mode.value}, result={winner.result.value}, rounds={winner.total_rounds}, client_id={winner.client_game_id})")
                    for dup in duplicates:
                        print(f"  -> Duplicate to remove: game_idn={dup.game_idn} (mode={dup.game_mode.value}, result={dup.result.value}, rounds={dup.total_rounds}, client_id={dup.client_game_id})")
                        games_to_delete.append(dup.game_idn)

        print(f"\nTotal duplicate games identified: {len(games_to_delete)}")

        if games_to_delete and apply_changes:
            print("Applying deletion...")
            for gid in games_to_delete:
                # GamePlayer cascades or delete explicitly
                await session.execute(delete(GamePlayer).where(GamePlayer.game_idn == gid))
                await session.execute(delete(Game).where(Game.game_idn == gid))
            await session.commit()
            print("Successfully deleted duplicates.")
        elif games_to_delete:
            print("Dry run complete. No changes were made. Use --apply to execute deletion.")
        else:
            print("No duplicates found!")

if __name__ == "__main__":
    apply = "--apply" in sys.argv
    asyncio.run(run_deduplication(apply_changes=apply))
