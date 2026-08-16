import io
import tarfile
from pathlib import Path
from unittest.mock import Mock

from arknightsavatar.sources.adb import AdbSource, _fmt_bytes, PullProgress
from arknightsavatar.sources.base import FileInfo


def _make_source() -> AdbSource:
    source = AdbSource.__new__(AdbSource)
    source.location = "/sdcard/game"
    source._show_progress = False
    source._batch = True
    source._compress = False
    return source


def _tar_bytes(files: dict[str, bytes], *, gzip: bool = False) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz" if gzip else "w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _attach_device(
    source: AdbSource, files: dict[str, bytes], *, gzip: bool = False
) -> Mock:
    """Wire a fake device whose pack pull yields a tar of ``files``."""
    device = Mock()

    def fake_shell(command, **kwargs):
        return "EXIT:0" if command.startswith("tar") else ""

    def fake_pull(device_path, local_path, progress_callback=None, **kwargs):
        if device_path.startswith("/data/local/tmp"):
            data = _tar_bytes(files, gzip=gzip)
            progress_callback(device_path, len(data), len(data))
            Path(local_path).write_bytes(data)
        else:
            name = device_path.rsplit("/", 1)[-1]
            data = files[name]
            progress_callback(device_path, len(data), len(data))
            Path(local_path).write_bytes(data)

    device.shell.side_effect = fake_shell
    device.pull.side_effect = fake_pull
    source._device = device
    return device


def test_fmt_bytes():
    assert _fmt_bytes(0) == "0 B"
    assert _fmt_bytes(512) == "512 B"
    assert _fmt_bytes(1024) == "1.0 KiB"
    assert _fmt_bytes(1024 * 1024) == "1.0 MiB"
    assert _fmt_bytes(1024**3) == "1.00 GiB"


def test_pull_progress_reports_percentage_bytes_and_speed():
    stream = io.StringIO()
    progress = PullProgress("characters/a.ab", enabled=True, stream=stream)

    chunk = 64 * 1024
    total = chunk * 4
    for _ in range(4):
        progress("characters/a.ab", chunk, total)
    progress.finish()

    out = stream.getvalue()
    assert out.startswith("\r")
    assert "characters/a.ab" in out
    assert "100.0%" in out
    assert "256.0 KiB/256.0 KiB" in out
    assert "/s" in out
    assert out.endswith("\n")


def test_pull_progress_with_position_shows_file_counter():
    stream = io.StringIO()
    progress = PullProgress(
        "characters/a.ab", enabled=True, stream=stream, position=(2, 10)
    )

    chunk = 64 * 1024
    for _ in range(2):
        progress("characters/a.ab", chunk, chunk * 4)
    progress.finish()

    out = stream.getvalue()
    assert out.startswith("\r[2/10] ")
    assert "characters/a.ab" in out
    assert "50.0%" in out


def test_pull_progress_without_position_has_no_counter():
    stream = io.StringIO()
    progress = PullProgress("characters/a.ab", enabled=True, stream=stream)
    progress("characters/a.ab", 512, 512)
    progress.finish()

    out = stream.getvalue()
    assert "[" not in out.split("\r")[-1]


def test_pull_progress_without_total_shows_no_percent():
    stream = io.StringIO()
    progress = PullProgress("characters/a.ab", enabled=True, stream=stream)
    progress("characters/a.ab", 512, 0)
    progress.finish()

    out = stream.getvalue()
    assert "%" not in out
    assert "512 B" in out


def test_pull_progress_disabled_is_quiet():
    stream = io.StringIO()
    progress = PullProgress("characters/a.ab", enabled=False, stream=stream)
    progress("characters/a.ab", 100, 100)
    progress.finish()
    assert stream.getvalue() == ""


def test_fetch_to_passes_progress_callback(tmp_path: Path):
    source = AdbSource.__new__(AdbSource)
    source.location = "/sdcard/game"
    source._show_progress = False

    data = b"ab" * 100
    device = Mock()

    def fake_pull(device_path, local_path, progress_callback=None, **kwargs):
        progress_callback(device_path, len(data), len(data))
        Path(local_path).write_bytes(data)

    device.pull.side_effect = fake_pull
    source._device = device

    dest = tmp_path / "characters" / "a.ab"
    source.fetch_to("characters/a.ab", dest)

    assert dest.read_bytes() == data
    assert device.pull.call_count == 1
    assert device.pull.call_args.kwargs["progress_callback"] is not None


def test_fetch_to_passes_read_timeout(tmp_path: Path):
    """P1-8: 单文件 pull 也传 read_timeout_s=60，与批量路径拉齐。"""
    source = AdbSource.__new__(AdbSource)
    source.location = "/sdcard/game"
    source._show_progress = False
    device = Mock()

    def fake_pull(device_path, local_path, progress_callback=None, **kwargs):
        Path(local_path).write_bytes(b"ab")

    device.pull.side_effect = fake_pull
    source._device = device
    source.fetch_to("characters/a.ab", tmp_path / "characters" / "a.ab")
    assert device.pull.call_args.kwargs.get("read_timeout_s") == 60


def test_ls_escapes_path(tmp_path: Path):
    """P1-7: _ls 对含空格/元字符的 path 用 shlex.quote。"""
    import shlex

    source = _make_source()
    source._ls_cache = {}  # _make_source 不初始化 ls 缓存，这里补上
    device = Mock()
    seen = []

    def fake_shell(command, **kwargs):
        seen.append(command)
        return ""  # no matching ls lines → empty listing

    device.shell.side_effect = fake_shell
    source._device = device
    source._ls("/sdcard/my dir/sp ace")
    assert device.shell.call_count == 1
    sent = seen[0]
    # 必须仍以 ls -l 开头且路径被引号包裹（空格未被拆成多个 argv）
    assert sent.startswith("ls -l ")
    assert "'/sdcard/my dir/sp ace'" in sent
    # 关键判定：用 shlex 拆分为恰好 3 个 argv，第三个还原为原始含空格路径
    tokens = shlex.split(sent)
    assert tokens == ["ls", "-l", "/sdcard/my dir/sp ace"]


def test_fetch_many_packs_pulls_once_and_extracts(tmp_path: Path):
    source = _make_source()
    files = {"a.ab": b"a" * 100, "b.ab": b"b" * 50}
    device = _attach_device(source, files)
    shells: list[str] = []
    device.shell.side_effect = lambda command, **kwargs: (
        shells.append(command) or ("EXIT:0" if command.startswith("tar") else "")
    )

    items = [
        (
            FileInfo(rel="characters/a.ab", size=100),
            tmp_path / "characters" / "a.ab.part",
        ),
        (
            FileInfo(rel="characters/b.ab", size=50),
            tmp_path / "characters" / "b.ab.part",
        ),
    ]
    assert source.fetch_many(items) == []

    assert (tmp_path / "characters" / "a.ab.part").read_bytes() == b"a" * 100
    assert (tmp_path / "characters" / "b.ab.part").read_bytes() == b"b" * 50

    # one pack pull, plus the per-file fallbacks that never fired
    assert device.pull.call_count == 1
    assert device.pull.call_args.args[0].startswith(
        "/data/local/tmp/arknights_ab_characters_"
    )

    tar_cmd = next(cmd for cmd in shells if cmd.startswith("tar"))
    assert "-cf" in tar_cmd and "-T" in tar_cmd and "-C" in tar_cmd
    assert "-C /sdcard/game/avg/characters" in tar_cmd
    assert "echo EXIT:$?" in tar_cmd

    # device scratch files are cleaned up
    rm_cmd = next(cmd for cmd in shells if cmd.startswith("rm -f"))
    assert ".tar" in rm_cmd and ".list" in rm_cmd
    # no leftover local pack
    assert not list(tmp_path.rglob("arknights_ab_*"))


def test_fetch_many_writes_listing_in_chunks(tmp_path: Path):
    source = _make_source()
    files = {f"f{i}.ab": b"x" for i in range(250)}
    device = _attach_device(source, files)
    shells: list[str] = []
    device.shell.side_effect = lambda command, **kwargs: (
        shells.append(command) or ("EXIT:0" if command.startswith("tar") else "")
    )

    items = [
        (FileInfo(rel=f"characters/f{i}.ab", size=1), tmp_path / f"f{i}.ab.part")
        for i in range(250)
    ]
    assert source.fetch_many(items) == []
    assert all((tmp_path / f"f{i}.ab.part").read_bytes() == b"x" for i in range(250))

    printf_cmds = [cmd for cmd in shells if cmd.startswith("printf")]
    assert len(printf_cmds) == 3  # 250 names / 100 per chunk
    assert " > " in printf_cmds[0]
    assert all(" >> " in cmd for cmd in printf_cmds[1:])


def test_fetch_many_missing_member_falls_back_to_per_file(tmp_path: Path):
    source = _make_source()
    device = Mock()

    def fake_shell(command, **kwargs):
        return "EXIT:0" if command.startswith("tar") else ""

    def fake_pull(device_path, local_path, progress_callback=None, **kwargs):
        if device_path.startswith("/data/local/tmp"):
            data = _tar_bytes({"a.ab": b"a" * 100})  # pack misses b.ab and c.ab
        else:
            data = {
                "/sdcard/game/avg/characters/b.ab": b"b" * 50,
                "/sdcard/game/avg/characters/c.ab": b"c" * 99,
            }[device_path]
        progress_callback(device_path, len(data), len(data))
        Path(local_path).write_bytes(data)

    device.shell.side_effect = fake_shell
    device.pull.side_effect = fake_pull
    source._device = device

    items = [
        (FileInfo(rel="characters/a.ab", size=100), tmp_path / "a.ab.part"),
        (FileInfo(rel="characters/b.ab", size=50), tmp_path / "b.ab.part"),
        (FileInfo(rel="characters/c.ab", size=99), tmp_path / "c.ab.part"),
    ]
    assert source.fetch_many(items) == []

    assert (tmp_path / "a.ab.part").read_bytes() == b"a" * 100
    # b.ab missing from the archive and c.ab size-mismatched -> re-pulled per file
    assert (tmp_path / "b.ab.part").read_bytes() == b"b" * 50
    assert (tmp_path / "c.ab.part").read_bytes() == b"c" * 99
    assert device.pull.call_count == 3  # 1 pack + 2 fallbacks
    fallback_paths = [call.args[0] for call in device.pull.call_args_list[1:]]
    assert fallback_paths == [
        "/sdcard/game/avg/characters/b.ab",
        "/sdcard/game/avg/characters/c.ab",
    ]


def test_fetch_many_reports_failures_when_fallback_also_fails(tmp_path: Path):
    source = _make_source()
    files = {"a.ab": b"a" * 100}
    device = _attach_device(source, files)

    def fake_pull(device_path, local_path, progress_callback=None, **kwargs):
        if device_path.startswith("/data/local/tmp"):
            data = _tar_bytes(files)
            progress_callback(device_path, len(data), len(data))
            Path(local_path).write_bytes(data)
        else:
            raise OSError("device gone")

    device.pull.side_effect = fake_pull
    source._device = device

    items = [
        (FileInfo(rel="characters/a.ab", size=100), tmp_path / "a.ab.part"),
        (FileInfo(rel="characters/b.ab", size=50), tmp_path / "b.ab.part"),
    ]
    failures = source.fetch_many(items)
    assert [info.rel for info, _ in failures] == ["characters/b.ab"]
    assert isinstance(failures[0][1], OSError)
    assert not (tmp_path / "b.ab.part").exists()


def test_fetch_many_falls_back_to_per_file_when_tar_fails(tmp_path: Path):
    source = _make_source()
    device = _attach_device(source, {"a.ab": b"abc"})
    device.shell.side_effect = lambda command, **kwargs: (
        "EXIT:1\ntar: not supported" if command.startswith("tar") else ""
    )

    items = [(FileInfo(rel="characters/a.ab", size=3), tmp_path / "a.ab.part")]
    assert source.fetch_many(items) == []

    assert (tmp_path / "a.ab.part").read_bytes() == b"abc"
    # per-file fallback pulled the real AB path, not the pack
    assert device.pull.call_count == 1
    assert device.pull.call_args.args[0] == "/sdcard/game/avg/characters/a.ab"


def test_fetch_many_cleanup_after_failed_pack_pull(tmp_path: Path):
    source = _make_source()
    files = {"a.ab": b"a" * 10, "b.ab": b"b" * 10}
    device = _attach_device(source, files)
    shells: list[str] = []
    device.shell.side_effect = lambda command, **kwargs: (
        shells.append(command) or ("EXIT:0" if command.startswith("tar") else "")
    )

    def fake_pull(device_path, local_path, progress_callback=None, **kwargs):
        if device_path.startswith("/data/local/tmp"):
            raise RuntimeError("link down")
        data = files[device_path.rsplit("/", 1)[-1]]
        progress_callback(device_path, len(data), len(data))
        Path(local_path).write_bytes(data)

    device.pull.side_effect = fake_pull
    source._device = device

    items = [
        (FileInfo(rel=f"characters/{name}", size=10), tmp_path / f"{name}.part")
        for name in files
    ]
    assert source.fetch_many(items) == []

    assert (tmp_path / "a.ab.part").read_bytes() == b"a" * 10
    assert (tmp_path / "b.ab.part").read_bytes() == b"b" * 10
    assert any(cmd.startswith("rm -f") for cmd in shells)


def test_fetch_many_compress_uses_gzip(tmp_path: Path):
    source = _make_source()
    source._compress = True
    files = {"a.ab": b"a" * 100}
    device = _attach_device(source, files, gzip=True)
    shells: list[str] = []
    device.shell.side_effect = lambda command, **kwargs: (
        shells.append(command) or ("EXIT:0" if command.startswith("tar") else "")
    )

    items = [(FileInfo(rel="characters/a.ab", size=100), tmp_path / "a.ab.part")]
    assert source.fetch_many(items) == []
    assert (tmp_path / "a.ab.part").read_bytes() == b"a" * 100
    tar_cmd = next(cmd for cmd in shells if cmd.startswith("tar"))
    assert "-czf" in tar_cmd


def test_fetch_many_empty_items_makes_no_device_calls(tmp_path: Path):
    source = _make_source()
    device = Mock()
    source._device = device
    assert source.fetch_many([]) == []
    device.shell.assert_not_called()
    device.pull.assert_not_called()


def test_fetch_many_batch_disabled_pulls_per_file(tmp_path: Path):
    source = _make_source()
    source._batch = False
    files = {"a.ab": b"a" * 10, "b.ab": b"b" * 10}
    device = _attach_device(source, files)

    items = [
        (FileInfo(rel=f"characters/{name}", size=10), tmp_path / f"{name}.part")
        for name in files
    ]
    assert source.fetch_many(items) == []
    assert (tmp_path / "a.ab.part").read_bytes() == b"a" * 10
    assert (tmp_path / "b.ab.part").read_bytes() == b"b" * 10
    assert device.pull.call_count == 2
    device.shell.assert_not_called()


def test_fetch_many_per_file_pull_shows_file_counter(tmp_path: Path):
    source = _make_source()
    source._batch = False
    files = {"a.ab": b"a" * 10, "b.ab": b"b" * 10}
    device = _attach_device(source, files)
    positions: list[tuple[int, int] | None] = []

    def fake_pull(device_path, local_path, progress_callback=None, **kwargs):
        positions.append(progress_callback._position)
        name = device_path.rsplit("/", 1)[-1]
        data = files[name]
        progress_callback(device_path, len(data), len(data))
        Path(local_path).write_bytes(data)

    device.pull.side_effect = fake_pull

    items = [
        (FileInfo(rel="characters/a.ab", size=10), tmp_path / "a.ab.part"),
        (FileInfo(rel="characters/b.ab", size=10), tmp_path / "b.ab.part"),
    ]
    assert source.fetch_many(items) == []
    # [done/total]: 0 and 1 files already pulled out of 2
    assert positions == [(0, 2), (1, 2)]


def test_fetch_many_single_file_has_no_counter(tmp_path: Path):
    source = _make_source()
    source._batch = False
    device = _attach_device(source, {"a.ab": b"a" * 10})
    positions: list[tuple[int, int] | None] = []

    def fake_pull(device_path, local_path, progress_callback=None, **kwargs):
        positions.append(progress_callback._position)
        data = b"a" * 10
        progress_callback(device_path, len(data), len(data))
        Path(local_path).write_bytes(data)

    device.pull.side_effect = fake_pull

    items = [(FileInfo(rel="characters/a.ab", size=10), tmp_path / "a.ab.part")]
    assert source.fetch_many(items) == []
    assert positions == [None]


def test_fetch_many_fallback_pull_shows_file_counter(tmp_path: Path):
    source = _make_source()
    device = Mock()

    def fake_shell(command, **kwargs):
        return "EXIT:0" if command.startswith("tar") else ""

    def fake_pull(device_path, local_path, progress_callback=None, **kwargs):
        if device_path.startswith("/data/local/tmp"):
            data = _tar_bytes({"a.ab": b"a" * 100})  # pack misses b.ab and c.ab
        else:
            data = {
                "/sdcard/game/avg/characters/b.ab": b"b" * 50,
                "/sdcard/game/avg/characters/c.ab": b"c" * 99,
            }[device_path]
        progress_callback(device_path, len(data), len(data))
        Path(local_path).write_bytes(data)

    device.shell.side_effect = fake_shell
    device.pull.side_effect = fake_pull
    source._device = device

    items = [
        (FileInfo(rel="characters/a.ab", size=100), tmp_path / "a.ab.part"),
        (FileInfo(rel="characters/b.ab", size=50), tmp_path / "b.ab.part"),
        (FileInfo(rel="characters/c.ab", size=99), tmp_path / "c.ab.part"),
    ]
    assert source.fetch_many(items) == []
    assert device.pull.call_count == 3  # 1 pack + 2 fallbacks
    # fallback counter counts within the fallback list only: [0/2], [1/2]
    fallback_positions = [
        call.kwargs["progress_callback"]._position
        for call in device.pull.call_args_list[1:]
    ]
    assert fallback_positions == [(0, 2), (1, 2)]
