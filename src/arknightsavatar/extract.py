"""Avatar extraction from character sprites (goal.md step 3).

Consumes the characters classification report and resolves an avatar crop box
per base through three priority tiers -- manual override > avatar match >
face/head model derivation -- then extracts 180x180 avatars for every base and
diff into per-character folders under ``data/export/``.

Incremental behavior:
- output PNGs that already exist are skipped unless ``--force``;
- face/head detection results are cached in a JSON file keyed by
  ``"<character>/<image>"`` (same primary-key format as the manual override
  file), so re-runs do not repeat model inference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]

try:
    from PIL import Image
except ImportError:  # pragma: no cover - optional dependency
    Image = None  # type: ignore[assignment]

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - optional dependency
    tqdm = None  # type: ignore[assignment]

from arknightsavatar import detect, detect_bases, match, paths, reporting
from arknightsavatar.skip import DEFAULT_SKIP, SkipList

DEFAULT_CLASSIFIED = paths.CLASSIFIED
DEFAULT_CHARACTERS_DIR = paths.UNPACKED_CHARACTERS_DIR
DEFAULT_MATCH = paths.AVATAR_MATCH
DEFAULT_AVATARS_DIR = paths.UNPACKED_AVATARS_DIR
DEFAULT_MANUAL = paths.MANUAL
DEFAULT_DERIVE_MODEL = paths.DERIVE_MODEL
DEFAULT_FACE_HEAD_CACHE = paths.FACE_HEAD_CACHE
DEFAULT_EXTRACT_CACHE = paths.EXTRACT_CACHE
DEFAULT_OUTPUT_DIR = paths.EXPORT_DIR
DEFAULT_OUTPUT = paths.EXTRACT_REPORT

MATCH_THRESHOLD = 0.8
FACE_CONF = 0.60
HEAD_CONF = 0.55
SPECIAL_MASK_IOU = 0.85
DIFF_FINGERPRINT_VERSION = 2  # diff IoU now restricted to base face range
DEDUP_SIM = 0.9
EXPORT_SIZE = 180
CACHE_SAVE_INTERVAL = 50

TIER_RANK = {"manual": 3, "match": 2, "derive": 1}
DOLLAR_RE = re.compile(r"\$(\d+)$")
STATS_KEYS = [
    "characters",
    "base_files",
    "base_ok",
    "base_skipped",
    "base_dropped",
    "base_no_box",
    "base_failed",
    "diff_files",
    "diff_ok",
    "diff_skipped",
    "diff_dropped",
    "diff_no_box",
    "diff_failed",
    "diff_special",
    "detect_cache_hits",
    "detect_cache_new",
    "similarity_cache_hits",
    "similarity_cache_new",
    "diff_match_cache_hits",
    "diff_match_cache_new",
]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("rt", encoding="utf8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _valid_box(box: Any) -> bool:
    return (
        isinstance(box, (list, tuple))
        and len(box) == 4
        and all(isinstance(v, (int, float)) for v in box)
    )


def _int_box(box: Sequence[float]) -> list[int]:
    return [round(float(v)) for v in box]


def _crop_box(image: Image.Image, box: Sequence[int]) -> Image.Image:
    """Crop ``[x1, y1, x2, y2]`` clamped to image bounds.

    Coordinates outside the image are clipped; an empty intersection returns a
    0x0 transparent image so out-of-range regions contribute nothing.
    """
    x1, y1, x2, y2 = _int_box(box)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(image.width, x2), min(image.height, y2)
    if x2 <= x1 or y2 <= y1:
        return Image.new("RGBA", (0, 0), (0, 0, 0, 0))
    return image.crop((x1, y1, x2, y2))


def load_manual(path: str | Path) -> dict:
    """Load the manual override file into a flat ``"<角色>/<图片>" -> entry`` map."""
    payload = _read_json(Path(path), {}) or {}
    if not isinstance(payload, dict):
        return {}
    images = payload.get("images")
    if isinstance(images, dict):
        return images
    return payload


def load_match_report(path: str | Path) -> dict | None:
    """Load the avatar match report; None when the file does not exist."""
    path = Path(path)
    if not path.is_file():
        return None
    return _read_json(path)


def load_derive_model(path: str | Path) -> dict:
    """Load the face/head -> avatar box derivation model (data/recognition/derive)."""
    model = _read_json(Path(path))
    if not isinstance(model, dict) or not isinstance(model.get("coef"), list):
        raise ValueError(  # noqa: TRY004 - 保持既有 API 约定的 ValueError 语义
            f"invalid derive model: {path}"
        )
    coef = model["coef"]
    if len(coef) != 9 or any(len(row) != 3 for row in coef):
        raise ValueError(f"invalid derive model coefficients: {path}")
    return model


def derive_box(model: dict, face_pos: dict, head_pos: dict) -> list[int]:
    """Derive a square crop box ``[x1, y1, x2, y2]`` from face/head boxes.

    Features follow ``data/recognition/derive/model.json``: face/head box centers
    (converted from top-left + size) plus widths/heights, then
    ``[cx, cy, s] = W @ features`` and ``box = [cx-s/2, cy-s/2, cx+s/2, cy+s/2]``.
    """
    feature_order = model.get("feature_order") or [
        "fx",
        "fy",
        "fw",
        "fh",
        "hx",
        "hy",
        "hw",
        "hh",
    ]
    coef = np.asarray(model["coef"], dtype=float)
    features = {
        "fx": face_pos["x"] + face_pos["w"] / 2.0,
        "fy": face_pos["y"] + face_pos["h"] / 2.0,
        "fw": float(face_pos["w"]),
        "fh": float(face_pos["h"]),
        "hx": head_pos["x"] + head_pos["w"] / 2.0,
        "hy": head_pos["y"] + head_pos["h"] / 2.0,
        "hw": float(head_pos["w"]),
        "hh": float(head_pos["h"]),
    }
    vec = np.asarray([features[key] for key in feature_order] + [1.0], dtype=float)
    cx, cy, side = vec @ coef
    return [
        round(cx - side / 2.0),
        round(cy - side / 2.0),
        round(cx + side / 2.0),
        round(cy + side / 2.0),
    ]


def face_group_for_diff(face_groups: list[dict], diff_name: str) -> dict | None:
    """Pick the face group for a diff: the ``$n`` series index maps to group n-1."""
    mm = DOLLAR_RE.search(Path(diff_name).stem)
    series = int(mm.group(1)) if mm else 1
    if not face_groups:
        return None
    index = series - 1
    if 0 <= index < len(face_groups):
        return face_groups[index]
    return None


def _is_alpha_diff(diff_name: str) -> bool:
    """True when the diff file is the dedicated ``alpha.png`` alpha-channel texture."""
    return Path(diff_name).name.lower() == "alpha.png"


def compose_diff(
    base_img: Image.Image,
    diff_img: Image.Image,
    face_group: dict | None,
    alpha_img: Image.Image | None = None,
) -> Image.Image:
    """Combine a diff with its base.

    Diffs whose size equals the base are already combined and returned as-is;
    smaller diffs are resized to the group's ``faceSize`` and pasted at
    ``facePos``. The diff's RGB is pasted with a face-region alpha mask and the
    resulting alpha channel is always taken from the base: the mask (and the
    final face-region alpha) uses ``alpha_img`` (``alpha.png``, grayscale) when
    provided, otherwise the base's own alpha in the face region. A
    missing/invalid face group raises ValueError.
    """
    base = base_img.convert("RGBA")
    diff = diff_img.convert("RGBA")
    if diff.size == base.size:
        return diff
    if face_group is None:
        raise ValueError("missing face group for diff composition")
    pos = face_group.get("facePos") or {}
    size = face_group.get("faceSize") or {}
    if not all(key in pos and key in size for key in ("x", "y")):
        raise ValueError("invalid face group")
    fx, fy = int(pos["x"]), int(pos["y"])
    fw, fh = int(size["x"]), int(size["y"])
    if fw <= 0 or fh <= 0:
        raise ValueError("invalid face size")
    if diff.size != (fw, fh):
        diff = diff.resize((fw, fh), Image.LANCZOS)
    if alpha_img is not None:
        face_alpha = alpha_img.convert("L").resize((fw, fh), Image.LANCZOS)
    else:
        face_alpha = base.crop((fx, fy, fx + fw, fy + fh)).split()[3]
    combined = base.copy()
    combined.paste(diff.convert("RGB"), (fx, fy), face_alpha)
    final_alpha = base.split()[3].copy()
    final_alpha.paste(face_alpha, (fx, fy))
    combined.putalpha(final_alpha)
    return combined


def alpha_mask_iou(
    a: Image.Image, b: Image.Image, box: Sequence[int] | None = None
) -> float:
    """IoU of opaque (alpha > 0) masks; ignores color/expression changes.

    When ``box`` (``[x1, y1, x2, y2]``) is given, only that region of both
    images is compared -- used to restrict the diff IoU to the base's face
    range instead of the whole image.
    """
    if box is not None:
        a = _crop_box(a, box)
        b = _crop_box(b, box)
    ma = np.asarray(a.convert("RGBA"))[..., 3] > 0
    mb = np.asarray(b.convert("RGBA"))[..., 3] > 0
    if ma.shape != mb.shape:
        mb = cv2.resize(mb.astype(np.uint8), (ma.shape[1], ma.shape[0])).astype(bool)
    intersection = int(np.logical_and(ma, mb).sum())
    union = int(np.logical_or(ma, mb).sum())
    return intersection / union if union else 1.0


def _composite_gray(image: Image.Image) -> np.ndarray:
    """Composite RGBA onto white and return a float32 grayscale image."""
    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    rgb = rgba[..., :3] * rgba[..., 3:4] + (1.0 - rgba[..., 3:4])
    gray = cv2.cvtColor((rgb.clip(0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    return gray.astype(np.float32)


def avatar_similarity(a: Image.Image, b: Image.Image) -> float:
    """Normalized template correlation between two avatar images (0..1)."""
    ga = _composite_gray(a)
    gb = _composite_gray(b)
    if ga.shape != gb.shape:
        gb = cv2.resize(gb, (ga.shape[1], ga.shape[0]), interpolation=cv2.INTER_LINEAR)
    result = cv2.matchTemplate(ga, gb, cv2.TM_CCOEFF_NORMED)
    return float(result[0, 0])


def _image_fingerprint(image: Image.Image) -> str:
    """sha256 of the raw RGBA pixel bytes; identifies a 180x180 avatar."""
    return hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()


def _diff_fingerprint(
    composed: Image.Image,
    base_box: list[int] | None,
    base_method: str | None,
    base_confidence: float | None,
    *,
    special_mask_iou: float,
    face_conf: float,
    head_conf: float,
    derive_model: dict,
) -> str:
    """sha256 over every input that can change a diff's match decision."""
    digest = hashlib.sha256()
    digest.update(_image_fingerprint(composed).encode("ascii"))
    digest.update(str(DIFF_FINGERPRINT_VERSION).encode("ascii"))
    digest.update(repr((special_mask_iou, face_conf, head_conf)).encode("ascii"))
    digest.update(
        json.dumps(derive_model, sort_keys=True, ensure_ascii=False).encode("utf8")
    )
    digest.update(f"{base_box}|{base_method}|{base_confidence}".encode())
    return digest.hexdigest()


def extract_avatar(
    image: Image.Image, box: Sequence[float], size: int = EXPORT_SIZE
) -> Image.Image:
    """Crop ``box`` (may extend past image edges; padded transparent) and resize."""
    x1, y1, x2, y2 = _int_box(box)
    width = x2 - x1
    height = y2 - y1
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid box: {box}")
    rgba = image.convert("RGBA")
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ix1, iy1 = max(0, x1), max(0, y1)
    ix2, iy2 = min(rgba.width, x2), min(rgba.height, y2)
    if ix2 > ix1 and iy2 > iy1:
        canvas.paste(rgba.crop((ix1, iy1, ix2, iy2)), (ix1 - x1, iy1 - y1))
    return canvas.resize((size, size), Image.LANCZOS)


class FaceHeadCache:
    """Persistent face/head detection cache keyed by ``"<角色>/<图片>"``."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        payload = _read_json(self.path, {}) or {}
        images = payload.get("images")
        self.images: dict = images if isinstance(images, dict) else {}
        self._new_count = 0

    def get(self, key: str) -> dict | None:
        entry = self.images.get(key)
        if isinstance(entry, dict) and "face" in entry and "head" in entry:
            return entry
        return None

    def put(self, key: str, entry: dict) -> None:
        self.images[key] = entry
        self._new_count += 1
        if self._new_count % CACHE_SAVE_INTERVAL == 0:
            self.save()

    def save(self) -> None:
        if not self.images and not self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": _now(),
            "key_format": "<角色>/<图片>",
            "images": self.images,
        }
        with self.path.open("wt", encoding="utf8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


class ExtractCache:
    """Persistent cache for base similarity and special-diff match decisions.

    Two sections:
    - ``similarities``: ``"<角色>/<baseA>__<baseB>"`` (pair sorted by name) ->
      ``{hashA, hashB, boxA, boxB, similarity}``, where the hashes identify the
      two 180x180 base avatars;
    - ``diff_matches``: ``"<角色>/<diff>"`` -> ``{fingerprint, iou, special, box,
      method, confidence, detect_cache_hit, error}``, where the fingerprint covers
      the composed image and every parameter that can change the decision.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        payload = _read_json(self.path, {}) or {}
        similarities = payload.get("similarities")
        diff_matches = payload.get("diff_matches")
        self.similarities: dict = similarities if isinstance(similarities, dict) else {}
        self.diff_matches: dict = diff_matches if isinstance(diff_matches, dict) else {}
        self._new_count = 0

    def get_similarity(self, key: str) -> dict | None:
        entry = self.similarities.get(key)
        return entry if isinstance(entry, dict) else None

    def put_similarity(self, key: str, entry: dict) -> None:
        self.similarities[key] = entry
        self._new_count += 1
        if self._new_count % CACHE_SAVE_INTERVAL == 0:
            self.save()

    def get_diff(self, key: str) -> dict | None:
        entry = self.diff_matches.get(key)
        return entry if isinstance(entry, dict) else None

    def put_diff(self, key: str, entry: dict) -> None:
        self.diff_matches[key] = entry
        self._new_count += 1
        if self._new_count % CACHE_SAVE_INTERVAL == 0:
            self.save()

    def save(self) -> None:
        if not self.similarities and not self.diff_matches and not self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": _now(),
            "key_format": "similarity: <角色>/<baseA>__<baseB>; diff: <角色>/<diff>",
            "similarities": self.similarities,
            "diff_matches": self.diff_matches,
        }
        with self.path.open("wt", encoding="utf8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


def _entry_from_detections(face: dict, head: dict) -> dict:
    return {
        "image_size": face.get("image_size"),
        "face": {
            "detected": face.get("detected"),
            "face_pos": face.get("face_pos"),
            "confidence": face.get("confidence"),
            "error": face.get("error"),
        },
        "head": {
            "head_detected": head.get("head_detected"),
            "head_pos": head.get("head_pos"),
            "head_confidence": head.get("head_confidence"),
            "head_error": head.get("head_error"),
        },
    }


def detect_face_head_path(
    image_path: str | Path,
    key: str,
    cache: FaceHeadCache,
    *,
    device: str | None = None,
    face_detector: Callable[[np.ndarray], list[dict]] | None = None,
    head_detector: Callable[[str], list[tuple[tuple[int, int, int, int], str, float]]]
    | None = None,
) -> tuple[dict, bool]:
    """Detect face + head on an image file, reusing/updating the cache."""
    entry = cache.get(key)
    if entry is not None:
        return entry, True
    face = detect.detect_top1(image_path, device=device, detector=face_detector)
    head = detect_bases.detect_head_top1(image_path, detector=head_detector)
    entry = _entry_from_detections(face, head)
    cache.put(key, entry)
    return entry, False


def detect_face_head_image(
    image: Image.Image,
    key: str,
    cache: FaceHeadCache,
    *,
    tmp_dir: str | Path | None = None,
    device: str | None = None,
    face_detector: Callable[[np.ndarray], list[dict]] | None = None,
    head_detector: Callable[[str], list[tuple[tuple[int, int, int, int], str, float]]]
    | None = None,
) -> tuple[dict, bool]:
    """Detect face + head on an in-memory image via a temp PNG file.

    The temp PNG is written to ``tmp_dir`` (default: the system temp dir via
    :func:`tempfile.mkstemp`) with a random name, so a crash that leaves a
    residual can never pollute the export directory (it would otherwise be
    globbed by ``*.png`` into npc-json / manifests / ``git add -A``). The file
    is always removed in the ``finally`` block.
    """
    entry = cache.get(key)
    if entry is not None:
        return entry, True
    if tmp_dir is not None:
        Path(tmp_dir).mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        suffix=".png",
        prefix="arknightsavatar_detect_",
        dir=str(tmp_dir) if tmp_dir is not None else None,
    )
    tmp = Path(tmp_name)
    try:
        os.close(fd)
        image.convert("RGBA").save(tmp)
        face = detect.detect_top1(tmp, device=device, detector=face_detector)
        head = detect_bases.detect_head_top1(tmp, detector=head_detector)
    finally:
        tmp.unlink(missing_ok=True)
    entry = _entry_from_detections(face, head)
    cache.put(key, entry)
    return entry, False


def _derive_from_entry(
    entry: dict,
    derive_model: dict,
    *,
    face_conf: float,
    head_conf: float,
) -> tuple[list[int], float] | None:
    """Apply tier-3 acceptance (face > face_conf, head > head_conf) + derive box."""
    face = entry.get("face") or {}
    head = entry.get("head") or {}
    fc = face.get("confidence")
    hc = head.get("head_confidence")
    if not (
        face.get("detected")
        and head.get("head_detected")
        and isinstance(fc, (int, float))
        and fc > face_conf
        and isinstance(hc, (int, float))
        and hc > head_conf
        and isinstance(face.get("face_pos"), dict)
        and isinstance(head.get("head_pos"), dict)
    ):
        return None
    box = derive_box(derive_model, face["face_pos"], head["head_pos"])
    return box, float(fc)


def resolve_base_box(
    name: str,
    base_name: str,
    base_path: Path,
    *,
    manual: dict,
    match_report: dict | None,
    force_match: bool,
    avatars_dir: Path,
    derive_model: dict,
    cache: FaceHeadCache,
    match_threshold: float = MATCH_THRESHOLD,
    face_conf: float = FACE_CONF,
    head_conf: float = HEAD_CONF,
    device: str | None = None,
    face_detector: Callable[[np.ndarray], list[dict]] | None = None,
    head_detector: Callable[[str], list[tuple[tuple[int, int, int, int], str, float]]]
    | None = None,
) -> tuple[list[int] | None, str | None, float | None, bool | None]:
    """Resolve a base's avatar box; ``(None, None, None, cache_hit)`` when no tier succeeds."""
    manual_entry = manual.get(f"{name}/{base_name}")
    if isinstance(manual_entry, dict) and _valid_box(manual_entry.get("box")):
        return _int_box(manual_entry["box"]), "manual", 1.0, None

    seq = match._char_seq(name)
    if seq:
        entry = None
        if match_report is not None and not force_match:
            char_item = (match_report.get("characters") or {}).get(name)
            if char_item:
                entry = (char_item.get("bases") or {}).get(base_name)
        if entry is None and (match_report is None or force_match):
            candidates = match._avatar_candidates(avatars_dir, seq, name)
            if candidates:
                result = match.match_base(base_path, candidates, avatars_dir)
                if result.ok and result.threshold is not None:
                    entry = {"threshold": result.threshold, "box": result.box}
        if isinstance(entry, dict):
            threshold = entry.get("threshold")
            box = entry.get("box")
            if (
                isinstance(threshold, (int, float))
                and threshold > match_threshold
                and _valid_box(box)
            ):
                return _int_box(box), "match", float(threshold), None

    entry, cache_hit = detect_face_head_path(
        base_path,
        f"{name}/{base_name}",
        cache,
        device=device,
        face_detector=face_detector,
        head_detector=head_detector,
    )
    derived = _derive_from_entry(
        entry, derive_model, face_conf=face_conf, head_conf=head_conf
    )
    if derived is None:
        return None, None, None, cache_hit
    box, confidence = derived
    return box, "derive", confidence, cache_hit


@dataclass
class ItemExtraction:
    """One base/diff extraction result."""

    status: str
    method: str | None = None
    confidence: float | None = None
    box: list[int] | None = None
    avatar_file: str | None = None
    error: str | None = None
    special: bool | None = None
    detect_cache_hit: bool | None = None

    def as_dict(self) -> dict:
        result = {"status": self.status}
        for key in (
            "method",
            "confidence",
            "box",
            "avatar_file",
            "error",
            "special",
            "detect_cache_hit",
        ):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        return result


@dataclass
class CharacterExtraction:
    name: str
    bases: dict[str, ItemExtraction] = field(default_factory=dict)
    diffs: dict[str, ItemExtraction] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "bases": {base: item.as_dict() for base, item in self.bases.items()},
            "diffs": {diff: item.as_dict() for diff, item in self.diffs.items()},
        }


@dataclass
class ExtractionReport:
    generated_at: str
    classified: str
    characters_dir: str
    match: str
    manual: str
    derive_model: str
    face_head_cache: str
    extract_cache: str
    output_dir: str
    thresholds: dict[str, float]
    characters: dict[str, CharacterExtraction]
    stats: dict[str, int]

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "classified": self.classified,
            "characters_dir": self.characters_dir,
            "match": self.match,
            "manual": self.manual,
            "derive_model": self.derive_model,
            "face_head_cache": self.face_head_cache,
            "extract_cache": self.extract_cache,
            "output_dir": self.output_dir,
            "thresholds": self.thresholds,
            "stats": self.stats,
            "characters": {
                name: item.as_dict() for name, item in self.characters.items()
            },
        }


_STALE_STATUSES = ("dropped", "no_box", "failed")


def _prune_stale_outputs(char_ext: CharacterExtraction, out_dir: Path) -> None:
    """Delete on-disk PNGs whose report status is no longer a kept output.

    Keeps ``skipped`` (prior valid output, untouched this run) and ``ok`` (just
    written) PNGs; removes ``dropped``/``no_box``/``failed`` ones so report
    status and on-disk state stay consistent -- otherwise a previously-ok avatar
    that is now dropped/no_box/failed would linger and be globbed by ``npc-json``
    / category manifests / ``sync-cache`` into the data repo.
    """
    for base_name, base_result in char_ext.bases.items():
        if base_result.status in _STALE_STATUSES:
            out_path = out_dir / f"{Path(base_name).stem}.png"
            if out_path.is_file():
                out_path.unlink()
    for diff_name, diff_result in char_ext.diffs.items():
        if diff_result.status in _STALE_STATUSES:
            out_path = out_dir / f"{Path(diff_name).stem}.png"
            if out_path.is_file():
                out_path.unlink()


def _dedup_bases(
    char_ext: CharacterExtraction,
    base_avatars: dict[str, Image.Image | None],
    *,
    dedup_sim: float = DEDUP_SIM,
    extract_cache: ExtractCache,
    stats: dict[str, int] | None = None,
) -> None:
    """Drop lower-confidence bases whose avatars are nearly identical (> dedup_sim)."""
    comparable = [
        name
        for name, avatar in base_avatars.items()
        if avatar is not None
        and char_ext.bases[name].status not in ("dropped", "no_box", "failed")
    ]
    if len(comparable) < 2:
        return
    ordered = sorted(
        comparable,
        key=lambda b: (
            -TIER_RANK.get(char_ext.bases[b].method or "", 0),
            -(char_ext.bases[b].confidence or 0.0),
            b,
        ),
    )
    for i, higher in enumerate(ordered):
        for lower in ordered[i + 1 :]:
            if char_ext.bases[lower].status == "dropped":
                continue
            if char_ext.bases[higher].status == "dropped":
                break
            key_a, key_b = sorted((higher, lower))
            avatar_a = base_avatars[key_a]
            avatar_b = base_avatars[key_b]
            if avatar_a is None or avatar_b is None:
                continue
            key = f"{char_ext.name}/{key_a}__{key_b}"
            hash_a = _image_fingerprint(avatar_a)
            hash_b = _image_fingerprint(avatar_b)
            entry = extract_cache.get_similarity(key)
            if (
                entry is not None
                and entry.get("hashA") == hash_a
                and entry.get("hashB") == hash_b
                and isinstance(entry.get("similarity"), (int, float))
            ):
                similarity = float(entry["similarity"])
                if stats is not None:
                    stats["similarity_cache_hits"] += 1
            else:
                similarity = avatar_similarity(avatar_a, avatar_b)
                extract_cache.put_similarity(
                    key,
                    {
                        "hashA": hash_a,
                        "hashB": hash_b,
                        "boxA": char_ext.bases[key_a].box,
                        "boxB": char_ext.bases[key_b].box,
                        "similarity": round(float(similarity), 6),
                    },
                )
                if stats is not None:
                    stats["similarity_cache_new"] += 1
            if similarity > dedup_sim:
                char_ext.bases[lower].status = "dropped"


def process_character(
    name: str,
    item: dict,
    *,
    characters_dir: Path,
    output_dir: Path,
    manual: dict,
    match_report: dict | None,
    force_match: bool,
    avatars_dir: Path,
    derive_model: dict,
    cache: FaceHeadCache,
    extract_cache: ExtractCache,
    force: bool = False,
    match_threshold: float = MATCH_THRESHOLD,
    face_conf: float = FACE_CONF,
    head_conf: float = HEAD_CONF,
    special_mask_iou: float = SPECIAL_MASK_IOU,
    dedup_sim: float = DEDUP_SIM,
    device: str | None = None,
    face_detector: Callable[[np.ndarray], list[dict]] | None = None,
    head_detector: Callable[[str], list[tuple[tuple[int, int, int, int], str, float]]]
    | None = None,
    stats: dict[str, int] | None = None,
    tmp_dir: str | Path | None = None,
) -> CharacterExtraction:
    """Extract one character's base/diff avatars and aggregate stats.

    ``tmp_dir`` is where in-memory special-diff detection temp PNGs are written
    (default ``None`` → the system temp dir, kept outside the export tree so
    crash residuals never enter npc-json/manifests/sync-cache).
    """
    if stats is None:
        stats = {key: 0 for key in STATS_KEYS}
    bases = item.get("bases") or {}
    char_dir = characters_dir / name
    out_dir = output_dir / name
    meta = _read_json(char_dir / "meta.json", {}) or {}
    face_groups = (
        meta.get("face_groups") if isinstance(meta.get("face_groups"), list) else []
    )
    alpha_img = None
    alpha_path = char_dir / "alpha.png"
    if alpha_path.is_file():
        try:
            alpha_img = Image.open(alpha_path).convert("RGBA")
        except Exception:  # noqa: BLE001 - unreadable alpha.png falls back to base alpha
            alpha_img = None
    char_ext = CharacterExtraction(name=name)
    base_avatars: dict[str, Image.Image | None] = {}

    for base_name in bases:
        stats["base_files"] += 1
        stem = Path(base_name).stem
        out_path = out_dir / f"{stem}.png"
        if out_path.is_file() and not force:
            # 复用既有 PNG（status=skipped）但补解析 box，否则该 base 的（新增/未提取）
            # diff 永远落入 box is None → no_box（旧增量缺陷：skipped base 不解析 box）。
            # 解析优先命中 match 报告/检测缓存，零额外开销；仅当两者皆无才回退检测并充
            # 实检测缓存。下面 diff 复用 base_result.box（同 ok 路径）。
            box, method, confidence, cache_hit = resolve_base_box(
                name,
                base_name,
                char_dir / base_name,
                manual=manual,
                match_report=match_report,
                force_match=force_match,
                avatars_dir=avatars_dir,
                derive_model=derive_model,
                cache=cache,
                match_threshold=match_threshold,
                face_conf=face_conf,
                head_conf=head_conf,
                device=device,
                face_detector=face_detector,
                head_detector=head_detector,
            )
            if cache_hit is not None:
                stats["detect_cache_hits" if cache_hit else "detect_cache_new"] += 1
            char_ext.bases[base_name] = ItemExtraction(
                status="skipped",
                avatar_file=str(out_path),
                box=box,
                method=method,
                confidence=confidence,
                detect_cache_hit=cache_hit,
            )
            try:
                base_avatars[base_name] = Image.open(out_path).convert("RGBA")
            except Exception:  # noqa: BLE001 - unreadable cached avatar cannot join dedup
                base_avatars[base_name] = None
            continue
        try:
            base_img = Image.open(char_dir / base_name)
        except Exception as error:  # noqa: BLE001 - single image failure does not abort the character
            char_ext.bases[base_name] = ItemExtraction(
                status="failed", error=f"{type(error).__name__}: {error}"
            )
            continue
        box, method, confidence, cache_hit = resolve_base_box(
            name,
            base_name,
            char_dir / base_name,
            manual=manual,
            match_report=match_report,
            force_match=force_match,
            avatars_dir=avatars_dir,
            derive_model=derive_model,
            cache=cache,
            match_threshold=match_threshold,
            face_conf=face_conf,
            head_conf=head_conf,
            device=device,
            face_detector=face_detector,
            head_detector=head_detector,
        )
        if cache_hit is not None:
            stats["detect_cache_hits" if cache_hit else "detect_cache_new"] += 1
        if box is None:
            char_ext.bases[base_name] = ItemExtraction(status="no_box")
            base_avatars[base_name] = None
            continue
        try:
            avatar = extract_avatar(base_img, box)
        except Exception as error:  # noqa: BLE001 - single image failure does not abort the character
            char_ext.bases[base_name] = ItemExtraction(
                status="failed", error=f"{type(error).__name__}: {error}"
            )
            continue
        char_ext.bases[base_name] = ItemExtraction(
            status="ok",
            method=method,
            confidence=confidence,
            box=box,
            detect_cache_hit=cache_hit,
        )
        base_avatars[base_name] = avatar

    _dedup_bases(
        char_ext,
        base_avatars,
        dedup_sim=dedup_sim,
        extract_cache=extract_cache,
        stats=stats,
    )

    for base_name, base_result in char_ext.bases.items():
        if base_result.status == "ok":
            avatar = base_avatars.get(base_name)
            if avatar is None:
                continue
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{Path(base_name).stem}.png"
            avatar.save(out_path)
            base_result.avatar_file = str(out_path)

    for base_name, base_entry in bases.items():
        diff_names = (
            base_entry.get("diff") or [] if isinstance(base_entry, dict) else []
        )
        diff_names = [
            diff_name for diff_name in diff_names if not _is_alpha_diff(diff_name)
        ]
        if char_ext.bases[base_name].status == "dropped":
            for diff_name in diff_names:
                stats["diff_files"] += 1
                char_ext.diffs[diff_name] = ItemExtraction(status="dropped")
            continue
        base_result = char_ext.bases[base_name]
        for diff_name in diff_names:
            stats["diff_files"] += 1
            stem = Path(diff_name).stem
            out_path = out_dir / f"{stem}.png"
            if out_path.is_file() and not force:
                char_ext.diffs[diff_name] = ItemExtraction(
                    status="skipped", avatar_file=str(out_path)
                )
                continue
            if base_result.box is None:
                char_ext.diffs[diff_name] = ItemExtraction(status="no_box")
                continue
            try:
                diff_img = Image.open(char_dir / diff_name)
                base_img = Image.open(char_dir / base_name)
                composed = compose_diff(
                    base_img,
                    diff_img,
                    face_group_for_diff(face_groups, diff_name),
                    alpha_img,
                )
            except Exception as error:  # noqa: BLE001 - single diff failure does not abort the character
                char_ext.diffs[diff_name] = ItemExtraction(
                    status="failed", error=f"{type(error).__name__}: {error}"
                )
                continue

            manual_entry = manual.get(f"{name}/{diff_name}")
            if isinstance(manual_entry, dict) and _valid_box(manual_entry.get("box")):
                box = _int_box(manual_entry["box"])
                method = "manual"
                confidence = 1.0
                cache_hit = None
                special = False
            else:
                diff_key = f"{name}/{diff_name}"
                fingerprint = _diff_fingerprint(
                    composed,
                    base_result.box,
                    base_result.method,
                    base_result.confidence,
                    special_mask_iou=special_mask_iou,
                    face_conf=face_conf,
                    head_conf=head_conf,
                    derive_model=derive_model,
                )
                cached = extract_cache.get_diff(diff_key)
                if (
                    cached is not None
                    and cached.get("fingerprint") == fingerprint
                    and isinstance(cached.get("iou"), (int, float))
                ):
                    stats["diff_match_cache_hits"] += 1
                    special = bool(cached.get("special"))
                    box = cached.get("box")
                    method = cached.get("method")
                    confidence = cached.get("confidence")
                    cache_hit = cached.get("detect_cache_hit")
                    if not _valid_box(box):
                        char_ext.diffs[diff_name] = ItemExtraction(
                            status="no_box",
                            special=special,
                            detect_cache_hit=cache_hit,
                            error=cached.get("error")
                            or "face/head confidence below thresholds",
                        )
                        continue
                    box = _int_box(box)
                else:
                    iou = alpha_mask_iou(composed, base_img, box=base_result.box)
                    if iou < special_mask_iou:
                        special = True
                        entry, cache_hit = detect_face_head_image(
                            composed,
                            diff_key,
                            cache,
                            tmp_dir=tmp_dir,
                            device=device,
                            face_detector=face_detector,
                            head_detector=head_detector,
                        )
                        if cache_hit is not None:
                            stats[
                                "detect_cache_hits" if cache_hit else "detect_cache_new"
                            ] += 1
                        derived = _derive_from_entry(
                            entry,
                            derive_model,
                            face_conf=face_conf,
                            head_conf=head_conf,
                        )
                        if derived is None:
                            extract_cache.put_diff(
                                diff_key,
                                {
                                    "fingerprint": fingerprint,
                                    "iou": round(float(iou), 6),
                                    "special": True,
                                    "box": None,
                                    "method": None,
                                    "confidence": None,
                                    "detect_cache_hit": cache_hit,
                                    "error": "face/head confidence below thresholds",
                                },
                            )
                            stats["diff_match_cache_new"] += 1
                            char_ext.diffs[diff_name] = ItemExtraction(
                                status="no_box",
                                special=True,
                                detect_cache_hit=cache_hit,
                                error="face/head confidence below thresholds",
                            )
                            continue
                        box, confidence = derived
                        method = "derive"
                    else:
                        special = False
                        box = base_result.box
                        method = base_result.method
                        confidence = base_result.confidence
                        cache_hit = None
                    extract_cache.put_diff(
                        diff_key,
                        {
                            "fingerprint": fingerprint,
                            "iou": round(float(iou), 6),
                            "special": special,
                            "box": box,
                            "method": method,
                            "confidence": confidence,
                            "detect_cache_hit": cache_hit,
                            "error": None,
                        },
                    )
                    stats["diff_match_cache_new"] += 1

            try:
                avatar = extract_avatar(composed, box)
            except Exception as error:  # noqa: BLE001 - single diff failure does not abort the character
                char_ext.diffs[diff_name] = ItemExtraction(
                    status="failed",
                    special=special,
                    error=f"{type(error).__name__}: {error}",
                )
                continue
            out_dir.mkdir(parents=True, exist_ok=True)
            avatar.save(out_path)
            char_ext.diffs[diff_name] = ItemExtraction(
                status="ok",
                method=method,
                confidence=confidence,
                box=box,
                avatar_file=str(out_path),
                special=special,
                detect_cache_hit=cache_hit,
            )

    _prune_stale_outputs(char_ext, out_dir)

    for base_result in char_ext.bases.values():
        stats[f"base_{base_result.status}"] += 1
    for diff_result in char_ext.diffs.values():
        stats[f"diff_{diff_result.status}"] += 1
        if diff_result.special:
            stats["diff_special"] += 1
    return char_ext


def extract_characters(
    classified: dict,
    characters_dir: str | Path,
    output_dir: str | Path,
    *,
    classified_file: str | Path | None = None,
    manual: dict | None = None,
    manual_file: str | Path | None = None,
    derive_model_file: str | Path | None = None,
    match_report: dict | None = None,
    match_file: str | Path | None = None,
    force_match: bool = False,
    avatars_dir: str | Path = DEFAULT_AVATARS_DIR,
    derive_model: dict,
    cache: FaceHeadCache,
    extract_cache: ExtractCache,
    force: bool = False,
    match_threshold: float = MATCH_THRESHOLD,
    face_conf: float = FACE_CONF,
    head_conf: float = HEAD_CONF,
    special_mask_iou: float = SPECIAL_MASK_IOU,
    dedup_sim: float = DEDUP_SIM,
    device: str | None = None,
    limit: int = 0,
    character: str | None = None,
    face_detector: Callable[[np.ndarray], list[dict]] | None = None,
    head_detector: Callable[[str], list[tuple[tuple[int, int, int, int], str, float]]]
    | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    skip: SkipList | None = None,
    tmp_dir: str | Path | None = None,
) -> ExtractionReport:
    """Extract avatars for all (or filtered) characters and aggregate a report."""
    characters_dir = Path(characters_dir)
    output_dir = Path(output_dir)
    avatars_dir = Path(avatars_dir)
    skip = skip or SkipList()
    classified = skip.filter_classified(classified)
    names = sorted((classified.get("characters") or {}).keys())
    if character is not None:
        names = [name for name in names if name == character]
    if limit:
        names = names[:limit]
    total = len(names)
    stats = {key: 0 for key in STATS_KEYS}
    characters: dict[str, CharacterExtraction] = {}
    manual = manual if manual is not None else {}

    for index, name in enumerate(names, 1):
        char_ext = process_character(
            name,
            classified["characters"][name],
            characters_dir=characters_dir,
            output_dir=output_dir,
            manual=manual,
            match_report=match_report,
            force_match=force_match,
            avatars_dir=avatars_dir,
            derive_model=derive_model,
            cache=cache,
            extract_cache=extract_cache,
            force=force,
            match_threshold=match_threshold,
            face_conf=face_conf,
            head_conf=head_conf,
            special_mask_iou=special_mask_iou,
            dedup_sim=dedup_sim,
            device=device,
            face_detector=face_detector,
            head_detector=head_detector,
            stats=stats,
            tmp_dir=tmp_dir,
        )
        characters[name] = char_ext
        stats["characters"] += 1
        if progress is not None:
            progress(index, total, name)

    cache.save()
    extract_cache.save()
    return ExtractionReport(
        generated_at=_now(),
        classified=str(classified_file) if classified_file is not None else "",
        characters_dir=str(characters_dir),
        match=str(match_file) if match_file is not None else "",
        manual=str(manual_file) if manual_file is not None else "",
        derive_model=str(derive_model_file) if derive_model_file is not None else "",
        face_head_cache=str(cache.path),
        extract_cache=str(extract_cache.path),
        output_dir=str(output_dir),
        thresholds={
            "match": match_threshold,
            "face": face_conf,
            "head": head_conf,
            "special_mask_iou": special_mask_iou,
            "dedup_sim": dedup_sim,
            "export_size": EXPORT_SIZE,
        },
        characters=characters,
        stats=stats,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-extract",
        description="Extract 180x180 avatars for character bases and diffs (incremental).",
    )
    parser.add_argument(
        "--classified",
        default=DEFAULT_CLASSIFIED,
        help="characters classification report",
    )
    parser.add_argument(
        "--characters-dir",
        default=DEFAULT_CHARACTERS_DIR,
        help="unpacked characters directory",
    )
    parser.add_argument(
        "--match", default=DEFAULT_MATCH, help="avatar match report (optional)"
    )
    parser.add_argument(
        "--avatars-dir", default=DEFAULT_AVATARS_DIR, help="unpacked avatars directory"
    )
    parser.add_argument(
        "--manual", default=DEFAULT_MANUAL, help="manual override JSON (optional)"
    )
    parser.add_argument(
        "--derive-model",
        default=DEFAULT_DERIVE_MODEL,
        help="face/head derive model JSON",
    )
    parser.add_argument(
        "--face-head-cache",
        default=DEFAULT_FACE_HEAD_CACHE,
        help="face/head detection cache JSON",
    )
    parser.add_argument(
        "--cache",
        default=DEFAULT_EXTRACT_CACHE,
        help="base similarity / diff match cache JSON",
    )
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR, help="avatar output directory"
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT, help="report path, or '-' for stdout"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="only process the first N characters"
    )
    parser.add_argument(
        "--character", default=None, help="only process the specified character name"
    )
    parser.add_argument(
        "--force", action="store_true", help="re-extract even when output PNGs exist"
    )
    parser.add_argument(
        "--force-match",
        action="store_true",
        help="re-run avatar matching instead of using the report",
    )
    parser.add_argument("--match-threshold", type=float, default=MATCH_THRESHOLD)
    parser.add_argument("--face-conf", type=float, default=FACE_CONF)
    parser.add_argument("--head-conf", type=float, default=HEAD_CONF)
    parser.add_argument("--special-mask-iou", type=float, default=SPECIAL_MASK_IOU)
    parser.add_argument("--dedup-sim", type=float, default=DEDUP_SIM)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
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
            total=total, unit="character", desc="extract avatar", dynamic_ncols=True
        )

        def progress(index: int, total_count: int, label: str) -> None:
            bar.set_postfix_str(label)
            bar.update(1)

        return progress, bar.close

    def progress(index: int, total_count: int, label: str) -> None:
        print(f"[{index}/{total_count}] {label}")

    return progress, lambda: None


def main(argv: list[str] | None = None) -> int:
    if cv2 is None or np is None or Image is None:
        print(
            "error: opencv-python-headless, numpy and Pillow are required (uv sync --extra detect)",
            file=sys.stderr,
        )
        return 1

    args = build_parser().parse_args(argv)
    for flag, value in (
        ("--match-threshold", args.match_threshold),
        ("--face-conf", args.face_conf),
        ("--head-conf", args.head_conf),
    ):
        if not 0.0 < value <= 1.0:
            print(f"error: {flag} must be in (0, 1] (got {value})", file=sys.stderr)
            return 1
    for flag, value in (
        ("--special-mask-iou", args.special_mask_iou),
        ("--dedup-sim", args.dedup_sim),
    ):
        if not 0.0 <= value <= 1.0:
            print(
                f"error: {flag} must be between 0 and 1 (got {value})", file=sys.stderr
            )
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
    try:
        derive_model = load_derive_model(args.derive_model)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    manual = load_manual(args.manual)
    match_report = load_match_report(args.match)
    cache = FaceHeadCache(args.face_head_cache)
    extract_cache = ExtractCache(args.cache)
    device = None if args.device == "auto" else args.device

    run_classified = skip_list.filter_classified(classified)
    names = sorted(run_classified["characters"])
    if args.character is not None:
        names = [name for name in names if name == args.character]
    if args.limit:
        names = names[: args.limit]
    progress, close_progress = _make_progress(len(names))
    try:
        report = extract_characters(
            classified,
            args.characters_dir,
            args.output_dir,
            classified_file=classified_path,
            manual=manual,
            manual_file=Path(args.manual),
            derive_model_file=args.derive_model,
            match_report=match_report,
            match_file=Path(args.match) if match_report is not None else None,
            force_match=args.force_match,
            avatars_dir=args.avatars_dir,
            derive_model=derive_model,
            cache=cache,
            extract_cache=extract_cache,
            force=args.force,
            match_threshold=args.match_threshold,
            face_conf=args.face_conf,
            head_conf=args.head_conf,
            special_mask_iou=args.special_mask_iou,
            dedup_sim=args.dedup_sim,
            device=device,
            limit=args.limit,
            character=args.character,
            progress=progress,
            skip=skip_list,
        )
    finally:
        cache.save()
        extract_cache.save()
        close_progress()

    stats = report.stats
    print(
        f"characters: {stats['characters']}  base_files: {stats['base_files']}  "
        f"base_ok: {stats['base_ok']}  base_skipped: {stats['base_skipped']}  "
        f"base_dropped: {stats['base_dropped']}  base_no_box: {stats['base_no_box']}"
    )
    print(
        f"diff_files: {stats['diff_files']}  diff_ok: {stats['diff_ok']}  "
        f"diff_skipped: {stats['diff_skipped']}  diff_dropped: {stats['diff_dropped']}  "
        f"diff_special: {stats['diff_special']}  diff_failed: {stats['diff_failed']}"
    )
    print(
        f"detect_cache: hits={stats['detect_cache_hits']}  new={stats['detect_cache_new']}"
    )
    print(
        f"similarity_cache: hits={stats['similarity_cache_hits']}  "
        f"new={stats['similarity_cache_new']}"
    )
    print(
        f"diff_match_cache: hits={stats['diff_match_cache_hits']}  "
        f"new={stats['diff_match_cache_new']}"
    )

    payload = report.as_dict()
    reporting.write_report(payload, args.output)
    if args.output != "-":
        print(f"report written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
