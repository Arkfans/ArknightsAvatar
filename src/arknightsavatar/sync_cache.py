"""Sync data categories into the GitHub data repo (``arknightsavatar-sync-cache``).

The four shared data families -- recognition data (``data/recognition``),
raw avatars (``data/unpacked/avatars``), extracted avatars (``data/export``,
``data/export_webp``) and statistics lists (``data/stats``,
``data/arknights_npc.json``) -- are carried by a separate GitHub data repo.

This tool mirrors those local paths into a local git working copy of the data
repo (``data_repo.yaml: path``, default ``data_cache``) and commits
incrementally through the git CLI: a commit is only created when something
changed, so repeated runs are cheap. Create the GitHub repo first, then fill
``data_repo.yaml: url`` and run -- the tool is ready as-is.

Options:
- ``--pull``    update the working copy from the remote before mirroring;
- ``--restore`` copy files that exist in the data repo but are missing
  locally (e.g. the derive model on a fresh machine);
- ``--dry-run`` mirror without committing;
- ``--message`` custom commit message.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from arknightsavatar.config import DataRepoConfig, load_config

# 比较模式：manifest 加速（默认） / 全量 sha256 / 旧 size+mtime
COMPARE_AUTO = "auto"
COMPARE_CONTENT = "content"
COMPARE_SIZE_MTIME = "size_mtime"


class SyncError(Exception):
    """A sync-cache failure with a user-facing message."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def git(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    """Run the git CLI in ``cwd``, capturing output (never raises on non-zero)."""
    return subprocess.run(
        ["git", *argv],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,  # 调用方检查 returncode
    )


def ensure_git_available() -> None:
    result = subprocess.run(
        ["git", "--version"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SyncError("git CLI not found on PATH; sync-cache requires git")


def ensure_working_copy(repo: DataRepoConfig, root: Path, pull: bool) -> Path:
    """Return the local git working copy of the data repo, cloning if needed."""
    workdir = Path(repo.path)
    if not workdir.is_absolute():
        workdir = (root / workdir).resolve()
    else:
        workdir = workdir.resolve()

    if (workdir / ".git").exists():
        if pull:
            result = git(workdir, "pull", "--ff-only")
            if result.returncode != 0:
                raise SyncError(f"git pull failed: {result.stderr.strip()}")
        return workdir

    if not repo.url:
        raise SyncError(
            "data_repo.url is empty: create the GitHub data repo first, then fill "
            "url in data_repo.yaml and run again"
        )
    if workdir.exists() and any(workdir.iterdir()):
        raise SyncError(
            f"{workdir} exists but is not a git working copy; "
            "move it away or fix data_repo.path"
        )
    workdir.parent.mkdir(parents=True, exist_ok=True)
    result = git(
        workdir.parent, "clone", "--branch", repo.branch, repo.url, str(workdir.name)
    )
    if result.returncode != 0:
        raise SyncError(f"git clone failed: {result.stderr.strip()}")
    return workdir


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_category_manifest(path: Path) -> dict[str, dict]:
    """Rel (posix) -> {size, sha256} from a category manifest; {} when missing/broken."""
    try:
        payload = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    files = payload.get("files") if isinstance(payload, dict) else None
    return files if isinstance(files, dict) else {}


def _same_size_mtime(source: Path, dest: Path) -> bool:
    stat = dest.stat()
    src_stat = source.stat()
    return stat.st_size == src_stat.st_size and stat.st_mtime_ns == src_stat.st_mtime_ns


def _same_file(
    source: Path,
    dest: Path,
    mode: str = COMPARE_AUTO,
    src_record: dict | None = None,
    dst_record: dict | None = None,
) -> bool:
    """Content-level file comparison.

    - ``content``: full sha256 of both sides (correct without manifests);
    - ``auto`` (default): when the file is covered by both the local and the
      destination category manifest, compare ``{size, sha256}`` (no hashing);
      otherwise fall back to ``size + mtime_ns`` (legacy behavior);
    - ``size_mtime``: legacy behavior, ignores manifests.
    """
    if not dest.is_file():
        return False
    if mode == COMPARE_SIZE_MTIME:
        return _same_size_mtime(source, dest)
    if mode == COMPARE_CONTENT:
        return _sha256(source) == _sha256(dest)
    if src_record is not None and dst_record is not None:
        return src_record.get("size") == dst_record.get("size") and src_record.get(
            "sha256"
        ) == dst_record.get("sha256")
    return _same_size_mtime(source, dest)


def mirror_dir(
    source: Path,
    dest: Path,
    stats: dict,
    mode: str = COMPARE_AUTO,
    manifest: dict[str, dict] | None = None,
) -> None:
    """Incremental mirror: copy new/changed files, remove stale ones.

    ``manifest`` (optional) is the local category manifest (``rel -> {size,
    sha256}``); the destination manifest is loaded automatically when present.
    Files covered by both manifests are compared by fingerprint without
    hashing; everything else falls back per ``mode``. ``manifest.json`` is
    copied last so an interrupted sync never leaves a newer manifest than the
    files it describes.
    """
    src_files = {p.relative_to(source): p for p in source.rglob("*") if p.is_file()}
    dest.mkdir(parents=True, exist_ok=True)
    dst_manifest = (
        load_category_manifest(dest / "manifest.json") if manifest is not None else {}
    )
    for rel, src_file in sorted(
        src_files.items(), key=lambda item: item[0].as_posix() == "manifest.json"
    ):
        dst_file = dest / rel
        same = _same_file(
            src_file,
            dst_file,
            mode=mode,
            src_record=manifest.get(rel.as_posix()) if manifest is not None else None,
            dst_record=dst_manifest.get(rel.as_posix()),
        )
        if not same:
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            stats["copied"] += 1
    for dst_file in sorted(dest.rglob("*"), reverse=True):
        rel = dst_file.relative_to(dest)
        if dst_file.is_file() and rel not in src_files:
            dst_file.unlink()
            stats["removed"] += 1
        elif dst_file.is_dir() and not any(dst_file.iterdir()):
            try:
                dst_file.rmdir()
            except OSError:  # pragma: no cover - concurrent access
                pass


def mirror_file(
    source: Path, dest: Path, stats: dict, mode: str = COMPARE_AUTO
) -> None:
    if dest.is_dir():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not _same_file(source, dest, mode=mode):
        shutil.copy2(source, dest)
        stats["copied"] += 1


def mirror_category(
    local: str,
    remote: str,
    root: Path,
    workdir: Path,
    stats: dict,
    mode: str = COMPARE_AUTO,
) -> None:
    source = Path(local)
    if not source.is_absolute():
        source = root / source
    dest = workdir / remote
    if source.is_dir():
        manifest = (
            load_category_manifest(source / "manifest.json")
            if mode != COMPARE_SIZE_MTIME
            else {}
        )
        mirror_dir(source, dest, stats, mode=mode, manifest=manifest)
    elif source.is_file():
        mirror_file(source, dest, stats, mode=mode)
    else:
        # 本地目录缺失时保留数据仓库中的现有内容（不删除）。
        stats["missing"] += 1


def restore_category(
    local: str, remote: str, root: Path, workdir: Path, stats: dict
) -> None:
    """Copy files present in the data repo but missing locally (never overwrites)."""
    source = workdir / remote
    dest = Path(local)
    if not dest.is_absolute():
        dest = root / dest
    if source.is_dir():
        for src_file in source.rglob("*"):
            if not src_file.is_file():
                continue
            dst_file = dest / src_file.relative_to(source)
            if not dst_file.exists():
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                stats["restored"] += 1
    elif source.is_file() and not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        stats["restored"] += 1


def commit_changes(workdir: Path, message: str, dry_run: bool) -> bool:
    """Stage everything and commit when there are staged changes."""
    git(workdir, "add", "-A")
    result = git(workdir, "diff", "--cached", "--quiet")
    if result.returncode == 0:
        return False  # 无变化，不产生空提交
    if dry_run:
        return True
    result = git(workdir, "commit", "-m", message)
    if result.returncode != 0:
        raise SyncError(
            f"git commit failed: {result.stderr.strip()}\n"
            "hint: configure git identity first (git config user.name/user.email)"
        )
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-sync-cache",
        description="Sync data categories into the GitHub data repo working copy and commit.",
    )
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument(
        "--pull",
        action="store_true",
        help="git pull the working copy before mirroring",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="copy files missing locally from the data repo (before mirroring)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="mirror into the working copy but do not commit",
    )
    parser.add_argument(
        "--message",
        default=None,
        help="commit message (default: 'sync <UTC timestamp>')",
    )
    compare = parser.add_mutually_exclusive_group()
    compare.add_argument(
        "--manifest",
        action="store_true",
        help="compare by category manifest fingerprints ({size, sha256}, no hashing); "
        "auto-detected by default",
    )
    compare.add_argument(
        "--content-hash",
        action="store_true",
        help="full sha256 content comparison (correct even without manifests)",
    )
    compare.add_argument(
        "--size-mtime",
        action="store_true",
        help="legacy size + mtime comparison (ignores manifests)",
    )
    return parser


def _compare_mode(args: argparse.Namespace) -> str:
    if args.content_hash:
        return COMPARE_CONTENT
    if args.size_mtime:
        return COMPARE_SIZE_MTIME
    return COMPARE_AUTO


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    mode = _compare_mode(args)
    try:
        config = load_config(args.config)
        ensure_git_available()
        root = Path.cwd()
        workdir = ensure_working_copy(config.data_repo, root, pull=args.pull)

        stats = {"copied": 0, "removed": 0, "restored": 0, "missing": 0}
        if args.restore:
            for category in config.data_repo.categories:
                restore_category(category.local, category.remote, root, workdir, stats)
        for category in config.data_repo.categories:
            mirror_category(
                category.local, category.remote, root, workdir, stats, mode=mode
            )

        message = args.message or f"sync {_now()}"
        committed = commit_changes(workdir, message, dry_run=args.dry_run)
    except SyncError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"working copy: {workdir}")
    print(
        f"copied={stats['copied']} removed={stats['removed']} "
        f"restored={stats['restored']} missing={stats['missing']} "
        f"committed={committed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
