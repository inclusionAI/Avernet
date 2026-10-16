# StateMachine 最终结果发布与群消息队列的写入权归属

- 日期：2026-10-14
- 状态：已实现，受影响模块本地测试通过；完整架构门禁未通过，详见第 13 节。核心根因由真实 publisher + message-flow 回归测试复现，未取得线上事件日志。
- 所属模块：BCS
- 目标分支：`dev`
- 实施范围：`apps/bcs/`
- 关联缺陷：用户反馈「BCS 发布一次性状态机最终结果时，消息未投递给 manager，会话卡住」

## 1. 依据与术语

架构依据：

- [仓库领域文档规范](../../../../docs/agents/domain.md)
- [架构约束](../../../../docs/arch/arch.rules.md)
- [CI 约束](../../../../docs/arch/ci.enforce.md)
- [BCS 本地规则](../../AGENTS.md) 及 [分层约束](../../CLAUDE.md)
- [Session 级状态机历史设计](../2026-09-21-state-machine-message-history/spec.md)
- [任务消息队列设计](../2026-09-16-task-message-queue/spec.md)
- [Direct A2A 消息队列设计](../2026-09-20-direct-a2a-message-queue/spec.md)

| 术语 | 本文含义 |
| --- | --- |
| 最终结果发布 | 一次性 StateMachine Run 结束后，把 Run 的输出作为一条 `chat` 消息发布回会话，并唤醒 manager/driver 的过程 |
| 发布方（publisher） | `MessageFlowStateMachineResultPublisher`，`crates/bootstrap/bcs/src/server.rs` |
| 群消息队列 | 受管投递（managed delivery）子系统：消息行与投递行在同一次事务写入，投递由调度器异步执行 |
| legacy 直投 | 队列未接管时的同步投递路径：先持久化消息，再在该请求内直接调用 `BotDeliveryPort` |
| 稳定消息身份 | 由生产者键推导的消息 ID 与 `client_msg_id`，用于历史幂等 |
| 投递幂等键 | `bcs_message_deliveries.idempotency_key`，与消息恒等键不同（见 `delivery.rs` 的 `plan_admission`） |
| 受管 Bot | `DeliveryPolicy::manages_group(bot)` 为真：`flow_enabled.group` 开启且该 Bot `mode == Enforce` |

文中的「必须」是验收要求；「建议」为实现选择，不表示已实现能力。

## 2. 问题陈述

### 2.1 现象

一次性 StateMachine Run 结束后，最终结果**已写入会话历史**，但 **manager 从未收到 `chat.send`**，Run 记为已送达（`Outcome::Published`），此后不再重试，会话卡住且无法自愈。

### 2.2 证据链

发布方在同一个幂等键上做了两次写入，第二次被队列判为重复：

1. `server.rs:506` 生成 `idempotency_key = "state-machine-result:{run_id}"`。
2. `server.rs:508` 用该键**预写**历史行（显式 `MessageVisibilityDomain::StateMachine` + `MessageAudience::Public` + `Value::String(content)`）。这是**第一次写 `bcs_messages`**。
3. `server.rs:536` 用**同一个键**调用 `handle_web_send`，`idempotency_key: Some(idempotency_key)`。
4. `queued_admission.rs:539` 把 `client_msg_id: command.idempotency_key.clone()` 交给投递事务。
5. `delivery.rs:435-465` 的幂等查询 `SELECT message_id FROM bcs_messages WHERE env = ? AND session_id = ? AND sender_id = ? AND client_msg_id = ?` **命中第 2 步写下的那行**。
6. 由于本次事务无状态变更（`has_transition == false`），走 duplicate 分支，`deliveries` 取 `DeliveryLookup::Message(id)`（`delivery.rs:587`，谓词为 `source_message_id = ?`）。
7. 该 `message_id` 没有任何投递行引用——投递行只由 admit 事务创建——因此**返回 `duplicate=true` 且 `deliveries` 为空**。
8. `web_send.rs:88` 对 `duplicate` 无条件提前返回 `status: "accepted"`，不检查 `deliveries` 是否为空，也不进入 legacy 投递循环。
9. `runtime_publication.rs` 收到 `Ok(())`，提交 `Outcome::Published`。此后 `ensure_chat_result` 不再重试。

最终表现为：历史有结果、投递从未发生、恢复路径（`runtime_message_history.rs:646` 用同一个键重放）确认历史无恙，因此问题不会被任何既有机制发现。

### 2.3 根因

同一个 `client_msg_id` 被两个写入方共享，而队列对它的语义假设是**「该键下的消息行由 admit 事务自己写入，故消息存在 ⟹ 投递存在」**。发布方抢先把消息行写进了 `bcs_messages`，使这条不变式失效：队列把「消息已入库」误读为「本流程已投递」，返回了一个语义为空的成功。

补充：若同一次持久化还包含生命周期变更，`has_transition == true` 时重复消息会返回 `Conflict`；这与 `DeliveryFlowKind` 是两个独立条件。本结果发布链路没有生命周期变更，触发的是上述空投递成功分支。

### 2.4 触发条件

三个条件同时成立即触发：

1. 目标 Bot 受队列管辖（`manages_group` 为真）——托管投递的正常配置，非异常状态。
2. 一次性 StateMachine Run 的接收方是该 Bot。manager-worker 群中无 @mention 广播的 lead 是 manager（`group.rs:612`），因此**丢失的正好是 manager 是路由角色的必然结果，而非该策略本身有缺陷**。
3. 结果通过 `publish_state_machine_result` 发布（一次性 Run 的正常路径）。

**chat 群只要 Bot 受队列管辖同样会触发**，不限于 manager-worker。`prepare_group_admission` 开头对 `GroupStrategy::StateMachine` 的提前返回（`queued_admission.rs:467`）对一次性 Run 是**死代码**：一次性 Run 只允许在 `Chat | ManagerWorker` 群提交（`runtime.rs:2683` 的 `unsupported_group_strategy`）。

### 2.5 测试为何未拦住

- `state_machine_result_tests.rs` 使用 `NoopMessageFlowService`，其 `handle_web_send` 直接返回 `service_not_configured`，只覆盖历史写入与内容冲突。
- `StateMachineResultPublisherPort` 契约测试（`test-support`）只传 `session_id`，没有受队列管辖的 Bot。
- `publication_sqlite.rs` 中的 publisher 为测试替身。

即**没有任何测试把真实的 `BcsMessageFlow` + `managed_deliveries` 置于 publisher 之后**。

## 3. 目标与非目标

### 3.1 目标

1. 一次性 StateMachine 最终结果的投递与历史持久化在**队列开启和关闭两种配置下都正确**。
2. 对受管的结果发布恢复「admission 成功 ⟹ 消息及对应投递均已持久化」，不把所有历史消息视为必然有投递。
3. 所有路径保持历史幂等；受管路径不产生第二组投递。legacy 不承诺网络投递幂等，混合目标的模糊重复调用须报错，不能盲目重放。
4. 修正过程中不改变既有历史读取契约、投递顺序约束与并发（lane）语义。

### 3.2 非目标

- 不改动状态机调度、节点转移、judge 结果、取消与超时规则。
- 不改动 `DeliveryFlowKind::StateMachine` 的预留语义（见 5.3）。
- 不引入历史回填、对账或旧数据迁移。
- 不重新设计队列的幂等模型，也不把 `duplicate` 分支的通用语义改为「必须非空」——仅对发布路径增加防御。
- 不新增对外 HTTP 契约。

## 4. 方案

### 4.1 核心原则

**消息行的写入权归属队列**：状态机结果不再预写 `bcs_messages`，而是由投递事务在写入投递行的同一次事务中写入消息行。这与现有任务结果路径同构。

现状与目标的对照：

| 步骤 | 现状 | 目标 |
| --- | --- | --- |
| 写历史行 | 发布方 `append_message_with_id`（抢跑） | admit 事务内原子写入 |
| 路由与投递 | `handle_web_send` | 专用 application 入口复用群路由及投递，显式消息由内部持久化分支写入 |
| 重试时 duplicate 命中 | 发布方写的孤儿行 → 空 `deliveries` | 队列自己写的那行 → 真实投递行 |

### 4.2 专用入队入口，而非透传参数

为状态机结果提供**一个统一的漏斗函数**，由它完成「路由 → 拆分受管与不受管 target → 入队或直投」。

强调：**该入口不等于「永远入队」**。队列未接管时（`managed_deliveries` 未配置，或 `manages_group` 为假）它走 legacy 直投，与今天一致。入口存在的意义是让**两种分支都使用显式行形状**（见 4.4），而不是把队列强制打开。

分层要求（依 `apps/bcs/CLAUDE.md`）：入口应作为 message-flow 的 application 接口暴露（例如 `MessageFlowService::publish_state_machine_result`），由 `bcs-message-flow` 内部组装 `AdmitMessageDeliveries`。发布方位于 `bootstrap/bcs`，只依赖该接口，**不得直接调用 `ManagedDeliveryService`**。

### 4.3 参照实现：任务结果回传

`queued_task_terminal.rs` 中 worker 结果回传 manager 已是同构场景，可作为模板：

- 稳定 `message_id`：`result_message_id(&task.task_id)` → `task-result:{task_id}`（`queued_task_terminal.rs:72`）。
- 行形状由该路径**自己填写**：`content`、`visibility_domain`、`audience` 均显式指定。
- 与生命周期 CAS 在同一次事务提交（`transition(..., reply: Some(reply))`）。
- **不预写历史行**。

`queued_task.rs:112` 与 `task_failure.rs:110`、`:137` 也采用「稳定 ID + 显式形状 + 直接 admit」的写法，均无预写。

### 4.4 必须显式指定的两个字段

以下两点是**该方案能否成立的前提**，不可省略：

**（a）可见域必须是 `StateMachine`，受众必须是 `Public`。** 不能按普通群策略落成 `ManagerWorker` 或 `Chat`，否则与原始发布事实及历史修复校验不一致。

**（b）内容必须为 `Value::String(content)`，并保留原始 `run_id` 与 `created_at_ms`。** 通用群入队默认使用 `{"text":...,"mentions":[]}`、空 Run ID 和当前时间，均不适用于该发布事实。队列 payload 重建也必须支持这一显式字符串形状，包括作为后续 Send 的绑定 Inject 上下文时的读取。

最终结果仍是一条普通 `chat` 历史消息。`list_state_machine_history` 与 `get_state_machine_messages_by_keys` 明确排除发布的 chat，只读取面板/节点输出等过程记录；本次不改变该读取契约。应从普通会话历史核验结果，并检查其 StateMachine/Public 属性。

### 4.5 legacy 分支也必须能写显式形状

这是删掉预写后**新增的改动点**，容易遗漏。

现状之所以在队列关闭时表现正常，完全是因为发布方抢先把正确形状的行写好了；legacy 的 `try_persist_group_message` 随后通过 `client_msg_id` 幂等命中该行并**继续投递**（`mysql.rs:105-131`），不会提前返回。

而 `try_persist_group_message` 内部固定走群策略推导（`group_flow/persistence.rs:60`），**没有传入显式形状的口子**。因此删掉预写后，需要为 legacy 分支提供一个接受构造好的 `NewMessage` 的持久化入口，否则队列关闭时结果行会落成错误的域、Run ID 或时间戳，破坏历史一致性。

分支结构：

```
route → targets
  ├─ 有受管 target → admit(显式形状消息 + deliveries，同一事务写入)
  └─ 无受管 target → 用显式形状写行(幂等)
随后：不受管 target 走 legacy 直投；已在 admission 中的 target 跳过
```

约束：

- **受管分支不得预写**，否则 duplicate 分支会再次命中孤儿行，退回原缺陷。
- 混合 target 时，admit 先写唯一消息行，随后仅对不在 admission 中的 target 直投，无需再次写历史。manager-worker 的普通广播只路由 manager；混合接收方回归用 chat 群覆盖。
- **不得写死 target**。target 必须由 `routing.route_with_overlay` 得出（manager-worker 群 lead 为 manager，chat 群 lead 为 driver），否则 chat 群会错误发给 manager。
- 需经过 `guard_legacy_targets`（`queued_admission.rs:23`）以保留 lane 顺序与并发约束。

### 4.6 发布方保留的职责

1. 删除预写（`server.rs:508`）。
2. 调用新入口。
3. 在 message-flow 内对「admit 返回 `duplicate` 但 `deliveries` 为空」报错。这是**防御性检查**：方案落地后该状态不应再出现，但保留可让同类不一致显式失败，而非静默成功。

原 publisher 的内容与身份不可变校验移至 message-flow 发布路径：队列分支核对 admission 返回消息，legacy 分支核对幂等写入返回消息，必须在返回发布成功前完成。不能仅依赖事后的 `matches_message` 历史修复。冲突返回 `Conflict`，不再投递 legacy 接收方。

### 4.7 成功、失败和重复调用

- 成功表示受管接收方获得有效持久化投递，legacy 接收方同步确认接受，不表示 Bot 完成回复。
- 空投递、RejectedCapacity、Failed、Cancelled、Expired、丢弃或不确定/取消中的投递不得被确认成发布成功。无 Send 目标也必须失败，不能把仅保存上下文当成唤醒成功。
- legacy 的外层 `Ok(WebSendOutcome)` 不代表投递成功；专用入口必须检查每个直接投递结果。
- 所有目标受管时，相同事实重复发布复用已有消息和投递；事实不一致返回错误。
- 混合目标重复 admission 只能证明受管方已接收，没有 legacy 投递账本可证明直投结果，必须返回明确的模糊重复错误，不能跳过或重放 legacy 目标并宣告成功。
- legacy 仍遵循既有 publisher port 契约：历史幂等不承诺网络幂等，不得盲目重放模糊调用。本次不新增持久化直投账本或自动重试策略。
- runtime 的既有发布失败/超时处理保持原样；不会自动补救已经标为 Published 的旧孤儿记录。

## 5. 备选方案与取舍

### 5.1 方案 B：透传 `stable_message_id` 进 `WebSendCommand`

在 `WebSendCommand` 增加字段，由 `prepare_group_admission` 使用，并删除预写。

- 优点：改动集中，复用既有路由与投递代码。
- 缺点：行形状与可见域的决定权散落到群消息的通用参数里，需要在 `prepare_group_admission` 内加分支特判；`WebSendCommand` 有约 57 处构造点需补字段。
- 弃用原因：形状归属不清晰，且未解决 4.5 的 legacy 分支问题。

### 5.2 方案 C：为路由使用独立幂等键

发布阶段改用 `state-machine-route:{run_id}`，不再与历史键共用。

- 优点：最小改动，可作热修。
- 缺点：虽然稳定的新键仍可对队列投递判重，但历史和投递被拆成两条消息身份，通用路径还会生成不同的内容封装、Run ID 和可见域，未解决消息写入权与行形状归属。
- 定位：**仅作为止血选项**，不作为最终形态。

### 5.3 关于 `DeliveryFlowKind::StateMachine`

该枚举变体已存在（`message_delivery.rs:15`），并有配套的 `flow_enabled.state_machine` 开关，但当前处于未就绪状态：`validate_ready_flows` 返回 `FlowNotReady`，`managed_delivery.rs:690` 在 admit 时直接返回 `Conflict`。

**不建议**用它承载本结果，理由：

1. **流量开关会分裂。** 结果消息与普通群消息发往同一 Bot、同一 Session，即同一条 lane。若使用 `StateMachine` kind，运维只开 `group` 而未开 `state_machine` 时，结果会退回 legacy 直投；此时 `guard_legacy_targets` 因 lane 中存在未结束的 group 投递而返回 `queue_draining`（`queued_admission.rs:62`），发布失败；若绕过该守卫，则破坏 lane 顺序与 `max_running` 并发约束。
2. **改动面大。** 需放开 `admit_batch` 白名单、新增 `manages_state_machine`、调整 `queued_group.rs:524` 的 projection 校验（现仅接受 Group/System/Task），并同步 `delivery_failure_notice` 与 ready-flow 校验。
3. **语义不符。** 该变体更可能预留给「状态机节点派发给 Bot」这类能力；结果发布在业务上就是一条发进群给 lead 的消息。

**采用 `DeliveryFlowKind::Group`**，与群消息共用开关与 lane，并可复用 `QueuedGroupProjection::capture` 与 `prepare_queued_group`。

## 6. 契约变更

| 位置 | 变更 | 影响 |
| --- | --- | --- |
| `MessageFlowService`（application） | 新增发布入口，内部完成路由/分流/入队 | message-flow 对外新增方法 |
| `AdmitMessageDeliveries` | **不新增字段**；`message_id`、`NewMessage` 的 `visibility_domain`/`audience`/`content` 已可由调用方指定 | 无结构变更 |
| `try_persist_group_message` / 持久化入口 | 需支持显式 `NewMessage` | `bcs-message-flow` 内部 |
| `WebSendCommand` | **不新增字段** | 无 |
| `StateMachineResultPublisherPort` | 语义职责收窄为「提交入队并确认」 | 契约注释需同步更新 |
| 消息行 `message_id` | 由 `physical_message_id("state-machine-result:{run_id}")` 统一生成 | 见 8.2 |

## 7. 验收标准

1. **队列开启 + manager-worker 群 + manager 受管**：发布后 manager 存在 `chat.send` 投递记录；结果行 `visibility_domain == StateMachine`、`audience == Public`；该行可被普通会话历史读到。
2. **队列关闭（或目标 Bot 不受管）**：结果行同样为 `StateMachine` 域与 `Public` 受众，且能进入普通会话历史。
3. **混合 target**：受管 target 入队、不受管 target 直投，两者均送达；不产生重复消息行。
4. **重复发布幂等**：受管路径同一 Run 第二次发布不产生第二行历史或第二组投递；混合路径按 4.7 拒绝模糊重放。
5. **历史内容校验**：`matches_message` 在新形状下通过；同 Run 内容变更的发布被拒绝。
6. **chat 群**：lead 为 driver 时投递到 driver，不被写死为 manager。
7. 既有状态机历史、任务队列、Direct A2A 及群消息契约测试全部通过。

## 8. 验证计划

### 8.1 新增测试

当前缺失的正是「真实 `BcsMessageFlow` + `managed_deliveries` + 真实 publisher」这一组合，必须补齐：

- 集成测试（队列开启）：manager-worker 群、manager 受管，断言投递记录存在且 `chat.send` 投递到 manager。
- 集成测试（队列关闭）：断言结果行落在 `StateMachine` 域并可被状态机历史读取。
- 幂等测试：同一 Run 发布两次，断言行数与投递数均不增长。
- 混合 target 测试：受管 + 不受管并存。
- chat 群测试：lead 为 driver。

建议先写**队列开启与关闭两个失败测试**钉住现状，再动 `queued_admission.rs`。

### 8.2 需一并核对的既有行为

- `repair_existing_history`（`runtime_message_history.rs:646`）使用 `physical_message_id(&key)` 作为行 ID，而发布方预写当前直接使用原始键（`server.rs:508`）。两者仅在键长 ≤64 时等价；`"state-machine-result:"` 加 UUID 为 57 字符，当前一致。新设计必须统一使用 `physical_message_id`，避免未来 `run_id` 形态变化导致分叉。
- 最终 chat 不属于 `get_state_machine_messages_by_keys` 的读取类型；验证稳定 ID 与普通会话历史、`repair_existing_history` 的一致性。
- `state_machine_result_tests.rs` 前提已改变，需重写。

### 8.3 验证要求

落地时须完成：受影响 crate 的单测与契约测试、`apps/bcs` 的模块门禁，以及依据 `docs/arch/ci.enforce.md` 的 CI 检查。生产者路径变更须按 `apps/bcs/AGENTS.md` 的「Database Access Cost」要求核算 admit 事务的数据库往返与批大小变化。

## 9. 兼容性与风险

| 风险 | 说明 | 处置 |
| --- | --- | --- |
| 历史行形状变更 | 若新路径未显式指定域、形状、Run 与原始时间，会破坏历史事实及修复校验 | 4.4 的两项为硬前提，须有测试覆盖 |
| legacy 分支未覆盖 | 队列关闭场景可能被忽略 | 验收标准 2 与 8.1 的队列关闭测试 |
| 混合 target 重复写行 | admit 与 legacy 各写一行 | 依赖 `client_msg_id` 幂等；需测试确认 |
| 重复发布 | 重试可能产生第二组投递 | 验收标准 4 |
| 投递行是否会被清理 | 未在代码中发现投递行的删除/GC 路径；若未来引入，「空 `deliveries` 恒为病态」的前提需重新界定 | 实现前复核 |
| 并发 | 发布与群消息共用 lane，须保持顺序与 `max_running` | 复用既有分流与 `guard_legacy_targets` |

回滚路径：改动集中在 `bcs-message-flow` 的发布入口与 `bootstrap/bcs` 的发布方，可按 commit 回滚；无 schema 变更，无数据迁移。

## 10. 实施切片

| 切片 | 内容 | 依赖 |
| --- | --- | --- |
| S1 | 新增失败集成测试（队列开启 + 队列关闭），钉住现状 | 无 |
| S2 | 提供 legacy 分支的显式形状持久化入口（4.5） | 无 |
| S3 | 实现专用入队入口：路由复用、受管/不受管拆分、显式形状与可见域 | S2 |
| S4 | 发布方删除预写，改调新入口，加入空 `deliveries` 防御检查 | S3 |
| S5 | 重写 `state_machine_result_tests.rs`；补齐 8.1 测试 | S4 |
| S6 | 核对 8.2 的三项既有行为 | S4 |

可选的止血动作：在 S1–S4 落地前，可先实施方案 C（独立幂等键）以停止线上静默丢投递，随后由本方案替换。

## 11. 未验证边界

- 未复现线上现象，未取得线上日志比对。
- 未运行任何测试，未验证文中引用的行号与行为在最新 `dev` 之外的分支是否一致。
- 未评估一次性 Run 在 channel（IM）入口下的行为差异。
- 未确认是否存在跨环境的发布方配置差异（`server.rs` 中有三处 `MessageFlowStateMachineResultPublisher::new` 装配点：`:2485`、`:4100`、`:4980`）。

## 12. 实现与数据库访问成本

- `MessageFlowService::publish_state_machine_result` 为 application 入口，bootstrap publisher 仅委托。
- `group_flow/state_machine_result.rs` 拥有显式消息形状、稳定 ID、冲突和发布结果检查；复用 web_send 的路由、上下文、队列选择及 legacy 投递。
- 原子 admission 替换原有预写加 admission，不新增表、索引或 schema。队列路径不再执行额外的预写及其查询/序号分配；duplicate 直接使用 admission 已返回的消息和投递进行校验，不新增数据库读取。
- legacy 路径仅一次幂等写入，事件启用时用已有消息/事件事务；无需额外按 ID 查询。混合目标由 admission 写一次历史，再只直投不受管接收方。
- target 数、批大小、队列扫描、重试、lane 守卫和连接池使用沿用已有实现。本次无新增轮询、缓存或跨网络数据库事务。失败不回退到另一条写入/投递路径。
- 实际测试结果见随变更交付的验证记录；未执行的门禁必须如实列明。

## 13. 本地验证记录（2026-10-16）

- 修复前：真实 publisher + BcsMessageFlow + managed delivery 回归失败，发布返回成功但没有 manager/driver Send 记录；同一组 legacy 基线通过。
- 修复后：`cargo test --manifest-path apps/bcs/Cargo.toml -p bcs --lib state_machine_result_tests --no-default-features`：16 项通过。包含 queue on/off、队列存在但目标不受管、manager/driver 路由、原始行形状、长 Run ID、重复/冲突、孤儿行、容量拒绝、下行错误、混合目标、lane drain、字符串 Inject 上下文和消息事件。
- `cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-message-flow -p bcs-message-store -p bcs-service-api`：62 个测试套件，共 896 项通过。覆盖既有 Group/System/Task/Direct A2A 与 Memory/SQLite repository conformance；新字符串 admission 形状也进入共享仓储契约测试。
- 状态机相关测试首次并行运行时，一项日志捕获计数断言失败（期望 4 条、得到 3 条）；单独复跑通过。`cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-collaboration-runtime --test runtime_progression -- --test-threads=1`：258 项全部通过。未改动该日志测试或 runtime 实现。
- `git diff --check` 通过。新增源文件及其他修改源文件均小于 1,000 行；既有 bootstrap `server.rs` 从 7,053 行降至 6,993 行，未扩大其历史大文件规模。

架构门禁与未完成验证：

- 已执行 `apps/bcs/scripts/ci/arch-check.sh`；依赖检查脚本在现有变量解析处报错，导入边界和 R25 检查报告既有问题。
- 从 HEAD 导出的未修改源码重跑导入边界检查，按文件/规则归一化的 51 条报告与本次工作区完全一致，无新增导入边界报告。未修改或放宽任何门禁。
- R25 后续通过 `cargo test --workspace --no-fail-fast -- --list` 编译整个 workspace，导致本机磁盘不足；该步骤及架构 runner 已中止，并清理本次生成的对象文件缓存。之后重新完成上述发布专项及相关模块测试。完整架构门禁不能记为通过。
- 未执行完整 workspace 测试、统一 Singlebox E2E/覆盖率、真实 MySQL 集成测试或线上复现；本次没有 schema/migration 变更，也没有修复既有 Published 孤儿数据。
