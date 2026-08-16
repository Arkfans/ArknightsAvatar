import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image

from arknightsavatar import detect
from arknightsavatar.skip import SkipList

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def workdir():
    """Project-internal writable temp dir (system Temp is not accessible in the sandbox)."""
    base = PROJECT_ROOT / f"arknightsavatar_detect_test_{uuid4().hex[:8]}"
    os.makedirs(base, mode=0o777)
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _write_image(path: Path, size: tuple[int, int] = (100, 100)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (200, 120, 80, 255)).save(path)


def _fake_detector(*boxes: tuple[float, ...]):
    """返回一个固定检测结果的测试替身：``callable(bgr) -> list[dict]``。"""

    def detector(bgr: np.ndarray) -> list[dict]:
        return [
            {"bbox": [round(v) for v in box[:4]], "confidence": float(box[4])}
            for box in boxes
        ]

    return detector


def _stub_top1(image_path, **kwargs):
    """CLI 测试用的 detect_top1 替身，不依赖真实模型。"""
    path = str(image_path)
    return {
        "image": path,
        "image_size": [100, 100],
        "detected": True,
        "face_pos": {"x": 10, "y": 20, "w": 81, "h": 81},
        "confidence": 0.9,
        "error": None,
    }


@pytest.fixture
def cli_env(monkeypatch):
    """让 CLI 测试绕过 ML 依赖检查与真实模型调用。"""
    monkeypatch.setattr(detect, "_check_ml_deps", lambda: True)
    monkeypatch.setattr(detect, "detect_top1", _stub_top1)


def test_scale_box_around_center():
    box = detect._scale_box(np.array([10, 20, 90, 100, 0.9], dtype=np.float32))
    assert np.allclose(box, [5.45, 15.45, 94.55, 104.55, 0.9])


def test_clip_bbox():
    assert detect._clip_bbox([-5, -10, 105, 110], 100, 100) == (0, 0, 100, 100)
    assert detect._clip_bbox([10.2, 20.6, 90.4, 99.8], 100, 100) == (10, 21, 90, 100)


def test_bbox_to_face_pos():
    assert detect._bbox_to_face_pos(10, 20, 90, 100) == {
        "x": 10,
        "y": 20,
        "w": 81,
        "h": 81,
    }
    assert detect._bbox_to_face_pos(0, 0, 2, 2) == {"x": 0, "y": 0, "w": 3, "h": 3}
    assert detect._bbox_to_face_pos(3, 4, 9, 12) == {"x": 3, "y": 4, "w": 7, "h": 9}


def test_detect_top1_picks_highest_confidence(tmp_path: Path):
    image = tmp_path / "a.png"
    _write_image(image)
    result = detect.detect_top1(
        image,
        conf=0.1,
        detector=_fake_detector((10, 20, 90, 100, 0.7), (20, 30, 80, 90, 0.9)),
    )
    assert result["detected"] is True
    assert result["confidence"] == 0.9
    assert result["face_pos"] == {"x": 20, "y": 30, "w": 61, "h": 61}
    assert result["image_size"] == [100, 100]
    assert result["error"] is None


def test_detect_top1_filters_below_conf(tmp_path: Path):
    image = tmp_path / "a.png"
    _write_image(image)
    result = detect.detect_top1(
        image,
        conf=0.5,
        detector=_fake_detector((10, 20, 90, 100, 0.4)),
    )
    assert result["detected"] is False
    assert result["face_pos"] is None
    assert result["confidence"] is None
    assert result["error"] is None


def test_detect_top1_no_detections(tmp_path: Path):
    image = tmp_path / "a.png"
    _write_image(image)
    result = detect.detect_top1(image, detector=_fake_detector())
    assert result["detected"] is False
    assert result["face_pos"] is None
    assert result["confidence"] is None


def test_detect_top1_clips_out_of_bounds(tmp_path: Path):
    image = tmp_path / "a.png"
    _write_image(image)
    result = detect.detect_top1(
        image,
        conf=0.1,
        detector=_fake_detector((95, 95, 120, 130, 0.9)),
    )
    assert result["detected"] is True
    assert result["face_pos"] == {"x": 95, "y": 95, "w": 6, "h": 6}


def test_detect_top1_missing_file(tmp_path: Path):
    result = detect.detect_top1(tmp_path / "missing.png")
    assert result["detected"] is False
    assert result["error"] is not None
    assert "图片不存在" in result["error"]


def test_detect_top1_detector_error(tmp_path: Path):
    image = tmp_path / "a.png"
    _write_image(image)

    def boom(bgr):
        raise RuntimeError("model failed")

    result = detect.detect_top1(image, detector=boom)
    assert result["detected"] is False
    assert result["error"] == "RuntimeError: model failed"


def test_detect_top1_detector_error_clears_image_size(tmp_path: Path):
    """P1-11: 图片已读取成功后检测失败 → image_size 被清空为 None，
    维持「error 非空 ⇒ 其余字段不可信」契约，避免下游误信尺寸。"""
    image = tmp_path / "a.png"
    _write_image(image)

    def boom(bgr):
        raise RuntimeError("model failed")

    result = detect.detect_top1(image, detector=boom)
    assert result["error"] == "RuntimeError: model failed"
    assert result["image_size"] is None
    assert result["face_pos"] is None
    assert result["confidence"] is None
    assert result["detected"] is False


def test_detect_characters_report_and_stats(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    _write_image(characters_dir / "avg_003_kalts_1" / "avg_003_kalts_1$1.png")
    _write_image(characters_dir / "avg_007_closre_1" / "base.png")

    report = detect.detect_characters(
        characters_dir,
        conf=0.1,
        detector=_fake_detector((10, 20, 90, 100, 0.9)),
    )

    assert report.stats == {
        "total_characters": 2,
        "total_images": 2,
        "detected": 2,
        "not_detected": 0,
        "errors": 0,
    }
    assert list(report.characters) == ["avg_003_kalts_1", "avg_007_closre_1"]
    item = report.characters["avg_003_kalts_1"].images["avg_003_kalts_1$1.png"]
    assert item.image_size == [100, 100]
    assert item.face_pos == {"x": 10, "y": 20, "w": 81, "h": 81}

    payload = report.as_dict()
    assert payload["characters_dir"] == str(characters_dir)
    assert (
        payload["characters"]["avg_003_kalts_1"]["images"]["avg_003_kalts_1$1.png"][
            "confidence"
        ]
        == 0.9
    )


def test_detect_characters_character_filter_and_limit(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    _write_image(characters_dir / "avg_003_kalts_1" / "a.png")
    _write_image(characters_dir / "avg_007_closre_1" / "b.png")

    filtered = detect.detect_characters(
        characters_dir,
        character="avg_007_closre_1",
        detector=_fake_detector((10, 20, 90, 100, 0.9)),
    )
    assert list(filtered.characters) == ["avg_007_closre_1"]
    assert filtered.stats["total_characters"] == 1

    limited = detect.detect_characters(
        characters_dir,
        limit=1,
        detector=_fake_detector((10, 20, 90, 100, 0.9)),
    )
    assert limited.stats["total_characters"] == 1
    assert list(limited.characters) == ["avg_003_kalts_1"]


def test_detect_characters_error_image(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    char_dir = characters_dir / "avg_001_a_1"
    char_dir.mkdir(parents=True)
    (char_dir / "broken.png").write_bytes(b"not an image")

    report = detect.detect_characters(
        characters_dir,
        detector=_fake_detector((10, 20, 90, 100, 0.9)),
    )

    assert report.stats["errors"] == 1
    assert report.stats["detected"] == 0
    assert report.characters["avg_001_a_1"].images["broken.png"].error is not None


def test_detect_characters_respects_skip(workdir: Path):
    characters_dir = workdir / "characters"
    _write_image(characters_dir / "skip_all" / "a.png")
    _write_image(characters_dir / "avg_001_a_1" / "keep.png")
    _write_image(characters_dir / "avg_001_a_1" / "skip.png")
    detector = _fake_detector((10, 20, 90, 100, 0.9))

    report = detect.detect_characters(
        characters_dir,
        conf=0.1,
        detector=detector,
        skip=SkipList({"skip_all": "reason", "avg_001_a_1/skip.png": "bad"}),
    )

    assert list(report.characters) == ["avg_001_a_1"]
    assert list(report.characters["avg_001_a_1"].images) == ["keep.png"]
    assert report.stats == {
        "total_characters": 1,
        "total_images": 1,
        "detected": 1,
        "not_detected": 0,
        "errors": 0,
    }


def test_cli_missing_ml_deps(monkeypatch, capsys: pytest.CaptureFixture):
    monkeypatch.setattr(detect, "_check_ml_deps", lambda: False)
    code = detect.main([])
    assert code == 1
    assert "torch and anime-face-detector" in capsys.readouterr().err


def test_cli_characters_dir_missing(
    cli_env, capsys: pytest.CaptureFixture, tmp_path: Path
):
    code = detect.main(["--characters-dir", str(tmp_path / "missing")])
    assert code == 1
    assert "not found" in capsys.readouterr().err


def test_cli_character_not_found(
    cli_env, capsys: pytest.CaptureFixture, tmp_path: Path
):
    characters_dir = tmp_path / "characters"
    characters_dir.mkdir()
    code = detect.main(
        ["--characters-dir", str(characters_dir), "--character", "avg_999_unknown_1"]
    )
    assert code == 1
    assert "character not found" in capsys.readouterr().err


def test_cli_character_filter(cli_env, tmp_path: Path):
    characters_dir = tmp_path / "characters"
    _write_image(characters_dir / "avg_003_kalts_1" / "a.png")
    _write_image(characters_dir / "avg_007_closre_1" / "b.png")
    output = tmp_path / "report.json"

    code = detect.main(
        [
            "--characters-dir",
            str(characters_dir),
            "--character",
            "avg_007_closre_1",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf8"))
    assert list(payload["characters"]) == ["avg_007_closre_1"]
    assert payload["stats"]["total_characters"] == 1
    assert payload["stats"]["total_images"] == 1


def test_cli_limit(cli_env, tmp_path: Path):
    characters_dir = tmp_path / "characters"
    _write_image(characters_dir / "avg_003_kalts_1" / "a.png")
    _write_image(characters_dir / "avg_007_closre_1" / "b.png")
    output = tmp_path / "report.json"

    code = detect.main(
        [
            "--characters-dir",
            str(characters_dir),
            "--limit",
            "1",
            "--output",
            str(output),
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf8"))
    assert payload["stats"]["total_characters"] == 1
    assert list(payload["characters"]) == ["avg_003_kalts_1"]


def test_cli_output_stdout_dash(cli_env, capsys: pytest.CaptureFixture, tmp_path: Path):
    characters_dir = tmp_path / "characters"
    _write_image(characters_dir / "avg_003_kalts_1" / "a.png")
    _write_image(characters_dir / "avg_007_closre_1" / "b.png")

    code = detect.main(["--characters-dir", str(characters_dir), "--output", "-"])

    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("{"))
    payload = json.loads("\n".join(lines[start:]))
    assert payload["stats"]["total_characters"] == 2
    assert payload["stats"]["detected"] == 2


def test_cli_single_image_mode(cli_env, tmp_path: Path):
    image = tmp_path / "a.png"
    _write_image(image)
    output = tmp_path / "report.json"

    code = detect.main([str(image), "--output", str(output)])

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf8"))
    assert payload["stats"] == {
        "images": 1,
        "detected": 1,
        "not_detected": 0,
        "errors": 0,
    }
    assert str(image) in payload["images"]
    assert payload["images"][str(image)]["face_pos"] == {
        "x": 10,
        "y": 20,
        "w": 81,
        "h": 81,
    }


def test_cli_invalid_device(cli_env):
    with pytest.raises(SystemExit):
        detect.main(["--device", "gpu"])


def test_cli_invalid_conf(cli_env, capsys: pytest.CaptureFixture):
    code = detect.main(["--conf", "1.5"])
    assert code == 1
    assert "--conf" in capsys.readouterr().err


def test_detect_characters_progress_reports_every_character(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    _write_image(characters_dir / "avg_003_kalts_1" / "a.png")
    _write_image(characters_dir / "avg_007_closre_1" / "b.png")

    calls: list[tuple[int, int, str]] = []
    detect.detect_characters(
        characters_dir,
        detector=_fake_detector((10, 20, 90, 100, 0.9)),
        progress=lambda index, total, label: calls.append((index, total, label)),
    )

    # 逐角色回调，不按 20 节流；total 与实际处理数一致
    assert calls == [(1, 2, "avg_003_kalts_1"), (2, 2, "avg_007_closre_1")]


def test_detect_characters_progress_total_respects_limit(tmp_path: Path):
    characters_dir = tmp_path / "characters"
    _write_image(characters_dir / "avg_003_kalts_1" / "a.png")
    _write_image(characters_dir / "avg_007_closre_1" / "b.png")

    calls: list[tuple[int, int, str]] = []
    detect.detect_characters(
        characters_dir,
        limit=1,
        detector=_fake_detector((10, 20, 90, 100, 0.9)),
        progress=lambda index, total, label: calls.append((index, total, label)),
    )

    assert calls == [(1, 1, "avg_003_kalts_1")]
