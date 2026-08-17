"""Sync data categories into the GitHub data repo (``arknightsavatar-sync-cache``).

The four shared data families -- recognition data (``data/recognition``),
raw avatars (``data/unpacked/avatars``), extracted avatars (``data/export``,
``data/export_webp``) and statistics lists (``data/stats``,
``data/arknights_npc.json``) -- are carried by a separate GitHub data repo.

This tool mirrors those local paths into a local git working copy of the data
repo (``data_repo.yaml: path``, default ``data_cache``) and commits
incrementally through the git CLI: a commit is only created when something
changed, so repeated runs are cheap. The initial clone uses git by default;
``--method gh`` or ``[sync_cache] method = "gh"`` uses ``gh repo clone``
instead. Create the GitHub repo first, then fill ``data_repo.yaml: url`` and
run -- the tool is ready as-is.

Options:
- ``--pull``    update the working copy from the remote before mirroring;
- ``--restore`` copy files that exist in the data repo but are missing
  locally (e.g. the derive model on a fresh machine);
- ``--dry-run`` mirror without committing;
- ``--message`` custom commit message;
- ``--push``    push the local branch to ``origin`` after committing
  (skipped when there is nothing to push; ignored with ``--dry-run``);
- ``--method``  choose ``git`` (default) or ``gh`` for the initial clone only.
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

from arknightsavatar import __version__, paths
from arknightsavatar.config import ConfigError, DataRepoConfig, load_config

# 比较模式：manifest 加速（默认） / 全量 sha256 / 旧 size+mtime
COMPARE_AUTO = "auto"
COMPARE_CONTENT = "content"
COMPARE_SIZE_MTIME = "size_mtime"

# 提取报告（avatar_extract.json）对应的提交消息正文比较模式标签
_COMPARE_LABELS = {
    COMPARE_AUTO: "manifest {size, sha256} fingerprints; size+mtime fallback",
    COMPARE_CONTENT: "full sha256 content hash",
    COMPARE_SIZE_MTIME: "size + mtime (legacy)",
}


class SyncError(Exception):
    """A sync-cache failure with a user-facing message."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load_extract_report(path: str | Path) -> dict:
    """Load ``avatar_extract.json``; ``{}`` when missing/broken."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_counts(report: dict) -> dict[str, int]:
    """character/bases/textures 计数（status==ok 口径）；空报告→0。

    - ``characters``：至少一个 diff 被解析出（status==ok）的角色数；
    - ``bases``：status==ok 的 base 头像数；
    - ``textures``：``bases`` + status==ok 的 diff 数（base + diff 总和）。
    """
    characters = report.get("characters") if isinstance(report, dict) else {}
    if not isinstance(characters, dict):
        characters = {}
    n_chars = n_bases = n_diffs = 0
    for char in characters.values():
        if not isinstance(char, dict):
            continue
        bases = char.get("bases") if isinstance(char.get("bases"), dict) else {}
        diffs = char.get("diffs") if isinstance(char.get("diffs"), dict) else {}
        b_ok = sum(
            1 for v in bases.values() if isinstance(v, dict) and v.get("status") == "ok"
        )
        d_ok = sum(
            1 for v in diffs.values() if isinstance(v, dict) and v.get("status") == "ok"
        )
        if d_ok:
            n_chars += 1
        n_bases += b_ok
        n_diffs += d_ok
    return {"characters": n_chars, "bases": n_bases, "textures": n_bases + n_diffs}


def _resolve_versions(report: dict, fallback_game: str) -> tuple[str, str]:
    """Pipeline/game version from extract report header; ``__version__`` / fallback on miss."""
    if isinstance(report, dict):
        pipeline = str(report.get("pipeline_version") or __version__)
        game = str(report.get("game_version") or fallback_game)
    else:
        pipeline = __version__
        game = fallback_game
    return pipeline, game


def build_commit_message(
    counts: dict[str, int],
    mode: str,
    pipeline_version: str,
    game_version: str,
) -> str:
    """Default rich commit message: ``sync <UTC timestamp>`` + 6 background lines.

    主题行保持 ``sync <UTC timestamp>``（与数据仓库历史及 ``"sync" in log`` 测试一致）；
    正文含 pipeline/game 版本、比较模式与提取计数。``--message`` 完全覆盖此默认。
    """
    return "\n".join(
        [
            f"sync {_now()}",
            "",
            f"pipeline:   arknightsavatar {pipeline_version}",
            f"game:       {game_version}",
            f"compare:    {_COMPARE_LABELS[mode]}",
            f"characters: {counts['characters']}",
            f"bases:      {counts['bases']}",
            f"textures:   {counts['textures']}",
        ]
    )


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
    try:
        result = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError as error:
        raise SyncError("git CLI not found on PATH; sync-cache requires git") from error
    if result.returncode != 0:
        raise SyncError("git CLI not found on PATH; sync-cache requires git")


def gh(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    """Run the GitHub CLI in ``cwd``, capturing output."""
    return subprocess.run(
        ["gh", *argv],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def ensure_gh_available(cwd: Path) -> None:
    """Raise a user-facing error when the GitHub CLI is unavailable."""
    try:
        result = gh(cwd, "--version")
    except FileNotFoundError as error:
        raise SyncError(
            "gh CLI not found on PATH; sync-cache --method gh requires gh"
        ) from error
    if result.returncode != 0:
        raise SyncError("gh CLI not available; sync-cache --method gh requires gh")


def ensure_working_copy(
    repo: DataRepoConfig, root: Path, pull: bool, method: str = "git"
) -> Path:
    """Return the local git working copy, cloning through the selected method."""
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
    if method == "git":
        result = git(
            workdir.parent, "clone", "--branch", repo.branch, repo.url, str(workdir.name)
        )
        if result.returncode != 0:
            raise SyncError(f"git clone failed: {result.stderr.strip()}")
    elif method == "gh":
        ensure_gh_available(workdir.parent)
        try:
            result = gh(
                workdir.parent,
                "repo",
                "clone",
                repo.url,
                str(workdir),
                "--",
                "--branch",
                repo.branch,
            )
        except FileNotFoundError as error:  # pragma: no cover - checked above
            raise SyncError(
                "gh CLI not found on PATH; sync-cache --method gh requires gh"
            ) from error
        if result.returncode != 0:
            raise SyncError(f"gh repo clone failed: {result.stderr.strip()}")
    else:  # pragma: no cover - CLI/config validation makes this unreachable
        raise SyncError(f"unknown clone method: {method}")
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
    if dest.exists() and dest.is_dir():
        # 配置错位（本地映射为文件、工作副本里却是目录，或反之）时显式报错，
        # 而非 shutil.rmtree 整个目录（真实数据丢失向量）。
        raise SyncError(
            f"destination is a directory, expected a file: {dest}"
            " (check data_repo.yaml local/remote mapping)"
        )
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
    add = git(workdir, "add", "-A")
    if add.returncode != 0:
        # add 失败（索引锁/hook 报错/权限）若不检查，diff --cached --quiet 会返回 0
        # 而被误判为无变化，本地数据被静默跳过同步。
        raise SyncError(f"git add failed: {add.stderr.strip()}")
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


def _unpushed_commits(workdir: Path, branch: str) -> bool:
    """True when the local branch has commits not yet on ``origin/<branch>``.

    Used by ``--push`` so a retry after a failed push (commit succeeded, push
    rejected) is not silently skipped: a commit exists locally but nothing new
    was mirrored. A ``rev-list`` failure (e.g. missing remote-tracking ref on
    the first push) counts as unpushed so the push is attempted; ``git push``
    then either creates the ref or fails loudly.
    """
    result = git(workdir, "rev-list", "--count", f"origin/{branch}..HEAD")
    if result.returncode != 0:
        return True
    return result.stdout.strip() not in ("", "0")


def push_changes(workdir: Path, branch: str) -> None:
    """Push the local ``branch`` to ``origin``; ``SyncError`` on rejection."""
    result = git(workdir, "push", "origin", branch)
    if result.returncode != 0:
        raise SyncError(
            f"git push failed: {result.stderr.strip()}\n"
            "hint: pull first (sync-cache --pull or git pull --rebase) when the "
            "remote has commits you don't have yet"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-sync-cache",
        description="Sync data categories into the GitHub data repo working copy and commit.",
    )
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument(
        "--method",
        choices=("git", "gh"),
        default=None,
        help="initial clone method (default: config sync_cache.method, then git); subsequent sync always uses git",
    )
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
        help="commit message (default: 'sync <UTC timestamp>' with pipeline/game version, compare mode, and extract counts)",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="git push the local branch to origin after committing (skipped when there is nothing to push; ignored with --dry-run)",
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
        method = args.method or config.sync_cache.method
        ensure_git_available()
        root = Path.cwd()
        workdir = ensure_working_copy(
            config.data_repo, root, pull=args.pull, method=method
        )

        stats = {"copied": 0, "removed": 0, "restored": 0, "missing": 0}
        if args.restore:
            for category in config.data_repo.categories:
                restore_category(category.local, category.remote, root, workdir, stats)
        for category in config.data_repo.categories:
            mirror_category(
                category.local, category.remote, root, workdir, stats, mode=mode
            )

        report = _load_extract_report(root / paths.EXTRACT_REPORT)
        counts = _extract_counts(report)
        pipeline_version, game_version = _resolve_versions(report, config.game_version)
        message = args.message or build_commit_message(
            counts, mode, pipeline_version, game_version
        )
        committed = commit_changes(workdir, message, dry_run=args.dry_run)
        pushed = False
        if (
            args.push
            and not args.dry_run
            and (committed or _unpushed_commits(workdir, config.data_repo.branch))
        ):
            push_changes(workdir, config.data_repo.branch)
            pushed = True
    except (ConfigError, SyncError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"working copy: {workdir}")
    print(
        f"copied={stats['copied']} removed={stats['removed']} "
        f"restored={stats['restored']} missing={stats['missing']} "
        f"committed={committed} pushed={pushed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
