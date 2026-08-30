"""Real-PostgREST round trip — red until the deployed stack matches.

Uses a throwaway account and logs in through the REAL shared auth service,
so this exercises the full production path: postgrest-auth verify (argon2id)
-> JWT claims -> session validation -> RLS -> game_state/progress tables.
Red until the argon2id-capable postgrest-auth is deployed.
"""

import uuid

import pytest

from app import auth, bootstrap, store as gstore
from app.users import UserStore, db_reachable


@pytest.fixture(scope="module")
def session():
    ok, detail = db_reachable()
    assert ok, f"database unreachable — red, not skipped: {detail}"
    bootstrap.apply_schema()
    reachable, detail = gstore.postgrest_reachable()
    assert reachable, f"postgrest unreachable — red, not skipped: {detail}"

    users = UserStore()
    username = f"ztest{uuid.uuid4().hex[:10]}"
    user = users.add(username, "postgrest-roundtrip-pw")
    try:
        token = auth.login_via_service(username, "postgrest-roundtrip-pw", ip="")
    except auth.AuthServiceError as exc:
        users.remove(username)
        raise AssertionError(f"auth service login failed — red, not skipped: {exc.detail}")
    assert auth.validate_token(token, users) is not None, "service token must validate in-app"
    yield user, token
    users.remove(username)  # cascades game_state/progress


def test_game_state_round_trip(session):
    user, token = session
    assert gstore.get_game_state(token, user.id) is None

    state = {"levelId": 1, "movesLeft": 100, "status": "playing", "columns": [], "stock": [], "waste": [], "slots": []}
    gstore.put_game_state(token, user.id, state)
    row = gstore.get_game_state(token, user.id)
    assert row is not None and row["state"]["movesLeft"] == 100

    state["movesLeft"] = 99
    gstore.put_game_state(token, user.id, state)
    assert gstore.get_game_state(token, user.id)["state"]["movesLeft"] == 99

    gstore.clear_game_state(token, user.id)
    assert gstore.get_game_state(token, user.id) is None


def test_progress_upsert(session):
    user, token = session
    gstore.upsert_progress(token, user.id, {"level_id": 1, "completed": True, "attempts": 1, "best_moves_left": 42})
    gstore.upsert_progress(token, user.id, {"level_id": 1, "completed": True, "attempts": 2, "best_moves_left": 50})
    rows = gstore.get_progress(token, user.id)
    assert len(rows) == 1
    assert rows[0]["attempts"] == 2 and rows[0]["best_moves_left"] == 50


def test_rls_blocks_other_users(session):
    user, token = session
    state = {"levelId": 1, "movesLeft": 10}
    gstore.put_game_state(token, user.id, state)

    with pytest.raises(gstore.StoreError):
        # writing a row for a DIFFERENT user_id must be rejected by RLS
        gstore.put_game_state(token, str(uuid.uuid4()), state)

    gstore.clear_game_state(token, user.id)
