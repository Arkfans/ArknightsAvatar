"""Offline production orchestration (``arknightsavatar-produce``).

Turns local data into the final avatar assets without touching the device:

    classify -> match -> extract -> export-webp -> npc-json

Shares argv composition and step execution with ``arknightsavatar.run``.
The derive model (``data/recognition/derive/model.json``) is pre-checked
because extract depends on it for the 3rd-tier crop boxes.
"""

from __future__ import annotations

import argparse
import sys

from arknightsavatar import paths
from arknightsavatar.run import (
    STEPS,
    _now,
    check_derive_model,
    run_steps,
    write_stats,
)

PRODUCE_STEPS = STEPS[STEPS.index("classify") :]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-produce",
        description=(
            "Produce final avatar assets from local data: "
            "classify → match → extract → export-webp → npc-json."
        ),
    )
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument(
        "--from",
        dest="from_step",
        choices=PRODUCE_STEPS,
        default="classify",
        help="first step to run (default: classify)",
    )
    parser.add_argument(
        "--until",
        dest="until_step",
        choices=PRODUCE_STEPS,
        default="npc-json",
        help="last step to run (default: npc-json)",
    )
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="only process the first N characters in match/extract/export-webp (0 = all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="pass --force to match/extract/export-webp",
    )
    parser.add_argument("--characters-dir", default=paths.UNPACKED_CHARACTERS_DIR)
    parser.add_argument("--avatars-dir", default=paths.UNPACKED_AVATARS_DIR)
    parser.add_argument("--classified", default=paths.CLASSIFIED)
    parser.add_argument("--match", dest="match_report", default=paths.AVATAR_MATCH)
    parser.add_argument("--derive-model", default=paths.DERIVE_MODEL)
    parser.add_argument("--extract-report", default=paths.EXTRACT_REPORT)
    parser.add_argument("--export-dir", default=paths.EXPORT_DIR)
    parser.add_argument("--export-webp-dir", default=paths.EXPORT_WEBP_DIR)
    parser.add_argument("--npc-json", dest="npc_json_out", default=paths.NPC_JSON)
    parser.add_argument(
        "--stats-out",
        default=str(paths.STATS_DIR + "/produce_stats.json"),
        help="stats summary JSON (default: data/stats/produce_stats.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if STEPS.index(args.from_step) > STEPS.index(args.until_step):
        print("error: --from must not be after --until", file=sys.stderr)
        return 1
    if not check_derive_model(args.derive_model):
        return 1

    started = _now()
    results = run_steps(args)
    expected = STEPS[STEPS.index(args.from_step) : STEPS.index(args.until_step) + 1]
    payload = {
        "generated_at": _now(),
        "started_at": started,
        "steps": results,
        "ok": len(results) == len(expected)
        and all(code == 0 for code in results.values()),
    }
    write_stats(args.stats_out, payload)
    print(f"produce stats written: {args.stats_out}")

    failed = [name for name, code in results.items() if code != 0]
    if failed:
        print(f"error: produce failed at step(s): {', '.join(failed)}", file=sys.stderr)
        return 1
    print("produce complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
