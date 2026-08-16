import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image, ImageDraw

from arknightsavatar import detect, detect_bases, extract
from arknightsavatar.skip import SkipList

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def workdir():
    """Project-internal writable temp dir (system Temp is not accessible in the sandbox)."""
    base = PROJECT_ROOT / f"arknightsavatar_test_{uuid4().hex[:8]}"
    os.makedirs(base, mode=0o777)
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _avatar_image(size: int = 180, seed: int = 0) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (20, 20, size - 20, size - 20),
        fill=(80 + seed * 40, 40, 200 - seed * 30, 255),
    )
    draw.ellipse(
        (size // 2 - 12, size // 2 - 12, size // 2 + 12, size // 2 + 12),
        fill=(255, 255, 255, 255),
    )
    return image


def _base_with_avatar(
    avatar: Image.Image, at=(300, 200), size=(1024, 1024)
) -> Image.Image:
    base = Image.new("RGBA", size, (0, 0, 0, 255))
    base.paste(avatar, at, avatar)
    return base


def _block_image(
    size: int = 180, color=(0, 0, 255, 255), margin: int = 20
) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    image.paste(
        Image.new("RGBA", (size - 2 * margin, size - 2 * margin), color),
        (margin, margin),
    )
    return image


def _write_png(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def _model() -> dict:
    coef = np.zeros((9, 3))
    coef[0, 0] = 1.0  # fx -> cx
    coef[1, 1] = 1.0  # fy -> cy
    coef[2, 2] = 1.0  # fw -> s
    return {
        "feature_order": ["fx", "fy", "fw", "fh", "hx", "hy", "hw", "hh"],
        "coef": coef.tolist(),
    }


def _face_detector(confidence: float = 0.9, box=(100, 50, 140, 90)):
    def detector(bgr: np.ndarray) -> list[dict]:
        return [{"bbox": list(box), "confidence": confidence}]

    return detector


def _head_detector(confidence: float = 0.8, box=(90, 40, 150, 100)):
    def detector(image_path: str) -> list[tuple[tuple[int, int, int, int], str, float]]:
        return [(tuple(box), "head", confidence)]

    return detector


def _raising_detector():
    def detector(*args, **kwargs):
        raise AssertionError("detector should not be called")

    return detector


def _classified(characters_dir: Path, characters: dict) -> dict:
    return {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "characters_dir": str(characters_dir),
        "stats": {},
        "characters": characters,
    }


def _char_entry(bases: dict) -> dict:
    return {"status": "ok", "bases": bases, "unassigned": [], "sizes": {}}


def _match_report(characters_dir: Path, entries: dict) -> dict:
    return {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "characters_dir": str(characters_dir),
        "characters": entries,
    }


def _base_match(threshold: float = 0.9, box=(100, 100, 300, 300)) -> dict:
    return {"avatar": "char_001_a.png", "threshold": threshold, "box": list(box)}


def _write_meta(char_dir: Path, face_groups: list[dict] | None = None) -> None:
    (char_dir / "meta.json").write_text(
        json.dumps(
            {
                "source": {"rel": f"characters/{char_dir.name}.ab", "sha256": "sha"},
                "textures": {},
                "sprites": [],
                "face_groups": face_groups or [],
            }
        ),
        encoding="utf8",
    )


def _standard_character(workdir: Path, *, diff_size=(64, 64)) -> tuple[Path, dict]:
    """One character (avg_001_a_1) with a base and one small diff + meta."""
    characters_dir = workdir / "characters"
    char_dir = characters_dir / "avg_001_a_1"
    avatar = _avatar_image(180, seed=1)
    _write_png(char_dir / "base.png", _base_with_avatar(avatar))
    diff = Image.new("RGBA", diff_size, (255, 0, 0, 255))
    _write_png(char_dir / "d1.png", diff)
    _write_meta(
        char_dir, [{"facePos": {"x": 10, "y": 10}, "faceSize": {"x": 64, "y": 64}}]
    )
    classified = _classified(
        characters_dir,
        {"avg_001_a_1": _char_entry({"base.png": {"diff": ["d1.png"]}})},
    )
    return characters_dir, classified


def _run(
    workdir: Path,
    characters_dir: Path,
    classified: dict,
    *,
    match_report: dict | None = None,
    manual: dict | None = None,
    force: bool = False,
    force_match: bool = False,
    special_mask_iou: float = extract.SPECIAL_MASK_IOU,
    face_detector=None,
    head_detector=None,
    skip=None,
) -> extract.ExtractionReport:
    return extract.extract_characters(
        classified,
        characters_dir,
        workdir / "export",
        manual=manual or {},
        match_report=match_report,
        match_file=workdir / "match.json" if match_report is not None else None,
        force_match=force_match,
        avatars_dir=workdir / "avatars",
        derive_model=_model(),
        cache=extract.FaceHeadCache(workdir / "cache.json"),
        extract_cache=extract.ExtractCache(workdir / "extract_cache.json"),
        force=force,
        special_mask_iou=special_mask_iou,
        face_detector=face_detector,
        head_detector=head_detector,
        skip=skip,
    )


def test_derive_box_uses_center_features():
    model = _model()
    box = extract.derive_box(
        model, {"x": 100, "y": 50, "w": 40, "h": 40}, {"x": 0, "y": 0, "w": 0, "h": 0}
    )
    assert box == [100, 50, 140, 90]


def test_load_derive_model_invalid(workdir: Path):
    path = workdir / "bad.json"
    path.write_text(json.dumps({"coef": [[1, 2]]}), encoding="utf8")
    with pytest.raises(ValueError):
        extract.load_derive_model(path)


def test_compose_diff_resizes_and_pastes():
    base = Image.new("RGBA", (100, 100), (0, 0, 255, 255))
    diff = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
    group = {"facePos": {"x": 10, "y": 20}, "faceSize": {"x": 64, "y": 64}}
    composed = extract.compose_diff(base, diff, group)
    assert composed.size == (100, 100)
    assert composed.getpixel((30, 30)) == (255, 0, 0, 255)
    assert composed.getpixel((90, 90)) == (0, 0, 255, 255)


def test_compose_diff_same_size_returns_diff():
    base = Image.new("RGBA", (100, 100), (0, 0, 255, 255))
    diff = Image.new("RGBA", (100, 100), (0, 255, 0, 255))
    composed = extract.compose_diff(base, diff, None)
    assert composed.getpixel((5, 5)) == (0, 255, 0, 255)


def test_compose_diff_missing_face_group_raises():
    base = Image.new("RGBA", (100, 100), (0, 0, 255, 255))
    diff = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
    with pytest.raises(ValueError):
        extract.compose_diff(base, diff, None)


def test_compose_diff_invalid_face_size_raises():
    base = Image.new("RGBA", (100, 100), (0, 0, 255, 255))
    diff = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
    group = {"facePos": {"x": 0, "y": 0}, "faceSize": {"x": 0, "y": 0}}
    with pytest.raises(ValueError):
        extract.compose_diff(base, diff, group)


def test_compose_diff_uses_base_alpha():
    base = Image.new("RGBA", (100, 100), (0, 0, 255, 255))
    base.putpixel((10, 20), (0, 0, 255, 0))
    diff = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
    group = {"facePos": {"x": 10, "y": 20}, "faceSize": {"x": 64, "y": 64}}
    composed = extract.compose_diff(base, diff, group)
    assert composed.getpixel((10, 20)) == (0, 0, 255, 0)  # base alpha 0 respected
    assert composed.getpixel((30, 30)) == (255, 0, 0, 255)  # base alpha 255 -> diff RGB
    assert composed.getpixel((90, 90)) == (0, 0, 255, 255)  # outside face unchanged
    assert np.array_equal(np.asarray(composed)[..., 3], np.asarray(base)[..., 3])


def test_compose_diff_prefers_alpha_png():
    base = Image.new("RGBA", (100, 100), (0, 0, 255, 255))
    diff = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
    alpha_mask = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(alpha_mask).rectangle((32, 0, 63, 63), fill=255)
    alpha = alpha_mask.convert("RGBA")
    group = {"facePos": {"x": 10, "y": 20}, "faceSize": {"x": 64, "y": 64}}
    composed = extract.compose_diff(base, diff, group, alpha_img=alpha)
    assert composed.getpixel((10, 20)) == (
        0,
        0,
        255,
        0,
    )  # alpha.png 0 overrides base alpha
    assert composed.getpixel((50, 20)) == (
        255,
        0,
        0,
        255,
    )  # alpha.png 255 -> diff RGB + alpha
    assert composed.getpixel((90, 90)) == (0, 0, 255, 255)  # outside face unchanged


def test_alpha_mask_iou():
    a = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    b = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    assert extract.alpha_mask_iou(a, b) == 1.0
    a.paste(Image.new("RGBA", (50, 50), (255, 0, 0, 255)), (0, 0))
    assert extract.alpha_mask_iou(a, b) == 0.0
    b.paste(Image.new("RGBA", (50, 50), (0, 255, 0, 255)), (25, 25))
    iou = extract.alpha_mask_iou(a, b)
    assert iou > 0.0
    assert iou < 1.0


def test_alpha_mask_iou_box_restricts_to_region():
    # Body pixels unchanged between base/diff dilute the full-image IoU;
    # restricting to the base face box makes the change detectable.
    base = Image.new("RGBA", (100, 100), (0, 0, 0, 255))
    base.paste(Image.new("RGBA", (20, 20), (255, 0, 0, 255)), (10, 10))
    diff = Image.new("RGBA", (100, 100), (0, 0, 0, 255))
    ImageDraw.Draw(diff).rectangle((10, 10, 29, 29), fill=(0, 0, 0, 0))

    full = extract.alpha_mask_iou(diff, base)
    face = extract.alpha_mask_iou(diff, base, box=[10, 10, 30, 30])
    assert full > face
    assert full > extract.SPECIAL_MASK_IOU >= face
    # Out-of-range boxes are clamped instead of raising.
    assert extract.alpha_mask_iou(diff, base, box=[-10, -10, 120, 120]) == full


def test_avatar_similarity():
    a = _avatar_image(180, seed=1)
    b = _avatar_image(180, seed=1)
    c = _block_image(180)
    assert extract.avatar_similarity(a, b) > 0.99
    assert extract.avatar_similarity(a, c) < 0.99


def test_extract_avatar_in_bounds():
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (80, 80), (255, 0, 0, 255)), (10, 10))
    avatar = extract.extract_avatar(image, (10, 10, 90, 90))
    assert avatar.size == (180, 180)
    assert avatar.getpixel((5, 5))[3] == 255


def test_extract_avatar_pads_out_of_bounds():
    image = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    avatar = extract.extract_avatar(image, (-10, -10, 70, 70))
    assert avatar.size == (180, 180)
    assert avatar.getpixel((0, 0))[3] == 0


def test_extract_avatar_invalid_box():
    image = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    with pytest.raises(ValueError):
        extract.extract_avatar(image, (50, 50, 50, 50))


def test_manual_priority_over_match(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    manual = {"avg_001_a_1/base.png": {"box": [50, 50, 250, 250]}}
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir, {"avg_001_a_1": {"bases": {"base.png": _base_match()}}}
        ),
        manual=manual,
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    entry = report.characters["avg_001_a_1"].bases["base.png"]
    assert entry.status == "ok"
    assert entry.method == "manual"
    assert entry.box == [50, 50, 250, 250]
    out = workdir / "export" / "avg_001_a_1" / "base.png"
    assert out.is_file()
    with Image.open(out) as image:
        assert image.size == (180, 180)


def test_match_tier_uses_report(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir, {"avg_001_a_1": {"bases": {"base.png": _base_match()}}}
        ),
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    entry = report.characters["avg_001_a_1"].bases["base.png"]
    assert entry.status == "ok"
    assert entry.method == "match"
    assert entry.confidence == 0.9
    assert entry.box == [100, 100, 300, 300]


def test_match_below_threshold_falls_to_derive(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir,
            {"avg_001_a_1": {"bases": {"base.png": _base_match(threshold=0.7)}}},
        ),
        face_detector=_face_detector(0.9),
        head_detector=_head_detector(0.8),
    )
    entry = report.characters["avg_001_a_1"].bases["base.png"]
    assert entry.status == "ok"
    assert entry.method == "derive"
    assert entry.detect_cache_hit is False
    assert entry.box == [100, 50, 141, 91]


def test_all_tiers_fail_no_box(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir,
            {"avg_001_a_1": {"bases": {"base.png": _base_match(threshold=0.7)}}},
        ),
        face_detector=_face_detector(0.2),
        head_detector=_head_detector(0.2),
    )
    char = report.characters["avg_001_a_1"]
    assert char.bases["base.png"].status == "no_box"
    assert char.diffs["d1.png"].status == "no_box"
    assert report.stats["detect_cache_new"] == 1
    assert not (workdir / "export" / "avg_001_a_1" / "base.png").exists()


def test_detection_cache_reused(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    match_report = _match_report(
        characters_dir,
        {"avg_001_a_1": {"bases": {"base.png": _base_match(threshold=0.7)}}},
    )
    calls = {"face": 0, "head": 0}

    def counting_face(bgr):
        calls["face"] += 1
        return [{"bbox": [100, 50, 140, 90], "confidence": 0.9}]

    def counting_head(image_path):
        calls["head"] += 1
        return [((90, 40, 150, 100), "head", 0.8)]

    _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        face_detector=counting_face,
        head_detector=counting_head,
    )
    assert calls == {"face": 1, "head": 1}
    cache_payload = json.loads((workdir / "cache.json").read_text(encoding="utf8"))
    assert "avg_001_a_1/base.png" in cache_payload["images"]
    entry = cache_payload["images"]["avg_001_a_1/base.png"]
    assert entry["face"]["confidence"] == 0.9
    assert entry["head"]["head_confidence"] == 0.8

    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        force=True,
        face_detector=counting_face,
        head_detector=counting_head,
    )
    assert calls == {"face": 1, "head": 1}
    entry = report.characters["avg_001_a_1"].bases["base.png"]
    assert entry.detect_cache_hit is True
    assert report.stats["detect_cache_hits"] == 1


def test_detection_cache_partial_entry_reruns(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    cache_payload = {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "images": {"avg_001_a_1/base.png": {"face": {"detected": True}}},
    }
    (workdir / "cache.json").write_text(json.dumps(cache_payload), encoding="utf8")
    calls = {"face": 0, "head": 0}

    def counting_face(bgr):
        calls["face"] += 1
        return [{"bbox": [100, 50, 140, 90], "confidence": 0.9}]

    def counting_head(image_path):
        calls["head"] += 1
        return [((90, 40, 150, 100), "head", 0.8)]

    _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir,
            {"avg_001_a_1": {"bases": {"base.png": _base_match(threshold=0.7)}}},
        ),
        face_detector=counting_face,
        head_detector=counting_head,
    )
    assert calls == {"face": 1, "head": 1}
    cache_payload = json.loads((workdir / "cache.json").read_text(encoding="utf8"))
    assert "head" in cache_payload["images"]["avg_001_a_1/base.png"]


def test_output_exists_skipped(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    out = workdir / "export" / "avg_001_a_1" / "base.png"
    _write_png(out, Image.new("RGBA", (180, 180), (0, 255, 0, 255)))
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir, {"avg_001_a_1": {"bases": {"base.png": _base_match()}}}
        ),
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    entry = report.characters["avg_001_a_1"].bases["base.png"]
    assert entry.status == "skipped"
    assert report.stats["base_skipped"] == 1
    assert not (workdir / "cache.json").exists()
    with Image.open(out) as image:
        assert image.getpixel((0, 0)) == (0, 255, 0, 255)


def test_force_reextracts(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    out = workdir / "export" / "avg_001_a_1" / "base.png"
    _write_png(out, Image.new("RGBA", (180, 180), (0, 255, 0, 255)))
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir, {"avg_001_a_1": {"bases": {"base.png": _base_match()}}}
        ),
        force=True,
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    entry = report.characters["avg_001_a_1"].bases["base.png"]
    assert entry.status == "ok"
    with Image.open(out) as image:
        assert image.getpixel((0, 0)) != (0, 255, 0, 255)


def test_special_diff_uses_derive(workdir: Path):
    characters_dir = workdir / "characters"
    char_dir = characters_dir / "avg_001_a_1"
    base = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    base.paste(Image.new("RGBA", (20, 20), (255, 0, 0, 255)), (0, 0))
    _write_png(char_dir / "base.png", base)
    _write_png(char_dir / "d1.png", Image.new("RGBA", (100, 100), (0, 255, 0, 255)))
    _write_meta(char_dir)
    classified = _classified(
        characters_dir,
        {"avg_001_a_1": _char_entry({"base.png": {"diff": ["d1.png"]}})},
    )
    calls = {"face": 0}

    def counting_face(bgr):
        calls["face"] += 1
        return [{"bbox": [100, 50, 140, 90], "confidence": 0.9}]

    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir,
            {"avg_001_a_1": {"bases": {"base.png": _base_match(box=(0, 0, 50, 50))}}},
        ),
        face_detector=counting_face,
        head_detector=_head_detector(0.8),
    )
    diff = report.characters["avg_001_a_1"].diffs["d1.png"]
    assert diff.status == "ok"
    assert diff.special is True
    assert diff.method == "derive"
    assert calls["face"] == 1
    assert (workdir / "export" / "avg_001_a_1" / "d1.png").is_file()


def test_normal_diff_uses_base_box(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir, {"avg_001_a_1": {"bases": {"base.png": _base_match()}}}
        ),
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    diff = report.characters["avg_001_a_1"].diffs["d1.png"]
    assert diff.status == "ok"
    assert diff.special is False
    assert diff.method == "match"
    assert diff.box == [100, 100, 300, 300]


def test_skip_character_removes_all_outputs(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir, {"avg_001_a_1": {"bases": {"base.png": _base_match()}}}
        ),
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
        skip=SkipList({"avg_001_a_1": "skip all"}),
    )

    assert report.characters == {}
    assert report.stats["characters"] == 0
    assert not (workdir / "export" / "avg_001_a_1" / "base.png").exists()


def test_skip_base_removes_base_and_diffs(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir, {"avg_001_a_1": {"bases": {"base.png": _base_match()}}}
        ),
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
        skip=SkipList({"avg_001_a_1/base.png": "skip base"}),
    )

    assert report.characters == {}
    assert not (workdir / "export" / "avg_001_a_1" / "base.png").exists()
    assert not (workdir / "export" / "avg_001_a_1" / "d1.png").exists()


def test_skip_diff_keeps_base(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir, {"avg_001_a_1": {"bases": {"base.png": _base_match()}}}
        ),
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
        skip=SkipList({"avg_001_a_1/d1.png": "skip diff"}),
    )

    char = report.characters["avg_001_a_1"]
    assert char.bases["base.png"].status == "ok"
    assert "d1.png" not in char.diffs
    assert (workdir / "export" / "avg_001_a_1" / "base.png").is_file()
    assert not (workdir / "export" / "avg_001_a_1" / "d1.png").exists()


def test_alpha_png_diff_ignored(workdir: Path):
    characters_dir = workdir / "characters"
    char_dir = characters_dir / "avg_001_a_1"
    _write_png(char_dir / "base.png", Image.new("RGBA", (100, 100), (0, 0, 255, 255)))
    _write_png(char_dir / "d1.png", Image.new("RGBA", (64, 64), (255, 0, 0, 255)))
    _write_png(
        char_dir / "alpha.png", Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    )
    _write_meta(
        char_dir, [{"facePos": {"x": 10, "y": 10}, "faceSize": {"x": 64, "y": 64}}]
    )
    classified = _classified(
        characters_dir,
        {"avg_001_a_1": _char_entry({"base.png": {"diff": ["d1.png", "alpha.png"]}})},
    )
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir,
            {"avg_001_a_1": {"bases": {"base.png": _base_match(box=(0, 0, 50, 50))}}},
        ),
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    char = report.characters["avg_001_a_1"]
    assert "alpha.png" not in char.diffs
    assert char.diffs["d1.png"].status == "ok"
    assert report.stats["diff_files"] == 1
    out_dir = workdir / "export" / "avg_001_a_1"
    assert not (out_dir / "alpha.png").exists()
    assert (out_dir / "d1.png").is_file()


def test_alpha_png_ignored_for_dropped_base(workdir: Path):
    characters_dir = workdir / "characters"
    char_dir = characters_dir / "avg_001_a_1"
    avatar = _avatar_image(180, seed=1)
    for base_name in ("base1.png", "base2.png"):
        _write_png(char_dir / base_name, _base_with_avatar(avatar))
    _write_png(char_dir / "d1.png", Image.new("RGBA", (64, 64), (255, 0, 0, 255)))
    _write_png(
        char_dir / "alpha.png", Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    )
    _write_meta(
        char_dir, [{"facePos": {"x": 10, "y": 10}, "faceSize": {"x": 64, "y": 64}}]
    )
    classified = _classified(
        characters_dir,
        {
            "avg_001_a_1": _char_entry(
                {
                    "base1.png": {"diff": ["d1.png"]},
                    "base2.png": {"diff": ["alpha.png"]},
                }
            )
        },
    )
    match_report = _match_report(
        characters_dir,
        {
            "avg_001_a_1": {
                "bases": {
                    "base1.png": _base_match(threshold=0.9, box=(300, 200, 480, 380)),
                    "base2.png": _base_match(threshold=0.85, box=(300, 200, 480, 380)),
                }
            }
        },
    )
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    char = report.characters["avg_001_a_1"]
    assert char.bases["base2.png"].status == "dropped"
    assert "alpha.png" not in char.diffs
    assert report.stats["diff_files"] == 1  # only d1.png for the kept base


def _special_diff_character(workdir: Path) -> tuple[Path, dict, dict]:
    characters_dir = workdir / "characters"
    char_dir = characters_dir / "avg_001_a_1"
    base = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    base.paste(Image.new("RGBA", (20, 20), (255, 0, 0, 255)), (0, 0))
    _write_png(char_dir / "base.png", base)
    _write_png(char_dir / "d1.png", Image.new("RGBA", (100, 100), (0, 255, 0, 255)))
    _write_meta(char_dir)
    classified = _classified(
        characters_dir,
        {"avg_001_a_1": _char_entry({"base.png": {"diff": ["d1.png"]}})},
    )
    match_report = _match_report(
        characters_dir,
        {"avg_001_a_1": {"bases": {"base.png": _base_match(box=(0, 0, 50, 50))}}},
    )
    return characters_dir, classified, match_report


def test_diff_match_cache_reused(workdir: Path):
    characters_dir, classified, match_report = _special_diff_character(workdir)
    calls = {"face": 0}

    def counting_face(bgr):
        calls["face"] += 1
        return [{"bbox": [100, 50, 140, 90], "confidence": 0.9}]

    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        face_detector=counting_face,
        head_detector=_head_detector(0.8),
    )
    diff = report.characters["avg_001_a_1"].diffs["d1.png"]
    assert diff.status == "ok"
    assert diff.special is True
    assert report.stats["diff_match_cache_new"] == 1
    assert calls["face"] == 1

    cache_payload = json.loads(
        (workdir / "extract_cache.json").read_text(encoding="utf8")
    )
    entry = cache_payload["diff_matches"]["avg_001_a_1/d1.png"]
    assert entry["special"] is True
    assert entry["iou"] < 0.95
    assert entry["box"] == diff.box
    assert entry["method"] == "derive"
    assert entry["confidence"] == 0.9

    report2 = _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        force=True,
        face_detector=counting_face,
        head_detector=_head_detector(0.8),
    )
    diff2 = report2.characters["avg_001_a_1"].diffs["d1.png"]
    assert diff2.status == "ok"
    assert diff2.box == diff.box
    assert report2.stats["diff_match_cache_hits"] == 1
    assert report2.stats["diff_match_cache_new"] == 0
    assert calls["face"] == 1


def test_diff_match_cache_invalidated_on_threshold_change(workdir: Path):
    characters_dir, classified, match_report = _special_diff_character(workdir)
    calls = {"face": 0}

    def counting_face(bgr):
        calls["face"] += 1
        return [{"bbox": [100, 50, 140, 90], "confidence": 0.9}]

    _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        face_detector=counting_face,
        head_detector=_head_detector(0.8),
    )
    assert calls["face"] == 1

    report2 = _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        force=True,
        special_mask_iou=0.0,
        face_detector=counting_face,
        head_detector=_head_detector(0.8),
    )
    diff2 = report2.characters["avg_001_a_1"].diffs["d1.png"]
    assert diff2.status == "ok"
    assert diff2.special is False
    assert diff2.box == [0, 0, 50, 50]
    assert report2.stats["diff_match_cache_hits"] == 0
    assert report2.stats["diff_match_cache_new"] == 1
    cache_payload = json.loads(
        (workdir / "extract_cache.json").read_text(encoding="utf8")
    )
    entry = cache_payload["diff_matches"]["avg_001_a_1/d1.png"]
    assert entry["special"] is False
    assert entry["box"] == [0, 0, 50, 50]


def test_diff_match_cache_replays_no_box(workdir: Path):
    characters_dir, classified, match_report = _special_diff_character(workdir)
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        face_detector=_face_detector(0.2),
        head_detector=_head_detector(0.2),
    )
    diff = report.characters["avg_001_a_1"].diffs["d1.png"]
    assert diff.status == "no_box"
    assert diff.special is True
    assert report.stats["diff_match_cache_new"] == 1

    report2 = _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        force=True,
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    diff2 = report2.characters["avg_001_a_1"].diffs["d1.png"]
    assert diff2.status == "no_box"
    assert diff2.special is True
    assert report2.stats["diff_match_cache_hits"] == 1


def test_diff_match_cache_records_normal_diff(workdir: Path):
    characters_dir, classified = _standard_character(workdir)
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=_match_report(
            characters_dir, {"avg_001_a_1": {"bases": {"base.png": _base_match()}}}
        ),
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    diff = report.characters["avg_001_a_1"].diffs["d1.png"]
    assert diff.status == "ok"
    cache_payload = json.loads(
        (workdir / "extract_cache.json").read_text(encoding="utf8")
    )
    entry = cache_payload["diff_matches"]["avg_001_a_1/d1.png"]
    assert entry["special"] is False
    assert isinstance(entry["iou"], float)
    assert entry["box"] == [100, 100, 300, 300]
    assert report.stats["diff_match_cache_new"] == 1


def test_dedup_drops_similar_base_and_diffs(workdir: Path):
    characters_dir = workdir / "characters"
    char_dir = characters_dir / "avg_001_a_1"
    avatar = _avatar_image(180, seed=1)
    for base_name, threshold in (("base1.png", 0.9), ("base2.png", 0.85)):
        _write_png(char_dir / base_name, _base_with_avatar(avatar))
    _write_png(char_dir / "d1.png", Image.new("RGBA", (64, 64), (255, 0, 0, 255)))
    _write_png(char_dir / "d2.png", Image.new("RGBA", (64, 64), (0, 255, 0, 255)))
    _write_meta(
        char_dir, [{"facePos": {"x": 10, "y": 10}, "faceSize": {"x": 64, "y": 64}}]
    )
    classified = _classified(
        characters_dir,
        {
            "avg_001_a_1": _char_entry(
                {
                    "base1.png": {"diff": ["d1.png"]},
                    "base2.png": {"diff": ["d2.png"]},
                }
            )
        },
    )
    match_report = _match_report(
        characters_dir,
        {
            "avg_001_a_1": {
                "bases": {
                    "base1.png": _base_match(threshold=0.9, box=(300, 200, 480, 380)),
                    "base2.png": _base_match(threshold=0.85, box=(300, 200, 480, 380)),
                }
            }
        },
    )
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    char = report.characters["avg_001_a_1"]
    assert char.bases["base1.png"].status == "ok"
    assert char.bases["base2.png"].status == "dropped"
    assert char.diffs["d1.png"].status == "ok"
    assert char.diffs["d2.png"].status == "dropped"
    assert (workdir / "export" / "avg_001_a_1" / "base1.png").is_file()
    assert not (workdir / "export" / "avg_001_a_1" / "base2.png").exists()
    assert not (workdir / "export" / "avg_001_a_1" / "d2.png").exists()


def test_dedup_keeps_distinct_bases(workdir: Path):
    characters_dir = workdir / "characters"
    char_dir = characters_dir / "avg_001_a_1"
    _write_png(char_dir / "base1.png", _base_with_avatar(_avatar_image(180, seed=1)))
    _write_png(
        char_dir / "base2.png", _base_with_avatar(_block_image(180), at=(600, 400))
    )
    _write_meta(char_dir)
    classified = _classified(
        characters_dir,
        {
            "avg_001_a_1": _char_entry(
                {"base1.png": {"diff": []}, "base2.png": {"diff": []}}
            )
        },
    )
    match_report = _match_report(
        characters_dir,
        {
            "avg_001_a_1": {
                "bases": {
                    "base1.png": _base_match(threshold=0.9, box=(300, 200, 480, 380)),
                    "base2.png": _base_match(threshold=0.85, box=(600, 400, 780, 580)),
                }
            }
        },
    )
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    char = report.characters["avg_001_a_1"]
    assert char.bases["base1.png"].status == "ok"
    assert char.bases["base2.png"].status == "ok"


def _two_base_character(workdir: Path) -> tuple[Path, dict, dict]:
    characters_dir = workdir / "characters"
    char_dir = characters_dir / "avg_001_a_1"
    avatar = _avatar_image(180, seed=1)
    for base_name in ("base1.png", "base2.png"):
        _write_png(char_dir / base_name, _base_with_avatar(avatar))
    _write_meta(char_dir)
    classified = _classified(
        characters_dir,
        {
            "avg_001_a_1": _char_entry(
                {"base1.png": {"diff": []}, "base2.png": {"diff": []}}
            )
        },
    )
    match_report = _match_report(
        characters_dir,
        {
            "avg_001_a_1": {
                "bases": {
                    "base1.png": _base_match(threshold=0.9, box=(300, 200, 480, 380)),
                    "base2.png": _base_match(threshold=0.85, box=(300, 200, 480, 380)),
                }
            }
        },
    )
    return characters_dir, classified, match_report


def test_similarity_cache_reused(workdir: Path, monkeypatch: pytest.MonkeyPatch):
    characters_dir, classified, match_report = _two_base_character(workdir)
    calls = {"n": 0}
    original = extract.avatar_similarity

    def counting(a, b):
        calls["n"] += 1
        return original(a, b)

    monkeypatch.setattr(extract, "avatar_similarity", counting)
    report = _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    assert report.stats["similarity_cache_new"] == 1
    assert report.stats["similarity_cache_hits"] == 0
    assert calls["n"] == 1

    cache_payload = json.loads(
        (workdir / "extract_cache.json").read_text(encoding="utf8")
    )
    key = "avg_001_a_1/base1.png__base2.png"
    entry = cache_payload["similarities"][key]
    assert entry["similarity"] > 0.98
    assert entry["boxA"] == [300, 200, 480, 380]

    report2 = _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    assert report2.stats["similarity_cache_hits"] == 1
    assert report2.stats["similarity_cache_new"] == 0
    assert calls["n"] == 1


def test_similarity_cache_invalidated_on_box_change(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
):
    characters_dir, classified, match_report = _two_base_character(workdir)
    calls = {"n": 0}
    original = extract.avatar_similarity

    def counting(a, b):
        calls["n"] += 1
        return original(a, b)

    monkeypatch.setattr(extract, "avatar_similarity", counting)
    _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report,
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    assert calls["n"] == 1

    match_report2 = _match_report(
        characters_dir,
        {
            "avg_001_a_1": {
                "bases": {
                    "base1.png": _base_match(threshold=0.9, box=(300, 200, 480, 380)),
                    "base2.png": _base_match(threshold=0.85, box=(350, 220, 530, 400)),
                }
            }
        },
    )
    report2 = _run(
        workdir,
        characters_dir,
        classified,
        match_report=match_report2,
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    assert report2.stats["similarity_cache_hits"] == 0
    assert report2.stats["similarity_cache_new"] == 1
    assert calls["n"] == 2
    cache_payload = json.loads(
        (workdir / "extract_cache.json").read_text(encoding="utf8")
    )
    entry = cache_payload["similarities"]["avg_001_a_1/base1.png__base2.png"]
    assert entry["boxB"] == [350, 220, 530, 400]


def test_force_match_runs_inline(workdir: Path):
    characters_dir = workdir / "characters"
    char_dir = characters_dir / "avg_001_a_1"
    avatar = _avatar_image(180, seed=1)
    _write_png(char_dir / "base.png", _base_with_avatar(avatar, at=(300, 200)))
    _write_meta(char_dir)
    avatars_dir = workdir / "avatars"
    _write_png(avatars_dir / "char_001_a.png", avatar)
    classified = _classified(
        characters_dir,
        {"avg_001_a_1": _char_entry({"base.png": {"diff": []}})},
    )
    match_report = _match_report(
        characters_dir,
        {"avg_001_a_1": {"bases": {"base.png": _base_match(threshold=0.5)}}},
    )
    report = extract.extract_characters(
        classified,
        characters_dir,
        workdir / "export",
        manual={},
        match_report=match_report,
        force_match=True,
        avatars_dir=avatars_dir,
        derive_model=_model(),
        cache=extract.FaceHeadCache(workdir / "cache.json"),
        extract_cache=extract.ExtractCache(workdir / "extract_cache.json"),
        face_detector=_raising_detector(),
        head_detector=_raising_detector(),
    )
    entry = report.characters["avg_001_a_1"].bases["base.png"]
    assert entry.status == "ok"
    assert entry.method == "match"
    assert entry.box == [300, 200, 480, 380]


def test_load_manual_supports_flat_and_wrapper(workdir: Path):
    flat = workdir / "flat.json"
    flat.write_text(json.dumps({"a/b.png": {"box": [1, 2, 3, 4]}}), encoding="utf8")
    assert extract.load_manual(flat) == {"a/b.png": {"box": [1, 2, 3, 4]}}
    wrapper = workdir / "wrapper.json"
    wrapper.write_text(
        json.dumps({"generated_at": "x", "images": {"a/b.png": {"box": [1, 2, 3, 4]}}}),
        encoding="utf8",
    )
    assert extract.load_manual(wrapper) == {"a/b.png": {"box": [1, 2, 3, 4]}}
    assert extract.load_manual(workdir / "missing.json") == {}


def _stub_top1(image_path, **kwargs):
    return {
        "image": str(image_path),
        "image_size": [100, 100],
        "detected": True,
        "face_pos": {"x": 10, "y": 20, "w": 81, "h": 81},
        "confidence": 0.9,
        "error": None,
    }


def _stub_head_top1(image_path, **kwargs):
    return {
        "head_detected": True,
        "head_pos": {"x": 20, "y": 10, "w": 60, "h": 60},
        "head_confidence": 0.8,
        "head_error": None,
    }


@pytest.fixture
def cli_env(monkeypatch):
    monkeypatch.setattr(detect, "_check_ml_deps", lambda: True)
    monkeypatch.setattr(detect_bases, "_check_head_deps", lambda: True)
    monkeypatch.setattr(detect, "detect_top1", _stub_top1)
    monkeypatch.setattr(detect_bases, "detect_head_top1", _stub_head_top1)


def _write_cli_inputs(workdir: Path) -> tuple[Path, Path, Path, Path]:
    characters_dir, classified = _standard_character(workdir)
    classified_path = workdir / "classified.json"
    classified_path.write_text(
        json.dumps(classified, ensure_ascii=False), encoding="utf8"
    )
    match_path = workdir / "match.json"
    match_path.write_text(
        json.dumps(
            _match_report(
                characters_dir,
                {"avg_001_a_1": {"bases": {"base.png": _base_match()}}},
            )
        ),
        encoding="utf8",
    )
    model_path = workdir / "model.json"
    model_path.write_text(json.dumps(_model()), encoding="utf8")
    manual_path = workdir / "manual.json"
    manual_path.write_text(json.dumps({}), encoding="utf8")
    return classified_path, match_path, model_path, manual_path


def test_cli_end_to_end(cli_env, workdir: Path, capsys: pytest.CaptureFixture):
    classified_path, match_path, model_path, manual_path = _write_cli_inputs(workdir)
    report_path = workdir / "report.json"
    cache_path = workdir / "extract_cache.json"
    code = extract.main(
        [
            "--classified",
            str(classified_path),
            "--characters-dir",
            str(workdir / "characters"),
            "--output-dir",
            str(workdir / "export"),
            "--match",
            str(match_path),
            "--avatars-dir",
            str(workdir / "avatars"),
            "--derive-model",
            str(model_path),
            "--manual",
            str(manual_path),
            "--face-head-cache",
            str(workdir / "cache.json"),
            "--cache",
            str(cache_path),
            "--output",
            str(report_path),
            "--character",
            "avg_001_a_1",
        ]
    )
    assert code == 0
    payload = json.loads(report_path.read_text(encoding="utf8"))
    entry = payload["characters"]["avg_001_a_1"]["bases"]["base.png"]
    assert entry["status"] == "ok"
    assert entry["method"] == "match"
    assert payload["stats"]["base_ok"] == 1
    assert payload["stats"]["diff_match_cache_new"] == 1
    assert payload["extract_cache"] == str(cache_path)
    assert (workdir / "export" / "avg_001_a_1" / "base.png").is_file()
    assert payload["match"] == str(match_path)
    cache_payload = json.loads(cache_path.read_text(encoding="utf8"))
    assert "avg_001_a_1/d1.png" in cache_payload["diff_matches"]
    assert cache_payload["similarities"] == {}


def test_cli_output_stdout_dash(cli_env, workdir: Path, capsys: pytest.CaptureFixture):
    classified_path, match_path, model_path, manual_path = _write_cli_inputs(workdir)
    code = extract.main(
        [
            "--classified",
            str(classified_path),
            "--characters-dir",
            str(workdir / "characters"),
            "--output-dir",
            str(workdir / "export"),
            "--match",
            str(match_path),
            "--derive-model",
            str(model_path),
            "--manual",
            str(manual_path),
            "--cache",
            str(workdir / "extract_cache.json"),
            "--output",
            "-",
            "--character",
            "avg_001_a_1",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{") : out.rfind("}") + 1])
    assert payload["stats"]["base_ok"] == 1


def test_cli_missing_classified(cli_env, capsys: pytest.CaptureFixture, workdir: Path):
    code = extract.main(["--classified", str(workdir / "missing.json")])
    assert code == 1
    assert "not found" in capsys.readouterr().err


def test_cli_character_not_found(cli_env, capsys: pytest.CaptureFixture, workdir: Path):
    classified_path, _, model_path, _ = _write_cli_inputs(workdir)
    code = extract.main(
        [
            "--classified",
            str(classified_path),
            "--derive-model",
            str(model_path),
            "--character",
            "avg_999_unknown_1",
        ]
    )
    assert code == 1
    assert "character not found" in capsys.readouterr().err


def test_cli_invalid_threshold(cli_env, capsys: pytest.CaptureFixture, workdir: Path):
    classified_path, _, model_path, _ = _write_cli_inputs(workdir)
    code = extract.main(
        [
            "--classified",
            str(classified_path),
            "--derive-model",
            str(model_path),
            "--match-threshold",
            "1.5",
        ]
    )
    assert code == 1
    assert "--match-threshold" in capsys.readouterr().err
