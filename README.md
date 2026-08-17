# ArknightsAvatar

明日方舟 NPC 头像提取管线（迁移重构），面向自动化调用：统一入口 + 编排型入口 +
12 个单工具，分阶段解耦：

1. **fetch**：从 APK 解包目录 / ADB 设备获取 AB 资源，缓存到 `data/raw/`，带 sha256 manifest，增量更新。
2. **unpack**：用 UnityPy 把 AB 解包为全分辨率 RGBA PNG + 元数据（含游戏自带 `facePos/faceSize`），输出到 `data/unpacked/`，按 `类型/id/` 层级存放。
3. **extract**：按 手动 > 头像匹配 > 模型推导 三档确定裁切框，提取各 base/diff 的头像（180×180 PNG），增量缓存 face/head 识别结果。

识别数据统一存放在 `data/recognition/`（文件名不带 `_` 前缀）；识别数据 / 原始 avatar /
提取 avatar / 统计列表由独立的 GitHub 数据仓库承载（`arknightsavatar sync-cache`
以「本地 git 工作副本 + git CLI」自动增量提交），主仓库不跟踪这些数据。

## 环境

依赖由 uv 管理（Python >= 3.12）。**GPU 依赖可选，默认 CPU**：`detect`（CPU 推理栈，
torch `+cpu` 轮子）作为默认 dependency-group 与 extra，`detect-gpu`（pytorch-cu126
索引，torch `+cu126` 轮子，仅 Windows / Linux x86_64）为互斥的 extra，二者二选一：

```bash
# 默认即为 CPU（安装 dev + detect 组，含 CPU 版 torch/torchvision）
uv sync

# 显式指定 CPU 推理栈（等价形式）
uv sync --extra detect

# GPU：排除默认 detect 组后安装 cu126（二选一，不可同时装 detect）
uv sync --no-group detect --extra detect-gpu

# 其它可选依赖
uv sync --extra fetch --extra unpack --extra match
```

- `uv sync` 默认安装 `dev` + `detect` 两个 dependency-group（`[tool.uv] default-groups`）。
- `detect` 与 `detect-gpu` 在 `[tool.uv] conflicts` 中声明互斥；同时安装会得到清晰的
  冲突报错。CUDA 不可用时 `--device auto` 自动回退 CPU。
- macOS 与 Linux aarch64 无专用 GPU 轮子，`detect-gpu` 在那些平台上回落到默认索引的
  CPU 轮子。
- `uv.lock` 中同时存有 `2.13.0+cpu` 与 `2.13.0+cu126` 两个 fork（conflicting extras）。

> 注意：pytorch 官方索引的 torchvision cu126 Windows wheel 可能不带 `#sha256=`
> 片段，`uv lock` 重新生成后若 Windows 上 `uv sync` 报 hash mismatch，需按
> `uv.lock` 中 torchvision 的 cp312-win_amd64 条目手工补回该 hash（历史版本即如此处理）。

## 统一入口与编排型入口

统一入口 `arknightsavatar` 按子命令分发；7 个编排型入口各自也有独立脚本。

```bash
uv run arknightsavatar --help            # 子命令总览
uv run arknightsavatar detect --conf 0.3 # 子命令分发到单工具（argv 透传）
```

### 编排型入口

| 命令 | 职责 |
| --- | --- |
| `arknightsavatar run` | 全流程：fetch → unpack → classify → match → extract → export-webp → npc-json |
| `arknightsavatar pull` | 设备侧获取：fetch（默认 adb+apk 双源合并；`--with-apk` 可选追加 pull-apk） |
| `arknightsavatar produce` | 离线生产：classify → match → extract → export-webp → npc-json（不触设备） |
| `arknightsavatar build-model` | 从零构建推导模型：fetch → unpack → classify → match → detect-bases → derive-model（含资源拉取） |
| `arknightsavatar derive-model` | 由 `data/recognition/face_detect_matched.json` 重新拟合 face/head → 裁切框推导模型 |
| `arknightsavatar sync-cache` | 把数据目录同步提交到 GitHub 数据仓库（本地 git 工作副本 + git CLI） |
| `arknightsavatar setup` | 初始化向导：全量同步（含 export）或交互选择分类仅下载数据文件 |

### run（全流程）

```bash
# 全流程（默认 source=adb+apk、category=all）
uv run arknightsavatar run

# 只跑部分步骤 / 限定角色数量 / 指定设备
uv run arknightsavatar run --from classify --until extract --limit 20 --device auto

# 强制重拉/重提/重匹配
uv run arknightsavatar run --force --source local-apk
```

步骤在进程内依次执行（复用各单工具的 `main(argv)`，行为与逐个运行一致），失败即中止
并指出失败步骤；每步退出码与总览写入 `data/stats/run_stats.json`（`--stats-out` 可换）。
`match` 步骤的输出报告（默认 `data/recognition/avatar_match.json`）已存在时按角色增量：
候选头像列表变化的角色若存在置信度低于 `--rematch-confidence`（默认 0.9）的底图则重匹配，
其余角色保留旧结果；无可重匹配角色时整步跳过，`--force` 强制全量重跑。
`extract` 依赖推导模型，缺失时给出「先 `derive-model` 或 `sync-cache --pull --restore`」的提示。

### pull（设备侧获取）

```bash
uv run arknightsavatar pull                     # 只 fetch
uv run arknightsavatar pull --with-apk          # pull-apk + fetch
uv run arknightsavatar pull --with-apk --package com.hypergryph.arknights.bilibili --out apk
```

默认 `--source adb apk` 双源合并拉取：设备游戏 Bundles 目录（characters/L2D 立绘等下载资源）
与设备已安装 APK（spritepack 头像等安装包资源）并集才是完整数据；同名文件以设备目录版本
优先（游戏实际运行的数据），APK 补齐设备缺失的文件。adb 源建立在 apk 源之上：Bundles
目录由游戏安装后**运行**生成（更新资源下载到其中），设备未安装/未运行游戏时两源都无数据，
需先安装并运行游戏。`--source adb` / `--source apk` 仍可只取单个源。

### produce（离线生产）

```bash
uv run arknightsavatar produce                  # classify → … → npc-json
uv run arknightsavatar produce --limit 20 --device cpu
```

产物与 `run` 的后半段一致；统计写入 `data/stats/produce_stats.json`。

### derive-model（推导模型）

```bash
uv run arknightsavatar derive-model             # 读 face_detect_matched.json，写 data/recognition/derive/
uv run arknightsavatar derive-model --min-conf 0.8 --out-dir data/recognition/derive
```

产物：`model.json`（extract 第 3 档读取）、`derive_coords.json`、`stats.json`、
`compare/`（抽样可视化，`--no-compare` 跳过）。输入由
`arknightsavatar detect-bases`（阈值 > 0.95 的高置信底图）生成。

### build-model（从零构建推导模型）

```bash
# 从零（空 data/、无中间产物）构建推导模型：拉资源 → 解包 → 分类 → 匹配 → 识别 → 拟合
uv run arknightsavatar build-model

# 断点续跑 / 限定范围 / 指定设备与数量
uv run arknightsavatar build-model --from match --until derive-model --limit 20 --device auto

# 强制重拉重跑（含 detect-bases 全量重识别）/ 渲染标注图（默认跳过） / 跳过 derive 对比图
uv run arknightsavatar build-model --force --vis-dir data/recognition/face_detect_vis --no-compare
```

链路 `fetch → unpack → classify → match → detect-bases → derive-model`，前四步复用
`run` 的 argv 拼装（行为与逐个运行工具一致），统计写入 `data/stats/build_model_stats.json`
（`--stats-out` 可换）。结束时打印最终报告：每个步骤的 ok/failed 状态、总耗时、
统计文件路径，以及 derive-model 产出的 `model.json` 拟合样本数（该步成功时）。
`run` 的 `extract` 依赖已存在的推导模型（缺失时给出
「先 `derive-model` 或 `sync-cache --pull --restore`」的提示），`build-model` 正是
冷启动补齐模型的一步到位入口：新环境或资源大版本更新后先跑它，再跑 `run`。
`detect-bases` 需要 detect 依赖栈（`uv sync` 默认已装 CPU 栈；GPU 用
`uv sync --no-group detect --extra detect-gpu`），缺失时在拉取资源前即报错。
detect-bases 的标注 PNG 默认跳过（`--vis-dir <路径>` 可开启并重定向）；
derive-model 的 compare 抽样图默认生成（`--no-compare` 跳过）。
detect-bases 步骤是增量的：`face_detect_matched.json` 里已有的底图直接复用，
缺失的才重新识别（`--force` 对 detect-bases 同样生效，强制全量重跑）。

### sync-cache（数据仓库同步）

识别数据 / 原始 avatar / 提取 avatar / 统计列表 / 增量更新数据文件由独立 GitHub 数据仓库
承载。创建好 GitHub 数据仓库后，把地址填入 `data_repo.yaml` 的 `url` 即可使用：

```bash
# 把本地数据镜像进本地工作副本 data_cache/ 并自动增量提交（无变化不产生提交）
uv run arknightsavatar sync-cache

# 先 git pull 工作副本；把数据仓库中有而本地缺失的文件取回（如 derive 模型）
uv run arknightsavatar sync-cache --pull --restore

# 只镜像不提交 / 自定义提交信息
uv run arknightsavatar sync-cache --dry-run
uv run arknightsavatar sync-cache --message "sync after 2.7.61"

# 提交后推送到 GitHub（--pull 先取回远端提交，避免非快进拒绝）
uv run arknightsavatar sync-cache --pull --push

# 比较模式（默认：manifest 加速，见下）
uv run arknightsavatar sync-cache --content-hash   # 全量 sha256 内容比较
uv run arknightsavatar sync-cache --size-mtime     # 旧行为：size + mtime
```

分类映射（可在 `data_repo.yaml` 的 `categories` 调整；`config.py` 内置同名默认表）：

| 数据类别 | 本地路径 | 数据仓库路径 |
| --- | --- | --- |
| 识别数据 | `data/recognition/` | `recognition/` |
| 原始 avatar | `data/unpacked/avatars/` | `avatars/` |
| 提取 avatar | `data/export/`、`data/export_webp/` | `export/`、`export_webp/` |
| 统计列表 | `data/stats/`、`data/arknights_npc.json` | `stats/`、`arknights_npc.json` |
| 增量更新数据文件 | `data/version.json`、`data/changelog.ndjson`、`data/schema/` | `version.json`、`changelog.ndjson`、`schema/` |

比较模式：默认（auto）下，某分类两侧（本地 + 工作副本）都有 `manifest.json` 时，按清单的
`{size, sha256}` 指纹比对（零哈希，内容级正确）；未被清单覆盖的文件（可视化目录、清单自身等）
回退 `size + mtime`。`--content-hash` 对全部文件做全量 sha256（清单缺失时的正确性模式）；
`--size-mtime` 完全恢复旧行为。镜像时 `manifest.json` 排在最后复制，中断不会出现"新清单配旧文件"。

`--push` 在提交成功后执行 `git push origin <branch>`（`branch` 取 `data_repo.yaml`，默认
`main`），把数据真正同步到 GitHub：无新提交时不推送（`pushed=False`）；`--dry-run` 下
忽略 `--push`（不做任何远端变更）；远端领先（非快进）时推送被拒并提示先 `--pull` 或
`git pull --rebase`。若上次推送失败（提交已留在本地），重跑 `sync-cache --push` 会
检测到未推送的本地提交并自动补推。

工作副本默认 `data_cache/`（已 gitignore）；url 为空时工具会提示先创建仓库并填写配置。
全程调用 git CLI（clone / pull / add / diff / commit / push），无其它依赖。

### setup（初始化）

交互式初始化向导（`uv run arknightsavatar setup`），数据仓库与分类以
`data_repo.yaml`（与 sync-cache 相同配置）为准，提供两个选项：

- **a) 全量同步（含 export）**：等价 `sync-cache --pull --restore` —— 克隆/拉取
  工作副本，把全部分类（识别数据、原始/提取 avatar、export / export_webp、统计、
  增量更新数据文件等）取回本地，再把本地变更镜像回工作副本并增量提交；
- **b) 仅下载数据文件**：交互多选分类（↑/↓ 移动，空格 选择/取消，回车 确认，
  Esc 取消，默认全选），只把选中分类从数据仓库取回本地 —— 补缺不覆盖、
  不镜像、不提交。

```bash
uv run arknightsavatar setup            # 交互菜单（↑/↓ + 回车，或直接按 a / b）
uv run arknightsavatar setup --full     # 直接全量同步（选项 a）
uv run arknightsavatar setup --download --category recognition --category schema
                                        # 只下载指定分类（跳过交互选择）
```

`restore` 只补本地缺失文件、不覆盖已有文件，重复运行幂等；仓库尚无文件时提示
「暂无文件可下载」，不报错。数据仓库未创建（`data_repo.yaml` 的 `url` 为空）时
提示先创建仓库并填写配置。菜单与选择列表需要交互式终端；非交互环境请用
`--full` 或 `--download --category <名称>`。

### manifest（增量更新数据文件）

`arknightsavatar manifest` 生成开发者增量更新所需的全部数据文件（内容清单、顶层版本指针、
跨版本变更清单、扁平统计），幂等：内容（除 `generated_at` 外）未变时不重写文件，
sync-cache 因此不会产生空提交。发布流程：

```bash
# run/produce 产出之后：生成四分类 manifest + data/version.json
uv run arknightsavatar manifest --version-out

# 发布前对比上一版本（数据仓库工作副本中的旧 version.json 或单个旧清单），
# 写 data/stats/changes.json 并追加 data/changelog.ndjson
uv run arknightsavatar manifest --version-out --since data_cache/version.json --append-changelog

# 附带逐角色扁平统计 data/stats/characters.csv（+.sha256）
uv run arknightsavatar manifest --characters-csv

# 只生成单个分类清单（stdout 输出：-o -）
uv run arknightsavatar manifest --category export
```

产物与格式：

- `data/{recognition,export,export_webp,stats}/manifest.json`：分类内容清单，逐文件
  `{size, sha256}`，键字典序、相对路径 `/` 分隔；清单自身不进清单。`recognition` 默认
  排除 `face_detect_vis/`、`diff_collage/`、`bases_sample/`，包含 `derive/`（`--exclude` 可增补）；
  `stats` 默认排除 `run_stats.json`、`produce_stats.json`（每次运行必变且无消费者读取，
  排除后 stats 指纹只在真实数据变化时更新，不产生空提交）。
- `data/version.json`：顶层指针。`game_version` + `categories.*{path, sha256, files}`（path 为
  数据仓库内相对路径），消费者先读它判断是否需要更新。
- `data/stats/changes.json`：`--since` 跨版本对比（added/removed/modified，按 sha256 判定；
  stats 分类不参与对比）。`--since` 接受旧 `version.json`（按 `categories.*.path` 定位清单）
  或单分类旧 manifest（需配 `--category <name>`）。
- `data/changelog.ndjson`：追加式变更日志，一行一次发布（断点续读按行数）；与末行相同时不重复追加。
- `data/stats/characters.csv`：逐角色一行、字典序（列定义见 `data/schema/README.md`）。

所有报告（classify/match/detect/detect-bases/extract/derive-model/run/produce）顶层统一注入版本头
`{schema_version, pipeline_version, game_version, generated_at}`（只增键，向后兼容）；
`game_version` 解析顺序：`data/raw/manifest.json` → `config.toml`/`ARKNIGHTSAVATAR_GAME_VERSION`
→ `"unknown"`。格式 Schema 见 `data/schema/`（version / manifest / changes / report）。

消费者增量拉取建议：读 `version.json` → 若 `game_version` 或分类指纹变化 → 按
`changes.json`（或 changelog）拉取 added/modified 文件、删除 removed 文件；全量校验用
各分类 `manifest.json` 的 sha256。

## 单工具

14 个单工具保留（改名），既可直接运行也可经统一入口调用：

```bash
# 从设备上已安装 APK 中提取头像（默认设备端解压打包后单次拉取）
uv run arknightsavatar-fetch --source apk --category avatars

# 检测设备是否支持 tar/unzip 等批量拉取依赖的命令（tar 打包、tar gzip、unzip -l/-p）
uv run arknightsavatar-device-caps

# 从本地 APK 解包目录取头像（回退来源）
uv run arknightsavatar-fetch --source local-apk --category avatars

# 从 ADB 设备游戏 Bundles 拉取
# （默认批量拉取：设备端 tar 打包 → 单次传输 → 本地解压，替代逐文件拉取）
uv run arknightsavatar-fetch --source adb --category characters

# 默认从 ADB 设备 Bundles 与已安装 APK 双源合并拉取 characters 与 avatars 两类资源
# （设备版本优先，APK 补齐缺失文件；两源并集才是完整数据）
uv run arknightsavatar-fetch

# 显式双源（与默认等价）
uv run arknightsavatar-fetch --source adb apk

# 逐文件拉取（禁用设备端打包，排查用）；进度行显示 [已拉取/需拉取总量]
uv run arknightsavatar-fetch --no-batch

# 设备端 gzip 压缩后再拉取（AB 已是 LZ4 压缩，实测仅省 ~10% 体积且耗设备 CPU，默认关闭）
uv run arknightsavatar-fetch --compress

# 解包
uv run arknightsavatar-unpack --category all

# 立绘分类（独立工具，暂不并入主流程）
uv run arknightsavatar-classify

# 底图抽样（独立工具，暂不并入主流程）
uv run arknightsavatar-sample-bases

# 人脸识别（独立工具，暂不并入主流程）
uv run arknightsavatar-detect

# 高置信底图人脸识别（独立工具，暂不并入主流程）
uv run arknightsavatar-detect-bases

# 头像提取（独立工具）
uv run arknightsavatar-extract

# 差分拼贴（独立工具）
uv run arknightsavatar-collage

# PNG 转 WebP（独立工具）
uv run arknightsavatar-export-webp

# NPC 头像索引 JSON（独立工具）
uv run arknightsavatar-npc-json
```

配置优先级：CLI 参数 > `ARKNIGHTSAVATAR_*` 环境变量 > `config.toml`（数据仓库见
`data_repo.yaml`）> 内置默认值。首次使用把 `config.example.toml` 复制为
`config.toml` 并按需修改（`config.toml` 已 gitignore）。

## 从设备拉取已安装 APK（独立工具）

`arknightsavatar-fetch --source apk` 通过 adb_shell 直连设备（本机无需安装 adb），
从手机上已安装的 APK 提取 AB 文件；默认批量：设备端把所需条目 `unzip -p` 解压到
临时目录后 `tar` 打包，单次 sync 传输拉回本地再解包校验（缺成员/大小不符的条目自动
回退逐条拉取），只传输本地缺少或大小变化的文件，不拉整包 APK。`--source local-apk`
仍可读取本地 APK 解包目录。

默认 `--source adb apk` 双源合并：APK 源只含 `spritepack`（avatars），characters
（L2D 立绘）只在设备下载目录 `files/Bundles`（adb 源），两源并集才是完整数据；同名
文件以设备 Bundles 版本优先，APK 补齐设备缺失的文件。adb 源依赖游戏安装并运行：
Bundles 目录由游戏启动后生成/下载，设备未安装游戏包时 `ApkAdbSource` 报
"package not installed"，且 adb 源也无数据可取——两源均以游戏已安装为前提。

`arknightsavatar-pull-apk` 通过 adb_shell 直连设备（本机无需安装 adb），从手机上已
安装的游戏包拉取 APK 到本地。包名默认从 `config.toml` 配置的 game location
（official/bilibili）自动推导（如 `com.hypergryph.arknights`）；`pm path` 查询安装
路径，`dumpsys` 读取版本，产物沿用 `arknights-hg-<版本>.apk` 命名
（versionName 2.7.61 → 2761）。拉取先写 `.part` 临时文件并计算 sha256，与本地已有
文件相同则删除临时文件，不同才替换；同步协议失败时自动降级为 `shell cat`。

```bash
# 同步环境（本工具只需 fetch 依赖：adb-shell、PyYAML）
uv sync --extra fetch

# 只探测设备（连接 + pm path + 版本），不拉取
uv run arknightsavatar-pull-apk --no-pull

# 完整拉取并比对（默认输出 apk/）
uv run arknightsavatar-pull-apk

# 指定包名 / 输出目录
uv run arknightsavatar-pull-apk --package com.hypergryph.arknights.bilibili --out apk
```

主机/端口来自 `config.toml` 的 `adb.host` / `adb.port`，服务区由 `adb.game.server`
决定（official / bilibili）。

## 检测设备命令能力（独立工具）

`arknightsavatar-device-caps` 通过 adb_shell 直连设备，探测拉取流程中用于提速的
设备端命令是否可用：`tar` 打包（`tar -cf -C -T`，fetch 批量拉取依赖）、`tar` gzip
（`tar -czf`，`--compress` 依赖）、`unzip -l` / `unzip -p`（apk 源解压依赖），
以及 `ls`/`cat`/`grep`/`printf`/`rm`/`mkdir` 等基础工具。探测是功能性的：在
`/data/local/tmp` 用临时文件实际执行命令并校验结果，失败/超时一律视为「不支持」，
不会报错中断。

```bash
uv run arknightsavatar-device-caps            # 输出能力报告
uv run arknightsavatar-device-caps --config config.toml
```

报告示例：

```
connected: 127.0.0.1:5555
device capability report:
  basic tools:  ls=yes  cat=yes  grep=yes  printf=yes  rm=yes  mkdir=yes
  tar:          present=yes  pack(tar -cf -C -T)=yes  gzip(tar -czf)=yes
  unzip:        present=yes  list(unzip -l)=yes  pipe(unzip -p)=yes
  gzip:         present=yes
  adb batch (device-side tar packing):  available
  apk batch (device-side unzip + tar):  available
  --compress (gzip pack):               available
```

`fetch` 启动时也会自动探测并自适应：设备不支持 `tar -cf -C -T` 时自动改为逐文件
拉取，不支持 `tar -z` 时自动禁用 `--compress`，均打印 warning 说明原因（逐文件/
逐条路径不需要任何额外能力，始终可用）。

## 跳过清单

`arknightsavatar-detect`、`arknightsavatar-detect-bases`、`arknightsavatar-extract`、
`arknightsavatar-collage`、`arknightsavatar-export-webp`、`arknightsavatar-npc-json`
支持读取 `data/recognition/avatar_skip.json` 跳过指定角色或图片。格式：

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
- 各命令可用 `--skip <path>` 覆盖默认路径；`arknightsavatar-export-webp` 与
  `arknightsavatar-npc-json` 还可用 `--classified <path>` 指定分类报告，用于把
  base 跳过展开到所属 diff。未提供分类报告时，base 级跳过只精确匹配该文件。

## 立绘分类（独立工具）

`arknightsavatar-classify` 扫描 `data/unpacked/characters/<npc_id>/`，按文件名把每个
角色的 PNG 划分为底图与差分，并让差分按所属底图分组，输出 JSON 报告（默认
`data/recognition/characters_classified.json`，`--output -` 输出到 stdout）。规则：
以角色 id 形似纹理（`avg_/char_/avgnew_/npc_`，不区分大小写）的公共前缀为角色根名，
裸根名或 `根名_1`/`根名#1`（序号 1）为底图，`根名$n` 全部为底图（多底图切分），
其余为差分；目录内仅有一张图片时无论名称如何都算底图。差分按 `$n` 归属对应底图
（如 `1$1` 属于 base1、`1$2` 属于 base2）。
每个角色的报告形如 `bases: {<底图文件>: {"diff": [...]}}`，无法归属的差分进
`unassigned`；仍无底图时兜底取字符串排序最小的文件作为底图（如 `char_242_mayer#2`）。
该工具不移动任何文件，也未接入 fetch/unpack 主流程。

## 头像匹配（独立工具）

`arknightsavatar-match` 读取 `data/recognition/characters_classified.json`，只处理命名
符合 `avg_\d+_.+` / `char_\d+_.*` 的角色，从 `data/unpacked/avatars/` 取数字 ID 对应
的头像（`char_<ID>_*`）作为候选，用 OpenCV 模板匹配（TM_CCOEFF_NORMED + 缩放搜索，
移植自旧版）在每张底图上定位头像包围盒，输出报告（默认
`data/recognition/avatar_match.json`）。该工具未接入 fetch/unpack 主流程，用于调整
匹配参数。匹配是增量的：输出报告已存在时逐角色对比候选头像列表，仅重匹配「候选列表
发生变化且存在置信度低于 `--rematch-confidence`（默认 0.9）的底图」的角色，其余角色
保留上次结果（新头像解包后重跑只补低置信角色，不重复计算）；没有任何角色需要重匹配
时整步跳过（管道重跑不重复计算），`--force` 强制全量重跑。

```bash
# 冒烟：只处理前 20 个角色，输出到 stdout
uv run arknightsavatar-match --limit 20 --output -

# 只匹配指定角色
uv run arknightsavatar-match --character avg_003_kalts_1

# 全量匹配
uv run arknightsavatar-match

# 报告已存在时强制重跑
uv run arknightsavatar-match --force
```

增量语义细节：某角色候选头像列表（报告里存储的 `candidates`，由 avatars 目录扫描生成）
与上次不同，且满足以下任一条件即重匹配该角色——任一底图匹配失败（error）、任一底图
`threshold < --rematch-confidence`、旧结果引用的头像已不在新候选列表中、或旧报告该角色
没有任何底图结果（如之前 `no_avatar`，现新增了候选头像）；候选列表未变化（或变化后
所有底图仍高置信）的角色一律保留旧结果。

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
`--confidence-target`（默认 0.85）、`--rematch-confidence`（默认 0.9，
候选列表变化时重匹配的底图置信度下限）、`--limit`、`--character <角色名>`
（只处理指定角色，需与分类报告中的角色名完全一致）、`--detail`
（输出逐 offset 的详细匹配情况）、`--force`（报告已存在时强制重跑）。

## 底图抽样（独立工具）

`arknightsavatar-sample-bases` 读取 `characters_classified.json`，从有底图的角色中
随机抽取指定数量（默认 100），把每个角色的底图复制到新文件夹（默认
`data/recognition/bases_sample/`），图片展平存放在同一层级，不建角色子目录。
只复制底图，不复制差分，也不改动源目录；若不同角色的底图同名，自动加
`<角色 id>_` 前缀避免覆盖。

```bash
# 随机抽取 100 个角色并复制底图
uv run arknightsavatar-sample-bases

# 指定抽样数量、目标目录与随机种子（同一种子结果可复现）
uv run arknightsavatar-sample-bases -n 50 -o data/recognition/bases_sample --seed 42

# 使用其它位置的分类报告
uv run arknightsavatar-sample-bases --classified data/recognition/characters_classified.json
```

源角色目录默认取自分类报告内的 `characters_dir` 字段，也可用
`--characters-dir` 覆盖；`-o` 指定输出目录，`--seed` 指定随机种子。

## 人脸识别（独立工具）

`arknightsavatar-detect` 使用 anime-face-detector 的 YOLOv3 人脸检测器（纯检测，
不加载关键点模型），对每张图片只输出**最高置信度**的一个结果：`face_pos`
（左上角 + 尺寸 `{x, y, w, h}`，原始像素、四舍五入）与 `confidence`。
置信度低于 `--conf`（默认 0.3）视为未检出。该工具未接入 fetch/unpack 主流程，
用于为后续头像提取的模型识别接口提供识别结果；设备默认 auto
（有 CUDA 用 GPU，否则 CPU），权重由 anime-face-detector 自动下载并缓存。

```bash
# 批量识别 characters 目录下所有角色（底图 + 差分）
uv run arknightsavatar-detect

# 只识别指定角色
uv run arknightsavatar-detect --character avg_003_kalts_1

# 只处理前 20 个角色，输出到 stdout
uv run arknightsavatar-detect --limit 20 --output -

# 单张/多张图片快速测试
uv run arknightsavatar-detect path/to/a.png path/to/b.png

# 指定置信度阈值与设备
uv run arknightsavatar-detect --conf 0.3 --device auto
```

输出语义：批量模式 `characters.<角色名>.images.<文件名>` 为
`{image, image_size, detected, face_pos, confidence, error}`；
`detected=false` 表示未检出或低于阈值（此时 `face_pos`/`confidence` 为 null），
读图失败或检测异常时 `error` 非空。单图模式输出 `{generated_at, images, stats}`。
报告默认写入 `data/recognition/face_detect.json`，`--output -` 输出到 stdout。

## 高置信底图人脸识别（独立工具）

`arknightsavatar-detect-bases` 读取头像匹配报告（默认
`data/recognition/avatar_match.json`），筛选 match threshold **严格大于**
`--threshold`（默认 0.95）的底图，对每张底图用与 `arknightsavatar-detect`
相同的模型识别方案（anime-face-detector YOLOv3，复用 `arknightsavatar.detect.detect_top1`）
识别人脸，输出：

1. JSON 报告（默认 `data/recognition/face_detect_matched.json`）；
2. 标注结果图（默认 `data/recognition/face_detect_vis/`，扁平存放，文件名
   `<角色名>__<底图>.png`）：绿色框为 avatar 匹配范围并标注 `match <threshold>`，
   红色框为 YOLO 人脸框并标注 `yolo <confidence>`，未检出时标注 `no face`；
3. tqdm 进度条显示逐张处理进度（缺 tqdm 时回退为 `[序号/总数] 角色/底图` 文本）。

识别是**增量**的：输出报告（`--output`）里已存在对应 `角色/底图` 条目时直接复用
缓存，只对缺失的底图重新推理。若全部底图都已有结果（如 `build-model` 重复运行的
常见场景），完全不调用模型，进度条直接快进到 100% 并提示
`all N base(s) already detected ... skipping re-detection`；部分命中时提示
`reusing N cached detection(s), detecting M new base(s)`。`--force` 忽略缓存全量重跑。
结束后打印最终报告（filtered / detected / not_detected / errors / heads_detected）
与报告/标注图输出路径。

```bash
# 全量处理高置信底图
uv run arknightsavatar-detect-bases

# 冒烟：只处理前 3 张高置信底图，输出到临时路径
uv run arknightsavatar-detect-bases --limit 3 --output tmp/face_detect_matched.json --vis-dir tmp/face_detect_vis

# 指定匹配报告、阈值与识别置信度
uv run arknightsavatar-detect-bases --match data/recognition/avatar_match.json --threshold 0.95 --conf 0.3

# 只处理指定角色；设备默认 auto（有 CUDA 用 GPU，否则 CPU）
uv run arknightsavatar-detect-bases --character avg_003_kalts_1 --device auto

# 强制全量重识别（忽略 face_detect_matched.json 缓存）
uv run arknightsavatar-detect-bases --force
```

输出语义：顶层为 `{generated_at, match_file, characters_dir, threshold, stats,
characters}`；`characters.<角色>.bases.<底图>` 为
`{image, avatar, threshold, box, box_norm, image_size, detected, face_pos,
confidence, error}`，其中 `avatar/threshold/box/box_norm` 来自匹配报告，
`face_pos/confidence` 为模型识别结果（`face_pos` 为左上角 + 尺寸
`{x, y, w, h}`，原始像素），`error` 非空表示读图失败或检测异常；渲染成功时
另附 `vis_image`。`stats` 为 `{filtered, detected, not_detected, errors,
heads_detected}`，`filtered` 表示报告中实际涵盖的底图数（`--limit`/`--character`
后，含复用的缓存条目）。`heads_detected` 为头部检测命中的底图数。
该工具不接入 fetch/unpack 主流程；模型权重首次运行时由 anime-face-detector
自动下载并缓存（需联网）。

## 头像提取（独立工具）

`arknightsavatar-extract` 读取 `data/recognition/characters_classified.json`，对每个
角色的 base 按 **手动指定 > 头像匹配 > 模型推导** 三档确定头像裁切框，再对 base 与
全部 diff 提取 180×180 头像 PNG（`data/export/<角色>/<图片stem>.png`，越界补透明）。
提取是增量的：目标文件已存在则跳过（`--force` 强制重提）；face/head 识别结果缓存在
`data/recognition/face_head_detect.json`（主键 `"<角色>/<图片>"`，与手动文件键格式
一致），重复运行不重复推理；base 两两相似度与 diff 匹配决策（IoU、special、box、
method、confidence）缓存在 `data/recognition/avatar_extract_cache.json`（`--cache`
可换路径），重跑/`--force` 不再重复计算，缓存文件本身也是调试产物。

```bash
# 提取全部角色
uv run arknightsavatar-extract

# 只处理指定角色 / 前 N 个角色
uv run arknightsavatar-extract --character avg_003_kalts_1
uv run arknightsavatar-extract --limit 20

# 强制重提 / 强制重算匹配
uv run arknightsavatar-extract --force
uv run arknightsavatar-extract --force-match
```

裁切框三档优先级：

1. **手动**：`data/recognition/avatar_manual.json`（键 `"<角色>/<图片>"`，值
   `{"box": [x1, y1, x2, y2]}`，原图像素），命中即用；
2. **匹配**：复用 `data/recognition/avatar_match.json` 中 threshold **> 0.8** 的结果；
   报告缺失或加 `--force-match` 时内联调用匹配逻辑重算；
3. **推导**：对人脸置信度 **> 0.8** 且头部置信度 **> 0.7** 的图，用
   `data/recognition/derive/model.json` 由 face/head 检测框推导正方形裁切框。

diff 处理：与 base 同尺寸的 diff 直接使用；小尺寸 diff 按 `meta.json` 的
`face_groups`（`$n` 系列对应第 n 组）缩放至 `faceSize` 后贴到 `facePos` 完成组合；
组合结果的 A 通道取自 base，存在 `alpha.png` 时面部区域优先用其灰度作为 A 通道与
贴图蒙版。`alpha.png` 本身是提供 alpha 通道的贴图而非真实 diff，不参与提取、不计入
报告与统计。
组合后与 base 在 base 裁切框（脸部范围）内比较 **alpha 不透明掩码 IoU**（默认 0.85，
`--special-mask-iou`）：正常 diff 复用 base 裁切框；低于阈值视为**特殊 diff**
（动作/姿态变化导致头像位置偏移），对组合图重新做人脸/头部识别并按第三档推导框提取。

多 base 角色：先提取各 base 头像，两两比较相似度（透明合成灰度相关，默认
**> 0.98** 判重复），保留置信度更高者（手动 > 匹配 > 推导，同档比分数，再按文件名
稳定排序）；被丢弃的 base 及其 diff 不写新文件（已存在文件不删除），报告中标
`dropped`。

报告默认写入 `data/recognition/avatar_extract.json`（`--output -` 输出到 stdout），
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
`--force`、`--force-match`、`--device`（auto/cuda/cpu）。依赖 `uv sync`（detect 组）。
模型权重未缓存时首次运行需联网；本地已缓存且离线运行时设 `HF_HUB_OFFLINE=1`。

## 差分拼贴（独立工具）

`arknightsavatar-collage` 读取 `data/recognition/characters_classified.json` 与
`data/export/`，把每个角色的全部 diff 头像（180×180，已由 extract 组合并裁切）
按网格拼贴成一张 PNG，每角色一张。参考旧项目 `NpcData.draw_all_face`：黑底、
每格白底 + 头像（RGBA 蒙版）、左上角标注 diff 文件名，缺失/读取失败的头像画
`[x]` 占位。默认处理所有角色、默认 3 列。

```bash
# 全部角色各生成一张拼贴图
uv run arknightsavatar-collage

# 只处理指定角色 / 前 N 个角色
uv run arknightsavatar-collage --character avg_003_kalts_1
uv run arknightsavatar-collage --limit 20

# 指定列数 / 关闭标注 / 自定义字体
uv run arknightsavatar-collage --columns 6
uv run arknightsavatar-collage --no-label
uv run arknightsavatar-collage --font C:\Windows\Fonts\msyh.ttc
```

输出目录默认 `data/recognition/diff_collage/<角色>.png`（`-o` 可换）。只拼贴分类
报告中归属 base 的 diff（`alpha.png` 与 `unassigned` 不计入）；无 diff 或角色
目录缺失时跳过该角色。依赖 Pillow（`uv sync --extra unpack`）。

## PNG 转 WebP（独立工具）

`arknightsavatar-export-webp` 扫描 `data/export/` 下各角色文件夹内的 PNG
头像，逐张转换为 WebP 输出到 `data/export_webp/<角色>/`，
保持目录结构与透明通道（PNG 按 RGBA 解码后保存）。
增量转换：输出 `.webp` 已存在时跳过，加 `--force` 强制重转。

```bash
# 全部角色转换
uv run arknightsavatar-export-webp

# 只转指定角色（可重复）/ 前 N 个角色
uv run arknightsavatar-export-webp --character avg_003_kalts_1
uv run arknightsavatar-export-webp --limit 20

# 调整压缩参数 / 强制重转
uv run arknightsavatar-export-webp --quality 75 --method 6
uv run arknightsavatar-export-webp --force
```

可调参数：`--export-dir`（默认 `data/export`）、
`-o/--output-dir`（默认 `data/export_webp`）、`--quality`（0-100，默认 80）、
`--method`（0-6，默认 4，越大压缩越慢体积越小）、
`--character`（可重复）、`--limit`、`--force`。
依赖 Pillow（`uv sync --extra unpack`）。

## 生成 NPC 头像索引 JSON（独立工具）

`arknightsavatar-npc-json` 扫描 `data/export/` 下各角色文件夹内的 PNG 头像，
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
uv run arknightsavatar-npc-json

# 指定输入目录 / 输出到 stdout
uv run arknightsavatar-npc-json --export-dir data/export -o -
```

可调参数：`--export-dir`（默认 `data/export`）、
`-o/--output`（默认 `data/arknights_npc.json`，`-` 输出到 stdout）。

## 磁盘契约

```text
# 本地缓存（不进数据仓库）
data/raw/manifest.json          # {game_version, updated_at, files: {rel: {size, sha256, source}}}
data/raw/_failed.json           # 拉取失败清单
data/raw/<category>/<name>.ab   # 原始 AB 缓存
data/unpacked/_manifest.json    # {rel: source_sha256}，增量解包依据（解包簿记）
data/unpacked/_failed.json      # 解包失败清单（解包簿记）
data/unpacked/characters/<npc_id>/<sprite>.png + meta.json
data/unpacked/avatars/<sprite>.png + _meta/<bundle>.json   # 原始 avatar（数据仓库承载）

# 识别数据（数据仓库承载，文件名不带 _ 前缀）
data/recognition/characters_classified.json  # classify 产物
data/recognition/avatar_match.json           # match 产物
data/recognition/face_detect.json            # detect 产物
data/recognition/face_detect_matched.json    # detect-bases 产物（derive-model 输入）
data/recognition/face_head_detect.json       # extract 的 face/head 识别缓存
data/recognition/avatar_extract_cache.json   # base 相似度 / diff 决策缓存
data/recognition/avatar_extract.json         # extract 提取报告
data/recognition/avatar_manual.json          # 手动裁切框（可选）
data/recognition/avatar_skip.json            # 跳过清单（可选）
data/recognition/face_detect_vis/            # detect-bases 标注可视化
data/recognition/diff_collage/               # collage 差分拼贴
data/recognition/bases_sample/               # sample-bases 抽样
data/recognition/derive/model.json           # derive-model 产物（extract 第 3 档输入）

# 提取 avatar（数据仓库承载）
data/export/<npc_id>/<sprite>.png        # extract 产物：180×180 头像（按角色分文件夹）
data/export_webp/<npc_id>/<sprite>.webp  # export-webp 产物

# 统计列表（数据仓库承载）
data/stats/*.json                        # 统计报告（extract_stats、no_box_characters、run/produce_stats 等）
data/stats/characters.csv + .sha256      # manifest 工具产物：逐角色扁平统计（列定义见 data/schema/README.md）
data/stats/changes.json                  # manifest --since 产物：跨版本变更清单
data/arknights_npc.json                  # npc-json 产物：<npc_id> -> [[], [头像文件名], ["npc"]]

# 增量更新数据文件（数据仓库承载）
data/version.json                        # manifest --version-out 产物：顶层版本指针（game_version + 分类清单指纹）
data/changelog.ndjson                    # manifest 产物：追加式跨版本变更日志（一行一次发布）
data/schema/*.json + README.md           # version/manifest/changes/report 格式 Schema 与 CSV 列定义
data/{recognition,export,export_webp,stats}/manifest.json
                                         # manifest 产物：分类内容清单（{size, sha256}，清单自身不进清单）
```

`meta.json` 保留 textures 尺寸、sprites 列表、`face_groups`（facePos/faceSize 配对），
供步骤 3 使用。所有默认路径常量集中在 `src/arknightsavatar/paths.py`。

历史分析产物（旧 derive 实验、match 性能基准等）归档在 `docs/analysis/`，见其中
`README.md`。
