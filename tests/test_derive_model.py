import json
from pathlib import Path

from arknightsavatar import derive_model


def _entry(
    index: int = 0,
    face=(100, 50, 40, 40),
    head=(80, 20, 80, 90),
    box=(60, 30, 260, 230),
):
    x1, y1, x2, y2 = box
    # 让各条目裁切框中心与边长都有差异，避免拟合时方差为 0
    shift = index * 6
    grow = index * 10
    return {
        "detected": True,
        "head_detected": True,
        "confidence": 0.95,
        "head_confidence": 0.90,
        "face_pos": {"x": face[0], "y": face[1], "w": face[2], "h": face[3]},
        "head_pos": {"x": head[0], "y": head[1], "w": head[2], "h": head[3]},
        "box": [x1 + shift, y1 + shift, x2 + shift + grow, y2 + shift + grow],
        "threshold": 0.97,
        "image": "img.png",
        "image_size": [1024, 1024],
        "avatar": "char_001_a",
    }


def _write_report(path: Path, n: int = 5) -> None:
    report = {"characters": {}}
    for i in range(n):
        name = f"avg_{i:03d}_x_1"
        report["characters"][name] = {"bases": {f"base{i}.png": _entry(index=i)}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf8")


def test_fit_and_predict_shapes():
    rows = [("c", "b.png", _entry(index=i)) for i in range(4)]
    X = derive_model.feature_matrix(rows)
    Y = derive_model.target_center_size(rows)
    assert X.shape == (4, 8)
    assert Y.shape == (4, 3)
    coef, r2 = derive_model.fit_model(X, Y)
    assert coef.shape == (9, 3)
    assert len(r2) == 3
    centers, boxes = derive_model.predict_boxes(coef, X)
    assert centers.shape == (4, 3)
    assert boxes.shape == (4, 4)


def test_load_valid_rows_filters_by_confidence(tmp_path):
    source = tmp_path / "report.json"
    payload = {"characters": {"a": {"bases": {"b.png": _entry()}}}}
    payload["characters"]["a"]["bases"]["low.png"] = _entry()
    payload["characters"]["a"]["bases"]["low.png"]["confidence"] = 0.5
    payload["characters"]["a"]["bases"]["nodetect.png"] = _entry()
    payload["characters"]["a"]["bases"]["nodetect.png"]["detected"] = False
    source.write_text(json.dumps(payload), encoding="utf8")

    report, rows = derive_model.load_valid_rows(source, min_conf=0.8)
    names = {name for _, name, _ in rows}
    assert names == {"b.png"}
    assert report["characters"]["a"]["bases"]["b.png"]["box"]


def test_main_writes_model_and_stats(tmp_path, capsys):
    source = tmp_path / "report.json"
    _write_report(source, n=6)
    out_dir = tmp_path / "derive"
    code = derive_model.main(
        ["--source", str(source), "--out-dir", str(out_dir), "--no-compare"]
    )
    assert code == 0
    model = json.loads((out_dir / "model.json").read_text(encoding="utf8"))
    assert model["fit_samples"] == 6
    assert model["feature_order"] == derive_model.FEATURES
    assert len(model["coef"]) == 9
    assert len(model["coef"][0]) == 3
    assert (out_dir / "derive_coords.json").is_file()
    assert (out_dir / "stats.json").is_file()
    stats = json.loads((out_dir / "stats.json").read_text(encoding="utf8"))
    assert stats["n"] == 6
    assert "iou" in stats


def test_main_missing_source(tmp_path, capsys):
    code = derive_model.main(
        ["--source", str(tmp_path / "nope.json"), "--out-dir", str(tmp_path / "out")]
    )
    assert code == 1
    assert "not found" in capsys.readouterr().err


def test_main_relative_source_resolved_against_cwd(tmp_path, monkeypatch, capsys):
    """相对 --source 按当前工作目录解析（而非 --out-dir）。"""
    source = tmp_path / "report.json"
    _write_report(source, n=3)
    out_dir = tmp_path / "derive"
    monkeypatch.chdir(tmp_path)
    code = derive_model.main(
        ["--source", "report.json", "--out-dir", str(out_dir), "--no-compare"]
    )
    assert code == 0
    assert (out_dir / "model.json").is_file()
    assert (out_dir / "stats.json").is_file()


def test_main_no_valid_rows(tmp_path, capsys):
    source = tmp_path / "report.json"
    source.write_text(json.dumps({"characters": {}}), encoding="utf8")
    code = derive_model.main(
        ["--source", str(source), "--out-dir", str(tmp_path / "out")]
    )
    assert code == 1
    assert "没有有效条目" in capsys.readouterr().err


def test_iou_and_norm_box():
    assert derive_model.iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert derive_model.iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert derive_model.norm_box([100, 50, 300, 250], (1000, 500)) == [
        0.1,
        0.1,
        0.3,
        0.5,
    ]


def test_fit_model_zero_variance_returns_zero_r2():
    """多行同目标 → ss_tot==0，r2 应降级为 0.0 而非除零崩溃。"""
    rows = [("c", f"b{i}.png", _entry(index=0)) for i in range(3)]
    X = derive_model.feature_matrix(rows)
    Y = derive_model.target_center_size(rows)
    coef, r2 = derive_model.fit_model(X, Y)
    assert coef.shape == (9, 3)
    assert r2 == [0.0, 0.0, 0.0]


def test_fit_model_single_sample_returns_zero_r2():
    """单样本同样 ss_tot==0；fit_model 不再抛 ZeroDivisionError。"""
    rows = [("c", "b0.png", _entry(index=0))]
    X = derive_model.feature_matrix(rows)
    Y = derive_model.target_center_size(rows)
    coef, r2 = derive_model.fit_model(X, Y)
    assert r2 == [0.0, 0.0, 0.0]


def test_main_single_sample_rejected(tmp_path, capsys):
    source = tmp_path / "report.json"
    _write_report(source, n=1)
    code = derive_model.main(
        ["--source", str(source), "--out-dir", str(tmp_path / "out")]
    )
    assert code == 1
    assert "不足 2" in capsys.readouterr().err
