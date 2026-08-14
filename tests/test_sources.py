from pathlib import Path

from npcavatar.sources import ApkSource


def test_apk_source_mapping(tmp_path: Path):
    root = tmp_path / "apk"
    ab_root = root / "assets" / "AB" / "Android"
    (ab_root / "spritepack").mkdir(parents=True)
    (ab_root / "spritepack" / "ui_char_avatar_0.ab").write_bytes(b"z")
    (ab_root / "avg" / "characters").mkdir(parents=True)
    (ab_root / "avg" / "characters" / "avg_007_closre_1.ab").write_bytes(b"c")

    source = ApkSource(root)
    assert [info.rel for info in source.list_files("characters")] == [
        "characters/avg_007_closre_1.ab"
    ]
    assert [info.rel for info in source.list_files("avatars")] == ["avatars/ui_char_avatar_0.ab"]

    dest = tmp_path / "out" / "avatars" / "ui_char_avatar_0.ab"
    source.fetch_to("avatars/ui_char_avatar_0.ab", dest)
    assert dest.read_bytes() == b"z"
