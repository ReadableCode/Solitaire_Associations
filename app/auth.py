"""Sessions and JWT transport; password verification runs in-process
through the postgrest-auth library.

Login: auth.login checks the password against solitaire.users over the
superuser connection with postgrest_auth.login, which owns the KDF policy
(argon2id, legacy bcrypt rehashed on login), the per-username+per-IP lockout,
and the no-enumeration timing defense. The token it returns is the session
cookie: an HS256 JWT signed with the shared PostgREST secret, with
role=<schema>_user so the very same token is the Bearer token for PostgREST
calls (RLS keys on its user_id claim). Sessions outlive container rebuilds
(stateless), max age 30 days, and die when password_changed_at moves past
their issue time — password change, disable, and re-enable all revoke.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import postgrest_auth
import psycopg

from . import config
from .users import User, UserStore


class LoginError(Exception):
    """Login rejected or unavailable; carries the HTTP status to surface."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def client_ip(request) -> str:
    return postgrest_auth.client_ip(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else None,
    )


def login(username: str, password: str, ip: str) -> str:
    """Exchange credentials for a session JWT.

    The viewer's IP feeds the per-IP lockout. ttl_hours keeps the 30-day
    session policy this app has always had.
    """
    try:
        return postgrest_auth.login(
            lambda: psycopg.connect(config.superuser_dsn()),
            config.APP_SCHEMA,
            username,
            password,
            jwt_secret=config.JWT_SECRET,
            client_ip=ip or None,
            ttl_hours=config.SESSION_MAX_AGE_SECONDS // 3600,
        )
    except postgrest_auth.InvalidCredentials:
        raise LoginError(401, "invalid username or password") from None
    except postgrest_auth.LoginLocked as exc:
        raise LoginError(429, str(exc)) from None
    except psycopg.Error as exc:
        raise LoginError(503, "login unavailable") from exc


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
