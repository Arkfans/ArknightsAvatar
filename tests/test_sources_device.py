"""Tests for ``sources.device`` shell helpers (pm path / dumpsys quoting).

Covers the hostile-filename / shell-metacharacter gap flagged in the code
review: ``installed_apk_paths``/``installed_version`` must ``shlex.quote`` the
package so a configured ``--package`` containing spaces or shell meta is
neither split nor injected.
"""

from __future__ import annotations

import shlex
from unittest.mock import Mock

from arknightsavatar.sources import device


def _fake_device() -> Mock:
    d = Mock()
    d.shell = Mock()
    return d


def test_installed_apk_paths_quotes_hostile_package():
    dev = _fake_device()
    dev.shell.return_value = "package:/data/app/base.apk\npackage:/data/app/split.apk\n"

    names = device.installed_apk_paths(dev, "my pkg'$0")
    assert names == ["/data/app/base.apk", "/data/app/split.apk"]

    sent = dev.shell.call_args.args[0]
    tokens = shlex.split(sent)
    assert tokens[0] == "pm"
    assert tokens[1] == "path"
    # 关键：含空格/元字符的 package 被还原为单个 argv（未被拆分/注入）
    assert tokens[2] == "my pkg'$0"


def test_installed_version_quotes_hostile_package():
    dev = _fake_device()
    dev.shell.return_value = "versionName=2.7.61\nversionCode=180\n"

    result = device.installed_version(dev, "a b'$c")
    assert result == {"versionName": "2.7.61", "versionCode": "180"}

    sent = dev.shell.call_args.args[0]
    tokens = shlex.split(sent)
    assert tokens[0] == "dumpsys"
    assert tokens[1] == "package"
    # 关键：含空格/单引号/$ 的 package 被还原为单个 argv
    assert tokens[2] == "a b'$c"
