"""Card art invariants: mapping covers the level bank, every glyph is on disk."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fetch_emoji_assets import ASSET_DIR, MAPPING, emoji_id  # noqa: E402

from app import config  # noqa: E402


def _level_words() -> set[str]:
    levels = json.loads(config.LEVELS_PATH.read_text())
    return {w for lvl in levels for cat in lvl["categories"] for w in cat["words"]}


def _mapping() -> dict:
    return json.loads(MAPPING.read_text())


def test_mapping_keys_exactly_cover_level_words():
    assert set(_mapping().keys()) == _level_words()


def test_every_mapped_emoji_has_an_svg_on_disk():
    missing = [
        f"{word} -> {emoji} ({emoji_id(emoji)}.svg)"
        for word, emoji in _mapping().items()
        if emoji and not (ASSET_DIR / f"{emoji_id(emoji)}.svg").is_file()
    ]
    assert missing == [], missing[:10]


def test_values_are_single_emoji_or_null():
    for word, emoji in _mapping().items():
        if emoji is None:
            continue
        assert isinstance(emoji, str) and 0 < len(emoji) <= 12, word
        assert not any(c.isascii() for c in emoji), f"{word} -> {emoji!r}"


def test_coverage_is_reasonable():
    mapping = _mapping()
    mapped = sum(1 for v in mapping.values() if v)
    assert mapped / len(mapping) >= 0.5, f"only {mapped}/{len(mapping)} mapped"
