"""Sessions and JWT transport; password verification is delegated to the
shared postgrest-auth service.

Login: POST credentials to the service (auth.py:login_via_service), which
owns the KDF policy (argon2id, legacy bcrypt rehashed on login), the
per-username+per-IP lockout, and the no-enumeration timing defense — one
copy for every app instead of one per app. The token it returns is the
session cookie: an HS256 JWT signed with the shared PostgREST secret, with
role=<schema>_user so the very same token is the Bearer token for PostgREST
calls (RLS keys on its user_id claim). Sessions outlive container rebuilds
(stateless), max age 30 days, and die when password_changed_at moves past
their issue time — password change, disable, and re-enable all revoke.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import jwt

from . import config
from .users import User, UserStore


class AuthServiceError(Exception):
    """Login rejected or unreachable; carries the HTTP status to surface."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=config.HTTP_TIMEOUT)
    return _client


def client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def login_via_service(username: str, password: str, ip: str) -> str:
    """Exchange credentials for a session JWT at the shared auth service.

    The viewer's IP is forwarded so the service's per-IP lockout counts the
    browser, not this container. ttl_hours keeps the 30-day session policy
    this app has always had.
    """
    try:
        resp = _http().post(
            f"{config.AUTH_URL}/token",
            json={
                "schema": config.APP_SCHEMA,
                "username": username,
                "password": password,
                "ttl_hours": config.SESSION_MAX_AGE_SECONDS // 3600,
            },
            headers={"X-Forwarded-For": ip} if ip else {},
        )
    except httpx.HTTPError as exc:
        raise AuthServiceError(503, "login service unavailable") from exc
    if resp.status_code == 200:
        return resp.json()["token"]
    if resp.status_code == 401:
        raise AuthServiceError(401, "invalid username or password")
    if resp.status_code == 429:
        try:
            detail = resp.json().get("detail", "too many attempts")
        except ValueError:
            detail = "too many attempts"
        raise AuthServiceError(429, detail)
    raise AuthServiceError(503, "login service unavailable")


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
