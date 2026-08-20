import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as engine from "../app/static/engine.js";

const LEVEL = {
  id: 1,
  difficulty: 1,
  move_budget: 110,
  categories: [
    { name: "Fruits", words: ["APPLE", "PEAR", "PLUM", "MANGO"] },
    { name: "Colors", words: ["RED", "BLUE", "GREEN", "YELLOW"] },
    { name: "Dogs", words: ["BEAGLE", "POODLE", "BOXER", "PUG"] },
    { name: "Tools", words: ["HAMMER", "WRENCH", "SAW", "DRILL"] },
  ],
};

const KEY = engine.answerKey(LEVEL);

function allWords(state) {
  const words = [];
  state.columns.forEach((c) => c.forEach((card) => { if (!card.gold) words.push(card.w); }));
  words.push(...state.stock, ...state.waste);
  state.slots.forEach((s) => words.push(...s.placed));
  return words;
}

test("deal covers every word exactly once, deterministic per seed", () => {
  const a = engine.createGame(LEVEL, 42);
  const b = engine.createGame(LEVEL, 42);
  const c = engine.createGame(LEVEL, 43);
  assert.deepEqual(a, b);
  assert.notDeepEqual(a, c);
  assert.equal(allWords(a).length, 16);
  assert.deepEqual(new Set(allWords(a)).size, 16);
  assert.equal(a.columns.length, 4); // difficulty 1
  a.columns.forEach((col) => {
    col.forEach((card, i) => assert.equal(card.up, i === col.length - 1));
  });
  assert.equal(a.movesLeft, 110);
});

test("difficulty scales column count", () => {
  const lvl5 = { ...LEVEL, difficulty: 5 };
  assert.equal(engine.createGame(lvl5, 1).columns.length, 6);
});

test("per-level knobs override the flat defaults", () => {
  const base = engine.createGame(LEVEL, 5);
  assert.equal(base.hintsLeft, 3); // fallback when the level omits them
  assert.equal(base.jokersLeft, 1);
  assert.equal(base.stock.length, 16 - Math.ceil(16 * 0.6));

  const late = engine.createGame(
    { ...LEVEL, tableau_frac: 0.9, hints: 0, jokers: 0 },
    5
  );
  assert.equal(late.hintsLeft, 0);
  assert.equal(late.jokersLeft, 0);
  assert.equal(late.stock.length, 16 - Math.ceil(16 * 0.9));
  // deeper burial: more cards dealt face-down into the same columns
  const buried = (s) =>
    s.columns.reduce((n, c) => n + c.filter((card) => !card.up).length, 0);
  assert.ok(buried(late) > buried(base));
});

test("draw moves stock to waste, costs a move, recycles when empty", () => {
  const game = engine.newGame(LEVEL, 7);
  const stockSize = game.state.stock.length;
  const drawn = engine.draw(game);
  assert.equal(drawn, engine.topOfWaste(game.state));
  assert.equal(game.state.stock.length, stockSize - 1);
  assert.equal(game.state.movesLeft, 109);
  // drain and recycle
  for (let i = 0; i < stockSize - 1; i++) engine.draw(game);
  assert.equal(game.state.stock.length, 0);
  engine.draw(game); // recycle + draw
  assert.equal(game.state.waste.length, 1);
  assert.equal(game.state.stock.length, stockSize - 1);
});

test("correct placement removes card and flips the next; wrong placement bounces", () => {
  const game = engine.newGame(LEVEL, 7);
  const col = game.state.columns.findIndex((c) => c.length >= 2);
  const word = engine.topOfColumn(game.state, col);
  const right = KEY.get(word);
  const wrong = (right + 1) % 4;

  const before = game.state.columns[col].length;
  assert.equal(engine.place(game, { type: "column", index: col }, wrong, KEY), false);
  assert.equal(game.state.columns[col].length, before, "card stays on wrong guess");
  assert.equal(game.state.wrongGuesses, 1);
  assert.equal(game.state.movesLeft, 109, "wrong guess still costs a move");

  assert.equal(engine.place(game, { type: "column", index: col }, right, KEY), true);
  assert.equal(game.state.columns[col].length, before - 1);
  assert.deepEqual(game.state.slots[right].placed, [word]);
  assert.ok(game.state.columns[col][game.state.columns[col].length - 1].up, "new top flipped");
});

test("moveToColumn repositions and costs a move", () => {
  const game = engine.newGame(LEVEL, 9);
  const from = 0;
  const to = 1;
  const word = engine.topOfColumn(game.state, from);
  engine.moveToColumn(game, { type: "column", index: from }, to);
  assert.equal(engine.topOfColumn(game.state, to), word);
  assert.equal(game.state.movesLeft, 109);
  assert.throws(() => engine.moveToColumn(game, { type: "column", index: to }, to), engine.GameError);
});

test("undo reverts state and refunds the move", () => {
  const game = engine.newGame(LEVEL, 11);
  const snapshot = engine.cloneState(game.state);
  engine.draw(game);
  assert.notDeepEqual(game.state, snapshot);
  engine.undo(game);
  assert.deepEqual(game.state, snapshot);
  assert.throws(() => engine.undo(game), engine.GameError);
});

test("hint returns the right slot without costing a move; joker places free", () => {
  const game = engine.newGame(LEVEL, 13);
  const word = engine.topOfColumn(game.state, 0);
  const slot = engine.hint(game, { type: "column", index: 0 }, KEY);
  assert.equal(slot, KEY.get(word));
  assert.equal(game.state.hintsLeft, 2);
  assert.equal(game.state.movesLeft, 110);

  engine.joker(game, { type: "column", index: 0 }, KEY);
  assert.equal(game.state.jokersLeft, 0);
  assert.equal(game.state.movesLeft, 110);
  assert.ok(game.state.slots[slot].placed.includes(word));
  assert.throws(() => engine.joker(game, { type: "column", index: 0 }, KEY), engine.GameError);
});

function solve(game, key = KEY) {
  // Greedy: place whatever is accessible and placeable (revealed category,
  // not padlocked); draw when nothing is. The gold/lock deal guarantees this
  // wins at exactly one placement per card plus one draw per stock card.
  let safety = 2000;
  while (game.state.status === "playing" && safety-- > 0) {
    let placed = false;
    for (let i = 0; i < game.state.columns.length; i++) {
      const word = engine.topOfColumn(game.state, i);
      if (word == null || engine.isLocked(game.state, word)) continue;
      const slot = key.get(word);
      if (game.state.slots[slot].revealed === false) continue;
      engine.place(game, { type: "column", index: i }, slot, key);
      placed = true;
      break;
    }
    if (placed || game.state.status !== "playing") continue;
    const wasteWord = engine.topOfWaste(game.state);
    if (wasteWord != null && game.state.slots[key.get(wasteWord)].revealed !== false) {
      engine.place(game, { type: "waste" }, key.get(wasteWord), key);
    } else {
      engine.draw(game);
    }
  }
  return game.state.status;
}

test("perfect play wins within budget on many seeds", () => {
  for (let seed = 1; seed <= 50; seed++) {
    const game = engine.newGame(LEVEL, seed);
    assert.equal(solve(game), "won", `seed ${seed}`);
  }
});

// The late levels run on a very tight budget (~1.3x a perfect solve), so an
// off-by-one in the curve would ship unwinnable levels. Play the real bank.
test("every shipped level is winnable by a player who knows every answer", () => {
  const levels = JSON.parse(
    readFileSync(new URL("../data/levels.json", import.meta.url))
  );
  assert.equal(levels.length, 150);
  for (const level of levels) {
    const key = engine.answerKey(level);
    for (const seed of [1, 2, 3, 4, 5]) {
      const game = engine.newGame(level, seed);
      assert.equal(
        solve(game, key),
        "won",
        `level ${level.id} (budget ${level.move_budget}) unwinnable at seed ${seed}`
      );
    }
  }
});

// --- gold category cards -----------------------------------------------------

const GLEVEL = { ...LEVEL, golds: true };
// difficulty 5 -> 6 columns, so there is room past the gold columns for locks
const LLEVEL = { ...LEVEL, difficulty: 5, golds: true, locks: 2 };

test("gold deal: categories start hidden, the top gold auto-reveals, rest stay buried", () => {
  const a = engine.createGame(GLEVEL, 42);
  const b = engine.createGame(GLEVEL, 42);
  assert.deepEqual(a, b, "still deterministic per seed");
  assert.equal(a.slots.filter((s) => s.revealed).length, 1);
  assert.equal(a.columns.flat().filter((c) => c.gold).length, 3);
  assert.equal(allWords(a).length, 16, "golds are extra cards, not words");
});

test("placing into a hidden category is rejected without costing a move", () => {
  const game = engine.newGame(GLEVEL, 42);
  const hiddenIdx = game.state.slots.findIndex((s) => !s.revealed);
  const col = game.state.columns.findIndex((c) => c.length);
  assert.throws(
    () => engine.place(game, { type: "column", index: col }, hiddenIdx, KEY),
    engine.GameError
  );
  assert.equal(game.state.movesLeft, GLEVEL.move_budget);
});

test("golds are budget-neutral: a perfect solve costs the same as without them", () => {
  for (const seed of [1, 2, 3]) {
    const plain = engine.newGame(LEVEL, seed);
    assert.equal(solve(plain), "won");
    const gold = engine.newGame(GLEVEL, seed);
    assert.equal(solve(gold), "won", `seed ${seed}`);
    assert.equal(gold.state.movesLeft, plain.state.movesLeft);
    assert.ok(gold.state.slots.every((s) => s.revealed));
  }
});

// --- locks and keys ----------------------------------------------------------

test("locked cards cannot be played until their key is placed", () => {
  const game = engine.newGame(LLEVEL, 3);
  const entries = Object.entries(game.state.locks);
  assert.equal(entries.length, 2);
  const lockedWord = entries[0][0];
  const colIdx = game.state.columns.findIndex(
    (c) => c.length && c[c.length - 1].w === lockedWord
  );
  assert.ok(colIdx >= 4, "locks live past the four gold columns");
  assert.equal(engine.accessibleWord(game.state, { type: "column", index: colIdx }), null);
  assert.throws(
    () => engine.place(game, { type: "column", index: colIdx }, KEY.get(lockedWord), KEY),
    engine.GameError
  );
  assert.throws(
    () => engine.joker(game, { type: "column", index: colIdx }, KEY),
    engine.GameError
  );
  // a perfect solve places the keys along the way, opens both locks, and wins
  assert.equal(solve(game), "won");
  assert.deepEqual(game.state.locks, {});
});

test("lock count is capped by available columns", () => {
  const st = engine.createGame({ ...LEVEL, locks: 2 }, 5); // difficulty 1 -> 4 columns
  assert.deepEqual(st.locks, {});
});

test("gold/lock states round-trip through save validation; tampered ones are rejected", () => {
  const game = engine.newGame(LLEVEL, 8);
  engine.draw(game);
  const saved = JSON.parse(JSON.stringify(game.state));
  assert.ok(engine.resumeGame(LLEVEL, saved));

  const noGold = JSON.parse(JSON.stringify(saved));
  for (const col of noGold.columns) {
    const i = col.findIndex((c) => c.gold); // hidden category with no gold = unwinnable
    if (i >= 0) col.splice(i, 1);
  }
  assert.equal(engine.resumeGame(LLEVEL, noGold), null);

  const earlyReveal = JSON.parse(JSON.stringify(saved));
  earlyReveal.slots.forEach((s) => { s.revealed = true; }); // golds still in play
  assert.equal(engine.resumeGame(LLEVEL, earlyReveal), null);

  const selfKey = JSON.parse(JSON.stringify(saved));
  const lw = Object.keys(selfKey.locks)[0];
  selfKey.locks[lw] = lw;
  assert.equal(engine.resumeGame(LLEVEL, selfKey), null);

  // saves from before golds/locks existed still resume
  const legacy = JSON.parse(JSON.stringify(engine.createGame(LEVEL, 8)));
  delete legacy.locks;
  legacy.slots.forEach((s) => { delete s.revealed; });
  assert.ok(engine.resumeGame(LEVEL, legacy));
});

test("running out of moves loses", () => {
  const lvl = { ...LEVEL, move_budget: 3 };
  const game = engine.newGame(lvl, 5);
  engine.draw(game);
  engine.draw(game);
  engine.draw(game);
  assert.equal(game.state.status, "lost");
  assert.throws(() => engine.draw(game), engine.GameError);
});

test("saved-state round trip validates and resumes; tampered states are rejected", () => {
  const game = engine.newGame(LEVEL, 21);
  engine.draw(game);
  const word = engine.topOfWaste(game.state);
  engine.place(game, { type: "waste" }, KEY.get(word), KEY);

  const saved = JSON.parse(JSON.stringify(game.state));
  const resumed = engine.resumeGame(LEVEL, saved);
  assert.ok(resumed);
  assert.deepEqual(resumed.state, game.state);

  const missing = JSON.parse(JSON.stringify(saved));
  missing.stock.pop();
  assert.equal(engine.resumeGame(LEVEL, missing), null);

  const dup = JSON.parse(JSON.stringify(saved));
  dup.stock[0] = dup.stock[1];
  assert.equal(engine.resumeGame(LEVEL, dup), null);

  const wrongLevel = JSON.parse(JSON.stringify(saved));
  wrongLevel.levelId = 99;
  assert.equal(engine.resumeGame(LEVEL, wrongLevel), null);

  const misplaced = JSON.parse(JSON.stringify(saved));
  misplaced.slots[0].placed = [misplaced.stock.pop()];
  assert.equal(engine.resumeGame(LEVEL, misplaced), null);
});
