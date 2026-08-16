import json
from pathlib import Path

from arknightsavatar.sample_bases import main, select_characters


def _write_file(char_dir: Path, name: str, payload: bytes = b"base") -> None:
    char_dir.mkdir(parents=True, exist_ok=True)
    (char_dir / name).write_bytes(payload)


def _build_report(root: Path, specs: dict[str, dict[str, list[str]]]) -> Path:
    """specs: {char_id: {base_file: [diff_files, ...]}}; writes PNGs + classification JSON."""
    characters_dir = root / "characters"
    characters: dict[str, dict] = {}
    for char_id, bases in specs.items():
        char_dir = characters_dir / char_id
        for base_name, diffs in bases.items():
            _write_file(char_dir, base_name)
            for diff in diffs:
                _write_file(char_dir, diff, b"diff")
        characters[char_id] = {
            "status": "ok" if bases else "empty",
            "bases": {base: {"diff": diffs} for base, diffs in bases.items()},
            "unassigned": [],
            "sizes": {},
        }
    report = root / "_characters_classified.json"
    report.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-09T00:00:00+00:00",
                "characters_dir": str(characters_dir),
                "stats": {
                    "total": len(specs),
                    "ok": sum(bool(b) for b in specs.values()),
                    "empty": sum(not b for b in specs.values()),
                    "no_base": 0,
                    "base_files": sum(len(b) for b in specs.values()),
                    "diff_files": sum(
                        len(diffs)
                        for bases in specs.values()
                        for diffs in bases.values()
                    ),
                },
                "characters": characters,
            }
        ),
        encoding="utf8",
    )
    return report


def test_copies_only_bases_flattened(tmp_path: Path):
    report = _build_report(
        tmp_path,
        {
            "avg_ok_1": {"avg_ok_1.png": ["1$1.png"]},
            "avg_ok_2": {"avg_ok_2$1.png": ["1$1.png"]},
            "avg_ok_3": {"avg_ok_3.png": []},
        },
    )
    output = tmp_path / "out"

    code = main(
        ["--classified", str(report), "-n", "3", "--seed", "1", "-o", str(output)]
    )

    assert code == 0
    assert sorted(p.name for p in output.iterdir()) == [
        "avg_ok_1.png",
        "avg_ok_2$1.png",
        "avg_ok_3.png",
    ]
    assert (output / "avg_ok_1.png").read_bytes() == b"base"
    assert not (output / "1$1.png").exists()
    assert (output / "avg_ok_2$1.png").read_bytes() == b"base"
    assert (output / "avg_ok_3.png").read_bytes() == b"base"
    assert all(p.is_file() for p in output.iterdir())


def test_seed_reproducible_and_count_limited(capsys, tmp_path: Path):
    report = _build_report(tmp_path, {f"c{i}": {f"c{i}.png": []} for i in range(5)})
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"

    assert (
        main(["--classified", str(report), "-n", "2", "--seed", "7", "-o", str(out1)])
        == 0
    )
    assert (
        main(["--classified", str(report), "-n", "2", "--seed", "7", "-o", str(out2)])
        == 0
    )

    first = {p.name for p in out1.iterdir()}
    second = {p.name for p in out2.iterdir()}
    assert first == second
    assert len(first) == 2
    assert "sampled 2/5 characters (seed=7)" in capsys.readouterr().out


def test_count_exceeds_eligible_copies_all(tmp_path: Path):
    report = _build_report(tmp_path, {"c1": {"c1.png": []}, "c2": {"c2.png": []}})
    output = tmp_path / "out"

    assert main(["--classified", str(report), "-n", "100", "-o", str(output)]) == 0

    assert {p.name for p in output.iterdir()} == {"c1.png", "c2.png"}


def test_empty_characters_excluded(tmp_path: Path):
    report = _build_report(tmp_path, {"c1": {"c1.png": []}, "c_empty": {}})
    output = tmp_path / "out"

    assert main(["--classified", str(report), "-n", "100", "-o", str(output)]) == 0

    assert [p.name for p in output.iterdir()] == ["c1.png"]


def test_missing_source_reported_but_continues(capsys, tmp_path: Path):
    report = _build_report(
        tmp_path,
        {"c1": {"c1.png": []}, "c2": {"ghost.png": []}},
    )
    (tmp_path / "characters" / "c2" / "ghost.png").unlink()
    output = tmp_path / "out"

    assert main(["--classified", str(report), "-n", "100", "-o", str(output)]) == 0

    stdout = capsys.readouterr().out
    assert "missing source files: 1" in stdout
    assert "c2/ghost.png" in stdout
    assert (output / "c1.png").exists()
    assert not (output / "c2.png").exists()


def test_multiple_bases_all_copied(tmp_path: Path):
    report = _build_report(
        tmp_path,
        {"c1": {"c1$1.png": ["1$1.png"], "c1$2.png": ["1$2.png"]}},
    )
    output = tmp_path / "out"

    assert main(["--classified", str(report), "-n", "1", "-o", str(output)]) == 0

    assert (output / "c1$1.png").read_bytes() == b"base"
    assert (output / "c1$2.png").read_bytes() == b"base"
    assert not (output / "1$1.png").exists()
    assert not (output / "1$2.png").exists()


def test_same_name_bases_renamed_with_char_prefix(capsys, tmp_path: Path):
    report = _build_report(
        tmp_path,
        {"c1": {"shared.png": []}, "c2": {"shared.png": []}},
    )
    output = tmp_path / "out"

    assert main(["--classified", str(report), "-n", "100", "-o", str(output)]) == 0

    assert (output / "shared.png").read_bytes() == b"base"
    assert (output / "c2_shared.png").read_bytes() == b"base"
    stdout = capsys.readouterr().out
    assert "same-name collisions renamed" in stdout
    assert "c2/shared.png -> c2_shared.png" in stdout


def test_select_characters_skips_no_base(tmp_path: Path):
    report = _build_report(tmp_path, {"c1": {"c1.png": []}, "c_empty": {}})

    selected, eligible = select_characters(
        json.loads(report.read_text(encoding="utf8")), 100, seed=None
    )

    assert eligible == 1
    assert selected == ["c1"]


def test_missing_classified_json(capsys, tmp_path: Path):
    assert main(["--classified", str(tmp_path / "nope.json")]) == 1
    assert "not found" in capsys.readouterr().err


def test_invalid_report_no_characters(capsys, tmp_path: Path):
    report = tmp_path / "bad.json"
    report.write_text('{"generated_at": "x"}', encoding="utf8")

    assert main(["--classified", str(report)]) == 1
    assert "no 'characters' object" in capsys.readouterr().err
