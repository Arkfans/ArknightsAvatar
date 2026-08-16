"""Device-side command capability detection for the pull pipeline.

The fetch sources speed up transfers by working on the device:
``AdbSource`` packs files with ``tar -cf -C -T`` (optionally ``tar -czf``
when ``--compress`` is given), and ``ApkAdbSource`` reads AB entries out of
the installed APK with ``unzip -l`` / ``unzip -p`` before packing them with
tar. Stock Android provides these through toybox, but stripped ROMs or
unusual devices may lack them entirely or ship a tar built without gzip
support.

This standalone module probes a connected adb_shell device and reports which
of those commands actually work. Probes are functional (they really run the
command against scratch files under ``/data/local/tmp``) and defensive: any
failing or hanging shell call counts as "not supported" instead of raising,
so the pull scripts can fall back to per-file transfers.

CLI: ``arknightsavatar device-caps`` (also ``arknightsavatar-device-caps``)
prints a human-readable report for the device from ``config.toml``.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import load_config

_DEVICE_TMP = "/data/local/tmp"
_CAPS_PREFIX = "arknights_caps"
_OK = "CAPS:OK"

# Basic tools the pull paths rely on (toybox applets / mksh builtins on
# stock Android, probed for completeness)...
_BASIC_TOOLS = ("ls", "cat", "grep", "printf", "rm", "mkdir")
# ...plus the efficiency commands this module focuses on.
_ALL_TOOLS = (*_BASIC_TOOLS, "tar", "gzip", "unzip")


@dataclass(frozen=True)
class DeviceCaps:
    """Which pull-optimization commands work on the device.

    Every field defaults to False, so a device that cannot be probed (shell
    errors, timeouts, missing commands) simply reports nothing as available;
    the per-file fetch paths need no capabilities at all.
    """

    # basic shell tools
    ls: bool = False
    cat: bool = False
    grep: bool = False
    printf: bool = False
    rm: bool = False
    mkdir: bool = False
    # efficiency commands used by the pull scripts
    tar: bool = False  # `tar` command exists
    tar_list: bool = False  # `tar -cf -C -T` device-side packing works
    tar_gzip: bool = False  # `tar -czf` / `tar -tzf` gzip packing works
    gzip: bool = False  # `gzip` command exists
    unzip: bool = False  # `unzip` command exists
    unzip_list: bool = False  # `unzip -l` works
    unzip_pipe: bool = False  # `unzip -p` works

    @property
    def adb_batch_ok(self) -> bool:
        """The AdbSource batch path (device-side tar packing) is usable."""
        return self.tar and self.tar_list

    @property
    def apk_batch_ok(self) -> bool:
        """The ApkAdbSource batch path (unzip entries + tar pack) is usable."""
        return self.unzip and self.unzip_list and self.unzip_pipe and self.adb_batch_ok

    @property
    def compress_ok(self) -> bool:
        """``--compress`` (gzip the device-side pack) is usable."""
        return self.tar_gzip


def _shell(
    device, command: str, *, read_timeout_s: int = 30, timeout_s: int = 60
) -> str:
    """Run a device command; a broken/hanging probe means "not supported"."""
    try:
        output = device.shell(
            command, read_timeout_s=read_timeout_s, timeout_s=timeout_s
        )
    except Exception:  # noqa: BLE001 - defensive probing, never raise
        return ""
    return output or ""


def _best_effort_rm(device, *paths: str, recursive: bool = False) -> None:
    """Best-effort removal of device scratch files; never raises."""
    if not paths:
        return
    flags = "rm -rf" if recursive else "rm -f"
    _shell(device, flags + " " + " ".join(shlex.quote(path) for path in paths))


def _probe_commands(device) -> dict[str, bool]:
    """One shell round trip: which pull tools ``command -v`` finds."""
    checks = "; ".join(
        f'command -v {name} >/dev/null 2>&1 && echo "OK {name}"' for name in _ALL_TOOLS
    )
    output = _shell(device, checks)
    present = {line.strip() for line in output.splitlines()}
    return {name: f"OK {name}" in present for name in _ALL_TOOLS}


def _probe_tar(device, *, gzip: bool, device_tmp: str) -> bool:
    """Functionally verify device-side tar packing with a ``-T`` file list.

    Builds a scratch directory, packs one listed file with
    ``tar -cf -C -T`` (or ``tar -czf`` / ``tar -tzf`` when ``gzip``) and
    checks the archive lists the file back. Scratch is removed both on
    success (inside the chain) and on failure (``finally``).
    """
    tag = "gz" if gzip else "list"
    scratch = f"{device_tmp}/{_CAPS_PREFIX}_{tag}_{os.getpid()}"
    d = shlex.quote(scratch)
    pack_flags = "czf" if gzip else "cf"
    list_flags = "tzf" if gzip else "tf"
    command = (
        f"rm -rf {d}; mkdir -p {d} && printf x >{d}/f && printf 'f\\n' >{d}/l && "
        f"tar -{pack_flags} {d}/p.tar -C {d} -T {d}/l && "
        f"tar -{list_flags} {d}/p.tar | grep -qx f && "
        f"rm -rf {d} && echo {_OK}"
    )
    try:
        return _OK in _shell(device, command)
    finally:
        _best_effort_rm(device, scratch, recursive=True)


def _unzip_help_flags(device) -> tuple[bool, bool]:
    """Best-effort fallback: parse ``unzip -h`` usage text for -l/-p."""
    output = _shell(device, "unzip -h 2>&1")
    return "-l" in output, "-p" in output


def _probe_unzip(device, *, device_tmp: str) -> tuple[bool, bool]:
    """Functionally verify ``unzip -l`` and ``unzip -p``.

    A tiny host-built zip is pushed to the device (adb_shell sync protocol,
    same transport as ``device.pull``), then listed and piped; both steps
    must succeed. If push is unavailable, fall back to parsing the unzip
    usage text.
    """
    remote = f"{device_tmp}/{_CAPS_PREFIX}_unzip_{os.getpid()}.zip"
    local_fd, local_path = tempfile.mkstemp(suffix=".zip", prefix="arknights_caps_")
    os.close(local_fd)
    try:
        with zipfile.ZipFile(local_path, "w") as archive:
            archive.writestr("caps.txt", f"{_OK}\n")
        try:
            device.push(local_path, remote)
        except Exception:  # noqa: BLE001 - fall back to usage text
            return _unzip_help_flags(device)
        u = shlex.quote(remote)
        list_ok = _OK in _shell(device, f"unzip -l {u} >/dev/null 2>&1 && echo {_OK}")
        pipe_ok = _OK in _shell(
            device, f"unzip -p {u} caps.txt 2>/dev/null | grep -qx {_OK} && echo {_OK}"
        )
        return list_ok, pipe_ok
    finally:
        try:
            Path(local_path).unlink(missing_ok=True)
        except OSError:
            pass
        _best_effort_rm(device, remote)


def detect_device_caps(
    device,
    *,
    probe_tar: bool = True,
    probe_unzip: bool = True,
    device_tmp: str = _DEVICE_TMP,
) -> DeviceCaps:
    """Probe a connected adb_shell device for pull-pipeline command support.

    ``device`` must expose ``shell(command, ...)`` and, when the unzip probe
    is enabled, ``push(local_path, device_path)``. Probes are defensive:
    failures and timeouts count as "unsupported", never raising.

    ``probe_tar`` / ``probe_unzip`` let callers skip probes they do not need
    (e.g. the tar-only AdbSource skips the unzip push).
    """
    found = _probe_commands(device)
    caps: dict[str, bool] = {
        "ls": found["ls"],
        "cat": found["cat"],
        "grep": found["grep"],
        "printf": found["printf"],
        "rm": found["rm"],
        "mkdir": found["mkdir"],
        "tar": found["tar"],
        "gzip": found["gzip"],
        "unzip": found["unzip"],
    }
    if probe_tar and found["tar"]:
        caps["tar_list"] = _probe_tar(device, gzip=False, device_tmp=device_tmp)
        caps["tar_gzip"] = _probe_tar(device, gzip=True, device_tmp=device_tmp)
    if probe_unzip and found["unzip"]:
        caps["unzip_list"], caps["unzip_pipe"] = _probe_unzip(
            device, device_tmp=device_tmp
        )
    return DeviceCaps(**caps)


def render_caps_report(caps: DeviceCaps) -> str:
    """Human-readable capability report (used by the CLI)."""

    def flag(value: bool) -> str:
        return "yes" if value else "no"

    def status(value: bool) -> str:
        return "available" if value else "unavailable"

    basic = "  ".join(f"{name}={flag(getattr(caps, name))}" for name in _BASIC_TOOLS)
    return "\n".join(
        [
            "device capability report:",
            f"  basic tools:  {basic}",
            (
                f"  tar:          present={flag(caps.tar)}  "
                f"pack(tar -cf -C -T)={flag(caps.tar_list)}  gzip(tar -czf)={flag(caps.tar_gzip)}"
            ),
            (
                f"  unzip:        present={flag(caps.unzip)}  "
                f"list(unzip -l)={flag(caps.unzip_list)}  pipe(unzip -p)={flag(caps.unzip_pipe)}"
            ),
            f"  gzip:         present={flag(caps.gzip)}",
            f"  adb batch (device-side tar packing):  {status(caps.adb_batch_ok)}",
            f"  apk batch (device-side unzip + tar):  {status(caps.apk_batch_ok)}",
            f"  --compress (gzip pack):               {status(caps.compress_ok)}",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-device-caps",
        description=(
            "Probe an ADB device for the commands the pull pipeline uses to "
            "speed up fetching (tar packing, tar gzip, unzip)."
        ),
    )
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument(
        "--adb-key",
        help=(
            "Path to the adb RSA private key (public key must be '<path>.pub'); "
            "defaults to ~/.android/adbkey, generating one if missing."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from .sources.device import connect_device, load_rsa_keys

        config = load_config(args.config)
        from adb_shell.adb_device import AdbDeviceTcp

        device = AdbDeviceTcp(host=config.adb.host, port=config.adb.port)
        rsa_keys = load_rsa_keys(args.adb_key)
        connect_device(
            device,
            rsa_keys,
            auth_timeout_s=30,
            target=f"设备 {config.adb.host}:{config.adb.port}",
        )
        caps = detect_device_caps(device)
        print(f"connected: {config.adb.host}:{config.adb.port}")
        print(render_caps_report(caps))
        return 0
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
