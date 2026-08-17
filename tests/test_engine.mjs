import { test } from "node:test";
import assert from "node:assert/strict";
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
  state.columns.forEach((c) => c.forEach((card) => words.push(card.w)));
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

function solve(game) {
  // Greedy: place whatever is accessible; draw when nothing is.
  let safety = 2000;
  while (game.state.status === "playing" && safety-- > 0) {
    let placed = false;
    for (let i = 0; i < game.state.columns.length; i++) {
      const word = engine.topOfColumn(game.state, i);
      if (word != null) {
        engine.place(game, { type: "column", index: i }, KEY.get(word), KEY);
        placed = true;
        break;
      }
    }
    if (placed || game.state.status !== "playing") continue;
    const wasteWord = engine.topOfWaste(game.state);
    if (wasteWord != null) {
      engine.place(game, { type: "waste" }, KEY.get(wasteWord), KEY);
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
