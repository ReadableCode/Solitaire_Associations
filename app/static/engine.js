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
  const tableauCount = Math.ceil(deck.length * 0.6);
  const columns = Array.from({ length: numCols }, () => []);
  for (let i = 0; i < tableauCount; i++) {
    columns[i % numCols].push({ w: deck[i], up: false });
  }
  columns.forEach((col) => {
    if (col.length) col[col.length - 1].up = true;
  });
  return {
    levelId: level.id,
    seed,
    difficulty: level.difficulty,
    moveBudget: level.move_budget,
    movesLeft: level.move_budget,
    hintsLeft: 3,
    jokersLeft: 1,
    wrongGuesses: 0,
    status: "playing",
    slots: level.categories.map((c) => ({
      name: c.name,
      total: c.words.length,
      placed: [],
    })),
    columns,
    stock: deck.slice(tableauCount),
    waste: [],
  };
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

export function accessibleWord(state, source) {
  if (!source) return null;
  if (source.type === "waste") return topOfWaste(state);
  if (source.type === "column") return topOfColumn(state, source.index);
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

function settle(state) {
  if (isWon(state)) state.status = "won";
}

function removeFromSource(state, source) {
  if (source.type === "waste") {
    return state.waste.pop();
  }
  const column = state.columns[source.index];
  const card = column.pop();
  if (column.length) column[column.length - 1].up = true;
  return card.w;
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
  if (!validateSavedState(level, savedState)) return null;
  return { state: cloneState(savedState), history: [] };
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
    removeFromSource(state, source);
    state.columns[targetCol].push({ w: word, up: true });
    spendMove(state);
  });
}

export function place(game, source, slotIndex, key) {
  return act(game, (state) => {
    const slot = state.slots[slotIndex];
    if (!slot) throw new GameError("no such slot");
    const word = accessibleWord(state, source);
    if (word == null) throw new GameError("no card there");
    const correct = key.get(word) === slotIndex;
    if (correct) {
      removeFromSource(state, source);
      slot.placed.push(word);
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
    removeFromSource(state, source);
    state.slots[slotIndex].placed.push(word);
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
  try {
    state.columns.forEach((col) => col.forEach((c) => every.push(c.w)));
    every.push(...state.stock, ...state.waste);
    state.slots.forEach((s, i) => {
      if (s.total !== level.categories[i].words.length) throw new Error("shape");
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
  if (typeof state.movesLeft !== "number" || state.movesLeft <= 0) return false;
  return true;
}

export { GameError, remainingCards };
