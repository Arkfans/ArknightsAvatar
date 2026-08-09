import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from npcavatar.match import (
    STOP_THRESHOLD,
    _avatar_candidates,
    _char_seq,
    _is_target_character,
    main,
    match_characters,
    template_match,
)


def _avatar_image(size: int = 180, seed: int = 0) -> Image.Image:
    """生成一个可识别的透明底方形图案，用作合成头像。"""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((20, 20, size - 20, size - 20), fill=(80 + seed * 40, 40, 200 - seed * 30, 255))
    draw.ellipse((size // 2 - 12, size // 2 - 12, size // 2 + 12, size // 2 + 12), fill=(255, 255, 255, 255))
    return image


def _write_image(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _write_classified(path: Path, characters: dict) -> None:
    path.write_text(
        json.dumps({"generated_at": "2026-08-09T00:00:00+00:00", "characters_dir": ".", "stats": {}, "characters": characters}),
        encoding="utf8",
    )


def _base_with_avatar(avatar: Image.Image, at: tuple[int, int], size: tuple[int, int] = (1024, 1024)) -> Image.Image:
    base = Image.new("RGBA", size, (0, 0, 0, 255))
    base.paste(avatar, at, avatar)
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


def test_template_match_locates_pasted_avatar(tmp_path: Path):
    avatar = _avatar_image(180, seed=1)
    avatar_path = tmp_path / "avatar.png"
    base_path = tmp_path / "base.png"
    _write_image(avatar_path, avatar)
    _write_image(base_path, _base_with_avatar(avatar, (300, 200)))

    threshold, box = template_match(base_path, avatar_path)

    assert threshold > 0.9
    assert box == (300, 200, 480, 380)


def test_match_characters_end_to_end(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"

    avatar_a = _avatar_image(180, seed=1)
    avatar_b = _avatar_image(180, seed=2)
    _write_image(avatars_dir / "char_003_kalts.png", avatar_a)
    _write_image(avatars_dir / "char_003_kalts_2.png", avatar_b)

    base_dir = characters_dir / "avg_003_kalts_1"
    _write_image(base_dir / "avg_003_kalts_1$1.png", _base_with_avatar(avatar_a, (100, 120)))

    no_avatar_dir = characters_dir / "char_011_talula_1"
    _write_image(no_avatar_dir / "char_011_talula_1.png", _base_with_avatar(avatar_a, (0, 0)))

    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {"status": "ok", "bases": {"avg_003_kalts_1$1.png": {"diff": []}}, "unassigned": [], "sizes": {}},
            "char_011_talula_1": {"status": "ok", "bases": {"char_011_talula_1.png": {"diff": []}}, "unassigned": [], "sizes": {}},
        },
    )
    output = tmp_path / "report.json"

    code = main(
        [
            "--classified", str(classified),
            "--characters-dir", str(characters_dir),
            "--avatars-dir", str(avatars_dir),
            "--output", str(output),
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf8"))
    chars = payload["characters"]
    assert chars["avg_003_kalts_1"]["status"] == "ok"
    assert chars["avg_003_kalts_1"]["candidates"] == ["char_003_kalts.png", "char_003_kalts_2.png"]
    base_result = chars["avg_003_kalts_1"]["bases"]["avg_003_kalts_1$1.png"]
    assert base_result["avatar"] == "char_003_kalts.png"
    assert base_result["threshold"] > 0.9
    assert base_result["box"] == [100, 120, 280, 300]
    assert len(base_result["box_norm"]) == 4
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


def test_missing_base_file_marks_failed(tmp_path: Path):
    avatars_dir = tmp_path / "avatars"
    _write_image(avatars_dir / "char_003_kalts.png", _avatar_image(180, seed=1))
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {"status": "ok", "bases": {"missing.png": {"diff": []}}, "unassigned": [], "sizes": {}},
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
    _write_classified(classified, {"avg_003_kalts_1": {"status": "empty", "bases": {}, "unassigned": [], "sizes": {}}})

    report = match_characters(json.loads(classified.read_text(encoding="utf8")), tmp_path / "characters", tmp_path / "avatars")

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
    _write_image(characters_dir / "avg_003_kalts_1" / "base.png", _base_with_avatar(avatar, (0, 0)))
    _write_image(characters_dir / "avg_007_closre_1" / "base.png", _base_with_avatar(avatar, (0, 0)))
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {
            "avg_003_kalts_1": {"status": "ok", "bases": {"base.png": {"diff": []}}, "unassigned": [], "sizes": {}},
            "avg_007_closre_1": {"status": "ok", "bases": {"base.png": {"diff": []}}, "unassigned": [], "sizes": {}},
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


def test_cli_output_stdout_dash(capsys, tmp_path: Path):
    characters_dir = tmp_path / "characters"
    avatars_dir = tmp_path / "avatars"
    avatar = _avatar_image(180, seed=1)
    _write_image(avatars_dir / "char_003_kalts.png", avatar)
    _write_image(characters_dir / "avg_003_kalts_1" / "base.png", _base_with_avatar(avatar, (0, 0)))
    classified = tmp_path / "classified.json"
    _write_classified(
        classified,
        {"avg_003_kalts_1": {"status": "ok", "bases": {"base.png": {"diff": []}}, "unassigned": [], "sizes": {}}},
    )

    code = main(
        [
            "--classified", str(classified),
            "--characters-dir", str(characters_dir),
            "--avatars-dir", str(avatars_dir),
            "--output", "-",
        ]
    )

    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("{"))
    payload = json.loads("\n".join(lines[start:]))
    assert payload["stats"]["total"] == 1
