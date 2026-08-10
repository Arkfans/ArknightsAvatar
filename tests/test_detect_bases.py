import json
import os
import shutil
from uuid import uuid4
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from npcavatar import detect, detect_bases


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def workdir():
    """项目内可写的临时目录（沙箱环境无法访问系统 Temp 时的替代实现）。"""
    base = PROJECT_ROOT / f"npcavatar_test_{uuid4().hex[:8]}"
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
            {"bbox": [int(round(v)) for v in box[:4]], "confidence": float(box[4])}
            for box in boxes
        ]

    return detector


def _entry(
    threshold: float = 0.99,
    avatar: str = "char_003_kalts.png",
    box: tuple[int, int, int, int] = (10, 20, 90, 100),
) -> dict:
    return {
        "avatar": avatar,
        "threshold": threshold,
        "box": list(box),
        "box_norm": [round(v / 1024, 6) for v in box],
    }


def _match_report(characters_dir: Path, entries: dict[str, dict[str, dict]]) -> dict:
    return {
        "generated_at": "2026-01-01T00:00:00+00:00",
        "characters_dir": str(characters_dir),
        "characters": {
            name: {"status": "ok", "candidates": [], "bases": bases}
            for name, bases in entries.items()
        },
    }


def _stub_top1(image_path, **kwargs):
    """CLI 测试用的 detect_top1 替身，不依赖真实模型。"""
    path = str(image_path)
    return {
        "image": path,
        "image_size": [100, 100],
        "detected": True,
        "face_pos": {"x": 50, "y": 60, "w": 81, "h": 81},
        "confidence": 0.9,
        "error": None,
    }


@pytest.fixture
def cli_env(monkeypatch):
    """让 CLI 测试绕过 ML 依赖检查与真实模型调用。"""
    monkeypatch.setattr(detect, "_check_ml_deps", lambda: True)
    monkeypatch.setattr(detect, "detect_top1", _stub_top1)


def test_filter_bases_includes_only_strictly_above_threshold(workdir: Path):
    characters_dir = workdir / "characters"
    report = _match_report(
        characters_dir,
        {
            "avg_001_a_1": {
                "above.png": _entry(0.96),
                "equal.png": _entry(0.95),
                "below.png": _entry(0.94),
            }
        },
    )

    selected = detect_bases.filter_bases(report, threshold=0.95)

    assert len(selected) == 1
    name, base, entry, path = selected[0]
    assert (name, base) == ("avg_001_a_1", "above.png")
    assert entry["threshold"] == 0.96
    assert path == characters_dir / "avg_001_a_1" / "above.png"


def test_filter_bases_skips_entries_without_threshold(workdir: Path):
    characters_dir = workdir / "characters"
    report = _match_report(
        characters_dir,
        {
            "avg_001_a_1": {
                "ok.png": _entry(0.96),
                "failed.png": {"error": "no readable avatar"},
                "none.png": {"avatar": "x.png", "threshold": None},
            }
        },
    )

    selected = detect_bases.filter_bases(report, threshold=0.95)

    assert [(name, base) for name, base, _, _ in selected] == [("avg_001_a_1", "ok.png")]


def test_filter_bases_characters_dir_override(workdir: Path):
    report = _match_report(
        workdir / "characters",
        {"avg_001_a_1": {"a.png": _entry(0.96)}},
    )
    other = workdir / "other"

    selected = detect_bases.filter_bases(report, threshold=0.95, characters_dir=other)

    assert selected[0][3] == other / "avg_001_a_1" / "a.png"


def test_detect_matched_bases_report_and_stats(workdir: Path):
    characters_dir = workdir / "characters"
    _write_image(characters_dir / "avg_001_a_1" / "a.png")
    _write_image(characters_dir / "avg_002_b_1" / "b.png")
    report_data = _match_report(
        characters_dir,
        {
            "avg_001_a_1": {"a.png": _entry(0.96)},
            "avg_002_b_1": {"b.png": _entry(0.97)},
        },
    )

    report = detect_bases.detect_matched_bases(
        report_data,
        characters_dir,
        conf=0.5,
        detector=_fake_detector((10, 20, 90, 100, 0.9)),
    )

    assert report.stats == {"filtered": 2, "detected": 2, "not_detected": 0, "errors": 0}
    assert list(report.characters) == ["avg_001_a_1", "avg_002_b_1"]
    item = report.characters["avg_001_a_1"].bases["a.png"]
    assert item.image_size == [100, 100]
    assert item.face_pos == {"x": 50, "y": 60, "w": 81, "h": 81}

    payload = report.as_dict()
    entry = payload["characters"]["avg_001_a_1"]["bases"]["a.png"]
    assert entry["avatar"] == "char_003_kalts.png"
    assert entry["threshold"] == 0.96
    assert entry["box"] == [10, 20, 90, 100]
    assert entry["detected"] is True
    assert entry["confidence"] == 0.9
    assert "vis_image" not in entry
    assert payload["threshold"] == 0.95
    assert payload["match_file"] == ""


def test_detect_matched_bases_not_detected_and_missing_file(workdir: Path):
    characters_dir = workdir / "characters"
    _write_image(characters_dir / "avg_001_a_1" / "a.png")
    report_data = _match_report(
        characters_dir,
        {
            "avg_001_a_1": {
                "a.png": _entry(0.96),
                "missing.png": _entry(0.97),
            }
        },
    )

    report = detect_bases.detect_matched_bases(
        report_data,
        characters_dir,
        conf=0.5,
        detector=_fake_detector((10, 20, 90, 100, 0.2)),
    )

    assert report.stats == {"filtered": 2, "detected": 0, "not_detected": 1, "errors": 1}
    assert report.characters["avg_001_a_1"].bases["missing.png"].error is not None


def test_detect_matched_bases_character_filter_and_limit(workdir: Path):
    characters_dir = workdir / "characters"
    _write_image(characters_dir / "avg_001_a_1" / "a.png")
    _write_image(characters_dir / "avg_001_a_1" / "a2.png")
    _write_image(characters_dir / "avg_002_b_1" / "b.png")
    report_data = _match_report(
        characters_dir,
        {
            "avg_001_a_1": {"a.png": _entry(0.96), "a2.png": _entry(0.97)},
            "avg_002_b_1": {"b.png": _entry(0.98)},
        },
    )
    detector = _fake_detector((10, 20, 90, 100, 0.9))

    limited = detect_bases.detect_matched_bases(
        report_data, characters_dir, limit=1, detector=detector
    )
    assert limited.stats["filtered"] == 1
    assert list(limited.characters) == ["avg_001_a_1"]
    assert list(limited.characters["avg_001_a_1"].bases) == ["a.png"]

    filtered = detect_bases.detect_matched_bases(
        report_data, characters_dir, character="avg_002_b_1", detector=detector
    )
    assert list(filtered.characters) == ["avg_002_b_1"]
    assert filtered.stats["filtered"] == 1


def test_detect_matched_bases_progress_callback(workdir: Path):
    characters_dir = workdir / "characters"
    _write_image(characters_dir / "avg_001_a_1" / "a.png")
    _write_image(characters_dir / "avg_002_b_1" / "b.png")
    report_data = _match_report(
        characters_dir,
        {
            "avg_001_a_1": {"a.png": _entry(0.96)},
            "avg_002_b_1": {"b.png": _entry(0.97)},
        },
    )
    calls: list[tuple[int, int, str]] = []

    def progress(index: int, total: int, label: str) -> None:
        calls.append((index, total, label))

    detect_bases.detect_matched_bases(
        report_data,
        characters_dir,
        detector=_fake_detector((10, 20, 90, 100, 0.9)),
        progress=progress,
    )

    assert calls == [
        (1, 2, "avg_001_a_1/a.png"),
        (2, 2, "avg_002_b_1/b.png"),
    ]


def test_draw_annotation_creates_png(workdir: Path):
    base = workdir / "base.png"
    _write_image(base, size=(100, 100))
    out = workdir / "vis.png"

    detect_bases.draw_annotation(
        base,
        [10, 20, 90, 100],
        0.96,
        {"x": 50, "y": 60, "w": 81, "h": 81},
        0.92,
        out,
    )

    assert out.is_file()
    data = np.fromfile(str(out), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    assert img.shape[0] == 100
    assert img.shape[1] == 100


def test_render_annotations_sets_vis_image(workdir: Path):
    characters_dir = workdir / "characters"
    _write_image(characters_dir / "avg_001_a_1" / "a.png")
    report_data = _match_report(
        characters_dir,
        {"avg_001_a_1": {"a.png": _entry(0.96)}},
    )
    report = detect_bases.detect_matched_bases(
        report_data,
        characters_dir,
        detector=_fake_detector((10, 20, 90, 100, 0.9)),
    )
    vis_dir = workdir / "vis"

    count = detect_bases.render_annotations(report, characters_dir, vis_dir)

    assert count == 1
    out = vis_dir / "avg_001_a_1__a.png"
    assert out.is_file()
    assert report.characters["avg_001_a_1"].bases["a.png"].vis_image == str(out)
    payload = report.as_dict()
    assert payload["characters"]["avg_001_a_1"]["bases"]["a.png"]["vis_image"] == str(out)


def test_render_annotations_bad_image_warns(workdir: Path, capsys: pytest.CaptureFixture):
    characters_dir = workdir / "characters"
    report_data = _match_report(
        characters_dir,
        {"avg_001_a_1": {"missing.png": _entry(0.96)}},
    )
    report = detect_bases.detect_matched_bases(
        report_data,
        characters_dir,
        detector=_fake_detector((10, 20, 90, 100, 0.9)),
    )

    count = detect_bases.render_annotations(report, characters_dir, workdir / "vis")

    assert count == 0
    assert report.characters["avg_001_a_1"].bases["missing.png"].vis_image is None
    assert "cannot render" in capsys.readouterr().err


def test_cli_missing_match_report(cli_env, capsys: pytest.CaptureFixture, workdir: Path):
    code = detect_bases.main(["--match", str(workdir / "missing.json")])
    assert code == 1
    assert "match report not found" in capsys.readouterr().err


def test_cli_run_writes_report_and_images(cli_env, workdir: Path):
    characters_dir = workdir / "characters"
    _write_image(characters_dir / "avg_001_a_1" / "a.png")
    _write_image(characters_dir / "avg_002_b_1" / "b.png")
    match_path = workdir / "match.json"
    match_path.write_text(
        json.dumps(
            _match_report(
                characters_dir,
                {
                    "avg_001_a_1": {"a.png": _entry(0.96)},
                    "avg_002_b_1": {"b.png": _entry(0.97)},
                },
            ),
            ensure_ascii=False,
        ),
        encoding="utf8",
    )
    output = workdir / "report.json"
    vis_dir = workdir / "vis"

    code = detect_bases.main(
        [
            "--match", str(match_path),
            "--output", str(output),
            "--vis-dir", str(vis_dir),
        ]
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf8"))
    assert payload["stats"] == {"filtered": 2, "detected": 2, "not_detected": 0, "errors": 0}
    assert payload["match_file"] == str(match_path)
    assert (vis_dir / "avg_001_a_1__a.png").is_file()
    assert (vis_dir / "avg_002_b_1__b.png").is_file()


def test_cli_output_stdout_dash(cli_env, capsys: pytest.CaptureFixture, workdir: Path):
    characters_dir = workdir / "characters"
    _write_image(characters_dir / "avg_001_a_1" / "a.png")
    match_path = workdir / "match.json"
    match_path.write_text(
        json.dumps(
            _match_report(characters_dir, {"avg_001_a_1": {"a.png": _entry(0.96)}}),
            ensure_ascii=False,
        ),
        encoding="utf8",
    )

    code = detect_bases.main(["--match", str(match_path), "--output", "-"])

    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{"): out.rfind("}") + 1])
    assert payload["stats"]["filtered"] == 1
    assert payload["stats"]["detected"] == 1


def test_cli_invalid_threshold(cli_env, capsys: pytest.CaptureFixture):
    code = detect_bases.main(["--threshold", "1.5"])
    assert code == 1
    assert "--threshold" in capsys.readouterr().err


def test_cli_character_not_found(cli_env, capsys: pytest.CaptureFixture, workdir: Path):
    match_path = workdir / "match.json"
    match_path.write_text(
        json.dumps(_match_report(workdir / "characters", {"avg_001_a_1": {}})),
        encoding="utf8",
    )

    code = detect_bases.main(
        ["--match", str(match_path), "--character", "avg_999_unknown_1"]
    )
    assert code == 1
    assert "character not found" in capsys.readouterr().err
