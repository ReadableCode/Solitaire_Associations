-- The credentials table must never be reachable through PostgREST.
-- (Same posture as Book-Bot deploy/03_secure_users.sql and
-- load-log deploy/03_secure_users.sql.)
-- Applied by app/bootstrap.py at startup.
-- Running this file by hand with psql might work, but it is not the intended
-- use: change it here and let the app apply it.

REVOKE ALL ON solitaire.users FROM solitaire_user;
REVOKE ALL ON solitaire.users FROM web_anon;
