import json
import subprocess
from pathlib import Path

import pytest

from arknightsavatar import cli, setup
from arknightsavatar.config import DataRepoCategory


def _git_available() -> bool:
    result = subprocess.run(["git", "--version"], capture_output=True, text=True)
    return result.returncode == 0


pytestmark = pytest.mark.skipif(not _git_available(), reason="git CLI not available")


def _run_git(cwd: Path, *argv: str) -> None:
    result = subprocess.run(["git", *argv], cwd=str(cwd), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _make_remote(remote: Path, files: dict[str, str] | None = None) -> None:
    remote.mkdir(parents=True, exist_ok=True)
    _run_git(remote, "init", "-b", "main")
    _run_git(remote, "config", "user.name", "test")
    _run_git(remote, "config", "user.email", "test@example.com")
    for rel, content in (files or {}).items():
        path = remote / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf8")
    (remote / "README.md").write_text("data repo\n", encoding="utf8")
    _run_git(remote, "add", "-A")
    _run_git(remote, "commit", "-m", "init")


def _write_config(tmp_path: Path, url: str, categories: list[dict]) -> Path:
    config = tmp_path / "config.toml"
    config.write_text("# main config\n", encoding="utf8")
    repo = tmp_path / "data_repo.yaml"
    # JSON 是合法 YAML，data_repo 配置与主配置同目录（load_config 的默认查找位置）。
    repo.write_text(
        json.dumps({"path": "data_cache", "url": url, "branch": "main",
                    "categories": categories}),
        encoding="utf8",
    )
    return config


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")


class FakeStdin:
    def __init__(self, isatty: bool = True) -> None:
        self._isatty = isatty

    def isatty(self) -> bool:
        return self._isatty


def _keys(*tokens: str):
    iterator = iter(tokens)
    return lambda: next(iterator)


class Writer:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    def __call__(self, text: str) -> None:
        self.chunks.append(text)

    @property
    def text(self) -> str:
        return "".join(self.chunks)


# ---------- picker 逻辑（注入脚本化按键与收集写入） ----------


def test_picker_multi_defaults_to_all_selected():
    writer = Writer()
    result = setup.pick(["a", "b", "c"], multi=True, read_key=_keys("enter"), write=writer)
    assert result == [0, 1, 2]
    assert "[x] a" in writer.text
    assert "[x] c" in writer.text


def test_picker_multi_toggle_and_move():
    writer = Writer()
    result = setup.pick(
        ["a", "b", "c"], multi=True, read_key=_keys("down", "space", "enter"), write=writer
    )
    assert result == [0, 2]  # 默认全选，下移后空格取消 b
    assert "[ ] b" in writer.text


def test_picker_multi_wraparound():
    writer = Writer()
    result = setup.pick(
        ["a", "b"], multi=True, read_key=_keys("up", "space", "enter"), write=writer
    )
    assert result == [0]  # 顶部按 ↑ 环绕到末尾并取消 b


def test_picker_multi_initial_override():
    writer = Writer()
    result = setup.pick(
        ["a", "b", "c"],
        multi=True,
        initial=[1],
        read_key=_keys("enter"),
        write=writer,
    )
    assert result == [1]


def test_picker_single_enter_confirms_cursor():
    writer = Writer()
    result = setup.pick(["x", "y"], multi=False, read_key=_keys("down", "enter"), write=writer)
    assert result == [1]


def test_picker_single_letter_shortcut():
    writer = Writer()
    assert setup.pick(["x", "y"], multi=False, read_key=_keys("b"), write=writer) == [1]


def test_picker_esc_cancels():
    writer = Writer()
    assert setup.pick(["x"], multi=False, read_key=_keys("esc"), write=writer) is None


def test_picker_empty_items():
    assert setup.pick([], multi=True, read_key=_keys("enter"), write=Writer()) == []


# ---------- 选项 a：全量同步（委托 sync-cache） ----------


def test_full_mode_delegates_to_sync_cache(monkeypatch, tmp_path):
    calls = []

    def fake_main(argv):
        calls.append(argv)
        return 7

    monkeypatch.setattr(setup.sync_cache, "main", fake_main)
    config = _write_config(tmp_path, "http://example.invalid/repo.git", [])
    assert setup.main(["--full", "--config", str(config)]) == 7
    assert calls == [["--config", str(config), "--pull", "--restore"]]


def test_full_mode_without_config(monkeypatch):
    calls = []
    monkeypatch.setattr(setup.sync_cache, "main", lambda argv: calls.append(argv) or 0)
    assert setup.main(["--full"]) == 0
    assert calls == [["--pull", "--restore"]]


def test_full_with_category_rejected(capsys):
    with pytest.raises(SystemExit) as excinfo:
        setup.main(["--full", "--category", "recognition"])
    assert excinfo.value.code == 2
    assert "--full" in capsys.readouterr().err


def test_full_mode_end_to_end(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote, {"recognition/a.json": "a", "export/avg_001/base.png": "png"})
    config = _write_config(
        tmp_path,
        str(remote),
        [
            {"local": "data/recognition", "remote": "recognition"},
            {"local": "data/export", "remote": "export"},
        ],
    )
    assert setup.main(["--full", "--config", str(config)]) == 0
    # 全量同步：克隆工作副本并取回全部分类（含 export）
    assert (tmp_path / "data_cache" / ".git").is_dir()
    assert (tmp_path / "data" / "recognition" / "a.json").is_file()
    assert (tmp_path / "data" / "export" / "avg_001" / "base.png").is_file()


# ---------- 选项 b：仅下载数据文件 ----------


def test_category_name_includes_description():
    category = DataRepoCategory(local="data/recognition", remote="recognition", desc="识别数据")
    assert setup._category_name(category) == "recognition（识别数据）"
    assert setup._category_name(DataRepoCategory(local="data/schema", remote="schema")) == "schema"


def test_prompt_categories_shows_descriptions(monkeypatch, capsys):
    class FakeReader:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return "enter"

    monkeypatch.setattr(setup, "KeyReader", lambda stream: FakeReader())
    monkeypatch.setattr(setup.sys, "stdin", FakeStdin(isatty=True))
    categories = [
        DataRepoCategory(local="data/recognition", remote="recognition", desc="识别数据"),
        DataRepoCategory(local="data/schema", remote="schema"),
    ]
    assert setup._prompt_categories(categories) == [0, 1]
    out = capsys.readouterr().out
    assert "recognition（识别数据）  ←  data/recognition" in out
    assert "schema  ←  data/schema" in out


def test_download_restores_selected_categories(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(
        remote,
        {"recognition/avatar_match.json": "{}", "export/avg_001/base.png": "png"},
    )
    config = _write_config(
        tmp_path,
        str(remote),
        [
            {"local": "data/recognition", "remote": "recognition"},
            {"local": "data/export", "remote": "export"},
            {"local": "data/unpacked/avatars", "remote": "avatars"},
        ],
    )
    # 本地已有文件不应被镜像回工作副本（仅下载，不镜像不提交）
    (tmp_path / "data" / "recognition").mkdir(parents=True)
    (tmp_path / "data" / "recognition" / "local_only.json").write_text("x", encoding="utf8")

    assert (
        setup.main(
            [
                "--download",
                "--category", "recognition",
                "--category", "export",
                "--config", str(config),
            ]
        )
        == 0
    )
    assert (tmp_path / "data" / "recognition" / "avatar_match.json").is_file()
    assert (tmp_path / "data" / "export" / "avg_001" / "base.png").is_file()
    # 未选择的 avatars 分类不下载
    assert not (tmp_path / "data" / "unpacked").exists()
    # 本地独有文件不进入工作副本
    assert not (tmp_path / "data_cache" / "recognition" / "local_only.json").exists()
    assert "restored=2" in capsys.readouterr().out


def test_download_interactive_all_selected(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote, {"recognition/a.json": "a", "schema/README.md": "s"})
    config = _write_config(
        tmp_path,
        str(remote),
        [
            {"local": "data/recognition", "remote": "recognition", "desc": "识别数据"},
            {"local": "data/schema", "remote": "schema"},
        ],
    )
    monkeypatch.setattr(setup, "pick", lambda items, **kw: list(range(len(items))))
    monkeypatch.setattr(setup.sys, "stdin", FakeStdin(isatty=True))
    assert setup.main(["--download", "--config", str(config)]) == 0
    assert (tmp_path / "data" / "recognition" / "a.json").is_file()
    assert (tmp_path / "data" / "schema" / "README.md").is_file()
    # 每个下载项展示 data_repo.yaml 中的说明
    out = capsys.readouterr().out
    assert "正在下载 recognition（识别数据）" in out
    assert "正在下载 schema" in out


def test_download_unknown_category(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    config = _write_config(
        tmp_path, str(remote), [{"local": "data/recognition", "remote": "recognition"}]
    )
    assert setup.main(["--download", "--category", "nope", "--config", str(config)]) == 1
    err = capsys.readouterr().err
    assert "未知分类" in err
    assert "recognition" in err


def test_download_empty_url_without_working_copy(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = _write_config(
        tmp_path, "", [{"local": "data/recognition", "remote": "recognition"}]
    )
    assert setup.main(["--download", "--category", "recognition", "--config", str(config)]) == 1
    assert "data_repo.url is empty" in capsys.readouterr().err


def test_download_requires_tty_for_interactive(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = _write_config(
        tmp_path, "http://example.invalid/repo.git",
        [{"local": "data/recognition", "remote": "recognition"}],
    )
    monkeypatch.setattr(setup.sys, "stdin", FakeStdin(isatty=False))
    assert setup.main(["--download", "--config", str(config)]) == 1
    assert "--category" in capsys.readouterr().err


def test_menu_cancel(monkeypatch, capsys):
    monkeypatch.setattr(setup.sys, "stdin", FakeStdin(isatty=True))
    monkeypatch.setattr(setup, "pick", lambda items, **kw: None)
    assert setup.main([]) == 1
    assert "已取消" in capsys.readouterr().err


def test_menu_requires_tty(monkeypatch, capsys):
    monkeypatch.setattr(setup.sys, "stdin", FakeStdin(isatty=False))
    assert setup.main([]) == 1
    assert "--full" in capsys.readouterr().err


def test_menu_a_runs_full_sync(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(setup.sync_cache, "main", lambda argv: calls.append(argv) or 0)
    monkeypatch.setattr(setup.sys, "stdin", FakeStdin(isatty=True))
    monkeypatch.setattr(setup, "pick", lambda items, **kw: [0])
    assert setup.main([]) == 0
    assert calls == [["--pull", "--restore"]]


def test_menu_b_runs_download(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    remote = tmp_path / "remote"
    _make_remote(remote)
    _write_config(
        tmp_path,
        str(remote),
        [
            {"local": "data/recognition", "remote": "recognition"},
            {"local": "data/schema", "remote": "schema"},
        ],
    )
    monkeypatch.setattr(setup.sys, "stdin", FakeStdin(isatty=True))
    monkeypatch.setattr(setup, "pick", lambda items, **kw: [1])
    assert setup.main([]) == 0
    assert "restored=0" in capsys.readouterr().out


# ---------- 注册 ----------


def test_cli_registers_setup(capsys):
    assert cli.main([]) == 0
    assert "setup" in capsys.readouterr().out


def test_setup_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        setup.main(["--help"])
    assert excinfo.value.code == 0
    assert "全量同步" in capsys.readouterr().out
