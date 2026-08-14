import os

from arknightsavatar.config import (
    DEFAULT_DATA_REPO_CATEGORIES,
    DataRepoConfig,
    load_config,
)


def test_env_prefix_is_arknightsavatar(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("adb: {host: 1.2.3.4, port: 9999}\n", encoding="utf8")
    monkeypatch.setenv("ARKNIGHTSAVATAR_ADB_HOST", "5.6.7.8")
    config = load_config(config_path)
    assert config.adb.host == "5.6.7.8"
    assert config.adb.port == 9999


def test_data_repo_defaults(tmp_path):
    config = load_config(tmp_path / "missing.yaml")
    assert config.data_repo.path == "data_cache"
    assert config.data_repo.url == ""
    assert config.data_repo.branch == "main"
    assert [item.local for item in config.data_repo.categories] == [
        item["local"] for item in DEFAULT_DATA_REPO_CATEGORIES
    ]
    assert [item.remote for item in config.data_repo.categories] == [
        item["remote"] for item in DEFAULT_DATA_REPO_CATEGORIES
    ]


def test_data_repo_from_yaml(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_repo:
  path: cache_dir
  url: https://github.com/me/arknightsavatar-data.git
  branch: trunk
  categories:
    - {local: data/recognition, remote: recognition}
""",
        encoding="utf8",
    )
    config = load_config(config_path)
    assert config.data_repo.path == "cache_dir"
    assert config.data_repo.url == "https://github.com/me/arknightsavatar-data.git"
    assert config.data_repo.branch == "trunk"
    assert len(config.data_repo.categories) == 1
    assert config.data_repo.categories[0].local == "data/recognition"
    assert config.data_repo.categories[0].remote == "recognition"


def test_data_repo_env_overrides(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("data_repo: {path: x, url: '', branch: main}\n", encoding="utf8")
    monkeypatch.setenv("ARKNIGHTSAVATAR_DATA_REPO_URL", "https://example.com/data.git")
    monkeypatch.setenv("ARKNIGHTSAVATAR_DATA_REPO_PATH", "elsewhere")
    monkeypatch.setenv("ARKNIGHTSAVATAR_DATA_REPO_BRANCH", "dev")
    config = load_config(config_path)
    assert config.data_repo.url == "https://example.com/data.git"
    assert config.data_repo.path == "elsewhere"
    assert config.data_repo.branch == "dev"


def test_data_repo_empty_categories_fall_back_to_defaults(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("data_repo: {categories: []}\n", encoding="utf8")
    config = load_config(config_path)
    assert len(config.data_repo.categories) == len(DEFAULT_DATA_REPO_CATEGORIES)


def test_data_repo_config_roundtrip():
    config = DataRepoConfig()
    assert config.path == "data_cache"
    assert config.url == ""
    assert config.categories
