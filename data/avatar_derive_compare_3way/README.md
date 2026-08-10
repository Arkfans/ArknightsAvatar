# match vs avatar_derive(07) vs avatar_derive_08 三路对比图

从 `data/unpacked/_avatar_match.json` 中确定性随机抽取 **100 张底图**
（随机种子 42），且这些底图同时存在于 `data/avatar_derive/`（阈值 0.7）
与 `data/avatar_derive_08/`（阈值 0.8）两份推导结果中，因此在同一张图上
可以同时绘制三个裁切范围。

## 图例

每张对比图顶部有信息栏，图像区域叠加绘制：

- 绿色实线：内置头像 match 框（`_avatar_match.json` 的 `box`，游戏原版裁切）
- 红色虚线：avatar_derive(07) 推导框（min-conf 0.7）
- 蓝色虚线：avatar_derive_08 推导框（min-conf 0.8）
- 黄色细线：人脸检测框 face_pos（仅供参考）

07/08 推导框使用**虚线**并错开相位绘制：两者在多数样本上几乎重合，
若全部使用实线，后绘制的框会完全盖住先绘制的框。

## 抽样

- 抽样池：`_avatar_match.json` 与两份推导结果的三方交集，共 **411 张**
- 抽样数量 / 种子：`--n 100` / `--seed 42`（可复现）
- 生成时间与完整清单见 `sample.json`；IoU 汇总见 `summary.csv`、`stats.json`

## 100 张抽样精度（IoU，与 match 框对比）

| 版本 | IoU 均值 | IoU 中位数 |
| --- | --- | --- |
| derive07 (0.7) | 0.8948 | 0.9025 |
| derive08 (0.8) | 0.8959 | 0.9044 |

## 文件说明

- `compare/`：100 张标注 PNG（文件名含角色、底图与两个 IoU）
- `sample.json`：抽样清单（match 框 / 07 框 / 08 框 / face 框 / IoU / 图像路径）
- `summary.csv`：每张底图的 IoU 汇总
- `stats.json`：抽样 IoU 统计
- `draw_compare_3way.py`：生成脚本（可重新生成）

## 使用

```bash
# 在脚本所在目录运行，默认抽取 100 张、种子 42
python draw_compare_3way.py

# 自定义数量 / 种子 / 输入
python draw_compare_3way.py --n 50 --seed 7 ^
  --match "..\unpacked\_avatar_match.json" ^
  --d07 "..\avatar_derive\derive_coords.json" ^
  --d08 "..\avatar_derive_08\derive_coords.json"
```
