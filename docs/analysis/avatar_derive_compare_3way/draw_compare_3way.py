"""三路对比图：match 框 vs avatar_derive(07) vs avatar_derive_08。

从 ``_avatar_match.json`` 中随机抽取 100 张底图（固定随机种子，且同时存在于
07/08 两份推导结果中），在每张图上叠加绘制：

- 绿色：内置头像 match 框（``_avatar_match.json`` 的 ``box``）
- 红色：avatar_derive (min-conf 0.7) 推导框
- 蓝色：avatar_derive_08 (min-conf 0.8) 推导框
- 黄色：人脸检测框（face_pos，仅供参考）

输出到脚本所在目录：

- ``compare/``：100 张标注 PNG
- ``sample.json``：抽样清单（含两个推导框与 IoU）
- ``summary.csv``：每张的 IoU 汇总
- ``README.md``：说明
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DATA = Path(__file__).resolve().parents[1]
MATCH_JSON = DATA / "unpacked" / "_avatar_match.json"
D07_JSON = DATA / "avatar_derive" / "derive_coords.json"
D08_JSON = DATA / "avatar_derive_08" / "derive_coords.json"
DEFAULT_OUT = Path(__file__).resolve().parent
DEFAULT_N = 100
DEFAULT_SEED = 42

COLORS = {
    "match": (0, 200, 0),
    "d07": (255, 60, 60),
    "d08": (0, 170, 255),
    "face": (255, 255, 0),
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_bases(derive: dict):
    for cname, cdata in (derive.get("characters") or {}).items():
        for bname, b in (cdata.get("bases") or {}).items():
            yield cname, bname, b


def load_font(size: int = 30):
    for name in ("arial.ttf", "segoeui.ttf", "msyh.ttc"):
        cand = Path("C:/Windows/Fonts") / name
        if cand.exists():
            try:
                return ImageFont.truetype(str(cand), size)
            except Exception:  # noqa: BLE001, S110 - 字体缺失时回退默认字体
                pass
    return ImageFont.load_default()


def legend_entries(row: dict) -> list[tuple[str, list[int]]]:
    return [
        ("match (built-in)", row["match_box"]),
        (f"derive07(dashed)  iou={row['iou07']:.3f}", row["box07"]),
        (f"derive08(dashed)  iou={row['iou08']:.3f}", row["box08"]),
        ("face", row["face_pos"]),
    ]


def draw_dashed_rect(draw, box, color, width, dash=14, gap=10, phase=0):
    """沿四条边绘制虚线矩形；phase 让多条虚线互相错开。"""
    x1, y1, x2, y2 = map(float, box)
    edges = [
        ((x1, y1), (x2, y1)),  # top
        ((x1, y2), (x2, y2)),  # bottom
        ((x1, y1), (x1, y2)),  # left
        ((x2, y1), (x2, y2)),  # right
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


def draw_image(row: dict, out_png: Path, font: ImageFont.ImageFont) -> None:
    img = Image.open(row["image"]).convert("RGB")
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)

    entries = legend_entries(row)
    dr.rectangle(row["match_box"], outline=COLORS["match"] + (255,), width=6)
    draw_dashed_rect(dr, row["box07"], COLORS["d07"] + (255,), width=5, phase=0)
    draw_dashed_rect(dr, row["box08"], COLORS["d08"] + (255,), width=5, phase=7)
    f = row["face_pos"]
    dr.rectangle(f, outline=COLORS["face"] + (255,), width=2)
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    header_h = 6 + font.size + 10 + len(entries) * (26 + 6) + 6
    canvas = Image.new("RGB", (w, h + header_h), (26, 26, 32))
    canvas.paste(img, (0, header_h))
    dr = ImageDraw.Draw(canvas)
    pad = 10
    title = f"{row['character']} / {row['base']}"
    swatch = 26
    dr.text((pad, 6), title, font=font, fill=(255, 255, 255))
    for i, (key, (label, _)) in enumerate(zip(COLORS, entries)):
        y = 6 + font.size + 10 + i * (swatch + 6)
        dr.rectangle([pad, y, pad + swatch, y + swatch - 8], fill=COLORS[key] + (255,))
        dr.text((pad + swatch + 8, y - 2), label, font=font, fill=(255, 255, 255))

    canvas.save(out_png)


def build_rows(match: dict, d07: dict, d08: dict, n: int, seed: int) -> list[dict]:
    d07_map = {(c, b): rec for c, b, rec in iter_bases(d07)}
    d08_map = {(c, b): rec for c, b, rec in iter_bases(d08)}

    common = []
    for cname, cdata in (match.get("characters") or {}).items():
        for bname, b in (cdata.get("bases") or {}).items():
            key = (cname, bname)
            if key in d07_map and key in d08_map:
                common.append(key)
    if len(common) < n:
        raise SystemExit(f"共同底图只有 {len(common)} 张，不足以抽样 {n} 张")

    rng = random.Random(seed)
    picked = rng.sample(common, n)

    rows = []
    for cname, bname in picked:
        b = match["characters"][cname]["bases"][bname]
        r07 = d07_map[(cname, bname)]
        r08 = d08_map[(cname, bname)]
        rows.append(
            {
                "character": cname,
                "base": bname,
                "image": r07["image"],
                "image_size": r07["image_size"],
                "avatar": r07["avatar"],
                "match_threshold": r07["match_threshold"],
                "match_box": b["box"],
                "face_pos": [
                    r07["face_pos"]["x"],
                    r07["face_pos"]["y"],
                    r07["face_pos"]["x"] + r07["face_pos"]["w"],
                    r07["face_pos"]["y"] + r07["face_pos"]["h"],
                ],
                "box07": r07["derived_box"],
                "box08": r08["derived_box"],
                "iou07": r07["iou"],
                "iou08": r08["iou"],
            }
        )
    return rows


def write_outputs(rows: list[dict], out_dir: Path, n: int, seed: int) -> None:
    vis_dir = out_dir / "compare"
    vis_dir.mkdir(parents=True, exist_ok=True)
    font = load_font(30)

    for row in rows:
        safe = row["base"].replace("$", "_").replace("#", "_")
        if safe.lower().endswith(".png"):
            safe = safe[:-4]
        name = f"{row['character']}__{safe}__iou07_{row['iou07']:.3f}_iou08_{row['iou08']:.3f}.png"
        draw_image(row, vis_dir / name, font)

    sample = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": str(MATCH_JSON),
        "derive_07": str(D07_JSON),
        "derive_08": str(D08_JSON),
        "sample": {"n": len(rows), "seed": seed},
        "rows": rows,
    }
    (out_dir / "sample.json").write_text(
        json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["character", "base", "iou07", "iou08"])
        for row in rows:
            writer.writerow([row["character"], row["base"], row["iou07"], row["iou08"]])

    i07 = [r["iou07"] for r in rows]
    i08 = [r["iou08"] for r in rows]
    stats = {
        "n": len(rows),
        "seed": seed,
        "iou07": {
            "mean": round(sum(i07) / len(i07), 4),
            "median": round(sorted(i07)[len(i07) // 2], 4),
        },
        "iou08": {
            "mean": round(sum(i08) / len(i08), 4),
            "median": round(sorted(i08)[len(i08) // 2], 4),
        },
    }
    (out_dir / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"draw images: {len(rows)} -> {vis_dir}")
    print(f"stats: {json.dumps(stats, ensure_ascii=False)}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--match", default=str(MATCH_JSON), help="_avatar_match.json 路径"
    )
    parser.add_argument(
        "--d07", default=str(D07_JSON), help="avatar_derive(07) derive_coords.json"
    )
    parser.add_argument(
        "--d08", default=str(D08_JSON), help="avatar_derive_08 derive_coords.json"
    )
    parser.add_argument(
        "--out-dir", default=str(DEFAULT_OUT), help="输出目录（默认脚本所在目录）"
    )
    parser.add_argument(
        "--n", type=int, default=DEFAULT_N, help=f"抽样数量（默认 {DEFAULT_N}）"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"随机种子（默认 {DEFAULT_SEED}）",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    match = load_json(Path(args.match))
    d07 = load_json(Path(args.d07))
    d08 = load_json(Path(args.d08))
    rows = build_rows(match, d07, d08, args.n, args.seed)
    write_outputs(rows, out_dir, args.n, args.seed)


if __name__ == "__main__":
    main()
