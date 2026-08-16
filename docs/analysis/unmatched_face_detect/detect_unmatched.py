"""对“没有对应 avatar”的底图做人脸 + 头部识别，并用 07/08 模型推导裁切框。

底图池 = 没有对应 avatar 的全部底图（base 从 ``_characters_classified.json``
获取）：

- 完全未出现在 ``_avatar_match.json`` 中的角色底图（绝大多数为
  ``avg_npc_*`` 等 NPC 角色，因命名不符合 avg_\\d+_ / char_\\d+ 从未参与匹配，
  如 ``avg_npc_1561_1$1.png``、``avg_npc_1614_1$1.png``）；
- ``_avatar_match.json`` 中 ``no_avatar`` 角色（无候选头像）登记的全部底图。

处理流程：固定随机种子打乱底图池，逐张做人脸（YOLOv3 top-1）与头部
（imgutils top-1）识别；face 或 head 置信度低于 ``--min-conf``（默认 0.7）
的底图跳过，继续取下一个，直到收集满 ``--n`` 张（默认 100）。对通过者，
用 ``avatar_derive``（07）与 ``avatar_derive_08``（08）两个推导模型的系数，
由 face/head 检测框推导正方形裁切框并绘制。

输出到脚本所在目录：

- ``report.json``：全部处理记录（含推导框与置信度）
- ``summary.csv``：每张底图的置信度与推导框
- ``stats.json``：汇总统计
- ``compare/``：通过阈值底图的标注 PNG（黄框 = 人脸，青框 = 头部，
  红虚线 = 07 推导框，蓝虚线 = 08 推导框，顶部图例标注 face/head 阈值）
- ``README.md``：说明
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

DATA = Path(__file__).resolve().parents[1]
DEFAULT_MATCH = DATA / "unpacked" / "_avatar_match.json"
DEFAULT_CLASSIFIED = DATA / "unpacked" / "_characters_classified.json"
DEFAULT_MODEL_07 = DATA / "avatar_derive" / "model.json"
DEFAULT_MODEL_08 = DATA / "avatar_derive_08" / "model.json"
DEFAULT_OUT = Path(__file__).resolve().parent
DEFAULT_N = 100
DEFAULT_SEED = 42
DEFAULT_MIN_CONF = 0.7

FACE_COLOR = (255, 255, 0)  # 黄色：人脸框
HEAD_COLOR = (0, 255, 255)  # 青色：头部框
D07_COLOR = (255, 60, 60)  # 红色虚线：07 推导框
D08_COLOR = (0, 170, 255)  # 蓝色虚线：08 推导框


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_pool(match: dict, classified: dict) -> list[dict]:
    """构造没有对应 avatar 的底图池（base 来自分类报告）。"""
    char_dir = Path(match.get("characters_dir", "data/unpacked/characters"))
    classified_chars = classified.get("characters") or {}
    reported = set(match.get("characters") or {})
    pool: list[dict] = []

    for cname, cdata in (match.get("characters") or {}).items():
        if cdata.get("status") != "no_avatar":
            continue
        for bname in classified_chars.get(cname, {}).get("bases") or {}:
            pool.append(
                {
                    "character": cname,
                    "base": bname,
                    "image": str(char_dir / cname / bname),
                    "reason": "no_avatar",
                    "match_threshold": None,
                }
            )

    for cname, cdata in classified_chars.items():
        if cname in reported:
            continue
        for bname in cdata.get("bases") or {}:
            pool.append(
                {
                    "character": cname,
                    "base": bname,
                    "image": str(char_dir / cname / bname),
                    "reason": "no_avatar_unreported",
                    "match_threshold": None,
                }
            )
    return pool


def load_model(path: Path) -> np.ndarray:
    """读取 derive model.json，返回 9x3 系数矩阵（8 特征 + 偏置，目标 cx/cy/s）。"""
    model = load_json(path)
    return np.asarray(model["coef"], dtype=float)


def derive_box(
    coef: np.ndarray, face_pos: dict, head_pos: dict
) -> tuple[list[int], list[float]]:
    """由 face/head 检测框推导正方形裁切框，返回 (整数框, 浮点框)。"""
    f, h = face_pos, head_pos
    feats = [
        f["x"] + f["w"] / 2.0,
        f["y"] + f["h"] / 2.0,
        f["w"],
        f["h"],
        h["x"] + h["w"] / 2.0,
        h["y"] + h["h"] / 2.0,
        h["w"],
        h["h"],
    ]
    cx, cy, s = (np.asarray(feats + [1.0]) @ coef).tolist()
    exact = [cx - s / 2.0, cy - s / 2.0, cx + s / 2.0, cy + s / 2.0]
    box = [round(v) for v in exact]
    return box, exact


def detect_one(
    item: dict, device: str | None, coef07: np.ndarray, coef08: np.ndarray
) -> None:
    from arknightsavatar import detect
    from arknightsavatar.detect_bases import detect_head_top1

    face = detect.detect_top1(item["image"], device=device, conf=0.0)
    head = detect_head_top1(item["image"], conf=0.0)
    item["image_size"] = face["image_size"]
    item["face_pos"] = face["face_pos"]
    item["face_confidence"] = face["confidence"]
    item["face_error"] = face["error"]
    item["head_pos"] = head["head_pos"]
    item["head_confidence"] = head["head_confidence"]
    item["head_error"] = head["head_error"]
    if face["face_pos"] and head["head_pos"]:
        box07, exact07 = derive_box(coef07, face["face_pos"], head["head_pos"])
        box08, exact08 = derive_box(coef08, face["face_pos"], head["head_pos"])
        item["derived_box07"] = box07
        item["derived_box08"] = box08
        item["derived_box07_exact"] = [round(v, 2) for v in exact07]
        item["derived_box08_exact"] = [round(v, 2) for v in exact08]


def load_font(size: int = 28):
    for name in ("arial.ttf", "segoeui.ttf", "msyh.ttc"):
        cand = Path("C:/Windows/Fonts") / name
        if cand.exists():
            try:
                return ImageFont.truetype(str(cand), size)
            except Exception:  # noqa: BLE001, S110 - 字体缺失时回退默认字体
                pass
    return ImageFont.load_default()


def draw_dashed_rect(draw, box, color, width, dash=14, gap=10, phase=0):
    x1, y1, x2, y2 = map(float, box)
    edges = [
        ((x1, y1), (x2, y1)),
        ((x1, y2), (x2, y2)),
        ((x1, y1), (x1, y2)),
        ((x2, y1), (x2, y2)),
    ]
    for (ax, ay), (bx, by) in edges:
        length = abs(bx - ax) + abs(by - ay)
        step = max(1.0, dash + gap)
        t = -phase
        while t < length:
            seg_end = min(t + dash, length)
            if seg_end > t:
                dx = (bx - ax) / length if length else 0.0
                dy = (by - ay) / length if length else 0.0
                draw.line(
                    [ax + t * dx, ay + t * dy, ax + seg_end * dx, ay + seg_end * dy],
                    fill=color,
                    width=width,
                )
            t += step


def draw_image(
    row: dict, out_png: Path, font: ImageFont.ImageFont, min_conf: float
) -> None:
    img = Image.open(row["image"]).convert("RGB")
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)

    if row["face_pos"]:
        f = row["face_pos"]
        dr.rectangle(
            [f["x"], f["y"], f["x"] + f["w"] - 1, f["y"] + f["h"] - 1],
            outline=FACE_COLOR + (255,),
            width=2,
        )
    if row["head_pos"]:
        hp = row["head_pos"]
        dr.rectangle(
            [hp["x"], hp["y"], hp["x"] + hp["w"] - 1, hp["y"] + hp["h"] - 1],
            outline=HEAD_COLOR + (255,),
            width=3,
        )
    if "derived_box07" in row:
        draw_dashed_rect(dr, row["derived_box07"], D07_COLOR + (255,), width=5, phase=0)
    if "derived_box08" in row:
        draw_dashed_rect(dr, row["derived_box08"], D08_COLOR + (255,), width=5, phase=7)
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    reason = {
        "no_avatar": "no avatar candidate",
        "no_avatar_unreported": "not in match report (NPC)",
    }.get(row["reason"], row["reason"])
    match_label = f"match: {reason}"
    face_label = f"face conf >= {min_conf:.1f} ({row['face_confidence']:.4f})"
    head_label = f"head conf >= {min_conf:.1f} ({row['head_confidence']:.4f})"

    header_h = 6 + font.size + 10 + 4 * (26 + 6) + 6
    canvas = Image.new("RGB", (w, h + header_h), (26, 26, 32))
    canvas.paste(img, (0, header_h))
    dr = ImageDraw.Draw(canvas)
    pad = 10
    title = f"{row['character']} / {row['base']}"
    dr.text((pad, 6), title, font=font, fill=(255, 255, 255))
    y = 6 + font.size + 10
    dr.text((pad, y), match_label, font=font, fill=(220, 220, 220))
    for label, color, yy in [
        (face_label, FACE_COLOR, y + 32),
        (head_label, HEAD_COLOR, y + 64),
        ("derive07 box", D07_COLOR, y + 96),
        ("derive08 box", D08_COLOR, y + 128),
    ]:
        dr.rectangle([pad, yy, pad + 26, yy + 18], fill=color + (255,))
        dr.text((pad + 34, yy - 2), label, font=font, fill=(255, 255, 255))
    canvas.save(out_png)


def write_outputs(
    rows: list[dict],
    accepted: list[dict],
    out_dir: Path,
    min_conf: float,
    device: str | None,
    pool_size: int,
    n: int,
    seed: int,
) -> None:
    vis_dir = out_dir / "compare"
    vis_dir.mkdir(parents=True, exist_ok=True)
    font = load_font(28)
    for row in accepted:
        safe = row["base"].replace("$", "_").replace("#", "_")
        if safe.lower().endswith(".png"):
            safe = safe[:-4]
        name = (
            f"{row['character']}__{safe}__"
            f"face_{row['face_confidence']:.3f}_head_{row['head_confidence']:.3f}.png"
        )
        draw_image(row, vis_dir / name, font, min_conf)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "min_conf": min_conf,
        "device": device,
        "sample": {"n_target": n, "seed": seed},
        "model_07": str(DEFAULT_MODEL_07),
        "model_08": str(DEFAULT_MODEL_08),
        "stats": {
            "pool_size": pool_size,
            "processed": len(rows),
            "accepted": len(accepted),
            "skipped": len(rows) - len(accepted),
        },
        "rows": rows,
    }
    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "character",
                "base",
                "reason",
                "face_confidence",
                "head_confidence",
                "derived_box07",
                "derived_box08",
                "accepted",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r["character"],
                    r["base"],
                    r["reason"],
                    ""
                    if r.get("face_confidence") is None
                    else round(r["face_confidence"], 4),
                    ""
                    if r.get("head_confidence") is None
                    else round(r["head_confidence"], 4),
                    r.get("derived_box07", ""),
                    r.get("derived_box08", ""),
                    r.get("accepted", False),
                ]
            )

    stats = {
        "pool_size": pool_size,
        "processed": len(rows),
        "accepted": len(accepted),
        "skipped": len(rows) - len(accepted),
        "reached_target": len(accepted) >= n,
    }
    (out_dir / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"draw images: {len(accepted)} -> {vis_dir}")
    print(f"stats: {json.dumps(stats, ensure_ascii=False)}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match", default=str(DEFAULT_MATCH))
    parser.add_argument("--classified", default=str(DEFAULT_CLASSIFIED))
    parser.add_argument("--model-07", default=str(DEFAULT_MODEL_07))
    parser.add_argument("--model-08", default=str(DEFAULT_MODEL_08))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--min-conf", type=float, default=DEFAULT_MIN_CONF)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    match = load_json(Path(args.match))
    classified = load_json(Path(args.classified))
    coef07 = load_model(Path(args.model_07))
    coef08 = load_model(Path(args.model_08))
    pool = build_pool(match, classified)
    if not pool:
        raise SystemExit("底图池为空")

    device = None if args.device == "auto" else args.device
    rng = random.Random(args.seed)
    order = pool[:]
    rng.shuffle(order)

    rows: list[dict] = []
    accepted: list[dict] = []
    for item in order:
        detect_one(item, device, coef07, coef08)
        rows.append(item)
        fc, hc = item["face_confidence"], item["head_confidence"]
        ok = (
            fc is not None
            and hc is not None
            and fc >= args.min_conf
            and hc >= args.min_conf
        )
        item["accepted"] = ok
        if ok:
            accepted.append(item)
            if len(accepted) >= args.n:
                break

    write_outputs(
        rows, accepted, out_dir, args.min_conf, device, len(pool), args.n, args.seed
    )


if __name__ == "__main__":
    main()
