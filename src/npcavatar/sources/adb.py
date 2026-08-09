from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from .base import FileInfo, Source

_LS_LINE = re.compile(r"^[bcdlps-][rwxsStT-]{9}\s+\S+\s+\S+\s+\S+\s+(\d+)\s+(?:\S+\s+){2,3}(.+)$")
_UNIT = 1024.0


def _fmt_bytes(value: float) -> str:
    """Format a byte count or byte rate compactly."""
    if value >= _UNIT ** 3:
        return f"{value / _UNIT ** 3:.2f} GiB"
    if value >= _UNIT ** 2:
        return f"{value / _UNIT ** 2:.1f} MiB"
    if value >= _UNIT:
        return f"{value / _UNIT:.1f} KiB"
    return f"{value:.0f} B"


class _PullProgress:
    """Accumulates adb_shell pull chunks and redraws a single progress line.

    adb_shell calls the callback as ``callback(device_path, chunk_bytes,
    total_bytes)``; the callback is responsible for accumulating bytes itself.
    """

    def __init__(self, name: str, *, enabled: bool = True, stream=None, min_interval: float = 0.1):
        self.name = name
        self.enabled = enabled
        self._stream = stream if stream is not None else sys.stderr
        self._min_interval = min_interval
        self._received = 0
        self._total = 0
        self._started = time.monotonic()
        self._last_draw = 0.0
        self._last_len = 0
        self._done = False

    def __call__(self, device_path: str, chunk_bytes: int, total_bytes: int) -> None:
        self._received += chunk_bytes
        if self._total == 0 and total_bytes:
            self._total = total_bytes
        self._draw(force=False)

    def _draw(self, *, force: bool) -> None:
        if not self.enabled or self._done:
            return
        now = time.monotonic()
        if not force and now - self._last_draw < self._min_interval:
            return
        self._last_draw = now
        elapsed = now - self._started
        speed = self._received / elapsed if elapsed > 0 else 0.0
        if self._total > 0:
            percent = min(100.0, self._received / self._total * 100.0)
            line = (
                f"{self.name}  {percent:5.1f}%  "
                f"{_fmt_bytes(self._received)}/{_fmt_bytes(self._total)}  {_fmt_bytes(speed)}/s"
            )
        else:
            line = f"{self.name}  {_fmt_bytes(self._received)}  {_fmt_bytes(speed)}/s"
        pad = " " * max(0, self._last_len - len(line))
        self._stream.write("\r" + line + pad)
        self._stream.flush()
        self._last_len = len(line)

    def finish(self) -> None:
        self._draw(force=True)
        self._done = True
        if self.enabled:
            self._stream.write("\n")
            self._stream.flush()


class AdbSource(Source):
    """Pulls AB files from an Android device over ADB (adb_shell)."""

    name = "adb"

    CATEGORY_SUBPATHS = {
        "characters": ("avg", "characters"),
        "chararts": ("chararts",),
        "skins": ("skinpack",),
        "avatars": ("spritepack",),
    }

    def __init__(self, host: str, port: int, location: str, *, progress: bool | None = None):
        from adb_shell.adb_device import AdbDeviceTcp

        self._device = AdbDeviceTcp(host=host, port=port)
        self._device.connect()
        self.location = location.rstrip("/")
        self._ls_cache: dict[str, list[tuple[str, int]]] = {}
        self._show_progress = sys.stderr.isatty() if progress is None else progress

    def dir_for(self, category: str) -> str:
        return "/".join((self.location, *self.CATEGORY_SUBPATHS[category]))

    def remote_path(self, rel: str) -> str:
        category, name = rel.split("/", 1)
        return f"{self.dir_for(category)}/{name}"

    def _ls(self, path: str) -> list[tuple[str, int]]:
        if path in self._ls_cache:
            return self._ls_cache[path]
        output = self._device.shell(f"ls -l {path}")
        entries: list[tuple[str, int]] = []
        for line in output.splitlines():
            match = _LS_LINE.match(line.strip())
            if match:
                entries.append((match.group(2), int(match.group(1))))
        self._ls_cache[path] = entries
        return entries

    def list_files(self, category: str) -> list[FileInfo]:
        results = []
        for name, size in self._ls(self.dir_for(category)):
            if name.endswith(".ab"):
                results.append(FileInfo(rel=f"{category}/{name}", size=size))
        return results

    def fetch_to(self, rel: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        progress = _PullProgress(rel, enabled=self._show_progress)
        try:
            self._device.pull(self.remote_path(rel), str(dest), progress_callback=progress)
        finally:
            progress.finish()
