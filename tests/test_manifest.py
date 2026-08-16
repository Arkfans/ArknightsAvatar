from pathlib import Path

from arknightsavatar.manifest import FailureLog, FileRecord, Manifest


def test_manifest_roundtrip(tmp_path: Path):
    path = tmp_path / "manifest.json"
    manifest = Manifest(path, game_version="arknights-hg-2761")
    manifest.set("characters/a.ab", FileRecord(size=3, sha256="abc", source="adb"))
    manifest.save()

    loaded = Manifest.load(path, game_version="other")
    assert loaded.game_version == "arknights-hg-2761"
    assert loaded.get("characters/a.ab") == FileRecord(
        size=3, sha256="abc", source="adb"
    )
    assert loaded.get("missing.ab") is None


def test_manifest_keeps_existing_entries_on_load(tmp_path: Path):
    path = tmp_path / "manifest.json"
    manifest = Manifest(path, game_version="v1")
    manifest.set("a.ab", FileRecord(size=1, sha256="x", source="apk"))
    manifest.save()

    loaded = Manifest.load(path, game_version="v1")
    loaded.set("b.ab", FileRecord(size=2, sha256="y", source="apk"))
    loaded.save()

    final = Manifest.load(path)
    assert set(final.files) == {"a.ab", "b.ab"}


def test_failure_log_roundtrip(tmp_path: Path):
    path = tmp_path / "_failed.json"
    log = FailureLog(path)
    log.add("characters/x.ab", source="adb", error="ValueError: 0 bytes")
    log.save()

    loaded = FailureLog.load(path)
    assert loaded.failures["characters/x.ab"]["error"] == "ValueError: 0 bytes"
