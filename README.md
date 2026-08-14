# NpcAvatar

明日方舟 NPC 头像提取管线（迁移重构）。分阶段解耦：

1. **fetch**：从 APK 解包目录 / ADB 设备获取 AB 资源，缓存到 `data/raw/`，带 sha256 manifest，增量更新。
2. **unpack**：用 UnityPy 把 AB 解包为全分辨率 RGBA PNG + 元数据（含游戏自带 `facePos/faceSize`），输出到 `data/unpacked/`，按 `类型/id/` 层级存放。
3. **extract**：按 手动 > 头像匹配 > 模型推导 三档确定裁切框，提取各 base/diff 的头像（180×180 PNG），增量缓存 face/head 识别结果。

## 环境

依赖由 uv 管理（Python >= 3.12）：

```bash
uv python pin 3.12
uv sync --extra fetch --extra unpack --extra match --extra detect --group dev
```

> 注意：pytorch 官方索引的 `torchvision 0.28.0+cu126` Windows wheel 不带
> `#sha256=` 片段，`uv.lock` 已手工补入 cp312-win_amd64 的 hash（否则 Windows 上
> `uv sync` 报 hash mismatch）。若重新执行 `uv lock` 后再次报错，需按
> `uv.lock` 中 torchvision 的 cp312-win_amd64 条目重新补回该 hash。

## 用法

```bash
# 从设备上已安装 APK 中，由设备端 unzip 解压并按需拉取头像
uv run npcavatar-fetch --source apk --category avatars

# 从本地 APK 解包目录取头像（回退来源）
uv run npcavatar-fetch --source local-apk --category avatars

# 从 ADB 设备游戏 Bundles 拉取
uv run npcavatar-fetch --source adb --category characters

# 默认从 ADB 设备拉取 characters 与 avatars 两类资源
uv run npcavatar-fetch

# 解包
uv run npcavatar-unpack --category all

# 立绘分类（独立工具，暂不并入主流程）
uv run npcavatar-classify

# 底图抽样（独立工具，暂不并入主流程）
uv run npcavatar-sample-bases

# 人脸识别（独立工具，暂不并入主流程）
uv run npcavatar-detect

# 高置信底图人脸识别（独立工具，暂不并入主流程）
uv run npcavatar-detect-bases

# 头像提取（独立工具）
uv run npcavatar-extract

# 差分拼贴（独立工具）
uv run npcavatar-collage

# PNG 转 WebP（独立工具）
uv run npcavatar-export-webp
```

配置优先级：CLI 参数 > `NPCAVATAR_*` 环境变量 > `config.yaml` > 内置默认值。

## 从设备拉取已安装 APK（独立工具）

`npcavatar-fetch --source apk` 通过 adb_shell 直连设备（本机无需安装 adb），从手机上
已安装的 APK 用设备端 `unzip` 直接解压单个 AB 条目后拉回本地；不传输完整 APK，也
不在设备上落地解包目录，只拉取本地缺少或大小变化的 AB 文件。`--source local-apk`
仍可读取本地 APK 解包目录。

`npcavatar-pull-apk` 通过 adb_shell 直连设备（本机无需安装 adb），从手机上已安装的
游戏包拉取 APK 到本地。包名默认从 `config.yaml` 配置的 game location
（official/bilibili）自动推导（如 `com.hypergryph.arknights`）；`pm path` 查询安装
路径，`dumpsys` 读取版本，产物沿用 `arknights-hg-<版本>.apk` 命名
（versionName 2.7.61 → 2761）。拉取先写 `.part` 临时文件并计算 sha256，与本地已有
文件相同则删除临时文件，不同才替换；同步协议失败时自动降级为 `shell cat`。

```bash
# 同步环境（本工具只需 fetch 依赖：adb-shell、PyYAML）
uv sync --extra fetch

# 只探测设备（连接 + pm path + 版本），不拉取
uv run npcavatar-pull-apk --no-pull

# 完整拉取并比对（默认输出 apk/）
uv run npcavatar-pull-apk

# 指定包名 / 输出目录
uv run npcavatar-pull-apk --package com.hypergryph.arknights.bilibili --out apk
```

主机/端口来自 `config.yaml` 的 `adb.host` / `adb.port`，服务区由 `adb.game.server`
决定（official / bilibili）。

## 跳过清单

`npcavatar-detect`、`npcavatar-detect-bases`、`npcavatar-extract`、
`npcavatar-collage`、`npcavatar-export-webp`、`npcavatar-npc-json`
支持读取 `data/unpacked/_avatar_skip.json` 跳过指定角色或图片。格式：

```json
{
  "avg_003_kalts_1": "该角色整体跳过，原因...",
  "avg_007_closre_1/base.png": "该图片跳过，原因..."
}
```

- 键没有 `/` 时跳过该角色全部图片；带 `/` 时只跳过对应 sprite。
- sprite 名可写文件名或省略 `.png`，大小写不敏感。
- 跳过 base 时同时跳过该 base 名下所有 diff；跳过 diff 时只跳过该 diff。
- 文件缺失或内容非法时视为空清单，不报错。
- 各命令可用 `--skip <path>` 覆盖默认路径；`npcavatar-export-webp` 与
  `npcavatar-npc-json` 还可用 `--classified <path>` 指定分类报告，用于把
  base 跳过展开到所属 diff。未提供分类报告时，base 级跳过只精确匹配该文件。

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
缩放搜索时模板最大边长不超过该值）、`--stop-threshold`（默认 0.70）、
`--confidence-target`（默认 0.85）、`--limit`、`--character <角色名>`
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

## 人脸识别（独立工具）

`npcavatar-detect` 使用 anime-face-detector 的 YOLOv3 人脸检测器（纯检测，
不加载关键点模型），对每张图片只输出**最高置信度**的一个结果：`face_pos`
（左上角 + 尺寸 `{x, y, w, h}`，原始像素、四舍五入）与 `confidence`。
置信度低于 `--conf`（默认 0.3）视为未检出。该工具未接入 fetch/unpack 主流程，
用于为后续头像提取（goal.md 步骤 3）的模型识别接口提供识别结果；设备默认 auto
（有 CUDA 用 GPU，否则 CPU），权重由 anime-face-detector 自动下载并缓存。

```bash
# 批量识别 characters 目录下所有角色（底图 + 差分）
uv run npcavatar-detect

# 只识别指定角色
uv run npcavatar-detect --character avg_003_kalts_1

# 只处理前 20 个角色，输出到 stdout
uv run npcavatar-detect --limit 20 --output -

# 单张/多张图片快速测试
uv run npcavatar-detect path/to/a.png path/to/b.png

# 指定置信度阈值与设备
uv run npcavatar-detect --conf 0.3 --device auto
```

输出语义：批量模式 `characters.<角色名>.images.<文件名>` 为
`{image, image_size, detected, face_pos, confidence, error}`；
`detected=false` 表示未检出或低于阈值（此时 `face_pos`/`confidence` 为 null），
读图失败或检测异常时 `error` 非空。单图模式输出 `{generated_at, images, stats}`。
报告默认写入 `data/unpacked/_face_detect.json`，`--output -` 输出到 stdout。

## 高置信底图人脸识别（独立工具）

`npcavatar-detect-bases` 读取头像匹配报告（默认
`data/unpacked/_avatar_match.json`），筛选 match threshold **严格大于**
`--threshold`（默认 0.95）的底图，对每张底图用与 `npcavatar-detect`
相同的模型识别方案（anime-face-detector YOLOv3，复用 `npcavatar.detect.detect_top1`）
识别人脸，输出：

1. JSON 报告（默认 `data/unpacked/_face_detect_matched.json`）；
2. 标注结果图（默认 `data/unpacked/_face_detect_vis/`，扁平存放，文件名
   `<角色名>__<底图>.png`）：绿色框为 avatar 匹配范围并标注 `match <threshold>`，
   红色框为 YOLO 人脸框并标注 `yolo <confidence>`，未检出时标注 `no face`；
3. tqdm 进度条显示逐张处理进度（缺 tqdm 时回退为 `[序号/总数] 角色/底图` 文本）。

```bash
# 全量处理高置信底图
uv run npcavatar-detect-bases

# 冒烟：只处理前 3 张高置信底图，输出到临时路径
uv run npcavatar-detect-bases --limit 3 --output tmp/_face_detect_matched.json --vis-dir tmp/_face_detect_vis

# 指定匹配报告、阈值与识别置信度
uv run npcavatar-detect-bases --match data/unpacked/_avatar_match.json --threshold 0.95 --conf 0.3

# 只处理指定角色；设备默认 auto（有 CUDA 用 GPU，否则 CPU）
uv run npcavatar-detect-bases --character avg_003_kalts_1 --device auto
```

输出语义：顶层为 `{generated_at, match_file, characters_dir, threshold, stats,
characters}`；`characters.<角色>.bases.<底图>` 为
`{image, avatar, threshold, box, box_norm, image_size, detected, face_pos,
confidence, error}`，其中 `avatar/threshold/box/box_norm` 来自匹配报告，
`face_pos/confidence` 为模型识别结果（`face_pos` 为左上角 + 尺寸
`{x, y, w, h}`，原始像素），`error` 非空表示读图失败或检测异常；渲染成功时
另附 `vis_image`。`stats` 为 `{filtered, detected, not_detected, errors}`，
`filtered` 表示实际处理的底图数（`--limit`/`--character` 后）。
该工具不接入 fetch/unpack 主流程；模型权重首次运行时由 anime-face-detector
自动下载并缓存（需联网）。

## 头像提取（独立工具）

`npcavatar-extract` 读取 `data/unpacked/_characters_classified.json`，对每个角色的
base 按 **手动指定 > 头像匹配 > 模型推导** 三档确定头像裁切框，再对 base 与全部 diff
提取 180×180 头像 PNG（`data/export/<角色>/<图片stem>.png`，越界补透明）。提取是
增量的：目标文件已存在则跳过（`--force` 强制重提）；face/head 识别结果缓存在
`data/unpacked/_face_head_detect.json`（主键 `"<角色>/<图片>"`，与手动文件键格式一致），
重复运行不重复推理；base 两两相似度与 diff 匹配决策（IoU、special、box、method、
confidence）缓存在 `data/unpacked/_avatar_extract_cache.json`（`--cache` 可换路径），
重跑/`--force` 不再重复计算，缓存文件本身也是调试产物。

```bash
# 提取全部角色
uv run npcavatar-extract

# 只处理指定角色 / 前 N 个角色
uv run npcavatar-extract --character avg_003_kalts_1
uv run npcavatar-extract --limit 20

# 强制重提 / 强制重算匹配
uv run npcavatar-extract --force
uv run npcavatar-extract --force-match
```

裁切框三档优先级：

1. **手动**：`data/unpacked/_avatar_manual.json`（键 `"<角色>/<图片>"`，值
   `{"box": [x1, y1, x2, y2]}`，原图像素），命中即用；
2. **匹配**：复用 `data/unpacked/_avatar_match.json` 中 threshold **> 0.8** 的结果；
   报告缺失或加 `--force-match` 时内联调用匹配逻辑重算；
3. **推导**：对人脸置信度 **> 0.8** 且头部置信度 **> 0.7** 的图，用
   `data/avatar_derive_08/model.json` 由 face/head 检测框推导正方形裁切框。

diff 处理：与 base 同尺寸的 diff 直接使用；小尺寸 diff 按 `meta.json` 的
`face_groups`（`$n` 系列对应第 n 组）缩放至 `faceSize` 后贴到 `facePos` 完成组合；
组合结果的 A 通道取自 base，存在 `alpha.png` 时面部区域优先用其灰度作为 A 通道与
贴图蒙版。`alpha.png` 本身是提供 alpha 通道的贴图而非真实 diff，不参与提取、不计入
报告与统计。
组合后与 base 在 base 裁切框（脸部范围）内比较 **alpha 不透明掩码 IoU**（默认 0.85，`--special-mask-iou`）：
正常 diff 复用 base 裁切框；低于阈值视为**特殊 diff**（动作/姿态变化导致头像位置
偏移），对组合图重新做人脸/头部识别并按第三档推导框提取。

多 base 角色：先提取各 base 头像，两两比较相似度（透明合成灰度相关，默认
**> 0.98** 判重复），保留置信度更高者（手动 > 匹配 > 推导，同档比分数，再按文件名
稳定排序）；被丢弃的 base 及其 diff 不写新文件（已存在文件不删除），报告中标
`dropped`。

报告默认写入 `data/unpacked/_avatar_extract.json`（`--output -` 输出到 stdout），
每角色 bases/diffs 各带 `{status, method, confidence, box, avatar_file, special?,
detect_cache_hit?}`；`stats` 汇总 base/diff 的 ok/skipped/dropped/no_box/failed 与
识别缓存及相似度/diff 决策缓存命中/新增数。两类缓存均每 50 次新写入增量落盘、结束
再落盘；相似度缓存键为 `"<角色>/<baseA>__<baseB>"`（base 名升序对），值为两个 180×180
头像的 sha256、裁切框与相似度，头像内容或裁切框变化即自动重算；diff 决策缓存键为
`"<角色>/<diff>"`，值为组合图+阈值+推导模型+所属 base 裁切结果的指纹与
`{iou, special, box, method, confidence, detect_cache_hit, error}`，任一输入变化
即自动重算（`no_box` 决策同样回放）。缓存不做键清理，旧条目保留作调试历史。

可调参数：`--match-threshold`（0.8）、`--face-conf`（0.8）、`--head-conf`（0.7）、
`--special-mask-iou`（0.85）、`--dedup-sim`（0.98）、`--manual`、`--derive-model`、
`--face-head-cache`、`--cache`、`--output-dir`、`--output`、`--limit`、`--character`、
`--force`、`--force-match`、`--device`（auto/cuda/cpu）。依赖 `uv sync --extra detect`。
模型权重未缓存时首次运行需联网；本地已缓存且离线运行时设 `HF_HUB_OFFLINE=1`。


## 差分拼贴（独立工具）

`npcavatar-collage` 读取 `data/unpacked/_characters_classified.json` 与
`data/export/`，把每个角色的全部 diff 头像（180×180，已由 extract 组合并裁切）
按网格拼贴成一张 PNG，每角色一张。参考旧项目 `NpcData.draw_all_face`：黑底、
每格白底 + 头像（RGBA 蒙版）、左上角标注 diff 文件名，缺失/读取失败的头像画
`[x]` 占位。默认处理所有角色、默认 3 列。

```bash
# 全部角色各生成一张拼贴图
uv run npcavatar-collage

# 只处理指定角色 / 前 N 个角色
uv run npcavatar-collage --character avg_003_kalts_1
uv run npcavatar-collage --limit 20

# 指定列数 / 关闭标注 / 自定义字体
uv run npcavatar-collage --columns 6
uv run npcavatar-collage --no-label
uv run npcavatar-collage --font C:\Windows\Fonts\msyh.ttc
```

输出目录默认 `data/unpacked/_diff_collage/<角色>.png`（`-o` 可换）。只拼贴分类
报告中归属 base 的 diff（`alpha.png` 与 `unassigned` 不计入）；无 diff 或角色
目录缺失时跳过该角色。依赖 Pillow（`uv sync --extra unpack`）。

## PNG 转 WebP（独立工具）

`npcavatar-export-webp` 扫描 `data/export/` 下各角色文件夹内的 PNG
头像，逐张转换为 WebP 输出到 `data/export_webp/<角色>/`，
保持目录结构与透明通道（PNG 按 RGBA 解码后保存）。
增量转换：输出 `.webp` 已存在时跳过，加 `--force` 强制重转。

```bash
# 全部角色转换
uv run npcavatar-export-webp

# 只转指定角色（可重复）/ 前 N 个角色
uv run npcavatar-export-webp --character avg_003_kalts_1
uv run npcavatar-export-webp --limit 20

# 调整压缩参数 / 强制重转
uv run npcavatar-export-webp --quality 75 --method 6
uv run npcavatar-export-webp --force
```

可调参数：`--export-dir`（默认 `data/export`）、
`-o/--output-dir`（默认 `data/export_webp`）、`--quality`（0-100，默认 80）、
`--method`（0-6，默认 4，越大压缩越慢体积越小）、
`--character`（可重复）、`--limit`、`--force`。
依赖 Pillow（`uv sync --extra unpack`）。


## 生成 NPC 头像索引 JSON（独立工具）

`npcavatar-npc-json` 扫描 `data/export/` 下各角色文件夹内的 PNG 头像，
生成与旧项目 `arknights_npc.json` 相同格式的索引 JSON（默认输出
`data/arknights_npc.json`，`-o -` 输出到 stdout）：

```json
{
  "avg_003_kalts_1": [
    [],
    ["1$1", "10$1", "11$1", "12$1", "13$1", "14$1", "15$1", "2$1", "3$1", "4$1", "5$1", "6$1", "7$1", "8$1", "9$1", "avg_003_kalts_1$1"],
    ["npc"]
  ]
}
```

每条形如 `[表情列表, 头像列表, 标签]`：首尾两元素为旧项目保留的固定占位
（`[]` / `["npc"]`），头像列表为目录内所有 PNG 文件名（去扩展名）的字典序。
键与头像列表均按字典序排序，输出确定可复现。纯标准库，无额外依赖。

```bash
# 全量生成
uv run npcavatar-npc-json

# 指定输入目录 / 输出到 stdout
uv run npcavatar-npc-json --export-dir data/export -o -
```

可调参数：`--export-dir`（默认 `data/export`）、
`-o/--output`（默认 `data/arknights_npc.json`，`-` 输出到 stdout）。

## 磁盘契约

```text
data/raw/manifest.json          # {game_version, updated_at, files: {rel: {size, sha256, source}}}
data/raw/_failed.json           # 拉取失败清单
data/raw/<category>/<name>.ab   # 原始 AB 缓存

data/unpacked/_manifest.json    # {rel: source_sha256}，增量解包依据
data/unpacked/_failed.json      # 解包失败清单
data/unpacked/characters/<npc_id>/<sprite>.png + meta.json
data/unpacked/avatars/<sprite>.png + _meta/<bundle>.json   # 扁平存放，仅保留 char_* 角色头像，其余素材解包时清理
data/unpacked/_face_head_detect.json     # extract 的 face/head 识别缓存（主键 <角色>/<图片>）
data/unpacked/_avatar_extract_cache.json # extract 的 base 相似度 / diff 决策缓存
data/unpacked/_avatar_extract.json       # extract 提取报告
data/export/<npc_id>/<sprite>.png        # extract 产物：各 base/diff 的 180×180 头像（按角色分文件夹）
data/export_webp/<npc_id>/<sprite>.webp   # export-webp 产物：头像 WebP 版（目录结构与 data/export 一致）
data/arknights_npc.json             # npc-json 产物：<npc_id> -> [[], [头像文件名], ["npc"]]
data/unpacked/_diff_collage/<npc_id>.png   # collage 产物：每角色一张差分拼贴图
```

`meta.json` 保留 textures 尺寸、sprites 列表、`face_groups`（facePos/faceSize 配对），供步骤 3 使用。
