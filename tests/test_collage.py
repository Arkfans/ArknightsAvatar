import json
import math
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from arknightsavatar import collage
from arknightsavatar.skip import SkipList

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TILE = collage.TILE_SIZE
PAD = collage.PADDING


@pytest.fixture
def workdir():
    """Project-internal writable temp dir (system Temp is not accessible in the sandbox)."""
    base = PROJECT_ROOT / f"arknightsavatar_test_{uuid4().hex[:8]}"
    os.makedirs(base, mode=0o777)
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _write_avatar(path: Path, size: int = TILE, color=(0, 128, 255, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (size, size), color).save(path)


def _build_report(root: Path, specs: dict[str, dict[str, list[str]]]) -> Path:
    """specs: {char_id: {base_file: [diff_files, ...]}}; writes classification JSON."""
    characters = {}
    for char_id, bases in specs.items():
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
                "generated_at": "2026-08-12T00:00:00+00:00",
                "characters_dir": str(root / "characters"),
                "stats": {"total": len(specs)},
                "characters": characters,
            }
        ),
        encoding="utf8",
    )
    return report


def _write_extract_report(root: Path, specs: dict[str, dict[str, str]]) -> Path:
    """specs: {char_id: {diff_name: status}}; writes extract report JSON."""
    report = root / "_avatar_extract.json"
    report.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-12T00:00:00+00:00",
                "characters": {
                    char_id: {
                        "bases": {},
                        "diffs": {
                            name: {"status": status} for name, status in diffs.items()
                        },
                    }
                    for char_id, diffs in specs.items()
                },
            }
        ),
        encoding="utf8",
    )
    return report


def _tile_box(index: int, columns: int) -> tuple[int, int, int, int]:
    column = index % columns
    row = index // columns
    x = column * TILE + (column + 1) * PAD
    y = row * TILE + (row + 1) * PAD
    return x, y, x + TILE, y + TILE


def _min_pixel_sum(region: Image.Image) -> int:
    """Min RGB channel sum over the region, avoiding the deprecated getdata()."""
    data = region.convert("RGBA").tobytes()
    return min(data[i] + data[i + 1] + data[i + 2] for i in range(0, len(data), 4))


def test_collect_diff_names_flattens_excludes_alpha_and_sorts():
    item = {
        "bases": {
            "base_a.png": {"diff": ["10$1.png", "2$1.png", "alpha.png"]},
            "base_b.png": {"diff": ["1$1.png", "3$1.png", "2$1.png"]},
        }
    }
    assert collage.collect_diff_names(item) == [
        "1$1.png",
        "10$1.png",
        "2$1.png",
        "3$1.png",
    ]


def test_collect_diff_names_handles_malformed_items():
    assert collage.collect_diff_names(None) == []
    assert collage.collect_diff_names({"bases": None}) == []
    assert (
        collage.collect_diff_names({"bases": {"b.png": {"diff": "not-a-list"}}}) == []
    )


def test_load_skipped_only_dropped(workdir):
    root = Path(workdir)
    report = _write_extract_report(
        root,
        {
            "c1": {
                "1$1.png": "ok",
                "2$1.png": "dropped",
                "3$1.png": "no_box",
                "4$1.png": "failed",
                "5$1.png": "skipped",
            },
            "c2": {"9$1.png": "dropped"},
            "c3": {"8$1.png": "ok"},
        },
    )
    assert collage.load_skipped(report) == {
        "c1": {"2$1.png"},
        "c2": {"9$1.png"},
    }


def test_load_skipped_invalid_report(workdir):
    root = Path(workdir)
    bad = root / "bad.json"
    bad.write_text("{}", encoding="utf8")
    assert collage.load_skipped(bad) == {}
    assert collage.load_skipped(root / "missing.json") == {}


def test_build_collage_dimensions():
    tiles = [
        (f"{i}$1.png", Image.new("RGBA", (TILE, TILE), (255, 0, 0, 255)), "")
        for i in range(5)
    ]
    columns = 3
    rows = math.ceil(5 / columns)
    image = collage.build_collage(tiles, columns=columns)
    assert image.size == (
        columns * TILE + (columns + 1) * PAD,
        rows * TILE + (rows + 1) * PAD,
    )

    # more columns than tiles -> single row
    image = collage.build_collage(tiles[:2], columns=5)
    assert image.height == TILE + 2 * PAD


def test_build_collage_placeholder_label():
    # missing tile: label draws dark text on the white tile
    image = collage.build_collage([("x.png", None, "[x]")], columns=1, label=True)
    box = _tile_box(0, 1)
    assert _min_pixel_sum(image.crop(box)) < 300

    # skipped tile: [skipped] suffix also draws dark text
    image = collage.build_collage([("x.png", None, "[skipped]")], columns=1, label=True)
    box = _tile_box(0, 1)
    assert _min_pixel_sum(image.crop(box)) < 300

    # no-label: the placeholder tile stays pure white
    image = collage.build_collage([("x.png", None, "[x]")], columns=1, label=False)
    box = _tile_box(0, 1)
    assert _min_pixel_sum(image.crop(box)) >= 750


def test_build_collage_pastes_avatar():
    tiles = [("1$1.png", Image.new("RGBA", (TILE, TILE), (255, 0, 0, 255)), "")]
    image = collage.build_collage(tiles, columns=1, label=False)
    box = _tile_box(0, 1)
    center = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
    assert image.getpixel(center)[:3] == (255, 0, 0)


def test_process_character_skips_no_diff_and_no_export(workdir):
    root = Path(workdir)
    out = root / "out"
    export = root / "export"
    result = collage.process_character(
        "c_empty",
        {"bases": {"b.png": {"diff": []}}},
        export_dir=export,
        output_dir=out,
    )
    assert result.skipped == "no_diff"
    assert result.output is None

    result = collage.process_character(
        "c_nodir",
        {"bases": {"b.png": {"diff": ["1$1.png"]}}},
        export_dir=export,
        output_dir=out,
    )
    assert result.skipped == "no_export"
    assert result.output is None
    assert not out.exists()


def test_process_character_missing_tile_placeholder_and_writes(workdir):
    root = Path(workdir)
    export = root / "export" / "c1"
    _write_avatar(export / "1$1.png", color=(0, 255, 0, 255))
    _write_avatar(export / "2$1.png", color=(0, 0, 255, 255))
    out = root / "out"

    result = collage.process_character(
        "c1",
        {"bases": {"b.png": {"diff": ["1$1.png", "2$1.png", "3$1.png"]}}},
        export_dir=root / "export",
        output_dir=out,
        columns=2,
    )
    assert result.output is not None
    assert result.output.is_file()
    assert result.missing == 1
    image = Image.open(result.output)
    assert image.size == (2 * TILE + 3 * PAD, 2 * TILE + 3 * PAD)


def test_process_character_omits_skipped_even_if_file_exists(workdir):
    root = Path(workdir)
    export = root / "export" / "c1"
    _write_avatar(export / "1$1.png")
    _write_avatar(export / "2$1.png")
    _write_avatar(export / "3$1.png")  # leftover avatar exists but base was dropped
    out = root / "out"

    result = collage.process_character(
        "c1",
        {"bases": {"b.png": {"diff": ["1$1.png", "2$1.png", "3$1.png"]}}},
        export_dir=root / "export",
        output_dir=out,
        columns=2,
        skipped={"3$1.png"},
    )
    assert result.output is not None
    assert result.skipped_omitted == 1
    assert result.missing == 0
    image = Image.open(result.output)
    assert image.size == (2 * TILE + 3 * PAD, TILE + 2 * PAD)  # 2 tiles -> 1 row


def test_process_character_show_skipped_present(workdir):
    root = Path(workdir)
    export = root / "export" / "c1"
    _write_avatar(export / "1$1.png")
    _write_avatar(export / "2$1.png")
    _write_avatar(export / "3$1.png", color=(255, 0, 0, 255))
    out = root / "out"

    result = collage.process_character(
        "c1",
        {"bases": {"b.png": {"diff": ["1$1.png", "2$1.png", "3$1.png"]}}},
        export_dir=root / "export",
        output_dir=out,
        columns=2,
        skipped={"3$1.png"},
        show_skipped=True,
    )
    assert result.output is not None
    assert result.skipped_shown == 1
    assert result.missing == 0
    image = Image.open(result.output)
    assert image.size == (2 * TILE + 3 * PAD, 2 * TILE + 3 * PAD)  # 3 tiles -> 2 rows
    box = _tile_box(2, 2)
    center = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
    assert image.getpixel(center)[:3] == (255, 0, 0)  # avatar rendered
    assert _min_pixel_sum(image.crop(box)) < 300  # [skipped] label drawn


def test_process_character_show_skipped_missing(workdir):
    root = Path(workdir)
    export = root / "export" / "c1"
    _write_avatar(export / "1$1.png")
    _write_avatar(export / "2$1.png")
    out = root / "out"

    result = collage.process_character(
        "c1",
        {"bases": {"b.png": {"diff": ["1$1.png", "2$1.png", "3$1.png"]}}},
        export_dir=root / "export",
        output_dir=out,
        columns=2,
        skipped={"3$1.png"},
        show_skipped=True,
    )
    assert result.output is not None
    assert result.skipped_shown == 1
    assert result.missing == 0
    image = Image.open(result.output)
    assert image.size == (2 * TILE + 3 * PAD, 2 * TILE + 3 * PAD)
    # third tile is a white [skipped] placeholder
    assert _min_pixel_sum(image.crop(_tile_box(2, 2))) < 300


def test_process_character_all_skipped(workdir):
    root = Path(workdir)
    (root / "export" / "c1").mkdir(parents=True)
    out = root / "out"

    result = collage.process_character(
        "c1",
        {"bases": {"b.png": {"diff": ["1$1.png", "2$1.png"]}}},
        export_dir=root / "export",
        output_dir=out,
        skipped={"1$1.png", "2$1.png"},
    )
    assert result.skipped == "all_skipped"
    assert result.skipped_omitted == 2
    assert result.output is None
    assert not out.exists()


def test_process_characters_respects_skip(workdir):
    root = Path(workdir)
    report = _build_report(
        root,
        {
            "c1": {"c1.png": ["1$1.png", "2$1.png"]},
            "c2": {"c2.png": ["3$1.png"]},
        },
    )
    export = root / "export"
    _write_avatar(export / "c1" / "1$1.png")
    _write_avatar(export / "c1" / "2$1.png")
    _write_avatar(export / "c2" / "3$1.png")
    out = root / "out"

    stats = collage.process_characters(
        json.loads(report.read_text(encoding="utf8")),
        export,
        out,
        skip=SkipList({"c2": "skip character", "c1/c1.png": "skip base"}),
    )

    assert stats["characters"] == 0
    assert stats["collaged"] == 0
    assert not out.exists()


def test_process_characters_skip_diff_only(workdir):
    root = Path(workdir)
    report = _build_report(root, {"c1": {"c1.png": ["1$1.png", "2$1.png"]}})
    export = root / "export"
    _write_avatar(export / "c1" / "1$1.png")
    _write_avatar(export / "c1" / "2$1.png")
    out = root / "out"

    stats = collage.process_characters(
        json.loads(report.read_text(encoding="utf8")),
        export,
        out,
        skip=SkipList({"c1/1$1.png": "skip diff"}),
    )

    assert stats["characters"] == 1
    assert stats["collaged"] == 1
    image = Image.open(out / "c1.png")
    assert image.size == (3 * TILE + 4 * PAD, TILE + 2 * PAD)


def test_process_characters_invalid_report_raises(workdir):
    root = Path(workdir)
    with pytest.raises(ValueError):
        collage.process_characters({"characters": None}, root / "export", root / "out")


def test_main_default_all_characters(workdir):
    root = Path(workdir)
    report = _build_report(
        root,
        {
            "c1": {"c1.png": ["1$1.png", "2$1.png"]},
            "c2": {"c2.png": ["3$1.png"]},
            "c_empty": {},
        },
    )
    _write_avatar(root / "export" / "c1" / "1$1.png")
    _write_avatar(root / "export" / "c1" / "2$1.png")
    _write_avatar(root / "export" / "c2" / "3$1.png")
    extract_report = _write_extract_report(root, {})
    out = root / "out"

    assert (
        collage.main(
            [
                "--classified",
                str(report),
                "--export-dir",
                str(root / "export"),
                "--extract-report",
                str(extract_report),
                "-o",
                str(out),
            ]
        )
        == 0
    )

    assert {p.name for p in out.iterdir()} == {"c1.png", "c2.png"}
    # 2 diffs, default 3 columns -> one row
    assert Image.open(out / "c1.png").size == (3 * TILE + 4 * PAD, TILE + 2 * PAD)
    assert Image.open(out / "c2.png").size == (3 * TILE + 4 * PAD, TILE + 2 * PAD)


def test_main_character_filter_and_limit(workdir):
    root = Path(workdir)
    report = _build_report(
        root,
        {"c1": {"c1.png": ["1$1.png"]}, "c2": {"c2.png": ["3$1.png"]}},
    )
    _write_avatar(root / "export" / "c1" / "1$1.png")
    _write_avatar(root / "export" / "c2" / "3$1.png")
    extract_report = _write_extract_report(root, {})
    export = root / "export"

    out1 = root / "out1"
    assert (
        collage.main(
            [
                "--classified",
                str(report),
                "--export-dir",
                str(export),
                "--extract-report",
                str(extract_report),
                "-o",
                str(out1),
                "--character",
                "c2",
            ]
        )
        == 0
    )
    assert [p.name for p in out1.iterdir()] == ["c2.png"]

    out2 = root / "out2"
    assert (
        collage.main(
            [
                "--classified",
                str(report),
                "--export-dir",
                str(export),
                "--extract-report",
                str(extract_report),
                "-o",
                str(out2),
                "--limit",
                "1",
            ]
        )
        == 0
    )
    assert [p.name for p in out2.iterdir()] == ["c1.png"]


def test_main_columns_changes_width(workdir):
    root = Path(workdir)
    report = _build_report(root, {"c1": {"c1.png": ["1$1.png", "2$1.png"]}})
    _write_avatar(root / "export" / "c1" / "1$1.png")
    _write_avatar(root / "export" / "c1" / "2$1.png")
    extract_report = _write_extract_report(root, {})
    export = root / "export"

    out2 = root / "out2"
    assert (
        collage.main(
            [
                "--classified",
                str(report),
                "--export-dir",
                str(export),
                "--extract-report",
                str(extract_report),
                "-o",
                str(out2),
                "--columns",
                "2",
            ]
        )
        == 0
    )
    out5 = root / "out5"
    assert (
        collage.main(
            [
                "--classified",
                str(report),
                "--export-dir",
                str(export),
                "--extract-report",
                str(extract_report),
                "-o",
                str(out5),
                "--columns",
                "5",
            ]
        )
        == 0
    )

    assert Image.open(out2 / "c1.png").width == 2 * TILE + 3 * PAD
    assert Image.open(out5 / "c1.png").width == 5 * TILE + 6 * PAD


def test_main_show_skipped_and_extract_report(workdir):
    root = Path(workdir)
    report = _build_report(root, {"c1": {"c1.png": ["1$1.png", "2$1.png", "3$1.png"]}})
    _write_avatar(root / "export" / "c1" / "1$1.png")
    _write_avatar(root / "export" / "c1" / "2$1.png")
    _write_avatar(root / "export" / "c1" / "3$1.png")
    extract_report = _write_extract_report(
        root,
        {"c1": {"1$1.png": "ok", "2$1.png": "ok", "3$1.png": "dropped"}},
    )
    export = root / "export"

    # default: skipped (dropped) diff omitted even though its avatar exists
    out_default = root / "out_default"
    assert (
        collage.main(
            [
                "--classified",
                str(report),
                "--export-dir",
                str(export),
                "--extract-report",
                str(extract_report),
                "-o",
                str(out_default),
                "--columns",
                "2",
            ]
        )
        == 0
    )
    assert Image.open(out_default / "c1.png").size == (
        2 * TILE + 3 * PAD,
        TILE + 2 * PAD,
    )

    # --show-skipped: 3 tiles, 2 rows, third is rendered with [skipped] label
    out_show = root / "out_show"
    assert (
        collage.main(
            [
                "--classified",
                str(report),
                "--export-dir",
                str(export),
                "--extract-report",
                str(extract_report),
                "-o",
                str(out_show),
                "--columns",
                "2",
                "--show-skipped",
            ]
        )
        == 0
    )
    image = Image.open(out_show / "c1.png")
    assert image.size == (2 * TILE + 3 * PAD, 2 * TILE + 3 * PAD)
    assert _min_pixel_sum(image.crop(_tile_box(2, 2))) < 300  # [skipped] label


def test_main_default_extract_report_missing_falls_back(workdir, monkeypatch):
    root = Path(workdir)
    report = _build_report(root, {"c1": {"c1.png": ["1$1.png", "2$1.png"]}})
    _write_avatar(root / "export" / "c1" / "1$1.png")
    # 2$1.png missing and no extract report -> rendered as [x] placeholder
    monkeypatch.setattr(
        collage, "DEFAULT_EXTRACT_REPORT", str(root / "no_extract.json")
    )
    out = root / "out"

    assert (
        collage.main(
            [
                "--classified",
                str(report),
                "--export-dir",
                str(root / "export"),
                "-o",
                str(out),
            ]
        )
        == 0
    )
    image = Image.open(out / "c1.png")
    assert image.size == (3 * TILE + 4 * PAD, TILE + 2 * PAD)
    assert _min_pixel_sum(image.crop(_tile_box(1, 3))) < 300  # [x] placeholder


def test_main_errors(workdir):
    root = Path(workdir)
    report = _build_report(root, {"c1": {"c1.png": ["1$1.png"]}})
    export = root / "export"
    _write_avatar(export / "c1" / "1$1.png")

    assert collage.main(["--classified", str(root / "missing.json")]) == 1
    assert (
        collage.main(
            ["--classified", str(report), "--export-dir", str(export), "--columns", "0"]
        )
        == 1
    )
    assert (
        collage.main(
            ["--classified", str(report), "--export-dir", str(export), "--limit", "-1"]
        )
        == 1
    )
    assert (
        collage.main(
            [
                "--classified",
                str(report),
                "--export-dir",
                str(export),
                "--character",
                "ghost",
            ]
        )
        == 1
    )
    # explicit missing extract report is an error
    assert (
        collage.main(
            [
                "--classified",
                str(report),
                "--export-dir",
                str(export),
                "--extract-report",
                str(root / "missing_extract.json"),
            ]
        )
        == 1
    )


def test_main_invalid_report_and_missing_export(workdir):
    root = Path(workdir)
    bad = root / "bad.json"
    bad.write_text("{}", encoding="utf8")
    assert collage.main(["--classified", str(bad)]) == 1

    report = _build_report(root, {"c1": {"c1.png": ["1$1.png"]}})
    assert (
        collage.main(
            ["--classified", str(report), "--export-dir", str(root / "noexport")]
        )
        == 1
    )
