"""Anime face detection (top-1) using anime-face-detector YOLOv3 box-only.

Ports the box-only path from ArknightsAvatarTest's face_detection.py: only the
YOLOv3 face detector is loaded (no HRNetV2 keypoint model), and per image
only the highest-confidence detection is kept, reported as face_pos
(top-left + size {x, y, w, h}) plus confidence.

The module is a standalone tool (arknightsavatar-detect) and also exposes Python
functions for later pipeline integration (goal.md step 3, model recognition).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from arknightsavatar import paths, reporting
from arknightsavatar.skip import DEFAULT_SKIP, SkipList

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

BOX_SCALE_FACTOR = 1.1
DEFAULT_CONF = 0.3
DEFAULT_CHARACTERS_DIR = paths.UNPACKED_CHARACTERS_DIR
DEFAULT_OUTPUT = paths.FACE_DETECT
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

# 模块级懒加载缓存：device -> box-only 检测器 / 完整管线检测器
_box_detectors: dict[str, Any] = {}
_full_detectors: dict[str, Any] = {}
_box_only_unavailable = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _auto_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda:0"
    except Exception:  # noqa: BLE001 - 无 torch/GPU 时回退 CPU
        pass
    return "cpu"


def _read_image_bgr(path: str | Path) -> np.ndarray:
    """读取图片为 BGR ndarray，兼容中文/特殊字符路径与带透明通道的图片。"""
    path = str(path)
    img = cv2.imread(path)
    if img is None:
        from PIL import Image

        pil = Image.open(path)
        img = cv2.cvtColor(np.asarray(pil.convert("RGB")), cv2.COLOR_RGB2BGR)
    if img is None:
        raise ValueError(f"无法读取图片: {path}")
    if img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def _get_face_detector(device: str) -> Any | None:
    """仅加载 YOLOv3 人脸检测器（不含 HRNetV2 关键点模型）。

    依赖 anime-face-detector 的内部接口 ``_face.load_face_detector``
    （0.1.0 可用）。若不可用返回 None，由调用方回退到完整管线。
    """
    global _box_only_unavailable
    if _box_only_unavailable:
        return None
    if device not in _box_detectors:
        try:
            from anime_face_detector import get_checkpoint_path
            from anime_face_detector._face import load_face_detector

            _box_detectors[device] = load_face_detector(
                "yolov3",
                str(get_checkpoint_path("yolov3")),
                device=device,
            )
        except Exception:  # noqa: BLE001 - 内部接口不可用时回退
            _box_only_unavailable = True
            return None
    return _box_detectors[device]


def _get_full_detector(device: str) -> Any:
    """完整管线（YOLOv3 + HRNetV2 关键点）兜底；关键点结果会被丢弃。"""
    if device not in _full_detectors:
        from anime_face_detector import create_detector

        _full_detectors[device] = create_detector(
            face_detector_name="yolov3",
            device=device,
        )
    return _full_detectors[device]


def _scale_box(box: np.ndarray) -> np.ndarray:
    """把 [x0, y0, x1, y1, score] 检测框按 BOX_SCALE_FACTOR 绕中心放大。"""
    box = np.asarray(box, dtype=np.float32)
    size = box[2:4] - box[0:2] + 1
    new_size = size * BOX_SCALE_FACTOR
    center = (box[0:2] + box[2:4]) / 2
    tl = center - new_size / 2
    br = tl + new_size
    return np.concatenate([tl, br, box[4:5]])


def _detect_boxes(bgr: np.ndarray, device: str) -> list[dict]:
    """对 BGR 图片运行 anime YOLOv3 人脸检测，返回统一结果列表。"""
    face_detector = _get_face_detector(device)
    if face_detector is not None:
        # 纯检测路径：只跑 YOLOv3。完整管线的 bbox 是原始框乘 box_scale_factor(1.1)，
        # 这里保持一致。
        out: list[dict] = []
        for raw_box in face_detector.detect(bgr):
            box = _scale_box(np.asarray(raw_box, dtype=np.float32))
            out.append(
                {
                    "bbox": [int(round(v)) for v in box[:4]],
                    "confidence": float(box[4]) if len(box) >= 5 else None,
                }
            )
        return out
    # 回退：完整管线照常推理，但输出里不保留关键点。
    detector = _get_full_detector(device)
    out = []
    for det in detector(bgr):
        box = np.asarray(det["bbox"], dtype=np.float32)
        out.append(
            {
                "bbox": [int(round(v)) for v in box[:4]],
                "confidence": float(box[4]) if len(box) >= 5 else None,
            }
        )
    return out


def _clip_bbox(
    bbox: list[int] | tuple[int, ...],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    """把 bbox 四舍五入并裁剪到图片边界内。"""
    x0, y0, x1, y1 = (int(round(v)) for v in bbox)
    x0 = max(0, min(width, x0))
    y0 = max(0, min(height, y0))
    x1 = max(0, min(width, x1))
    y1 = max(0, min(height, y1))
    return x0, y0, x1, y1


def _bbox_to_face_pos(x0: int, y0: int, x1: int, y1: int) -> dict[str, int]:
    """由裁剪后的检测框推导 face_pos：左上角 + 尺寸（原始像素，四舍五入）。"""
    return {
        "x": int(round(x0)),
        "y": int(round(y0)),
        "w": int(round(x1 - x0 + 1)),
        "h": int(round(y1 - y0 + 1)),
    }


def detect_top1(
    image_path: str | Path,
    *,
    device: str | None = None,
    conf: float = DEFAULT_CONF,
    detector: Callable[[np.ndarray], list[dict]] | None = None,
) -> dict:
    """对单张图片做 top-1 人脸检测，返回 JSON 可序列化的结果 dict。

    detector 为可注入的测试替身（``callable(bgr) -> list[{"bbox", "confidence"}]``），
    默认使用懒加载的真实模型。
    """
    path = Path(image_path)
    result = {
        "image": str(path),
        "image_size": None,
        "detected": False,
        "face_pos": None,
        "confidence": None,
        "error": None,
    }
    if not path.is_file():
        result["error"] = f"图片不存在: {path}"
        return result
    try:
        bgr = _read_image_bgr(path)
    except Exception as exc:  # noqa: BLE001 - 单图失败只记录 error
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    height, width = bgr.shape[:2]
    result["image_size"] = [width, height]
    try:
        if detector is not None:
            detections = detector(bgr)
        else:
            detections = _detect_boxes(bgr, device or _auto_device())
    except Exception as exc:  # noqa: BLE001 - 检测异常不中断批量
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    if not detections:
        return result

    top = max(detections, key=lambda d: d.get("confidence") or 0.0)
    bbox = top.get("bbox")
    confidence = top.get("confidence")
    if bbox is None or confidence is None:
        result["error"] = "检测结果缺少 bbox/confidence"
        return result
    if confidence < conf:
        return result

    x0, y0, x1, y1 = _clip_bbox(bbox, width, height)
    result["detected"] = True
    result["face_pos"] = _bbox_to_face_pos(x0, y0, x1, y1)
    result["confidence"] = confidence
    return result


@dataclass
class ImageDetection:
    """单张图片的识别结果。"""

    image: str
    image_size: list[int] | None
    detected: bool
    face_pos: dict[str, int] | None
    confidence: float | None
    error: str | None

    def as_dict(self) -> dict:
        return {
            "image": self.image,
            "image_size": self.image_size,
            "detected": self.detected,
            "face_pos": self.face_pos,
            "confidence": self.confidence,
            "error": self.error,
        }


@dataclass
class CharacterDetection:
    """一个角色目录内全部图片的识别结果。"""

    name: str
    images: dict[str, ImageDetection] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"images": {name: item.as_dict() for name, item in self.images.items()}}


@dataclass
class DetectionReport:
    """characters 目录批量识别的聚合报告。"""

    generated_at: str
    characters_dir: str
    characters: dict[str, CharacterDetection]
    stats: dict[str, int]

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "characters_dir": self.characters_dir,
            "stats": self.stats,
            "characters": {name: item.as_dict() for name, item in self.characters.items()},
        }


def _target_dirs(
    characters_dir: Path,
    character: str | None = None,
    limit: int = 0,
    skip: SkipList | None = None,
) -> list[Path]:
    """按排序返回本次实际扫描的角色目录（character/skip/limit 过滤），
    进度总数与处理数共用以保证一致。"""
    skip = skip or SkipList()
    dirs: list[Path] = []
    for char_dir in sorted(p for p in characters_dir.iterdir() if p.is_dir()):
        if character is not None and char_dir.name != character:
            continue
        if skip.is_character_skipped(char_dir.name):
            continue
        if limit and len(dirs) >= limit:
            break
        dirs.append(char_dir)
    return dirs


def detect_characters(
    characters_dir: str | Path,
    *,
    conf: float = DEFAULT_CONF,
    device: str | None = None,
    limit: int = 0,
    character: str | None = None,
    detector: Callable[[np.ndarray], list[dict]] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    skip: SkipList | None = None,
) -> DetectionReport:
    """扫描 characters 目录下每个角色的全部图片（底图 + 差分）并聚合统计。

    progress 回调签名 ``(index, total, name)``，index 从 1 开始，每处理一个角色调用一次。
    """
    stats = {
        "total_characters": 0,
        "total_images": 0,
        "detected": 0,
        "not_detected": 0,
        "errors": 0,
    }
    characters: dict[str, CharacterDetection] = {}
    characters_dir = Path(characters_dir)
    skip = skip or SkipList()
    dirs = _target_dirs(characters_dir, character=character, limit=limit, skip=skip)
    total = len(dirs)
    for char_dir in dirs:
        stats["total_characters"] += 1

        files = sorted(
            p for p in char_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
        )
        char_det = CharacterDetection(name=char_dir.name)
        for path in files:
            if skip.is_sprite_skipped(char_dir.name, path.name):
                continue
            raw = detect_top1(path, device=device, conf=conf, detector=detector)
            char_det.images[path.name] = ImageDetection(**raw)
            stats["total_images"] += 1
            if raw["error"]:
                stats["errors"] += 1
            elif raw["detected"]:
                stats["detected"] += 1
            else:
                stats["not_detected"] += 1
        characters[char_dir.name] = char_det
        if progress is not None:
            progress(stats["total_characters"], total, char_dir.name)

    return DetectionReport(
        generated_at=_now(),
        characters_dir=str(characters_dir),
        characters=characters,
        stats=stats,
    )


def _images_stats(results: list[dict]) -> dict:
    stats = {"images": len(results), "detected": 0, "not_detected": 0, "errors": 0}
    for result in results:
        if result["error"]:
            stats["errors"] += 1
        elif result["detected"]:
            stats["detected"] += 1
        else:
            stats["not_detected"] += 1
    return stats


def _check_ml_deps() -> bool:
    try:
        import anime_face_detector  # noqa: F401
        import torch  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-detect",
        description=(
            "Detect anime faces (top-1) in character sprites using "
            "anime-face-detector YOLOv3 (no keypoints)."
        ),
    )
    parser.add_argument(
        "images",
        nargs="*",
        help="one or more image paths for quick testing; omit to scan --characters-dir",
    )
    parser.add_argument(
        "--characters-dir",
        default=DEFAULT_CHARACTERS_DIR,
        help=f"unpacked characters directory (default: {DEFAULT_CHARACTERS_DIR})",
    )
    parser.add_argument(
        "--character",
        default=None,
        help="only process the specified character name, e.g. avg_003_kalts_1 (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="only process the first N characters (default: 0 = all)",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=DEFAULT_CONF,
        help=(
            "minimum confidence for a detection; lower results count as "
            f"not detected (default: {DEFAULT_CONF})"
        ),
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cuda", "cpu"),
        default="auto",
        help="inference device; auto picks cuda:0 when available (default: auto)",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"JSON report path, or '-' for stdout (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--skip",
        default=DEFAULT_SKIP,
        help=f"skip-list JSON path (default: {DEFAULT_SKIP})",
    )
    return parser


def _make_progress(total: int) -> tuple[Callable[[int, int, str], None], Callable[[], None]]:
    """返回 (progress, close)；优先 tqdm 进度条，缺失时回退为逐条文本。"""
    if tqdm is not None:
        bar = tqdm(total=total, unit="char", desc="face detect", dynamic_ncols=True)

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
    if not _check_ml_deps():
        print(
            "error: torch and anime-face-detector are required (uv sync --extra detect)",
            file=sys.stderr,
        )
        return 1

    args = build_parser().parse_args(argv)
    if not 0.0 <= args.conf <= 1.0:
        print(f"error: --conf must be between 0 and 1 (got {args.conf})", file=sys.stderr)
        return 1
    if args.limit < 0:
        print(f"error: --limit must be >= 0 (got {args.limit})", file=sys.stderr)
        return 1
    device = None if args.device == "auto" else args.device

    if args.images:
        results = [detect_top1(image, device=device, conf=args.conf) for image in args.images]
        stats = _images_stats(results)
        payload = {
            "generated_at": _now(),
            "images": {result["image"]: result for result in results},
            "stats": stats,
        }
        print(
            f"images: {stats['images']}  detected: {stats['detected']}  "
            f"not_detected: {stats['not_detected']}  errors: {stats['errors']}"
        )
    else:
        characters_dir = Path(args.characters_dir)
        if not characters_dir.is_dir():
            print(f"error: characters directory not found: {characters_dir}", file=sys.stderr)
            return 1
        if args.character is not None and not (characters_dir / args.character).is_dir():
            print(
                f"error: character not found in {characters_dir}: {args.character}",
                file=sys.stderr,
            )
            return 1
        skip = SkipList.load(args.skip)
        targets = _target_dirs(
            characters_dir,
            character=args.character,
            limit=args.limit,
            skip=skip,
        )
        progress, close_progress = _make_progress(len(targets))
        try:
            report = detect_characters(
                characters_dir,
                conf=args.conf,
                device=device,
                limit=args.limit,
                character=args.character,
                skip=skip,
                progress=progress,
            )
        finally:
            close_progress()
        stats = report.stats
        payload = report.as_dict()
        print(
            f"characters: {stats['total_characters']}  images: {stats['total_images']}  "
            f"detected: {stats['detected']}  not_detected: {stats['not_detected']}  "
            f"errors: {stats['errors']}"
        )

    reporting.write_report(payload, args.output)
    if args.output != "-":
        print(f"report written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
