from arknightsavatar import pull


def test_pull_runs_pull_apk_then_fetch(monkeypatch):
    calls = []

    def fake_run_step(name, argv):
        calls.append((name, argv))
        return 0

    monkeypatch.setattr(pull, "_run_step", fake_run_step)
    code = pull.main(["--source", "adb", "--package", "com.example.game"])
    assert code == 0
    names = [name for name, _ in calls]
    assert names == ["pull-apk", "fetch"]
    pull_argv = calls[0][1]
    assert pull_argv[pull_argv.index("--package") + 1] == "com.example.game"


def test_pull_no_pull_skips_apk(monkeypatch):
    calls = []

    def fake_run_step(name, argv):
        calls.append(name)
        return 0

    monkeypatch.setattr(pull, "_run_step", fake_run_step)
    assert pull.main(["--no-pull"]) == 0
    assert calls == ["fetch"]


def test_pull_stops_on_failure(monkeypatch):
    def fake_run_step(name, argv):
        return 1

    monkeypatch.setattr(pull, "_run_step", fake_run_step)
    assert pull.main(["--no-pull"]) == 1
