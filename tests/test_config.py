from pathlib import Path

import pytest

from arknightsavatar.config import (
    DEFAULT_DATA_REPO_CATEGORIES,
    ConfigError,
    load_config,
)


def test_env_prefix_is_arknightsavatar(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[adb]\nhost = "1.2.3.4"\nport = 9999\n', encoding="utf8")
    monkeypatch.setenv("ARKNIGHTSAVATAR_ADB_HOST", "5.6.7.8")
    config = load_config(config_path)
    assert config.adb.host == "5.6.7.8"
    assert config.adb.port == 9999


def test_adb_apk_from_toml(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
# 顶层键须位于任何表头之前
game_version = "2.7.61"

[adb]
host = "10.0.0.1"
port = 16448

[adb.game]
server = "bilibili"
location = "/custom/location"

[apk]
file = "apk/game.apk"
dir = "apk/game"
""",
        encoding="utf8",
    )
    config = load_config(config_path)
    assert config.adb.host == "10.0.0.1"
    assert config.adb.port == 16448
    assert config.adb.server == "bilibili"
    assert config.adb.location == "/custom/location"
    assert config.adb.resolved_location() == "/custom/location"
    assert config.apk.file == Path("apk/game.apk")
    assert config.apk.dir == Path("apk/game")
    assert config.game_version == "2.7.61"


def test_adb_game_default_location(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[adb]\nhost = "1.2.3.4"\n[adb.game]\nserver = "official"\n', encoding="utf8"
    )
    config = load_config(config_path)
    assert config.adb.location == ""
    assert config.adb.resolved_location() == (
        "/storage/emulated/0/Android/data/com.hypergryph.arknights/files/Bundles"
    )


def test_data_repo_defaults(tmp_path):
    config = load_config(tmp_path / "missing.toml")
    assert config.data_repo.path == "data_cache"
    assert config.data_repo.url == ""
    assert config.data_repo.branch == "main"
    assert [item.local for item in config.data_repo.categories] == [
        item["local"] for item in DEFAULT_DATA_REPO_CATEGORIES
    ]
    assert [item.remote for item in config.data_repo.categories] == [
        item["remote"] for item in DEFAULT_DATA_REPO_CATEGORIES
    ]


def test_sync_cache_method_defaults_to_git(tmp_path):
    assert load_config(tmp_path / "missing.toml").sync_cache.method == "git"


def test_sync_cache_method_from_toml(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[sync_cache]\nmethod = 'gh'\n", encoding="utf8")
    assert load_config(config_path).sync_cache.method == "gh"


@pytest.mark.parametrize("method", ["api", "Git", ""])
def test_invalid_sync_cache_method_raises_config_error(tmp_path, method):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"[sync_cache]\nmethod = {method!r}\n", encoding="utf8"
    )
    with pytest.raises(ConfigError, match="sync_cache.method"):
        load_config(config_path)


def test_data_repo_from_yaml_next_to_config(tmp_path):
    repo_path = tmp_path / "data_repo.yaml"
    repo_path.write_text(
        """
path: cache_dir
url: https://github.com/me/arknightsavatar-data.git
branch: trunk
categories:
  - {local: data/recognition, remote: recognition, desc: 识别数据}
""",
        encoding="utf8",
    )
    config = load_config(tmp_path / "config.toml")
    assert config.data_repo.path == "cache_dir"
    assert config.data_repo.url == "https://github.com/me/arknightsavatar-data.git"
    assert config.data_repo.branch == "trunk"
    assert len(config.data_repo.categories) == 1
    assert config.data_repo.categories[0].local == "data/recognition"
    assert config.data_repo.categories[0].remote == "recognition"
    assert config.data_repo.categories[0].desc == "识别数据"


def test_data_repo_category_desc_defaults_to_empty(tmp_path):
    repo_path = tmp_path / "data_repo.yaml"
    repo_path.write_text(
        "categories:\n  - {local: data/recognition, remote: recognition}\n",
        encoding="utf8",
    )
    config = load_config(tmp_path / "config.toml")
    assert config.data_repo.categories[0].desc == ""


def test_data_repo_default_categories_have_descriptions(tmp_path):
    config = load_config(tmp_path / "missing.toml")
    assert all(item["desc"] for item in DEFAULT_DATA_REPO_CATEGORIES)
    assert all(category.desc for category in config.data_repo.categories)


def test_data_repo_env_overrides(tmp_path, monkeypatch):
    repo_path = tmp_path / "data_repo.yaml"
    repo_path.write_text("path: x\nurl: ''\nbranch: main\n", encoding="utf8")
    monkeypatch.setenv("ARKNIGHTSAVATAR_DATA_REPO_URL", "https://example.com/data.git")
    monkeypatch.setenv("ARKNIGHTSAVATAR_DATA_REPO_PATH", "elsewhere")
    monkeypatch.setenv("ARKNIGHTSAVATAR_DATA_REPO_BRANCH", "dev")
    config = load_config(tmp_path / "config.toml")
    assert config.data_repo.url == "https://example.com/data.git"
    assert config.data_repo.path == "elsewhere"
    assert config.data_repo.branch == "dev"


def test_data_repo_config_env_override(tmp_path, monkeypatch):
    repo_path = tmp_path / "custom_repo.yaml"
    repo_path.write_text(
        "path: elsewhere\nurl: https://example.com/data.git\n", encoding="utf8"
    )
    monkeypatch.setenv("ARKNIGHTSAVATAR_DATA_REPO_CONFIG", str(repo_path))
    config = load_config(tmp_path / "config.toml")
    assert config.data_repo.path == "elsewhere"
    assert config.data_repo.url == "https://example.com/data.git"


def test_data_repo_path_param(tmp_path):
    repo_path = tmp_path / "custom_repo.yaml"
    repo_path.write_text("path: param_dir\n", encoding="utf8")
    config = load_config(tmp_path / "config.toml", data_repo_path=repo_path)
    assert config.data_repo.path == "param_dir"


def test_data_repo_empty_categories_fall_back_to_defaults(tmp_path):
    repo_path = tmp_path / "data_repo.yaml"
    repo_path.write_text("categories: []\n", encoding="utf8")
    config = load_config(tmp_path / "config.toml")
    assert len(config.data_repo.categories) == len(DEFAULT_DATA_REPO_CATEGORIES)


@pytest.mark.parametrize("raw", ["abc", "12.5", "0", "65536", "-1"])
def test_invalid_adb_port_env_raises_config_error(tmp_path, monkeypatch, raw):
    monkeypatch.setenv("ARKNIGHTSAVATAR_ADB_PORT", raw)
    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path / "config.toml")
    assert "ADB_PORT" in str(exc_info.value)
    assert str(raw) in str(exc_info.value)


def test_empty_adb_port_env_falls_back_to_default(tmp_path, monkeypatch):
    # 空环境变量与未设置等价（与旧 `"" or default` 行为一致），不应报错
    monkeypatch.setenv("ARKNIGHTSAVATAR_ADB_PORT", "")
    assert load_config(tmp_path / "config.toml").adb.port == 5555


@pytest.mark.parametrize("raw", ["abc", 0, 65536, -1])
def test_invalid_adb_port_config_raises_config_error(tmp_path, raw):
    config_path = tmp_path / "config.toml"
    config_path.write_text(f"[adb]\nport = {raw!r}\n", encoding="utf8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_path)
    assert "adb.port" in str(exc_info.value)


def test_valid_adb_port_bounds_accepted(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[adb]\nport = 1\n", encoding="utf8")
    assert load_config(config_path).adb.port == 1
    config_path.write_text("[adb]\nport = 65535\n", encoding="utf8")
    assert load_config(config_path).adb.port == 65535
