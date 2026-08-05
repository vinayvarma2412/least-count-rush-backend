#!/usr/bin/env python3
"""
clear_db.py — Wipe all data from the Supabase PostgreSQL database.

Uses asyncpg (already a project dependency) — no extra packages needed.

Tables are truncated in dependency order (children before parents) so that
foreign-key constraints are never violated.  Enum types and table schemas
are left untouched; only rows are removed.

Usage (from the backend/ directory):
    python scripts/clear_db.py              # dry-run preview
    python scripts/clear_db.py --confirm    # actually deletes data
"""

import asyncio
import os
import sys
from pathlib import Path

# ── Load .env so DATABASE_URL is available even when run standalone ─────────
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

import asyncpg  # noqa: E402  (installed as part of requirements.txt)

# ── Table truncation order (children → parents) ─────────────────────────────
TABLES_IN_ORDER = [
    "game_players",             # → games, users
    "games",                    # → users (winner/creator — SET NULL)
    "messages",                 # → users
    "notifications",            # → users
    "friends",                  # → users
    "user_topic_subscriptions", # → users
    "user_devices",             # → users
    "season_leaderboard_stats", # → leaderboard_seasons, users
    "user_leaderboard_stats",   # → users
    "leaderboard_seasons",      # root table (for seasons)
    "users",                    # root table
]


def _clean_url(raw: str) -> str:
    """Strip the SQLAlchemy driver prefix so asyncpg can parse the URL."""
    return (
        raw.replace("postgresql+asyncpg://", "postgresql://")
           .replace("postgres+asyncpg://", "postgresql://")
    )


async def main() -> None:
    dry_run = "--confirm" not in sys.argv

    print("=" * 60)
    print("  Least Count — Supabase DB Cleaner")
    print("=" * 60)

    if dry_run:
        print("\n⚠️   DRY-RUN MODE — no data will be deleted.")
        print("     Re-run with  --confirm  to actually clear the DB.\n")

    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        print("❌  DATABASE_URL is not set. Check your backend/.env file.")
        sys.exit(1)

    conn: asyncpg.Connection = await asyncpg.connect(
        _clean_url(raw_url),
        ssl="require",
        timeout=15,
    )

    try:
        # ── Show current row counts ──────────────────────────────────────────
        print(f"\n  {'Table':<35} {'Rows':>8}")
        print("  " + "-" * 43)
        total_rows = 0
        for table in TABLES_IN_ORDER:
            n = await conn.fetchval(f'SELECT COUNT(*) FROM "{table}"')
            total_rows += n
            print(f"  {table:<35} {n:>8,}")
        print("  " + "-" * 43)
        print(f"  {'TOTAL':<35} {total_rows:>8,}\n")

        if total_rows == 0:
            if dry_run:
                print("✅  Database is already empty. Nothing to do.")
                return

        if dry_run:
            print("Run with  --confirm  to delete all rows shown above.")
            return

        # ── Final confirmation ───────────────────────────────────────────────
        print(f"🚨  About to DELETE ALL {total_rows:,} rows from the database and populate default bot users!")
        answer = input("    Type  YES  to continue: ").strip()
        if answer != "YES":
            print("Aborted — nothing was changed.")
            return

        # ── Truncate all tables and insert bots in one transaction ───────────
        print("\n🗑️   Truncating tables …")
        async with conn.transaction():
            for table in TABLES_IN_ORDER:
                await conn.execute(
                    f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE'
                )
                print(f"    ✓  {table}")
            
            print("\n🤖   Inserting default bot users …")
            insert_query = """
            INSERT INTO users (user_id, user_name, display_name, role, user_type, is_online, entity_active)
            VALUES
              ('bot_zero_hero',    'zero_hero',    'Zero Hero',    'bot', 'bot', false, true),
              ('bot_count_crush',  'count_crush',  'Count Crush',  'bot', 'bot', false, true),
              ('bot_minimax',      'minimax',      'Minimax',      'bot', 'bot', false, true),
              ('bot_sneaky_seven', 'sneaky_seven', 'Sneaky Seven', 'bot', 'bot', false, true),
              ('bot_drop_master',  'drop_master',  'Drop Master',  'bot', 'bot', false, true)
            ON CONFLICT (user_id) DO NOTHING;
            """
            await conn.execute(insert_query)
            print("    ✓  Default bot users inserted")

        print("\n✅  Database cleared and populated successfully!")
        print("    All sequences have been reset to 1.")

    except asyncpg.PostgresError as e:
        print(f"\n❌  PostgreSQL error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌  Unexpected error: {e}")
        sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
