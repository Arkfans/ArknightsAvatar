from __future__ import annotations

import shutil
from pathlib import Path

from ..util import sha256_file
from .base import FileInfo, Source


class LocalSource(Source):
    """Reads AB files from a directory of directories (category -> dir)."""

    name = "local"

    def __init__(self, category_dirs: dict[str, str | Path]):
        self.category_dirs: dict[str, Path] = {k: Path(v) for k, v in category_dirs.items()}

    def dir_for(self, category: str) -> Path:
        directory = self.category_dirs.get(category)
        if not directory:
            raise ValueError(f"local source has no directory configured for category '{category}'")
        return directory

    def supports(self, category: str) -> bool:
        return category in self.category_dirs

    def path_for(self, rel: str) -> Path:
        category, name = rel.split("/", 1)
        return self.dir_for(category) / name

    def list_files(self, category: str) -> list[FileInfo]:
        if category not in self.category_dirs:
            return []
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
