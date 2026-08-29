@firebase_router.get("/users", response_model=List[ActiveUserResponse])
async def get_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    active_today: bool = Query(False),
    sortBy: str = Query("createdAt"),
    sortOrder: str = Query("desc"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(_require_admin)
):
    try:
        from sqlalchemy import func, asc, desc
        from app.models.db_models import GamePlayer, Game, GameTypeEnum
        from datetime import datetime, timezone, timedelta
        
        offset = (page - 1) * limit

        subq_online = select(func.count(GamePlayer.game_idn)).join(
            Game, GamePlayer.game_idn == Game.game_idn
        ).where(
            GamePlayer.user_idn == User.user_idn,
            Game.game_type == GameTypeEnum.online,
            GamePlayer.entity_active == True,
            Game.entity_active == True
        ).scalar_subquery()

        subq_offline = select(func.count(GamePlayer.game_idn)).join(
            Game, GamePlayer.game_idn == Game.game_idn
        ).where(
            GamePlayer.user_idn == User.user_idn,
            Game.game_type == GameTypeEnum.offline,
            GamePlayer.entity_active == True,
            Game.entity_active == True
        ).scalar_subquery()

        query = select(
            User,
            subq_online.label("online_games"),
            subq_offline.label("offline_games")
        ).where(
            User.entity_active == True
        )

        if active_today:
            # Active today means last_active_date is today in UTC
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            query = query.where(User.last_active_date >= today_start)

        if sortBy == "onlineGames":
            order_col = subq_online
        elif sortBy == "offlineGames":
            order_col = subq_offline
        elif sortBy == "name":
            order_col = User.display_name
        else:
            order_col = User.crt_dt

        if sortOrder == "asc":
            query = query.order_by(asc(order_col))
        else:
            query = query.order_by(desc(order_col))

        query = query.offset(offset).limit(limit)

        result = await db.execute(query)
        rows = result.all()

        response = []
        for user_obj, online_count, offline_count in rows:
            response.append({
                "user_idn": user_obj.user_idn,
                "display_name": user_obj.display_name,
                "email": user_obj.email,
                "last_active_date": user_obj.last_active_date,
                "online_games": online_count or 0,
                "offline_games": offline_count or 0,
                "date_online_games": 0,
                "date_offline_games": 0,
            })
            
        return response
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        raise HTTPException(status_code=500, detail=str(e))
