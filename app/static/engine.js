// Pure game engine for word-association solitaire. No DOM, no fetch —
// deterministic given (level, seed), fully serializable, testable in node.
//
// Mechanics (mirrors the iPhone game):
//   - Level = 4 categories; every word belongs to exactly one.
//   - Cards are dealt into tableau columns (only the top card of a column is
//     accessible; buried cards are face-down) plus a face-down stock.
//   - Actions costing 1 move: draw from stock (recycles waste when empty),
//     repositioning a card between columns, and every category placement —
//     wrong placements bounce back but still consume the move.
//   - Hints reveal a card's category (no move). Jokers auto-place a card
//     correctly (no move). Undo reverts the last move and refunds it.
//   - Gold cards (level.golds): categories start hidden; each has a gold
//     "ace" card buried in the tableau that flies to its slot for free the
//     moment it is uncovered. Words can only be placed into revealed slots.
//   - Locks (level.locks): the top card of a column past the four gold
//     columns is padlocked — unplayable until its key card (a normal word
//     somewhere reachable) is correctly placed, which breaks the lock.
//   - Both are dealt so a perfect solve still costs one placement per card
//     plus one draw per stock card: golds are free and auto-collected, and
//     the key's placement is a scoring move it needed anyway.
//   - Win: all cards sorted. Lose: move budget exhausted first.

export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function shuffled(items, rng) {
  const arr = items.slice();
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

function columnsForDifficulty(difficulty) {
  if (difficulty <= 2) return 4;
  if (difficulty === 3) return 5;
  return 6;
}

// Per-level difficulty knobs, set by gen_levels.py. The fallbacks are the
// old flat values, so a level authored before the curve still plays.
const DEFAULT_TABLEAU_FRAC = 0.6;
const DEFAULT_HINTS = 3;
const DEFAULT_JOKERS = 1;

function knob(level, name, fallback) {
  const v = level[name];
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

export function answerKey(level) {
  const key = new Map();
  level.categories.forEach((cat, i) => cat.words.forEach((w) => key.set(w, i)));
  return key;
}

export function createGame(level, seed) {
  const rng = mulberry32(seed);
  const allWords = level.categories.flatMap((c) => c.words);
  const deck = shuffled(allWords, rng);
  const numCols = columnsForDifficulty(level.difficulty);
  const tableauCount = Math.ceil(
    deck.length * knob(level, "tableau_frac", DEFAULT_TABLEAU_FRAC)
  );
  const columns = Array.from({ length: numCols }, () => []);
  for (let i = 0; i < tableauCount; i++) {
    columns[i % numCols].push({ w: deck[i], up: false });
  }
  const useGolds = level.golds === true;
  const state = {
    levelId: level.id,
    seed,
    difficulty: level.difficulty,
    moveBudget: level.move_budget,
    movesLeft: level.move_budget,
    hintsLeft: knob(level, "hints", DEFAULT_HINTS),
    jokersLeft: knob(level, "jokers", DEFAULT_JOKERS),
    wrongGuesses: 0,
    status: "playing",
    slots: level.categories.map((c) => ({
      name: c.name,
      total: c.words.length,
      placed: [],
      revealed: !useGolds,
    })),
    columns,
    stock: deck.slice(tableauCount),
    waste: [],
    locks: {},
  };
  if (useGolds) dealGolds(state, level, rng);
  dealLocks(state, level, rng);
  state.columns.forEach((col) => {
    if (col.length) col[col.length - 1].up = true;
  });
  collectGolds(state);
  return state;
}

// Bury the four gold "ace" cards so a knowing player uncovers them without
// a single wasted move: gold i sits in column i under exactly i cards from
// the previously-revealed category, so reveals chain from the first (which
// starts on top) with only placements that score anyway.
function dealGolds(state, level, rng) {
  const catOrder = shuffled([0, 1, 2, 3], rng);
  const catOf = answerKey(level);
  const reserved = new Set(); // cards already arranged as gold cover
  for (let i = 0; i < 4; i++) {
    const col = state.columns[i];
    const cover = Math.min(i, col.length);
    for (let d = 0; d < cover; d++) {
      const target = col[col.length - 1 - d];
      if (catOf.get(target.w) !== catOrder[i - 1]) {
        swapIn(state, target, catOrder[i - 1], catOf, reserved);
      }
      reserved.add(target);
    }
    col.splice(col.length - cover, 0, { gold: true, cat: catOrder[i], up: false });
  }
}

// Swap a word of the wanted category into `target` from the stock or from an
// unarranged tableau position. Always succeeds: a cover needs at most 3 words
// of one category and every category has at least 4.
function swapIn(state, target, wantCat, catOf, reserved) {
  for (let i = 0; i < state.stock.length; i++) {
    if (catOf.get(state.stock[i]) === wantCat) {
      [state.stock[i], target.w] = [target.w, state.stock[i]];
      return;
    }
  }
  for (const col of state.columns) {
    for (const card of col) {
      if (card !== target && !card.gold && !reserved.has(card) &&
          catOf.get(card.w) === wantCat) {
        [card.w, target.w] = [target.w, card.w];
        return;
      }
    }
  }
  throw new Error("no cover card available for gold deal");
}

// Padlock the top card of columns past the four gold columns. The key is a
// word in the stock or in a gold column — both fully reachable without ever
// touching the locked column, so the level stays winnable at perfect cost.
function dealLocks(state, level, rng) {
  const want = Math.min(knob(level, "locks", 0), state.columns.length - 4);
  for (let k = 0; k < want; k++) {
    const col = state.columns[4 + k];
    const lockCard = col[col.length - 1];
    if (!lockCard || lockCard.gold) continue;
    const used = new Set(Object.keys(state.locks));
    for (const v of Object.values(state.locks)) used.add(v);
    const candidates = [];
    for (let c = 0; c < 4; c++) {
      for (const card of state.columns[c]) {
        if (!card.gold && !used.has(card.w)) candidates.push(card.w);
      }
    }
    for (const w of state.stock) if (!used.has(w)) candidates.push(w);
    if (!candidates.length || used.has(lockCard.w)) continue;
    state.locks[lockCard.w] = candidates[Math.floor(rng() * candidates.length)];
  }
}

export function cloneState(state) {
  return JSON.parse(JSON.stringify(state));
}

// --- accessors ---------------------------------------------------------------

export function topOfColumn(state, col) {
  const column = state.columns[col];
  return column && column.length ? column[column.length - 1].w : null;
}

export function topOfWaste(state) {
  return state.waste.length ? state.waste[state.waste.length - 1] : null;
}

export function isLocked(state, word) {
  return !!state.locks && Object.prototype.hasOwnProperty.call(state.locks, word);
}

export function accessibleWord(state, source) {
  if (!source) return null;
  if (source.type === "waste") return topOfWaste(state);
  if (source.type === "column") {
    const w = topOfColumn(state, source.index);
    return w != null && isLocked(state, w) ? null : w;
  }
  return null;
}

export function isWon(state) {
  return state.slots.every((s) => s.placed.length === s.total);
}

function remainingCards(state) {
  return (
    state.columns.reduce((n, c) => n + c.length, 0) +
    state.stock.length +
    state.waste.length
  );
}

// --- move machinery ----------------------------------------------------------

class GameError extends Error {}

function assertPlaying(state) {
  if (state.status !== "playing") throw new GameError(`game is ${state.status}`);
}

function spendMove(state) {
  state.movesLeft -= 1;
  if (state.movesLeft <= 0 && !isWon(state)) {
    state.movesLeft = Math.max(0, state.movesLeft);
    state.status = "lost";
  }
}

// An uncovered gold card flies to its foundation slot for free and reveals
// the category; the card it exposes flips (and may cascade another gold).
function collectGolds(state) {
  let moved = true;
  while (moved) {
    moved = false;
    for (const col of state.columns) {
      const top = col.length ? col[col.length - 1] : null;
      if (top && top.gold) {
        col.pop();
        state.slots[top.cat].revealed = true;
        if (col.length) col[col.length - 1].up = true;
        moved = true;
      }
    }
  }
}

function settle(state) {
  collectGolds(state);
  if (isWon(state)) state.status = "won";
}

// Correctly placing a key card breaks the lock it opens.
function unlockByKey(state, word) {
  for (const [lockedWord, keyWord] of Object.entries(state.locks)) {
    if (keyWord === word) delete state.locks[lockedWord];
  }
}

function removeFromSource(state, source) {
  if (source.type === "waste") {
    return { w: state.waste.pop(), up: true };
  }
  const column = state.columns[source.index];
  const card = column.pop();
  if (column.length) column[column.length - 1].up = true;
  return card;
}

// Every mutating action goes through act(): snapshot -> mutate -> settle.
// history lives OUTSIDE the state so saved states stay small.
export function act(game, fn) {
  const { state, history } = game;
  assertPlaying(state);
  const before = cloneState(state);
  const result = fn(state);
  history.push(before);
  if (history.length > 100) history.shift();
  settle(state);
  return result;
}

export function newGame(level, seed) {
  return { state: createGame(level, seed), history: [] };
}

export function resumeGame(level, savedState) {
  if (!savedState || typeof savedState !== "object") return null;
  const state = cloneState(savedState);
  // saves from before golds/locks existed lack these fields
  if (!state.locks || typeof state.locks !== "object") state.locks = {};
  if (Array.isArray(state.slots)) {
    state.slots.forEach((s) => {
      if (s && s.revealed === undefined) s.revealed = true;
    });
  }
  if (!validateSavedState(level, state)) return null;
  return { state, history: [] };
}

export function draw(game) {
  return act(game, (state) => {
    if (state.stock.length === 0) {
      if (state.waste.length === 0) throw new GameError("nothing to draw");
      state.stock = state.waste.reverse();
      state.waste = [];
    }
    state.waste.push(state.stock.pop());
    spendMove(state);
    return topOfWaste(state);
  });
}

export function moveToColumn(game, source, targetCol) {
  return act(game, (state) => {
    if (targetCol < 0 || targetCol >= state.columns.length) {
      throw new GameError("no such column");
    }
    if (source.type === "column" && source.index === targetCol) {
      throw new GameError("same column");
    }
    const word = accessibleWord(state, source);
    if (word == null) throw new GameError("no card there");
    const card = removeFromSource(state, source);
    card.up = true;
    state.columns[targetCol].push(card);
    spendMove(state);
  });
}

export function place(game, source, slotIndex, key) {
  return act(game, (state) => {
    const slot = state.slots[slotIndex];
    if (!slot) throw new GameError("no such slot");
    if (slot.revealed === false) throw new GameError("category not revealed yet");
    const word = accessibleWord(state, source);
    if (word == null) throw new GameError("no card there");
    const correct = key.get(word) === slotIndex;
    if (correct) {
      removeFromSource(state, source);
      slot.placed.push(word);
      unlockByKey(state, word);
    } else {
      state.wrongGuesses += 1;
    }
    spendMove(state);
    return correct;
  });
}

export function hint(game, source, key) {
  const { state } = game;
  assertPlaying(state);
  if (state.hintsLeft <= 0) throw new GameError("no hints left");
  const word = accessibleWord(state, source);
  if (word == null) throw new GameError("no card there");
  state.hintsLeft -= 1; // no snapshot: hints are not undoable, cost no move
  return key.get(word);
}

export function joker(game, source, key) {
  return act(game, (state) => {
    if (state.jokersLeft <= 0) throw new GameError("no jokers left");
    const word = accessibleWord(state, source);
    if (word == null) throw new GameError("no card there");
    const slotIndex = key.get(word);
    if (state.slots[slotIndex].revealed === false) {
      throw new GameError("category not revealed yet");
    }
    removeFromSource(state, source);
    state.slots[slotIndex].placed.push(word);
    unlockByKey(state, word);
    state.jokersLeft -= 1; // no spendMove: jokers are free
    return slotIndex;
  });
}

export function undo(game) {
  if (!game.history.length) throw new GameError("nothing to undo");
  game.state = game.history.pop();
  return game.state;
}

export function canUndo(game) {
  return game.history.length > 0;
}

// --- persistence guards ------------------------------------------------------

export function validateSavedState(level, state) {
  if (!state || state.levelId !== level.id || state.status !== "playing") return false;
  const key = answerKey(level);
  const seen = new Set();
  const every = [];
  const columnWords = new Set();
  const goldsSeen = new Set();
  try {
    state.columns.forEach((col) => col.forEach((c) => {
      if (c && c.gold === true) {
        if (!Number.isInteger(c.cat) || c.cat < 0 || c.cat > 3 || goldsSeen.has(c.cat)) {
          throw new Error("bad gold");
        }
        goldsSeen.add(c.cat);
      } else {
        every.push(c.w);
        columnWords.add(c.w);
      }
    }));
    every.push(...state.stock, ...state.waste);
    state.slots.forEach((s, i) => {
      if (s.total !== level.categories[i].words.length) throw new Error("shape");
      const revealed = s.revealed !== false;
      if (!revealed && s.placed.length) throw new Error("placed in hidden slot");
      // a hidden slot needs its gold still in play; a revealed one must not
      if (revealed === goldsSeen.has(i)) throw new Error("gold mismatch");
      s.placed.forEach((w) => {
        if (key.get(w) !== i) throw new Error("misplaced");
        every.push(w);
      });
    });
  } catch {
    return false;
  }
  if (every.length !== key.size) return false;
  for (const w of every) {
    if (!key.has(w) || seen.has(w)) return false;
    seen.add(w);
  }
  if (state.locks !== undefined) {
    if (!state.locks || typeof state.locks !== "object" || Array.isArray(state.locks)) return false;
    const placed = new Set(state.slots.flatMap((s) => s.placed));
    for (const [lockedWord, keyWord] of Object.entries(state.locks)) {
      // a locked card can never leave its column; a placed key opens its lock
      if (!key.has(lockedWord) || !key.has(keyWord) || lockedWord === keyWord) return false;
      if (!columnWords.has(lockedWord) || placed.has(keyWord)) return false;
    }
  }
  if (typeof state.movesLeft !== "number" || state.movesLeft <= 0) return false;
  return true;
}

export { GameError, remainingCards };
