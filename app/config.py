"""Environment resolution.

Runs in two places with the same code (no docker-only paths):
  - elitedesk container: env injected via env_file -> personal_credentials/personal.env
    plus the overrides in deploy/compose.elitedesk.yaml
  - bare local run: .env symlink -> ../personal_credentials/personal.env
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip().strip('"').strip("'")


APP_SCHEMA = _env("APP_SCHEMA", "solitaire")

POSTGRES_URL = _env("POSTGRES_URL")
POSTGRES_PORT = _env("POSTGRES_PORT", "5432")
POSTGRES_DB = _env("POSTGRES_DB", "apps")
POSTGRES_USER = _env("POSTGRES_USER")
POSTGRES_PASSWORD = _env("POSTGRES_PASSWORD")

POSTGREST_URL = _env("POSTGREST_URL").rstrip("/")
JWT_SECRET = _env("POSTGREST_JWT_SECRET") or _env("JWT_SECRET")

SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # matches Sync_Plex
SESSION_COOKIE = "solitaire_session"

HTTP_TIMEOUT = 10.0

# Path to a Sync_Plex users.json to import accounts from at startup.
# Empty = importer disabled. Enabled later via a compose change (git deploy).
SYNCPLEX_USERS_IMPORT = _env("SOLITAIRE_IMPORT_SYNCPLEX_USERS")

LEVELS_PATH = Path(_env("SOLITAIRE_LEVELS_PATH") or REPO_ROOT / "data" / "levels.json")


def superuser_dsn() -> str:
    return (
        f"host={POSTGRES_URL} port={POSTGRES_PORT} dbname={POSTGRES_DB} "
        f"user={POSTGRES_USER} password={POSTGRES_PASSWORD} connect_timeout=5"
    )


def db_configured() -> bool:
    return bool(POSTGRES_URL and POSTGRES_USER and POSTGRES_PASSWORD)
