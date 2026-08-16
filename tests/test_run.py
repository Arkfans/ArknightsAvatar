import json

from arknightsavatar import run


def _args(**overrides):
    argv = [
        "--from",
        "fetch",
        "--until",
        "npc-json",
        "--raw-dir",
        "raw",
        "--unpacked-dir",
        "unpacked",
        "--characters-dir",
        "unpacked/characters",
        "--avatars-dir",
        "unpacked/avatars",
        "--classified",
        "recognition/characters_classified.json",
        "--match",
        "recognition/avatar_match.json",
        "--derive-model",
        "recognition/derive/model.json",
        "--extract-report",
        "recognition/avatar_extract.json",
        "--export-dir",
        "export",
        "--export-webp-dir",
        "export_webp",
        "--npc-json",
        "arknights_npc.json",
        "--stats-out",
        "stats/run_stats.json",
    ]
    for key, value in overrides.items():
        if value is None:
            continue
        flag = {"from_step": "--from", "until_step": "--until"}.get(
            key, f"--{key.replace('_', '-')}"
        )
        if value is False:
            continue
        if value is True:
            argv += [flag]
        else:
            argv += [flag, str(value)]
    return run.build_parser().parse_args(argv)


def test_step_argv_fetch():
    args = _args(source="adb", category="all", config=None, force=False)
    assert run.step_argv("fetch", args) == [
        "--source",
        "adb",
        "--category",
        "all",
        "--raw-dir",
        "raw",
    ]


def test_step_argv_fetch_default_source_is_both():
    args = _args()
    argv = run.step_argv("fetch", args)
    assert argv[:3] == ["--source", "adb", "apk"]


def test_step_argv_fetch_with_config_and_force():
    args = _args(config="cfg.toml", force=True)
    argv = run.step_argv("fetch", args)
    assert argv == [
        "--source",
        "adb",
        "apk",
        "--category",
        "all",
        "--raw-dir",
        "raw",
        "--config",
        "cfg.toml",
        "--force",
    ]


def test_step_argv_extract():
    args = _args(device="cpu", limit=3)
    argv = run.step_argv("extract", args)
    assert argv[argv.index("--device") + 1] == "cpu"
    assert argv[argv.index("--limit") + 1] == "3"
    assert argv[argv.index("--derive-model") + 1] == "recognition/derive/model.json"


def test_step_argv_npc_json():
    args = _args()
    argv = run.step_argv("npc-json", args)
    assert argv == [
        "--export-dir",
        "export",
        "-o",
        "arknights_npc.json",
        "--classified",
        "recognition/characters_classified.json",
    ]


def test_step_argv_match():
    args = _args()
    assert run.step_argv("match", args) == [
        "--classified",
        "recognition/characters_classified.json",
        "--characters-dir",
        "unpacked/characters",
        "--avatars-dir",
        "unpacked/avatars",
        "--output",
        "recognition/avatar_match.json",
        "--limit",
        "0",
    ]


def test_step_argv_match_force():
    args = _args(force=True)
    argv = run.step_argv("match", args)
    assert argv[-1] == "--force"
    assert argv.count("--force") == 1


def test_run_steps_order_and_early_stop(monkeypatch):
    calls = []

    def fake_run_step(name, argv, modules=None):
        calls.append(name)
        return 1 if name == "match" else 0

    args = _args()
    results = run.run_steps(args, run_step_func=fake_run_step)
    assert calls == ["fetch", "unpack", "classify", "match"]
    assert results == {"fetch": 0, "unpack": 0, "classify": 0, "match": 1}


def test_run_steps_from_until(monkeypatch):
    calls = []

    def fake_run_step(name, argv, modules=None):
        calls.append(name)
        return 0

    args = _args(from_step="extract", until_step="export-webp")
    run.run_steps(args, run_step_func=fake_run_step)
    assert calls == ["extract", "export-webp"]


def test_main_requires_derive_model_for_extract(tmp_path, monkeypatch, capsys):
    args = [
        "--from",
        "extract",
        "--derive-model",
        str(tmp_path / "missing.json"),
        "--stats-out",
        str(tmp_path / "stats.json"),
    ]
    assert run.main(args) == 1
    assert "derive model not found" in capsys.readouterr().err


def test_main_writes_stats_and_fails_on_step(tmp_path, monkeypatch, capsys):
    def fake_run_step(name, argv, modules=None):
        return 1 if name == "npc-json" else 0

    monkeypatch.setattr(run, "run_step", fake_run_step)
    (tmp_path / "model.json").write_text("{}", encoding="utf8")
    stats_out = tmp_path / "stats.json"
    code = run.main(
        [
            "--from",
            "classify",
            "--derive-model",
            str(tmp_path / "model.json"),
            "--stats-out",
            str(stats_out),
        ]
    )
    assert code == 1
    payload = json.loads(stats_out.read_text(encoding="utf8"))
    assert payload["steps"]["npc-json"] == 1
    assert payload["ok"] is False


def test_main_success_writes_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "run_step", lambda name, argv, modules=None: 0)
    (tmp_path / "model.json").write_text("{}", encoding="utf8")
    stats_out = tmp_path / "stats.json"
    code = run.main(
        [
            "--from",
            "classify",
            "--until",
            "classify",
            "--derive-model",
            str(tmp_path / "model.json"),
            "--stats-out",
            str(stats_out),
        ]
    )
    assert code == 0
    payload = json.loads(stats_out.read_text(encoding="utf8"))
    assert payload["steps"] == {"classify": 0}
    assert payload["ok"] is True


def test_from_after_until_is_error(tmp_path, monkeypatch, capsys):
    code = run.main(
        [
            "--from",
            "npc-json",
            "--until",
            "fetch",
            "--stats-out",
            str(tmp_path / "stats.json"),
        ]
    )
    assert code == 1
    assert "--from must not be after --until" in capsys.readouterr().err


def test_check_derive_model_hint(tmp_path, capsys):
    missing = tmp_path / "derive" / "model.json"
    assert run.check_derive_model(missing) is False
    err = capsys.readouterr().err
    assert "derive-model" in err
    assert "sync-cache" in err
    (tmp_path / "derive").mkdir()
    (tmp_path / "derive" / "model.json").write_text("{}", encoding="utf8")
    assert run.check_derive_model(missing) is True
