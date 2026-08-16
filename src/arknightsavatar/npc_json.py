"""Generate the legacy NPC avatar index JSON (``data/arknights_npc.json``).

Scans ``data/export/<npc_id>/`` and writes a JSON mapping each character to
its avatar PNG stems, in the same format as the old project's
``arknights_npc.json``::

    {
      "avg_003_kalts_1": [
        [],
        ["1$1", "10$1", "11$1", ...],
        ["npc"]
      ],
      ...
    }

The first element (expressions) and the third element (tag) are fixed legacy
placeholders; only the middle avatar list varies. Keys and stems are sorted
lexicographically so the output is deterministic. Pure stdlib (no Pillow).

CLI: ``arknightsavatar-npc-json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from arknightsavatar import paths
from arknightsavatar.skip import DEFAULT_SKIP, SkipList

DEFAULT_EXPORT_DIR = paths.EXPORT_DIR
DEFAULT_OUTPUT_FILE = paths.NPC_JSON
DEFAULT_CLASSIFIED = paths.CLASSIFIED


def _read_json(path: Path) -> object | None:
    try:
        with path.open("rt", encoding="utf8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return None


def iter_character_dirs(export_dir: Path) -> list[Path]:
    """Return the per-character subdirectories of ``export_dir``, sorted."""
    if not export_dir.is_dir():
        return []
    return sorted(p for p in export_dir.iterdir() if p.is_dir())


def iter_png_stems(char_dir: Path) -> list[str]:
    """Return the sorted stems of PNG files directly under ``char_dir``.

    Dotfiles (residual detection scratch, hidden files) are skipped so a stray
    ``.arknightsavatar_detect_*.png`` left by a crash can never become a ghost
    avatar entry.
    """
    return sorted(
        p.stem
        for p in char_dir.glob("*.png")
        if p.is_file() and not p.name.startswith(".")
    )


def build_npc_avatar_map(
    export_dir: Path,
    skip: SkipList | None = None,
    classified: dict | None = None,
) -> dict[str, list]:
    """Build the legacy ``{npc_id: [[], [avatars], ["npc"]]}`` mapping."""
    skip = skip or SkipList()
    skipped_characters, skipped_stems = skip.expand(classified)
    data: dict[str, list] = {}
    for char_dir in iter_character_dirs(export_dir):
        if char_dir.name.casefold() in skipped_characters:
            continue
        stems = [
            stem
            for stem in iter_png_stems(char_dir)
            if stem.casefold() not in skipped_stems.get(char_dir.name.casefold(), set())
        ]
        data[char_dir.name] = [
            [],
            stems,
            ["npc"],
        ]
    return data


def write_npc_json(data: dict[str, list], output: Path) -> None:
    """Write the mapping as pretty JSON (2-space indent, UTF-8)."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wt", encoding="utf8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-npc-json",
        description="Scan data/export and generate the legacy arknights_npc.json index.",
    )
    parser.add_argument(
        "--export-dir",
        default=DEFAULT_EXPORT_DIR,
        help=f"source PNG directory (default: {DEFAULT_EXPORT_DIR})",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=DEFAULT_OUTPUT_FILE,
        help=f"JSON output path, or '-' for stdout (default: {DEFAULT_OUTPUT_FILE})",
    )
    parser.add_argument(
        "--classified",
        default=DEFAULT_CLASSIFIED,
        help=f"classification JSON for base-to-diff skip expansion (default: {DEFAULT_CLASSIFIED})",
    )
    parser.add_argument(
        "--skip",
        default=DEFAULT_SKIP,
        help=f"skip-list JSON path (default: {DEFAULT_SKIP})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    export_dir = Path(args.export_dir)
    if not export_dir.is_dir():
        print(f"error: export directory not found: {export_dir}", file=sys.stderr)
        return 1

    classified = _read_json(Path(args.classified))
    if not isinstance(classified, dict):
        classified = None
    skip_list = SkipList.load(args.skip)
    data = build_npc_avatar_map(export_dir, skip=skip_list, classified=classified)
    characters = len(data)
    images = sum(len(item[1]) for item in data.values())

    if args.output == "-":
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        output = Path(args.output)
        write_npc_json(data, output)
        print(f"written: {output}")

    print(f"characters: {characters}  images: {images}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
