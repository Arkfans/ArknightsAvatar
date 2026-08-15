"""Incremental-update manifest tool (``arknightsavatar-manifest``).

Generates the developer-facing incremental-update data files:

- per-category content manifests ``{size, sha256}`` per file (work item B);
- the top-level pointer ``data/version.json`` (work item A, ``--version-out``);
- cross-version change lists ``data/stats/changes.json`` + append-only
  ``data/changelog.ndjson`` (work item C, ``--since``);
- the flat per-character CSV ``data/stats/characters.csv`` (work item D-1,
  ``--characters-csv``).

Categories mirror the data repo layout (``data_repo.yaml``): ``recognition``,
``export``, ``export_webp``, ``stats``. Manifests live inside each category
directory (self-excluded from the scan) so they travel with the data repo
automatically. Generation is idempotent: when nothing changed apart from
``generated_at`` the file is not rewritten, so ``sync-cache`` does not produce
empty commits.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path

from arknightsavatar import paths, reporting

# 分类定义：本地扫描目录 / 清单位置 / 数据仓库内路径（version.json 的 path 字段）
CATEGORIES: dict[str, dict] = {
    "recognition": {
        "dir": paths.RECOGNITION_DIR,
        "manifest": paths.RECOGNITION_MANIFEST,
        "remote": "recognition",
        "default_excludes": ("face_detect_vis", "diff_collage", "bases_sample"),
    },
    "export": {
        "dir": paths.EXPORT_DIR,
        "manifest": paths.EXPORT_MANIFEST,
        "remote": "export",
        "default_excludes": (),
    },
    "export_webp": {
        "dir": paths.EXPORT_WEBP_DIR,
        "manifest": paths.EXPORT_WEBP_MANIFEST,
        "remote": "export_webp",
        "default_excludes": (),
    },
    "stats": {
        "dir": paths.STATS_DIR,
        "manifest": paths.STATS_MANIFEST,
        "remote": "stats",
        # run/produce 的每次运行都会重写这两个记录文件（内容必变），
        # 排除后 stats 指纹只在真实数据变化时更新，避免空提交。
        "default_excludes": ("run_stats.json", "produce_stats.json"),
    },
}

# stats 不参与跨版本对比（每次运行都会变化，无对比价值）
COMPARED_CATEGORIES = ("export", "export_webp", "recognition")

CSV_COLUMNS = [
    "npc_id",
    "base_count",
    "diff_count",
    "base_ok",
    "base_skipped",
    "base_dropped",
    "base_no_box",
    "base_failed",
    "diff_ok",
    "diff_skipped",
    "diff_dropped",
    "diff_no_box",
    "diff_failed",
    "diff_special",
    "base_method_match",
    "base_method_derive",
    "has_avatar",
]

STATUSES = ("ok", "skipped", "dropped", "no_box", "failed")

DEFAULT_CHANGES_OUT = str(Path(paths.STATS_DIR) / "changes.json")


def _excluded(rel: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern + "/*"):
            return True
    return False


def scan_category(
    root: str | Path,
    old_files: dict[str, dict],
    *,
    force: bool = False,
    excludes: list[str] | None = None,
) -> dict[str, dict]:
    """Scan a category dir; reuse old sha256 for size-unchanged files (unless force)."""
    excludes = excludes or []
    result: dict[str, dict] = {}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == "manifest.json" or rel.endswith(".tmp"):
            continue  # 清单自排除；临时文件不进清单
        if _excluded(rel, excludes):
            continue
        size = path.stat().st_size
        old = old_files.get(rel)
        if old and old.get("size") == size and not force:
            sha256 = old["sha256"]
        else:
            sha256 = reporting.sha256_file(path)
        result[rel] = {"size": size, "sha256": sha256}
    return result


def load_manifest(path: str | Path) -> dict | None:
    """Load a category manifest; None when missing/broken."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def manifest_files(payload: dict) -> dict[str, dict]:
    files = payload.get("files")
    return files if isinstance(files, dict) else {}


def generate_manifest(
    name: str,
    *,
    force: bool = False,
    extra_excludes: list[str] | None = None,
    output: str | None = None,
    game_version: str | None = None,
) -> dict | None:
    """Scan category ``name`` and write its manifest (idempotent).

    Returns ``{"files": ..., "header": {...}}``; None when the category dir is
    missing (manifest skipped, like sync-cache's missing-category semantics).
    """
    info = CATEGORIES[name]
    root = Path(info["dir"])
    if not root.is_dir():
        print(f"warning: category dir missing, manifest skipped: {info['dir']}", file=sys.stderr)
        return None
    old = load_manifest(info["manifest"])
    old_files = manifest_files(old) if old is not None else {}
    excludes = list(info["default_excludes"]) + (extra_excludes or [])
    files = scan_category(root, old_files, force=force, excludes=excludes)
    generated_at = reporting.now_iso()
    payload = {
        "generated_at": generated_at,
        "category": name,
        "files": files,
    }
    out = output if output is not None else info["manifest"]
    reporting.write_report(payload, out, game_version=game_version, idempotent=True)
    game_version = game_version if game_version is not None else reporting.load_game_version()
    if out != "-":
        print(f"manifest written: {out} ({len(files)} files)")
    return {"files": files, "header": {"game_version": game_version, "generated_at": generated_at}}


def compare_manifests(old: dict[str, dict], new: dict[str, dict]) -> dict:
    """added / removed / modified between two file dicts (by sha256)."""
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    modified = sorted(
        key for key in set(old) & set(new) if old[key].get("sha256") != new[key].get("sha256")
    )
    unchanged = len(set(old) & set(new)) - len(modified)
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
            "unchanged": unchanged,
        },
    }


def load_old_manifests(since: str, category_names: list[str]) -> dict[str, dict | None]:
    """Resolve old manifests from a version.json (by categories.*.path) or a plain manifest."""
    since_path = Path(since)
    try:
        payload = json.loads(since_path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        raise ValueError(f"cannot read --since file: {since}")
    if isinstance(payload, dict) and isinstance(payload.get("categories"), dict):
        old: dict[str, dict | None] = {}
        for name in category_names:
            entry = payload["categories"].get(name)
            if not isinstance(entry, dict) or not entry.get("path"):
                print(f"warning: no {name} manifest path in {since}, skipped", file=sys.stderr)
                continue
            old_path = since_path.parent / entry["path"]
            manifest = load_manifest(old_path)
            if manifest is None:
                print(f"warning: old {name} manifest not found: {old_path}", file=sys.stderr)
                continue
            old[name] = manifest
        return old
    if len(category_names) == 1:
        # 单分类 + 纯旧 manifest：直接使用该文件
        return {category_names[0]: payload}
    raise ValueError("a plain manifest --since requires --category <name>")


def write_changes(
    since: str,
    category_names: list[str],
    generated: dict[str, dict],
    game_version: str,
    changes_out: str,
) -> dict[str, dict] | None:
    """Compare generated manifests against old ones; write changes.json. None = nothing to compare."""
    old = load_old_manifests(since, category_names)
    if not old:
        print("warning: no old manifests found, changes skipped", file=sys.stderr)
        return None
    diffs: dict[str, dict] = {}
    from_header = {"game_version": "unknown", "generated_at": ""}
    for name, old_manifest in old.items():
        new_manifest = generated.get(name)
        if new_manifest is None:
            continue
        if from_header["generated_at"] == "" and isinstance(old_manifest, dict):
            from_header = {
                "game_version": str(old_manifest.get("game_version", "unknown")),
                "generated_at": str(old_manifest.get("generated_at", "")),
            }
        diffs[name] = compare_manifests(
            manifest_files(old_manifest), new_manifest["files"]
        )
    payload = {
        "generated_at": reporting.now_iso(),
        "from": from_header,
        "to": {
            "game_version": game_version,
            "generated_at": reporting.now_iso(),
        },
        "categories": diffs,
    }
    reporting.write_report(payload, changes_out, game_version=game_version, idempotent=True)
    print(f"changes written: {changes_out}")
    return diffs


def append_changelog(path: str | Path, line: dict) -> None:
    """Append one NDJSON line; skip when identical to the current last line."""
    text = json.dumps(line, ensure_ascii=False) + "\n"
    path = Path(path)
    if path.is_file():
        try:
            last = path.read_text(encoding="utf8").rstrip("\n").splitlines()[-1]
            if json.loads(last) == line:
                print(f"changelog unchanged, no append: {path}")
                return
        except (OSError, ValueError):
            pass
    with path.open("a", encoding="utf8") as f:
        f.write(text)
    print(f"changelog appended: {path}")


def _status_counts(items: dict) -> dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for item in items.values():
        status = item.get("status")
        if status in counts:
            counts[status] += 1
    return counts


def _method_counts(items: dict) -> dict[str, int]:
    counts = {"match": 0, "derive": 0}
    for item in items.values():
        if item.get("status") == "ok" and item.get("method") in counts:
            counts[item["method"]] += 1
    return counts


def build_characters_csv(
    extract_report: str | Path = paths.EXTRACT_REPORT,
    classified: str | Path = paths.CLASSIFIED,
) -> str:
    """Per-character flat statistics; rows sorted by npc_id, zero row when missing."""
    extract = load_manifest(extract_report) or {}
    class_payload = load_manifest(classified) or {}
    extract_chars = extract.get("characters") if isinstance(extract, dict) else None
    class_chars = class_payload.get("characters") if isinstance(class_payload, dict) else None
    extract_chars = extract_chars if isinstance(extract_chars, dict) else {}
    class_chars = class_chars if isinstance(class_chars, dict) else {}
    names = sorted(set(extract_chars) | set(class_chars))

    lines = [",".join(CSV_COLUMNS)]
    for name in names:
        char = extract_chars.get(name)
        if not isinstance(char, dict):
            lines.append(",".join([name] + ["0"] * (len(CSV_COLUMNS) - 1)))
            continue
        bases = char.get("bases") if isinstance(char.get("bases"), dict) else {}
        diffs = char.get("diffs") if isinstance(char.get("diffs"), dict) else {}
        base_counts = _status_counts(bases)
        diff_counts = _status_counts(diffs)
        base_method = _method_counts(bases)
        diff_special = sum(1 for item in diffs.values() if item.get("special"))
        has_avatar = any(item.get("avatar_file") for item in {**bases, **diffs}.values())
        row = [
            name,
            len(bases),
            len(diffs),
            base_counts["ok"],
            base_counts["skipped"],
            base_counts["dropped"],
            base_counts["no_box"],
            base_counts["failed"],
            diff_counts["ok"],
            diff_counts["skipped"],
            diff_counts["dropped"],
            diff_counts["no_box"],
            diff_counts["failed"],
            diff_special,
            base_method["match"],
            base_method["derive"],
            1 if has_avatar else 0,
        ]
        lines.append(",".join(str(value) for value in row))
    return "\n".join(lines) + "\n"


def write_characters_csv(path: str | Path) -> None:
    """Write characters.csv plus a sha256 sidecar (for diff verification)."""
    text = build_characters_csv()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf8")
    digest = reporting.sha256_file(path)
    (path.with_name(path.name + ".sha256")).write_text(
        f"{digest}  {path.name}\n", encoding="utf8"
    )
    print(f"characters csv written: {path}")


def write_version_json(
    generated: dict[str, dict],
    game_version: str,
    output: str,
    *,
    no_categories: bool = False,
) -> None:
    """Write data/version.json (idempotent); categories reference the manifests."""
    categories: dict[str, dict] = {}
    if not no_categories:
        for name, result in generated.items():
            if result is None:
                continue
            info = CATEGORIES[name]
            manifest = info["manifest"]
            categories[name] = {
                "path": f"{info['remote']}/manifest.json",
                "sha256": reporting.sha256_file(manifest),
                "files": len(result["files"]),
            }
        npc = Path(paths.NPC_JSON)
        if npc.is_file():
            categories["arknights_npc.json"] = {"sha256": reporting.sha256_file(npc)}
    payload = {"generated_at": reporting.now_iso(), "categories": categories}
    reporting.write_report(payload, output, game_version=game_version, idempotent=True)
    print(f"version written: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-manifest",
        description=(
            "Generate incremental-update data files: category manifests, "
            "data/version.json (--version-out), changes.json + changelog.ndjson "
            "(--since), characters.csv (--characters-csv)."
        ),
    )
    parser.add_argument(
        "--category",
        choices=[*CATEGORIES, "all"],
        default="all",
        help="category to manifest (default: all)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="manifest output path override (single category only; '-' = stdout)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="extra fnmatch exclude pattern (posix relpath); adds to category defaults",
    )
    parser.add_argument("--force", action="store_true", help="re-hash all files")
    parser.add_argument(
        "--version-out",
        nargs="?",
        const=paths.VERSION_JSON,
        default=None,
        metavar="PATH",
        help=f"also generate data/version.json (default: {paths.VERSION_JSON})",
    )
    parser.add_argument(
        "--no-categories",
        action="store_true",
        help="debug: version.json without category fingerprints",
    )
    parser.add_argument(
        "--since",
        default=None,
        metavar="OLD_VERSION_JSON|OLD_MANIFEST",
        help="compare against an older version.json or single-category manifest",
    )
    parser.add_argument(
        "--changes-out",
        default=DEFAULT_CHANGES_OUT,
        help=f"changes.json output (default: {DEFAULT_CHANGES_OUT})",
    )
    parser.add_argument(
        "--append-changelog",
        nargs="?",
        const=paths.CHANGELOG,
        default=None,
        metavar="PATH",
        help=f"append one changelog.ndjson line (default: {paths.CHANGELOG})",
    )
    parser.add_argument(
        "--characters-csv",
        nargs="?",
        const=paths.CHARACTERS_CSV,
        default=None,
        metavar="PATH",
        help=f"write per-character CSV (default: {paths.CHARACTERS_CSV})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.version_out is not None:
            names = list(CATEGORIES)  # version.json 需要全部四分类清单
        elif args.category == "all":
            names = list(CATEGORIES)
        else:
            names = [args.category]
        if args.output is not None and len(names) != 1:
            print("error: --output is only valid with a single --category", file=sys.stderr)
            return 1

        game_version = reporting.load_game_version()
        if args.characters_csv is not None:
            write_characters_csv(args.characters_csv)

        generated: dict[str, dict] = {}
        # 非 stats 分类先生成；changes.json / characters.csv 之后再生 stats（含新产物）
        for name in names:
            if name == "stats":
                continue
            generated[name] = generate_manifest(
                name,
                force=args.force,
                extra_excludes=args.exclude,
                output=args.output if name == args.category else None,
                game_version=game_version,
            )

        if args.since is not None:
            compared = [name for name in COMPARED_CATEGORIES if name in names]
            diffs = write_changes(
                args.since, compared, generated, game_version, args.changes_out
            )
            if diffs is not None and args.append_changelog is not None:
                total = sum(
                    counts["added"] + counts["removed"] + counts["modified"]
                    for diff in diffs.values()
                    for counts in [diff["counts"]]
                )
                if total > 0:
                    append_changelog(
                        args.append_changelog,
                        {
                            "generated_at": reporting.now_iso(),
                            "game_version": game_version,
                            "from_version": _from_version(args.since),
                            "counts": {
                                name: {k: v for k, v in diff["counts"].items() if k != "unchanged"}
                                for name, diff in diffs.items()
                            },
                        },
                    )
                else:
                    print("no changes, changelog not appended")

        if "stats" in names:
            generated["stats"] = generate_manifest(
                "stats",
                force=args.force,
                extra_excludes=args.exclude,
                game_version=game_version,
            )

        if args.version_out is not None:
            write_version_json(
                generated, game_version, args.version_out, no_categories=args.no_categories
            )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _from_version(since: str) -> str:
    """Old game version from the --since manifest/version.json (best effort)."""
    manifest = load_manifest(since)
    if isinstance(manifest, dict):
        return str(manifest.get("game_version", "unknown"))
    return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
