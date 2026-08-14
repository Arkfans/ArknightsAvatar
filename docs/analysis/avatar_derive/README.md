# face/head → 内置头像裁切框 推导结果

本文件夹是独立产物，不依赖也不接入主程序（`src/arknightsavatar`）。

## 目标

基于 `_face_detect_matched.json` 中的有效底图（`face_confidence > 0.7`
且 `head_confidence > 0.7`），仅凭人脸检测框（`face_pos`）与头部检测框
（`head_pos`）推导出与内置头像匹配框（`box`，即游戏内置头像裁切）尽可能一致的
正方形裁切坐标。

## 数据与筛选

- 输入：`data/unpacked/_face_detect_matched.json`（底图 477 条）
- 筛选条件：`confidence > 0.7` 且 `head_confidence > 0.7`，共 **430 条**有效底图
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
权重矩阵 `W` 用全部 430 条有效样本做最小二乘拟合，系数见 `model.json`
（`r2`: cx≈0.997, cy≈0.996, s≈0.864）。

## 精度（与 match `box` 对比，430 条）

| 指标 | 值 |
| --- | --- |
| IoU 均值 / 中位数 | 0.8927 / 0.9045 |
| IoU P10 / P90 | 0.8153 / 0.9570 |
| IoU ≥ 0.90 占比 | 51.6% |
| 中心偏差均值（像素） | 6.6 |
| 边长比值均值（推导/match） | 1.003 |

5 折交叉验证的 IoU 均值 ≈ 0.890，与样本内基本一致，未过拟合。

## 文件说明

- `derive_coords.json`：430 条有效底图的推导坐标 + 输入检测框 + match 框 + IoU
- `model.json`：推导公式的系数、特征顺序与拟合信息（可复用）
- `stats.json`：汇总精度统计
- `compare/`：24 张抽样对比图（绿色 = match 框，红色 = 推导框，
  黄色 = 人脸框），覆盖最好 / 中位 / 最差样本
- `derive.py`：独立生成脚本，可重新生成以上产物

## 使用

```bash
# 本文件夹默认按 --min-conf 0.7 重新生成
python derive.py

# 指定源文件 / 输出目录 / 置信度阈值
python derive.py --source "..\unpacked\_face_detect_matched.json" --out-dir . --min-conf 0.7
```

参数：`--source` 指定输入报告；`--out-dir` 指定输出目录（默认脚本所在目录）；
`--min-conf` 指定 face/head 置信度下限（默认 0.7，严格大于）；
`--no-compare` 跳过抽样可视化。

> 阈值 0.8 的推导版本见 `data/avatar_derive_08/`，两者互为独立文件夹。

`derive_coords.json` 中每条记录的关键字段：

- `derived_box`：推导出的裁切框 `[x1, y1, x2, y2]`（原图像素，整数）
- `derived_box_exact`：未取整的浮点版本
- `derived_center` / `derived_size`：正方形框中心与边长
- `derived_box_norm`：按原图尺寸归一化的 `[0,1]` 坐标
- `match_box` / `match_box_norm`：内置头像匹配框（对照）
- `iou`：推导框与 match 框的 IoU
