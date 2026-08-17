-- The credentials table must never be reachable through PostgREST.
-- (Same posture as Book-Bot deploy/03_secure_users.sql and
-- load-log deploy/03_post_migrate.sql.)

REVOKE ALL ON solitaire.users FROM solitaire_user;
REVOKE ALL ON solitaire.users FROM web_anon;
