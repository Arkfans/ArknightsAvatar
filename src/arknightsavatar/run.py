"""Full-pipeline orchestration (``arknightsavatar-run``).

Runs the whole production pipeline in-process:

    fetch -> unpack -> classify -> match -> extract -> export-webp -> npc-json

Each step invokes the corresponding tool's ``main(argv)`` with an argv list
composed from the orchestration options, so behavior (incrementality,
validation, reports) stays identical to running the tools one by one.
Steps run in order and the pipeline stops at the first failing step;
per-step exit codes are summarized and written to ``--stats-out``.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path

from arknightsavatar import paths, reporting
from arknightsavatar.config import CATEGORIES

STEPS = ("fetch", "unpack", "classify", "match", "extract", "export-webp", "npc-json")

STEP_MODULES: dict[str, str] = {
    "fetch": "arknightsavatar.fetch",
    "unpack": "arknightsavatar.unpack.unpacker",
    "classify": "arknightsavatar.classify",
    "match": "arknightsavatar.match",
    "extract": "arknightsavatar.extract",
    "export-webp": "arknightsavatar.export_webp",
    "npc-json": "arknightsavatar.npc_json",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-run",
        description=(
            "Run the full avatar pipeline: "
            "fetch → unpack → classify → match → extract → export-webp → npc-json."
        ),
    )
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--source", choices=["apk", "local-apk", "adb"], default="adb")
    parser.add_argument("--category", choices=[*CATEGORIES, "all"], default="all")
    parser.add_argument(
        "--from",
        dest="from_step",
        choices=STEPS,
        default="fetch",
        help="first step to run (default: fetch)",
    )
    parser.add_argument(
        "--until",
        dest="until_step",
        choices=STEPS,
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
        help="pass --force to fetch/unpack/match/extract/export-webp",
    )
    parser.add_argument(
        "--no-batch",
        action="store_true",
        help="pass --no-batch to fetch (pull AB files one by one)",
    )
    parser.add_argument(
        "--compress",
        action="store_true",
        help="pass --compress to fetch (gzip the device-side archive)",
    )
    parser.add_argument("--raw-dir", default=paths.RAW_DIR)
    parser.add_argument("--unpacked-dir", default=paths.UNPACKED_DIR)
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
        default=str(Path(paths.STATS_DIR) / "run_stats.json"),
        help=f"pipeline stats summary JSON (default: {Path(paths.STATS_DIR) / 'run_stats.json'})",
    )
    return parser


def step_argv(name: str, args: argparse.Namespace) -> list[str]:
    """Compose the argv list handed to the tool ``name``'s ``main()``."""
    if name == "fetch":
        argv = ["--source", args.source, "--category", args.category, "--raw-dir", args.raw_dir]
        if args.config:
            argv += ["--config", args.config]
        if args.force:
            argv += ["--force"]
        if args.no_batch:
            argv += ["--no-batch"]
        if args.compress:
            argv += ["--compress"]
        return argv
    if name == "unpack":
        argv = [
            "--raw-dir",
            args.raw_dir,
            "--unpacked-dir",
            args.unpacked_dir,
            "--category",
            args.category,
        ]
        if args.config:
            argv += ["--config", args.config]
        if args.force:
            argv += ["--force"]
        return argv
    if name == "classify":
        return ["--characters-dir", args.characters_dir, "--output", args.classified]
    if name == "match":
        return [
            "--classified",
            args.classified,
            "--characters-dir",
            args.characters_dir,
            "--avatars-dir",
            args.avatars_dir,
            "--output",
            args.match_report,
            "--limit",
            str(args.limit),
            *(["--force"] if args.force else []),
        ]
    if name == "extract":
        return [
            "--classified",
            args.classified,
            "--characters-dir",
            args.characters_dir,
            "--match",
            args.match_report,
            "--avatars-dir",
            args.avatars_dir,
            "--derive-model",
            args.derive_model,
            "--output-dir",
            args.export_dir,
            "--output",
            args.extract_report,
            "--device",
            args.device,
            "--limit",
            str(args.limit),
            *(["--force"] if args.force else []),
        ]
    if name == "export-webp":
        return [
            "--export-dir",
            args.export_dir,
            "-o",
            args.export_webp_dir,
            "--classified",
            args.classified,
            "--limit",
            str(args.limit),
            *(["--force"] if args.force else []),
        ]
    if name == "npc-json":
        return [
            "--export-dir",
            args.export_dir,
            "-o",
            args.npc_json_out,
            "--classified",
            args.classified,
        ]
    raise ValueError(f"unknown step: {name}")


def run_step(name: str, argv: list[str], modules: dict[str, str] | None = None) -> int:
    """Import the tool module for ``name`` and run ``main(argv)``."""
    table = modules if modules is not None else STEP_MODULES
    module = importlib.import_module(table[name])
    return int(module.main(argv))


def check_derive_model(path: str | Path) -> bool:
    """Pre-check the derive model used by extract (3rd tier crop boxes)."""
    if Path(path).is_file():
        return True
    print(f"error: derive model not found: {path}", file=sys.stderr)
    print(
        "hint: 有识别报告时运行 `arknightsavatar derive-model` 重新拟合；",
        file=sys.stderr,
    )
    print(
        "      或运行 `arknightsavatar sync-cache --pull --restore` 从数据仓库取回。",
        file=sys.stderr,
    )
    return False


def run_steps(
    args: argparse.Namespace,
    run_step_func=None,
    on_step=None,
) -> dict[str, int]:
    """Run the steps between ``--from`` and ``--until`` (inclusive).

    Stops at the first non-zero exit code. ``on_step(name, code)`` is invoked
    after every executed step (used for progress display and tests).
    """
    runner = run_step_func if run_step_func is not None else run_step
    start = STEPS.index(args.from_step)
    end = STEPS.index(args.until_step) + 1
    results: dict[str, int] = {}
    for name in STEPS[start:end]:
        print(f"[arknightsavatar-run] step: {name}", flush=True)
        code = runner(name, step_argv(name, args))
        results[name] = code
        if on_step is not None:
            on_step(name, code)
        if code != 0:
            print(f"error: step '{name}' failed with exit code {code}", file=sys.stderr)
            break
    return results


def write_stats(path: str | Path, payload: dict) -> None:
    """Write a pipeline stats JSON with the shared version header (used by run and produce)."""
    reporting.write_report(payload, path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if STEPS.index(args.from_step) > STEPS.index(args.until_step):
        print("error: --from must not be after --until", file=sys.stderr)
        return 1

    if "extract" in STEPS[STEPS.index(args.from_step) : STEPS.index(args.until_step) + 1] and not check_derive_model(
        args.derive_model
    ):
        return 1

    started = _now()
    results = run_steps(args)
    expected = STEPS[STEPS.index(args.from_step) : STEPS.index(args.until_step) + 1]
    payload = {
        "generated_at": _now(),
        "started_at": started,
        "steps": results,
        "ok": len(results) == len(expected) and all(code == 0 for code in results.values()),
    }
    write_stats(args.stats_out, payload)
    print(f"pipeline stats written: {args.stats_out}")

    failed = [name for name, code in results.items() if code != 0]
    if failed:
        print(f"error: pipeline failed at step(s): {', '.join(failed)}", file=sys.stderr)
        return 1
    print("pipeline complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
