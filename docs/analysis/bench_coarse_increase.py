"""Benchmark COARSE_INCREASE 1..5 on arknightsavatar-match --limit 50.

Each value runs the match pipeline once (deterministic workload), records wall
time, and counts template-match evaluations (sum of --detail offsets). Reports
are saved per value so accuracy across values can be compared.

c=1 is not accepted by the CLI (--coarse-increase must be >= 2), so it is run
through the library API directly with the same defaults.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from arknightsavatar.match import match_characters


def run_one_direct(coarse_increase: int, limit: int, out_dir: Path) -> dict:
    """c=1 fallback: call match_characters directly, bypassing CLI validation."""
    classified_path = Path("data/unpacked/_characters_classified.json")
    out_path = out_dir / f"_avatar_match_c{coarse_increase}.json"
    classified = json.loads(classified_path.read_text(encoding="utf8"))
    start = time.perf_counter()
    report = match_characters(
        classified,
        Path("data/unpacked/characters"),
        Path("data/unpacked/avatars"),
        classified_path=classified_path,
        limit=limit,
        coarse_increase=coarse_increase,
        detail=True,
    )
    elapsed = time.perf_counter() - start
    payload = report.as_dict()
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf8"
    )
    evaluations = sum(
        len(records)
        for char in payload["characters"].values()
        for base in char["bases"].values()
        for records in (base.get("offsets") or {}).values()
    )
    return {
        "coarse_increase": coarse_increase,
        "elapsed_s": elapsed,
        "evaluations": evaluations,
        "stats": payload["stats"],
        "report": str(out_path),
        "mode": "api",
    }


def run_one(coarse_increase: int, limit: int, out_dir: Path) -> dict:
    out_path = out_dir / f"_avatar_match_c{coarse_increase}.json"
    start = time.perf_counter()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "arknightsavatar.match",
            "--limit",
            str(limit),
            "--coarse-increase",
            str(coarse_increase),
            "--detail",
            "--output",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=False,  # 调用方检查 returncode
    )
    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        return {
            "coarse_increase": coarse_increase,
            "error": f"exit {result.returncode}: {result.stderr.strip()}",
            "elapsed_s": elapsed,
            "evaluations": 0,
        }
    try:
        report = json.loads(out_path.read_text(encoding="utf8"))
    except Exception as exc:  # noqa: BLE001
        return {
            "coarse_increase": coarse_increase,
            "error": f"cannot read report: {exc}",
            "elapsed_s": elapsed,
            "evaluations": 0,
        }
    evaluations = 0
    for char in report["characters"].values():
        for base in char["bases"].values():
            for records in (base.get("offsets") or {}).values():
                evaluations += len(records)
    return {
        "coarse_increase": coarse_increase,
        "elapsed_s": elapsed,
        "evaluations": evaluations,
        "stats": report["stats"],
        "report": str(out_path),
        "mode": "cli",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--values", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/bench_coarse_increase"),
    )
    parser.add_argument(
        "--serial",
        action="store_true",
        help="run values one at a time instead of in parallel",
    )
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.serial:
        results = []
        for value in args.values:
            if value == 1:
                results.append(run_one_direct(value, args.limit, args.out_dir))
            else:
                results.append(run_one(value, args.limit, args.out_dir))
    else:
        with ThreadPoolExecutor(max_workers=len(args.values)) as pool:
            results = list(
                pool.map(lambda v: run_one(v, args.limit, args.out_dir), args.values)
            )

    rows = []
    for row in results:
        if "error" in row:
            print(f"c={row['coarse_increase']}: ERROR {row['error']}", file=sys.stderr)
            rows.append(row)
            continue
        rows.append(row)
        evals = row["evaluations"]
        print(
            f"c={row['coarse_increase']} [{row.get('mode', '?')}]: {row['elapsed_s']:.2f}s  "
            f"evals={evals}  ok={row['stats']['ok']} "
            f"matched_bases={row['stats']['matched_bases']} "
            f"low_confidence={row['stats']['low_confidence']}"
        )

    summary = args.out_dir / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "command": f"uv run arknightsavatar-match --limit {args.limit}",
                "runs": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf8",
    )
    print(f"summary: {summary}")
    return 0 if all("error" not in row for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
