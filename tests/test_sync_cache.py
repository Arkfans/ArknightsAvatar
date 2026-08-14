import json
import shutil
import subprocess
from pathlib import Path

import pytest

from arknightsavatar import sync_cache


def _git_available() -> bool:
    result = subprocess.run(["git", "--version"], capture_output=True, text=True)
    return result.returncode == 0


pytestmark = pytest.mark.skipif(not _git_available(), reason="git CLI not available")


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    # clone 不继承源仓库的 user.name/user.email；用环境变量提供身份。
    monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")


def _run_git(cwd: Path, *argv: str) -> None:
    result = subprocess.run(["git", *argv], cwd=str(cwd), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _make_remote(remote: Path) -> None:
    remote.mkdir(parents=True, exist_ok=True)
    _run_git(remote, "init", "-b", "main")
    _run_git(remote, "config", "user.name", "test")
    _run_git(remote, "config", "user.email", "test@example.com")
    (remote / "README.md").write_text("data repo\n", encoding="utf8")
    _run_git(remote, "add", "-A")
    _run_git(remote, "commit", "-m", "init")


def _write_config(tmp_path: Path, url: str, categories: list[dict]) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        json.dumps({"data_repo": {"path": "data_cache", "url": url, "branch": "main",
                                  "categories": categories}}),
        encoding="utf8",
    )
    return config


def test_empty_url_without_working_copy(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = _write_config(tmp_path, "", [{"local": "data/recognition", "remote": "recognition"}])
    assert sync_cache.main(["--config", str(config)]) == 1
    assert "data_repo.url is empty" in capsys.readouterr().err


def test_sync_clones_mirrors_and_commits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    local_dir = tmp_path / "data" / "recognition"
    local_dir.mkdir(parents=True)
    (local_dir / "a.json").write_text('{"a": 1}', encoding="utf8")
    config = _write_config(tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}])

    assert sync_cache.main(["--config", str(config)]) == 0
    workdir = tmp_path / "data_cache"
    assert (workdir / ".git").is_dir()
    assert (workdir / "recognition" / "a.json").is_file()
    log = subprocess.run(["git", "-C", str(workdir), "log", "--oneline"],
                         capture_output=True, text=True)
    assert "sync" in log.stdout


def test_second_sync_without_changes_creates_no_commit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    local_dir = tmp_path / "data" / "recognition"
    local_dir.mkdir(parents=True)
    (local_dir / "a.json").write_text('{"a": 1}', encoding="utf8")
    config = _write_config(tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}])

    assert sync_cache.main(["--config", str(config)]) == 0
    workdir = tmp_path / "data_cache"
    count_before = subprocess.run(
        ["git", "-C", str(workdir), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert sync_cache.main(["--config", str(config)]) == 0
    count_after = subprocess.run(
        ["git", "-C", str(workdir), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    assert count_before == count_after


def test_sync_mirrors_updates_and_removals(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    local_dir = tmp_path / "data" / "recognition"
    local_dir.mkdir(parents=True)
    (local_dir / "a.json").write_text("v1", encoding="utf8")
    (local_dir / "b.json").write_text("b", encoding="utf8")
    config = _write_config(tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}])
    assert sync_cache.main(["--config", str(config)]) == 0

    (local_dir / "a.json").write_text("v2", encoding="utf8")
    (local_dir / "b.json").unlink()
    assert sync_cache.main(["--config", str(config)]) == 0
    workdir = tmp_path / "data_cache"
    assert (workdir / "recognition" / "a.json").read_text(encoding="utf8") == "v2"
    assert not (workdir / "recognition" / "b.json").exists()


def test_missing_local_source_leaves_remote_intact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    config = _write_config(
        tmp_path,
        str(remote),
        [
            {"local": "data/recognition", "remote": "recognition"},
            {"local": "data/missing_dir", "remote": "missing"},
        ],
    )
    (tmp_path / "data" / "recognition").mkdir(parents=True)
    assert sync_cache.main(["--config", str(config)]) == 0
    workdir = tmp_path / "data_cache"
    assert not (workdir / "missing").exists()
    assert (workdir / "recognition").is_dir()


def test_restore_copies_missing_files_down(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    local_dir = tmp_path / "data" / "recognition"
    local_dir.mkdir(parents=True)
    (local_dir / "a.json").write_text("a", encoding="utf8")
    config = _write_config(tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}])
    assert sync_cache.main(["--config", str(config)]) == 0

    # 本地删除后 --restore 从工作副本取回；不覆盖已存在文件
    (local_dir / "a.json").unlink()
    (tmp_path / "data_cache" / "recognition" / "extra.json").write_text("x", encoding="utf8")
    assert sync_cache.main(["--config", str(config), "--restore"]) == 0
    assert (local_dir / "a.json").read_text(encoding="utf8") == "a"
    assert (local_dir / "extra.json").read_text(encoding="utf8") == "x"


def test_dry_run_mirrors_but_does_not_commit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    local_dir = tmp_path / "data" / "recognition"
    local_dir.mkdir(parents=True)
    (local_dir / "a.json").write_text("a", encoding="utf8")
    config = _write_config(tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}])
    assert sync_cache.main(["--config", str(config), "--dry-run"]) == 0
    workdir = tmp_path / "data_cache"
    log = subprocess.run(["git", "-C", str(workdir), "log", "--oneline"],
                         capture_output=True, text=True)
    assert "sync" not in log.stdout
    status = subprocess.run(["git", "-C", str(workdir), "status", "--porcelain"],
                            capture_output=True, text=True)
    assert status.stdout.strip()  # 有已暂存未提交的变更


def test_sync_file_category(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "arknights_npc.json").write_text("{}", encoding="utf8")
    config = _write_config(
        tmp_path, str(remote), [{"local": "data/arknights_npc.json", "remote": "arknights_npc.json"}]
    )
    assert sync_cache.main(["--config", str(config)]) == 0
    assert (tmp_path / "data_cache" / "arknights_npc.json").is_file()
