-- Runs only when the db volume is first created. For an existing volume:
--   docker compose exec db createdb -U besentry besentry_log
CREATE DATABASE besentry_log;
