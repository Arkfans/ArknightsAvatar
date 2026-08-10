# 没有对应 avatar 底图的人脸/头部识别 + 07/08 face 推导

对**没有对应 avatar** 的底图做人脸（YOLOv3 top-1）与头部（imgutils top-1）
识别，跳过 face/head 置信度低于 0.7 的底图并继续抽取，直到收集满 100 张；
对通过者用 `avatar_derive`（07）与 `avatar_derive_08`（08）两个推导模型，
由 face/head 检测框推导正方形裁切框并绘制。

## 底图池（没有对应 avatar 的 base，来自 `_characters_classified.json`）

共 **1377 张**：

- **no_avatar_unreported（1360 张）**：完全未出现在 `_avatar_match.json`
  中的角色底图，绝大多数为 `avg_npc_*` 等 NPC 角色（命名不符合
  `avg_\d+_` / `char_\d+`，从未参与头像匹配），例如
  `avg_npc_1561_1$1.png`、`avg_npc_1614_1$1.png`
- **no_avatar（17 张）**：匹配报告中 `no_avatar` 角色（无候选头像）的底图

## 识别与筛选

- 人脸：anime-face-detector YOLOv3 最高置信度检测框（取原始置信度）
- 头部：dghs-imgutils `detect_heads` 最高置信度检测框（取原始置信度）
- 过滤：face 与 head 置信度均 **≥ 0.7** 才通过；任一低于 0.7 则跳过，
  按固定随机顺序（种子 42）继续取下一个，直到 100 张

## 07/08 face 推导

两个模型的系数分别来自 `data/avatar_derive/model.json`（07，min-conf 0.7）
与 `data/avatar_derive_08/model.json`（08，min-conf 0.8）。推导公式：

```
[cx, cy, s] = W @ [fx, fy, fw, fh, hx, hy, hw, hh, 1]
derived_box = [cx - s/2, cy - s/2, cx + s/2, cy + s/2]
```

其中 f/h 为 face/head 检测框中心与宽高。无 match 框可对照，推导框仅反映
07/08 模型在该底图上的预测。

## 图例（每张标注图顶部信息栏）

- 第一行：角色 / 底图文件名
- 第二行：avatar 匹配状态（no avatar candidate / not in match report (NPC)）
- 黄色色块：`face conf >= 0.7 (实测置信度)`，图中黄框为人脸框
- 青色色块：`head conf >= 0.7 (实测置信度)`，图中青框为头部框
- 红色色块：`derive07 box`（红虚线）
- 蓝色色块：`derive08 box`（蓝虚线）

## 结果（100 张抽样）

| 项 | 值 |
| --- | --- |
| 底图池 | 1377 |
| 实际处理（打乱顺序） | 148 |
| 通过（face、head ≥ 0.7） | **100** |
| 跳过（face 或 head < 0.7） | 48 |
| 通过来源 | avg_npc_* 等未报告角色 99 / no_avatar 1 |

## 文件说明

- `compare/`：100 张通过阈值底图的标注 PNG（含 07/08 推导框）
- `report.json`：全部处理记录（含 face/head 框、置信度与两个推导框）
- `summary.csv`：每张底图的置信度与推导框
- `stats.json`：汇总统计
- `detect_unmatched.py`：生成脚本（可重新生成）

## 使用

```bash
# 默认：face/head 阈值 0.7、100 张、种子 42
.venv\Scripts\python.exe data\unmatched_face_detect\detect_unmatched.py

# 自定义
.venv\Scripts\python.exe data\unmatched_face_detect\detect_unmatched.py ^
  --min-conf 0.7 --n 100 --seed 42
```
