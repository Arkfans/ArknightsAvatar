from PIL import Image

from arknightsavatar.unpack.ab import extract_face_groups, merge_rgba


def test_merge_rgba():
    rgb = Image.new("RGB", (4, 4), (255, 0, 0))
    alpha = Image.new("L", (4, 4), 128)
    merged = merge_rgba(rgb, alpha)
    assert merged.mode == "RGBA"
    assert merged.size == (4, 4)
    assert merged.getpixel((0, 0)) == (255, 0, 0, 128)


def test_merge_rgba_resizes_alpha():
    rgb = Image.new("RGB", (4, 4), (0, 255, 0))
    alpha = Image.new("L", (2, 2), 64)
    merged = merge_rgba(rgb, alpha)
    assert merged.size == (4, 4)
    assert merged.getpixel((3, 3)) == (0, 255, 0, 64)


def test_extract_face_groups_from_typetree():
    tree = {
        "spriteGroups": [
            {
                "sprites": [],
                "facePos": {"x": 489.0, "y": 84.0, "z": 0.0},
                "faceSize": {"x": 85.0, "y": 60.0},
            },
            {
                "sprites": [],
                "facePos": {"x": 492.0, "y": 81.0, "z": 0.0},
                "faceSize": {"x": 87.0, "y": 60.0},
            },
        ]
    }
    groups = extract_face_groups(tree)
    assert groups == [
        {"facePos": {"x": 489, "y": 84}, "faceSize": {"x": 85, "y": 60}},
        {"facePos": {"x": 492, "y": 81}, "faceSize": {"x": 87, "y": 60}},
    ]


def test_extract_face_groups_ignores_unpaired():
    tree = {"a": {"facePos": {"x": 1, "y": 2}}, "b": {"faceSize": {"x": 3, "y": 4}}}
    assert extract_face_groups(tree) == [
        {"facePos": {"x": 1, "y": 2}, "faceSize": {"x": 3, "y": 4}}
    ]
