import json

from arknightsavatar import produce, run


def test_missing_derive_model_aborts(tmp_path, capsys):
    code = produce.main(["--derive-model", str(tmp_path / "missing.json"),
                         "--stats-out", str(tmp_path / "s.json")])
    assert code == 1
    assert "derive model not found" in capsys.readouterr().err


def test_produce_runs_steps_and_writes_stats(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_run_step(name, argv, modules=None):
        calls.append(name)
        return 0

    monkeypatch.setattr(run, "run_step", fake_run_step)
    model = tmp_path / "model.json"
    model.write_text("{}", encoding="utf8")
    stats_out = tmp_path / "stats.json"
    code = produce.main(["--derive-model", str(model), "--stats-out", str(stats_out)])
    assert code == 0
    assert calls == ["classify", "match", "extract", "export-webp", "npc-json"]
    payload = json.loads(stats_out.read_text(encoding="utf8"))
    assert payload["ok"] is True
    assert payload["steps"] == {name: 0 for name in calls}
    assert "produce complete" in capsys.readouterr().out


def test_produce_stops_at_failed_step(tmp_path, monkeypatch):
    calls = []

    def fake_run_step(name, argv, modules=None):
        calls.append(name)
        return 1 if name == "extract" else 0

    monkeypatch.setattr(run, "run_step", fake_run_step)
    model = tmp_path / "model.json"
    model.write_text("{}", encoding="utf8")
    code = produce.main(["--derive-model", str(model), "--stats-out", str(tmp_path / "s.json")])
    assert code == 1
    assert calls == ["classify", "match", "extract"]
    payload = json.loads((tmp_path / "s.json").read_text(encoding="utf8"))
    assert payload["ok"] is False
