# Solitaire Associations

Web clone of the iPhone word-solitaire game: word cards are dealt into
tableau columns plus a draw deck, and every card belongs to exactly one of
four categories. Place accessible cards into the right category before the
move budget runs out. Hints reveal a card's category, jokers auto-place a
card, undo refunds the last move.

Two mechanics from the original ride on top of that core:

- **Gold cards** — categories start hidden ("?" slots). Each has a gold
  "ace" card buried in the first four tableau columns; uncovering it flies
  it to its slot for free and reveals the group. Words can only be placed
  into revealed slots.
- **Lock & key** — from level 61, the top card of a column past the gold
  columns is padlocked and unplayable. Its key is a normal word card
  (marked 🔑) elsewhere in the deal; correctly placing the key breaks the
  lock.

Both are dealt so they cost a perfect player zero extra moves (golds are
auto-collected free and chained under already-placeable covers; the key's
placement is a scoring move it needed anyway), which keeps the budget
formula and the winnability guarantees below untouched.

## Architecture

- **FastAPI** app serving a vanilla-JS frontend (`app/static/`), port 8790.
- Game logic is a pure ES module (`app/static/engine.js`) — deterministic
  given (level, seed), fully serializable, tested under node.
- **Own schema `solitaire`** in the shared `apps` Postgres. All game data
  goes through the shared **PostgREST** (`Accept-Profile: solitaire`, RLS
  keyed on the JWT `user_id` claim). The only direct-DB paths are the same
  ones every sibling app has: startup bootstrap, per-request session checks,
  and the account CLI.
- **Accounts**: argon2id hashes, lowercase usernames, no self-signup.
  Password verification is delegated to the shared **postgrest-auth**
  service (timing-safe, 5-failure/15-minute lockout per user and per IP);
  30-day sessions are revoked by `password_changed_at`.
  The session cookie holds the HS256 JWT the service mints with the shared
  PostgREST secret (`role: solitaire_user`), forwarded verbatim as the
  PostgREST Bearer token.
- Game state autosaves after every move (debounced, `sendBeacon` on tab
  close). There is no save/load UI — reload resumes exactly.

## Difficulty

150 levels. Card count steps up by tier (16 / 20 / 20 / 24 / 28 cards at
difficulty 1-5), but the *pressure* ramps on level id, so a level also gets
harder within its tier rather than only at the 31/61/91/121 boundaries.
`curve()` in `gen_levels.py` is the single knob; it bakes three fields into
each level in `data/levels.json`:

| field | level 1 | level 150 |
| --- | --- | --- |
| `move_budget` | 3.5x a perfect solve | 1.27x |
| `tableau_frac` | 60% of the deck dealt face-down | 90% |
| `hints` / `jokers` | 3 / 1 | 0 / 0 |
| `locks` | 0 (first lock at level 61, two from 91) | 2 |

Every level also carries `golds: true`; the engine caps `locks` at
columns − 4 so locks never collide with the gold columns.

A perfect solve is one placement per card plus one draw per stock card, so
the budget is never below it — `tests/test_engine.mjs` plays all 150 levels
with full knowledge on 5 seeds each and asserts every one is winnable.
`tests/test_levels.py` asserts the curve is monotonic: no level may offer
more slack, deeper hints, or a shallower tableau than the level before it.

Re-tune by editing `curve()` and running `python3 gen_levels.py`, which
rewrites `data/levels.json` deterministically from the quad bank above it.
`engine.js` falls back to the old flat values (60% / 3 / 1, no golds, no
locks) for any level missing the fields, and old saved games without the
gold/lock fields still resume.

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

Accounts are created only through that CLI. There is no self-signup and no
import path: this app never reads another app's storage.

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

## Reference game and scope

The game being emulated is Solitaire Associations Journey by Hitapps Games
(App Store id 6748950306). Implemented: core tableau/stock/categories, hints,
jokers, undo, move budget, gold-border cards that reveal hidden categories
(`golds: true`, budget-neutral deal), lock and key cards (`locks` knob, level
61+, key is a normal word card). Deliberately not implemented: coins, lives,
ads, extra-move purchases, events, daily challenges, journey map theming, and
wild multi-category cards (they would break the save-state word-multiset
validation). Gameplay only, by decision.

Auth is argon2id in-app (not the bcrypt auth service): the app verifies and
mints its own HS256 JWT with `POSTGREST_JWT_SECRET`, role `solitaire_user`.
Accounts come only from the users CLI; no importer from any other app's data
will be reintroduced.
