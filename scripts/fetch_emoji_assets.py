"""Sync self-hosted Noto Emoji SVGs with data/word_emoji.json.

Reads the word->emoji mapping, downloads each referenced glyph's SVG from the
googlefonts/noto-emoji repo (Apache-2.0) into app/static/img/emoji/<id>.svg,
where <id> is the codepoint sequence (fe0f stripped, zwj kept) joined by '_'.
Emoji that don't exist in Noto get their mapping entry set to null so the
frontend falls back to the procedural medallion. Idempotent; run after any
mapping change, commit the SVGs alongside the mapping.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAPPING = REPO_ROOT / "data" / "word_emoji.json"
ASSET_DIR = REPO_ROOT / "app" / "static" / "img" / "emoji"
RAW_BASE = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/svg"
FLAG_BASE = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/third_party/region-flags/svg"


def emoji_id(emoji: str) -> str:
    """Codepoint id matching noto-emoji's file naming (fe0f dropped)."""
    return "_".join(f"{ord(ch):x}" for ch in emoji if ord(ch) != 0xFE0F)


def flag_url(emoji: str) -> str | None:
    """Country flags live in region-flags (public domain), named by ISO code."""
    cps = [ord(ch) for ch in emoji]
    if cps and all(0x1F1E6 <= cp <= 0x1F1FF for cp in cps):
        code = "".join(chr(cp - 0x1F1E6 + ord("A")) for cp in cps)
        return f"{FLAG_BASE}/{code}.svg"
    return None


def fetch(url: str, dest: Path, retries: int = 3) -> bool:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                dest.write_bytes(resp.read())
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            time.sleep(1 + attempt)
        except OSError:
            time.sleep(1 + attempt)
    return False


def main() -> None:
    mapping: dict[str, str | None] = json.loads(MAPPING.read_text())
    ASSET_DIR.mkdir(parents=True, exist_ok=True)

    wanted: dict[str, str] = {}  # id -> emoji (first word using it)
    for word, emoji in mapping.items():
        if emoji:
            wanted.setdefault(emoji_id(emoji), emoji)

    downloaded = cached = missing = 0
    missing_ids: set[str] = set()
    for eid in sorted(wanted):
        dest = ASSET_DIR / f"{eid}.svg"
        if dest.is_file() and dest.stat().st_size > 0:
            cached += 1
            continue
        url = flag_url(wanted[eid]) or f"{RAW_BASE}/emoji_u{eid}.svg"
        if fetch(url, dest):
            downloaded += 1
        else:
            dest.unlink(missing_ok=True)
            missing += 1
            missing_ids.add(eid)
            print(f"  not in noto: {wanted[eid]} ({eid})")

    if missing_ids:
        for word, emoji in mapping.items():
            if emoji and emoji_id(emoji) in missing_ids:
                mapping[word] = None
        MAPPING.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=0, sort_keys=True) + "\n"
        )
        print(f"nulled {missing} unavailable glyphs in mapping")

    total = len(mapping)
    mapped = sum(1 for v in mapping.values() if v)
    print(f"glyphs: {downloaded} downloaded, {cached} cached, {missing} missing")
    print(f"coverage: {mapped}/{total} words mapped ({mapped * 100 // total}%)")


if __name__ == "__main__":
    sys.exit(main())
