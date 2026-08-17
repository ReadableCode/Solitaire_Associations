"""Level bank invariants — a malformed level must never ship."""

import json
import math

from app import config

DIFFICULTY_WORDS = {1: {4}, 2: {5}, 3: {5}, 4: {6}, 5: {6, 7}}


def _levels():
    return json.loads(config.LEVELS_PATH.read_text())


def _cards(lvl):
    return sum(len(cat["words"]) for cat in lvl["categories"])


def _perfect_solve(lvl):
    """Moves a player who knows every answer needs: one placement per card,
    plus one draw per stock card. Mirrors createGame() in engine.js."""
    cards = _cards(lvl)
    stock = cards - math.ceil(cards * lvl["tableau_frac"])
    return cards + stock


def test_levels_load_and_ids_are_sequential():
    levels = _levels()
    assert len(levels) >= 100
    assert [lvl["id"] for lvl in levels] == list(range(1, len(levels) + 1))


def test_every_level_is_well_formed():
    for lvl in _levels():
        assert lvl["difficulty"] in DIFFICULTY_WORDS, lvl["id"]
        assert len(lvl["categories"]) == 4, lvl["id"]
        words = [w for cat in lvl["categories"] for w in cat["words"]]
        assert len(words) == len(set(words)), f"duplicate words in level {lvl['id']}"
        for cat in lvl["categories"]:
            assert cat["name"].strip(), lvl["id"]
            assert len(cat["words"]) in DIFFICULTY_WORDS[lvl["difficulty"]], (
                f"level {lvl['id']} cat {cat['name']!r} has {len(cat['words'])} words"
            )
            for word in cat["words"]:
                assert word == word.upper() and word.strip(), f"level {lvl['id']} word {word!r}"


def test_every_level_stays_winnable():
    """The budget may be tight, but never below a perfect solve."""
    for lvl in _levels():
        assert lvl["move_budget"] >= _perfect_solve(lvl), (
            f"level {lvl['id']} is unwinnable: budget {lvl['move_budget']} "
            f"< perfect solve {_perfect_solve(lvl)}"
        )


def test_difficulty_knobs_are_in_range():
    for lvl in _levels():
        assert 0.5 <= lvl["tableau_frac"] <= 0.95, lvl["id"]
        assert 0 <= lvl["hints"] <= 3, lvl["id"]
        assert 0 <= lvl["jokers"] <= 1, lvl["id"]


def test_pressure_ramps_monotonically_across_the_whole_bank():
    """The bug this guards: every knob used to scale with card count, so
    levels 1 and 150 played at identical pressure. Each knob must now tighten
    (or hold) with level id — including *within* a difficulty tier."""
    levels = _levels()
    for prev, cur in zip(levels, levels[1:]):
        ratio_prev = prev["move_budget"] / _perfect_solve(prev)
        ratio_cur = cur["move_budget"] / _perfect_solve(cur)
        assert ratio_cur <= ratio_prev + 1e-9, (
            f"level {cur['id']} gives more slack ({ratio_cur:.2f}x) "
            f"than level {prev['id']} ({ratio_prev:.2f}x)"
        )
        assert cur["tableau_frac"] >= prev["tableau_frac"], cur["id"]
        assert cur["hints"] <= prev["hints"], cur["id"]
        assert cur["jokers"] <= prev["jokers"], cur["id"]


def test_the_curve_actually_spans_a_meaningful_range():
    levels = _levels()
    first = levels[0]["move_budget"] / _perfect_solve(levels[0])
    last = levels[-1]["move_budget"] / _perfect_solve(levels[-1])
    assert first >= 3.0, f"level 1 should be forgiving, got {first:.2f}x"
    assert last <= 1.4, f"the last level should be tight, got {last:.2f}x"
    assert levels[0]["hints"] > levels[-1]["hints"]
