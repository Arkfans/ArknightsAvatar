import json
from pathlib import Path

from PIL import Image

from arknightsavatar.unpack.unpacker import run_unpack, unpack_one
from arknightsavatar.util import sha256_file


class FakeParse:
    def __init__(self, path: Path):
        self._path = path
        self.sprites = {"2$1": None}
        self.face_groups = [{"facePos": {"x": 1, "y": 2}, "faceSize": {"x": 3, "y": 4}}]

    def merged_images(self) -> dict[str, Image.Image]:
        return {"2$1": Image.new("RGBA", (10, 10), (255, 0, 0, 255))}


class FakeAvatarParse(FakeParse):
    def __init__(self, path: Path):
        super().__init__(path)
        self.sprites = {
            "char_002_amiya": None,
            "char_4237_jcinta_2": None,
            "trap_451_xbflare": None,
        }

    def merged_images(self) -> dict[str, Image.Image]:
        return {
            "char_002_amiya": Image.new("RGBA", (8, 8), (0, 0, 255, 255)),
            "char_4237_jcinta_2": Image.new("RGBA", (8, 16), (255, 255, 0, 255)),
            "trap_451_xbflare": Image.new("RGBA", (8, 8), (255, 0, 0, 255)),
        }


class SelectiveParse:
    """Succeeds for 'good' files, raises for 'bad' files."""

    def __init__(self, path: Path):
        if "bad" in path.name:
            raise RuntimeError("boom")
        self.sprites = {"2$1": None}
        self.face_groups = [{"facePos": {"x": 1, "y": 2}, "faceSize": {"x": 3, "y": 4}}]

    def merged_images(self) -> dict[str, Image.Image]:
        return {"2$1": Image.new("RGBA", (4, 4), (0, 0, 0, 0))}


class DottedParse:
    """Asset names containing or ending with double dots."""

    def __init__(self, path: Path):
        self.sprites = {"char_002_amiya..test": None, "char_003_kalts.": None}
        self.face_groups = []

    def merged_images(self) -> dict[str, Image.Image]:
        return {
            "char_002_amiya..test": Image.new("RGBA", (8, 8), (0, 255, 0, 255)),
            "char_003_kalts.": Image.new("RGBA", (8, 8), (0, 0, 255, 255)),
            "full..body": Image.new("RGBA", (8, 8), (255, 255, 0, 255)),
        }


class AlwaysFailParse:
    def __init__(self, path: Path):
        raise RuntimeError("boom")


def test_unpack_characters(tmp_path: Path):
    ab_path = tmp_path / "avg_007_closre_1.ab"
    ab_path.write_bytes(b"x")
    unpacked = tmp_path / "unpacked"

    stats = unpack_one(
        ab_path,
        unpacked,
        "characters",
        "characters/avg_007_closre_1.ab",
        "sha1",
        parser_cls=FakeParse,
    )
    assert stats == {"textures": 1, "sprites": 1, "face_groups": 1}

    item_dir = unpacked / "characters" / "avg_007_closre_1"
    assert (item_dir / "2$1.png").exists()
    meta = json.loads((item_dir / "meta.json").read_text(encoding="utf8"))
    assert meta["source"] == {"rel": "characters/avg_007_closre_1.ab", "sha256": "sha1"}
    assert meta["textures"] == {"2$1": [10, 10]}
    assert meta["sprites"] == ["2$1"]
    assert meta["face_groups"] == [
        {"facePos": {"x": 1, "y": 2}, "faceSize": {"x": 3, "y": 4}}
    ]


def test_unpack_avatars_flat(tmp_path: Path):
    ab_path = tmp_path / "ui_char_avatar_0.ab"
    ab_path.write_bytes(b"x")
    unpacked = tmp_path / "unpacked"

    unpack_one(
        ab_path,
        unpacked,
        "avatars",
        "avatars/ui_char_avatar_0.ab",
        "sha2",
        parser_cls=FakeAvatarParse,
    )

    assert (unpacked / "avatars" / "char_002_amiya.png").exists()
    # 半身像（非正方形，如 char_portrait 180x360）应被过滤
    assert not (unpacked / "avatars" / "char_4237_jcinta_2.png").exists()
    assert not (unpacked / "avatars" / "trap_451_xbflare.png").exists()
    meta = json.loads(
        (unpacked / "avatars" / "_meta" / "ui_char_avatar_0.json").read_text(
            encoding="utf8"
        )
    )
    assert meta["source"]["sha256"] == "sha2"


def test_run_unpack_avatars_prunes_non_char(tmp_path: Path):
    raw = tmp_path / "raw"
    raw_avatars = raw / "avatars"
    raw_avatars.mkdir(parents=True)
    (raw_avatars / "ui_char_avatar_0.ab").write_bytes(b"x")
    unpacked = tmp_path / "unpacked"
    out_avatars = unpacked / "avatars"
    out_avatars.mkdir(parents=True)
    (out_avatars / "stale_icon.png").write_bytes(b"stale")
    (out_avatars / "char_002_amiya.png").write_bytes(b"existing")
    Image.new("RGBA", (8, 16), (255, 0, 0, 255)).save(
        out_avatars / "char_stale_portrait.png"
    )

    stats = run_unpack(raw, unpacked, ["avatars"], parser_cls=FakeAvatarParse)
    assert stats["avatars"]["unpacked"] == 1
    assert (out_avatars / "char_002_amiya.png").exists()
    assert not (out_avatars / "stale_icon.png").exists()
    # 历史遗留的半身像 PNG 应被清理
    assert not (out_avatars / "char_stale_portrait.png").exists()
    assert (out_avatars / "_meta" / "ui_char_avatar_0.json").exists()
    assert (unpacked / "_manifest.json").exists()

    # 增量全部跳过时，遗留的非 char_* PNG 仍会被清理
    (out_avatars / "stale_icon2.png").write_bytes(b"stale2")
    stats2 = run_unpack(raw, unpacked, ["avatars"], parser_cls=FakeAvatarParse)
    assert stats2["avatars"]["skipped"] == 1
    assert not (out_avatars / "stale_icon2.png").exists()


def test_run_unpack_incremental_and_failures(tmp_path: Path):
    raw = tmp_path / "raw"
    characters = raw / "characters"
    characters.mkdir(parents=True)
    good = characters / "avg_good_1.ab"
    good.write_bytes(b"good")
    bad = characters / "avg_bad_1.ab"
    bad.write_bytes(b"bad")
    unpacked = tmp_path / "unpacked"

    stats = run_unpack(raw, unpacked, ["characters"], parser_cls=SelectiveParse)
    assert stats["characters"]["listed"] == 2
    assert stats["characters"]["unpacked"] == 1
    assert stats["characters"]["failed"] == 1

    progress = json.loads((unpacked / "_manifest.json").read_text(encoding="utf8"))
    assert progress == {"characters/avg_good_1.ab": sha256_file(good)}
    failures = json.loads((unpacked / "_failed.json").read_text(encoding="utf8"))
    assert set(failures) == {"characters/avg_bad_1.ab"}

    # 第二次运行失败清单应保持不变（幂等）
    stats2 = run_unpack(raw, unpacked, ["characters"], parser_cls=SelectiveParse)
    assert stats2["characters"]["unpacked"] == 0
    assert stats2["characters"]["skipped"] == 1
    assert stats2["characters"]["failed"] == 1


def test_run_unpack_creates_output_dir_when_all_fail(tmp_path: Path):
    raw = tmp_path / "raw"
    characters = raw / "characters"
    characters.mkdir(parents=True)
    (characters / "avg_bad_1.ab").write_bytes(b"bad")
    unpacked = tmp_path / "unpacked"

    stats = run_unpack(raw, unpacked, ["characters"], parser_cls=AlwaysFailParse)
    assert stats["characters"]["failed"] == 1
    assert (unpacked / "_manifest.json").exists()
    assert (unpacked / "_failed.json").exists()


def test_unpack_sanitizes_double_dots_in_filenames(tmp_path: Path):
    ab_path = tmp_path / "ui_char_avatar_1.ab"
    ab_path.write_bytes(b"x")
    unpacked = tmp_path / "unpacked"

    unpack_one(
        ab_path,
        unpacked,
        "avatars",
        "avatars/ui_char_avatar_1.ab",
        "sha3",
        parser_cls=DottedParse,
    )
    avatars_dir = unpacked / "avatars"
    # sprite 名中的连续句点折叠为单个句点
    assert (avatars_dir / "char_002_amiya.test.png").exists()
    # sprite 名以句点结尾时，拼接 .png 产生的 .. 同样被折叠
    assert (avatars_dir / "char_003_kalts.png").exists()
    # 原始（未清洗）文件名不应存在
    assert not (avatars_dir / "char_002_amiya..test.png").exists()
    assert not (avatars_dir / "char_003_kalts..png").exists()

    ab_path2 = tmp_path / "avg_dotted_1.ab"
    ab_path2.write_bytes(b"y")
    unpack_one(
        ab_path2,
        unpacked,
        "characters",
        "characters/avg_dotted_1.ab",
        "sha4",
        parser_cls=DottedParse,
    )
    item_dir = unpacked / "characters" / "avg_dotted_1"
    # 纹理名中的连续句点同样被折叠
    assert (item_dir / "full.body.png").exists()
    assert not (item_dir / "full..body.png").exists()
    # 元数据里仍保留原始资产命名作为标识
    meta = json.loads((item_dir / "meta.json").read_text(encoding="utf8"))
    assert "full..body" in meta["textures"]


def test_run_unpack_progress_callback(tmp_path: Path):
    raw = tmp_path / "raw"
    characters = raw / "characters"
    characters.mkdir(parents=True)
    (characters / "avg_good_1.ab").write_bytes(b"good")
    (characters / "avg_bad_1.ab").write_bytes(b"bad")
    (characters / "avg_good_2.ab").write_bytes(b"good2")
    unpacked = tmp_path / "unpacked"

    calls: list[tuple[int, int, str]] = []
    run_unpack(
        raw,
        unpacked,
        ["characters"],
        parser_cls=SelectiveParse,
        progress=lambda index, total, label: calls.append((index, total, label)),
    )
    # 成功与失败文件都推进进度，index 从 1 起、total 为文件总数
    assert calls == [
        (1, 3, "characters/avg_bad_1.ab"),
        (2, 3, "characters/avg_good_1.ab"),
        (3, 3, "characters/avg_good_2.ab"),
    ]

    # 第二趟：成功文件全部跳过、失败文件重试失败，进度仍照常推进
    calls2: list[tuple[int, int, str]] = []
    stats2 = run_unpack(
        raw,
        unpacked,
        ["characters"],
        parser_cls=SelectiveParse,
        progress=lambda index, total, label: calls2.append((index, total, label)),
    )
    assert stats2["characters"]["skipped"] == 2
    assert stats2["characters"]["failed"] == 1
    assert calls2 == [
        (1, 3, "characters/avg_bad_1.ab"),
        (2, 3, "characters/avg_good_1.ab"),
        (3, 3, "characters/avg_good_2.ab"),
    ]


def test_run_unpack_progress_total_across_categories(tmp_path: Path):
    raw = tmp_path / "raw"
    for category in ("characters", "avatars"):
        category_dir = raw / category
        category_dir.mkdir(parents=True)
        (category_dir / f"{category}_1.ab").write_bytes(b"x")
        (category_dir / f"{category}_2.ab").write_bytes(b"x")
    unpacked = tmp_path / "unpacked"

    calls: list[tuple[int, int, str]] = []
    run_unpack(
        raw,
        unpacked,
        ["characters", "avatars"],
        parser_cls=FakeAvatarParse,
        progress=lambda index, total, label: calls.append((index, total, label)),
    )
    # total 为各分类目录文件数之和，index 跨分类连续递增（按 categories 顺序）
    assert calls == [
        (1, 4, "characters/characters_1.ab"),
        (2, 4, "characters/characters_2.ab"),
        (3, 4, "avatars/avatars_1.ab"),
        (4, 4, "avatars/avatars_2.ab"),
    ]
