# NpcAvatar头像提取

添加模块，用于从characters中提取处理后的npc avatar，详细职责如下：

1. 判断characters中的角色立绘属于base还是diff（classify）
2. 通过三种方式进行头像匹配
   1. 手动指定，优先度最高
   2. 头像匹配，如果可在avatar中找到对应头像，则进行匹配，匹配度高于0.8选择该结果（match），优先度次之
   3. 模型识别，通过face与head识别，当face > 0.8，head > 0.7时，使用avatar_dervive_08推导出脸部范围，使用此范围
3. 根据头像匹配的结果，对character中的base与diff进行头像提取，产物为各base与diff的头像，png格式，按照角色区分文件夹

## 说明

### base与diff组合

diff通过meta的face_pos完成与base的组合，部分diff长宽与base一致，此时不用进行额外处理（即视为已经组合）

### 头像提取

先对base进行头像提取，如果存在多个base，且两个base提取后的头像相似度过高（>0.98），则取最高置信度的base，忽略低置信度的base和响应的diff。然后再对diff提取

### 特殊diff

有些diff会有动作的改变，导致头像位置与base不一致，在提取阶段应通过与base的比较自动识别特殊diff，检测方式使用透明部分匹配，忽略角色面部表情变化。
识别到的特殊diff，应通过模型识别自动识别面部，进行提取。
