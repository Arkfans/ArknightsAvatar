from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from arknightsavatar import paths, reporting

ID_PREFIXES = ("avg_", "char_", "avgnew_", "npc_")
INDEX_SEPARATORS = ("#", "_", "$")

DEFAULT_CHARACTERS_DIR = paths.UNPACKED_CHARACTERS_DIR
DEFAULT_OUTPUT = paths.CLASSIFIED

try:
    from PIL import Image
except ImportError:  # pragma: no cover - optional dependency
    Image = None  # type: ignore[assignment]


def _strip_png(name: str) -> str:
    """Strip literal trailing .png (some texture names carry it)."""
    while name.lower().endswith(".png"):
        name = name[:-4]
    return name


def _id_like(name: str) -> bool:
    """Texture name looks like a character id (case-insensitive)."""
    return name.lower().startswith(ID_PREFIXES)


def _lcp(a: str, b: str) -> str:
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return a[:i]


def _dollar_index(name: str) -> str | None:
    mm = re.search(r"\$(\d+)$", name)
    return mm.group(1) if mm else None


def _is_seq1(name: str) -> bool:
    """序号是否为 1：$n 均为底图切分；_n/#n 仅 n==1；裸名视为 1。"""
    if _dollar_index(name) is not None:
        return True
    mm = re.search(r"[#_](\d+)$", name)
    if mm:
        return mm.group(1) == "1"
    return True


def _find_base_names(names: list[str], dirname: str) -> list[str]:
    """Find logical base-texture names for one character directory.

    Rule (validated over all 1779 directories):
    - A single id-like texture is the base (covers renamed bases such as
      char_2006_fmzuki_1 inside char_2006_weiywfmzuki_1).
    - Otherwise use the longest common prefix of id-like textures as the
      character root. Base = bare root (if present; then every numbered
      variant is a diff), root_1/root#1, or all root$<n> atlas parts.
    - Fallback: the texture equal to the directory name (mixed-character
      bundles such as char_190_clour containing cameo sprites).
    """
    ids = [n for n in names if _id_like(n)]
    if not ids:
        return []
    if len(ids) == 1:
        return [ids[0]] if _is_seq1(ids[0]) else []

    prefix = ids[0]
    for n in ids[1:]:
        prefix = _lcp(prefix, n)
    root = prefix[:-1] if prefix and prefix[-1] in INDEX_SEPARATORS else prefix

    lower_map = {n.lower(): n for n in names}
    bases: list[str] = []
    bare = [n for n in names if n.lower() == root.lower()]
    if bare:
        bases.extend(bare)
        bases.extend(n for n in names if n.lower().startswith(root.lower() + "$"))
    else:
        for candidate in (root + "_1", root + "#1"):
            matched = lower_map.get(candidate.lower())
            if matched is not None:
                bases.append(matched)
        bases.extend(n for n in names if n.lower().startswith(root.lower() + "$"))

    if not bases:
        matched = lower_map.get(dirname.lower())
        if matched is not None:
            bases.append(matched)
    return sorted(set(bases))


@dataclass
class CharacterClassification:
    """底图/差分划分：差分按所属底图分组。"""

    name: str
    bases: dict[str, list[str]] = field(default_factory=dict)
    unassigned: list[str] = field(default_factory=list)
    status: str = "ok"
    sizes: dict[str, list[int] | None] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "bases": {base: {"diff": diffs} for base, diffs in self.bases.items()},
            "unassigned": self.unassigned,
            "sizes": self.sizes,
        }


@dataclass
class ClassificationReport:
    """整个 characters 目录的分类报告。"""

    characters_dir: str
    generated_at: str
    characters: dict[str, CharacterClassification]
    stats: dict[str, int]

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "characters_dir": self.characters_dir,
            "stats": self.stats,
            "characters": {
                name: item.as_dict() for name, item in self.characters.items()
            },
        }


def _load_meta_textures(char_dir: Path) -> dict[str, list[int]]:
    """Read texture sizes from meta.json; empty dict when missing/broken."""
    meta = char_dir / "meta.json"
    if not meta.exists():
        return {}
    try:
        payload = json.loads(meta.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    textures = payload.get("textures")
    if not isinstance(textures, dict):
        return {}
    result: dict[str, list[int]] = {}
    for key, size in textures.items():
        if (
            isinstance(size, list)
            and len(size) == 2
            and all(isinstance(v, int) for v in size)
        ):
            result[str(key)] = size
    return result


def _image_size(path: Path) -> list[int] | None:
    """Read image size via Pillow; null when unavailable (does not affect classes)."""
    if Image is None:
        return None
    try:
        with Image.open(path) as image:
            return [image.width, image.height]
    except Exception:  # noqa: BLE001 - unreadable images keep null
        return None


def classify_character_dir(char_dir: Path) -> CharacterClassification:
    """Classify one character directory; diffs are grouped by their base."""
    name = char_dir.name
    files = sorted(p.name for p in char_dir.glob("*.png") if p.is_file())
    if not files:
        return CharacterClassification(name=name, status="empty")

    meta_sizes = _load_meta_textures(char_dir)
    sizes: dict[str, list[int] | None] = {}
    for file_name in files:
        stem = file_name[: -len(".png")]
        size = meta_sizes.get(stem)
        if size is None:
            size = _image_size(char_dir / file_name)
        sizes[file_name] = size

    if len(files) == 1:
        # 仅有一张图片时，无论名称如何都视为底图
        return CharacterClassification(
            name=name,
            bases={files[0]: []},
            status="ok",
            sizes=sizes,
        )

    logical = {f: _strip_png(f) for f in files}
    base_logical = set(_find_base_names(sorted(set(logical.values())), name))

    bases: dict[str, list[str]] = {}
    unassigned: list[str] = []
    if not base_logical:
        # 兜底：仍无底图时，取字符串排序最小的文件作为底图（如 char_242_mayer#2）
        bases[files[0]] = files[1:]
        status = "ok"
    elif len(base_logical) == 1:
        base_file = next(f for f in files if logical[f] in base_logical)
        bases[base_file] = [f for f in files if logical[f] not in base_logical]
        status = "ok"
    else:
        base_by_dollar: dict[str, str] = {}
        for f in files:
            if logical[f] in base_logical:
                bases[f] = []
                idx = _dollar_index(logical[f])
                if idx is not None:
                    base_by_dollar[idx] = f
        for f in files:
            if logical[f] in base_logical:
                continue
            idx = _dollar_index(logical[f])
            if idx is not None and idx in base_by_dollar:
                bases[base_by_dollar[idx]].append(f)
            else:
                unassigned.append(f)
        status = "ok"

    return CharacterClassification(
        name=name,
        bases=bases,
        unassigned=unassigned,
        status=status,
        sizes=sizes,
    )


def classify_characters(characters_dir: Path) -> ClassificationReport:
    """Classify all characters and aggregate stats."""
    stats = {
        "total": 0,
        "ok": 0,
        "empty": 0,
        "no_base": 0,
        "base_files": 0,
        "diff_files": 0,
    }
    characters: dict[str, CharacterClassification] = {}
    for char_dir in sorted(p for p in characters_dir.iterdir() if p.is_dir()):
        classification = classify_character_dir(char_dir)
        characters[classification.name] = classification
        stats["total"] += 1
        stats[classification.status] += 1
        stats["base_files"] += len(classification.bases)
        stats["diff_files"] += sum(
            len(diffs) for diffs in classification.bases.values()
        )
        stats["diff_files"] += len(classification.unassigned)
    return ClassificationReport(
        characters_dir=str(characters_dir),
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        characters=characters,
        stats=stats,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-classify",
        description="Classify characters sprites into base images (底图) and diffs (差分).",
    )
    parser.add_argument(
        "--characters-dir",
        default=DEFAULT_CHARACTERS_DIR,
        help=f"unpacked characters directory (default: {DEFAULT_CHARACTERS_DIR})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"JSON report path, or '-' for stdout (default: {DEFAULT_OUTPUT})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    characters_dir = Path(args.characters_dir)
    if not characters_dir.is_dir():
        print(
            f"error: characters directory not found: {characters_dir}", file=sys.stderr
        )
        return 1

    report = classify_characters(characters_dir)
    stats = report.stats
    print(
        f"characters: {stats['total']}  ok: {stats['ok']}  empty: {stats['empty']}  "
        f"no_base: {stats['no_base']}"
    )
    print(f"base_files: {stats['base_files']}  diff_files: {stats['diff_files']}")

    payload = report.as_dict()
    reporting.write_report(payload, args.output)
    if args.output != "-":
        print(f"report written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
