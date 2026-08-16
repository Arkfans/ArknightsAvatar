import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from arknightsavatar.skip import SkipList

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_load_missing_and_invalid():
    workdir = PROJECT_ROOT / f"arknightsavatar_skip_test_{uuid4().hex[:8]}"
    os.makedirs(workdir, mode=0o777)
    try:
        assert (
            SkipList.load(workdir / "missing.json").is_character_skipped("anything")
            is False
        )

        bad = workdir / "bad.json"
        bad.write_text("{not json", encoding="utf8")
        assert SkipList.load(bad).is_character_skipped("anything") is False

        non_object = workdir / "array.json"
        non_object.write_text(json.dumps(["x"]), encoding="utf8")
        assert SkipList.load(non_object).is_character_skipped("anything") is False
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def test_character_and_sprite_matching():
    skip = SkipList(
        {
            "avg_001_a_1": "bad character",
            "avg_002_b_1/base.png": "bad base",
            "avg_003_c_1/base": "stem key",
        }
    )

    assert skip.is_character_skipped("avg_001_a_1")
    assert skip.is_sprite_skipped("avg_001_a_1", "anything.png")
    assert skip.is_sprite_skipped("avg_002_b_1", "base.png")
    assert skip.is_sprite_skipped("avg_002_b_1", "base")
    assert skip.is_sprite_skipped("AVG_002_B_1", "BASE.PNG")
    assert not skip.is_sprite_skipped("avg_002_b_1", "other.png")

    assert skip.reason("avg_001_a_1") == "bad character"
    assert skip.reason("avg_002_b_1", "base.png") == "bad base"


def test_filter_classified_character_base_and_diff():
    skip = SkipList(
        {
            "skip_all": "all",
            "avg_001_a_1/base1.png": "base",
            "avg_001_a_1/d1.png": "diff",
        }
    )
    classified = {
        "generated_at": "x",
        "characters": {
            "skip_all": {"bases": {"a.png": {"diff": ["b.png"]}}},
            "avg_001_a_1": {
                "bases": {
                    "base1.png": {"diff": ["d1.png", "d2.png"]},
                    "base2.png": {"diff": ["d3.png"]},
                }
            },
            "empty_ok": {"bases": {}},
        },
    }

    result = skip.filter_classified(classified)
    assert "skip_all" not in result["characters"]
    entry = result["characters"]["avg_001_a_1"]
    assert list(entry["bases"]) == ["base2.png"]
    assert entry["bases"]["base2.png"]["diff"] == ["d3.png"]
    assert "empty_ok" in result["characters"]


def test_filter_classified_all_bases_skipped_removes_character():
    skip = SkipList({"avg_001_a_1/base.png": "base"})
    classified = {
        "characters": {
            "avg_001_a_1": {"bases": {"base.png": {"diff": ["d.png"]}}},
            "avg_002_b_1": {"bases": {"ok.png": {"diff": []}}},
        }
    }

    result = skip.filter_classified(classified)
    assert "avg_001_a_1" not in result["characters"]
    assert "avg_002_b_1" in result["characters"]


def test_expand_base_to_diffs_and_fallback_to_exact_stem():
    skip = SkipList(
        {
            "avg_001_a_1": "whole character",
            "avg_002_b_1/base1.png": "base",
            "avg_002_b_1/standalone.png": "standalone",
            "avg_missing_c_1/only.png": "unknown",
        }
    )
    classified = {
        "characters": {
            "avg_002_b_1": {
                "bases": {
                    "base1.png": {"diff": ["d1.png", "d2.png"]},
                    "base2.png": {"diff": ["d3.png"]},
                }
            }
        }
    }

    skipped_characters, skipped_stems = skip.expand(classified)
    assert "avg_001_a_1" in skipped_characters
    assert skipped_stems["avg_002_b_1"] == {"base1", "d1", "d2", "standalone"}
    assert skipped_stems["avg_missing_c_1"] == {"only"}
