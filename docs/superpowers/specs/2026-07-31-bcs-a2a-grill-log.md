> **历史归档 / 非实现基线**：本文记录早期讨论方案，可能包含已废弃的 `role`、`edge_id` 下发、`permission_profiles[]` / `rules_grants[]` 拆分、`extra_rules` 等设计。实现请以 `docs/superpowers/specs/2026-08-07-bcs-a2a-authz-final-design.md` 和 `docs/superpowers/specs/2026-08-07-bcs-a2a-authz-implementation-spec.md` 为准。

# BCS × A2A 设计 Grill 记录

> 用途：从 2026-07-31 起，记录每次 `$grill-me` 提出的关键问题、你的回答、以及形成的设计结论。

## Q001：所有 bot-to-bot 调用是否必须经过 BCS A2A Gateway？

**问题**

我们要不要让所有 bot-to-bot 调用都必须经过 BCS A2A Gateway？

**你的回答**

同意。我们希望利用 A2A 把 bot 间协作串起来，包括认证、申请权限、交流协作等；BCS 的协作鉴权能力应通过 A2A 扩展融入协议中，从而实现所有调用都经过 BCS A2A Gateway。

**设计结论**

MVP 中，所有 bot-to-bot 协作调用必须经过 BCS A2A Gateway，不允许 bot 直接绕过 BCS 调用另一个 bot 的 A2A endpoint。

原因：

- BCS Gateway 负责认证 caller bot；
- BCS Gateway 负责维护 TaskCtx / participants / current_originator；
- BCS Gateway 负责计算 EdgeGrant / EffectivePermissionView；
- BCS Gateway 负责在 A2A message metadata / extension 中盖章运行时授权上下文；
- 直接调用会破坏 originator 防伪、participants 可信性和审计链。


## Q002：BCS 是否要定义自己的 A2A Extension，作为协作鉴权的协议承载？

**问题**

我们是否明确要求：BCS 协作鉴权不放进 A2A 原生 `securitySchemes` 本身，而是定义一个必需的 BCS A2A Extension，用来承载 TaskCtx、EdgeGrant 判定结果句柄、权限申请、审批状态、participants 维护等 BCS 语义？

**你的回答**

待确认。

**设计结论**

待确认。

**澄清记录**

用户反馈：原问题表述容易误解成“把权限集放进 A2A extension/header 里传递”。这不是目标设计。

修正后的含义：

- Bot 的完整权限集仍由 BCS 统一管理，并通过 AuthSnapshot 下发给 target bot。
- Runtime A2A message/header/metadata 只携带 BCS 盖章的运行时上下文，例如 task_id、caller、originator 相关版本/标识、task_ctx_version、auth_snapshot_version 要求等。
- Target bot 收到消息后，用本地 AuthSnapshot + 运行时上下文激活/计算本次 EffectivePermission。
- BCS A2A Extension 的作用是定义这些 BCS 运行时上下文字段的标准位置和语义，不是传递完整权限集。

## Q003：A2A message 中是否只传 BCS 运行时上下文，不传权限集？

**问题**

BCS 接入 A2A 后，runtime A2A message/header/metadata 中是否只传 BCS Gateway 盖章的运行时上下文，而不传完整权限集？

**你的回答**

同意。原设计是 BCS 统一管理所有 bot 的权限集，并下发给每个 target bot 权限集快照；运行时 target bot 根据 caller、originator、task_id 等信息在本地激活自己的权限集。

**设计结论**

A2A runtime 消息只承载上下文，不承载完整权限集。

允许放入 A2A extension/header/metadata 的字段类型：

- task_id / A2A taskId；
- session_id / A2A contextId；
- caller_actor_id：由 BCS Gateway 根据认证身份盖章；
- current_originator 或 originator handle：由 BCS Gateway 从 TaskCtx 读取并盖章；
- originator_epoch；
- task_ctx_version；
- min_auth_snapshot_version；
- target_bot_id；
- request_id / trace_id / audit_id。

不允许在 runtime A2A 消息中传递：

- 完整 AuthSnapshot；
- 完整 EdgeGrant；
- 完整 RoleDef；
- 完整 participants 列表；
- caller bot 自报的 permission result；
- caller bot 自报的 originator。

BCS A2A Extension 的作用不是传权限集，而是规范这些运行时上下文字段的位置、语义、可信来源和校验规则。

## Q004：MVP 中 target bot 是否只认证 BCS A2A Gateway？

**问题**

MVP 中 target bot 是否只认证 BCS A2A Gateway，而不直接认证原始 caller bot？

**你的回答**

同意。target bot 只信任 BCS Gateway。用户理解为：这相当于 A2A `securitySchemes` 中声明的认证方式，用来认证调用方是可信 BCS Gateway。

**设计结论**

MVP 采用 Gateway 信任模型：

- target bot 的 A2A endpoint 只接受来自 BCS A2A Gateway 的请求；
- A2A `securitySchemes` / `securityRequirements` 用来表达 target bot 如何认证 BCS Gateway；
- 原始 caller bot 身份不由 target bot 直接认证；
- `caller_actor_id` 由 BCS Gateway 根据入站认证结果重新盖章后写入 BCS A2A Extension；
- `current_originator` / `originator_epoch` / `task_ctx_version` 也由 BCS Gateway 从 TaskCtx 读取并盖章；
- target bot 使用“可信 Gateway + BCS runtime context + 本地 AuthSnapshot”激活本次 EffectivePermission。

注意：这不是把 EdgeGrant/RoleDef 放进 `securitySchemes`。`securitySchemes` 只负责认证 Gateway；BCS A2A Extension 承载本次调用上下文。

## Q005：权限申请是否也统一走 A2A，而不是单独裸 BCS API？

**问题**

既然目标是用 A2A 串起 bot 间协作，包括认证、申请权限、交流协作等，那么权限申请/审批是否也应该包装成 A2A 交互，而不是暴露一套完全独立的裸 BCS management API？

**你的回答**

待确认。

**设计结论**

待确认。

**你的回答更新**

同意。权限申请/审批也需要走 A2A。目标是把 bot 协作全流程尽量用 A2A 串起来。

**设计结论更新**

权限申请、审批、状态查询、审批结果通知，外部协议形态统一包装成 A2A task/message/skill；BCS Gateway 仍然是最终写入 EdgeGrant、触发 AuthSnapshot 更新的权威组件。

## Q006：AuthSnapshot 下发是否也要走 A2A？

**问题**

BCS 统一管理权限并分发给各 target bot 本地插件的 AuthSnapshot。这个分发链路是否也要走 A2A？还是保留为 BCS control plane / plugin sync channel？

**你的回答**

暂定方案 A：AuthSnapshot 下发保留为 BCS control plane / plugin sync channel，不走普通 A2A message。

**设计结论**

暂定采用方案 A：

- AuthSnapshot 是控制面数据，不是 bot 间协作消息；
- AuthSnapshot 由 BCS control plane 生成并通过专用 plugin sync channel 下发给 target bot 本地 auth plugin；
- A2A 不承载完整 AuthSnapshot；
- A2A 协作消息只承载 runtime context，例如 task_id、caller、originator、版本号等；
- 未来可以考虑用 A2A 做 snapshot updated 通知，但 MVP 暂不要求。

## Q007：Bot 能力暴露时，A2A AgentCard 的 skills 和 BCS BotCapabilityRegistry 如何分工？

**问题**

Bot 暴露能力时，A2A AgentCard 里有 `skills`，BCS 也有 BotCapabilityRegistry。两者是否等同？如果不等同，谁用于发现，谁用于权限判定？

**你的回答**

待确认。

**设计结论**

待确认。

**Q007 进一步澄清**

用户提出两个关键问题：

1. A2A AgentCard.skills 应该对应 BCS 鉴权模型里的哪个位置？是 role、capability、还是别的东西？
2. 如果 AgentCard.skills 只是宽泛描述，对方如何知道内部具体权限？是否应该先发现 skills，再申请查看该 skill 需要的权限，再申请角色/权限集，然后审批？

**修正后的设计倾向**

A2A AgentCard.skills 不应直接等同于 BCS RoleDef，也不应直接等同于 BCS 底层 permission tuple 中的 skill/capability。它应作为“对外可申请的协作入口/产品能力”，绑定到一个或多个 BCS 权限申请模板。

建议新增/明确一层：

- A2A AgentSkill：对外发现与协作入口，例如 `schedule_meeting`；
- BCS PermissionPackage / GrantTemplate：对外可申请的权限包/角色模板，例如 `meeting_scheduler_basic`；
- BCS RoleDef：target owner 管理的具体角色定义，可被模板引用或生成；
- BCS Capability/Rule：真正用于本地鉴权的细粒度能力规则。

建议申请流程：

1. caller 读取 public AgentCard，看到宽泛 skills；
2. caller 通过 BCS A2A extension 请求某个 skill 的 grant options / permission packages；
3. target/BCS 返回可申请的 packages，每个 package 有描述、包含能力摘要、可配置 scope、审批要求；
4. caller 选择 package，并填写 scope/reason；
5. target owner 审批；
6. BCS 生成/激活 EdgeGrant，并触发 AuthSnapshot 更新。

**Q007 用户反驳与修正**

用户指出：`GrantTemplate / PermissionPackage` 与 `RoleDef` 概念重合，容易把模型越做越复杂。不能在 AgentSkill 与 RoleDef 之间随意新增一层导致设计臃肿。

**修正方向**

取消新增 `GrantTemplate / PermissionPackage` 作为独立核心模型。保留现有核心模型：

- A2A AgentCard.skills：对外发现入口；
- BCS RoleDef：target owner 定义的可申请角色/权限集；
- BCS Capability/Rule：RoleDef 内部的细粒度权限规则；
- EdgeGrant：caller -> target 的实际授权边。

A2A skill 与 BCS RoleDef 的关系应是“skill 暴露可申请 RoleDef 列表”，而不是新增权限包层。

**Q007 最终修正结论**

用户指出：如果 AgentCard.skills 里再挂多个 roles，会绕回原问题并增加复杂度。需要直接回答 AgentCard.skills 到底对应 BCS 什么模型。

最终 MVP 结论：

- A2A AgentCard.skills 在 BCS 协作授权场景中，直接对应“对外可申请的 RoleDef”。
- 不再设计 `AgentSkill -> 多个 RoleDef` 的中间关系。
- 一个 AgentCard skill 就是一种对外暴露的协作角色/权限集入口。
- `skill.id` 应直接等于或稳定映射到 `role_def_id`。
- 如果需要 basic / advanced / readonly / writer 等不同权限，就暴露为多个 skills，而不是一个 skill 下挂多个 roles。

AgentCard.skills 中是否写权限内容：

- Public AgentCard 不放完整细粒度 rules；
- Public AgentCard 应放角色名、描述、能力摘要、scope 形状、是否需要审批；
- 已认证 caller 可通过 BCS A2A extension 查询该 skill/role 的更详细 RoleDef；
- 申请和审批对象就是这个 RoleDef；
- 审批通过后生成 EdgeGrant(caller -> target, role_def_id=skill.id)。

## Q008：AgentCard.skills 与 RoleDef 一一对应后，skill 权限详情如何分级暴露？

**问题**

既然 MVP 中 `AgentCard.skills` 直接对应对外可申请的 `RoleDef`，那么 public AgentCard 里应该暴露多少权限信息？详细 RoleDef 规则是否需要通过已认证的 A2A extension 查询？

**你的回答**

同意采用“Public AgentCard 摘要 + authenticated `bcs.role.describe` 查询详情”的模式。

**设计结论**

MVP 中 RoleDef/skill 权限信息分两级暴露：

1. Public AgentCard 只暴露 RoleDef 摘要，用于发现和初筛：
   - skill_id / role_def_id；
   - name；
   - description；
   - capability_summary；
   - scope_summary / scope_schema 摘要；
   - approval_required。
2. 完整或更详细的 RoleDef 规则通过 BCS A2A Extension 查询：
   - operation: `bcs.role.describe`；
   - 调用方必须先通过 A2A securitySchemes 完成认证；
   - 返回可用于申请决策的 rules/capabilities/scope 详情；
   - 返回内容可以按 caller 身份做脱敏或裁剪。

原因：

- public AgentCard 不能只写宽泛描述，否则 caller 不知道申请什么；
- public AgentCard 也不应暴露完整 rules，避免泄露内部资源结构和安全策略；
- authenticated describe 在可理解性和安全性之间折中。

## Q009：权限申请对象是 skill_id 还是 role_def_id？

**问题**

既然 MVP 中 AgentCard.skill 直接对应可申请 RoleDef，权限申请请求里应该提交 `skill_id`、`role_def_id`，还是两者都提交？

**你的回答**

同意对外申请只提交 `skill_id`，BCS 内部解析为 `role_def_id`，最终 EdgeGrant 只存 `role_def_id`。同时追问：为什么不一开始就直接让 `skill_id == role_def_id`？

**设计结论**

MVP 直接采用 `skill_id == role_def_id`。

- AgentCard.skills[].id 直接使用 BCS RoleDef.id；
- 对外 A2A 字段仍叫 `skillId`，因为这是 A2A 视角下从 AgentCard.skills 发现的对象；
- BCS 内部不需要复杂映射，只做校验：该 `skillId` 是否存在同名 active RoleDef；
- EdgeGrant 中只存 `role_def_id = skill_id`；
- 如果未来 A2A skill 与内部 RoleDef 需要解耦，再引入映射层，但 MVP 不引入。

原因：

- 最简单；
- 避免 skill/role 两套 ID 不一致；
- 申请、审批、审计都能直接围绕同一个 ID；
- 仍然保留 A2A 语义：caller 是“申请某个 skill”；BCS 语义是“授予同名 RoleDef”。

## Q010：RoleDef / AgentCard skill 由谁创建、谁发布、什么时候同步？

**问题**

既然 MVP 中 `AgentCard.skills[].id == RoleDef.id`，那么 RoleDef 和 AgentCard skill 的来源关系是什么？是 owner 先创建 RoleDef，再由 BCS 发布到 AgentCard？还是 bot 自己声明 skill，再由 BCS 生成 RoleDef？

**你的回答**

同意。RoleDef/权限集自然应该由 BCS 先创建和管理，然后再声明发布，不能由 bot 自己随意声明。

**设计结论**

RoleDef 是 canonical source，AgentCard.skills 是 RoleDef 的 A2A projection。

- target owner 在 BCS 管理面创建/修改 RoleDef；
- BCS 校验 RoleDef 的 rules/capabilities/scope 合法性；
- 只有标记为可对外申请的 RoleDef 才发布到 AgentCard.skills；
- `AgentCard.skills[].id == RoleDef.id`；
- AgentCard.skills 由 BCS Gateway/registry 渲染发布，不由 bot runtime 自己声明；
- bot runtime 不能伪造、增加、修改自己对外发布的可申请 RoleDef；
- RoleDef 更新后，AgentCard projection version 应更新；
- RoleDef 语义变化后，需要触发相关 AuthSnapshot 版本更新/失效。

## Q011：AgentCard 由谁托管和返回？

**问题**

既然 AgentCard.skills 是 BCS RoleDef 的 projection，target bot 的 AgentCard 应该由 target bot 自己返回，还是由 BCS A2A Gateway 统一返回？

**你的回答**

同意采用“BCS 生成 AgentCardSnapshot，下发给 bot，本地 bot 作为 A2A Agent Server 返回该快照”的模式。

**设计结论**

MVP 中，BCS 是 AgentCard 内容的 canonical 管理方，但 bot 本地是 A2A AgentCard 的服务方。

- BCS control plane 管理 RoleDef、Bot profile、securitySchemes、BCS extension declaration、skill/role 摘要；
- BCS 生成 AgentCardSnapshot，并下发给 target bot；
- target bot 本地 A2A server 返回该 AgentCardSnapshot；
- bot runtime 不能自由伪造、增加、删除 AgentCard.skills；
- AgentCardSnapshot 应带 version / digest / BCS signature 或等价可信校验；
- BCS A2A Gateway 在 runtime 可检查 target bot 的 AgentCardSnapshot/AuthSnapshot 版本是否满足要求。

**Q011 用户反驳与重新打开**

用户指出：如果 AgentCard 完全由 BCS Gateway 返回，会变成 A2BCS，而不是 A2A；也会让所有职责过度集中到 BCS，削弱 bot 本身意义。

用户倾向：

- BCS 负责统一管理权限集、RoleDef、AgentCard projection 等 canonical 配置；
- BCS 生成后应下发给 bot 本地快照；
- bot 本地仍然作为 A2A Agent Server 暴露/返回自己的 AgentCard；
- BCS/Gateway 在 A2A 调用时负责填写、完善或检查 BCS runtime context；
- 需要比较“Gateway 托管 AgentCard”和“bot 本地托管 AgentCard 快照”的优缺点。

Q011 暂不定稿，等待后续确认。

## Q012：AgentCard.skills 是否直接暴露 RoleDef 对应的权限集？

**问题**

此前暂定 public AgentCard 只暴露 RoleDef 摘要，详细权限通过 `bcs.role.describe` 二次查询。mentor 建议先简化：既然 skills 已经和 RoleDef 对齐，能否直接在 AgentCard.skills 里暴露 role 及其对应权限集，避免二次申请/查询？

**你的回答**

同意。MVP 先简化，AgentCard.skills 直接暴露 RoleDef 及其对应权限集，不再要求 `bcs.role.describe` 二次查询。

**设计结论**

MVP 中，AgentCardSnapshot.skills 直接作为 RoleDef 的对外发布形态：

- `skill.id == RoleDef.id`；
- `skill.name/description` 来自 RoleDef 展示字段；
- `skill.metadata.bcs.role_def_id == RoleDef.id`，可选，主要用于显式说明；
- `skill.metadata.bcs.permissions` 暴露该 RoleDef 对应的权限集/rules 的对外表示；
- caller 发现 AgentCard 后即可理解申请该 skill/role 会获得哪些权限；
- 权限申请直接提交 `skill_id`；
- 审批通过后生成 `EdgeGrant(caller -> target, role_def_id = skill_id)`；
- MVP 不要求 `bcs.role.describe` 二次查询；
- 未来如需减少暴露，可再引入 public card / authenticated extended card / role describe 分级机制。

注意：AgentCardSnapshot 仍由 BCS 生成并下发给 bot，本地 bot 只是返回快照，不能自己伪造 permissions。

## Q013：AgentCardSnapshot 和 AuthSnapshot 是否是两个独立快照？

**问题**

现在同时存在 AgentCardSnapshot 和 AuthSnapshot。前者用于 A2A 能力/角色发布，后者用于本地鉴权。它们是否应该合并？还是保持两个独立快照、由 BCS 分别生成和下发？

**你的回答**

同意保持独立。

**设计结论**

AgentCardSnapshot 和 AuthSnapshot 是两个独立快照，不能合并：

- AgentCardSnapshot：A2A 发现/发布视图，给 caller 看 target bot 有哪些可申请 skill/role 以及对应权限集说明；
- AuthSnapshot：本地 runtime 鉴权视图，给 target bot 本地 auth plugin 用于根据 caller/originator/task 激活 EffectivePermission。

二者共享 RoleDef 来源，但 projection 不同：

- `RoleDef + BotProfile + security declaration + BCS extension declaration -> AgentCardSnapshot`；
- `RoleDef + EdgeGrant + PlatformGuard + BotCapabilityRegistry -> AuthSnapshot`。

生命周期不同：

- RoleDef 展示/发布字段变化：通常影响 AgentCardSnapshot；
- RoleDef 权限语义变化：影响 AgentCardSnapshot 和 AuthSnapshot；
- EdgeGrant 审批/撤销：影响 AuthSnapshot，不一定影响 AgentCardSnapshot；
- Bot 展示信息变化：影响 AgentCardSnapshot，不应刷新 AuthSnapshot。

## Q014：权限申请/审批在 A2A 里是调用谁的 skill？

**问题**

权限申请也要走 A2A。那么 caller bot 申请 target bot 的某个 skill/role 时，这个 A2A message 是发给 target bot 的申请 skill，还是发给 BCS Gateway 的管理 skill？谁负责创建审批任务？

**你的回答**

确认修正版：权限申请语义上发给 target bot；网络链路仍经过 BCS A2A Gateway；target bot/plugin 触发用户审批或自动策略；最终审批结果写回 BCS。

**设计结论**

权限申请/审批采用“target bot 决策入口 + BCS 可信路由/落库”的模式：

- caller bot 申请 target bot 的某个 `skill_id == role_def_id`；
- 该申请作为 BCS A2A extension operation 发给 target bot；
- 网络链路仍必须经过 BCS A2A Gateway；
- BCS Gateway 负责认证 caller、记录申请、路由、审计、基础校验；
- target bot/plugin 是申请接收方和审批入口；
- target bot/plugin 将申请上报给前端提醒 target owner/用户审批；
- 或根据用户预设白名单/自动审批策略处理；
- 审批结果由 target bot/plugin 或审批前端写回 BCS control plane；
- BCS control plane 校验结果后生成/更新 EdgeGrant；
- BCS 触发 AuthSnapshot 更新并下发给 target bot。

一句话：BCS 提供统一服务和权限事实管理，但授权决策主体是 target owner/user，不是 BCS Gateway 自己。

**Q014 用户反驳与修正**

用户指出：虽然 BCS 提供统一服务和最终管理能力，但权限决策根本上应由 target bot 的用户/owner 决定。因此权限申请不应简单建模成“发给 BCS Gateway 的管理 skill”。

用户倾向：

- caller bot 申请 target bot 的某个 skill/role；
- 申请应先发给 target bot 的 A2A endpoint；
- target bot 本地插件收到后，上报给前端页面提醒用户/owner 审批；
- 或者 target bot 本地插件根据用户提前设置的白名单/自动审批策略处理；
- 审批结果再由插件/BCS control plane 写回并生成/更新 EdgeGrant、AuthSnapshot。

修正方向：

- BCS 是权限事实与分发的统一服务，不是替用户做授权决策的主体；
- target bot/owner 是审批入口和决策主体；
- BCS Gateway/control plane 负责记录申请、校验、落库 EdgeGrant、版本更新、审计和快照下发；
- A2A 权限申请可以表现为发送给 target bot 的某个标准 BCS extension operation，而不是普通业务调用。

Q014 已定稿：申请语义上发给 target bot，网络仍经过 BCS Gateway，审批由 target owner/user 或其预设策略决定，结果写回 BCS。

## Q015：权限申请是否必须依附已有 TaskCtx？

**问题**

caller bot 申请 target bot 的某个 skill/role 时，这个申请必须发生在某个已有 TaskCtx 内吗？还是可以作为独立的授权管理流程，不依赖当前用户任务？

**你的回答**

待确认。

**设计结论**

待确认。

## Q016：runtime 权限不足时返回什么 A2A 状态？

**问题**

既然 MVP 不支持运行时动态申请权限，那么 task runtime 中 caller 调 target 时如果没有匹配 EdgeGrant / AuthSnapshot 权限不足，应该返回普通失败、A2A AUTH_REQUIRED，还是 BCS 自定义错误？

**你的回答**

同意：MVP runtime 权限不足时返回 A2A `REJECTED` + BCS extension error，不使用 `AUTH_REQUIRED`。同时提醒后续 grill 不要继续陷入具体层面选择，要回到“BCS 鉴权模型如何结合 A2A”的主线。

**设计结论**

MVP runtime 中不做动态权限申请：

- 若 caller -> target 没有匹配 EdgeGrant；
- 或 originator_policy 不匹配；
- 或 target 本地 AuthSnapshot 版本不足/失效且无法刷新；
- 或 EffectivePermission 激活失败；

则 fail-closed，返回 A2A `REJECTED`，并在 BCS extension error 中说明 BCS_DENIED / AUTH_SNAPSHOT_TOO_OLD / ORIGINATOR_POLICY_DENIED 等机器可读原因。

不用 `AUTH_REQUIRED`，因为 MVP 不支持 runtime 中继续授权后恢复当前任务。

## 主线提醒：不要偏离到零散现象选择

用户要求：当前目标不是不断追问细枝末节，而是理清“BCS 协作鉴权模型如何整体结合 A2A”的主线，最后生成一份按 A2A 进行协作鉴权全过程的设计文档。

已清楚的 AgentCard 主线：

- BCS 管理 RoleDef；
- `AgentCard.skills[].id == RoleDef.id`；
- skills 直接暴露 role 及其 permissions/rules；
- BCS 生成 AgentCardSnapshot；
- AgentCardSnapshot 下发给 bot；
- bot 本地作为 A2A Agent Server 返回该快照；
- AgentCardSnapshot 与 AuthSnapshot 独立；
- 申请权限在 task 前完成；
- runtime 权限不足直接 REJECTED，不动态申请。

后续 grill 应围绕 A2A 全过程主线继续：发现、申请、审批、快照、runtime dispatch、结果/审计。

## Q017：BCS 的 TaskCtx 应该如何映射到 A2A Task/contextId？

**问题**

在 A2A 协作全过程中，BCS TaskCtx 是 runtime 鉴权的核心上下文。它应该如何映射到 A2A 的 `contextId` 和 `taskId`？

**你的回答**

待确认。

**设计结论**

待确认。

## Q018：A2A runtime message 中 BCS extension 应承载哪些最小上下文字段？

**问题**

在 bot-to-bot runtime 协作调用中，A2A message 已有 `contextId` 和 `taskId`。BCS A2A extension 还需要携带哪些最小上下文字段，才能让 target bot 用本地 AuthSnapshot 激活 EffectivePermission？

**你的回答**

待确认。

**设计结论**

待确认。

## Q019：participants 在 A2A runtime 中由谁维护、是否传递？

**问题**

BCS 之前设计里 participants 用于防止 bot 拿别人的 task_id 冒用任务上下文。接入 A2A 后，participants 是否放进 A2A message 传给 bot？还是只由 BCS Gateway 维护和检查？

**你的回答**

待确认。

**设计结论**

待确认。

**Q019 用户追问**

用户提出：participants 是否可以放进 A2A message，但由 BCS 检查；如果 participants 不对，就打回。

**分析结论倾向**

可以放“非权威 participants_hint / trace 展示信息”，但不能放“权威 participants”。

- BCS Gateway 必须只信自己维护的 TaskCtx.participants；
- caller bot 提交的 participants 不能作为 admission 依据；
- 如果 A2A message 中带 participants，只能用于 debug/trace/UI，不参与授权；
- 若 message 中携带的 participants_hint 与 BCS 权威 TaskCtx.participants 冲突，Gateway 可以 reject 或覆盖重写；
- MVP 为避免误解，建议 runtime A2A message 不传 participants；如确实需要可传 `participants_hint`，字段名必须显式说明 non-authoritative。

## Q020：A2A message 的 `role` 字段和 BCS RoleDef 是否要关联？

**问题**

A2A Message 自带 `role` 字段，BCS 也有 `RoleDef`。二者名称相似但语义可能完全不同。是否需要明确：A2A `message.role` 不等于 BCS `RoleDef`，权限申请/授权必须使用 `skill_id == role_def_id`，不能使用 A2A `role` 字段表达 BCS 角色？

**你的回答**

待确认。

**设计结论**

待确认。

## Q021：originator 在 A2A runtime 中如何表达？

**问题**

BCS runtime 鉴权需要 `current_originator`。接入 A2A 后，是否在 BCS extension 中直接携带 `current_originator_id`？这个字段由谁写、target bot 能否信、caller bot 能否提交？

**你的回答**

待确认。

**设计结论**

待确认。

## Q022：target bot 本地 auth plugin 在 A2A request 生命周期中在哪一步执行？

**问题**

A2A runtime message 到达 target bot 后，target bot 本地 auth plugin 应该在什么时候根据 AuthSnapshot + BCS runtime context 激活 EffectivePermission？是在业务 handler 前强制执行，还是业务 handler 自己按需调用？

**你的回答**

待确认。

**设计结论**

待确认。

## Q023：A2A Task/Artifact/status 与 BCS 审计链如何对应？

**问题**

A2A runtime 会产生 Task、Message、Artifact、status。BCS 也需要审计 caller、target、originator、EdgeGrant、EffectivePermission、结果。两者如何对应，才能形成完整协作鉴权链路？

**你的回答**

待确认。

**设计结论**

待确认。

## 当前阶段小结

截至 Q023，BCS × A2A 主线问题已基本确认，可以进入设计文档整理阶段。

已定主线：

1. 所有 bot-to-bot runtime 调用必须经过 BCS A2A Gateway；
2. A2A `securitySchemes` 用于 target bot 认证可信 BCS Gateway；
3. BCS A2A extension 承载 runtime context，不承载完整权限集；
4. AuthSnapshot 下发走 BCS control plane / plugin sync，不走普通 A2A message；
5. `AgentCard.skills[].id == RoleDef.id`；
6. AgentCard.skills 直接暴露 RoleDef 及其 permissions/rules；
7. RoleDef 是 canonical source，AgentCardSnapshot 是 A2A projection；
8. AgentCardSnapshot 由 BCS 生成并下发，bot 本地作为 A2A Agent Server 返回；
9. AgentCardSnapshot 与 AuthSnapshot 独立；
10. 权限申请/审批走 A2A，但语义上发给 target bot，target owner/user 决策，BCS 负责可信路由/落库/快照；
11. MVP 中权限申请在 task 开始前完成，runtime 不动态申请；
12. A2A `contextId = SessionContext.id`，`taskId = TaskCtx.task_id`；
13. runtime BCS extension 最小字段：caller_actor_id、target_bot_id、current_originator_id、originator_epoch、task_ctx_version、min_auth_snapshot_version；
14. participants 是 BCS TaskCtx 权威状态，runtime 不传权威 participants；
15. A2A `message.role` 与 BCS RoleDef 无关；
16. caller 可提交 originator hint，但 BCS 以 TaskCtx 为准，检查后盖章；
17. target bot 本地 auth plugin 作为 A2A request 前置 middleware；
18. A2A 对象保持原生语义，BCS audit 用 trace_id/dispatch_id 关联完整鉴权事实。


## Q024：participants_hint 应写在 A2A Message metadata 还是 Task metadata？

**问题**

用户澄清：participants 可以写进 A2A metadata，但由 BCS 检查；如果错了就修改或驳回。需要明确写在 Message.metadata 还是 Task.metadata。

**设计结论**

写在 `Message.metadata.bcs.participants_hint`，不是权威 `participants`。

- 权威 participants 仍然只在 BCS `TaskCtx.participants`；
- `participants_hint` 是 caller 对本次 dispatch 上下文的提示；
- BCS Gateway 根据 `taskId` 查权威 TaskCtx 后检查；
- 缺失：允许，Gateway 可补齐或不转发；
- 一致：允许，Gateway 盖章后转发；
- 不一致：MVP 建议 reject；如覆盖重写则必须 audit；
- 不把权威 participants 放入 `Task.metadata`，避免把展示状态误当安全状态。


## Q025：roles/rules/skills 的数组基数表达

**问题**

用户强调：一个 bot 会有很多 roles，每个 role 也会有很多 rules。文档表示时应在关键位置用 `[]` 表示数组，避免误解为单个 role/rule。

**设计结论**

已在 A2A 设计文档中补充基数关系：

```text
Bot has many RoleDef[]
RoleDef has many rules[] / permissions[]
AgentCardSnapshot has many skills[]
AgentCardSnapshot.skills[] one-to-one projects RoleDef[]
AgentSkill.metadata.bcs.permissions[] projects RoleDef.rules[]
EdgeGrant[] can exist between the same caller -> target
```

文档后续应统一使用 `RoleDef[]`、`rules[]`、`permissions[]`、`skills[]`、`EdgeGrant[]` 表达多值关系。


## Q026：A2A SendMessage 是否携带 Task 对象，以及 BCS TaskCtx 放在哪里

**问题**

用户指出：1.7 runtime SendMessage 示例只写了 message，没有 Task；并追问 originator/participants 是写在 Message.metadata 还是 Task.metadata。

**设计修正**

A2A `SendMessageRequest` 发送的是 `message`，不是完整 `Task` 对象。`Task` 通常是 target agent 处理后返回的状态对象；`message.taskId` 只用于继续一个已经存在的 A2A Task。

因此修正映射：

```text
A2A contextId = BCS SessionContext.id
BCS TaskCtx.task_id = Message.metadata.bcs.task_ctx_id
A2A message.taskId = target bot 已创建的 A2A Task.id；首次 dispatch 可为空
```

BCS runtime 鉴权字段，包括 `task_ctx_id`、originator hint、participants_hint，应放在 `Message.metadata.bcs`，因为它们描述本次 dispatch。`Task.metadata` 可在响应/状态中 echo trace/debug 信息，但不能作为 Gateway admission 的权威输入。


## Q027：修正 A2A taskId 与 BCS TaskCtx.task_id 的 MVP 映射

**问题**

用户指出：MVP 设定是一个 session 目前只有一个 task，task 在 human 发起时已经由 BCS 创建。bot A 找 bot B 办事时，沿用这个 task_id，不是从 A2A 角度让 bot B 创建新 task。

**设计结论**

修正 Q026 的过度 A2A 化理解。MVP 映射为：

```text
A2A contextId = BCS SessionContext.id
A2A message.taskId = BCS TaskCtx.task_id
```

A2A SendMessage 仍然只发送 `message`，不是完整 Task；但 message 里的 `taskId` 就是 BCS 已创建的 task。caller bot 可以在 Message.metadata.bcs 里写 originator、participants_hint、target 等运行时上下文；BCS Gateway 根据自己记录的 TaskCtx 检查，检查通过后盖章转发。
