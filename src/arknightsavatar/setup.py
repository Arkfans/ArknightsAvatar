"""Interactive first-run initialization (``arknightsavatar setup``).

Two modes, both driven by the sync config (``data_repo.yaml`` -- the same file
``sync-cache`` uses):

a) **full sync** -- delegate to ``sync-cache --pull --restore``: clone / pull
   the data repo working copy, restore every category (including
   ``export`` / ``export_webp``) into ``data/``, then mirror local changes back
   and commit;

b) **download only** -- interactively multi-select the categories from
   ``data_repo.yaml`` (``↑/↓`` move, ``space`` toggle, ``enter`` confirm, ``esc``
   cancel, all selected by default; each item shows its ``desc`` from
   ``data_repo.yaml``) and restore exactly those from the working copy into
   ``data/``, without mirroring or committing anything.

Non-interactive equivalents: ``--full``, ``--download --category <remote>``.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Self

from arknightsavatar import sync_cache
from arknightsavatar.config import ConfigError, DataRepoCategory, load_config

# ANSI control sequences (only used when the terminal supports VT processing).
_CURSOR_HIDE = "\x1b[?25l"
_CURSOR_SHOW = "\x1b[?25h"
_CLEAR_LINE = "\x1b[2K"

MENU_TITLE = "ArknightsAvatar 初始化（数据仓库与分类以 data_repo.yaml 为准）:"
MENU_ITEMS = [
    "a) 全量同步（sync-cache --pull --restore，含 export）",
    "b) 仅下载数据文件（交互选择分类）",
]
MENU_HINT = "↑/↓ 移动   回车 确认（或直接按 a / b）   Esc 取消"


class SetupError(Exception):
    """A setup failure with a user-facing message."""


def _enable_ansi_windows() -> bool:
    """Enable VT processing on a Windows console; False when not possible."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001 - degradation is intentional
        return False


# Windows 需要显式开启 VT 才支持 ANSI 转义；POSIX 终端默认支持。
_ANSI_SUPPORTED = _enable_ansi_windows() if os.name == "nt" else True


def _normalize(ch: str) -> str:
    """Map one raw character to a picker token (letters pass through)."""
    if ch in ("\r", "\n"):
        return "enter"
    if ch == " ":
        return "space"
    if ch == "\x1b":
        return "esc"
    if len(ch) == 1 and ch.isalpha():
        return ch
    return "unknown"


class KeyReader:
    """Raw console key reader: normalize platform key events into tokens.

    Tokens: ``up`` / ``down`` / ``space`` / ``enter`` / ``esc`` plus single
    letters (menu shortcuts). Windows uses ``msvcrt``; POSIX switches stdin to
    raw mode on enter and restores it on exit. Windows consoles additionally
    get VT processing enabled.
    """

    def __init__(self, stream) -> None:
        self._stream = stream
        self._fd: int | None = None
        self._old_attr = None

    def __enter__(self) -> Self:
        if os.name == "nt":
            _enable_ansi_windows()
            return self
        try:
            import termios  # POSIX only
            import tty

            self._fd = self._stream.fileno()
            self._old_attr = termios.tcgetattr(self._fd)
            tty.setraw(self._fd)
        except Exception:  # noqa: BLE001 - non-terminal streams degrade to normal reads
            self._fd = None
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._fd is not None:
            try:
                import termios  # POSIX only

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_attr)
            except Exception:  # noqa: BLE001, S110 - best-effort restore
                pass

    def read(self) -> str:
        if os.name == "nt":
            return self._read_windows()
        return self._read_posix()

    def _read_windows(self) -> str:
        import msvcrt

        first = msvcrt.getwch()
        if first in ("\x00", "\xe0"):  # 扩展键前缀（方向键等）
            second = msvcrt.getwch()
            return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(
                second, "unknown"
            )
        return _normalize(first)

    def _read_posix(self) -> str:
        first = self._stream.read(1)
        if first == "\x1b":
            import select

            rest = ""
            if select.select([self._stream], [], [], 0.2)[0]:
                rest = self._stream.read(2)
            return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(
                rest, "esc"
            )
        return _normalize(first)


def pick(
    items: Sequence[str],
    *,
    multi: bool,
    read_key: Callable[[], str],
    write: Callable[[str], None],
    initial: Sequence[int] | None = None,
    title: str = "",
    hint: str = "",
    ansi: bool = True,
) -> list[int] | None:
    """Interactive list picker; returns selected indices, or ``None`` on Esc.

    ``multi``: space toggles the cursor row, enter confirms. Default selection
    is all rows when ``initial`` is ``None`` (download defaults to everything).
    Single-select: enter confirms the cursor row; letter keys ``a``..``z``
    confirm the matching row immediately (menu shortcuts).

    The picker is pure logic: key events come from ``read_key`` and rendering
    goes through ``write``, so tests inject scripted keys and a collector.
    With ``ansi=False`` each redraw reprints the block without cursor movement
    (terminal without VT support).
    """
    n = len(items)
    if n == 0:
        return []
    if multi:
        selected = set(range(n)) if initial is None else set(initial)
    else:
        selected = set()
    cursor = 0
    total = n + (1 if title else 0) + (1 if hint else 0)
    clear = _CLEAR_LINE if ansi else ""

    def block() -> str:
        parts: list[str] = []
        if title:
            parts.append(clear + title + "\n")
        for i, item in enumerate(items):
            mark = ">" if i == cursor else " "
            if multi:
                box = "[x]" if i in selected else "[ ]"
                parts.append(f"{clear}{mark} {box} {item}\n")
            else:
                parts.append(f"{clear}{mark} {item}\n")
        if hint:
            parts.append(clear + hint + "\n")
        return "".join(parts)

    drawn = False
    try:
        while True:
            if ansi and not drawn:
                write(_CURSOR_HIDE)
            if ansi and drawn and total:
                write(f"\x1b[{total}A")
            write(block())
            drawn = True
            key = read_key()
            if key == "up":
                cursor = (cursor - 1) % n
            elif key == "down":
                cursor = (cursor + 1) % n
            elif key == "space" and multi:
                if cursor in selected:
                    selected.discard(cursor)
                else:
                    selected.add(cursor)
            elif key == "enter":
                return sorted(selected) if multi else [cursor]
            elif key == "esc":
                return None
            elif not multi and len(key) == 1 and "a" <= key <= "z":
                index = ord(key) - ord("a")
                if index < n:
                    return [index]
    finally:
        if ansi:
            write(_CURSOR_SHOW)


def _interactive_available() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except Exception:  # noqa: BLE001 - absence of a TTY is a normal condition
        return False


def _category_name(category: DataRepoCategory) -> str:
    """Display name for one download item: ``remote`` plus its description."""
    if category.desc:
        return f"{category.remote}（{category.desc}）"
    return category.remote


def _prompt_categories(categories: Sequence[DataRepoCategory]) -> list[int] | None:
    labels = [f"{_category_name(c)}  ←  {c.local}" for c in categories]
    reader = KeyReader(sys.stdin)
    with reader:
        return pick(
            labels,
            multi=True,
            read_key=reader.read,
            write=sys.stdout.write,
            title="请选择要下载的数据分类（默认全选）:",
            hint="↑/↓ 移动   空格 选择/取消   回车 确认   Esc 取消",
            ansi=_ANSI_SUPPORTED,
        )


def run_full_sync(config_path: str | None) -> int:
    """Option a: full sync via sync-cache (pull + restore, all categories)."""
    argv = ["--pull", "--restore"]
    if config_path:
        argv = ["--config", config_path, *argv]
    return sync_cache.main(argv)


def _match_remotes(
    categories: Sequence[DataRepoCategory], selected_remotes: Sequence[str]
) -> list[DataRepoCategory]:
    by_remote = {c.remote: c for c in categories}
    names = list(dict.fromkeys(selected_remotes))
    unknown = [name for name in names if name not in by_remote]
    if unknown:
        valid = "、".join(by_remote) or "（无）"
        raise SetupError(
            f"未知分类: {'、'.join(unknown)}；data_repo.yaml 可用分类: {valid}"
        )
    return [by_remote[name] for name in names]


def run_download(config_path: str | None, selected_remotes: list[str] | None) -> int:
    """Option b: restore only the selected categories from the data repo."""
    try:
        config = load_config(config_path)
        sync_cache.ensure_git_available()
        categories = config.data_repo.categories

        if selected_remotes is not None:
            chosen = _match_remotes(categories, selected_remotes)
        else:
            if not _interactive_available():
                print(
                    "error: 需要交互式终端才能选择分类；非交互环境请用 "
                    "--category <名称> 指定要下载的分类。",
                    file=sys.stderr,
                )
                return 1
            chosen = None

        print("正在准备数据仓库工作副本（首次运行将克隆仓库）…")
        root = Path.cwd()
        workdir = sync_cache.ensure_working_copy(config.data_repo, root, pull=True)

        if chosen is None:
            indices = _prompt_categories(categories)
            if indices is None:
                print("已取消。", file=sys.stderr)
                return 1
            chosen = [categories[i] for i in indices]
        if not chosen:
            print("未选择任何分类，未执行下载。")
            return 0

        stats = {"restored": 0}
        for category in chosen:
            print(f"正在下载 {_category_name(category)} …")
            sync_cache.restore_category(
                category.local, category.remote, root, workdir, stats
            )

        print(f"工作副本: {workdir}")
        print(f"restored={stats['restored']}（仅补缺失，不覆盖已有文件）")
        if stats["restored"] == 0:
            print("提示: 数据仓库中暂无文件可下载，或本地文件已齐全。")
        return 0
    except (sync_cache.SyncError, SetupError, ConfigError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def run_menu(config_path: str | None) -> int:
    if not _interactive_available():
        print(
            "error: 需要交互式终端才能显示菜单；非交互环境请使用 --full "
            "或 --download --category <名称>。",
            file=sys.stderr,
        )
        return 1
    reader = KeyReader(sys.stdin)
    with reader:
        choice = pick(
            MENU_ITEMS,
            multi=False,
            read_key=reader.read,
            write=sys.stdout.write,
            title=MENU_TITLE,
            hint=MENU_HINT,
            ansi=_ANSI_SUPPORTED,
        )
    if choice is None:
        print("已取消。", file=sys.stderr)
        return 1
    if choice[0] == 0:
        return run_full_sync(config_path)
    return run_download(config_path, None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arknightsavatar setup",
        description="初始化：全量同步数据仓库（含 export）或交互选择分类仅下载数据文件。",
    )
    parser.add_argument("--config", help="Path to config file")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--full",
        action="store_true",
        help="全量同步：等价 sync-cache --pull --restore（覆盖全部分类，含 export）",
    )
    group.add_argument(
        "--download",
        action="store_true",
        help="仅下载数据文件：交互选择分类后取回本地（不镜像、不提交）",
    )
    parser.add_argument(
        "--category",
        action="append",
        metavar="REMOTE",
        help="仅下载指定分类（数据仓库内路径名，可重复；隐含 --download，跳过交互选择）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.full:
        if args.category:
            parser.error("--category 不能与 --full 同时使用")
        return run_full_sync(args.config)
    if args.download or args.category is not None:
        return run_download(args.config, args.category)
    return run_menu(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
