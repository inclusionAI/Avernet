# Manifest Capability 全量替换与 Direct claim

状态：accepted。

## 决策

Manifest schema v1 的 `skills` 与 `mcp` 均采用“存在即全量 Replace”：字段缺失表示不表达意见，显式空数组表示清空对应快照。Apply 是一次性 Bot 级命令，保存的 Manifest 文档不是持续控制器，删除文档也不改变已接受的能力状态。

每个显式 Skill/MCP 都转换成 Direct claim。普通 SkillSet membership 被移除；Default 或 engine/template policy 供应先写 Bot exclusion，再写 Installation。`exclusion + Installation` 是合法 Direct 状态，由现有 membership、policy、exclusion 与 Installation 事实推断，不新增 provenance、managed-by-Manifest 字段或 schema v2。普通激活仍不能从零创建该状态；已经存在的 Direct 可由普通更新或删除操作管理，删除 Installation 时保留 exclusion。

`skills` 同时声明最终有效 Skill 集合与完整 Bot-owned Local 资产集合。每个声明包都完整覆盖；已有效同名项报告 `UPDATED`，inactive/absent 项报告 `CREATED`。未声明 Local 资产先解除目标 Bot 引用，再物理删除包和记录；共享 Repo/Center 资产只移除 Bot 关系。确认包已不存在时允许清理残留记录，存储状态不确定仍失败。

`mcp` 声明最终显式 MCP 集合及完整 Bot override。声明的 Installation 与 override 在单 MCP 事务内提交。最终 Runtime MCP 集合是显式 Direct MCP 与最终有效 Skills 的依赖并集；dependency-only MCP 不写 Installation。`mcp` 存在时清除未声明 dependency-only override，但 retained dependency 不进入 `removed`，而以 `UPDATED` 加说明报告。

Skills 与 MCP 保持独立失败边界，不增加跨 Manifest 事务。中间来源转换只写控制面，最终状态按现有 delivery strategy 投影。Runtime 不可用或投影失败不回滚已接受的控制面状态。Local 物理删除失败报告 `PARTIAL`，保留资产记录供后续 Apply 重试，并按确定顺序在首个失败处停止。

## 后果

- 同一 Manifest 不再因历史 SkillSet、Default、UI 或 dependency 来源而得到不同结果。
- 旧的 ownership-protection materialiser 语义被替换，不保留 feature flag 或双 dialect。
- 发布前必须审计已保存 Manifest、Direct/SkillSet 状态、Default exclusion、Bot-owned Local 资产、exclusion+Installation 组合及失败 Apply。
- 公共领域策略、持久化与合同留在 Avernet；企业 DI、Device Adapter 和 Singlebox 验收在 OCB 以精确 gitlink 集成。

依据：GitHub #2425、#2426、#2427、#2428。
