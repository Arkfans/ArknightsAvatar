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

# 立绘分类（独立工具，暂不并入主流程）
uv run npcavatar-classify

# 底图抽样（独立工具，暂不并入主流程）
uv run npcavatar-sample-bases
```

配置优先级：CLI 参数 > `NPCAVATAR_*` 环境变量 > `config.yaml` > 内置默认值。

## 立绘分类（独立工具）

`npcavatar-classify` 扫描 `data/unpacked/characters/<npc_id>/`，按文件名把每个角色的 PNG
划分为底图与差分，并让差分按所属底图分组，输出 JSON 报告（默认
`data/unpacked/_characters_classified.json`，`--output -` 输出到 stdout）。规则：
以角色 id 形似纹理（`avg_/char_/avgnew_/npc_`，不区分大小写）的公共前缀为角色根名，
裸根名或 `根名_1`/`根名#1`（序号 1）为底图，`根名$n` 全部为底图（多底图切分），
其余为差分；目录内仅有一张图片时无论名称如何都算底图。差分按 `$n` 归属对应底图
（如 `1$1` 属于 base1、`1$2` 属于 base2）。
每个角色的报告形如 `bases: {<底图文件>: {"diff": [...]}}`，无法归属的差分进
`unassigned`；仍无底图时兜底取字符串排序最小的文件作为底图（如 `char_242_mayer#2`）。
该工具不移动任何文件，也未接入 fetch/unpack 主流程。

## 底图抽样（独立工具）

`npcavatar-sample-bases` 读取 `_characters_classified.json`，从有底图的角色中随机抽取
指定数量（默认 100），把每个角色的底图复制到新文件夹（默认
`data/unpacked/bases_sample/`），图片展平存放在同一层级，不建角色子目录。
只复制底图，不复制差分，也不改动源目录；若不同角色的底图同名，自动加
`<角色 id>_` 前缀避免覆盖。

```bash
# 随机抽取 100 个角色并复制底图
uv run npcavatar-sample-bases

# 指定抽样数量、目标目录与随机种子（同一种子结果可复现）
uv run npcavatar-sample-bases -n 50 -o data/unpacked/bases_sample --seed 42

# 使用其它位置的分类报告
uv run npcavatar-sample-bases --classified data/unpacked/_characters_classified.json
```

源角色目录默认取自分类报告内的 `characters_dir` 字段，也可用
`--characters-dir` 覆盖；`-o` 指定输出目录，`--seed` 指定随机种子。

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
