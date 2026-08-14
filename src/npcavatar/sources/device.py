from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_PACKAGE = "com.hypergryph.arknights"

_PACKAGE_FROM_LOCATION = re.compile(r"/Android/data/([^/]+)/files/Bundles/?$")


def package_from_location(location: str) -> str:
    """Derive the Android package name from a game data location."""
    match = _PACKAGE_FROM_LOCATION.search(location.replace("\\", "/"))
    if match:
        return match.group(1)
    return DEFAULT_PACKAGE


def load_rsa_keys(key_path: str | None = None) -> list:
    """Return an adb RSA signer list for device authentication."""
    from adb_shell.auth.keygen import keygen
    from adb_shell.auth.sign_pythonrsa import PythonRSASigner

    path = Path(key_path) if key_path else Path.home() / ".android" / "adbkey"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        keygen(str(path))
        print(f"generated adb key: {path} (authorize it once on the device)", file=sys.stderr)
    return [PythonRSASigner.FromRSAKeyPath(str(path))]


def installed_apk_paths(device, package: str) -> list[str]:
    """Run `pm path <package>` and return the device APK paths (base + splits)."""
    output = device.shell(f"pm path {package}")
    paths = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            paths.append(line[len("package:") :])
    return paths


def installed_version(device, package: str) -> dict[str, str]:
    """Return {'versionName': ..., 'versionCode': ...} parsed from dumpsys."""
    output = device.shell(f"dumpsys package {package} | grep -E 'version(Name|Code)='")
    result: dict[str, str] = {}
    for line in output.splitlines():
        for key in ("versionName", "versionCode"):
            marker = f"{key}="
            if marker in line:
                result[key] = line.split(marker, 1)[1].strip()
    return result
