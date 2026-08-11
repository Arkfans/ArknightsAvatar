# no_box 角色清单（附带 face/head 匹配数据）

- 生成时间：2026-08-12 06:23:03 中国标准时间
- 定义：**底图全部 no_box**（match 档未达标 + derive 档 face>0.8 且 head>0.7 未满足），即该角色无任何底图头像产物
- 数据源：`data/unpacked/_avatar_extract.json`、`_face_head_detect.json`、`_avatar_match.json`
- 产物：`docs/no_box_characters.json`（完整数据）、`docs/no_box_characters.csv`（每张 no_box 底图一行，UTF-8 BOM）

## 汇总

- **角色数：456**（avg 440 / char 14 / npc 2 / other 0）
- **no_box 底图：493** 张；face 检出 **182（36.9%）**，head 检出 **306（62.1%）**
- 关联差分：1169 张，其中 no_box 1169 张
- 另有 **16** 个角色部分底图 no_box（见 JSON `partial_no_box`）

## 失败原因分布（no_box 底图）

| 原因 | 数量 | 占比 |
|---|---|---|
| face未检出 | 311 | 63.1% |
| face低置信 | 143 | 29.0% |
| head低置信 | 27 | 5.5% |
| head未检出 | 12 | 2.4% |

## 头像匹配（match）情况

- 有 match 记录但阈值 ≤ 0.8（因此未采用）：**14** 张，阈值范围 0.360 ~ 0.708
- 其中阈值 ≥ 0.70：**2** 张；≥ 0.75：**0** 张
- 其余均为无候选头像（`no_avatar`）或角色不在 match 报告内（多为 `avg_npc_*`）

阈值最接近 0.8 的 match 记录：

| 角色 | 底图 | match 阈值 |
|---|---|---|
| char_285_medic2_1 | char_285_medic2_1.png | 0.7079 |
| avg_1042_phatm2_1 | avg_1042_phatm2_1$1.png | 0.7068 |
| char_362_saga | char_362_Saga#1.png | 0.6951 |
| char_1011_wizard_1 | char_1011_wizard_1.png | 0.6271 |
| avg_286_cast3_1 | avg_286_cast3_1$1.png | 0.6056 |
| char_264_mountain_1 | char_264_Mountain_1#1.png | 0.5942 |
| avg_4166_varkis_1 | avg_4166_varkis_1$1.png | 0.5941 |
| avg_4166_varkis_1 | avg_4166_varkis_1$2.png | 0.5941 |
| avg_4188_confes_1 | avg_4188_confes_1$1.png | 0.5446 |
| avg_4093_frston_1 | avg_4093_frston_1$1.png | 0.4866 |

## 说明

1. no_box 底图的 face/head 数据全部来自 `_face_head_detect.json` 缓存（493 张全覆盖）。
2. `face置信度不足`/`head置信度不足` 表示有检出但低于阈值——可考虑下调 `--face-conf`/`--head-conf` 或补手动框；`face/head未检出` 需人工确认立绘或补 `_avatar_manual.json`。
3. 有 match 记录但阈值 ≤ 0.8 的底图，可考虑下调 `--match-threshold`（当前 0.8），但多数阈值远低于 0.7，收益有限。
4. 其余 diff 的 no_box 是因为所属 base 无框直接继承失败，未做 face/head 识别。
