-- Row-level security: every PostgREST request only sees its own rows,
-- keyed on the user_id claim of the JWT. Idempotent (DROP/CREATE pairs).

CREATE OR REPLACE FUNCTION solitaire.jwt_user_id() RETURNS uuid
LANGUAGE plpgsql STABLE AS $$
DECLARE
    claims text := NULLIF(current_setting('request.jwt.claims', true), '');
    uid text;
BEGIN
    IF claims IS NOT NULL THEN
        uid := COALESCE(claims::jsonb ->> 'user_id', claims::jsonb ->> 'sub');
    END IF;
    -- pre-v9 PostgREST exposes claims as individual settings
    uid := COALESCE(uid, NULLIF(current_setting('request.jwt.claim.user_id', true), ''));
    RETURN uid::uuid;
EXCEPTION WHEN others THEN
    RETURN NULL;
END $$;

ALTER TABLE solitaire.game_state ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS user_isolation ON solitaire.game_state;
CREATE POLICY user_isolation ON solitaire.game_state
    USING (user_id = solitaire.jwt_user_id())
    WITH CHECK (user_id = solitaire.jwt_user_id());

ALTER TABLE solitaire.progress ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS user_isolation ON solitaire.progress;
CREATE POLICY user_isolation ON solitaire.progress
    USING (user_id = solitaire.jwt_user_id())
    WITH CHECK (user_id = solitaire.jwt_user_id());
