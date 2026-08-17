"""Real-database tests — hit the actual shared Postgres, red if unreachable.

Creates throwaway accounts prefixed _test_ and removes them. Runs the same
version-gated bootstrap the app runs at startup (no-op once converged).
"""

import uuid

import pytest

from app import bootstrap, config
from app.users import UserStore, db_reachable, hash_password


@pytest.fixture(scope="module")
def store():
    ok, detail = db_reachable()
    assert ok, f"database unreachable — this test must be red, not skipped: {detail}"
    bootstrap.apply_schema()
    return UserStore()


@pytest.fixture()
def temp_username(store):
    username = f"ztest{uuid.uuid4().hex[:10]}"
    yield username
    try:
        store.remove(username)
    except ValueError:
        pass


def test_account_lifecycle(store, temp_username):
    user = store.add(temp_username, "correct-horse-battery", display_name="Temp")
    assert user.username == temp_username
    assert user.role == "user"

    assert store.verify(temp_username, "correct-horse-battery") is not None
    assert store.verify(temp_username, "wrong-password-here") is None
    assert store.verify("no-such-user-at-all", "whatever-password") is None

    before = store.get(temp_username).password_changed_at
    store.set_password(temp_username, "another-good-password")
    after = store.get(temp_username).password_changed_at
    assert after > before, "password change must bump password_changed_at"
    assert store.verify(temp_username, "another-good-password") is not None

    store.set_disabled(temp_username, True)
    assert store.verify(temp_username, "another-good-password") is None
    store.set_disabled(temp_username, False)
    assert store.verify(temp_username, "another-good-password") is not None


def test_validation_rules(store):
    with pytest.raises(ValueError):
        store.add("Bad Username!", "long-enough-password")
    with pytest.raises(ValueError):
        store.add("ztestshortpw", "short")


def test_add_prehashed_verifies_unchanged(store, temp_username):
    """An already-computed argon2id hash must verify as-is once stored."""
    password = "prehashed-password-1"
    store.add_prehashed(
        temp_username,
        hash_password(password),
        role="admin",
        display_name="Prehashed",
    )

    user = store.verify(temp_username, password)
    assert user is not None, "stored argon2id hash must verify as-is"
    assert user.role == "admin"
    assert user.display_name == "Prehashed"


def test_users_table_hidden_from_postgrest_roles(store):
    """The credentials table must not be readable by the PostgREST roles."""
    import psycopg

    with psycopg.connect(config.superuser_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT grantee, privilege_type FROM information_schema.role_table_grants
               WHERE table_schema = %s AND table_name = 'users'
                 AND grantee IN ('solitaire_user', 'web_anon')""",
            (config.APP_SCHEMA,),
        )
        assert cur.fetchall() == []
