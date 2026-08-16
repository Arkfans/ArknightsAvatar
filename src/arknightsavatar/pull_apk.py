from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .config import load_config
from .sources.adb import _PullProgress
from .sources.device import (
    connect_device,
    installed_apk_paths,
    installed_version,
    load_rsa_keys,
    package_from_location,
)
from .util import sha256_file


def version_stem(version: dict[str, str]) -> str:
    """Build the version token used in file names, e.g. versionName 2.7.61 -> 2761."""
    digits = re.sub(r"\D", "", version.get("versionName", ""))
    if digits:
        return digits
    return version.get("versionCode", "unknown")


def pull_apk(device, remote_path: str, dest: Path, *, progress: bool = True) -> None:
    """Pull a device APK to dest with a progress line.

    Uses the adb sync protocol first (like `adb pull`); falls back to
    `shell cat` when direct file reads are denied.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    bar = _PullProgress(dest.name, enabled=progress)
    try:
        try:
            device.pull(remote_path, str(dest), progress_callback=bar)
        except Exception as error:  # noqa: BLE001
            print(
                f"\nsync pull failed ({type(error).__name__}: {error}); falling back to shell cat",
                file=sys.stderr,
            )
            dest.write_bytes(device.shell(f"cat {remote_path}", decode=False))
    finally:
        bar.finish()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar-pull-apk",
        description="Pull the installed Arknights APK from the device over ADB.",
    )
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument(
        "--package",
        help=(
            "Android package name; defaults to the package derived from the "
            "configured game location (official/bilibili)."
        ),
    )
    parser.add_argument("--out", default="apk", help="Output directory (default: apk)")
    parser.add_argument(
        "--adb-key",
        help=(
            "Path to the adb RSA private key (public key must be '<path>.pub'); "
            "defaults to ~/.android/adbkey, generating one if missing."
        ),
    )
    parser.add_argument(
        "--no-pull",
        action="store_true",
        help="Only probe the device (pm path + version), do not pull.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        package = args.package or package_from_location(config.adb.resolved_location())
        out_dir = Path(args.out)

        from adb_shell.adb_device import AdbDeviceTcp

        device = AdbDeviceTcp(host=config.adb.host, port=config.adb.port)
        rsa_keys = load_rsa_keys(args.adb_key)
        # auth_timeout_s gives the user time to accept the device's
        # "allow debugging" prompt on first-time connections.
        connect_device(
            device,
            rsa_keys,
            auth_timeout_s=30,
            target=f"设备 {config.adb.host}:{config.adb.port}",
        )
        print(f"connected: {config.adb.host}:{config.adb.port}  package={package}")

        paths = installed_apk_paths(device, package)
        if not paths:
            print(f"error: package not installed: {package}", file=sys.stderr)
            return 1
        print(f"pm path ({len(paths)}):")
        for path in paths:
            print(f"  {path}")

        version = installed_version(device, package)
        for key, value in version.items():
            print(f"  {key}={value}")

        if args.no_pull:
            return 0

        # Pull the base APK; prefer the path whose name contains 'base'.
        remote = next((p for p in paths if p.endswith("base.apk")), paths[0])
        dest = out_dir / f"arknights-hg-{version_stem(version)}.apk"
        part = dest.with_name(dest.name + ".part")

        pull_apk(device, remote, part)
        digest = sha256_file(part)
        size = part.stat().st_size

        if dest.exists():
            old_digest = sha256_file(dest)
            same = old_digest == digest
            print(f"pulled: {dest}  ({size} bytes)")
            print(f"sha256: {digest}")
            print(f"existing sha256: {old_digest}")
            print(
                "result: identical to existing local APK"
                if same
                else "result: DIFFERENT from existing local APK"
            )
            if not same:
                os.replace(part, dest)
                print(f"replaced {dest}")
            else:
                part.unlink(missing_ok=True)
        else:
            os.replace(part, dest)
            print(f"pulled: {dest}  ({size} bytes)")
            print(f"sha256: {digest}")
        return 0
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
