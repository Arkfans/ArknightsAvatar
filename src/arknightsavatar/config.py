from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

GAME_LOCATIONS = {
    "official": "/storage/emulated/0/Android/data/com.hypergryph.arknights/files/Bundles",
    "bilibili": "/storage/emulated/0/Android/data/com.hypergryph.arknights.bilibili/files/Bundles",
}

CATEGORIES = ("characters", "avatars")

DEFAULT_CONFIG_FILE = "config.toml"
DEFAULT_DATA_REPO_CONFIG_FILE = "data_repo.yaml"
ENV_PREFIX = "ARKNIGHTSAVATAR_"

# 数据仓库（sync-cache）默认分类映射：本地路径 -> 数据仓库内路径。
# desc 为该分类的一句话说明，交互选择（setup 选项 b）与下载进度中展示。
DEFAULT_DATA_REPO_CATEGORIES = [
    {"local": "data/recognition", "remote": "recognition", "desc": "识别数据"},
    {"local": "data/unpacked/avatars", "remote": "avatars", "desc": "原始 avatar"},
    {"local": "data/export", "remote": "export", "desc": "提取 avatar"},
    {
        "local": "data/export_webp",
        "remote": "export_webp",
        "desc": "提取 avatar（WebP）",
    },
    {"local": "data/stats", "remote": "stats", "desc": "统计列表"},
    {
        "local": "data/arknights_npc.json",
        "remote": "arknights_npc.json",
        "desc": "NPC 头像索引",
    },
    {"local": "data/version.json", "remote": "version.json", "desc": "顶层版本指针"},
    {
        "local": "data/changelog.ndjson",
        "remote": "changelog.ndjson",
        "desc": "追加式变更日志",
    },
    {"local": "data/schema", "remote": "schema", "desc": "数据文件格式 Schema"},
]


@dataclass
class AdbConfig:
    host: str = "127.0.0.1"
    port: int = 5555
    server: str = "official"
    location: str = ""

    def resolved_location(self) -> str:
        return self.location or GAME_LOCATIONS.get(
            self.server, GAME_LOCATIONS["official"]
        )


@dataclass
class ApkConfig:
    file: Path | None = None
    dir: Path | None = None


@dataclass
class DataRepoCategory:
    local: str
    remote: str
    desc: str = ""  # 一句话说明，交互选择与下载进度中展示


@dataclass
class DataRepoConfig:
    """GitHub 数据仓库承载配置（sync-cache 使用）。

    ``url`` 为空表示数据仓库尚未创建/未配置，sync-cache 会给出友好提示；
    仓库创建后把地址填入 data_repo.yaml 的 ``url`` 即可使用。
    """

    path: str = "data_cache"
    url: str = ""
    branch: str = "main"
    categories: list[DataRepoCategory] = field(
        default_factory=lambda: [
            DataRepoCategory(**item) for item in DEFAULT_DATA_REPO_CATEGORIES
        ]
    )


@dataclass
class Config:
    adb: AdbConfig = field(default_factory=AdbConfig)
    apk: ApkConfig = field(default_factory=ApkConfig)
    data_repo: DataRepoConfig = field(default_factory=DataRepoConfig)
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


def _parse_data_repo(data: dict) -> DataRepoConfig:
    raw_categories = data.get("categories")
    if not isinstance(raw_categories, list) or not raw_categories:
        raw_categories = DEFAULT_DATA_REPO_CATEGORIES
    categories = []
    for item in raw_categories:
        if isinstance(item, dict) and item.get("local") and item.get("remote"):
            categories.append(
                DataRepoCategory(
                    local=str(item["local"]),
                    remote=str(item["remote"]),
                    desc=str(item.get("desc") or ""),
                )
            )
    return DataRepoConfig(
        path=str(data.get("path") or "data_cache"),
        url=str(data.get("url") or ""),
        branch=str(data.get("branch") or "main"),
        categories=categories
        or [DataRepoCategory(**item) for item in DEFAULT_DATA_REPO_CATEGORIES],
    )


def load_config(
    path: str | Path | None = None,
    data_repo_path: str | Path | None = None,
) -> Config:
    """Load the main TOML config (adb/apk) plus the data repo YAML config.

    ``path`` points at the main config file (default ``config.toml``);
    ``data_repo_path`` points at the data repo config (default
    ``data_repo.yaml`` next to the main config file). Either may be overridden
    through ``ARKNIGHTSAVATAR_CONFIG`` / ``ARKNIGHTSAVATAR_DATA_REPO_CONFIG``;
    a missing file falls back to built-in defaults.
    """
    path = Path(path or _env("CONFIG") or DEFAULT_CONFIG_FILE)
    data: dict = {}
    if path.exists():
        with path.open("rb") as f:
            data = tomllib.load(f)

    data_repo_path = Path(
        data_repo_path
        or _env("DATA_REPO_CONFIG")
        or path.parent / DEFAULT_DATA_REPO_CONFIG_FILE
    )
    data_repo_data: dict = {}
    if data_repo_path.exists():
        with data_repo_path.open("rt", encoding="utf8") as f:
            data_repo_data = yaml.safe_load(f) or {}

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
        file=Path(_env("APK_FILE"))
        if _env("APK_FILE")
        else (Path(apk_data["file"]) if apk_data.get("file") else None),
        dir=Path(_env("APK_DIR"))
        if _env("APK_DIR")
        else (Path(apk_data["dir"]) if apk_data.get("dir") else None),
    )

    data_repo = _parse_data_repo(data_repo_data)
    data_repo.path = _env("DATA_REPO_PATH") or data_repo.path
    data_repo.url = _env("DATA_REPO_URL") or data_repo.url
    data_repo.branch = _env("DATA_REPO_BRANCH") or data_repo.branch

    game_version = (
        _env("GAME_VERSION")
        or data.get("game_version")
        or infer_game_version(apk)
        or "unknown"
    )
    return Config(adb=adb, apk=apk, data_repo=data_repo, game_version=game_version)
