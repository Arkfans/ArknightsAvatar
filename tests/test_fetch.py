from pathlib import Path

from arknightsavatar import fetch
from arknightsavatar.config import AdbConfig, ApkConfig, Config
from arknightsavatar.fetch import run_fetch
from arknightsavatar.manifest import FailureLog, Manifest
from arknightsavatar.sources import ApkSource, MultiSource
from arknightsavatar.util import sha256_file


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
    assert manifest.get("characters/a.ab").sha256 == sha256_file(
        tmp_path / "apk" / "assets" / "AB" / "Android" / "avg" / "characters" / "a.ab"
    )
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

    (
        tmp_path / "apk" / "assets" / "AB" / "Android" / "avg" / "characters" / "a.ab"
    ).write_bytes(b"b" * 100)
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


def test_make_sources_combines_multiple_in_order(monkeypatch, tmp_path: Path):
    captured = {}

    class FakeApkAdbSource:
        name = "apk"

        def __init__(self, **kwargs):
            captured["apk"] = kwargs

    class FakeAdbSource:
        name = "adb"

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

    source = fetch.make_sources(["adb", "apk"], config)
    assert isinstance(source, MultiSource)
    assert [type(item) for item in source.sources] == [FakeAdbSource, FakeApkAdbSource]
    assert source.name == "adb+apk"


def test_make_sources_single_name_returns_plain_source(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(fetch, "ApkAdbSource", lambda **kwargs: object())
    monkeypatch.setattr(fetch, "AdbSource", lambda **kwargs: object())
    config = Config(
        adb=AdbConfig(
            location="/storage/emulated/0/Android/data/com.hypergryph.arknights/files/Bundles"
        ),
        apk=ApkConfig(dir=tmp_path),
    )
    assert not isinstance(fetch.make_sources(["local-apk"], config), MultiSource)


def test_run_fetch_multi_source_union_and_first_wins(tmp_path: Path):
    class DeviceSource(ApkSource):
        name = "adb"

    device_root = tmp_path / "device"
    device_dir = device_root / "assets" / "AB" / "Android" / "avg" / "characters"
    device_dir.mkdir(parents=True)
    (device_dir / "both.ab").write_bytes(b"device-version")
    (device_dir / "only_device.ab").write_bytes(b"d" * 50)

    apk_root = tmp_path / "apk"
    apk_dir = apk_root / "assets" / "AB" / "Android" / "avg" / "characters"
    apk_dir.mkdir(parents=True)
    (apk_dir / "both.ab").write_bytes(b"apk-version")
    (apk_dir / "only_apk.ab").write_bytes(b"a" * 60)

    source = MultiSource([DeviceSource(device_root), ApkSource(apk_root)])
    raw = tmp_path / "raw"
    stats = run_fetch(source, ["characters"], raw, game_version="v1")

    assert stats["characters"]["listed"] == 3
    assert stats["characters"]["fetched"] == 3
    # duplicate rel: the earlier (device) source's version wins
    assert (raw / "characters" / "both.ab").read_bytes() == b"device-version"
    assert (raw / "characters" / "only_device.ab").read_bytes() == b"d" * 50
    assert (raw / "characters" / "only_apk.ab").read_bytes() == b"a" * 60

    manifest = Manifest.load(raw / "manifest.json", game_version="v1")
    assert manifest.get("characters/both.ab").source == "adb"
    assert manifest.get("characters/only_device.ab").source == "adb"
    assert manifest.get("characters/only_apk.ab").source == "apk"

    # second run is idempotent: everything skipped
    stats = run_fetch(source, ["characters"], raw, game_version="v1")
    assert stats["characters"]["fetched"] == 0
    assert stats["characters"]["skipped"] == 3
