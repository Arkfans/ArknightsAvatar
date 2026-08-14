from arknightsavatar import paths


def test_recognition_paths_have_no_underscore_prefix():
    """识别数据统一 data/recognition/ 且文件名不带 _ 前缀。"""
    assert paths.RECOGNITION_DIR == "data/recognition"
    for name in (
        paths.CLASSIFIED,
        paths.AVATAR_MATCH,
        paths.FACE_DETECT,
        paths.FACE_DETECT_MATCHED,
        paths.FACE_HEAD_CACHE,
        paths.EXTRACT_CACHE,
        paths.EXTRACT_REPORT,
        paths.MANUAL,
        paths.SKIP_LIST,
    ):
        assert name.startswith("data/recognition/")
        stem = name.rsplit("/", 1)[-1]
        assert not stem.startswith("_")


def test_unpack_bookkeeping_stays_in_unpacked():
    assert paths.UNPACKED_MANIFEST == "data/unpacked/_manifest.json"
    assert paths.UNPACKED_FAILED == "data/unpacked/_failed.json"


def test_derive_model_and_products():
    assert paths.DERIVE_MODEL == "data/recognition/derive/model.json"
    assert paths.DERIVE_DIR == "data/recognition/derive"
    assert paths.EXPORT_DIR == "data/export"
    assert paths.EXPORT_WEBP_DIR == "data/export_webp"
    assert paths.NPC_JSON == "data/arknights_npc.json"
    assert paths.STATS_DIR == "data/stats"
    assert paths.DATA_REPO_DIR == "data_cache"
