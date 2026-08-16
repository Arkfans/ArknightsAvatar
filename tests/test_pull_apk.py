from arknightsavatar.pull_apk import load_rsa_keys, package_from_location, version_stem


def test_package_from_location_official():
    location = "/storage/emulated/0/Android/data/com.hypergryph.arknights/files/Bundles"
    assert package_from_location(location) == "com.hypergryph.arknights"
    # same regex-match branch covers a suffixed (Bilibili) package name
    bilibili = "/storage/emulated/0/Android/data/com.hypergryph.arknights.bilibili/files/Bundles"
    assert package_from_location(bilibili) == "com.hypergryph.arknights.bilibili"


def test_package_from_location_unrecognized_falls_back():
    assert package_from_location("/sdcard/game") == "com.hypergryph.arknights"


def test_version_stem_from_version_name():
    assert version_stem({"versionName": "2.7.61", "versionCode": "180"}) == "2761"


def test_version_stem_falls_back_to_version_code():
    assert version_stem({"versionCode": "180"}) == "180"
    assert version_stem({}) == "unknown"


def test_load_rsa_keys_generates_and_loads(tmp_path):
    key = tmp_path / "adbkey"
    signers = load_rsa_keys(str(key))
    assert len(signers) == 1
    assert key.exists()
    assert key.with_suffix(".pub").exists()
    assert signers[0].GetPublicKey()


def test_load_rsa_keys_reuses_existing_key(tmp_path):
    key = tmp_path / "adbkey"
    load_rsa_keys(str(key))
    before = key.read_bytes()
    load_rsa_keys(str(key))
    assert key.read_bytes() == before


def test_pull_apk_cat_fallback_quotes_hostile_remote_path(tmp_path):
    """P2: sync pull 失败回退到 shell cat 时，含空格/元字符的 remote_path 被 shlex.quote 转义。"""
    import shlex
    from unittest.mock import Mock

    from arknightsavatar.pull_apk import pull_apk

    device = Mock()
    hostile = "/data/app/My Arknights $PKG/base.apk"

    def fake_pull(remote, dest, progress_callback=None, **kwargs):
        raise RuntimeError("permission denied")  # 触发 cat 回退分支

    device.pull.side_effect = fake_pull
    dest = tmp_path / "out.apk"
    device.shell.return_value = b"apk-bytes"

    pull_apk(device, hostile, dest, progress=False)
    sent = device.shell.call_args.args[0]
    tokens = shlex.split(sent)
    assert tokens[0] == "cat"
    # 关键：含空格/$ 的路径被还原为单个 argv（未被注入/拆分）
    assert tokens[1] == hostile
    assert dest.read_bytes() == b"apk-bytes"
