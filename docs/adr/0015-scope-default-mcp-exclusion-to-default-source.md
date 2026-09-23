# Default Skill/MCP 排除只作用于 Default 来源

状态：accepted（本 PR 实现；合并、部署与运行验证另行确认）。

现有策略把 Engine/Template 代码型默认 MCP 和 Default 技能集中的显式 MCP 成员，在排除后仍视为由 Default 占用。普通技能集添加时分别被平台策略校验或“已属于其他技能集”校验拒绝。这防止排除被其他来源绕过，却阻断了用户将 MCP 从 Default 转入自定义技能集。

对精确 owner、Bot、Default 技能集和能力身份的排除，只抑制 Default 对该 Bot 的贡献；Engine/Template 代码型默认 MCP、Default 表中的显式 MCP 成员及 Skill 成员适用同一来源规则。存在这条排除时，允许 Skill/MCP 加入同一 Bot 的普通技能集，并保留排除；普通技能集激活后可重新提供该能力。没有排除时，Default 成员仍不能重复加入普通技能集。权限、Direct Active 和同一 Bot 其他普通技能集的冲突规则继续适用；其他 Bot 的状态不参与本次判定。

平台默认 MCP 的 Direct 安装与停用限制继续生效；被排除的 Default Skill 仍是 Set-managed，不能通过 Direct 操作绕过。重复排除 Default 仍是幂等操作；若普通技能集已持有该能力，不得清除它提供的 Installation 或 MCP 的 Bot 配置。如果后端尝试取消排除，而同一 Bot 的普通技能集仍持有该能力，应拒绝并提示先从普通技能集移除。本决策不新增入口或隐式迁移成员关系。

目标发布基线的有效 Skill/MCP 计算已分别扣除 Default 的排除记录并合并普通技能集的 Installation。本决策不改变这项并集算法。

这有意修订了 [#2036](https://github.com/inclusionAI/Avernet/commit/a7b19d3df8ea9752ffb96c291d02173727018c64) 的“排除后仍拒绝普通集添加”规则，也缩小了“被排除的 Default 显式成员仍阻止加入另一个 Set”这条规则在 Skill/MCP 添加时的适用范围。实现时必须在事务内重检精确排除和成员关系，并协调添加与取消排除的并发操作，避免两种来源同时取得所有权；Skill Center 的维护约定及相关合同测试也须同步修订。
