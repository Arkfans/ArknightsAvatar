from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]

MATCH_SIZE = 1024
BASE_INCREASE = 1
MIN_AVATAR_SIZE = 130
STOP_THRESHOLD = 0.6
MAX_OPTIMIZE_TIMES = 50
FIND_MAX_OPTIMIZE_TIMES = 10

AVG_NAME_RE = re.compile(r"^avg_\d+_.+", re.IGNORECASE)
CHAR_NAME_RE = re.compile(r"^char_\d+_.*", re.IGNORECASE)
CHAR_SEQ_RE = re.compile(r"^(?:avg|char)_(\d+)_", re.IGNORECASE)


def _is_target_character(name: str) -> bool:
    """角色名是否匹配 avg_\\d+_.+ 或 char_\\d+_.*（不区分大小写）。"""
    return bool(AVG_NAME_RE.match(name) or CHAR_NAME_RE.match(name))


def _char_seq(name: str) -> str | None:
    """从 avg_<id>_... / char_<id>_... 提取数字 ID。"""
    mm = CHAR_SEQ_RE.match(name)
    return mm.group(1) if mm else None


def _avatar_candidates(avatars_dir: Path, seq: str) -> list[str]:
    """avatars 目录下所有 char_<seq>_* 头像文件名（排序稳定）。"""
    if not avatars_dir.is_dir():
        return []
    pattern = re.compile(rf"^char_{re.escape(seq)}_", re.IGNORECASE)
    return sorted(p.name for p in avatars_dir.glob("*.png") if p.is_file() and pattern.match(p.name))


def _read_bgr(path: Path) -> np.ndarray:
    """读取图像为 BGR；RGBA 透明像素转黑，大面积白色转黑（避免白底干扰相关度）。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"cannot read image: {path}")
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 3:
        return image
    b, g, r, a = cv2.split(image)
    transparent = a == 0
    b[transparent] = 0
    g[transparent] = 0
    r[transparent] = 0
    image = cv2.merge([b, g, r])
    if _color_proportion(image, (255, 255, 255)) > 0.5:
        white = np.all(image == 255, axis=-1)
        image[white] = 0
    return image


def _color_proportion(image: np.ndarray, color: tuple[int, int, int]) -> float:
    mask = np.all(image == np.array(color), axis=-1)
    return float(np.sum(mask)) / (image.shape[0] * image.shape[1])


def _prepare_base(path: Path) -> tuple[np.ndarray, tuple[int, int]]:
    """底图灰度化并缩放到 MATCH_SIZE；返回 (gray, 原始尺寸 (w, h))。"""
    image = _read_bgr(path)
    size = (image.shape[1], image.shape[0])
    resized = cv2.resize(image, (MATCH_SIZE, MATCH_SIZE))
    return cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY), size


def _prepare_avatar(path: Path) -> np.ndarray:
    """头像灰度化（保持原始尺寸，缩放搜索在匹配内部进行）。"""
    image = _read_bgr(path)
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _template_match_gray(
    base: np.ndarray,
    avatar: np.ndarray,
    min_avatar_size: int = MIN_AVATAR_SIZE,
    stop_threshold: float = STOP_THRESHOLD,
) -> tuple[float, tuple[int, int, int, int]]:
    """移植自旧版：TM_CCOEFF_NORMED + 模板缩放搜索，返回 (threshold, box)。"""
    avatar_h, avatar_w = avatar.shape[:2]

    def _find(offset: int) -> np.ndarray:
        scaled = cv2.resize(avatar, (avatar_w + offset, avatar_h + offset))
        return cv2.matchTemplate(base, scaled, cv2.TM_CCOEFF_NORMED)

    def _check_valid_offset(offset: int) -> bool:
        return avatar_h + offset > min_avatar_size and avatar_w + offset > min_avatar_size

    def _optimize(o_offset: int, o_increase: int) -> bool:
        nonlocal offset, best_offset, res, best_threshold
        times = FIND_MAX_OPTIMIZE_TIMES if best_threshold > stop_threshold else MAX_OPTIMIZE_TIMES
        for _ in range(times):
            o_offset += o_increase
            if not _check_valid_offset(o_offset):
                return False
            o_res = _find(o_offset)
            if float(np.max(o_res)) > best_threshold:
                offset = o_offset
                best_offset = o_offset
                best_threshold = float(np.max(o_res))
                res = o_res
                return True
        return False

    offset = 0
    best_offset = 0
    res = _find(0)
    best_threshold = float(np.max(res))
    initial_threshold = best_threshold
    increase = BASE_INCREASE if float(np.max(_find(1))) > initial_threshold else -BASE_INCREASE
    if_reversed = False

    while True:
        offset += increase
        if not _check_valid_offset(offset):
            if not if_reversed and best_threshold < stop_threshold:
                if_reversed = True
                if _optimize(0, -increase):
                    increase = -increase
                    continue
            break
        current = _find(offset)
        if float(np.max(current)) > best_threshold:
            best_threshold = float(np.max(current))
            best_offset = offset
            res = current
        else:
            if _optimize(offset, increase):
                continue
            if not if_reversed and best_threshold < stop_threshold:
                if_reversed = True
                if _optimize(0, -increase):
                    increase = -increase
                    continue
            break

    y, x = np.unravel_index(int(np.argmax(res)), res.shape)
    w = avatar_w + best_offset
    h = avatar_h + best_offset
    return best_threshold, (int(x), int(y), int(x + w), int(y + h))


def template_match(
    base_path: Path | str,
    avatar_path: Path | str,
    min_avatar_size: int = MIN_AVATAR_SIZE,
    stop_threshold: float = STOP_THRESHOLD,
) -> tuple[float, tuple[int, int, int, int]]:
    """对单张底图与单张头像做模板匹配，box 位于 MATCH_SIZE 坐标系。"""
    base, _size = _prepare_base(Path(base_path))
    avatar = _prepare_avatar(Path(avatar_path))
    return _template_match_gray(base, avatar, min_avatar_size, stop_threshold)


@dataclass
class BaseMatch:
    """一张底图的匹配结果；error 非空表示该底图匹配失败。"""

    avatar: str | None = None
    threshold: float | None = None
    box: list[int] | None = None
    box_norm: list[float] | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def as_dict(self) -> dict:
        if self.error is not None:
            return {"error": self.error}
        return {
            "avatar": self.avatar,
            "threshold": self.threshold,
            "box": self.box,
            "box_norm": self.box_norm,
        }


@dataclass
class CharacterMatch:
    name: str
    status: str
    candidates: list[str]
    bases: dict[str, BaseMatch] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "candidates": self.candidates,
            "bases": {base: item.as_dict() for base, item in self.bases.items()},
        }


@dataclass
class MatchReport:
    generated_at: str
    classified: str
    characters_dir: str
    avatars_dir: str
    characters: dict[str, CharacterMatch]
    stats: dict[str, int]

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "classified": self.classified,
            "characters_dir": self.characters_dir,
            "avatars_dir": self.avatars_dir,
            "stats": self.stats,
            "characters": {name: item.as_dict() for name, item in self.characters.items()},
        }


def match_base(
    base_path: Path,
    avatar_paths: Sequence[str],
    avatars_dir: Path,
    min_avatar_size: int = MIN_AVATAR_SIZE,
    stop_threshold: float = STOP_THRESHOLD,
) -> BaseMatch:
    """用一张底图对多个候选头像匹配，取阈值最高者；坐标换算回底图原始像素。"""
    try:
        base_gray, (width, height) = _prepare_base(base_path)
    except Exception as error:  # noqa: BLE001 - 单张底图失败不中断整体
        return BaseMatch(error=f"{type(error).__name__}: {error}")

    best: tuple[float, tuple[int, int, int, int], str] | None = None
    for avatar_name in avatar_paths:
        try:
            avatar_gray = _prepare_avatar(avatars_dir / avatar_name)
        except Exception:  # noqa: BLE001 - 单个头像不可读时跳过
            continue
        threshold, box = _template_match_gray(base_gray, avatar_gray, min_avatar_size, stop_threshold)
        if best is None or threshold > best[0]:
            best = (threshold, box, avatar_name)

    if best is None:
        return BaseMatch(error="no readable avatar")

    threshold, box, avatar_name = best
    sx = width / MATCH_SIZE
    sy = height / MATCH_SIZE
    box_orig = [round(box[0] * sx), round(box[1] * sy), round(box[2] * sx), round(box[3] * sy)]
    box_norm = [round(v / MATCH_SIZE, 6) for v in box]
    return BaseMatch(avatar=avatar_name, threshold=threshold, box=box_orig, box_norm=box_norm)


def match_characters(
    classified: dict,
    characters_dir: Path,
    avatars_dir: Path,
    classified_path: Path | None = None,
    limit: int = 0,
    min_avatar_size: int = MIN_AVATAR_SIZE,
    stop_threshold: float = STOP_THRESHOLD,
    progress: Callable[[int, str], None] | None = None,
) -> MatchReport:
    """遍历分类报告中符合条件的角色并匹配其底图，聚合统计。"""
    stats = {
        "total": 0,
        "ok": 0,
        "no_avatar": 0,
        "empty": 0,
        "failed": 0,
        "base_files": 0,
        "matched_bases": 0,
        "low_confidence": 0,
    }
    characters: dict[str, CharacterMatch] = {}

    for name, item in classified.get("characters", {}).items():
        if not _is_target_character(name):
            continue
        if limit and stats["total"] >= limit:
            break
        stats["total"] += 1

        bases = item.get("bases") or {}
        seq = _char_seq(name)
        candidates = _avatar_candidates(avatars_dir, seq) if seq else []
        match = CharacterMatch(name=name, status="ok", candidates=candidates)

        stats["base_files"] += len(bases)
        if not bases:
            match.status = "empty"
        elif not candidates:
            match.status = "no_avatar"
        else:
            for base_name in bases:
                result = match_base(
                    characters_dir / name / base_name,
                    candidates,
                    avatars_dir,
                    min_avatar_size=min_avatar_size,
                    stop_threshold=stop_threshold,
                )
                match.bases[base_name] = result
                if result.ok:
                    stats["matched_bases"] += 1
                    if result.threshold is not None and result.threshold < stop_threshold:
                        stats["low_confidence"] += 1
                else:
                    match.status = "failed"

        stats[match.status] += 1
        characters[name] = match
        if progress is not None:
            progress(stats["total"], name)

    return MatchReport(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        classified=str(classified_path or characters_dir.parent / "_characters_classified.json"),
        characters_dir=str(characters_dir),
        avatars_dir=str(avatars_dir),
        characters=characters,
        stats=stats,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="npcavatar-match",
        description="Match avatars against character base images to locate avatar ranges.",
    )
    parser.add_argument(
        "--classified",
        default="data/unpacked/_characters_classified.json",
        help="characters classification report path (default: data/unpacked/_characters_classified.json)",
    )
    parser.add_argument(
        "--characters-dir",
        default="data/unpacked/characters",
        help="unpacked characters directory (default: data/unpacked/characters)",
    )
    parser.add_argument(
        "--avatars-dir",
        default="data/unpacked/avatars",
        help="unpacked avatars directory (default: data/unpacked/avatars)",
    )
    parser.add_argument(
        "--output",
        default="data/unpacked/_avatar_match.json",
        help="JSON report path, or '-' for stdout (default: data/unpacked/_avatar_match.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="only process the first N matched characters (default: 0 = all)",
    )
    parser.add_argument(
        "--min-avatar-size",
        type=int,
        default=MIN_AVATAR_SIZE,
        help=f"minimum template size during scale search (default: {MIN_AVATAR_SIZE})",
    )
    parser.add_argument(
        "--stop-threshold",
        type=float,
        default=STOP_THRESHOLD,
        help=f"threshold guiding search direction; results below it count as low confidence (default: {STOP_THRESHOLD})",
    )
    return parser


def _on_progress(index: int, name: str) -> None:
    if index % 20 == 0:
        print(f"matched {index}: {name}")


def main(argv: list[str] | None = None) -> int:
    if cv2 is None or np is None:
        print(
            "error: opencv-python-headless and numpy are required (uv sync --extra match)",
            file=sys.stderr,
        )
        return 1

    args = build_parser().parse_args(argv)
    classified_path = Path(args.classified)
    if not classified_path.is_file():
        print(f"error: classified report not found: {classified_path}", file=sys.stderr)
        return 1
    try:
        with classified_path.open("rt", encoding="utf8") as f:
            classified = json.load(f)
    except (OSError, ValueError) as error:
        print(f"error: cannot read {classified_path}: {error}", file=sys.stderr)
        return 1

    report = match_characters(
        classified,
        Path(args.characters_dir),
        Path(args.avatars_dir),
        classified_path=classified_path,
        limit=args.limit,
        min_avatar_size=args.min_avatar_size,
        stop_threshold=args.stop_threshold,
        progress=_on_progress,
    )
    stats = report.stats
    print(
        f"characters: {stats['total']}  ok: {stats['ok']}  no_avatar: {stats['no_avatar']}  "
        f"empty: {stats['empty']}  failed: {stats['failed']}"
    )
    print(
        f"base_files: {stats['base_files']}  matched_bases: {stats['matched_bases']}  "
        f"low_confidence: {stats['low_confidence']}"
    )

    payload = report.as_dict()
    if args.output == "-":
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wt", encoding="utf8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"report written: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
