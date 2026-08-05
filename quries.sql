select *
from ad_impressions
where user_idn = 20;


select *
from users
where user_idn = 20;


select *
from game_players
where user_idn = 27
ORDER BY crt_dt desc -- update users
-- set ads_free = false
-- where user_idn = 20;
 -- UPDATE users
-- set rated_at = NULL,
--     wins_since_dismissed = 0,
--     upd_dt = now();

select *
from deleted_users -- TRUNCATE TABLE deleted_users;

select *
from users -- TRUNCATE table deleted_users CASCADE;

select *
from messages