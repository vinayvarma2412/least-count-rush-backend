"""
Service for persisting online (multiplayer WebSocket) game lifecycle events
to the `games` and `game_players` database tables.

Lifecycle:
  1. create_online_game()   — called when a game_start is broadcast
  2. increment_round_count() — called at the START of each new tournament round
  3. finalize_online_game()  — called when the game/tournament is fully over

All DB calls are wrapped in try/except so a database failure never crashes
the live game.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.db_models import (
    Game,
    GamePlayer,
    GameModeEnum,
    GameResultEnum,
    GameTypeEnum,
)
from app.utils.room_logger import global_log
from app.services.leaderboard_service import leaderboard_service


class OnlineGameStatsService:
    # ------------------------------------------------------------------
    # 1. Game creation — called at game_start
    # ------------------------------------------------------------------

    async def create_online_game(
        self,
        db: AsyncSession,
        *,
        game_mode: str,           # "Single Game" or "Tournament"
        total_players: int,
        score_limit: Optional[int],
        created_user_idn: Optional[int],
        player_user_idns: list[tuple[int, int]],  # [(user_idn, seat_no), ...]
    ) -> Optional[int]:
        """
        INSERT a new `games` row and one `game_players` row per player.

        Returns the new `game_idn`, or None on error.
        Players whose `user_idn` is None (unknown / guest) are skipped.
        """
        try:
            mode = (
                GameModeEnum.tournament
                if "Tournament" in game_mode
                else GameModeEnum.single
            )

            game = Game(
                game_type=GameTypeEnum.online,
                game_mode=mode,
                result=GameResultEnum.in_progress,   # updated at end
                total_players=total_players,
                total_rounds=1,                    # starts at 1, incremented per round
                created_user_idn=created_user_idn,
                winner_user_idn=None,
                score_limit=str(score_limit) if score_limit is not None else None,
                started_at=datetime.now(timezone.utc),
                ended_at=None,
                duration_seconds=None,
            )
            db.add(game)
            await db.flush()  # populate game.game_idn before inserting players

            for user_idn, seat_no in player_user_idns:
                if user_idn is None:
                    continue
                gp = GamePlayer(
                    game_idn=game.game_idn,
                    user_idn=user_idn,
                    seat_no=seat_no,
                    final_score=0,
                    rank_position=None,
                    rounds_survived=0,
                )
                db.add(gp)

            await db.commit()
            global_log.info(
                "online_game_created",
                {
                    "game_idn": game.game_idn,
                    "game_mode": mode.value,
                    "total_players": total_players,
                },
            )
            return game.game_idn

        except Exception as exc:
            await db.rollback()
            global_log.error("online_game_create_error", {"error": str(exc)})
            return None

    # ------------------------------------------------------------------
    # 2. Round counter — called at the START of every new tournament round
    # ------------------------------------------------------------------

    async def increment_round_count(
        self,
        db: AsyncSession,
        game_idn: int,
    ) -> bool:
        """
        Atomically increment `total_rounds` by 1 for the given game row.
        Returns True on success, False on error.
        """
        try:
            from sqlalchemy import text

            await db.execute(
                text(
                    "UPDATE games SET total_rounds = total_rounds + 1 "
                    "WHERE game_idn = :gid"
                ),
                {"gid": game_idn},
            )
            await db.commit()
            global_log.info(
                "online_game_round_incremented", {"game_idn": game_idn}
            )
            return True

        except Exception as exc:
            await db.rollback()
            global_log.error(
                "online_game_round_increment_error",
                {"game_idn": game_idn, "error": str(exc)},
            )
            return False

    # ------------------------------------------------------------------
    # 3. Finalization — called when the game / tournament ends
    # ------------------------------------------------------------------

    async def finalize_online_game(
        self,
        db: AsyncSession,
        *,
        game_idn: int,
        started_at: datetime,
        result: str = "completed",          # "completed" | "cancelled"
        winner_user_idn: Optional[int],
        player_results: list[dict],
        is_survival_win: bool = False,
        # Each dict: {user_idn, final_score, rank_position, rounds_survived}
    ) -> bool:
        """
        UPDATE the `games` row with end-time / result / winner,
        and UPDATE each `game_players` row with final score, rank, rounds_survived.

        Returns True on success, False on error.
        """
        try:
            ended_at = datetime.now(timezone.utc)
            duration_seconds = int((ended_at - started_at).total_seconds())

            game_result = (
                GameResultEnum.cancelled
                if result == "cancelled"
                else GameResultEnum.completed
            )

            await db.execute(
                update(Game)
                .where(Game.game_idn == game_idn)
                .values(
                    ended_at=ended_at,
                    duration_seconds=duration_seconds,
                    winner_user_idn=winner_user_idn,
                    result=game_result,
                )
            )

            exited_user_idns = set()
            for pr in player_results:
                u_idn = pr.get("user_idn")
                if u_idn is None:
                    continue
                if pr.get("is_exited"):
                    exited_user_idns.add(u_idn)
                    
                await db.execute(
                    update(GamePlayer)
                    .where(
                        GamePlayer.game_idn == game_idn,
                        GamePlayer.user_idn == u_idn,
                    )
                    .values(
                        final_score=pr.get("final_score", 0),
                        rank_position=pr.get("rank_position"),
                        rounds_survived=pr.get("rounds_survived", 0),
                    )
                )

            await db.commit()
            global_log.info(
                "online_game_finalized",
                {
                    "game_idn": game_idn,
                    "winner_user_idn": winner_user_idn,
                    "duration_seconds": duration_seconds,
                    "result": game_result.value,
                },
            )
            
            # Calculate and save Leaderboard Points
            if game_result == GameResultEnum.completed:
                try:
                    await leaderboard_service.update_leaderboard_for_game(
                        db, 
                        game_idn, 
                        exited_user_idns=exited_user_idns,
                        is_survival_win=is_survival_win
                    )
                except Exception as lb_exc:
                    global_log.error("leaderboard_update_error", {"game_idn": game_idn, "error": str(lb_exc)})

                # Increment Rate Us win counter for the winner (non-fatal)
                if winner_user_idn is not None:
                    try:
                        from app.services.rate_us_service import increment_wins_since_dismissed
                        await increment_wins_since_dismissed(winner_user_idn, db)
                    except Exception as ru_exc:
                        global_log.error("rate_us_increment_error", {"game_idn": game_idn, "error": str(ru_exc)})
                    
            return True

        except Exception as exc:
            await db.rollback()
            global_log.error(
                "online_game_finalize_error",
                {"game_idn": game_idn, "error": str(exc)},
            )
            return False

    # ------------------------------------------------------------------
    # Helper — bulk-resolve Firebase UIDs → user_idn integers
    # ------------------------------------------------------------------

    async def resolve_user_idns(
        self,
        db: AsyncSession,
        firebase_uids: list[str],
    ) -> dict[str, Optional[int]]:
        """
        Given a list of Firebase UIDs (player_ids), return a mapping
        { firebase_uid: user_idn | None }.

        Unknown / non-existent UIDs map to None and will be skipped.
        """
        from app.models.db_models import User

        if not firebase_uids:
            return {}

        result = await db.execute(
            select(User.user_id, User.user_idn).where(
                User.user_id.in_(firebase_uids)
            )
        )
        rows = result.all()
        mapping: dict[str, Optional[int]] = {uid: None for uid in firebase_uids}
        for uid, idn in rows:
            mapping[uid] = idn
        return mapping

    # ------------------------------------------------------------------
    # 4. Cleanup abandoned games — called by background task
    # ------------------------------------------------------------------

    async def cleanup_abandoned_games(
        self,
        db: AsyncSession,
        max_age_hours: float = 1.0
    ) -> int:
        """
        Mark any 'in_progress' games older than max_age_hours as 'cancelled'.
        Returns the number of games marked as cancelled.
        """
        try:
            from datetime import timedelta
            
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

            result = await db.execute(
                update(Game)
                .where(
                    Game.result == GameResultEnum.in_progress,
                    Game.started_at < cutoff_time
                )
                .values(
                    result=GameResultEnum.cancelled,
                    ended_at=datetime.now(timezone.utc),
                )
            )
            await db.commit()
            
            cleaned_count = result.rowcount
            if cleaned_count > 0:
                global_log.info("abandoned_games_cleaned", {"count": cleaned_count})
                
            return cleaned_count

        except Exception as exc:
            await db.rollback()
            global_log.error("cleanup_abandoned_games_error", {"error": str(exc)})
            return 0


online_game_stats_service = OnlineGameStatsService()
