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

CLI: ``npcavatar-npc-json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_EXPORT_DIR = "data/export"
DEFAULT_OUTPUT_FILE = "data/arknights_npc.json"


def iter_character_dirs(export_dir: Path) -> list[Path]:
    """Return the per-character subdirectories of ``export_dir``, sorted."""
    if not export_dir.is_dir():
        return []
    return sorted(p for p in export_dir.iterdir() if p.is_dir())


def iter_png_stems(char_dir: Path) -> list[str]:
    """Return the sorted stems of PNG files directly under ``char_dir``."""
    return sorted(p.stem for p in char_dir.glob("*.png") if p.is_file())


def build_npc_avatar_map(export_dir: Path) -> dict[str, list]:
    """Build the legacy ``{npc_id: [[], [avatars], ["npc"]]}`` mapping."""
    data: dict[str, list] = {}
    for char_dir in iter_character_dirs(export_dir):
        data[char_dir.name] = [
            [],
            iter_png_stems(char_dir),
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
        prog="npcavatar-npc-json",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    export_dir = Path(args.export_dir)
    if not export_dir.is_dir():
        print(f"error: export directory not found: {export_dir}", file=sys.stderr)
        return 1

    data = build_npc_avatar_map(export_dir)
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
