from arknightsavatar import cli
from arknightsavatar import __version__


def test_help_exits_zero(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "usage: arknightsavatar" in out
    assert "run" in out
    assert "sync-cache" in out
    assert "build-model" in out
    assert "detect-bases" in out


def test_version(capsys):
    assert cli.main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_unknown_subcommand(capsys):
    assert cli.main(["nope"]) == 2
    err = capsys.readouterr().err
    assert "unknown subcommand: nope" in err


def test_dispatch_to_tool(monkeypatch, capsys):
    calls = []

    class FakeModule:
        @staticmethod
        def main(argv):
            calls.append(argv)
            return 7

    monkeypatch.setattr(cli.importlib, "import_module", lambda name: FakeModule())
    assert cli.main(["detect", "--conf", "0.5"]) == 7
    assert calls == [["--conf", "0.5"]]


def test_import_failure_reports_error(monkeypatch, capsys):
    def boom(name):
        raise ImportError("missing")

    monkeypatch.setattr(cli.importlib, "import_module", boom)
    assert cli.main(["fetch"]) == 1
    assert "cannot import" in capsys.readouterr().err
