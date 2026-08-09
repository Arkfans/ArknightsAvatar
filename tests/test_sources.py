from pathlib import Path

from npcavatar.sources import ApkSource, LocalSource


def test_local_source_lists_and_fetches(tmp_path: Path):
    directory = tmp_path / "characters"
    directory.mkdir()
    (directory / "a.ab").write_bytes(b"x" * 10)
    (directory / "empty.ab").write_bytes(b"")

    source = LocalSource({"characters": directory})
    infos = source.list_files("characters")
    assert {info.rel for info in infos} == {"characters/a.ab", "characters/empty.ab"}
    sizes = {info.rel: info.size for info in infos}
    assert sizes["characters/a.ab"] == 10
    assert sizes["characters/empty.ab"] == 0

    dest = tmp_path / "out" / "characters" / "a.ab"
    source.fetch_to("characters/a.ab", dest)
    assert dest.read_bytes() == b"x" * 10
    assert source.sha256("characters/a.ab") == __import__("npcavatar.util", fromlist=["sha256_file"]).sha256_file(dest)


def test_local_source_missing_category(tmp_path: Path):
    source = LocalSource({"characters": tmp_path / "characters"})
    assert source.list_files("chararts") == []
    assert source.supports("chararts") is False


def test_apk_source_mapping(tmp_path: Path):
    root = tmp_path / "apk"
    ab_root = root / "assets" / "AB" / "Android"
    (ab_root / "spritepack").mkdir(parents=True)
    (ab_root / "spritepack" / "ui_char_avatar_0.ab").write_bytes(b"z")
    (ab_root / "chararts").mkdir()
    (ab_root / "chararts" / "char_002_amiya.ab").write_bytes(b"y")

    source = ApkSource(root)
    assert [info.rel for info in source.list_files("avatars")] == ["avatars/ui_char_avatar_0.ab"]
    assert [info.rel for info in source.list_files("chararts")] == ["chararts/char_002_amiya.ab"]
    assert source.list_files("characters") == []
    assert source.list_files("skins") == []

    dest = tmp_path / "out" / "avatars" / "ui_char_avatar_0.ab"
    source.fetch_to("avatars/ui_char_avatar_0.ab", dest)
    assert dest.read_bytes() == b"z"
