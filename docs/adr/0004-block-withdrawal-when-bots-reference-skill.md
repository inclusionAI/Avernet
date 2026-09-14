---
status: accepted
---

# 有 Bot 引用时阻断 Skill 下线

只要存在有效 Bot Binding 或仍被服务 Bot Artifact 使用，Skill Version Withdrawal 或 Skill Retirement 就必须被前置阻断；风险提示和用户二次确认不能绕过。执行下线时必须重新检查引用，避免预检查与执行之间新增引用。

发布或升级不采用相同阻断规则。发布前展示全部受影响 Bot 及 Owner，用户知晓后可以继续；发布成功后更新 Bot 草稿态解析和草稿容器，已经发布的服务 Bot Artifact 保持不变，直到服务 Bot 下次发布才使用新 Skill 版本。

新版本发布后，旧版本成为历史版本，不等同于被下线或删除。既有 Artifact 仍可通过精确的 `skill_uuid + external_version_key` 获取其固化版本。

Skill Asset Deletion 比 Offline 或 Retirement 更严格：只有在没有 Installation、SkillSet Membership、Draft、Publication、Version 或 Service Artifact 血缘引用时才允许删除。删除命令必须先锁定 Skill，并在同一事务中重新检查数据库 blocker；所有新增引用命令也必须遵循相同的 Skill 锁顺序，避免检查后并发新增引用。失败时返回 `409 SKILL_ASSET_IN_USE` 和不泄露跨用户信息的 blocker 计数，不能静默删除 Membership 或 Installation。Center Skill Offline 保留现有的 impact 预览、命令时重检和锁内 guard。

仍在 BFF 使用的 legacy `ac_skill_member` 没有 tenant/env 字段。在该表正式退役前，匹配 `skill_uuid` 的成员关系按全局 Grant blocker fail closed；宁可跨环境保守阻断，也不能删除 Skill 后留下无法归属的成员行。

当前可回放 Service Artifact 只保存 exact Center Skill ref，而每个可进入 Artifact 的 exact ref 必然来自不可变的 `ac_skill_version`。因此硬删除以 Version 作为 Artifact 的 dominant DB blocker，不在 Repository 内再次扫描可能 offload 的 Artifact；Offline/Retirement 的产品影响面仍使用 `ServiceArtifactLineageReader` 展示具体血缘。若未来 Artifact 支持不经过 Version 的 Skill corpus，必须先新增可事务校验的 lineage fact，不能绕过本约束。

当受治理的 Git 源消失但引用仍存在时，保留 Skill 资产和 Desired State，不自动停用 Bot；Git Sync 将其报告为结构化 `SOURCE_MISSING_IN_USE` 失败，Runtime 使用现有的 `MANAGED_SOURCE_MISSING` / `PENDING` 表达，直到源恢复或引用被显式解除。本期不为此新增持久状态字段。

Legacy BFF 的 Local Skill 删除统一委托可补偿的 `LocalSkillDeleteService`，不再使用先删 active/source 文件、后删数据库的旧路径。Bot 未 Ready 时返回冲突，不保留历史 metadata-only 删除 fallback；否则平台无法证明 Runtime、内容和数据库三者安全收敛。

上述不变量适用于所有 Skill hard-delete primitive 及其调用方，不能只依赖 HTTP 层预检查。Bot 删除属于独立生命周期流程：先显式清理该 Bot 的 Installation，再删除 Bot-owned Skill；不能为了 Bot 清理而放宽普通 Asset Deletion。

历史 dangling Installation 不能通过恢复不完整或已失去来源的 Skill 资产来掩盖。运维订正先导出审计证据，再同时删除指向不存在 Skill 的 Installation 与无效 Membership；若容器检查确认没有对应悬空 Runtime entry，则不修改容器文件，只在订正后执行 Effective Read / Runtime Projection 验证。该订正是对已损坏状态的显式修复，不是普通 Asset Deletion 隐式停用 Bot 的先例。
