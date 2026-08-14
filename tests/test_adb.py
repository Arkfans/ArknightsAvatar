import io
from pathlib import Path
from unittest.mock import Mock

from arknightsavatar.sources.adb import AdbSource, _PullProgress, _fmt_bytes


def test_fmt_bytes():
    assert _fmt_bytes(0) == "0 B"
    assert _fmt_bytes(512) == "512 B"
    assert _fmt_bytes(1024) == "1.0 KiB"
    assert _fmt_bytes(1024 * 1024) == "1.0 MiB"
    assert _fmt_bytes(1024 ** 3) == "1.00 GiB"


def test_pull_progress_reports_percentage_bytes_and_speed():
    stream = io.StringIO()
    progress = _PullProgress("characters/a.ab", enabled=True, stream=stream)

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


def test_pull_progress_without_total_shows_no_percent():
    stream = io.StringIO()
    progress = _PullProgress("characters/a.ab", enabled=True, stream=stream)
    progress("characters/a.ab", 512, 0)
    progress.finish()

    out = stream.getvalue()
    assert "%" not in out
    assert "512 B" in out


def test_pull_progress_disabled_is_quiet():
    stream = io.StringIO()
    progress = _PullProgress("characters/a.ab", enabled=False, stream=stream)
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
