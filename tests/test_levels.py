"""Level bank invariants — a malformed level must never ship."""

import json

from app import config

DIFFICULTY_WORDS = {1: {4}, 2: {5}, 3: {5}, 4: {6}, 5: {6, 7}}


def _levels():
    return json.loads(config.LEVELS_PATH.read_text())


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
        assert lvl["move_budget"] >= len(words) * 4, f"level {lvl['id']} budget too tight"
