"""Word -> card-art lookup, injected into level payloads.

data/word_emoji.json maps each level word to an emoji (or null). The frontend
receives codepoint ids and loads /static/img/emoji/<id>.svg (self-hosted Noto
Emoji, Apache-2.0); unmapped words get a procedural medallion client-side.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("solitaire.icons")

MAPPING_PATH = Path(__file__).resolve().parent.parent / "data" / "word_emoji.json"

_ids: dict[str, str] = {}


def _emoji_id(emoji: str) -> str:
    return "_".join(f"{ord(ch):x}" for ch in emoji if ord(ch) != 0xFE0F)


def load() -> None:
    global _ids
    try:
        mapping = json.loads(MAPPING_PATH.read_text())
    except FileNotFoundError:
        log.warning("no word_emoji.json — all cards will use medallion fallback")
        _ids = {}
        return
    _ids = {word: _emoji_id(emoji) for word, emoji in mapping.items() if emoji}
    log.info("card art loaded for %d words", len(_ids))


def icons_for_level(level: dict) -> dict[str, str]:
    return {
        word: _ids[word]
        for cat in level["categories"]
        for word in cat["words"]
        if word in _ids
    }
