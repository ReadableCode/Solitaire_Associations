"""Sessions and login rate limiting, JWT transport.

The session cookie holds an HS256 JWT signed with the shared PostgREST secret,
with role=<schema>_user so the very same token is the Bearer token for
PostgREST calls (RLS keys on its user_id claim). Sessions outlive container
rebuilds (stateless), max age 30 days, and die when password_changed_at moves
past their issue time — password change, disable, and re-enable all revoke.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import jwt

from . import config
from .users import User, UserStore

LOCKOUT_MAX_FAILURES = 5
LOCKOUT_WINDOW_SECONDS = 15 * 60
LOCKOUT_DURATION_SECONDS = 15 * 60


class LoginRateLimiter:
    """5 failures / 15 min per username AND per client IP -> 15 min lockout."""

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def _keys(self, username: str, ip: str) -> tuple[str, ...]:
        keys = [f"user:{username.strip().lower()}"]
        if ip:
            keys.append(f"ip:{ip}")
        return tuple(keys)

    def locked_for(self, username: str, ip: str) -> int:
        now = time.monotonic()
        remaining = 0
        for key in self._keys(username, ip):
            until = self._locked_until.get(key, 0.0)
            if until > now:
                remaining = max(remaining, int(until - now))
        return remaining

    def record_failure(self, username: str, ip: str) -> None:
        now = time.monotonic()
        for key in self._keys(username, ip):
            window = [t for t in self._failures.get(key, []) if now - t < LOCKOUT_WINDOW_SECONDS]
            window.append(now)
            self._failures[key] = window
            if len(window) >= LOCKOUT_MAX_FAILURES:
                self._locked_until[key] = now + LOCKOUT_DURATION_SECONDS

    def record_success(self, username: str, ip: str) -> None:
        for key in self._keys(username, ip):
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


def client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def issue_token(user: User) -> str:
    now = datetime.now(UTC)
    payload = {
        "role": f"{config.APP_SCHEMA}_user",
        "user_id": user.id,
        "username": user.username,
        "app_role": user.role,
        "iat": now,
        "exp": now + timedelta(seconds=config.SESSION_MAX_AGE_SECONDS),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def validate_token(token: str, store: UserStore) -> tuple[User, str] | None:
    """Returns (user, token) when the session is still valid, else None."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    username = payload.get("username", "")
    issued_ts = payload.get("iat")
    if not username or issued_ts is None:
        return None
    user = store.get(username)
    if user is None or user.disabled:
        return None
    issued = datetime.fromtimestamp(float(issued_ts), tz=UTC)
    # Small grace: iat is second-granular, password_changed_at is not.
    if issued + timedelta(seconds=1) < user.password_changed_at:
        return None
    return user, token
