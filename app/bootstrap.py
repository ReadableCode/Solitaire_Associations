"""App-owned DB bootstrap — Book-Bot pattern.

Runs from the FastAPI lifespan (never the docker entrypoint), converges the
solitaire schema inside the shared `apps` database, and is version-gated via
solitaire.deploy_meta so it is a no-op on every boot after the first. Only
ADDITIVE statements live in the SQL files; nothing here can touch any other
schema. Failures are logged and the app still serves — next boot retries.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from . import config

log = logging.getLogger("solitaire.bootstrap")

DEPLOY_DIR = Path(__file__).resolve().parent.parent / "deploy"
SCHEMA_FILES = ("02_schema.sql", "03_secure_users.sql", "04_rls.sql")
SCHEMA_VERSION = 1

ROLE_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'solitaire_user') THEN
        CREATE ROLE solitaire_user NOLOGIN;
    END IF;
END $$;
GRANT solitaire_user TO postgrest_authenticator;
"""


def _applied_version(cur) -> int | None:
    cur.execute(
        """SELECT 1 FROM information_schema.tables
           WHERE table_schema = 'solitaire' AND table_name = 'deploy_meta'"""
    )
    if cur.fetchone() is None:
        return None
    cur.execute("SELECT version FROM solitaire.deploy_meta LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else None


def apply_schema(force: bool = False) -> bool:
    """Returns True when something was applied."""
    if not config.db_configured():
        log.warning("POSTGRES_* env missing — skipping schema bootstrap")
        return False
    with psycopg.connect(config.superuser_dsn()) as conn:
        with conn.cursor() as cur:
            if not force and _applied_version(cur) == SCHEMA_VERSION:
                log.info("schema already at version %s — nothing to apply", SCHEMA_VERSION)
                return False
            for role in ("postgrest_authenticator", "web_anon"):
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                if cur.fetchone() is None:
                    raise RuntimeError(
                        f"cluster role {role!r} missing — run load-log/deploy/01_create_roles.sql first"
                    )
            cur.execute(ROLE_SQL)
            for name in SCHEMA_FILES:
                log.info("applying %s", name)
                cur.execute((DEPLOY_DIR / name).read_text())
            cur.execute(
                """CREATE TABLE IF NOT EXISTS solitaire.deploy_meta (
                       version integer NOT NULL,
                       applied_at timestamptz NOT NULL DEFAULT now()
                   )"""
            )
            cur.execute("DELETE FROM solitaire.deploy_meta")
            cur.execute("INSERT INTO solitaire.deploy_meta (version) VALUES (%s)", (SCHEMA_VERSION,))
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("NOTIFY pgrst, 'reload schema'")
        conn.commit()
    log.info("schema converged to version %s", SCHEMA_VERSION)
    return True


def bootstrap_best_effort() -> None:
    try:
        apply_schema()
    except Exception:
        log.exception("schema bootstrap failed — serving anyway, will retry next boot")
