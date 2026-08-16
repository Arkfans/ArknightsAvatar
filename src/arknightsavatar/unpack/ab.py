from __future__ import annotations

from os import PathLike
from typing import Any

from PIL import Image

try:
    import UnityPy
    from UnityPy import classes as UnityClasses

    from .ak import install_unitypy_ak_patch

    # Arknights bundles use a custom LZ4 variant under the "LZHAM" flag.
    install_unitypy_ak_patch()
except ImportError:  # pragma: no cover - optional dependency
    UnityPy = None  # type: ignore[assignment]
    UnityClasses = None  # type: ignore[assignment]


def merge_rgba(image_rgb: Image.Image, image_alpha: Image.Image) -> Image.Image:
    """Merge an RGB image with a separate alpha-channel image into RGBA."""
    rgba = image_rgb.convert("RGBA")
    alpha = image_alpha.convert("L").resize(rgba.size)
    rgba.putalpha(alpha)
    return rgba


def extract_face_groups(tree: dict[str, Any]) -> list[dict[str, dict[str, int]]]:
    """Recursively extract facePos/faceSize pairs from a MonoBehaviour typetree.

    The game stores face data as a list of groups; each group has one facePos
    and one faceSize (Vector2/Vector3 x/y values).
    """
    groups: list[dict[str, dict[str, int]]] = []
    current: dict[str, dict[str, int]] = {}

    def walk(key: str, value: Any) -> None:
        nonlocal current
        normalized = key[0].lower() + key[1:] if key else key
        if (
            normalized in ("facePos", "faceSize")
            and isinstance(value, dict)
            and "x" in value
        ):
            if len(current) == 2:
                groups.append(current)
                current = {}
            current[normalized] = {"x": int(value["x"]), "y": int(value["y"])}
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                walk(sub_key, sub_value)
        elif isinstance(value, list):
            for sub_value in value:
                walk(normalized, sub_value)

    for key, value in tree.items():
        walk(key, value)
    if len(current) == 2:
        groups.append(current)
    return groups


class AbParse:
    """Parse a Unity AssetBundle into textures, sprites and face metadata."""

    def __init__(self, path: str | PathLike):
        if UnityPy is None:
            raise ImportError("UnityPy is not installed; run: uv sync --extra unpack")
        # 读入内存再解析：UnityPy 会持有文件句柄，Windows 上会锁定文件
        # （导致临时目录清理/文件替换失败），bytes 输入无此问题。
        with open(path, "rb") as f:
            payload = f.read()
        self.ab = UnityPy.load(payload)
        self.textures: dict[str, UnityClasses.Texture2D] = {}
        self.sprites: dict[str, UnityClasses.Sprite] = {}
        self.face_groups: list[dict[str, dict[str, int]]] = []
        self.data: dict[str, dict[str, Any]] = {}

        for obj in self.ab.objects:
            object_type = obj.type.name
            if object_type == "Texture2D":
                texture = obj.read()
                self.textures[
                    getattr(texture, "m_Name", "") or f"unnamed_{len(self.textures)}"
                ] = texture
            elif object_type == "Sprite":
                sprite = obj.read()
                self.sprites[
                    getattr(sprite, "m_Name", "") or f"unnamed_{len(self.sprites)}"
                ] = sprite
            elif object_type == "MonoBehaviour":
                # read_typetree 位于 ObjectReader 上，不在 read() 返回的对象上
                tree = obj.read_typetree()
                self.face_groups.extend(extract_face_groups(tree))
                self.data[str(obj.path_id)] = tree

    def merged_images(self) -> dict[str, Image.Image]:
        """Full-resolution images with RGB + [alpha] textures merged to RGBA."""
        merged: dict[str, Image.Image] = {}
        for name, texture in self.textures.items():
            if name.endswith("[alpha]"):
                continue
            alpha_key = f"{name}[alpha]"
            if alpha_key in self.textures:
                merged[name] = merge_rgba(texture.image, self.textures[alpha_key].image)
            else:
                merged[name] = texture.image
        return merged
