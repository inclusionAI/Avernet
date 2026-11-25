# Bot MCP 配置与 SkillSet 生命周期

本词汇表仅定义本议题的领域语言，区分成员关系、能力供应、保存配置与运行时可用性。

## Language

**SkillSet 成员关系（Membership）**:
某个 Skill 或 MCP 属于某个 SkillSet 的关系。成员关系本身不表示目标 Bot 当前具有该能力。
_Avoid_: 已启用能力、Bot MCP 配置归属

**显式安装（Installation）**:
Bot 对某项能力的显式安装事实，可来自 Direct 操作或已启用 SkillSet 的成员供应。仅由 Skill 依赖带来的 MCP 可用性不等于显式安装。
_Avoid_: 全部有效能力、保存配置

**Direct 供应（Direct Supply）**:
独立于 SkillSet 显式成员供应、由直接能力操作建立的 Bot 能力供应。
_Avoid_: 任意已保存配置、SkillSet 供应

**有效 MCP 供应（Effective MCP Supply）**:
目标 Bot 当前从显式安装、适用默认来源和有效 Skill 依赖获得的 MCP 集合。它与运行时已经完成交付的集合分别判断。
_Avoid_: Bot override 集合、设备残留配置集合

**Bot MCP 覆盖配置（Bot MCP Override）**:
针对一个 Bot 与一个 MCP 保存的显式连接配置，未覆盖部分按约定继承其他配置来源。它从属于该 Bot 对该 MCP 的配置关系，独立于 SkillSet 成员关系和当前能力供应。
_Avoid_: 集级配置快照、启用凭证

**配置声明（Config Declaration）**:
调用方针对目标 Bot/MCP 发出的配置意图，区分未声明配置操作、明确清除覆盖以及提供覆盖值。未声明操作与明确要求无覆盖是不同意图。
_Avoid_: 未声明即清空、所有空值具有同一含义

**Manifest 级联清理（Manifest Cascade Cleanup）**:
Manifest 显式移除其管理范围内的 MCP 父条目时，同时清除对应 Bot MCP override 的规则。它与保留父条目时省略配置、以及普通 SkillSet 生命周期变更分别定义。
_Avoid_: 任意供应变化即清配置、省略整个类别即清空

**暂存 override（Dormant Override）**:
已保存但当前没有有效 MCP 供应的 Bot MCP override。暂存描述供应状态，不代表配置已失效或应当清理。
_Avoid_: 孤儿配置、陈旧垃圾配置

**Default 排除（Default Exclusion）**:
对特定 Bot 撤销某项能力的 Default 来源供应的决定。它与该能力的 Bot 配置分别管理。
_Avoid_: 清空配置、删除 MCP 注册

**运行时投影（Runtime Projection）**:
将 Bot 的当前期望能力及有效配置交付给运行实例的过程。期望已保存与运行时已收敛是两个状态。
_Avoid_: 数据库提交成功、自动授予权限
