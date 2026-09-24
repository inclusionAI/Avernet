# Direct A2A 消息接入 BCS 队列

- 日期：2026-09-20
- 状态：已实现，受影响模块回归通过；完整集成验收与真实 MySQL 验证尚未通过
- 所属模块：BCS / bcs-message-flow / bcs-message-store / bcs-chat-run-store / bcs-cli
- 范围：`POST /bots/{id}/chat-async`、Admin Direct A2A invocation、ChatRun 查询与取消、bcs-cli chat
- 架构依据：[架构约束](../../../../docs/arch/arch.rules.md)、[消息流边界](../../crates/services/bcs-message-flow/CONTEXT.md)、[Message Store 边界](../../crates/services/bcs-message-store/CONTEXT.md)、[Service API 边界](../../crates/service-api/bcs-service-api/CONTEXT.md)、[消息拥塞控制设计](../../docs/plans/2026-09-04-bcs-message-congestion-control-design.md)。本变更延续现有 managed delivery 架构，无新增系统级 ADR。
- 相关 ADR：当前 `docs/adr/` 中没有适用于 BCS 消息队列或 Direct A2A Session 的既有 ADR；本决定局限于 BCS 模块，通过本 spec 固化。

## 1. 问题与目标

Direct A2A 当前创建 ChatRun、构造 `chat.send` 后直接调用 Bot delivery，不写
`bcs_messages` 和 `bcs_message_deliveries`，因此无法使用现有队列的 Bot 并发限制、
Session FIFO、发送间隔、队列容量、持久恢复和 managed cancel。

本次将 Direct A2A Send 接入统一 managed delivery runtime，并保持以下外部语义：

- 调用方继续通过现有 async chat API 获取 `run_id` 和 `session_id`。
- API、ChatRun、delivery 和下游 `chat.send` 使用同一个 Session ID。
- 显式传入的 Session ID 保持原值；未传入时继续使用现有生成规则。
- 同一环境中的一个 Session ID 只能属于一种 Session 类型：`group` 或 `direct_a2a`。
- Direct A2A 的 canonical 请求正文进入 `bcs_messages`；delivery 不复制正文。
- ChatRun 继续保存流式响应和面向调用方的结果投影。
- 已关闭 Direct A2A 队列时保留旧直发路径，但不能越过同 lane 的既有队列消息。

本次不增加 Direct A2A 历史查询 API，不把 Direct A2A 消息暴露到 Group history，也不实现
Provider scope-wide abort、队列位置估算、附件输入或 StateMachine 消息迁移。

## 2. 已确认的核心决定

### 2.1 Session ID 不做双重映射

对受管 Direct A2A：

```text
HTTP/CLI session_id
  = ChatRun.session_key
  = bcs_session_registry.session_id
  = bcs_messages.session_id
  = bcs_message_deliveries.session_id
  = chat.send downstream session key
```

`QueuedTransportContext.downstream_session_key` 仍可保存发送时快照，用于 frame 和回调作用域
校验；其值必须等于 delivery 的 `session_id`。该字段不是第二个 Session 身份。

同一 target Bot 和 Session ID 构成 scheduler lane。不同 target 使用相同 Session ID 时共享
消息序号空间，但属于不同 delivery lane；序号存在间隙不影响每个 lane 的 FIFO。

### 2.2 Session ID 类型归属由共享 registry 保证

`bcs_chat_runs` 是一请求一行的运行记录，同一 `session_key` 可以对应多个 run，终态 run 还会按
retention 清理，因此它不能作为 Session 身份或类型归属的事实源。仅在 ChatRun 与
`bcs_group_sessions` 之间执行“先查询再插入”也无法阻止并发的跨类型创建。

新增 BCS 全局 Session ID registry。它不是 Direct A2A 专用表；所有需要占用 Session ID 的
Session 类型都必须登记，以数据库唯一键作为类型归属的唯一事实源。Direct A2A 行同时保存消息
序号：

```sql
CREATE TABLE bcs_session_registry (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  env VARCHAR(64) NOT NULL,
  session_id VARCHAR(128) NOT NULL,
  session_type VARCHAR(32) NOT NULL,
  current_msg_seq BIGINT NULL,
  gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_session_registry (env, session_id)
);
```

`session_type` 首版只允许：

| 值 | 含义 |
| --- | --- |
| `group` | `bcs_group_sessions` 中的 Session，包括 Chat、ManagerWorker 和 StateMachine session_kind |
| `direct_a2a` | Direct A2A Session |

客户端不能提供或修改 `session_type`。`group` 行的 `current_msg_seq` 必须为 null，Group 消息序号
继续由 `bcs_group_sessions.current_msg_seq` 管理；`direct_a2a` 行必须从 0 开始且非 null，由
Direct admission 原子递增。未知类型或不符合该约束的行会阻止 Direct A2A 队列 readiness。

registry 与现有业务表的职责如下：

| Session 类型 | registry 职责 | 业务数据位置 |
| --- | --- | --- |
| `group` | 占用 Session ID，阻止 Direct A2A 使用同一 ID | `bcs_group_sessions` |
| `direct_a2a` | 占用 Session ID，并维护 `current_msg_seq` | `bcs_chat_runs`、`bcs_messages`、`bcs_message_deliveries` |

`bcs_session_registry` 不替代 `bcs_group_sessions` 或 `bcs_chat_runs`。Group Session 的参与者、状态
和生命周期继续属于 `bcs_group_sessions`；每次 Direct A2A 调用的运行状态和响应内容继续属于
`bcs_chat_runs`。

registry 写入规则：

1. 数据库升级时，将现有 `bcs_group_sessions` 全量回填为 `group` 行。
2. 新建 Group Session 时，在同一数据库事务中写 registry 和 `bcs_group_sessions`。
3. 首次使用 Direct A2A Session 时，ensure 一条 `direct_a2a` registry 行。
4. 已存在不同 `session_type` 的同 ID 行时返回 `session_type_conflict`。

### 2.3 不新增 `bcs_direct_a2a_sessions`

Direct A2A 当前没有独立于 ChatRun 的标题、参与者、状态或生命周期元数据。为仅保存一个序号再
增加一张 Direct 专用 Session 表没有必要；`bcs_session_registry` 的一行已经同时提供：

- `(env, session_id)` 的全局类型唯一性。
- Direct A2A 的持久 `current_msg_seq` 和 admission CAS 行。
- 空 Session 的稳定身份；ChatRun 清理后仍不会被另一 Session 类型复用。

registry 不保存 source Bot、target Bot、请求正文、响应正文、凭证或 Provider 路由。一个显式
Session ID 可以被多个 Direct A2A run 使用；每个 run 的参与者和授权事实保存在 ChatRun、
canonical message 和 delivery projection 中。registry 首版不提供 completed/deleted 生命周期。

## 3. Session 认领契约

### 3.1 Group Session

所有新 Group Session 创建事务必须同时：

1. 插入 `(env, session_id, group, current_msg_seq=NULL)` registry 行。
2. 插入 `bcs_group_sessions`。
3. 在同一数据库事务中提交。

registry 行已存在时：

- 类型为 `group`：按现有 Session 创建幂等和冲突规则处理。
- 类型为 `direct_a2a`：返回固定错误 `session_type_conflict`，不创建 Group Session。

### 3.2 Direct A2A Session

每次 Direct A2A 请求在业务鉴权和 Session ID 格式校验通过后，都要 ensure Direct A2A
Session；这一规则同时适用于队列开启和 legacy 直发，确保关闭期间使用过的 Session ID
也不会随后被 Group Session 认领。

ensure 事务必须：

1. 尝试插入 `(env, session_id, direct_a2a, current_msg_seq=0)` registry 行。
2. 行已存在且类型为 `direct_a2a` 时读取并返回该行。
3. 新增或读取结果必须满足 `current_msg_seq IS NOT NULL`。

registry 行已存在时：

- 类型为 `direct_a2a`：幂等返回现有 Session。
- 类型为 `group`：返回 `session_type_conflict`，不创建 ChatRun、不发送消息。

同类型的两个并发 ensure 最终都读取同一行；跨类型并发只允许一个事务取得 registry 行，另一个
明确失败。Memory 和 SQL 实现必须共享该契约，不能仅依赖进程内锁。

### 3.3 表归属与调用方向

Session Store 拥有 Session registry 与 Group Session 的创建/认领契约。Message Store 在 Direct
admission 中读取 registry、锁定并递增其中的 `current_msg_seq`；它不创建业务 Session。

Direct A2A application service 通过 transport-neutral Session port 调用 ensure，不直接访问
DB plugin。Group Session 现有创建服务通过同一 registry port/transaction plan 写入 registry。

Direct Session ensure 与消息 admission 是两个事务。ensure 成功后进程崩溃可能留下
`current_msg_seq=0` 的空 registry 行；这是允许的。消息序号递增、message 和 delivery 必须保持
同一 admission 事务，不能产生只有序号或只有 delivery 的半提交。

## 4. 数据库迁移和启动门禁

### 4.1 迁移内容

MySQL/OceanBase 与 SQLite 的下一个可用迁移必须包含：

1. 创建 `bcs_session_registry`。
2. 将全部现有 `bcs_group_sessions` 回填为 `session_type=group`、
   `current_msg_seq=NULL` 的 registry 行。
3. 将 `bcs_messages` 的唯一键从 `(session_id, session_seq)` 调整为
   `(env, session_id, session_seq)`，与现有环境隔离查询一致。
4. 给 `bcs_chat_runs` 增加 nullable `delivery_id`、`source_message_id`，并增加环境范围索引。

回填遇到同一 `(env, session_id)` 的不同类型数据时迁移失败，不选择覆盖方。迁移前没有
Direct A2A registry 数据，因此正常升级只产生 `group` 行。

### 4.2 启动一致性检查

代码声明 Direct A2A ready 前，启动和 master 接管必须验证：

- 每个 Group Session 有且仅有一个 `group` registry 行，且 `current_msg_seq IS NULL`。
- 每个 `direct_a2a` registry 行的 `current_msg_seq` 非 null 且不小于 0。
- registry 类型与 `bcs_group_sessions` 的存在关系一致。
- 已删除 Group Session 的 `group` registry 行保留为类型占位；`direct_a2a` registry 行不能有同 ID Group Session。
- Message/Session 唯一索引已包含 env。

校验失败时：

- Direct A2A managed admission fail closed。
- `flow_enabled.direct_a2a=true` 的策略不能发布或激活。
- 已有 Group/System/Task 队列不因 Direct A2A readiness 失败而被改写。

### 4.3 分阶段发布

发布 A：

- 应用迁移并回填 registry。
- 所有 Group Session writer 开始写 registry。
- 所有 Direct A2A 请求开始 ensure registry，但仍走 legacy delivery。
- `direct_a2a` 继续被 code-owned readiness 拒绝。

完成全量实例升级和一致性核验后，发布 A 成为回滚基线。

发布 B：

- 部署 Direct A2A admission、preparation、terminal、cancel 和 CLI 适配。
- 验收通过后将 DirectA2a 加入 code-owned ready flows。
- 按单 Bot override 灰度启用。

启用后只能回滚到包含 Session registry 写入能力的发布 A。更老的二进制会创建不带 registry 的
Group Session，不能作为安全回滚目标。

## 5. Canonical message 与可见性

新增 `MessageVisibilityDomain::DirectA2a`。它必须携带 Directed audience，Human 非 Full
视图只在 actor ID 命中 audience 时可见；普通 Group history 仍按 group/session scope 查询，
不会返回 `group_id=""` 的 Direct A2A message。

Direct A2A 请求保存为：

| 字段 | 值 |
| --- | --- |
| `message_id` | admission 生成 UUID |
| `group_id` | 空字符串 |
| `session_id` | ChatRun/downstream 使用的原 Session ID |
| `sender_id` | authenticated `from_bot_id` |
| `sender_type` | `bot` |
| `message_type` | `direct_a2a_request` |
| `content` | `{"text": <request message>}` |
| `client_msg_id` | `direct-a2a:<run_id>` |
| `owner_bot_id` | target Bot ID |
| `visibility_domain` | `direct_a2a` |
| `audience` | Directed `[from_bot_id, target_bot_id]`，排序去重 |
| `run_id` | ChatRun run ID |

Message Store admission 校验调整为：

- Group/System/Task 继续要求非空 `group_id`。
- DirectA2a 必须要求空 `group_id`、非空 Session、一个 Send target、上述 message type 和可见性。
- DirectA2a 不允许 Inject，不绑定 Group/System pending context。
- DirectA2a 不生成 `message.created` Group 事件。

ChatRun 的 `original_request` 可以保留现有只写审计快照，但它不是 frame 重建或重试来源；
managed delivery 只能从 `bcs_messages` 读取正文。

## 6. Direct A2A admission

### 6.1 入口顺序

Managed 和 legacy Direct A2A 共用以下前置步骤：

1. 解析 authenticated source Bot 和可选 staff identity。
2. 校验 source ownership。
3. 校验 friend 或 organization pair authorization。
4. 校验 target 存在、未隐藏并满足现有可达性规则。
5. 解析并校验 Session ID。
6. ensure `direct_a2a` Session 归属。

命中 managed policy 后：

7. 预分配 source message ID；单 target Direct delivery ID 使用 run ID，并在创建 Pending ChatRun 时一起保存。
8. 构造 Direct A2A admission command。
9. 原子分配 `session_seq`、写 canonical message 和 delivery。
10. 根据已提交 delivery 校准 ChatRun 状态。
11. 返回 HTTP 202；不调用旧 `bot_delivery.deliver()`。

步骤 7 成功而步骤 9 失败时，确定的参数拒绝将 ChatRun 标记 Failed；存储错误可能隐藏已提交
事务，必须返回 5xx 并保留 Pending，由查询/恢复确认 delivery。创建后 30 秒仍没有 delivery 的
orphan run 标记 Failed。关联 ID 在 admission 前已经保存，不存在事后回填失败窗口。

### 6.2 admission contract

Direct A2A 只有一个 Send target，复用 canonical `NewMessage.run_id` 作为逻辑 run 身份，
无需再向每个 target 添加重复的 `logical_run_id` 字段。store 将同一个值写入 delivery 的
`run_id`、`delivery_id` 和 `idempotency_key`；其他 flow 保持原来的 store 分配规则。
`(env, run_id)` 唯一键继续阻止重复运行准入，Memory 实现执行相同唯一性检查。

Direct projection 保存绝对执行 deadline；公共 runtime 在 StartSend 时使用该 deadline，
不能将它替换成 Group 的全局 run timeout。同一 admission batch 不允许混合 Direct 和 Group 类序号源。

### 6.3 admission 事务

DirectA2a admission 使用现有 Bot capacity writer lock 和 Session writer lock，然后：

1. 从 primary/transaction 锁定 registry 行，要求 type=`direct_a2a` 且序号非 null。
2. 读取 `bcs_session_registry.current_msg_seq`。
3. 执行 sender/client_msg_id 幂等查找。
4. 读取 target 当前 queued 数量并决定 `queued` 或 `rejected_capacity`。
5. 计算 `next_seq=current_msg_seq+1`。
6. 用旧 sequence 做 CAS 更新。
7. 插入 canonical message。
8. 插入唯一一条 `flow_kind=direct_a2a`、`kind=send` delivery。
9. 在同一事务提交。

容量拒绝是持久终态：message 和 `rejected_capacity` delivery 一起提交，ChatRun 随后投影为
Failed，不调用 transport。Provider Header 不满足持久化白名单时沿用固定 admission error，
不保存被拒绝 Header 值。

Direct admission 不读取 `bcs_group_sessions`，也不创建伪 Group。

### 6.4 semantic projection

新增 versioned `DirectProjection`：

| 字段 | 含义 |
| --- | --- |
| `version` | 首版为 1 |
| `from_bot_id` | authenticated source Bot |
| `from_actor_id` | frame 中的来源 actor 快照 |
| `sender_name` | 展示名称快照 |
| `target_tags` | Provider target tags 快照 |
| `response_mode` | full / after_last_tool_call |
| `caller_wait_mode` | 调用方等待模式 |
| `completion_policy` | wait_for_final / detach_delivery_ack |
| `organization_code` | 可选组织作用域标识，用于发送前重新授权 |
| `client_kind` | 指标和 Provider transport preference 分类 |
| `expires_at_ms` | ChatRun 绝对截止时间 |
| `provider_route_headers` | 仅允许配置白名单中的规范化 Header |
| `policy_version` | admission 使用的策略版本 |

正文、token、Cookie、staff credential、完整 wire frame、response delta 和未在白名单中的 Header
不得进入 projection。

## 7. 策略选择与关闭排空

`DeliveryPolicy` 增加 `manages_direct_a2a(target_bot_id)`，并将 direct_a2a 纳入
`needs_scheduler()`。新请求只有同时满足以下条件才进入队列：

```text
flow_enabled.direct_a2a == true
target Bot effective mode == enforce
Direct A2A code-owned readiness == ready
scheduler supervision healthy
durable store available
```

未命中时走 legacy 直发，但首先按 `(target_bot_id, session_id)` 查询 `LanePending`：

- lane 无未完成 Send：允许 legacy。
- lane 有 queued/active/unknown/cancelling Send：返回 `queue_draining`，不能越序直发。

已入队 Direct A2A 在策略关闭后继续 drain、接收 terminal 和 cancel。关闭开关不删除 message、
delivery、ChatRun 或 Session registry。

## 8. preparation、send-start 与下行

公共 `ManagedDeliveryPreparationService` 按 `flow_kind` 分派：

```text
Group/System/Task -> 既有 group-like preparation
DirectA2a        -> Direct A2A preparation
StateMachine     -> 保持未就绪
```

通用 `before_send` 和 `prepare_abort` 逻辑抽出复用；Direct preparation 不查询 Group 或 Group
Session，不调用 group reply/interceptor helper 中依赖 Group 的部分。

Direct preparation 必须：

1. 解码且严格校验 projection version。
2. 按 message ID 读取 canonical request，校验 sender/run/session/target 对齐。
3. 读取 Session registry，要求仍为 `direct_a2a` 且序号有效。
4. 重新检查 target 未隐藏、当前 delivery target 可用。
5. 对 organization scoped run 重新执行 pair authorization；普通 friend 路径重新检查当前关系。
6. 运行当前 outbound policy/interceptor；interceptor 必须使用 canonical text。
7. 解析当前 WS/Provider target 和协议版本。
8. 使用 delivery Session ID 构造现有 `chat.send` frame。
9. bcs-cli client kind 继续选择 Provider Callback，其他客户端保持 SSE-first 规则。
10. 生成不含敏感值的 `QueuedTransportContext`。

StartSend 持久化成功后、网络 I/O 前：

- 注册 `BotRunContext`，`group_id=""`、`bcs_session_id=session_id`。
- 注册 active run 和当前 transport owner。
- 绑定 request attempt alias。
- 将 `chat.send.id` 替换为当前 attempt request ID；canonical run ID 保持在 delivery/context。

submission 只证明 transport 接受请求。可信 ACK 或 delta 才进入 Running；final/error/aborted
可以从 Dispatching 直接结算终态。

## 9. deadline 与本地等待任务

Direct A2A 保持现有端到端 `expires_at_ms = admission time + requested timeout`。队列 TTL 是
额外上限：

```text
delivery.expire_at_ms = min(chat_run.expires_at_ms, policy queue TTL if present)
delivery.run_deadline_at_ms at StartSend = chat_run.expires_at_ms
```

如果绝对 deadline 在 StartSend 前已经到达，delivery 直接 Expired，不发送网络请求。

公共 runtime 在 DirectA2a StartSend 时读取已验证的 `DirectProjection.expires_at_ms`；
Group-like flow 继续使用现有 runtime default。`PreparedManagedDelivery` 无需重复保存该字段。

Managed Direct A2A 不再使用从 HTTP admission 时开始的进程内 `drain_async_run(timeout_ms)`
独立决定终态。超时、过期和取消由 durable delivery 状态机驱动，公共 scheduler 对关联 delivery
发起 cancel/expiry 并等待可信结果，ChatRun cleanup 只校准投影，不能绕过 delivery 制造终态。

## 10. Bot 事件与 ChatRun 投影

WS 和 Provider 事件继续先进入 `MessageFlowService::handle_bot_event`。现有空 group run context
恢复逻辑将事件关联到 Direct A2A delivery。

新增 transport-neutral `DirectChatRunProjectionPort`，由 Direct Chat 服务实现并注入 MessageFlow。
它接收已经完成 delivery 身份校验的 Direct A2A event，更新 ChatRun content/state。Managed path
不依赖进程内 run channel 才能记录终态；legacy path 保持现有 channel 行为。

Direct terminal 分支必须：

- 按 run/request/downstream alias 找到唯一 delivery。
- 校验 target Bot、空 group、Session 和 run scope。
- 在 per-delivery event lock 内处理 delta/final/error/aborted。
- delta 可提交 Accepted/Running，并更新 ChatRun response content。
- final/error/aborted 通过 delivery state_version CAS 提交 Completed/Failed/Cancelled。
- delivery 提交后更新 ChatRun 投影并清理 run context/channel/tracker。
- 不调用 Group `settle_without_relay`，不创建 `run_reply`，不写 Group history，不执行群转发。
- DB 写失败传播；storage-only retry 可以重试稳定的 lookup/CAS/ChatRun 写入，不能重放网络或整条事件管线。

ChatRun 继续是响应正文的持久投影。本次不把 Direct A2A reply 另写为 `bcs_messages`，也不新增
Direct A2A history API。

## 11. ChatRun 与 delivery 一致性

### 11.1 关联

新 managed ChatRun 保存 nullable `delivery_id` 和 `source_message_id`。旧 run 和 legacy run
保持 null。delivery 的 `(env, run_id)` 唯一索引保证一个逻辑 run 对应唯一 delivery。

创建顺序固定为 ChatRun 在前、delivery admission 在后，避免 scheduler 在 ChatRun 不存在时
发送。允许的崩溃窗口：

| 窗口 | 恢复行为 |
| --- | --- |
| ChatRun 已创建，admission 未提交 | 宽限期后将 orphan Pending ChatRun 标记 Failed；没有 delivery，不发送 |
| admission 已提交，HTTP 结果丢失 | 根据预先保存的 delivery ID 查询并校准 ChatRun |
| delivery 已终态，ChatRun 未终态 | 查询/恢复根据 delivery 校准 ChatRun |
| ChatRun 已终态，delivery 非终态 | 以 delivery 为执行事实；发起对应 cancel 或返回临时不一致错误，不释放 lane |

### 11.2 状态映射

| Delivery 状态 | ChatRun 投影 |
| --- | --- |
| queued | pending |
| dispatching 且无 submitted_at | pending |
| dispatching 且有 submitted_at | submitted |
| running | running |
| unknown | 保留当前非终态，delivery summary 显示 unknown |
| cancelling / cancel_unknown | 保留当前非终态，delivery summary 显示取消进度 |
| completed | completed |
| failed | failed，使用固定 error code/安全文本 |
| expired | failed，`queue_expired` 或 `run_timeout` |
| rejected_capacity | failed，`queue_capacity_exceeded` |
| cancelled | cancelled |

查询和 long-poll 返回前执行有界校准。Managed run 的 API revision 为 ChatRun version 与
delivery state_version 之和，响应正文和队列控制状态的变化都会推进 revision。long-poll 最长 30 秒，
每秒复查一次；不能只等待进程内 notify。

## 12. 取消

`A2aChat::cancel_run` 对关联 delivery 使用 managed cancel：

- queued：CAS `CancelRequested -> Cancelled`，不发 abort，随后投影 ChatRun Cancelled。
- dispatching/running/unknown：持久化 cancel intent，进入 Cancelling，由 worker 执行 exact abort。
- WebSocket exact abort：沿用原 connection/run scope 校验。
- Provider exact abort 不受支持：进入 CancelUnknown，不能显示为 Cancelled。
- final/error 先提交：保留真实 Completed/Failed，cancel 返回 too late。
- 重复 cancel：幂等返回当前状态，不重复 abort。

legacy ChatRun 保持原取消行为。ChatRun cleanup 只有在确认 terminal 后才注销关联资源；取消请求
本身不能提前丢弃仍可能到达的 final/error。

## 13. HTTP contract

ChatRun wire contract 升级为 v3。v2 状态枚举保持不变，不新增 `queued` ChatRun state；v3 在
submit、status 和 cancel response 中增加可选 delivery summary：

```json
{
  "delivery": {
    "delivery_id": "...",
    "message_id": "...",
    "status": "queued",
    "wait_reason": "bot_capacity",
    "state_version": 1
  }
}
```

- v3 客户端通过 `X-BCS-CHAT-VERSION: 3` 请求该字段。
- v2/未带版本客户端继续看到 pending/submitted/running/terminal，不依赖 summary。
- submit admission 成功且 delivery queued 时返回 HTTP 202、ChatRun pending。
- capacity/header rejection 已持久化时仍返回带 run ID 的 HTTP 202 和 terminal Failed snapshot，
  使客户端可以查询稳定结果；CLI 将其视为失败。
- Session 类型冲突发生在 ChatRun 创建前，返回 HTTP 409、固定错误 `session_type_conflict`。
- 存储结果不明确时返回 5xx，不伪造未创建或已接纳结论。

API 不返回正文预览、organization code、route Header、staff identity 或 semantic projection。

## 14. bcs-cli 适配

现有 `bcs-cli chat` 继续调用 async submit + poll。

### 14.1 detach

`--detach` 在 durable admission 后立即返回，不再等待 ChatRun Running：

- queued/dispatching/running/completed：退出码 0。
- rejected_capacity/failed/expired/cancelled：退出码 1。
- submission transport error 且无 run ID：保持 `submit_indeterminate`。

需要等待实际启动的调用方使用新增 `--wait-until running`；该模式复用现有
`chat_poll_run_until_running`。默认 blocking 模式继续等待 terminal，客户端本地 timeout 包含
排队等待时间，但不会取消服务端 run。

### 14.2 输出

JSON stdout 继续只输出一个对象；早期日志和 TTY 进度写 stderr。新增可选字段：

```json
{
  "submitted": true,
  "delivered": false,
  "run_id": "...",
  "session_id": "...",
  "state": "pending",
  "delivery_status": "queued",
  "wait_reason": "bot_capacity"
}
```

`submitted=true` 表示获得稳定 run ID；`delivered=true` 只表示已经 Running 或 Completed。
人类输出使用 `Queued`、`Running`、`Completed` 等准确状态，不把 queued 打印成 delivered。

### 14.3 run 管理

新增不破坏现有 `chat` 参数的命令：

```text
bcs-cli chat-run status --run-id <id>
bcs-cli chat-run cancel --run-id <id>
```

status 展示 ChatRun 和 delivery summary。cancel 在 CancelUnknown/Cancelling 时返回非终态并说明
仍需查询，不能输出成功取消。结构化输出保持固定字段和单对象 stdout。

## 15. 错误码与观测

新增或固定以下安全错误码：

| 错误码 | 场景 |
| --- | --- |
| `session_type_conflict` | Session ID 已归属另一类型 |
| `session_registry_missing` | admission 引用的 Session registry 行不存在 |
| `session_registry_invalid` | registry 类型或序号状态与 admission flow 不一致 |
| `queue_capacity_exceeded` | target queued 数达到上限 |
| `queue_expired` | 发送前 queue TTL 到期 |
| `run_timeout` | run 绝对 deadline 到期 |
| `queue_draining` | policy 关闭但 lane 仍有未完成 delivery |
| `exact_abort_not_supported` | Provider 无法证明精确 abort |

日志和指标沿用固定 `flow_kind=direct_a2a`、状态和 client kind 标签，不记录正文、Session 原始
内容以外的用户数据、organization code、Header 值、token、Cookie 或完整 projection。

队列统计自动包含 direct_a2a。Session 类型冲突使用固定错误码；恢复和投影错误必须向调用方或清理任务传播。
wait_reason 仍来自公共 scheduler，delivery 状态指标自动包含 `flow_kind=direct_a2a`。

## 16. 契约传播与预计修改范围

| 边界 | 变更 |
| --- | --- |
| bcs-domain | DirectA2a visibility、既有 DeliveryFlowKind 进入 ready 使用 |
| bcs-service-api | Session registry port、ChatRun delivery 关联、持久 checkpoint CAS 和有界恢复分页 |
| bcs-session-store | registry claim、Group Session 同事务认领、Direct Session ensure |
| bcs-message-store | Direct sequence allocation、empty-group admission、run ID 注入、迁移与 conformance |
| bcs-message-flow | admission、Direct preparation、terminal、cancel、drain guard、ChatRun reconcile |
| bcs-chat-run-store | 新关联列、CAS/scan/recovery |
| bcs-config-api | manages_direct_a2a、scheduler/readiness 校验 |
| bootstrap/bcs | wiring、启动一致性检查、ready flow、scheduler preparation dispatch |
| bcs-protocol / bcs-http | chat v3 optional delivery summary、409 映射 |
| bcs-cli | detach、wait-until、status/cancel、结构化输出 |
| docs | message-delivery API、配置示例、回滚和 Direct A2A ready 状态 |

现有 Plugin API 不增加业务语义；DDL 继续由 migration 管理，store 通过 `bcs-db-api` 执行。

## 17. 验收标准

### 17.1 Session 归属和迁移

- Memory、迁移后 SQLite、MySQL/OceanBase 验证同 env 同 Session ID 只能有一种 registry 类型。
- Group 与 Direct 并发认领时恰好一个成功，另一个得到 `session_type_conflict`。
- 同类型并发 ensure 幂等返回同一 Session。
- 不同 env 可以使用相同 Session ID。
- 现有 Group Session 全量回填，缺失/错配会阻止 Direct readiness。
- Group Session 新建、回滚和失败事务不会留下 registry/session 半提交。
- Direct ensure 后 admission 失败允许留下 `current_msg_seq=0` registry 行，不产生 message/delivery。

### 17.2 admission 和调度

- Direct request 原子写 message、sequence、delivery；任一步失败全部回滚。
- ChatRun、message 和 delivery 使用相同 canonical run ID；Bot callback 的 run/request alias 必须
  唯一解析到该 run。下行 `chat.send.id` 继续使用每次发送的 attempt request ID。
- API/ChatRun/delivery/frame 使用相同 Session ID。
- 同 target/session 严格 FIFO；不同 Session 共享 Bot max_running 和 rate limit。
- max_queued 并发准入不超发，拒绝结果持久且不调用 transport。
- flow 关闭后的 lane drain 不允许 legacy 越序。
- Bot 在 admission 后离线按现有 bounded offline 规则失败，不自动重发。
- restart 将可能已发送状态恢复为 Unknown，不重复发送。

### 17.3 事件、状态和取消

- WS 与 Provider 的 ACK/delta/final/error/aborted 均关联正确 delivery 和 ChatRun。
- Direct terminal 不读取 Group、不写 Group history、不产生下游 relay。
- final 无 delta、delta 后空 final、错误带部分正文、重复 terminal 均得到稳定结果。
- queued cancel 不发 abort；active cancel 等可信确认；Provider 不支持 exact abort 时为 CancelUnknown。
- delivery/ChatRun 跨事务窗口通过查询和启动恢复收敛。
- ChatRun cleanup 不绕过 delivery 制造终态。

### 17.4 API 与 CLI

- v2 客户端忽略 delivery summary 仍可完成 submit/poll/cancel。
- v3 返回 delivery ID、状态、wait_reason 和 state_version，不泄露 projection。
- CLI detach 对 queued 立即返回 0，对 durable terminal rejection 返回 1。
- `--wait-until running`、blocking terminal、status、cancel 均覆盖 queued 和不确定状态。
- JSON stdout 始终只有一个可解析对象，进度和早期 ACK 只写 stderr。

### 17.5 建议测试入口

- 新增 `conformance_session_registry`，运行 Memory 和真实迁移 SQLite；远端数据库补充
  MySQL/OceanBase 并发认领验证。
- 新增 `conformance_queued_direct_a2a`，覆盖 admission、FIFO、容量、deadline、restart、
  policy drain、WS/Provider、terminal、cancel 和 ChatRun 校准。
- 扩展 `contract_a2a_chat`、`contract_bot_event`、`bot_chat_contract`、Provider transport contract、
  WS frame compatibility 和 bcs-cli parser/output tests。
- 运行受影响 crate 全量测试、`cargo check -p bcs --all-targets`、架构检查及 BCS Singlebox
  user-story E2E。真实 OceanBase、Provider callback/SSE 和 master 切换必须在预发验证。

## 18. 兼容性、风险和回滚

- Session registry 是持久约束。发布 B 只能回滚到已经写 registry 的发布 A。
- Direct queue 开启后，旧 binary 不认识 Direct projection。回滚前先关闭新 admission，排空或
  明确处置 Direct delivery，再切回发布 A。
- `bcs_messages` 唯一键加入 env 是放宽跨环境冲突；现有查询必须继续携带 env。
- legacy Direct A2A 首次在发布 A 后使用 Session ID 时会认领 `direct_a2a`。若同 ID 已存在
  Group Session，请求开始返回 409；这是类型唯一约束的预期行为。
- ChatRun 和 delivery 不跨仓储原子提交，依赖唯一 run ID、查询校准和恢复扫描；不能删除这些
  修复路径来简化正常流程。
- Provider exact abort 能力限制不变；CancelUnknown 可能长期占用 lane，仍需现有人工 resolve。
- 开启前必须确认 Direct A2A 的请求量和 Session ID 基数不会使 registry 无界增长；首版不自动
  删除 registry 行，后续 retention 需独立 spec，不能在本实现中顺带清理活跃身份。

## 19. 验证记录

2026-09-24：在 `codex/direct-a2a-message-queue` 上实现；开始前已 fetch 并 rebase 到
`origin/dev` 的 `893790245f`。以下为创建 PR 前的本地验证记录；本功能尚未部署。

### 已通过

- `cargo check --workspace --all-targets` 与 `cargo test --workspace --no-run`：所有目标编译通过。
- `cargo test -p bcs-session-store -p bcs-session -p bcs-message-store -p bcs-chat-run-store -p bcs-message-flow -p bcs-http -p bcs-config-api --no-fail-fast`：1,397 项通过，0 失败。
- `conformance_direct_a2a`：45 项通过（含 8 项 Direct A2A 场景及复用的迁移测试），
  覆盖 Memory/SQLite 认领冲突、FIFO、容量、权限复核、TTL、WS/Provider 取消、
  final 写入故障恢复、新缓存读回、有界恢复查询与 orphan 修复。
- SQLite 31 → 32 升级验证：Group registry 回填、原 Group 序号不变、跨 env 唯一性、
  重跑幂等、旧迁移 checksum/history 不变。
- CLI library/binary 单元测试 147 项通过。另通过实际 CLI 进程和本地 HTTP mock 验证 8 个场景：
  detach queued、容量拒绝、wait-until running、阻塞 final、旧服务器无 delivery 字段、
  status、cancel_unknown JSON 和取消未确认的人类输出。
- `git diff --check`、所有新增/修改 Rust 文件不超过 1,000 行检查通过。
  原先超限的 bootstrap、Session repository/application、CLI 文件按职责拆开；逐函数 AST
  对照未发现原函数丢失，非功能部分保持原函数体，无全局格式化。

### 未通过或未执行

- `cargo test --workspace --no-fail-fast` 的完整运行未完成：在已有 Group 列表和好友集成测试中
  出现 HTTP 超时；Group 列表用例单独重跑仍超时。`human_regress_integration` 超过 5 分钟未结束，
  已终止该测试进程；记录这些失败后停止全量运行，另行完成上面的受影响模块回归。
  这些失败没有被忽略或标记通过，其与本次修改的因果关系尚未通过 dev 基线运行确认。
- Rust 测试进程启动 CLI 子进程时被 macOS AMFI 拒绝，系统日志报告 ad-hoc 签名/未知证书链；
  因而 CLI 集成测试整套未通过。重新签名未解决 Rust 子进程路径；同一 CLI 的正常进程启动、
  147 项单元测试和上述 8 项 HTTP smoke 验证通过。未关闭系统签名检查。
- `scripts/ci/arch-check.sh` 未通过：依赖脚本在 macOS shell 下报告 unbound variable，
  并报告已有 import/trait/conformance 违规。导入违规已逐项对照 `893790245f`，47 项均已存在，
  本次没有新增；Direct registry 的 conformance 已抽到公共 harness。完整门禁未记为通过。
- `scripts/ci/singlebox_coverage.sh` 已尝试：先修正本次运行的 `LOG_FORMAT` 环境值，
  再为 BCS 选择空闲端口；插桩 BCS 启动成功，但另一 worktree 的 OpenClaw 占用
  30001/30011/30021/30031/30041，阻止完整栈启动。没有停止其他 worktree 的进程。
  因未生成完整报告，Singlebox 覆盖率与 artifact verifier 尚未通过。
- 当前机器没有可用 MySQL/OceanBase 实例或容器运行时，完整 MySQL 迁移链、真实 Provider
  callback/SSE、master 切换与生产负载尚未验证。MySQL 静态 DDL 检查不能替代该发布门禁。

启用生产 Direct queue 前仍需完成上述真实数据库与集成验收。

## 20. 实现与数据库访问成本

- MySQL 迁移为 `031_session_registry.sql`，SQLite 为 `032_session_registry.sql`；历史迁移保持不变。
- Direct preparation 与终态处理通过 `BcsMessageFlow` 内部的 `A2aChat` 引用组合，
  使用 weak owner 绑定统一队列，没有新增跨服务的协议或重复 lifecycle。
- 每次 Direct ensure：一个两步事务（幂等 claim + primary read）。
- Admission 沿用 bot/session writer lock、queued count、sender 幂等查询和序号 CAS；
  Direct 单 target，消息、delivery、序号在同一事务提交，不跨网络持有 DB 事务。
- Managed response checkpoint 每次内容变化写一次 SQL CAS 并读回，保证 final 内容在 delivery
  结算前已经落盘；已有 legacy 流仍使用原来的 streaming overlay。单 run 正文继续限制为 1 MiB。
- 每次状态读取执行 ChatRun/关联 delivery 查询；发生状态变化时执行 checkpoint CAS。
  长轮询间隔为 1 秒、上限 30 秒，不进行 100ms 数据库轮询。
- 定时恢复沿用 10 秒 cleanup tick，以 run ID cursor 每批最多 8 条；一次 ChatRun 分页查询、
  一次 delivery ID batch 查询，只有需要修复的记录才执行 CAS，不逐 run 查询 delivery。
  满批返回正文的内存上界约 8 MiB。扫描失败向上返回，由下一个 cleanup tick 重试。
  LIMIT 约束返回行数；历史终态过滤的实际扫描量仍需真实 MySQL 执行计划和负载验证。
- 启动、master 接管和 Direct policy 启用检查 registry 类型/序号、Group 认领、环境唯一索引以及
  ChatRun 关联列；旧的 Group registry 占位允许保留。
- 存储短暂故障只重试稳定的 checkpoint 或 delivery CAS，沿用 100ms 至 5 秒的指数退避；
  不重放网络 send。测试覆盖故障注入后 final 落盘及新缓存实例读取。
