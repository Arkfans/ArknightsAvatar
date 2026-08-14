from pathlib import Path
from unittest.mock import Mock

from arknightsavatar.sources import ApkAdbSource, ApkSource


def _make_apk_adb_source(paths, outputs):
    source = ApkAdbSource.__new__(ApkAdbSource)
    source._device = Mock()
    source._apk_paths = paths
    source._ls_cache = {}
    source._entry_apk = {}

    def fake_shell(command, decode=False):
        for needle, output in outputs:
            if needle in command:
                return output
        return ""

    source._device.shell.side_effect = fake_shell
    return source


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


def test_parse_unzip_l_extracts_size_and_name():
    output = "\n".join(
        [
            "Archive:  /data/app/base.apk",
            "  Length      Date    Time    Name",
            "---------  ---------- -----   ----",
            "    10022  2026-07-22 00:19   assets/AB/Android/spritepack/a.ab",
            "  3205090  2026-07-22 00:19   assets/AB/Android/spritepack/b.ab",
            "---------                     -------",
            "  3215112                      2 files",
        ]
    )
    assert ApkAdbSource._parse_unzip_l(output) == [
        (10022, "assets/AB/Android/spritepack/a.ab"),
        (3205090, "assets/AB/Android/spritepack/b.ab"),
    ]


def test_apk_adb_source_lists_and_dedups_with_base_priority(tmp_path: Path):
    source = _make_apk_adb_source(
        ["/data/app/base.apk", "/data/app/split.apk"],
        [
            (
                "unzip -l /data/app/base.apk",
                "  Length      Date    Time    Name\n"
                "    10022  2026-07-22 00:19   assets/AB/Android/spritepack/ui_char_avatar_0.ab\n"
                "        1  2026-07-22 00:19   assets/AB/Android/spritepack/dup.ab\n",
            ),
            (
                "unzip -l /data/app/split.apk",
                "  Length      Date    Time    Name\n"
                "       20  2026-07-22 00:19   assets/AB/Android/spritepack/split_only.ab\n"
                "       99  2026-07-22 00:19   assets/AB/Android/spritepack/dup.ab\n",
            ),
        ],
    )

    infos = source.list_files("avatars")
    assert [(info.rel, info.size) for info in infos] == [
        ("avatars/dup.ab", 1),
        ("avatars/split_only.ab", 20),
        ("avatars/ui_char_avatar_0.ab", 10022),
    ]
    assert source._entry_apk["avatars/dup.ab"] == "/data/app/base.apk"


def test_apk_adb_source_fetch_to_uses_unzip_p(tmp_path: Path):
    payload = b"ab" * 100
    source = _make_apk_adb_source(
        ["/data/app/base.apk"],
        [("unzip -p /data/app/base.apk", payload)],
    )
    source._entry_apk["avatars/ui_char_avatar_0.ab"] = "/data/app/base.apk"

    dest = tmp_path / "avatars" / "ui_char_avatar_0.ab"
    source.fetch_to("avatars/ui_char_avatar_0.ab", dest)

    assert dest.read_bytes() == payload
    command = source._device.shell.call_args.args[0]
    assert "unzip -p" in command
    assert "/data/app/base.apk" in command
    assert "assets/AB/Android/spritepack/ui_char_avatar_0.ab" in command
