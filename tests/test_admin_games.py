from datetime import datetime, timezone
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.db_models import Game, GameTypeEnum, GameModeEnum, GameResultEnum, User, UserRoleEnum
from app.api.routes.admin import get_dashboard_stats_games, get_admin_games, get_admin_game_details


@pytest_asyncio.fixture
async def game_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        admin = User(
            user_idn=1,
            user_id="admin-uid",
            user_name="admin",
            display_name="Admin",
            role=UserRoleEnum.admin,
        )
        player = User(
            user_idn=2,
            user_id="player-uid",
            user_name="player",
            display_name="Player",
            role=UserRoleEnum.user,
        )
        session.add_all([admin, player])
        await session.commit()

        now = datetime.now(timezone.utc)
        g_public = Game(
            game_idn=1,
            game_type=GameTypeEnum.online,
            game_mode=GameModeEnum.single,
            result=GameResultEnum.in_progress,
            total_players=2,
            total_rounds=1,
            created_user_idn=2,
            room_type="public",
            crt_dt=now,
        )
        g_private = Game(
            game_idn=2,
            game_type=GameTypeEnum.online,
            game_mode=GameModeEnum.single,
            result=GameResultEnum.in_progress,
            total_players=2,
            total_rounds=1,
            created_user_idn=2,
            room_type="private",
            crt_dt=now,
        )
        g_offline = Game(
            game_idn=3,
            game_type=GameTypeEnum.offline,
            game_mode=GameModeEnum.single,
            result=GameResultEnum.completed,
            total_players=2,
            total_rounds=3,
            created_user_idn=2,
            room_type=None,
            crt_dt=now,
        )
        session.add_all([g_public, g_private, g_offline])
        await session.commit()

    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_games_stats_public_private(game_session: AsyncSession):
    admin = await game_session.get(User, 1)

    stats = await get_dashboard_stats_games(
        tz_offset=0,
        current_user=admin,
        db=game_session,
    )

    assert stats.games_online_today == 2
    assert stats.games_public_today == 1
    assert stats.games_private_today == 1
    assert stats.games_offline_today == 1
    assert stats.games_in_progress_online == 2
    assert stats.games_in_progress_public == 1
    assert stats.games_in_progress_private == 1


@pytest.mark.asyncio
async def test_games_listing_room_type(game_session: AsyncSession):
    admin = await game_session.get(User, 1)

    games = await get_admin_games(
        current_user=admin,
        db=game_session,
    )

    assert len(games) == 3
    room_types = {g["game_idn"]: g["room_type"] for g in games}
    assert room_types[1] == "public"
    assert room_types[2] == "private"
    assert room_types[3] is None

    # Filter by public room_type
    public_games = await get_admin_games(
        room_type="public",
        current_user=admin,
        db=game_session,
    )
    assert len(public_games) == 1
    assert public_games[0]["game_idn"] == 1

    # Filter by private room_type
    private_games = await get_admin_games(
        room_type="private",
        current_user=admin,
        db=game_session,
    )
    assert len(private_games) == 1
    assert private_games[0]["game_idn"] == 2

    # Game details
    details = await get_admin_game_details(
        game_idn=1,
        current_user=admin,
        db=game_session,
    )
    assert details["game"]["room_type"] == "public"
