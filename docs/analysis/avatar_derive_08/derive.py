"""由 face/head 检测框推导内置头像裁切框（独立工具，不接入主程序）。

输入：``_face_detect_matched.json``（含 face_pos / head_pos / box 的匹配底图；
      face_pos / head_pos 的 x, y 为检测框左上角，w, h 为宽高）
筛选：face_confidence > min_conf 且 head_confidence > min_conf
      （--min-conf 可调，默认 0.7）
方法：对每个有效底图，用 face+head 的 8 维特征线性拟合得到正方形裁切框的
      中心 (cx, cy) 与边长 s，即 derived_box = [cx-s/2, cy-s/2, cx+s/2, cy+s/2]。
      模型特征使用检测框中心（由左上角 + 宽高换算：x + w/2, y + h/2）。
输出：model.json（拟合系数）、derive_coords.json（坐标）、stats.json（精度）、
      compare/（抽样可视化）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

DEFAULT_SOURCE = "../unpacked/_face_detect_matched.json"
DEFAULT_OUT_DIR = str(Path(__file__).resolve().parent)
DEFAULT_MIN_CONF = 0.8
FEATURES = ["fx", "fy", "fw", "fh", "hx", "hy", "hw", "hh"]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_valid_rows(source: Path, min_conf: float = DEFAULT_MIN_CONF):
    """读取报告并返回 (char, base, entry) 的有效条目列表（置信度均 > min_conf）。"""
    report = json.loads(source.read_text(encoding="utf-8"))
    rows = []
    for cname, cdata in (report.get("characters") or {}).items():
        for bname, b in (cdata.get("bases") or {}).items():
            if not b.get("detected") or not b.get("head_detected"):
                continue
            fc = b.get("confidence")
            hc = b.get("head_confidence")
            if fc is None or hc is None or fc <= min_conf or hc <= min_conf:
                continue
            rows.append((cname, bname, b))
    return report, rows


def feature_matrix(rows):
    """构造特征矩阵；输入 x/y 为左上角，统一换算为中心点参与拟合。"""
    X = []
    for _, _, b in rows:
        f, h = b["face_pos"], b["head_pos"]
        X.append(
            [
                f["x"] + f["w"] / 2.0,
                f["y"] + f["h"] / 2.0,
                f["w"],
                f["h"],
                h["x"] + h["w"] / 2.0,
                h["y"] + h["h"] / 2.0,
                h["w"],
                h["h"],
            ]
        )
    return np.asarray(X, dtype=float)


def target_center_size(rows):
    Y = []
    for _, _, b in rows:
        x1, y1, x2, y2 = b["box"]
        Y.append([(x1 + x2) / 2.0, (y1 + y2) / 2.0, x2 - x1])
    return np.asarray(Y, dtype=float)


def fit_model(X, Y):
    A = np.column_stack([X, np.ones(len(X))])
    coef, *_ = np.linalg.lstsq(A, Y, rcond=None)
    pred = A @ coef
    resid = Y - pred
    r2 = []
    for j, name in enumerate(["cx", "cy", "s"]):
        ss_tot = float(((Y[:, j] - Y[:, j].mean()) ** 2).sum())
        ss_res = float((resid[:, j] ** 2).sum())
        r2.append(round(1 - ss_res / ss_tot, 6))
    return coef, r2


def predict_boxes(coef, X):
    A = np.column_stack([X, np.ones(len(X))])
    c = A @ coef  # [cx, cy, s]
    return c, np.column_stack(
        [
            c[:, 0] - c[:, 2] / 2,
            c[:, 1] - c[:, 2] / 2,
            c[:, 0] + c[:, 2] / 2,
            c[:, 1] + c[:, 2] / 2,
        ]
    )


def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def norm_box(box, size):
    w, h = size
    return [
        round(box[0] / w, 6),
        round(box[1] / h, 6),
        round(box[2] / w, 6),
        round(box[3] / h, 6),
    ]


def build_outputs(
    report, rows, coef, out_dir: Path, min_conf: float = DEFAULT_MIN_CONF
):
    X = feature_matrix(rows)
    centers, boxes = predict_boxes(coef, X)
    ious = [iou(b["box"], boxes[i]) for i, (_, _, b) in enumerate(rows)]

    characters: dict = {}
    for i, (cname, bname, b) in enumerate(rows):
        match_box = b["box"]
        db = [round(v, 2) for v in boxes[i]]
        db_int = [round(v) for v in boxes[i]]
        cx, cy, s = (
            round(centers[i, 0], 2),
            round(centers[i, 1], 2),
            round(centers[i, 2], 2),
        )
        characters.setdefault(cname, {"bases": {}})
        characters[cname]["bases"][bname] = {
            "image": b["image"],
            "image_size": list(b["image_size"]),
            "face_pos": b["face_pos"],
            "face_confidence": round(b["confidence"], 6),
            "head_pos": b["head_pos"],
            "head_confidence": round(b["head_confidence"], 6),
            "avatar": b["avatar"],
            "match_threshold": round(b["threshold"], 6),
            "match_box": match_box,
            "match_box_norm": norm_box(match_box, b["image_size"]),
            "derived_box": db_int,
            "derived_box_exact": db,
            "derived_box_norm": norm_box(db, b["image_size"]),
            "derived_center": [cx, cy],
            "derived_size": s,
            "iou": round(ious[i], 4),
        }

    ious_arr = np.array(ious)
    pc = (boxes[:, 0] + boxes[:, 2]) / 2
    py = (boxes[:, 1] + boxes[:, 3]) / 2
    mc = (np.array([b["box"] for _, _, b in rows])[:, [0, 2]].sum(axis=1)) / 2
    my = (np.array([b["box"] for _, _, b in rows])[:, [1, 3]].sum(axis=1)) / 2
    center_err = np.hypot(pc - mc, py - my)
    msize = boxes[:, 2] - boxes[:, 0]
    tsize = np.array([b["box"][2] - b["box"][0] for _, _, b in rows])
    size_ratio = msize / tsize

    stats = {
        "n": len(rows),
        "filter": {"confidence_min": min_conf},
        "iou": {
            "mean": round(float(ious_arr.mean()), 4),
            "median": round(float(np.median(ious_arr)), 4),
            "p10": round(float(np.percentile(ious_arr, 10)), 4),
            "p90": round(float(np.percentile(ious_arr, 90)), 4),
            "min": round(float(ious_arr.min()), 4),
            "lt_0.80": round(float((ious_arr < 0.80).mean()), 4),
            "ge_0.90": round(float((ious_arr >= 0.90).mean()), 4),
        },
        "center_error_px": {
            "mean": round(float(center_err.mean()), 2),
            "median": round(float(np.median(center_err)), 2),
        },
        "size_ratio": {
            "mean": round(float(size_ratio.mean()), 4),
            "median": round(float(np.median(size_ratio)), 4),
        },
    }
    return characters, stats


def write_compare_images(rows, centers, boxes, out_dir: Path, n: int = 24):
    """按 IoU 分位抽样，输出 match(绿) vs derived(红) 叠加图。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("PIL 不可用，跳过 compare/ 可视化", file=sys.stderr)
        return
    ious = [iou(b["box"], boxes[i]) for i, (_, _, b) in enumerate(rows)]
    order = np.argsort(ious)
    n = min(n, len(order))
    picks = np.unique(np.linspace(0, len(order) - 1, n).astype(int))
    vis_dir = out_dir / "compare"
    vis_dir.mkdir(parents=True, exist_ok=True)
    for rank in picks:
        i = int(order[rank])
        cname, bname, b = rows[i]
        try:
            img = Image.open(b["image"]).convert("RGB")
        except Exception:  # noqa: BLE001, S112 - 跳过无法读取的图片
            continue
        dr = ImageDraw.Draw(img)
        dr.rectangle(b["box"], outline=(0, 200, 0), width=4)
        db = [float(v) for v in boxes[i]]
        dr.rectangle(db, outline=(255, 0, 0), width=4)
        f = b["face_pos"]
        dr.rectangle(
            [f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"]],
            outline=(255, 255, 0),
            width=2,
        )
        label = f"{cname}/{bname} iou={ious[i]:.3f}"
        dr.text((8, 8), label, fill=(255, 255, 255))
        safe = bname.replace("$", "_").replace("#", "_")
        img.save(vis_dir / f"{cname}__{safe}__iou_{ious[i]:.3f}.png")
    print(f"compare images: {len(picks)} -> {vis_dir}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", default=DEFAULT_SOURCE, help="输入 _face_detect_matched.json"
    )
    parser.add_argument(
        "--out-dir", default=DEFAULT_OUT_DIR, help="输出目录（默认脚本所在目录）"
    )
    parser.add_argument(
        "--min-conf",
        type=float,
        default=DEFAULT_MIN_CONF,
        help=f"face/head 置信度下限（严格大于，默认 {DEFAULT_MIN_CONF}）",
    )
    parser.add_argument("--no-compare", action="store_true", help="跳过抽样可视化")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    source = Path(args.source)
    if not source.is_absolute():
        source = (out_dir / source).resolve()

    min_conf = args.min_conf
    report, rows = load_valid_rows(source, min_conf)
    print(f"有效条目（face>{min_conf} & head>{min_conf}）: {len(rows)}")
    if not rows:
        raise SystemExit("没有有效条目，退出")

    X = feature_matrix(rows)
    Y = target_center_size(rows)
    coef, r2 = fit_model(X, Y)
    centers, boxes = predict_boxes(coef, X)

    model = {
        "generated_at": now_iso(),
        "fit_samples": len(rows),
        "min_conf": min_conf,
        "feature_order": FEATURES,
        "target_order": ["cx", "cy", "s"],
        "formula": "derived_box = [cx - s/2, cy - s/2, cx + s/2, cy + s/2]; "
        "[cx, cy, s] = coef @ [fx, fy, fw, fh, hx, hy, hw, hh, 1]",
        "r2": dict(zip(["cx", "cy", "s"], r2)),
        "coef": [[round(v, 6) for v in row] for row in coef],
    }
    (out_dir / "model.json").write_text(
        json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    characters, stats = build_outputs(report, rows, coef, out_dir, min_conf)
    payload = {
        "generated_at": now_iso(),
        "source": str(source),
        "filter": {"confidence_min": min_conf},
        "model_file": "model.json",
        "stats": stats,
        "characters": characters,
    }
    (out_dir / "derive_coords.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not args.no_compare:
        write_compare_images(rows, centers, boxes, out_dir)
    print(f"输出目录: {out_dir}")
    print(f"stats: {json.dumps(stats['iou'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
