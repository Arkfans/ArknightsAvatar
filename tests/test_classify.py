import json
from pathlib import Path

from PIL import Image

from arknightsavatar.classify import classify_character_dir, classify_characters, main


def _write_png(path: Path, size: tuple[int, int] = (4, 4)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (255, 0, 0, 255)).save(path)


def _write_meta(char_dir: Path, textures: dict[str, list[int]]) -> None:
    (char_dir / "meta.json").write_text(
        json.dumps(
            {
                "source": {"rel": f"characters/{char_dir.name}.ab", "sha256": "sha"},
                "textures": textures,
                "sprites": list(textures),
                "face_groups": [],
            }
        ),
        encoding="utf8",
    )


def test_underscore_variants_are_diffs(tmp_path: Path):
    """序号 >1 的 _n 全幅图是差分（如 avg_1012_skadisp_2.._8）。"""
    char_dir = tmp_path / "avg_1012_skadisp_1"
    char_dir.mkdir()
    _write_meta(char_dir, {f"avg_1012_skadisp_{i}": [1024, 1024] for i in range(1, 9)})
    for i in range(1, 9):
        _write_png(char_dir / f"avg_1012_skadisp_{i}.png", (1024, 1024))

    result = classify_character_dir(char_dir)

    assert result.status == "ok"
    assert result.unassigned == []
    assert list(result.bases) == ["avg_1012_skadisp_1.png"]
    assert result.bases["avg_1012_skadisp_1.png"] == [
        f"avg_1012_skadisp_{i}.png" for i in range(2, 9)
    ]


def test_dollar_multi_base_diffs_grouped(tmp_path: Path):
    """多底图角色：1$1 属于 base1，1$2 属于 base2。"""
    char_dir = tmp_path / "avg_1014_nearl2_1"
    char_dir.mkdir()
    _write_meta(
        char_dir,
        {
            "avg_1014_nearl2_1$1": [1024, 1024],
            "avg_1014_nearl2_1$2": [1024, 1024],
            "1$1": [64, 64],
            "2$1": [64, 64],
            "1$2": [64, 64],
            "2$2": [64, 64],
        },
    )
    for n in ("avg_1014_nearl2_1$1", "avg_1014_nearl2_1$2", "1$1", "2$1", "1$2", "2$2"):
        _write_png(
            char_dir / f"{n}.png",
            (64, 64) if "$" in n and not n.startswith("avg") else (1024, 1024),
        )

    result = classify_character_dir(char_dir)

    assert result.status == "ok"
    assert result.unassigned == []
    assert result.bases["avg_1014_nearl2_1$1.png"] == ["1$1.png", "2$1.png"]
    assert result.bases["avg_1014_nearl2_1$2.png"] == ["1$2.png", "2$2.png"]


def test_skin_numbered_dir_base_is_seq1(tmp_path: Path):
    """皮肤号目录（char_017_homura_3）底图是 _1，_2/_3 是差分。"""
    char_dir = tmp_path / "char_017_homura_3"
    char_dir.mkdir()
    _write_meta(char_dir, {f"char_017_homura_{i}": [1024, 1024] for i in (1, 2, 3)})
    for i in (1, 2, 3):
        _write_png(char_dir / f"char_017_homura_{i}.png", (1024, 1024))

    result = classify_character_dir(char_dir)

    assert result.status == "ok"
    assert list(result.bases) == ["char_017_homura_1.png"]
    assert result.bases["char_017_homura_1.png"] == [
        "char_017_homura_2.png",
        "char_017_homura_3.png",
    ]


def test_bare_root_preferred_over_numbered(tmp_path: Path):
    """裸根名存在时它是唯一底图，_1.._7 都是差分（char_201_moeshd）。"""
    char_dir = tmp_path / "char_201_moeshd"
    char_dir.mkdir()
    _write_meta(
        char_dir,
        {f"char_201_moeshd_{i}": [1024, 1024] for i in range(1, 8)}
        | {"char_201_moeshd": [1024, 1024]},
    )
    _write_png(char_dir / "char_201_moeshd.png", (1024, 1024))
    for i in range(1, 8):
        _write_png(char_dir / f"char_201_moeshd_{i}.png", (1024, 1024))

    result = classify_character_dir(char_dir)

    assert result.status == "ok"
    assert list(result.bases) == ["char_201_moeshd.png"]
    assert len(result.bases["char_201_moeshd.png"]) == 7
    assert "char_201_moeshd_1.png" in result.bases["char_201_moeshd.png"]


def test_npc_id_bare_base(tmp_path: Path):
    """npc 编号目录：裸名 avg_npc_010 是底图，_2 是差分。"""
    char_dir = tmp_path / "avg_npc_010"
    char_dir.mkdir()
    _write_meta(char_dir, {"avg_npc_010": [1024, 1024], "avg_npc_010_2": [1024, 1024]})
    _write_png(char_dir / "avg_npc_010.png", (1024, 1024))
    _write_png(char_dir / "avg_npc_010_2.png", (1024, 1024))

    result = classify_character_dir(char_dir)

    assert result.status == "ok"
    assert result.bases == {"avg_npc_010.png": ["avg_npc_010_2.png"]}


def test_hash_series_base_is_hash1(tmp_path: Path):
    """#1..#n 系列：仅 #1 是底图。"""
    char_dir = tmp_path / "char_264_mountain_1"
    char_dir.mkdir()
    _write_meta(char_dir, {f"char_264_Mountain_1#{i}": [1024, 1024] for i in (1, 2, 3)})
    for i in (1, 2, 3):
        _write_png(char_dir / f"char_264_Mountain_1#{i}.png", (1024, 1024))

    result = classify_character_dir(char_dir)

    assert result.status == "ok"
    assert list(result.bases) == ["char_264_Mountain_1#1.png"]
    assert result.bases["char_264_Mountain_1#1.png"] == [
        "char_264_Mountain_1#2.png",
        "char_264_Mountain_1#3.png",
    ]


def test_no_base_fallback_smallest_string(tmp_path: Path):
    """无底图时兜底取字符串排序最小的文件为底图（char_242_mayer#2）。"""
    char_dir = tmp_path / "char_242_mayer"
    char_dir.mkdir()
    _write_meta(char_dir, {f"char_242_mayer#{i}": [1024, 1024] for i in (2, 3, 4, 5)})
    for i in (2, 3, 4, 5):
        _write_png(char_dir / f"char_242_mayer#{i}.png", (1024, 1024))

    result = classify_character_dir(char_dir)

    assert result.status == "ok"
    assert result.bases == {
        "char_242_mayer#2.png": [f"char_242_mayer#{i}.png" for i in (3, 4, 5)]
    }
    assert result.unassigned == []


def test_empty_status(tmp_path: Path):
    empty_dir = tmp_path / "avg_empty_1"
    empty_dir.mkdir()
    assert classify_character_dir(empty_dir).status == "empty"


def test_single_image_any_name_is_base(tmp_path: Path):
    """仅一张图片时，无论名称如何都算底图。"""
    char_dir = tmp_path / "char_242_mayer"
    char_dir.mkdir()
    _write_meta(char_dir, {"char_242_mayer#2": [1024, 1024]})
    _write_png(char_dir / "char_242_mayer#2.png", (1024, 1024))

    result = classify_character_dir(char_dir)

    assert result.status == "ok"
    assert result.bases == {"char_242_mayer#2.png": []}
    assert result.unassigned == []


def test_case_insensitive_base_match(tmp_path: Path):
    char_dir = tmp_path / "avg_274_astesia_1"
    char_dir.mkdir()
    _write_meta(
        char_dir, {"avg_274_Astesia_1": [1024, 1024], "avg_274_Astesia_2": [1024, 1024]}
    )
    _write_png(char_dir / "avg_274_Astesia_1.png", (1024, 1024))
    _write_png(char_dir / "avg_274_Astesia_2.png", (1024, 1024))

    result = classify_character_dir(char_dir)

    assert result.status == "ok"
    assert result.bases == {"avg_274_Astesia_1.png": ["avg_274_Astesia_2.png"]}


def test_mixed_character_bundle_fallback(tmp_path: Path):
    """跨角色混合目录：以目录同名纹理为底图，客串纹理为差分。"""
    char_dir = tmp_path / "char_190_clour"
    char_dir.mkdir()
    _write_meta(
        char_dir,
        {"char_190_clour": [1024, 1024], "char_007_closre_1": [1024, 1024]},
    )
    _write_png(char_dir / "char_190_clour.png", (1024, 1024))
    _write_png(char_dir / "char_007_closre_1.png", (1024, 1024))

    result = classify_character_dir(char_dir)

    assert result.status == "ok"
    assert result.bases == {"char_190_clour.png": ["char_007_closre_1.png"]}


def test_missing_meta_fallback_to_pillow(tmp_path: Path):
    char_dir = tmp_path / "avg_126_shotst_1"
    char_dir.mkdir()
    _write_png(char_dir / "avg_126_shotst_1.png", (1024, 1024))
    _write_png(char_dir / "1.png", (128, 128))

    result = classify_character_dir(char_dir)

    assert result.status == "ok"
    assert result.bases == {"avg_126_shotst_1.png": ["1.png"]}
    assert result.sizes["avg_126_shotst_1.png"] == [1024, 1024]
    assert result.sizes["1.png"] == [128, 128]


def test_classify_characters_stats(tmp_path: Path):
    ok_dir = tmp_path / "avg_ok_1"
    ok_dir.mkdir()
    _write_png(ok_dir / "avg_ok_1.png", (1024, 1024))
    _write_png(ok_dir / "1.png", (64, 64))

    fallback_dir = tmp_path / "char_242_mayer"
    fallback_dir.mkdir()
    _write_png(fallback_dir / "char_242_mayer#2.png", (1024, 1024))
    _write_png(fallback_dir / "char_242_mayer#3.png", (1024, 1024))

    empty_dir = tmp_path / "avg_empty_1"
    empty_dir.mkdir()

    report = classify_characters(tmp_path)

    assert report.stats == {
        "total": 3,
        "ok": 2,
        "empty": 1,
        "no_base": 0,
        "base_files": 2,
        "diff_files": 2,
    }
    assert list(report.characters) == ["avg_empty_1", "avg_ok_1", "char_242_mayer"]
    payload = report.as_dict()
    assert set(payload) == {"generated_at", "characters_dir", "stats", "characters"}


def test_cli_writes_report(capsys, tmp_path: Path):
    char_dir = tmp_path / "avg_007_closre_1"
    char_dir.mkdir()
    _write_png(char_dir / "avg_007_closre_1$1.png", (1024, 1024))
    _write_png(char_dir / "1$1.png", (64, 64))
    output = tmp_path / "report.json"

    code = main(["--characters-dir", str(tmp_path), "--output", str(output)])

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf8"))
    assert payload["stats"]["total"] == 1
    assert payload["stats"]["ok"] == 1
    char = payload["characters"]["avg_007_closre_1"]
    assert char["bases"]["avg_007_closre_1$1.png"]["diff"] == ["1$1.png"]
    assert "primary_base" not in char
    stdout = capsys.readouterr().out
    assert "characters: 1" in stdout
    assert "ok: 1" in stdout
    assert "report written" in stdout


def test_cli_output_stdout_dash(capsys, tmp_path: Path):
    char_dir = tmp_path / "avg_ok_1"
    char_dir.mkdir()
    _write_png(char_dir / "avg_ok_1.png", (1024, 1024))

    code = main(["--characters-dir", str(tmp_path), "--output", "-"])

    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("{"))
    payload = json.loads("\n".join(lines[start:]))
    assert payload["stats"]["total"] == 1
    assert payload["characters"]["avg_ok_1"]["status"] == "ok"


def test_cli_missing_dir(capsys, tmp_path: Path):
    code = main(["--characters-dir", str(tmp_path / "nope")])
    assert code == 1
    assert "not found" in capsys.readouterr().err
