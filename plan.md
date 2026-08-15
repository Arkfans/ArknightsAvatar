# 增量更新友好数据文件 — 变更计划（修订版）

> 状态：**计划**（本会话不实现）。目标：为 ArknightsAvatar 增加对开发者增量更新友好的数据文件。
> 范围：A 版本头 + 指针文件 / B 内容清单 / C 跨版本变更清单 / D 扁平统计 + Schema / E 内容级比较。
> 关联目标：goal `a0217910-9baf-4f7e-973c-c5e03e3c4a4f`（多轮实现）。

## 0. 核查结论（相对上一版 plan.md 的修正，均已对照代码核实）

1. **注入方式收敛**：不再在 8 个模块的 `main()` 里手动 merge payload；改为 `reporting.write_report(payload, path)` 统一包装（注入头 + `indent=2` + `ensure_ascii=False` + `mkdir` + `-` 输出 stdout），各模块只替换 json.dump 调用点。`as_dict()` 一律不动（现有测试 `set(payload) == {...}` 精确断言不受影响）。
2. **game_version 来源修正**：`config.toml` 当前**没有** `game_version` 键（靠 `apk` 文件名推断，config.py L160）。正确优先级：`data/raw/manifest.json` 的 `game_version` → `load_config().game_version`（含 `ARKNIGHTSAVATAR_GAME_VERSION` env）→ `"unknown"`。**不给 8 个模块加 `--game-version` CLI 参数**（原方案的侵入面），统一由 `reporting.load_game_version()` 自动探测。
3. **D1 定案：工具合一**。只实现 `arknightsavatar-manifest`（含 `--version-out` 生成 version.json）；不单独提供 `arknightsavatar-version`。理由：version.json 依赖 manifest 指纹，A 不能独立于 B；两个入口徒增维护面。
4. **D2 定案：stats 有独立 manifest**（version.json 的 stats 指纹由它得出）。**D3 定案**：recognition manifest 默认排除 `face_detect_vis/`、`diff_collage/`、`bases_sample/`，**`derive/` 默认包含**（仅 3 个小 JSON，是 extract 输入、可复现性关键；当前 recognition 清单共 10 文件 = 7 个报告/缓存 JSON + derive 3 个）。
5. **幂等性缺陷（重要）**：manifest/version.json 若每次重写 `generated_at`，内容未变也会导致 sha256 变化 → sync-cache 每次产生提交。修正：生成时若「除 `generated_at` 外与旧文件内容一致」则保留旧 `generated_at` 且不重写文件；changelog 仅在确有变更时追加。与 sync-cache「无变化不提交」哲学一致。
6. **`--since` 逻辑缺陷**：旧 version.json 只含 manifest 的 **sha256**，无法定位清单文件。修正：version.json 的 `categories.*` 条目增加 `"path"` 字段（数据仓库内相对路径，如 `"export/manifest.json"`），`--since` 接受旧 version.json（按 path 定位）或单分类旧 manifest 路径。
7. **E 默认策略修正**：默认不是「全量 sha256」，而是「manifest 加速」——两侧（本地 + 数据仓库工作副本）均有该分类 manifest 时按 `{size, sha256}` 比对（零哈希）；未被 manifest 覆盖的文件（如可视化目录、manifest 自身）回退 size+mtime（现状行为）。`--content-hash` 全量哈希（无 manifest 时的正确性模式）；`--size-mtime` 完全恢复旧行为。"镜像后顺带更新 manifest"澄清：manifest 作为分类内普通文件随 `mirror_dir` 复制，**排序保证 manifest 最后复制**（中断时消费者看到旧指纹不会误判），无需额外逻辑。
8. **文件清单补漏**：`pyproject.toml` 需注册 `arknightsavatar-manifest` 入口；`.gitignore` 需新增 `data/version.json`、`data/changelog.ndjson`、`data/schema/`（现状不被忽略，会误入主仓库）；`config.py` 的 `DEFAULT_DATA_REPO_CATEGORIES` 默认兜底表（L22-29）需与 `data_repo.yaml` 同步新增 3 个分类条目。
9. **CSV 语义修正**：`avatar_extract.json` 每条目含 `status/method/special/avatar_file`（`method`/`special` 仅在非 None 时写出，增量运行全 skipped 时为空）——`base_method_*`、`diff_special` 列反映**最后一次 extract 的决策**，`has_avatar` 反映磁盘状态，README 注明。`diff_count` 以 extract 报告 diffs 键为准（已排除 alpha diff，与 `diff_*` 计数口径一致），不用 classified 的 diff 列表。补充 `base_failed`/`diff_failed` 列（STATS_KEYS 与 extract_stats.json 中均存在）。
10. **derive_model.py 修正**：产出 3 个文件均加头——`model.json`（dict L293-305）、`derive_coords.json`（payload L311-318）、`stats.json`（L322-324，纯 stats dict，当前无 `generated_at`，由 write_report 补齐）。缓存文件（`avatar_extract_cache.json`、`face_head_detect.json`、extract.py L359/413 的 payload）**不加头**（非报告）。
11. **run.py/produce.py 编排不改**：STEPS 元组保持不变（避免连带改动 produce 切片与 `--from/--until` 契约）；manifest/version 作为独立命令在 `npc-json` 之后执行，README 写明发布流程 `run → manifest --category all --version-out → sync-cache`。
12. **行号/数值核查**：原 plan 的写入点行号全部属实（classify L287-295、match L799-806、detect L454-498、detect_bases L599-607、extract L1265-1273、derive_model L294-324、run L268-274、produce L92-98、sync_cache `_same_file` L87-90）；`data/export` 1404 目录 / 12429 文件、`export_webp` 12429 文件、测试 25 个文件均属实；报告确实无版本字段。附带发现：`avatar_extract.json` 内既有路径字段反斜杠/正斜杠混用（`data\\recognition\\...` vs `data/recognition/...`），为既有问题，仅备注不处理。
13. **stats 默认排除运行记录（实现期追加）**：`run_stats.json`/`produce_stats.json` 每次 run/produce 必变、src 中无任何读取方（纯人工排查用）。默认加入 stats 清单排除项后，stats 指纹只在真实数据（characters.csv、changes.json 等）变化时更新，发布不再因运行记录产生空提交。

## 1. 背景与现状

### 1.1 当前磁盘契约（`README.md`「磁盘契约」+ `src/arknightsavatar/paths.py`）

```text
data/raw/manifest.json                        # {game_version, updated_at, files: {rel: {size, sha256, source}}}
data/raw/<category>/<name>.ab                 # 原始 AB 缓存（本地，不进数据仓库）
data/unpacked/_manifest.json                  # {rel: source_sha256}（解包簿记，本地）
data/unpacked/characters/<npc_id>/<sprite>.png + meta.json
data/unpacked/avatars/<sprite>.png + _meta/<bundle>.json
data/recognition/{characters_classified,avatar_match,face_detect,face_detect_matched,face_head_detect,avatar_extract_cache,avatar_extract,avatar_manual,avatar_skip}.json
data/recognition/{face_detect_vis,diff_collage,bases_sample}/   # 可视化/抽样
data/recognition/derive/{model.json,derive_coords.json,stats.json}
data/export/<npc_id>/<sprite>.png             # 180×180 头像（1404 角色 / 12429 文件）
data/export_webp/<npc_id>/<sprite>.webp       # WebP 版（12429 文件）
data/stats/{extract_stats.json,run_stats.json,no_box_characters.{csv,json}}
data/arknights_npc.json                       # npc_id -> [[], [头像列表], ["npc"]]
```

### 1.2 对增量更新有利的现状

- `data/raw/manifest.json`：逐文件 `{size, sha256, source}` + `game_version`（当前 `arknights-hg-2761`）。
- `data/unpacked/_manifest.json`：按源 sha256 增量解包。
- 识别报告为确定性字典序 JSON；`sync-cache` 走 git 增量提交；extract/export-webp/match 本身有增量缓存。

### 1.3 缺口（本计划解决）

1. 识别/导出/统计报告均无版本字段（只有 `generated_at`，已核实）。
2. `data/export/`、`data/export_webp/`、`data/recognition/` 无内容清单，消费者无法在下载前判断变化。
3. 无顶层版本指针；无跨版本变更清单；`sync-cache._same_file` 用 `size + mtime_ns` 非内容级判断。
4. 无扁平易 diff 的逐角色统计（CSV）与格式 Schema 文档。

## 2. 总体设计

- **新增 1 个 CLI 工具**：`arknightsavatar-manifest`（B+C+D 生成入口 + A 的 version.json，含 `--since` 变更对比、`--characters-csv`、`--version-out`）。注册进 `cli.py` TOOLS 与 `pyproject.toml [project.scripts]`。
- **新增共享模块** `src/arknightsavatar/reporting.py`：`SCHEMA_VERSION` 常量、`report_header()`、`load_game_version()`、`write_report()`、manifest/changes 读写、CSV 输出、sha256 工具、幂等写入（内容未变不重写）。
- **版本头**：所有报告顶层注入 `{schema_version, pipeline_version, game_version, generated_at}`（`generated_at` 保留原键语义，其余为新增键；只增键不改键，向后兼容）。
- **数据仓库新增承载**（`data_repo.yaml` + `config.py` 默认表同步）：`data/version.json` → `version.json`、`data/changelog.ndjson` → `changelog.ndjson`、`data/schema/` → `schema/`；各 manifest 位于分类目录内随现有目录分类自动同步。
- 版本号来源：`game_version` = `reporting.load_game_version()`（raw manifest → config → `"unknown"`）；`pipeline_version` = `arknightsavatar.__version__`（0.1.0）；`schema_version` = 常量 `1`。

## 3. 工作项 A：版本头

### 3.1 `reporting.py` 核心 API

```python
SCHEMA_VERSION = 1

def report_header(game_version: str | None = None, generated_at: str | None = None) -> dict:
    """{schema_version, pipeline_version, game_version, generated_at}"""

def load_game_version() -> str:
    """data/raw/manifest.json 的 game_version → load_config().game_version → 'unknown'"""

def write_report(payload: dict, output: str | Path, *, game_version: str | None = None) -> None:
    """inject_header(payload) 后原子写入（tmp + os.replace）；output '-' 输出 stdout。"""

def inject_header(payload: dict, *, game_version: str | None = None) -> dict:
    """header 在前、payload 在后合并（payload 的 generated_at 保留）。"""
```

### 3.2 接入点（各模块把 json.dump 调用换成 `write_report`，`as_dict()` 不动）

| 模块 | 替换点（已核实行号） | 说明 |
|---|---|---|
| `classify.py` | L287-295 | 顶层 |
| `match.py` | L799-806 | 顶层 |
| `detect.py` | L491-499（两分支共用） | 顶层 |
| `detect_bases.py` | L599-607 | 顶层 |
| `extract.py` | L1265-1273 | 顶层 |
| `derive_model.py` | L306-308 / L319-321 / L322-324 | model.json、derive_coords.json、stats.json 均加头 |
| `run.py` | `write_stats`（L247-251，produce.py 共用） | 单点收敛 |
| `produce.py` | 经 `write_stats` | 同上 |

- 缓存文件（`face_head_detect.json`、`avatar_extract_cache.json`、extract.py L359/413 payload）**不加头**（注释说明）。
- `npc_json.py` 输出旧项目格式，**不加头**（注释说明）；`export_webp.py` 无报告 JSON，不涉及。
- `paths.py` 新增：`VERSION_JSON`、`CHANGELOG`、`SCHEMA_DIR`、`EXPORT_MANIFEST`、`EXPORT_WEBP_MANIFEST`、`RECOGNITION_MANIFEST`、`STATS_MANIFEST`、`CHARACTERS_CSV`。

## 4. 工作项 B：内容清单 manifest

### 4.1 统一清单格式（与 `raw/manifest.json` 对齐；键字典序；相对路径一律 `/` 分隔）

```json
{
  "schema_version": 1,
  "pipeline_version": "0.1.0",
  "game_version": "arknights-hg-2761",
  "generated_at": "...",
  "category": "export",
  "files": {"avg_003_kalts_1/1$1.png": {"size": 12345, "sha256": "..."}}
}
```

### 4.2 清单位置与扫描根

| 分类 | 清单文件 | 扫描根与排除 |
|---|---|---|
| 识别数据 | `data/recognition/manifest.json` | `data/recognition/`，默认排除 `face_detect_vis/`、`diff_collage/`、`bases_sample/`；**包含 `derive/`** |
| 提取头像 | `data/export/manifest.json` | `data/export/` |
| WebP | `data/export_webp/manifest.json` | `data/export_webp/` |
| 统计 | `data/stats/manifest.json` | `data/stats/`（含 extract_stats、no_box_characters.*、characters.csv、changes.json；**默认排除 `run_stats.json`、`produce_stats.json`**——每次运行必变且无消费者读取，排除后 stats 指纹只在真实数据变化时更新） |

- 清单文件自排除；`--exclude PATTERN`（fnmatch，posix 相对路径）可覆盖默认排除。
- 增量：读旧清单，仅对 `size` 与旧记录不同的文件重算 sha256（size 相同信任旧指纹，`--force` 全量重算；README 注明该取舍）。
- **幂等**：新 files 与旧 files 相同 → 不重写文件（保留旧 `generated_at`、旧 mtime）。原子写（tmp + `os.replace`）。

### 4.3 工具 `arknightsavatar-manifest`

```
usage: arknightsavatar-manifest [--category export|export_webp|recognition|stats|all]
                                [--output PATH] [-o -] [--exclude PATTERN] [--force]
                                [--version-out PATH] [--no-categories]
                                [--since OLD_VERSION_JSON|OLD_MANIFEST] [--changes-out PATH]
                                [--append-changelog PATH] [--characters-csv PATH]
```

职责：扫描分类生成/更新 manifest；`--version-out` 生成 version.json（默认 `data/version.json`）；`--since` 触发 C；`--characters-csv` 触发 D-1。`--version-out` 时自动生成全部四分类 manifest（含 stats），随后计算各清单 sha256 与 `arknights_npc.json` 的 sha256。

### 4.4 `data/version.json`（顶层指针）

```json
{
  "schema_version": 1,
  "pipeline_version": "0.1.0",
  "game_version": "arknights-hg-2761",
  "generated_at": "...",
  "categories": {
    "recognition":   {"path": "recognition/manifest.json",  "sha256": "...", "files": 10},
    "export":        {"path": "export/manifest.json",       "sha256": "...", "files": 12429},
    "export_webp":   {"path": "export_webp/manifest.json",  "sha256": "...", "files": 12429},
    "stats":         {"path": "stats/manifest.json",        "sha256": "...", "files": 7},
    "arknights_npc.json": {"sha256": "..."}
  }
}
```

- `path` 为数据仓库内相对路径（消费者直接用于下载定位；也是 `--since` 定位依据）。
- 幂等规则同 4.2（内容未变不重写）。`--no-categories` 调试模式跳过 categories 计算。

## 5. 工作项 C：跨版本变更清单

### 5.1 `data/stats/changes.json`

```json
{
  "schema_version": 1,
  "generated_at": "...",
  "from": {"game_version": "...", "generated_at": "..."},
  "to":   {"game_version": "...", "generated_at": "..."},
  "categories": {
    "export": {
      "added": ["..."], "removed": ["..."], "modified": ["..."],
      "counts": {"added": 12, "removed": 3, "modified": 40, "unchanged": 12374}
    },
    "export_webp": {"..."}, "recognition": {"..."}
  }
}
```

- 判定：`added` 仅新清单有；`removed` 仅旧清单有；`modified` 两清单都有但 sha256 不同。**stats 分类不参与对比**（每次运行都会变、无对比价值，README 注明）。
- `--since` 接受：旧 version.json（按 `categories.*.path` 定位各分类清单）或单分类旧 manifest（只对比该分类）。无 `--since` 时跳过并提示（非错误）。

### 5.2 `data/changelog.ndjson`（追加式）

- 每行 `{"generated_at","game_version","from_version","counts":{...}}`；**仅在任一分类有 added/removed/modified > 0 时追加**（否则零变更行无意义）；文件不存在则创建；断点续读以**行数**为准（`generated_at` 可能相同）。

## 6. 工作项 D：`characters.csv` + JSON Schema

### 6.1 `data/stats/characters.csv`（`--characters-csv`，默认该路径）

```csv
npc_id,base_count,diff_count,base_ok,base_skipped,base_dropped,base_no_box,base_failed,diff_ok,diff_skipped,diff_dropped,diff_no_box,diff_failed,diff_special,base_method_match,base_method_derive,has_avatar
```

- 每角色一行、字典序；数据源 `avatar_extract.json`（主）+ `characters_classified.json`（extract 报告缺失的角色补零行）。`base_count`/`diff_count` 取 extract 报告 bases/diffs 键数（diff 口径已排除 alpha diff）；`*_method_*`/`diff_special` 仅统计最近一次 extract 记录的方法/特殊标记（增量 skipped 条目无记录，为 0）；`has_avatar` = 存在任一 `avatar_file` 条目。
- 附带输出 `characters.csv.sha256`（便于 diff 校验）。

### 6.2 `data/schema/`（手工维护的静态 JSON Schema，不引入生成器依赖）

| 文件 | 对应 |
|---|---|
| `data/schema/version.json` | `data/version.json` |
| `data/schema/manifest.json` | 各分类 manifest |
| `data/schema/changes.json` | `data/stats/changes.json` |
| `data/schema/report.json` | 带版本头的报告公共结构（`additionalProperties: true`，只约束头字段） |
| `data/schema/README.md` | CSV 列定义与维护约定 |

## 7. 工作项 E：`sync-cache` 内容级比较

### 7.1 变更点（`src/arknightsavatar/sync_cache.py`）

- 新参数：`--content-hash`（全量 sha256 比较）、`--size-mtime`（完全旧行为）、`--manifest`（显式开关 manifest 加速；默认自动探测）。
- 默认策略（`_same_file` 扩展）：
  1. 本地与工作副本该分类均有 manifest 且文件被两清单覆盖 → 按 `{size, sha256}` 比对（零哈希）；
  2. 未被覆盖（可视化目录、manifest 自身、清单外的零散文件）→ 现状 size+mtime；
  3. `--content-hash` 强制全量哈希；`--size-mtime` 忽略 manifest。
- `mirror_dir` 复制顺序：**manifest.json 排最后复制**（中断时消费者读到的旧清单仍与工作副本内容自洽）。无其它"顺带更新"逻辑——manifest 作为普通文件随镜像复制。

### 7.2 测试

`tests/test_sync_cache.py` 增补：manifest 覆盖时内容相同 mtime 不同 → 不复制；内容不同 → 复制；清单外文件回退 size+mtime；`--content-hash`/`--size-mtime` 行为。现有测试（基于 size+mtime 语义与 copy2 保 mtime）不受影响。

## 8. 依赖与执行顺序

```
阶段 1（A 基础）: reporting.py（头/加载/写/幂等）→ 8 个模块接入 → 新增测试 tests/test_reporting.py
阶段 2（B 核心）: manifest_tool.py → paths.py 常量 → 四分类清单 + version.json（--version-out）
阶段 3（C 变更）: changes.json 对比 → changelog.ndjson 追加
阶段 4（D 易用）: characters.csv → data/schema/*.json
阶段 5（E 同步）: sync-cache manifest 加速 + --content-hash/--size-mtime
阶段 6（收尾）:  pyproject.toml 入口、cli.py 注册、.gitignore、data_repo.yaml + config.py 默认分类、
                 README（磁盘契约/命令/分类表/发布流程/消费者增量拉取指南）→ 全量 pytest
```

- 阶段 1-4 各自独立可交付；阶段 5 依赖 B（manifest 加速为可选优化，`--content-hash` 可先行）。
- 每阶段结束 `uv run pytest`（现有 25 个测试文件 + 新增）。

## 9. 涉及文件清单

### 新增
- `src/arknightsavatar/reporting.py`、`src/arknightsavatar/manifest_tool.py`
- `data/schema/{version,manifest,changes,report}.json` + `data/schema/README.md`
- `tests/test_reporting.py`、`tests/test_manifest_tool.py`
- 产物：`data/version.json`、`data/changelog.ndjson`、`data/{recognition,export,export_webp,stats}/manifest.json`、`data/stats/{changes.json,characters.csv,characters.csv.sha256}`

### 修改
- `src/arknightsavatar/paths.py`（8 个常量）
- `src/arknightsavatar/{classify,match,detect,detect_bases,extract,derive_model,run,produce}.py`（write_report 接入）
- `src/arknightsavatar/sync_cache.py`（内容级比较）
- `src/arknightsavatar/cli.py`（TOOLS 注册 manifest）+ `pyproject.toml`（`[project.scripts]` 加 `arknightsavatar-manifest`）
- `src/arknightsavatar/config.py`（`DEFAULT_DATA_REPO_CATEGORIES` 增 3 条）+ `data_repo.yaml`（增 3 条：version.json、changelog.ndjson、schema）
- `.gitignore`（增 `data/version.json`、`data/changelog.ndjson`、`data/schema/`）
- `README.md`（磁盘契约、命令表、分类映射表、版本头说明、发布流程、消费者增量拉取指南）
- `tests/test_sync_cache.py`（增补）；`config.example.toml`（可选：`game_version` 注释示例已存在，无需改）

### 不改
- `npc_json.py`、`export_webp.py`；各报告既有键/排序/`as_dict()` 结构；缓存文件 payload；`run.py`/`produce.py` 的 STEPS 编排。

## 10. 验收标准

1. 每个识别报告顶层含 `{schema_version, pipeline_version, game_version, generated_at}`，旧字段与排序不变（`as_dict()` 输出不变）。
2. `data/version.json` 存在；`categories.*.sha256` 与对应清单文件实际一致；**内容未变时重复生成不重写文件、不改变 `generated_at`**（幂等，sync-cache 不产生空提交）。
3. 四个 manifest：文件数、sha256 与磁盘一致；键字典序；增量运行只重算 size 变化的文件；清单文件自排除。
4. `changes.json` 对比两个不同版本清单时 added/removed/modified 正确；相同清单时 counts 全 0；无 `--since` 时跳过并提示。
5. `changelog.ndjson` 追加不覆盖历史行；零变更时不追加。
6. `characters.csv` 列与 6.1 一致、每角色一行、字典序；extract 报告缺失角色补零行。
7. `sync-cache`：manifest 覆盖时内容相同 mtime 不同不复制、内容不同必复制；`--content-hash` 无 manifest 时全量哈希正确；`--size-mtime` 恢复旧行为。
8. `uv run pytest` 全绿；README 磁盘契约与实现一致。
9. 新产物（version.json、changelog.ndjson、schema/、各 manifest、changes.json、characters.csv）可被 sync-cache 正常镜像进数据仓库（新分类映射生效，主仓库 git 不跟踪）。

## 11. 风险与备注

- **全量 sha256 性能**：首轮 2.5 万+ 文件哈希分钟级，靠增量 manifest（size 快路径）与幂等写入缓解；`--force` 仅调试用。
- **size 相同内容变化的漏检**：增量 manifest 与 E 的默认策略都依赖 size 快路径；PNG/WebP 内容变化几乎必变 size，`--force`/`--content-hash` 作逃生舱，README 注明取舍。
- **Windows 路径**：manifest/changes/version 内一律 `/`；报告内既有反斜杠路径字段为存量问题，本次不处理（备注）。
- **版本头向后兼容**：只增键不改键；`schema_version=1` 期间无破坏；旧消费者忽略新键。
- **`game_version` 缺失**（无 raw manifest / 无 config / 无 apk 文件名）：回退 `"unknown"`，version.json 仍生成。
- **stats 指纹稳定**：`run_stats.json`/`produce_stats.json` 每次运行都变且无消费者读取，
  已默认排除出 stats 清单——stats 指纹只在真实数据变化（characters.csv/changes.json 等）时更新，
  不再产生空提交（实现时追加的决策，见 0 节第 13 条）。
- **识别清单含缓存文件**：`avatar_extract_cache.json`（4MB）等随 extract 变化会触发 recognition 指纹更新，属正确行为。
- **可视化目录仍进数据仓库**：manifest 排除只影响消费者更新判定，`mirror_dir` 仍按现状整目录镜像（不做分类级 exclude，避免扩 scope）。
