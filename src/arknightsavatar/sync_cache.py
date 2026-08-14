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
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from arknightsavatar.config import DataRepoConfig, load_config


class SyncError(Exception):
    """A sync-cache failure with a user-facing message."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    """Run the git CLI in ``cwd``, capturing output (never raises on non-zero)."""
    return subprocess.run(["git", *argv], cwd=str(cwd), capture_output=True, text=True)


def ensure_git_available() -> None:
    result = subprocess.run(
        ["git", "--version"], capture_output=True, text=True
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
    result = git(workdir.parent, "clone", "--branch", repo.branch, repo.url, str(workdir.name))
    if result.returncode != 0:
        raise SyncError(f"git clone failed: {result.stderr.strip()}")
    return workdir


def _same_file(source: Path, dest: Path) -> bool:
    stat = dest.stat()
    src_stat = source.stat()
    return stat.st_size == src_stat.st_size and stat.st_mtime_ns == src_stat.st_mtime_ns


def mirror_dir(source: Path, dest: Path, stats: dict) -> None:
    """Incremental mirror: copy new/changed files, remove stale ones."""
    src_files = {p.relative_to(source): p for p in source.rglob("*") if p.is_file()}
    dest.mkdir(parents=True, exist_ok=True)
    for rel, src_file in src_files.items():
        dst_file = dest / rel
        if not dst_file.is_file() or not _same_file(src_file, dst_file):
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


def mirror_file(source: Path, dest: Path, stats: dict) -> None:
    if dest.is_dir():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file() or not _same_file(source, dest):
        shutil.copy2(source, dest)
        stats["copied"] += 1


def mirror_category(local: str, remote: str, root: Path, workdir: Path, stats: dict) -> None:
    source = Path(local)
    if not source.is_absolute():
        source = root / source
    dest = workdir / remote
    if source.is_dir():
        mirror_dir(source, dest, stats)
    elif source.is_file():
        mirror_file(source, dest, stats)
    else:
        # 本地目录缺失时保留数据仓库中的现有内容（不删除）。
        stats["missing"] += 1


def restore_category(local: str, remote: str, root: Path, workdir: Path, stats: dict) -> None:
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
            mirror_category(category.local, category.remote, root, workdir, stats)

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
