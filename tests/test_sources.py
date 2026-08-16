import io
import tarfile
from pathlib import Path
from unittest.mock import Mock

from arknightsavatar.sources import ApkAdbSource, ApkSource
from arknightsavatar.sources.base import FileInfo


def _make_apk_adb_source(paths, outputs):
    source = ApkAdbSource.__new__(ApkAdbSource)
    source._device = Mock()
    source._apk_paths = paths
    source._ls_cache = {}
    source._entry_apk = {}
    source._batch = True
    source._show_progress = False

    def fake_shell(command, decode=False, **kwargs):
        for needle, output in outputs:
            if needle in command:
                return output
        return ""

    source._device.shell.side_effect = fake_shell
    return source


def _tar_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _attach_apk_pack_device(source, files, *, pack=None):
    """Wire a fake device whose pack pull yields a tar of ``files``.

    Per-file fallbacks go through the shell: an ``unzip -p <apk> <entry>``
    command returns the payload bytes (decode=False), so missing entries
    raise KeyError just like a device-side failure would.
    """
    device = Mock()
    shells: list[str] = []
    pack_files = files if pack is None else pack

    def fake_shell(command, decode=False, **kwargs):
        shells.append(command)
        if command.startswith("unzip -p "):
            entry = command.rsplit(" ", 1)[-1].strip("'\"")
            return files[entry.rsplit("/", 1)[-1]]
        return "EXIT:0" if command.startswith("tar") else ""

    def fake_pull(device_path, local_path, progress_callback=None, **kwargs):
        data = _tar_bytes(pack_files)
        progress_callback(device_path, len(data), len(data))
        Path(local_path).write_bytes(data)

    device.shell.side_effect = fake_shell
    device.pull.side_effect = fake_pull
    source._device = device
    return device, shells


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


def test_apk_adb_fetch_many_packs_pulls_once_and_extracts(tmp_path: Path):
    source = _make_apk_adb_source(
        ["/data/app/base.apk"],
        [("unzip -l /data/app/base.apk", "")],
    )
    source._entry_apk = {
        "avatars/a.ab": "/data/app/base.apk",
        "avatars/b.ab": "/data/app/base.apk",
    }
    files = {"a.ab": b"a" * 100, "b.ab": b"b" * 50}
    device, shells = _attach_apk_pack_device(source, files)

    items = [
        (FileInfo(rel="avatars/a.ab", size=100), tmp_path / "a.ab.part"),
        (FileInfo(rel="avatars/b.ab", size=50), tmp_path / "b.ab.part"),
    ]
    assert source.fetch_many(items) == []

    assert (tmp_path / "a.ab.part").read_bytes() == b"a" * 100
    assert (tmp_path / "b.ab.part").read_bytes() == b"b" * 50

    # one pack pull over the sync protocol, with progress and the read timeout
    assert device.pull.call_count == 1
    assert device.pull.call_args.args[0].startswith("/data/local/tmp/arknights_ab_avatars_")
    assert device.pull.call_args.kwargs["progress_callback"] is not None
    assert device.pull.call_args.kwargs["read_timeout_s"] == 60

    # names + zip entries written as device listings, then unzipped to tmpdir
    printf_cmds = [cmd for cmd in shells if cmd.startswith("printf")]
    assert len(printf_cmds) == 2
    assert any(".list" in cmd and "a.ab" in cmd for cmd in printf_cmds)
    assert any(".entries_0" in cmd and "assets/AB/Android/spritepack" in cmd for cmd in printf_cmds)

    unzip_cmd = next(cmd for cmd in shells if cmd.startswith("mkdir -p"))
    assert "/data/app/base.apk" in unzip_cmd
    assert "/arknights_ab_avatars_" in unzip_cmd
    assert '${e##*/}' in unzip_cmd  # basename via mksh parameter expansion
    assert "while IFS= read -r e; do unzip -p" in unzip_cmd

    tar_cmd = next(cmd for cmd in shells if cmd.startswith("tar"))
    assert "-cf" in tar_cmd and "-T" in tar_cmd and "echo EXIT:$?" in tar_cmd
    assert "arknights_ab_avatars_" in tar_cmd and ".d" in tar_cmd and ".list" in tar_cmd

    # device scratch files are cleaned up: plain rm for files, rm -rf for tmpdir
    rm_cmds = [cmd for cmd in shells if cmd.startswith("rm -f")]
    assert rm_cmds and any(".tar" in cmd and ".list" in cmd and ".entries_0" in cmd for cmd in rm_cmds)
    assert any(cmd.startswith("rm -rf") and ".d" in cmd for cmd in shells)
    # no leftover local pack
    assert not list(tmp_path.rglob("arknights_ab_*"))


def test_apk_adb_fetch_many_groups_entries_by_apk(tmp_path: Path):
    source = _make_apk_adb_source(
        ["/data/app/base.apk", "/data/app/split.apk"],
        [("unzip -l /data/app/base.apk", ""), ("unzip -l /data/app/split.apk", "")],
    )
    source._entry_apk = {
        "avatars/a.ab": "/data/app/base.apk",
        "avatars/b.ab": "/data/app/split.apk",
    }
    files = {"a.ab": b"a" * 100, "b.ab": b"b" * 50}
    device, shells = _attach_apk_pack_device(source, files)

    items = [
        (FileInfo(rel="avatars/a.ab", size=100), tmp_path / "a.ab.part"),
        (FileInfo(rel="avatars/b.ab", size=50), tmp_path / "b.ab.part"),
    ]
    assert source.fetch_many(items) == []
    assert device.pull.call_count == 1

    printf_cmds = [cmd for cmd in shells if cmd.startswith("printf")]
    assert len(printf_cmds) == 3  # names list + one entries list per APK
    assert "assets/AB/Android/spritepack/a.ab" in printf_cmds[1] and ".entries_0" in printf_cmds[1]
    assert "assets/AB/Android/spritepack/b.ab" in printf_cmds[2] and ".entries_1" in printf_cmds[2]

    unzip_cmds = [cmd for cmd in shells if cmd.startswith("mkdir -p")]
    assert len(unzip_cmds) == 2
    assert "/data/app/base.apk" in unzip_cmds[0]
    assert "/data/app/split.apk" in unzip_cmds[1]

    rm_cmds = [cmd for cmd in shells if cmd.startswith("rm -f")]
    assert any(".entries_0" in cmd and ".entries_1" in cmd for cmd in rm_cmds)


def test_apk_adb_fetch_many_missing_and_size_mismatch_fall_back(tmp_path: Path):
    source = _make_apk_adb_source(
        ["/data/app/base.apk"],
        [("unzip -l /data/app/base.apk", "")],
    )
    source._entry_apk = {
        "avatars/a.ab": "/data/app/base.apk",
        "avatars/b.ab": "/data/app/base.apk",
        "avatars/c.ab": "/data/app/base.apk",
    }
    files = {"a.ab": b"a" * 100, "b.ab": b"b" * 50, "c.ab": b"c" * 99}
    # pack misses c.ab entirely and ships a wrong-sized b.ab
    device, shells = _attach_apk_pack_device(
        source, files, pack={"a.ab": b"a" * 100, "b.ab": b"b" * 10}
    )

    items = [
        (FileInfo(rel="avatars/a.ab", size=100), tmp_path / "a.ab.part"),
        (FileInfo(rel="avatars/b.ab", size=50), tmp_path / "b.ab.part"),
        (FileInfo(rel="avatars/c.ab", size=99), tmp_path / "c.ab.part"),
    ]
    assert source.fetch_many(items) == []

    # a.ab came from the pack; b.ab (size mismatch) and c.ab (missing) re-fetched
    assert (tmp_path / "a.ab.part").read_bytes() == b"a" * 100
    assert (tmp_path / "b.ab.part").read_bytes() == b"b" * 50
    assert (tmp_path / "c.ab.part").read_bytes() == b"c" * 99
    assert device.pull.call_count == 1  # fallbacks go through shell unzip -p
    fallback_cmds = [cmd for cmd in shells if cmd.startswith("unzip -p ")]
    assert len(fallback_cmds) == 2
    assert "assets/AB/Android/spritepack/b.ab" in fallback_cmds[0]
    assert "assets/AB/Android/spritepack/c.ab" in fallback_cmds[1]


def test_apk_adb_fetch_many_reports_failures_when_fallback_also_fails(tmp_path: Path):
    source = _make_apk_adb_source(
        ["/data/app/base.apk"],
        [("unzip -l /data/app/base.apk", "")],
    )
    source._entry_apk = {
        "avatars/a.ab": "/data/app/base.apk",
        "avatars/b.ab": "/data/app/base.apk",
    }
    # b.ab is in neither the pack nor the device: both paths must fail
    device, _ = _attach_apk_pack_device(source, {"a.ab": b"a" * 100})

    items = [
        (FileInfo(rel="avatars/a.ab", size=100), tmp_path / "a.ab.part"),
        (FileInfo(rel="avatars/b.ab", size=50), tmp_path / "b.ab.part"),
    ]
    failures = source.fetch_many(items)
    assert [info.rel for info, _ in failures] == ["avatars/b.ab"]
    assert isinstance(failures[0][1], KeyError)
    assert (tmp_path / "a.ab.part").read_bytes() == b"a" * 100
    assert not (tmp_path / "b.ab.part").exists()


def test_apk_adb_fetch_many_falls_back_to_per_file_when_tar_fails(tmp_path: Path):
    source = _make_apk_adb_source(
        ["/data/app/base.apk"],
        [("unzip -l /data/app/base.apk", "")],
    )
    source._entry_apk = {"avatars/a.ab": "/data/app/base.apk"}
    files = {"a.ab": b"a" * 100}
    device, shells = _attach_apk_pack_device(source, files)
    original = device.shell.side_effect

    def fake_shell(command, **kwargs):
        if command.startswith("tar"):
            return "EXIT:1\ntar: not supported"
        return original(command, **kwargs)

    device.shell.side_effect = fake_shell

    items = [(FileInfo(rel="avatars/a.ab", size=100), tmp_path / "a.ab.part")]
    assert source.fetch_many(items) == []
    assert (tmp_path / "a.ab.part").read_bytes() == b"a" * 100
    # per-file fallback re-unzipped via the shell; no pack pull happened
    assert device.pull.call_count == 0
    assert any(cmd.startswith("unzip -p ") for cmd in shells)


def test_apk_adb_fetch_many_cleanup_after_failed_pack_pull(tmp_path: Path):
    source = _make_apk_adb_source(
        ["/data/app/base.apk"],
        [("unzip -l /data/app/base.apk", "")],
    )
    source._entry_apk = {
        "avatars/a.ab": "/data/app/base.apk",
        "avatars/b.ab": "/data/app/base.apk",
    }
    files = {"a.ab": b"a" * 10, "b.ab": b"b" * 10}
    device, shells = _attach_apk_pack_device(source, files)

    def fake_pull(device_path, local_path, progress_callback=None, **kwargs):
        raise RuntimeError("link down")

    device.pull.side_effect = fake_pull

    items = [(FileInfo(rel=f"avatars/{name}", size=10), tmp_path / f"{name}.part") for name in files]
    assert source.fetch_many(items) == []

    assert (tmp_path / "a.ab.part").read_bytes() == b"a" * 10
    assert (tmp_path / "b.ab.part").read_bytes() == b"b" * 10
    assert any(cmd.startswith("rm -f") for cmd in shells)
    assert any(cmd.startswith("rm -rf") and ".d" in cmd for cmd in shells)


def test_apk_adb_fetch_many_refills_entry_apk(tmp_path: Path):
    listing = (
        "  Length      Date    Time    Name\n"
        "    100  2026-07-22 00:19   assets/AB/Android/spritepack/a.ab\n"
    )
    source = _make_apk_adb_source(
        ["/data/app/base.apk"],
        [("unzip -l /data/app/base.apk", listing), ("unzip -p /data/app/base.apk", b"a" * 100)],
    )
    device = source._device
    shells: list[str] = []
    original_shell = device.shell.side_effect

    def fake_shell(command, decode=False, **kwargs):
        shells.append(command)
        return original_shell(command, decode=decode)

    def fake_pull(device_path, local_path, progress_callback=None, **kwargs):
        data = _tar_bytes({"a.ab": b"a" * 100})
        progress_callback(device_path, len(data), len(data))
        Path(local_path).write_bytes(data)

    device.shell.side_effect = fake_shell
    device.pull.side_effect = fake_pull

    # _entry_apk intentionally empty: the pack path must re-list first
    items = [(FileInfo(rel="avatars/a.ab", size=100), tmp_path / "a.ab.part")]
    assert source.fetch_many(items) == []
    assert (tmp_path / "a.ab.part").read_bytes() == b"a" * 100
    assert source._entry_apk["avatars/a.ab"] == "/data/app/base.apk"
    assert any(cmd.startswith("unzip -l ") for cmd in shells)


def test_apk_adb_fetch_many_batch_disabled_pulls_per_file(tmp_path: Path):
    source = _make_apk_adb_source(
        ["/data/app/base.apk"],
        [("unzip -p /data/app/base.apk", b"x")],
    )
    source._entry_apk = {
        "avatars/a.ab": "/data/app/base.apk",
        "avatars/b.ab": "/data/app/base.apk",
    }
    source._batch = False
    device = source._device
    shells: list[str] = []
    original_shell = device.shell.side_effect

    def fake_shell(command, decode=False, **kwargs):
        shells.append(command)
        return original_shell(command, decode=decode)

    device.shell.side_effect = fake_shell

    items = [
        (FileInfo(rel="avatars/a.ab", size=100), tmp_path / "a.ab.part"),
        (FileInfo(rel="avatars/b.ab", size=50), tmp_path / "b.ab.part"),
    ]
    assert source.fetch_many(items) == []
    assert (tmp_path / "a.ab.part").read_bytes() == b"x"
    assert (tmp_path / "b.ab.part").read_bytes() == b"x"
    # no tar/pull at all: everything went through per-entry shell unzip
    assert device.pull.call_count == 0
    assert len(shells) == 2
    assert all(cmd.startswith("unzip -p ") for cmd in shells)
