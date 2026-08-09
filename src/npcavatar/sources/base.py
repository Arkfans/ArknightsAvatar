from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileInfo:
    rel: str
    size: int


class Source(ABC):
    """A source of AB files. Files are addressed by rel path like
    'characters/avg_007_closre_1.ab'."""

    name: str = "base"

    @abstractmethod
    def list_files(self, category: str) -> list[FileInfo]:
        """List all AB files of a category in this source."""

    @abstractmethod
    def fetch_to(self, rel: str, dest: Path) -> None:
        """Copy/pull the file to dest (dest is a complete file path)."""

    def sha256(self, rel: str) -> str | None:
        """Return the source-side sha256 if cheaply computable, else None."""
        return None

    def supports(self, category: str) -> bool:
        return True
