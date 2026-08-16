from __future__ import annotations

import shutil
from pathlib import Path
from typing import ClassVar

from ..util import sha256_file
from .base import FileInfo, Source


class ApkSource(Source):
    """Reads AB files from an extracted APK directory (assets/AB/Android)."""

    name = "apk"

    CATEGORY_SUBPATHS: ClassVar[dict[str, tuple[str, ...]]] = {
        "characters": ("avg", "characters"),
        "avatars": ("spritepack",),
    }

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.ab_root = self.root / "assets" / "AB" / "Android"

    def dir_for(self, category: str) -> Path:
        return self.ab_root.joinpath(*self.CATEGORY_SUBPATHS[category])

    def path_for(self, rel: str) -> Path:
        category, name = rel.split("/", 1)
        return self.dir_for(category) / name

    def list_files(self, category: str) -> list[FileInfo]:
        directory = self.dir_for(category)
        if not directory.exists():
            return []
        return [
            FileInfo(rel=f"{category}/{path.name}", size=path.stat().st_size)
            for path in sorted(directory.glob("*.ab"))
            if path.is_file()
        ]

    def fetch_to(self, rel: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.path_for(rel), dest)

    def sha256(self, rel: str) -> str:
        return sha256_file(self.path_for(rel))
