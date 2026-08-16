"""From-zero derivation-model orchestration (``arknightsavatar-build-model``).

Builds ``data/recognition/derive/model.json`` from scratch, including pulling
the game resources from the device / local APK:

    fetch -> unpack -> classify -> match -> detect-bases -> derive-model

The first four steps reuse the argv composition of ``arknightsavatar.run``
(behavior identical to running the tools one by one); the last two are
composed here. This is the missing "cold start" entry: ``run`` requires the
derive model to already exist (extract's 3rd tier), while this command
produces it.

Dependencies: the detect stack (torch / anime-face-detector / dghs-imgutils /
opencv / numpy) is pre-checked before any step runs when ``detect-bases`` is
within the selected step range, so a missing stack fails fast instead of after
the resource pull.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from arknightsavatar import detect, detect_bases, paths
from arknightsavatar import run as run_module

BUILD_MODEL_STEPS = ("fetch", "unpack", "classify", "match", "detect-bases", "derive-model")

STEP_MODULES: dict[str, str] = {
    "fetch": "arknightsavatar.fetch",
    "unpack": "arknightsavatar.unpack.unpacker",
    "classify": "arknightsavatar.classify",
    "match": "arknightsavatar.match",
    "detect-bases": "arknightsavatar.detect_bases",
    "derive-model": "arknightsavatar.derive_model",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-build-model",
        description=(
            "Build the face/head -> avatar crop-box derivation model from "
            "scratch: fetch -> unpack -> classify -> match -> detect-bases -> "
            "derive-model (writes data/recognition/derive/model.json)."
        ),
    )
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--source", choices=["apk", "local-apk", "adb"], default="adb")
    parser.add_argument("--category", choices=["characters", "avatars", "all"], default="all")
    parser.add_argument(
        "--from",
        dest="from_step",
        choices=BUILD_MODEL_STEPS,
        default="fetch",
        help="first step to run (default: fetch)",
    )
    parser.add_argument(
        "--until",
        dest="until_step",
        choices=BUILD_MODEL_STEPS,
        default="derive-model",
        help="last step to run (default: derive-model)",
    )
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="only process the first N characters/bases in match/detect-bases (0 = all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="pass --force to fetch/unpack/match",
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
    parser.add_argument(
        "--detect-threshold",
        type=float,
        default=detect_bases.DEFAULT_THRESHOLD,
        help=(
            "only run detection on bases with match threshold strictly greater "
            f"than this (default: {detect_bases.DEFAULT_THRESHOLD})"
        ),
    )
    parser.add_argument(
        "--detect-conf",
        type=float,
        default=detect.DEFAULT_CONF,
        help=(
            "minimum confidence for the face detector; lower results count as "
            f"not detected (default: {detect.DEFAULT_CONF})"
        ),
    )
    parser.add_argument(
        "--head-conf",
        type=float,
        default=detect_bases.DEFAULT_HEAD_CONF,
        help=(
            "minimum confidence for the imgutils head detector; lower results "
            f"are dropped (default: {detect_bases.DEFAULT_HEAD_CONF})"
        ),
    )
    parser.add_argument(
        "--min-conf",
        type=float,
        default=0.8,
        help="derive-model face/head confidence lower bound, strictly greater (default: 0.8)",
    )
    parser.add_argument(
        "--no-compare",
        action="store_true",
        help="pass --no-compare to derive-model (skip sampled compare images)",
    )
    parser.add_argument(
        "--no-vis",
        action="store_true",
        help="pass --no-vis to detect-bases (skip annotated PNG rendering; default)",
    )
    parser.add_argument(
        "--vis-dir",
        default=None,
        help=(
            f"directory for annotated PNGs; when given, rendering is enabled "
            f"(default: skipped; tool default: {paths.FACE_DETECT_VIS_DIR})"
        ),
    )
    parser.add_argument("--raw-dir", default=paths.RAW_DIR)
    parser.add_argument("--unpacked-dir", default=paths.UNPACKED_DIR)
    parser.add_argument("--characters-dir", default=paths.UNPACKED_CHARACTERS_DIR)
    parser.add_argument("--avatars-dir", default=paths.UNPACKED_AVATARS_DIR)
    parser.add_argument("--classified", default=paths.CLASSIFIED)
    parser.add_argument("--match", dest="match_report", default=paths.AVATAR_MATCH)
    parser.add_argument("--face-detect", dest="face_detect", default=paths.FACE_DETECT_MATCHED)
    parser.add_argument("--derive-dir", default=paths.DERIVE_DIR)
    parser.add_argument(
        "--stats-out",
        default=str(Path(paths.STATS_DIR) / "build_model_stats.json"),
        help="pipeline stats summary JSON (default: data/stats/build_model_stats.json)",
    )
    return parser


def step_argv(name: str, args: argparse.Namespace) -> list[str]:
    """Compose the argv list handed to the tool ``name``'s ``main()``."""
    if name in ("fetch", "unpack", "classify", "match"):
        return run_module.step_argv(name, args)
    if name == "detect-bases":
        argv = [
            "--match",
            args.match_report,
            "--characters-dir",
            args.characters_dir,
            "--threshold",
            str(args.detect_threshold),
            "--conf",
            str(args.detect_conf),
            "--head-conf",
            str(args.head_conf),
            "--device",
            args.device,
            "--limit",
            str(args.limit),
            "--output",
            args.face_detect,
        ]
        if args.vis_dir is not None:
            argv += ["--vis-dir", args.vis_dir]
        else:
            argv += ["--no-vis"]
        return argv
    if name == "derive-model":
        argv = [
            # 相对路径按 derive-model 的 CWD 解析；这里直接绝对化避免歧义
            "--source",
            str(Path(args.face_detect).resolve()),
            "--out-dir",
            args.derive_dir,
            "--min-conf",
            str(args.min_conf),
        ]
        if args.no_compare:
            argv += ["--no-compare"]
        return argv
    raise ValueError(f"unknown step: {name}")


def check_detect_deps() -> bool:
    """Pre-check the detect stack used by detect-bases (fail fast before the pull).

    Same diagnostics as the standalone tool, so a missing ``uv sync --extra
    detect`` (or ``--extra detect-gpu``) stack is reported before any step runs.
    """
    if detect_bases.cv2 is None or detect_bases.np is None:
        print(
            "error: opencv-python-headless and numpy are required (uv sync --extra detect)",
            file=sys.stderr,
        )
        return False
    if not detect._check_ml_deps():
        print(
            "error: torch and anime-face-detector are required (uv sync --extra detect)",
            file=sys.stderr,
        )
        return False
    if not detect_bases._check_head_deps():
        print(
            "error: dghs-imgutils is required (uv sync --extra detect)",
            file=sys.stderr,
        )
        return False
    return True


def run_steps(
    args: argparse.Namespace,
    run_step_func=None,
    on_step=None,
) -> dict[str, int]:
    """Run the steps between ``--from`` and ``--until`` (inclusive).

    Stops at the first non-zero exit code. ``on_step(name, code)`` is invoked
    after every executed step (used for progress display and tests).
    """
    runner = run_step_func if run_step_func is not None else run_module.run_step
    start = BUILD_MODEL_STEPS.index(args.from_step)
    end = BUILD_MODEL_STEPS.index(args.until_step) + 1
    results: dict[str, int] = {}
    for name in BUILD_MODEL_STEPS[start:end]:
        print(f"[arknightsavatar-build-model] step: {name}", flush=True)
        code = runner(name, step_argv(name, args))
        results[name] = code
        if on_step is not None:
            on_step(name, code)
        if code != 0:
            print(f"error: step '{name}' failed with exit code {code}", file=sys.stderr)
            break
    return results


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if BUILD_MODEL_STEPS.index(args.from_step) > BUILD_MODEL_STEPS.index(args.until_step):
        print("error: --from must not be after --until", file=sys.stderr)
        return 1

    selected = BUILD_MODEL_STEPS[
        BUILD_MODEL_STEPS.index(args.from_step) : BUILD_MODEL_STEPS.index(args.until_step) + 1
    ]
    if "detect-bases" in selected and not check_detect_deps():
        return 1

    started = run_module._now()
    results = run_steps(args)
    expected = BUILD_MODEL_STEPS[
        BUILD_MODEL_STEPS.index(args.from_step) : BUILD_MODEL_STEPS.index(args.until_step) + 1
    ]
    payload = {
        "generated_at": run_module._now(),
        "started_at": started,
        "steps": results,
        "ok": len(results) == len(expected) and all(code == 0 for code in results.values()),
    }
    run_module.write_stats(args.stats_out, payload)
    print(f"build-model stats written: {args.stats_out}")

    failed = [name for name, code in results.items() if code != 0]
    if failed:
        print(f"error: build-model failed at step(s): {', '.join(failed)}", file=sys.stderr)
        return 1
    print("build-model complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
