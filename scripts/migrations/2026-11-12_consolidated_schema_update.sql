-- ==============================================================================
-- CONSOLIDATED DATABASE MIGRATION SCRIPT
-- ==============================================================================
-- Includes:
--   1. 006_client_game_id_schema.sql       (Client Game ID & Idempotency)
--   2. 007_room_type_schema.sql            (Room Type for Public/Private games)
--   3. 008_ad_placement_enum_update.sql   (New Ad Placement enum values)
--   4. 2026-10-18_iap_transactions.sql     (In-App Purchase audit & verification)
--   5. 2026-10-28_online_bot_role.sql      (Online Bot user role enum value)
--
-- NOTE ON EXECUTION:
-- Execute this script in autocommit mode (outside a manual BEGIN...COMMIT block)
-- because PostgreSQL restricts ALTER TYPE ... ADD VALUE inside transaction blocks.
-- All statements are designed to be idempotent and safe to re-run.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. ENUM UPDATES
-- ------------------------------------------------------------------------------

-- Matchmaking bots role
ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'online_bot';

-- Ad placements for Play With Randoms and Play With Bot
ALTER TYPE ad_placement_enum ADD VALUE IF NOT EXISTS 'play_with_randoms_rewarded';
ALTER TYPE ad_placement_enum ADD VALUE IF NOT EXISTS 'play_with_bot_rewarded_interstitial';


-- ------------------------------------------------------------------------------
-- 2. GAMES TABLE: client_game_id (Migration 006)
-- ------------------------------------------------------------------------------

-- Column to store offline SQLite game id for sync deduplication
ALTER TABLE games ADD COLUMN IF NOT EXISTS client_game_id VARCHAR(100);

-- Unique constraint ensuring idempotency per user.
-- Wrapped in a DO block because PostgreSQL does not support IF NOT EXISTS on ADD CONSTRAINT.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_user_client_game'
    ) THEN
        ALTER TABLE games ADD CONSTRAINT uq_user_client_game UNIQUE (created_user_idn, client_game_id);
    END IF;
END $$;


-- ------------------------------------------------------------------------------
-- 3. GAMES TABLE: room_type (Migration 007)
-- ------------------------------------------------------------------------------

-- Column to distinguish between 'public' matchmaking and 'private' friend rooms
ALTER TABLE games ADD COLUMN IF NOT EXISTS room_type VARCHAR(50);

-- Backfill existing historical online games to 'private'
UPDATE games
SET room_type = 'private'
WHERE game_type = 'online'
  AND room_type IS NULL;

-- Ensure offline games have room_type = NULL
UPDATE games
SET room_type = NULL
WHERE game_type = 'offline'
  AND room_type IS NOT NULL;


-- ------------------------------------------------------------------------------
-- 4. IAP TRANSACTIONS TABLE & INDEXES (Migration 2026-10-18)
-- ------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.iap_transactions (
    iap_transaction_idn SERIAL PRIMARY KEY,
    user_idn INTEGER NOT NULL REFERENCES public.users(user_idn) ON DELETE CASCADE,
    platform VARCHAR NOT NULL,
    product_id VARCHAR NOT NULL,
    plan_id VARCHAR,
    purchase_token VARCHAR NOT NULL,
    store_transaction_id VARCHAR NOT NULL,
    original_transaction_id VARCHAR,
    verification_status VARCHAR NOT NULL DEFAULT 'pending',
    source VARCHAR NOT NULL DEFAULT 'purchase',
    environment VARCHAR,
    purchase_dt TIMESTAMPTZ,
    expires_dt TIMESTAMPTZ,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    error_message VARCHAR,
    store_payload JSONB,
    crt_dt TIMESTAMPTZ DEFAULT NOW(),
    upd_dt TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT ck_iap_platform CHECK (platform IN ('android', 'ios')),
    CONSTRAINT ck_iap_verification_status CHECK (
        verification_status IN ('pending', 'verified', 'rejected', 'expired', 'refunded')
    ),
    CONSTRAINT ck_iap_source CHECK (source IN ('purchase', 'restore', 'renew', 'verify')),
    CONSTRAINT uq_iap_platform_store_txn UNIQUE (platform, store_transaction_id)
);

CREATE INDEX IF NOT EXISTS ix_iap_transactions_user_idn ON public.iap_transactions (user_idn);
CREATE INDEX IF NOT EXISTS ix_iap_transactions_purchase_token ON public.iap_transactions (purchase_token);
CREATE INDEX IF NOT EXISTS ix_iap_transactions_original_txn ON public.iap_transactions (original_transaction_id);
CREATE INDEX IF NOT EXISTS ix_iap_transactions_crt_dt ON public.iap_transactions (crt_dt DESC);

