"""Device-side resource acquisition (``arknightsavatar-pull``).

Orchestrates the two device-facing tools:

    fetch (pull-apk only when --with-apk is given)

Fetch defaults to the ``adb`` + ``apk`` sources together: the device's live
Bundles directory (characters/L2D) and the installed APK (spritepack) — only
both sources together are the complete dataset. Both steps reuse the single
tools' ``main(argv)``; the pipeline stops at the first failing step.
"""

from __future__ import annotations

import argparse
import importlib
import sys

from arknightsavatar import paths
from arknightsavatar.config import CATEGORIES

PULL_STEP_MODULES: dict[str, str] = {
    "pull-apk": "arknightsavatar.pull_apk",
    "fetch": "arknightsavatar.fetch",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-pull",
        description=(
            "Acquire game resources from the device: fetch "
            "(pull-apk only with --with-apk; default sources: adb + apk)."
        ),
    )
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument(
        "--with-apk",
        action="store_true",
        help="also pull the installed APK from the device (pull-apk step; default: skip)",
    )
    parser.add_argument("--package", help="Android package name for pull-apk")
    parser.add_argument("--out", default="apk", help="pull-apk output directory (default: apk)")
    parser.add_argument(
        "--source",
        choices=["apk", "local-apk", "adb"],
        nargs="+",
        default=["adb", "apk"],
        help="resource source(s); default: adb + apk (live device Bundles wins, "
        "installed APK fills gaps — together they are the complete dataset)",
    )
    parser.add_argument("--category", choices=[*CATEGORIES, "all"], default="all")
    parser.add_argument("--raw-dir", default=paths.RAW_DIR)
    parser.add_argument("--force", action="store_true", help="pass --force to fetch")
    return parser


def _pull_apk_argv(args: argparse.Namespace) -> list[str]:
    argv = ["--out", args.out]
    if args.config:
        argv += ["--config", args.config]
    if args.package:
        argv += ["--package", args.package]
    return argv


def _fetch_argv(args: argparse.Namespace) -> list[str]:
    argv = ["--source", *args.source, "--category", args.category, "--raw-dir", args.raw_dir]
    if args.config:
        argv += ["--config", args.config]
    if args.force:
        argv += ["--force"]
    return argv


def _run_step(name: str, argv: list[str]) -> int:
    module = importlib.import_module(PULL_STEP_MODULES[name])
    return int(module.main(argv))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    steps: list[tuple[str, list[str]]] = []
    if args.with_apk:
        steps.append(("pull-apk", _pull_apk_argv(args)))
    steps.append(("fetch", _fetch_argv(args)))

    for name, step_argv in steps:
        print(f"[arknightsavatar-pull] step: {name}", flush=True)
        code = _run_step(name, step_argv)
        if code != 0:
            print(f"error: step '{name}' failed with exit code {code}", file=sys.stderr)
            return 1
    print("pull complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
