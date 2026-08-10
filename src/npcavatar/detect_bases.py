"""High-confidence matched base face detection + visualization.

Reads an ``_avatar_match.json`` report, selects the bases whose avatar match
threshold is strictly greater than a cutoff, runs the model-based face
detector (``npcavatar.detect``, anime-face-detector YOLOv3) on each selected
base, and writes:

1. a JSON report (default ``data/unpacked/_face_detect_matched.json``);
2. annotated PNGs (default ``data/unpacked/_face_detect_vis/``) showing the
   avatar match box + threshold (green) and the YOLO face box + confidence
   (red).

The tool is a standalone CLI (``npcavatar-detect-bases``) and also exposes
Python functions for pipeline integration. Progress is reported through a
callback so the CLI can render a tqdm progress bar.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - optional dependency
    tqdm = None  # type: ignore[assignment]

from npcavatar import detect

DEFAULT_MATCH = "data/unpacked/_avatar_match.json"
DEFAULT_OUTPUT = "data/unpacked/_face_detect_matched.json"
DEFAULT_VIS_DIR = "data/unpacked/_face_detect_vis"
DEFAULT_THRESHOLD = 0.95
DEFAULT_CHARACTERS_DIR = "data/unpacked/characters"

# 从匹配条目中保留的字段（offsets 等明细不进入识别报告）
MATCH_FIELDS = ("avatar", "threshold", "box", "box_norm")

MATCH_BOX_COLOR = (0, 200, 0, 255)  # 绿色：avatar 匹配框（BGRA）
YOLO_BOX_COLOR = (0, 0, 255, 255)  # 红色：YOLO 人脸框（BGRA）
BOX_THICKNESS = 3
FONT_SCALE = 0.7
TEXT_THICKNESS = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def filter_bases(
    match_report: dict,
    threshold: float = DEFAULT_THRESHOLD,
    characters_dir: str | Path | None = None,
) -> list[tuple[str, str, dict, Path]]:
    """从匹配报告中筛选 match threshold 严格大于阈值的底图。

    返回 ``(角色名, 底图文件名, base 条目, 底图图片路径)`` 的有序列表；
    条目缺少 threshold 或匹配失败（条目为 error dict）时跳过。
    characters_dir 缺省时取报告内的 ``characters_dir`` 字段。
    """
    characters_dir = Path(characters_dir or match_report.get("characters_dir", DEFAULT_CHARACTERS_DIR))
    selected: list[tuple[str, str, dict, Path]] = []
    for name, item in (match_report.get("characters") or {}).items():
        for base_name, entry in (item.get("bases") or {}).items():
            entry_threshold = entry.get("threshold")
            if not isinstance(entry_threshold, (int, float)) or entry_threshold <= threshold:
                continue
            selected.append((name, base_name, entry, characters_dir / name / base_name))
    return selected


@dataclass
class BaseFaceDetection:
    """一张底图的模型识别结果（合并匹配信息）。"""

    image: str
    image_size: list[int] | None
    detected: bool
    face_pos: dict[str, int] | None
    confidence: float | None
    error: str | None
    match: dict[str, Any]
    vis_image: str | None = None

    def as_dict(self) -> dict:
        result = {
            "image": self.image,
            "image_size": self.image_size,
            "detected": self.detected,
            "face_pos": self.face_pos,
            "confidence": self.confidence,
            "error": self.error,
            **self.match,
        }
        if self.vis_image is not None:
            result["vis_image"] = self.vis_image
        return result


@dataclass
class CharacterFaceDetection:
    """一个角色内全部被选中底图的识别结果。"""

    name: str
    bases: dict[str, BaseFaceDetection] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"bases": {base: item.as_dict() for base, item in self.bases.items()}}


@dataclass
class MatchedDetectReport:
    """高置信底图批量识别的聚合报告。"""

    generated_at: str
    match_file: str
    characters_dir: str
    threshold: float
    characters: dict[str, CharacterFaceDetection]
    stats: dict[str, int]

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "match_file": self.match_file,
            "characters_dir": self.characters_dir,
            "threshold": self.threshold,
            "stats": self.stats,
            "characters": {name: item.as_dict() for name, item in self.characters.items()},
        }


def detect_matched_bases(
    match_report: dict,
    characters_dir: str | Path,
    *,
    match_file: str | Path | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    conf: float = detect.DEFAULT_CONF,
    device: str | None = None,
    limit: int = 0,
    character: str | None = None,
    detector: Callable[[np.ndarray], list[dict]] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> MatchedDetectReport:
    """筛选高置信底图并逐张模型识别人脸，聚合报告。

    progress 回调签名 ``(index, total, label)``，index 从 1 开始，label 为
    ``<角色>/<底图>``。detector 为可注入的测试替身，默认使用懒加载的真实模型。
    """
    characters_dir = Path(characters_dir)
    stats = {"filtered": 0, "detected": 0, "not_detected": 0, "errors": 0}
    characters: dict[str, CharacterFaceDetection] = {}

    selected = filter_bases(match_report, threshold=threshold, characters_dir=characters_dir)
    if character is not None:
        selected = [item for item in selected if item[0] == character]
    if limit:
        selected = selected[:limit]
    total = len(selected)

    for index, (name, base_name, entry, image_path) in enumerate(selected, 1):
        if name not in characters:
            characters[name] = CharacterFaceDetection(name=name)
        raw = detect.detect_top1(image_path, device=device, conf=conf, detector=detector)
        match_info = {key: entry.get(key) for key in MATCH_FIELDS}
        characters[name].bases[base_name] = BaseFaceDetection(
            image=str(image_path),
            image_size=raw["image_size"],
            detected=raw["detected"],
            face_pos=raw["face_pos"],
            confidence=raw["confidence"],
            error=raw["error"],
            match=match_info,
        )
        stats["filtered"] += 1
        if raw["error"]:
            stats["errors"] += 1
        elif raw["detected"]:
            stats["detected"] += 1
        else:
            stats["not_detected"] += 1
        if progress is not None:
            progress(index, total, f"{name}/{base_name}")

    return MatchedDetectReport(
        generated_at=_now(),
        match_file=str(match_file) if match_file is not None else "",
        characters_dir=str(characters_dir),
        threshold=threshold,
        characters=characters,
        stats=stats,
    )


def _read_bgra(path: Path) -> np.ndarray:
    """读取图片为 4 通道 BGRA（兼容中文/特殊字符路径）。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"cannot read image: {path}")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    elif image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    return image


def _face_bbox(face_pos: dict[str, int]) -> tuple[int, int, int, int]:
    """由 face_pos（中心 + 宽高）反推检测框 [x0, y0, x1, y1]，与存储结果一致。"""
    x, y, w, h = face_pos["x"], face_pos["y"], face_pos["w"], face_pos["h"]
    x0 = x - w // 2
    y0 = y - h // 2
    return x0, y0, x0 + w - 1, y0 + h - 1


def _put_label(canvas: np.ndarray, text: str, x: int, y: int, color: tuple[int, int, int, int]) -> None:
    """在画布上写字，坐标裁剪到图片内且预留文字高度。"""
    height, width = canvas.shape[:2]
    x = max(4, min(x, max(4, width - 1)))
    y = max(18, min(y, max(18, height - 4)))
    cv2.putText(canvas, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, FONT_SCALE, color, TEXT_THICKNESS, cv2.LINE_AA)


def draw_annotation(
    base_path: str | Path,
    match_box: list[int] | tuple[int, ...] | None,
    match_threshold: float | None,
    face_pos: dict[str, int] | None,
    confidence: float | None,
    output_path: str | Path,
) -> None:
    """在底图上绘制匹配框（绿，标 match 阈值）与人脸框（红，标 yolo 置信度），保存 PNG。"""
    canvas = _read_bgra(Path(base_path))

    if match_box and len(match_box) == 4:
        x1, y1, x2, y2 = (int(round(v)) for v in match_box)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), MATCH_BOX_COLOR, BOX_THICKNESS)
        if match_threshold is not None:
            label_y = y1 - 8 if y1 - 8 >= 20 else y1 + 24
            _put_label(canvas, f"match {match_threshold:.4f}", x1, label_y, MATCH_BOX_COLOR)

    if face_pos is not None:
        x1, y1, x2, y2 = _face_bbox(face_pos)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), YOLO_BOX_COLOR, BOX_THICKNESS)
        if confidence is not None:
            label_y = y1 - 8 if y1 - 8 >= 20 else y1 + 24
            _put_label(canvas, f"yolo {confidence:.4f}", x1, label_y, YOLO_BOX_COLOR)
    else:
        _put_label(canvas, "no face", 8, 20, YOLO_BOX_COLOR)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", canvas)
    if not ok:
        raise RuntimeError("imencode failed")
    buf.tofile(str(output_path))


def render_annotations(
    report: MatchedDetectReport,
    characters_dir: str | Path,
    vis_dir: str | Path,
) -> int:
    """为报告中每个 base 生成标注 PNG（扁平目录，``<角色>__<底图>.png``）。

    成功的条目写入 ``vis_image``，返回写入数量；单张失败只告警不中断。
    """
    characters_dir = Path(characters_dir)
    vis_dir = Path(vis_dir)
    vis_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for name, char in report.characters.items():
        for base_name, det in char.bases.items():
            out_path = vis_dir / f"{name}__{Path(base_name).stem}.png"
            try:
                draw_annotation(
                    characters_dir / name / base_name,
                    det.match.get("box"),
                    det.match.get("threshold"),
                    det.face_pos,
                    det.confidence,
                    out_path,
                )
                det.vis_image = str(out_path)
                count += 1
            except Exception as error:  # noqa: BLE001 - 单张失败不中断
                print(f"warning: cannot render {name}/{base_name}: {error}", file=sys.stderr)
    return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="npcavatar-detect-bases",
        description=(
            "Detect faces (top-1, YOLOv3) in bases whose avatar match "
            "threshold exceeds a cutoff, and write a JSON report plus "
            "annotated PNGs."
        ),
    )
    parser.add_argument(
        "--match",
        default=DEFAULT_MATCH,
        help=f"avatar match report path (default: {DEFAULT_MATCH})",
    )
    parser.add_argument(
        "--characters-dir",
        default=None,
        help="base image directory; defaults to the value stored in the match report",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"only process bases with match threshold strictly greater than this (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=detect.DEFAULT_CONF,
        help=(
            "minimum confidence for a face detection; lower results count as "
            f"not detected (default: {detect.DEFAULT_CONF})"
        ),
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cuda", "cpu"),
        default="auto",
        help="inference device; auto picks cuda:0 when available (default: auto)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="only process the first N filtered bases (default: 0 = all)",
    )
    parser.add_argument(
        "--character",
        default=None,
        help="only process the specified character name, e.g. avg_003_kalts_1 (default: all)",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"JSON report path, or '-' for stdout (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--vis-dir",
        default=DEFAULT_VIS_DIR,
        help=f"directory for annotated PNGs (default: {DEFAULT_VIS_DIR})",
    )
    return parser


def _make_progress(total: int) -> tuple[Callable[[int, int, str], None], Callable[[], None]]:
    """返回 (progress, close)；优先 tqdm 进度条，缺失时回退为逐条文本。"""
    if tqdm is not None:
        bar = tqdm(total=total, unit="base", desc="face detect", dynamic_ncols=True)

        def progress(index: int, total_count: int, label: str) -> None:
            bar.set_postfix_str(label)
            bar.update(1)

        return progress, bar.close

    def progress(index: int, total_count: int, label: str) -> None:
        print(f"[{index}/{total_count}] {label}")

    return progress, lambda: None


def main(argv: list[str] | None = None) -> int:
    if cv2 is None or np is None:
        print(
            "error: opencv-python-headless and numpy are required (uv sync --extra detect)",
            file=sys.stderr,
        )
        return 1
    if not detect._check_ml_deps():
        print(
            "error: torch and anime-face-detector are required (uv sync --extra detect)",
            file=sys.stderr,
        )
        return 1

    args = build_parser().parse_args(argv)
    if not 0.0 <= args.conf <= 1.0:
        print(f"error: --conf must be between 0 and 1 (got {args.conf})", file=sys.stderr)
        return 1
    if not 0.0 < args.threshold <= 1.0:
        print(f"error: --threshold must be in (0, 1] (got {args.threshold})", file=sys.stderr)
        return 1
    if args.limit < 0:
        print(f"error: --limit must be >= 0 (got {args.limit})", file=sys.stderr)
        return 1

    match_path = Path(args.match)
    if not match_path.is_file():
        print(f"error: match report not found: {match_path}", file=sys.stderr)
        return 1
    try:
        with match_path.open("rt", encoding="utf8") as f:
            match_report = json.load(f)
    except (OSError, ValueError) as error:
        print(f"error: cannot read {match_path}: {error}", file=sys.stderr)
        return 1

    characters_dir = Path(
        args.characters_dir or match_report.get("characters_dir", DEFAULT_CHARACTERS_DIR)
    )
    if args.character is not None and args.character not in (match_report.get("characters") or {}):
        print(f"error: character not found in {match_path}: {args.character}", file=sys.stderr)
        return 1

    selected = filter_bases(match_report, threshold=args.threshold, characters_dir=characters_dir)
    if args.character is not None:
        selected = [item for item in selected if item[0] == args.character]
    if args.limit:
        selected = selected[: args.limit]

    device = None if args.device == "auto" else args.device
    progress, close_progress = _make_progress(len(selected))
    try:
        report = detect_matched_bases(
            match_report,
            characters_dir,
            match_file=match_path,
            threshold=args.threshold,
            conf=args.conf,
            device=device,
            limit=args.limit,
            character=args.character,
            progress=progress,
        )
    finally:
        close_progress()

    stats = report.stats
    print(
        f"filtered: {stats['filtered']}  detected: {stats['detected']}  "
        f"not_detected: {stats['not_detected']}  errors: {stats['errors']}"
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

    vis_dir = Path(args.vis_dir)
    img_count = render_annotations(report, characters_dir, vis_dir)
    print(f"images written: {img_count} -> {vis_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
