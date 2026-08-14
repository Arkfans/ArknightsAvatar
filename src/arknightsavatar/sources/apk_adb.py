from __future__ import annotations

import re
import shlex
from pathlib import Path

from .base import FileInfo, Source
from .device import connect_device, installed_apk_paths, load_rsa_keys

_UNZIP_L_LINE = re.compile(r"^\s*(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(.+)$")


class ApkAdbSource(Source):
    """Reads AB files from an installed APK by unzipping entries on the device."""

    name = "apk"

    CATEGORY_SUBPATHS = {
        "characters": ("avg", "characters"),
        "avatars": ("spritepack",),
    }

    AB_ROOT = "assets/AB/Android"

    def __init__(
        self,
        host: str,
        port: int,
        package: str,
        *,
        adb_key: str | None = None,
        auth_timeout_s: int = 30,
    ):
        from adb_shell.adb_device import AdbDeviceTcp

        self._device = AdbDeviceTcp(host=host, port=port)
        rsa_keys = load_rsa_keys(adb_key)
        connect_device(
            self._device,
            rsa_keys,
            auth_timeout_s=auth_timeout_s,
            target=f"设备 {host}:{port}",
        )

        self.package = package
        self._ls_cache: dict[str, list[FileInfo]] = {}
        self._entry_apk: dict[str, str] = {}
        paths = installed_apk_paths(self._device, package)
        if not paths:
            raise SystemExit(f"package not installed: {package}")
        self._apk_paths = sorted(paths, key=lambda path: (0 if path.endswith("base.apk") else 1, path))

    @staticmethod
    def _quote(value: str) -> str:
        return shlex.quote(value)

    def _entry_for_rel(self, rel: str) -> str:
        category, name = rel.split("/", 1)
        return "/".join((self.AB_ROOT, *self.CATEGORY_SUBPATHS[category], name))

    @staticmethod
    def _parse_unzip_l(output: str) -> list[tuple[int, str]]:
        entries: list[tuple[int, str]] = []
        for line in output.splitlines():
            match = _UNZIP_L_LINE.match(line.strip())
            if match:
                entries.append((int(match.group(1)), match.group(4)))
        return entries

    def _list_apk_entries(self, apk: str, category: str) -> list[tuple[int, str]]:
        pattern = "/".join((self.AB_ROOT, *self.CATEGORY_SUBPATHS[category], "*.ab"))
        command = f"unzip -l {self._quote(apk)} {self._quote(pattern)}"
        return self._parse_unzip_l(self._device.shell(command))

    def list_files(self, category: str) -> list[FileInfo]:
        if category in self._ls_cache:
            return self._ls_cache[category]

        seen: dict[str, int] = {}
        for apk in self._apk_paths:
            for size, name in self._list_apk_entries(apk, category):
                rel = f"{category}/{name.rsplit('/', 1)[-1]}"
                if rel in seen:
                    continue
                seen[rel] = size
                self._entry_apk[rel] = apk

        files = [FileInfo(rel=rel, size=size) for rel, size in sorted(seen.items())]
        self._ls_cache[category] = files
        return files

    def fetch_to(self, rel: str, dest: Path) -> None:
        apk = self._entry_apk.get(rel)
        if apk is None:
            category = rel.split("/", 1)[0]
            self.list_files(category)
            apk = self._entry_apk.get(rel)
        if apk is None:
            raise FileNotFoundError(rel)

        entry = self._entry_for_rel(rel)
        command = f"unzip -p {self._quote(apk)} {self._quote(entry)}"
        data = self._device.shell(command, decode=False)
        if isinstance(data, str):
            data = data.encode()

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def sha256(self, rel: str) -> str | None:
        return None
