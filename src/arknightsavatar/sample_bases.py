from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from arknightsavatar import paths

DEFAULT_CLASSIFIED = paths.CLASSIFIED
DEFAULT_OUTPUT_DIR = paths.BASES_SAMPLE_DIR


@dataclass
class SampleResult:
    """一次抽样复制的结果。"""

    selected: list[str]
    eligible: int
    copied: list[Path] = field(default_factory=list)
    missing: list[tuple[str, str]] = field(default_factory=list)
    collisions: list[tuple[str, str]] = field(default_factory=list)


def select_characters(
    report: dict, count: int, seed: int | None
) -> tuple[list[str], int]:
    """从报告中随机抽取 count 个至少含一张底图的角色，返回 (选中列表, 可选总数)。"""
    characters = report.get("characters", {})
    eligible = sorted(
        char_id
        for char_id, info in characters.items()
        if isinstance(info, dict) and info.get("bases")
    )
    if count >= len(eligible):
        return eligible, len(eligible)
    rng = random.Random(seed)
    return rng.sample(eligible, count), len(eligible)


def copy_bases(
    report: dict,
    selected: list[str],
    characters_dir: Path,
    output_dir: Path,
) -> tuple[list[Path], list[tuple[str, str]], list[tuple[str, str]]]:
    """把每个选中角色的底图展平复制到 output_dir/，返回 (已复制, 缺失, 改名冲突)。"""
    copied: list[Path] = []
    missing: list[tuple[str, str]] = []
    collisions: list[tuple[str, str]] = []
    used: set[str] = set()
    characters = report.get("characters", {})
    for char_id in selected:
        info = characters.get(char_id, {})
        base_names = sorted(info.get("bases", {}))
        for base_name in base_names:
            src = characters_dir / char_id / base_name
            if not src.is_file():
                missing.append((char_id, base_name))
                continue
            target_name = base_name
            if target_name.lower() in used:
                target_name = f"{char_id}_{base_name}"
                collisions.append((char_id, base_name))
            used.add(target_name.lower())
            dst = output_dir / target_name
            output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(dst)
    return copied, missing, collisions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-sample-bases",
        description="Randomly sample characters and copy their base images (底图) to a new folder.",
    )
    parser.add_argument(
        "--classified",
        default=DEFAULT_CLASSIFIED,
        help=f"classification JSON path (default: {DEFAULT_CLASSIFIED})",
    )
    parser.add_argument(
        "--characters-dir",
        default=None,
        help="characters directory override (default: value stored in the classification JSON)",
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=100,
        help="number of characters to sample (default: 100)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"destination folder (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="random seed for reproducible sampling",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    classified = Path(args.classified)
    if not classified.is_file():
        print(f"error: classification JSON not found: {classified}", file=sys.stderr)
        return 1
    try:
        report = json.loads(classified.read_text(encoding="utf8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {classified}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(report.get("characters"), dict):
        print(f"error: no 'characters' object in {classified}", file=sys.stderr)
        return 1

    characters_dir = Path(
        args.characters_dir
        or report.get("characters_dir")
        or paths.UNPACKED_CHARACTERS_DIR
    )
    if not characters_dir.is_dir():
        print(
            f"error: characters directory not found: {characters_dir}", file=sys.stderr
        )
        return 1
    if args.count < 1:
        print("error: --count must be >= 1", file=sys.stderr)
        return 1

    selected, eligible = select_characters(report, args.count, args.seed)
    if not selected:
        print("error: no characters with base images in the report", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    copied, missing, collisions = copy_bases(
        report, selected, characters_dir, output_dir
    )

    seed_note = f" (seed={args.seed})" if args.seed is not None else ""
    print(f"sampled {len(selected)}/{eligible} characters{seed_note} -> {output_dir}")
    print(f"base files copied: {len(copied)}")
    if collisions:
        print(f"same-name collisions renamed with <char_id>_ prefix: {len(collisions)}")
        for char_id, base_name in collisions:
            print(f"  {char_id}/{base_name} -> {char_id}_{base_name}")
    if missing:
        print(f"missing source files: {len(missing)}")
        for char_id, base_name in missing:
            print(f"  {char_id}/{base_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
