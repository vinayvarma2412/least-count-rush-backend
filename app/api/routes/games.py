"""
Game management REST API endpoints
"""
from fastapi import APIRouter, HTTPException
from app.services.game_service import game_service
from app.services.room_service import room_service
from app.utils.room_logger import get_room_logger
from app.schemas.game import (
    GameState,
    ShowRequest,
    ShowResponse,
    TournamentScoresResponse,
    TournamentRoundScores,
)
from app.database import AsyncSessionLocal
from app.services.online_game_stats_service import online_game_stats_service
from fastapi import Depends
from app.api.dependencies import get_current_firebase_user

router = APIRouter(prefix="/api/games", tags=["games"])

# In-memory tournament scores per room (for online scoreboard)
_tournament_scores: dict[str, dict] = {}


def clear_tournament_scores(room_id: str):
    """Clear tournament scores and online game tracking for a given room"""
    _tournament_scores.pop(room_id, None)
    try:
        from app.api.websocket.game_ws import _online_game_ids, _online_game_started_at
        _online_game_ids.pop(room_id, None)
        _online_game_started_at.pop(room_id, None)
    except Exception:
        pass


async def _get_exited_or_eliminated_player_ids(room_id: str, room) -> set[str]:
    """Players excluded from active gameplay (exited OR eliminated)."""
    excluded = set(_tournament_scores.get(room_id, {}).get("eliminated", []))
    if room:
        excluded.update({p.player_id for p in room.players if getattr(p, "is_exited", False)})
        gs = await game_service.get_game_state(room_id)
        if gs:
            for idx in (gs.eliminated_indices or []):
                if 0 <= idx < len(room.players):
                    excluded.add(room.players[idx].player_id)
    return excluded


@router.get("/{room_id}/state", response_model=GameState)
async def get_game_state(room_id: str, user: dict = Depends(get_current_firebase_user)):
    """Get current game state for a room"""
    room = await room_service.get_room(room_id)
    if not room:
        room = await room_service.get_room_by_code(room_id.upper())
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        room_id = room.room_id

    game_state = await game_service.get_game_state(room_id)
    if not game_state:
        raise HTTPException(status_code=404, detail="Game not found or not started")

    return game_state


@router.get("/{room_id}/public-state")
async def get_public_game_state(room_id: str, user: dict = Depends(get_current_firebase_user)):
    """Get public game state (without private hands)"""
    room = await room_service.get_room(room_id)
    if not room:
        room = await room_service.get_room_by_code(room_id.upper())
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        room_id = room.room_id

    public_state = await game_service.get_public_game_state(room_id)
    if not public_state:
        raise HTTPException(status_code=404, detail="Game not found or not started")

    return public_state


async def process_and_broadcast_show_results(room_id: str, showed_by_id: str, results: list, is_survival_win: bool = False, is_bot_play: bool = False):
    log = get_room_logger(room_id)
    # Record this round's scores for tournament scoreboard and detect eliminations
    tournament_meta = None
    try:
        room_for_scores = await room_service.get_room(room_id)
        if room_for_scores and getattr(room_for_scores, "game_mode", None) == "Tournament":
            if room_id not in _tournament_scores:
                _tournament_scores[room_id] = {
                    "player_ids": [p.player_id for p in room_for_scores.players],
                    "player_names": [p.player_name for p in room_for_scores.players],
                    "rounds": [],
                    "eliminated": [],
                    "elimination_rounds": {},
                    "exited_rounds": {},
                    "last_starting_player_index": None,
                }

            excluded_ids = await _get_exited_or_eliminated_player_ids(room_id, room_for_scores)
            exited_ids = {p.player_id for p in room_for_scores.players if getattr(p, "is_exited", False)}
            player_id_to_score = {r["player_id"]: r["rule_score"] for r in results}
            round_scores = [
                0 if p.player_id in excluded_ids else int(player_id_to_score.get(p.player_id, 0))
                for p in room_for_scores.players
            ]
            _tournament_scores[room_id]["rounds"].append(round_scores)
            current_round = len(_tournament_scores[room_id]["rounds"])
            exited_rounds = _tournament_scores[room_id].setdefault("exited_rounds", {})
            for pid in exited_ids:
                if pid not in exited_rounds:
                    exited_rounds[pid] = current_round

            score_limit = getattr(room_for_scores, "score_limit", None)
            if score_limit is not None:
                num_p = len(_tournament_scores[room_id]["player_ids"])
                totals = [0] * num_p
                for rs in _tournament_scores[room_id]["rounds"]:
                    for i, s in enumerate(rs):
                        if i < num_p:
                            pid = _tournament_scores[room_id]["player_ids"][i]
                            if pid not in excluded_ids:
                                totals[i] += s
                eliminated = list(_tournament_scores[room_id].get("eliminated", []))
                for i in range(num_p):
                    pid = _tournament_scores[room_id]["player_ids"][i]
                    if pid not in exited_ids and pid not in eliminated and totals[i] > score_limit:
                        eliminated.append(pid)
                _tournament_scores[room_id]["eliminated"] = eliminated

                elim_rounds = _tournament_scores[room_id]["elimination_rounds"]
                for pid in eliminated:
                    if pid not in elim_rounds:
                        elim_rounds[pid] = current_round

                log.info("tournament_round_scored", {
                    "round_number": current_round,
                    "round_scores": round_scores,
                    "totals": totals,
                    "score_limit": score_limit,
                    "newly_eliminated": [pid for pid in eliminated if elim_rounds.get(pid) == current_round],
                    "all_eliminated": eliminated,
                })

            eliminated_ids = _tournament_scores.get(room_id, {}).get("eliminated", [])
            excluded_ids = await _get_exited_or_eliminated_player_ids(room_id, room_for_scores)
            active_count = len(room_for_scores.players) - len([
                p for p in room_for_scores.players if p.player_id in excluded_ids
            ])
            t_finished = active_count <= 1
            
            log.info("DEBUG_TOURNAMENT_FINISH", {
                "room_id": room_id,
                "eliminated": eliminated_ids,
                "exited_ids": list(exited_ids),
                "excluded_ids": list(excluded_ids),
                "active_count": active_count,
                "t_finished": t_finished
            })

            winner_id = None
            if t_finished:
                for p in room_for_scores.players:
                    if p.player_id not in excluded_ids:
                        winner_id = p.player_id
                        break
            rounds_played = len(_tournament_scores.get(room_id, {}).get("rounds", []))
            tournament_meta = {
                "is_tournament": True,
                "round_index": rounds_played,
                "score_limit": score_limit,
                "eliminated_player_ids": eliminated_ids,
                "exited_player_ids": list(exited_ids),
                "has_next_round": not t_finished,
                "tournament_finished": t_finished,
                "tournament_winner_id": winner_id,
                "elimination_rounds": _tournament_scores.get(room_id, {}).get("elimination_rounds", {}),
                "exited_rounds": _tournament_scores.get(room_id, {}).get("exited_rounds", {}),
            }

            if t_finished:
                log.info("tournament_finished", {
                    "winner_id": winner_id,
                    "total_rounds": rounds_played,
                    "eliminated_player_ids": eliminated_ids,
                })
    except Exception as e:
        log.error("tournament_score_recording_failed", {"error": str(e)}, exc_info=True)

    is_single_game = tournament_meta is None
    is_tournament_finished = (
        tournament_meta is not None
        and tournament_meta.get("tournament_finished", False)
    )

    from app.api.websocket.connection_manager import manager

    if is_single_game or is_tournament_finished:
        from app.schemas.room import RoomStatus
        await room_service.update_room_status(room_id, RoomStatus.FINISHED)
        updated_room = await room_service.get_room(room_id)
        if updated_room:
            await manager.broadcast_to_room({
                "type": "room_update",
                "data": updated_room.model_dump(mode='json')
            }, room_id)
            log.info("room_status_completed_broadcast", {"room_id": room_id})

    # Explicitly stop the turn timer and mark the game phase as waiting
    try:
        from app.api.websocket.turn_timer import stop_turn_timer
        stop_turn_timer(room_id)
        
        gs_final = await game_service._load(room_id)
        if gs_final:
            gs_final.phase = "waiting"
            gs_final.declared_player = None
            await game_service._save(room_id, gs_final)
    except Exception as e:
        log.error("failed_to_stop_timer_on_results", {"error": str(e)})

    broadcast_data = {
        "showed_by_id": showed_by_id,
        "players": results,
    }
    if is_survival_win:
        broadcast_data["is_survival_win"] = True
    if is_bot_play:
        broadcast_data["is_bot_play"] = True
    if tournament_meta is not None:
        broadcast_data["tournament"] = tournament_meta
    await manager.broadcast_to_room({
        "type": "show_results",
        "data": broadcast_data,
    }, room_id)
    log.info("show_results_broadcast", {
        "showed_by_id": showed_by_id,
        "tournament": tournament_meta is not None,
    })

    # ── Persist game result to the database ─────────────────────────────────
    try:
        from app.api.websocket.game_ws import _online_game_ids, _online_game_started_at
        game_idn = _online_game_ids.get(room_id)
        started_at = _online_game_started_at.get(room_id)

        if game_idn is not None and started_at is not None:
            is_single_game = tournament_meta is None
            is_tournament_finished = (
                tournament_meta is not None
                and tournament_meta.get("tournament_finished", False)
            )

            log.info("DEBUG_DB_FINALIZE_START", {
                "room_id": room_id,
                "game_idn": game_idn,
                "is_single_game": is_single_game,
                "is_tournament_finished": is_tournament_finished,
                "tournament_meta": tournament_meta
            })

            if is_single_game or is_tournament_finished:
                room_for_db = await room_service.get_room(room_id)
                if room_for_db:
                    firebase_uids = [p.player_id for p in room_for_db.players]
                    async with AsyncSessionLocal() as db:
                        uid_to_idn = await online_game_stats_service.resolve_user_idns(db, firebase_uids)

                    pid_to_score: dict[str, int] = {}
                    if tournament_meta and room_id in _tournament_scores:
                        t_scores = _tournament_scores[room_id]
                        p_ids = t_scores.get("player_ids", [])
                        rounds = t_scores.get("rounds", [])
                        for i, pid in enumerate(p_ids):
                            pid_to_score[pid] = sum(r[i] for r in rounds if i < len(r))
                    else:
                        pid_to_score = {
                            r["player_id"]: r.get("rule_score", 0)
                            for r in results
                        }

                    sortable_players = []
                    elim_rounds: dict[str, int] = tournament_meta.get("elimination_rounds", {}) if tournament_meta else {}
                    exited_rounds: dict[str, int] = tournament_meta.get("exited_rounds", {}) if tournament_meta else {}

                    for p in room_for_db.players:
                        pid = p.player_id
                        score = pid_to_score.get(pid, 0)
                        if tournament_meta:
                            elim_r = elim_rounds.get(pid)
                            if elim_r is None:
                                elim_r = exited_rounds.get(pid, -1)
                            sortable_players.append({"player_id": pid, "score": score, "elim_round": elim_r})
                        else:
                            sortable_players.append({"player_id": pid, "score": score})

                    if tournament_meta:
                        def get_sort_key(p):
                            is_eliminated = p["elim_round"] != -1
                            return (is_eliminated, -p["elim_round"], p["score"])
                        sortable_players.sort(key=get_sort_key)
                        winner_pid = tournament_meta.get("tournament_winner_id")
                    else:
                        winner_pid = None
                        for r in results:
                            if r.get("game_result") == "won":
                                winner_pid = r.get("player_id")
                                break
                        
                        def get_single_sort_key(p):
                            pid = p["player_id"]
                            is_winner = (pid == winner_pid)
                            r_data = next((r for r in results if r.get("player_id") == pid), {})
                            is_exited = r_data.get("is_exited_player", False)
                            # Tie breaker: lower score is better, exited players rank last
                            return (not is_winner, is_exited, p["score"])
                            
                        sortable_players.sort(key=get_single_sort_key)

                    pid_to_rank = {p["player_id"]: idx + 1 for idx, p in enumerate(sortable_players)}
                    winner_user_idn = uid_to_idn.get(winner_pid) if winner_pid else None

                    total_rounds_played = (
                        len(_tournament_scores.get(room_id, {}).get("rounds", []))
                        if tournament_meta
                        else 1
                    )

                    player_results_for_db = []
                    for p in room_for_db.players:
                        u_idn = uid_to_idn.get(p.player_id)
                        if u_idn is None:
                            continue
                        score = pid_to_score.get(p.player_id, 0)
                        rank = pid_to_rank.get(p.player_id)
                        rounds_survived = elim_rounds.get(p.player_id, total_rounds_played)
                        
                        is_exited_player = False
                        if tournament_meta:
                            is_exited_player = p.player_id in tournament_meta.get("exited_player_ids", [])
                        else:
                            r_data = next((r for r in results if r.get("player_id") == p.player_id), {})
                            is_exited_player = r_data.get("is_exited_player", False)

                        player_results_for_db.append({
                            "user_idn": u_idn,
                            "final_score": score,
                            "rank_position": rank,
                            "rounds_survived": rounds_survived,
                            "is_exited": is_exited_player,
                        })

                    async with AsyncSessionLocal() as db:
                        await online_game_stats_service.finalize_online_game(
                            db,
                            game_idn=game_idn,
                            started_at=started_at,
                            winner_user_idn=winner_user_idn,
                            player_results=player_results_for_db,
                            is_survival_win=is_survival_win,
                        )

                    log.info("online_game_db_finalized", {
                        "game_idn": game_idn,
                        "winner_user_idn": winner_user_idn,
                        "is_tournament_finished": is_tournament_finished,
                    })

                    _online_game_ids.pop(room_id, None)
                    _online_game_started_at.pop(room_id, None)
        else:
            log.warn("online_game_db_finalize_skipped", {
                "reason": "no_game_idn_tracked",
                "room_id": room_id,
            })
    except Exception as exc:
        log.error("DEBUG_DB_FINALIZE_ERROR", {"error": str(exc)}, exc_info=True)
        log.error("online_game_db_finalize_error", {"error": str(exc)}, exc_info=True)

    # ── Prepare next round for tournament mode ───────────────────────────────
    try:
        from app.schemas.room import RoomStatus as RoomStatusEnum
        room = await room_service.get_room(room_id)
        if room and getattr(room, "game_mode", None) == "Tournament" and len(room.players) > 1:
            eliminated_ids = _tournament_scores.get(room_id, {}).get("eliminated", [])
            excluded_ids = await _get_exited_or_eliminated_player_ids(room_id, room)
            eliminated_indices = [
                idx for idx, p in enumerate(room.players)
                if p.player_id in excluded_ids
            ]
            active_player_count = len(room.players) - len(eliminated_indices)

            if active_player_count <= 1:
                log.info("tournament_no_next_round", {
                    "reason": "tournament_finished",
                    "active_player_count": active_player_count,
                })
            else:
                last_starter = _tournament_scores.get(room_id, {}).get("last_starting_player_index")
                num_players_total = len(room.players)
                if last_starter is None:
                    try:
                        current_gs_obj = await game_service.get_game_state(room_id)
                        last_starter = current_gs_obj.initial_turn if (current_gs_obj and hasattr(current_gs_obj, "initial_turn")) else 0
                    except Exception:
                        last_starter = 0
                    if last_starter is None:
                        last_starter = 0

                next_starter = (last_starter + 1) % num_players_total
                attempts = 0
                while next_starter in eliminated_indices and attempts < num_players_total:
                    next_starter = (next_starter + 1) % num_players_total
                    attempts += 1
                _tournament_scores[room_id]["last_starting_player_index"] = next_starter

                log.info("tournament_next_round_preparing", {
                    "next_starter_index": next_starter,
                    "prev_starter_index": last_starter,
                    "eliminated_indices": eliminated_indices,
                    "active_player_count": active_player_count,
                })

                # Mark all players ready/in-game so auto-start triggers
                for player_obj in room.players:
                    await room_service.update_player_fields(
                        room_id, player_obj.player_id,
                        is_ready=True, is_in_game=True,
                    )

                async def delayed_start_next_round(r_id, elim_indices, turn_override, g_service, r_service):
                    import asyncio
                    log.info("tournament_next_round_waiting_12s")
                    await asyncio.sleep(12)
                    try:
                        await g_service.clear_game(r_id)
                        await r_service.update_room_status(r_id, RoomStatusEnum.WAITING)
                        from app.api.websocket.game_ws import start_game_for_room
                        started = await start_game_for_room(r_id, eliminated_indices=elim_indices, initial_turn_override=turn_override)
                        log.info("tournament_next_round_started", {
                            "started": started,
                            "next_starter_index": turn_override,
                        })
                    except Exception as e:
                        log.error("error_in_delayed_start_next_round", {"error": str(e)})

                import asyncio
                asyncio.create_task(delayed_start_next_round(room_id, eliminated_indices, next_starter, game_service, room_service))
    except Exception as e:
        log.error("tournament_next_round_failed", {"error": str(e)}, exc_info=True)

    return tournament_meta


@router.post("/{room_id}/show", response_model=ShowResponse)
async def calculate_show_results(room_id: str, request: ShowRequest, user: dict = Depends(get_current_firebase_user)):
    """Calculate game results when a player shows"""
    log = get_room_logger(room_id)

    room = await room_service.get_room(room_id)
    if not room:
        room = await room_service.get_room_by_code(room_id.upper())
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        room_id = room.room_id

    results = await game_service.calculate_show_results(room_id, request.showed_by_id)
    if results is None:
        raise HTTPException(status_code=404, detail="Game not found or player not found")

    tournament_meta = await process_and_broadcast_show_results(room_id, request.showed_by_id, results, is_survival_win=False)
    
    return ShowResponse(players=results, tournament=tournament_meta)


@router.get("/{room_id}/tournament-scores", response_model=TournamentScoresResponse)
async def get_tournament_scores(room_id: str, user: dict = Depends(get_current_firebase_user)):
    """Get aggregated tournament scores (per-round and totals) for a room"""
    room = await room_service.get_room(room_id)
    if not room:
        room = await room_service.get_room_by_code(room_id.upper())
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        room_id = room.room_id

    data = _tournament_scores.get(room_id)
    if not data:
        player_ids = [p.player_id for p in room.players]
        player_names = [p.player_name for p in room.players]
        totals = [0 for _ in player_ids]
        return TournamentScoresResponse(
            player_ids=player_ids,
            player_names=player_names,
            rounds=[],
            totals=totals,
            exited_rounds={},
        )

    player_ids = data["player_ids"]
    player_names = []
    for i, pid in enumerate(player_ids):
        player = next((p for p in room.players if p.player_id == pid), None)
        if player:
            player_names.append(player.player_name)
        else:
            player_names.append(data["player_names"][i])

    rounds_raw: list[list[int]] = data["rounds"]

    num_players = len(player_ids)
    totals = [0 for _ in range(num_players)]
    for round_scores in rounds_raw:
        for idx, score in enumerate(round_scores):
            if idx < num_players:
                totals[idx] += int(score)

    rounds = [
        TournamentRoundScores(round=i + 1, scores=[int(s) for s in scores])
        for i, scores in enumerate(rounds_raw)
    ]

    return TournamentScoresResponse(
        player_ids=player_ids,
        player_names=player_names,
        rounds=rounds,
        totals=totals,
        elimination_rounds=data.get("elimination_rounds", {}),
        exited_rounds=data.get("exited_rounds", {}),
    )
