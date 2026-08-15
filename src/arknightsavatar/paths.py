"""Centralized default on-disk paths (``data/`` contract).

Every tool reads its default paths from here so the disk layout is defined
in one place. Pipeline artifacts split into four families:

- local caches:            ``data/raw``, ``data/unpacked`` (not shared);
- recognition data:        ``data/recognition/`` (reports, caches, manual /
                           skip lists, derive model -- carried by the data repo);
- produced assets:         ``data/export``, ``data/export_webp``,
                           ``data/arknights_npc.json``;
- statistics lists:        ``data/stats/``.
"""

# 拉取 / 解包（本地缓存，不进数据仓库）
RAW_DIR = "data/raw"
RAW_MANIFEST = "data/raw/manifest.json"
RAW_FAILED = "data/raw/_failed.json"
UNPACKED_DIR = "data/unpacked"
UNPACKED_CHARACTERS_DIR = "data/unpacked/characters"
UNPACKED_AVATARS_DIR = "data/unpacked/avatars"
# 解包簿记（非识别数据，保留在 data/unpacked 根下）
UNPACKED_MANIFEST = "data/unpacked/_manifest.json"
UNPACKED_FAILED = "data/unpacked/_failed.json"

# 识别数据（统一 data/recognition/，去掉 _ 前缀）
RECOGNITION_DIR = "data/recognition"
CLASSIFIED = "data/recognition/characters_classified.json"
AVATAR_MATCH = "data/recognition/avatar_match.json"
FACE_DETECT = "data/recognition/face_detect.json"
FACE_DETECT_MATCHED = "data/recognition/face_detect_matched.json"
FACE_HEAD_CACHE = "data/recognition/face_head_detect.json"
EXTRACT_CACHE = "data/recognition/avatar_extract_cache.json"
EXTRACT_REPORT = "data/recognition/avatar_extract.json"
MANUAL = "data/recognition/avatar_manual.json"
SKIP_LIST = "data/recognition/avatar_skip.json"
FACE_DETECT_VIS_DIR = "data/recognition/face_detect_vis"
DIFF_COLLAGE_DIR = "data/recognition/diff_collage"
BASES_SAMPLE_DIR = "data/recognition/bases_sample"

# 推导模型（derive-model 产物，extract 第 3 档输入）
DERIVE_DIR = "data/recognition/derive"
DERIVE_MODEL = "data/recognition/derive/model.json"

# 产物
EXPORT_DIR = "data/export"
EXPORT_WEBP_DIR = "data/export_webp"
NPC_JSON = "data/arknights_npc.json"

# 统计列表
STATS_DIR = "data/stats"

# 增量更新数据文件（数据仓库承载，manifest 位于各分类目录内）
VERSION_JSON = "data/version.json"
CHANGELOG = "data/changelog.ndjson"
SCHEMA_DIR = "data/schema"
EXPORT_MANIFEST = "data/export/manifest.json"
EXPORT_WEBP_MANIFEST = "data/export_webp/manifest.json"
RECOGNITION_MANIFEST = "data/recognition/manifest.json"
STATS_MANIFEST = "data/stats/manifest.json"
CHARACTERS_CSV = "data/stats/characters.csv"

# 数据仓库本地 git 工作副本（sync-cache）
DATA_REPO_DIR = "data_cache"
