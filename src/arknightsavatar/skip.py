"""Skip-list support for character sprites.

The list is a small JSON object where each key is either ``"<character>"``
(skip every sprite for that character) or ``"<character>/<sprite>"`` (skip one
sprite). Values are human-readable reasons.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from arknightsavatar import paths

DEFAULT_SKIP = paths.SKIP_LIST


def _key(value: str) -> str:
    """Normalize a character/sprite name for case-insensitive matching."""
    return value.strip().casefold()


def _sprite_key(value: str) -> str:
    """Normalize a sprite key so ``base`` and ``base.png`` compare equal."""
    return Path(value.strip()).stem.casefold()


class SkipList:
    """Parsed skip configuration.

    Missing or invalid entries are ignored; a missing/unreadable file behaves
    like an empty configuration.
    """

    def __init__(self, raw: dict | None = None) -> None:
        raw = raw if isinstance(raw, dict) else {}
        self._characters: dict[str, str] = {}
        self._sprites: dict[str, dict[str, str]] = {}

        for key, value in raw.items():
            if not isinstance(key, str) or not _key(key):
                continue
            reason = value if isinstance(value, str) else str(value)
            character, separator, sprite = key.strip().partition("/")
            character = character.strip()
            if not character:
                continue
            if not separator or not sprite.strip():
                self._characters[_key(character)] = reason
            else:
                self._sprites.setdefault(_key(character), {})[_sprite_key(sprite)] = (
                    reason
                )

    @classmethod
    def load(cls, path: str | Path | None = None) -> SkipList:
        """Load a skip JSON file, returning an empty list on any failure."""
        path = Path(path or DEFAULT_SKIP)
        try:
            with path.open("rt", encoding="utf8") as file:
                payload = json.load(file)
        except (OSError, ValueError):
            return cls()
        if not isinstance(payload, dict):
            return cls()
        return cls(payload)

    def is_character_skipped(self, character: str) -> bool:
        """Return True when the entire character should be skipped."""
        return _key(character) in self._characters

    def is_sprite_skipped(self, character: str, sprite: str) -> bool:
        """Return True when this character/sprite pair should be skipped."""
        character_key = _key(character)
        if character_key in self._characters:
            return True
        sprites = self._sprites.get(character_key)
        return bool(sprites and _sprite_key(sprite) in sprites)

    def reason(self, character: str, sprite: str | None = None) -> str | None:
        """Return the most specific configured reason, if any."""
        character_key = _key(character)
        if sprite is not None:
            sprites = self._sprites.get(character_key)
            if sprites and _sprite_key(sprite) in sprites:
                return sprites[_sprite_key(sprite)]
        return self._characters.get(character_key)

    def filter_character_entry(self, character: str, item: dict) -> dict | None:
        """Return a filtered copy of one classification entry.

        ``None`` means the character was fully skipped. Skipping a base removes
        that base and all of its diffs; skipping a diff removes only that diff.
        """
        if self.is_character_skipped(character):
            return None

        filtered = dict(item)
        bases = item.get("bases")
        if not isinstance(bases, dict):
            return filtered

        had_bases = bool(bases)
        new_bases: dict[str, dict] = {}
        for base_name, base_entry in bases.items():
            if self.is_sprite_skipped(character, base_name):
                continue
            if not isinstance(base_entry, dict):
                new_bases[base_name] = base_entry
                continue
            new_entry = dict(base_entry)
            diffs = base_entry.get("diff")
            if isinstance(diffs, list):
                new_entry["diff"] = [
                    diff
                    for diff in diffs
                    if isinstance(diff, str)
                    and not self.is_sprite_skipped(character, diff)
                ]
            new_bases[base_name] = new_entry

        if had_bases and not new_bases:
            return None
        filtered["bases"] = new_bases

        unassigned = filtered.get("unassigned")
        if isinstance(unassigned, list):
            filtered["unassigned"] = [
                diff
                for diff in unassigned
                if isinstance(diff, str) and not self.is_sprite_skipped(character, diff)
            ]
        return filtered

    def filter_classified(self, classified: dict) -> dict:
        """Return a classified-report copy with skipped items removed."""
        filtered = dict(classified)
        characters = classified.get("characters")
        if not isinstance(characters, dict):
            return filtered

        new_characters: dict[str, dict] = {}
        for name, item in characters.items():
            if not isinstance(name, str):
                continue
            if not isinstance(item, dict):
                continue
            filtered_item = self.filter_character_entry(name, item)
            if filtered_item is not None:
                new_characters[name] = filtered_item
        filtered["characters"] = new_characters
        return filtered

    def expand(
        self, classified: dict | None = None
    ) -> tuple[set[str], dict[str, set[str]]]:
        """Expand skips for output-oriented stages.

        Returns ``(skipped_characters, skipped_stems)``. Character keys are
        case-folded; stem keys are case-folded sprite stems without extension.
        When classification is available, skipping a base also expands to all
        diff stems under that base.
        """
        skipped_characters = set(self._characters)
        skipped_stems: dict[str, set[str]] = defaultdict(set)

        class_chars: dict[str, dict] = {}
        classified = classified if isinstance(classified, dict) else {}
        characters = classified.get("characters")
        if isinstance(characters, dict):
            for name, item in characters.items():
                if isinstance(name, str) and isinstance(item, dict):
                    class_chars[_key(name)] = item

        for character_key, sprite_reasons in self._sprites.items():
            item = class_chars.get(character_key)
            bases = item.get("bases") if isinstance(item, dict) else None
            base_map: dict[str, dict] = {}
            if isinstance(bases, dict):
                for base_name, base_entry in bases.items():
                    base_map[_sprite_key(base_name)] = base_entry

            for sprite_key in sprite_reasons:
                if sprite_key in base_map:
                    skipped_stems[character_key].add(sprite_key)
                    diffs = base_map[sprite_key].get("diff")
                    if isinstance(diffs, list):
                        skipped_stems[character_key].update(
                            _sprite_key(diff) for diff in diffs if isinstance(diff, str)
                        )
                else:
                    skipped_stems[character_key].add(sprite_key)

        return skipped_characters, dict(skipped_stems)
