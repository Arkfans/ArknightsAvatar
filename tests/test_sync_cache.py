import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from arknightsavatar import sync_cache


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_manifest(dir_path: Path, files: dict[str, tuple[int, str]]) -> None:
    """Write a category manifest with the given rel -> (size, sha256)."""
    manifest = dir_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pipeline_version": "0.1.0",
                "game_version": "unknown",
                "generated_at": "2026-01-01T00:00:00+00:00",
                "category": "recognition",
                "files": {
                    rel: {"size": size, "sha256": sha}
                    for rel, (size, sha) in files.items()
                },
            }
        ),
        encoding="utf8",
    )


def _git_available() -> bool:
    result = subprocess.run(
        ["git", "--version"], capture_output=True, text=True, check=False
    )
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
    result = subprocess.run(
        ["git", *argv], cwd=str(cwd), capture_output=True, text=True, check=False
    )
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
    config = tmp_path / "config.toml"
    config.write_text("# main config\n", encoding="utf8")
    repo = tmp_path / "data_repo.yaml"
    # JSON 是合法 YAML，data_repo 配置与主配置同目录（load_config 的默认查找位置）。
    repo.write_text(
        json.dumps(
            {
                "path": "data_cache",
                "url": url,
                "branch": "main",
                "categories": categories,
            }
        ),
        encoding="utf8",
    )
    return config


def test_empty_url_without_working_copy(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = _write_config(
        tmp_path, "", [{"local": "data/recognition", "remote": "recognition"}]
    )
    assert sync_cache.main(["--config", str(config)]) == 1
    assert "data_repo.url is empty" in capsys.readouterr().err


def test_sync_clones_mirrors_and_commits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    local_dir = tmp_path / "data" / "recognition"
    local_dir.mkdir(parents=True)
    (local_dir / "a.json").write_text('{"a": 1}', encoding="utf8")
    config = _write_config(
        tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}]
    )

    assert sync_cache.main(["--config", str(config)]) == 0
    workdir = tmp_path / "data_cache"
    assert (workdir / ".git").is_dir()
    assert (workdir / "recognition" / "a.json").is_file()
    log = subprocess.run(
        ["git", "-C", str(workdir), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "sync" in log.stdout


def test_second_sync_without_changes_creates_no_commit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    local_dir = tmp_path / "data" / "recognition"
    local_dir.mkdir(parents=True)
    (local_dir / "a.json").write_text('{"a": 1}', encoding="utf8")
    config = _write_config(
        tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}]
    )

    assert sync_cache.main(["--config", str(config)]) == 0
    workdir = tmp_path / "data_cache"
    count_before = subprocess.run(
        ["git", "-C", str(workdir), "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert sync_cache.main(["--config", str(config)]) == 0
    count_after = subprocess.run(
        ["git", "-C", str(workdir), "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
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
    config = _write_config(
        tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}]
    )
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
    config = _write_config(
        tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}]
    )
    assert sync_cache.main(["--config", str(config)]) == 0

    # 本地删除后 --restore 从工作副本取回；不覆盖已存在文件
    (local_dir / "a.json").unlink()
    (tmp_path / "data_cache" / "recognition" / "extra.json").write_text(
        "x", encoding="utf8"
    )
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
    config = _write_config(
        tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}]
    )
    assert sync_cache.main(["--config", str(config), "--dry-run"]) == 0
    workdir = tmp_path / "data_cache"
    log = subprocess.run(
        ["git", "-C", str(workdir), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "sync" not in log.stdout
    status = subprocess.run(
        ["git", "-C", str(workdir), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert status.stdout.strip()  # 有已暂存未提交的变更


def test_sync_file_category(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "arknights_npc.json").write_text("{}", encoding="utf8")
    config = _write_config(
        tmp_path,
        str(remote),
        [{"local": "data/arknights_npc.json", "remote": "arknights_npc.json"}],
    )
    assert sync_cache.main(["--config", str(config)]) == 0
    assert (tmp_path / "data_cache" / "arknights_npc.json").is_file()


def test_manifest_covered_unchanged_content_not_copied(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    local_dir = tmp_path / "data" / "recognition"
    local_dir.mkdir(parents=True)
    (local_dir / "a.json").write_text("v1", encoding="utf8")
    _write_manifest(local_dir, {"a.json": (2, _sha256_bytes(b"v1"))})
    config = _write_config(
        tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}]
    )
    assert sync_cache.main(["--config", str(config)]) == 0
    # 内容不变，仅 mtime 变化（本地与工作副本不同）：manifest 指纹相等 → 不复制
    mtime = 1700000000  # seconds
    os.utime(local_dir / "a.json", (mtime, mtime))
    os.utime(tmp_path / "data_cache" / "recognition" / "a.json", (mtime + 1, mtime + 1))
    assert sync_cache.main(["--config", str(config)]) == 0
    assert "copied=0 removed=0" in capsys.readouterr().out


def test_manifest_content_change_copied_after_regeneration(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    local_dir = tmp_path / "data" / "recognition"
    local_dir.mkdir(parents=True)
    (local_dir / "a.json").write_text("v1", encoding="utf8")
    _write_manifest(local_dir, {"a.json": (2, _sha256_bytes(b"v1"))})
    config = _write_config(
        tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}]
    )
    assert sync_cache.main(["--config", str(config)]) == 0
    # 内容变化并重新生成 manifest（真实流程：先 manifest 后 sync）→ 必复制
    (local_dir / "a.json").write_text("v2", encoding="utf8")
    _write_manifest(local_dir, {"a.json": (2, _sha256_bytes(b"v2"))})
    assert sync_cache.main(["--config", str(config)]) == 0
    # a.json（指纹变化）+ manifest.json（自身重写后 mtime 变化）→ 恰好 2 个
    assert "copied=2" in capsys.readouterr().out
    assert (tmp_path / "data_cache" / "recognition" / "a.json").read_text(
        encoding="utf8"
    ) == "v2"


def test_content_hash_catches_change_with_stale_manifest(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    local_dir = tmp_path / "data" / "recognition"
    local_dir.mkdir(parents=True)
    (local_dir / "a.json").write_text("v1", encoding="utf8")
    _write_manifest(local_dir, {"a.json": (2, _sha256_bytes(b"v1"))})
    config = _write_config(
        tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}]
    )
    assert sync_cache.main(["--config", str(config)]) == 0
    # 同 size 内容变化但 manifest 未重生成：默认（auto）按旧指纹判定相同 → 不复制
    (local_dir / "a.json").write_text("v2", encoding="utf8")
    assert sync_cache.main(["--config", str(config)]) == 0
    assert "copied=0" in capsys.readouterr().out
    # --content-hash 全量哈希 → 必复制
    assert sync_cache.main(["--config", str(config), "--content-hash"]) == 0
    assert "copied=1" in capsys.readouterr().out
    assert (tmp_path / "data_cache" / "recognition" / "a.json").read_text(
        encoding="utf8"
    ) == "v2"


def test_size_mtime_mode_ignores_manifest(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    local_dir = tmp_path / "data" / "recognition"
    local_dir.mkdir(parents=True)
    (local_dir / "a.json").write_text("v1", encoding="utf8")
    _write_manifest(local_dir, {"a.json": (2, _sha256_bytes(b"v1"))})
    config = _write_config(
        tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}]
    )
    assert sync_cache.main(["--config", str(config)]) == 0
    # 本地 mtime 变化（内容不变）：--size-mtime 恢复旧行为 → 复制
    os.utime(local_dir / "a.json", (1700000000, 1700000000))
    assert sync_cache.main(["--config", str(config), "--size-mtime"]) == 0
    assert "copied=1" in capsys.readouterr().out
