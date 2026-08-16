from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
import tarfile
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from arknightsavatar.device_caps import _DEVICE_TMP, detect_device_caps

from .base import CATEGORY_SUBPATHS, FileInfo, Source
from .device import connect_device, load_rsa_keys

_LS_LINE = re.compile(
    r"^[bcdlps-][rwxsStT-]{9}\s+\S+\s+\S+\s+\S+\s+(\d+)\s+(?:\S+\s+){2,3}(.+)$"
)
_UNIT = 1024.0
_LIST_CHUNK = 100  # file names per printf invocation (keeps shell commands short)
_EXIT_RE = re.compile(r"EXIT:(\d+)\s*$")


def _fmt_bytes(value: float) -> str:
    """Format a byte count or byte rate compactly."""
    if value >= _UNIT**3:
        return f"{value / _UNIT**3:.2f} GiB"
    if value >= _UNIT**2:
        return f"{value / _UNIT**2:.1f} MiB"
    if value >= _UNIT:
        return f"{value / _UNIT:.1f} KiB"
    return f"{value:.0f} B"


def write_device_listing(device, listing: str, names: Sequence[str]) -> None:
    """Write names to a device list file, chunked to keep commands short."""
    for start in range(0, len(names), _LIST_CHUNK):
        chunk = " ".join(
            shlex.quote(name) for name in names[start : start + _LIST_CHUNK]
        )
        op = ">" if start == 0 else ">>"
        device.shell(
            f"printf '%s\\n' {chunk} {op} {shlex.quote(listing)}",
            read_timeout_s=30,
            timeout_s=120,
        )


def rm_device_files(device, *paths: str, recursive: bool = False) -> None:
    """Best-effort removal of device scratch files; never raises."""
    if not paths:
        return
    flags = "rm -rf" if recursive else "rm -f"
    command = flags + " " + " ".join(shlex.quote(path) for path in paths)
    try:
        device.shell(command, read_timeout_s=30, timeout_s=60)
    except Exception:  # noqa: S110, BLE001 - best-effort cleanup; never mask the real error
        pass


def tar_on_device(
    device, pack: str, directory: str, listing: str, *, compress: bool = False
) -> None:
    """Create a device-side archive; raise if tar failed."""
    flags = "czf" if compress else "cf"
    command = (
        f"tar -{flags} {shlex.quote(pack)} -C {shlex.quote(directory)} "
        f"-T {shlex.quote(listing)} ; echo EXIT:$?"
    )
    output = device.shell(command, read_timeout_s=600, timeout_s=3600)
    match = _EXIT_RE.search(output.rstrip())
    if match is None or match.group(1) != "0":
        raise RuntimeError(f"device tar failed: {output.strip()[-300:] or 'no output'}")


def extract_pack(
    local_pack: Path,
    items: Sequence[tuple[FileInfo, Path]],
    *,
    compress: bool = False,
) -> list[tuple[FileInfo, Exception]]:
    """Extract a pulled archive into the per-file dest paths.

    Verifies every member exists and its size matches the size observed
    at listing time (catches files changed while the pack was built).
    """
    failures: list[tuple[FileInfo, Exception]] = []
    mode = "r:gz" if compress else "r:"
    with tarfile.open(local_pack, mode) as archive:
        members = {
            member.name: member for member in archive.getmembers() if member.isfile()
        }
        for info, dest in items:
            name = info.rel.split("/", 1)[1]
            try:
                member = members.get(name)
                if member is None:
                    raise FileNotFoundError(f"{name} missing from device archive")
                if member.size != info.size:
                    raise OSError(f"{name} size mismatch: {member.size} != {info.size}")
                source = archive.extractfile(member)
                if source is None:
                    raise OSError(f"{name} not extractable from device archive")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("wb") as target:
                    shutil.copyfileobj(source, target)
            except Exception as error:  # noqa: BLE001 - report and continue
                dest.unlink(missing_ok=True)
                failures.append((info, error))
    return failures


class PullProgress:
    """Accumulates adb_shell pull chunks and redraws a single progress line.

    adb_shell calls the callback as ``callback(device_path, chunk_bytes,
    total_bytes)``; the callback is responsible for accumulating bytes itself.

    ``position`` is an optional ``(done, total)`` file counter shown as a
    ``[done/total]`` prefix (已拉取/需拉取总量) when pulling file by file:
    ``done`` is how many files were already pulled before this one, ``total``
    how many files this run needs to pull.
    """

    def __init__(
        self,
        name: str,
        *,
        enabled: bool = True,
        stream=None,
        min_interval: float = 0.1,
        position: tuple[int, int] | None = None,
    ):
        self.name = name
        self.enabled = enabled
        self._stream = stream if stream is not None else sys.stderr
        self._min_interval = min_interval
        self._position = position
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
        prefix = ""
        if self._position is not None:
            done, total = self._position
            prefix = f"[{done}/{total}] "
        if self._total > 0:
            percent = min(100.0, self._received / self._total * 100.0)
            line = (
                f"{prefix}{self.name}  {percent:5.1f}%  "
                f"{_fmt_bytes(self._received)}/{_fmt_bytes(self._total)}  {_fmt_bytes(speed)}/s"
            )
        else:
            line = f"{prefix}{self.name}  {_fmt_bytes(self._received)}  {_fmt_bytes(speed)}/s"
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

    CATEGORY_SUBPATHS: ClassVar[dict[str, tuple[str, ...]]] = CATEGORY_SUBPATHS

    def __init__(
        self,
        host: str,
        port: int,
        location: str,
        *,
        progress: bool | None = None,
        batch: bool = True,
        compress: bool = False,
    ):
        from adb_shell.adb_device import AdbDeviceTcp

        self._device = AdbDeviceTcp(host=host, port=port)
        connect_device(
            self._device,
            load_rsa_keys(),
            auth_timeout_s=30,
            target=f"设备 {host}:{port}",
        )
        self.location = location.rstrip("/")
        self._ls_cache: dict[str, list[tuple[str, int]]] = {}
        self._show_progress = sys.stderr.isatty() if progress is None else progress
        self._batch = batch
        self._compress = compress
        if batch or compress:
            caps = detect_device_caps(self._device, probe_unzip=False)
            if batch and not caps.adb_batch_ok:
                print(
                    "warning: 设备不支持 tar 批量打包（tar -cf -C -T 探测失败），"
                    "已改为逐文件拉取",
                    file=sys.stderr,
                )
                self._batch = False
            if compress and not caps.compress_ok:
                print(
                    "warning: 设备 tar 不支持 gzip（tar -czf 探测失败），已禁用 --compress",
                    file=sys.stderr,
                )
                self._compress = False

    def dir_for(self, category: str) -> str:
        return "/".join((self.location, *self.CATEGORY_SUBPATHS[category]))

    def remote_path(self, rel: str) -> str:
        category, name = rel.split("/", 1)
        return f"{self.dir_for(category)}/{name}"

    def _ls(self, path: str) -> list[tuple[str, int]]:
        if path in self._ls_cache:
            return self._ls_cache[path]
        # path 来自用户配置 location（可含空格/元字符），与 apk_adb 一致使用 shlex.quote
        output = self._device.shell(f"ls -l {shlex.quote(path)}")
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

    def fetch_to(
        self,
        rel: str,
        dest: Path,
        *,
        position: tuple[int, int] | None = None,
    ) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        progress = PullProgress(rel, enabled=self._show_progress, position=position)
        try:
            self._device.pull(
                self.remote_path(rel),
                str(dest),
                progress_callback=progress,
                read_timeout_s=60,  # 与批量路径拉齐，避免单文件 pull 长挂
            )
        finally:
            progress.finish()

    def fetch_many(
        self, items: Sequence[tuple[FileInfo, Path]]
    ) -> list[tuple[FileInfo, Exception]]:
        """Fetch several files at once by packing them on the device.

        Each category becomes one tar archive on the device; the archive is
        pulled with a single sync-protocol transfer and extracted locally.
        Any file the pack path could not deliver (missing member, size
        mismatch, failed tar, failed pull) falls back to the per-file pull,
        so a batch failure never loses data.
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
        """Pull each file individually, showing a [done/total] file counter.

        ``position`` shows 已拉取/需拉取总量: how many files were already
        pulled before the current one, out of the total this run needs.
        """
        total = len(items)
        for index, (info, dest) in enumerate(items):
            try:
                self.fetch_to(
                    info.rel, dest, position=(index, total) if total > 1 else None
                )
            except Exception as error:  # noqa: BLE001 - report and continue
                failures.append((info, error))

    def _fetch_category_pack(
        self,
        category: str,
        items: Sequence[tuple[FileInfo, Path]],
    ) -> list[tuple[FileInfo, Exception]]:
        """Tar the listed files on the device, pull once, extract locally.

        Raises if the whole pack (tar or pull) failed; returns per-file
        failures otherwise.
        """
        names = [info.rel.split("/", 1)[1] for info, _ in items]
        directory = self.dir_for(category)
        unique = f"{category}_{os.getpid()}"
        pack = f"{_DEVICE_TMP}/arknights_ab_{unique}.tar"
        listing = f"{_DEVICE_TMP}/arknights_ab_{unique}.list"
        local_fd, local_pack = tempfile.mkstemp(
            suffix=".tar", prefix=f"arknights_ab_{category}_"
        )
        os.close(local_fd)
        local_pack = Path(local_pack)
        try:
            self._write_device_listing(listing, names)
            self._tar_on_device(pack, directory, listing)
            progress = PullProgress(
                f"{category} ({len(items)} files)", enabled=self._show_progress
            )
            try:
                self._device.pull(
                    pack, str(local_pack), progress_callback=progress, read_timeout_s=60
                )
            finally:
                progress.finish()
            return self._extract_pack(local_pack, items)
        finally:
            self._rm_device_files(pack, listing)
            local_pack.unlink(missing_ok=True)

    def _rm_device_files(self, *paths: str) -> None:
        return rm_device_files(self._device, *paths)

    def _write_device_listing(self, listing: str, names: Sequence[str]) -> None:
        return write_device_listing(self._device, listing, names)

    def _tar_on_device(self, pack: str, directory: str, listing: str) -> None:
        return tar_on_device(
            self._device, pack, directory, listing, compress=self._compress
        )

    def _extract_pack(
        self,
        local_pack: Path,
        items: Sequence[tuple[FileInfo, Path]],
    ) -> list[tuple[FileInfo, Exception]]:
        return extract_pack(local_pack, items, compress=self._compress)
