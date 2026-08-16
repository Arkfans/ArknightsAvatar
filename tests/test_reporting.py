"""Tests for the shared reporting helpers (version headers, game version, writes)."""

import hashlib
import json
from pathlib import Path

import pytest

from arknightsavatar import __version__, reporting


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 隔离 cwd：避免读到仓库根的真实 data/raw/manifest.json 与 config.toml
    monkeypatch.chdir(tmp_path)


def _write_raw_manifest(tmp_path: Path, game_version: str) -> Path:
    manifest = tmp_path / "data" / "raw" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"game_version": game_version, "files": {}}), encoding="utf8"
    )
    return manifest


def test_report_header_shape():
    header = reporting.report_header(game_version="g1", generated_at="t1")
    assert header == {
        "schema_version": 1,
        "pipeline_version": __version__,
        "game_version": "g1",
        "generated_at": "t1",
    }


def test_load_game_version_raw_manifest_wins(tmp_path: Path):
    manifest = _write_raw_manifest(tmp_path, "arknights-hg-2761")
    assert reporting.load_game_version(manifest) == "arknights-hg-2761"


def test_load_game_version_falls_back_to_unknown(tmp_path: Path):
    assert reporting.load_game_version(tmp_path / "missing.json") == "unknown"


def test_load_game_version_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ARKNIGHTSAVATAR_GAME_VERSION", "env-version")
    assert reporting.load_game_version(tmp_path / "missing.json") == "env-version"


def test_load_game_version_broken_manifest_falls_back(tmp_path: Path):
    manifest = tmp_path / "data" / "raw" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("not json", encoding="utf8")
    assert reporting.load_game_version(manifest) == "unknown"


def test_write_report_file_injects_header(tmp_path: Path):
    out = tmp_path / "out" / "report.json"
    reporting.write_report({"generated_at": "t0", "stats": {"a": 1}}, out)
    payload = json.loads(out.read_text(encoding="utf8"))
    assert payload["schema_version"] == 1
    assert payload["pipeline_version"] == __version__
    assert payload["game_version"] == "unknown"
    assert payload["generated_at"] == "t0"  # payload 的时间戳保留
    assert payload["stats"] == {"a": 1}
    # 头字段在前、顺序固定
    assert list(payload)[:4] == [
        "schema_version",
        "pipeline_version",
        "game_version",
        "generated_at",
    ]


def test_write_report_generated_at_defaults_to_now(tmp_path: Path):
    out = tmp_path / "stats.json"
    reporting.write_report({"stats": {}}, out)
    payload = json.loads(out.read_text(encoding="utf8"))
    assert payload["generated_at"]  # 无 payload 时间戳时由 header 生成


def test_write_report_stdout_dash(capsys: pytest.CaptureFixture):
    reporting.write_report({"generated_at": "t0"}, "-")
    payload = json.loads(capsys.readouterr().out)
    assert payload["generated_at"] == "t0"
    assert payload["schema_version"] == 1


def test_write_report_idempotent_keeps_old_timestamp_and_mtime(tmp_path: Path):
    out = tmp_path / "manifest.json"
    assert reporting.write_report(
        {"files": {"a": 1}, "generated_at": "t1"}, out, idempotent=True
    )
    mtime_ns = out.stat().st_mtime_ns
    assert (
        reporting.write_report(
            {"files": {"a": 1}, "generated_at": "t2"}, out, idempotent=True
        )
        is False
    )
    payload = json.loads(out.read_text(encoding="utf8"))
    assert payload["generated_at"] == "t1"  # 保留旧时间戳
    assert out.stat().st_mtime_ns == mtime_ns  # 文件未被重写


def test_write_report_idempotent_rewrites_on_content_change(tmp_path: Path):
    out = tmp_path / "manifest.json"
    reporting.write_report({"files": {"a": 1}}, out, idempotent=True)
    assert reporting.write_report({"files": {"a": 2}}, out, idempotent=True) is True
    payload = json.loads(out.read_text(encoding="utf8"))
    assert payload["files"] == {"a": 2}


def test_sha256_file(tmp_path: Path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"hello world")
    assert reporting.sha256_file(path) == hashlib.sha256(b"hello world").hexdigest()
