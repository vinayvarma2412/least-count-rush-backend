from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
import logging

from app.models.db_models import (
    Game,
    GamePlayer,
    UserLeaderboardStat,
    SeasonLeaderboardStat,
    LeaderboardSeason,
    GameModeEnum
)

logger = logging.getLogger(__name__)

# 1st-place base LP scales with lobby size (fewer players = harder to dominate → wait, actually
# fewer players = easier to win → less reward). Multiplier applied on top via PLAYER_COUNT_CONFIG.
POSITION_POINTS = {
    6: [30, 24, 18, 12, 8, 5],
    5: [28, 20, 13, 7,  4],
    4: [25, 17,  8, 4],
    3: [20, 12,  4],
    2: [15,  4],
    1: [5]  # fallback
}

# Option B: position points multiplied + flat participation bonus per lobby size.
PLAYER_COUNT_CONFIG = {
    6: {"multiplier": 1.5,  "flat": 12},
    5: {"multiplier": 1.35, "flat": 8},
    4: {"multiplier": 1.2,  "flat": 5},
    3: {"multiplier": 1.1,  "flat": 2},
    2: {"multiplier": 1.0,  "flat": 0},
    1: {"multiplier": 1.0,  "flat": 0},
}

class LeaderboardService:

    @staticmethod
    def calculate_lp(
        game_mode: GameModeEnum,
        score_limit: Optional[str],
        total_players: int,
        player_score: int,
        winner_score: int,
        rank_position: int
    ) -> Tuple[int, Dict[str, int]]:
        
        # 1. Participation
        participation = 10 if game_mode == GameModeEnum.tournament else 5
        
        # 2. Position
        pos_array = POSITION_POINTS.get(total_players, POSITION_POINTS[min(max(total_players, 1), 6)])
        pos_index = min(max(rank_position - 1, 0), len(pos_array) - 1)
        position = pos_array[pos_index]
        
        # 3. Tournament Bonus
        tournament_bonus = 0
        if game_mode == GameModeEnum.tournament and score_limit:
            try:
                limit = int(score_limit)
                if limit >= 400: tournament_bonus = 40
                elif limit >= 300: tournament_bonus = 30
                elif limit >= 200: tournament_bonus = 20
                elif limit >= 100: tournament_bonus = 10
                elif limit >= 50: tournament_bonus = 5
            except ValueError:
                pass
                
        # 4. Player Count Bonus (Option B: multiplier on position + flat bonus)
        pc_cfg = PLAYER_COUNT_CONFIG.get(total_players, PLAYER_COUNT_CONFIG[2])
        position = round(position * pc_cfg["multiplier"])  # scale position LP by lobby size
        player_count_bonus = pc_cfg["flat"]               # flat bonus on top
        
        # 5. Performance Bonus
        performance = 0
        diff = player_score - winner_score
        if diff <= 10: performance = 10
        elif diff <= 25: performance = 5
        elif diff <= 50: performance = 2
        
        total = participation + position + tournament_bonus + player_count_bonus + performance
        
        breakdown = {
            "participation": participation,
            "position": position,
            "tournament_bonus": tournament_bonus,
            "player_count_bonus": player_count_bonus,
            "player_count_multiplier": pc_cfg["multiplier"],
            "performance": performance
        }
        
        return total, breakdown

    async def update_leaderboard_for_game(self, db: AsyncSession, game_idn: int, exited_user_idns: set[int] = None, is_survival_win: bool = False):
        """
        Calculates LP and updates stats for a completed game.
        """
        if exited_user_idns is None:
            exited_user_idns = set()
            
        # Get game details
        result = await db.execute(select(Game).where(Game.game_idn == game_idn))
        game = result.scalar_one_or_none()
        
        if not game or game.result.name != "completed":
            return
            
        # Get all players
        players_result = await db.execute(select(GamePlayer).where(GamePlayer.game_idn == game_idn))
        players = list(players_result.scalars().all())
        
        if not players:
            return
            
        # Find winner score
        winner_score = 0
        for p in players:
            if p.rank_position == 1:
                winner_score = p.final_score
                break
                
        # Find active season
        season_result = await db.execute(
            select(LeaderboardSeason)
            .where(LeaderboardSeason.is_active == True)
            .order_by(LeaderboardSeason.season_idn.desc())
            .limit(1)
        )
        active_season = season_result.scalar_one_or_none()
        
        score_limit_int = 0
        if game.score_limit:
            try:
                score_limit_int = int(game.score_limit)
            except ValueError:
                pass
                
        for player in players:
            if player.user_idn is None:
                continue
                
            if player.user_idn in exited_user_idns:
                total_lp = 0
                lp_breakdown = {}
            elif is_survival_win:
                total_lp = 0
                lp_breakdown = {"survival_win": 0}
            else:
                total_lp, lp_breakdown = self.calculate_lp(
                    game_mode=game.game_mode,
                    score_limit=game.score_limit,
                    total_players=game.total_players,
                    player_score=player.final_score,
                    winner_score=winner_score,
                    rank_position=player.rank_position or 1
                )
            
            # Update GamePlayer
            player.lp_earned = total_lp
            player.lp_breakdown = lp_breakdown
            
            won = 1 if player.rank_position == 1 else 0
            top3 = 1 if player.rank_position and player.rank_position <= 3 else 0
            
            # Upsert Lifetime Stats
            await self._upsert_stat(
                db=db,
                model=UserLeaderboardStat,
                user_idn=player.user_idn,
                season_idn=None,
                total_lp=total_lp,
                won=won,
                top3=top3,
                rank_position=player.rank_position or 1,
                score_limit_int=score_limit_int
            )
            
            # Upsert Season Stats
            if active_season:
                await self._upsert_stat(
                    db=db,
                    model=SeasonLeaderboardStat,
                    user_idn=player.user_idn,
                    season_idn=active_season.season_idn,
                    total_lp=total_lp,
                    won=won,
                    top3=top3,
                    rank_position=player.rank_position or 1,
                    score_limit_int=score_limit_int
                )
                
        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(f"Error updating leaderboard for game {game_idn}: {e}")

    async def _upsert_stat(
        self, 
        db: AsyncSession, 
        model, 
        user_idn: int, 
        season_idn: Optional[int], 
        total_lp: int, 
        won: int, 
        top3: int, 
        rank_position: int,
        score_limit_int: int
    ):
        # Query existing
        stmt = select(model).where(model.user_idn == user_idn)
        if season_idn is not None:
            stmt = stmt.where(model.season_idn == season_idn)
            
        result = await db.execute(stmt)
        stat = result.scalar_one_or_none()
        
        if not stat:
            stat_dict = {
                "user_idn": user_idn,
                "total_points": total_lp,
                "games_played": 1,
                "games_won": won,
                "top_3_finishes": top3,
                "total_rank_sum": rank_position,
                "best_tournament_win_limit": score_limit_int if won else 0,
                "current_streak": 1 if won else 0,
                "longest_win_streak": 1 if won else 0
            }
            if season_idn is not None:
                stat_dict["season_idn"] = season_idn
                
            stat = model(**stat_dict)
            db.add(stat)
        else:
            stat.total_points += total_lp
            stat.games_played += 1
            stat.games_won += won
            stat.top_3_finishes += top3
            stat.total_rank_sum += rank_position
            
            if won:
                stat.current_streak += 1
                if stat.current_streak > stat.longest_win_streak:
                    stat.longest_win_streak = stat.current_streak
                if score_limit_int > stat.best_tournament_win_limit:
                    stat.best_tournament_win_limit = score_limit_int
            else:
                stat.current_streak = 0

leaderboard_service = LeaderboardService()
