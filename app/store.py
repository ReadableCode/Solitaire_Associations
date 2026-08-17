"""Game-state persistence — every read/write goes through PostgREST.

Same client shape as Book-Bot app/store.py / load-log api_client.py: schema
pinned per-request with Accept-Profile/Content-Profile, the caller's session
JWT forwarded verbatim as the Bearer token so RLS scopes every query.
"""

from __future__ import annotations

import httpx

from . import config


class StoreError(Exception):
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


def _headers(token: str, write: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept-Profile": config.APP_SCHEMA,
    }
    if write:
        headers["Content-Profile"] = config.APP_SCHEMA
        headers["Content-Type"] = "application/json"
    return headers


def _check(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        raise StoreError(resp.status_code, f"postgrest {resp.status_code}: {resp.text[:300]}")


def get_game_state(token: str, user_id: str) -> dict | None:
    resp = _http().get(
        f"{config.POSTGREST_URL}/game_state",
        params={"user_id": f"eq.{user_id}", "select": "state,updated_at"},
        headers=_headers(token),
    )
    _check(resp)
    rows = resp.json()
    return rows[0] if rows else None


def put_game_state(token: str, user_id: str, state: dict) -> None:
    resp = _http().post(
        f"{config.POSTGREST_URL}/game_state",
        params={"on_conflict": "user_id"},
        headers={**_headers(token, write=True), "Prefer": "resolution=merge-duplicates,return=minimal"},
        json={"user_id": user_id, "state": state, "updated_at": "now()"},
    )
    _check(resp)


def clear_game_state(token: str, user_id: str) -> None:
    resp = _http().delete(
        f"{config.POSTGREST_URL}/game_state",
        params={"user_id": f"eq.{user_id}"},
        headers=_headers(token, write=True),
    )
    _check(resp)


def get_progress(token: str, user_id: str) -> list[dict]:
    resp = _http().get(
        f"{config.POSTGREST_URL}/progress",
        params={
            "user_id": f"eq.{user_id}",
            "select": "level_id,completed,attempts,best_moves_left,completed_at",
            "order": "level_id.asc",
        },
        headers=_headers(token),
    )
    _check(resp)
    return resp.json()


def upsert_progress(token: str, user_id: str, row: dict) -> None:
    resp = _http().post(
        f"{config.POSTGREST_URL}/progress",
        params={"on_conflict": "user_id,level_id"},
        headers={**_headers(token, write=True), "Prefer": "resolution=merge-duplicates,return=minimal"},
        json={"user_id": user_id, **row},
    )
    _check(resp)


def postgrest_reachable() -> tuple[bool, str]:
    try:
        resp = _http().get(f"{config.POSTGREST_URL}/", headers={"Accept-Profile": config.APP_SCHEMA})
        return resp.status_code < 500, f"http {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, str(exc)
