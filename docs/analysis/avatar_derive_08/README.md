# face/head → 内置头像裁切框 推导结果（置信度 > 0.8 版）

本文件夹是独立产物，不依赖也不接入主程序（`src/arknightsavatar`）。
与 `data/avatar_derive/`（阈值 0.7 版）对照使用，本版过滤更严格。

## 目标

基于 `_face_detect_matched.json` 中的有效底图（`face_confidence > 0.8`
且 `head_confidence > 0.8`），仅凭人脸检测框（`face_pos`）与头部检测框
（`head_pos`）推导出与内置头像匹配框（`box`，即游戏内置头像裁切）尽可能一致的
正方形裁切坐标。

## 数据与筛选

- 输入：`data/unpacked/_face_detect_matched.json`（底图 477 条）
- 筛选条件：`confidence > 0.8` 且 `head_confidence > 0.8`，共 **411 条**有效底图
  （0.7 版为 430 条，本版排除了 min 置信度 ≤ 0.8 的 19 条）
- 语义：`face_pos` / `head_pos` 的 `x, y` 是检测框**左上角**，`w, h` 是宽高
  （模型特征由左上角换算为检测框中心：`x + w/2, y + h/2`）；
  `box` 是匹配到的内置头像裁切框 `[x1, y1, x2, y2]`（原图像素，y 可为负，
  表示裁切框超出原图顶部，与 `_avatar_match.json` 语义一致）

## 推导方法

内置头像裁切框是正方形（长宽比 ≈ 1.000），因此把问题建模为预测正方形框的
中心 `(cx, cy)` 与边长 `s`：

```
[cx, cy, s] = W @ [fx, fy, fw, fh, hx, hy, hw, hh, 1]
derived_box = [cx - s/2, cy - s/2, cx + s/2, cy + s/2]
```

其中 `fx/fy/fw/fh` 为人脸框中心/宽/高，`hx/hy/hw/hh` 为头部框中心/宽/高。
权重矩阵 `W` 用全部 411 条有效样本做最小二乘拟合，系数见 `model.json`
（`r2`: cx≈0.997, cy≈0.996, s≈0.866）。

## 精度（与 match `box` 对比，411 条）

| 指标 | 值 |
| --- | --- |
| IoU 均值 / 中位数 | 0.8947 / 0.9047 |
| IoU P10 / P90 | 0.8190 / 0.9569 |
| IoU ≥ 0.90 占比 | 54.3% |
| 中心偏差均值（像素） | 6.5 |
| 边长比值均值（推导/match） | 1.003 |

对比 0.7 版（430 条，IoU 均值 0.8927）：本版样本更少但精度略优，
主要受益于剔除低置信度（多为头部检测不准）的样本。

## 文件说明

- `derive_coords.json`：411 条有效底图的推导坐标 + 输入检测框 + match 框 + IoU
- `model.json`：推导公式的系数、特征顺序与拟合信息（含 `min_conf: 0.8`）
- `stats.json`：汇总精度统计
- `compare/`：24 张抽样对比图（绿色 = match 框，红色 = 推导框，
  黄色 = 人脸框），覆盖最好 / 中位 / 最差样本
- `derive.py`：独立生成脚本，默认即按 `--min-conf 0.8` 重新生成本文件夹内容

## 使用

```bash
# 在本文件夹内直接运行（默认 min-conf=0.8，输出到本文件夹）
python derive.py

# 指定其它阈值（如 0.7）到其它目录
python derive.py --min-conf 0.7 --out-dir ../avatar_derive
```

`derive_coords.json` 中每条记录的关键字段：

- `derived_box`：推导出的裁切框 `[x1, y1, x2, y2]`（原图像素，整数）
- `derived_box_exact`：未取整的浮点版本
- `derived_center` / `derived_size`：正方形框中心与边长
- `derived_box_norm`：按原图尺寸归一化的 `[0,1]` 坐标
- `match_box` / `match_box_norm`：内置头像匹配框（对照）
- `iou`：推导框与 match 框的 IoU
