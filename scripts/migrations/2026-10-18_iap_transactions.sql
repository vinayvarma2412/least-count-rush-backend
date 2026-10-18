-- IAP transaction history and server-side verification audit log.
-- Run against Postgres before deploying the verify endpoint.

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
