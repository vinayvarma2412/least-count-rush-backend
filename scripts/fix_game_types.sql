-- Fix game_type for all bot games that were incorrectly saved as 'online'.
-- A bot game is one where the winner or any game_player is one of the 5 bot users (user_idn 1-5).
-- We update ALL games that have at least one bot participant to 'offline'.

UPDATE games
SET game_type = 'offline'
WHERE game_idn IN (
    SELECT DISTINCT game_idn
    FROM game_players
    WHERE user_idn IN (1, 2, 3, 4, 5)  -- bot user_idns
);

-- Verify
SELECT game_idn, game_type, total_players, winner_user_idn
FROM games
ORDER BY game_idn DESC
LIMIT 20;
