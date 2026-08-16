"""Tests for the manifest tool (manifests, version.json, changes, changelog, CSV)."""

import json
import shutil
from pathlib import Path

import pytest

from arknightsavatar import manifest_tool


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)


def _make_tree(tmp_path: Path) -> None:
    """Standard data tree: export/export_webp/recognition/stats + arknights_npc.json."""
    for name in ("export", "export_webp", "recognition", "stats"):
        (tmp_path / "data" / name).mkdir(parents=True)
    (tmp_path / "data" / "recognition" / "face_detect_vis").mkdir()
    (tmp_path / "data" / "recognition" / "derive").mkdir()
    (tmp_path / "data" / "export" / "a.png").write_bytes(b"png-a")
    (tmp_path / "data" / "export" / "b.png").write_bytes(b"png-b")
    (tmp_path / "data" / "export_webp" / "a.webp").write_bytes(b"webp-a")
    (tmp_path / "data" / "recognition" / "r.json").write_text(
        '{"a": 1}', encoding="utf8"
    )
    (tmp_path / "data" / "recognition" / "face_detect_vis" / "v.png").write_bytes(b"v")
    (tmp_path / "data" / "recognition" / "derive" / "model.json").write_text(
        "{}", encoding="utf8"
    )
    (tmp_path / "data" / "stats" / "s.json").write_text("{}", encoding="utf8")
    (tmp_path / "data" / "stats" / "run_stats.json").write_text(
        '{"generated_at": "t"}', encoding="utf8"
    )
    (tmp_path / "data" / "arknights_npc.json").write_text("{}", encoding="utf8")


def test_scan_category_excludes_self_and_vis(tmp_path: Path):
    _make_tree(tmp_path)
    files = manifest_tool.scan_category(
        "data/recognition", {}, excludes=["face_detect_vis"]
    )
    assert set(files) == {"r.json", "derive/model.json"}
    assert files["r.json"]["sha256"]  # 非空指纹
    # 清单自排除
    files2 = manifest_tool.scan_category("data/export", {})
    assert set(files2) == {"a.png", "b.png"}


def test_generate_manifest_writes_header_and_is_idempotent(tmp_path: Path):
    _make_tree(tmp_path)
    result = manifest_tool.generate_manifest("export")
    assert set(result["files"]) == {"a.png", "b.png"}
    manifest = json.loads(
        (tmp_path / "data" / "export" / "manifest.json").read_text(encoding="utf8")
    )
    assert manifest["schema_version"] == 1
    assert manifest["category"] == "export"
    assert manifest["pipeline_version"]
    mtime_ns = (tmp_path / "data" / "export" / "manifest.json").stat().st_mtime_ns
    # 幂等：内容未变不重写
    manifest_tool.generate_manifest("export")
    assert (
        tmp_path / "data" / "export" / "manifest.json"
    ).stat().st_mtime_ns == mtime_ns
    # 内容变化后重写
    (tmp_path / "data" / "export" / "c.png").write_bytes(b"png-c")
    manifest_tool.generate_manifest("export")
    manifest = json.loads(
        (tmp_path / "data" / "export" / "manifest.json").read_text(encoding="utf8")
    )
    assert set(manifest["files"]) == {"a.png", "b.png", "c.png"}


def test_generate_manifest_missing_dir_returns_none(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    assert manifest_tool.generate_manifest("export") is None
    assert "missing" in capsys.readouterr().err
    assert not (tmp_path / "data" / "export" / "manifest.json").exists()


def test_compare_manifests():
    old = {"a": {"sha256": "1"}, "b": {"sha256": "2"}, "c": {"sha256": "3"}}
    new = {"b": {"sha256": "2"}, "c": {"sha256": "9"}, "d": {"sha256": "4"}}
    diff = manifest_tool.compare_manifests(old, new)
    assert diff["added"] == ["d"]
    assert diff["removed"] == ["a"]
    assert diff["modified"] == ["c"]
    assert diff["counts"] == {"added": 1, "removed": 1, "modified": 1, "unchanged": 1}


def test_main_full_flow_version_out(tmp_path: Path):
    _make_tree(tmp_path)
    assert manifest_tool.main(["--version-out"]) == 0
    # 四个 manifest
    export = json.loads(
        (tmp_path / "data" / "export" / "manifest.json").read_text(encoding="utf8")
    )
    assert set(export["files"]) == {"a.png", "b.png"}
    rec = json.loads(
        (tmp_path / "data" / "recognition" / "manifest.json").read_text(encoding="utf8")
    )
    assert set(rec["files"]) == {"r.json", "derive/model.json"}  # vis 排除、derive 包含
    stats = json.loads(
        (tmp_path / "data" / "stats" / "manifest.json").read_text(encoding="utf8")
    )
    assert set(stats["files"]) == {"s.json"}
    # version.json
    version = json.loads(
        (tmp_path / "data" / "version.json").read_text(encoding="utf8")
    )
    assert version["schema_version"] == 1
    assert version["game_version"] == "unknown"
    assert version["categories"]["export"]["path"] == "export/manifest.json"
    assert version["categories"]["export"]["files"] == 2
    assert version["categories"]["export"][
        "sha256"
    ] == manifest_tool.reporting.sha256_file(
        tmp_path / "data" / "export" / "manifest.json"
    )
    assert version["categories"]["recognition"]["files"] == 2
    assert "arknights_npc.json" in version["categories"]
    # 幂等：再次运行不产生任何重写
    export_mtime = (tmp_path / "data" / "export" / "manifest.json").stat().st_mtime_ns
    version_mtime = (tmp_path / "data" / "version.json").stat().st_mtime_ns
    assert manifest_tool.main(["--version-out"]) == 0
    assert (
        tmp_path / "data" / "export" / "manifest.json"
    ).stat().st_mtime_ns == export_mtime
    assert (tmp_path / "data" / "version.json").stat().st_mtime_ns == version_mtime


def test_stats_manifest_excludes_run_records(tmp_path: Path):
    """run_stats/produce_stats/build_model_stats 每次运行必变且无消费者，默认排除出 stats 清单。"""
    _make_tree(tmp_path)
    (tmp_path / "data" / "stats" / "produce_stats.json").write_text(
        '{"generated_at": "t"}', encoding="utf8"
    )
    (tmp_path / "data" / "stats" / "build_model_stats.json").write_text(
        '{"generated_at": "t"}', encoding="utf8"
    )
    assert manifest_tool.main(["--version-out"]) == 0
    stats = json.loads(
        (tmp_path / "data" / "stats" / "manifest.json").read_text(encoding="utf8")
    )
    assert set(stats["files"]) == {"s.json"}  # 三个运行记录文件被排除
    # 运行记录本身仍在磁盘上（不进清单 ≠ 删除）
    assert (tmp_path / "data" / "stats" / "run_stats.json").is_file()


def test_main_since_changes_and_changelog(tmp_path: Path):
    _make_tree(tmp_path)
    assert manifest_tool.main(["--category", "export"]) == 0
    old_manifest = tmp_path / "old_export.json"
    shutil.copy(tmp_path / "data" / "export" / "manifest.json", old_manifest)
    # 修改 a.png、新增 c.png
    (tmp_path / "data" / "export" / "a.png").write_bytes(b"png-a-changed")
    (tmp_path / "data" / "export" / "c.png").write_bytes(b"png-c")
    assert (
        manifest_tool.main(
            ["--category", "export", "--since", str(old_manifest), "--append-changelog"]
        )
        == 0
    )
    changes = json.loads(
        (tmp_path / "data" / "stats" / "changes.json").read_text(encoding="utf8")
    )
    diff = changes["categories"]["export"]
    assert diff["counts"] == {"added": 1, "removed": 0, "modified": 1, "unchanged": 1}
    assert diff["added"] == ["c.png"]
    assert diff["modified"] == ["a.png"]
    changelog = (
        (tmp_path / "data" / "changelog.ndjson").read_text(encoding="utf8").splitlines()
    )
    assert len(changelog) == 1
    line = json.loads(changelog[0])
    assert line["game_version"] == "unknown"
    assert line["counts"]["export"] == {"added": 1, "removed": 0, "modified": 1}
    # 原样重跑：不重复追加 changelog（末行相同）
    assert (
        manifest_tool.main(
            ["--category", "export", "--since", str(old_manifest), "--append-changelog"]
        )
        == 0
    )
    changelog = (
        (tmp_path / "data" / "changelog.ndjson").read_text(encoding="utf8").splitlines()
    )
    assert len(changelog) == 1


def test_main_since_version_json(tmp_path: Path):
    _make_tree(tmp_path)
    assert manifest_tool.main(["--version-out"]) == 0
    # 模拟数据仓库旧版本：data_cache/ 下的 version.json + 各分类清单
    repo = tmp_path / "data_cache"
    for name in ("export", "export_webp", "recognition"):
        (repo / name).mkdir(parents=True)
        shutil.copy(
            tmp_path / "data" / name / "manifest.json",
            repo / name / "manifest.json",
        )
    shutil.copy(tmp_path / "data" / "version.json", repo / "version.json")
    (tmp_path / "data" / "export" / "b.png").write_bytes(b"png-b-changed")
    assert (
        manifest_tool.main(["--since", "data_cache/version.json", "--append-changelog"])
        == 0
    )
    changes = json.loads(
        (tmp_path / "data" / "stats" / "changes.json").read_text(encoding="utf8")
    )
    assert set(changes["categories"]) == {"export", "export_webp", "recognition"}
    assert changes["categories"]["export"]["counts"]["modified"] == 1
    assert changes["categories"]["export_webp"]["counts"] == {
        "added": 0,
        "removed": 0,
        "modified": 0,
        "unchanged": 1,
    }
    assert changes["from"]["game_version"] == "unknown"


def test_plain_manifest_since_requires_single_category(tmp_path: Path):
    _make_tree(tmp_path)
    assert manifest_tool.main(["--category", "export"]) == 0
    old_manifest = tmp_path / "old_export.json"
    shutil.copy(tmp_path / "data" / "export" / "manifest.json", old_manifest)
    assert manifest_tool.main(["--since", str(old_manifest)]) == 1
    assert (
        manifest_tool.main(["--since", str(old_manifest), "--category", "export"]) == 0
    )


def test_load_old_manifests_plain_single_category(tmp_path: Path):
    payload = {"game_version": "g1", "files": {"a.png": {"size": 1, "sha256": "x"}}}
    path = tmp_path / "old.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    assert manifest_tool.load_old_manifests(str(path), ["export"]) == {
        "export": payload
    }


def test_build_characters_csv(tmp_path: Path):
    extract = {
        "generated_at": "t",
        "characters": {
            "avg_1": {
                "bases": {
                    "b1.png": {"status": "ok", "method": "match", "avatar_file": "x"}
                },
                "diffs": {
                    "d1.png": {"status": "ok", "method": "derive"},
                    "d2.png": {
                        "status": "special",
                        "special": True,
                        "avatar_file": "y",
                    },
                },
            },
            "avg_2": {"bases": {}, "diffs": {}},
        },
    }
    classified = {"generated_at": "t", "characters": {"avg_1": {}, "avg_3": {}}}
    extract_path = tmp_path / "extract.json"
    class_path = tmp_path / "classified.json"
    extract_path.write_text(json.dumps(extract), encoding="utf8")
    class_path.write_text(json.dumps(classified), encoding="utf8")
    text = manifest_tool.build_characters_csv(extract_path, class_path)
    lines = text.strip().splitlines()
    assert lines[0] == ",".join(manifest_tool.CSV_COLUMNS)
    rows = {line.split(",")[0]: line.split(",")[1:] for line in lines[1:]}
    assert list(rows) == ["avg_1", "avg_2", "avg_3"]  # 字典序

    def cell(row: list[str], column: str) -> str:
        return row[manifest_tool.CSV_COLUMNS.index(column) - 1]

    avg1 = rows["avg_1"]
    assert cell(avg1, "base_count") == "1"
    assert cell(avg1, "diff_count") == "2"
    assert cell(avg1, "base_ok") == "1"
    assert cell(avg1, "base_method_match") == "1"
    assert cell(avg1, "base_method_derive") == "0"
    assert cell(avg1, "diff_special") == "1"
    assert cell(avg1, "has_avatar") == "1"
    # 缺失角色补零行
    assert rows["avg_3"] == ["0"] * (len(manifest_tool.CSV_COLUMNS) - 1)


def test_write_characters_csv_sidecar(tmp_path: Path):
    extract_path = tmp_path / "extract.json"
    extract_path.write_text('{"generated_at": "t", "characters": {}}', encoding="utf8")
    out = tmp_path / "data" / "stats" / "characters.csv"
    manifest_tool.write_characters_csv(out)
    assert out.is_file()
    sidecar = out.with_name(out.name + ".sha256").read_text(encoding="utf8")
    digest = sidecar.split()[0]
    assert digest == manifest_tool.reporting.sha256_file(out)
