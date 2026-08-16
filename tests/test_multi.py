"""Tests for the MultiSource composite (sources/multi.py)."""

from pathlib import Path

import pytest

from arknightsavatar.sources.base import FileInfo, Source
from arknightsavatar.sources.multi import MultiSource


class FakeSource(Source):
    """Minimal source: per-category name->size maps; records calls."""

    def __init__(self, name: str, files: dict[str, dict[str, int]]):
        self.name = name
        self._files = files
        self.list_calls: list[str] = []
        self.fetch_calls: list[str] = []
        self.fetch_many_calls: list[list[FileInfo]] = []

    def list_files(self, category: str) -> list[FileInfo]:
        self.list_calls.append(category)
        return [
            FileInfo(rel=f"{category}/{name}", size=size)
            for name, size in self._files.get(category, {}).items()
        ]

    def fetch_to(self, rel: str, dest: Path) -> None:
        self.fetch_calls.append(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f"{self.name}:{rel}".encode())

    def fetch_many(self, items):
        self.fetch_many_calls.append([info for info, _ in items])
        return []

    def sha256(self, rel: str) -> str | None:
        return f"sha-{self.name}-{rel}"


def test_multi_source_merges_and_first_source_wins():
    adb = FakeSource("adb", {"characters": {"a.ab": 10, "b.ab": 20}, "avatars": {"dup.ab": 30}})
    apk = FakeSource("apk", {"characters": {"c.ab": 40}, "avatars": {"dup.ab": 999}})
    multi = MultiSource([adb, apk])

    assert multi.name == "adb+apk"
    assert [(info.rel, info.size) for info in multi.list_files("characters")] == [
        ("characters/a.ab", 10),
        ("characters/b.ab", 20),
        ("characters/c.ab", 40),
    ]
    # duplicate rel: the earlier source's size wins
    assert [(info.rel, info.size) for info in multi.list_files("avatars")] == [
        ("avatars/dup.ab", 30)
    ]
    assert multi.source_name("avatars/dup.ab") == "adb"
    assert multi.source_name("characters/c.ab") == "apk"
    # listings are cached
    assert multi.list_files("characters") is multi.list_files("characters")
    assert adb.list_calls == ["characters", "avatars"]


def test_multi_source_fetch_to_routes_to_owner(tmp_path: Path):
    adb = FakeSource("adb", {"characters": {"a.ab": 10}})
    apk = FakeSource("apk", {"characters": {"c.ab": 40}})
    multi = MultiSource([adb, apk])
    multi.list_files("characters")

    dest_a = tmp_path / "a.ab"
    multi.fetch_to("characters/a.ab", dest_a)
    assert dest_a.read_bytes() == b"adb:characters/a.ab"
    dest_c = tmp_path / "c.ab"
    multi.fetch_to("characters/c.ab", dest_c)
    assert dest_c.read_bytes() == b"apk:characters/c.ab"
    assert adb.fetch_calls == ["characters/a.ab"]
    assert apk.fetch_calls == ["characters/c.ab"]


def test_multi_source_fetch_many_groups_by_owner(tmp_path: Path):
    adb = FakeSource("adb", {"characters": {"a.ab": 10}})
    apk = FakeSource("apk", {"characters": {"c.ab": 40}})
    multi = MultiSource([adb, apk])
    multi.list_files("characters")

    items = [
        (FileInfo(rel="characters/a.ab", size=10), tmp_path / "a.part"),
        (FileInfo(rel="characters/c.ab", size=40), tmp_path / "c.part"),
    ]
    assert multi.fetch_many(items) == []
    assert [info.rel for info in adb.fetch_many_calls[0]] == ["characters/a.ab"]
    assert [info.rel for info in apk.fetch_many_calls[0]] == ["characters/c.ab"]


def test_multi_source_fetch_many_merges_failures(tmp_path: Path):
    class FailingSource(FakeSource):
        def fetch_many(self, items):
            return [(info, RuntimeError("boom")) for info, _ in items]

    adb = FailingSource("adb", {"characters": {"a.ab": 10}})
    apk = FakeSource("apk", {"characters": {"c.ab": 40}})
    multi = MultiSource([adb, apk])
    multi.list_files("characters")

    items = [
        (FileInfo(rel="characters/a.ab", size=10), tmp_path / "a.part"),
        (FileInfo(rel="characters/c.ab", size=40), tmp_path / "c.part"),
    ]
    failures = multi.fetch_many(items)
    assert [info.rel for info, _ in failures] == ["characters/a.ab"]
    assert isinstance(failures[0][1], RuntimeError)


def test_multi_source_sha256_delegates_to_owner():
    adb = FakeSource("adb", {"characters": {"a.ab": 10}})
    apk = FakeSource("apk", {"characters": {"c.ab": 40}})
    multi = MultiSource([adb, apk])
    # no list_files call first: sha256 triggers the lazy listing
    assert multi.sha256("characters/a.ab") == "sha-adb-characters/a.ab"
    assert multi.sha256("characters/c.ab") == "sha-apk-characters/c.ab"
    assert adb.list_calls == ["characters"]
    assert apk.list_calls == ["characters"]


def test_multi_source_lazy_listing_before_fetch(tmp_path: Path):
    adb = FakeSource("adb", {"characters": {"a.ab": 10}})
    multi = MultiSource([adb])

    dest = tmp_path / "a.part"
    multi.fetch_to("characters/a.ab", dest)  # fetch before any list_files
    assert dest.read_bytes() == b"adb:characters/a.ab"
    assert "characters" in adb.list_calls


def test_multi_source_rejects_empty():
    with pytest.raises(ValueError, match="at least one source"):
        MultiSource([])
