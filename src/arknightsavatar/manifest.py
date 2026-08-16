from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class FileRecord:
    size: int
    sha256: str
    source: str


class Manifest:
    """JSON manifest tracking files in data/raw, keyed by rel path."""

    def __init__(self, path: Path, game_version: str = "unknown"):
        self.path = path
        self.game_version = game_version
        self.updated_at = ""
        self.files: dict[str, FileRecord] = {}

    @classmethod
    def load(cls, path: Path, game_version: str = "unknown") -> Manifest:
        manifest = cls(path, game_version)
        if path.exists():
            with path.open("rt", encoding="utf8") as f:
                data = json.load(f)
            manifest.game_version = data.get("game_version", game_version)
            manifest.updated_at = data.get("updated_at", "")
            for rel, record in (data.get("files") or {}).items():
                manifest.files[rel] = FileRecord(**record)
        return manifest

    def get(self, rel: str) -> FileRecord | None:
        return self.files.get(rel)

    def set(self, rel: str, record: FileRecord) -> None:
        self.files[rel] = record

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "game_version": self.game_version,
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "files": {
                rel: asdict(record) for rel, record in sorted(self.files.items())
            },
        }
        with self.path.open("wt", encoding="utf8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


class FailureLog:
    """Persistent dict of failures: rel -> info dict."""

    def __init__(self, path: Path):
        self.path = path
        self.failures: dict[str, dict] = {}

    @classmethod
    def load(cls, path: Path) -> FailureLog:
        log = cls(path)
        if path.exists():
            with path.open("rt", encoding="utf8") as f:
                log.failures = json.load(f)
        return log

    def add(self, rel: str, **info) -> None:
        self.failures[rel] = info

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("wt", encoding="utf8") as f:
            json.dump(self.failures, f, ensure_ascii=False, indent=2)
