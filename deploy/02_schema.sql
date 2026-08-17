-- Core schema. Idempotent; applied by app/bootstrap.py at startup
-- (version-gated via solitaire.deploy_meta).

CREATE SCHEMA IF NOT EXISTS solitaire;

-- Credentials read ONLY by the app process (superuser);
-- 03_secure_users.sql revokes it from the PostgREST-facing roles.
CREATE TABLE IF NOT EXISTS solitaire.users (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username            text UNIQUE NOT NULL,
    password_hash       text NOT NULL,
    role                text NOT NULL DEFAULT 'user',
    display_name        text NOT NULL DEFAULT '',
    disabled            boolean NOT NULL DEFAULT false,
    created_at          timestamptz NOT NULL DEFAULT now(),
    password_changed_at timestamptz NOT NULL DEFAULT now()
);

-- One live game per user: the whole board serialized by the client engine.
-- Written (debounced) after every move; reload resumes exactly.
CREATE TABLE IF NOT EXISTS solitaire.game_state (
    user_id    uuid PRIMARY KEY REFERENCES solitaire.users (id) ON DELETE CASCADE,
    state      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Per-level results for the level map / stars / streaks.
CREATE TABLE IF NOT EXISTS solitaire.progress (
    user_id      uuid NOT NULL REFERENCES solitaire.users (id) ON DELETE CASCADE,
    level_id     integer NOT NULL,
    completed    boolean NOT NULL DEFAULT false,
    attempts     integer NOT NULL DEFAULT 0,
    best_moves_left integer,
    completed_at timestamptz,
    PRIMARY KEY (user_id, level_id)
);

GRANT USAGE ON SCHEMA solitaire TO solitaire_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON solitaire.game_state TO solitaire_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON solitaire.progress TO solitaire_user;
