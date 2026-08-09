from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

GAME_LOCATIONS = {
    "official": "/storage/emulated/0/Android/data/com.hypergryph.arknights/files/Bundles",
    "bilibili": "/storage/emulated/0/Android/data/com.hypergryph.arknights.bilibili/files/Bundles",
}

CATEGORIES = ("characters", "chararts", "skins", "avatars")

DEFAULT_CONFIG_FILE = "config.yaml"
ENV_PREFIX = "NPCAVATAR_"


@dataclass
class AdbConfig:
    host: str = "127.0.0.1"
    port: int = 5555
    server: str = "official"
    location: str = ""

    def resolved_location(self) -> str:
        return self.location or GAME_LOCATIONS.get(self.server, GAME_LOCATIONS["official"])


@dataclass
class ApkConfig:
    file: Path | None = None
    dir: Path | None = None


@dataclass
class Config:
    adb: AdbConfig = field(default_factory=AdbConfig)
    apk: ApkConfig = field(default_factory=ApkConfig)
    game_version: str = "unknown"


def _env(name: str) -> str | None:
    return os.environ.get(ENV_PREFIX + name)


def infer_game_version(apk: ApkConfig) -> str:
    candidates = []
    if apk.file:
        candidates.append(apk.file)
    if apk.dir:
        candidates.append(apk.dir)
    for candidate in candidates:
        stem = Path(candidate).stem
        if stem and not stem.startswith("."):
            return stem
    return ""


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path or _env("CONFIG") or DEFAULT_CONFIG_FILE)
    data: dict = {}
    if path.exists():
        with path.open("rt", encoding="utf8") as f:
            data = yaml.safe_load(f) or {}

    adb_data = data.get("adb") or {}
    game_data = adb_data.get("game") or {}
    apk_data = data.get("apk") or {}

    server = _env("ADB_GAME_SERVER") or game_data.get("server", "official")
    adb = AdbConfig(
        host=_env("ADB_HOST") or adb_data.get("host", "127.0.0.1"),
        port=int(_env("ADB_PORT") or adb_data.get("port", 5555)),
        server=server,
        location=_env("ADB_GAME_LOCATION") or game_data.get("location", ""),
    )

    apk = ApkConfig(
        file=Path(_env("APK_FILE")) if _env("APK_FILE") else (Path(apk_data["file"]) if apk_data.get("file") else None),
        dir=Path(_env("APK_DIR")) if _env("APK_DIR") else (Path(apk_data["dir"]) if apk_data.get("dir") else None),
    )

    game_version = _env("GAME_VERSION") or data.get("game_version") or infer_game_version(apk) or "unknown"
    return Config(adb=adb, apk=apk, game_version=game_version)
