"""Convert extracted avatar PNGs to WebP (``data/export`` -> ``data/export_webp``).

Walks every PNG under ``data/export/<character>/`` and writes a WebP copy into
``data/export_webp/<character>/`` preserving the folder layout. Transparency is
kept (PNG is decoded as RGBA before saving). Incremental behavior: an output
``.webp`` that already exists is skipped unless ``--force``.

CLI: ``arknightsavatar-export-webp``. Only depends on Pillow.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from arknightsavatar import paths
from arknightsavatar.skip import DEFAULT_SKIP, SkipList

try:
    from PIL import Image
except ImportError:  # pragma: no cover - optional dependency
    Image = None  # type: ignore[assignment]

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - optional dependency
    tqdm = None  # type: ignore[assignment]

DEFAULT_EXPORT_DIR = paths.EXPORT_DIR
DEFAULT_OUTPUT_DIR = paths.EXPORT_WEBP_DIR
DEFAULT_QUALITY = 80
DEFAULT_METHOD = 4  # Pillow WebP default compression method (0-6)
DEFAULT_CLASSIFIED = paths.CLASSIFIED

STATS_KEYS = ["characters", "images", "converted", "skipped", "failed"]


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


def iter_pngs(char_dir: Path) -> list[Path]:
    """Return all PNG files under a character directory, sorted by name."""
    return sorted(char_dir.rglob("*.png"))


def convert_image(png_path: Path, webp_path: Path, quality: int, method: int) -> bool:
    """Convert one PNG to WebP, returning True on success.

    Raises the underlying exception when the source is unreadable or the WebP
    write fails; callers decide whether to count it as a failure.
    """
    webp_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(png_path) as image:
        image.convert("RGBA").save(
            webp_path,
            format="WEBP",
            quality=quality,
            method=method,
        )
    return True


def convert_characters(
    export_dir: Path,
    output_dir: Path,
    *,
    characters: Sequence[str] | None = None,
    limit: int = 0,
    force: bool = False,
    quality: int = DEFAULT_QUALITY,
    method: int = DEFAULT_METHOD,
    progress: Callable[[int, int, str], None] | None = None,
    skip: SkipList | None = None,
    classified: dict | None = None,
) -> dict[str, int]:
    """Convert PNG avatars from ``export_dir`` into ``output_dir``.

    ``characters`` optionally restricts processing to the given character
    names; ``limit`` caps how many (sorted) characters are processed. Existing
    WebP outputs are skipped unless ``force``. Returns stats with keys in
    ``STATS_KEYS``.
    """
    stats = {key: 0 for key in STATS_KEYS}
    skip = skip or SkipList()
    skipped_characters, skipped_stems = skip.expand(classified)
    names = [p.name for p in iter_character_dirs(export_dir)]
    names = [name for name in names if name.casefold() not in skipped_characters]
    if characters is not None:
        wanted = set(characters)
        names = [name for name in names if name in wanted]
    if limit:
        names = names[:limit]

    total = len(names)
    for index, name in enumerate(names, start=1):
        char_dir = export_dir / name
        out_dir = output_dir / name
        pngs = [
            png_path
            for png_path in iter_pngs(char_dir)
            if png_path.stem.casefold() not in skipped_stems.get(name.casefold(), set())
        ]
        stats["characters"] += 1
        stats["images"] += len(pngs)
        converted = skipped = failed = 0
        for png_path in pngs:
            webp_path = out_dir / png_path.relative_to(char_dir).with_suffix(".webp")
            if not force and webp_path.is_file():
                skipped += 1
                continue
            try:
                convert_image(png_path, webp_path, quality, method)
                converted += 1
            except Exception:  # noqa: BLE001 - one bad image must not stop the batch
                failed += 1
        stats["converted"] += converted
        stats["skipped"] += skipped
        stats["failed"] += failed
        if progress is not None:
            message = f"{name}: +{converted} ={skipped} !{failed}"
            progress(index, total, message)
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-export-webp",
        description="Convert extracted avatar PNGs in data/export to WebP in data/export_webp.",
    )
    parser.add_argument(
        "--export-dir",
        default=DEFAULT_EXPORT_DIR,
        help=f"source PNG directory (default: {DEFAULT_EXPORT_DIR})",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"WebP output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=DEFAULT_QUALITY,
        help=f"WebP quality 0-100 (default: {DEFAULT_QUALITY})",
    )
    parser.add_argument(
        "--method",
        type=int,
        default=DEFAULT_METHOD,
        help=f"WebP compression effort 0-6, slower is smaller (default: {DEFAULT_METHOD})",
    )
    parser.add_argument(
        "--character",
        action="append",
        default=None,
        help="only convert the given character (repeatable; default: all characters)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="only process the first N characters (default: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-convert even when the WebP output already exists",
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


def _make_progress(
    total: int,
) -> tuple[Callable[[int, int, str], None], Callable[[], None]]:
    if tqdm is not None:
        bar = tqdm(
            total=total, unit="character", desc="convert webp", dynamic_ncols=True
        )

        def progress(index: int, total_count: int, label: str) -> None:
            bar.set_postfix_str(label)
            bar.update(1)

        return progress, bar.close

    def progress(index: int, total_count: int, label: str) -> None:
        print(f"[{index}/{total_count}] {label}")

    return progress, lambda: None


def main(argv: list[str] | None = None) -> int:
    if Image is None:
        print("error: Pillow is required (uv sync --extra unpack)", file=sys.stderr)
        return 1

    args = build_parser().parse_args(argv)
    if not 0 <= args.quality <= 100:
        print(
            f"error: --quality must be between 0 and 100 (got {args.quality})",
            file=sys.stderr,
        )
        return 1
    if not 0 <= args.method <= 6:
        print(
            f"error: --method must be between 0 and 6 (got {args.method})",
            file=sys.stderr,
        )
        return 1
    if args.limit < 0:
        print(f"error: --limit must be >= 0 (got {args.limit})", file=sys.stderr)
        return 1

    export_dir = Path(args.export_dir)
    if not export_dir.is_dir():
        print(f"error: export directory not found: {export_dir}", file=sys.stderr)
        return 1
    classified = _read_json(Path(args.classified))
    if not isinstance(classified, dict):
        classified = None
    skip_list = SkipList.load(args.skip)
    names = [p.name for p in iter_character_dirs(export_dir)]
    skipped_characters, _ = skip_list.expand(classified)
    names = [name for name in names if name.casefold() not in skipped_characters]
    if args.character is not None:
        unknown = [name for name in args.character if name not in names]
        if unknown:
            print(
                f"error: character not found in {export_dir}: {', '.join(unknown)}",
                file=sys.stderr,
            )
            return 1
        names = [name for name in names if name in set(args.character)]
    if args.limit:
        names = names[: args.limit]

    output_dir = Path(args.output_dir)
    progress, close_progress = _make_progress(len(names))
    try:
        stats = convert_characters(
            export_dir,
            output_dir,
            characters=args.character,
            limit=args.limit,
            force=args.force,
            quality=args.quality,
            method=args.method,
            progress=progress,
            skip=skip_list,
            classified=classified,
        )
    finally:
        close_progress()

    print(
        f"characters: {stats['characters']}  images: {stats['images']}  "
        f"converted: {stats['converted']}  skipped: {stats['skipped']}  "
        f"failed: {stats['failed']}"
    )
    print(f"output: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
