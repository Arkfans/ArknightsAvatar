from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from arknightsavatar import paths, reporting

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]

MATCH_SIZE = 1024
BASE_EXTEND_TOP = 76
MATCH_HEIGHT = MATCH_SIZE + BASE_EXTEND_TOP
COARSE_INCREASE = 5
FINE_INCREASE = 1
MIN_AVATAR_SIZE = 130
MAX_AVATAR_SIZE = 325
STOP_THRESHOLD = 0.70
CONFIDENCE_TARGET = 0.85
REMATCH_CONFIDENCE = 0.9
MAX_OPTIMIZE_TIMES = 50
FIND_MAX_OPTIMIZE_TIMES = 10

OUTPUT_BASE_SIZE = 512
AVATAR_MAX_SIZE = 256
BOX_COLOR = (0, 0, 255)
BOX_THICKNESS = 3
TEXT_COLOR = (0, 0, 255)

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


def _edit_distance(a: str, b: str) -> int:
    """标准 Levenshtein 编辑距离，小写比较。"""
    a = a.lower()
    b = b.lower()
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + int(ca != cb)))
        prev = curr
    return prev[-1]


def _avatar_candidates(
    avatars_dir: Path, seq: str, character: str | None = None
) -> list[str]:
    """avatars 目录下所有 char_<seq>_* 头像文件名。

    默认按文件名稳定排序；传入 character 时，先去掉其末尾的 _<数字> 变体编号，
    再与头像基名（去 .png、忽略大小写）计算编辑距离，按距离升序优先匹配。
    """
    if not avatars_dir.is_dir():
        return []
    pattern = re.compile(rf"^char_{re.escape(seq)}_", re.IGNORECASE)
    names = sorted(
        p.name for p in avatars_dir.glob("*.png") if p.is_file() and pattern.match(p.name)
    )
    if character is None:
        return names
    key = re.sub(r"_\d+$", "", character).lower()
    return sorted(names, key=lambda n: (_edit_distance(key, Path(n).stem), n))


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


def _read_rgba(path: Path) -> np.ndarray:
    """读取图像并归一化为 4 通道 BGRA（alpha 缺失时补 255），保留原始像素用于显示。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"cannot read image: {path}")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
    elif image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    return image


def _composite_on_color(rgba: np.ndarray, bg: tuple[int, int, int] = (255, 255, 255)) -> np.ndarray:
    """将 BGRA 图像 alpha 合成到纯色背景上，返回 3 通道 BGR。"""
    b, g, r, a = cv2.split(rgba)
    alpha = a.astype(np.float32) / 255.0
    bg_arr = np.array(bg, dtype=np.float32)
    blended = cv2.merge([b, g, r]).astype(np.float32) * alpha[:, :, None] + bg_arr * (1.0 - alpha[:, :, None])
    return blended.clip(0, 255).astype(np.uint8)


def render_match_image(
    base_path: Path,
    avatar_path: Path,
    box_norm: list[float],
    threshold: float,
    base_size: int = OUTPUT_BASE_SIZE,
    avatar_max: int = AVATAR_MAX_SIZE,
) -> np.ndarray:
    """渲染单张可视化图：512×512 base + 红框 + 匹配分数 + 右下角 overlay 头像。"""
    base_rgba = _read_rgba(base_path)
    base_resized = cv2.resize(base_rgba, (base_size, base_size))
    canvas = _composite_on_color(base_resized, bg=(255, 255, 255))

    if box_norm:
        x1 = max(0, round(box_norm[0] * base_size))
        y1 = max(0, round(box_norm[1] * base_size))
        x2 = min(base_size - 1, round(box_norm[2] * base_size))
        y2 = min(base_size - 1, round(box_norm[3] * base_size))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), BOX_COLOR, BOX_THICKNESS)
        label = f"{threshold:.4f}"
        anchor_y = y1 - 6 if y1 - 6 >= 16 else y1 + 16
        cv2.putText(canvas, label, (x1 + 4, anchor_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, TEXT_COLOR, 1, cv2.LINE_AA)

    try:
        avatar_rgba = _read_rgba(avatar_path)
        avatar_bgr = _composite_on_color(avatar_rgba, bg=(255, 255, 255))
    except Exception:  # noqa: BLE001 - 头像不可读时仅返回底图
        return canvas

    ah, aw = avatar_bgr.shape[:2]
    if max(ah, aw) > avatar_max:
        scale = avatar_max / max(ah, aw)
        avatar_bgr = cv2.resize(avatar_bgr, (round(aw * scale), round(ah * scale)))

    ah, aw = avatar_bgr.shape[:2]
    paste_x = base_size - aw
    paste_y = base_size - ah
    canvas[paste_y:paste_y + ah, paste_x:paste_x + aw] = avatar_bgr
    return canvas


def render_match_images(
    report: MatchReport,
    characters_dir: Path,
    avatars_dir: Path,
    image_dir: Path,
    base_size: int = OUTPUT_BASE_SIZE,
) -> int:
    """为每个匹配成功的 base 生成可视化 PNG，输出到扁平目录，返回写入数量。"""
    image_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for name, char in report.characters.items():
        for base_name, result in char.bases.items():
            if not result.ok or result.box_norm is None or result.avatar is None or result.threshold is None:
                continue
            try:
                out_name = f"{name}__{Path(base_name).stem}.png"
                out_path = image_dir / out_name
                image = render_match_image(
                    characters_dir / name / base_name,
                    avatars_dir / result.avatar,
                    result.box_norm,
                    result.threshold,
                    base_size=base_size,
                )
                ok, buf = cv2.imencode(".png", image)
                if not ok:
                    raise RuntimeError("imencode failed")
                buf.tofile(str(out_path))
                count += 1
            except Exception as error:  # noqa: BLE001 - 单张失败不中断
                print(f"warning: cannot render {name}/{base_name}: {error}", file=sys.stderr)
    return count


def _prepare_base(path: Path) -> tuple[np.ndarray, tuple[int, int]]:
    """底图灰度化、缩放到 MATCH_SIZE，并在顶部向上扩展 BASE_EXTEND_TOP 像素。

    返回 (gray, 原始尺寸 (w, h))；gray 高度为 MATCH_HEIGHT，扩展区位于原图
    上方（原图左上角对应 y=BASE_EXTEND_TOP，扩展区 y 为负）。
    """
    image = _read_bgr(path)
    size = (image.shape[1], image.shape[0])
    resized = cv2.resize(image, (MATCH_SIZE, MATCH_SIZE))
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    canvas = np.zeros((MATCH_HEIGHT, MATCH_SIZE), dtype=np.uint8)
    canvas[BASE_EXTEND_TOP:BASE_EXTEND_TOP + MATCH_SIZE, :] = gray
    return canvas, size


def _prepare_avatar(path: Path) -> np.ndarray:
    """头像灰度化（保持原始尺寸，缩放搜索在匹配内部进行）。"""
    image = _read_bgr(path)
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _template_match_gray(
    base: np.ndarray,
    avatar: np.ndarray,
    min_avatar_size: int = MIN_AVATAR_SIZE,
    max_avatar_size: int = MAX_AVATAR_SIZE,
    stop_threshold: float = STOP_THRESHOLD,
    coarse_threshold: float = CONFIDENCE_TARGET,
    coarse_increase: int = COARSE_INCREASE,
    detail: bool = False,
    top_offset: int = 0,
) -> tuple[float, tuple[int, int, int, int], list[OffsetMatch]]:
    """TM_CCOEFF_NORMED + 模板缩放搜索，返回 (threshold, box, offsets)。

    detail 为 True 时，offsets 记录每一次缩放 offset 的匹配明细（分数、位置、尺寸），
    否则为空列表。坐标以底图原图左上角为原点：top_offset 表示画布顶部相对原图的
    扩展行数，返回的 box 与 offsets.y 统一减去 top_offset，因此扩展区内坐标为负。

    缩放范围限制在 (min_avatar_size, max_avatar_size]，即模板短边/长边不超过
    max_avatar_size，也不低于 min_avatar_size（严格大于）。

    缩放搜索步进自适应：最佳匹配度低于 coarse_threshold 时以 coarse_increase 步长粗搜，
    达标后改用 FINE_INCREASE 微调；粗搜首次跨过阈值时按步长 x 回查最佳 offset 的
    ±1..±(x-1) 位置，避免粗步跳过中间偏移的峰值。
    """
    avatar_h, avatar_w = avatar.shape[:2]
    offsets: list[OffsetMatch] = []

    def _find(offset: int) -> np.ndarray:
        scaled = cv2.resize(avatar, (avatar_w + offset, avatar_h + offset))
        result = cv2.matchTemplate(base, scaled, cv2.TM_CCOEFF_NORMED)
        if detail and all(record.offset != offset for record in offsets):
            y, x = np.unravel_index(int(np.argmax(result)), result.shape)
            offsets.append(
                OffsetMatch(
                    offset=offset,
                    size=[avatar_w + offset, avatar_h + offset],
                    threshold=float(np.max(result)),
                    x=int(x),
                    y=int(y) - top_offset,
                )
            )
        return result

    def _check_valid_offset(offset: int) -> bool:
        return (
            min_avatar_size < avatar_h + offset <= max_avatar_size
            and min_avatar_size < avatar_w + offset <= max_avatar_size
        )

    offset = 0
    best_offset = 0
    res = _find(0)
    best_threshold = float(np.max(res))
    initial_threshold = best_threshold
    refined = False

    def _step() -> int:
        return coarse_increase if best_threshold < coarse_threshold else FINE_INCREASE

    def _update_best(o_offset: int, o_res: np.ndarray) -> None:
        """更新最佳 offset/阈值；首次跨阈值时按粗步长回查 ±1..±(coarse_increase-1)。"""
        nonlocal best_offset, best_threshold, res, refined
        crossed = best_threshold < coarse_threshold and float(np.max(o_res)) >= coarse_threshold
        best_offset = o_offset
        best_threshold = float(np.max(o_res))
        res = o_res
        if crossed and not refined:
            refined = True
            center = best_offset
            for distance in range(1, coarse_increase):
                for neighbor in (center - distance, center + distance):
                    if not _check_valid_offset(neighbor):
                        continue
                    neighbor_res = _find(neighbor)
                    if float(np.max(neighbor_res)) > best_threshold:
                        best_offset = neighbor
                        best_threshold = float(np.max(neighbor_res))
                        res = neighbor_res

    def _optimize(o_offset: int, o_increase: int) -> bool:
        nonlocal offset
        times = FIND_MAX_OPTIMIZE_TIMES if best_threshold > stop_threshold else MAX_OPTIMIZE_TIMES
        for _ in range(times):
            o_offset += _step() if o_increase > 0 else -_step()
            if not _check_valid_offset(o_offset):
                return False
            o_res = _find(o_offset)
            if float(np.max(o_res)) > best_threshold:
                offset = o_offset
                _update_best(o_offset, o_res)
                return True
        return False

    increase = _step() if float(np.max(_find(_step()))) > initial_threshold else -_step()
    if_reversed = False

    while True:
        offset += _step() if increase > 0 else -_step()
        if not _check_valid_offset(offset):
            if not if_reversed and best_threshold < stop_threshold:
                if_reversed = True
                if _optimize(0, -increase):
                    increase = -increase
                    continue
            break
        current = _find(offset)
        if float(np.max(current)) > best_threshold:
            _update_best(offset, current)
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
    y -= top_offset
    w = avatar_w + best_offset
    h = avatar_h + best_offset
    if detail:
        best_record = None
        for record in offsets:
            if record.offset == best_offset:
                best_record = record
        if best_record is not None:
            best_record.best = True
    return best_threshold, (int(x), int(y), int(x + w), int(y + h)), offsets


def template_match(
    base_path: Path | str,
    avatar_path: Path | str,
    min_avatar_size: int = MIN_AVATAR_SIZE,
    max_avatar_size: int = MAX_AVATAR_SIZE,
    stop_threshold: float = STOP_THRESHOLD,
    coarse_threshold: float = CONFIDENCE_TARGET,
    coarse_increase: int = COARSE_INCREASE,
) -> tuple[float, tuple[int, int, int, int]]:
    """对单张底图与单张头像做模板匹配，box 位于底图坐标系（原图顶部 y=0，扩展区为负）。"""
    base, _size = _prepare_base(Path(base_path))
    avatar = _prepare_avatar(Path(avatar_path))
    threshold, box, _offsets = _template_match_gray(
        base,
        avatar,
        min_avatar_size,
        max_avatar_size,
        stop_threshold,
        coarse_threshold,
        coarse_increase,
        top_offset=BASE_EXTEND_TOP,
    )
    return threshold, box


@dataclass
class OffsetMatch:
    """一次缩放 offset 的匹配明细；坐标位于底图坐标系（扩展区 y 为负）。"""

    offset: int
    size: list[int]
    threshold: float
    x: int
    y: int
    best: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> OffsetMatch:
        """从报告 JSON 条目还原 OffsetMatch。"""
        return cls(
            offset=data["offset"],
            size=data["size"],
            threshold=data["threshold"],
            x=data["x"],
            y=data["y"],
            best=bool(data.get("best", False)),
        )

    def as_dict(self) -> dict:
        return {
            "offset": self.offset,
            "size": self.size,
            "threshold": self.threshold,
            "x": self.x,
            "y": self.y,
            "best": self.best,
        }


@dataclass
class BaseMatch:
    """一张底图的匹配结果；error 非空表示该底图匹配失败。"""

    avatar: str | None = None
    threshold: float | None = None
    box: list[int] | None = None
    box_norm: list[float] | None = None
    error: str | None = None
    offsets: dict[str, list[OffsetMatch]] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def from_dict(cls, data: dict) -> BaseMatch:
        """从报告 JSON 条目还原 BaseMatch（error-only 与完整字段两种形态）。"""
        if "error" in data:
            return cls(error=data["error"])
        offsets = data.get("offsets")
        return cls(
            avatar=data.get("avatar"),
            threshold=data.get("threshold"),
            box=data.get("box"),
            box_norm=data.get("box_norm"),
            offsets=(
                {
                    name: [OffsetMatch.from_dict(record) for record in records]
                    for name, records in offsets.items()
                }
                if offsets is not None
                else None
            ),
        )

    def as_dict(self) -> dict:
        if self.error is not None:
            return {"error": self.error}
        result = {
            "avatar": self.avatar,
            "threshold": self.threshold,
            "box": self.box,
            "box_norm": self.box_norm,
        }
        if self.offsets is not None:
            result["offsets"] = {
                name: [record.as_dict() for record in records]
                for name, records in self.offsets.items()
            }
        return result


@dataclass
class CharacterMatch:
    name: str
    status: str
    candidates: list[str]
    bases: dict[str, BaseMatch] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict) -> CharacterMatch:
        """从报告 JSON 条目还原 CharacterMatch（增量合并旧报告时使用）。"""
        return cls(
            name=name,
            status=str(data.get("status", "ok")),
            candidates=list(data.get("candidates") or []),
            bases={
                base: BaseMatch.from_dict(item) for base, item in (data.get("bases") or {}).items()
            },
        )

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
    max_avatar_size: int = MAX_AVATAR_SIZE,
    stop_threshold: float = STOP_THRESHOLD,
    confidence_target: float = CONFIDENCE_TARGET,
    coarse_increase: int = COARSE_INCREASE,
    detail: bool = False,
) -> BaseMatch:
    """用一张底图对多个候选头像匹配，取阈值最高者；坐标换算回底图原始像素。

    候选按传入顺序逐个完整匹配，取阈值最高者；某头像阈值高于 confidence_target 时立即采用
    该结果并跳过后续候选（候选级早停）；confidence_target 同时也是每个候选缩放搜索的
    粗搜/微调切换阈值（低于它用 coarse_increase 步长粗搜，达标后 1px 微调）。
    顶部扩展区对应原始像素中的负 y。
    """
    try:
        base_gray, (width, height) = _prepare_base(base_path)
    except Exception as error:  # noqa: BLE001 - 单张底图失败不中断整体
        return BaseMatch(error=f"{type(error).__name__}: {error}")

    best: tuple[float, tuple[int, int, int, int], str] | None = None
    offsets: dict[str, list[OffsetMatch]] | None = {} if detail else None
    for avatar_name in avatar_paths:
        try:
            avatar_gray = _prepare_avatar(avatars_dir / avatar_name)
        except Exception:  # noqa: BLE001 - 单个头像不可读时跳过
            continue
        threshold, box, candidate_offsets = _template_match_gray(
            base_gray,
            avatar_gray,
            min_avatar_size,
            max_avatar_size,
            stop_threshold,
            coarse_threshold=confidence_target,
            coarse_increase=coarse_increase,
            detail=detail,
            top_offset=BASE_EXTEND_TOP,
        )
        if offsets is not None:
            offsets[avatar_name] = candidate_offsets
        if best is None or threshold > best[0]:
            best = (threshold, box, avatar_name)
        if threshold > confidence_target:
            break

    if best is None:
        return BaseMatch(error="no readable avatar")

    threshold, box, avatar_name = best
    sx = width / MATCH_SIZE
    sy = height / MATCH_SIZE
    box_orig = [round(box[0] * sx), round(box[1] * sy), round(box[2] * sx), round(box[3] * sy)]
    box_norm = [round(v / MATCH_SIZE, 6) for v in box]
    return BaseMatch(
        avatar=avatar_name,
        threshold=threshold,
        box=box_orig,
        box_norm=box_norm,
        offsets=offsets,
    )


def compute_stats(
    characters: dict[str, CharacterMatch],
    stop_threshold: float = STOP_THRESHOLD,
    classified: dict | None = None,
) -> dict[str, int]:
    """从角色结果聚合统计（全量匹配与增量合并共用同一实现）。

    base_files 统计分类报告中的底图条目数（含未匹配/无候选头像的底图），
    与旧实现一致；未提供 classified 时退化为按已匹配结果计数。
    """
    source = (classified or {}).get("characters") or {}
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
    for match in characters.values():
        stats["total"] += 1
        if source:
            stats["base_files"] += len((source.get(match.name) or {}).get("bases") or {})
        else:
            stats["base_files"] += len(match.bases)
        for result in match.bases.values():
            if result.ok:
                stats["matched_bases"] += 1
                if result.threshold is not None and result.threshold < stop_threshold:
                    stats["low_confidence"] += 1
        stats[match.status] += 1
    return stats


def needs_rematch(
    old: dict,
    new_candidates: list[str],
    rematch_confidence: float = REMATCH_CONFIDENCE,
) -> bool:
    """候选头像更新后，该角色（旧报告条目）是否需要重匹配。

    仅当候选列表发生变化且并非所有 base 都高置信匹配时才需要重匹配：
    - 任一 base 匹配失败（error，无 threshold）；
    - 任一 base 的 threshold 低于 rematch_confidence；
    - 旧结果引用的头像已不在新候选列表中（悬空引用保护，避免下游读到
      已不存在的头像文件）；
    - 旧报告没有任何 base 结果（如之前 no_avatar，现新增了候选头像）。
    候选列表未变化时一律返回 False，严格遵守「候选更新 + 低置信」双条件。
    """
    if (old.get("candidates") or []) == new_candidates:
        return False
    bases = old.get("bases") or {}
    if not bases:
        return True
    for result in bases.values():
        if "error" in result:
            return True
        if result.get("avatar") not in new_candidates:
            return True
        threshold = result.get("threshold")
        if threshold is None or threshold < rematch_confidence:
            return True
    return False


def plan_rematch(
    classified: dict,
    old_characters: dict[str, dict],
    avatars_dir: Path,
    rematch_confidence: float = REMATCH_CONFIDENCE,
    character: str | None = None,
) -> tuple[set[str], set[str]]:
    """按分类报告顺序决定哪些角色需要重匹配，返回 (需重匹配, 保持不变)。

    不在旧报告中的角色视为新角色，直接归入需重匹配；候选列表未变化或
    所有 base 均高置信匹配的角色保持不变。
    """
    to_rematch: set[str] = set()
    kept: set[str] = set()
    for name in classified.get("characters") or {}:
        if not _is_target_character(name):
            continue
        if character is not None and name != character:
            continue
        old = old_characters.get(name)
        if old is None:
            to_rematch.add(name)
            continue
        seq = _char_seq(name)
        candidates = _avatar_candidates(avatars_dir, seq, name) if seq else []
        if needs_rematch(old, candidates, rematch_confidence):
            to_rematch.add(name)
        else:
            kept.add(name)
    return to_rematch, kept


def match_characters(
    classified: dict,
    characters_dir: Path,
    avatars_dir: Path,
    classified_path: Path | None = None,
    limit: int = 0,
    character: str | None = None,
    only: set[str] | None = None,
    min_avatar_size: int = MIN_AVATAR_SIZE,
    max_avatar_size: int = MAX_AVATAR_SIZE,
    stop_threshold: float = STOP_THRESHOLD,
    confidence_target: float = CONFIDENCE_TARGET,
    coarse_increase: int = COARSE_INCREASE,
    detail: bool = False,
    progress: Callable[[int, str], None] | None = None,
) -> MatchReport:
    """遍历分类报告中符合条件的角色并匹配其底图，聚合统计；指定 character 时只处理该角色。

    only 非空时只处理集合内的角色（与 character/limit 过滤叠加，增量重匹配使用）。
    """
    characters: dict[str, CharacterMatch] = {}
    processed = 0

    for name, item in classified.get("characters", {}).items():
        if not _is_target_character(name):
            continue
        if character is not None and name != character:
            continue
        if only is not None and name not in only:
            continue
        if limit and processed >= limit:
            break
        processed += 1

        bases = item.get("bases") or {}
        seq = _char_seq(name)
        candidates = _avatar_candidates(avatars_dir, seq, name) if seq else []
        match = CharacterMatch(name=name, status="ok", candidates=candidates)

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
                    max_avatar_size=max_avatar_size,
                    stop_threshold=stop_threshold,
                    confidence_target=confidence_target,
                    coarse_increase=coarse_increase,
                    detail=detail,
                )
                match.bases[base_name] = result
                if not result.ok:
                    match.status = "failed"

        characters[name] = match
        if progress is not None:
            progress(processed, name)

    return MatchReport(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        classified=str(classified_path or characters_dir.parent / "_characters_classified.json"),
        characters_dir=str(characters_dir),
        avatars_dir=str(avatars_dir),
        characters=characters,
        stats=compute_stats(characters, stop_threshold=stop_threshold, classified=classified),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-match",
        description="Match avatars against character base images to locate avatar ranges.",
    )
    parser.add_argument(
        "--classified",
        default=paths.CLASSIFIED,
        help=f"characters classification report path (default: {paths.CLASSIFIED})",
    )
    parser.add_argument(
        "--characters-dir",
        default=paths.UNPACKED_CHARACTERS_DIR,
        help=f"unpacked characters directory (default: {paths.UNPACKED_CHARACTERS_DIR})",
    )
    parser.add_argument(
        "--avatars-dir",
        default=paths.UNPACKED_AVATARS_DIR,
        help=f"unpacked avatars directory (default: {paths.UNPACKED_AVATARS_DIR})",
    )
    parser.add_argument(
        "--output",
        default=paths.AVATAR_MATCH,
        help=f"JSON report path, or '-' for stdout (default: {paths.AVATAR_MATCH})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="only process the first N matched characters (default: 0 = all)",
    )
    parser.add_argument(
        "--character",
        default=None,
        help="only process the specified character name, e.g. avg_003_kalts_1 (default: all)",
    )
    parser.add_argument(
        "--min-avatar-size",
        type=int,
        default=MIN_AVATAR_SIZE,
        help=f"minimum template size during scale search (default: {MIN_AVATAR_SIZE})",
    )
    parser.add_argument(
        "--max-avatar-size",
        type=int,
        default=MAX_AVATAR_SIZE,
        help=f"maximum template size during scale search (default: {MAX_AVATAR_SIZE})",
    )
    parser.add_argument(
        "--stop-threshold",
        type=float,
        default=STOP_THRESHOLD,
        help=f"threshold guiding search direction; results below it count as low confidence (default: {STOP_THRESHOLD})",
    )
    parser.add_argument(
        "--confidence-target",
        type=float,
        default=CONFIDENCE_TARGET,
        help=(
            f"threshold guiding coarse-to-fine scale search and stopping further "
            f"candidate avatars once reached (default: {CONFIDENCE_TARGET})"
        ),
    )
    parser.add_argument(
        "--coarse-increase",
        type=int,
        default=COARSE_INCREASE,
        help=(
            f"coarse scale-search step in pixels, >= 2; skipped offsets within ±(step-1) "
            f"are re-checked after crossing the threshold (default: {COARSE_INCREASE})"
        ),
    )
    parser.add_argument(
        "--rematch-confidence",
        type=float,
        default=REMATCH_CONFIDENCE,
        help=(
            "when a character's candidate avatar list changed, re-match it only if "
            f"some base has a threshold below this value (default: {REMATCH_CONFIDENCE})"
        ),
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="include per-offset scale search details for every candidate avatar in the report (default: off)",
    )
    parser.add_argument(
        "--image-dir",
        default=None,
        help="optional directory for match visualization PNGs (one per matched base; default: off)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-run matching even if the report already exists",
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
    if args.coarse_increase < 2:
        print(
            f"error: --coarse-increase must be >= 2 (got {args.coarse_increase})",
            file=sys.stderr,
        )
        return 1
    if args.min_avatar_size >= args.max_avatar_size:
        print(
            f"error: --min-avatar-size ({args.min_avatar_size}) must be "
            f"less than --max-avatar-size ({args.max_avatar_size})",
            file=sys.stderr,
        )
        return 1
    if not 0 < args.rematch_confidence <= 1:
        print(
            f"error: --rematch-confidence must be in (0, 1] (got {args.rematch_confidence})",
            file=sys.stderr,
        )
        return 1
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
    if args.character is not None:
        if args.character not in (classified.get("characters") or {}):
            print(f"error: character not found in {classified_path}: {args.character}", file=sys.stderr)
            return 1
        if not _is_target_character(args.character):
            print(
                f"error: {args.character} is not a matchable name "
                f"(expected avg_<id>_... or char_<id>_...)",
                file=sys.stderr,
            )
            return 1

    output = Path(args.output)
    old_report: dict | None = None
    if args.output != "-" and output.is_file() and not args.force:
        try:
            with output.open("rt", encoding="utf8") as f:
                old_report = json.load(f)
        except (OSError, ValueError) as error:
            print(
                f"warning: cannot read existing report {output}: {error}; full match",
                file=sys.stderr,
            )
        if old_report is not None and not isinstance(old_report.get("characters"), dict):
            print(
                f"warning: existing report {output} has no characters map; full match",
                file=sys.stderr,
            )
            old_report = None

    to_rematch: set[str] = set()
    kept: set[str] = set()
    if old_report is not None:
        to_rematch, kept = plan_rematch(
            classified,
            old_report.get("characters") or {},
            Path(args.avatars_dir),
            args.rematch_confidence,
            character=args.character,
        )
        if not to_rematch:
            print(
                f"no candidate changes to re-match, skipping match: {output} "
                "(use --force to re-run)",
                file=sys.stdout,
            )
            return 0
        print(
            f"report exists, re-matching {len(to_rematch)} character(s) "
            f"(candidates changed and low confidence), keeping {len(kept)} unchanged",
            file=sys.stdout,
        )

    report = match_characters(
        classified,
        Path(args.characters_dir),
        Path(args.avatars_dir),
        classified_path=classified_path,
        limit=args.limit,
        character=args.character,
        only=to_rematch or None,
        min_avatar_size=args.min_avatar_size,
        max_avatar_size=args.max_avatar_size,
        stop_threshold=args.stop_threshold,
        confidence_target=args.confidence_target,
        coarse_increase=args.coarse_increase,
        detail=args.detail,
        progress=_on_progress,
    )
    if old_report is not None:
        # 增量合并：新结果覆盖旧条目，其余角色保留旧报告结果，避免子集重匹配丢数据。
        merged: dict[str, CharacterMatch] = {}
        for name in classified.get("characters") or {}:
            if not _is_target_character(name):
                continue
            if args.character is not None and name != args.character:
                continue
            if name in report.characters:
                merged[name] = report.characters[name]
            elif name in (old_report.get("characters") or {}):
                merged[name] = CharacterMatch.from_dict(name, old_report["characters"][name])
        report = MatchReport(
            generated_at=report.generated_at,
            classified=report.classified,
            characters_dir=report.characters_dir,
            avatars_dir=report.avatars_dir,
            characters=merged,
            stats=compute_stats(merged, stop_threshold=args.stop_threshold, classified=classified),
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
    reporting.write_report(payload, args.output)
    if args.output != "-":
        print(f"report written: {args.output}")

    if args.image_dir is not None:
        image_dir = Path(args.image_dir)
        img_count = render_match_images(
            report,
            Path(args.characters_dir),
            Path(args.avatars_dir),
            image_dir,
        )
        print(f"images written: {img_count} -> {image_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
