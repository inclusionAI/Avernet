# SkillSet 启停按 MCP 影响候选集选择投影范围

## Status

Accepted for implementation on `dev`.

## Problem

普通 SkillSet 启停即使没有 MCP 成员，也固定构建完整 MCP 计划并重新声明设备 allow-list 与 Passport 范围。只看 Set 直接 MCP 成员又会遗漏成员 Skill 的 MCP dependencies；仅由依赖带来的 MCP 在 Set 启用时还可能没有设备配置交付。

## Required behavior

在 `set_skill_set_active` 的同一事务内，汇总直接 MCP 成员与此次启停涉及的 Skill MCP dependencies，作为**保守影响候选集**。Local/Repo Skill 从 Skill 资产读取依赖；Center Skill 从当前解析到的最新 PUBLISHED Version 元数据读取。候选码由投影器与变更后的完整有效 MCP 集合交叉过滤；候选非空不要求最终有效集合一定变化。

| 命令结果 | Runtime 范围 |
| --- | --- |
| Set 状态变化，候选集已知为空 | `skills=True, mcp=False` |
| Set 状态变化，候选集非空 | `skills=True, mcp=True` |
| Set 状态未变化 | 保留 `skills=True, mcp=True`，作为既有 Desired State 的重试收敛 |
| 启用时依赖无法可靠解析 | 事务失败，不提交启用 |
| 停用时依赖无法可靠解析 | 允许清理 Desired State，但保留内部“未知”信号并以 `mcp=True` 保守投影 |

启用时 `claimed_mcp` 为直接 MCP 成员与 Skill dependency 候选的并集；文件型 Runtime 继续先交付被确认仍有效的 MCP 配置，再覆盖 allow-list，最后更新 Passport。停用时 `claimed_mcp` 与 `released_mcp` 均为空，只撤销可调用范围，保留设备配置。`legacy_activate` 与正式启用共享同一范围规则。Teclaw 仍按其 Whole Artifact 合同交付完整内容，不新增引擎分支。

Skill dependency 只进入有效 MCP 范围，不写入显式 `BotMCPInstallation`；Set 停用对显式 Installation 和 Bot 级 override 的既有清理仍只作用于 Set 的直接 MCP 成员。

“无依赖”和“无法解析”不能混淆。Center Version 显式 `mcp_dependencies: []` 是已知为空；缺少 PUBLISHED Version、字段缺失或损坏是未知。历史上已无法解析的 Center 成员仍可停用，且撤销旧 Installation。内部未知信号不进入 HTTP response。

## Compatibility and boundaries

- HTTP、数据库 schema、设备协议、任务队列和 Desired State 先提交、Runtime best-effort 投影的返回语义不变。
- 本次不增加 Skill dependency 权限门禁。添加带依赖 Skill 时的权限检查另行设计，不属于此变更。
- 本次不处理停用删除 Bot 级 MCP override 后、同一码仍由其他来源供给时的设备配置回退问题；该既有配置收敛问题单独评估。
- 不为精确比较前后有效 MCP 而重复构建完整计划，也不改动 SkillSetServiceFactory 的构造路径。

## Acceptance

- 无直接 MCP、无 Skill dependency 的状态变化只投影 Skill，不构建 MCP 计划，不调用设备 MCP endpoint 或 Passport 更新。
- 直接 MCP、Local/Repo/Center Skill dependency 及其混合候选均触发 MCP 投影；启用时仅对投影后仍有效的候选交付配置。
- 重叠供给时保守投影，不删除仍有效的设备配置；重复启停仍完整重投影。
- Center latest PUBLISHED Version 的显式空数组、非空依赖、无效元数据、不可解析旧成员分别覆盖；启用失败不得留下 Set 状态或 Installation 变更。
- 验证 OpenClaw 分域投影、Teclaw 整体 Artifact、Legacy 启用及相关 OCB Corp 装配边界。
