# 历史分析产物归档

本目录保存早前针对头像提取各阶段的离线实验脚本与结果快照，迁移自仓库
根目录与 `data/` 下的独立分析文件夹。它们**不接入**主程序
（`src/arknightsavatar/`），仅作历史参考：

| 归档目录 | 原位置 | 内容 |
| --- | --- | --- |
| `avatar_derive/` | `data/avatar_derive/` | face/head → 裁切框推导（阈值 0.7 版，最小二乘拟合 + 精度统计） |
| `avatar_derive_08/` | `data/avatar_derive_08/` | 同上（阈值 0.8 版，精度略优；其模型曾为 extract 第 3 档输入） |
| `avatar_derive_compare_3way/` | `data/avatar_derive_compare_3way/` | 手动 / match / derive 三方裁切框对比 |
| `bench_coarse_increase/` + `bench_coarse_increase.py` | 仓库根目录 + `data/bench_coarse_increase/` | match 粗搜步长性能基准（`PERF_COARSE_INCREASE.md` 为结论） |
| `unmatched_face_detect/` | `data/unmatched_face_detect/` | 对未匹配底图的人脸识别排查 |

## 与现行代码的关系

- **derive 模型的现行实现**已移植进主包：`src/arknightsavatar/derive_model.py`
  （CLI：`uv run arknightsavatar-derive-model`，即统一入口的
  `arknightsavatar derive-model`）。输入默认
  `data/recognition/face_detect_matched.json`，输出
  `data/recognition/derive/`（`model.json` / `derive_coords.json` /
  `stats.json` / `compare/`）。
- extract（第 3 档「模型推导」裁切框）默认读取
  `data/recognition/derive/model.json`；该文件由数据仓库承载
  （`arknightsavatar sync-cache --pull --restore` 可取回），不随主仓库跟踪。
- 本目录脚本的默认输入路径（如 `../unpacked/_face_detect_matched.json`）
  仍指向旧布局，按需手动改路径；其中对 `arknightsavatar` 包的 import 已更新。
- 现行统计列表统一放在 `data/stats/`（由独立 GitHub 数据仓库承载），
  对应旧文件：`docs/extract_stats.json` → `data/stats/extract_stats.json`、
  `docs/no_box_characters.{json,csv}` → `data/stats/no_box_characters.{json,csv}`。
