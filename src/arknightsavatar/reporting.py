"""Shared reporting helpers: version headers, JSON output, sha256, idempotent writes.

Every recognition/statistics report carries a small version header
``{schema_version, pipeline_version, game_version, generated_at}`` so consumers
can tell which pipeline and game version produced a file. Only new keys are
added on top of the existing payloads; existing keys and their order are left
untouched (backward compatible with old consumers).

``write_report()`` is the single chokepoint used by the report-producing tools:
it injects the header and writes atomically (tmp + ``os.replace``) or to stdout
(``-``). ``idempotent=True`` additionally skips rewriting when the content is
unchanged apart from ``generated_at``, so manifest/version files keep their old
timestamp and mtime and do not produce spurious data-repo commits when nothing
changed (same philosophy as ``sync-cache``'s "no changes -> no commit").
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from arknightsavatar import __version__, paths

SCHEMA_VERSION = 1

# 版本头键（顺序即输出顺序）
HEADER_KEYS = ("schema_version", "pipeline_version", "game_version", "generated_at")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_file(path: str | Path) -> str:
    """Content sha256 of a file (streamed in 1 MiB chunks)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_game_version(raw_manifest: str | Path = paths.RAW_MANIFEST) -> str:
    """Resolve the game version: raw manifest -> config -> 'unknown'.

    ``data/raw/manifest.json`` is authoritative (content-level fingerprint);
    ``config.toml`` / ``ARKNIGHTSAVATAR_GAME_VERSION`` is the fallback; last
    resort is ``"unknown"`` so version files are still generated.
    """
    try:
        payload = json.loads(Path(raw_manifest).read_text(encoding="utf8"))
    except (OSError, ValueError):
        payload = None
    if isinstance(payload, dict):
        version = payload.get("game_version")
        if isinstance(version, str) and version:
            return version
    from arknightsavatar.config import load_config

    return load_config().game_version or "unknown"


def report_header(
    game_version: str | None = None, generated_at: str | None = None
) -> dict:
    """The shared report header ``{schema_version, pipeline_version, game_version, generated_at}``."""
    return {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": __version__,
        "game_version": game_version
        if game_version is not None
        else load_game_version(),
        "generated_at": generated_at if generated_at is not None else now_iso(),
    }


def inject_header(payload: dict, *, game_version: str | None = None) -> dict:
    """Header first, then ``payload`` (a payload ``generated_at`` is kept)."""
    merged = report_header(game_version=game_version)
    merged.update(payload)
    return merged


def write_report(
    payload: dict,
    output: str | Path,
    *,
    game_version: str | None = None,
    idempotent: bool = False,
) -> bool:
    """Inject the version header and write ``payload`` to ``output`` (stdout when ``-``).

    Files are written atomically (tmp + ``os.replace``) with ``indent=2``.
    With ``idempotent=True`` the file is not rewritten when the content is
    unchanged apart from ``generated_at`` (old timestamp/mtime are kept).
    Returns True when something was actually written.
    """
    data = inject_header(payload, game_version=game_version)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if str(output) == "-":
        print(text, end="")
        return True
    path = Path(output)
    if idempotent and path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf8"))
        except (OSError, ValueError):
            old = None
        if isinstance(old, dict) and _same_ignoring(old, data, "generated_at"):
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件用随机后缀（mkstemp），避免固定名 .tmp 的并发竞争/写失败残留；
    # 仍在目标同一目录以保证 os.replace 跨同文件系统原子替换。
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(text, encoding="utf8")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    return True


def _same_ignoring(a: dict, b: dict, *keys: str) -> bool:
    """Dict equality ignoring the given keys' values (key sets must match)."""
    if set(a) != set(b):
        return False
    return all(a[key] == b[key] for key in a if key not in keys)
