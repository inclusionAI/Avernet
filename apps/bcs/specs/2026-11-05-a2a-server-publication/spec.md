# BCS 作为 A2A 注册中心与 A2A Server：Bot 发布与权限设计

日期：2026-11-05

状态：设计草案，待评审；不授权实施。本文路径相对 `apps/bcs/`。

## 1. 背景与问题

BCS 目前对 A2A 只有**入口方向**的能力：

- `bcs-app-a2a-bot` + `A2aProviderRegistrationCore`：Provider 用 AgentCard（URL 或
  inline）注册 Gateway 模式 Bot，快照存进 `A2aBotRecord`（带 `revision` 的 CAS）。
- `bcs-a2a-http`：BCS 作为 **A2A Client**，把群聊/私聊消息投递给远端 A2A Agent
  （JSON-RPC / gRPC、`contextId` 映射、remote task、abort）。
- Bot 查询（detail/discover/search）可以带出 `agent_card`，但只经过 BCS 自己的
  可见性/好友检查，不对外提供标准 A2A 端点。

缺失的部分是**反方向**：把 BCS 里的任意一个 Bot（WS 插件 Bot、HTTP Provider
Bot、A2A 注册 Bot）**发布**成一个标准 A2A Server，让外部 A2A Client 能够：

1. 在 BCS 这个**注册中心**里发现 AgentCard；
2. 按标准 A2A 协议（`SendMessage` / `SendStreamingMessage` / `GetTask` /
   `CancelTask` / `SubscribeToTask` / 认证扩展卡）调用它；
3. 调用时继续受 BCS 现有的可见性、好友（edge grant）、owner/manager 权限约束。

同时，Agent 的部署形态并不统一，权限语义必须区分：

| 形态 | 例子 | 一个实例服务多少人 | 以谁的身份执行 |
| --- | --- | --- | --- |
| 个人型 | Claude Code / OpenClaw，每人一个 | 1 个 owner | owner |
| 共享服务型 | LangGraph 远端服务，多人共用 | N 个调用者 | 调用者 |
| 每人一实例（模板） | AgentPass subbots：一个父 Agent 派生每人的子 Bot | 每实例 1 人 | 实例 owner（= 调用者本人） |

## 2. 目标与非目标

目标：

- 定义"发布"（Publication）这一 BCS 资源：哪个 Bot、以什么模式、以什么卡片、对谁可见。
- BCS 提供 A2A 注册中心查询接口和每个已发布 Agent 的标准 A2A 端点。
- 把现有 `edge_grants`（好友/profile/rules）、`visibility`、owner/manager、
  caller execution 映射到 A2A Server 的发现、调用、任务读取三个层面。
- 按三种 Agent 形态给出默认权限策略与执行身份规则。

非目标：

- 不修改 A2A 协议本身；BCS 专有信息只走 A2A 允许的 `metadata` / extensions。
- 不改变现有好友模型（D1–D13，见
  `docs/superpowers/specs/2026-08-18-friend-edge-permission-reform.md`）和
  owner/manager 模型（`docs/superpowers/specs/2026-10-10-bot-authority-simple-design.md`）。
- 本期不做跨注册中心联邦、计费、A2A push notification（放到 P3）。

## 3. 术语

| 术语 | 含义 |
| --- | --- |
| Publication（发布） | 一个 Bot 对外暴露为 A2A Agent 的配置记录，Bot 与发布 1:1 |
| Published Card | BCS 对外返回的 AgentCard，由来源卡片/Bot 能力生成并改写 |
| Source Card | A2A 注册 Bot 存在 `A2aBotRecord.agent_card` 里的原始卡片 |
| A2A Client Actor | 不在 BCS 里注册为 Bot/Human 的外部调用方（第三方 App/Agent），BCS 新增的 actor 类型 |
| 直接调用方 `from` | 发起 A2A 请求的主体（Human / Bot / Client） |
| 发起人 `originator` | 最终负责的 Human；Agent 代人调用时 ≠ `from` |
| 执行身份 | 目标 Bot 处理请求时使用谁的权限/数据：owner 或 caller |

`from`/`to`/`originator` 直接复用 `EdgeGrant.originator_policy_*` 和
`AdmissionService::check_admission(actor, bot, originator, env)` 已有的三元组语义。

## 4. 总体方案

### 4.1 两种发布模式

| 模式 | BCS 在数据面上吗 | 适用 Bot | 能强制好友权限吗 |
| --- | --- | --- | --- |
| `proxy`（托管，默认） | 是：BCS 托管 A2A 端点，转译为内部消息流再投递 | 所有 Bot（WS、HTTP Provider、A2A） | 能，每次请求都做准入检查 |
| `registry_only`（仅登记） | 否：卡片 `url` 指向源 Agent | 仅源卡片可公网访问的 A2A Bot | 只控制**发现**；调用面要靠源 Agent 校验 BCS 签发的访问令牌（§6.5） |

推荐默认 `proxy`。原因：WS/插件 Bot 本身没有 A2A 端点，只能 proxy；好友、
caller execution、审计、撤权实时生效都依赖 BCS 在数据面上。`registry_only`
用于已经有成熟网关和鉴权的企业 Agent，BCS 只做目录。

### 4.2 组件与调用方向

```
外部 A2A Client
   │  GET  /a2a/agents/{slug}/.well-known/agent-card.json
   │  POST /a2a/agents/{slug}            (JSON-RPC, SSE)
   │  GET  /a2a/registry/agents          (注册中心查询)
   ▼
adapters/http/bcs-a2a-server   ← A2A wire 类型 / SDK 类型只在这一层
   │ 只调 application
   ▼
application::a2a_publication   (发布管理 / 注册中心查询)
application::a2a_server        (入站 task 用例：send/stream/get/cancel/subscribe)
   │
   ├── core::a2a_publication::A2aPublicationCoreService   (services/bcs-a2a-publication)
   │      └── port::repo::a2a_publication                   (services/bcs-a2a-publication-store)
   ├── AdmissionService / EdgeGrant 端口（现有）
   ├── BotAuthority（owner/manager，现有）
   ├── CallerIdentityPlugin（现有 JWT 实现，签发 caller execution proof）
   └── MessageFlow + BotDeliveryPort（现有：WS / HTTP Provider / bcs-a2a-http）
```

和既有分层一致：A2A 协议定义归 delivery adapter，不进 `service-api`；Core 只依赖
repo 端口；出站投递复用现有 `BotDeliveryPort`，不新开第二条投递链路。

### 4.3 发布状态机

```
draft ──publish──▶ published ──suspend──▶ suspended ──resume──▶ published
  ▲                    │                       │
  └──── edit ──────────┤                       │
                       └──────unpublish────────┴──▶ unpublished（保留行，slug 冷却期内不复用）
```

- 只有 Bot 的 owner/manager（`can_manage`）可以 publish/edit/suspend/unpublish；
  Provider admin 可以 suspend 自己名下的 Bot（运维止血），但不能 publish（发布是对外承诺，属于 Bot 业务所有人）。
- Bot 删除、Provider 禁用、Bot `status=hidden` 时，发布自动视为不可调用（读时判定，不做后台扫描）。
- 源卡片刷新（`refresh_card` 生成新 `revision`）后，Published Card 在读时按
  `(publication.revision, source_card.revision)` 重新生成并缓存，不需要重新发布；
  但**新增 skill 不会自动对外开放**（见 §6.3 的 skill 白名单）。

## 5. Published Card 生成规则

| 字段 | A2A 注册 Bot（有 Source Card） | WS / HTTP Provider Bot（无卡片） |
| --- | --- | --- |
| `name` / `description` | 源卡片，可由 publication 覆盖 | `BotCapabilities.name/summary` |
| `skills` | 源卡片 skills ∩ publication 白名单 | `skills`/`domains` 合成 `AgentSkill`（`tags` = domains） |
| `url` / `supportedInterfaces` | proxy：改写为 `{public_base_url}/a2a/agents/{slug}`；registry_only：保留源 URL | BCS 端点 |
| `capabilities.streaming` | proxy 下恒为 true（BCS 负责 SSE）；registry_only 保留源值 | true |
| `capabilities.pushNotifications` | P3 前恒为 false | false |
| `securitySchemes` / `security` | **丢弃源值**，替换为 BCS 的方案（§6.1）；源 Agent 的鉴权只存在于 BCS→源 的出站链路 | BCS 方案 |
| `provider` | `{organization: <部署配置>, url: <public_base_url>}` | 同左 |
| `supportsAuthenticatedExtendedCard` | protected 发布为 true | 同左 |
| `signatures` | P2 起用 BCS 卡片签名密钥签 JWS（与 caller identity 签名密钥分开） | 同左 |
| 未知字段 | 原样保留（与现有 `agent_card` 透传策略一致） | — |

原则：**Published Card 永远不包含 Provider 的出站凭证、内部 URL、`provider_bot_ref`、
owner 工号**。生成函数是纯函数，放在 `bcs-domain`，用一致性测试锁定。

## 6. 权限设计

权限分三个层面，按请求先后检查：**发现 → 调用准入 → 任务/会话读取**。另外每个
Bot 有一个**执行身份**维度，决定下游以谁的权限干活。

### 6.1 调用方身份（认证）

| 调用方 | 认证方式 | BCS actor | 说明 |
| --- | --- | --- | --- |
| BCS Human（工作台/CLI） | Gateway 签名的 `X-Avernet-Principal`，或 BCS OAuth 会话 | `human_<user_id>` | `from = originator = human` |
| BCS Bot | Bot runtime token / AgentPass | `bot_uuid` | `originator` 取 Bot 当前 run 的发起人；没有则取 Bot owner |
| 外部 A2A Client（第三方 Agent/App） | OAuth2 `client_credentials`，客户端在 BCS 登记 | `client_<client_id>`（新 `ActorKind::Client`） | 只能以自己名义调用；`originator` = 客户端登记的负责人 |
| 外部 Client 代某个 Human | OAuth2 授权码 / RFC 8693 token exchange，令牌同时带 `sub=human`、`act=client` | `from = client_*`，`originator = human_*` | 需要该 Human 显式授权过这个 Client（consent 也是一条边，见下） |

Published Card 的 `securitySchemes` 对应声明 `oauth2`（client_credentials +
authorizationCode）和 `openIdConnect`；内部 Bot/Human 继续走现有 bearer。
令牌的 `aud` 必须是 `a2a:{publication_id}` 或注册中心，**不能**拿 BCS 其它 API 的
令牌直接调用 A2A 端点，反过来也一样。

新增 `ActorKind::Client` 后，外部客户端也能当作 `edge_grants.from_id` 参与授权，
不需要另造一套 ACL。Client 登记建议复用 Gateway 的 App/AccessKey 主体
（`app_name` 精确匹配，跟 authority 设计 §3.1 一致），BCS 只存
`client_<id>` actor 行和负责人。

### 6.2 发现层（注册中心与卡片可见性）

沿用 Bot 已有的 `visibility`，另加一个发布级别的 `listed` 开关（是否出现在目录列表里）：

| Bot visibility | 公开卡片 `/.well-known/agent-card.json` | 目录 `/a2a/registry/agents` | 认证扩展卡（完整 skills） |
| --- | --- | --- | --- |
| `public` | 匿名可读 | `listed=true` 时所有已认证调用方可见 | 所有已认证调用方 |
| `protected` | 匿名可读**精简卡**（name、description、securitySchemes，不含 skills 细节） | 已认证调用方可见（显示"需建立好友"） | 只有准入通过（有边或 public_default）的调用方 |
| `private` | 404（不暴露存在性） | 不可见 | 只有 owner/manager 和已有边的调用方 |

目录查询必须**批量**过滤：一次查发布 + Bot 配置（分页，有上限），一次用
`from IN (caller) AND to IN (page bot ids)` 查边，避免 N+1。`private` 的不存在
与无权限都统一返回 404。

### 6.3 调用准入层（好友关系怎么体现）

每个 JSON-RPC 请求进入 `application::a2a_server` 后按顺序执行：

1. **发布状态**：`published`，且 Bot 未删除、未 hidden、Provider 未禁用 → 否则返回 A2A
   错误 `-32001 TaskNotFound` 或 HTTP 404（不泄露原因）。
2. **准入**：`AdmissionService::check_admission(from, bot, originator, env)`，规则与现有一致：
   `hidden` → 拒；有已批准的边（且 `originator_policy` 匹配）→ 放行；`public`
   → `public_default`；否则拒。拒绝时返回 HTTP 403 / JSON-RPC `-32003`（BCS 扩展错误码），
   `data = {reason_code: "no_edge", connect: "<connect 请求入口>"}`。
3. **Skill 授权**：边对应的 profile/rules 用 `Rule { tool: "a2a.skill", operation: <skill_id> }`
   表达。
   - default profile（好友边）= wildcard allow，等价于"能用所有**已发布** skill"；
   - 非 default profile（如 `reader` / `consult`）只放行列出的 skill；
   - A2A 消息本身不强制声明 skill，所以约定 `message.metadata["avernet.skill_id"]`；
     当生效 profile 不是 wildcard 时，必须带这个字段，否则拒绝。BCS 只做这一层粗粒度强制，
     细粒度判断通过 `AuthzContext.grants` 交给 Bot。
4. **执行身份 / 下游凭证**：见 §6.5。
5. 把 `AuthzContext`（现有类型，含 `from_id`/`to_id`/`originator`/`grants`）注入投递给
   Bot 的消息；Bot 只能看到 grant 引用，看不到 `EdgeGrant` 内部字段。

**好友关系在 Server 侧的对应**：

| BCS 现有概念 | A2A Server 上的含义 |
| --- | --- |
| Human→Bot 单向边（D3） | 这个 Human 可以调用这个已发布 Agent |
| Bot↔Bot 双向边（D3） | 两个 BCS Bot 之间互为 A2A Client/Server |
| 新增：Client→Bot 单向边 | 外部 Agent 可以调用；由 Bot owner/manager 审批，或 `connect_auto_approve` |
| 新增：Human→Client consent 边（`GrantKind::Rules`，`rules` 记录允许代理的 publication 范围） | Human 授权某个外部 Client 代自己调用（token exchange 的前提） |
| `connect_auto_approve` / `friend_check_in_strategy=OPEN` | A2A 调用方首次 connect 自动批准 |
| `APPROVAL` | connect 进入 owner 收件箱（现有 `permission_requests` + human-notify） |
| `DEPT_FREE` | 同组织调用方自动批准（组织判定复用 `bcs-organization`） |
| 撤销边 | 下一次请求即拒绝；进行中的 stream 在下一次事件推送前复查并以 `canceled` 结束（检查有缓存时，缓存 TTL 就是撤权生效上限，须写明并测试） |

Connect 入口：A2A 规范里没有"申请权限"方法，我们提供 BCS 扩展方法
`avernet/connect`（在卡片 `capabilities.extensions` 里声明 URI），内部就是创建
`PermissionRequest { kind: Connect, from: <caller>, to: bot }`。不支持扩展的 Client
可以走工作台/REST 的 connect 入口。

### 6.4 任务与会话读取层

内部映射：一个 A2A `contextId` 对应一个 BCS 私聊会话，一个 A2A task 对应一次 chat run。

- 会话隔离键 = `(publication_id, from, originator, contextId)`。**不同调用方即使传同一个
  `contextId` 也落到不同会话**；`contextId` 由 BCS 生成，客户端传入未知值时新建会话，
  不会接入别人的会话。
- `GetTask` / `SubscribeToTask` / `CancelTask`：只允许原调用方（`from` 与 `originator` 都相同）
  或该 Bot 的 owner/manager；其他人一律返回 `TaskNotFound`。
- 个人型 Bot 的 owner 能在工作台看到所有外部调用的会话（他是被调用的人）；
  共享服务型 Bot 的 owner/manager **默认看不到调用者的消息内容**，只看元数据（谁、何时、
  结果状态），避免共享服务的运营者看到所有用户的数据。是否允许查看内容由发布配置
  `owner_transcript_access: full | metadata` 决定，共享型默认 `metadata`。

### 6.5 执行身份：三种 Agent 形态的区别

这是权限设计里最核心的维度。发布记录增加 `tenancy` 字段：

```
tenancy = personal | shared | per_user_instance
```

并与现有配置做一致性校验（不一致就拒绝发布）：`shared` 必须开启
`bot_info.$.caller.caller_identity.enabled = true`；`per_user_instance` 只允许
`agentpass-subbots` Provider 的父 Agent。

#### A. 个人型（`personal`，如 Claude Code / OpenClaw）

- Bot 以 **owner 的身份和数据**执行，调用方只是"借用 owner 的助手"。风险：别人能驱动
  owner 的 Agent 去读 owner 的文件、用 owner 的凭证。
- 默认策略：
  - 默认 `visibility=protected`、`listed=false`；
  - 好友边（default profile）对**外部调用**默认**不是** wildcard：发布时生成一个
    `a2a_consult` profile（只放行 owner 勾选的 skill），connect 审批时默认挂这个 profile；
    owner 想全开放时显式选 default；
  - 敏感 skill（owner 标记）在执行前走现有 human-notify 人工确认（HITL），状态映射为
    A2A `input-required` / `auth-required`；
  - 每个调用方的并发/频率限制（防止别人耗尽 owner 的配额）。
- 投递给 Bot 的是 `caller_context`（调用方是谁，信息性），**不签发** caller execution
  token：执行身份就是 owner，不能让调用方的身份去"冒充"成执行身份。
- 适配：Bot 必须在提示/工具层区分"owner 的指令"与"外部调用者的请求"。BCS 在消息里用
  `AuthzContext.originator != owner` 标明，供 Bot 侧安全策略使用。

#### B. 共享服务型（`shared`，如 LangGraph 远端服务）

- Bot 以**调用者的身份**执行；服务自己负责按用户隔离数据（线程、记忆、下游 API）。
- BCS 的职责是"谁能用"（准入）和"告诉服务这是谁"（可信身份）：
  - 准入通常很宽：`visibility=public` + `listed=true`，或组织内 `DEPT_FREE`；
    边在这里的含义是**使用资格**（订阅），而不是"私人好友"；
  - 每次请求复用现有 caller execution 链路：BCS 用 `CallerIdentityPlugin` 签发短时
    `caller_execution_token`（EdDSA JWT，`aud=bot_runtime:<bot>`，`sub` 是按
    env 区分的 HMAC 假名，不是原始工号；一次性 `jti`，TTL ≤ `max_ttl_seconds`），通过
    已有的 `caller_context` / `caller_execution_token` 扩展字段投递给服务；
  - 服务用 BCS 公布的公钥验签，以 `sub` 作为用户主键分隔线程/记忆。需要下游真实凭证时，
    服务拿这个 proof 去自己的授权服务器做 token exchange，BCS **不转发**调用方的原始令牌；
  - 会话隔离键里的 `from/originator` 保证一个用户拿不到另一个用户的 task；
  - 配额按 `(publication, originator)` 统计，不是按 Bot。
- 对 A2A 注册的共享服务，出站链路仍用 Provider bearer 访问源 Agent（现有
  `a2a_authentication`），caller proof 放在 A2A `message.metadata` / 请求 extensions 里，
  两者职责分开：Provider bearer 证明"请求来自 BCS"，caller proof 证明"代表哪个用户"。
- 当前限制：`docs/caller-execution.md` 写明 caller execution 只支持 HTTP Provider
  的单 Bot 目标，不支持群、状态机路由、WS 投递和 fan-out。A2A Server 入站是单 Bot
  私聊，正好在支持范围内；**WS 插件 Bot 不能声明 `shared`**，需要先扩展 WS 投递。

#### C. 每人一实例（`per_user_instance`，AgentPass subbots）

- 发布的是**父 Agent（模板）**，目录里只出现一张卡片；每个用户的实例是独立的子 Bot，
  owner 是用户本人。
- 调用时按 `originator` 路由到该用户自己的子 Bot（`provider_bot_ref = parent:<user slug>`）：
  - 已有实例 → 准入就是 ownership（自己调用自己的 Agent），不需要好友边；
  - 没有实例 → 发布配置 `auto_provision=true` 时走现有 subbot 注册流程创建，否则返回
    `auth-required` 并引导开通；
  - 调用方是外部 Client 时，必须带 `originator`（代人调用），否则拒绝，因为没有
    "Client 自己的实例"。
- 用户把**自己的实例**分享给别人时，就回到 A 的个人型规则（实例自己再发布一次）。

#### 汇总矩阵

| 维度 | personal | shared | per_user_instance |
| --- | --- | --- | --- |
| 默认 visibility / listed | protected / false | public 或组织内 / true | public / true（模板卡） |
| 边的含义 | 好友：允许借用我的助手 | 使用资格 | 不需要（ownership） |
| 默认外部 profile | `a2a_consult`（skill 白名单） | default（wildcard） | — |
| connect 默认策略 | APPROVAL | OPEN / DEPT_FREE | — |
| 执行身份 | owner | caller（caller execution proof） | 实例 owner = caller |
| 下发给 Bot 的身份 | `caller_context`（信息性） | `caller_context` + `caller_execution_token` | 路由到实例，`caller_context` |
| 会话/任务可见 | 调用方 + owner（全文） | 调用方；owner 只看元数据 | 调用方本人 |
| 限流维度 | 每调用方 | 每 originator | 每实例 |
| HITL | 敏感 skill 需 owner 确认 | 服务自定 | 用户本人 |

### 6.6 `registry_only` 模式的调用鉴权

BCS 不在数据面上，好友关系只能靠令牌带过去：

- BCS 作为 OAuth2 授权服务器，准入通过后为调用方签发 `aud=<源 Agent URL>`、带 `sub`、
  `act`、`grants`（profile/skill 摘要）的短时访问令牌；
- 源 Agent 用 BCS 的 JWKS 验签并自己执行 skill 授权；
- 撤权生效上限 = 令牌 TTL（建议 ≤ 5 分钟，不提供 refresh token）。
- 发布时要求源卡片声明的 securityScheme 能接受 BCS 签发的令牌（配置 issuer 校验），
  否则只允许发布为"仅目录、不可调用"的条目（卡片标记为外部 Agent，由调用方自行对接）。

## 7. 协议映射（proxy 模式）

| A2A | BCS 内部 |
| --- | --- |
| `SendMessage` | 解析/创建私聊会话 → `MessageFlow` 准入 → 投递；阻塞到终态或 `configuration.blocking=false` 时立即返回 `submitted` |
| `SendStreamingMessage` | 同上，订阅 run 事件流（现有 WS V3 统一 run 事件 / Provider SSE）→ `TaskStatusUpdateEvent` / `TaskArtifactUpdateEvent` |
| `GetTask` | 查 `bcs_a2a_inbound_tasks` + run 状态；`historyLength` 从会话历史裁剪 |
| `CancelTask` | 复用 chat abort 协议（`docs/chat-abort-protocol.md`），A2A Bot 再透传到源 Agent |
| `SubscribeToTask` | 非终态 task 重新挂到 run 事件流 |
| 状态 | run 排队→`submitted`，执行→`working`，HITL→`input-required`，未授权→`auth-required`，完成/失败/中止→`completed`/`failed`/`canceled` |
| Part | Text ↔ 文本消息；File ↔ 会话文件（`bcs-session-file`，限大小与类型）；Data ↔ 结构化 JSON part |
| `metadata` | 只读取 `avernet.*` 命名空间，其余透传给 Bot |

源 Bot 本身就是 A2A 时，BCS 是"A2A→BCS→A2A"的中转：`remote_task_id` 和
`contextId` 映射已经由 `bcs-a2a-http` 维护，入站 task id 由 BCS 自己生成，**不把源
Agent 的 task id 暴露给调用方**（否则绕过 §6.4 的读取隔离）。

## 8. 数据模型（新增编号迁移，不修改已有迁移）

`bcs_a2a_publications`

| 列 | 说明 |
| --- | --- |
| `publication_id` | 外部 id（UUID） |
| `env`, `bot_uuid` | `(env, bot_uuid)` 唯一 |
| `slug` | URL 段，`(env, slug)` 唯一；unpublished 后冷却期内不复用 |
| `mode` | `proxy` / `registry_only` |
| `tenancy` | `personal` / `shared` / `per_user_instance` |
| `status` | `draft` / `published` / `suspended` / `unpublished` |
| `listed` | 是否进目录 |
| `skill_allowlist` | JSON，空 = 全部关闭（新增 skill 默认不开放） |
| `card_overrides` | JSON：name/description/文档链接等，只允许白名单字段 |
| `owner_transcript_access` | `full` / `metadata` |
| `revision` | CAS 版本 |
| `created_by`, `updated_by`, 时间戳 | 审计 |

`bcs_a2a_inbound_tasks`

| 列 | 说明 |
| --- | --- |
| `task_id` | BCS 生成，对外暴露 |
| `publication_id`, `context_id`, `session_id`, `run_id` | 映射 |
| `from_actor`, `originator` | 读取授权与隔离键，`(publication_id, from_actor, originator, context_id)` 建索引 |
| `state`, `updated_at` | 终态后按保留策略清理（复用现有 retention 机制，分批删除） |

`ActorKind::Client` 复用 `bcs_bots` 的 actor 行（`actor_kind='client'`），不新建身份表。
写入失败一律向上返回错误，不吞写失败。

## 9. API 面

公开（A2A 标准 + 注册中心）：

| 方法 | 路径 | 鉴权 |
| --- | --- | --- |
| GET | `/a2a/agents/{slug}/.well-known/agent-card.json` | 按 §6.2 |
| POST | `/a2a/agents/{slug}` | JSON-RPC 2.0；SSE 用于 streaming |
| GET | `/a2a/agents/{slug}/extendedAgentCard`（及 JSON-RPC 方法） | 已认证 + 准入 |
| GET | `/a2a/registry/agents?q=&skill=&tag=&cursor=` | 已认证；分页上限 50 |
| GET | `/.well-known/agent-card.json` | BCS 注册中心自身的卡片（可选，声明目录查询扩展） |

管理（沿用 authority 设计的 `/api/v1/collaboration/` 前缀，owner/manager 鉴权）：

| 方法 | 路径 |
| --- | --- |
| PUT | `/api/v1/collaboration/bots/{bot_id}/a2a-publication`（创建/编辑，带 `revision`） |
| POST | `.../a2a-publication/{publish|suspend|resume|unpublish}` |
| GET | `.../a2a-publication`（含预览的 Published Card） |
| GET/POST | `/api/v1/collaboration/a2a-clients`（外部 Client 登记，P2） |

配置（默认关闭，fail-closed）：

```toml
[a2a_server]
enabled = false
public_base_url = "https://bcs.example.com"
organization = "Example Org"
max_inbound_tasks_per_caller = 8
card_signing_kid = "..."            # P2，与 caller_identity 密钥独立；密钥走秘密下发
```

## 10. 安全要点

- 入站：JSON-RPC 请求体大小、Part 数量、文件大小上限；SSE 连接数按调用方限流。
- 出站：registry_only 源 URL 和未来的 push notification URL 都经过 `OutboundUrlGuard`（防 SSRF）。
- 凭证：调用方令牌不往下游传；Provider bearer 不出现在卡片、日志或 task 结果里；
  caller execution token 沿用已有的日志脱敏。
- 卡片投毒：源卡片的 description/skills 在目录里展示前做长度和字符集限制；目录结果标注
  "外部来源"，Bot 侧不能把卡片文本当作指令。
- 撤权：发布 suspend、边 revoke、Bot hidden 都在下一次请求生效；缓存 TTL 写进文档。
- 审计：publish/unpublish、connect 审批、外部 Client 登记写权限审计；每次入站调用记录
  `(publication, from, originator, task_id, reason_code)`。

## 11. 性能估算（热路径）

一次 `SendMessage`（缓存命中）：发布 + Bot 配置（缓存，0 次 DB）→ 准入（1 次索引边查询，
可缓存短 TTL）→ 写入站 task（1 次插入）→ 现有消息流写入。不扫表，不持有锁/事务做网络
投递。目录查询每页固定 2 次查询（发布+配置联合查询、批量边查询）。

## 12. 分期

| 阶段 | 内容 |
| --- | --- |
| P0 注册中心 | 发布资源 + 状态机、Published Card 生成（纯函数 + 一致性测试）、公开卡片/目录接口、可见性过滤；不提供调用 |
| P1 personal proxy | JSON-RPC `SendMessage`/`SendStreamingMessage`/`GetTask`/`CancelTask`/`SubscribeToTask`；BCS Human/Bot 调用方；准入 + 任务隔离；`a2a_consult` profile；`avernet/connect` 扩展 |
| P2 shared / 外部 Client | `ActorKind::Client`、OAuth2 client_credentials + token exchange、consent 边、caller execution 接入入站链路、卡片签名、`owner_transcript_access` |
| P3 | `per_user_instance` 路由与自动开通、`registry_only` 的令牌签发、push notification、skill 级配额、REST/gRPC 绑定 |

每个阶段都要补齐 `tests/contracts/` 的一致性测试（publication repo 的 local/prod 实现 +
Noop/Mock），符合 `docs/arch/arch.rules.md`。

## 13. 待确认

1. 外部 Client 登记是完全复用 Gateway App 主体，还是 BCS 自己维护 OAuth client（影响 P2 工作量）。
2. 个人型 Bot 的 `a2a_consult` 默认 skill 集合是空（owner 必须勾选）还是"只读类 skill"（需要 skill 标注读写属性）。
3. 共享型 owner 默认 `metadata` 访问是否符合运营排障需求；是否需要"按单次工单临时授权查看内容"。
4. 目录是否对未认证用户开放 `public` 条目（影响爬取和滥用面）。
5. WS 插件 Bot 支持 `shared` 是否值得在 P2 做（需要扩展 caller execution 到 WS 投递）。
