# BCS Event Subscription / Webhook 开发计划

- 日期：2026-08-19
- 对应 Spec：`docs/superpowers/specs/2026-08-18-bcs-event-subscription-webhook-design.md`
- 当前范围：Group scope、Webhook sink、strict-per-stream、16 个公共事件

## 1. 目标

为 BCS 提供与具体业务资源解耦的公共事件机制。调用方可在创建 Group 时内联配置
Webhook，也可通过独立 API 管理 Group 订阅。业务事实可靠落库后，后台异步投递，
业务请求不等待外部 Webhook。

本期必须做到：

1. 公共事件 Contract 可版本化、可校验；
2. 持久化业务变更与 Event append 同成同败；
3. 投递至少一次，接收方可按 `event_id` 去重；
4. 同一 Subscription、同一 stream 严格有序；
5. endpoint 按 revision 持久化，API 永不回显完整 URL/path；
6. 失败可重试、进入 DLQ，并支持单条 replay/skip；
7. 仅实现 Spec 列出的 Group scope 与 16 个事件。

## 2. 明确不做

- Tenant、Workspace、Session、Task、Run 等订阅 scope；
- MQ、内部总线、SSE 等其他 sink；
- best-effort ordering、全局顺序或跨 Subscription 顺序；
- `group.updated`、`group.deleted`、participant mode 更新等未列入 Catalog 的事件；
- Group 删除后的 draining Subscription；
- 创建者个人权限变化触发的通用 grant reconciler；
- ManagerWorker Task Ledger 持久化；
- 状态机失败、超时、等待人工等额外公共事件；
- Subscription 级鉴权、签名和自定义 Header；内部服务身份由部署基础设施统一处理；
- 旧 callback 的自动迁移或删除。

这些能力不得只为“以后可能需要”预先加入公共枚举、数据库字段或执行分支。

## 3. 固定事件清单

### Group

- `group.created`
- `group.participant.added`
- `group.participant.removed`

### Session

- `session.created`
- `session.completed`
- `session.participant.added`
- `session.participant.removed`

### ManagerWorker Task

- `task.assigned`
- `task.completed`

### State Machine

- `state_machine.run.created`
- `state_machine.run.started`
- `state_machine.node.started`
- `state_machine.node.completed`
- `state_machine.node.retry_scheduled`
- `state_machine.run.completed`

### Message

- `message.created`

`message.created` 只适用于自由聊天和 ManagerWorker 群的逻辑聊天消息。

## 4. 实现分层

### Contract

- Event envelope、Subscription、Delivery、Attempt、错误码定义在
  `bcs-service-api`；
- JSON Schema Catalog 是公共事件名称和 Payload 的权威来源；
- OpenAPI 只暴露 Group scope、Webhook sink 和 strict ordering。

### Application

- `bcs-eventing` 负责订阅 CRUD、授权、过滤、Payload 投影、fanout、重试、
  DLQ、replay、skip 和生命周期；
- Application 层不依赖 HTTP 或数据库实现。

### Persistence

- `bcs-event-store` 提供 Memory 与 DB 实现；
- DB 通过 `DbPlugin::transaction` 组合业务 mutation 与 Event append；
- Memory 实现先构造 candidate state，Event batch 全部成功后一次发布。

### Delivery

- `bcs-webhook-client` 只负责 JSON body 发送、安全地址校验与错误分类；
- dispatcher 负责领取、顺序、重试和状态迁移；
- HTTP route 只做 DTO 映射。

### Composition

- bootstrap 注入 Repo、Webhook client、workers 和 metrics；
- 生产方仅依赖 `EventRecordFactoryPort`，不直接依赖 Event Store。

## 5. 一致性矩阵

| 业务事实 | 原子提交要求 |
| --- | --- |
| Group 创建 | Group 可见、内联 Subscription 激活、`group.created`、初始 `session.created` 同事务 |
| Group 成员增删 | Group version/participant 与 Event 同事务 |
| Group 删除 | Group 删除、直接 Group Subscription 禁用、pending target 与 pending/retry Delivery 取消同事务；不产生 `group.deleted` |
| Session 创建/完成/成员增删 | Session mutation 与 Event 同事务 |
| Message 创建 | session message seq、message row 与 Event 同事务 |
| Task assign/complete | Event 先成为 durable fact，再更新暂存的内存 Ledger |
| State Machine run 启动 | Pending -> Running 与 run.created/run.started Event batch 同事务 |
| State Machine 节点与 run 完成 | CAS 状态迁移与对应 Event 同事务 |

Task Ledger 持久化不在本期范围。当前做法确保不会出现“内存 Task 已成功但 durable
Event 缺失”；进程崩溃恢复 Ledger 的问题由后续 Ledger 持久化独立解决。

## 6. 顺序规则

- lane key：`(subscription_id, stream_key)`；
- Group：`group:{group_id}`；
- Session 与 Message：`session:{session_id}`；
- Task：`task:{task_id}`；
- State Machine：`state-machine-run:{run_id}`；
- 前序 Delivery 成功或显式 skip 前，后序 Delivery 不可领取；
- 前序进入 DLQ 时 lane 阻塞，replay 成功或 skip 后恢复；
- 不同 lane 可并发。

状态机同一 run 的所有公共事件使用同一个 stream，因此顺序执行时 Webhook 到达顺序
与 Event Store sequence 一致。Webhook 返回 2xx 后，BCS 不保证接收方内部异步处理
完成顺序。

## 7. 消息持久化改造

原实现把聊天历史视为附属能力：写失败只记录日志，主投递继续执行。这能降低历史库
故障对实时聊天的影响，但会造成业务返回成功而消息历史、sequence 和 Hook 缺失。

本期改造：

1. 写失败向上返回；
2. DB 中 sequence 分配、message insert、Event append 使用同一事务；
3. Memory 中使用 candidate state 原子发布；
4. 不再把 `message.created` 建立在 best-effort 历史之上。

## 8. 开发任务与状态

| 任务 | 状态 |
| --- | --- |
| Event Contract、Catalog、JSON Schema | 完成 |
| Subscription / Delivery API 与 OpenAPI | 完成 |
| Memory / DB Event Store | 完成 |
| Webhook client 与 endpoint 脱敏 | 完成 |
| Fanout、strict lane、retry、DLQ、replay、skip | 完成 |
| Group 创建内联订阅与 Group 事件 | 完成 |
| Session 事件与原子提交 | 完成 |
| Message 持久化错误传播与原子提交 | 完成 |
| ManagerWorker Task 事件 | 完成 |
| State Machine 事件与原子 CAS | 完成 |
| Group 删除时自动禁用订阅并取消未开始投递 | 完成 |
| 删除超出 Catalog 的事件与 future-only 状态 | 完成 |
| Contract、单元、SQLite 集成验证 | 完成 |

## 9. 验证清单

### Contract

- Catalog 中只存在 16 个事件；
- schema fixture 全部通过；
- OpenAPI 不出现非 Group scope、非 strict ordering 或 draining；
- public DTO 不接受 `auth`，完整 endpoint URL/path 不可序列化回显。

### Store

- producer key 幂等；
- stream sequence 单调且并发不重复；
- 因果引用非法时整个 Event batch 回滚；
- 业务 mutation 与 Event append 任一失败时双方都不提交；
- DB 与 Memory 行为一致。

### Delivery

- 同 stream 后序不越过 pending/retry/DLQ 前序；
- 2xx 成功；可重试状态进入 retry；终止状态进入 DLQ；
- replay/skip 正确解除 blocker；
- payload bytes 在自动重试中保持不变；
- 每次 attempt 更新 timestamp 和 attempt，复用同一 Delivery body。

### Producer

- Group、Session、Message、Task、State Machine 每个 Catalog 事件至少一个测试；
- 非 Catalog 的 patch、mode update、delete 不产生公共 Event；
- 状态机 run.created -> run.started -> node events -> run.completed sequence 有序；
- 消息写失败不继续返回成功。

## 10. 完成标准

1. 相关 Cargo tests、Event Contract tests、OpenAPI tests 全部通过；
2. `cargo check -p bcs --all-targets` 通过；
3. 代码与文档搜索不到已删除的 future-only 公共能力；
4. Spec、Catalog、OpenAPI、数据库和生产方事件清单一致；
5. 回顾中明确记录例外：Task Ledger 仍为内存状态，但不影响 Event durable fact。

## 11. 最终验证记录

- Event/OpenAPI Python Contract：67 passed；
- Webhook delivery conformance：5 passed；
- Event Subscription Service API Contract：7 passed；
- Event Store、Eventing、Group Store、Group Application 定向回归：205 passed；
- Event HTTP routes：4 passed；V1 OpenAPI mount：7 passed；
- Spec 涉及的完整 Rust 定向 workspace：通过；
- `cargo check -p bcs --all-targets`：通过；
- `git diff --check`：通过。

依赖 `BCS_TEST_MYSQL_URL` 的 MySQL Event Store conformance 以及要求 MySQL 语法的完整 Group Repo 用例未在本机
执行；它们保持 ignored，由带 MySQL service 的 CI 运行。SQLite 使用同一 Event Store conformance suite，并已覆盖
新事务和 Group 删除清理路径。
