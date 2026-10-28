-- Matchmaking bots use a fourth users.role value.
-- PostgreSQL 15+ accepts IF NOT EXISTS. Run outside a transaction if an
-- older server rejects ADD VALUE inside a transaction block.
ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'online_bot';
