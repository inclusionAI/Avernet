> **历史归档 / 非实现基线**：本文记录早期讨论方案，可能包含已废弃的 `role`、`edge_id` 下发、`permission_profiles[]` / `rules_grants[]` 拆分、`extra_rules` 等设计。实现请以 `docs/superpowers/specs/2026-08-07-bcs-a2a-authz-final-design.md` 和 `docs/superpowers/specs/2026-08-07-bcs-a2a-authz-implementation-spec.md` 为准。

---
title: BCS bot 协作鉴权 MVP 设计
date: 2026-07-30
status: draft
scope: BCS cooperation authorization MVP
supersedes:
  - 2026-07-16-bcs-edge-permission-briefing.md
  - 2026-07-16-bcs-edge-permission-design.md
  - 2026-07-21-bcs-edge-permission-design.html
  - 2026-07-21-bcs-proxy-briefing.md
  - 2026-07-24-bcs-edge-permission-design.md
  - 2026-07-27-bcs-edge-permission-briefing.html
---

# BCS bot 协作鉴权 MVP 设计

## 0. 本文要解决的问题

BCS 协作鉴权解决一个问题：

```text
当 caller 让 target bot 做事时，target bot 在执行 tool / Skill / MCP / workflow / API / memory / network 等能力前，如何判断这次协作是否被 target bot owner 授权？
```

本文只定义 **BCS cooperation authorization**。

它不定义 bot runtime 自己的本地 `ask / deny / allow` 策略，也不定义 agent loop 被拒后怎么继续规划。

最终执行条件是：

```text
BCS cooperation auth allow
AND target bot local auth allow
=> 才能执行
```

BCS deny 是硬拒绝：

```text
BCS cooperation auth deny
=> 不进入 target bot local execution
```

MVP 的核心原则：

1. **每跳独立授权**：`Alice -> bot1` 不会自动变成 `bot1 -> bot3`。
2. **target owner 授权 target 的运行姿态**：`caller -> target` 边上挂的是 target owner 定义/批准的权限。
3. **originator 由 BCS 从 TaskCtx 解析**：bot 不能自报 originator。
4. **participants 只管 task 续接**：participant 不是权限来源。
5. **插件本地执行鉴权**：BCS 下发授权快照，target 插件在 action 前本地 fail-closed 判断。
6. **无法证明安全就 deny**：缺字段、过期、unsupported、unmappable、版本不一致都 deny。

---

## 1. 先看一条完整链路

典型链路：

```text
Alice -> bot1 -> bot3 -> tool/action
```

### 1.1 Human kickoff

Alice 让 bot1 做事：

```text
Alice -> BCS -> bot1
```

BCS 创建或更新当前 session 的 active `TaskCtx`：

```text
TaskCtx T {
  task_id = T                         # BCS 签发的不透明 task 句柄
  current_originator = Alice           # 当前 task 的 human 决策上下文
  originator_epoch = 0                 # originator 变化版本
  participants = { bot1 }              # BCS 已把该 task 交付给 bot1
  status = active                      # active 才能续接
}
```

### 1.2 bot1 想调用 bot3

bot1 不能直接把消息甩给 bot3。它必须通过 BCS dispatch：

```text
bot1 -> BCS:
  dispatch target=bot3, task_id=T
```

BCS 做 dispatch admission：

```text
1. authenticate caller bot1                         # caller 由 BCS 认证，不由 bot 自报
2. load TaskCtx(T)                                  # task_id 必须存在
3. require TaskCtx.status == active                 # 旧 task 不能复用
4. require bot1 in TaskCtx.participants             # bot1 必须已经参与该 task
5. current_originator = TaskCtx.current_originator  # 从 TaskCtx 解析，不采信 bot 上报
6. find active EdgeGrant(bot1 -> bot3)              # 每跳必须有独立授权边
7. require EdgeGrant.originator_policy matches current_originator
8. admission allow 后，participants += bot3
```

通过后，BCS 给 bot3 插件下发可信入站上下文：

```text
InboundDispatchContext {
  env
  task_id
  caller_id = bot1                    # BCS stamped immediate caller
  target_bot_id = bot3
  task_ctx_version                    # 用于防旧 TaskCtxSnapshot
  originator_epoch                    # 用于防旧 originator 上下文
  min_auth_snapshot_version           # 可选；要求插件至少持有这个授权快照版本
}
```

注意：`InboundDispatchContext` **不携带 `current_originator`**。插件必须用 `task_id` 读取/刷新 `TaskCtxSnapshot`，以执行时最新可确认的 `current_originator` 做 action auth。

### 1.3 bot3 执行 action 前鉴权

bot3 插件执行前本地判断：

```text
AuthSnapshot(bot3)                    # target bot 的授权事实快照
+ TaskCtxSnapshot(T)                   # task 的动态上下文快照
+ InboundDispatchContext               # 本跳 caller/target/task
+ runtime action -> canonical request  # adapter 标准化后的能力请求
=> allow / deny
```

如果 Alice 之后 Bob 插话：

```text
current_originator = Bob
originator_epoch += 1
```

bot3 执行前必须看到新 epoch。旧的 Alice 权限不能继续被缓存使用。

---

## 2. 核心图模型：点、边、运行时传递

先把 BCS 协作鉴权抽象成一张图：

```text
点 = Actor
边 = EdgeGrant(caller_actor -> target_bot)
运行时 = BCS 根据已认证 caller + TaskCtx 选择本跳可用边，再把可信上下文交给 target plugin
```

### 2.1 点：Actor 怎么设计

`Actor` 是授权图里的点。

```text
Actor {
  env                              # 环境隔离；dev/prod 的点不是同一个点
  actor_id                         # 稳定 id；所有边和审计都引用它
  kind: human | bot | service | system
  status: active | disabled        # disabled actor 不能参与 runtime auth

  owner_id                         # bot 时存在；表示谁管理这个 bot
  display_name                     # 展示用；不参与鉴权
}
```

MVP 中点的可用语义：

| actor kind | 能否做 caller | 能否做 target | 能否做 current_originator | 能否进 participants | MVP 状态 |
| --- | --- | --- | --- | --- | --- |
| `human` | 可以 | 不可以 | 可以 | 不可以 | 启用 |
| `bot` | 可以，但只支持带 task_id 的 continuation dispatch | 可以 | 不可以 | 可以 | 启用 |
| `service` | 不可以 | 不可以 | 不可以 | 不可以 | future |
| `system` | 不参与普通 runtime | 不可以 | 不可以 | 不可以 | 管理/迁移保留 |

关键点：

- human 是 MVP 唯一 `current_originator`；
- bot 可以是本跳 `caller`，但 MVP 不允许 bot fresh/self-start task；
- bot 可以是 `target_bot`，因为被保护和执行能力的是 bot；
- `participants` 只放 bot，不放 human；
- `owner_id` 是管理关系，不等于 runtime caller，也不自动等于 originator。

### 2.2 边：点和点之间怎么表达

`EdgeGrant` 是授权图里的有向边：

```text
caller_actor -> target_bot
```

它的含义是：

```text
当 caller_actor 让 target_bot 做事，且当前 task 的 originator_policy 匹配时，
target_bot 可以按这条边批准的 RoleDef / extra_rules 运行。
```

注意方向：

```text
A -> B
```

不是“A 的权限传给 B”，而是：

```text
B 以某个被批准的角色/规则姿态为 A 做事。
```

边的最小表达：

```text
EdgeGrant {
  caller_id                         # from 点；本跳 immediate caller
  target_bot_id                     # to 点；被保护的 bot
  role_def_id                       # target owner 定义的 RoleDef；可为空
  extra_rules                       # 边级规则；role_def_id 为空时必须非空
  originator_policy                 # DirectOnly / OriginatorIn；决定边何时激活
  status                            # active 才参与 runtime
  approval fields                   # target owner 批准的证据
}
```

边不表达：

- caller 拥有哪些外部资源身份；
- originator 的资源权限是否传给 target；
- 上一跳的权限是否复制到下一跳；
- bot runtime 本地 ask/deny/allow 策略。

### 2.3 运行时怎么传递点边数据

运行时不把整张图到处传。BCS 只传递 target plugin 需要的可信上下文和快照。

控制面存储：

```text
Actors
RoleDefs(target-owned)
EdgeGrants(caller -> target)
BotCapabilityRegistry(target-owned)
PlatformGuard
```

下发给 target plugin 的授权快照：

```text
AuthSnapshot(target_bot) {
  target bot 的 BotCapabilityRegistry
  target bot 拥有的 RoleDefs
  指向 target bot 的 active EdgeGrants
  PlatformGuard
  adapter/mapping/catalog 版本
}
```

一次 bot-to-bot dispatch 中，bot 只能提交：

```text
target_bot_id
task_id
payload
```

bot 不能提交：

```text
caller_id
originator
participants
active_edge_ids
permission result
```

BCS 在本跳重新盖定：

```text
caller_id = authenticated caller bot
TaskCtx = load(task_id)
current_originator = TaskCtx.current_originator
participants = TaskCtx.participants
matched_edges = active EdgeGrant(caller_id -> target_bot_id) whose originator_policy matches current_originator
```

然后给 target plugin 的不是“可永久使用的权限”，而是：

```text
InboundDispatchContext {
  task_id
  caller_id
  target_bot_id
  task_ctx_version
  originator_epoch
  min_auth_snapshot_version
}
```

插件执行 action 前再本地组合：

```text
InboundDispatchContext
+ AuthSnapshot(target_bot)
+ TaskCtxSnapshot(task_id)
+ runtime action mapped to canonical request
=> EffectivePermissionView
=> allow / deny
```

这就是“点边数据传递”的边界：

- 控制面保存完整点边事实；
- AuthSnapshot 按 target 裁剪边和角色；
- runtime dispatch 只传 task/caller/target/version 上下文；
- originator 每次从 TaskCtx 解析，不由 bot 传；
- action allow/deny 在 target plugin 执行前计算。

---

## 3. 四个身份词必须分清

| 词 | 含义 | 谁决定 | 用途 | 不能推出什么 |
| --- | --- | --- | --- | --- |
| `caller` | 本跳直接调用 target 的 actor | BCS/plugin 认证 | 找 `caller -> target` EdgeGrant | 不能推出它代表哪个 human |
| `target_bot` | 本跳被保护、即将执行能力的 bot | BCS 路由/协议 | 选择 target 的 RoleDef、EdgeGrant、AuthSnapshot | 不能由 LLM 参数自报 |
| `current_originator` | 当前 task 的 human 决策上下文 | BCS TaskCtx | 激活 EdgeGrant.originator_policy | 不能推出 caller 有边 |
| `participants` | 允许续接该 task_id 的 bot 集合 | BCS TaskCtx | 防止 bot 冒用别人的 task_id | 不能授予任何 capability |

关键不变量：

```text
caller 有 EdgeGrant
AND originator_policy matched
AND caller is participant
```

这三件事互不替代：

- `caller in participants` 只说明 caller bot 可续接这个 task；
- `originator_policy matched` 只说明某条边的 originator 条件满足；
- `caller -> target` 有 EdgeGrant 只说明这跳存在候选授权边；
- 最终 action 还要看 RoleDef / extra_rules / Platform Guard / adapter mapping。

---

## 4. TaskCtx：task_id 为什么安全

### 4.1 TaskCtx 数据模型

```text
TaskCtx {
  env                               # 环境隔离；dev/prod 不共享 task
  task_id                           # BCS 签发的不透明、高熵、不可枚举 id
  session_id                        # MVP 一个 session 最多一个 active task
  status: active | closed | expired # active 才能 dispatch / execute

  created_by                        # kickoff human；审计用
  current_originator                # 当前 human originator；MVP 必须是 active human
  originator_epoch                  # current_originator 每变化一次递增

  participants: Set<BotActorId>     # BCS 已交付过该 task 的 bot；不含 human
  task_ctx_version                  # TaskCtx 安全字段变化版本

  created_at
  updated_at
  expires_at
  closed_at
}
```

字段解释：

| 字段 | 为什么需要 |
| --- | --- |
| `task_id` | bot-to-bot continuation 的唯一句柄；bot 只能提交它，不能提交 originator。 |
| `current_originator` | 当前 task 权限上下文，用来匹配 `originator_policy`。 |
| `originator_epoch` | 防止旧 originator 缓存继续放权。 |
| `participants` | 防止 bot 拿别人的 task_id 冒用别人的 originator。 |
| `task_ctx_version` | 防止旧 TaskCtxSnapshot 继续使用。 |
| `status` | 防止 closed/expired task 被复用。 |

### 4.2 participants 是什么，怎样确定

`participants` 的精确定义：

```text
BCS 曾经把该 task 的可信上下文交付过的 bot 集合。
```

它不含 human。human 用 `created_by`、`current_originator` 和 audit 表达。

进入 participants 只有两种方式：

```text
1. Human kickoff 到 bot1：
   BCS 创建 TaskCtx，并加入 kickoff target bot1。

2. Bot-to-bot dispatch admission 成功：
   caller bot 已在 participants 中，且 caller->target EdgeGrant / originator_policy 通过后，
   BCS 才把 target bot 加入 participants。
```

不得出现：

```text
bot 自报 participants
bot 请求里带 participants += target
BCS 在 admission 失败时把 target 加入 participants
human 放进 participants
```

### 4.3 participants 如何防 task_id 冒用

攻击：bot9 拿到了 Alice task 的 `task_id=T`，想冒用 Alice 的 originator 调 bot3。

```text
TaskCtx T:
  current_originator = Alice
  participants = { bot1 }
```

bot9 发起：

```text
bot9 -> BCS:
  dispatch target=bot3, task_id=T
```

BCS 判断：

```text
caller_id = authenticated bot9
require bot9 in TaskCtx(T).participants
```

结果：

```text
bot9 not in {bot1}
=> deny
```

BCS 不会让 bot9 使用 `TaskCtx(T).current_originator = Alice`。

### 4.4 originator 规则

MVP 使用 **current_originator**，不是 kickoff 时固定不变的 originator。

```text
human kickoff:
  current_originator = kickoff human
  originator_epoch = 0

任何 human message:
  current_originator = message.author
  originator_epoch += 1
  task_ctx_version += 1

bot message:
  current_originator 不变
  originator_epoch 不变
```

MVP 不做“有效发言”判断：任何 human message 都更新 `current_originator`。

如果产品要保留初始发起人，可加：

```text
kickoff_originator                  # audit-only；不参与 MVP auth
```

### 4.5 task 生命周期

```text
active -> closed
active -> expired
```

规则：

- `closed/expired` task 不能 dispatch；
- `closed/expired` task 不能执行 delayed action；
- close 必须递增 `task_ctx_version`；
- 插件看到旧 TaskCtxSnapshot 必须刷新，刷新失败 deny；
- task_id 不复用。

---

## 5. 授权配置：RoleDef + EdgeGrant

### 5.1 RoleDef 是 target owner 定义的角色

`RoleDef` 是 target bot owner 给自己 bot 定义的具名权限集。

```text
RoleDef {
  env
  role_def_id                       # 稳定 id；EdgeGrant 引用 id，不引用展示名
  target_bot_id                     # 这个 role 属于哪个 target bot

  name                              # 展示名，如 repo_reader；可改
  description                       # 给申请/审批看的说明
  danger_level                      # normal/elevated/restricted；用于展示和审计
  is_builtin: owner | default | system | null
  status: draft | published | disabled

  semantic_version                  # 授权语义变化时递增
  metadata_version                  # 只展示变化时递增
  role_semantic_digest              # rules 等授权语义的 hash

  rules: RoleRule[]                 # capability + scope + decision

  created_by                        # MVP 必须是 target bot owner 或 system bootstrap
  created_at
  updated_at
}
```

`RoleRule`：

```text
RoleRule {
  bot_capability_id                 # 引用 target bot registry 中的能力
  scope {
    matcher                         # exact/glob；specifier 的匹配语义
    specifier                       # 能力范围值，如 /docs/**
  }
  decision: allow | deny            # BCS 只有 allow/deny，没有 ask
}
```

权限逻辑仍是三元组：

```text
capability + scope + decision
scope = matcher + specifier
```

`matcher` 不是第四个权限维度，但它影响 scope 语义，所以必须进入版本、digest、审批快照和审计。

### 5.2 EdgeGrant 是 caller->target 的授权事实

`EdgeGrant` 是 target owner 批准的一条授权事实：

```text
caller 在某 originator_policy 下，可以让 target bot 使用某 RoleDef 和/或 extra_rules。
```

```text
EdgeGrant {
  env
  edge_grant_id                     # 授权事实 id；同一 caller->target 可有多条
  caller_id                         # 本跳 immediate caller；human 或 bot
  target_bot_id                     # 被保护的 target bot

  role_def_id                       # nullable；null 表示匿名 custom / extra-only grant
  extra_rules: RoleRule[]           # 边级增量/覆盖规则；可为空

  originator_policy                 # DirectOnly / OriginatorIn；决定这条边何时激活
  delegation_depth                  # MVP 保留字段；不复制权限；可固定 0/null

  status: pending | active | rejected | revoked | expired
  expires_at

  applicant                         # 申请方；caller 是 bot 时通常是 caller bot owner
  approver                          # MVP 必须是 target bot owner
  grantor                           # MVP = approver
  approved_at

  approved_role_semantic_version    # 审批时 RoleDef 版本；role_def_id=null 时为空
  approved_role_digest              # 审批时 RoleDef digest；role_def_id=null 时为空
  approved_extra_rules_digest       # 审批时 extra_rules digest
  approved_effective_rules_digest   # RoleDef.rules + extra_rules 合成后的 digest
  approved_display_snapshot         # 审批页看到的名称/描述/风险/摘要

  revoked_by
  revoked_at
  revocation_reason
  audit_meta                        # purpose/reason/source 等；不参与 runtime matching
  created_at
  updated_at
}
```

### 5.3 role_def_id / extra_rules 的已定规则

历史已定结论必须保留：

```text
role_def_id != null, extra_rules empty:
  普通具名角色边。

role_def_id != null, extra_rules non-empty:
  role_with_extra；在 RoleDef 基础上加边级增量/覆盖。

role_def_id = null, extra_rules non-empty:
  anonymous/custom/单权限边。

role_def_id = null, extra_rules empty:
  invalid，不能发布/审批。
```

单条 EdgeGrant 内部合成：

```text
base_rules = RoleDef.rules if role_def_id != null else []
edge_rules = EdgeGrant.extra_rules

effective_edge_rules = base_rules overridden_by edge_rules
```

冲突规则：

```text
同一 capability + scope 上 RoleDef rule 与 extra_rules 冲突时：
extra_rules 优先。
```

例子：

```text
RoleDef repo_reader:
  deny file.write /tmp/**

EdgeGrant.extra_rules:
  allow file.write /tmp/session-123/**

=> 对 /tmp/session-123/a.txt，extra_rules allow 生效。
```

### 5.4 多 EdgeGrant 合并

同一 `caller -> target` 可以有多条 active EdgeGrant。

多边合并规则：

```text
1. Platform Guard hard deny 最高，命中即 deny。
2. 找所有 active 且 originator_policy matched 的 EdgeGrant。
3. 每条 EdgeGrant 先做 RoleDef.rules + extra_rules 合成。
4. 多条 EdgeGrant 之间 allow-union：任一 effective rule allow 命中则 allow。
5. 没有 allow 命中则 deny。
```

说明：

- 07/16 的“一时刻一个 channel 整体替换”不作为 MVP 最终模型；
- 后续结论采用“多 EdgeGrant 并存 + allow-union”；
- 但 `role_with_extra / custom / anonymous single-rule edge` 的表达能力保留。

### 5.5 owner / default / friend

builtin RoleDef：

```text
owner      target bot owner 的全权角色
 default    target owner 发布的默认低风险角色
system     系统迁移/内部保留角色
null       普通自定义角色
```

bootstrap：

```text
bot creator / owner gets:
  EdgeGrant(human_owner -> bot, role=owner, originator_policy=DirectOnly, active)
```

重要规则：

- `owner` grant 必须是 `DirectOnly`。owner 全权只能用于 owner 本人作为 `current_originator` 的直连/亲身语境，不能被 bot relay 借用成 owner 全权代理。
- owner 想允许某 bot 代表 Alice/Bob 使用 target，必须另建普通 RoleDef + `OriginatorIn([Alice/Bob])` 的 EdgeGrant。
- `default/friend` grant 默认也是 `DirectOnly`。它只表达直连熟人入场，不承担 relay 责任。
- 链中需要 default-like relay 时，target owner 必须显式批准 `caller bot -> target` 的 `OriginatorIn([...])` grant。

---

## 6. originator_policy

`originator_policy` 是 EdgeGrant 的激活条件。

它回答：

```text
当前 TaskCtx.current_originator 是否允许激活这条 caller->target 边？
```

MVP 只支持：

```text
DirectOnly
OriginatorIn([human_ids...])
```

### 6.1 DirectOnly

```text
matches iff caller_id == current_originator
```

用于 human 直达：

```text
caller = Bob
current_originator = Bob
=> DirectOnly matched
```

在 relay 中默认不激活：

```text
caller = bot1
current_originator = Alice
=> DirectOnly not matched
```

### 6.2 OriginatorIn

```text
matches iff current_originator in ids
```

用于明确 relay：

```text
EdgeGrant(bot1 -> bot3, role=lark_writer, OriginatorIn([Alice]))
```

含义：

```text
当当前 task originator 是 Alice 时，bot1 可以让 bot3 使用 lark_writer。
```

如果 Bob 在同一 task 中发言：

```text
current_originator = Bob
originator_epoch += 1
```

这条边不再激活，除非名单也包含 Bob。

### 6.3 谁批准 originator_policy

`originator_policy` 由 **target bot owner** 批准。

caller 可以申请：

```text
requested_originator_policy = OriginatorIn([Alice])
```

但 active EdgeGrant 中真正生效的 `originator_policy` 必须来自 target owner 的 approve decision。

MVP 中：

- `OriginatorIn` 只允许 active human ActorId；
- 不要求名单中的 human 另行 consent；是否加 human consent 属 future/product policy；
- 审批时不支持修改申请内容，target owner 想改则 reject 后重申。

### 6.4 MVP 不支持的 policy

MVP 不支持：

```text
Any
OriginatorType(human | bot | service)
has-edge-to-target
custom predicate
```

原因：

- `Any` 风险太高；
- `OriginatorType` 会让新增 actor 自动获得激活资格；
- `has-edge-to-target` 需要跨边查询，增加缓存一致性和解释复杂度；
- custom predicate 会让 MVP 失去可审计、可 conformance 的简单语义。

---

## 7. 能力语言、Registry 与 Adapter

### 7.1 统一能力语言

BCS 使用统一权限语言：

```text
capability + scope + decision
```

其中：

```text
scope = matcher + specifier
```

例子：

```text
file.read + glob:/workspace/docs/** + allow
shell.exec + exact:["git", "status"] + allow
mcp.tool.invoke + exact:{server:"github", tool:"create_issue"} + allow
```

`decision` 只有：

```text
allow | deny
```

没有 `ask`。bot runtime 本地 ask 策略不属于 BCS cooperation auth。

### 7.2 CanonicalCapabilityCatalog

`CanonicalCapabilityCatalog` 是平台统一能力词典。

```text
CanonicalCapability {
  canonical_capability_id          # 如 file.read / shell.exec / mcp.tool.invoke
  description                      # 能力语义说明
  specifier_schema                 # specifier 的结构/schema
  allowed_matchers                 # exact/glob 等
  default_danger_level             # normal/elevated/restricted
  version
  digest
}
```

它回答：

```text
平台里 file.read / shell.exec / skill.invoke 这些能力到底是什么意思？specifier 应该怎么写？
```

### 7.3 RuntimeAdapterSupport

不同 runtime 的原生动作不同。adapter 负责映射：

```text
runtime native action -> canonical capability request
```

例子：

```text
OpenCode Read(path) -> file.read + path
OpenCode Edit(path) -> file.write + path
MCP github.create_issue -> mcp.tool.invoke + {server:"github", tool:"create_issue"}
```

`RuntimeAdapterSupport` 声明某 adapter 能安全支持哪些 canonical capability：

```text
RuntimeAdapterSupport {
  adapter_id                       # opencode / claudecode / openclaw / ...
  adapter_version                  # adapter 代码版本
  mapping_version                  # native->canonical 映射语义版本
  supported_canonical_capabilities # 能准确映射的 canonical capability
  supported_matchers               # exact/glob 等
}
```

无法准确映射：

```text
deny
```

不能近似放行。

### 7.4 BotCapabilityRegistry

`BotCapabilityRegistry` 是某个 target bot 实际可授权能力边界。

它由三者交集形成：

```text
CanonicalCapabilityCatalog
∩ RuntimeAdapterSupport
∩ target bot owner enabled capabilities
```

```text
BotCapability {
  env
  target_bot_id
  bot_capability_id                # target 内稳定 id；RoleRule 引用它
  canonical_capability_id          # 指向平台统一能力

  specifier_domain                 # 这个 capability 最大可授权范围
  allowed_matchers                 # 这个 capability 允许 RoleRule 使用的 matcher
  danger_level                     # 风险等级；不得低于 canonical 默认风险
  status: enabled | disabled

  adapter_id
  adapter_version
  mapping_version
  canonical_capability_catalog_version
  version
  digest
}
```

### 7.5 specifier_domain 是什么

`specifier_domain` 是 BotCapability 的最大授权边界。

例子：

```text
BotCapability:
  bot_capability_id = bot3.file_read_workspace
  canonical_capability_id = file.read
  specifier_domain = glob:/workspace/**
```

意思：

```text
RoleDef 最多只能授权 bot3 读取 /workspace/** 内的文件。
```

RoleRule 只能在 domain 内收窄：

```text
allow file.read glob:/workspace/docs/**     # OK
allow file.read glob:/etc/**                # reject
```

发布 RoleDef 时必须能证明：

```text
RoleRule.scope ⊆ BotCapability.specifier_domain
```

判断结果：

```text
true        => 通过
false       => 拒绝
undecidable => 拒绝；MVP fail closed
```

实现里可以有检查函数，但文档主语义只需要表达：RoleRule 的 scope 必须能被证明落在 BotCapability 的 domain 内；证明不了就拒绝。

### 7.6 为什么 BotCapability 要记录 adapter 来源

`BotCapabilityRegistry` 不是手写权限表。它来自 adapter 对 runtime 能力的识别与映射。

被批准的不是孤立字符串 `file.read`，而是：

```text
在 adapter_id / adapter_version / mapping_version / catalog_version 这组语义下，
某 runtime 原生动作会被解释成某 canonical capability。
```

如果 mapping 变了，旧授权不能静默沿用。

例子：

```text
旧 mapping:
  Bash("cat docs/a.md") => file.read /docs/a.md

新 mapping:
  Bash("cat docs/a.md && curl ...") => shell.exec + network.access
```

如果继续沿用旧 registry，插件可能把更危险的新动作当成旧 `file.read` 放行。

因此 adapter/mapping/catalog 语义变化时：

```text
1. affected BotCapability version bump or disabled
2. affected RoleDef semantic_version/digest recomputed
3. affected EdgeGrant revoke or reapproval required, unless provably safe-narrowed
4. AuthSnapshot version bump and redistribute
5. compiled matcher / EffectivePermissionView invalidated
```

---

## 8. AuthSnapshot、TaskCtxSnapshot、EffectivePermissionView

这三个东西经常混淆，先给结论：

```text
AuthSnapshot = target bot 的授权事实快照，不是 task auth。
TaskCtxSnapshot = 某个 task 的动态上下文快照。
EffectivePermissionView = 当前 task/caller/originator 下派生出的有效权限视图。
```

### 8.1 AuthSnapshot 下发什么

`AuthSnapshot(env, target_bot_id, version)` 按 target bot 下发。

它包含：

```text
AuthSnapshot {
  env
  target_bot_id
  auth_snapshot_version

  catalog_version / digest          # canonical capability 语义版本
  adapter_id / adapter_version
  mapping_version

  bot_capability_registry           # target bot 可授权能力边界
  role_defs                         # target owner 定义的 published RoleDef
  active_edge_grants_to_target      # 指向 target 的 active EdgeGrant
  platform_guard                    # 平台 hard deny

  issued_at
  expires_at
  integrity_digest / signature
}
```

它不包含：

```text
task_id
current_originator
participants
originator_epoch
task status
```

这些属于 `TaskCtxSnapshot`。

### 8.2 AuthSnapshot 什么时候下发/刷新

AuthSnapshot 不是按 task 下发。它按 target bot 下发/刷新：

```text
1. target plugin connect / reconnect
2. RoleDef / EdgeGrant / extra_rules / BotCapability / PlatformGuard 变化
3. adapter_id / adapter_version / mapping_version / catalog version 变化
4. target bot status / owner security status 变化
5. TTL 到期刷新
6. 执行前发现本地 snapshot 低于 min_auth_snapshot_version
```

刷新失败：

```text
deny
```

### 8.3 AuthSnapshot 什么时候失效

任一情况失效：

```text
EdgeGrant active/revoked/expired/changed
EdgeGrant.extra_rules changed through reapproval
RoleDef semantic_version/digest changed
BotCapability version/status/domain changed
adapter/mapping/catalog version changed
PlatformGuard changed
target bot status changed
snapshot expired
snapshot digest/signature invalid
snapshot missing required referenced object
```

失效后必须同时废掉：

```text
compiled matcher
runtime-local permission profile
EffectivePermissionView
cached allow/deny decision
```

### 8.4 TaskCtxSnapshot

`TaskCtxSnapshot` 是插件拿到的 task 动态上下文副本：

```text
TaskCtxSnapshot {
  env
  task_id
  task_ctx_version
  status
  current_originator
  originator_epoch
  participants_digest              # 可选；用于证明 caller/target 关系，不一定暴露全集
  issued_at
  expires_at
  integrity_digest / signature
}
```

它用于回答：

```text
当前 task 是否 active？
当前 originator 是谁？
originator_epoch 是否已经变化？
```

### 8.5 EffectivePermissionView

`EffectivePermissionView` 是插件本地派生缓存。

```text
EffectivePermissionView {
  env
  task_id
  caller_id
  target_bot_id
  current_originator
  originator_epoch

  auth_snapshot_version
  task_ctx_version
  adapter_id / adapter_version / mapping_version

  active_edge_grant_ids
  effective_rules                  # 已合成 RoleDef.rules + extra_rules
  compiled_matcher_refs            # 可引用预编译 matcher
  expires_at
}
```

它回答：

```text
在这个 task、这个 caller、这个 current_originator 下，target bot 当前真正生效哪些规则？
```

派生过程：

```text
1. 从 AuthSnapshot 找 caller -> target 的 active EdgeGrant。
2. 用 TaskCtxSnapshot.current_originator 匹配 originator_policy。
3. 对每条 matched EdgeGrant 合成 RoleDef.rules + extra_rules。
4. 多条 EdgeGrant allow-union。
5. 得到本次上下文的 effective rules。
```

### 8.6 预编译 matcher 与 EffectivePermissionView 的区别

预编译 matcher：

```text
把规则变成更快的匹配器。
```

EffectivePermissionView：

```text
决定当前 task/caller/originator 下哪些规则真的生效。
```

关系：

```text
AuthSnapshot rules -> 可预编译成 matcher
EffectivePermissionView -> 可引用这些 matcher
```

但预编译 matcher 不能绕过 EffectivePermissionView 直接 allow。

例子：

```text
AuthSnapshot 有 repo_reader matcher。
但当前 originator=Bob，而 grant 只允许 OriginatorIn([Alice])。
=> EffectivePermissionView active rules 为空。
=> 即使 matcher 还在，也不能放行。
```

---

## 9. Runtime flow

### 9.1 Human kickoff

```text
Alice -> BCS -> bot1
```

BCS：

```text
1. authenticate Alice
2. create/update active TaskCtx
3. current_originator = Alice
4. originator_epoch = 0 or += 1
5. participants includes bot1
6. deliver InboundDispatchContext to bot1
```

Human direct to bot 仍需要 EdgeGrant：

```text
EdgeGrant(Alice -> bot1, DirectOnly, active)
```

owner/bootstrap grant 可以由系统生成。

### 9.2 Bot-to-bot dispatch admission

bot outbound request：

```text
DispatchRequest {
  target_bot_id                    # bot 想调用谁
  task_id                          # 必须携带；MVP 不支持 bot fresh/self-start
  payload                          # 业务消息；不可信，不含 auth source of truth
}
```

不得包含：

```text
originator
caller_id override
participants
active_edge_ids
permission result
```

BCS admission：

```text
1. authenticate caller bot
2. require task_id present
3. load TaskCtx(task_id)
4. require TaskCtx.status == active
5. require caller bot in TaskCtx.participants
6. current_originator = TaskCtx.current_originator
7. find active EdgeGrant(caller -> target)
8. require originator_policy matches current_originator
9. if allow: add target to participants and deliver
10. if deny: do not modify participants
```

MVP 规则：

```text
bot-to-bot dispatch without task_id => deny
caller not in participants => deny
bot self-reported originator => ignore or reject
```

### 9.3 Target plugin before-execution auth

每次 runtime action 前：

```text
1. receive runtime native action
2. adapter maps native action -> canonical capability request
3. if unsupported/unmappable => deny
4. ensure valid AuthSnapshot
5. ensure valid TaskCtxSnapshot
6. derive or load EffectivePermissionView
7. apply Platform Guard
8. apply effective rules
9. emit audit
10. return allow/deny
```

不能把 dispatch admission allow 当成 tool allow。

### 9.4 Retry / queue / delayed execution

延迟执行必须重新鉴权：

```text
retry / queued / delayed action
=> refresh/check AuthSnapshot
=> refresh/check TaskCtxSnapshot
=> recompute EffectivePermissionView if keys changed
=> then allow/deny
```

如果 originator_epoch 变化，必须用新 originator 重新判断。

---

## 10. 生命周期与失效

### 10.1 RoleDef 变化

```text
rules / scope / decision changed
BotCapability reference changed
semantic_version changed
status disabled
```

影响：

```text
RoleDef digest recomputed
EdgeGrant effective digest mismatch
AuthSnapshot version bump
EffectivePermissionView invalidated
reapproval required unless provably safe-narrowed
```

### 10.2 EdgeGrant 变化

```text
pending -> active
active -> revoked
active -> expired
originator_policy changed through reapproval
role_def_id changed through reapproval
extra_rules changed through reapproval
```

影响：

```text
AuthSnapshot version bump
compiled matcher invalidated if rules changed
EffectivePermissionView invalidated
```

### 10.3 Adapter / mapping / registry 变化

```text
adapter_id changed
adapter_version changed
mapping_version changed
canonical catalog semantic changed
BotCapability domain/status changed
```

影响：

```text
affected BotCapability version bump or disabled
affected RoleDef semantic_version/digest recomputed
affected EdgeGrant revoked or requires reapproval
AuthSnapshot version bump
EffectivePermissionView invalidated
```

### 10.4 TaskCtx 变化

```text
current_originator changed
participants changed
status changed
task expired/closed
```

影响：

```text
task_ctx_version bump
originator_epoch bump if originator changed
TaskCtxSnapshot invalidated
EffectivePermissionView invalidated
```

---

## 11. Fail-closed 规则

必须 deny 的情况：

```text
missing task_id for bot-to-bot dispatch
TaskCtx missing / closed / expired
caller bot not in TaskCtx.participants
bot request contains originator/caller override and policy is reject-on-present
no active EdgeGrant(caller -> target)
originator_policy not matched
role_def_id and extra_rules both empty
RoleDef disabled / missing / digest mismatch
EdgeGrant expired / revoked / pending / rejected
AuthSnapshot missing / expired / signature invalid / version too old
TaskCtxSnapshot missing / expired / signature invalid / version too old
adapter cannot map runtime action to canonical request
capability unsupported or disabled
RoleRule.scope cannot be proven inside BotCapability.specifier_domain
Platform Guard hard deny matched
audit required but cannot be durably recorded
```

原则：

```text
unsupported != allow
unknown != allow
undecidable != allow
stale != allow
```

---

## 12. 审计

BCS 平台是授权事实的审计权威；target 插件是执行前 allow/deny 的审计来源。

每次 decision 至少记录：

```text
AuthorizationDecisionAudit {
  env
  task_id
  caller_id
  target_bot_id
  current_originator
  originator_epoch

  auth_snapshot_version
  task_ctx_version
  adapter_id / adapter_version / mapping_version

  canonical_capability_id
  bot_capability_id
  scope_hash                         # 不直接泄漏敏感 specifier
  decision: allow | deny
  reason_code

  matched_edge_grant_ids
  matched_role_def_ids
  matched_rule_ids
  platform_guard_rule_id

  created_at
}
```

敏感字段处理：

- 文件路径、命令、URL、MCP input 不应默认明文进日志；
- 可记录 normalized hash / redacted summary；
- 调试需要明文时必须有单独开关、TTL 和访问控制。

---

## 13. 验收场景

### 13.1 Bob 直达 bot3

```text
caller = Bob
current_originator = Bob
EdgeGrant(Bob -> bot3, repo_reader, DirectOnly, active)
request = file.read /docs/a.md
=> allow if RoleDef allows and Guard does not deny
```

### 13.2 Alice 经 bot1 调 bot3

```text
TaskCtx.current_originator = Alice
participants = { bot1 }
EdgeGrant(bot1 -> bot3, lark_writer, OriginatorIn([Alice]), active)

bot1 dispatch bot3 with task_id
=> admission allow, participants += bot3
bot3 action lark.message.send
=> allow if lark_writer allows and Guard does not deny
```

### 13.3 Alice 直连有权限，不代表 bot1 有权限

```text
EdgeGrant(Alice -> bot3, repo_reader, DirectOnly, active)
No EdgeGrant(bot1 -> bot3)

bot1 dispatch bot3 with Alice task
=> deny
```

### 13.4 participants 防冒用

```text
TaskCtx T:
  current_originator = Alice
  participants = { bot1 }

bot9 dispatch target=bot3 task_id=T
=> caller bot9 not in participants
=> deny
```

### 13.5 extra_rules 覆盖 RoleDef

```text
RoleDef:
  deny file.write /tmp/**

EdgeGrant.extra_rules:
  allow file.write /tmp/session-123/**

request file.write /tmp/session-123/a.txt
=> allow, because extra_rules override RoleDef conflict inside same EdgeGrant
```

### 13.6 role_def_id=null 匿名边

```text
EdgeGrant:
  role_def_id = null
  extra_rules = [allow file.read /handoff/**]
  originator_policy = OriginatorIn([Alice])

Alice task, caller matched
=> /handoff/** read can be allowed
```

### 13.7 Bob 插话导致旧权限失效

```text
T0 current_originator = Alice, originator_epoch = 0
EdgeGrant(bot1 -> bot3, lark_writer, OriginatorIn([Alice])) matched

T1 Bob sends human message
current_originator = Bob
originator_epoch = 1

bot3 delayed action executes
=> must refresh TaskCtxSnapshot
=> OriginatorIn([Alice]) no longer matched
=> deny
```

### 13.8 adapter mapping 变化

```text
adapter mapping_version changes
BotCapability affected
RoleDef digest recomputed
EdgeGrant cannot silently stay active unless safe-narrowed is proven
AuthSnapshot version bump
EffectivePermissionView invalidated
```

---

## 14. MVP 不支持

MVP 不支持：

```text
multiple active tasks in same session
bot fresh/self-start task
bot as originator without human task
service/system as caller or originator
Any / OriginatorType / has-edge-to-target originator_policy
runtime-time permission application/approval
approval-time modification of application content
custom adapter mapping by bot owner
unsupported capability approximation
checkpoint / lease renewal during long-running tool execution
bot local ask/deny/allow policy design
```

这些未来扩展不得改变 MVP 主不变量：

```text
每跳独立 EdgeGrant
BCS resolves originator from TaskCtx
participants only gates task continuation
plugin performs before-execution auth
fail closed on unknown/stale/unsupported
```
