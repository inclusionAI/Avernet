# 后续工作追踪

本文件记录本期明确暂缓的事项，不代表本期实施范围或已创建外部工单。

## GENOME-CENTER-UUID：迁移后移除 CenterSkillByName

来源：用户针对接口草图批注明确要求保留后续追踪，防止兼容分支长期遗忘。

- 现状：Genome spec/capture 仍以 name 表达 Center；共用 DesiredSkill 暂保留 CenterSkillByName 和旧查找路径以免本期破坏其行为。
- 后续目标：Genome 的 schema、capture、compiler、相关校验与 diff/drift 使用稳定 UUID；完成迁移后移除或明确退役 CenterSkillByName 及 name-only lookup 的兼容入口。
- 历史合同：沿此前 Q13 确认方向，保留历史 JSON/ID；缺少稳定身份的旧修订不靠 name 猜测。实施前盘点实际数据并明确新旧读/写/执行策略。
- 完成条件：新产出均使用 UUID，历史读取和拒绝/迁移策略有验收，既有 promotion 不被破坏，确认无仍依赖旧分支的生产者后再移除。
- 当前状态：后续待建设；本期只保留兼容，不修改 Genome 上层。

## GENOME-SKILLSETS：普通集完整快照与重放

- Genome 的 spec/schema、capture、compile/replay、diff/drift 与相关校验支持真实普通集，包含 inactive、成员身份和启停。
- Direct 与 Set 分开，不把成员摊平成 Direct；Default 继续平台供应。
- 新增字段的历史修订兼容与 patch/policy/自动 actor 权限另行设计。
- 当前状态：后续待建设。本期共用能力聚合后的普通集及 Default 子目标须能表达“不参与”；旧 Genome 保留原 Direct Skills/MCP 目标，不清空普通集或改变 Default。具体字段名随聚合模型 review，不预设独立 DesiredState.skill_sets 字段。
- 本期兼容边界：仅处理旧声明接入共用聚合层所需的兼容归一；不借此增加 Genome 上层 SkillSet 能力，不迁移历史 revision JSON/ID。
