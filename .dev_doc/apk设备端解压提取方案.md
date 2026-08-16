# APK 设备端解压、仅拉取所需 AB 资源方案

> 状态：已实现并真机验证（2026-08-16）
> 日期：2026-08-16（按项目环境时间）

## 1. 背景与目标

当前 `pull` 流程 = `pull-apk`（整包下载到本地存档）→ `fetch`。其中：

- `pull-apk`（`src/arknightsavatar/pull_apk.py`）把设备上整个 base.apk（实测 **2011.9 MB**）经 ADB sync 拉到本地，无版本级跳过——同版本重复运行也会完整重传；
- `fetch --source apk`（`src/arknightsavatar/sources/apk_adb.py`）虽然已在设备端 `unzip -p` 按条目解压、只传所需字节，但存在三个效率问题：
  1. 逐条 shell 往返：每个文件一次 `unzip -p` 命令；
  2. shell 流传输只有 sync 协议约一半吞吐（实测 5.5 vs 10.7 MB/s）；
  3. 整文件驻留内存（`device.shell(decode=False)` 全量读入再写盘），无进度、无流式。

目标：在 ADB 设备端对 APK 解压，仅提取所需的 `.ab` 条目并**打包成 tar 后单次 sync 拉回**，最大化减少传输字节与命令往返。

## 2. 实测数据（设备 TAS-AL00 / Android 12 / toybox 0.8.4，192.168.2.191:36888）

| 检查项 | 结果 | 结论 |
|---|---|---|
| unzip（`-l`/`-p`） | 实测正常（201 条目列表、解压输出成功） | 可用 |
| tar（toybox） | `tar -cf` + `tar -tf` 实测通过 | 可设备端打包 |
| 设备端解压速度 | 最大条目 6.8 MB → `/dev/null` 0.11 s ≈ **63.9 MB/s** | 不是瓶颈 |
| sync pull 吞吐 | 32 MB 实测 **10.7 MB/s**（WiFi TCP adb） | 传输瓶颈 |
| `unzip -p` shell 流吞吐 | 实测 **5.5 MB/s** | 仅为 sync 一半 |

体积数据：

- base.apk = **2011.9 MB**（单包，无 split）；
- 所需条目 = **302.8 MB**（`spritepack` 201 个文件）；
- ⚠️ `avg/characters` = **0 个文件**：该版本 APK 中不存在 `assets/AB/Android/avg/` 目录，L2D 立绘全部走下载目录 `files/Bundles`。**apk 源在此设备/版本只覆盖 avatars 类别**，characters 仍需 `--source adb`。

时间对比（全量拉 avatars 302.8 MB）：

| 方案 | 耗时构成 | 估算 |
|---|---|---|
| 整包拉回（现状 pull-apk） | 2011.9 MB @ 10.7 MB/s | ~188 s |
| 逐条 unzip -p（当前 apk 源） | 302.8 MB @ 5.5 MB/s + 201 次往返 | ~60–150 s |
| **设备端解压 + tar 打包 + 单次 sync pull（本方案）** | 302.8 MB @ 10.7 MB/s + 设备端解压 ~5 s | **~35–40 s** |

收益：一次性全量省 **1709 MB（85%）** 传输量、约 **4–5 倍**提速；增量（manifest 跳过已有）场景近乎零传输，对比同版本重复跑 pull-apk（每次 2 GB）为数量级差距。

## 3. 方案设计：`ApkAdbSource.fetch_many` 设备端打包

复用 `AdbSource._fetch_category_pack`（`src/arknightsavatar/sources/adb.py`）的成熟模式，为 `ApkAdbSource` 新增批处理路径：

```
对每个类别：
  1. 用 _write_device_listing 逻辑把所需文件名写入 /data/local/tmp/<pid>.list
     （按 _LIST_CHUNK=100 分批 printf，控制单条命令长度）
  2. 设备端逐条解压到临时目录，再 tar 打包：
     tar -cf /data/local/tmp/arknights_ab_<pid>_<cat>.tar -C <apk临时目录> -T <list>
     或：for f in $(cat list); do unzip -p <apk> "<entry>" > tmp/$f; done（shlex.quote 处理）
  3. 单次 device.pull(pack, local, progress_callback=..., read_timeout_s=60)
  4. 本地 tarfile 解包，逐文件校验成员存在 + size 与 unzip -l 一致
  5. 失败条目（缺成员/size 不符/打包失败/拉取失败）回退逐条 fetch_to，绝不丢数据
  6. finally 清理设备端临时文件（rm -f）与本地临时 tar
```

要点：

- **打包粒度**：条目 = zip 内单个 `.ab` 文件（`assets/AB/Android/spritepack/*.ab`、`avg/characters/*.ab`），`list_files` 已有精确的 `(rel, size)` 清单，直接用；
- **entry 归属**：`_entry_apk` 已记录每个 rel 所在的 APK（base 优先去重），打包时按 APK 分组分别 `unzip -p`；
- **压缩**：`.ab` 内部已是 LZ4/LZMA 压缩的 AssetBundle，设备端 tar 不追加 gzip（`-cf` 即可）；
- **进度**：`_PullProgress` 直接复用，展示单包拉取进度；
- **内存**：走 `device.pull` 落盘，避免整文件驻留内存。

## 4. 风险与边界

1. **characters=0**：此设备 APK 无 `avg/` 目录，方案只解决 avatars；若主要目标是 L2D 立绘，收益有限（需改用/兼用 `--source adb`）；
2. **toybox tar 差异**：Android 上为 toybox 实现，需用基础 `-cf`/`-tf`，避免 GNU 特有 flag；实测创建/列出通过；
3. **`unzip -p` 逐条在设备端解压**：302.8 MB @ 63.9 MB/s ≈ 5 s，可接受；若条目极多（数千），解压+打包命令需分批（沿用 `_LIST_CHUNK` 思路）避免单条 shell 命令过长；
4. **带宽上限**：本设备 WiFi TCP adb 实测 10.7 MB/s，方案收益上限受此约束；USB adb 可再快 3–4 倍；
5. **完整性**：沿用 size 校验 + 本地 sha256 落盘校验，缺失/变化条目回退逐条拉取。

## 5. 实施步骤（待办）

- [x] `ApkAdbSource` 新增 `fetch_many`：设备端 list 写入 → 逐条 `unzip -p` 到临时目录 → `tar -cf` → 单次 `device.pull` → 本地 `tarfile` 解包校验 → 失败回退
  - 2026-08-16 已实现：`src/arknightsavatar/sources/apk_adb.py`（`_fetch_category_pack` + `_unzip_entries_on_device`）；`adb.py` 的 list 写入 / tar / 解包 / 清理原语提升为模块级函数供两个源复用；`--no-batch` 现对 apk 源同样生效（`fetch.py`）；
  - 设备端解压用 `while IFS= read -r e; do unzip -p <apk> "$e" > <tmpdir>/"${e##*/}"; done < <entries.list>`（仅依赖已实测的 `unzip -p` 与 mksh 参数展开，命令长度与条目数无关，无需分批 unzip）；失败条目经本地 size 校验回退；
- [x] 单元测试：mock `device.shell`/`device.pull`，覆盖去重归属、size 校验、回退路径（`tests/test_sources.py` 新增 9 例，`tests/test_adb.py` 19 例全绿）
- [x] 真机端到端：本设备（TAS-AL00）跑 `fetch --source apk --category avatars`，对比改进前后耗时
  - 2026-08-16 实测：全量 201 文件 / 302.8 MB **38.2 s**（命中目标区间 ~35–40 s；对比整包 188 s、逐条 unzip -p 60–150 s）；增量重跑 **1.3 s**（201/201 manifest 跳过，近零传输）；2 文件冒烟测试 0.55 s，设备端 `while read` + `${e##*/}` 解压循环实测可用
- [ ] （可选）`pull_apk.py` 增加版本级跳过：`dest.exists()` 且版本一致时不再下载整包，或先 `ls -l` 比对远端 size

## 6. 附录

- 探测脚本：`/tmp/ak_probe.py`（连接配置设备，输出工具可用性、APK/条目体积、三类吞吐实测）
- 相关代码：`src/arknightsavatar/sources/apk_adb.py`、`src/arknightsavatar/sources/adb.py`（`_fetch_category_pack`/`_PullProgress`）、`src/arknightsavatar/pull_apk.py`
- 设备命令能力检测（2026-08-16 新增）：`src/arknightsavatar/device_caps.py` 独立模块，功能性探测设备端
  `tar -cf -C -T` / `tar -czf` / `unzip -l` / `unzip -p` 等批量拉取依赖命令（探测文件放
  `/data/local/tmp`，失败/超时一律视为不支持）；`fetch` 的 adb/apk 源启动时自动探测，不支持时
  回退逐文件/逐条拉取并打印 warning；CLI：`arknightsavatar device-caps`（`arknightsavatar-device-caps`）。
