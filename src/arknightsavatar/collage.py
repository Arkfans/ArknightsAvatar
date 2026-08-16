"""Diff avatar collage (差分拼贴).

Reads the characters classification report and the extracted avatar PNGs under
``data/export/<character>/``, then builds one contact-sheet PNG per character
containing every diff avatar (180x180) in a grid. Mirrors the old project's
``NpcData.draw_all_face`` (black background, white tiles, per-tile diff label,
``[x]`` placeholder for missing tiles).

Skipped bases: diffs belonging to a deduplicated (``dropped``) base are
detected via the extract report (``data/recognition/avatar_extract.json``) and
omitted by default, even when a leftover avatar still exists in ``data/export``.
``--show-skipped`` renders them again, labelled ``[skipped]`` (using the avatar
when available, otherwise a white placeholder). Other missing diffs keep the
``[x]`` marker.

CLI: ``arknightsavatar-collage``. Only depends on Pillow.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from arknightsavatar import paths
from arknightsavatar.skip import DEFAULT_SKIP, SkipList

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - optional dependency
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFont = None  # type: ignore[assignment]

DEFAULT_CLASSIFIED = paths.CLASSIFIED
DEFAULT_EXPORT_DIR = paths.EXPORT_DIR
DEFAULT_OUTPUT_DIR = paths.DIFF_COLLAGE_DIR
DEFAULT_EXTRACT_REPORT = paths.EXTRACT_REPORT
TILE_SIZE = 180
PADDING = 5
DEFAULT_COLUMNS = 3
FONT_SIZE = 15

STATS_KEYS = [
    "characters",
    "collaged",
    "skipped_no_diff",
    "skipped_no_export",
    "skipped_all",
    "missing_tiles",
    "skipped_omitted",
    "skipped_shown",
]


def _read_json(path: Path, default: object = None) -> object:
    try:
        with path.open("rt", encoding="utf8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _is_alpha_diff(diff_name: str) -> bool:
    """True when the diff file is the dedicated ``alpha.png`` alpha-channel texture."""
    return Path(diff_name).name.lower() == "alpha.png"


def collect_diff_names(item: dict) -> list[str]:
    """Flatten all diffs grouped under any base, excluding alpha.png, sorted.

    ``item`` is one ``characters.<name>`` entry of the classification report
    (``{"bases": {<base>: {"diff": [...]}, ...}}``). Bases and unassigned diffs
    are intentionally not included: extract only exports grouped diffs.
    """
    if not isinstance(item, dict):
        return []
    bases = item.get("bases")
    if not isinstance(bases, dict):
        return []
    names: list[str] = []
    for base_entry in bases.values():
        if not isinstance(base_entry, dict):
            continue
        diffs = base_entry.get("diff")
        if isinstance(diffs, list):
            names.extend(
                d for d in diffs if isinstance(d, str) and not _is_alpha_diff(d)
            )
    return sorted(set(names))


def load_skipped(extract_report_path: Path) -> dict[str, set[str]]:
    """Map character -> set of diff names skipped by extract (dropped base).

    Reads ``characters.<name>.diffs.*`` from the extract report; diffs whose
    status is ``dropped`` belong to a deduplicated (skipped) base and are not
    part of the display set. Missing/invalid reports yield an empty map.
    """
    payload = _read_json(extract_report_path)
    if not isinstance(payload, dict):
        return {}
    characters = payload.get("characters")
    if not isinstance(characters, dict):
        return {}
    skipped: dict[str, set[str]] = {}
    for name, info in characters.items():
        if not isinstance(info, dict):
            continue
        diffs = info.get("diffs")
        if not isinstance(diffs, dict):
            continue
        dropped = {
            diff_name
            for diff_name, entry in diffs.items()
            if isinstance(entry, dict) and entry.get("status") == "dropped"
        }
        if dropped:
            skipped[name] = dropped
    return skipped


def load_tiles(
    char_dir: Path, diff_names: Sequence[str]
) -> list[tuple[str, Image.Image | None]]:
    """Load each diff avatar; unreadable/missing files become ``None`` placeholders."""
    tiles: list[tuple[str, Image.Image | None]] = []
    for diff_name in diff_names:
        try:
            with Image.open(char_dir / diff_name) as image:
                tiles.append((diff_name, image.convert("RGBA")))
        except Exception:  # noqa: BLE001 - unreadable tile becomes a placeholder
            tiles.append((diff_name, None))
    return tiles


def build_collage(
    tiles: Sequence[tuple[str, Image.Image | None, str]],
    columns: int = DEFAULT_COLUMNS,
    *,
    label: bool = True,
    font: ImageFont.ImageFont | None = None,
) -> Image.Image:
    """Build one RGBA contact sheet: black background, white tiles, optional labels.

    Each tile is ``(diff_name, image_or_None, suffix)``; ``suffix`` (e.g. ``[x]``
    / ``[skipped]``, empty for normal tiles) is appended to the label. Images
    are resized to ``TILE_SIZE`` x ``TILE_SIZE`` and pasted with their own alpha
    as mask; missing images keep a white tile.
    """
    if columns < 1:
        raise ValueError("columns must be >= 1")
    count = len(tiles)
    rows = math.ceil(count / columns) if count else 0
    width = columns * TILE_SIZE + (columns + 1) * PADDING
    height = rows * TILE_SIZE + (rows + 1) * PADDING
    collage = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    white = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (255, 255, 255, 255))
    draw = ImageDraw.Draw(collage) if label else None
    for index, (diff_name, tile, suffix) in enumerate(tiles):
        column = index % columns
        row = index // columns
        box = (
            column * TILE_SIZE + (column + 1) * PADDING,
            row * TILE_SIZE + (row + 1) * PADDING,
        )
        collage.paste(white, box)
        if tile is not None:
            if tile.size != (TILE_SIZE, TILE_SIZE):
                tile = tile.resize((TILE_SIZE, TILE_SIZE), Image.LANCZOS)
            collage.paste(tile, box, tile)
        if draw is not None:
            draw.text(
                (box[0] + PADDING, box[1]),
                diff_name + suffix,
                fill=(0, 0, 0),
                font=font,
            )
    return collage


@dataclass
class CharacterCollage:
    """Outcome of collaging one character.``"""

    name: str
    diff_count: int = 0
    missing: int = 0
    skipped_omitted: int = 0
    skipped_shown: int = 0
    output: Path | None = None
    skipped: str | None = field(default=None)  # "no_diff" | "no_export" | "all_skipped"


def process_character(
    name: str,
    item: dict,
    *,
    export_dir: Path,
    output_dir: Path,
    columns: int = DEFAULT_COLUMNS,
    label: bool = True,
    font: ImageFont.ImageFont | None = None,
    skipped: set[str] | None = None,
    show_skipped: bool = False,
) -> CharacterCollage:
    """Build one character's diff collage and save ``<name>.png``.

    ``skipped`` lists diffs belonging to a dropped (skipped) base per the extract
    report: they are omitted by default (even when a leftover avatar exists), or
    rendered labelled ``[skipped]`` when ``show_skipped`` is set. Other missing
    diffs keep ``[x]``.
    """
    diff_names = collect_diff_names(item)
    if not diff_names:
        return CharacterCollage(name=name, skipped="no_diff")
    char_dir = export_dir / name
    if not char_dir.is_dir():
        return CharacterCollage(
            name=name, diff_count=len(diff_names), skipped="no_export"
        )
    skipped = skipped or set()
    loaded = load_tiles(char_dir, diff_names)

    tiles: list[tuple[str, Image.Image | None, str]] = []
    missing = 0
    skipped_omitted = 0
    skipped_shown = 0
    for diff_name, tile in loaded:
        is_skipped = diff_name in skipped
        if is_skipped and not show_skipped:
            skipped_omitted += 1
        elif is_skipped:
            skipped_shown += 1
            tiles.append((diff_name, tile, "[skipped]"))
        elif tile is not None:
            tiles.append((diff_name, tile, ""))
        else:
            missing += 1
            tiles.append((diff_name, None, "[x]"))
    if not tiles:
        return CharacterCollage(
            name=name,
            diff_count=len(diff_names),
            skipped="all_skipped",
            skipped_omitted=skipped_omitted,
            skipped_shown=skipped_shown,
        )

    collage = build_collage(tiles, columns, label=label, font=font)
    out_path = output_dir / f"{name}.png"
    output_dir.mkdir(parents=True, exist_ok=True)
    collage.save(out_path)
    return CharacterCollage(
        name=name,
        diff_count=len(diff_names),
        missing=missing,
        skipped_omitted=skipped_omitted,
        skipped_shown=skipped_shown,
        output=out_path,
    )


def process_characters(
    classified: dict,
    export_dir: Path,
    output_dir: Path,
    *,
    columns: int = DEFAULT_COLUMNS,
    label: bool = True,
    font: ImageFont.ImageFont | None = None,
    character: str | None = None,
    limit: int = 0,
    skipped: dict[str, set[str]] | None = None,
    show_skipped: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
    skip: SkipList | None = None,
) -> dict[str, int]:
    """Collage every (selected) character and aggregate stats."""
    skip = skip or SkipList()
    classified = skip.filter_classified(classified)
    characters = classified.get("characters")
    if not isinstance(characters, dict):
        raise ValueError(  # noqa: TRY004 - 保持既有 API 约定的 ValueError 语义
            "invalid classified report: missing 'characters'"
        )
    names = sorted(characters)
    if character is not None:
        names = [character] if character in characters else []
    if limit:
        names = names[:limit]

    skipped = skipped or {}
    stats = {key: 0 for key in STATS_KEYS}
    stats["characters"] = len(names)
    for index, name in enumerate(names, 1):
        result = process_character(
            name,
            characters[name],
            export_dir=export_dir,
            output_dir=output_dir,
            columns=columns,
            label=label,
            font=font,
            skipped=skipped.get(name),
            show_skipped=show_skipped,
        )
        stats["missing_tiles"] += result.missing
        stats["skipped_omitted"] += result.skipped_omitted
        stats["skipped_shown"] += result.skipped_shown
        if result.skipped is None:
            stats["collaged"] += 1
            message = f"{name} ({result.diff_count} diffs) -> {result.output}"
        else:
            stats[f"skipped_{result.skipped}"] += 1
            message = f"{name} skipped ({result.skipped})"
        if progress is not None:
            progress(index, len(names), message)
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-collage",
        description="Collage every character's diff avatars (差分) into one PNG per character.",
    )
    parser.add_argument(
        "--classified",
        default=DEFAULT_CLASSIFIED,
        help=f"classification JSON path (default: {DEFAULT_CLASSIFIED})",
    )
    parser.add_argument(
        "--export-dir",
        default=DEFAULT_EXPORT_DIR,
        help=f"extracted avatars directory (default: {DEFAULT_EXPORT_DIR})",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"collage output folder (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--extract-report",
        default=DEFAULT_EXTRACT_REPORT,
        help=f"extract report for skipped-base detection (default: {DEFAULT_EXTRACT_REPORT})",
    )
    parser.add_argument(
        "--columns",
        type=int,
        default=DEFAULT_COLUMNS,
        help=f"number of grid columns (default: {DEFAULT_COLUMNS})",
    )
    parser.add_argument(
        "--character",
        default=None,
        help="only collage the given character (default: all characters)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="only process the first N characters (default: all)",
    )
    parser.add_argument(
        "--no-label",
        action="store_true",
        help="do not draw diff name labels on tiles",
    )
    parser.add_argument(
        "--show-skipped",
        action="store_true",
        help="render skipped-base diffs as [skipped] tiles (default: omit them)",
    )
    parser.add_argument(
        "--font",
        default=None,
        help="TTF/TTC font path for labels (default: PIL built-in font)",
    )
    parser.add_argument(
        "--skip",
        default=DEFAULT_SKIP,
        help=f"skip-list JSON path (default: {DEFAULT_SKIP})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if Image is None:
        print("error: Pillow is required (uv sync --extra unpack)", file=sys.stderr)
        return 1

    args = build_parser().parse_args(argv)
    if args.columns < 1:
        print(f"error: --columns must be >= 1 (got {args.columns})", file=sys.stderr)
        return 1
    if args.limit < 0:
        print(f"error: --limit must be >= 0 (got {args.limit})", file=sys.stderr)
        return 1

    classified_path = Path(args.classified)
    if not classified_path.is_file():
        print(f"error: classified report not found: {classified_path}", file=sys.stderr)
        return 1
    classified = _read_json(classified_path)
    if not isinstance(classified, dict) or not isinstance(
        classified.get("characters"), dict
    ):
        print(f"error: invalid classified report: {classified_path}", file=sys.stderr)
        return 1
    if args.character is not None and args.character not in classified["characters"]:
        print(
            f"error: character not found in {classified_path}: {args.character}",
            file=sys.stderr,
        )
        return 1

    skip_list = SkipList.load(args.skip)
    extract_report_path = Path(args.extract_report)
    skipped: dict[str, set[str]] = {}
    if extract_report_path.is_file():
        skipped = load_skipped(extract_report_path)
    elif args.extract_report != DEFAULT_EXTRACT_REPORT:
        print(
            f"error: extract report not found: {extract_report_path}", file=sys.stderr
        )
        return 1

    font = None
    if args.font:
        try:
            font = ImageFont.truetype(args.font, FONT_SIZE)
        except OSError as error:
            print(f"error: cannot load font {args.font}: {error}", file=sys.stderr)
            return 1

    export_dir = Path(args.export_dir)
    if not export_dir.is_dir():
        print(f"error: export directory not found: {export_dir}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    stats = process_characters(
        classified,
        export_dir,
        output_dir,
        columns=args.columns,
        label=not args.no_label,
        font=font,
        character=args.character,
        limit=args.limit,
        skipped=skipped,
        show_skipped=args.show_skipped,
        progress=lambda index, total, message: print(f"[{index}/{total}] {message}"),
        skip=skip_list,
    )
    print(
        f"collaged: {stats['collaged']}  skipped_no_diff: {stats['skipped_no_diff']}  "
        f"skipped_no_export: {stats['skipped_no_export']}  skipped_all: {stats['skipped_all']}  "
        f"missing_tiles: {stats['missing_tiles']}"
    )
    print(
        f"skipped_omitted: {stats['skipped_omitted']}  skipped_shown: {stats['skipped_shown']}"
    )
    print(f"output: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
