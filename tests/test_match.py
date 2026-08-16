import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from arknightsavatar.match import (
    AVATAR_MAX_SIZE,
    BASE_EXTEND_TOP,
    CONFIDENCE_TARGET,
    MAX_AVATAR_SIZE,
    MIN_AVATAR_SIZE,
    OUTPUT_BASE_SIZE,
    STOP_THRESHOLD,
    CharacterMatch,
    _avatar_candidates,
    _char_seq,
    _composite_on_color,
    _edit_distance,
    _is_target_character,
    _prepare_avatar,
    _prepare_base,
    _read_rgba,
    _template_match_gray,
    main,
    match_characters,
    needs_rematch,
    plan_rematch,
    template_match,
)


def _avatar_image(size: int = 180, seed: int = 0) -> Image.Image:
    """生成一个可识别的透明底方形图案，用作合成头像。"""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (20, 20, size - 20, size - 20), fill=(80 + seed * 40, 40, 200 - seed * 30, 255)
    )
    draw.ellipse(
        (size // 2 - 12, size // 2 - 12, size // 2 + 12, size // 2 + 12),
        fill=(255, 255, 255, 255),
    )
    return image


def _write_image(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _write_classified(path: Path, characters: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-09T00:00:00+00:00",
                "characters_dir": ".",
                "stats": {},
                "characters": characters,
            }
        ),
        encoding="utf8",
    )


def _base_with_avatar(
    avatar: Image.Image, at: tuple[int, int], size: tuple[int, int] = (1024, 1024)
) -> Image.Image:
    base = Image.new("RGBA", size, (0, 0, 0, 255))
    base.paste(avatar, at, avatar)
    return base


def _scaled_base_with_avatar(
    avatar: Image.Image, scale: float, at: tuple[int, int]
) -> Image.Image:
    """把头像按 scale 缩放后贴到黑色底图上，用于构造需要缩放搜索的匹配场景。"""
    scaled = avatar.resize(
        (round(avatar.width * scale), round(avatar.height * scale)), Image.LANCZOS
    )
    base = Image.new("RGBA", (1024, 1024), (0, 0, 0, 255))
    base.paste(scaled, at, scaled)
    return base


def test_name_filtering():
    assert _is_target_character("avg_003_kalts_1")
    assert _is_target_character("avg_2026_yu_1")
    assert _is_target_character("char_002_amiya_1")
    assert _is_target_character("char_242_mayer")
    assert _is_target_character("char_1011_lava2")
    assert _is_target_character("avg_274_Astesia_1")
    assert not _is_target_character("avg_npc_010")
    assert not _is_target_character("avg_6d5_1")
    assert not _is_target_character("avgnew_003_kalts_1")
    assert not _is_target_character("npc_001")
    assert not _is_target_character("some_other")


def test_char_seq():
    assert _char_seq("avg_003_kalts_1") == "003"
    assert _char_seq("char_1012_skadi2_1") == "1012"
    assert _char_seq("avg_npc_010") is None
    assert _char_seq("other_123_x") is None


def test_avatar_candidates(tmp_path: Path):
    avatars_dir = tmp_path / "avatars"
    avatars_dir.mkdir()
    names = [
        "char_003_kalts_2.png",
        "char_003_kalts_boc#6.png",
        "char_003_kalts.png",
        "char_1003_other.png",
        "char_003.png",
        "readme.txt",
    ]
    for name in names:
        (avatars_dir / name).write_bytes(b"")
    assert _avatar_candidates(avatars_dir, "003") == [
        "char_003_kalts.png",
        "char_003_kalts_2.png",
        "char_003_kalts_boc#6.png",
    ]
    assert _avatar_candidates(tmp_path / "missing", "003") == []


def test_edit_distance():
    assert _edit_distance("sunbr_1", "sunbr") == 2
    assert _edit_distance("ABC", "abd") == 1
    assert _edit_distance("abc", "abc") == 0
    assert _edit_distance("", "x") == 1
    assert _edit_distance("char_196_sunbr", "char_196_sunbr_summer#1") == 9


def test_avatar_candidates_edit_distance_order(tmp_path: Path):
    avatars_dir = tmp_path / "avatars"
    avatars_dir.mkdir()
    names = [
        "char_196_sunbr_2.png",
        "char_196_sunbr_summer#1.png",
        "char_196_sunbr.png",
    ]
    for name in names:
        (avatars_dir / name).write_bytes(b"")
    assert _avatar_candidates(avatars_dir, "196", "char_196_sunbr_1") == [
        "char_196_sunbr.png",
        "char_196_sunbr_2.png",
        "char_196_sunbr_summer#1.png",
    ]
    assert _avatar_candidates(avatars_dir, "196", "char_196_sunbr_summer") == [
        "char_196_sunbr_summer#1.png",
        "char_196_sunbr_2.png",
        "char_196_sunbr.png",
    ]
    # 不传 character 时保持字母序
    assert _avatar_candidates(avatars_dir, "196") == [
        "char_196_sunbr.png",
        "char_196_sunbr_2.png",
        "char_196_sunbr_summer#1.png",
    ]


def test_template_match_locates_pasted_avatar(tmp_path: Path):
    avatar = _avatar_image(180, seed=1)
    avatar_path = tmp_path / "avatar.png"
    base_path = tmp_path / "base.png"
    _write_image(avatar_path, avatar)
    _write_image(base_path, _base_with_avatar(avatar, (300, 200)))

    threshold, box = template_match(base_path, avatar_path)

    assert threshold > 0.9
    assert box == (300, 200, 480, 380)


def test_template_match_located_above_top_edge(tmp_path: Path):
    avatar = _avatar_image(180, seed=1)
    avatar_path = tmp_path / "avatar.png"
    base_path = tmp_path / "base.png"
    _write_image(avatar_path, avatar)
    _write_image(base_path, _base_with_avatar(avatar, (300, -50)))

    threshold, box = template_match(base_path, avatar_path)

    assert threshold > STOP_THRESHOLD
    assert box == (300, -50, 480, 130)


def test_scale_search_capped_at_max_avatar_size(tmp_path: Path):
    """缩放搜索的模板边长不超过 MAX_AVATAR_SIZE，即使底图中目标更大。"""
    avatar = _avatar_image(180, seed=1)
    avatar_path = tmp_path / "avatar.png"
    base_path = tmp_path / "base.png"
    _write_image(avatar_path, avatar)
    # 底图中的头像为 180 * 1.9 = 342px，超过 325px 上限
    _write_image(base_path, _scaled_base_with_avatar(avatar, 1.9, (300, 200)))

    base_gray, _ = _prepare_base(base_path)
    avatar_gray = _prepare_avatar(avatar_path)
    _threshold, box, _offsets = _template_match_gray(
        base_gray, avatar_gray, top_offset=BASE_EXTEND_TOP
    )

    avatar_h, avatar_w = avatar_gray.shape[:2]
    best_offset = box[2] - box[0] - avatar_w
    assert MIN_AVATAR_SIZE < avatar_h + best_offset <= MAX_AVATAR_SIZE
    assert MIN_AVATAR_SIZE < avatar_w + best_offset <= MAX_AVATAR_SIZE


def test_cli_rejects_max_avatar_size_not_greater_than_min(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    """--max-avatar-size 必须大于 --min-avatar-size，否则报错退出。"""
    code = main(
        [
            "--classified",
            str(tmp_path / "missing.json"),
            "--min-avatar-size",
            "325",
            "--max-avatar-size",
            "325",
        ]
    )
    assert code == 1
    assert "--min-avatar-size" in capsys.readouterr().err


def test_match_report_negative_y_for_above_top_edge(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar = _avatar_image(180, seed=1)
    _write_image(avatars_dir / "char_007_closre.png", avatar)
    _write_image(
        characters_dir / "avg_007_closre_1" / "avg_007_closre_1$1.png",
        _base_with_avatar(avatar, (300, -40)),
    )
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_007_closre_1": {
                "status": "ok",
                "bases": {"avg_007_closre_1$1.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
        },
    )

    report = match_characters(
        json.loads(classified.read_text(encoding="utf8")),
        characters_dir,
        avatars_dir,
    )

    base_result = report.characters["avg_007_closre_1"].bases["avg_007_closre_1$1.png"]
    assert base_result.box == [300, -40, 480, 140]
    assert base_result.box_norm is not None and base_result.box_norm[1] < 0


def test_match_characters_end_to_end(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"

    avatar_a = _avatar_image(180, seed=1)
    avatar_b = _avatar_image(180, seed=2)
    _write_image(avatars_dir / "char_003_kalts.png", avatar_a)
    _write_image(avatars_dir / "char_003_kalts_2.png", avatar_b)

    base_dir = characters_dir / "avg_003_kalts_1"
    _write_image(
        base_dir / "avg_003_kalts_1$1.png", _base_with_avatar(avatar_a, (100, 120))
    )

    no_avatar_dir = characters_dir / "char_011_talula_1"
    _write_image(
        no_avatar_dir / "char_011_talula_1.png", _base_with_avatar(avatar_a, (0, 0))
    )

    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"avg_003_kalts_1$1.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
            "char_011_talula_1": {
                "status": "ok",
                "bases": {"char_011_talula_1.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
        },
    )
    output = tmp_path / "report.json"

    code = main(
        [
            "--classified",
            str(classified),
            "--characters-dir",
            str(characters_dir),
            "--avatars-dir",
            str(avatars_dir),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf8"))
    chars = payload["characters"]
    assert chars["avg_003_kalts_1"]["status"] == "ok"
    assert chars["avg_003_kalts_1"]["candidates"] == [
        "char_003_kalts.png",
        "char_003_kalts_2.png",
    ]
    base_result = chars["avg_003_kalts_1"]["bases"]["avg_003_kalts_1$1.png"]
    assert base_result["avatar"] == "char_003_kalts.png"
    assert base_result["threshold"] > 0.9
    assert base_result["box"] == [100, 120, 280, 300]
    assert len(base_result["box_norm"]) == 4
    assert "offsets" not in base_result
    assert chars["char_011_talula_1"]["status"] == "no_avatar"
    assert payload["stats"] == {
        "total": 2,
        "ok": 1,
        "no_avatar": 1,
        "empty": 0,
        "failed": 0,
        "base_files": 2,
        "matched_bases": 1,
        "low_confidence": 0,
    }


def test_detail_offsets_reported(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar_a = _avatar_image(180, seed=1)
    avatar_b = _avatar_image(180, seed=2)
    _write_image(avatars_dir / "char_003_kalts.png", avatar_a)
    _write_image(avatars_dir / "char_003_kalts_2.png", avatar_b)
    _write_image(
        characters_dir / "avg_003_kalts_1" / "base.png",
        _base_with_avatar(avatar_a, (40, 60)),
    )
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"base.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            }
        },
    )

    output = tmp_path / "report.json"
    code = main(
        [
            "--classified",
            str(classified),
            "--characters-dir",
            str(characters_dir),
            "--avatars-dir",
            str(avatars_dir),
            "--detail",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    base_result = json.loads(output.read_text(encoding="utf8"))["characters"][
        "avg_003_kalts_1"
    ]["bases"]["base.png"]
    offsets = base_result["offsets"]
    # 候选级早停：首个候选（编辑距离最近）即达标，后续候选不再尝试
    assert set(offsets) == {"char_003_kalts.png"}
    records = offsets[base_result["avatar"]]
    assert records and records[0]["offset"] == 0
    assert all(
        record["size"] == [180 + record["offset"], 180 + record["offset"]]
        for record in records
    )
    assert records[0]["x"] == 40
    assert records[0]["y"] == 60
    best = [record for record in records if record["best"]]
    assert len(best) == 1
    assert best[0]["threshold"] == base_result["threshold"]


def test_candidate_early_stop(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar_a = _avatar_image(180, seed=1)
    avatar_b = _avatar_image(180, seed=2)
    _write_image(avatars_dir / "char_003_kalts.png", avatar_a)
    _write_image(avatars_dir / "char_003_kalts_2.png", avatar_b)
    _write_image(
        characters_dir / "avg_003_kalts_1" / "base.png",
        _base_with_avatar(avatar_a, (40, 60)),
    )
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"base.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            }
        },
    )

    report = match_characters(
        json.loads(classified.read_text(encoding="utf8")),
        characters_dir,
        avatars_dir,
        detail=True,
    )

    base_result = report.characters["avg_003_kalts_1"].bases["base.png"]
    assert base_result.avatar == "char_003_kalts.png"
    assert (
        base_result.threshold is not None and base_result.threshold > CONFIDENCE_TARGET
    )
    # 首个候选即达标，第二个候选被跳过
    assert base_result.offsets is not None
    assert set(base_result.offsets) == {"char_003_kalts.png"}


def test_missing_base_file_marks_failed(tmp_path: Path):
    avatars_dir = tmp_path / "avatars"
    _write_image(avatars_dir / "char_003_kalts.png", _avatar_image(180, seed=1))
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"missing.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
        },
    )

    report = match_characters(
        json.loads(classified.read_text(encoding="utf8")),
        tmp_path / "characters",
        avatars_dir,
    )

    char = report.characters["avg_003_kalts_1"]
    assert char.status == "failed"
    assert "error" in char.bases["missing.png"].as_dict()
    assert report.stats["failed"] == 1
    assert report.stats["matched_bases"] == 0


def test_empty_character_status(tmp_path: Path):
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "empty",
                "bases": {},
                "unassigned": [],
                "sizes": {},
            }
        },
    )

    report = match_characters(
        json.loads(classified.read_text(encoding="utf8")),
        tmp_path / "characters",
        tmp_path / "avatars",
    )

    assert report.characters["avg_003_kalts_1"].status == "empty"
    assert report.stats["empty"] == 1


def test_cli_missing_classified(capsys, tmp_path: Path):
    code = main(["--classified", str(tmp_path / "nope.json")])
    assert code == 1
    assert "not found" in capsys.readouterr().err


def test_cli_limit(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar = _avatar_image(180, seed=1)
    _write_image(avatars_dir / "char_003_kalts.png", avatar)
    _write_image(
        characters_dir / "avg_003_kalts_1" / "base.png",
        _base_with_avatar(avatar, (0, 0)),
    )
    _write_image(
        characters_dir / "avg_007_closre_1" / "base.png",
        _base_with_avatar(avatar, (0, 0)),
    )
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"base.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
            "avg_007_closre_1": {
                "status": "ok",
                "bases": {"base.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
        },
    )

    report = match_characters(
        json.loads(classified.read_text(encoding="utf8")),
        characters_dir,
        avatars_dir,
        limit=1,
    )

    assert report.stats["total"] == 1
    assert list(report.characters) == ["avg_003_kalts_1"]


def test_cli_character(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar = _avatar_image(180, seed=1)
    _write_image(avatars_dir / "char_003_kalts.png", avatar)
    _write_image(
        characters_dir / "avg_003_kalts_1" / "base.png",
        _base_with_avatar(avatar, (0, 0)),
    )
    _write_image(
        characters_dir / "avg_007_closre_1" / "base.png",
        _base_with_avatar(avatar, (0, 0)),
    )
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"base.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
            "avg_007_closre_1": {
                "status": "ok",
                "bases": {"base.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
        },
    )
    output = tmp_path / "report.json"

    code = main(
        [
            "--classified",
            str(classified),
            "--characters-dir",
            str(characters_dir),
            "--avatars-dir",
            str(avatars_dir),
            "--character",
            "avg_007_closre_1",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf8"))
    assert list(payload["characters"]) == ["avg_007_closre_1"]
    assert payload["stats"]["total"] == 1


def test_cli_character_missing(capsys, tmp_path: Path):
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {},
                "unassigned": [],
                "sizes": {},
            }
        },
    )

    code = main(["--classified", str(classified), "--character", "avg_999_unknown_1"])

    assert code == 1
    assert "character not found" in capsys.readouterr().err


def test_cli_output_stdout_dash(capsys, tmp_path: Path):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar = _avatar_image(180, seed=1)
    _write_image(avatars_dir / "char_003_kalts.png", avatar)
    _write_image(
        characters_dir / "avg_003_kalts_1" / "base.png",
        _base_with_avatar(avatar, (0, 0)),
    )
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"base.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            }
        },
    )

    code = main(
        [
            "--classified",
            str(classified),
            "--characters-dir",
            str(characters_dir),
            "--avatars-dir",
            str(avatars_dir),
            "--output",
            "-",
        ]
    )

    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("{"))
    payload = json.loads("\n".join(lines[start:]))
    assert payload["stats"]["total"] == 1


def test_cli_skips_when_report_exists(tmp_path: Path, capsys, monkeypatch):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar = _avatar_image(180, seed=1)
    _write_image(avatars_dir / "char_003_kalts.png", avatar)
    _write_image(
        characters_dir / "avg_003_kalts_1" / "base.png",
        _base_with_avatar(avatar, (0, 0)),
    )
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"base.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            }
        },
    )
    output = tmp_path / "report.json"
    argv = [
        "--classified",
        str(classified),
        "--characters-dir",
        str(characters_dir),
        "--avatars-dir",
        str(avatars_dir),
        "--output",
        str(output),
    ]

    assert main(argv) == 0
    before = output.read_bytes()

    def _boom(*args, **kwargs):
        raise AssertionError(
            "match_characters must not run when the report already exists"
        )

    monkeypatch.setattr("arknightsavatar.match.match_characters", _boom)
    assert main(argv) == 0
    assert "skipping match" in capsys.readouterr().out
    assert output.read_bytes() == before


def test_cli_force_reruns_when_report_exists(tmp_path: Path, capsys):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar = _avatar_image(180, seed=1)
    _write_image(avatars_dir / "char_003_kalts.png", avatar)
    _write_image(
        characters_dir / "avg_003_kalts_1" / "base.png",
        _base_with_avatar(avatar, (0, 0)),
    )
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"base.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            }
        },
    )
    output = tmp_path / "report.json"
    argv = [
        "--classified",
        str(classified),
        "--characters-dir",
        str(characters_dir),
        "--avatars-dir",
        str(avatars_dir),
        "--output",
        str(output),
    ]

    assert main(argv) == 0
    assert main(argv + ["--force"]) == 0
    out = capsys.readouterr().out
    assert "skipping match" not in out
    payload = json.loads(output.read_text(encoding="utf8"))
    assert payload["stats"]["total"] == 1
    assert payload["characters"]["avg_003_kalts_1"]["status"] == "ok"


def _setup_match_env(tmp_path: Path, avatar_size: int = 180, seed: int = 1):
    """搭建一套角色/头像/分类文件，返回 (classified_path, characters_dir, avatars_dir, expected_box)。"""
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar = _avatar_image(avatar_size, seed=seed)
    paste_x, paste_y = 300, 200
    _write_image(avatars_dir / "char_003_kalts.png", avatar)
    _write_image(
        characters_dir / "avg_003_kalts_1" / "avg_003_kalts_1$1.png",
        _base_with_avatar(avatar, (paste_x, paste_y)),
    )
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"avg_003_kalts_1$1.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            }
        },
    )
    expected_box = [paste_x, paste_y, paste_x + avatar_size, paste_y + avatar_size]
    return classified, characters_dir, avatars_dir, expected_box


def test_image_output_end_to_end(tmp_path: Path):
    pytest.importorskip("cv2")
    classified, characters_dir, avatars_dir, _ = _setup_match_env(tmp_path)
    image_dir = tmp_path / "images"
    output = tmp_path / "report.json"

    code = main(
        [
            "--classified",
            str(classified),
            "--characters-dir",
            str(characters_dir),
            "--avatars-dir",
            str(avatars_dir),
            "--output",
            str(output),
            "--image-dir",
            str(image_dir),
        ]
    )

    assert code == 0
    pngs = list(image_dir.glob("*.png"))
    assert len(pngs) == 1
    assert pngs[0].name == "avg_003_kalts_1__avg_003_kalts_1$1.png"

    data = np.fromfile(str(pngs[0]), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    assert img.shape == (OUTPUT_BASE_SIZE, OUTPUT_BASE_SIZE, 3)

    # 红框应改变底图区域像素（与纯白缩放底图对比）
    base_path = characters_dir / "avg_003_kalts_1" / "avg_003_kalts_1$1.png"
    base_rgba = _read_rgba(base_path)
    base_plain = _composite_on_color(
        cv2.resize(base_rgba, (OUTPUT_BASE_SIZE, OUTPUT_BASE_SIZE))
    )
    diff = cv2.absdiff(img, base_plain)
    assert int(diff.sum()) > 0

    # 头像应出现在右下角（180px 贴在 512 画布右下角）
    avatar_path = avatars_dir / "char_003_kalts.png"
    avatar_rgba = _read_rgba(avatar_path)
    avatar_bgr = _composite_on_color(avatar_rgba)
    ah, aw = avatar_bgr.shape[:2]
    paste_x = OUTPUT_BASE_SIZE - aw
    paste_y = OUTPUT_BASE_SIZE - ah
    region = img[paste_y : paste_y + ah, paste_x : paste_x + aw]
    avatar_diff = cv2.absdiff(region, avatar_bgr)
    assert int(avatar_diff.sum()) < 500  # 近乎一致（允许极小编码误差）


def test_image_output_avatar_downscale(tmp_path: Path):
    pytest.importorskip("cv2")
    classified, characters_dir, avatars_dir, _ = _setup_match_env(
        tmp_path, avatar_size=360, seed=3
    )
    image_dir = tmp_path / "images"
    output = tmp_path / "report.json"

    code = main(
        [
            "--classified",
            str(classified),
            "--characters-dir",
            str(characters_dir),
            "--avatars-dir",
            str(avatars_dir),
            "--output",
            str(output),
            "--image-dir",
            str(image_dir),
        ]
    )

    assert code == 0
    pngs = list(image_dir.glob("*.png"))
    assert len(pngs) == 1

    data = np.fromfile(str(pngs[0]), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    assert img.shape == (OUTPUT_BASE_SIZE, OUTPUT_BASE_SIZE, 3)

    # 360 头像应缩放至 256px，贴在右下角
    expected_size = AVATAR_MAX_SIZE
    paste_x = OUTPUT_BASE_SIZE - expected_size
    paste_y = OUTPUT_BASE_SIZE - expected_size
    avatar_path = avatars_dir / "char_003_kalts.png"
    avatar_rgba = _read_rgba(avatar_path)
    avatar_bgr = _composite_on_color(avatar_rgba)
    scale = AVATAR_MAX_SIZE / 360
    avatar_scaled = cv2.resize(avatar_bgr, (round(360 * scale), round(360 * scale)))
    region = img[paste_y : paste_y + expected_size, paste_x : paste_x + expected_size]
    avatar_diff = cv2.absdiff(region, avatar_scaled)
    assert int(avatar_diff.sum()) < 2000  # 缩放后近乎一致


def test_image_output_skips_failed_and_no_avatar(tmp_path: Path):
    pytest.importorskip("cv2")
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar_a = _avatar_image(180, seed=1)
    _write_image(avatars_dir / "char_003_kalts.png", avatar_a)
    _write_image(
        characters_dir / "avg_003_kalts_1" / "avg_003_kalts_1$1.png",
        _base_with_avatar(avatar_a, (100, 120)),
    )
    _write_image(
        characters_dir / "char_011_talula_1" / "char_011_talula_1.png",
        _base_with_avatar(avatar_a, (0, 0)),
    )
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"avg_003_kalts_1$1.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
            "char_011_talula_1": {
                "status": "ok",
                "bases": {"char_011_talula_1.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
        },
    )
    image_dir = tmp_path / "images"
    output = tmp_path / "report.json"

    code = main(
        [
            "--classified",
            str(classified),
            "--characters-dir",
            str(characters_dir),
            "--avatars-dir",
            str(avatars_dir),
            "--output",
            str(output),
            "--image-dir",
            str(image_dir),
        ]
    )

    assert code == 0
    pngs = list(image_dir.glob("*.png"))
    assert (
        len(pngs) == 1
    )  # only the matched avg_003_kalts_1 base; talula has no avatar candidates


def test_image_output_disabled_by_default(tmp_path: Path):
    pytest.importorskip("cv2")
    classified, characters_dir, avatars_dir, _ = _setup_match_env(tmp_path)
    output = tmp_path / "report.json"

    code = main(
        [
            "--classified",
            str(classified),
            "--characters-dir",
            str(characters_dir),
            "--avatars-dir",
            str(avatars_dir),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    pngs = list(tmp_path.rglob("*.png"))
    # 头像源文件本身是 png，排除后应无可视化输出
    output_pngs = [
        p
        for p in pngs
        if "images" not in p.parts
        and "avatars" not in p.parts
        and "characters" not in p.parts
    ]
    assert output_pngs == []


def _old_char(
    candidates: list[str],
    bases: dict,
    status: str = "ok",
) -> dict:
    """构造一个旧报告中的角色条目（与 as_dict 输出同构）。"""
    return {"status": status, "candidates": candidates, "bases": bases}


def _base_result(
    avatar: str,
    threshold: float,
    box: list[int] | None = None,
    error: str | None = None,
) -> dict:
    if error is not None:
        return {"error": error}
    return {
        "avatar": avatar,
        "threshold": threshold,
        "box": box or [0, 0, 1, 1],
        "box_norm": [0.0, 0.0, 0.001, 0.001],
    }


def test_needs_rematch_candidates_unchanged():
    """候选列表未变化时不重匹配，即使存在低置信度 base。"""
    old = _old_char(
        ["char_003_kalts.png"],
        {"b.png": _base_result("char_003_kalts.png", 0.5)},
    )
    assert not needs_rematch(old, ["char_003_kalts.png"], 0.9)


def test_needs_rematch_candidates_changed_low_confidence():
    """候选更新 + 任一 base 低于阈值 → 重匹配。"""
    old = _old_char(
        ["char_003_kalts.png"],
        {"b.png": _base_result("char_003_kalts.png", 0.85)},
    )
    assert needs_rematch(old, ["char_003_kalts.png", "char_003_kalts_2.png"], 0.9)


def test_needs_rematch_candidates_changed_all_confident():
    """候选更新但所有 base 均高于阈值 → 不重匹配。"""
    old = _old_char(
        ["char_003_kalts.png"],
        {"b.png": _base_result("char_003_kalts.png", 0.95)},
    )
    assert not needs_rematch(old, ["char_003_kalts.png", "char_003_kalts_2.png"], 0.9)


def test_needs_rematch_failed_base():
    """候选更新 + 任一 base 匹配失败（error）→ 重匹配。"""
    old = _old_char(
        ["char_003_kalts.png"],
        {"b.png": _base_result("", 0.0, error="no readable avatar")},
    )
    assert needs_rematch(old, ["char_003_kalts.png", "char_003_kalts_2.png"], 0.9)


def test_needs_rematch_stale_avatar():
    """候选更新 + 旧结果引用的头像已不在新候选列表中 → 重匹配（悬空引用保护）。"""
    old = _old_char(
        ["char_003_kalts.png", "char_003_kalts_2.png"],
        {"b.png": _base_result("char_003_kalts_2.png", 0.95)},
    )
    assert needs_rematch(old, ["char_003_kalts.png"], 0.9)


def test_needs_rematch_no_base_results():
    """之前 no_avatar（无候选、无 base 结果）新增候选 → 重匹配；候选仍为空 → 不重匹配。"""
    old = _old_char([], {}, status="no_avatar")
    assert needs_rematch(old, ["char_003_kalts.png"], 0.9)
    assert not needs_rematch(old, [], 0.9)


def test_plan_rematch(tmp_path: Path):
    avatars_dir = tmp_path / "avatars"
    avatars_dir.mkdir()
    for name in [
        "char_003_kalts.png",
        "char_003_kalts_2.png",
        "char_007_closre.png",
        "char_011_talula.png",
    ]:
        (avatars_dir / name).write_bytes(b"")
    classified = {
        "characters": {
            "avg_003_kalts_1": {"bases": {"b.png": {}}},  # 候选更新 + 低置信 → 重匹配
            "avg_007_closre_1": {"bases": {"b.png": {}}},  # 候选更新但高置信 → 保留
            "char_011_talula_1": {"bases": {"b.png": {}}},  # 候选未变 → 保留
            "avg_004_new_1": {"bases": {"b.png": {}}},  # 新角色 → 重匹配
            "avg_npc_010": {"bases": {"b.png": {}}},  # 非目标角色 → 忽略
        }
    }
    old_characters = {
        "avg_003_kalts_1": _old_char(
            ["char_003_kalts.png"], {"b.png": _base_result("char_003_kalts.png", 0.85)}
        ),
        "avg_007_closre_1": _old_char(
            ["char_007_closre.png"],
            {"b.png": _base_result("char_007_closre.png", 0.95)},
        ),
        "char_011_talula_1": _old_char(
            ["char_011_talula.png"],
            {"b.png": _base_result("char_011_talula.png", 0.95)},
        ),
    }

    to_rematch, kept = plan_rematch(classified, old_characters, avatars_dir, 0.9)
    assert to_rematch == {"avg_003_kalts_1", "avg_004_new_1"}
    assert kept == {"avg_007_closre_1", "char_011_talula_1"}

    # --character 过滤后只考虑该角色
    to_rematch, kept = plan_rematch(
        classified, old_characters, avatars_dir, 0.9, character="avg_007_closre_1"
    )
    assert to_rematch == set()
    assert kept == {"avg_007_closre_1"}


def test_character_from_dict_roundtrip():
    """CharacterMatch.from_dict 应还原 as_dict 输出（含 offsets 与 error-only 两种 base 形态）。"""
    data = {
        "status": "ok",
        "candidates": ["char_003_kalts.png"],
        "bases": {
            "b.png": {
                "avatar": "char_003_kalts.png",
                "threshold": 0.93,
                "box": [1, 2, 3, 4],
                "box_norm": [0.1, 0.2, 0.3, 0.4],
                "offsets": {
                    "char_003_kalts.png": [
                        {
                            "offset": 0,
                            "size": [180, 180],
                            "threshold": 0.93,
                            "x": 1,
                            "y": 2,
                            "best": True,
                        }
                    ]
                },
            },
            "e.png": {"error": "no readable avatar"},
        },
    }
    restored = CharacterMatch.from_dict("avg_003_kalts_1", data)
    assert restored.name == "avg_003_kalts_1"
    assert restored.as_dict() == data


def test_match_characters_only(tmp_path: Path):
    """only 参数把匹配限制在指定角色集合内。"""
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar = _avatar_image(180, seed=1)
    _write_image(avatars_dir / "char_003_kalts.png", avatar)
    _write_image(
        characters_dir / "avg_003_kalts_1" / "base.png",
        _base_with_avatar(avatar, (0, 0)),
    )
    _write_image(
        characters_dir / "avg_007_closre_1" / "base.png",
        _base_with_avatar(avatar, (0, 0)),
    )
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"base.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
            "avg_007_closre_1": {
                "status": "ok",
                "bases": {"base.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
        },
    )

    report = match_characters(
        json.loads(classified.read_text(encoding="utf8")),
        characters_dir,
        avatars_dir,
        only={"avg_003_kalts_1"},
    )

    assert list(report.characters) == ["avg_003_kalts_1"]
    assert report.stats["total"] == 1


def test_cli_rejects_invalid_rematch_confidence(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    code = main(
        ["--classified", str(tmp_path / "missing.json"), "--rematch-confidence", "1.5"]
    )
    assert code == 1
    assert "--rematch-confidence" in capsys.readouterr().err


def test_cli_incremental_rematch_on_candidate_update(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    """候选头像更新 + 存在低置信度 base → 重跑 match 时只重匹配该角色，其余角色保留旧结果。"""
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar_a = _avatar_image(180, seed=1)
    avatar_b = _avatar_image(180, seed=2)
    # kalts 底图内头像为 180*2.0=360px，超过 325px 缩放上限 → 匹配阈值约 0.85（低置信）
    _write_image(
        characters_dir / "avg_003_kalts_1" / "avg_003_kalts_1$1.png",
        _scaled_base_with_avatar(avatar_a, 2.0, (300, 200)),
    )
    _write_image(
        characters_dir / "char_011_talula_1" / "char_011_talula_1.png",
        _base_with_avatar(avatar_a, (0, 0)),
    )
    _write_image(avatars_dir / "char_003_kalts.png", avatar_a)
    _write_image(avatars_dir / "char_011_talula.png", avatar_a)
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"avg_003_kalts_1$1.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
            "char_011_talula_1": {
                "status": "ok",
                "bases": {"char_011_talula_1.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            },
        },
    )
    output = tmp_path / "report.json"
    argv = [
        "--classified",
        str(classified),
        "--characters-dir",
        str(characters_dir),
        "--avatars-dir",
        str(avatars_dir),
        "--output",
        str(output),
    ]

    assert main(argv) == 0
    report1 = json.loads(output.read_text(encoding="utf8"))
    assert (
        report1["characters"]["avg_003_kalts_1"]["bases"]["avg_003_kalts_1$1.png"][
            "threshold"
        ]
        < 0.9
    )

    # 新增候选头像 → 候选列表变化
    _write_image(avatars_dir / "char_003_kalts_2.png", avatar_b)
    assert main(argv) == 0

    out = capsys.readouterr().out
    assert "re-matching 1 character(s)" in out
    report2 = json.loads(output.read_text(encoding="utf8"))
    chars = report2["characters"]
    assert chars["avg_003_kalts_1"]["candidates"] == [
        "char_003_kalts.png",
        "char_003_kalts_2.png",
    ]
    assert chars["avg_003_kalts_1"]["status"] == "ok"
    # 候选未变的角色保留旧结果
    assert (
        chars["char_011_talula_1"]["bases"]["char_011_talula_1.png"]
        == report1["characters"]["char_011_talula_1"]["bases"]["char_011_talula_1.png"]
    )
    assert report2["generated_at"] != report1["generated_at"]
    assert report2["stats"]["total"] == 2


def test_cli_incremental_skips_when_confident_and_candidates_change(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    """候选头像更新但所有 base 置信度 ≥ 0.9 → 不重匹配，报告文件保持不变。"""
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar_a = _avatar_image(180, seed=1)
    avatar_b = _avatar_image(180, seed=2)
    # 180*1.9=342px 仍超上限但更接近，阈值约 0.93（≥ 0.9，高置信）
    _write_image(
        characters_dir / "avg_003_kalts_1" / "avg_003_kalts_1$1.png",
        _scaled_base_with_avatar(avatar_a, 1.9, (300, 200)),
    )
    _write_image(avatars_dir / "char_003_kalts.png", avatar_a)
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {"avg_003_kalts_1$1.png": {"diff": []}},
                "unassigned": [],
                "sizes": {},
            }
        },
    )
    output = tmp_path / "report.json"
    argv = [
        "--classified",
        str(classified),
        "--characters-dir",
        str(characters_dir),
        "--avatars-dir",
        str(avatars_dir),
        "--output",
        str(output),
    ]

    assert main(argv) == 0
    report1 = json.loads(output.read_text(encoding="utf8"))
    assert (
        report1["characters"]["avg_003_kalts_1"]["bases"]["avg_003_kalts_1$1.png"][
            "threshold"
        ]
        >= 0.9
    )
    before = output.read_bytes()

    _write_image(avatars_dir / "char_003_kalts_2.png", avatar_b)
    assert main(argv) == 0
    assert "skipping match" in capsys.readouterr().out
    assert output.read_bytes() == before


def test_match_characters_progress_reports_every_character(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {},
                "unassigned": [],
                "sizes": {},
            },
            "avg_007_closre_1": {
                "status": "ok",
                "bases": {},
                "unassigned": [],
                "sizes": {},
            },
            "npc_001": {"status": "ok", "bases": {}},  # 非 target 角色，不处理
        },
    )

    calls: list[tuple[int, int, str]] = []
    match_characters(
        json.loads(classified.read_text(encoding="utf8")),
        characters_dir,
        avatars_dir,
        progress=lambda index, total, label: calls.append((index, total, label)),
    )

    # 逐角色回调，不按 20 节流；total 与实际处理数一致
    assert calls == [(1, 2, "avg_003_kalts_1"), (2, 2, "avg_007_closre_1")]


def test_match_characters_progress_total_respects_limit(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {
                "status": "ok",
                "bases": {},
                "unassigned": [],
                "sizes": {},
            },
            "avg_007_closre_1": {
                "status": "ok",
                "bases": {},
                "unassigned": [],
                "sizes": {},
            },
        },
    )

    calls: list[tuple[int, int, str]] = []
    match_characters(
        json.loads(classified.read_text(encoding="utf8")),
        characters_dir,
        avatars_dir,
        limit=1,
        progress=lambda index, total, label: calls.append((index, total, label)),
    )

    assert calls == [(1, 1, "avg_003_kalts_1")]
