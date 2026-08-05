from sqlalchemy import Column, Integer, String, Boolean, DateTime, Date, ForeignKey, Enum, JSON, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum

# --- ENUM Definitions ---

class UserRoleEnum(str, enum.Enum):
    admin = "admin"
    user = "user"
    bot = "bot"

class PlatformEnum(str, enum.Enum):
    ios = "ios"
    android = "android"
    web = "web"

class FriendStatusEnum(str, enum.Enum):
    waiting = "waiting"
    accepted = "accepted"
    rejected = "rejected"

class MessageTypeEnum(str, enum.Enum):
    text = "text"
    image = "image"
    emoji = "emoji"

class NotifStatusEnum(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    failed = "failed"

class GameTypeEnum(str, enum.Enum):
    online = "online"
    offline = "offline"

class GameModeEnum(str, enum.Enum):
    single = "single"
    tournament = "tournament"

class GameResultEnum(str, enum.Enum):
    completed = "completed"
    cancelled = "cancelled"
    draw = "draw"
    in_progress = "in_progress"

class AdPlacementEnum(str, enum.Enum):
    rewarded     = "rewarded"
    banner       = "banner"
    interstitial = "interstitial"

# --- Models ---

class User(Base):
    __tablename__ = "users"

    user_idn = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, nullable=True)  # nullable: cleared on soft-delete # Firebase UID
    email = Column(String, unique=True, nullable=True)
    user_name = Column(String, unique=True, nullable=True)
    display_name = Column(String, nullable=True)
    avatar_seed = Column(String, nullable=True)
    role = Column(Enum(UserRoleEnum, name="user_role_enum", create_type=False), nullable=False, default=UserRoleEnum.user)
    is_online = Column(Boolean, default=False, index=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    last_active_date = Column(DateTime(timezone=True), nullable=True)
    user_type = Column(String, default="authenticated") # 'authenticated' or 'guest'
    ads_free = Column(Boolean, nullable=False, default=False)  # True = skip all ads
    ads_free_until = Column(DateTime(timezone=True), nullable=True)  # None = permanent
    rated_at = Column(DateTime(timezone=True), nullable=True)  # Set when user taps "Rate Now"; NULL = not yet rated
    wins_since_dismissed = Column(Integer, nullable=False, default=0)  # Wins since last "Not Now"; show popup at 5
    entity_active = Column(Boolean, default=True)
    crt_dt = Column(DateTime(timezone=True), server_default=func.now())
    upd_dt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    devices = relationship("UserDevice", back_populates="user", cascade="all, delete-orphan")
    topic_subscriptions = relationship("UserTopicSubscription", back_populates="user", cascade="all, delete-orphan")
    games_created = relationship("Game", back_populates="creator", foreign_keys="Game.created_user_idn")
    games_won = relationship("Game", back_populates="winner", foreign_keys="Game.winner_user_idn")
    game_participations = relationship("GamePlayer", back_populates="user", cascade="all, delete-orphan")


class UserDevice(Base):
    __tablename__ = "user_devices"

    user_device_idn = Column(Integer, primary_key=True, index=True)
    user_idn = Column(Integer, ForeignKey("users.user_idn", ondelete="CASCADE"), nullable=False)
    platform = Column(Enum(PlatformEnum, name="platform_enum", create_type=False), nullable=False)
    device_id = Column(String, nullable=False)
    fcm_token = Column(String, nullable=True)
    last_active_at = Column(DateTime(timezone=True), server_default=func.now())
    entity_active = Column(Boolean, default=True)
    crt_dt = Column(DateTime(timezone=True), server_default=func.now())
    upd_dt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("user_idn", "device_id", name="uq_user_device"),)

    user = relationship("User", back_populates="devices")


class UserTopicSubscription(Base):
    __tablename__ = "user_topic_subscriptions"

    subscription_idn = Column(Integer, primary_key=True, index=True)
    user_idn = Column(Integer, ForeignKey("users.user_idn", ondelete="CASCADE"), nullable=False)
    topic_name = Column(String, nullable=False)
    entity_active = Column(Boolean, default=True)
    crt_dt = Column(DateTime(timezone=True), server_default=func.now())
    upd_dt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("user_idn", "topic_name", name="uq_user_topic"),)

    user = relationship("User", back_populates="topic_subscriptions")


class Friend(Base):
    __tablename__ = "friends"

    friend_idn = Column(Integer, primary_key=True, index=True)
    user_idn = Column(Integer, ForeignKey("users.user_idn", ondelete="CASCADE"), nullable=False)
    friend_user_idn = Column(Integer, ForeignKey("users.user_idn", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(Enum(FriendStatusEnum, name="friend_status_enum", create_type=False), nullable=False, default=FriendStatusEnum.waiting)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    entity_active = Column(Boolean, default=True)
    crt_dt = Column(DateTime(timezone=True), server_default=func.now())
    upd_dt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_idn", "friend_user_idn", name="uq_friend_pair"),
        CheckConstraint("user_idn <> friend_user_idn", name="chk_no_self_friend"),
    )


class Message(Base):
    __tablename__ = "messages"

    message_idn = Column(Integer, primary_key=True, index=True)
    from_user_idn = Column(Integer, ForeignKey("users.user_idn", ondelete="CASCADE"), nullable=False)
    to_user_idn = Column(Integer, ForeignKey("users.user_idn", ondelete="CASCADE"), nullable=False)
    message_type = Column(Enum(MessageTypeEnum, name="message_type_enum", create_type=False), nullable=False, default=MessageTypeEnum.text)
    content = Column(String, nullable=False)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    entity_active = Column(Boolean, default=True)
    crt_dt = Column(DateTime(timezone=True), server_default=func.now())
    
    # Indexes are generally created via Alembic or direct SQL, 
    # but we represent the columns here


class Notification(Base):
    __tablename__ = "notifications"

    notification_idn = Column(Integer, primary_key=True, index=True)
    receiver_user_idn = Column(Integer, ForeignKey("users.user_idn", ondelete="CASCADE"), nullable=True)
    receiver_user_topic = Column(String, nullable=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    status = Column(Enum(NotifStatusEnum, name="notif_status_enum", create_type=False), nullable=False, default=NotifStatusEnum.pending)
    schedule_to = Column(DateTime(timezone=True), nullable=True)
    entity_active = Column(Boolean, default=True)
    crt_dt = Column(DateTime(timezone=True), server_default=func.now())
    upd_dt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("receiver_user_idn IS NOT NULL OR receiver_user_topic IS NOT NULL", name="chk_receiver_target"),
    )


class Game(Base):
    __tablename__ = "games"

    game_idn = Column(Integer, primary_key=True, index=True)
    game_type = Column(Enum(GameTypeEnum, name="game_type_enum", create_type=False), nullable=False, default=GameTypeEnum.online)
    game_mode = Column(Enum(GameModeEnum, name="game_mode_enum", create_type=False), nullable=False, default=GameModeEnum.single)
    result = Column(Enum(GameResultEnum, name="game_result_enum", create_type=False), nullable=False, default=GameResultEnum.completed)
    total_players = Column(Integer, nullable=False)
    total_rounds = Column(Integer, nullable=False)
    winner_user_idn = Column(Integer, ForeignKey("users.user_idn", ondelete="SET NULL"), nullable=True)
    created_user_idn = Column(Integer, ForeignKey("users.user_idn", ondelete="SET NULL"), nullable=True)
    score_limit = Column(String(50), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    entity_active = Column(Boolean, default=True)
    crt_dt = Column(DateTime(timezone=True), server_default=func.now())

    winner = relationship("User", foreign_keys=[winner_user_idn], back_populates="games_won")
    creator = relationship("User", foreign_keys=[created_user_idn], back_populates="games_created")
    players = relationship("GamePlayer", back_populates="game", cascade="all, delete-orphan")


class GamePlayer(Base):
    __tablename__ = "game_players"

    game_player_idn = Column(Integer, primary_key=True, index=True)
    game_idn = Column(Integer, ForeignKey("games.game_idn", ondelete="CASCADE"), nullable=False)
    user_idn = Column(Integer, ForeignKey("users.user_idn", ondelete="CASCADE"), nullable=False)
    seat_no = Column(Integer, nullable=True)
    final_score = Column(Integer, nullable=False)
    rank_position = Column(Integer, nullable=True)
    rounds_survived = Column(Integer, default=0)
    lp_earned = Column(Integer, default=0)
    lp_breakdown = Column(JSON, nullable=True)
    entity_active = Column(Boolean, default=True)
    crt_dt = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("game_idn", "user_idn", name="uq_game_user"),)

    game = relationship("Game", back_populates="players")
    user = relationship("User", back_populates="game_participations")


class LeaderboardSeason(Base):
    __tablename__ = "leaderboard_seasons"

    season_idn = Column(Integer, primary_key=True, index=True)
    season_name = Column(String, nullable=False)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    is_active = Column(Boolean, default=False, index=True)
    crt_dt = Column(DateTime(timezone=True), server_default=func.now())

    season_stats = relationship("SeasonLeaderboardStat", back_populates="season", cascade="all, delete-orphan")


class SeasonLeaderboardStat(Base):
    __tablename__ = "season_leaderboard_stats"

    season_stat_idn = Column(Integer, primary_key=True, index=True)
    season_idn = Column(Integer, ForeignKey("leaderboard_seasons.season_idn", ondelete="CASCADE"), nullable=False)
    user_idn = Column(Integer, ForeignKey("users.user_idn", ondelete="CASCADE"), nullable=False)
    total_points = Column(Integer, default=0, index=True)
    games_played = Column(Integer, default=0)
    games_won = Column(Integer, default=0)
    top_3_finishes = Column(Integer, default=0)
    total_rank_sum = Column(Integer, default=0)
    best_tournament_win_limit = Column(Integer, default=0)
    current_streak = Column(Integer, default=0)
    longest_win_streak = Column(Integer, default=0)
    upd_dt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("season_idn", "user_idn", name="uq_season_user"),)

    season = relationship("LeaderboardSeason", back_populates="season_stats")
    user = relationship("User")


class UserLeaderboardStat(Base):
    __tablename__ = "user_leaderboard_stats"

    user_idn = Column(Integer, ForeignKey("users.user_idn", ondelete="CASCADE"), primary_key=True)
    total_points = Column(Integer, default=0, index=True)
    games_played = Column(Integer, default=0)
    games_won = Column(Integer, default=0)
    top_3_finishes = Column(Integer, default=0)
    total_rank_sum = Column(Integer, default=0)
    best_tournament_win_limit = Column(Integer, default=0)
    current_streak = Column(Integer, default=0)
    longest_win_streak = Column(Integer, default=0)
    crt_dt = Column(DateTime(timezone=True), server_default=func.now())
    upd_dt = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User")


class AdImpression(Base):
    """Tracks how many times a user has seen each ad placement today."""
    __tablename__ = "ad_impressions"

    ad_impression_idn = Column(Integer, primary_key=True, index=True)
    user_idn          = Column(Integer, ForeignKey("users.user_idn", ondelete="CASCADE"), nullable=False)
    placement         = Column(Enum(AdPlacementEnum, name="ad_placement_enum", create_type=False), nullable=False)
    impression_date   = Column(Date, nullable=False)  # server sets via SQL DEFAULT
    impression_count  = Column(Integer, nullable=False, default=1)
    crt_dt            = Column(DateTime(timezone=True), server_default=func.now())
    upd_dt            = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_idn", "placement", "impression_date", name="uq_user_placement_date"),
    )

    user = relationship("User")

class DeletedUser(Base):
    __tablename__ = "deleted_users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, index=True)
    firebase_uid = Column(String, nullable=True)
    deleted_at = Column(DateTime(timezone=True), server_default=func.now())
    blocked_until = Column(DateTime(timezone=True), nullable=False)
