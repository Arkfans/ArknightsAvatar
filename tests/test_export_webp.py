import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from arknightsavatar import export_webp
from arknightsavatar.skip import SkipList

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def workdir():
    """Project-internal writable temp dir (system Temp is not accessible in the sandbox)."""
    base = PROJECT_ROOT / f"arknightsavatar_test_{uuid4().hex[:8]}"
    os.makedirs(base, mode=0o777)
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _write_png(
    path: Path, size: int = 180, color=(0, 128, 255, 255), transparent: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (size, size), color)
    if transparent:
        image.putpixel((0, 0), (0, 0, 0, 0))
    image.save(path)


def test_iter_character_dirs_sorted_and_ignores_files(workdir):
    root = Path(workdir)
    export = root / "export"
    (export / "b_char").mkdir(parents=True)
    (export / "a_char").mkdir(parents=True)
    (export / "note.txt").write_text("x", encoding="utf8")
    assert [p.name for p in export_webp.iter_character_dirs(export)] == [
        "a_char",
        "b_char",
    ]
    assert export_webp.iter_character_dirs(root / "missing") == []


def test_iter_pngs_finds_nested_and_sorted(workdir):
    root = Path(workdir)
    char_dir = root / "export" / "c1"
    (char_dir / "sub").mkdir(parents=True)
    (char_dir / "10$1.png").write_bytes(b"x")
    (char_dir / "2$1.png").write_bytes(b"x")
    (char_dir / "sub" / "1$1.png").write_bytes(b"x")
    (char_dir / "alpha.png").write_bytes(b"x")
    names = [
        p.relative_to(char_dir).as_posix() for p in export_webp.iter_pngs(char_dir)
    ]
    assert names == ["10$1.png", "2$1.png", "alpha.png", "sub/1$1.png"]


def test_convert_image_writes_webp_keeping_alpha(workdir):
    root = Path(workdir)
    png = root / "in" / "1$1.png"
    _write_png(png, transparent=True)
    webp = root / "out" / "1$1.webp"
    assert export_webp.convert_image(png, webp, quality=80, method=4) is True
    assert webp.is_file()
    with Image.open(webp) as image:
        assert image.format == "WEBP"
        assert image.size == (180, 180)
        assert image.getpixel((0, 0))[3] == 0  # alpha preserved


def test_convert_characters_incremental_and_stats(workdir):
    root = Path(workdir)
    export = root / "export"
    _write_png(export / "c1" / "1$1.png")
    _write_png(export / "c1" / "2$1.png")
    _write_png(export / "c2" / "3$1.png")
    out = root / "out"

    calls: list[str] = []
    stats = export_webp.convert_characters(
        export,
        out,
        progress=lambda index, total, label: calls.append(f"{index}/{total} {label}"),
    )
    assert stats["characters"] == 2
    assert stats["images"] == 3
    assert stats["converted"] == 3
    assert stats["skipped"] == 0
    assert stats["failed"] == 0
    assert (out / "c1" / "1$1.webp").is_file()
    assert (out / "c2" / "3$1.webp").is_file()
    assert calls == ["1/2 c1: +2 =0 !0", "2/2 c2: +1 =0 !0"]

    # second run skips everything
    stats = export_webp.convert_characters(export, out)
    assert stats["converted"] == 0
    assert stats["skipped"] == 3

    # --force re-converts
    stats = export_webp.convert_characters(export, out, force=True)
    assert stats["converted"] == 3
    assert stats["skipped"] == 0


def test_convert_characters_filter_and_limit(workdir):
    root = Path(workdir)
    export = root / "export"
    _write_png(export / "c1" / "1$1.png")
    _write_png(export / "c2" / "2$1.png")
    _write_png(export / "c3" / "3$1.png")
    out = root / "out"

    stats = export_webp.convert_characters(export, out, characters=["c2"])
    assert stats["characters"] == 1
    assert [p.name for p in (out / "c2").iterdir()] == ["2$1.webp"]
    assert not (out / "c1").exists()

    out2 = root / "out2"
    stats = export_webp.convert_characters(export, out2, limit=2)
    assert stats["characters"] == 2
    assert sorted(p.name for p in out2.iterdir()) == ["c1", "c2"]

    out3 = root / "out3"
    stats = export_webp.convert_characters(export, out3, limit=99)
    assert stats["characters"] == 3


def test_convert_characters_respects_skip(workdir):
    root = Path(workdir)
    export = root / "export"
    _write_png(export / "c1" / "base.png")
    _write_png(export / "c1" / "d1.png")
    _write_png(export / "c1" / "d2.png")
    _write_png(export / "c2" / "other.png")
    out = root / "out"

    classified = {
        "characters": {
            "c1": {
                "bases": {
                    "base.png": {"diff": ["d1.png", "d2.png"]},
                }
            },
            "c2": {"bases": {"other.png": {"diff": []}}},
        }
    }
    skip = SkipList({"c2": "skip character", "c1/base.png": "skip base"})

    stats = export_webp.convert_characters(
        export,
        out,
        skip=skip,
        classified=classified,
    )

    assert stats["characters"] == 1
    assert stats["images"] == 0
    assert not (out / "c1" / "base.webp").exists()
    assert not (out / "c1" / "d1.webp").exists()
    assert not (out / "c1" / "d2.webp").exists()
    assert not (out / "c2").exists()


def test_convert_characters_skip_diff_only(workdir):
    root = Path(workdir)
    export = root / "export"
    _write_png(export / "c1" / "base.png")
    _write_png(export / "c1" / "d1.png")
    _write_png(export / "c1" / "d2.png")
    out = root / "out"

    classified = {
        "characters": {
            "c1": {
                "bases": {
                    "base.png": {"diff": ["d1.png", "d2.png"]},
                }
            }
        }
    }
    skip = SkipList({"c1/d1.png": "skip diff"})

    stats = export_webp.convert_characters(
        export,
        out,
        skip=skip,
        classified=classified,
    )

    assert stats["characters"] == 1
    assert stats["images"] == 2
    assert (out / "c1" / "base.webp").is_file()
    assert not (out / "c1" / "d1.webp").exists()
    assert (out / "c1" / "d2.webp").is_file()


def test_convert_characters_counts_bad_image_as_failed(workdir):
    root = Path(workdir)
    export = root / "export"
    (export / "c1").mkdir(parents=True)
    (export / "c1" / "bad.png").write_bytes(b"not an image")
    out = root / "out"
    stats = export_webp.convert_characters(export, out)
    assert stats["characters"] == 1
    assert stats["images"] == 1
    assert stats["failed"] == 1
    assert stats["converted"] == 0


def test_main_end_to_end(workdir):
    root = Path(workdir)
    export = root / "export"
    _write_png(export / "c1" / "1$1.png")
    _write_png(export / "c1" / "2$1.png")
    _write_png(export / "c2" / "3$1.png")
    out = root / "out"

    assert export_webp.main(["--export-dir", str(export), "-o", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["c1", "c2"]
    with Image.open(out / "c1" / "2$1.webp") as image:
        assert image.format == "WEBP"

    # incremental: nothing re-converted
    assert export_webp.main(["--export-dir", str(export), "-o", str(out)]) == 0

    # --character / --limit / --force
    out_char = root / "out_char"
    assert (
        export_webp.main(
            ["--export-dir", str(export), "-o", str(out_char), "--character", "c2"]
        )
        == 0
    )
    assert sorted(p.name for p in out_char.iterdir()) == ["c2"]

    out_limit = root / "out_limit"
    assert (
        export_webp.main(
            ["--export-dir", str(export), "-o", str(out_limit), "--limit", "1"]
        )
        == 0
    )
    assert sorted(p.name for p in out_limit.iterdir()) == ["c1"]

    out_force = root / "out_force"
    assert (
        export_webp.main(["--export-dir", str(export), "-o", str(out_force), "--force"])
        == 0
    )


def test_main_errors(workdir):
    root = Path(workdir)
    export = root / "export"
    _write_png(export / "c1" / "1$1.png")

    assert export_webp.main(["--export-dir", str(root / "missing")]) == 1
    assert export_webp.main(["--export-dir", str(export), "--character", "ghost"]) == 1
    assert export_webp.main(["--export-dir", str(export), "--quality", "101"]) == 1
    assert export_webp.main(["--export-dir", str(export), "--quality", "-1"]) == 1
    assert export_webp.main(["--export-dir", str(export), "--method", "7"]) == 1
    assert export_webp.main(["--export-dir", str(export), "--limit", "-1"]) == 1
