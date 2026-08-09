from __future__ import annotations

import re
from pathlib import Path

from .base import FileInfo, Source

_LS_LINE = re.compile(r"^[bcdlps-][rwxsStT-]{9}\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.+)$")


class AdbSource(Source):
    """Pulls AB files from an Android device over ADB (adb_shell)."""

    name = "adb"

    CATEGORY_SUBPATHS = {
        "characters": ("avg", "characters"),
        "chararts": ("avg", "chararts"),
        "skins": ("avg", "skinpack"),
        "avatars": ("spritepack",),
    }

    def __init__(self, host: str, port: int, location: str):
        from adb_shell.adb_device import AdbDeviceTcp

        self._device = AdbDeviceTcp(host=host, port=port)
        self._device.connect()
        self.location = location.rstrip("/")
        self._ls_cache: dict[str, list[tuple[str, int]]] = {}

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
        self._device.pull(self.remote_path(rel), str(dest))
