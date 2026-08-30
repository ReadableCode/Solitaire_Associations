"""Session token + service-login unit tests (no DB, no network — the store
is stubbed and the auth service is a mock transport). Tokens are crafted
with the same claim set postgrest-auth mints."""

import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt as pyjwt
import pytest

from app import auth, config
from app.users import User


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(config, "JWT_SECRET", "test-secret-not-a-real-one")


def make_user(**overrides) -> User:
    base = dict(
        id="4d5cc9c2-0000-0000-0000-000000000001",
        username="jason",
        password_hash="x",
        role="admin",
        display_name="Jason",
        disabled=False,
        created_at=datetime.now(UTC) - timedelta(days=90),
        password_changed_at=datetime.now(UTC) - timedelta(days=30),
    )
    base.update(overrides)
    return User(**base)


def mint_token(user: User, **overrides) -> str:
    """A token shaped exactly like postgrest-auth's /token payload."""
    now = datetime.now(UTC)
    payload = {
        "role": f"{config.APP_SCHEMA}_user",
        "user_id": user.id,
        "username": user.username,
        "app_role": user.role,
        "iat": now,
        "exp": now + timedelta(seconds=config.SESSION_MAX_AGE_SECONDS),
    }
    payload.update(overrides)
    return pyjwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


class StubStore:
    def __init__(self, user):
        self.user = user

    def get(self, username):
        return self.user if self.user and username == self.user.username else None


def test_service_shaped_token_validates():
    user = make_user()
    token = mint_token(user)
    payload = pyjwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    assert payload["role"] == f"{config.APP_SCHEMA}_user"
    assert payload["user_id"] == user.id
    assert payload["app_role"] == "admin"

    found = auth.validate_token(token, StubStore(user))
    assert found is not None
    assert found[0].username == "jason"
    assert found[1] == token


def test_password_change_revokes_session():
    user = make_user()
    token = mint_token(user)
    changed = make_user(password_changed_at=datetime.now(UTC) + timedelta(seconds=5))
    assert auth.validate_token(token, StubStore(changed)) is None


def test_disabled_and_unknown_users_rejected():
    user = make_user()
    token = mint_token(user)
    assert auth.validate_token(token, StubStore(make_user(disabled=True))) is None
    assert auth.validate_token(token, StubStore(None)) is None


def test_garbage_tokens_rejected():
    user = make_user()
    store = StubStore(user)
    assert auth.validate_token("", store) is None
    assert auth.validate_token("not-a-jwt", store) is None
    forged = pyjwt.encode({"username": "jason", "iat": time.time()}, "wrong-secret", algorithm="HS256")
    assert auth.validate_token(forged, store) is None


def test_expired_token_rejected():
    user = make_user()
    now = datetime.now(UTC)
    token = mint_token(user, iat=now - timedelta(days=40), exp=now - timedelta(days=10))
    assert auth.validate_token(token, StubStore(user)) is None


# --- login_via_service --------------------------------------------------------


def _service(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(auth, "_http", lambda: client)


def test_login_via_service_success(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        seen["xff"] = request.headers.get("X-Forwarded-For")
        return httpx.Response(200, json={"token": "the-token"})

    _service(monkeypatch, handler)
    assert auth.login_via_service("jason", "pw", "1.2.3.4") == "the-token"
    assert seen["url"] == f"{config.AUTH_URL}/token"
    assert seen["xff"] == "1.2.3.4"
    assert b'"schema"' in seen["body"] and b'"ttl_hours"' in seen["body"]


def test_login_via_service_maps_statuses(monkeypatch):
    for status, detail_out in (
        (401, "invalid username or password"),
        (429, "too many attempts — locked for 3 more minute(s)"),
        (503, "login service unavailable"),
        (500, "login service unavailable"),
    ):
        _service(monkeypatch, lambda r, s=status: httpx.Response(
            s, json={"detail": "too many attempts — locked for 3 more minute(s)"}))
        with pytest.raises(auth.AuthServiceError) as exc:
            auth.login_via_service("jason", "pw", "1.2.3.4")
        assert exc.value.status_code == (status if status in (401, 429) else 503)
        assert exc.value.detail == detail_out


def test_login_via_service_unreachable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("boom")

    _service(monkeypatch, handler)
    with pytest.raises(auth.AuthServiceError) as exc:
        auth.login_via_service("jason", "pw", "")
    assert exc.value.status_code == 503
