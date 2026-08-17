"""User accounts, backed by solitaire.users.

argon2id hashes, lowercase usernames, dummy-hash timing safety, transparent
rehash, password_changed_at bumped on password/enable/disable so existing
sessions die. The table is REVOKEd from the PostgREST roles; only this
process (superuser) reads it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from . import config

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
MIN_PASSWORD_LENGTH = 10

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("solitaire-no-such-user")

_CACHE_TTL_SECONDS = 30.0


@dataclass
class User:
    id: str
    username: str
    password_hash: str
    role: str
    display_name: str
    disabled: bool
    created_at: datetime
    password_changed_at: datetime


_COLS = "id, username, password_hash, role, display_name, disabled, created_at, password_changed_at"


def _row_to_user(row) -> User:
    return User(str(row[0]), row[1], row[2], row[3], row[4], row[5], row[6], row[7])


class UserStore:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, User | None]] = {}

    def _conn(self):
        return psycopg.connect(config.superuser_dsn())

    def get(self, username: str) -> User | None:
        username = username.strip().lower()
        now = time.monotonic()
        hit = self._cache.get(username)
        if hit and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLS} FROM {config.APP_SCHEMA}.users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
        user = _row_to_user(row) if row else None
        self._cache[username] = (now, user)
        return user

    def _invalidate(self, username: str) -> None:
        self._cache.pop(username.strip().lower(), None)

    def list(self) -> list[User]:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {_COLS} FROM {config.APP_SCHEMA}.users ORDER BY username")
            return [_row_to_user(r) for r in cur.fetchall()]

    def verify(self, username: str, password: str) -> User | None:
        """Timing-safe: always runs one argon2 verification."""
        user = self.get(username)
        try:
            _hasher.verify(user.password_hash if user else _DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            return None
        if user is None or user.disabled:
            return None
        if _hasher.check_needs_rehash(user.password_hash):
            self._set_hash_only(user.username, _hasher.hash(password))
        return user

    def add(
        self,
        username: str,
        password: str,
        role: str = ROLE_USER,
        display_name: str = "",
    ) -> User:
        username = username.strip().lower()
        if not _USERNAME_RE.match(username):
            raise ValueError(f"invalid username: {username!r}")
        if role not in ROLES:
            raise ValueError(f"invalid role: {role!r}")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
        return self.add_prehashed(username, _hasher.hash(password), role, display_name)

    def add_prehashed(
        self,
        username: str,
        password_hash: str,
        role: str = ROLE_USER,
        display_name: str = "",
        created_at: datetime | None = None,
        disabled: bool = False,
    ) -> User:
        """Insert with an already-computed argon2 hash."""
        username = username.strip().lower()
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {config.APP_SCHEMA}.users
                    (username, password_hash, role, display_name, disabled, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING {_COLS}""",
                (
                    username,
                    password_hash,
                    role,
                    display_name,
                    disabled,
                    created_at or datetime.now(UTC),
                ),
            )
            row = cur.fetchone()
        self._invalidate(username)
        return _row_to_user(row)

    def _set_hash_only(self, username: str, password_hash: str) -> None:
        """Transparent rehash: does NOT bump password_changed_at."""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {config.APP_SCHEMA}.users SET password_hash = %s WHERE username = %s",
                (password_hash, username),
            )
        self._invalidate(username)

    def set_password(self, username: str, password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {config.APP_SCHEMA}.users
                    SET password_hash = %s, password_changed_at = now()
                    WHERE username = %s""",
                (_hasher.hash(password), username.strip().lower()),
            )
            if cur.rowcount == 0:
                raise ValueError(f"no such user: {username}")
        self._invalidate(username)

    def set_disabled(self, username: str, disabled: bool) -> None:
        # Bump password_changed_at in both directions: disabling kills sessions,
        # and re-enabling must not resurrect pre-disable ones.
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {config.APP_SCHEMA}.users
                    SET disabled = %s, password_changed_at = now()
                    WHERE username = %s""",
                (disabled, username.strip().lower()),
            )
            if cur.rowcount == 0:
                raise ValueError(f"no such user: {username}")
        self._invalidate(username)

    def set_role(self, username: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"invalid role: {role!r}")
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {config.APP_SCHEMA}.users SET role = %s WHERE username = %s",
                (role, username.strip().lower()),
            )
            if cur.rowcount == 0:
                raise ValueError(f"no such user: {username}")
        self._invalidate(username)

    def remove(self, username: str) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {config.APP_SCHEMA}.users WHERE username = %s",
                (username.strip().lower(),),
            )
            if cur.rowcount == 0:
                raise ValueError(f"no such user: {username}")
        self._invalidate(username)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def db_reachable() -> tuple[bool, str]:
    if not config.db_configured():
        return False, "POSTGRES_* env not configured"
    try:
        with psycopg.connect(config.superuser_dsn()) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True, "ok"
    except psycopg.Error as exc:
        return False, str(exc).strip()
