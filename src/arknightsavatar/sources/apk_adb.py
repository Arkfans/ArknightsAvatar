from __future__ import annotations

import os
import re
import shlex
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from arknightsavatar.device_caps import detect_device_caps

from .adb import (
    _DEVICE_TMP,
    PullProgress,
    extract_pack,
    rm_device_files,
    tar_on_device,
    write_device_listing,
)
from .base import CATEGORY_SUBPATHS, FileInfo, Source
from .device import connect_device, installed_apk_paths, load_rsa_keys

_UNZIP_L_LINE = re.compile(r"^\s*(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(.+)$")


class ApkAdbSource(Source):
    """Reads AB files from an installed APK by unzipping entries on the device."""

    name = "apk"

    CATEGORY_SUBPATHS: ClassVar[dict[str, tuple[str, ...]]] = CATEGORY_SUBPATHS

    AB_ROOT = "assets/AB/Android"

    def __init__(
        self,
        host: str,
        port: int,
        package: str,
        *,
        adb_key: str | None = None,
        auth_timeout_s: int = 30,
        progress: bool | None = None,
        batch: bool = True,
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
        self._apk_paths = sorted(
            paths, key=lambda path: (0 if path.endswith("base.apk") else 1, path)
        )
        self._show_progress = sys.stderr.isatty() if progress is None else progress
        self._batch = batch
        if batch:
            caps = detect_device_caps(self._device)
            if not caps.apk_batch_ok:
                print(
                    "warning: 设备不支持 unzip/tar 批量打包（unzip -l/-p 或 tar -cf -T "
                    "探测失败），已改为逐条解压",
                    file=sys.stderr,
                )
                self._batch = False

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

    def fetch_many(
        self, items: Sequence[tuple[FileInfo, Path]]
    ) -> list[tuple[FileInfo, Exception]]:
        """Fetch several files at once by packing them on the device.

        Each category becomes one tar archive on the device: the needed zip
        entries are unzipped into a device temp directory, tarred, and pulled
        with a single sync-protocol transfer, then extracted locally with size
        verification. Any file the pack path could not deliver (missing
        member, size mismatch, failed unzip/tar/pull) falls back to the
        per-entry unzip (fetch_to), so a batch failure never loses data.
        """
        failures: list[tuple[FileInfo, Exception]] = []
        by_category: dict[str, list[tuple[FileInfo, Path]]] = {}
        for info, dest in items:
            by_category.setdefault(info.rel.split("/", 1)[0], []).append((info, dest))

        for category, cat_items in by_category.items():
            if self._batch:
                try:
                    pack_failures = self._fetch_category_pack(category, cat_items)
                except Exception as error:  # noqa: BLE001 - whole pack failed
                    pack_failures = [(info, error) for info, _ in cat_items]
                failed_rels = {info.rel for info, _ in pack_failures}
                if not failed_rels:
                    continue
                fallback = [
                    (info, dest) for info, dest in cat_items if info.rel in failed_rels
                ]
                self._fetch_one_by_one(fallback, failures)
                continue

            self._fetch_one_by_one(cat_items, failures)
        return failures

    def _fetch_one_by_one(
        self,
        items: Sequence[tuple[FileInfo, Path]],
        failures: list[tuple[FileInfo, Exception]],
    ) -> None:
        """Fetch each file individually (shell unzip has no stream progress)."""
        for info, dest in items:
            try:
                self.fetch_to(info.rel, dest)
            except Exception as error:  # noqa: BLE001 - report and continue
                failures.append((info, error))

    def _fetch_category_pack(
        self,
        category: str,
        items: Sequence[tuple[FileInfo, Path]],
    ) -> list[tuple[FileInfo, Exception]]:
        """Unzip the needed entries on the device, tar once, pull once.

        Raises if the whole pack (tar or pull) failed; returns per-file
        failures otherwise. Scratches live under /data/local/tmp and are
        removed in the finally block.
        """
        if any(info.rel not in self._entry_apk for info, _ in items):
            self.list_files(category)  # populate _entry_apk like fetch_to does

        unique = f"{category}_{os.getpid()}"
        pack = f"{_DEVICE_TMP}/arknights_ab_{unique}.tar"
        tmpdir = f"{_DEVICE_TMP}/arknights_ab_{unique}.d"
        names_list = f"{_DEVICE_TMP}/arknights_ab_{unique}.list"
        entries_lists = [
            f"{_DEVICE_TMP}/arknights_ab_{unique}.entries_{index}"
            for index in range(len(self._apk_paths))
        ]
        local_fd, local_pack = tempfile.mkstemp(
            suffix=".tar", prefix=f"arknights_ab_{category}_"
        )
        os.close(local_fd)
        local_pack = Path(local_pack)
        try:
            names = [info.rel.split("/", 1)[1] for info, _ in items]
            write_device_listing(self._device, names_list, names)

            # Group the needed zip entries by their owning APK (base wins on
            # duplicates, so each rel maps to exactly one APK).
            by_apk: dict[str, list[str]] = {}
            for info, _ in items:
                by_apk.setdefault(self._entry_apk[info.rel], []).append(
                    self._entry_for_rel(info.rel)
                )
            for apk, entries in by_apk.items():
                entries_list = entries_lists[self._apk_paths.index(apk)]
                write_device_listing(self._device, entries_list, entries)
                self._unzip_entries_on_device(apk, entries_list, tmpdir)

            tar_on_device(self._device, pack, tmpdir, names_list)
            progress = PullProgress(
                f"{category} ({len(items)} files)", enabled=self._show_progress
            )
            try:
                self._device.pull(
                    pack, str(local_pack), progress_callback=progress, read_timeout_s=60
                )
            finally:
                progress.finish()
            return extract_pack(local_pack, items)
        finally:
            rm_device_files(self._device, pack, names_list, *entries_lists)
            rm_device_files(self._device, tmpdir, recursive=True)
            local_pack.unlink(missing_ok=True)

    def _unzip_entries_on_device(
        self, apk: str, entries_list: str, tmpdir: str
    ) -> None:
        """Extract the listed zip entries to tmpdir (as basenames) via unzip -p.

        Only relies on the device `unzip -p` (verified on toybox); entries
        that fail to extract leave an empty file behind, which the local size
        check turns into a per-file fallback.
        """
        command = (
            f"mkdir -p {shlex.quote(tmpdir)} && "
            f'while IFS= read -r e; do unzip -p {self._quote(apk)} "$e" '
            f'> {shlex.quote(tmpdir)}/"${{e##*/}}"; done < {shlex.quote(entries_list)}'
        )
        self._device.shell(command, read_timeout_s=600, timeout_s=3600)

    def sha256(self, rel: str) -> str | None:
        return None
