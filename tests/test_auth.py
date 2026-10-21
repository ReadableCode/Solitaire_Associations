"""Session token + login unit tests (no DB, no network — the store is
stubbed and postgrest_auth.login is faked). Tokens are crafted with the same
claim set postgrest_auth.mint_token produces."""

import time
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import postgrest_auth
import psycopg
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
    """A token shaped exactly like postgrest_auth.mint_token's payload."""
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


def test_login_shaped_token_validates():
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


# --- login ---------------------------------------------------------------------


def _fake_login(monkeypatch, outcome):
    seen = {}

    def fake(connect, schema, username, password, **kwargs):
        seen.update(schema=schema, username=username, password=password, **kwargs)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(auth.postgrest_auth, "login", fake)
    return seen


def test_login_success(monkeypatch):
    seen = _fake_login(monkeypatch, "the-token")
    assert auth.login("jason", "pw", "1.2.3.4") == "the-token"
    assert seen["schema"] == config.APP_SCHEMA
    assert seen["client_ip"] == "1.2.3.4"
    assert seen["ttl_hours"] == 720
    assert seen["jwt_secret"] == config.JWT_SECRET


def test_login_without_ip_passes_none(monkeypatch):
    seen = _fake_login(monkeypatch, "the-token")
    auth.login("jason", "pw", "")
    assert seen["client_ip"] is None


def test_login_maps_errors(monkeypatch):
    for outcome, status, detail in (
        (postgrest_auth.InvalidCredentials("x"), 401, "invalid username or password"),
        (postgrest_auth.LoginLocked(150), 429, "too many attempts — locked for 3 more minute(s)"),
        (psycopg.OperationalError("boom"), 503, "login unavailable"),
    ):
        _fake_login(monkeypatch, outcome)
        with pytest.raises(auth.LoginError) as exc:
            auth.login("jason", "pw", "1.2.3.4")
        assert exc.value.status_code == status
        assert exc.value.detail == detail
