from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from PIL import Image

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - optional dependency
    tqdm = None  # type: ignore[assignment]

from .. import paths
from ..config import CATEGORIES, load_config
from ..manifest import FailureLog, Manifest
from ..util import sha256_file
from .ab import AbParse


def _write_meta(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wt", encoding="utf8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _safe_filename(basename: str) -> str:
    """Replace ``..`` in a generated file basename with ``.``.

    Unity 资源名可能含连续句点或以 ``.`` 结尾，直接拼上 ``.png`` 会得到形如
    ``xxx..png`` 的文件名；这里把 ``..`` 折叠为单个 ``.``。
    """
    while ".." in basename:
        basename = basename.replace("..", ".")
    return basename


def unpack_one(
    ab_path: Path,
    unpacked_dir: Path,
    category: str,
    rel: str,
    source_sha: str,
    parser_cls=AbParse,
) -> dict[str, int]:
    """Unpack one AB file into unpacked_dir. Returns a stats dict."""
    parsed = parser_cls(ab_path)
    merged = parsed.merged_images()
    meta = {
        "source": {"rel": rel, "sha256": source_sha},
        "textures": {name: list(image.size) for name, image in merged.items()},
        "sprites": list(parsed.sprites.keys()),
        "face_groups": parsed.face_groups,
    }

    if category == "avatars":
        # 小头像扁平存放，bundle 元数据归到 _meta/ 下避免与头像文件冲突
        avatars_dir = unpacked_dir / "avatars"
        avatars_dir.mkdir(parents=True, exist_ok=True)
        for sprite_name in parsed.sprites:
            if not sprite_name.lower().startswith("char_"):
                # 只保留 char_* 角色头像，其余素材（trap_*/sp_char_* 等）忽略
                continue
            image = merged.get(sprite_name)
            if image is None:
                sprite = parsed.sprites[sprite_name]
                try:
                    image = sprite.image
                except Exception:  # noqa: BLE001, S112 - skip sprites without extractable image
                    continue
            if image.width != image.height:
                # 过滤半身像：char_portrait（180x360）、skin portrait（292x552）等
                # 非正方形竖版图不是角色头像，仅保留 180x180 正方形头像
                continue
            image.save(avatars_dir / _safe_filename(f"{sprite_name}.png"))
        _write_meta(avatars_dir / "_meta" / f"{ab_path.stem}.json", meta)
    else:
        item_dir = unpacked_dir / category / ab_path.stem
        item_dir.mkdir(parents=True, exist_ok=True)
        for name, image in merged.items():
            image.save(item_dir / _safe_filename(f"{name}.png"))
        _write_meta(item_dir / "meta.json", meta)

    return {
        "textures": len(merged),
        "sprites": len(parsed.sprites),
        "face_groups": len(parsed.face_groups),
    }


def _meta_exists(unpacked_dir: Path, category: str, ab_path: Path) -> bool:
    if category == "avatars":
        return (unpacked_dir / "avatars" / "_meta" / f"{ab_path.stem}.json").exists()
    return (unpacked_dir / category / ab_path.stem / "meta.json").exists()


def _is_square_png(path: Path) -> bool:
    """Check whether a PNG is square; unreadable files are kept to avoid误删."""
    try:
        with Image.open(path) as image:
            return image.width == image.height
    except Exception:  # noqa: BLE001 - 无法读取时保守保留
        return True


def _prune_avatars(unpacked_dir: Path) -> int:
    """Remove stale top-level PNGs that are not square char_* avatars."""
    avatars_dir = unpacked_dir / "avatars"
    if not avatars_dir.exists():
        return 0
    removed = 0
    for png in avatars_dir.glob("*.png"):
        if png.is_file() and (
            not png.name.lower().startswith("char_") or not _is_square_png(png)
        ):
            png.unlink()
            removed += 1
    return removed


def run_unpack(
    raw_dir: Path,
    unpacked_dir: Path,
    categories: list[str],
    force: bool = False,
    parser_cls=AbParse,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, dict[str, int]]:
    """Unpack every AB file under ``raw_dir`` into ``unpacked_dir``.

    ``progress``, when given, is called once per AB file as
    ``progress(index, total, label)`` with ``index`` starting at 1 (skipped
    and failed files advance the counter too).
    """
    manifest = Manifest.load(raw_dir / "manifest.json")
    progress_path = unpacked_dir / "_manifest.json"
    unpacked_dir.mkdir(parents=True, exist_ok=True)
    done: dict[str, str] = {}
    if progress_path.exists():
        with progress_path.open("rt", encoding="utf8") as f:
            done = json.load(f)
    failures = FailureLog.load(unpacked_dir / "_failed.json")
    stats = {
        category: {"listed": 0, "unpacked": 0, "skipped": 0, "failed": 0}
        for category in categories
    }
    dirty = 0

    # 先枚举全部 AB 文件以得到总进度；缺失的分类目录不计入总数
    category_paths: list[tuple[str, list[Path]]] = []
    for category in categories:
        category_dir = raw_dir / category
        if not category_dir.exists():
            continue
        ab_paths = sorted(category_dir.glob("*.ab"))
        stats[category]["listed"] = len(ab_paths)
        category_paths.append((category, ab_paths))
    total = sum(len(ab_paths) for _, ab_paths in category_paths)

    index = 0
    for category, ab_paths in category_paths:
        for ab_path in ab_paths:
            index += 1
            rel = f"{category}/{ab_path.name}"
            record = manifest.get(rel)
            source_sha = record.sha256 if record else sha256_file(ab_path)
            if progress is not None:
                progress(index, total, rel)
            if (
                not force
                and done.get(rel) == source_sha
                and _meta_exists(unpacked_dir, category, ab_path)
            ):
                stats[category]["skipped"] += 1
                continue
            try:
                unpack_one(
                    ab_path,
                    unpacked_dir,
                    category,
                    rel,
                    source_sha,
                    parser_cls=parser_cls,
                )
                done[rel] = source_sha
                stats[category]["unpacked"] += 1
            except Exception as error:  # noqa: BLE001 - record and continue
                failures.add(
                    rel,
                    source=record.source if record else "unknown",
                    error=f"{type(error).__name__}: {error}",
                )
                stats[category]["failed"] += 1
            dirty += 1
            if dirty % 20 == 0:
                with progress_path.open("wt", encoding="utf8") as f:
                    json.dump(done, f, ensure_ascii=False, indent=2)
                failures.save()
        if category == "avatars":
            # 产物契约：avatars 目录只保留 char_* 头像，其余遗留 PNG 一并清理
            _prune_avatars(unpacked_dir)

    with progress_path.open("wt", encoding="utf8") as f:
        json.dump(done, f, ensure_ascii=False, indent=2)
    failures.save()
    return stats


def _make_progress(
    total: int,
) -> tuple[Callable[[int, int, str], None], Callable[[], None]]:
    """Return (progress, close); prefer a tqdm bar, fall back to per-file text."""
    if tqdm is not None:
        bar = tqdm(total=total, unit="ab", desc="unpack ab", dynamic_ncols=True)

        def progress(index: int, total_count: int, label: str) -> None:
            bar.set_postfix_str(label)
            bar.update(1)

        return progress, bar.close

    def progress(index: int, total_count: int, label: str) -> None:
        print(f"[{index}/{total_count}] {label}")

    return progress, lambda: None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-unpack",
        description="Unpack AB resources into PNG + metadata.",
    )
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument(
        "--raw-dir", default=paths.RAW_DIR, help="Input AB cache directory"
    )
    parser.add_argument(
        "--unpacked-dir", default=paths.UNPACKED_DIR, help="Output directory"
    )
    parser.add_argument("--category", choices=[*CATEGORIES, "all"], default="all")
    parser.add_argument(
        "--force", action="store_true", help="Re-unpack even if manifest says done"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        load_config(args.config)
        categories = list(CATEGORIES) if args.category == "all" else [args.category]
        raw_dir = Path(args.raw_dir)
        # 先枚举全部 AB 文件算出总进度（缺失的分类目录不计入）
        total = 0
        for category in categories:
            category_dir = raw_dir / category
            if category_dir.exists():
                total += len(sorted(category_dir.glob("*.ab")))
        progress, close_progress = _make_progress(total)
        try:
            stats = run_unpack(
                raw_dir,
                Path(args.unpacked_dir),
                categories,
                force=args.force,
                progress=progress,
            )
        finally:
            close_progress()
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    for category in categories:
        stat = stats[category]
        print(
            f"{category:11s} listed={stat['listed']:5d} unpacked={stat['unpacked']:5d} "
            f"skipped={stat['skipped']:5d} failed={stat['failed']:3d}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
