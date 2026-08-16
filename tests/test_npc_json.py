import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from arknightsavatar import npc_json
from arknightsavatar.skip import SkipList

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def workdir():
    """Project-internal writable temp dir (system Temp is not accessible in the sandbox)."""
    base = PROJECT_ROOT / f"arknightsavatar_test_{uuid4().hex[:8]}"
    os.makedirs(base, mode=0o777)
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def test_iter_png_stems_sorted_and_ignores_non_png(workdir):
    char_dir = Path(workdir) / "export" / "avg_003_kalts_1"
    char_dir.mkdir(parents=True)
    _write_png(char_dir / "10$1.png")
    _write_png(char_dir / "2$1.png")
    _write_png(char_dir / "avg_003_kalts_1$1.png")
    _write_png(char_dir / "1$1.png")
    (char_dir / "meta.json").write_text("{}", encoding="utf8")
    (char_dir / "sub").mkdir()
    _write_png(char_dir / "sub" / "nested.png")
    assert npc_json.iter_png_stems(char_dir) == [
        "1$1",
        "10$1",
        "2$1",
        "avg_003_kalts_1$1",
    ]


def test_build_npc_avatar_map_legacy_format(workdir):
    export = Path(workdir) / "export"
    _write_png(export / "avg_003_kalts_1" / "1$1.png")
    _write_png(export / "avg_003_kalts_1" / "2$1.png")
    _write_png(export / "char_002_amiya_1" / "char_002_amiya_1.png")

    data = npc_json.build_npc_avatar_map(export)
    assert list(data) == ["avg_003_kalts_1", "char_002_amiya_1"]
    entry = data["avg_003_kalts_1"]
    assert len(entry) == 3
    assert entry[0] == []
    assert entry[1] == ["1$1", "2$1"]
    assert entry[2] == ["npc"]


def test_build_npc_avatar_map_empty(workdir):
    export = Path(workdir) / "export"
    export.mkdir(parents=True)
    assert npc_json.build_npc_avatar_map(export) == {}


def test_build_npc_avatar_map_respects_skip(workdir):
    export = Path(workdir) / "export"
    _write_png(export / "c1" / "base.png")
    _write_png(export / "c1" / "d1.png")
    _write_png(export / "c1" / "d2.png")
    _write_png(export / "c2" / "other.png")

    classified = {
        "characters": {
            "c1": {"bases": {"base.png": {"diff": ["d1.png", "d2.png"]}}},
            "c2": {"bases": {"other.png": {"diff": []}}},
        }
    }
    skip = SkipList({"c2": "skip character", "c1/base.png": "skip base"})

    data = npc_json.build_npc_avatar_map(export, skip=skip, classified=classified)

    assert data == {"c1": [[], [], ["npc"]]}


def test_build_npc_avatar_map_skip_diff_only(workdir):
    export = Path(workdir) / "export"
    _write_png(export / "c1" / "base.png")
    _write_png(export / "c1" / "d1.png")
    _write_png(export / "c1" / "d2.png")

    classified = {
        "characters": {
            "c1": {"bases": {"base.png": {"diff": ["d1.png", "d2.png"]}}},
        }
    }
    skip = SkipList({"c1/d1.png": "skip diff"})

    data = npc_json.build_npc_avatar_map(export, skip=skip, classified=classified)

    assert data == {"c1": [[], ["base", "d2"], ["npc"]]}


def test_main_end_to_end(workdir):
    export = Path(workdir) / "export"
    _write_png(export / "avg_003_kalts_1" / "1$1.png")
    _write_png(export / "avg_003_kalts_1" / "2$1.png")
    _write_png(export / "npc_2001_aya_1" / "3$1.png")
    output = Path(workdir) / "out" / "arknights_npc.json"

    assert npc_json.main(["--export-dir", str(export), "-o", str(output)]) == 0
    assert output.is_file()
    data = json.loads(output.read_text(encoding="utf8"))
    assert list(data) == ["avg_003_kalts_1", "npc_2001_aya_1"]
    assert data["avg_003_kalts_1"][1] == ["1$1", "2$1"]
    assert data["npc_2001_aya_1"][2] == ["npc"]
    # pretty printed (2-space indent)
    assert output.read_text(encoding="utf8").startswith("{\n  ")


def test_main_stdout(workdir, capsys):
    export = Path(workdir) / "export"
    _write_png(export / "c1" / "1$1.png")
    assert npc_json.main(["--export-dir", str(export), "-o", "-"]) == 0
    out = capsys.readouterr().out
    body = out.split("characters:")[0].strip()
    assert json.loads(body) == {"c1": [[], ["1$1"], ["npc"]]}
    assert "characters: 1  images: 1" in out


def test_main_error_missing_dir(workdir, capsys):
    assert npc_json.main(["--export-dir", str(Path(workdir) / "missing")]) == 1
    assert "error" in capsys.readouterr().err
