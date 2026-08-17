"""Session token + rate limiter unit tests (no DB — store is stubbed)."""

import time
from datetime import UTC, datetime, timedelta

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


class StubStore:
    def __init__(self, user):
        self.user = user

    def get(self, username):
        return self.user if self.user and username == self.user.username else None


def test_token_round_trip_carries_postgrest_claims():
    user = make_user()
    token = auth.issue_token(user)
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
    token = auth.issue_token(user)
    changed = make_user(password_changed_at=datetime.now(UTC) + timedelta(seconds=5))
    assert auth.validate_token(token, StubStore(changed)) is None


def test_disabled_and_unknown_users_rejected():
    user = make_user()
    token = auth.issue_token(user)
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
    token = pyjwt.encode(
        {
            "role": f"{config.APP_SCHEMA}_user",
            "user_id": user.id,
            "username": user.username,
            "iat": now - timedelta(days=40),
            "exp": now - timedelta(days=10),
        },
        config.JWT_SECRET,
        algorithm="HS256",
    )
    assert auth.validate_token(token, StubStore(user)) is None


def test_lockout_after_five_failures_per_user_and_ip():
    limiter = auth.LoginRateLimiter()
    for _ in range(5):
        limiter.record_failure("jason", "1.2.3.4")
    assert limiter.locked_for("jason", "1.2.3.4") > 0
    assert limiter.locked_for("jason", "9.9.9.9") > 0, "username dimension locks"
    assert limiter.locked_for("other", "1.2.3.4") > 0, "ip dimension locks"
    assert limiter.locked_for("other", "9.9.9.9") == 0

    limiter.record_success("jason", "1.2.3.4")
    assert limiter.locked_for("jason", "1.2.3.4") == 0
