# Repository Guidelines

This repository implements the Arknights NPC avatar extraction pipeline. Keep generated data out of version control and use `uv` for all development tasks.

## Project Structure & Module Organization

- `src/arknightsavatar/` — Python package and CLI entry points. Keep orchestration tools flat and put resource adapters in `sources/`, bundle decoding in `unpack/`.
- `tests/` — pytest suite; name files `test_*.py` and mirror the source module being tested.
- `docs/` — user-facing and analysis documentation; `.dev_doc/` — implementation notes.
- `config.example.toml`, `data_repo.yaml` — committed templates. Local `config.toml`, `data/`, `apk/`, `.venv/`, and generated caches are gitignored.

## Build, Test, and Development Commands

```bash
uv sync                                      # install default dev + CPU detect groups
uv sync --no-group detect --extra detect-gpu  # GPU stack; mutually exclusive with detect
uv run pytest                                # run the test suite
uv build                                     # build the wheel via hatchling
uv run arknightsavatar --help                # list subcommands
uv run arknightsavatar run                   # full local/device pipeline
uv run arknightsavatar produce               # offline production steps only
```

Run `uv run arknightsavatar <subcommand> --help` for command-specific options.

## Coding Style & Naming Conventions

Use Python 3.12, PEP 8, 4-space indentation, type hints, and `from __future__ import annotations`. Modules and functions use `snake_case`; CLI subcommands use hyphenated names such as `detect-bases` and `sync-cache`. Keep each tool's public contract as `main(argv: list[str] | None = None) -> int`.

## Testing Guidelines

Use pytest. Add unit tests for new tools and regression cases for pipeline edge conditions. Prefer focused tests named after the behavior, and run `uv run pytest` before submitting changes.

## Commit & Pull Request Guidelines

Follow Conventional Commits from the git history: `feat(scope): description`, `fix(scope): description`, plus `refactor`, `perf`, `test`, `docs`, `style`, and `chore`. Keep the subject concise and explain non-obvious pipeline behavior in the PR description. Link related issues and include command/output evidence when changing extraction, matching, or sync behavior.

## Configuration & Data Notes

Do not commit `config.toml` or generated files under `data/`. If a change adds configuration, update `config.example.toml` or `data_repo.yaml` and document it in `README.md`.
