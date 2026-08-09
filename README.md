# NpcAvatar

明日方舟 NPC 头像提取管线（迁移重构）。分阶段解耦：

1. **fetch**：从 APK 解包目录 / ADB 设备获取 AB 资源，缓存到 `data/raw/`，带 sha256 manifest，增量更新。
2. **unpack**：用 UnityPy 把 AB 解包为全分辨率 RGBA PNG + 元数据（含游戏自带 `facePos/faceSize`），输出到 `data/unpacked/`，按 `类型/id/` 层级存放。
3. 人脸识别（规划中，匹配方式待更新）。

## 环境

依赖由 uv 管理（Python >= 3.12）：

```bash
uv python pin 3.12
uv sync --extra fetch --extra unpack --group dev
```

## 用法

```bash
# 从 APK 解包目录取头像
uv run npcavatar-fetch --source apk --category avatars

# 从 ADB 设备拉取
uv run npcavatar-fetch --source adb --category characters

# 默认从 ADB 设备拉取全部四类资源
uv run npcavatar-fetch

# 解包
uv run npcavatar-unpack --category all
```

配置优先级：CLI 参数 > `NPCAVATAR_*` 环境变量 > `config.yaml` > 内置默认值。

## 磁盘契约

```text
data/raw/manifest.json          # {game_version, updated_at, files: {rel: {size, sha256, source}}}
data/raw/_failed.json           # 拉取失败清单
data/raw/<category>/<name>.ab   # 原始 AB 缓存

data/unpacked/_manifest.json    # {rel: source_sha256}，增量解包依据
data/unpacked/_failed.json      # 解包失败清单
data/unpacked/characters/<npc_id>/<sprite>.png + meta.json
data/unpacked/chararts/<char_id>/<sprite>.png + meta.json
data/unpacked/skins/<char_id>/<sprite>.png + meta.json
data/unpacked/avatars/<sprite>.png + _meta/<bundle>.json   # 扁平存放，仅保留 char_* 角色头像，其余素材解包时清理
```

`meta.json` 保留 textures 尺寸、sprites 列表、`face_groups`（facePos/faceSize 配对），供步骤 3 使用。
