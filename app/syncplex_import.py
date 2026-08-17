"""One-way, idempotent import of Sync_Plex accounts.

Reads a Sync_Plex users.json (mounted READ-ONLY into the container) and
inserts any account that doesn't already exist in solitaire.users, hash and
metadata intact — argon2id hashes verify unchanged because app/users.py uses
the same hasher as Sync_Plex. Existing rows are never touched, so this is
safe to leave enabled on every boot. Never writes to the Sync_Plex side.

Disabled unless SOLITAIRE_IMPORT_SYNCPLEX_USERS points at the file.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from . import config
from .users import ROLES, UserStore

log = logging.getLogger("solitaire.syncplex_import")


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def import_users(store: UserStore, path: str | Path) -> tuple[int, int]:
    """Returns (imported, skipped_existing)."""
    path = Path(path)
    payload = json.loads(path.read_text())
    entries = payload.get("users", [])
    imported = skipped = 0
    for entry in entries:
        username = str(entry.get("username", "")).strip().lower()
        password_hash = entry.get("password_hash", "")
        if not username or not password_hash:
            log.warning("skipping malformed entry: %r", entry.get("username"))
            continue
        if store.get(username) is not None:
            skipped += 1
            continue
        role = entry.get("role", "user")
        store.add_prehashed(
            username,
            password_hash,
            role=role if role in ROLES else "user",
            display_name=str(entry.get("display_name", "")),
            created_at=_parse_dt(entry.get("created_at")),
            disabled=bool(entry.get("disabled", False)),
        )
        imported += 1
        log.info("imported syncplex account %r (role=%s)", username, role)
    return imported, skipped


def import_best_effort(store: UserStore) -> None:
    path = config.SYNCPLEX_USERS_IMPORT
    if not path:
        return
    try:
        imported, skipped = import_users(store, path)
        log.info("syncplex import done: %d imported, %d already present", imported, skipped)
    except FileNotFoundError:
        log.warning("syncplex users file not found at %s — import skipped", path)
    except Exception:
        log.exception("syncplex account import failed — serving anyway")
