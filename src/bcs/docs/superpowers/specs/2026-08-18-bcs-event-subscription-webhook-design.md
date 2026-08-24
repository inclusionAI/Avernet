# BCS 通用事件订阅与 Webhook 投递设计

- 日期：2026-08-18
- 状态：MVP 实现对照稿
- 范围：BCS 事件产生、订阅管理、Webhook 投递、顺序保证、可靠性、安全与运维
- 主要新增 Contract：Event Subscription Service API、Event Store Repo Port、Event Delivery Port、OpenAPI V1 HTTP Contract
- 兼容性：新增能力；不直接修改现有 `bcs-callback` 的服务调用完成回调语义

## 1. 摘要

BCS 需要在群、会话、任务、消息和状态机等业务状态发生变化后，把稳定、可审计的事件异步投递给外部系统。
本设计不把该能力限定为“群回调”，而是引入通用的 **BCS Event**、**Event Subscription** 和
**Event Sink**：MVP 只提供 Webhook，事件生产方不依赖 HTTP 投递实现。

核心决策如下：

1. 业务操作只负责提交业务状态和事件记录，不在请求链路同步调用外部 Webhook。
2. 事件记录通过 Transactional Outbox 与对应业务状态原子提交；外部投递采用 at-least-once。
3. MVP 只公开 group scope；群创建内联配置和独立创建接口使用同一 Subscription Contract。
4. 群级订阅固定包含其 session、task、message 和 state-machine 子资源事件；所有 MVP Event 必须有
   `group_id`。
5. 事件使用稳定 `event_id` 去重，并在确定的事件流中使用单调递增 `sequence` 表达顺序。
6. 状态机事件默认并强制按 `run_id` 严格有序投递；前一个事件未成功时，后续事件不得越过。
7. 聊天消息只有在逻辑消息成功持久化后才产生 `message.created`。当前静默吞掉消息写失败的行为必须改造。
8. Webhook 投递复用现有出站 URL 安全能力，每次尝试都重新执行 SSRF 防护且不跟随重定向。MVP 面向内部服务，
   不提供 Subscription 级鉴权或签名字段。
9. 现有 ManagerWorker Task Ledger 是否持久化不属于本设计范围；事件 Contract 不依赖其未来存储形态。

## 2. 背景与问题

当前 BCS 已存在面向 service invocation session 完成场景的 `bcs-callback`，但它不是通用业务事件系统：

- 回调配置挂在特定 service spec 上，无法自然覆盖群、成员、消息、任务和状态机节点事件；
- 投递通过进程内异步任务执行，没有通用的持久化 Outbox、统一重试、死信和回放模型；
- Payload 和投递状态围绕 session completion 设计，不适合作为未来所有 BCS 资源的事件 Contract；
- 事件顺序、去重、版本演进和订阅权限没有统一语义。

消息链路还存在一个与事件可靠性直接相关的问题：
`try_persist_group_message` 在 `append_message` 失败时只记录 warning，调用方继续执行。该行为使 API 或 Bot
投递可能成功，但历史消息和对应 Hook 永久缺失。MySQL 消息存储当前还把 session sequence 分配和 message
INSERT 放在两个事务边界内，INSERT 失败时会留下 sequence 空洞。

因此，本设计同时规定消息事件的持久化前置条件和渐进式修复路径。Hook 不能建立在 best-effort 的消息历史之上。

## 3. 目标

### 3.1 功能目标

- 创建群时可以配置一个或多个事件订阅和 Webhook 地址。
- 可以脱离群创建流程，独立创建、查询、更新、禁用、测试和删除订阅。
- 支持以下首版事件族：
  - group、group participant；
  - session、session participant；
  - ManagerWorker task；
  - state-machine run、node；
  - 自由聊天和 ManagerWorker 群的逻辑聊天消息。
- 支持一次性状态机和群预配置状态机，使用同一事件类型，通过 `run_mode` 区分。
- 支持重试、死信、回放、禁用和审计。
- 支持单实例和多实例 BCS，服务重启不得丢失已提交但尚未成功投递的事件。
- 事件生产、持久化和 HTTP 投递保持边界分离。

### 3.2 可靠性目标

- Eventing 启用时，已返回成功的受支持业务状态转换最终必须存在对应事件记录。
- 事件投递采用 at-least-once，不承诺 exactly-once。
- 同一严格顺序流中，接收方不会在前序事件未成功响应时收到后序事件。
- 重复请求或幂等业务调用没有造成新状态转换时，不产生重复业务事件。
- 每个订阅独立重试；一个订阅失败不影响业务请求，也不阻塞其他订阅。

### 3.3 安全目标

- 订阅只能由对 scope 具有管理权限的身份创建或修改。
- Webhook 地址不能用于访问环回、链路本地、私网、云元数据或其他被策略禁止的目标。
- Webhook 完整 URL 不出现在查询响应、普通日志或错误信息中。
- 默认事件 Payload 不包含消息全文、思考过程、工具参数、临时分享 token 或内部凭据。

## 4. 非目标

以下内容不在首版实现范围：

- 不解决 ManagerWorker Task Ledger 的持久化；
- 不提供 Kafka、Pulsar、NATS 等 MQ Sink，也不预置对应公共枚举、配置字段或执行分支；
- 不提供任意表达式过滤、JSONPath 或用户上传脚本；
- 不保证不同事件流、不同 run、不同 session 或不同订阅之间的全局顺序；
- 不承诺外部接收方在返回 2xx 后的内部异步处理顺序；
- 不发送流式 token delta、thinking、原始 tool arguments 或底层 Bot transport frame；
- 不对事件历史做创建订阅前的自动回填；
- 不用 Webhook 成功与否决定原业务操作是否成功；
- 不在本设计中移除现有 `bcs-callback`；
- 不提供跨 BCS 部署的全局 event sequence。

## 5. 术语

| 术语 | 定义 |
| --- | --- |
| BCS Event | 已发生业务事实的不可变记录，例如 `session.completed`。本文简称 Event。 |
| Event Producer | 完成业务状态转换并产生 Event 的 application/core use case。 |
| Event Subscription | 声明 scope、事件过滤器、Payload 策略、顺序策略和 Sink 的持久化配置。 |
| Event Sink | BCS 主动投递 Event 的目标能力；首版只有 Webhook。 |
| Webhook | 通过出站 HTTP POST 接收 Event 的 Sink 实现。 |
| Event Stream | 具有稳定 `stream_key` 和单调 `sequence` 的有序事件序列。 |
| Fanout Target | Event 提交时固化的 Subscription revision 匹配结果；由异步 worker materialize 为 Delivery。 |
| Delivery | 某个 Event 向某个 Subscription 的一次逻辑投递，可能包含多次 Attempt。 |
| Attempt | Delivery 的一次具体 HTTP 请求。 |
| Outbox | 与业务状态一起持久化、等待异步分发的 Event 记录。 |
| DLQ | 达到重试上限后进入的 dead-letter 状态。 |
| Subject | Event 直接描述的资源，例如某个 node 或 message。 |
| Scope | Subscription 的授权和筛选边界，也是 Event 的资源归属链。 |

“Hook”仅作为用户侧通俗称呼。公开 Contract、表名和代码 Contract 使用 Event Subscription / Event
Delivery，避免把事件模型绑定到 HTTP 回调。

## 6. 总体架构

```text
HTTP / WS / CLI delivery adapters
              |
              v
bcs_service_api::application use cases
              |
              +------ business state transition
              |              |
              |              v
              |       business Repo Port
              |
              +------ EventRecorder contract
                             |
                   same transaction / UoW
                             |
                             v
                 bcs_events Outbox
              + bcs_event_fanout_targets
                             |
                    Event Fanout / Dispatcher
                             |
               +-------------+-------------+
               |                           |
               v                           v
      bcs_event_deliveries        future sink deliveries
               |
               v
       EventDeliveryPort
               |
               v
       Webhook HTTP adapter
```

### 6.1 分层与 Contract 分类

新增能力必须遵循 BCS 既有 application/core/port 分层：

- `bcs_service_api::application::v1::event_subscription`
  - **Service API**；
  - 供 HTTP delivery adapter 调用；
  - 提供订阅管理、测试、投递查询、回放和 skip use case。
- `bcs_service_api::port::event_recording::EventRecorderPort`
  - 跨 use case 的 transport-neutral Event extension point；
  - 只能在业务 mutation 的 Unit of Work 内记录 Event，不能自行 post-commit；
  - Contract 必须让调用者观察 record failure，禁止 fire-and-forget/noop success。
- `bcs_service_api::port::repo::event`
  - 持久化 Repo Port；
  - 定义 Event、Subscription、scope epoch、fanout target、Delivery 和 Attempt 的存储语义；
  - Memory 和 MySQL 实现必须通过同一 conformance suite。
- `bcs_service_api::port::event_delivery::EventDeliveryPort`
  - 出站业务 Port；
  - Event Dispatcher 调用它投递与 HTTP 无关的 `EventDeliveryCommand`；
  - Webhook 实现负责 HTTP 发送和响应映射。
- `services/bcs-eventing`
  - 实现订阅 application service、事件 fanout、调度、重试和回放策略；
  - 不导入 HTTP framework 类型。
- `services/bcs-event-store`
  - 实现 Memory/MySQL Repo Port，拥有 SQL、租约和表映射。
- `external-clients/bcs-webhook-client`
  - 实现 `EventDeliveryPort`；
  - 复用出站 URL guard；
  - 不拥有事件筛选、重试或顺序业务策略。
- `bootstrap/bcs`
  - 读取并验证配置；
  - 选择具体 Store 和 Webhook Delivery 实现；
  - 把 Dispatcher 注册进 `ServiceLifecycle`。

HTTP DTO、状态码和 URL 不进入 core service。业务 use case 不直接依赖具体 Webhook client。

### 6.2 Event Producer 集成方式

事件是跨多个 use case 的横切能力，生产方通过声明的 Event Recorder extension point 集成。生产方只构造
transport-neutral 的 `NewEvent`，不得：

- 读取订阅地址；
- 执行 HTTP；
- 决定重试时间；
- 根据订阅是否存在改变业务行为。

Event Recorder 必须接受调用方提供的幂等键、subject、scope、stream、actor、trace、causation 和 data，
并在存储层完成 event ID、sequence、匹配 Subscription revision 的事务内快照和持久化约束。Producer 不读取
endpoint，target snapshot 由 Event Store 在 Unit of Work 内完成。

### 6.3 生命周期

Dispatcher 实现 `ServiceLifecycle`：

1. `initialize` 校验 store 和 delivery port 可用；
2. 启动 fanout、delivery 和 lease recovery worker；
3. `shutdown` 停止领取新任务；
4. 在配置的 drain timeout 内等待在途请求结束；
5. 释放或等待投递 lease 到期，保证其他实例可继续处理。

初始化失败应阻止宣称 Eventing 可用，但不得通过在业务层动态选择 Noop 实现隐藏配置错误。

## 7. 事件一致性与产生规则

### 7.1 Post-commit 语义

所有外部 Event 表示已提交事实：

- 业务事务回滚时不得产生可投递 Event；
- Event 不得早于对应业务状态对正常读 API 可见；
- Event 的 `occurred_at` 是业务转换时间；
- Event 的 `recorded_at` 是 Outbox 提交时间；
- Webhook 的 `delivered_at` 不属于 Event 本身，只属于 Delivery。

### 7.2 Transactional Outbox

单资源状态转换必须在同一数据库事务内完成：

```text
BEGIN
  mutate business row(s)
  allocate stream sequence
  lock Event scope chain for subscription snapshot
  insert bcs_events
  snapshot matching active subscription revisions
  insert bcs_event_fanout_targets
COMMIT
```

如果业务操作使用 Memory Store，则同一个 scope-local Unit of Work 锁临界区必须同时更新业务状态、Memory Event
Store 和 fanout target snapshot；不同 scope 不得无条件共享一个 env 全局 mutex。测试不能用
“先更新业务 Map，再异步写 Event”的弱化实现冒充 conformance。

如果现有 Repo Port 无法让业务写和 Event insert 共享事务，实现阶段必须引入显式 Unit of Work 或为该 mutation
增加原子 transition store contract。禁止用以下方式替代：

- 状态提交后 `tokio::spawn` 写 Event；
- 忽略 Event insert 失败并返回业务成功；
- 依靠日志补偿；
- 让 HTTP adapter 在响应成功后补写 Event。

### 7.3 复合资源创建

建群会依次创建 group、初始 session，并可能配置 state-machine runtime。`group.created` 的定义是“完整群资源
已成功 provision”，而不是“group row 已插入”。实现必须引入可恢复的 provisioning/finalization 边界：

1. 预分配 `group_id`；
2. 建立 provisional group scope epoch，校验并创建 group scope 的 subscription；
3. 建立 group 和初始 session；
4. 如果是自定义协作群，配置所需 runtime；
5. 在 finalization transaction 中把群标记为可用，并按确定顺序写入 `group.created` 和初始
   `session.created` Event；
6. 失败时执行现有补偿，并保证没有 created Event 进入 available 状态；
7. 进程在步骤 2～5 之间退出时，由 provisioning reconciler 继续完成或回滚，不能永久留下孤儿 pending
   Subscription，或“业务成功但无 Event”的 provisioning Group。

reconciler 在服务接流量前执行一次无等待恢复，并在运行期周期重试。运行期必须使用安全时间窗口，避免把仍在执行的
建群请求误判为孤儿；缺少 Group 的过期 pending 集合自动取消，结构完整的 provisioning Group 原子完成，缺少初始
Session 或确定性 runtime 配置的 Group 执行补偿，临时存储错误保留到下一轮重试。

同一创建请求内的内联订阅必须先于 finalization 生效，因此能够收到本次 `group.created`。仅做 URL 安全和配置
校验，不在建群同步链路发网络探测请求。

初始 `session.created` 必须把同批 `group.created` 写为 `causation_event_id`。如果某个 Subscription 同时匹配
两者，Dispatcher 必须先成功投递 `group.created`；如果它只匹配 `session.created`，则不因为未创建的 parent
Delivery 而阻塞。

### 7.4 幂等

每个生产点必须提供稳定 `producer_key`，Event Store 对 `(producer, producer_key, event_type)` 建唯一约束。
推荐 producer key：

| 场景 | producer key |
| --- | --- |
| create group | `group:{group_id}:created` |
| create session | `session:{session_id}:created` |
| participant add/remove | mutation request id，或资源版本 + actor id + operation |
| task assign | `task:{task_id}:assigned` |
| task terminal | `task:{task_id}:{terminal_status}` |
| message created | `message:{logical_message_id}:created` |
| run transition | `run:{run_id}:{status}` |
| node transition | `run:{run_id}:node:{node_id}:attempt:{attempt}:{status}` |

同一请求重试并返回既有资源时，Event Store 返回既有 Event，不分配新 sequence，不创建重复 Delivery。

### 7.5 无变化不发事件

以下情况不产生新 Event：

- 重复 complete 已完成的 session；
- 重复添加已经具有相同角色和模式的 participant；
- 删除不存在的 participant 且业务 Contract 把它定义为幂等成功；
- PATCH 后字段值未变化；
- 重放已处理的 Bot terminal event；
- 幂等 client message 返回既有 message。

如果重复请求本身被业务 Contract 定义为冲突或错误，则照常返回错误，也不产生 Event。

### 7.6 ManagerWorker Task Ledger 过渡边界

当前 ManagerWorker Task Ledger 是 process-local runtime state，本设计不把它升级为持久化业务表，也不新增 durable
inbox：

- `task.assigned` 使用稳定 task ID 先持久化 Event，再注册内存 Ledger；
- terminal callback 在把 Ledger 标记为 replied 前持久化 `task.completed`；
- Event 写失败不得把 Ledger 提前推进到 terminal，处理链向上返回错误；
- Event 使用稳定 producer key，使同一进程内的 callback 重试不会产生重复 Event；
- 进程在 Ledger 状态和 Event 之间崩溃时的 Task 恢复不在 MVP 保证范围。

因此，“业务状态与 Event 原子提交”只适用于已有持久化事务边界的资源。Task Ledger 的调度恢复、终态恢复和重启后
继续协作由其后续持久化方案解决，本期不为它预置 inbox、恢复表或额外公共 Contract。

## 8. Event Envelope Contract

### 8.1 JSON 结构

```json
{
  "spec_version": "1.0",
  "event_id": "evt_01J...",
  "event_type": "state_machine.node.completed",
  "schema_version": "1.0",
  "source": "bcs",
  "occurred_at": "2026-08-18T10:00:00.123Z",
  "recorded_at": "2026-08-18T10:00:00.130Z",
  "subject": {
    "type": "state_machine.node",
    "id": "review"
  },
  "scope": {
    "group_id": "group-1",
    "session_id": "session-1",
    "run_id": "run-1"
  },
  "stream": {
    "key": "state-machine-run:run-1",
    "sequence": 12
  },
  "actor": {
    "type": "bot",
    "id": "bot-1",
    "display_name": "Reviewer"
  },
  "correlation_id": "corr_01J...",
  "causation_event_id": "evt_01H...",
  "trace_id": "trace-1",
  "data": {}
}
```

### 8.2 字段约束

| 字段 | 必填 | 语义 |
| --- | --- | --- |
| `spec_version` | 是 | Envelope 版本；首版固定 `1.0`。 |
| `event_id` | 是 | 全局唯一且在所有重试、回放中保持不变。 |
| `event_type` | 是 | 稳定事件名。 |
| `schema_version` | 是 | 当前 event type 的 data schema 版本。 |
| `source` | 是 | 事件来源；本设计固定 `bcs`，未来可扩展部署标识字段。 |
| `occurred_at` | 是 | UTC RFC3339，毫秒精度。 |
| `recorded_at` | 是 | Event Outbox 提交时间。 |
| `subject.type` | 是 | 被描述资源类型。 |
| `subject.id` | 是 | 被描述资源 ID；对外按 opaque string 处理。 |
| `scope` | 是 | 资源归属链；不适用字段省略，不传空字符串。 |
| `stream.key` | 是 | 顺序分区键。 |
| `stream.sequence` | 是 | 从 1 开始的单调递增整数。 |
| `actor` | 否 | 触发状态转换的 Human/Bot/App/System。系统恢复任务可使用 `system`。 |
| `correlation_id` | 否 | 一次业务操作或协作运行的关联 ID。 |
| `causation_event_id` | 否 | 直接导致当前 Event 的前序 Event；同一 Subscription 同时匹配两者时形成投递依赖。 |
| `trace_id` | 否 | 可安全对外暴露的 trace ID，不包含内部拓扑。 |
| `data` | 是 | event type 专属数据；允许空 object，不允许 `null`。 |

`causation_event_id` 必须引用同一 env 中此前已经提交的 Event，或同一事务内先插入且将原子提交的 Event。Event
Store 必须拒绝自引用、尚未插入的未来引用和回边；按插入先后建立边，因此因果图无环。它表达直接因果关系，
不用于把所有相关 Event 串成全局链。

### 8.3 Scope 规则

MVP Scope 归属链如下：

```text
group -> session -> task
                \-> run
```

- 所有 Event 必须有 `group_id`；
- session Event 必须有 `session_id`；
- task Event 必须有 `task_id`，历史调用没有独立 session 时允许省略 `session_id`；
- state-machine Event 必须有 `run_id` 和 `session_id`；
- subject 不必等于最窄 scope，例如 node Event 的 subject 是 node，最窄 scope 是 run；
- scope ID 均为不透明字符串，接收方不得解析其格式。

### 8.4 Actor 规则

`actor.type` 只允许：

- `human`
- `bot`
- `app`
- `system`

Actor 是实际获授权并执行 mutation 的身份，不是 HTTP body 中声明的任意 sender。自动恢复、超时调度和
reconciler 使用 `system`。如果操作由 Human 代表自己拥有的 Bot 发起，actor 记录 application use case 最终选择
的 effective actor，并可在 event data 中增加业务所需的 `on_behalf_of`，但不得混淆认证身份。

### 8.5 大小限制

- 序列化后的 Event body 上限默认 256 KiB；
- 单个字符串字段默认上限 64 KiB；
- 超限内容不得静默截断为看似完整的数据；
- 可截断字段必须同时提供 `truncated: true` 和原始字节数；
- 不可截断字段超限时业务 mutation 应在提交前失败，或只产生明确的 metadata-only Event；
- 首版不提供事件 Payload 外部下载地址，避免再引入临时 token 生命周期。

## 9. Event Stream 与顺序

### 9.1 Stream 映射

| Event family | stream key | 顺序范围 |
| --- | --- | --- |
| `group.*`、`group.participant.*` | `group:{group_id}` | 同一 group 生命周期和正式成员变更 |
| `session.*`、`session.participant.*` | `session:{session_id}` | 同一 session 生命周期和成员变更 |
| `message.*` | `session:{session_id}` | 同一 session 逻辑消息持久化顺序 |
| `task.*` | `task:{task_id}` | 同一 ManagerWorker 子任务生命周期 |
| `state_machine.*` | `state-machine-run:{run_id}` | 同一状态机 run 的全部 run/node/input 事件 |

跨 stream 不承诺总顺序。唯一例外是显式 `causation_event_id`：同一 Subscription 同时匹配 cause 和 effect
时，effect Delivery 必须等待 cause Delivery 成功或被管理员显式 skip。`task.completed` 和关联的
`message.created` 位于不同 stream 时，只有 producer 显式设置 causation 才有这一局部顺序；否则接收方只能通过
`correlation_id` 和 `task_id` 建立关系，不能依赖到达先后。

### 9.2 Sequence 分配

- sequence 在 Event 与业务状态的事务内分配；
- 同一个 stream 不得重复；
- 事务回滚不得消耗对外可见 sequence；
- Event Store 对 `(stream_key, sequence)` 建唯一约束；
- 时间戳不用于排序；
- 多实例并发生产时由存储层串行化 sequence 分配。

### 9.3 严格顺序投递

Subscription 默认 `ordering.mode = strict_per_stream`。严格模式下，同一个
`(subscription_id, stream_key)` 形成独立 delivery lane：

1. 仅 sequence 最小且尚未终结的 Delivery 可以被领取；
2. 前序 Event 收到 2xx 后，才允许领取后序 Event；
3. 前序 Event 处于 retry wait 时，后序 Event 保持 pending；
4. 前序 Event 进入 DLQ 时，该 lane 进入 blocked；
5. 未解决的 DLQ blocker 只允许其 replay replacement 越过 blocker 被领取，普通后序 Delivery 仍等待；replacement
   成功或管理员显式 skip 原 Delivery 后，lane 才恢复；
6. Delivery 存在 cross-stream causation target 时，前置 target 对应 Delivery 未成功或 skip 前不得领取；
7. 其他无因果关系的 stream 和其他 subscription 不受影响。

因果依赖按 `(subscription_id, subscription_revision)` 解析，不能引用另一个 revision 中已经 cancelled、DLQ 或
replay 的不确定 Delivery。effect target 创建时：

1. 如果同一 revision 已有 cause target，则复用它；
2. 如果没有，但该 revision 的 filter 也匹配 cause Event，则创建 deterministic `causal_prerequisite` target；
3. 如果该 revision 不匹配 cause Event，则不建立投递依赖；
4. cause target 或其 Delivery 被策略取消时，依赖它且尚未开始的 effect target/Delivery 及其依赖后代必须在同一
   事务中传递取消；
5. effect 被管理员 replay 到新 revision 时，重新执行以上解析，不能继续引用旧 revision 的 cause Delivery。

`causal_prerequisite` 是保证局部因果顺序的必要补投，不等同于创建订阅前的通用历史 backfill。它和正常 target
使用不同的幂等 purpose；同一 revision、Event 和 purpose 最多创建一次。

MVP 只有 `strict_per_stream`，请求不能覆盖 ordering；不存在同一 stream 越过失败事件的模式。

### 9.4 状态机顺序语义

顺序状态机必须产生并投递如下可观察顺序：

```text
run.created
run.started
node-A.started
node-A.completed
node-B.started
node-B.retry_scheduled
node-B.started
node-B.completed
run.completed
```

如果 `node-A.completed` 第一次投递返回 500，则 `node-B.started` 不得先到达该订阅。

并行状态机也使用 run 级总序列。并发节点事件的总顺序定义为 Event Store 的事务提交顺序；除此之外必须满足：

- 同一 node attempt 的 `started` 早于其 terminal Event；
- `retry_scheduled` 早于下一 attempt 的 `started`；
- 所有必要终态节点 Event 早于 `run.completed`；
- `run.created` 早于 `run.started`；
- run terminal Event 是该 run 的最后一个业务生命周期 Event。

BCS 保证 HTTP 请求按上述规则开始，并在前一个请求成功返回后才开始下一个。接收方如果返回 2xx 后自行放入
并发队列，BCS 不对其内部处理完成顺序负责。

## 10. Event Subscription Contract

### 10.1 Subscription JSON

```json
{
  "subscription_id": "sub_01J...",
  "name": "workflow-observer",
  "scope": {
    "type": "group",
    "id": "group-1"
  },
  "include_descendants": true,
  "event_filters": [
    "group.*",
    "session.*",
    "task.*",
    "message.created",
    "state_machine.*"
  ],
  "payload": {
    "mode": "metadata_only"
  },
  "ordering": {
    "mode": "strict_per_stream"
  },
  "sink": {
    "type": "webhook",
    "url": "https://example.com/bcs/events"
  },
  "status": "active",
  "created_at": "2026-08-18T10:00:00Z",
  "updated_at": "2026-08-18T10:00:00Z"
}
```

`sink.url` 仅是写入字段。查询响应中的 `sink.endpoint` 只返回 `scheme`、`host` 和不可逆的 `path_hash`，任何读接口
都不得返回完整 URL 或 path。MVP 不接受 `sink.auth`；DTO 使用严格字段校验，携带该字段的请求返回
`invalid_request`。如内部部署需要服务身份鉴别，应由网关或 service mesh 在基础设施层统一完成，不进入
Subscription Contract。

### 10.2 Scope 类型

MVP 只允许 `{ "type": "group", "id": "..." }`。`id` 必填，调用者必须是 group creator/driver 或其他已获
群管理权限的身份。`include_descendants` 在响应中固定为 `true`，不作为创建或 PATCH 输入；它表示匹配该 group
下的 session、task、message 和 run Event，不能跨 group。

### 10.3 Event filter 语法

- 精确匹配：`message.created`；
- 后缀通配：`state_machine.*`；
- `*` 只能位于最后一个 segment 且独占该 segment；
- 不支持中间通配、正则、否定表达式和 data 字段判断；
- 名称只允许小写 ASCII、数字、下划线和 `.`；
- 单个 Subscription 默认最多 64 个 filter；
- filter 必须命中当前 Event catalog；精确类型和 family wildcard 未命中当前 Catalog 时都拒绝，避免拼写错误
  静默无效。

Family wildcard 是选择当前已注册事件族的简写。新增公共 Event 是否应进入既有 wildcard Subscription 必须在该
Event 的兼容性评审中明确决定，不能把 wildcard 当作预先授权的未来事件入口。

更新 filter 只影响更新完成之后产生的 Event，不重写既有 Delivery。

### 10.4 Payload mode

首版支持：

- `metadata_only`：默认；message 不含正文，状态机不含完整 input/output/artifact；
- `full`：包含该 Event catalog 明确允许的正文类字段，仍执行敏感字段剔除和大小限制。

`full` 只允许 group scope 的管理者配置。

Payload mode 在 Event fanout 时投影，不改变 canonical Event 的事实字段。Delivery 必须记录所用 subscription
revision，保证重试时 Payload 不因订阅后来更新而变化。

### 10.5 状态机

Subscription 状态：

- `pending`：创建中，尚不能匹配 Event；
- `active`：正常匹配和投递；
- `disabled`：不匹配新 Event，停止未开始的投递；
- `deleted`：软删除，仅保留审计和历史 Delivery。

状态转换：

```text
pending -> active
pending -> deleted
active  -> disabled
active  -> deleted
disabled -> active
disabled -> deleted
```

删除和禁用后，尚未 materialize 的 target 与尚未开始或等待重试的 Delivery 变为 `cancelled`；已经发出的 HTTP
请求可能完成，其结果照常记录。重新启用不自动补投禁用期间发生的 Event。

### 10.6 Revision

以下变更创建新的 immutable subscription revision：

- filter；
- payload mode；
- endpoint URL；
- timeout override。

每个 fanout target 和 Delivery 固定引用产生时匹配的 revision。为避免向已废弃 endpoint 继续发送，修改 URL、
把 payload 从 full 收紧为 metadata-only、禁用或删除 Subscription 时，旧 revision
尚未 materialize 的 target 与尚未开始或等待重试的 Delivery 默认取消。管理员可以在新 revision 下显式 replay
原 Event，此时保留 `event_id`，生成新的 target 和 `delivery_id`。

只修改 filter/timeout 时，已经匹配的旧 target/Delivery 继续使用旧 revision，新 Event 使用新 revision。

### 10.7 数量限制

默认限制通过 validated configuration 提供：

- 每个 group：10 个 active Subscription；
- 每个 Subscription：64 个 filter；
- URL：2048 bytes；
- name：128 UTF-8 characters；
- metadata keys 不允许由调用方任意注入到 HTTP Header。

达到限制返回明确的 resource limit error，不隐式覆盖旧订阅。

## 11. 群创建内联配置

OpenAPI V1 `CreateGroupRequest` 的 Normal 和 DM 两种 variant 都增加可选字段：

```json
{
  "event_subscriptions": [
    {
      "name": "group-events",
      "event_filters": ["group.*", "session.*", "message.created"],
      "payload": { "mode": "metadata_only" },
      "sink": {
        "type": "webhook",
        "url": "https://example.com/bcs/events"
      }
    }
  ]
}
```

内联语义：

- scope 固定为即将创建的 group，调用方不能在 body 中覆盖；
- scope 和 `include_descendants=true` 由服务端固定；
- 与群创建使用同一授权身份；
- 任何订阅配置校验失败时，群创建在产生业务资源前失败；
- 地址只做静态和安全解析校验，不同步调用远端；
- 群 provisioning 失败时，pending subscription 必须一起删除或进入可自动清理的 cancelled 状态；
- 成功响应返回 group，同时返回已创建 subscription 的脱敏摘要；
- 内联 subscription 能收到本次 `group.created` 和初始 `session.created`；
- 没有该字段时现有请求和响应保持兼容。

Legacy `POST /groups` 在兼容期内也接受同形的 `event_subscriptions` 字段，并复用上述原子 provisioning
能力；未传该字段时继续走原 legacy 创建路径。携带内联 Subscription 时 Group ID 必须由服务端生成，且首版不与
`service_spec`、延迟启动 State Machine 组合使用。

## 12. OpenAPI V1 HTTP Contract

路由挂载在公共 OpenAPI 前缀 `/openapi/v1/collaboration` 下：

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/event-subscriptions` | 创建独立 Subscription |
| `GET` | `/event-subscriptions` | 按 scope/status 分页查询 |
| `GET` | `/event-subscriptions/{id}` | 获取脱敏详情 |
| `PATCH` | `/event-subscriptions/{id}` | 创建新 revision 或改变状态 |
| `DELETE` | `/event-subscriptions/{id}` | 软删除 |
| `POST` | `/event-subscriptions/{id}:test` | 发送测试 Delivery |
| `GET` | `/event-subscriptions/{id}/deliveries` | 查询投递历史 |
| `GET` | `/event-deliveries/{id}` | 查询 Delivery 和 Attempt 摘要 |
| `POST` | `/event-deliveries/{id}:replay` | 在当前 active revision 下回放原 Event |
| `POST` | `/event-deliveries/{id}:skip` | 审计后跳过 DLQ Event 并解除严格顺序 lane |

所有 JSON 响应使用现有 V1 envelope。状态约定：创建 Subscription 返回 201；查询、PATCH、DELETE、test 和 skip
返回 200；replay 成功创建 queued Delivery 后返回 202。DELETE 返回脱敏后的最终 Subscription 摘要，不使用空
204。列表接口使用 cursor pagination，默认 20、最大 100，不提供无上限查询。

### 12.1 创建请求

```json
{
  "name": "workflow-observer",
  "scope": { "type": "group", "id": "group-1" },
  "event_filters": ["state_machine.*"],
  "payload": { "mode": "metadata_only" },
  "sink": {
    "type": "webhook",
    "url": "https://example.com/bcs/events"
  }
}
```

创建返回 HTTP 201 和现有 V1 success envelope。完整 URL 只在请求中出现，不回显。

### 12.2 PATCH

PATCH 使用 `deny_unknown_fields`，只接受显式出现且非 null 的字段。以下操作使用普通字段更新：

- name；
- event filters；
- payload；
- sink URL；
- status 的 active/disabled 切换。

scope 和 subscription ID 不可修改。需要改变 scope 时创建新 Subscription。

PATCH 必须带当前 `revision` 或 `If-Match`，版本不匹配返回 409，避免并发覆盖。

### 12.3 Test

`:test` 通过相同 Webhook Delivery Port、URL guard 和 timeout 发送：

```json
{
  "spec_version": "1.0",
  "event_id": "test_evt_...",
  "event_type": "event_subscription.test",
  "schema_version": "1.0",
  "source": "bcs",
  "occurred_at": "...",
  "recorded_at": "...",
  "subject": {
    "type": "event_subscription",
    "id": "sub_..."
  },
  "scope": {
    "group_id": "group-1"
  },
  "stream": {
    "key": "event-subscription-test:sub_...",
    "sequence": 1
  },
  "data": {
    "test": true
  }
}
```

Test Event 不进入业务 Event catalog、不匹配其他 Subscription、不影响正常 stream sequence。接口等待本次测试
请求完成并返回脱敏结果；它不启用自动重试。

### 12.4 Replay 与 Skip

- replay 需要 Subscription scope 的管理权限；
- 首版 replay 只允许尚未解决的 dead-lettered Delivery；创建 replacement 时必须锁定原 Delivery，同一 blocker
  同时最多有一个非终态 replacement；
- replay 保留原 `event_id`、canonical Event 和 stream sequence，生成新 `delivery_id`；
- replay 使用当前 active revision，不允许任意历史 backfill；
- replay 使用所选 revision 的 endpoint 和 payload policy 重新投影 immutable body，并重新执行因果前置
  target 解析；不得依赖旧 revision 下已取消或不存在的 Delivery；
- replay 本身不重新执行业务操作；
- strict lane 中 replay 成功后把原 Delivery 标记为由该 replacement 解决，并解除对应 dead-letter blocker；
- skip 只允许尚未解决的 dead-lettered Delivery，必须提供非空 reason；
- skip 写审计 actor、时间和 reason，并把状态设为 `skipped`；
- skip 是数据丢失确认动作，不提供批量默认操作。

### 12.5 错误映射

至少定义以下稳定 application error code：

| code | HTTP | 场景 |
| --- | --- | --- |
| `event_subscription_not_found` | 404 | Subscription 不存在或调用者不可见 |
| `event_delivery_not_found` | 404 | Delivery 不存在或调用者不可见 |
| `invalid_event_filter` | 400 | filter 语法错误或未知 family |
| `invalid_event_scope` | 400 | scope/type/id 组合无效 |
| `invalid_webhook_url` | 400 | URL 语法、scheme 或 URL policy 不允许 |
| `event_subscription_limit_reached` | 409 | 超过 scope 限制 |
| `event_subscription_revision_conflict` | 409 | 乐观锁冲突 |
| `event_subscription_forbidden` | 403 | 无 scope 管理权限 |
| `event_delivery_not_replayable` | 409 | Event 已过保留期或状态不允许 |
| `event_delivery_lane_blocked` | 409 | 操作与 strict lane 状态冲突 |

外部 endpoint 的响应 body 不直接透传给 API 调用方，只返回 HTTP status、错误分类、时间和 request/delivery ID。

## 13. Webhook HTTP Contract

### 13.1 请求

- Method：`POST`
- Content-Type：`application/json; charset=utf-8`
- Body：Event Envelope 的 canonical JSON bytes
- Redirect：禁止
- Compression：首版不启用，保证持久化 body 与实际发送 bytes 一致

除 `Content-Type: application/json; charset=utf-8` 外，Webhook Contract 不定义业务 Header。Event Envelope body
是事件 ID、类型、时间、scope、stream 和 data 的唯一权威来源。

### 13.2 鉴权边界

MVP 面向受控内部服务，不定义 Subscription 级 `auth`、共享 secret 或签名 Header。接收方必须：

1. 用 `event_id` 实现幂等去重；
2. 只有在事件被可靠接收后才返回 2xx。

同一个 Event 的自动重试使用同一个 `delivery_id`；管理员 replay 使用新的 `delivery_id` 并保留原 `event_id`。
BCS 在创建 Delivery 时一次性序列化并持久化原始 body bytes，自动重试不得重新序列化。

如部署环境需要调用方身份、传输加密之外的完整性保护或零信任访问控制，应在 API gateway、egress proxy、mTLS 或
service mesh 层统一配置。未来若要让不同 Subscription 使用不同鉴权信息，必须通过独立 Spec 引入，不能在 MVP
Contract 中预留 `auth` 枚举、secret 字段、数据库列或空实现。

### 13.3 Endpoint 管理

- 完整 URL 仅由管理写接口接收，并按 Subscription revision 持久化；
- 数据库保存 URL 明文，依赖数据库访问控制和备份权限保护；MVP 不引入专用 URL 加密能力；
- URL 禁止 query、userinfo 和 fragment，避免在 URL 中携带 credential；
- 查询响应、日志和错误只允许暴露 scheme、host 或 path hash，不返回完整 URL/path；
- URL 更新创建新 revision，并取消旧 revision 尚未开始或等待重试的投递。

### 13.4 响应判定

| 结果 | 行为 |
| --- | --- |
| 任意 2xx | 成功，Delivery `succeeded` |
| 网络错误、DNS 暂时错误、TLS 暂时错误 | 重试 |
| 408、425、429 | 重试 |
| 500～599 | 重试 |
| 410 | 禁用 Subscription，取消其 pending target 和 pending/retry Delivery 并告警 |
| 300～399 | 不跟随，按 terminal failure 处理 |
| 其他 400～499 | terminal failure，进入 DLQ |

`Retry-After` 只对 429 和 503 生效，支持 delta-seconds 和 HTTP date，并受最大 1 小时 cap 约束。响应 body
最多读取 4 KiB 用于脱敏诊断，不进入普通业务日志，不作为业务错误透传。

### 13.5 Timeout

默认值：

- DNS + connect timeout：3 秒；
- 整体 request timeout：10 秒；
- response body drain/read timeout 包含在整体 timeout 中；
- Subscription 可以在部署配置允许的范围内设置 1～30 秒 request timeout；
- timeout 到达后 Attempt 结束，不无限等待远端连接。

## 14. 重试、死信与回放

### 14.1 Retry schedule

- 首次 Attempt 在 fanout 完成后立即执行；
- 后续采用 exponential backoff + full jitter；
- 基础间隔 5 秒；
- 单次最大间隔 1 小时；
- 默认最多 12 次 Attempt；
- 默认总重试窗口 24 小时；
- `Retry-After` 可以延长当前间隔，但不能超过单次和总窗口上限；
- 所有参数由 validated configuration 设置，不能由业务服务读取环境变量。

### 14.2 Delivery 状态

```text
pending
  -> in_flight
  -> succeeded
  -> retry_wait -> in_flight
  -> dead_lettered
  -> cancelled
  -> skipped
```

- worker 通过有期限 lease 领取 Delivery；
- 进程退出或崩溃后，lease 到期可由其他实例恢复；
- 同一 Delivery 同时只能有一个有效 lease；
- lease 不能提供 exactly-once，因此 receiver 仍必须去重；
- strict lane 只允许领取 head Delivery。

### 14.3 DLQ

Delivery 在以下情况进入 dead-letter：

- 收到 terminal HTTP status；
- 超过 attempt 上限；
- 超过 retry window；
- Payload 投影出现不可恢复错误；
- endpoint revision 已损坏且无法安全恢复。

DLQ 记录稳定 error category、最后 HTTP status、attempt count、first/last attempt time。错误摘要必须脱敏。

### 14.4 保留期

首版使用单一 Eventing 保留窗口，默认 30 天。canonical Event、投递 Payload、Delivery 和 Attempt 在该窗口内
共同保留；安全清理必须以整组引用关系为单位，不能只删除其中一张表：

- 已删除 Subscription 和管理审计从终态时间起使用同一保留窗口；active 配置不按创建时间清理；
- 超过 Event 保留期后不能 replay body；
- 清理任务不得删除仍被 pending fanout target、causal dependency、active retry、blocked strict lane 或合规 hold
  引用的 Event；

保留参数通过配置校验。降低保留期属于运维和兼容性变化，需要在部署说明中明确。

## 15. Webhook 出站安全

### 15.1 URL 校验

创建、更新、测试以及每次实际 Attempt 都必须执行 Outbound URL Guard：

- production 只允许 HTTPS；
- local 模式可以通过显式配置允许 HTTP，但只限 loopback 测试服务；
- 禁止 userinfo；
- 禁止 query，不得用 URL 承载鉴权信息；
- 禁止 fragment；
- 默认禁止非标准端口；精确私网 endpoint allowlist 可按 host pattern、CIDR 和 port 交集放开；
- host pattern 只支持精确域名或前缀 `*.`，例如 `*.hooks.example.internal` 匹配其一级或多级子域，但不匹配 `hooks.example.internal`；
- 未被 allowlist 覆盖时，继续禁止环回、私网、链路本地、组播、保留地址和云元数据地址；allowlist CIDR 只接受 RFC1918 或 IPv6 ULA 的规范网络地址；
- 每一次 Attempt 都实时解析 DNS 并校验所有候选结果，不跨 Attempt 缓存或持久化解析结果；
- 单次 Attempt 内，连接必须复用该次 guard 已校验的解析结果，避免校验与连接之间发生 DNS rebinding；
- 不跟随重定向；
- 代理配置不能绕过相同的目标校验。

创建时校验成功不代表后续永久可信，因此每次 Attempt 都重新解析和校验。运行时地址变为不安全目标时，该 Attempt
失败并告警，不发出请求。

### 15.2 数据最小化

默认 `metadata_only`。所有模式下永久禁止外发：

- Bot token、Bearer token、cookie、OAuth credential；
- provider bypass header；
- state-machine 内部 prompt、thinking 和未脱敏 tool arguments；
- 文件 object handle、内部存储 URL、临时 share token；
- owner-only ManagerWorker 物理消息副本信息；
- 内部堆栈、SQL、内部主机名和私有 endpoint；
- 原始认证 Principal claims。

### 15.3 日志与审计

日志可以记录：subscription ID、event ID、delivery ID、attempt、endpoint host 的 hash 或批准的脱敏形式、HTTP
status、latency、error category、stream key hash。日志不得记录完整 URL/path、完整 body 或响应 body。

创建、更新、禁用、删除、replay 和 skip 必须写管理审计，包含 actor、scope、revision、时间和操作类型。

## 16. 首版 Event Catalog

### 16.1 通用规则

- event type 使用小写 dot-separated 名称；
- `created` 表示资源从不存在或不可用进入已提交、可读状态；
- `completed` 只表示成功终态；失败、取消和超时事件延期，不在 MVP Contract 中预注册；
- participant 使用 actor-neutral 命名，不能继续假设所有成员都是 Bot；
- Event data 只放本次事实所需的最小字段；
- 每个 Event 的 `schema_version` 首版为 `1.0`；
- Event 的产生条件必须由 application/core Contract 定义，不能由 HTTP route 猜测。
- Catalog 只登记已经有真实 Producer 的事件；新增事件必须同时提交 Producer、schema、fixture 和测试。

### 16.2 Group 与成员

| Event type | 触发条件 | subject | 关键 data |
| --- | --- | --- | --- |
| `group.created` | 群完整 provisioning 成功 | group | `group_kind`, `strategy`, `name`, `status`, `version` |
| `group.participant.added` | 正式群成员新增 | participant | `actor_id`, `actor_type`, `role`, `mode`, `group_version` |
| `group.participant.removed` | 正式群成员移除 | participant | `actor_id`, `actor_type`, `previous_role`, `reason`, `group_version` |

`group.created` 不包含完整 context 或 participant 私密详情；订阅方需要当前快照时通过授权 API 查询。

### 16.3 Session 与成员

| Event type | 触发条件 | subject | 关键 data |
| --- | --- | --- | --- |
| `session.created` | session 成为可读可用资源 | session | `session_kind`, `status`, `created_by?`, `initial` |
| `session.completed` | session 首次进入 completed | session | `completed_by`, `reason`, `summary` |
| `session.participant.added` | session 范围成员新增 | participant | `actor_id`, `actor_type`, `role`, `mode`, `visible_from_seq` |
| `session.participant.removed` | session 范围成员移除 | participant | `actor_id`, `actor_type`, `previous_role`, `reason` |

ManagerWorker 的 `bcs_task_complete` 当前语义是 manager 宣告协作会话完成；它产生 `session.completed`，不产生一个
虚构的“所有 worker task 共用 task.completed”。

### 16.4 ManagerWorker Task

| Event type | 触发条件 | subject | 关键 data |
| --- | --- | --- | --- |
| `task.assigned` | assign task 被接受并形成有效子任务 | task | `task_id`, `manager_id`, `worker_id`, `session_id?`, `assignment` |
| `task.completed` | worker final result 被验证并成功接收为该 task 的结果 | task | `task_id`, `manager_id`, `worker_id`, `session_id?`, `result`, `completed_at` |

这里的 `task.completed` 是单个 worker task 的终态，不等于 manager 完成整个 session。Task Ledger 未来持久化时
必须保持这些 Event 语义不变。

`assignment` 和 `result` 使用 Content Projection；metadata-only 不含正文，full 模式也不得包含内部 prompt、
thinking 或原始工具参数。

### 16.5 State Machine Run

| Event type | 触发条件 | subject | 关键 data |
| --- | --- | --- | --- |
| `state_machine.run.created` | run 与定义快照持久化完成 | run | `definition_id`, `definition_version`, `run_mode`, `status=running` |
| `state_machine.run.started` | run 开始调度首批节点 | run | `run_mode`, `started_at`, `input` |
| `state_machine.run.completed` | run 首次进入 Completed | run | `completed_at`, `output`, `duration_ms` |

`run_mode`：

- `configured`：来自群已配置的自定义协作定义；
- `one_shot`：通过当前 session 临时提交 YAML、binding 和 input 执行。

当前 runtime 直接创建 Running run；`run.created` 和 `run.started` 可以在同一业务请求中连续提交，但必须是两个
不同 sequence 的事件，以区分一次性状态机的“创建”和“执行”。

### 16.6 State Machine Node

| Event type | 触发条件 | subject | 关键 data |
| --- | --- | --- | --- |
| `state_machine.node.started` | 某 attempt 进入 Running | node | `run_id`, `node_id`, `attempt`, `predecessor_node_ids?`, `assignee_id?`, `started_at` |
| `state_machine.node.completed` | 某 attempt 成功完成 | node | `run_id`, `node_id`, `attempt`, `outcome`, `output`, `completed_at`, `duration_ms` |
| `state_machine.node.retry_scheduled` | 失败后决定重试 | node | `node_id`, `attempt`, `next_attempt`, `max_attempts`, `retry_at`, `reason` |

BotTask 与 HumanInput 节点共享 started/completed 事件。Judge、ready、failed、timed-out、skipped 和 human-input
细分生命周期均不在 MVP Catalog；内部 collaboration event 不得直接转成公共 Event。

新生产的 `state_machine.node.started` 必须携带 `predecessor_node_ids`：它是定义中全部直接前序 node ID
的去重数组，按 ID 升序输出，初始节点输出 `[]`。为兼容字段引入前已经持久化的 Event，v1 schema 中该字段
保持可选；消费方不得把旧 Event 的字段缺省解释为初始节点。

### 16.7 Message

| Event type | 触发条件 | subject | 关键 data |
| --- | --- | --- | --- |
| `message.created` | 一条外部可见逻辑聊天消息成功持久化 | message | `logical_message_id`, `message_type`, `sender`, `run_id?`, `content`, `attachments` |

Message Event 规则：

- 自由聊天和 ManagerWorker 群都使用 `message.created`；
- `message_type` 首版只允许 `chat`；
- streaming delta 不产生 Event；
- Bot final chat 每个最终逻辑 segment 最多产生一个 Event；
- thinking、tool start/end、原始 tool arguments、内部协调 echo 不产生 `message.created`；
- `logical_message_id` 与持久化 message ID 相同并作为 Producer 幂等键；
- `session_seq` 由消息事务分配，但不进入首版 Event data；顺序由 Event `stream.sequence` 表达；
- metadata-only 不包含正文、摘要或正文派生哈希，只包含长度和 content type；
- full 模式可以包含最终可见文本，但仍受 64 KiB 和敏感信息策略限制；
- attachment 只包含 file ID、文件名、media type、size 和 readiness，不包含 object handle、内部 URL 或 share token。

### 16.8 Data schema 通用类型与示例

Event data 使用 `snake_case`。除非 Event catalog 或 OpenAPI schema 明确标为 optional，字段必须存在；optional
字段无值时省略，不能用空字符串或 `null` 代替。ID 是 1～256 bytes 的 opaque UTF-8 string，version/attempt/
sequence 是非负 JSON integer，时间使用 UTC RFC3339，duration 使用非负 `*_ms` integer。

正文、input、output、artifact、assignment 和 result 统一使用 Content Projection：

```json
{
  "included": false,
  "content_type": "text/plain",
  "size_bytes": 128,
  "truncated": false
}
```

full 模式可以变为：

```json
{
  "included": true,
  "content_type": "text/plain",
  "size_bytes": 128,
  "delivered_bytes": 128,
  "text": "final externally visible text",
  "sha256": "lowercase-hex",
  "truncated": false
}
```

- metadata-only 不提供 preview，避免“摘要”绕过正文权限；
- metadata-only 不得包含正文派生的 hash、fingerprint 或其他可用于低熵内容字典猜测的标识；
- `size_bytes` 描述脱敏后、允许进入 Event 投影的业务正文长度，不包含被禁止的内部字段；
- full 模式的 `sha256` 描述最终投递的 `text` 或 canonical JSON bytes，仅用于诊断和关联；
- `delivered_bytes` 只在 `included=true` 时出现；
- JSON 类型内容使用 `json` 字段替代 `text`，二者不能同时出现；
- full producer 必须先执行 Event data policy，再计算 hash；
- hash 用于诊断和关联，不作为认证机制。

Attachment Summary 固定为：

```json
{
  "file_id": "file-1",
  "name": "report.pdf",
  "media_type": "application/pdf",
  "size_bytes": 2048,
  "status": "ready"
}
```

状态改变 Event 使用 `changed_fields: string[]`。`before` 和 `after` 只包含 `changed_fields` 指定的、该 Event
schema 允许公开的标量或小型 object；不复制整个资源。失败类 Event 使用稳定 `error_category`/`reason_code`，
`reason_message` 是 optional、脱敏、非堆栈文本。

`message.created` full 示例：

```json
{
  "spec_version": "1.0",
  "event_id": "evt_message_1",
  "event_type": "message.created",
  "schema_version": "1.0",
  "source": "bcs",
  "occurred_at": "2026-08-18T10:01:00.000Z",
  "recorded_at": "2026-08-18T10:01:00.005Z",
  "subject": {
    "type": "message",
    "id": "logical-message-1"
  },
  "scope": {
    "group_id": "group-1",
    "session_id": "session-1"
  },
  "stream": {
    "key": "session:session-1",
    "sequence": 18
  },
  "actor": {
    "type": "human",
    "id": "human-1",
    "display_name": "Alice"
  },
  "correlation_id": "request-1",
  "data": {
    "logical_message_id": "logical-message-1",
    "message_type": "chat",
    "sender": {
      "type": "human",
      "id": "human-1",
      "display_name": "Alice"
    },
    "session_seq": 12,
    "content": {
      "included": true,
      "content_type": "text/plain",
      "size_bytes": 15,
      "delivered_bytes": 15,
      "text": "请检查执行结果",
      "sha256": "lowercase-hex",
      "truncated": false
    },
    "attachments": []
  }
}
```

`task.completed` metadata-only 的 data 示例：

```json
{
  "task_id": "task-1",
  "manager_id": "bot-manager",
  "worker_id": "bot-worker",
  "session_id": "session-1",
  "result": {
    "included": false,
    "content_type": "text/plain",
    "size_bytes": 320,
    "truncated": false
  },
  "completed_at": "2026-08-18T10:02:00.000Z"
}
```

该 Event 的 scope 必须包含 `group_id`、`session_id` 和 `task_id`，stream key 为 `task:task-1`。

`state_machine.node.retry_scheduled` 的 data 示例：

```json
{
  "run_id": "run-1",
  "node_id": "review",
  "attempt": 1,
  "next_attempt": 2,
  "max_attempts": 3,
  "retry_at": "2026-08-18T10:03:00.000Z",
  "reason_code": "bot_delivery_timeout"
}
```

OpenAPI 必须为 Envelope、通用类型和每个首版 Event data 定义独立 schema，并以 `event_type` 作为 discriminator。
表格中的“关键 data”不是允许实现省略 schema 的理由；OpenAPI schema fixture 和 producer test fixture 才是实现期
逐字段的权威 Contract。

## 17. 消息持久化改造要求

### 17.1 当前行为判断

当前 `try_persist_group_message`：

- message repo 未注入时直接返回；
- `append_message` 失败时只 warning；
- 返回类型为 `()`；
- 多个调用方在失败后继续 Bot、Frontend 或 ManagerWorker 流程。

代码没有记录该决策的正式原因。结合可选 repo、`try_` 命名和调用顺序，可以推断历史上把消息历史视为
best-effort side effect，以优先保证实时投递；该推断不能作为继续吞错的 Contract。

### 17.2 修复边界

持久化发生在外部投递前的入口，写失败必须阻止后续投递。已经接收到 Bot callback 或已经完成外部投递的入口，
MVP 仍必须把消息写失败向上返回，不能记录 warning 后伪装成功；已有 idempotency key 的入口继续用它去重。由于
内部 Bot callback 并非都具有持久化 inbox 或稳定 message key，既有外部投递和 callback 重试在极端失败下仍可能
重复，这是明确保留的现有链路限制，不由 Event Subscription 顺手扩展解决。

### 17.3 目标语义

消息入口分两类：

#### A. 尚未发生不可逆外部副作用

例如用户发起的新聊天消息：

1. 验证和授权；
2. 在事务中持久化 canonical logical message；
3. 同事务写 `message.created`；
4. commit；
5. 再路由给 Bot/Frontend；
6. 落库失败则返回 retryable persistence error，不发生投递。

#### B. 已接收到外部 Bot 回调或已有不可逆副作用

MVP 保持既有调用顺序，但收紧成功语义：

1. 保留入口已有的 callback/run/tool/event ID 和幂等门禁，不为缺失稳定 ID 的入口合成新的跨进程语义；
2. logical message 与 Event 写入失败时向上返回错误，不继续返回成功；
3. 已被现有 run/Task 状态识别为完成的重复 callback 不产生新 message 或 Event；进程崩溃后的重复处理不在 MVP
   保证范围；
4. 只有最终成功写入 message 后才产生 Event；
5. 不新增 durable inbox、持久化 retry work item 或可恢复 callback worker。

Frontend 在线广播仍是 best-effort 的瞬时通知：持久化事实已经提交后，广播失败不回滚 message/Event，也不改变 API
成功语义。

### 17.4 MySQL 原子性

以下步骤必须合并到同一 DB transaction：

```text
UPDATE bcs_group_sessions SET current_msg_seq = current_msg_seq + 1
SELECT current_msg_seq
INSERT bcs_messages (... logical_message_id, session_seq ...)
LOCK message Event scope chain FOR SUBSCRIPTION SNAPSHOT
INSERT bcs_events (... message.created ...)
INSERT bcs_event_fanout_targets (... matching subscription revision ...)
```

任一步失败全部回滚，不留下 sequence 空洞。Memory 实现使用同一锁临界区提供等价语义。

## 18. 数据模型

以下是逻辑 schema；字段类型和索引长度可按现有 DB abstraction 调整，但语义和唯一约束是 Contract 的一部分。

### 18.1 `bcs_event_subscriptions`

| 字段 | 说明 |
| --- | --- |
| `subscription_id` PK | opaque ID |
| `name` | 用户可读名称 |
| `scope_type`, `scope_id` | MVP 固定为 group 及其 ID |
| `status` | pending/active/disabled/deleted |
| `current_revision` | 当前 revision |
| `created_by_type`, `created_by_id` | 管理 actor |
| `created_at`, `updated_at`, `deleted_at` | 生命周期时间 |
| `env` | 部署环境隔离标签并必须用于查询隔离 |

索引：scope 查询、status、creator、updated_at。

### 18.2 `bcs_event_subscription_revisions`

| 字段 | 说明 |
| --- | --- |
| `(subscription_id, revision)` PK | immutable revision |
| `event_filters_json` | 已规范化 filter |
| `payload_mode` | metadata_only/full |
| `endpoint_url` | 已校验的完整 endpoint URL；仅投递路径读取，不通过 API 回显 |
| `request_timeout_ms` | 已校验 timeout |
| `activated_at`, `retired_at` | revision 的事务提交和审计时间；历史匹配结果以 fanout target 为准 |

URL 本身不是 credential；MVP 在数据库中明文保存完整 URL，并依赖数据库访问控制和备份权限保护。列表和详情接口
都只展示 scheme、host 和 path hash，不显示完整 URL。URL 禁止 query、userinfo 和 fragment。

`include_descendants=true`、`ordering=strict_per_stream` 和 `sink=webhook` 是 MVP Contract 常量，不进入 revision
表，也不存在内部可覆盖分支。

### 18.3 `bcs_event_scope_epochs`

该表提供 scope-local 的订阅变更线性化点，避免所有 Event 竞争一个 env 全局行：

| 字段 | 说明 |
| --- | --- |
| `(env, scope_type, scope_id)` PK | group scope |
| `epoch` | 每次该 scope 的 Subscription create/update/enable/disable/delete 时递增 |
| `updated_at` | 更新时间 |

group 创建时必须在同一 Unit of Work 建立对应行。Subscription mutation 对 group scope 行取得排他锁；Event
append 取得对应 group scope 锁，再快照匹配 revision 和创建 target。等价实现可以使用数据库 serializable
conflict/retry，但必须通过相同并发 conformance tests，不能退化为 env 全局互斥。

### 18.4 `bcs_event_streams`

| 字段 | 说明 |
| --- | --- |
| `stream_key` PK | 稳定 stream |
| `last_sequence` | 最后已提交 sequence |
| `updated_at` | 更新时间 |
| `env` | 环境隔离 |

sequence 分配必须使用行锁/CAS 或数据库等价原语。

### 18.5 `bcs_events`

| 字段 | 说明 |
| --- | --- |
| `event_id` PK | 稳定 Event ID |
| `event_type`, `schema_version` | Event Contract |
| `producer`, `producer_key` | 幂等来源 |
| `subject_type`, `subject_id` | 直接资源 |
| `group_id`, `session_id`, `task_id`, `run_id` | 可索引 scope；MVP Event 必有 group_id |
| `stream_key`, `sequence` | 顺序 |
| `actor_json` | 脱敏 actor |
| `correlation_id`, `causation_event_id`, `trace_id` | 关联信息 |
| `data_json` | canonical data |
| `occurred_at`, `recorded_at` | 事件时间 |
| `fanout_status` | pending/completed/failed |
| `retention_until` | 可清理时间 |
| `env` | 环境隔离 |

唯一约束：

- `(env, producer, producer_key, event_type)`；
- `(env, stream_key, sequence)`。

### 18.6 `bcs_event_fanout_targets`

fanout target 是 Event 提交时固化的匹配结果，不包含 endpoint 或 HTTP payload：

| 字段 | 说明 |
| --- | --- |
| `target_id` PK | 稳定 target ID |
| `event_id` | canonical Event |
| `subscription_id`, `subscription_revision` | Event 事务快照的 immutable revision |
| `purpose` | normal/causal_prerequisite/manual_replay |
| `replay_request_id` | manual_replay 的幂等请求 ID，其他 purpose 省略 |
| `replay_of_delivery_id` | manual_replay 要解决的 DLQ Delivery，其他 purpose 省略 |
| `depends_on_target_id` | 可选的跨 stream 因果前置 target |
| `status` | pending/materialized/cancelled/failed |
| `created_at`, `materialized_at`, `cancelled_at` | 生命周期时间 |
| `env` | 环境隔离 |

正常匹配和因果前置 target 分别以
`(env, subscription_id, subscription_revision, event_id, purpose)` 唯一。manual replay 额外包含
`replay_request_id`，使同一重放请求可安全重试，同时允许管理员以后再次重放。

Event、匹配 revision 和 normal target 必须在同一业务事务提交。`causal_prerequisite` 在解析 effect target 时使用
同一 Event Store 事务幂等补建；manual replay 在回放命令事务中创建。异步 fanout 只把 target materialize 为
Delivery，不能重新读取当前 Subscription 来改写历史匹配结果。

### 18.7 `bcs_event_deliveries`

| 字段 | 说明 |
| --- | --- |
| `delivery_id` PK | 逻辑 Delivery ID |
| `fanout_target_id` | 产生该 Delivery 的 immutable target |
| `event_id` | canonical Event |
| `subscription_id`, `subscription_revision` | 固定配置快照 |
| `stream_key`, `sequence` | lane 排序 |
| `payload_bytes`, `payload_sha256` | 当前 Delivery 不可变的原始 HTTP body 和摘要 |
| `status` | delivery 状态 |
| `attempt_count` | 已开始次数 |
| `first_attempt_at`, `last_attempt_at`, `next_attempt_at` | 调度时间 |
| `lease_owner`, `lease_until` | 多实例租约 |
| `last_http_status`, `last_error_category`, `last_error_summary` | 脱敏结果 |
| `dead_lettered_at`, `cancelled_at`, `skipped_at` | 终态时间 |
| `skip_actor`, `skip_reason` | 数据丢失审计 |
| `replay_of_delivery_id` | replacement 所解决的 DLQ Delivery，普通 Delivery 省略 |
| `resolved_by_delivery_id`, `resolved_at` | DLQ 被成功 replay replacement 解决的审计信息 |
| `created_at`, `succeeded_at` | 生命周期时间 |
| `env` | 环境隔离 |

唯一约束：`(env, fanout_target_id)`，每个 target 最多 materialize 一个 Delivery。manual replay 先以
`replay_request_id` 创建新的 target，再生成新的 Delivery；不得通过绕开唯一约束直接插入 Delivery。

同一个 Delivery 的全部自动重试必须复用完全相同的 `payload_bytes`。管理员 replay 在 12.4 允许选择的 Subscription
revision 下重新投影 canonical Event，生成新的 Delivery 和 payload；因此 replay 可以因新的 payload policy 而
省略更多字段，但不得获得原 canonical Event 中不存在的数据。

### 18.8 `bcs_event_delivery_attempts`

| 字段 | 说明 |
| --- | --- |
| `(delivery_id, attempt_no)` PK | Attempt |
| `started_at`, `completed_at`, `latency_ms` | 时间 |
| `result` | success/retryable/terminal |
| `http_status` | 可空 HTTP status |
| `error_category`, `error_summary` | 脱敏错误 |
| `response_bytes_observed` | 诊断大小，不存原 body |
| `worker_id` | 领取 worker |

### 18.9 Message schema 变更

`bcs_messages` 增加 `logical_message_id`。同一逻辑消息的 visibility/owner 物理副本共享该值。至少增加：

- `(env, logical_message_id)` 查询索引；
- 防止 canonical message Event 重复的唯一或应用级约束；
- client message idempotency 与 logical message ID 的稳定映射。

## 19. Subscription 匹配与 Fanout

### 19.1 匹配条件

Event 同时满足以下条件才在业务事务内创建 normal fanout target：

1. Subscription 在 Event 提交时为 active；
2. Event type 命中 filter；
3. Event scope 与 Subscription scope 完全相同，或 include descendants 允许沿 scope chain 匹配；
4. 当前调用部署/env 一致；
5. Payload policy 允许投影该 Event；
6. 没有既有 normal target 唯一键。

### 19.2 并发一致性

Subscription 创建/更新/禁用以其状态事务持有 scope epoch 排他锁期间的 commit 为线性化点。Event append 按固定
顺序取得 scope chain 的共享锁，在同一业务事务中读取 active revision，并把匹配结果写为 immutable fanout
target；该 target snapshot 是 Event 的线性化点。scope 锁必须保持到事务结束。
Revision 查询必须使用取得 scope 锁之后建立的 lock-consistent/current transaction view；不得复用锁前的陈旧 MVCC
snapshot，否则等待中的 Subscription create/update 提交后仍可能匹配旧 revision。

两个事务有明确先后时必须观察该先后；发生重叠时，结果由数据库事务隔离和冲突处理确定，但 Event 与 target 必须
原子提交：

1. Subscription create/enable 先取得排他锁并提交，后取得共享锁的 Event 必须匹配新 revision；
2. Event 先取得共享锁并完成 target snapshot，重叠的 create/enable 等待后再提交，该 Event 默认不回填；
3. update 在同一事务中 retire 旧 revision 并 activate 新 revision，Event 只能匹配事务快照可见的一个 revision；
4. disable/delete 在 Event target snapshot 前提交，该 Event 不创建 target；
5. endpoint/payload 收紧或状态变更还必须在 revision 事务中取消受策略影响、尚未开始的旧 target 和
   Delivery，防止并发 worker 继续使用旧配置。

该机制必须满足：

- create 成功之后提交的匹配 Event 一定有 Delivery；
- create 成功之前已提交的 Event 默认不回填；
- update 成功之后提交的 Event 使用新 revision；
- disable 成功之后提交的 Event 不产生 Delivery；
- fanout worker 延迟执行时，结果不因 Subscription 当前值变化而漂移；
- 不同 group/session 的 Event append 不竞争同一个 env 级 cursor 行。

禁止 fanout 时只读取“现在的 Subscription”来猜测过去 Event 应使用的配置。

群创建内联 Subscription 必须先在同一 provisioning Unit of Work 中进入 active，再记录 `group.created` 和初始
`session.created` 的 target snapshot，因此能收到本次创建事件；实现不能依赖事务外的后台 worker 恰好先后执行。

### 19.3 Fanout failure

fanout worker 使用原子 claim/lease 独立领取 target，并把它 materialize 为 Delivery。因果前置由
`depends_on_target_id` 和 deterministic `causal_prerequisite` target 解析，不依赖先扫描整个 env 的 Event 顺序。
一个 Event 的所有 target 均 materialized、cancelled 或以确定性错误进入可审计终态后，`fanout_status` 才变为
completed。

可重试数据库错误只重试受影响 target，不暂停同一 env 的其他 Event。确定性的 projection/schema/config 错误必须
为受影响 Subscription 创建可审计的 dead-letter Delivery 或禁用损坏 revision，并推进该 target；不能永久阻塞
其他 scope。Fanout 不执行外部 HTTP，不受 endpoint 可用性影响。

## 20. Dispatcher 并发模型

- 每个 BCS 实例可以运行多个 delivery worker；
- worker 通过 store 的原子 claim API 领取 Delivery；
- claim API 必须执行 strict lane head 检查；
- 不允许先批量读取再在内存里无锁更新；
- 默认全局并发 64、单 endpoint host 并发 8，可配置；
- 同一个 strict lane 并发固定为 1；
- endpoint host 限流只影响对应 host；
- worker crash 后由 lease recovery 恢复；
- shutdown 后不领取新工作；
- 时间使用可注入 Clock，测试不得依赖真实 sleep。

未来做 leader-only fanout 或 delivery 时可以复用 `LeaderElectionPort`，但正确性不能依赖恰好只有一个进程。

## 21. 配置

配置形态：

```toml
[eventing]
enabled = true
dispatcher_enabled = true
fanout_poll_interval_ms = 200
delivery_poll_interval_ms = 200
worker_concurrency = 64
per_host_concurrency = 8
lease_ms = 30000
drain_timeout_ms = 10000
event_retention_days = 30

[eventing.retry]
base_delay_ms = 5000
max_delay_ms = 3600000
max_attempts = 12
max_elapsed_ms = 86400000

[eventing.webhook]
connect_timeout_ms = 3000
request_timeout_ms = 10000
max_request_timeout_ms = 30000
max_event_body_bytes = 262144
max_response_body_bytes = 4096
allow_http_loopback = false
allow_non_standard_ports = false

[[eventing.webhook.private_endpoint_allowlist]]
host = "*.hooks.example.internal"
cidrs = ["10.20.0.0/16"]
ports = [443, 8443]

[eventing.limits]
max_group_subscriptions = 10
max_session_subscriptions = 5
max_task_subscriptions = 5
max_run_subscriptions = 5
max_filters_per_subscription = 64
```

要求：

- 所有 key 进入显式 config contract/schema；
- unknown key 启动失败；
- timeout、并发、保留期和数量限制做范围校验；
- core/application 不读取环境变量；
- `dispatcher_enabled = false` 只暂停投递，不停止 Event 记录；
- `enabled = false` 时独立 Subscription API 和群内联 Subscription 必须明确返回 capability disabled，不能悄悄
  接受后丢 Event；
- `enabled = false` 时普通业务 use case 仍可执行，Event Recorder 必须显式返回 `Disabled` 而不是伪装成
  `Recorded`；只有 `Recorded` 模式适用本文的 Event 完整性承诺；
- production 禁止通过配置关闭 SSRF guard 或 TLS 校验。

Singlebox/local 模式使用 Memory Event Store 和 Recording/Localhost Webhook adapter，不需要访问外部网络。

## 22. 可观测性与运维

### 22.1 Metrics

至少提供：

```text
bcs_event_produced_total{event_type}
bcs_event_produce_failed_total{event_type,error_category}
bcs_event_fanout_lag_seconds
bcs_event_fanout_failed_total
bcs_event_delivery_attempt_total{event_type,result,status_class}
bcs_event_delivery_latency_seconds{event_type}
bcs_event_delivery_pending
bcs_event_delivery_retry_wait
bcs_event_delivery_dead_lettered
bcs_event_delivery_blocked_lanes
bcs_event_delivery_oldest_pending_seconds
bcs_event_subscription_total{status,scope_type}
bcs_event_webhook_ssrf_block_total{reason}
bcs_message_persistence_failed_total{source}
bcs_message_persistence_deferred_total{source}
```

避免把 subscription ID、event ID、URL 或 group ID 直接作为高基数 metric label。

### 22.2 告警建议

- 最老 pending Delivery 超过 5 分钟；
- fanout lag 超过 1 分钟；
- 任意 strict lane blocked；
- dead-letter 增长；
- SSRF policy 在已存在 Subscription 上突然触发；
- message persistence failure 非零；
- dispatcher lifecycle 未启动或 worker 全部退出。

### 22.3 管理查询

MVP 管理 API 提供以下查询：

- Subscription 列表按 group scope、status 过滤，并使用 cursor/limit 分页；
- Subscription 详情按 subscription ID 查询；
- 指定 Subscription 下的 Delivery 列表按 delivery status 过滤，并使用 cursor/limit 分页；
- Delivery 详情按 delivery ID 查询，返回脱敏摘要和 Attempt 列表。

MVP 不提供按 event ID/type、时间范围或 stream key 的全局检索。

查询不返回完整 endpoint URL/path、完整 response body 或默认 full Event body。查看 full Event body 需要额外资源读取权限和
审计。

## 23. Schema 版本与兼容性

### 23.1 版本规则

- Envelope `spec_version` 和各 event `schema_version` 分开演进；
- 同一 major schema version 内可以增加 optional 字段；
- 删除字段、改变类型、改变字段含义或终态语义必须升 major；
- 接收方必须忽略未知字段；
- BCS 不复用旧 event type 表达不同事实；
- 新 event type 是 additive；
- Event catalog 和 OpenAPI schema 必须在同一 PR 更新。

### 23.2 Payload 稳定性

Delivery retry 必须使用首次 fanout 生成的 canonical Event 和 subscription revision，不能用当前资源重新组装
Payload。否则同一个 `event_id` 在不同 Attempt 中会出现不同 body。

### 23.3 旧 Callback

现有 `bcs-callback` 继续处理既有 service invocation session completion：

- 现有配置和 Payload 不在首版被自动改写；
- 新 `session.completed` Event 是独立能力；
- 同时配置旧 callback 和新 Subscription 时，外部系统可能收到两种通知，迁移文档必须明确；
- 后续可以用兼容 projection adapter 把 Event 转成旧 callback Payload；
- 在旧 Contract 宣布 deprecated、提供观测和迁移窗口之前不得移除旧路径。

## 24. 权限模型

### 24.1 管理权限

Subscription 管理是写操作，必须通过现有 V1 Principal 验证和 application authorization：

- group scope：group creator、driver 或已有 group 管理身份；
- Bot 只能在现有 ownership 和资源权限允许时代表自身管理；
- App identity 需要显式 scope grant，不能仅凭存在 App Principal 获权。

为避免资源存在性泄露，无读取权限时详情接口可以返回 404。创建时目标资源不存在返回 404，存在但无权返回 403。

### 24.2 Event 数据权限

创建和每次管理 Subscription 时都重新校验当前 group 管理权限，Payload full 权限单独校验。MVP 的
Subscription 是 group 所有的配置，不是创建者个人 grant 的派生物；创建者后来退出群不会隐式删除已由群批准的
配置，当前 group creator/driver 或其他群管理身份仍可管理它。

Group 删除时，资源删除事务必须把该 Group 的 active Subscription 改为 disabled，并取消 pending target 和
pending/retry Delivery；已成功或正在发送的 HTTP 请求无法撤回。细粒度 grant 撤销、外部 ACL 同步和通用权限
reconciler 不在 MVP 范围，待 BCS 有明确 grant 模型后另行设计。

## 25. 测试计划

### 25.1 Contract / Conformance

`EventRepoPort` 的 Memory 和 MySQL 实现运行同一 suite，至少覆盖：

- event 幂等；
- stream sequence 单调和并发唯一；
- business mutation + Event 原子成功/回滚；
- scope-local revision/target snapshot 生效边界和并发冲突重试；
- create/update 与 Event append 交错时不复用锁前 MVCC snapshot；
- scope descendant 匹配；
- fanout 幂等；
- 不同 group/session 的 Event append 不竞争 env 全局锁；
- Delivery 原子 claim 和 lease recovery；
- strict lane head claim；
- cross-stream causation dependency、跨 revision 取消传播和循环引用拒绝；
- DLQ、replay、skip；
- 并发 replay 同一 DLQ blocker 只创建一个非终态 replacement；
- retention 不删除被引用 Event；
- env 隔离。

`EventDeliveryPort` conformance 覆盖：

- raw body 与 Content-Type 映射；
- 2xx、retryable、terminal、410 分类；
- timeout；
- 禁止 redirect；
- response body cap；
- endpoint URL 和 body 不出现在 debug/error。

### 25.2 Application Service

- 只有具有 scope 管理权限的 actor 可以 CRUD/test/replay/skip；
- full payload 单独授权；
- optimistic revision conflict；
- disable/delete 取消未开始 target 和 Delivery；
- endpoint 变更创建 revision；
- filter 语法和 family registry；
- group inline scope 不可伪造；
- group 创建失败不留下 active Subscription；
- group 创建成功的内联 Subscription 收到 group.created 和初始 session.created。

### 25.3 Event Producer

每个 catalog Event 至少包含：

- happy path 产生一次；
- 业务写失败不产生；
- Event 写失败导致事务回滚；
- 幂等重试不重复；
- 无变化 mutation 不产生；
- actor、scope、stream 和 data schema 正确。

### 25.4 顺序与并发

- 顺序状态机完整事件序列；
- node completed 返回 500 时，后续 node started 不发出；
- 前序 retry 成功后 lane 继续；
- 前序 DLQ 后 lane blocked；
- replay/skip 后恢复；
- cause target 因 revision 更新取消时，尚未开始的 effect target 同事务取消，不永久 blocked；
- effect replay 到新 revision 时重建 causal prerequisite，不引用旧 revision Delivery；
- 两个 run 可以并行；
- 两个 Subscription 互不阻塞；
- 同一 Subscription 同时匹配 group.created/session.created 时按 causation 投递；
- Subscription 只匹配 effect Event 时不因缺少 cause Delivery 阻塞；
- 多实例 worker 不并发领取同一 Delivery；
- worker crash/lease expiry 后恢复；
- 并行节点的 sequence 唯一并符合事务提交顺序。

### 25.5 Message

- 用户消息持久化失败时不投递给 Bot；
- MySQL sequence update、message insert 和 Event insert 任一步失败全部回滚；
- streaming delta 不发 Event；
- final logical message 只发一次；
- ManagerWorker 多个 owner 物理副本共享 logical ID 且只发一个 Event；
- task terminal callback 重试不重复消息/Event；
- metadata-only 不含正文、摘要或正文派生 hash；
- full 模式仍移除工具参数、token 和内部 URL；
- attachment 不暴露 object handle/share token；
- full payload 的 UTF-8 截断不会切断多字节字符。

### 25.6 安全

- loopback/private/link-local/metadata IPv4 和 IPv6；
- DNS 多结果中包含不安全地址；
- DNS rebinding；
- redirect 到私网；
- URL userinfo、fragment、非法 scheme；
- production HTTP 拒绝；
- endpoint path/header/body 日志泄漏扫描；
- body/response 大小边界；
- Group 删除时自动禁用直接 Group Subscription，并取消尚未开始的投递。

### 25.7 OpenAPI 与 E2E

- 更新 `api-contracts/v1/openapi`；
- OpenAPI schema/DTO round-trip；
- unknown fields 拒绝；
- V1 envelope/status/error code；
- singlebox 启动本地 callback receiver；
- 建群内联订阅到实际收到 Event；
- BCS 重启后继续投递；
- receiver 先 500 后 200；
- 429 Retry-After；
- 410 自动禁用；
- DLQ 管理 replay；
- configured 和 one-shot 状态机完整序列。

### 25.8 Architecture gates

- core/service 不导入 HTTP client/framework；
- adapter 不拥有筛选、顺序和 retry policy；
- concrete store/client 只在 composition root 选择；
- raw environment access 仅在 config/bootstrap；
- 新 config schema 校验；
- 新 Service API、Port 和 replaceable capability 有 conformance suite；
- 没有硬编码外部 URL、token 或私有 endpoint。

## 26. 验收标准

本功能完成需要同时满足：

1. 创建群可以内联创建 group scope Webhook Subscription，且能收到该群的 `group.created`。
2. 独立 API 可以管理 group scope Subscription；请求不能选择其他 scope 或覆盖 descendants/ordering。
3. 本文首版 Event catalog 均有明确生产点、schema fixture 和测试。
4. 业务状态和 Event 原子提交；故障注入下不存在“业务成功但 Event 永久丢失”。
5. Dispatcher 重启和多实例运行不丢 Delivery，不并发投递同一 strict lane head。
6. 状态机同一 run 严格有序；前序重试或 DLQ 时后序不能越过。
7. Webhook 只使用标准 JSON Content-Type，Event Envelope body 是唯一权威事件数据；Subscription API 不接受或返回 `auth` 字段。
8. 自动重试、Retry-After、DLQ、replay、skip 和 410 disable 均有 E2E。
9. 每次 Attempt 执行 SSRF guard，不跟随 redirect，production 不允许不安全 HTTP。
10. Message persistence 不再静默吞错；`message.created` 只在逻辑消息成功持久化后产生。
11. ManagerWorker visibility 物理副本不会形成多个外部 Message Event。
12. 一次性状态机产生独立 `state_machine.run.created`、`state_machine.run.started` 和 terminal Event，并标记
    `run_mode=one_shot`。
13. 完整 endpoint URL/path 不在任何读取 API、日志、metric label、error 或 Event body 中出现。
14. Memory/MySQL store 和 Webhook delivery 通过 conformance tests。
15. OpenAPI、配置 schema、迁移、运维文档和架构传播分析与实现同时更新。
16. 因果依赖不会引用被取消的旧 revision Delivery；更新和 replay 场景不会造成永久 blocked lane。
17. Event append 使用 group scope-local target snapshot，不存在 env 级全局 offset 热点；metadata-only 不含正文派生哈希。

## 27. 实施阶段

### Phase 0：Contract 与迁移骨架

- 确认本 Spec；
- 定义 Event catalog schema fixtures；
- 增加 application Service API、Repo Port 和 Delivery Port；
- 更新 OpenAPI 草案和配置 schema；
- 创建数据库 migration，但不启动 Dispatcher。

### Phase 1：Eventing 基础设施

- Memory/MySQL Event Store；
- Subscription CRUD、revision、scope/filter matcher；
- fanout、Delivery、Attempt、lease、strict lane；
- Webhook adapter、JSON body、SSRF guard；
- lifecycle、metrics、DLQ、replay、skip；
- conformance tests。

### Phase 2：Group/Session 生命周期

- 群创建内联 Subscription；
- group provisioning finalization/recovery；
- group/session/member Event；
- group.created + initial session.created E2E。

### Phase 3：Message 一致性

- logical_message_id；
- MySQL message + sequence 原子事务；
- persist failure 显式向调用方返回；
- message.created projection 和隐私测试。

在完成本阶段前，不应对外宣称 `message.created` 具有可靠 Contract。

### Phase 4：ManagerWorker Task

- 接入当前 assign、dispatch result 和 worker terminal transition；
- 明确 task.completed 与 session.completed；
- 不改 Task Ledger 存储形态。

### Phase 5：State Machine

- run Pending -> Running 拆分；
- run/node/retry Event；
- configured/one_shot run mode；
- run strict stream 和完整故障注入 E2E。

### Phase 6：兼容迁移与运营

- 旧 `bcs-callback` 和新 Event 的并行观测；
- migration guide、dashboard、alerts；
- 容量和 backpressure 压测；
- 根据观测决定旧 callback deprecation 时间，不在本阶段自动删除。

每个 Phase 应独立可验证，不能用一个跨所有模块的大 PR 一次完成。

## 28. 发布与回滚

### 28.1 发布顺序

1. 部署 additive database migration；
2. 部署能写 Event/Subscription 但 `dispatcher_enabled=false` 的版本；
3. 验证 Event、fanout backlog 和 schema；
4. 开启 Dispatcher 小流量；
5. 按 group 灰度开放 Subscription API；
6. 接入各事件生产方；
7. 最后开放 message full payload，默认仍保持 metadata-only。

### 28.2 回滚

- 关闭 Dispatcher 只暂停外发，保留 Event/Delivery；
- 回滚 delivery adapter 不影响业务状态；
- 新增表和 optional OpenAPI 字段可以保留；
- 已经对外投递的 Event 无法撤回；
- 如果回滚 Event producer，必须在变更记录中声明可能出现的 Event 时间缺口；
- 不允许通过删除 pending Event 或清空表完成回滚；
- endpoint 废弃或误配时更新 revision、取消旧 pending target/Delivery 并审计。

## 29. Contract 传播范围

### 29.1 消费方

- OpenAPI V1 HTTP clients；
- group create clients；
- Webhook receiver；
- BCS 运维和审计工具；
- 后续 MQ/internal sink adapter。

### 29.2 实现方

- `bcs-api-http`；
- group/session application service；
- message flow/message store；
- ManagerWorker task flow；
- collaboration runtime；
- Event Store Memory/MySQL；
- Webhook Delivery adapter；
- bootstrap lifecycle/configuration；
- test-support 和 singlebox E2E。

### 29.3 部署影响

- additive DB migrations；
- 新 dispatcher background workers；
- 出站 HTTPS 网络策略；
- 新 metrics、alerts 和 retention jobs；
- 容量评估：Event、Delivery 和 Attempt 写放大。

### 29.4 兼容性

- 群创建字段为 optional additive change；
- 旧客户端不受影响；
- 新 receiver Contract 从 `1.0` 开始；
- 旧 `bcs-callback` 保持兼容；
- message persistence 从 best-effort 改为显式一致性会改变错误返回和时延，调用方需要把持久化失败视为请求失败。

## 30. 明确延后项

以下能力在模型中有扩展位置，但需要新的 Spec：

- MQ Sink 和 consumer group；
- 订阅创建前 Event 的按时间/cursor backfill；
- JSON field filter；
- Webhook batch delivery；
- Event payload object storage 和临时拉取 URL；
- 跨 region/部署复制；
- tenant 用户自助配额与计费；
- 公开 Judge lifecycle；
- Task Ledger 持久化；
- 旧 callback 自动转换和最终下线。

MVP 不为这些延后项预置公共枚举、数据库字段或空执行分支；后续能力需要独立 Spec 和兼容性评审。
