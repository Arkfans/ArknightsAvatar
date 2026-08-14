from pathlib import Path

from npcavatar import fetch
from npcavatar.config import AdbConfig, ApkConfig, Config
from npcavatar.fetch import run_fetch
from npcavatar.manifest import FailureLog, Manifest
from npcavatar.sources import ApkSource
from npcavatar.sources.base import FileInfo, Source
from npcavatar.util import sha256_file


def _make_apk_source(tmp_path: Path, names: list[tuple[str, bytes]]) -> ApkSource:
    root = tmp_path / "apk"
    directory = root / "assets" / "AB" / "Android" / "avg" / "characters"
    directory.mkdir(parents=True)
    for name, content in names:
        (directory / name).write_bytes(content)
    return ApkSource(root)


def test_fetch_apk_end_to_end(tmp_path: Path):
    source = _make_apk_source(tmp_path, [("a.ab", b"a" * 100), ("empty.ab", b"")])
    raw = tmp_path / "raw"

    stats = run_fetch(source, ["characters"], raw, game_version="v1")
    assert stats["characters"]["listed"] == 2
    assert stats["characters"]["fetched"] == 1
    assert stats["characters"]["failed"] == 1
    assert stats["characters"]["skipped"] == 0

    assert (raw / "characters" / "a.ab").read_bytes() == b"a" * 100
    assert not (raw / "characters" / "empty.ab").exists()
    assert not list(raw.glob("*.part"))

    failures = FailureLog.load(raw / "_failed.json")
    assert "characters/empty.ab" in failures.failures
    manifest = Manifest.load(raw / "manifest.json", game_version="v1")
    assert manifest.game_version == "v1"
    assert manifest.get("characters/a.ab").sha256 == sha256_file(tmp_path / "apk" / "assets" / "AB" / "Android" / "avg" / "characters" / "a.ab")
    assert manifest.get("characters/empty.ab") is None


def test_fetch_idempotent(tmp_path: Path):
    source = _make_apk_source(tmp_path, [("a.ab", b"a" * 100)])
    raw = tmp_path / "raw"
    run_fetch(source, ["characters"], raw)

    stats = run_fetch(source, ["characters"], raw)
    assert stats["characters"]["fetched"] == 0
    assert stats["characters"]["skipped"] == 1


def test_fetch_replaces_changed_file(tmp_path: Path):
    source = _make_apk_source(tmp_path, [("a.ab", b"a" * 100)])
    raw = tmp_path / "raw"
    run_fetch(source, ["characters"], raw)

    (tmp_path / "apk" / "assets" / "AB" / "Android" / "avg" / "characters" / "a.ab").write_bytes(b"b" * 100)
    stats = run_fetch(source, ["characters"], raw)
    assert stats["characters"]["fetched"] == 1
    assert stats["characters"]["skipped"] == 0
    assert (raw / "characters" / "a.ab").read_bytes() == b"b" * 100


def test_fetch_force_repulls(tmp_path: Path):
    source = _make_apk_source(tmp_path, [("a.ab", b"a" * 100)])
    raw = tmp_path / "raw"
    run_fetch(source, ["characters"], raw)

    stats = run_fetch(source, ["characters"], raw, force=True)
    assert stats["characters"]["fetched"] == 1
    assert stats["characters"]["skipped"] == 0


def test_make_source_maps_apk_and_local_apk(monkeypatch, tmp_path: Path):
    captured = {}

    class FakeApkAdbSource:
        def __init__(self, **kwargs):
            captured["apk-adb"] = kwargs

    class FakeAdbSource:
        def __init__(self, **kwargs):
            captured["adb"] = kwargs

    monkeypatch.setattr(fetch, "ApkAdbSource", FakeApkAdbSource)
    monkeypatch.setattr(fetch, "AdbSource", FakeAdbSource)

    config = Config(
        adb=AdbConfig(
            host="1.2.3.4",
            port=5555,
            location="/storage/emulated/0/Android/data/com.hypergryph.arknights/files/Bundles",
        ),
        apk=ApkConfig(dir=tmp_path),
    )

    assert isinstance(fetch.make_source("apk", config), FakeApkAdbSource)
    assert captured["apk-adb"]["host"] == "1.2.3.4"
    assert captured["apk-adb"]["package"] == "com.hypergryph.arknights"

    assert isinstance(fetch.make_source("local-apk", config), ApkSource)
    assert isinstance(fetch.make_source("adb", config), FakeAdbSource)


class FakeSource(Source):
    name = "fake"

    def __init__(self, files):
        self.files = files

    def list_files(self, category):
        return self.files

    def fetch_to(self, rel, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")


def test_run_fetch_with_incremental_source(tmp_path: Path):
    raw = tmp_path / "raw"
    source = FakeSource([FileInfo(rel="characters/a.ab", size=1)])

    stats = run_fetch(source, ["characters"], raw, game_version="v1")
    assert stats["characters"]["listed"] == 1
    assert stats["characters"]["fetched"] == 1

    stats = run_fetch(source, ["characters"], raw, game_version="v1")
    assert stats["characters"]["fetched"] == 0
    assert stats["characters"]["skipped"] == 1
