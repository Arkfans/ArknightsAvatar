"""Composite resource source: merges several sources into one.

The merged listing is the union of all sources' listings; when two sources
list the same rel, the earliest source in ``sources`` wins — it provides the
size and owns the fetch (its version is authoritative). This is what makes
``adb`` + ``apk`` complete: the APK only ships spritepack (avatars), while
characters (L2D) live in the device's downloaded Bundles directory, so the
device version wins on duplicates and the APK fills the gaps.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .base import FileInfo, Source


class MultiSource(Source):
    """Merge several sources; earlier sources win on duplicate rels."""

    def __init__(self, sources: Sequence[Source]):
        if not sources:
            raise ValueError("MultiSource requires at least one source")
        self.sources = list(sources)
        self.name = "+".join(source.name for source in self.sources)
        self._ls_cache: dict[str, list[FileInfo]] = {}
        self._owner: dict[str, Source] = {}

    def _ensure_listed(self, rel: str) -> None:
        """Populate the owner map for ``rel``'s category (lazy listing)."""
        if rel not in self._owner:
            self.list_files(rel.split("/", 1)[0])

    def list_files(self, category: str) -> list[FileInfo]:
        if category in self._ls_cache:
            return self._ls_cache[category]
        merged: dict[str, FileInfo] = {}
        for source in self.sources:
            for info in source.list_files(category):
                if info.rel not in merged:
                    merged[info.rel] = info
                    self._owner[info.rel] = source
        files = sorted(merged.values(), key=lambda item: item.rel)
        self._ls_cache[category] = files
        return files

    def fetch_to(self, rel: str, dest: Path) -> None:
        self._ensure_listed(rel)
        self._owner[rel].fetch_to(rel, dest)

    def fetch_many(
        self, items: Sequence[tuple[FileInfo, Path]]
    ) -> list[tuple[FileInfo, Exception]]:
        """Group items by their owning source and delegate to each source.

        Each source keeps its own batch path (device tar packing for adb,
        device-side unzip packing for apk); failures are merged.
        """
        failures: list[tuple[FileInfo, Exception]] = []
        by_owner: dict[Source, list[tuple[FileInfo, Path]]] = {}
        for info, dest in items:
            self._ensure_listed(info.rel)
            by_owner.setdefault(self._owner[info.rel], []).append((info, dest))
        for source, group in by_owner.items():
            failures.extend(source.fetch_many(group))
        return failures

    def sha256(self, rel: str) -> str | None:
        self._ensure_listed(rel)
        return self._owner[rel].sha256(rel)

    def source_name(self, rel: str) -> str:
        """Name of the source that owns ``rel`` (manifest provenance)."""
        self._ensure_listed(rel)
        return self._owner[rel].name
