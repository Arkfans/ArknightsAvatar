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
