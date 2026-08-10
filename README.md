# NpcAvatar

明日方舟 NPC 头像提取管线（迁移重构）。分阶段解耦：

1. **fetch**：从 APK 解包目录 / ADB 设备获取 AB 资源，缓存到 `data/raw/`，带 sha256 manifest，增量更新。
2. **unpack**：用 UnityPy 把 AB 解包为全分辨率 RGBA PNG + 元数据（含游戏自带 `facePos/faceSize`），输出到 `data/unpacked/`，按 `类型/id/` 层级存放。
3. 人脸识别（规划中，匹配方式待更新）。

## 环境

依赖由 uv 管理（Python >= 3.12）：

```bash
uv python pin 3.12
uv sync --extra fetch --extra unpack --extra match --group dev
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

## 头像匹配（独立工具）

`npcavatar-match` 读取 `data/unpacked/_characters_classified.json`，只处理命名符合
`avg_\d+_.+` / `char_\d+_.*` 的角色，从 `data/unpacked/avatars/` 取数字 ID 对应的头像
（`char_<ID>_*`）作为候选，用 OpenCV 模板匹配（TM_CCOEFF_NORMED + 缩放搜索，移植自旧版）
在每张底图上定位头像包围盒，输出报告（默认 `data/unpacked/_avatar_match.json`）。
该工具未接入 fetch/unpack 主流程，用于调整匹配参数。

```bash
# 冒烟：只处理前 20 个角色，输出到 stdout
uv run npcavatar-match --limit 20 --output -

# 只匹配指定角色
uv run npcavatar-match --character avg_003_kalts_1

# 全量匹配
uv run npcavatar-match
```

输出语义：`characters.<name>.bases.<底图>` = `{avatar, threshold, box, box_norm}`，
其中 `box` 为头像在底图原始像素中的包围盒 `[x1, y1, x2, y2]`，`box_norm` 为按
1024 归一化坐标（x 为 0~1，y 可为负）；
匹配前底图会在顶部向上扩展 76px（匹配高度 1100px），坐标原点保持原图左上角不变，
因此 `box`/`box_norm` 的 y1/y2 可为负，负 y 表示头像位于原图顶部上方。
加 `--detail` 后每个底图还会带 `offsets`，按候选头像列出缩放搜索中每一次 offset
的 `{offset, size, threshold, x, y, best}`（`x/y` 为匹配坐标系中的左上角，y 可为负，
`best` 标记最终选中的 offset）。
无候选头像的角色记为 `no_avatar`，全部底图失败记为 `failed`，阈值低于
`--stop-threshold` 的底图计入统计 `low_confidence`。
候选头像按“角色名去掉末尾 `_<数字>` 变体编号后与头像基名的编辑距离”升序匹配，
同距离按文件名稳定排序；某个候选在整次缩放搜索后阈值高于 `--confidence-target`
即采用该结果并跳过后续候选（候选级早停）。
可调参数：`--min-avatar-size`（默认 130）、`--max-avatar-size`（默认 325，
缩放搜索时模板最大边长不超过该值）、`--stop-threshold`（默认 0.85）、
`--confidence-target`（默认 0.9）、`--limit`、`--character <角色名>`
（只处理指定角色，需与分类报告中的角色名完全一致）、`--detail`
（输出逐 offset 的详细匹配情况）。

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
