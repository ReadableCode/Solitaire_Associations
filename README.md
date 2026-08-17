# Solitaire Associations

Web clone of the iPhone word-solitaire game: word cards are dealt into
tableau columns plus a draw deck, and every card belongs to exactly one of
four categories. Place accessible cards into the right category before the
move budget runs out. Hints reveal a card's category, jokers auto-place a
card, undo refunds the last move.

## Architecture

- **FastAPI** app serving a vanilla-JS frontend (`app/static/`), port 8790.
- Game logic is a pure ES module (`app/static/engine.js`) — deterministic
  given (level, seed), fully serializable, tested under node.
- **Own schema `solitaire`** in the shared `apps` Postgres. All game data
  goes through the shared **PostgREST** (`Accept-Profile: solitaire`, RLS
  keyed on the JWT `user_id` claim). The only direct-DB paths are the same
  ones every sibling app has: startup bootstrap, login verification, and the
  account CLI.
- **Accounts work exactly like Sync_Plex**: argon2id hashes, lowercase
  usernames, timing-safe verify, 5-failure/15-minute lockout per user and
  per IP, 30-day sessions revoked by `password_changed_at`, no self-signup.
  The session cookie holds an HS256 JWT signed with the shared PostgREST
  secret (`role: solitaire_user`), forwarded verbatim as the PostgREST
  Bearer token.
- Game state autosaves after every move (debounced, `sendBeacon` on tab
  close). There is no save/load UI — reload resumes exactly.

## Schema bootstrap

`app/bootstrap.py` converges the schema at startup (Book-Bot pattern):
version-gated via `solitaire.deploy_meta`, additive-only SQL in `deploy/`,
`NOTIFY pgrst, 'reload schema'` at the end. The docker entrypoint is inert.
Manual run: `uv run python scripts/init_db.py [--force]`.

## Accounts

```
docker compose -f docker_compose_projects.yaml exec solitaire-web \
    python -m app.users_cli <add|list|passwd|role|disable|enable|remove> ...
```

Sync_Plex account import: set `SOLITAIRE_IMPORT_SYNCPLEX_USERS` to the
mounted `users.json` path (see `deploy/compose.elitedesk.yaml`). Idempotent —
existing rows are never touched; the mount is read-only.

## Deploy

Standard federation: `deploy/compose.elitedesk.yaml` is `include:`d by
`Docker/docker_compose_projects.yaml`; the schema is listed in
`PGRST_DB_SCHEMAS`; `git_pull.sh` maps `Solitaire_Associations` →
`solitaire-web`; SWAG proxies `solitaire.tinkernet.me` → `solitaire_web:8790`.
Push to master and the elitedesk cron does the rest.

## Card art

`data/word_emoji.json` maps every level word to an emoji (or null).
`scripts/fetch_emoji_assets.py` syncs the referenced Noto Emoji SVGs into
`app/static/img/emoji/` (committed; see `licenses/`). The backend injects
per-level `icons` ids; unmapped words get a procedural monogram medallion
client-side. After editing levels or the mapping, rerun the fetch script —
`tests/test_icons.py` fails if any mapped glyph is missing from disk.

## Tests

```
uv run pytest                      # python: auth, levels, real-DB account roundtrip
node --test tests/test_engine.mjs  # engine
```

DB-backed tests hit the real database and go red when it is unreachable —
never skipped.
