from arknightsavatar import pull


def test_pull_default_source_is_adb_and_apk(monkeypatch):
    calls = []

    def fake_run_step(name, argv):
        calls.append((name, argv))
        return 0

    monkeypatch.setattr(pull, "_run_step", fake_run_step)
    assert pull.main([]) == 0
    name, argv = calls[0]
    assert name == "fetch"
    source_args = argv[argv.index("--source") + 1 : argv.index("--category")]
    assert source_args == ["adb", "apk"]


def test_pull_skips_apk_by_default(monkeypatch):
    calls = []

    def fake_run_step(name, argv):
        calls.append(name)
        return 0

    monkeypatch.setattr(pull, "_run_step", fake_run_step)
    assert pull.main(["--source", "adb"]) == 0
    assert calls == ["fetch"]


def test_pull_with_apk_runs_pull_apk_then_fetch(monkeypatch):
    calls = []

    def fake_run_step(name, argv):
        calls.append((name, argv))
        return 0

    monkeypatch.setattr(pull, "_run_step", fake_run_step)
    code = pull.main(["--source", "adb", "--package", "com.example.game", "--with-apk"])
    assert code == 0
    names = [name for name, _ in calls]
    assert names == ["pull-apk", "fetch"]
    pull_argv = calls[0][1]
    assert pull_argv[pull_argv.index("--package") + 1] == "com.example.game"


def test_pull_stops_on_failure(monkeypatch):
    def fake_run_step(name, argv):
        return 1

    monkeypatch.setattr(pull, "_run_step", fake_run_step)
    assert pull.main(["--with-apk"]) == 1
