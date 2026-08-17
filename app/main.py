"""Solitaire Associations — FastAPI app.

Browser -> this app (cookie session) -> PostgREST (Bearer JWT, RLS) -> apps DB.
The only direct-DB paths are the ones every sibling app has: startup schema
bootstrap, login verification against solitaire.users, and the account CLI.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import auth, bootstrap, config, icons, store, syncplex_import
from .users import UserStore, db_reachable

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("solitaire")

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_STATE_BYTES = 64 * 1024

users = UserStore()
limiter = auth.LoginRateLimiter()

_levels: list[dict] = []
_levels_by_id: dict[int, dict] = {}


def _load_levels() -> None:
    global _levels, _levels_by_id
    _levels = json.loads(config.LEVELS_PATH.read_text())
    _levels_by_id = {lvl["id"]: lvl for lvl in _levels}
    log.info("loaded %d levels", len(_levels))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _load_levels()
    icons.load()
    await run_in_threadpool(bootstrap.bootstrap_best_effort)
    await run_in_threadpool(syncplex_import.import_best_effort, users)
    yield


app = FastAPI(title="Solitaire Associations", lifespan=_lifespan)


# --- session plumbing ---------------------------------------------------------


def _session(request: Request) -> tuple:
    found = auth.validate_token(request.cookies.get(config.SESSION_COOKIE, ""), users)
    if found is None:
        raise HTTPException(status_code=401, detail="not logged in")
    return found


def _require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    if origin.split("://", 1)[-1].split("/", 1)[0] != host:
        raise HTTPException(status_code=403, detail="cross-origin request rejected")


def _cookie_secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto == "https"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# --- auth endpoints -----------------------------------------------------------


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def login(body: LoginBody, request: Request, response: Response):
    _require_same_origin(request)
    ip = auth.client_ip(request)
    username = body.username.strip().lower()
    locked = limiter.locked_for(username, ip)
    if locked:
        raise HTTPException(status_code=429, detail=f"too many attempts — locked for {locked}s")
    ok, detail = await run_in_threadpool(db_reachable)
    if not ok:
        log.error("login unavailable, db unreachable: %s", detail)
        raise HTTPException(status_code=503, detail="account database unavailable")
    user = await run_in_threadpool(users.verify, username, body.password)
    if user is None:
        limiter.record_failure(username, ip)
        raise HTTPException(status_code=401, detail="invalid username or password")
    limiter.record_success(username, ip)
    token = auth.issue_token(user)
    response.set_cookie(
        config.SESSION_COOKIE,
        token,
        max_age=config.SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
    )
    return {"username": user.username, "display_name": user.display_name, "role": user.role}


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    _require_same_origin(request)
    response.delete_cookie(config.SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request):
    user, _ = _session(request)
    return {"username": user.username, "display_name": user.display_name, "role": user.role}


# --- levels -------------------------------------------------------------------


@app.get("/api/levels")
async def levels_meta():
    return [
        {
            "id": lvl["id"],
            "difficulty": lvl["difficulty"],
            "move_budget": lvl["move_budget"],
            "cards": sum(len(c["words"]) for c in lvl["categories"]),
        }
        for lvl in _levels
    ]


@app.get("/api/levels/{level_id}")
async def level_detail(level_id: int, request: Request):
    _session(request)
    lvl = _levels_by_id.get(level_id)
    if lvl is None:
        raise HTTPException(status_code=404, detail="no such level")
    return {**lvl, "icons": icons.icons_for_level(lvl)}


# --- game state (all through PostgREST) ---------------------------------------


def _store_call(fn, *args):
    try:
        return fn(*args)
    except store.StoreError as exc:
        log.error("postgrest error: %s", exc.detail)
        raise HTTPException(status_code=502, detail="state store unavailable") from exc


@app.get("/api/state")
async def get_state(request: Request):
    user, token = _session(request)
    game = await run_in_threadpool(_store_call, store.get_game_state, token, user.id)
    progress = await run_in_threadpool(_store_call, store.get_progress, token, user.id)
    return {"game": game, "progress": progress}


@app.post("/api/state")
async def save_state(request: Request):
    user, token = _session(request)
    _require_same_origin(request)
    raw = await request.body()
    if len(raw) > MAX_STATE_BYTES:
        raise HTTPException(status_code=413, detail="state too large")
    try:
        payload = json.loads(raw)
        state = payload["state"]
        level_id = int(state["levelId"])
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="malformed state") from exc
    if level_id not in _levels_by_id:
        raise HTTPException(status_code=422, detail="unknown level")
    await run_in_threadpool(_store_call, store.put_game_state, token, user.id, state)
    return {"ok": True}


@app.delete("/api/state")
async def delete_state(request: Request):
    user, token = _session(request)
    _require_same_origin(request)
    await run_in_threadpool(_store_call, store.clear_game_state, token, user.id)
    return {"ok": True}


class CompleteBody(BaseModel):
    level_id: int
    won: bool
    moves_left: int = 0


@app.post("/api/complete")
async def complete_level(body: CompleteBody, request: Request):
    user, token = _session(request)
    _require_same_origin(request)
    if body.level_id not in _levels_by_id:
        raise HTTPException(status_code=422, detail="unknown level")

    def _apply():
        rows = _store_call(store.get_progress, token, user.id)
        existing = next((r for r in rows if r["level_id"] == body.level_id), None)
        attempts = (existing["attempts"] if existing else 0) + 1
        completed = bool(existing and existing["completed"]) or body.won
        best = existing.get("best_moves_left") if existing else None
        if body.won and (best is None or body.moves_left > best):
            best = body.moves_left
        row = {
            "level_id": body.level_id,
            "completed": completed,
            "attempts": attempts,
            "best_moves_left": best,
        }
        if body.won and not (existing and existing["completed"]):
            row["completed_at"] = "now()"
        _store_call(store.upsert_progress, token, user.id, row)
        _store_call(store.clear_game_state, token, user.id)

    await run_in_threadpool(_apply)
    return {"ok": True}


# --- health -------------------------------------------------------------------


@app.get("/api/health")
async def health():
    db_ok, db_detail = await run_in_threadpool(db_reachable)
    pg_ok, pg_detail = await run_in_threadpool(store.postgrest_reachable)
    body = {
        "status": "ok" if (db_ok and pg_ok) else "degraded",
        "db": db_detail if not db_ok else "ok",
        "postgrest": pg_detail if not pg_ok else "ok",
        "levels": len(_levels),
    }
    return JSONResponse(body, status_code=200 if body["status"] == "ok" else 503)


# --- static frontend ----------------------------------------------------------


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
