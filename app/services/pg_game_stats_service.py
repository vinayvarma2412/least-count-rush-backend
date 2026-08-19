from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete
from sqlalchemy.orm import joinedload
from app.models.db_models import Game, GamePlayer, GameTypeEnum, GameModeEnum, GameResultEnum
from datetime import datetime, timezone

# Hardcoded bot user_idn mapping — matches the seeded bot rows (user_idn 1–5)
# and the botNames list in opponent.dart.
BOT_NAME_TO_USER_IDN: dict[str, int] = {
    "Zero Hero":    1,
    "Count Crush":  2,
    "Minimax":      3,
    "Sneaky Seven": 4,
    "Drop Master":  5,
}

class PgGameStatsService:
    async def save_game_stats_batch(self, db: AsyncSession, user_idn: int, stats_list: list[dict]) -> bool:
        """
        Saves a batch of game stats to the PostgreSQL database.
        Each item creates a Game and a corresponding GamePlayer record.
        """
        try:
            for stat in stats_list:
                # Map Flutter fields to PostgreSQL fields
                game_mode_str = stat.get("gameMode", "Single Game")
                if "Tournament" in game_mode_str:
                    game_mode = GameModeEnum.tournament
                else:
                    game_mode = GameModeEnum.single

                # Check if playedAt is provided, else use now
                played_at_ms = stat.get("playedAt")
                if played_at_ms:
                    started_at = datetime.fromtimestamp(played_at_ms / 1000.0, tz=timezone.utc)
                else:
                    started_at = datetime.now(timezone.utc)

                is_winner = stat.get("isWinner", False)
                rank = stat.get("userPosition")
                if is_winner and not rank:
                    rank = 1

                # Use hardcoded bot mapping (no DB query needed)
                bot_name_map = BOT_NAME_TO_USER_IDN
                
                # Parse opponent names from the stat
                opponent_names_str = stat.get("opponentNames", "") or ""
                opponent_names = [n.strip() for n in opponent_names_str.split(",") if n.strip()]
                
                # Determine the winner's user_idn
                winner_name = stat.get("winner", "")
                winner_user_idn = None
                if is_winner:
                    winner_user_idn = user_idn
                elif winner_name and winner_name in bot_name_map:
                    winner_user_idn = bot_name_map[winner_name]


                # Map status to GameResultEnum
                status_str = stat.get("status", "completed")
                game_result = GameResultEnum.completed
                if status_str == "cancelled":
                    game_result = GameResultEnum.cancelled

                client_game_id = stat.get("id")

                # Check if game already exists for idempotency
                existing_game = None
                if client_game_id:
                    result = await db.execute(
                        select(Game).where(Game.created_user_idn == user_idn, Game.client_game_id == str(client_game_id))
                    )
                    existing_game = result.scalar_one_or_none()

                if existing_game:
                    # Update existing game
                    existing_game.result = game_result
                    existing_game.total_rounds = stat.get("roundsPlayed", 0)
                    existing_game.winner_user_idn = winner_user_idn
                    existing_game.duration_seconds = stat.get("durationSeconds")
                    
                    # Update game player
                    result = await db.execute(
                        select(GamePlayer).where(GamePlayer.game_idn == existing_game.game_idn, GamePlayer.user_idn == user_idn)
                    )
                    existing_player = result.scalar_one_or_none()
                    if existing_player:
                        existing_player.final_score = stat.get("playerScore", 0)
                        existing_player.rank_position = rank
                        existing_player.rounds_survived = stat.get("roundsSurvived", 0)
                    continue

                # Create Game
                game = Game(
                    game_type=GameTypeEnum.offline,  # Bot games are offline
                    game_mode=game_mode,
                    result=game_result,
                    total_players=stat.get("numberOfPlayers", 1),
                    total_rounds=stat.get("roundsPlayed", 0),
                    created_user_idn=user_idn,
                    winner_user_idn=winner_user_idn,
                    started_at=started_at,
                    ended_at=started_at,  # Approximate
                    duration_seconds=stat.get("durationSeconds"),
                    score_limit=stat.get("scoreLimit", ""),
                    client_game_id=str(client_game_id) if client_game_id else None
                )
                db.add(game)
                await db.flush()  # To get game.game_idn

                # Create GamePlayer for the human user
                game_player = GamePlayer(
                    game_idn=game.game_idn,
                    user_idn=user_idn,
                    final_score=stat.get("playerScore", 0),
                    rank_position=rank,
                    rounds_survived=stat.get("roundsSurvived", 0)
                )
                db.add(game_player)


            await db.commit()
            return True
        except Exception as e:
            await db.rollback()
            from app.utils.room_logger import global_log
            global_log.error("pg_save_game_stats_error", {"error": str(e)})
            return False

    async def _get_bot_name_map(self, db: AsyncSession) -> dict[str, int]:
        pass  # Replaced by BOT_NAME_TO_USER_IDN constant above

    async def get_game_stats_for_user(self, db: AsyncSession, user_idn: int, limit: int = 10, offset: int = 0, game_type: GameTypeEnum | None = None, year: int | None = None) -> list[dict]:
        """
        Retrieves paginated game stats for a user and maps them back to the format
        expected by the Flutter client.
        """
        from sqlalchemy import extract
        query = (
            select(Game, GamePlayer)
            .join(GamePlayer, Game.game_idn == GamePlayer.game_idn)
            .where(GamePlayer.user_idn == user_idn)
        )
        
        if game_type:
            query = query.where(Game.game_type == game_type)
        if year:
            query = query.where(extract('year', Game.started_at) == year)
            
        result = await db.execute(
            query
            .options(joinedload(Game.winner), joinedload(Game.players).joinedload(GamePlayer.user))
            .order_by(Game.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        
        records = result.unique().all()
        stats_list = []
        
        for game, game_player in records:
            started_at_ms = int(game.started_at.timestamp() * 1000) if game.started_at else 0
            
            # Reconstruct opponentNames (only humans will be here since we don't save bots)
            opponent_names_list = []
            for gp in game.players:
                if gp.user_idn != user_idn and gp.user:
                    opponent_names_list.append(gp.user.display_name or gp.user.user_name or f"User {gp.user_idn}")
            opponent_names_str = ", ".join(opponent_names_list) if opponent_names_list else None

            stat_map = {
                "id": str(game.game_idn),
                "gameMode": "Tournament" if game.game_mode == GameModeEnum.tournament else "Single Game",
                "numberOfPlayers": game.total_players,
                "scoreLimit": game.score_limit or "",
                "playedAt": started_at_ms,
                "playerScore": game_player.final_score,
                "roundsPlayed": game.total_rounds,
                "isWinner": game.winner_user_idn == user_idn or game_player.rank_position == 1,
                "roundsSurvived": game_player.rounds_survived or 0,
                "isSynced": True,
                "userPosition": game_player.rank_position,
                "winner": game.winner.display_name if game.winner else None,
                "syncedAt": started_at_ms,  # approximate
                "status": game.result.name if game.result else "completed",
                "durationSeconds": game.duration_seconds,
                "lpEarned": game_player.lp_earned,
                "lpBreakdown": game_player.lp_breakdown
            }
            stats_list.append(stat_map)
            
        return stats_list

    async def get_game_stats_summary_for_user(self, db: AsyncSession, user_idn: int, game_type: GameTypeEnum | None = None, year: int | None = None) -> dict:
        """
        Calculates summary statistics for a user from Postgres.
        """
        from sqlalchemy import func, extract
        
        # Base query to get games user played
        base_query = select(Game, GamePlayer).join(GamePlayer, Game.game_idn == GamePlayer.game_idn).where(GamePlayer.user_idn == user_idn)
        if game_type:
            base_query = base_query.where(Game.game_type == game_type)
        if year:
            base_query = base_query.where(extract('year', Game.started_at) == year)
        
        result = await db.execute(base_query)
        records = result.all()
        
        total_games = len(records)
        total_wins = 0
        tournament_games = 0
        single_games = 0
        
        for game, game_player in records:
            # Check win condition
            if game.winner_user_idn == user_idn or game_player.rank_position == 1:
                total_wins += 1
                
            if game.game_mode == GameModeEnum.tournament:
                tournament_games += 1
            else:
                single_games += 1
                
        win_rate = (total_wins / total_games * 100) if total_games > 0 else 0.0
        
        return {
            "totalGames": total_games,
            "totalWins": total_wins,
            "winRate": win_rate,
            "tournamentGames": tournament_games,
            "singleGames": single_games,
        }

    async def delete_game_stats_for_user(self, db: AsyncSession, user_idn: int) -> bool:
        """
        Deletes all games where the user is the creator (or just the game_player records).
        """
        try:
            # Delete GamePlayer records
            await db.execute(delete(GamePlayer).where(GamePlayer.user_idn == user_idn))
            # Delete Games created by user
            await db.execute(delete(Game).where(Game.created_user_idn == user_idn))
            await db.commit()
            return True
        except Exception as e:
            await db.rollback()
            from app.utils.room_logger import global_log
            global_log.error("pg_delete_game_stats_error", {"error": str(e)})
            return False

    async def get_achievements_for_user(self, db: AsyncSession, user_idn: int, game_type: GameTypeEnum | None = None, year: int | None = None) -> dict[str, int]:
        """
        Calculates achievement progress counters from Postgres.
        Returns a dict mapping achievement_type (str) to current_progress (int).
        """
        from sqlalchemy import extract
        
        query = select(Game, GamePlayer).join(GamePlayer, Game.game_idn == GamePlayer.game_idn).where(GamePlayer.user_idn == user_idn)
        if game_type:
            query = query.where(Game.game_type == game_type)
        if year:
            query = query.where(extract('year', Game.started_at) == year)
            
        result = await db.execute(query.order_by(Game.started_at.desc()))
        records = result.all()
        
        games_played = len(records)
        games_won = 0
        tournaments_won = 0
        singles_won = 0
        perfect_wins = 0
        perfect_tournaments_won = 0
        perfect_singles_won = 0
        
        # Streak tracking
        max_streak = 0
        current_streak = 0
        
        # Player counts and positions tracking
        unique_player_counts = set()
        unique_tournament_player_counts = set()
        unique_single_player_counts = set()
        unique_tournament_positions = set()
        
        for game, game_player in records:
            is_winner = game.winner_user_idn == user_idn or game_player.rank_position == 1
            
            # Count wins
            if is_winner:
                games_won += 1
                
                if game.game_mode == GameModeEnum.tournament:
                    tournaments_won += 1
                else:
                    singles_won += 1
                    
                # Perfect wins
                if game_player.final_score == 0:
                    perfect_wins += 1
                    if game.game_mode == GameModeEnum.tournament:
                        perfect_tournaments_won += 1
                    else:
                        perfect_singles_won += 1
            
            # Since records are sorted descending (newest first), streak logic differs slightly if tracking the max streak overall.
            # Wait, the records are sorted by desc. Max streak is historical. If they won, streak increments.
            # No, if it's descending, consecutive wins from past to present are reversed.
            # So if is_winner is true, current_streak++, if false current_streak = 0.
            # The max streak is the same regardless of direction.
            if is_winner:
                current_streak += 1
                if current_streak > max_streak:
                    max_streak = current_streak
            else:
                current_streak = 0
                
            unique_player_counts.add(game.total_players)
            if game.game_mode == GameModeEnum.tournament:
                unique_tournament_player_counts.add(game.total_players)
                if game_player.rank_position:
                    unique_tournament_positions.add(game_player.rank_position)
            else:
                unique_single_player_counts.add(game.total_players)
                
        return {
            "gamesPlayed": games_played,
            "gamesWon": games_won,
            "tournamentsWon": tournaments_won,
            "singlesWon": singles_won,
            "perfectWins": perfect_wins,
            "perfectTournamentsWon": perfect_tournaments_won,
            "perfectSinglesWon": perfect_singles_won,
            "winningStrike": max_streak,
            "numberOfPlayers": len(unique_player_counts),
            "numberOfPlayersTournament": len(unique_tournament_player_counts),
            "numberOfPlayersSingle": len(unique_single_player_counts),
            "tournamentPosition": len(unique_tournament_positions),
            "general": 0
        }

pg_game_stats_service = PgGameStatsService()
