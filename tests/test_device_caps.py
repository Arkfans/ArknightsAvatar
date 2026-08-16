import tempfile
from pathlib import Path
from unittest.mock import Mock

import adb_shell.adb_device

import arknightsavatar.device_caps as dc
from arknightsavatar.config import AdbConfig, Config
from arknightsavatar.device_caps import (
    DeviceCaps,
    detect_device_caps,
    render_caps_report,
)
from arknightsavatar.sources.adb import AdbSource
from arknightsavatar.sources.apk_adb import ApkAdbSource


def _fake_device(outputs: list[tuple[str, str]], *, push_ok: bool = True) -> Mock:
    """A fake device answering shell commands by first needle match."""
    device = Mock()

    def fake_shell(command, **kwargs):
        for needle, output in outputs:
            if needle in command:
                return output
        return ""

    device.shell.side_effect = fake_shell
    if not push_ok:
        device.push.side_effect = RuntimeError("push denied")
    return device


def _all_yes_device() -> Mock:
    return _fake_device(
        [
            (
                "command -v",
                "OK ls\nOK cat\nOK grep\nOK printf\nOK rm\nOK mkdir\nOK tar\nOK gzip\nOK unzip\n",
            ),
            ("tar -czf", "CAPS:OK"),
            ("tar -cf", "CAPS:OK"),
            ("unzip -l", "CAPS:OK"),
            ("unzip -p", "CAPS:OK"),
        ]
    )


def test_probe_commands_parses_existence():
    device = _fake_device([("command -v", "OK ls\nOK tar\nOK unzip\n")])
    found = dc._probe_commands(device)
    assert found["ls"] and found["tar"] and found["unzip"]
    assert not found["gzip"] and not found["cat"]


def test_detect_full_suite_all_available():
    device = _all_yes_device()
    caps = detect_device_caps(device)

    assert caps.ls and caps.cat and caps.grep and caps.printf and caps.rm and caps.mkdir
    assert caps.tar and caps.tar_list and caps.tar_gzip
    assert caps.gzip
    assert caps.unzip and caps.unzip_list and caps.unzip_pipe
    assert caps.adb_batch_ok and caps.apk_batch_ok and caps.compress_ok

    # unzip probe pushed one tiny zip and cleaned it up again
    assert device.push.call_count == 1
    remote = device.push.call_args.args[1]
    assert remote.startswith(
        "/data/local/tmp/arknights_caps_unzip_"
    ) and remote.endswith(".zip")
    rm_calls = [
        call.args[0]
        for call in device.shell.call_args_list
        if call.args[0].startswith("rm -f")
    ]
    assert any("arknights_caps_unzip_" in cmd for cmd in rm_calls)
    assert not list(Path(tempfile.gettempdir()).glob("arknights_caps_*.zip"))


def test_detect_missing_tools_skips_functional_probes():
    device = _fake_device([("command -v", "OK tar\n")])
    caps = detect_device_caps(device)

    assert caps.tar is True
    assert caps.unzip is False and caps.gzip is False
    # tar exists but the packing probe failed -> no batch paths
    assert caps.tar_list is False and caps.tar_gzip is False
    assert not caps.adb_batch_ok and not caps.apk_batch_ok
    device.push.assert_not_called()


def test_detect_can_skip_probes():
    device = _fake_device([("command -v", "OK tar\nOK unzip\n")])
    caps = detect_device_caps(device, probe_tar=False, probe_unzip=False)
    assert caps.tar is True and caps.unzip is True
    assert (
        caps.tar_list is False and caps.unzip_list is False and caps.unzip_pipe is False
    )
    device.push.assert_not_called()


def test_detect_shell_errors_count_as_unsupported():
    device = Mock()
    device.shell.side_effect = OSError("device gone")
    assert detect_device_caps(device) == DeviceCaps()  # all defaults False


def test_tar_probe_failure_still_cleans_scratch():
    device = _fake_device([("command -v", "OK tar\n"), ("tar -cf", "")])
    caps = detect_device_caps(device)
    assert caps.tar_list is False and caps.tar_gzip is False
    rm_calls = [
        call.args[0]
        for call in device.shell.call_args_list
        if call.args[0].startswith("rm -rf")
    ]
    assert any("arknights_caps_list_" in cmd for cmd in rm_calls)
    assert any("arknights_caps_gz_" in cmd for cmd in rm_calls)


def test_unzip_probe_falls_back_to_usage_text_when_push_fails():
    device = _fake_device(
        [
            ("command -v", "OK unzip\n"),
            ("unzip -h", "usage: unzip [-d DIR] [-loq] FILE[.zip] [FILE...]\n"),
        ],
        push_ok=False,
    )
    caps = detect_device_caps(device)
    assert caps.unzip is True
    assert caps.unzip_list is True  # "-l" appears in the usage text
    assert caps.unzip_pipe is False  # "-p" does not
    assert not caps.apk_batch_ok


def test_device_caps_properties():
    caps = DeviceCaps(tar=True, tar_list=True)
    assert caps.adb_batch_ok
    assert not caps.apk_batch_ok  # unzip missing
    assert not caps.compress_ok

    full = DeviceCaps(
        tar=True, tar_list=True, unzip=True, unzip_list=True, unzip_pipe=True
    )
    assert full.apk_batch_ok

    assert DeviceCaps(tar_gzip=True).compress_ok


def test_render_caps_report():
    caps = DeviceCaps(
        ls=True,
        cat=True,
        grep=True,
        printf=True,
        rm=True,
        mkdir=True,
        tar=True,
        tar_list=True,
        tar_gzip=False,
        gzip=True,
        unzip=True,
        unzip_list=True,
        unzip_pipe=True,
    )
    report = render_caps_report(caps)
    assert report.startswith("device capability report:")
    assert "ls=yes" in report and "cat=yes" in report
    assert "pack(tar -cf -C -T)=yes" in report and "gzip(tar -czf)=no" in report
    assert "list(unzip -l)=yes" in report and "pipe(unzip -p)=yes" in report
    assert "adb batch (device-side tar packing):  available" in report
    assert "apk batch (device-side unzip + tar):  available" in report
    assert "--compress (gzip pack):               unavailable" in report


def _patch_connection(monkeypatch, device: Mock):
    from arknightsavatar.sources import device as device_mod

    monkeypatch.setattr(adb_shell.adb_device, "AdbDeviceTcp", lambda host, port: device)
    monkeypatch.setattr(device_mod, "load_rsa_keys", lambda *args, **kwargs: [])
    monkeypatch.setattr(device_mod, "connect_device", lambda *args, **kwargs: None)


def test_main_reports_caps(monkeypatch, tmp_path, capsys):
    config = Config(adb=AdbConfig(host="1.2.3.4", port=5555))
    monkeypatch.setattr(dc, "load_config", lambda *args, **kwargs: config)
    _patch_connection(monkeypatch, _all_yes_device())

    assert dc.main(["--config", str(tmp_path / "config.toml")]) == 0
    out = capsys.readouterr().out
    assert "connected: 1.2.3.4:5555" in out
    assert "device capability report:" in out
    assert "adb batch (device-side tar packing):  available" in out
    assert "apk batch (device-side unzip + tar):  available" in out


def test_main_reports_connection_error(monkeypatch, capsys):
    from arknightsavatar.sources import device as device_mod

    monkeypatch.setattr(dc, "load_config", lambda *args, **kwargs: Config())

    def boom(*args, **kwargs):
        raise RuntimeError("auth timeout")

    monkeypatch.setattr(device_mod, "connect_device", boom)
    assert dc.main([]) == 1
    assert "RuntimeError: auth timeout" in capsys.readouterr().err


# --- wiring: sources consult caps before enabling batch/compress ---


def _patch_adb_source(monkeypatch, caps: DeviceCaps):
    from arknightsavatar.sources import adb as adb_mod

    captured: dict = {}

    def fake_detect(device, **kwargs):
        captured.update(kwargs)
        return caps

    monkeypatch.setattr(adb_mod, "detect_device_caps", fake_detect)
    monkeypatch.setattr(adb_mod, "connect_device", lambda *args, **kwargs: None)
    monkeypatch.setattr(adb_mod, "load_rsa_keys", lambda *args, **kwargs: [])
    monkeypatch.setattr(adb_shell.adb_device, "AdbDeviceTcp", lambda host, port: Mock())
    return captured


def test_adb_source_disables_batch_and_compress_without_tar(monkeypatch):
    captured = _patch_adb_source(monkeypatch, DeviceCaps(tar=True, tar_list=False))
    source = AdbSource("127.0.0.1", 5555, "/sdcard/game", batch=True, compress=True)
    assert captured == {"probe_unzip": False}  # tar source never probes unzip
    assert source._batch is False
    assert source._compress is False


def test_adb_source_disables_compress_without_gzip(monkeypatch):
    _patch_adb_source(monkeypatch, DeviceCaps(tar=True, tar_list=True, tar_gzip=False))
    source = AdbSource("127.0.0.1", 5555, "/sdcard/game", batch=True, compress=True)
    assert source._batch is True
    assert source._compress is False


def test_adb_source_keeps_batch_when_caps_ok(monkeypatch):
    _patch_adb_source(monkeypatch, DeviceCaps(tar=True, tar_list=True, tar_gzip=True))
    source = AdbSource("127.0.0.1", 5555, "/sdcard/game", batch=True, compress=True)
    assert source._batch is True
    assert source._compress is True


def test_adb_source_no_detection_when_batch_and_compress_disabled(monkeypatch):
    from arknightsavatar.sources import adb as adb_mod

    called = []
    monkeypatch.setattr(adb_mod, "detect_device_caps", lambda *a, **k: called.append(1))
    monkeypatch.setattr(adb_mod, "connect_device", lambda *args, **kwargs: None)
    monkeypatch.setattr(adb_mod, "load_rsa_keys", lambda *args, **kwargs: [])
    monkeypatch.setattr(adb_shell.adb_device, "AdbDeviceTcp", lambda host, port: Mock())

    AdbSource("127.0.0.1", 5555, "/sdcard/game", batch=False, compress=False)
    assert called == []


def _patch_apk_adb_source(monkeypatch, caps: DeviceCaps):
    from arknightsavatar.sources import apk_adb as apk_mod

    monkeypatch.setattr(apk_mod, "detect_device_caps", lambda device, **kwargs: caps)
    monkeypatch.setattr(apk_mod, "connect_device", lambda *args, **kwargs: None)
    monkeypatch.setattr(apk_mod, "load_rsa_keys", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        apk_mod, "installed_apk_paths", lambda *args, **kwargs: ["/data/app/base.apk"]
    )
    monkeypatch.setattr(adb_shell.adb_device, "AdbDeviceTcp", lambda host, port: Mock())


def test_apk_adb_source_disables_batch_without_unzip(monkeypatch):
    _patch_apk_adb_source(
        monkeypatch,
        DeviceCaps(
            tar=True, tar_list=True, unzip=True, unzip_list=False, unzip_pipe=False
        ),
    )
    source = ApkAdbSource("127.0.0.1", 5555, "com.hypergryph.arknights", batch=True)
    assert source._batch is False


def test_apk_adb_source_disables_batch_without_tar(monkeypatch):
    _patch_apk_adb_source(
        monkeypatch,
        DeviceCaps(
            unzip=True, unzip_list=True, unzip_pipe=True, tar=True, tar_list=False
        ),
    )
    source = ApkAdbSource("127.0.0.1", 5555, "com.hypergryph.arknights", batch=True)
    assert source._batch is False


def test_apk_adb_source_keeps_batch_when_caps_ok(monkeypatch):
    _patch_apk_adb_source(
        monkeypatch,
        DeviceCaps(
            tar=True, tar_list=True, unzip=True, unzip_list=True, unzip_pipe=True
        ),
    )
    source = ApkAdbSource("127.0.0.1", 5555, "com.hypergryph.arknights", batch=True)
    assert source._batch is True
