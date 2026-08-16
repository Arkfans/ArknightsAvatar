from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
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

    def fetch_many(
        self, items: Sequence[tuple[FileInfo, Path]]
    ) -> list[tuple[FileInfo, Exception]]:
        """Fetch several files in one call; dest paths are complete file paths.

        Sources may override this to batch the transfer (e.g. pack files on
        the device and pull once). The default implementation fetches one
        file at a time and collects per-file failures, so every source keeps
        working without a batch path.
        """
        failures: list[tuple[FileInfo, Exception]] = []
        for info, dest in items:
            try:
                self.fetch_to(info.rel, dest)
            except Exception as error:  # noqa: BLE001 - report and continue
                failures.append((info, error))
        return failures

    def sha256(self, rel: str) -> str | None:
        """Return the source-side sha256 if cheaply computable, else None."""
        return None

    def supports(self, category: str) -> bool:
        return True

    def source_name(self, rel: str) -> str:
        """Name of the source that provides ``rel`` (manifest provenance).

        Composites (e.g. MultiSource) override this to report the real
        delivering source per file instead of a combined name.
        """
        return self.name
