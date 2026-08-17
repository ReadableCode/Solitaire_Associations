// UI shell: login -> level map -> game. All persistence flows through the
// backend (cookie session), which talks to PostgREST. State is autosaved
// after every action; closing the tab flushes via sendBeacon.

import * as engine from "/static/engine.js?v=1";

const app = document.getElementById("app");

const S = {
  me: null,
  levels: [], // meta
  progress: new Map(), // level_id -> row
  game: null, // {state, history}
  level: null, // full level detail for current game
  key: null, // word -> slot index
  selected: null, // {type:'column'|'waste', index}
  hintedSlot: null,
  savedGame: null, // state loaded from server, not yet resumed
  dirty: false,
};

// --- tiny helpers ------------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue; // null/undefined = attribute absent
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

let toastTimer = null;
function toast(msg) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const t = el("div", { class: "toast show" }, msg);
  document.body.append(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 1800);
}

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...opts,
  });
  if (resp.status === 401 && path !== "/api/login") {
    S.me = null;
    renderLogin("session expired — log in again");
    throw new Error("unauthorized");
  }
  if (!resp.ok) {
    let detail = `error ${resp.status}`;
    try { detail = (await resp.json()).detail || detail; } catch { /* keep */ }
    throw new Error(detail);
  }
  return resp.status === 204 ? null : resp.json();
}

// --- autosave ----------------------------------------------------------------

let saveTimer = null;

function scheduleSave() {
  S.dirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushSave, 300);
}

async function flushSave() {
  if (!S.dirty || !S.game || S.game.state.status !== "playing") return;
  S.dirty = false;
  try {
    await api("/api/state", { method: "POST", body: JSON.stringify({ state: S.game.state }) });
  } catch (err) {
    S.dirty = true; // retry on next action
    console.warn("autosave failed:", err.message);
  }
}

window.addEventListener("pagehide", () => {
  if (S.dirty && S.game && S.game.state.status === "playing") {
    navigator.sendBeacon(
      "/api/state",
      new Blob([JSON.stringify({ state: S.game.state })], { type: "application/json" })
    );
    S.dirty = false;
  }
});

// --- login -------------------------------------------------------------------

function renderLogin(message = "") {
  const user = el("input", {
    placeholder: "username", autocomplete: "username",
    autocapitalize: "none", autocorrect: "off",
  });
  const pass = el("input", { placeholder: "password", type: "password", autocomplete: "current-password" });
  const error = el("div", { class: "login-error" }, message);
  const submit = async () => {
    error.textContent = "";
    try {
      S.me = await api("/api/login", {
        method: "POST",
        body: JSON.stringify({ username: user.value, password: pass.value }),
      });
      await loadHome();
    } catch (err) {
      error.textContent = err.message;
    }
  };
  pass.addEventListener("keydown", (e) => e.key === "Enter" && submit());
  app.replaceChildren(
    el("div", { class: "login-box" },
      el("div", { class: "screen-title" }, "Solitaire Associations"),
      el("div", { class: "screen-sub" }, "Sort the words. Find the connections."),
      user, pass, error,
      el("button", { class: "primary", onclick: submit }, "Log in")
    )
  );
  user.focus();
}

// --- home / level map --------------------------------------------------------

async function loadHome() {
  S.levels = await api("/api/levels");
  try {
    const stateResp = await api("/api/state");
    S.progress = new Map(stateResp.progress.map((r) => [r.level_id, r]));
    S.savedGame = stateResp.game ? stateResp.game.state : null;
  } catch (err) {
    // State store briefly unavailable: still let the user in — autosave
    // retries on every action, so nothing is lost once it comes back.
    S.progress = new Map();
    S.savedGame = null;
    toast("progress unavailable right now");
    console.warn("state load failed:", err.message);
  }
  S.game = null;
  renderHome();
}

function highestUnlocked() {
  let unlocked = 1;
  for (const lvl of S.levels) {
    if (S.progress.get(lvl.id)?.completed) unlocked = Math.max(unlocked, lvl.id + 1);
  }
  return unlocked;
}

function renderHome() {
  const unlocked = highestUnlocked();
  const cells = S.levels.map((lvl) => {
    const done = S.progress.get(lvl.id)?.completed;
    const locked = lvl.id > unlocked;
    return el("div", {
      class: `level-cell ${done ? "done" : ""} ${locked ? "locked" : ""} ${lvl.id === unlocked ? "current" : ""}`,
      onclick: () => !locked && startLevel(lvl.id),
    }, String(lvl.id));
  });
  const banner = S.savedGame
    ? el("div", { class: "continue-banner" },
        el("div", {}, `Level ${S.savedGame.levelId} in progress — ${S.savedGame.movesLeft} moves left`),
        el("button", { class: "primary", onclick: () => resumeSaved() }, "Continue"))
    : null;
  app.replaceChildren(
    el("div", { class: "topbar" },
      el("div", { class: "screen-title" }, "Solitaire Associations"),
      el("div", {},
        el("span", { class: "who" }, S.me.display_name || S.me.username), " ",
        el("button", {
          onclick: async () => { await api("/api/logout", { method: "POST" }); S.me = null; renderLogin(); },
        }, "Log out"))),
    banner,
    el("div", { class: "level-grid" }, cells)
  );
}

// --- game lifecycle ----------------------------------------------------------

async function startLevel(levelId, { fresh = false } = {}) {
  const level = await api(`/api/levels/${levelId}`);
  S.level = level;
  S.key = engine.answerKey(level);
  if (!fresh && S.savedGame && S.savedGame.levelId === levelId) {
    const resumed = engine.resumeGame(level, S.savedGame);
    if (resumed) {
      S.game = resumed;
      renderGame();
      return;
    }
  }
  S.game = engine.newGame(level, (Math.random() * 0xffffffff) >>> 0);
  S.savedGame = null;
  scheduleSave();
  renderGame();
}

function resumeSaved() {
  startLevel(S.savedGame.levelId);
}

async function finishLevel(won) {
  clearTimeout(saveTimer);
  S.dirty = false;
  try {
    await api("/api/complete", {
      method: "POST",
      body: JSON.stringify({
        level_id: S.game.state.levelId,
        won,
        moves_left: S.game.state.movesLeft,
      }),
    });
  } catch (err) {
    toast(`progress not saved: ${err.message}`);
  }
}

// --- game actions ------------------------------------------------------------

function guarded(fn) {
  try {
    fn();
    S.selected = null;
    S.hintedSlot = null;
    scheduleSave();
  } catch (err) {
    if (err instanceof engine.GameError) toast(err.message);
    else throw err;
  }
  afterAction();
}

function afterAction() {
  const status = S.game.state.status;
  renderGame();
  if (status !== "playing") {
    clearTimeout(saveTimer);
    S.dirty = false;
    finishLevel(status === "won");
  }
}

function onSelect(source) {
  const word = engine.accessibleWord(S.game.state, source);
  if (word == null) return;
  const same = S.selected && S.selected.type === source.type && S.selected.index === source.index;
  S.selected = same ? null : source;
  S.hintedSlot = null;
  renderGame();
}

function onColumnTap(index) {
  if (S.selected && !(S.selected.type === "column" && S.selected.index === index)) {
    guarded(() => engine.moveToColumn(S.game, S.selected, index));
  } else {
    onSelect({ type: "column", index });
  }
}

function onSlotTap(slotIndex) {
  if (!S.selected) {
    toast("pick a card first");
    return;
  }
  const source = S.selected;
  guarded(() => {
    const correct = engine.place(S.game, source, slotIndex, S.key);
    flashSlot(slotIndex, correct);
    if (!correct) toast("not that group — move used");
  });
}

function flashSlot(slotIndex, good) {
  requestAnimationFrame(() => {
    const node = document.querySelector(`[data-slot="${slotIndex}"]`);
    if (node) node.classList.add(good ? "flash-good" : "flash-bad");
  });
}

function onHint() {
  if (!S.selected) { toast("pick a card first"); return; }
  try {
    S.hintedSlot = engine.hint(S.game, S.selected, S.key);
    renderGame();
  } catch (err) {
    toast(err.message);
  }
}

function onJoker() {
  if (!S.selected) { toast("pick a card first"); return; }
  const source = S.selected;
  guarded(() => engine.joker(S.game, source, S.key));
}

function onUndo() {
  if (!engine.canUndo(S.game)) { toast("nothing to undo"); return; }
  engine.undo(S.game);
  S.selected = null;
  S.hintedSlot = null;
  scheduleSave();
  renderGame();
}

async function onQuitToMap() {
  await flushSave();
  await loadHome();
}

// --- game rendering ----------------------------------------------------------

function renderGame() {
  const st = S.game.state;
  const hud = el("div", { class: "hud" },
    el("button", { onclick: onQuitToMap }, "‹ Map"),
    el("div", { class: `moves ${st.movesLeft <= 10 ? "low" : ""}` }, `${st.movesLeft} moves`),
    el("div", { class: "tools" },
      el("button", { onclick: onHint, disabled: st.hintsLeft ? null : "" }, `Hint ${st.hintsLeft}`),
      el("button", { onclick: onJoker, disabled: st.jokersLeft ? null : "" }, `Joker ${st.jokersLeft}`),
      el("button", { onclick: onUndo, disabled: engine.canUndo(S.game) ? null : "" }, "Undo")));

  const slots = el("div", { class: "slots" },
    st.slots.map((slot, i) => {
      const complete = slot.placed.length === slot.total;
      return el("div", {
        class: `slot ${complete ? "complete" : ""} ${S.hintedSlot === i ? "hinted" : ""}`,
        "data-slot": String(i),
        onclick: () => !complete && onSlotTap(i),
      },
        el("div", { class: "slot-name" }, slot.name),
        el("div", { class: "slot-count" }, complete ? "✓ complete" : `${slot.placed.length} / ${slot.total}`));
    }));

  const columns = el("div", { class: "tableau" },
    st.columns.map((col, i) => {
      const isSel = S.selected?.type === "column" && S.selected.index === i;
      const cards = col.map((card, j) => {
        const top = j === col.length - 1;
        return el("div", {
          class: `card ${card.up ? "up" : "down"} ${top && isSel ? "selected" : ""}`,
          onclick: top ? (e) => { e.stopPropagation(); onColumnTap(i); } : undefined,
        }, card.up ? card.w : "");
      });
      return el("div", { class: "column", onclick: () => onColumnTap(i) },
        cards.length ? cards : el("div", { class: "column-empty" }, "empty"));
    }));

  const wasteTop = engine.topOfWaste(st);
  const wasteSel = S.selected?.type === "waste";
  const piles = el("div", { class: "piles" },
    el("div", { class: "pile" },
      el("div", {
        class: `stock-card ${st.stock.length ? "" : "empty"}`,
        onclick: () => guarded(() => engine.draw(S.game)),
      }, st.stock.length ? String(st.stock.length) : (st.waste.length ? "recycle" : "")),
      el("div", { class: "pile-label" }, "deck")),
    el("div", { class: "pile" },
      wasteTop
        ? el("div", {
            class: `card up ${wasteSel ? "selected" : ""}`,
            onclick: () => onSelect({ type: "waste" }),
          }, wasteTop)
        : el("div", { class: "column-empty" }, "waste"),
      el("div", { class: "pile-label" }, st.waste.length ? `waste (${st.waste.length})` : "waste")));

  const children = [hud, slots, columns, piles];

  if (st.status !== "playing") {
    const won = st.status === "won";
    children.push(el("div", { class: "overlay" },
      el("div", { class: "panel" },
        el("h2", {}, won ? "Level complete!" : "Out of moves"),
        el("div", { class: "detail" },
          won
            ? `Level ${st.levelId} cleared with ${st.movesLeft} moves to spare.`
            : `Level ${st.levelId} — ${st.wrongGuesses} wrong guesses cost you.`),
        won && S.levels.some((l) => l.id === st.levelId + 1)
          ? el("button", { class: "primary", onclick: () => startLevel(st.levelId + 1, { fresh: true }) }, "Next level")
          : null,
        won ? null : el("button", { class: "primary", onclick: () => startLevel(st.levelId, { fresh: true }) }, "Try again"),
        el("button", { onclick: onQuitToMap }, "Back to map"))));
  }

  app.replaceChildren(...children);
}

// --- boot --------------------------------------------------------------------

(async function boot() {
  try {
    S.me = await api("/api/me");
    await loadHome();
  } catch (err) {
    // 401 already rendered the login screen inside api(); anything else
    // must still leave the user a way forward — never a blank page.
    if (S.me !== null) {
      app.replaceChildren(
        el("div", { class: "login-box" },
          el("div", { class: "screen-title" }, "Solitaire Associations"),
          el("div", { class: "login-error" }, err.message),
          el("button", { class: "primary", onclick: () => location.reload() }, "Retry"))
      );
    } else if (!document.querySelector(".login-box")) {
      renderLogin();
    }
  }
})();
