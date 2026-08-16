from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from arknightsavatar import paths

from .config import CATEGORIES, Config, load_config
from .manifest import FailureLog, FileRecord, Manifest
from .sources import ApkSource, MultiSource, Source
from .sources.adb import AdbSource
from .sources.apk_adb import ApkAdbSource
from .sources.base import FileInfo
from .sources.device import package_from_location
from .util import sha256_file


def make_source(
    name: str,
    config: Config,
    *,
    batch: bool = True,
    compress: bool = False,
) -> Source:
    if name == "apk":
        return ApkAdbSource(
            host=config.adb.host,
            port=config.adb.port,
            package=package_from_location(config.adb.resolved_location()),
            batch=batch,
        )
    if name == "local-apk":
        if not config.apk.dir:
            raise SystemExit("apk.dir is not configured (config.toml or ARKNIGHTSAVATAR_APK_DIR)")
        return ApkSource(config.apk.dir)
    if name == "adb":
        return AdbSource(
            host=config.adb.host,
            port=config.adb.port,
            location=config.adb.resolved_location(),
            batch=batch,
            compress=compress,
        )
    raise SystemExit(f"unknown source: {name}")


def make_sources(
    names: list[str],
    config: Config,
    *,
    batch: bool = True,
    compress: bool = False,
) -> Source:
    """Build the source for one or more ``--source`` names.

    A single name returns that source directly; several names are merged
    into a MultiSource where earlier names win on duplicate files (so the
    default ``["adb", "apk"]`` prefers the live device Bundles and lets the
    APK fill the gaps — the union is the complete dataset).
    """
    if len(names) == 1:
        return make_source(names[0], config, batch=batch, compress=compress)
    return MultiSource(
        [make_source(name, config, batch=batch, compress=compress) for name in names]
    )


def run_fetch(
    source: Source,
    categories: list[str],
    raw_dir: Path,
    force: bool = False,
    game_version: str = "unknown",
) -> dict[str, dict[str, int]]:
    """Fetch listed files into raw_dir, updating manifest and failure log.

    Returns per-category stats: {"listed", "fetched", "skipped", "failed"}.
    """
    manifest = Manifest.load(raw_dir / "manifest.json", game_version=game_version)
    failures = FailureLog.load(raw_dir / "_failed.json")
    stats = {category: {"listed": 0, "fetched": 0, "skipped": 0, "failed": 0} for category in categories}
    dirty = 0

    for category in categories:
        infos = source.list_files(category)
        stats[category]["listed"] = len(infos)

        needed: list[tuple[FileInfo, Path]] = []
        for info in sorted(infos, key=lambda item: item.rel):
            dest = raw_dir / info.rel
            record = manifest.get(info.rel)
            if (
                not force
                and record is not None
                and record.size == info.size
                and dest.exists()
            ):
                remote_sha = source.sha256(info.rel)
                if remote_sha is None or remote_sha == record.sha256:
                    stats[category]["skipped"] += 1
                    continue
            needed.append((info, dest.with_name(dest.name + ".part")))

        try:
            batch_failures = source.fetch_many(needed)
        except Exception as error:  # noqa: BLE001 - whole batch crashed
            batch_failures = [(info, error) for info, _ in needed]
        failed_rels = {info.rel: error for info, error in batch_failures}

        for info, tmp in needed:
            dest = raw_dir / info.rel
            error = failed_rels.get(info.rel)
            if error is not None:
                tmp.unlink(missing_ok=True)
                failures.add(
                    info.rel,
                    source=source.source_name(info.rel),
                    size=info.size,
                    error=f"{type(error).__name__}: {error}",
                )
                stats[category]["failed"] += 1
            else:
                try:
                    size = tmp.stat().st_size
                    if size == 0:
                        raise ValueError("file is 0 bytes")
                    digest = sha256_file(tmp)
                    os.replace(tmp, dest)
                    manifest.set(
                        info.rel,
                        FileRecord(size=size, sha256=digest, source=source.source_name(info.rel)),
                    )
                    stats[category]["fetched"] += 1
                except Exception as error:  # noqa: BLE001 - record and continue
                    tmp.unlink(missing_ok=True)
                    failures.add(
                        info.rel,
                        source=source.source_name(info.rel),
                        size=info.size,
                        error=f"{type(error).__name__}: {error}",
                    )
                    stats[category]["failed"] += 1
            dirty += 1
            if dirty % 50 == 0:
                manifest.save()
                failures.save()

    manifest.save()
    failures.save()
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arknightsavatar-fetch", description="Fetch AB resources into data/raw.")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument(
        "--source",
        choices=["apk", "local-apk", "adb"],
        nargs="+",
        default=["adb", "apk"],
        help="resource source(s); default: adb + apk (live device Bundles wins, "
        "installed APK fills gaps — together they are the complete dataset)",
    )
    parser.add_argument("--category", choices=[*CATEGORIES, "all"], default="all")
    parser.add_argument("--raw-dir", default=paths.RAW_DIR, help="Output cache directory")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if manifest says unchanged")
    parser.add_argument(
        "--no-batch",
        action="store_true",
        help="disable device-side packing; pull file by file (adb/apk source)",
    )
    parser.add_argument(
        "--compress",
        action="store_true",
        help="gzip the device-side archive before pulling (adb source; ABs are "
        "already compressed, so savings are small)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        source = make_sources(
            args.source,
            config,
            batch=not args.no_batch,
            compress=args.compress,
        )
        categories = list(CATEGORIES) if args.category == "all" else [args.category]
        stats = run_fetch(source, categories, Path(args.raw_dir), force=args.force, game_version=config.game_version)
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    for category in categories:
        stat = stats[category]
        print(
            f"{category:11s} listed={stat['listed']:5d} fetched={stat['fetched']:5d} "
            f"skipped={stat['skipped']:5d} failed={stat['failed']:3d}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
