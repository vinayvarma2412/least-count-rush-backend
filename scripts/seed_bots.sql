-- Seed bot users (must match the bot names defined in opponent.dart)
-- These are dummy users used when a player plays against bots.
-- Their user_idn values will be 1-5 (SERIAL starts at 1) if this is run on a fresh table.
-- If the table already has rows, adjust accordingly.

INSERT INTO users (user_id, user_name, display_name, role, user_type, is_online, entity_active)
VALUES
  ('bot_zero_hero',    'zero_hero',    'Zero Hero',    'bot', 'bot', false, true),
  ('bot_count_crush',  'count_crush',  'Count Crush',  'bot', 'bot', false, true),
  ('bot_minimax',      'minimax',      'Minimax',       'bot', 'bot', false, true),
  ('bot_sneaky_seven', 'sneaky_seven', 'Sneaky Seven', 'bot', 'bot', false, true),
  ('bot_drop_master',  'drop_master',  'Drop Master',  'bot', 'bot', false, true)
ON CONFLICT (user_id) DO NOTHING;

-- Verify the inserted bots
SELECT user_idn, user_id, user_name, display_name, role FROM users WHERE role = 'bot' ORDER BY user_idn;
