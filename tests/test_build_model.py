"""Tests for the build-model orchestration (from-zero derive model)."""

import json
from pathlib import Path

from arknightsavatar import build_model, run


def _args(argv: list[str]):
    return build_model.build_parser().parse_args(argv)


def _full_argv() -> list[str]:
    """与 test_run._args 相同的路径覆盖（run.step_argv 委托测试用）。"""
    return [
        "--config", "cfg.toml",
        "--source", "apk",
        "--category", "characters",
        "--raw-dir", "raw",
        "--unpacked-dir", "unpacked",
        "--characters-dir", "unpacked/characters",
        "--avatars-dir", "unpacked/avatars",
        "--classified", "recognition/characters_classified.json",
        "--match", "recognition/avatar_match.json",
        "--face-detect", "recognition/face_detect_matched.json",
        "--derive-dir", "recognition/derive",
        "--limit", "3",
        "--force",
    ]


def test_step_argv_shared_steps_delegate_to_run():
    args = _args(_full_argv())
    for name in ("fetch", "unpack", "classify", "match"):
        assert build_model.step_argv(name, args) == run.step_argv(name, args)


def test_step_argv_detect_bases_defaults():
    args = _args(_full_argv())
    argv = build_model.step_argv("detect-bases", args)
    assert argv == [
        "--match", "recognition/avatar_match.json",
        "--characters-dir", "unpacked/characters",
        "--threshold", "0.95",
        "--conf", "0.3",
        "--head-conf", "0.4",
        "--device", "auto",
        "--limit", "3",
        "--output", "recognition/face_detect_matched.json",
        "--no-vis",
    ]


def test_step_argv_detect_bases_custom_knobs():
    args = _args([
        "--detect-threshold", "0.9",
        "--detect-conf", "0.25",
        "--head-conf", "0.5",
        "--device", "cpu",
    ])
    argv = build_model.step_argv("detect-bases", args)
    assert argv[argv.index("--threshold") + 1] == "0.9"
    assert argv[argv.index("--conf") + 1] == "0.25"
    assert argv[argv.index("--head-conf") + 1] == "0.5"
    assert argv[argv.index("--device") + 1] == "cpu"


def test_step_argv_detect_bases_vis_dir_disables_no_vis():
    args = _args(["--vis-dir", "recognition/face_detect_vis"])
    argv = build_model.step_argv("detect-bases", args)
    assert "--no-vis" not in argv
    assert argv[argv.index("--vis-dir") + 1] == "recognition/face_detect_vis"


def test_step_argv_derive_model_absolute_source(tmp_path: Path):
    source = tmp_path / "report.json"
    args = _args([
        "--face-detect", str(source),
        "--derive-dir", "recognition/derive",
        "--min-conf", "0.85",
        "--no-compare",
    ])
    argv = build_model.step_argv("derive-model", args)
    assert argv[argv.index("--source") + 1] == str(source.resolve())
    assert argv[argv.index("--out-dir") + 1] == "recognition/derive"
    assert argv[argv.index("--min-conf") + 1] == "0.85"
    assert "--no-compare" in argv


def test_step_argv_derive_model_defaults():
    args = _args([])
    argv = build_model.step_argv("derive-model", args)
    assert argv[argv.index("--min-conf") + 1] == "0.8"
    assert "--no-compare" not in argv
    assert "--no-vis" not in argv  # derive-model 无 --no-vis


def test_run_steps_order_and_early_stop(monkeypatch):
    calls = []

    def fake_run_step(name, argv, modules=None):
        calls.append(name)
        return 1 if name == "detect-bases" else 0

    args = _args([])
    results = build_model.run_steps(args, run_step_func=fake_run_step)
    assert calls == ["fetch", "unpack", "classify", "match", "detect-bases"]
    assert results == {"fetch": 0, "unpack": 0, "classify": 0, "match": 0, "detect-bases": 1}


def test_run_steps_from_until():
    calls = []

    def fake_run_step(name, argv, modules=None):
        calls.append(name)
        return 0

    args = _args(["--from", "classify", "--until", "derive-model"])
    build_model.run_steps(args, run_step_func=fake_run_step)
    assert calls == ["classify", "match", "detect-bases", "derive-model"]


def test_main_success_writes_stats(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(run, "run_step", lambda name, argv, modules=None: 0)
    monkeypatch.setattr(build_model, "check_detect_deps", lambda: True)
    stats_out = tmp_path / "stats.json"
    code = build_model.main(["--stats-out", str(stats_out)])
    assert code == 0
    payload = json.loads(stats_out.read_text(encoding="utf8"))
    assert payload["steps"] == {name: 0 for name in build_model.BUILD_MODEL_STEPS}
    assert payload["ok"] is True


def test_main_fails_on_step_writes_stats(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(run, "run_step", lambda name, argv, modules=None: 1 if name == "derive-model" else 0)
    monkeypatch.setattr(build_model, "check_detect_deps", lambda: True)
    stats_out = tmp_path / "stats.json"
    code = build_model.main(["--stats-out", str(stats_out)])
    assert code == 1
    payload = json.loads(stats_out.read_text(encoding="utf8"))
    assert payload["steps"]["derive-model"] == 1
    assert payload["ok"] is False
    assert "failed at step(s): derive-model" in capsys.readouterr().err


def test_main_requires_detect_deps_when_in_range(tmp_path: Path, monkeypatch, capsys):
    calls = []

    def fake_run_step(name, argv, modules=None):
        calls.append(name)
        return 0

    monkeypatch.setattr(run, "run_step", fake_run_step)
    monkeypatch.setattr(build_model, "check_detect_deps", lambda: False)
    code = build_model.main(["--stats-out", str(tmp_path / "stats.json")])
    assert code == 1
    assert calls == []  # 预检失败：任何步骤都不执行


def test_main_skips_detect_precheck_outside_range(tmp_path: Path, monkeypatch):
    calls = []
    checked = []

    def fake_run_step(name, argv, modules=None):
        calls.append(name)
        return 0

    def fake_check_detect_deps():
        checked.append(True)
        return True

    monkeypatch.setattr(run, "run_step", fake_run_step)
    monkeypatch.setattr(build_model, "check_detect_deps", fake_check_detect_deps)
    stats_out = tmp_path / "stats.json"
    code = build_model.main([
        "--from", "derive-model",
        "--until", "derive-model",
        "--stats-out", str(stats_out),
    ])
    assert code == 0
    assert calls == ["derive-model"]
    assert checked == []


def test_main_from_after_until_is_error(tmp_path: Path, capsys):
    code = build_model.main(["--from", "derive-model", "--until", "fetch",
                             "--stats-out", str(tmp_path / "stats.json")])
    assert code == 1
    assert "--from must not be after --until" in capsys.readouterr().err


def test_check_detect_deps_cv2_missing(monkeypatch, capsys):
    monkeypatch.setattr(build_model.detect_bases, "cv2", None)
    assert build_model.check_detect_deps() is False
    assert "opencv-python-headless" in capsys.readouterr().err


def test_check_detect_deps_ml_missing(monkeypatch, capsys):
    monkeypatch.setattr(build_model.detect, "_check_ml_deps", lambda: False)
    assert build_model.check_detect_deps() is False
    assert "torch" in capsys.readouterr().err


def test_check_detect_deps_head_missing(monkeypatch, capsys):
    monkeypatch.setattr(build_model.detect_bases, "_check_head_deps", lambda: False)
    assert build_model.check_detect_deps() is False
    assert "dghs-imgutils" in capsys.readouterr().err


def test_check_detect_deps_ok(monkeypatch):
    monkeypatch.setattr(build_model.detect_bases, "cv2", object())
    monkeypatch.setattr(build_model.detect_bases, "np", object())
    monkeypatch.setattr(build_model.detect, "_check_ml_deps", lambda: True)
    monkeypatch.setattr(build_model.detect_bases, "_check_head_deps", lambda: True)
    assert build_model.check_detect_deps() is True
