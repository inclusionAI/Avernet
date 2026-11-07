# BCS Caller 身份与 Caller Container 方案 Spec

- **文档定位：** Caller 身份的目标设计与接口契约，不作为功能上线或验收完成声明
- **适用范围：** H→A、Caller 群聊、群内受控 A2A、Manager/Worker、Workflow、Bot Runtime Gateway、Caller Container、MCP Egress 授权

> 核心原则：**caller 认证与执行材料不落业务 DB，不变更发言权/执行事实的持久边界；BCS 每次 dispatch 建立绑定目标 Bot、Run/attempt 的独立受保护执行上下文；Bot 间不直接传身份，由 BCS 校验可信 parent 执行关联后建立 child 上下文，按插件或出口需要签发身份材料。**

> 实施进度、代码映射、测试证据和发布阻塞统一记录在同目录的 [实施 Plan](plan.md)（2026-10-09 基于最新源码重新规划）。旧 Plan 的完成标记不代表回退后的实现状态。本文保留最终设计、兼容边界及验收要求，不记录逐轮实现过程。

---

> 阅读约定：受保护执行上下文是通用要求，token 是按需采用的凭据表示。明确标注 token 的示例描述插件凭据路径；正式字段规则见 §10.9，信任职责见 §10.13.0。Gateway 信任受认证 BCS 的 callerID，不承担通用逐任务 proof 验签。

## 1. 一页结论

### 1.1 背景问题

BCN 中 Bot 协作和 MCP 执行在部分链路上默认使用 Bot Owner 身份，但真实任务可能由 Human 发起。如果直接用 Owner 身份执行，会带来：

1. MCP 审计主体错误；
2. Owner 有权限但 Human 无权限时产生越权；
3. 多 Human 共用 Runtime 时身份、缓存、记忆、工具凭据串用；
4. 部分 Runtime 不能做到线程级身份隔离，需要 “一个 caller 一个容器”；
5. H→A→B 多跳时，A 不能安全判断或转发 Human 身份；
6. 群聊中多人发言时，不能让一个 Human 的 caller 身份被其他 Human 借用。

### 1.2 核心方案

```text
可信 Human 入口解析 caller
        ↓
既有执行系统创建/定位真实 run / message turn
        ↓
BCS 建立绑定 caller、目标 Bot、run/attempt 的独立受保护上下文，并把 origin/parent token 存入短期 Identity Material Ledger
        ↓
Runtime Gateway 路由到 caller 对应的 Bot Container
        ↓
Runtime/MCP Egress 在具体 MCP 调用出口检查授权并兑换凭据
        ↓
A2A / Worker / Workflow 子任务必须回到 BCS
        ↓
BCS 校验可信 parent run/attempt 关联后，建立 child 上下文，按需签发 token
```

一句话：

> **caller 身份不是由 Bot A 传给 Bot B，而是 A 提交子任务意图；受信 Runtime/Gateway 提供真实 parent run/attempt 执行关联（可采用 proof）；BCS 校验归属后，为 B 的 child run 独立建立 caller context，并按需签发 token。**

### 生命周期复用原则

**Caller 复用现有任务生命周期，只补身份所需的最小可信关联，不新增独立的 A2A parent/child 生命周期体系。** 本文的 root/parent/child 是既有执行任务之间的逻辑关系，不要求另建 Caller run 表、状态机或结果仓储。

- 现有 Chat/Managed delivery、主子 task 与 Workflow 系统分别拥有任务创建、attempt、派发、重试、取消、终态和结果恢复；Caller 只读取其权威事实，不维护第二份可变状态。
- Caller 聚焦可信 Human 入口、身份兑换、按目标签发、Gateway 容器路由，以及上下文共享和隔离。
- 缺少必要关系时，先核实既有对象，在原拥有方补最小非凭据关联；不得为了统一 Caller 接口复制完整执行模型。
- 无法取得可信执行关联的模式暂不开放 Caller 继承，不能以模型自报 parent、当前 lease holder 或历史 DB 消息重建身份。
- Human 直接触发群内多个 Bot 是同 turn 下的多个既有目标执行，不人为建立 Bot-to-Bot child 关系。

### 1.3 最终规则

1. **Human 身份只能来自可信 Human-aware Ingress**，不能由消息体、Bot 参数或模型输出自报。
2. **BCS 维护一份独立、加密、短 TTL 的 Identity Material Ledger**，保存 Human origin handle 和 parent execution token，用于 H→A→A/A2A 的目标签发与父链兑换。它不是业务 DB、消息历史、模型上下文或日志；Provider 不回传 token，BCS 通过真实 parent run/attempt 从 ledger 取材料。材料缺失/过期即 fail closed，不回退 Owner或当前 lease。

BCS 用父 token 兑换下一跳 token；Owner parent 则用 Human origin handle 签发下一跳 token。
3. **BCS 每个新的逻辑 dispatch/执行 attempt 都建立独立受保护执行上下文，token/proof 按需签发（网络重投按 §7.5 幂等处理，不续期）**，上下文绑定：`run_id`、`target_bot_id`、`group_id/session_id`、`message_turn_id`、TTL、delegation depth。
4. **caller_execution_token 是短期、opaque/runtime-only 的执行令牌**，不能暴露给 Bot 业务逻辑、不能进入模型上下文、不能写日志、不能落 DB。
5. **Caller Container 按 caller×bot 隔离**。已确认使用长期 `Human×Bot` 容器，由 Runtime 保证 session 上下文隔离；不满足该保证的 Runtime 不开放此复用能力。
6. **H→A**：只有 A 声明 caller_identity.enabled=true 并且本次入口是 Human 时，BCS 才进入 caller_task 并建立执行上下文，按需签发 token；否则是 open_task。
7. **群聊**：谁发言，谁是该 message_turn 的 caller。每条已准入的新 Human 发言生成新的 `message_turn_id`，各目标建立独立 root 上下文；抢占只改变提交资格，实际发言时重新确认身份，不复用上一位 Human 的 caller 身份。
8. **群内受控 A2A 可以传递 caller**，前提是同 group/session、A2A 经过 BCS、parent run/attempt 有效、可信执行关联有效（采用 proof 时须验证 proof）、source/target 都在群内、depth 不超限。
9. **私聊/开放式自由 A2A 默认不传递 caller**；如果 target Bot 要 caller，则返回 caller unavailable / caller required，不自动升级。
10. **Manager/Worker 与 Workflow 可以传递 caller**，但必须由 BCS 根据受控拓扑及可信 parent run/attempt 关联为每个 child run 建立独立上下文，按需签发 token。
11. **BCS 不预测 MCP 工具列表，不持有 MCP Credential**。具体 Tool/Resource/Action 的 Grant 与短期 MCP Credential 在 Runtime/MCP Egress 出口处理。
12. 任何路由失败、token 失效、container 不匹配、grant 缺失都必须 fail closed，不得静默降级为 Owner 身份执行敏感操作。

---

## 2. 四个边界

```text
visibility / audience
    保持现有消息视图/受众语义；不新增到 caller 容器的受众映射。

Caller Execution Context
    决定本次 run 是否代表某个 Human caller 执行。

Container Routing
    BCS 决定逻辑目标 caller；Gateway 校验后定位 Runtime / Caller Container，不决定同步策略。

MCP Grant / Credential
    决定具体 MCP 工具是否允许执行，以及 MCP Server 如何认证。
```

边界约束：

- `message.visibility/audience` 不承担身份授权职责，也不重新解释为 caller 容器的公开/私有标签；
- BCS 按现有协作规则选择目标 Bot，再决定共享上下文同步的目标 caller；Gateway 不自行扩大同步范围；
- `caller_context` 不是 Human 登录态，也不是 MCP Credential；
- `caller_execution_token` 证明本次 dispatch 的 caller 执行合法，不证明 MCP 授权；
- `chat.inject` 只同步上下文，不触发可执行 caller run；
- Bot 不能把自己的 caller_context、token 或 Gateway 内部 binding/路由材料转发给另一个 Bot。

---

## 3. 角色职责

| 角色 | 负责 | 不负责 |
|---|---|---|
| Human-aware Ingress | 认证 Human，向 BCS 提供可信 Human Principal | 不把原始 Cookie/Bearer 写入业务消息 |
| BCS | 编排 run、判断 caller mode、签发短期 token/context、通过 Provider 调用 ensure/dispatch、做 parent run 校验与结果投影；决定同步内容和目标 caller | 不把身份材料写入业务 DB/消息/日志；不持有 MCP Credential |
| Bot 配置管理方 | TeamClaw 由 Backend、其他接入方由被授权管理服务同步 Bot Caller 开关及最低隔离要求 | 不以 Bot 配置代替 Provider 技术能力声明 |
| Provider | 下行模式下 BCS 的唯一运行时对接主体；声明技术能力，提供下行方法及受认证回调，承担对外协议契约 | 不以能力声明覆盖 Bot Caller 开关，不静默忽略 Caller 扩展 |
| Bot Runtime Gateway（Provider 内部职责） | 创建/恢复/回收 Caller Container，维护 binding→container 内部路由，信任受认证 BCS 的 callerID 并按目标/执行关联路由 | 不作为独立 Provider 再注册，不维护另一份对外能力真值；不决定业务授权或群聊同步策略，不把身份 token 暴露给 Bot 逻辑 |
| Caller Container / Runtime | 在隔离容器内执行 Bot 逻辑，通过 Runtime-only metadata 发起 child dispatch / MCP egress | 不伪造 caller，不直接转发 caller token 给其他 Bot |
| MCP Egress | 具体 MCP 调用时检查 Grant、发起异步授权、向 Credential Provider 兑换短期凭据 | 不把 Owner 权限当作 Human 授权 |
| CallerIdentityPlugin | 建立 subject、签发/验证执行 token；默认 JWT，internal AgentPass | 不决定业务授权，不把身份材料写入业务 DB |
| BCS Identity Material Ledger | 保存 BCS 维护的短期加密身份材料：Human origin handle 与父执行 token；按真实 parent run/attempt 兑换/签发目标 token | 不是业务 DB、模型上下文或 Provider 缓存，不作为消息回执内容 |
| MCP Credential Provider | 在工具出口兑换具体 MCP audience 凭据 | 与 caller 身份签发分离，不向 BCS 返回凭据 |
| MCP Server | 接收短期 audience credential 并执行工具 | 不接收 caller_context 作为通用登录凭据 |


### 3.1 Provider 与 Gateway 的关系

在 `provider_downlink` 模式下，**Provider 是接入主体，Gateway 是 Provider 内部承担的
容器管理与路由职责**，不是 BCS 需要额外注册或维护的第二个接入方。Provider 可以自行
实现 Gateway，也可以在内部调用独立部署的 Gateway 服务；部署拆分不改变对 BCS 的契约责任。
本文后续“BCS 调用 Gateway”“Gateway 返回”等描述均为职责简称，实际经过目标 Bot 绑定的 Provider。

```text
BCS
  │ 目标 Bot 当前有效的 Provider 绑定
  ▼
Provider 下行入口（统一对外契约与服务认证）
  ├─ caller.container.ensure：准备或复用隔离环境
  ├─ chat.send：普通发送 / Caller 执行扩展
  ├─ chat.inject：上下文同步 / observe-only 扩展
  └─ execution.status / execution.cancel：执行控制
  │
  ▼
Gateway 职责：Provider 内部校验目标并路由
  ├─ Bot 普通执行环境
  ├─ Alice×Bot Caller Container
  └─ Bob×Bot Caller Container

Runtime 结果 / child 请求 → Provider 受信返回路径 → BCS
```

- Bot 配置回答“是否需要 Caller”；Provider 能力回答“能否满足所需协议和隔离要求”。
  不新增独立的 Gateway 能力配置、注册对象或 BCS→物理容器连接。
- `provider_downlink` 本身不代表支持 Caller。能力必须显式声明，且按场景匹配；
  普通 H2A 不因缺少其不使用的群同步能力而被阻塞。
- “Provider 不支持”指必要能力或协议不匹配；“本次准备失败”指 ensure、隔离检查或历史
  准备未成功。BCS 插件/派发依赖缺失属于 BCS 自身未就绪，不写入 Provider 能力声明。
  不以新鲜度 TTL 或额外心跳推断能力，也不因失败将 Caller 任务降级为 Owner。

### 3.2 方法复用与传输边界

| 方法 | 演进方式 | 下行语义 |
|---|---|---|
| `caller.container.ensure` | 新增容器准备方法 | 通过同一 Provider 创建/复用环境；不执行聊天任务 |
| `chat.send` | 扩展原方法 | 使用受保护 `extensions.caller_context` 和按需 `extensions.caller_execution_token`，关联独立 run/attempt |
| `chat.inject` | 扩展原方法 | `extensions.caller_context.subject` 指定观察 Human，runtime_target 仅可选引用/代次，`extensions.execution.mode=observe_only`；禁止携带执行凭据 |
| 状态查询与取消 | 复用/扩展既有控制能力，不足时新增版本化方法 | 满足 §10.12.1 的关联、幂等、终态及取消语义 |
| Provider→BCS 回调 | 保持原返回方向，具体入口按事件契约划分 | 不因内部 Gateway 拆分新增一套重复回调体系；控制终态入口与结果事件仍按 §10.12 区分 |

**复用方法不等于强制复用同一个 HTTP URL。** 优先复用现有 Provider 下行入口，使用
method 区分操作；如受保护信封需版本化 HTTP 入口，仍属于同一 Provider 协议，不新建
Gateway Provider。具体 URL、版本与序列化由 Provider 传输契约定义。

`ensure` 的返回项名为 `observe_target`；BCS 从中取得目标引用与代次，构造 inject 的
`extensions.runtime_target`，而不是新增同名 inject 字段。observe-only 采用上述 mode，
不是另设 `observe_only=true`。正式字段分别见 §10.8–§10.10。

普通请求不含 Caller 扩展时保持原行为；含扩展时必须显式匹配协议能力。不支持、缺失或
错误字段须拒绝，不能忽略后按 Owner 执行。身份信封不得进入模型、持久化或普通正文日志；
若原入口无法满足这一点，必须调整受保护承载和日志路径后才能开放，不以接口复用豁免。

---

## 4. 数据模型

### 4.1 Run 元数据：可落 DB

以下为从既有执行对象读取或最小扩展得到的逻辑视图，不是新增表定义。status、attempt 和终态始终以原执行拥有方为准，不在 Caller 侧镜像维护。DB 只保存非身份化执行元数据：

```json
{
  "run_id": "run_ab",
  "root_run_id": "run_ha",
  "parent_run_id": "run_ha",
  "message_turn_id": "turn_001",
  "source_actor_kind": "bot",
  "source_actor_id": "bot_a",
  "target_bot_id": "bot_b",
  "group_id": "group_001",
  "session_id": "session_001",
  "execution_mode": "caller_task",
  "caller_mode": true,
  "delegation_depth": 1,
  "status": "running",
  "created_at_ms": 1780000000000,
  "expires_at_ms": 1780000300000
}
```

DB 禁止保存：

```text
human_id / staff_id / login_name / display_name
Human Cookie / Bearer Token / Refresh Token
iamtoken / x-one-id 明文 / MCP Grant / MCP Credential
caller_context 原文
caller_execution_token 原文
可直接反查 Human 的 caller_ref
binding_ref / route_ref 原文（除非它被证明完全不含身份且仅为短期技术索引）
run_id -> human_id 的持久映射
```

> 隐私边界：这里禁止的是 caller 认证/执行材料及其敏感身份映射；允许既有业务 Actor ID 和 session 发言权 holder 关联持久化，不删除既有消息 sender/展示/审计字段。holder 关联只授予提交资格，不能作为签发身份的依据。run 关联 message 后仍可能关联到 Human，不能声称 run 完全匿名或不可关联。

> 可选：DB 可以保存不可逆、不可反查的 `token_jti_hash` 或短期执行摘要 hash 用于防重放和排障，但不能保存可还原身份的密文映射。默认不依赖 DB 加盐/加密保存 caller 身份。

### 4.2 Message Turn

`message_turn_id` 是群聊 caller 身份的边界。

```text
一条 Human 发言 = 一个 message_turn
一个 message_turn = 一个 Human 协作关联，可包含多个目标 Bot 的 root execution lineage
换 Human 发言 = 新 message_turn + 各目标独立的 root 上下文
```

示例：

```text
Alice 发言：turn_001, root_run=run_alice_a, caller=Alice
Bob 发言：  turn_002, root_run=run_bob_a,   caller=Bob
Alice 再发：turn_003, root_run=run_alice_2, caller=Alice
```

关键规则：

- 不存在长期 “group active caller” 供其他 Human 借用；
- Caller 群通过 Speaking Lease 控制 Human 提交发言，不定义长期身份，也不要求整个执行链独占 session；
- 发言权切换后，新的 Human 消息必须重新认证并建立独立 root 上下文，token 按需签发；
- 已确认：抢占只转移发言权，不取消旧 turn；旧任务及合法派生继续使用原 caller，仍受原身份有效期、scope、run 状态及工具权限约束。

### 4.3 Caller Execution Token

`caller_execution_token` 是选用插件凭据路径时由 BCS 按需签发的短期、opaque/runtime-only token，不是所有 dispatch 的必填字段；如果该目标可作为后续派生的 caller parent，BCS 会把该 token 的最小可派生材料放入 Identity Material Ledger，供下一跳 exchange 使用。其格式由 CallerIdentityPlugin 决定：默认 JWT；internal AgentPass 的 opaque proof 或具备执行约束的受信封装。裸 x-one-id 不等同于执行 token。JWT 对业务层不可见，但签名 payload 不自动保密，见 §10.13。

Token 内部声明建议包含：

```json
{
  "iss": "bcs",
  "aud": "bot_runtime:bot_b",
  "token_type": "caller_execution",
  "jti": "token-jti",
  "caller": "encrypted-or-agentpass-ref",
  "run_id": "run_ab",
  "root_run_id": "run_ha",
  "parent_run_id": "run_ha",
  "target_bot_id": "bot_b",
  "group_id": "group_001",
  "session_id": "session_001",
  "message_turn_id": "turn_001",
  "delegation_depth": 1,
  "max_delegation_depth": 2,
  "iat": 1780000000,
  "exp": 1780000300
}
```

对外表现必须是 opaque：

```text
Bot 业务逻辑不可读
模型上下文不可见
日志必须脱敏
DB 不保存 token 原文
只能由 Runtime/Gateway 在 runtime-only metadata 中携带
```

### 4.4 Caller Context

Caller Context 是受信 BCS 下发的 runtime-only 主体信息，不是独立授权。
send 与普通 observe-only inject 统一使用 `extensions.caller_context.subject`，
不在 runtime_target 重复携带 subject。主体引用沿用可信入口/身份插件的命名空间，
不能从消息正文或 observed_speaker 推导。

执行请求的 caller_context：

```json
{
  "subject": { "kind": "human", "ref": "opaque_caller_ref" },
  "expires_at_ms": 1780000300000
}
```

仅观察 inject 的 caller_context：

```json
{
  "subject": { "kind": "human", "ref": "opaque_observer_ref" }
}
```

- send 的 subject 关联执行/派生主体，具体权限由有效 proof 的 purpose 决定；Owner 仅派生不能升级为 Human 执行。
- inject 的 subject 选择接收 Human×Bot 环境，不等于消息 sender。必须使用 observe_only 模式，不携带执行凭据，不初始化工具执行身份。
- run/attempt/dispatch/session/目标等事实继续复用 envelope 与 execution 的唯一来源，不复制到 caller_context。
- ensure 模式的 inject 可以另带 runtime_target 引用与代次；无 ensure 模式不带 runtime_target。两种模式都必须有 subject。
- 普通非 Caller send/inject 保持既有行为。ensure 准备请求保留原结构；history_complete 控制变体也携带路由专用 subject，见 §10.10.1。
- subject 与其他运行时身份材料均不进入模型、业务历史、DB 或正文日志。

### 4.5 Caller Isolation Binding 与 Gateway 内部路由

统一契约字段为 `binding_ref`；早期草案的 container_binding_id/isolation_binding_ref 仅为旧称，不并行暴露。

逻辑绑定由 Runtime Gateway 管理，用来把 caller 与某个 Bot 的隔离执行环境关联起来：

```text
长期容器内部复用键 = environment/provider 信任域 + bot_id + 稳定 caller subject
会话与执行关联 = session_id + run/attempt（不改变 Human×Bot 容器复用粒度）
```

已确认的容器复用与会话隔离：

```text
长期容器：Human×Bot（在所属 environment/provider 信任域内）
内部会话：session_id
```

Runtime 必须保证跨 session 的历史、私有状态和任务关联隔离。无法保证时不得宣称支持该复用能力；不是自动切换成另一种默认架构。同容器多 run 的调度与 token 隔离按 B1 执行，见 §4.5.1。


#### 4.5.1 同容器多 run 调度（B1）

- 同一 Human 在多个群或私聊调用同一 Bot，仍可复用长期 Human×Bot 容器，但每次执行的 session、run/attempt、身份凭证及任务关联必须相互隔离。
- Runtime 能保证上述执行级隔离时才允许并发；否则由 Gateway/Runtime 对该 Human×Bot 容器串行排队。串行不替代 session 历史与私有状态隔离，BCS 不接管容器内部调度。
- 不得用容器级可变全局 token 承载多个任务的执行身份；新任务不得覆盖旧任务的凭证或上下文。串行切换也必须清理前一任务的执行材料，不能沿用残留身份。
- 排队不延长身份有效期。出队执行前必须重新校验身份凭证与目标、session、任务的绑定及有效期；失效时等待可信身份恢复或失败，不自动续签、不回退 Owner。按 B5，一期不提供身份过期后原 run 的通用恢复；Human 重新发起，不得据此新增自动恢复能力。
- caller token/凭据不能写入持久化队列、数据库、日志或快照；任务取消、失败与清理不得污染其他任务的身份状态。重试与 attempt 语义按 B6（§7.5）。

关键收敛：

```text
container_route 不再作为 BCS/Gateway 接口字段。
Gateway 内部自己维护 binding/session/container/connection 的路由映射。
BCS 不感知 container_id、ws_connection_id、pod_id、route_ref 等物理路由信息。
```

`binding_ref` 也不是 caller_context 的必需字段。它只是 Gateway 在支持强 container-bound 隔离时返回给 BCS 的短期 opaque 技术索引，可用于 BCS 签发或兑换 `caller_execution_token` 时加入隔离约束。

兼容规则：

```text
如果 Gateway 能返回 binding_ref：
  BCS 可把该 binding 作为 token 的内部约束，Runtime/Gateway 在执行出口校验 token 与当前 binding 一致。

如果 Gateway 不能返回 binding_ref：
  BCS 仍可基于 caller_subject_ref + bot_id + scope + isolation_level 签发 token；
  Gateway 必须用自身内部会话、容器或 token-introspection 机制完成 caller 容器路由/隔离校验。

无论哪种方式：
  caller_context 只表达“本次代表谁/哪个 run/scope 执行”；
  binding_ref 不进入 caller_context，也默认不作为 chat.send 明文字段；
  容器路由和连接细节都留在 Gateway 内部。
```

### 4.6 Dispatch Proof / Isolation Proof

本方案 V1 不要求单独的 `route_proof` 字段。采用插件凭据路径时，`caller_execution_token` 表达本次 caller dispatch 的受限执行证明，可包含隔离约束；不采用该路径时仍必须维护独立受保护执行上下文。以下为 token 内部约束示例：

```json
{
  "version": "1.0",
  "run_id": "run_ab",
  "target_bot_id": "bot_b",
  "message_turn_id": "turn_001",
  "isolation": {
    "level": "container_bound",
    "binding_ref": "optional_bind_ref_123",
    "generation": 3
  },
  "issued_at_ms": 1780000000000,
  "expires_at_ms": 1780000300000
}
```

BCS 及实际凭据消费方按需执行以下凭据约束；Gateway 仅负责受信来源、路由绑定，不强制额外验签（见 B9）：

```text
使用 token 时确认其受信来源
run_id 匹配当前请求
target_bot_id 匹配当前 Bot
message_turn_id / session_id / group_id 与当前 dispatch 一致
如果 token 含 binding_ref，则必须与当前 Gateway 内部 binding 一致
如果 token 不含 binding_ref，则必须满足声明的 isolation_level 对应的 Gateway 内部隔离策略
expires_at 未过期
```

---

## 5. H→A 单聊流程

### 5.1 A 开启 caller

下列流程和 JSON 展示选用执行 token 的路径；通用必需项是独立受保护上下文，token 的条件要求见 §10.9。

```text
Human H
  -> Ingress 认证得到 Human Principal
  -> BCS 判断 A.caller_identity.enabled
  -> BCS 创建 root run: run_ha
  -> BCS 不把 H 认证与执行材料写业务 DB，只进入 Identity Material Ledger
  -> BCS 调 Gateway Ensure H×A Caller Container
  -> BCS 调 CallerIdentityPlugin 为 H+A+run_ha 签发 token，带可选 binding 约束
  -> BCS 下发 chat.send(run_ha) + caller_context + caller_execution_token
  -> Gateway 校验并路由到 H×A Container
  -> Runtime 执行
  -> MCP Egress 按具体 MCP 检查授权并兑换短期凭据
  -> 结果经 Gateway -> BCS -> H
```

BCS 下发示例：

```json
{
  "type": "req",
  "id": "run_ha",
  "method": "chat.send",
  "extensions": {
    "execution": {
      "mode": "caller_task",
      "run_id": "run_ha",
      "root_run_id": "run_ha",
      "parent_run_id": null,
      "delegation_depth": 0
    },
    "caller_context": {
      "subject": { "kind": "human", "ref": "opaque_caller_ref" },
      "expires_at_ms": 1780000300000
    },
    "caller_execution_token": {
      "version": "1.0",
      "format": "jwt",
      "type": "jwt",
      "token": "<runtime-only-token>",
      "expires_at_ms": 1780000300000
    }
  }
}
```

### 5.2 A 未开启 caller

```text
Human H
  -> Ingress 认证
  -> BCS 创建 open_task run
  -> BCS 不签发 caller_context/token
  -> Gateway 路由到 A Owner/普通 Container
```

如果 Runtime/MCP Egress 后续发现某个 MCP 必须 caller：

```text
返回 CALLER_REQUIRED / CALLER_CONTEXT_NOT_AVAILABLE
不得使用 Owner 身份静默执行敏感操作
```

---

## 6. 群聊流程

### 6.1 群聊是否进入 caller 模式

```text
group_caller_enabled =
  has_human_member
  && exists bot where caller_identity.enabled=true
```

- `group_caller_enabled=false`：普通群聊，不创建 Caller Container，不限制 Human 并发，不签发 caller token。Human 无须获取/抢占发言权，消息准入不创建、读取或校验 Speaking Lease，沿用原有权限、并发、调度及路由行为。
- `group_caller_enabled=true`：Human 发言会进入 message_turn caller 边界；对支持 caller 的目标 Bot 使用 Caller Container 和短期 token。

### 6.2 Human 发言规则

群聊 caller 的核心不是“群当前 active caller”，而是：

```text
当前 Human 发言 -> 新 message_turn -> 新 root caller execution
```

Alice 发言：

```text
BCS 创建 turn_001
对 BotA: run_alice_a, token_alice_a, route Alice×BotA
对 BotB: run_alice_b, token_alice_b, route Alice×BotB
```

Bob 发言：

```text
BCS 创建 turn_002
对 BotA: run_bob_a, token_bob_a, route Bob×BotA
对 BotB: run_bob_b, token_bob_b, route Bob×BotB
```

不能出现：

```text
Bob 发言，但继续使用 Alice 的 caller token/container 执行
```

### 6.3 发言权 / Speaking Lease

**抢占只转移发言权，允许已接收的旧任务以原 caller 继续执行。** Caller 群同一时刻只有一个 Human 可以提交发言，但不同 message_turn 的任务可以并行执行；普通群不增加发言权限制。

```text
Alice 持有 lease(epoch=10) → 提交 turn_A → Alice 任务开始
Bob 抢占 → 原子转移 lease(epoch=11) → Alice 的旧提交资格失效
Bob 提交 turn_B → Bob 任务开始
turn_A 与 turn_B 独立执行，身份、parent lineage、结果关联互不替换
```

规则：

1. 抢占、主动释放和超时回收不依赖任务结束；Bot 回复或 final 不自动释放另一个 Human 的发言权。
2. 受认证群成员才能获取/抢占。BCS 原子校验 holder、lease_id、epoch 和有效期，并以同一串行化边界决定消息是否接收。先被接收的旧消息合法；抢占成功后才到达的旧 lease 提交拒绝，不能出现校验通过后越过抢占边界再接收的竞态。
3. epoch 只防止旧发言权提交，不是执行撤销版本。child dispatch、MCP 执行和旧结果回传不能因当前 session lease epoch 改变而被拒绝，必须检查各自原 turn/run/proof。
4. 新 Human 发言使用新的 message_turn/root lineage 和当前可信身份。抢占本身不签发 execution token，也不续签旧 token。
5. 旧任务及其合法子任务保持原 caller，直至完成、显式取消、身份到期或其他独立策略使其失效。允许继续不代表自动延长 TTL；授权等待后证明过期仍需重新认证或失败。
6. 旧结果按原 run/attempt/turn 关联并依既有发布规则展示，不归入新 Human 的任务；共享 inject 不改变执行 caller。
7. 显式取消任务、Bot caller 能力撤销、成员权限撤销独立处理，不能与转移发言权混为一谈。

**持有方式已确认：持续持有 + Human 主动抢占 + 空闲超时释放。** 提交消息不消费 lease。空闲 TTL 初始默认值采用 60 秒（服务端可配置），它不是 caller token TTL，也不是 run timeout。

- 获取或抢占成功时，从 BCS 服务端时间开始计时；持有者每次成功提交一条新的 Human 消息，更新 `expires_at_ms = accepted_at_ms + idle_timeout_ms`，lease_id/epoch 不变。
- 成功提交指消息已被 BCS 原子接收，而不是工具执行成功；下游任务失败不撤销该次续期。重复消息重试返回原接收结果，不新建 turn、不刷新空闲期限。
- Bot 回复、任务执行、输入状态、页面心跳、重复 acquire、失败提交均不续期。每条新发言仍校验当前可信 Human 身份，持有 lease 不代表登录态长期有效。
- 受认证 holder 可主动释放；其他受认证且有发言权限的群成员可显式抢占。普通 acquire 遇到其他有效 holder 返回冲突，不能隐式抢占。
- 主动释放、抢占和过期都只影响后续提交资格。超时以服务端时间为准，到期即失效，不依赖后台清理任务及时运行；失效后需重新获取，不能用一条迟到消息复活旧 lease。
- 释放/超时清理必须校验 lease_id/epoch；超时清理还要检查当前到期时间，防止旧计时器清除已续期或已被新 Human 持有的 lease。重新获取/抢占生成新的 lease_id 和递增 epoch。

### 6.3.1 发言权持久化与普通群兼容（B7）

允许持久化业务发言权状态：session_id、holder_actor_id（既有业务 Actor ID）、lease_id、epoch、expires_at_ms。具体复用现有表还是新增表，在实施时核实；不要求保存 Human 登录态、caller proof、可兑换身份的 x-one-id/ref 或容器映射。

- 每条发言由可信入口重新认证；BCS 比较认证 Human 与 holder_actor_id，再原子校验 Lease 与接收消息。不得仅凭 DB holder 签发 caller 身份。
- 多实例通过数据库事务/CAS 实现获取、抢占、释放、超时与消息准入的原子顺序，不允许先查询再无条件提交。旧清理须校验 lease_id/epoch 与当前到期时间。
- 重启后可继续使用未过期 Lease，但不刷新有效期、不恢复已丢失的 proof；过期 Lease 不复活。数据库不可用时，不能可靠判断 Lease 的 caller 消息不准入，写失败不得返回成功。
- 普通群完全绕过 Speaking Lease，不因 Lease 状态或其服务故障增加新的阻断；仍保留原有认证、成员、权限、限流及数据库依赖，不承诺整个数据库故障时普通群也能工作。
- 群是否需要 caller 由 BCS 当前已应用的成员、Bot 配置与受控拓扑决定；无 Human 或相关 Bot 都未开启 caller 时不启用 Lease。Provider 不 ready 不能把 caller 群降级为普通群。
- caller 需求关闭后，后续消息走普通群路径，不受残留 Lease 阻塞；旧 Lease 不应在后续重新开启 caller 时自动恢复提交资格。群模式切换与准入的原子处理需在实施中验收；已经接收的任务按 B4 处理，不重新指定 caller。

### 6.4 BCS 控制共享上下文同步，Gateway 按目标 caller 路由

**同步单位是目标 Bot 在某个 session 的共享会话上下文，不是整个 session 的全部消息。** caller 容器是同一 Bot 的执行环境，不是新的群 Actor。

```text
BCS 原有自由聊天 / Manager-Worker / Workflow 投递规则
  -> 确定接收消息的 Bot
  -> 确定哪些消息已进入该 Bot 的 session 共享上下文
  -> 确定当前执行 caller 和其他有效同步目标 caller
  -> 对执行目标 chat.send + caller_context/token
  -> 对其他目标 chat.inject + runtime_target，observe_only
Gateway
  -> 验证 BCS 请求及目标范围
  -> 按 bot + session + target caller 定位内部环境
  -> 投递，不自行广播或选择业务受众
```

`visibility/audience` 保持原有语义。如果原投递链路使用它们，继续沿用；如果只控制 Human 历史展示，也不把它们扩展为容器同步权限。**不新增基于这些字段的 caller 容器二次过滤规则，也不绕过已有定向投递规则。** 当前源码缺失，`public/directed/full_only` 的精确实现语义待源码核查，不能等同于早期设计的 `group_public/caller_private/restricted`。

| 消息来源 | 同步规则 |
|---|---|
| 已投递给目标 Bot 并进入共享上下文的 Human 群发言 | 同步至该 Bot、该 session 下其他有效 caller 环境 |
| 已进入该共享上下文的 Bot 群回复（含该 Bot 自己发布的回复） | 同步；源容器排除或按 message_id 去重 |
| Manager/Worker 定向消息、Workflow 内部消息 | 不因 session 相同而广播；只有明确进入目标 Bot 共享上下文的部分参与同步 |
| MCP 原始结果、私有文件、缓存、执行中间状态 | 不自动进入共享流；经既有发布策略明确发布的消息才按共享消息处理 |
| token、credential、runtime-only context | 永远不进入上下文同步消息 |
| 其他 session 消息 | 不同步，即使底层复用长期容器 |

同步触发按原提交/派发事实判断：

- root 或群内 A2A 的目标 `chat.send` 获得 Accepted 后，才将该目标已分类的共享输入同步到其同 session 的其他 Caller 容器；Unknown/Rejected 不触发输入 fan-out。
- A2A 输入去重键使用本次 child 的 canonical source message ID，不复用 root turn ID；未分类、私有或不符合目标投影的内容不共享。
- Bot 群回复首次成功提交后即参与源 Bot 的共享流同步，不以是否还有下游 Bot 接收者或 `relayed` 为条件；重复终态不重复触发。此同步包括源容器，使用同一 canonical reply ID 去重。
- 输入与回复同步在每轮重试前读取当前成员：使用与路由一致的 session participants（非空时）或 group participants。仅 Human 成员参与 subject 比对；`human_<工号>` actor ID 映射为裸工号，不把 Bot ID 或展示名视为 Caller。成员读取失败/会话归属不匹配时不发送。
- 每个目标发送前复查引用有效期、代次和原任期；引用被撤销或替换后，已复制的目标列表也不能恢复它。每轮成员快照不等于成员变更原子屏障；立即失效仍需成员变更事件接入，不能声称退群和在途网络发送原子互斥。
- 实时同步为有界 best-effort：失败不回滚已受理执行或已提交回复，不保证重启后补发；缺失部分仍须依赖可信 ensure 后的历史补齐和 Runtime 快照屏障。

BCS 负责共享流、同步目标和补发决策；Gateway 负责真实容器绑定、内部路由和投递确认。BCS 不需要保存物理容器映射。目标 caller 的 opaque 路由引用只在运行时持有，不能从 Human 展示 ID 伪造，不能写入消息历史或作为 MCP 执行凭证。

### 6.5 发言人、执行 caller、同步目标 caller

Alice 发言时：

```text
消息 sender = Alice
chat.send: caller_context 与执行 token 绑定 Alice，Gateway 路由到 Alice×BotA
chat.inject: caller_context.subject.ref = Bob，Gateway 路由到 Bob×BotA
             sender 仍为 Alice；caller_context 仅含 Bob 的 subject，不携带执行 token，不代表 Bob 执行
```

`runtime_target` 是逻辑接收环境选择信息，不是物理 `container_route`，也不是执行身份授权。Gateway 必须验证请求来自受信 BCS，且引用属于指定 env/provider/bot/session；猜到或持有 ref 不产生执行权限。

### 6.6 顺序、补历史与目标引用生命周期

- BCS 使用 message_id/session_seq 维护目标 Bot 的共享消息流及投递水位；跨目标未投递的消息可能造成序号空隙，不能要求每个 Bot 收到 session 的所有序号。
- Gateway 回传已投递确认，BCS 决定重试/补发；按消息与目标环境去重，不重复回灌源容器，不把注入内容再次当作新群消息形成循环。
- 新 caller 环境执行前，补齐到当前发言之前的共享历史基线；当前发言通过 send 进入，或显式去重，避免重复注入。
- 容器未创建/已回收时，保留共享历史而非为每条 inject 强制拉起容器；初始化后补齐。补发不能使 observe_only 升级成 send。
- BCS 的目标 ref 清单为非持久运行态；仅由可信 `caller.container.ensure` 响应中的 `observe_target` 获取/替换，字段与刷新规则见 §10.8。它不是执行身份，不要求其他 Human 提供登录 token 来接收历史；没有有效引用则暂停该目标 inject。
- ref 缺失、过期或 BCS 重启丢失时，停止该目标实时投递；按 B3，一期等待下次可信 ensure 从 Gateway 获取目标引用，验证其 bot/session/作用域后补齐合规历史；不增加容器枚举接口，不借用当前发言人的执行 token 定位其他 caller。恢复 observe-only 引用不恢复执行权限。
- 成员退出、目标解绑时 BCS 停止同步，Gateway 拒绝失效目标；私聊和 caller 私有状态不能混入共享流。

---


### 6.7 执行期间 inject 的可见性（B2）

- `chat.inject` 可以持续更新目标 Bot×session 的合规共享历史，但不能隐式修改已启动 run 的共享历史输入，也不能把后续 Human 消息当作旧任务的新指令。
- run 执行时使用准入已固定的共享历史水位/快照；快照必须遵守 BCS 批准的本次任务历史范围与 §6.6 的补历史规则。排队期间到达的新消息不能因实际执行较晚而自动扩大该任务的历史范围。
- 冻结的是外部共享历史输入，不是禁止 run 正常接收自己的工具结果、推理过程或受控子任务结果。执行过程中不得通过重新读取最新 session 历史绕过该快照边界。
- 水位之后的 inject 仅供后续符合历史范围的任务读取；`observe_only` 不启动 run、不唤醒工具执行，也不改变已有执行 caller。显式更新在途任务不属于 inject 契约。
- 执行前补历史、当前消息 send/inject 去重以及目标 Bot 的既有定向可见范围继续生效；不得把整个 session 全量广播作为快照方案。
- BCS 负责合规历史范围和投递顺序，Gateway/Runtime 负责快照执行与运行中隔离。历史范围及就绪屏障通过 §6.8、§10.9–§10.10 的契约承载；既有等价字段可映射复用，不允许仅凭“队列有序”省略就绪确认。

### 6.8 Root / Child 历史边界与执行就绪屏障

以下是 caller-aware 群任务（含 Owner delegation_only 任务）的历史契约；非 caller 群保持既有行为。历史分为两类，不能混用：

1. **共享历史快照**：BCS 为目标 Bot 选出的会话共享消息集合。Human 消息准入时固定 `history_cutoff_seq`，取本条消息之前的 session 序号；当前任务消息由 send 单独传递。记录 `history_snapshot_id`、目标 Bot/session、cutoff、投影规则版本、消息 ID 清单或可重建该清单的不可变索引，以及 `manifest_digest`。这些均为非凭据业务元数据，允许持久化；只存一个最大序号不足以证明补齐。
2. **任务因果输入**：本次 task、获准传给目标的前驱结果或 child 结果。它可以在 cutoff 后产生，但必须关联真实任务/节点结果并按既有定向投递规则显式传递，不得借此读取 cutoff 后整个群历史，也不自动进入共享流。

child 沿用 parent 的 `history_cutoff_seq` 上界，不能取派生时最新群水位。目标不同则重新计算“目标 Bot 既有投递范围 ∩ 原历史上界 ∩ 当前仍允许访问的范围”；不是复制 parent Bot 的全部消息。Workflow 同一编排实例内所有节点继承初始 Human turn 的上界，join 的前驱结果属于因果输入，不提高该上界。权限收窄时不读已撤销内容；无法得到一致的合法快照则拒绝执行，不静默改用最新历史。

执行顺序：

```text
BCS 固定目标快照 → Caller 目标 ensure 得到观察引用和环境代次；Owner 目标沿用既有 Owner 路由
→ inject 合规缺失消息（按 message_id 去重）
→ inject history_complete（快照清单/摘要）
→ Gateway/Runtime 返回 history_ready（精确快照、目标及当前环境代次）
→ BCS chat.send 引用该快照 → Runtime 再次确认就绪且固定读取范围 → 执行
```

- Owner delegation_only 任务同样需要固定快照及 ready，但不调用 Human×Owner ensure；由受认证 Gateway 根据既有 Bot Owner 路由准备历史。其控制消息使用 §10.10.1 的 bot_default 目标变体，不伪造 Human 观察引用，不能跨入 Caller Container。
- `history_ready` 只在实际应用所有选定消息并可按该快照隔离读取后返回；空快照也要确认。序号空隙可以合法存在，不能用最大已收序号替代清单校验。
- history_ready 丢失时可幂等重发 complete；重复确认不启动任务。先到的 send 不执行，返回 `HISTORY_NOT_READY`；BCS 补齐后只重投原 dispatch，不新建 attempt。
- 环境回收/代次变化使旧 ready 失效。执行前可重新 ensure/补历史；已可能开始执行则按 §7.8 处理，不能用新容器自动重放旧任务。
- 同 Bot×session 持续 inject 的最新历史与 run 固定快照并存；只有最大水位、无法隔离历史读取的 Runtime 不满足 caller 协作接入条件。


## 7. A2A / Parent Run / Child Run

### 7.1 私聊或开放式自由 A2A

默认不传递 Human caller：

```text
A -> BCS -> B
execution_mode = open_task
caller_context = null
caller_execution_token = null
```

如果 B 配置 caller_identity.enabled=true：

```json
{
  "error": {
    "code": "CALLER_CONTEXT_NOT_AVAILABLE",
    "message": "target bot requires caller identity, but this A2A path is not a caller-controlled scope"
  }
}
```

原因：私聊开放式 A2A 缺少可控 group/session/message_turn 边界，容易从 H→A 发散到 B/C/D，Human 难以感知。

### 7.2 群内受控 A2A

如果 A 和 B 都在同一个 caller-enabled group/session 内，且 A2A 请求经过 BCS，则可以受控传递 caller。这里的“受控”不要求一定是固定的 Manager/Worker 接口；群内自由聊天产生的 A2A，只要仍处在同一个 group/session/message_turn 边界内，并能通过可信 parent run/attempt 关联校验（采用 proof 路径时包括 token 校验），也可以按受控 A2A 处理。

未进入受保护队列的 legacy relay `Send` 不具备 Caller 派生能力。已装配 Caller 配置检查时，对 Caller-enabled 目标或配置读取失败必须返回该目标投递失败，不能生成普通 Owner `chat.send`；普通 Owner 目标与 observe-only `Inject` 保持原行为。此拒绝保护不代表 legacy relay 已支持 Caller 继承。

A 发起 child dispatch 时，A 只提交任务关系：

```json
{
  "target_bot_id": "bot_b",
  "message": "请协助处理这部分任务",
  "parent_run_id": "run_alice_a"
}
```

Runtime/Gateway 只需要提供模型不可覆盖的真实 parent run/attempt 关联，不在业务消息或回执中回传 `token_A`：

```json
{
  "parent_run_id": "run_alice_a",
  "parent_attempt": 1
}
```

BCS 用这两个事实在 Identity Material Ledger 中取回上一跳 token 或根身份 handle，再完成目标 Bot 的 exchange/issue。Bot A 业务逻辑不能读取或手动转发任何身份材料。

### 7.2.1 自由群 Owner Bot 的 lineage 初始化

在已启用受控 A2A 的 caller 群，BCS 接收 Human 发言并通过 Lease 准入时，就为本次实际接收任务的 Bot 建立 caller-aware root lineage，不等待其决定调用谁：

| 目标 Bot | 执行与路由 | 运行时身份用途 |
|---|---|---|
| caller enabled | caller_task；ensure Human×Bot 容器 | execution，按受控策略允许派生 |
| caller disabled | open_task；原 Owner 路由，不为该 Bot 强建 Human 容器 | delegation_only；仅供受信 Runtime/Gateway 向 BCS 关联 child |

Owner A 的关联绑定本次 Human、A 的真实 run/attempt、group/session/turn、历史上界、TTL 和派生预算；模型不可见，身份材料仅在受保护内存中。A 的 MCP 仍使用 Owner 策略，不能消费该关联作为 Human 执行凭据。Gateway/Runtime 不支持 Owner 运行的受保护 lineage 承载时，明确返回 `CALLER_LINEAGE_UNSUPPORTED`，不得悄悄丢弃关联后声称支持 caller 协作。

A→Owner B→Caller C 每跳仍经 BCS：对 Owner child 建立新的 delegation_only 关联，对 Caller child 建立 execution 关联；共用原 turn 预算，不额外弹 Human 授权。Owner 中间节点不因未声明 caller 而抹掉已批准 lineage，也不能扩大其范围。

普通群、没有可信 Human 的 Bot 自发消息、私聊 Owner A，以及未获准 caller 协作的路径不初始化该 lineage。运行后才开启配置不能追认旧任务身份；缺失/过期关联只能拒绝继承，不能由 sender、Lease holder 或历史消息补建。


### 7.2.2 BCS 侧身份材料与父 token 兑换

BCS 不要求 Provider 把 `token_A` 原样回传；BCS 在**本地短期 Identity Material Ledger**中同时
保存 Human origin handle 和本次派发的 parent execution token，供下一次派生使用。

```text
H ingress
  -> BCS Identity Material Ledger
       ├─ Human origin handle   # root/lineage
       └─ Execution token       # caller parent
A -> B:
  BCS 用 parent_run_id/attempt 取出 A 的 execution token
  BCS 依据 target Bot、run/attempt、scope、purpose 调插件 exchange
  BCS 将 token_B 原样交给目标 Runtime，并新登记 token_B 为可派生材料
```

| 场景 | BCS 使用的材料 | 下一跳 token |
|---|---|---|
| H → A(caller) → B(caller) | A 的 parent execution token | 由 token_A 显式兑换 token_B，并把 token_B 登记为下一级 parent |
| H → A(owner) → B(caller) | 原 Human origin / delegation lineage handle | 直接签发 token_B，A 不需要也不获得 caller execution token |
| A(owner) → B(caller) 多跳 | 继承祖先 lineage handle，不重新取当前发言权 | 由旧 lineage 签发新目标 token，不得放宽 proof/Purpose |

如果目标 Bot 是 Owner，BCS 不签发 `caller_execution_token`，只在 Identity Material Ledger 中保留可派生的 lineage handle；后续需要 Caller child 时再从原 lineage 签发目标 token。


### 7.3 Parent Run 校验

BCS 通过现有执行服务读取真实状态与 attempt；下列状态名表示逻辑语义，由各执行拥有方映射，不新增 Caller 生命周期枚举或表。校验须遵守既有派发/取消的 CAS 边界，不能只凭一次过时的查询授权派发。BCS 校验分两层。以下清单适用于 `parent_kind=bot_run`；`parent_kind=orchestration` 使用 §8.5 的编排归属校验，不冒充 source Bot，也不使用已完成的数据前驱作身份 parent。

#### 既有执行对象的权威事实校验

```text
1. parent_run_id 存在
2. parent_run.status in [running, waiting_child, waiting_tool, waiting_authorization, streaming]
3. parent_run.caller_lineage == true
4. parent 为 caller_task，或具有经批准 delegation-only lineage 的 owner/open_task
5. parent_run.target_bot_id == source_bot_id
6. parent_run.group_id/session_id 与当前 child dispatch 一致
7. parent_run.message_turn_id 与当前 scope 一致
8. parent_run 未过期
9. parent_run.delegation_depth + 1 未超过策略
```

#### 可信 parent 执行关联校验

BCS 验证请求服务身份与真实 parent run/attempt 的归属，并从受保护关联取得原 caller；不能接受模型自填 parent ID 或 DB holder 作为身份依据。可复用受信 Runtime 回调关联；无法取得该关联时拒绝派生。

采用 proof 路径时，另执行下列 token 校验；无 token 不等于免除上述关联校验：

```text
1. token 由 BCS/AgentPass 信任边界签发
2. token 未过期
3. token.aud == bot_runtime:{source_bot_id}
4. token.run_id == parent_run_id
5. token.root_run_id == parent_run.root_run_id
6. token.target_bot_id == source_bot_id
7. token.group_id/session_id/message_turn_id 与 parent_run 一致
8. token.delegation_depth == parent_run.delegation_depth
9. token 允许继续派生 child
```

### 7.4 Child Run 独立上下文与按需签发

校验通过后，由现有协作执行链创建或幂等定位目标任务。Caller 使用该任务的真实 run/attempt，不再注册一份 A2A 专用 run。逻辑关联示例：

```json
{
  "run_id": "run_alice_a_b",
  "root_run_id": "run_alice_a",
  "parent_run_id": "run_alice_a",
  "message_turn_id": "turn_001",
  "source_actor_id": "bot_a",
  "target_bot_id": "bot_b",
  "execution_mode": "caller_task",
  "delegation_depth": 1
}
```

然后 BCS：

```text
1. 先根据可信 parent run/attempt 在 Identity Material Ledger 取得 caller 或 human origin 材料；caller 子链必须使用 parent token 兑换，Owner 中间链则使用 ancestor/handle 材料
2. 不写入 DB
3. Ensure caller×BotB Caller Container
4. 为 BotB 建立绑定 child run/attempt 的独立 caller_context
5. 所选凭据路径需要时，为 BotB 签发新的 caller_execution_token_b
6. 使用 token 时，根据 ensure 结果加入可选 isolation binding 约束
7. 按 §6.8 为 BotB 固定 parent 历史上界内的快照，补齐并取得 history_ready
8. 按 §7.8 登记 dispatch 并发送 chat.send；Gateway 内部路由且检查快照就绪
```

也就是说：

```text
token_alice_a 只给 BotA 当前 run 使用
token_alice_b 是 BCS 为 BotB child run 新签发的 token
```

---

### 7.5 Turn / Run / Attempt / Proof（B6）

| 概念 | 语义 | 不代表什么 |
|---|---|---|
| message_turn | 一条通过可信入口及准入检查的 Human 消息产生的协作关联；同条消息派出的多个 root 及受控 child 共享该关联 | 不是发言权 Lease，也不是凭 ID 就能执行的身份凭证 |
| run | 每次独立的目标 Bot 逻辑任务；各 root 独立记录状态，child 独立建 run 并关联 parent 和所属 root | 不是一个 Bot 永久共用的任务，也不是同条消息所有 Bot 共用的状态 |
| attempt | 同一逻辑任务的一次实际执行尝试；明确批准的执行重试创建新 attempt | 不是网络重投次数，不自动保证副作用幂等 |
| proof | 面向目标 Bot、run/attempt、scope、用途及有效期的受保护身份证明 | 不是 Human 原始登录态，不是 MCP Grant，不是公开消息正文 |

规则：

1. 同一消息直接派给 A、B（非 Workflow 节点）：两个独立 root run，共享 turn；A 再受控派给 C：新 child run，parent=A 的 run，root=A 所属 root，turn 不变。后续另一条 Human 消息必须使用新的 turn/run，不继承旧消息证明。Workflow 节点关联活动编排 parent，见 §8.5。
2. 优先复用已有消息 ID 作为 turn 关联，复用既有 run/attempt 模型；这些逻辑职责不要求新建四套对象或四张表。实际字段和数据库映射需对照完整源码核实，不宣称现有系统已支持。
3. 真正执行重试：只有满足任务重试、身份有效性及副作用安全条件，才允许同 run 下新 attempt。网络超时后重投同一派发必须保持幂等，不新建执行尝试、不重复创建 child、不延长证明有效期。不得因为不知道第一次是否执行就直接再执行。
4. 每个新的逻辑派发/执行尝试建立独立受保护上下文；按插件、A2A 关联或出口需要签发受限 proof（按 B9，不强制所有下行携带 token）；普通网络重投不是新的签发授权。重投使用的凭证与幂等标识具体 wire 映射在实现中核实，但不得扩大权限、续期或产生额外执行。因身份过期由 Human 重新提交，按 B5 创建新 turn/run，不恢复旧 attempt。
5. proof 须约束目标、run/attempt、turn/session（群聊含 group）、用途及有效期；BCS 核准后 Gateway 按受信上下文路由，Runtime 保证执行绑定与隔离；如使用 proof，由实际消费方按契约处理，不能仅凭相同 turn 或可猜测的 run ID 执行。可信上一跳 proof 还必须匹配发起 child 的有效 parent 执行尝试；旧 attempt 的迟到请求不得替换当前 attempt。
6. child 由 BCS 校验 parent 状态、可信执行关联（按需验证 proof）、成员/拓扑/派生预算后建立独立上下文，按需签发 token，不直接转用 parent token；child 不扩大上一跳范围或延长可信有效期。本规则不扩大已允许的 A2A 范围。
7. `purpose=execution` 用于指定目标任务的 caller 执行，MCP 出口仍需检查具体权限；`purpose=delegation_only` 仅供 BCS 验证受控 child 派生。Owner Manager 自己仍按 Owner 执行，不能用后者执行 Human MCP；执行 proof 也不自动赋予任意派生权限。
8. proof 由受信 Gateway/Runtime 在模型不可见的运行时信封中保管与传递，不写入消息、日志、数据库或持久化快照。非敏感任务关联元数据与证明分离，不得持久化 caller 身份材料。
9. 状态事件必须关联真实 run/attempt，并按受信来源、事件顺序及合法状态转换处理。旧 attempt 的迟到 final/failed 不得结束新 attempt，重复事件不能重复推进状态；取消确认也必须对应被取消的执行尝试。

示例（逻辑关系，不是已核实的 wire schema）：

```text
Alice 消息 M1 / turn T1
├── Manager A：run R1 / attempt A1 / Owner 执行
│   └── runtime-only 上下文：delegation_only（proof 按需）
│       └── BCS 校验并派发 Worker C
│           └── run R3 / attempt A1 / parent R1 / root R1 / turn T1
│               └── execution 上下文：caller Alice，目标 C（proof 按需）
└── Bot B：run R2 / attempt A1 / root R2 / turn T1
    └── 合法执行重试：run R2 / attempt A2 / 独立受保护上下文（proof 按需）
```

§10 中省略 attempt 的示例仅是字段片段，不是完整派发契约；真实执行与事件必须携带可验证的 attempt 关联。

#### 7.5.1 已提交 Delivery 的身份准备与派发

Caller 准备必须使用 Managed Delivery 已提交的真实执行关联，而不是在 queued 阶段猜测 attempt：

```text
可信 Human 入口：同次认证得到 HumanActor + ingress
  → 应用准入：核实目标、Session、成员和当前 Bot Caller 配置
  → Managed admission：提交 canonical message + queued delivery
    ├─ DB：非敏感任务关联、caller_required、caller_config_revision
    └─ 同一 leader epoch 内存：一次性 ingress handoff
  → 原 elected scheduler 选中目标，StartSend CAS 提交真实 run/attempt/request
  → 独占消费 handoff，复核当前策略
  → subject → Gateway ensure → 按需 issue/verify
  → 最终策略及 canonical attempt 复核
  → CAS 提交规范化任务摘要和执行截止时间，再复核派发版本
  → 受保护 chat.send → Gateway → Caller Container
    ├─ accepted：仅进入受理/运行状态，不代表完成
    ├─ outcome_unknown：查询原 attempt，不盲目重发
    └─ cancel requested：重放已提交取消控制，等停止/终态证据
  ← 受认证终态回执：Managed CAS 收敛；结果发布遵守独立消息契约
```

**真实模型映射：** `message_turn_id = source_message_id`，使用 canonical message ID，
不是客户端自报 `client_msg_id`。`StartSend` 提交 `attempt_no` 与 `request_id`，后者作为
`dispatch_id` 及本次 ensure 幂等关联。H2A 根任务的 `root_run_id = run_id`、
`parent_run_id = None`；同消息多个目标独立 run，共享 turn。attempt 必须大于零，
签发和验证均拒绝零值，不自动修正为 1。child 关联另按 §7.2–§7.6 校验。

**H2A 准入与策略复核：**

- 同次可信认证产出 HumanActor 和 ingress，核对 principal/sender 对应关系；
  请求正文不提供身份、目标策略、run/attempt 或已准入投影。DM 的唯一 Bot 由真实路由确定，
  正文 @ 其他 Bot 不改变目标。
- Group 必须是 active 的 Chat DM，成员恰为该 Human 与目标 Bot；Session 属于该 Group，
  处于 Running/Chat，Human 为 Present、Bot 为 Auto。不通过自动加成员、取消静默或恢复
  Session 绕过准入；检查必须早于这些副作用。
- 以同环境控制面中的已启用 Caller 配置和 minimum isolation 为准；
  `caller_config_revision` 取权威配置版本，不采信请求体版本。配置损坏或读取失败不得解释为 disabled。
- 准备前和交付执行材料前复核成员、Session、配置及隔离要求；版本须与已准入版本一致。
  关闭后重新开启的新版本不能静默升级旧任务。策略依赖或可信关联缺失即拒绝。
- 仅支持某类输入的适配路径应明确拒绝附件、child 或历史等未支持投影，不能截断为文本执行。
  普通聊天路径不能承接要求 Caller 的任务；未满足接入条件时，在副作用前 fail closed。

**准备期限与失败收敛：** ingress、请求和 run 的最早截止构成同一个准备预算，
覆盖策略复核、身份插件及 Gateway 等待；阶段切换不重置预算。proof 的有效期还受
subject、容器准备结果及原身份上界约束，不能用容器 TTL 延长身份 TTL。
数据库初始读取和终态写入遵守各自超时，不将准备预算描述为整个 API 的耗时保证。

缺失/错范围/旧版本/旧 attempt/已提交/取消/Unknown/终态均拒绝。只有 owning dispatcher
持有“本 attempt 确认未发送”的证据时，才用原 `state_version + request_id` 提交
`DefinitelyNotSent(retry=false)`；取消已先提交时不得覆盖取消。CAS 冲突和存储失败必须传播，
不通过重读新版本强行提交，不自动重试、续签或回退 Owner。ensure 成功不等于已执行，
后续失败不盲目销毁长期复用容器。

准备结果只在内存中持有。独立 `validate` 是只读失效检查，不撤销已交付 proof，也不是
原子发送许可；发送与取消的本地 CAS 排序及远端去重/fence 必须同时遵守 §7.8、§10.12.1。

#### 7.5.2 一次性身份交接与任期生命周期

HTTP 请求结束后到异步派发前，通过同一 owning dispatcher 任期的有界内存 handoff
承载身份；不将身份加入 durable queue，不从消息 sender、DB Lease holder 或 run 元数据恢复身份。

| 阶段 | 规则 |
|---|---|
| 预留与准入 | 在普通 Managed admission 前预留有界 slot；scope 固定 environment、group/session、canonical source message 和目标 Bot。目录键不含 principal、subject 或 token |
| 发布 | 只有 fresh Queued 回执才能发布一次身份；重复 client key、DB duplicate、失败或不确定提交不能补入新身份、覆盖旧身份或延长有效期 |
| 领取 | 只允许首个已提交 StartSend attempt 独占领取；领取即消费，失败、超时或 future 取消不归还身份；锁不跨 DB/Gateway await |
| 校验 | 核对真实 delivery/run/attempt/request/version 与准入策略关联；持久化配置版本不是身份证据 |
| 清理 | 容量包含 pending 与已消费 slot；新准入和空闲期都清理过期项，不能只依赖后续消息触发清理 |
| 关闭 | owner 丢失、降级、异常退出或 shutdown 时不可逆关闭；身份发布、领取、结果交付与 close 排序，关闭后不再交出 prepared 结果 |
| 再当选 | 创建空的新任期，不能 reopen 旧对象或为旧 durable 消息补签；follower 不接收需要本任期身份交接的准入 |

关闭先于准入完成时，DB 仍可能提交成功，但不再发布身份；关闭或取消本地 future 不证明
远端 ensure/send 已撤回，也不撤销此前已交付的 proof。在途材料随本地工作结束释放，
不承诺强制远端终止或内存擦除。缺失、已消费或关闭的 slot 不能单独证明“未发送”；
已经可能派发的任务只能按原非凭据关联查询、取消或等待证据。

**同一调度所有权：** ingress、handoff、sender、取消及恢复必须共享同一环境、
Managed 服务和 elected scheduler 任期，不新增第二个 Caller scheduler。身份准备与发送
沿用任务上限及目标 Bot 的串行/发送间隔约束；慢 ensure 不能绕过调度限制。
启用发送须同时具备策略检查、受保护传输、状态查询、取消及有效超时配置，缺失时拒绝装配。
控制恢复不需要重建 Human 身份，任期关闭不等于业务任务取消。

**持久执行要求与防降级：**

- 单目标 projection 的 `caller_required=true` 仅表示不得按 Owner 执行，与 delivery 同事务提交；
  不包含 caller、subject、IAM、token、proof、可兑换引用或容器映射，不构成身份凭证。
- 普通执行路径遇到 Caller 标记必须拒绝，包括异常 `false/null`，不得删除标记后调用 Owner transport。
  普通任务省略该字段，保留原行为；回滚版本也必须拒绝无法理解的 Caller 投影。
- 已启动 Caller 任务的 `DefinitelyNotSent` 不允许自动重新入队；缺失身份、关闭 handoff
  或 Bot 后续关闭 Caller 都不消除原执行要求。重新提交必须重新认证。
- 身份与 prepared 对象不得公开序列化或记录；消息回执只包含非敏感关联。
  进程内一次性消费不等于端到端 exactly-once 执行。

### 7.6 群内 caller 派生开放范围与预算（B10）

预算属于既有协作控制，不由 Caller 新建生命周期账本。下列限制仍保留；优先复用现有限额与幂等机制，确实缺少累计计数时只在协作拥有方补最小计数，不附带独立 parent/child 状态及结果表。未具备所需限制的模式不开放 Caller 继承。

自由群、主子及 Workflow 共用 BCS 受控派发机制，不新增 Human 逐跳授权。自由群也允许 caller 继承，但必须来自同群、同 session 的有效 parent run/attempt，Bot A2A 的发起与目标 Bot 均为有效成员；Workflow 系统调度校验受信编排来源及目标 Bot 成员关系，不虚构 source Bot（§8.5）。主子/Workflow 还须满足其角色或拓扑约束。私聊开放 A2A 不因此获得继承能力。

- caller 从原任务的可信执行关联取得，不接受模型自报，不取当前发言权 holder。BCS 核验受信请求方与实际 parent 执行尝试关联，仅知道 parent ID 不足以派生。
- 自由群最大派生深度初始为 **2**，服务端可配置：Human 直接目标为 depth=0，A→B 为 1，B→C 为 2；C 再派生则拒绝。主子/Workflow 按既定有限拓扑深度控制，不强制套用自由群两跳值。
- 群内受控协作每个 Human turn 最多预占 **16 次派生调用**；同一 session/turn 的多个 root 共享预算，root 本身不扣额度。这不是“成功创建 child 的数量”，不维护独立 child 状态或结果账本。
- BCS 在受信协调入口校验 parent 后，先原子预占内存额度，再调用既有任务准入。任务 run、幂等、结果和终态继续由原执行链拥有。失败、取消、结果未知均不退额度，避免不确定提交造成超发；代价是本 turn 可实际创建的任务可能少于 16。既有协调 intent/toolCallId 去重处理请求重放，内存计数拒绝重复 parent/invocation；同 run 的发送/attempt 重试不重新走调用准入。
- 计数只由 BCS 的有界可信内存持有，不信任 Bot 消息自报 remaining；同一个 owner 内的所有 root 共用计数。身份和预算丢失（重启/owner 切换/过期）后拒绝旧链继续继承，不从 DB 或会话消息重建身份，也不把旧 turn 预算重置后放行。此方案不承诺跨 owner 的分布式累计计数；未能保证单 owner 的入口不得开放 Caller 继承。超限明确拒绝，不降级 Owner。
- 当前协调入口上限固定为 depth=2、每 turn 16 次，不宣称已有可配置拓扑预算。未来拓扑配置由既有协作拥有方实现，不为 Caller 增设生命周期。
- child 不延长原身份或 parent 的剩余有效期；不另设自动续期。抢占不取消旧任务，但旧任务派生仍使用原 turn/caller 和原剩余预算。
- 并发/排队复用 Gateway/Runtime 既有机制并遵守 B1，不新增独立并发调度系统。inject 始终 observe-only，私有工具结果不自动公开。 群内协作采用 §7.7 非阻塞消息串行语义，等待 child 不占住阻塞后续消息的执行位置。
- MCP 权限和既有异步授权由 Runtime/Egress 负责；不新增 Bot 跳转授权，不绕过既有具体操作确认。任务状态与取消入口可复用现有能力。

### 7.7 群内非阻塞消息串行协作

Caller 方案沿用群内非阻塞的消息串行协作，不新增回环检测器或独立调度系统。此处确认的是接入要求，不代表已核实当前 BCS/Gateway/Runtime 实现具备该行为；实施前须按 Plan 验收。

**消息处理段、执行位置与逻辑 Run 终态必须区分：**串行约束作用于既有调度粒度下的消息处理段；Bot 发出协作请求后，不持有会阻塞目标任务或后续回复处理的执行资源。释放执行位置不等于原 run 已进入 final，也不改变原 caller、attempt、历史范围或身份有效期。Speaking Lease 只控制 Human 消息准入，不是执行锁。

```text
A 处理 Human 消息 → 提交 B 的 child 请求 → 释放消息处理执行位置
B 处理协作消息 → 如需 A 协助，提交 A 的 child 请求 → 释放执行位置
A 处理新的协作任务 → 结果按真实 run/attempt/parent 关联返回
原任务接收自己的 child 结果，不从当前 Lease holder 或最新群历史重建身份
```

规则：

1. 同步阻塞式“父任务占住执行位置直到 child 完成”不属于本接入方案。BCS 的路由处理结束不足以证明符合要求，Gateway/Runtime 也不得保留会阻塞 child 或结果处理的占用。
2. child 准入必须在 parent 仍有效时完成，并与 parent 终态转换原子排序。调用方仅发出请求不等于 BCS 已登记 child；重复请求按既有幂等关联返回，不重复消耗预算。
3. parent 仍需接收 child 结果时，释放执行位置但保持 `waiting_child` 等合法非终态；收到结果后按原任务关联继续，不把回复误判成新的 Human turn 或新的 child。释放再继续本身不构成新的 attempt，真正执行重试仍按 B6 处理。
4. parent 不再等待结果且任务完成时，只能在必要 child 已被 BCS 接受登记后进入终态。终态后才首次到达的派生请求仍拒绝，不为迁就异步传输放宽 active parent 校验。已登记 child 按自身状态及适用取消策略处理，不能把回复变成新的派生授权。
5. Runtime 必须在模型不可见、非持久的执行上下文中保留所需关联；切换处理段不得覆盖其他 run 的身份或私有状态。继续处理前重新检查身份有效性、取消状态及隔离条件，不续签，不回退 Owner；身份过期沿用 B5。
6. A→B→A 的消息往返本身不认定为占用型死锁；持续循环派生仍受 depth、每 turn child 总数及 TTL 限制。每次独立目标任务仍有独立 run，普通结果回传不计为新 child。
7. B1 的 session/run 隔离要求、B2 的历史快照要求保持不变。不能通过释放执行位置让原任务读入后续 Human 指令，也不能借此默认允许同容器并发执行。
8. 无法安全释放执行位置、保留隔离状态并继续处理结果的接入方，不开放依赖该能力的 caller 协作路径；不得以“BCS 已串行”代替验收。该限制不引入全群锁，不改变普通非 caller 群行为。

验收至少覆盖 A→B→A 的非阻塞消息往返、父任务等待时释放执行位置、child 登记与 parent final 的竞态、child 结果去重、不同 caller 切换不串身份，以及等待期间身份到期或任务取消后不继续执行。

### 7.8 派发、取消、Attempt 与故障闭环

逻辑 run 状态、网络派发状态和取消状态分别记录，不用 HTTP 超时推导执行结果。下表均为非凭据业务状态；身份信封不进入 DB/outbox。

| 对象 | 状态/字段 | 语义 |
|---|---|---|
| Dispatch | `dispatch_id`、run/attempt、目标、`request_digest`、`not_after_ms` | 同次网络重投不变；摘要仅覆盖规范化业务任务及非秘密控制元数据，不散列或存储身份原文 |
| 派发状态 | `prepared / sending / outcome_unknown / accepted / rejected` | accepted 仅证明下游已受理，不等于开始或成功；rejected 必须证明该 dispatch 未启动 |
| 执行状态 | `queued / running / waiting_child / waiting_tool / waiting_authorization / streaming` 与既有终态 | history 就绪不是 running；waiting 不释放任务身份约束 |
| 取消状态 | `none / requested / fenced / stopped / unconfirmed` | fenced 表示迟到派发已被拦截，不表示运行中的工具已经停止；stopped 才是停止确认 |
| Attempt fencing | `current_attempt`、单调 `control_revision` | 同 run 的有效尝试和控制版本；不同于 session Lease epoch |

#### 7.8.1 派发结果未知与重投

1. BCS 先原子提交 run/attempt、dispatch、历史快照和非凭据状态，再发下行；写失败不派发。发送前以 CAS 检查当前 attempt、取消/到期/配置状态，将 prepared 置为 sending。凭据仅在内存中装入信封。
2. Gateway 以 `(env, provider, bot, run_id, attempt, dispatch_id)` 去重；同 ID 不同规范化任务摘要返回 `IDEMPOTENCY_CONFLICT`。它在启动 Runtime 前登记受理事实；已受理或永久拒绝的重复请求只返回原状态，不能重复执行。持久化的去重/取消标记不含 caller、binding 或 token。
3. 请求超时、断连或 BCS 在 sending 后重启，一律视作 `outcome_unknown`，不新建 attempt，不标记“执行失败可安全重试”。BCS 通过 §10.12.1 状态查询/受信事件确认。若幂等账本和旧身份仍有效且没有取消，可以原 dispatch 重投；不会延长 TTL 或再次消耗 child 预算。
4. `not_found` 只表示没查到，不能证明在途请求以后不会到。若要放弃旧派发或进入新 attempt，先 fence 旧 attempt 并取得“旧派发不再可启动，已运行任务已停止/结束”的证据；未知状态不可直接重试。
5. run 已提交但内存身份因崩溃丢失：prepared 且确定未发出时等待可信材料或失败；sending/unknown 时只能查询/取消，不从 DB holder 或消息恢复身份。查到旧执行成功可处理受信结果，不等于恢复身份或授权新执行。无通用自动重放承诺。
6. Gateway 重启后若丢失受理/去重/取消账本，不得把迟到 send 当首次请求；须阻断该执行通道，直到完成 BCS 当前 attempt/取消状态对账并恢复隔离。无法证明未执行仍返回 unknown，不返回可安全重试。账本至少保留到 not_after 加约定的最大重投/时钟偏差窗口；超期请求无条件拒绝。

**无凭据状态恢复：** 从原已提交 dispatch 与 Provider transport snapshot 取得
`environment / target_bot_id / provider_id / provider_bot_ref / session_id / run_id / attempt / dispatch_id`。
查询不读取登录态、不 ensure 容器、不兑换身份、不切换 Provider。Accepted 仅证明受理；
NotFound/Unknown 不释放 lane、不新建 attempt。使用查询前版本进行 CAS，拒绝并发取消、
终态或新 attempt 造成的旧快照；版本化 wire 与恢复生命周期见 §10.12.1。

#### 7.8.2 取消与延迟派发

- BCS 用事务/CAS 标记 requested 并提高 control_revision，原子阻止该 run 的后续派发/child 准入；撤销 scope 时对相关活动任务执行相同流程。与终态/child 创建的竞态必须有唯一提交顺序。
- `execution.cancel` 可以先于 chat.send 到达。Gateway 先记录该 run/attempt 的取消 tombstone，清除未启动排队项，再返回 fenced；此后旧 revision/attempt 的迟到 send、重投及恢复继续均拒绝。取消不要求携带 Human token，即使身份已过期仍可由认证 BCS 发起。
- 已运行任务由 Runtime/Egress 停止新工具启动并尝试取消在途操作；所有阻塞执行已结束才回报 stopped。外部已发生副作用不保证回滚；不可中断工具明确返回等待停止/状态未知，不能假报 cancelled。
- BCS 取消先提交但 Gateway 尚未收到 fence 的传播窗口内，旧 send 仍可能已开始；不能声称跨系统原子撤销。BCS 不再发送、持续重试取消并反馈未确认；传播/停止 SLA 由下游承诺及故障测试验收。停止确认前不启用替代 attempt。
- 若 final 先于取消提交，保留已完成结果；若取消已提交后才收到迟到 final，记录实际结果/副作用并通知状态，不将逻辑取消改成新的成功执行，不派生或恢复。`unconfirmed` 不是已停止，普通超时也不是。

#### 7.8.3 旧 Attempt 与容器回收

- 新 attempt 只能在旧尝试已结束或 fenced 且实际停止、身份仍可信有效、任务重试/副作用策略允许时创建；BCS CAS 提升 current_attempt，Gateway 安装新执行版本后拒绝旧 attempt 的 send/继续/工具启动。
- 事件关联 provider/Bot/run/attempt/dispatch/event_seq；旧 attempt 的 final、delta、授权成功、child 请求不得推进新 attempt、发布成新结果或触发新工具。可保留不含凭据的迟到事件审计，不能覆写终态。
- child 记录 `parent_attempt`；父 attempt 被替换前，旧尝试派生且仍活动的 child 必须结束或被取消并确认。不得将旧 child 的结果自动接入新 attempt，避免双重副作用。
- 容器代次在确定尚未执行时变化，可以在原 dispatch 下重新 ensure 和准备同一历史快照；若 proof 绑定旧代次，只在可信身份仍有效且旧派发已被证明未启动/不可启动时重建受限材料，不延长有效期。已可能执行则进入 unknown/取消流程；不能因容器消失就自动重跑。

#### 7.8.4 串行回环与继续执行

复用 §7.7，不新增调度器或回环检测器。A→B→A 是三个独立目标 run；原 A 在 waiting_child 时释放会阻塞新 A 处理段的资源，结果返回再继续原 run/attempt。释放位置后不能残留全局 mutable caller/token。

child 接收 ACK 丢失时 parent 保持等待并按同一 child 幂等键查询/重投，不提前 final；child 结果先于 parent 进入 waiting_child 时，由既有相关事件队列/结果表按 run/attempt 幂等挂接，进入等待后恰好消费一次。该缓冲不包含身份信封，私有结果遵循既有私有结果存储策略，不塞入群共享消息表。身份内存丢失不能仅凭结果自动恢复执行。超过 depth/预算必须返回确定失败结果使 parent 收敛，不无限等待。

接入验收必须证明：释放执行位置、隔离不同 caller、保留原快照、取消/到期后不继续、先到结果不丢失；不满足时拒绝相关 caller 协作能力，而非改成全群大锁。


## 8. Manager/Worker 与 Workflow

### 8.1 适用原则

Manager/Worker 和 Workflow 是 BCS 受控编排链路，比开放式 A2A 更可控，可以传递 caller，但仍必须遵守：

```text
Bot 不直接转发 caller 身份
每个 worker/node run 都由 BCS 独立建立上下文，按需签发 token
Manager child 关联 Bot parent；Workflow node 关联活动编排 parent，数据前驱另列
DB 不保存 caller 认证/执行材料
```

### 8.2 初始化规则

对于 H 触发的主子或 workflow 任务：

```text
只要参与拓扑中任一 Bot 声明 caller_identity.enabled=true，root run 就进入 caller-aware 初始化。
```

BCS 可以为支持 caller 的参与 Bot 预建逻辑 binding；物理 container 可懒创建。

### 8.3 Manager owner、Worker caller 的情况

如果 Manager 未开启 caller、Worker 开启 caller：

```text
H -> Manager root task 仍需要创建 caller-aware root lineage，至少用于后续 Worker child run。
Manager 自己可用 owner/open_task 执行，但 BCS/Gateway 需要在 Identity Material Ledger 中保存仅可派生的 Human/lineage 上下文（purpose=delegation_only，proof 按需）以便 worker 派生；该材料只能在内存或 runtime-only metadata 中存在，不能写入 DB 或暴露给 Manager 业务逻辑。
```

Worker 执行时：

```text
Manager -> BCS: dispatch worker task + parent_run_id
BCS 校验 workflow/manager-worker 拓扑
BCS 为 Worker child run 建立独立 caller_context，按需签发 token
BCS 路由到 H×Worker Caller Container
```

如果 root task 一开始没有 caller-aware lineage，则不能在 Worker 阶段临时凭空升级为 Human caller。

### 8.4 多跳策略

自由 caller 群内协作已确认（普通非 caller 群行为不变）：

```text
max_delegation_depth = 2
max_child_runs_per_turn = 16
```

Workflow / ManagerWorker 可由编排定义决定更深链路：

```text
max_delegation_depth = workflow-defined
child target 必须出现在 DAG / role binding / manager-worker 拓扑中
```

---

### 8.5 Workflow 编排 Parent 与数据前驱

Workflow 不将“上一个节点已完成”解释为“其身份仍可派生”。复用现有 workflow instance 的生命周期建立 **编排执行关联**，不创建虚假的 Bot 或容器：

- Human 触发 caller-aware Workflow 时，BCS 为实例建立 delegation_only 可信关联，绑定 group/session/turn、拓扑版本、原身份有效期和历史上界；仅在受保护内存中持有身份。实例的现有 run ID 可作为 `root_run_id`/编排 parent ID，其业务状态允许持久化，但不能从该 ID 恢复身份。
- 节点 delivery 的 `parent_kind=orchestration`、`parent_run_id=workflow_run_id`、`parent_attempt=workflow_attempt`；`predecessor_node_run_ids` 独立表示数据依赖。节点依旧拥有自己的 Bot run/attempt/dispatch，不与 workflow ID 混为同一任务。
- BCS 验证受信编排服务/内部调用来源、当前活动 workflow attempt、节点/拓扑版本、依赖完成事实及派发幂等键；不套用 `parent.target_bot_id == source_bot_id`。普通 Bot 不能提交 orchestration 类型来绕过真实 Bot parent 校验。
- 节点 A 完成后，编排实例仍 running/waiting，BCS 从该实例的可信关联为节点 B 建立独立上下文；不使用 A 的已终态 proof。Fan-out/join 各节点共用本实例 turn 预算，join 按依赖结果去重，不把多个 Human lineage 合并。
- 编排 parent 不得让深度每个节点归零：节点深度由批准的有限拓扑层级计算，内嵌 Bot A2A 在该层级继续递增；循环/重试须有有限次数及总预算。node retry 是新 attempt，不是新 child；编排重试须先收敛旧节点尝试，遵守 §7.8。
- 编排实例在仍可能调度节点时不得 final；结束/取消/到期后不再派生。关联丢失或过期则等待或失败，不能从已完成节点/DB 重新签身份；Human 重新提交产生新 turn。已获准节点按自身生命周期处理，取消实例须请求取消其在途节点。

```text
Human turn T → 活动编排实例 W（delegation_only）
  W → 节点 A / run RA / attempt 1 → 完成，产出数据 DA
  W 仍活动且身份有效 → 节点 B / run RB / attempt 1
  B 的身份 parent=W，数据前驱=RA，历史上界=T 的初始上界
  全部节点收敛后 W 才终态；RA 终态不被重新打开
```



## 9. MCP 授权与凭据

Runtime/MCP Egress 在具体 MCP 调用时检查：

```text
1. 当前 run 是否 caller_task
2. caller_context/token 是否有效
3. 当前 Gateway/Runtime 隔离环境是否满足 token 中的 isolation 约束
4. 具体 MCP Tool/Resource/Action 是否需要 Human Grant
5. Grant 是否存在且未过期
```

如果未授权：

```text
Runtime 自行通过既有 Human 通知/授权通道发起异步授权
Run 进入 waiting_authorization
授权通过后重新校验原 run、可信执行身份的有效性、隔离约束及工具权限；使用 proof 时须验证 proof，均有效才继续执行
可信执行身份已过期（包括所用 proof 过期）则不执行；一期不原地重签恢复，由 Human 重新发起
拒绝/超时则 fail closed
```

BCS 不预测 MCP 工具，也不保存 MCP Grant/Credential。

**B5：**身份过期或无法恢复可信身份时，任务保持不执行的等待状态或失败；一期不提供通用原 run resume，不通过授权回调自动续签。Runtime 通过既有通知渠道告知 Human 重新发起，新提交必须重新通过可信入口认证。提示应说明已完成操作、已知结果及重复副作用风险；不得隐式重放旧任务已完成的操作。等待不是允许稍后借用新 token 自动唤醒旧任务。原 proof 仍有效时正常结束 MCP 授权等待，不属于“过期后原任务恢复”。

Credential Provider 输入建议：

```json
{
  "caller_ref": "opaque_caller_ref_or_x_one_id_ref",
  "actor_bot_id": "bot_b",
  "run_id": "run_alice_a_b",
  "audience": "mcp.calendar",
  "tool": "calendar.query",
  "resource": "calendar/alice",
  "ttl_ms": 300000
}
```

输出是面向 MCP audience 的短期 credential，只返回 Runtime/MCP Egress，不返回 BCS。

---

## 10. 核心领域模型与 API 扩展定义

本节把前面流程收敛为需要落地的核心领域模型和接口契约。设计原则是：

```text
DB/Repo 只保存非身份化执行元数据
BCS 运行时建立受保护执行上下文，按需签发 caller_execution_token
Gateway 负责 caller isolation binding 与内部物理路由
Runtime/MCP Egress 负责具体 MCP 授权与凭据兑换
Bot 业务逻辑只能看到任务语义，看不到 token、grant、credential
```

### 10.1 领域模型总览

| 领域模型 | 所属边界 | 是否落 DB | 作用 |
|---|---|---:|---|
| `TrustedHumanIngressContext` | Human-aware Ingress / BCS | 否 | 入口认证后的本次请求身份上下文，BCS 用它建立 root 受保护上下文并按需签发 token，但不落 DB。 |
| `BotCallerCapability` | Bot Registry / Gateway | 是 | 描述 Bot 是否支持 caller、是否需要 caller container。 |
| `ExecutionRun` | BCS Control Plane | 是 | 非身份化 run lineage 元数据，用于 parent 校验、scope 校验、depth 控制。 |
| `MessageTurn` | Message / Group | 复用消息表 | 群聊中“一条 Human 发言”的 caller 边界。建议用 `bcs_messages.message_id`。 |
| `CallerSubjectRef` | Runtime-only Identity | 否 | 对 Human caller 的短期 opaque 引用，不可反查真实身份。 |
| `CallerExecutionToken` | Runtime-only Identity | 否 | 插件凭据路径按需签发的短期执行令牌。 |
| `CallerIsolationBinding` | Gateway | Gateway 管理，BCS 最多持有短期 opaque ref | caller×bot×scope 的隔离绑定；可选返回 `binding_ref` 供 token 绑定；不暴露物理 route。 |
| `DispatchProof` | CallerExecutionToken 内部 | 否 | 证明本次 dispatch 被允许以 caller 身份进入目标 Bot；可含可选 isolation binding 约束。 |
| `DispatchEnvelope` | Delivery | 否 | `chat.send` / `chat.inject` 的统一扩展外壳。 |
| `CallerExecutionReceipt` | Runtime/Gateway -> BCS | 可落审计摘要 | 执行回执，只包含 hash/状态，不包含 token 原文。 |

---

### 10.1.1 `TrustedHumanIngressContext`

Human 身份进入 caller 执行链路的唯一可信来源是 Human-aware Ingress。结合现有 BCS 群聊实现，需要先区分几类已经存在但语义不同的上下文：

| 现有概念 | 当前承载 | 当前语义 | 是否可直接作为 caller 身份 | 说明 |
|---|---|---|---:|---|
| 请求调用者 | `WebSendCommand.caller` / `GroupChatCommand.caller`，类型为 `CallerContext` | 本次 HTTP/WS/Channel 请求通过认证后的调用主体，可为 Human/Bot/Integration/Admin | 部分可用 | 只有 `CallerContext::Human` 才能作为 Human caller 的信任起点。 |
| 消息发送者 | `from_actor_id`、`sender_id`、`sender_type`，落到 `bcs_messages` | 群消息是谁发出的，用于历史、事件、可见性、路由 | 不可单独使用 | 当前部分路径用 `human_` 前缀推断 `SenderType::Human`，这只能用于消息分类，不能证明 caller 身份。 |
| Channel 发送人信息 | `source_im_message_id`、`channel_sender_identity` | 外部 IM/WebUI 渠道来源与展示归因，按 Bot binding opt-in 投影给 Bot | 默认不可用 | 除非由受信 Channel Ingress 明确认证并映射，否则只能作为展示/溯源信息，不能作为执行凭证。 |
| Session 调用者 | `bcs_group_sessions.caller_principal` | 创建/拉起 session 的粗粒度 principal | 不可用 | 这是 session 级审计/创建者信息，不是每条 Human 发言的执行身份，群聊多人场景会过期或错配。 |
| Human 历史视图 | `HumanMessageView { actor_id, scope, ... }` | 读历史时的可见性过滤上下文 | 不可用 | 只决定 Human 能看哪些消息，不决定用谁的身份执行。 |
| 可见性/受众 | `MessageVisibilityDomain` / `MessageAudience` | 消息视图/受众规则，精确实现语义待源码核实 | 不可用 | 沿用原语义，不新增 caller 容器权限解释；执行身份与同步目标独立。 |

因此，`TrustedHumanIngressContext` 不应替代上述模型，也不应作为新的持久身份对象；它是 **BCS admission 阶段临时构造的可信包络**，用于把“已认证请求调用者”和“本次消息发送者/消息 turn”绑定起来，再派生 runtime-only caller token。

#### 实际类型与准入契约

保留两种不同职责的类型，不将执行认证字段并入通用 `CallerContext`：

- `CallerContext`：请求访问主体，可序列化；不是执行凭据。
- `AuthenticatedHumanCallerIngress`：本节可信入口包络的 Rust 实现，只包含私有的 `HumanActor { actor_id, staff_no }`、`auth_source`、`authenticated_at_ms`、`expires_at_ms` 和 `env`。不包含 token，不支持 serde，Debug 隐去主体。构造函数要求 actor 与 staff_no 均非空、有效期单调为正并验证基础形状，但不替代认证层的真实性校验。
- HTTP/WS 适配层从认证结果构造 ingress；消息应用入口必须再验证 `CallerContext::Human` 与 ingress 的 actor_id、staff_no 完全相同，`from_actor_id` 与该 Human 相同，环境一致且认证未过期。任一不符拒绝，不能从显示发送者、群发言权或 Bot owner 补造身份。
- 现有兼容语义允许 Human 用 owned Bot 作为 effective sender（HTTP `from` / WS `bot_id` 指向自己拥有的 Bot）。这不是 H2A：认证 principal 是 Human，但消息 sender 是 Bot。该路径不得携带 Human ingress，也不得把 Human principal 单独当作 Caller 模式开关。目标全部未开启 Caller 时保持普通 Bot 路径；请求中任何 caller-enabled 目标存在时 fail-closed，拒绝用 owned Bot 代发言绕过 Caller 身份。
- Caller 模式由目标 Bot 配置决定，不能以“是否携带 ingress”为模式开关。Caller 目标缺 ingress、认证过期或配置查询失败时拒绝；普通非 Caller 目标缺 ingress 保持原有行为。有效 ingress 随消息进入准入后，还要在 reserve/persist 前复核有效期、canonical sender 与各目标 scope 环境。
- Ingress **支持 Clone**，用于 WS 连接级可信认证快照；Clone 不刷新有效期，也不赋予绕过准入的权利。
- `WebSendCommand::clone` / `GroupChatCommand::clone` **移除 caller_ingress**。执行身份转交必须移动原命令，或显式 `take`/move 字段；不能把命令克隆当成可保留 Caller 身份的重派发。现有 GroupChat → WebSend 使用字段 move。handoff 的一次性消费由每个 scope 的状态保证，而非由“类型不可 Clone”保证。

下列是概念分解示意，不是可序列化的 ingress wire schema；message/session scope 复用已有命令和准入对象，不重复塞进 ingress：



```json
{
  "request_caller": {
    "actor_type": "human",
    "actor_id": "human_alice",
    "staff_no": "S12345",
    "auth_source": "http_auth | ws_auth | channel_ingress",
    "authenticated": true,
    "auth_time_ms": 1780000000000
  },
  "effective_sender": {
    "sender_id": "human_alice",
    "sender_type": "human",
    "source": "resolved_group_sender"
  },
  "message_scope": {
    "group_id": "group_001",
    "session_id": "session_001",
    "message_turn_id": "msg_001",
    "client_msg_id": "client-msg-001",
    "source_im_message_id": "im-msg-001"
  },
  "channel_sender_identity": {
    "present": true,
    "trust_level": "display_only | trusted_channel",
    "channel_type": "dingtalk | webui | api"
  },
  "trust": {
    "issuer": "bcs-ingress",
    "expires_at_ms": 1780000300000
  }
}
```

字段约束：

```text
request_caller 必须来自认证层/受信 Channel Ingress，不能来自消息 body 自报。
effective_sender 必须经过 BCS 现有 sender 校验：Human 只能以自己身份发言；如允许“Human 代 owned Bot 发言”，该路径不能自动升级为 Human caller。
message_turn_id 建议使用持久化后的 bcs_messages.message_id；群聊 caller 边界是“一条 Human 发言”，不是 group/session 长期状态。
channel_sender_identity 默认 trust_level=display_only，只可用于 Bot 看到外部发送人展示；只有受信 Channel Ingress 完成认证映射后才可标记 trusted_channel。
TrustedHumanIngressContext 不落 DB，不进入消息 content，不下发给 Bot 业务逻辑。
不能包含 Human Cookie、Bearer Token、Refresh Token、IAM Token、MCP Grant、MCP Credential。
```

#### CallerIdentityPlugin 与入口身份建立边界

统一调用 `CallerIdentityPlugin.establish_subject`：默认 JWT 实现从已认证 principal 建立短期 subject/proof，不依赖 IAM；internal AgentPass 实现才需要下面的 IAM 兑换流程。两种适配位置均通过同一 Plugin API，不直接耦合 AgentPass SDK。

后续如果需要“用用户 IAM Token 兑换 x-one-id，再把 x-one-id 作为 caller 身份标识”，也不应该把 IAM Token 放进 `TrustedHumanIngressContext`。推荐把原始 IAM Token 控制在 **Human-aware Ingress / Delivery Adapter / Authn Middleware** 这一层，BCS 领域对象只接收兑换后的 opaque caller ref。

推荐两种落地方式，优先级从高到低：

| 方式 | 原始 IAM Token 到哪里 | TrustedHumanIngressContext 里放什么 | 优点 | 风险/代价 |
|---|---|---|---|---|
| Ingress 预兑换 | 只到 Ingress/Authn Middleware | `x_one_id_ref` 或 `caller_subject_ref` | BCS core 完全不接触原始 token，边界最清晰 | 需要 Ingress/Authn Middleware 对接 AgentPass |
| BCS Adapter 即时兑换 | 只到 BCS Delivery Adapter，不进入 core/domain，不落 DB | `credential_exchange_handle` 的结果，即 `x_one_id_ref` | 改造路径较短，复用当前 BCS 入口 | 必须严格约束 token 不进日志/DB/消息/下行 payload |

不推荐：

```text
把 iam_token 作为 TrustedHumanIngressContext 字段传到 BCS core、Gateway 或 Runtime。
把 iam_token 写入 bcs_messages / ExecutionRun / transport_context_json。
把 iam_token 通过 chat.send 下发给 Bot Runtime。
```

建议模型扩展为：

```json
{
  "request_caller": {
    "actor_type": "human",
    "actor_id": "human_alice",
    "staff_no": "S12345",
    "auth_source": "http_auth | ws_auth | channel_ingress",
    "authenticated": true
  },
  "caller_subject": {
    "kind": "x_one_id_ref",
    "ref": "xone_ref_opaque_123",
    "issued_by": "agentpass",
    "expires_at_ms": 1780000300000
  },
  "message_scope": {
    "group_id": "group_001",
    "session_id": "session_001",
    "message_turn_id": "msg_001"
  }
}
```

其中：

```text
caller_subject.ref 是插件建立的受限主体引用；默认 JWT 使用作用域化假名引用，internal AgentPass 使用 x-one-id 的受限引用，不向 core 暴露实现特有身份。
如果 x-one-id 本身是敏感身份标识，则 BCS/Gateway/Runtime 之间继续传 opaque ref，Runtime/MCP Egress 调用 Credential Provider 时再解析。
如果 x-one-id 被定义为可传递的短期 caller subject，则也必须设置 audience、ttl 和 scope，不能长期复用。
```

BCS admission 行为：

```text
1. 从入口认证得到 CallerContext::Human。
2. 解析并校验本次 from_actor_id / requested_sender_id。
3. 只有“认证 Human == 本次 Human 发送者”或“受信 Channel 映射证明二者等价”时，才可构造 TrustedHumanIngressContext。
4. 完成适用的 Lease/权限准入后，原子接收并持久化消息事实，保留既有 sender 与视图字段。
5. 使用 persisted.message_id 作为 message_turn_id，并为各目标创建独立 root run。
6. 在内存中建立可信 subject 和独立执行上下文，调用 Gateway ensure 创建/复用 Human×Bot 隔离环境。
7. 若使用执行 token，在 ensure 成功后签发并加入可选隔离约束；chat.send 携带受保护上下文及按需 token，Gateway 内部路由。
```

场景行为：

```text
H->A 单聊: 若目标 Bot 声明 caller identity，则用 TrustedHumanIngressContext 创建 root ExecutionRun 和独立受保护上下文，token 按需签发。
Caller 群聊: 每条 Human 发言独立构造 TrustedHumanIngressContext，message_turn_id=该条消息 id；谁发言谁是 caller。
普通群/open_task: 可以记录 sender/message，但不签发 caller token。
Bot 发言/A2A: 不能从 sender_id 推导 Human caller；只能在受控 parent run/attempt 及可信执行关联校验通过后由 BCS 建立 child 上下文，token 按需签发。
```

落库边界：

```text
bcs_messages 继续保存 sender_id/sender_type/message_type/content/visibility/audience/run_id 等消息事实。
ExecutionRun 只保存 run lineage、group/session/message_turn_id、target_bot_id、parent_run_id 等非身份化元数据。
不得把入口认证上下文整体、staff_no、principal ref、caller token、channel trusted mapping 写入 DB；既有业务 Actor ID 可按 §6.3.1 用作发言权 holder，不代表允许持久化执行身份映射。
如需要审计，只保存事件状态、token hash 或不可反查摘要。
```

---

### 10.2 `BotCallerCapability`：配置、技术能力与生效状态

统一使用 `caller_identity.enabled` 表达 Bot 开关，不再用 supported/required 表达两套业务行为。下面是 BCS 只读合并视图示例，不是一个可整体写入的注册 DTO：

```json
{
  "bot_id": "bot_a",
  "caller_identity": {
    "enabled": true,
    "minimum_isolation": "caller_container"
  },
  "provider_capabilities": {
    "supports_caller": true,
    "supports_isolation_binding": true,
    "supports_observe_inject": true,
    "container_routing": "ensure",
    "token_visibility": "runtime_only"
  },
  "effective_status": "ready"
}
```

- Bot 配置：TeamClaw 由 Backend 同步；其他接入方扩展其现有 Bot 注册/更新接口，实际路径待源码核查。
- Provider 能力：受认证 Provider 控制面单独上报（包括其内部 Gateway 职责所需能力），不能覆盖 Bot 开关。`supports_isolation_binding` 指可选 `binding_ref`，不是必须向 BCS 暴露物理容器。
- `container_routing` 缺省为 `ensure`：先调用 ensure，再派发；显式 `caller_subject` 表示 Provider 无需 ensure RPC，承诺按可信 subject 懒创建/复用 Human×Bot 容器并隔离 session。该模式仍要求 `supports_caller=true`，群同步还要求 `supports_observe_inject=true`。能力读取失败、未声明 Caller 能力、ensure 的失败/404/超时均不得自动启用 subject 模式或回退 Owner。
- 两种模式的 Caller `chat.send` 和普通 observe-only `chat.inject` 均携带 `extensions.caller_context.subject.ref`。它来自可信派发/观察索引，不从消息正文或 sender 推导；不代表 MCP 授权。send 沿用 proof 的 purpose 区分 Caller 执行与 Owner 仅派生（execution.caller_task 是任务描述，不是授权开关）；inject 使用 execution.mode=observe_only，caller_context 仅含 subject，不能触发凭据兑换、模型或工具执行。
- BCS 计算 `disabled/ready/blocked`。开启但无法满足隔离、验证或路由要求时 blocked，不回退 Owner。群模式判断依据配置开启，而非暂时 ready。
- max_delegation_depth 是受控派生策略，不是 caller 开关；具体首期限制见 §14.2 B10。

```text
H->A: Human 入站且 A.enabled=true -> caller_task；ready 才可 dispatch，否则拒绝
群聊: has_human_member && exists bot enabled=true -> caller-enabled group
Manager/Workflow: 任一参与 Bot enabled=true -> 初始化 caller-aware lineage
```

兼容说明：早期草案的 mode=none 映射为 enabled=false，supported/required 均映射为 enabled=true。这只是文档字段归并，不声称线上已有旧协议；若源码存在旧字段，须在适配器冻结版本兼容规则，不能同时接受互相冲突的可写开关。

---

### 10.3 `ExecutionRun`：既有执行对象的逻辑视图

`ExecutionRun` 只表示 Caller 所需的可信执行事实，不新增 `bcs_execution_runs`，也不新增 A2A 专用 parent/child 生命周期。Chat run、Managed delivery、主子 task 和 Workflow 各自仍是其状态的唯一权威源。

| 逻辑事实 | 来源与约束 |
|---|---|
| run / target / session | 既有目标任务及其实际派发对象，不从模型参数确认归属 |
| attempt / dispatch / cancel / terminal | 原执行拥有方的当前尝试、派发和取消状态；Caller 不镜像维护 |
| parent / root / turn | 复用已有因果关联；缺失时只补必要非凭据关联，turn 复用 canonical message ID |
| invocation / toolCallId | 复用实际子调用标识做幂等；不能仅以 parent×target 合并同一 Bot 的多次合法调用 |
| parent_kind | 区分 Bot 执行与活动 Workflow 编排实例；数据前驱不自动成为身份 parent |
| caller_lineage / purpose / depth | 最小身份派生约束；标记只表示资格，不能从标记恢复 Human 身份 |
| history cutoff / snapshot | 按 §6.8 固定历史边界，复用消息与历史投影，不复制任务状态 |

Caller 在受保护运行期关联中保留已验证的 Human 来源及有效期。subject、IAM token、执行 token、observe target、binding 和可兑换身份的引用不落 DB。持久任务记录存续不代表运行期身份存续；过期、重启丢失可信身份后拒绝继续，不从任务记录自动续签。

#### 10.3.1 接入与一致性边界

1. 先把每个场景映射到真实 run/task/node、attempt、dispatch 及受认证回调；不能创建空壳 Caller run 弥补映射缺失。
2. 任务创建、幂等、预算、取消排序、结果归属与恢复继续由现有协作链完成。Caller 身份校验接入已有派发边界，不另设 child 注册事务或 parent continuation 队列。 来源关联复用 `bcs_message_deliveries.semantic_projection_json.caller_delegation` 中的真实 `parent_run_id`、`invocation_id` 和用途字段；不再读写独立 child/turn ledger 仓储。预算遵循 §7.6 的内存预占语义，与任务准入不是一个持久事务。Worker 完成走既有 delivery/task 终态 CAS，不因 Caller 身份过期或内存丢失而阻止已执行结果归档。历史迁移保留，本次不删除已部署表。
3. 现有模型确实缺少关联时，在原拥有方以字段或最小关系记录补齐；只存业务 ID，不复制状态、终态、结果交付状态或凭据。
4. 结果通过既有结果通道返回并去重。旧 attempt、终态后的事件和取消按既有执行契约处理；不能从 child 结果恢复 Caller 身份。
5. 最终出口按真实目标和 attempt 建立独立上下文；目标签发不改变原执行系统的重试和取消语义。

---

### 10.4 `MessageTurn`

`MessageTurn` 是群聊 caller 身份边界，不建议新增独立表，优先复用 `bcs_messages.message_id`。

```json
{
  "message_turn_id": "msg_001",
  "group_id": "group_001",
  "session_id": "session_001",
  "source_message_id": "msg_001",
  "speaker_actor_kind": "human",
  "created_at_ms": 1780000000000
}
```

规则：

```text
一条 Human 发言 = 一个 MessageTurn
一个 MessageTurn = 一个 Human 协作关联，可包含多个目标 Bot 的 root execution lineage
换 Human 发言 = 新 MessageTurn + 各目标独立 Root Run/上下文，token 按需签发
```

BCS 可从入口登录态知道真实 sender，但 DB 中的 `speaker_actor_id/sender_id` 只能作为消息审计和展示，不作为 caller credential。

---

### 10.5 `CallerSubjectRef` 与 `CallerExecutionToken`

`CallerSubjectRef` 是运行时短期 opaque 引用：

```json
{
  "kind": "human",
  "ref": "opaque_caller_ref",
  "ref_type": "bcs_ephemeral | agentpass_lineage_ref | x_one_id_ref",
  "expires_at_ms": 1780000300000
}
```

`CallerExecutionToken` 是插件凭据路径按需签发的 runtime-only token。对 Bot 业务逻辑必须 opaque。

```json
{
  "version": "1.0",
      "format": "jwt",
  "token": "<runtime-only-token>",
  "jti_hint": "optional-redacted-id",
  "expires_at_ms": 1780000300000
}
```

Token 内部声明至少绑定：

```text
iss = bcs 或可信 AgentPass
aud = bot_runtime:{target_bot_id}
run_id
root_run_id
parent_run_id
target_bot_id
group_id/session_id
message_turn_id
delegation_depth
max_delegation_depth
iat/exp/jti
```

约束：

```text
不进 DB
不进日志
不进 original_request
不进模型上下文
不由 Bot A 转发给 Bot B
只在 Runtime/Gateway runtime-only metadata 中携带
```

---

### 10.6 `CallerIsolationBinding` 与 Gateway 内部路由

V1 由 Gateway 管理长期 Human×Bot 容器，Runtime 隔离 session 和执行上下文。`caller_container` 是最低隔离要求，`container_bound` 是对应的实际保证；`gateway_managed`/`provider_downlink` 分别表示管理责任/接入方式，不作为 isolation_level。可选 binding_ref 仅表示是否暴露技术引用。

`CallerIsolationBinding` 是 Gateway 管理的逻辑绑定。BCS 可以请求 ensure，但不要求 Gateway 把物理路由返回给 BCS：

```json
{
  "binding_ref": "optional_bind_ref_123",
  "bot_id": "bot_b",
  "scope": {
    "session_id": "session_001",
    "group_id": "group_001"
  },
  "state": "creating | ready | failed | expired",
  "isolation_level": "container_bound",
  "generation": 3,
  "created_at_ms": 1780000000000,
  "expires_at_ms": 1780003600000
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---:|---|
| `binding_ref` | 否 | Gateway 支持强绑定时返回的 opaque ref；BCS 可用于签发 token 的内部约束。不可进入 `caller_context`。 |
| `isolation_level` | 是 | Gateway 承诺的隔离级别。V1 为 `container_bound`，表示不同 caller 使用独立容器；不返回 binding_ref 不改变此保证。其他隔离实现预留后续版本，不把管理方式作为隔离级别。 |
| `generation` | 否 | 容器/绑定代数。返回时可进入 token 内部约束；不返回时由 Gateway 自行校验。 |

不再定义跨边界的 `ContainerRoute`：

```text
BCS -> Gateway 的 dispatch 不携带 container_route。
Gateway 收到 chat.send 后，基于 bot_id、caller_context.subject.ref、scope、run_id/token 在内部路由到正确容器。
BCS 不保存也不理解 container_id、连接 ID、Pod 地址或 route_ref。
```

V1 也不要求单独 `route_proof` 字段；采用 token 路径时，`caller_execution_token` 是 dispatch proof。Gateway 信任 BCS 执行上下文并维持内部绑定；binding_ref 存在时按受信隔离约束路由，不要求 Gateway 额外验签 token。

---

### 10.7 API 1：Bot caller 配置同步与 Provider 能力注册

**新增需求：Bot 开启/关闭 caller 的配置必须从权威配置源同步到 BCS，不依赖等到下一次 Runtime 建连才获知。** 以下为拟议 Service API 语义，TeamClaw 的权威配置同步方已确认为 Backend；其他接入方扩展现有 Bot 注册/更新接口，真实路径及版本兼容须在完整源码中核查，不声称已有 `bot.info.sync` 接口。

#### 10.7.1 两个来源，不互相覆盖

| 数据 | 权威来源 | BCS 用途 |
|---|---|---|
| Bot 是否开启 caller、最低隔离要求 | TeamClaw：Backend；其他接入方：其被授权的 Bot 管理服务 | 决定 caller 业务模式 |
| Provider 支持的协议、隔离保证、ensure/inject 能力、token profile（含内部 Gateway 职责） | 受认证 Provider 控制面 | 判断所需模式能否安全执行 |
| 生效状态 | BCS 计算，不接受外部直接写入 | `disabled / ready / blocked` |

统一的最小 Bot 配置只用 `caller_identity.enabled` 表达开启；不同时引入 `mode=none|supported|required` 作为另一可写开关。早期草案中的 supported/required 都映射为 enabled=true、none 为 false，仅作兼容映射；不要让 supported 在身份失败时静默回退 Owner。最低隔离要求默认 `caller_container`，不支持线程隔离的 Runtime 不得降低要求。

Provider 的 `supports_caller` 是技术能力，不是 Bot 开关；`provider_downlink` 只是接入方式，也不等于支持 caller。群 caller 判定依据有 Human 且成员/受控拓扑中有 Bot 配置 enabled=true，不因某 Provider 暂时不 ready 而将它降级成普通群。

#### 10.7.2 优先扩展现有 Bot 信息同步

首次注册时，初始 Caller 快照、canonical Provider 归属与 Gateway binding 必须在同一
安全发布边界内建立；不能先发布未配置 Bot、再补 Caller 开关。重复注册冲突，不覆盖旧配置，
不在失败后重试普通注册。配置读取与同步必须认证 Provider 管理范围、环境及 canonical Bot 归属；
不能回退旧 binding 或自动认领。已拥有但未配置可以返回空快照，缺失/越域/删除、坏数据及
存储失败不能解释为 Caller disabled。Provider 未就绪允许保存配置意图，但不得启用执行。

优先在现有注册/更新/全量对账的 Bot 信息 DTO 增加 caller 配置。若现有链路无法覆盖配置变化，再新增受认证的内部同步适配入口，内部调用同一 Bot 配置 Service API，不建立第二套能力数据库。拟议请求示例：

```json
{
  "schema_version": "1.0",
  "event_id": "bot-config-event-42",
  "env": "prod",
  "bot_id": "bot_a",
  "source_revision": 42,
  "caller_identity": {
    "enabled": true,
    "minimum_isolation": "caller_container"
  }
}
```

- `bot_id/env` 必须与受认证同步方的管理范围匹配；不能凭消息 body 自报 owner/provider 获得修改权。运行中的 Bot 消息或模型不能修改此开关。
- `source_revision` 是权威源按 Bot 单调递增的 caller 配置修订号，不是任意事件时间戳；`event_id` 用于传输去重，不代替版本比较。
- payload 是 caller 子对象的完整快照。缺少 caller 子对象的旧增量更新表示“不修改”，不能清空已经启用的设置。仅对从未配置过的旧 Bot 迁移默认 false；明确关闭必须发送 enabled=false 和新版本。
- 不包含 Human、IAM、subject ref、token、容器 mapping。Bot 配置是非 caller 身份控制面数据，允许持久化。

响应示例：

```json
{
  "bot_id": "bot_a",
  "env": "prod",
  "applied_revision": 42,
  "sync_status": "applied",
  "effective_status": "blocked",
  "reason_code": "PROVIDER_CALLER_UNSUPPORTED"
}
```

`sync_status` 为 applied/unchanged/stale；同版本同内容幂等返回 unchanged，同版本不同内容返回 `REVISION_CONFLICT`，旧版本不覆盖且返回当前 applied_revision。写 DB 失败必须报错，不得返回 applied。Bot 配置接受成功但 Provider 尚未准备好时返回 blocked，不能假装配置已经 ready，也不能删除该配置。

**配置管理入口：** `GET/PATCH /providers/{provider_id}/bots/{bot_uuid}/caller`
通过受认证的精确绑定查询解析目标；检查 Provider admin、路径范围与 active，缺失/禁用
隐藏为 404，错误不回退全量列表。配置读写继续校验 environment、Provider 及 canonical Bot
归属。绑定解析不是 Caller readiness，也不保证撤销与配置 CAS 原子。

**注册失败边界：** `register_bot_with_caller` 只接受现有 Provider-scoped 注册授权，
owner、Provider、allowed_modes 从受信凭据提取。初始快照与 Bot/binding 同事务提交。
若提交后的 Human/owner-edge 初始化失败，返回 `registration_reconciliation_required`，
表示已提交但初始化未完成；不返回 Bot 凭据或存储错误，不自动重试、删除 Bot 或回放凭据。
调用方通过已有受认证管理查询对账。Caller 开关不能代替注册完成状态，关闭 Caller
也不能让未完成注册的 Bot 绕过安全发布边界。

#### 10.7.3 Provider 注册保持独立

Provider 能力读取须验证当前 Provider 存在、未禁用及管理权限；未声明能力与损坏配置、
目标缺失、存储失败必须区分。能力声明保存在 Provider 控制面，不在 Bot 配置复制第二套真值。
读取成功不证明派发与撤销原子，也不替代完整协议和隔离能力协商。

沿用现有 Provider 控制面能力更新，声明版本、ensure、observe-only inject、实际隔离保证、是否返回 binding 及支持的 token profile。BCS 只接受被授权的 Provider 更新自身能力，不能用 Provider 心跳/重连覆盖 Bot 配置 revision。所有接入对外使用同一能力模型；一期仅验收 provider_downlink。

BCS 计算：

```text
bot enabled=false -> disabled（普通执行策略）
bot enabled=true && Provider 能力满足 && 隔离绑定满足要求 && 受信服务链路及身份执行能力就绪 -> ready
bot enabled=true && 任一前提不满足 -> blocked（拒绝新 caller dispatch，不回退 Owner）
```

Caller 协作协议还须验收 delegation-only lineage、observe_target/history_ready、dispatch 去重及 status/cancel fencing；能力版本缺失则不启用依赖它的路径，不能静默忽略字段。这里的 binding 是 Bot→Provider 控制面绑定，不是 caller binding_ref。Manager 未开启但 Worker 开启时，Manager 可按 Owner 执行，root 仍需保留 delegation-only lineage；Worker 必须 ready 才能执行 caller 任务。

#### 10.7.4 持久化、传播与动态变更

执行能力未就绪、目标无效或配置读取失败时，Caller 内容派发必须关闭，不能调用 Owner
transport；取消控制仍须可用。单次配置检查不能替代检查后到网络发送间的取消/fencing 契约。

建议复用 `bcs_bots.bot_info` 的 caller 子对象保存配置及 source_revision；Provider 能力保存在既有 Provider 配置/能力位置，不在 Bot JSON 复制一套可写真值。需先核实 JSON 更新语义，保证并发更新不会覆盖其他 Bot 字段；必要的独立版本列/索引由实际 repository 决定，不预设新表。

**B8：**以 BCS 通过受信控制面成功接收、校验并应用的最新有效 Bot 配置为运行依据。“最新”按有效配置版本判断，不按网络到达顺序覆盖。

同步链路：既有 Bot 注册/更新入口 → BCS 鉴权与版本比较 → 原子写入 → 本地/多实例缓存按既有机制更新 → 返回确认。保留版本、幂等、旧客户端省略字段不清除及写失败反馈规则。

- 不设置 caller 配置新鲜度 TTL，不因长时间没有更新而过期；不增加派发前查 Backend、caller 专属同步失败恢复、定期对账或配置心跳机制。既有控制面的可靠投递/恢复机制可继续使用，但不在本方案扩建。
- 未接收更新时继续使用已应用配置。BCS 不知道上游未送达的变更，不保证从上游修改时刻起立即生效；配置传播缺失的风险由既有控制面承担，不以 proof 验证替代配置同步。
- B4 的相关撤销由 BCS 成功应用有效关闭/退群/解绑变更触发。BCS 到 Gateway/Runtime 的取消 SLA 不等于上游修改到执行停止的端到端 SLA。
- TeamClaw 权威源仍是 Backend；其他方复用既有注册更新。真实 API、版本字段、存储及缓存映射需源码核实，不代表已实现。

- BCS 在 root admission 和每次 child dispatch 使用当前配置；run 只记录采用的非身份配置版本。长任务不能靠开始时的快照无限绕过关闭策略。
- 开启配置只影响新准入；已开始的 Owner run 不自动升级成 Human caller。创建群、增删 Human/Bot、Bot 配置变化、拓扑变化触发群需求重算和必要预热；无可信 subject 时只登记预热意图，不伪造身份。
- 关闭/撤销、Provider 解绑或能力降低后，BCS 禁止新的 caller dispatch/child 签发；旧 run 不转换为 Owner。已到 Gateway 的执行需受信取消或在线有效性检查，不能宣称更新 DB 就能使离线 JWT 立即失效。
- **B4：**Bot caller 配置关闭、Human 退群或相关绑定解除后，阻止受影响范围的新 caller 准入及 child 派生，并请求 Gateway/Runtime 取消相关在途 run（含相关子任务）；不得误取消其他 session 或其他 caller 的无关任务。抢占/释放/空闲到期仍不取消旧任务。
- BCS 发出取消请求不等于执行已经停止。Gateway/Runtime 必须确认实际取消结果；请求接收确认不能冒充停止确认。超时或失联必须明确记录/反馈尚未确认停止，保持禁止新准入，不回退 Owner，不宣称旧 token 已即时失效。已完成或已发出的外部副作用不保证回滚。
- B4 的行为已确定，具体取消接口、确认事件及最大传播/取消确认时限由执行方在联调契约中承诺并验收；本文不虚构数值。未完成这项工程验收不能宣称满足撤销 SLA。配置生效边界按已确认 B8，不增加新鲜度期限；run/attempt 事件映射关联 B6。

验收包含：首次全量导入、启用/关闭、重连、旧版本/乱序/重复事件、同版本冲突、越权 Bot/跨环境写入、DB 写失败、缓存多副本失效、Provider 不支持时 blocked、Manager Owner+Worker Caller、群需求重算、在途撤销、旧客户端省略字段不覆盖新配置。

---

### 10.8 API 2：`caller.container.ensure`

方向：`BCS -> 目标 Bot 绑定的 Provider（内部 Gateway 职责）`。

作用：确保指定 Bot 在指定 caller/scope 下存在可路由的 Caller Container 或逻辑 binding。

请求：

```json
{
  "method": "caller.container.ensure",
  "id": "ensure-001",
  "params": {
    "bot_id": "bot_b",
    "caller_subject": {
      "kind": "human",
      "ref": "opaque_caller_ref"
    },
    "scope": {
      "session_id": "session_001",
      "group_id": "group_001"
    },
    "run": {
      "run_id": "run_alice_a_b",
      "root_run_id": "run_alice_a",
      "parent_run_id": "run_alice_a",
      "message_turn_id": "msg_001"
    },
    "policy": {
      "minimum_isolation": "caller_container",
      "create_mode": "lazy | warmup | must_exist",
      "ttl_ms": 3600000
    },
    "idempotency_key": "ensure:bot_b:session_001:run_alice_a_b"
  }
}
```

响应：

```json
{
  "ok": true,
  "isolation": {
    "level": "container_bound",
    "binding_ref": "optional_bind_ref_123",
    "generation": 3,
    "reused": true,
    "expires_at_ms": 1780003600000
  },
  "observe_target": {
    "caller_ref": "opaque_observe_target_ref",
    "bot_id": "bot_b",
    "session_id": "session_001",
    "target_epoch": "opaque_observer_generation_3",
    "expires_at_ms": 1780003600000,
    "purpose": "observe_only"
  }
}
```

`observe_target` 是 caller 群同步所需的成功返回项，与可选 isolation.binding_ref 独立。纯 H2A 且不使用共享同步的 ensure 可省略；声明支持 caller 群同步却无法提供时返回 `OBSERVE_TARGET_UNAVAILABLE`，不伪造引用。引用只在已认证 BCS→Gateway 链路接受，绑定 env/provider/Bot/session 和 Gateway 内部目标代次；不能作为执行凭据、不能从 Human ID 推导、不进入模型/DB/日志。expiry 不超过成员/会话绑定允许的观察期限，不等于 Human 登录态有效期；MCP 身份过期不自动阻止无执行权限的历史同步。

重复 ensure 在同目标仍有效时可返回同引用；回收/解绑/成员撤销后旧引用及 target_epoch 失效，下次可信 ensure 返回新引用，BCS 原子替换内存项并重新补历史。不得跨 session 复用。无 binding_ref 仍必须支持逻辑代次校验；observe_target 不暴露物理路由。引用缺失/过期时 inject 不触发 ensure，不借别人的 token 续引用。observe_target 可由 Gateway 内部查验/更新，不要求新容器枚举接口。

错误：

```json
{
  "ok": false,
  "error": {
    "code": "CONTAINER_UNAVAILABLE | CALLER_CONTAINER_NOT_SUPPORTED | ISOLATION_NOT_SUPPORTED",
    "message": "...",
    "retryable": true
  }
}
```

约束：

```text
caller_subject.ref 是 opaque，Gateway 不能反查真实 Human，除非 ref 本身由 AgentPass/Gateway 信任边界托管。
BCS 不直接持有物理容器连接。
Gateway 可返回短期 opaque binding_ref/generation，也可以不返回 binding_ref；无论哪种情况都不返回容器内部路由、连接或凭据。
```

---

### 10.9 API 3：`chat.send` caller 扩展

**Provider HTTP 派发字段的唯一来源**

Caller 复用既有任务、attempt 和派发关联，不建立独立 parent/child 生命周期或表。
内部 `CallerDispatchAssociation` 只是 StartSend 事实的类型化载体，不要求 Provider 回传。

| 信息 | 唯一来源 |
|---|---|
| Provider / Provider Bot | envelope 的 `to_bot.provider_id/provider_bot_ref` |
| 会话 | envelope 的 `session_id` |
| 回调 run | envelope 的 `id`，即既有 `run_id` |
| Human 主体 | `extensions.caller_context.subject` |
| 执行 proof | `extensions.caller_execution_token`，runtime-only |
| 执行期限 / 历史边界 | `extensions.execution.not_after_ms/history` |
| attempt、dispatch、控制版本、业务摘要、parent/root/turn | BCS 既有执行记录及可信运行时关联；不重复放入 send wire |

BCS 在发送前核对目标 Bot/Provider 与既有派发关联，网络 ACK 由在途调用关联回原命令，
不从 Provider 回显取得 CAS 版本或摘要。身份 proof 内的目标、用途和期限约束不因此取消。
`isolation.binding_ref` 是可选隔离约束，不是另一份执行生命周期。

**兼容性：** 这是 Caller 接线契约的协调升级：新版 send 不再输出旧 execution 关联字段，
旧 Provider 若读取它们，须先迁移为 envelope/run 关联及 proof 消费；不能仅凭 ACK 向后兼容
宣称整个 wire 无破坏。普通 Owner 请求、数据库结构和 BCN envelope 版本不变。
旧 ACK 的 echo 字段可以存在，但不作为权威。`caller-terminal` HTTP 入口已随 b503e66b3
移除（从未上线、无接入方）；新的结果/终态一律走既有 `/bot/events`，仅内部 typed CAS
语义保留（见下文墓碑说明）。

**重试边界：** `run_id` 不天然区分 attempt。结果未知不能自动换 attempt 重发；
同一 run 只允许已证明未受理的安全重投。BCS 内部 attempt/CAS/取消控制不删除。
普通事件不能凭 `run_id` 证明事件属于哪个旧 attempt，也不等价于带序号的远端取消证据；
需要此类强证据的 status/cancel 控制契约仍保留，不能用普通 final 伪造其水位。




**派发提交顺序：** StartSend 产生真实派发关联，身份准备完成后用
`commit_caller_dispatch` CAS 保存规范化业务摘要和最终期限，不保存凭据，也不提前标记
submitted/accepted。仅接受未提交网络、未取消的 Dispatching 行；期限不能超出 run 上界。
发送前重算摘要并核对当前 attempt/version；版本只能衔接本次成功 CAS 的结果，不能重读
后覆盖并发取消。完全重复提交幂等，写失败回滚。网络前确认失败不自动重试；结果未知
保留 lane；网络后落库失败传播，不假装成功或重发。受保护信封不能进入普通正文日志。
具体 Provider HTTP wire 版本见 `apps/bcs/docs/caller-container-gateway.md`；下列为 Caller 扩展的 Provider wire 核心字段（公共协议字段沿用原 envelope）。

方向：`BCS -> Provider 下行入口（内部 Gateway 职责）-> Target Runtime`。

作用：投递一个可执行 run。caller_task 时携带 runtime-only caller 材料。

请求：

```json
{
  "method": "chat.send",
  "id": "run_alice_a_b",
  "to_bot": {"provider_id": "provider-1", "provider_bot_ref": "runtime-b"},
  "session_id": "session-1",
  "bcn_group_id": "group-1",
  "message": {"text": "请处理这部分任务"},
  "extensions": {
    "execution": {
      "not_after_ms": 1800000000000,
      "history": {
        "history_snapshot_id": "history-b-turn-1",
        "history_cutoff_seq": 41,
        "manifest_digest": "sha256:selected-message-manifest",
        "projection_version": 1
      }
    },
    "caller_context": {
      "subject": {"kind": "human", "ref": "opaque-caller-ref"},
      "expires_at_ms": 1800000000000
    },
    "caller_execution_token": {
      "version": "1.0", "format": "jwt", "type": "jwt",
      "token": "<runtime-only-proof>", "expires_at_ms": 1800000000000
    }
  }
}
```

字段规则：

| 字段 | `open_task` | `caller_task` |
|---|---|---|
| 执行用途 | 普通 Owner 请求无 Human 执行 proof | 由受信 proof 的 purpose 约束，不增加明文 mode 授权开关 |
| `caller_context` | 不含 Human 执行 context；已批准 delegation-only 关联在受保护信封单独承载 | 必须存在，包含 opaque subject ref 与有效期；执行关联只在 envelope/execution，不含登录态或物理路由 |
| `caller_execution_token` | 不得携带 Human execution token；按需使用的 delegation-only proof 不能冒充此字段 | 按所选凭据路径提供，runtime-only；未提供时仍须有可信执行上下文及实际身份执行能力，不得回退 Owner |
| `runtime_isolation` | 可为空 | 默认为空；如未来扩展，只能放非敏感隔离要求，不能放物理 route 或可反查身份的 binding |

持久化要求：

```text
chat.send 中的 caller_execution_token、caller_context、runtime_isolation 等 runtime-only 材料不得写入 bcs_chat_runs.original_request；binding_ref 默认只进入 token 内部或 Gateway 内部状态。
如需审计，只保存 redacted/hash 摘要。
```

---

#### 10.9.1 Dispatch 与历史字段（caller 执行及 delegation-only 协作必需）

BCS 内部保留真实 attempt、dispatch、control_revision、parent/root/turn 关联和规范摘要，
不重复要求 Provider 在 send/ACK/结果中携带。历史描述仍在 `execution.history`，
使用 `history_snapshot_id/history_cutoff_seq/manifest_digest/projection_version`。
Owner 的 delegation-only 与 Human 执行用途由 proof 约束，不能按正文或 subject 推断权限。

同步接收成功返回 `{"ok":true}`；可选 state 为 accepted/unknown（字符串或 state 对象）。
拒绝返回 `{"ok":false,"error":{"code":"HISTORY_NOT_READY","retryable":true}}`。
BCS 从原命令关联 ACK，不接纳 Provider 对本地控制版本或摘要的覆盖。

`HISTORY_NOT_READY` 必须是受理前拒绝。历史补齐后按同一 run、相同内容、同一期限
重新检查并最多受理一次，不创建新 attempt，不延长身份有效期。未知结果不能套用此例外。
异步回复复用 `/bot/events` 的 run 关联；普通事件不携带身份或伪造取消序号。

A→B 的身份来自 BCS 的可信运行时 lineage，不要求 A 回传旧 token；当前插件按 subject
签发目标 proof。若真实身份系统要求父 token 兑换，需要另行明确插件输入及短期内存
材料管理；当前实现未支持该兑换，不能通过正文、持久化 token 或使用群当前 caller 补齐。

### 10.10 API 4：`chat.inject` observe-only 扩展

方向：`BCS -> Provider 下行入口（内部 Gateway 职责）-> Observer Runtime/Container`。

作用：BCS 将目标 Bot 的 session 共享消息同步到指定 caller 环境；Gateway 仅做受信目标校验和内部路由，不授予响应责任，不携带可执行 caller token。

请求：

```json
{
  "method": "chat.inject",
  "id": "inject-001",
  "params": {
    "target_bot_id": "bot_a",
    "session_id": "session_001",
    "message_id": "msg_001",
    "session_seq": 42,
    "message": {
      "role": "user",
      "content": [{ "type": "text", "text": "Alice 已进入 Bot A 共享上下文的发言" }]
    },
    "delivery": {
      "mode": "inject",
      "response_required": false
    },
    "extensions": {
      "caller_context": {
        "subject": { "kind": "human", "ref": "trusted_subject_bob" }
      },
      "runtime_target": {
        "caller_ref": "opaque_target_bob_ref",
        "target_epoch": "opaque_observer_generation_3",
        "purpose": "observe_only"
      },
      "execution": {
        "mode": "observe_only",
        "source_run_id": "run_alice_a",
        "message_turn_id": "msg_001"
      },
      "observed_speaker": {
        "kind": "human",
        "ref": "opaque_sender_ref"
      }
    }
  }
}
```

Provider 下行落线沿用根级 envelope：`session_id`、`message: {"text": "..."}`、
`extensions.caller_context.subject`、可选的 `extensions.runtime_target`、`extensions.execution.mode=observe_only` 和
`extensions.message_dedup_key`；上图的逻辑参数不应误读为嵌套 HTTP `params`。
成功要求 HTTP 2xx 且 JSON `ok=true`。普通 Provider ACK 的 `ok=false` 表示失败，
仅显式顶层 `retryable=true` 允许重试；4xx、畸形/空 ACK 和超过 64 KiB 的 ACK 不重试，
5xx、传输失败或超时可在同步层有界重试同一消息 ID。HTTP 发送及 ACK 读取共用 10 秒 deadline，
读取过程中执行 64 KiB 上限；ACK 不包含或回显 Caller 凭据。

字段约束：

- `caller_context.subject.ref`：Caller 观察目标始终必填，是接收容器所属 Human，不是消息 sender，也不授予该 Human 的执行权限。
- `runtime_target.caller_ref/target_epoch`：ensure 模式由 Gateway 返回，成对传递；显式 subject 模式两者均省略（不填 null、不拿 subject 冒充 ref）。同时存在时，Gateway 必须确认它们与 subject、Bot、session 指向同一环境；不一致应拒绝，不能忽略旧引用重新路由。
- `target_bot_id/session_id`：限定接收环境，Gateway 不能跨 Bot/session 自动扩散。
- `message_id/session_seq`：用于去重与补发，BCS 负责同步策略。
- `observed_speaker`：仅展示消息来源，不能用于授权。
- `caller_context`：inject 仅携带 subject；不得包含 IAM token、可兑换凭据或执行身份有效期。`caller_execution_token` 在 inject 中禁止携带。
- 原有消息视图 metadata 如协议要求可原样保留，但不新增容器受众解释。
- 错误至少区分目标引用失效、环境未就绪、目标范围不匹配；前两者由 BCS 补发，范围不匹配拒绝，不得改投 Owner。

禁止：

```text
chat.inject 不得携带 caller_execution_token
chat.inject 不得让 Runtime 发起 caller MCP 执行
chat.inject 不得注入其他 Human 的私有 MCP 原始结果
```

---

#### 10.10.1 观察目标与快照确认

Caller Container 普通消息 inject 始终携带 `caller_context.subject`；ensure 模式额外使用返回的 `caller_ref/target_epoch`，Gateway 验证引用有效、主体、目标和代次匹配。subject 模式由 Gateway 按认证 Provider 域、Bot、subject 定位容器，并用 session 隔离历史；未就绪必须拒绝/返回可重试结果，不能改投默认容器。每条确认返回 message_id、目标代次及 applied/duplicate，不仅是“收到网络包”。

目标采用显式判别变体：Caller 目标统一以 caller_context.subject 标识，ensure 模式另有 runtime_target.caller_ref/target_epoch；`kind=bot_default` 仅用于已批准 Owner delegation_only 任务的既有 Owner 目标，由认证 BCS 指定 bot/session，Gateway 确认该 run 确为 Owner 模式后路由。Bot 默认目标的消息历史沿用既有注入路径；两类目标都要求相同快照就绪语义，绝不能以缺 caller_ref 自动回退 bot_default。

沿用 chat.inject 增加 `kind=history_complete` 控制变体（无业务 message，不触发模型）：Bot/session 使用现有 Provider envelope，Caller 目标必填 `extensions.caller_context.subject`（kind=human、ref=裸工号），不携带执行 token。`extensions.history` 为 history_snapshot_id、history_cutoff_seq、projection_version、选定 message_ids、manifest_digest；`extensions.execution.mode=history_complete`。去重键为 `history_complete:{snapshot_id}`，必须在认证 Provider / Bot / subject / session / 当前环境代次范围内使用，不能全局共享去重记录。

BCS 在 complete 前按冻结清单注入正文（普通 observe-only inject，原 message_id），空快照也执行 complete。不是重新查询“最新历史”，不以最后一批替代全清单。当前纯文本恢复子集限制最多 256 个 ID、128 KiB 正文；缺行、重复、越界、摘要不符、非空附件、未知正文格式均拒绝恢复，不静默丢弃。生产 manifest 沿用 bounded contexts 的既有限额。

当前 Provider HTTP 成功响应为 `{"ok":true,"history_ready":{"history_snapshot_id":"…","manifest_digest":"…"}}`。BCS 要求快照 ID 和摘要与请求完全一致；仅 `ok=true` 不是 ready。Gateway 必须先确认同一 Caller/session 当前隔离环境已应用清单，再返回匹配确认；内部环境代次改变必须使旧 ready 和去重缓存失效。此 HTTP 子集不回显物理容器 ID 或 target_epoch，不等于放弃 Gateway 的环境代次约束。后续 protected send 仍再次核对快照，不得绕过 ready 执行。

正文/complete 失败不能进入重发；明确 retryable 按退避继续，其他拒绝收敛失败。BCS 只重投原 dispatch/attempt/digest/proof，不新建 attempt 或补签身份。运行时仍检查原 run/request/attempt、任期和期限，重启后不从 DB 恢复凭据。生产 composition root 从 Provider inject factory 与既有 MessageRepo 装配恢复端口；自定义装配缺端口时保留既有 marker+deadline 失败关闭行为。本适配器仅处理 Caller 目标，不实现上文 Owner bot_default 的独立历史恢复。


### 10.11 API 5：Child Dispatch / A2A 请求

方向：`Runtime/Gateway -> Provider 受信返回路径 -> BCS`（Gateway 为 Provider 内部职责，不要求额外物理转发跳数）。

作用：Bot A 提交 child task 和 parent 提示；受信 Runtime/Gateway 绑定真实 parent run/attempt并提供受保护关联校验。**请求本身不携带 `caller_execution_token`；BCS 在 Identity Material Ledger 中取回 parent token 或 origin handle 完成下一跳签发。**

请求：

```json
{
  "method": "execution.child_dispatch.request",
  "id": "child-001",
  "params": {
    "source_bot_id": "bot_a",
    "target_bot_id": "bot_b",
    "parent_kind": "bot_run",
    "parent_run_id": "run_alice_a",
    "parent_attempt": 1,
    "source_dispatch_id": "dispatch_a_1",
    "task": {
      "content": [{ "type": "text", "text": "请协助分析这部分" }]
    },
    "scope_hint": {
      "group_id": "group_001",
      "session_id": "session_001",
      "message_turn_id": "msg_001"
    },
    "runtime_evidence": {
      "binding_ref": "optional_bind_alice_a",
      "generation": 2,
      "purpose": "caller_child_dispatch"
    },
    "idempotency_key": "child:run_alice_a:bot_b:001"
  }
}
```

BCS 校验：

```text
parent_run_id 存在且 active
parent_run.caller_lineage == true
parent execution_mode 可以是 caller_task 或具备已批准 delegation-only 上下文的 open_task
parent_run.target_bot_id == source_bot_id
scope_hint 与 parent_run 的 group/session/message_turn 一致
受信请求方与真实 parent run/attempt 归属匹配；BCS 从 Identity Material Ledger 取得原 caller 材料
如果下一跳是 Caller child，只能使用 parent execution token 显式 exchange；如果 parent 是 Owner/Manager，则使用 ancestor/root handle，不伪造 Caller token
depth 未超过策略
path 属于 caller-enabled group A2A、manager-worker 或 workflow
```

响应：

```json
{
  "ok": true,
  "child_run": {
    "run_id": "run_alice_a_b",
    "root_run_id": "run_alice_a",
    "parent_run_id": "run_alice_a",
    "execution_mode": "caller_task",
    "delegation_depth": 1,
    "status": "accepted"
  },
  "delivery": {
    "target_bot_id": "bot_b",
    "mode": "caller_task"
  }
}
```

如果不允许继承 caller：

```json
{
  "ok": false,
  "error": {
    "code": "CALLER_CONTEXT_NOT_AVAILABLE | CALLER_DELEGATION_NOT_ALLOWED | PARENT_RUN_INVALID | TOKEN_INVALID | DEPTH_EXCEEDED",
    "message": "caller cannot be inherited on this path"
  }
}
```

---

#### 10.11.1 Child 请求的完整关联

Bot 请求补齐 `parent_kind=bot_run`、`parent_attempt` 与当前 source dispatch_id；runtime_evidence 为受保护关联或按需 proof。相同 child 幂等键绑定 source run/attempt、目标和 task 摘要：同内容返回原 child，即使 parent 随后终态也仅返回已登记结果，不产生新派生；不同内容返回 IDEMPOTENCY_CONFLICT。鉴权和原请求归属检查仍必须执行。

Workflow 编排使用 `parent_kind=orchestration` 的内部受信入口，输入 workflow ID/attempt、拓扑版本、node ID/attempt、predecessor_node_run_ids；不接受 Bot 自报这类身份。BCS 在同一事务中检查 parent/current attempt/取消状态、child 幂等记录与 turn 预算，并保存 child 的 parent_attempt 和历史上界。`accepted` 只表示 child 已登记，实际派发可能仍在等待历史就绪或结果未知；调用方按 child/run 关联查询，不自行改换 ID 重试。


### 10.12 API 6：`/bot/events` caller execution receipt

方向：`Runtime/Gateway -> Provider 受信返回路径 -> BCS`（Gateway 为 Provider 内部职责，不要求额外物理转发跳数）。

作用：回传执行状态、最终结果、授权等待状态和 caller 执行摘要。不得回传 token 原文。

请求：

```json
{
  "run_id": "run_alice_a_b",
  "attempt": 1,
  "dispatch_id": "dispatch_ab_1",
  "control_revision": 1,
  "seq": 3,
  "event": "status | delta | final | failed | authorization_required",
  "payload": {
    "state": "running | waiting_authorization | final | failed",
    "message": {
      "content": [{ "type": "text", "text": "处理结果" }]
    },
    "error": null
  },
  "caller_execution_receipt": {
    "execution_mode": "caller_task",
    "isolation_level": "container_bound",
    "isolation_binding_hash": "sha256:optional",
    "token_jti_hash": "sha256:...",
    "mcp_authorization_state": "not_required | waiting | granted | rejected | expired"
  }
}
```

BCS 处理：

```text
先验证真实 provider/Bot/run/attempt/dispatch、事件顺序、取消与 control_revision
合法当前事件交由既有执行拥有方通过 CAS 更新真实 run/dispatch 状态；重复/旧 attempt 仅返回 duplicate/stale
仅合法事件推进 chat/state-machine 原有 run 状态
按 visibility/audience 投影结果
BCS 将已发布且进入目标 Bot 共享上下文的回复 inject 到其他有效目标 caller；Gateway 按 caller_context.subject 及可选 runtime_target 路由
不保存 token 原文，不保存 MCP credential
```

---

#### 10.12.1 状态查询、取消与事件关联契约

这组为 **BCS→Provider Plugin API（由其内部 Gateway 职责承接）** 的逻辑方法，扩展既有下行能力并单独版本化；不是宣称已有同名 HTTP endpoint。Provider→BCS events 是受认证 Service API，不回传身份原文。

| 方法 | 请求 | 返回及保证 |
|---|---|---|
| `execution.status` | env、target_bot_id、run_id、attempt、dispatch_id | accepted/queued/running/terminal/not_found/unknown，control_revision、最新 event_seq、cancel_state、已知结果引用；不包含 token/ref。not_found 不证明不可再执行 |
| `execution.cancel` | 同上、cancel_request_id、control_revision、reason、not_after_ms | 同请求幂等；cancel_state=requested/fenced/stopped/unconfirmed，实际 execution_state、最后 event_seq；先登记 fence，后请求 Runtime 停止，确认语义见 §7.8 |
| `/bot/events` 扩展 | provider/Bot、run/attempt/dispatch、event_id/seq、control_revision；状态/结果/取消确认 | BCS 鉴权及 CAS 后 applied/duplicate/stale；stale 可审计但不改变当前 attempt 或发布成当前回复 |

BCS 只接受真实控制面绑定对应的 Provider 事件。事件 seq 在 attempt 内单调；重复事件幂等，乱序有缺口时按状态查询补齐，终态不能被 running 覆盖。delta/final 先于派发 ACK 到达可作为受理证据，必须匹配已提交 dispatch；未知 run 的事件不能创建 caller lineage。

status/cancel 不需要 Human token，仅使用 BCS 服务认证与非凭据关联；查状态不授权新执行。Gateway 若无可靠去重、查询与取消 fencing 能力，不启用 caller 执行/协作协议版本。普通非 caller 旧接口保持不变，不能静默忽略新字段。部署必须明确最大迟到窗口、账本保留、时钟偏差及取消 SLA，未验收不宣称安全自动恢复或 exactly-once 外部副作用。


**控制通道与目标绑定**

status/cancel 只使用服务认证及原派发八字段：
`environment / target_bot_id / provider_id / provider_bot_ref / session_id / run_id / attempt / dispatch_id`。
每次调用重新解析并核对原 Bot/Provider/runtime ref；禁用、替换、解析失败或不支持的接入模式
不回退旧 endpoint/凭据，不改写原目标。解析器缓存不是实时撤销证明，查询到发送之间的
竞态必须纳入 Gateway 的隔离、账本和 fencing 契约。

Provider HTTP 使用 BCN 2.0；请求/响应必须真实回显完整关联、frame id、版本与观察结果。
缺失/未知字段、错类型、错版本、错关联及 HTTP 错误均拒绝，不能从请求补齐回显。
所有控制调用使用总 deadline、URL/DNS guard、响应大小限制、无重定向/隐式重试/正文日志；
配额等待、目标解析和连接初始化不能重置预算，发送前再检查期限。HTTPS 不降级或关闭证书校验。
详细 wire 以 `apps/bcs/docs/caller-container-gateway.md` 为准。

**状态查询版本**

- `execution.status` v1 仅返回 accepted/not_found/unknown；HTTP 404 不是可信 not_found。
- v2 显式选择，不自动协商或降级；除上述状态外允许
  `terminal { outcome, event_seq, control_revision }`，outcome 为 completed/failed/aborted。
  非终态不附加伪造的终态字段。查询请求 ID 与原 dispatch ID 分离。
- Accepted 只确认受理，Unknown 可收敛到 Running；CancelUnknown 不因 ACK 恢复执行。
  NotFound/Unknown 不写成终态、不释放 lane、不重发。查询可在执行期限后进行，不延长授权。
- 终态快照经与回调相同的 Managed CAS 处理，使用查询前版本；不从 ACK、超时或本地取消
  推断终态。控制状态恢复不等于事件缺口补齐、结果发布或创建 child。

**取消意图与回执**

1. `CancelRequested` 在同一 CAS 中提交状态、稳定 cancel_request_id、control_revision、
   reason（user_requested/scope_revoked）及原 not_after_ms；不能等待后续调度才建立意图。
   control_revision 固定为意图提交时的版本，恢复不得按新 state_version 重算；重复取消不覆盖。
2. `execution.cancel` v1 的 params.command 携带完整原关联与原 control。重试只能重放同一命令，
   transport 不签发 ID 或提高 revision。控制缺失/损坏不回退 legacy abort。
3. 回执含完整 command、cancel_state、execution_state、last_event_seq；
   requested/fenced/unconfirmed 均不释放 lane，stopped 仅接受 terminal/not_found 执行状态。
   not_found 单独不是停止证据，stopped 不承诺回滚副作用。
4. Managed 锁内重读原关联和当前版本，回执、生命周期及 bound context 同事务提交。
   完全重复无写幂等；变更回执要求 seq 严格递增。已确认 fenced 不得被更大 seq 的
   requested/unconfirmed 撤销；Gateway 账本丢失不能伪装为较新的无 fence 观察。
5. 超时/失败保持意图和占用，存储失败传播。网络 I/O 不持有事务或 Managed 锁。
   已提交取消的恢复优先消费原 control，不降级到状态查询或重建身份。

**队列 TTL 与执行期限**

尚未发送的任务允许队列过期。Caller StartSend 在原 CAS 中清除队列 expire_at_ms，保留
run_deadline_at_ms；Unknown/CancelUnknown 不重新设置队列 TTL。已可能发出的任务不能
通过 QueueExpired 变成“已停止”或释放 lane。执行超时只能触发取消，仍需停止/终态证据；
普通非 Caller 队列 TTL 保持原语义。

**终态仲裁**

终态回执必须绑定原 dispatch、真实 attempt、event_seq、control_revision 和当前 state_version。
control revision 不得超前于已提交控制；取消前生成的 final 可以携带 revision=0。
终态 seq 必须高于已保存取消观察水位；精确重复幂等，同序号不同内容、旧序号、终态后的
不同观察均拒绝。旧 attempt 不推进新执行，已可能发送的 Caller 不接受无序号旧终态绕过仲裁。

取消先提交时逻辑状态为 Cancelled，实际 completed/failed/aborted 独立记录；Completed 先提交
则不被迟到取消覆盖。终态观察、生命周期、bound context 同事务，失败整体回滚。
状态终结不自动发布正文；公开结果须另经原消息投影和上下文同步规则。

**Provider 结果入口：复用 `POST /bot/events`**

新接入以原 chat.send 的 id 作为 run_id 提交 final/error/aborted，沿用服务认证、
既有结果投影和 managed 终态处理；不回传 Caller Context 或 token，不再强制双回执。
普通事件不携带完整取消水位，不应宣称它满足下面旧控制接口的序号仲裁。

**墓碑：`POST /bot/events/caller-terminal`（已移除，b503e66b3，2026-10-20）**

> 下文保留原设计描述仅作历史参考。该 HTTP 入口从未推送上线、无任何 Provider 按此接入，
> 已随 b503e66b3 整体删除；新接入方不要按此实现，终态/结果一律走既有 `POST /bot/events`。
> 判据与 a2a_child 表族下线（7ea603b8c）相同。原设计承诺的内部 typed CAS 语义
> （`ManagedMessageDeliveryService::handle_caller_terminal`）保留在代码中，当前生产装配
> 恒传 `caller_terminal: None`，仅契约测试可达；重设计的终态入口落地前不对外承诺。

原设计描述（历史）：此独立 Service API 仅收控制状态，与 `/bot/events` 的一般事件/结果
契约分离。按 dispatch_id 定位原 Bot/session/run/attempt 后执行一次 Managed CAS；
凭据只在认证期间使用，不持久化或回显；错误处理不记录原始请求体。下表的 wire 契约
**已不存在**：

| 部分 | 字段/规则 |
| --- | --- |
| ~~请求头~~ | ~~`Authorization: Bearer <Provider credential>`、`X-BCN-Provider-Id`；ProviderAdmin 还需要 `X-BCN-Provider-Bot-Ref`~~ |
| ~~版本~~ | ~~`version=1`，其他版本拒绝~~ |
| ~~原派发关联~~ | ~~`environment`、`provider_bot_ref`、`target_bot_id`、`session_id`、`run_id`、`attempt`、`dispatch_id`~~ |
| ~~观察~~ | ~~`outcome` 为 completed / failed / aborted、`event_seq`、`control_revision`~~ |
| ~~成功~~ | ~~`200 {ok:true, delivery_id, status, state_version}`~~ |
| ~~失败~~ | ~~400/401/403/409/503，固定错误码，无输入回显~~ |

**恢复调度与任期生命周期**

恢复复用同一 elected scheduler、环境及 Managed 服务，不另建 Caller worker。以有界游标
扫描 Unknown/CancelUnknown；配置取消消费者时也处理 Cancelling。每页最多消费 32 条，
逐条核验 Caller 标记及原关联，最多一个远端请求在途；普通/畸形候选不能触发 Gateway。
存储失败中止本页，已提交结果不回滚；NotFound/Unknown 的观察成功不代表任务完成。

正常页间隔 5 秒，慢页不补发积压 tick；短页仅使下一轮游标回到开头。失败按
10/20/40/60 秒退避，成功非空页恢复正常节奏，空页不清除已有退避。存储失败保留起始游标，
下轮只查询/取消，不重发执行。契约冲突结束恢复并通知 owner，不无限重试。

未配置恢复时不启动扫描；启用发送必须具备匹配协议的恢复和取消能力。降级、选举异常或
shutdown 停止当前任期本地等待；再当选使用新游标和退避，不恢复旧身份。恢复意外退出
须停止 owner 并关闭相关准入。本地取消无法撤回已开始的 DB 提交或远端操作，不构成跨副本 fence。

### 10.13 Caller Identity Plugin API（默认 JWT / internal AgentPass）

本节定义 caller 主体建立、执行 token 签发和验证的替换边界。BCS core 不依赖 AgentPass、
不解释 x-one-id，也不直接实现 JWT 编解码。默认 JWT 与 internal AgentPass 共用后续 Plugin API。

**可信认证提取**

Human-aware Ingress 在同一次认证中返回 sender 展示信息与独立的
`AuthenticatedHumanCallerIngress`，两者必须来自同一已验证 principal。
认证材料及其有效期来自真实认证结果，不从 sender、owner、DB holder 或当前时间推算。
OAuth Cookie 适配路径验证登录签名、exp 和当前会话指纹，异步返回后复核期限；
不转发原 Cookie/Bearer。IAM/Bearer 等其他来源必须有明确的可信适配器，不能由 Cookie 路径兜底。
混合 Human/机器凭据、Bot/Owner principal、缺失来源或截止时间、过期及时间转换溢出均拒绝。

`Unsupported` 表示适配器不提供严格提取，`Unavailable` 表示无可接受的 Caller 证据；
`AuthenticationFailed` 与 `Expired` 分别表示认证失败与期限失效。所有错误均保持 Caller
拒绝，不重试旧认证路径或回退 Owner。普通非 Caller 聊天独立选择原路径，不先严格认证失败
再二次认证。聚合认证对象不可公开序列化或记录；只有 sender 展示元数据进入普通消息。
Bot 认证携带的 staff ID 不等于可信 Human 执行授权。

**身份准备与绑定校验**

```text
可信认证 + 业务准入 + 已提交真实 run/attempt
  → establish_subject
  → CallerContainerPreparation → Gateway.ensure
  → 按需 issue_execution → verify_execution
  ← runtime-only PreparedHumanCallerExecution
  → owning dispatcher 最终检查及受保护派发
```

结构校验不代替登录、Session/目标授权或 MCP 授权。有效期不得超过 ingress、subject、
请求、容器准备结果及 proof 的最早期限；异步返回后复核。验证结果精确匹配
subject/Bot/audience/run/attempt/session/purpose，不能仅缩短返回元数据来接受超长 proof。

ensure 返回的可选 `binding_ref` 由受信 consumer 提供给签发/验证双方；`None` 仅接受
无绑定 proof，不是通配符；空绑定拒绝，插件不支持所需绑定时失败。binding 不进入
CallerContext、业务消息或 DB；JWS 可读，因此其中 binding 必须是非凭据引用，不能夹带 IAM。
binding 相等不替代 generation/撤销 fencing，也不要求 Gateway 逐任务验证 proof。

材料只在受保护内存对象中传递，错误脱敏，不自动续签、重试或回退 Owner。
ensure 失败不证明远端没有创建容器；prepared 不等于派发许可，实际发送仍按 §7.5、§7.8 排序。

#### 10.13.0 BCS 信任边界与接入要求（B9）

BCS 是执行 caller 的确认和决策入口。Gateway/Runtime 信任受认证 BCS 下行请求中的 callerID，不新增逐任务 BCS proof 验签体系；不信任模型、公共请求或消息正文自报的 callerID。

| 角色 | 确定职责 |
|---|---|
| BCS | 可信入口身份、群/任务/parent 准入，组装独立执行上下文；按实际插件/出口需要兑换或签发身份材料 |
| Gateway | 复用既有服务认证确认 BCS 来源，按 caller＋Bot 路由/ensure，维持 session/run/attempt 关联；不重复业务身份决策或额外 proof 验签 |
| Runtime | 保管模型不可见的执行上下文，隔离 caller/session/run/attempt；A2A 回调附加原任务受保护关联，不允许模型覆盖 |
| Runtime/Egress | 检查执行材料有效性和工具权限；排队/授权等待后必要的再检查；沿用异步 MCP 授权，不回退 Owner |

与 B6 一致：每次新的逻辑派发/attempt 必须建立独立受保护执行上下文；token/proof 仅在身份插件、可信 A2A 关联或工具出口需要时签发，非所有下行的必选字段。execution/delegation_only 是上下文用途，不能仅依赖是否存在 JWT 判断。Owner Manager 不因此获得 Human MCP 权限。

A2A 中 BCS 必须验证受信 Runtime 请求与 parent/run/attempt 的真实归属，并从受保护关联取得原 caller。可复用现有受信回调与执行关联；若采用 proof 路径，由 BCS 调插件校验。不能只凭可填写的 parent ID 或 DB holder 重建身份，关联丢失则拒绝继承。

默认 JWT / internal AgentPass 插件仍保留，用于实际需要的主体建立、身份兑换、证明签发/验证。后续 token/claims 示例描述插件凭据路径，不再要求 Gateway 部署对应 verifier；业务字段 callerID/subject ref 不是 MCP 凭据。AgentPass 故障不回退 JWT/Owner。具体字段、密钥/接口和受保护信封承载由研发联调验收。

接入门槛：受信服务认证可验证、执行绑定不可被模型覆盖、材料不进入模型/日志/DB/快照、隔离与工具授权有效。无法满足则阻止 caller 接入，不影响原有非 caller 能力。

#### 10.13.1 分层与实现选择

```text
Human 认证适配器 -> CallerIdentityPlugin.establish_subject
BCS execution service -> CallerIdentityPlugin.issue_execution / verify_execution
                            ├─ 默认 JwtCallerIdentityPlugin
                            └─ internal AgentPassCallerIdentityPlugin
Gateway -> 信任受认证 BCS 上下文并路由；Runtime/Egress -> 隔离与工具执行身份检查（不新增逐任务 BCS proof 验证体系）
MCP Egress -> 独立的 MCP Credential Provider（不由 BCS 托管工具凭据）
```

- Plugin API 属于 BCS 调用外部身份能力的出站契约；Human ingress/child dispatch 仍为 Service API。
- 默认社区实现使用 JWT，可离线运行，不依赖 IAM 或 AgentPass。internal 实现对接 AgentPass，IAM、x-one-id、内部错误码只存在于该实现与可信认证适配器内。
- composition root 按配置注入实现；core、HTTP delivery 和群编排不写 `if agentpass/jwt` 分支。不在插件故障时自动从 AgentPass 切换到 JWT。
- `CallerIdentityPlugin` 替代此前零散的 `CallerTokenIssuer` 和直接 AgentPass 调用，不并行保留两套签发入口。
- Gateway 路由、群聊共享策略、parent 状态与拓扑授权仍由既有职责承担；身份插件不决定消息投递、MCP grant 或是否允许 child。

#### 10.13.2 核心接口（Rust 语义草案）

```rust
trait CallerIdentityPlugin {
    async fn establish_subject(
        &self, input: EstablishCallerSubject,
    ) -> Result<EstablishedCallerSubject, CallerIdentityError>;

    async fn issue_execution(
        &self, input: IssueCallerExecution,
    ) -> Result<CallerExecutionToken, CallerIdentityError>;

    async fn verify_execution(
        &self, input: VerifyCallerExecution,
    ) -> Result<VerifiedCallerExecution, CallerIdentityError>;
}
```

此处只定义语义；async trait/object safety 和 secret 类型沿用完整源码的契约习惯，不据此创建独立框架。

| 方法 | 输入 | 输出与保证 |
|---|---|---|
| `establish_subject` | 认证适配器产生的 `VerifiedHumanPrincipal`、env/trust domain、认证有效期及受限 purpose；必要的上游认证证据仅在适配器调用此方法时提供 | `EstablishedCallerSubject { subject_ref, subject_proof, expires_at_ms }`；从真实认证建立主体，不接受消息体自报 Human；subject_proof 仅用于身份建立/派生，不是 MCP 凭据 |
| `issue_execution` | 不可由公共反序列化构造的 validated subject/parent、BCS 批准的 run/root/parent/turn、目标 Bot/Provider/Gateway audience、scope、delegation bounds、purpose、请求 TTL、可信工号 staff_no、目标注册 agent_code、ensure 返回的真实 sandbox_container_id 及可选隔离约束；其 `source` 取自 BCS Material Ledger，只能是 RootOriginHandle 或 ParentExecutionToken | 面向目标 run 的新 token，每次新 jti；有效期不超过入口认证、subject proof、parent proof 和策略的最早截止时间；caller 子链必须触发 exchange，child 不能越过 parent 限制 |
| `verify_execution` | secret token、调用方认证信息、期望 env/issuer/audience/purpose/Bot/run/scope、可选 binding、当前时间 | 验证后结构化 claims/subject handle，绝不把未验签的 claims 当可信 caller；失败返回结构化错误 |

`EstablishedCallerSubject`、verified handle 只能在受信运行时创建和持有，不进 DB、Debug、trace 或 message。上游认证证据是语义允许省略的字段：JWT 默认只需要已经认证的 principal；AgentPass 需要 IAM 时由可信适配器直接传入插件，不能放入 `TrustedHumanIngressContext`。

内部派生原则：`verify_execution` 验证密码学/声明约束，BCS 再核对 parent active、成员、拓扑、depth，并将核准结果交给 `issue_execution`；插件不能仅凭“上游签名有效”就自行允许 A2A。

#### 10.13.2.1 BCS-owned Identity Material Ledger

`CallerIdentityPlugin` 保持“建立主体、签发、验证”三个职责；材料由 BCS 侧的短期加密
Identity Material Ledger 维护。当前不要求 AgentPass 托管 token，也不要求 Provider 回传。

其记录分两类:

| 记录 | 内容 | 用途 | 生命周期 |
|---|---|---|---|
| Human origin handle | 已认证 Human 的 opaque 影子、root/turn、scope、有效期；只保存身份插件签发的 handle，不保存原始 IAM/Cookie/Bearer | Owner Manager / Workflow / 方案 8.3 等无 caller token 的中继派生 | 不超过入口认证/任务最早的可用期限 |
| Parent execution token | 上一跳 caller 执行证明、real parent run/attempt、purpose、scope、有效期 | A→B 等更进一步 caller 子链的 exchange；不用于 Owner Manager 的普通执行 | 不超过父 proof/run 有效期 |

流水边界:
1. BCS 从可信 Human ingress 取得 subject 后，将 human origin handle 传给插件。
2. BCS 为 caller-enabled root/worker 签发 execution token，并配套登记 Identity Material Ledger。
3. B 在子任务请求时, BCS ledger 提供它对应的材料; caller 链必须用 parent token 兑换, Owner 链使用 root/lineage handle.
4. Provider / Runtime 不承担把 token 发回 BCS；ledger 材料不进消息、回执或日志。
5. Ledger 不做业务 DB，也不做普通消息缓存。单实例可使用进程内存；多实例必须配置共享短期安全存储（memory-only、加密、TTL、独立最小权限）。共享存储丢失/过期均 fail closed，不自动续签、不回退 Owner；存储不可落盘、备份或普通日志。
6. 重启后必须先验证 ledger 中的根身份或父 token 仍有效，再允许继续派生；如果不存在，则视为缺失，不尝试从当前会话或 lease 重建。

#### 10.13.3 统一输出，不把 x-one-id 暴露为领域依赖

```json
{
  "version": "1.0",
  "format": "jwt",
  "type": "jwt",
  "token": "<runtime-only-token>",
  "expires_at_ms": 1790928300000
}
```

- `format` 是协议编码标签，不是信任依据；默认 JWT 输出 `jwt`，AgentPass 可输出 `opaque`，由注册能力和受信配置确定允许的格式。
- `type` 是签发插件标签：默认 JWT profile 输出 `jwt`，BCS AgentPass 签发输出 `agentpass`。它同样只是 wire 标签，信任仍来自受信装配。
- caller_context.subject.ref 始终携带可信员工工号（不加 human_ 前缀），供 Provider 路由；send 使用执行 Human，observe-only inject 使用接收 Human。插件内部 subject_ref 与路由工号分离；不新增 `x_one_id`、IAM 或 binding 字段。
- AgentPass adapter 将 x-one-id 或其引用映射为统一 subject/ref，并委托签发/兑换 execution proof。如果上游 x-one-id 本身无法约束 audience/run/purpose，adapter 必须用受信执行封装补足，不能把裸身份标识直接作为执行 token。
- 无 binding 仍可签发 proof，但 Gateway 必须满足协商的隔离要求。有 binding 时先 ensure，再将 binding/generation 作为 token 内部约束。
- `purpose` 区分 subject establishment、caller execution、仅派生 lineage；Owner Manager 的 lineage proof 不得用于 Human MCP 执行。observe-only 的 runtime_target 引用也不是 execution token。

#### 10.13.4 默认 JWT 实现

默认实现输入的是已认证 Human principal，**不是接受任意人名并签 token**。默认不要求 IAM token，不声称生成真实 AgentPass x-one-id。

项目 JWT profile 要求：

- 使用成熟 JOSE 实现与明确的非对称签名算法白名单；验证端不能持有签发私钥；禁止接受 `none` 或由来包自由选择算法。
- 验证签名、受信 issuer、audience、显式 token type/purpose、exp/nbf/iat、env/目标/作用域；`kid` 只能查询受信密钥集合，不按来包任意地址取密钥。
- JWT 标准 NumericDate 使用秒；外部 `expires_at_ms` 使用毫秒，转换必须测试，不能沿用示例中的毫秒值填 exp。
- JWS 可解码，不是加密。本方案“opaque”指业务层不得解析/看见，不代表 JWT payload 保密。payload 不放原始 Human ID、IAM、真实 x-one-id 或 MCP credential；subject 使用受限伪名引用，敏感材料需要保密时使用受信引用或标准 JWE。
- 长期容器 key 不使用 token 字符串/jti。默认实现可在可信插件边界从认证 principal 生成稳定、带 env/信任域隔离的加密学伪名 subject；密钥不与签名 key 混用，该伪名仍是敏感可关联身份材料，不落 BCS DB。
- 本地开发可使用进程临时生成密钥；重启导致旧 proof 无效须显式处理。部署使用配置注入的持久密钥引用和轮换，不提交私钥；公钥按受信部署分发。
- 离线验签不自动获得实时撤销能力。取消/过期由 run 状态、短 TTL 及 Runtime/Egress 的在线检查或受信取消通知配合完成；不能把验签成功等同于任务仍有效。

安全依据：RFC 7519（JWT 声明与 NumericDate）、RFC 8725 §3（算法、issuer/audience、类型和交叉 JWT 混淆防护）。这些是实现/契约测试要求，不允许手写签名算法。

#### 10.13.5 internal AgentPass 实现

- internal 插件依赖内部 AgentPass SDK/服务，社区契约和默认启动不依赖公司网络、私有包或地址。
- `establish_subject` 接收可信适配器提供的 IAM evidence，调用 AgentPass 兑换 x-one-id/ref，并映射为统一 subject/proof；认证证据调用结束后不保留、不持久化。
- `issue_execution` 向内部 exchange port 提交工号 `staffNo`、目标 Bot 的 `agentCode`、目标真实沙箱 `sandboxContainerId`，以及 `exchangeStage`。Human-origin/Owner lineage 使用 INITIAL；Caller parent 使用 RELAY，并带 BCS 精确检索的 `previousCallerToken`。不扩大 TTL、Bot 或 delegation 权限。
- `agentCode` 从目标 Bot 注册信息读取，不以 bot_id 兜底；沙箱 ID 从 Gateway ensure 顶层 `sandbox_container_id` 读取，不用 binding_ref/caller_ref 替代。AgentPass 缺任一字段即拒绝。默认 JWT 仍兼容没有沙箱 ID 的 subject 路由。
- RELAY 校验 parent run/attempt/root/turn/environment/session/purpose/depth；原 proof 与新 proof 的有效期只收缩。上游返回超过请求上限的 token 必须拒绝。真实 HTTP/SDK 传输负责 snake_case → camelCase 和 INITIAL/RELAY 映射，公开 bootstrap 尚不支持 AgentPass 生产装配。
- `verify_execution` 使用受信 introspection/验证能力返回统一 claims；网络超时、拒绝、过期分别映射领域错误。
- AgentPass 不可用时 fail closed，不回退 JWT、不回退 Owner；实际凭据消费方必须具备所选身份方案的处理能力，不强制 Gateway 配置额外 verifier。
- AgentPass 的真实 endpoint、请求结构和可用能力由 internal 集成确认，spec 中的 `/agentpass/x-one-id/exchange` 是需求语义，不宣称它已存在。

#### 10.13.6 MCP Credential Provider 保持独立

CallerIdentityPlugin 只提供“本次是谁、允许进入哪个执行范围”的 proof，不授予具体 MCP Tool/Resource/Action。

```text
Runtime/MCP Egress
  -> 检查可信执行身份的有效性和当前 run/隔离约束；使用 proof 时验证 proof
  -> 检查具体 MCP Grant；缺权时异步申请
  -> Credential Provider 换面向具体 MCP audience 的短期凭据
  -> MCP Server
```

默认 JWT 若用于证明，只可被明确配置为信任该 issuer/profile 的实际验证端接受；Gateway 下行不强制使用这一路径，不能直接假定任意 MCP Server 接受。未配置 MCP 凭据实现或信任契约时明确失败。MCP credential 不返回 BCS；internal 可以在身份插件和 Credential Provider 背后复用 AgentPass 客户端，但不能合并两种授权语义。

#### 10.13.7 配置、错误与测试

**生命周期与信任边界：**沿用项目统一插件生命周期，不新增业务层启动入口。初始化时验证配置、加载密钥或建立受信客户端，ready 后才接收签发；初始化失败拒绝启用 caller 能力；关闭时停止签发、释放连接并清除临时身份材料。该插件属于受信进程内身份基础设施，不是任意第三方扩展：JWT 仅访问配置的密钥源；internal AgentPass 仅访问配置的身份服务。不允许插件直接写消息/run DB、改变群拓扑或发送工具调用；通过依赖边界、配置校验与 conformance 测试约束。


落地公共配置（TOML，键名以 `bcs-config-api::CallerIdentityServiceConfig` 为准；
注释版模板见 `apps/bcs/configs/bcs-config-example.toml`——早期草案的
`allowed_algorithms`/`signing_key_ref`/扁平 `issuer` 键**未落地**，勿按旧草案配置）：

```toml
[caller_identity]
plugin = "jwt"                      # 本构建仅 jwt;agentpass 为内部适配器,fail-closed

[caller_identity.jwt]
issuer = "bcs-caller"
signing_kid = "kid-2026-01"         # 须匹配 public_keys 中一项
signing_key_pem = "configured-secret-reference"   # Ed25519 PKCS#8,生产走秘密下发
pseudonym_key_hex = "configured-pseudonym-secret" # 与签名密钥独立,>=16 bytes hex
max_ttl_seconds = 300

[[caller_identity.jwt.public_keys]]
kid = "kid-2026-01"
public_pem = "configured-public-key"
```

internal 可选择 `plugin=agentpass`，通过 internal 配置 schema 提供连接/认证引用（公开装配 fail-closed 拒启）。unknown plugin、密钥缺失、实际凭据消费方缺必要验证能力、接入能力不匹配时 caller 能力不可就绪；不得读取 Bot 消息切换插件。实现选择在 composition root 完成。

共同错误：`UNTRUSTED_PRINCIPAL`、`UPSTREAM_EVIDENCE_REQUIRED`、`IDENTITY_EXCHANGE_REJECTED`、`IDENTITY_PROVIDER_UNAVAILABLE`、`TOKEN_INVALID`、`TOKEN_EXPIRED`、`AUDIENCE_MISMATCH`、`PURPOSE_MISMATCH`、`SCOPE_MISMATCH`、`ISOLATION_UNSUPPORTED`；错误不回显秘密。

同一 conformance suite 必须覆盖两种实现：真实 consumer 调用、可信 Human 建立 subject、scope 缩减、有/无 binding、过期/错 audience/错 purpose、Owner lineage 不得执行、重签 child、防日志/DB/模型泄露。默认 JWT 增加篡改、算法、密钥轮换和秒/毫秒测试；internal 使用可记录调用的 AgentPass 替身测契约，真实服务另做部署验收，不声称替身等于生产验证。

---

### 10.13.8 API：Speaking Lease（仅 Caller 群）

> **当前实现补充（2026-10-20）：**上文的 `group.speaking_lease.*` 是早期拟议形状；当前 Workbench WS 实际使用 `lease.acquire` / `lease.preempt` / `lease.release` / `lease.current`，holder 始终来自认证连接。`lease.current` 与本人 success 响应均返回 `holderActorId`，前端用它区分“本人持有”和“他人占用”。`chat.send` 的实际可选字段为 `leaseId` / `leaseEpoch`。SQLite 通过 baseline DDL 创建 `bcs_session_speaking_leases`，MySQL 通过 versioned migration 032 创建同构表；群聊前端已接入 lease 命令、发言权控制条及发送时 lease 坐标注入。

方向：`Human Client/Ingress -> BCS`。Caller 群启用；普通群不需要。以下是拟议契约，不代表已有 API。holder 从可信入口认证取得，不接受公共请求 body 自报。

Acquire 请求：

```json
{
  "method": "group.speaking_lease.acquire",
  "params": {
    "group_id": "group_001",
    "session_id": "session_001",
    "preempt": false,
    "client_msg_id": "client-msg-001"
  }
}
```

Acquire 响应：

```json
{
  "ok": true,
  "lease": {
    "lease_id": "lease_001",
    "epoch": 12,
    "idle_timeout_ms": 60000,
    "expires_at_ms": 1780000060000
  }
}
```

Submit Human message 时带 lease：

```json
{
  "group_id": "group_001",
  "session_id": "session_001",
  "lease_id": "lease_001",
  "lease_epoch": 12,
  "client_msg_id": "message-001",
  "message": { "text": "请 A 帮我查一下" }
}
```

规则：

```text
持续持有：成功接收新消息仅刷新空闲期限，不消费 lease
默认 idle_timeout_ms=60000，由服务端配置，客户端不能任意延长
重复提交、心跳、Bot 回复与任务状态不刷新期限
submit 成功后生成新的 message_turn_id/root_run/token
lease holder 必须等于入口 authenticated human
lease 不代表长期 group active caller
抢占只使旧 lease 提交资格失效，不取消旧 run 或改变其 caller
child/执行/结果检查原 turn 与 run，不以当前 lease epoch 撤销旧执行
换人需获取/显式抢占 lease；每条新发言分别建立 turn 与 caller token
```

---

#### Speaking Lease 的抢占、释放与响应

- Acquire 的 `preempt=false`：空闲时获取；本人已持有时返回当前 lease，不续期；他人持有时返回 `LEASE_HELD`。
- `preempt=true`：在鉴权后原子抢占；携带幂等请求标识，重复请求不能再次抢占或刷新期限。客户端明确表达 Human 的抢占意图。
- Release 使用 `group.speaking_lease.release`，传 group_id/session_id/lease_id/lease_epoch；验证当前 holder 与 epoch，仅释放匹配 lease。旧释放不能清除新持有者。
- 提交成功的响应包含 message_id、lease_id、lease_epoch、刷新后的 expires_at_ms；重复提交可返回原接收结果，客户端据当前 lease 状态展示，不得将旧响应视为新的有效期。
- 提交失败区分 `LEASE_REQUIRED`、`LEASE_EXPIRED`、`LEASE_STALE` 与持有者不匹配；身份认证失败独立处理，不自动以新 holder 的身份重试。
- 获取/抢占/释放/提交续期的多副本原子性、幂等记录与消息接收边界必须一起实现。请求去重需绑定认证主体、session 与请求内容；不允许重放其他 Human 的 acquire/submit。lease 状态不包含 IAM、execution token 或 MCP credential；具体存储位置遵守既有身份不落库边界，在源码定位时确认。

---

### 10.14 BCS 内部 Service / Repo 接口建议

为了保持 BCS 分层，建议在 application/core/port 中拆成以下能力，而不是把判断散落在 HTTP/WS adapter 中。

#### Caller 应用层职责

Caller 应用层只校验可信来源、读取既有执行关联、调用身份插件、准备容器及历史并在最终出口附加身份。不得提供另一套 create_root/create_child/mark_status 生命周期 API；任务的创建与状态更新仍调用现有执行服务。

具体复用接口须在源码映射后确定，不为统一命名先新增无消费者的 service/repo 抽象。

#### `CallerIdentityPlugin`

统一替代 CallerTokenIssuer，契约见 §10.13；默认 JWT、internal AgentPass，由 composition root 注入。core 只调用 establish/issue/verify，不解析实现特有凭据。

#### `CallerContainerGatewayPort`

```rust
trait CallerContainerGatewayPort {
    async fn ensure_container(input: EnsureCallerContainer) -> Result<CallerIsolationDescriptor>;
    async fn dispatch_send(input: CallerDispatchEnvelope) -> Result<DispatchAck>;
    async fn dispatch_inject(input: ObserveInjectEnvelope) -> Result<ObserveAck>;
    async fn query_execution(input: ExecutionStatusQuery) -> Result<ExecutionStatus>;
    async fn cancel_execution(input: CancelExecution) -> Result<CancelReceipt>;
}
```

#### 既有执行服务 / Repo

复用现有 Chat、Managed delivery、task 与 Workflow 的读取、准入、派发、取消和结果处理接口，不新增 `ExecutionRunRepo` 或 A2A 专用生命周期 Repo。只在确有缺口时向原拥有方补充最小关联查询/写入，并为真实调用链增加契约测试。

分层规则：

```text
HTTP/WS adapter 只解析协议并调用 application service
既有 application service 编排 root/child dispatch；Caller 只附加身份准备与校验
core service 做 caller mode、parent、scope、depth 判断
port 负责 Gateway、CallerIdentityPlugin、Repo 等外部依赖
DB/cache plugin 只在 repo 实现内部使用
```

---

### 10.15 最小必做接口清单

MVP 必做（本表 P0/P1 表示接口优先级，不是实施 Plan 的阶段编号）：

| 优先级 | 接口/模型 | 目的 |
|---|---|---|
| P0 | `BotCallerCapability` | BCS 知道 Bot 是否需要 caller。 |
| P0 | 既有 run/task/node 的最小可信关联 | 支撑 parent/scope/depth 校验；不新增 Caller 生命周期表，不保存身份凭据。 |
| P0 | `caller.container.ensure` | 创建或获取 caller 隔离环境；可选返回 binding ref，不返回 route。 |
| P0 | `chat.send` caller 扩展 | 把 caller_task 投递给正确容器。 |
| P0 | `chat.inject` observe-only 扩展 | 同步上下文但不授予 caller 执行权。 |
| P0 | `execution.child_dispatch.request` | 支撑群内 A2A、主子、workflow 子任务重新签发。 |
| P0 | `caller_execution_receipt` | 回传真实 attempt/dispatch 状态和审计摘要，不回传 token。 |
| P0 | `execution.status / execution.cancel` | 查询未知派发、取消 fencing 与实际停止确认，防止迟到派发和旧 attempt 恢复。 |
| P0 | `observe_target / history_complete / history_ready` | ensure 返回观察引用、确认固定历史快照，执行前就绪屏障。 |
| P0 | BCS Identity Material Ledger | 保存 Human origin handle 与 parent execution token，支撑下一跳签发与兑换，不落业务 DB |
| P0 | `CallerIdentityPlugin` | 默认 JWT 身份签发/验证；internal AgentPass 实现同一契约。 |
| P1 | MCP Credential Provider | Runtime/Egress 在工具授权后兑换具体 MCP 凭据，与身份插件分离。 |
| P1 | Speaking Lease API | Caller 群控制提交权；抢占不取消旧执行；普通群不需要。 |

---

## 11. BCS 决策算法

### 11.1 Root dispatch

先读取 §10.7 已同步 Bot 配置与 Provider 生效状态。目标开启 caller 但 blocked 时拒绝，不进入 else 的 Owner 分支。若 Human 触发获准 caller 协作的群任务（包括自由群 Owner 目标、Manager/Workflow），仍先建立 caller-aware root/编排 lineage；Owner 目标只接收 delegation_only 上下文（proof 按需），不授予 Human MCP 执行权。

```text
read applied Bot configuration and Provider readiness
if authenticated Human turn is admitted to approved caller-enabled group collaboration:
    establish protected root lineage for each actual target; orchestration association for Workflow
    freeze history cutoff and projection (does not grant Owner targets Human MCP access)
if request.from_human && target_bot.caller_identity.enabled:
    require caller readiness
    create root run metadata and independent protected execution context
    ensure caller container
    if selected credential path requires token:
        issue_execution for target bot/run/attempt after ensure
        save origin handle / issuer material into Identity Material Ledger before dispatch
    prepare fixed history and require history_ready for group tasks
    commit dispatch metadata; dispatch caller_task with protected context and optional token
else:
    mode = open_task
    if protected delegation-only context was established:
        require Owner lineage capability; prepare fixed history on existing Owner route
        require history_ready; commit dispatch metadata
        dispatch Owner task with runtime-only lineage, without Human execution token
    else:
        dispatch ordinary open_task without caller lineage
```

### 11.2 Child dispatch

```text
if path is private/open A2A:
    do not inherit caller
    if target.caller_identity.enabled -> reject
    else dispatch open_task

if path is caller-enabled group A2A or workflow/manager-worker:
    resolve bot_run parent or authenticated orchestration parent; data predecessor is not identity parent
    validate parent run metadata
    validate trusted parent run/attempt association and execution/delegation_only purpose
    verify proof if the selected association path uses one
    validate topology/scope/depth/current parent attempt and cancellation
    atomically reserve shared in-memory turn allowance (no refund on admission error/unknown)
    admit real task through existing lifecycle/idempotency; persist only parent/invocation metadata
    inherit history cutoff and select target Bot snapshot
    if target.caller_identity.enabled:
        ensure caller container
        load parent token / root handle from Identity Material Ledger; verify scope and expiry
        caller child uses exchange(parent token, target execution); owner child uses origin/lineage handle
        save the newly issued target material for later derivation
    else:
        keep Owner route; establish delegation-only context for approved group collaboration
    prepare target history; require history_ready
    recheck current attempt, cancellation, membership/configuration and identity expiry
    commit dispatch metadata and send child with its independent protected context
    inject alone never creates a child run
```

### 11.3 Parent active 状态

Bot parent 按下列执行状态判断；Workflow 仅接受其活动编排 attempt（§8.5）。两者都必须 caller lineage 有效、身份未过期、无取消/fence、当前 attempt 匹配。已有 child 幂等重查不属于新派生；终态不授予新 child。

允许派生：

```text
running
waiting_child
waiting_tool
waiting_authorization
streaming
```

拒绝派生：

```text
final
completed
failed
cancelled
aborted
timeout
expired
```

---

## 12. 安全不变量

1. Caller 认证/执行材料及敏感映射不落 DB；既有业务 Actor ID 和 Lease 状态按 B7 允许持久化。
2. Caller token 不进入日志、DB、模型上下文或 Bot 可见消息。
3. Bot 不能自报 caller，也不能转发 caller 给其他 Bot。
4. 每个新的逻辑派发/attempt 建立独立受保护上下文，绑定目标、run/attempt、scope、purpose 和有效期；token 按需签发，网络重投保持幂等且不续期。
5. 未建立获准 caller lineage 的 H→A open_task，不能在 A→B 阶段自动升级为 caller_task。受控群内 Owner Bot（含 Manager）可携带 delegation_only 上下文供 BCS 派生 Caller child，Owner Bot 本身仍按 Owner 执行；不扩大私聊 A2A 范围。
6. 群聊中 Bob 发言不能使用 Alice 的 caller token/container。
7. 群内 A2A 继承 caller 必须经过 BCS，并校验可信 parent run/attempt 关联；使用 proof 时须验证 proof。
8. 私聊/开放式 A2A 默认不继承 caller。
9. Workflow/ManagerWorker 传递 caller 必须受拓扑约束。
10. MCP Credential 只在 Runtime/MCP Egress 与 Credential Provider/MCP Server 之间流转。
11. 任一校验失败必须 fail closed。

---

## 13. 场景验收标准

### 13.1 H→A

- A 开启 caller 时，BCS 创建 root run 和独立受保护上下文，token 按需签发；DB 不出现认证/执行材料，既有 sender/Lease 业务字段不因此删除。
- A 不支持 caller 时，不下发 caller_context/token。
- MCP 需要 Human 权限且无 Grant 时，Runtime 异步申请或失败，不用 Owner 静默执行。

### 13.2 群聊

- Alice 发言只使用 Alice 的 caller container/token。
- Bob 发言必须生成新的 message_turn 及各目标独立 root 上下文，token 按需签发，不复用 Alice 的执行身份。
- `chat.inject observe_only` 由 BCS 选择内容和目标，携带仅含目标 subject 的 caller_context、可选 runtime_target，不携带可执行 token；Gateway 不自行广播。
- 同 session 的定向协作消息不跨 Bot 扩散；共享消息在同 Bot 容器间一致，私有工具状态不进入共享流。
- 非 Caller 群不限制 Human 并发，不创建 caller container。

### 13.3 群内 A2A

- A→B 必须关联真实 parent run/attempt，由受信 Runtime/Gateway 提供受保护关联；token 按需附加。
- BCS 校验 parent active、请求方归属、scope、depth 和可信 caller 关联后，才为 B 建立独立上下文；使用 proof 时验证并重新签发目标 proof。
- token_a 不能直接用于 BotB。
- 自由群 Human→Owner A→Owner B→Caller C：准入时建立 lineage，Owner 工具无 Human 权限，C 使用原 Human；无 lineage/超限拒绝，不能临时升级。
- child 使用目标 Bot 在原 cutoff 内的合规历史；Bob 后续消息不进入 Alice child，历史不完整或代次失效时不执行。
- ensure 无 binding 但返回有效 observe_target 的同步路径成功；引用缺失/过期暂停，下一次可信 ensure 后恢复，不借他人凭据。

### 13.4 私聊 A2A

- 默认不继承 caller。
- target Bot required caller 时返回明确错误。

### 13.5 Manager/Workflow

- 任一参与 Bot 需要 caller 时，root 任务初始化 caller-aware lineage。
- 每个 Worker/Node 都由 BCS 建立独立上下文，token 按需签发，不由 Manager 转发执行身份。
- 多跳 depth 由 workflow 拓扑明确控制。
- 数据前驱 A 终态后，活动编排 W 可派 B；B parent=W 而非 A。join/节点重试幂等，编排终态/身份过期不再派发。
- ACK 丢失、cancel 先于 send、旧 attempt final/child/授权成功、容器代次丢失均按 §7.8 拒绝错误执行，不凭超时换 attempt。
- A→B→A 释放执行位置且 parent 保持 waiting_child；先到结果不丢、不双消费，超深/超预算有失败结果，不靠全群锁掩盖回环。

---

## 14. 设计决策索引

### 14.1 核心结论

| 编号 | 结论 |
|---|---|
| C1 | TeamClaw 的 Bot caller 配置由 Backend 同步；其他接入方扩展现有 Bot 注册/更新接口。Provider 技术能力独立上报。 |
| C2 | Caller 群发言权持续持有，Human 可主动抢占，主动释放或空闲到期失效。60 秒是当前可配置初始默认值，不是身份 TTL。 |
| C3 | 只有成功接收持有者的新消息才刷新空闲期限；重复提交、心跳、Bot 回复和任务运行均不刷新。 |
| C4 | 抢占/释放/空闲到期只影响新提交；旧任务及合法 child 继续使用原 caller，epoch 不撤销旧执行。 |
| C5 | Caller 容器长期按 Human×Bot 复用，Runtime 必须隔离 session 上下文。多 run 调度按 B1：执行级隔离才并发，否则串行。 |
| C6 | 异步授权后再次校验可信身份；无法恢复则等待或失败，不自动续签、不切换 Owner。B5：一期不提供过期后原 run 通用恢复，由 Human 重新发起。 |
| C7 | 身份签发/验证使用 CallerIdentityPlugin：默认 JWT、internal AgentPass；MCP 授权与 credential 兑换独立。 |
| C8 | BCS 决定共享内容与逻辑目标，Gateway 验证并路由；不传物理 route，binding_ref 可选且不进 caller_context。 |

字段归并：正式开关统一 enabled、可选隔离引用统一 binding_ref；旧字段只在兼容说明中保留，不代表已实现或已核实线上协议。

### 14.2 边界决策

以下编号用于各章节及 Plan 的稳定引用；本表只列最终决策，不记录讨论或实施状态。

| 编号 | 边界 | 设计结论 | 协同方 / 影响范围 |
|---|---|---|---|
| B1 | 同 Human×Bot 容器的多 run 并发、队列与 token 绑定 | 能隔离 run/attempt 才并发，否则 Gateway/Runtime 排队；出队重新验有效期，不自动续签 | Gateway/Runtime；H2A 多任务与群聊 |
| B2 | 执行期间 inject 的可见时机 | 同步历史但不隐式修改在途任务输入；run 使用准入固定的历史水位快照 | 产品、Runtime、BCS；并行群聊 |
| B3 | observe-only 引用丢失后的恢复 | 一期仅持有内存目标，失效暂停实时同步，下次可信 ensure 后补历史，不增加容器枚举接口 | 产品、Gateway、BCS；上下文同步 |
| B4 | 配置关闭、退群、解绑等撤销 | 阻止新准入/派生并请求停止相关在途任务；取消须确认，超时/失联明确反馈；具体时限由执行方承诺并验收，不同于抢占 | 产品、Gateway/Egress、BCS；撤销 |
| B5 | 过期后原任务恢复 | 一期不提供通用 resume，要求重新发起；重发不能隐式重放已有副作用，需结果/幂等提示 | 产品、Ingress、Runtime；异步授权 |
| B6 | run/attempt/parent 与 proof wire 契约 | 每独立目标任务独立 run，同消息共用 turn；执行重试新 attempt，网络重投幂等；新派发/attempt 独立受保护上下文，proof 按 B9 按需使用，区分 execution/delegation_only，详见 §7.5 | BCS、Gateway；H2A/A2A/事件 |
| B7 | lease 状态存储与多副本一致性 | 业务 holder/Lease 状态入 DB，认证与执行材料不入 DB；每条消息独立认证，DB 原子准入；普通群绕过 Lease，详见 §6.3.1 | 产品/安全、BCS；Caller 群 |
| B8 | Bot 配置生效与同步边界 | 以 BCS 已成功应用的最新有效版本为准，不设新鲜度、不新增同步失败恢复；保留版本/幂等/写失败反馈 | Backend、其他接入方、BCS；所有模式 |
| B9 | 信任与执行身份接入 | BCS 确认 caller；Gateway 信任受认证 BCS 并路由，不新增逐任务 proof 验证；Runtime/Egress 隔离和工具授权，见 §10.13.0 | BCS、Gateway、Runtime/Egress；接入验收 |
| B10 | 群内 A2A 开放与预算 | 允许自由群受控 caller 继承，无 Human 逐跳授权；自由群默认深度 2，每 turn 默认 16 child，服务端可配置，见 §7.6 | BCS、Runtime；预算与回归验收 |

详细规范：B1 见 §4.5.1；B2 见 §6.7；B3 见 §6.6；B4、B8 见 §10.7；
B5 见 §9；B6 见 §7.5；B7 见 §6.3.1；B9 见 §10.13.0；B10 见 §7.6。
各接入方必须完成接口、信任链、隔离、故障与 SLA 验收后才能开放对应能力。

---

## 15. 最终端到端摘要

### H→A

```text
H -> BCS -> A
BCS 从 Ingress 获取 Human
BCS 不保存 Human 认证与执行材料到 DB
BCS 建立独立执行上下文，按需签发 token_ha
A 在 H×A Caller Container 执行
MCP 在 Runtime 出口授权/兑换凭据
```

### 群聊

```text
Alice 发言 -> turn_001 -> 各目标独立 root 上下文，caller=Alice
Bob 发言   -> turn_002 -> 各目标独立 root 上下文，caller=Bob
换人/抢发言权 -> 新提交资格；实际新发言 -> 新 turn/上下文，token 按需签发
```

### 群内 A2A

```text
A -> BCS: parent_run_id + child task
Runtime/Gateway 提供真实 parent run/attempt 的受保护关联（proof 按需）
BCS 校验可信 parent 关联
BCS 原子预占内存调用额度，经既有任务链准入，准备原 cutoff 内目标历史并确认 ready
BCS 为独立 child 上下文按需签发 token_b，持久化非凭据 dispatch 关联
BCS -> B: caller_context 及按需 token_b；Gateway 内部路由到 caller container
```

### 私聊自由 A2A

```text
默认 open_task，不传 caller。
需要 caller 则失败，不自动升级。
```

### Manager/Workflow

```text
H root task 建立 caller-aware lineage
Manager 提交 child dispatch；Workflow 编排器根据节点依赖提交 node dispatch
BCS 分别验证 Bot parent 或活动编排 parent；已完成节点只作为数据前驱
继承原历史上界，准备目标快照并确认 ready，再派发独立上下文，token 按需签发
```

---

## 16. 增量设计与归档索引（2026-10-23 文档合并）

> 本节由多份过程文档合并而来；合并完成后原文档删除，全文可按 §16.4 索引从 git
> 历史检索。设计与执行记录的权威仍以本 Spec 与同目录 [实施 Plan](plan.md) 为准。

### 16.1 state_machine 群 Caller 身份接入（G1 入口关联 + G2 speaking_mode 特判，2026-10-22 已实施）

前置（Plan §15.83 S1–S4 已全链落地并生产装配）：S1 节点保护派发 port、S2 提交侧实例
关联注册、S3 runtime 分支 refuse-closed、S4 `with_caller_orchestration` 三件套；此前唯一
已贯通的 Human 触发入口是 one-shot `POST /sessions/{sid}/state-machine-runs`。本增量
补齐非一次性运行的两个 Human 入口与 connect speaking_mode 特判。

**范围与边界**：只在已存在的 `OrchestrationInstanceAssociation` 通道上新增两个生产者；
不改 `CallerDispatchPipeline`、不改 wire 协议、不改 node/attempt/claim 生命周期。身份
材料永远 runtime-only（`AuthenticatedHumanCallerIngress` 内存态），不进持久
request/frame；runtime 盖章 scope、registrar 对失真事实 fail-closed 的既有语义全部沿用。

- **入口 A（bootstrap 初始 run）**：建群 HTTP 请求携带的受信 Human ingress 随
  `StartStateMachineRunCommand.caller_association` 模板进入（对齐 one-shot 入口的形状），
  runtime 盖章/注册零改动。**模板构造以组合装配为门**：`HttpState` 增组合派生布尔
  `caller_orchestration_wired`，仅 wired 时构造——否则建群请求会在 §15.90 冻结的
  "association supplied but no registrar" 路径被整条拒绝，令未装配 `[caller_identity]`
  的部署无法建 state_machine 群（回归不可接受）。
- **入口 B（HumanInput 推进）**：`HandleSessionHumanInputCommand` 增
  `caller_ingress: Option<AuthenticatedHumanCallerIngress>`；WS dispatcher 从连接 auth
  快照透传（§15.97 连接期语义）；human 响应被节点**成功消费后**复用同一盖章格式幂等
  续注 run 的关联窗口。
- **续注策略（收敛在 registrar 实现内）**：`register_instance` 幂等键 `workflow_run_id`
  ——同主体（staff_no 相同）刷新窗口；异主体拒绝重绑（返回 false，runtime 仅 warn，节点
  按既有事实 caller-closed），防 mid-run 身份劫持。
- **G2**：`resolve_connect_speaking_mode` 增 `group_strategy` 入参，`state_machine` 群
  一律 `Plain`（在 caller-enabled 检查前返回）；HumanInput 消费面不与 lease 语义相交，
  前端零改动；manager_worker 群不受特判影响。

**窗口语义（ADR）**：bootstrap 关联窗口 = 建群请求时刻的登录剩余有效期
（`extract_caller_request` 提取的 `ingress.expires_at_ms()`）；HumanInput 续注窗口 = WS
连接**升级时刻**的登录窗口（§15.97 已冻结：连接存活期间不重查；续注真正生效于前端
重连后的新输入——凭据 lifetime 从不虚增，是安全性属性而非缺陷）。节点 proof 仅在窗口
内签发（§15.90 单调收缩语义不变）；窗口耗尽后节点 refuse-closed（`ORCHESTRATION_REFUSED`，
既有行为）。**跨主体规则**：一个 run 的身份父只绑定一次；后续异主体输入不重绑、不停机，
节点按既有事实派发并留告警日志。被否决的替代：冻结一次性窗口（误杀"人刚参与输入"的长
流程主场景）；绑定 run 存续期（把 30 分钟登录凭据虚增至 3 小时运行期，违背真实有效期原则）。

**错误处理**：

| 情形 | 行为 |
|---|---|
| 组合未装配 caller（registrar 缺失） | 入口 A/B：不构造/跳过续注＋告警（身份升级是建群的副产品，不得令建群或 HumanInput 消费失败——与 §15.90 "显式提交不得降级"刻意区分：one-shot 提交的主意图就是 caller run，维持既有整条拒绝）；均有日志，非静默 |
| 入口 A 组合 wired 但提取失败（无受信窗口） | association=None，run 照建（caller-closed） |
| 入口无 ingress（WS 陈旧登录 / V1 display-only / Bot） | association=None，run 保持 caller-closed |
| 异主体续注 | registrar 返回 false + warn；run 继续运行，原关联不被动 |
| 倒置窗口 / 空主体 | 既有 §15.90 入索引前 fail-closed |
| 节点运行时窗口耗尽 | 既有 refuse-closed（ORCHESTRATION_REFUSED） |

**非目标**：Identity Material Ledger 多进程 failover（P5 另批）；join 节点关联语义；
`services.rs` / `session/launch.rs` / v1 app-group 三个机器中心入口本期保持
caller-closed（无 Human 认证链），文档标注即可。

### 16.2 Provider 下行协议与对接 API 报告要点（2026-10-19 基线）

来源：同目录 `api-report.md`（已删，全文见 §16.4 索引）。核心变化：**不再给 Bot 一个
不区分 Human 的执行入口，而是在既有 Provider webhook 上携带可信 Caller 与本次派发
关联，由 Provider/Gateway 路由到对应执行环境**；Gateway 是 Provider 承担的能力，BCS
不另连一套容器 HTTP/WS 接口。

对接面速览（原文 §1.1）：

| 对接面 | Caller 增量 | 是否新开 URL |
|---|---|---|
| Provider webhook `caller.container.ensure` | 新增 method；创建/复用 Human×Bot 容器 | 否 |
| 同一 webhook `chat.send` | 解析根级 `extensions.caller_context`、`caller_execution_token`、`execution`，路由 Caller 环境并返回 JSON ACK | 否 |
| 同一 webhook `chat.inject` | 解析 `caller_context.subject`、可选 `runtime_target`/`observe_only`/消息去重键，仅更新指定容器上下文 | 否 |
| Bot 配置同步 | 配置权威方调用 Caller GET/PATCH 子资源（`/providers/{id}/bots/{ref}/caller`），提交版本化开关快照 | 是，BCS 侧子资源 |
| 执行结束通知 | 复用 `POST /bot/events` 的 run 关联和结果链（`caller-terminal` 已于 b503e66b3 移除） | 否（BCS 侧回调） |
| 身份兑换 | BCS 内部插件建立主体并签发目标 proof；Provider 不负责替 BCS 换取 Human IAM 登录态 | 不新增公共兑换 URL |

不变式：不新开容器 chat API、不要求 `container_route`/物理 container ID、不恢复
`extensions.binding`、不要求 Caller 专用任务生命周期；普通 Owner 请求保持原协议；
`caller.enabled` 不等于 Provider 容器能力就绪，更不等于 Human 已获 MCP 权限。实现差距
与对接验收以 Plan §15.* 执行记录为唯一证据源。

### 16.3 历史沿革：BCS V1 Human Caller 集成（2026-08-02）

来源：`apps/bcs/docs/superpowers/specs/2026-08-02-bcs-v1-human-caller-integration-design.md`
（已删）。该文中的 "Caller" 指 Gateway 签名 `X-Avernet-Principal` 验证出的完整
`AuthenticatedCaller`（可同时含 User/Bot/App/AccessKey），其核心决策已实现在 BCS V1
HTTP adapter 并沿用：BCS V1 全部 32 个 HTTP 操作要求可用 User 身份并按 Human Actor
执行；HTTP middleware 保存完整 Caller、Application 用例显式选择 User 身份后再做资源
鉴权；无 User 的合法 Caller 一律禁止，不回退 Bot/App/AccessKey 元数据；路径或 body 中的
`bot_uuid` 只标识受管资源，不把 Human Caller 变成该 Bot Actor；View Actor 仅作用域
返回数据，不改变 Caller、不冒充、不授予管理权。其后的身份委托与执行保护演进由本
Spec 全面覆盖。

### 16.4 已归档文档索引

以下文档于 2026-10-23 合并入本节与 Plan 后删除；全文可从 git 历史按文件名检索：

| 原路径 | 类型 | 处置 |
|---|---|---|
| 同目录 `api-report.md` | 实现对接报告 | 要点并入 §16.2 |
| `docs/superpowers/specs/2026-10-22-statemachine-group-caller-identity-design.md` | 增量设计 | 并入 §16.1 |
| `docs/superpowers/plans/2026-10-22-statemachine-group-caller-identity.md` | 实施 Plan | 执行记录并入 Plan §15.101–§15.104 |
| `apps/bcs/docs/plans/2026-10-03-caller-identity-source-mapping.md` | 源码映射审计 | 被 Plan §15.* 执行记录取代 |
| `apps/bcs/docs/superpowers/specs/2026-08-02-bcs-v1-human-caller-integration-design.md` | 早期 V1 设计 | 沿革并入 §16.3 |
| `apps/bcs/docs/caller-p6-acceptance-readiness.md` | P6 验收审计索引 | 勘误已登记 Plan §15.95 |
