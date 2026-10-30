# StateMachine 运行失败或取消时可靠唤醒 manager

- 日期：2026-10-30
- 状态：设计稿，待维护者评审（Issue #2610，队伍 Wake-Up Call）；已有对应实现，见第 15 节
- 所属模块：BCS（`bcs-collaboration-runtime`、`bcs-collaboration-store`、`bcs-message-flow`、
  `bootstrap/bcs`）；singlebox「谁是卧底」主持人技能
- 目标分支：`oss-camp2026`
- 实施范围：`apps/bcs/`、`apps/bcs/api-contracts/events/v1/`、
  `singlebox/agents/6bots_undercover_game_profile/`
- 复现记录：[repro.md](repro.md)

文中的「必须」是验收要求；「建议」是实现选择，不表示已实现。

## 1. 依据与术语

依据：

- [架构约束](../../../../docs/arch/arch.rules.md)、[CI 约束](../../../../docs/arch/ci.enforce.md)、
  [BCS 本地规则](../../AGENTS.md) 及 [分层约束](../../CLAUDE.md)
- [任务消息队列](../2026-09-16-task-message-queue/spec.md)、
  [Manager/Worker 状态通知](../2026-09-22-manager-worker-status-notifications/spec.md)
- [Session 级状态机历史](../2026-09-21-state-machine-message-history/spec.md)、
  [StateMachine 最终结果发布与群消息队列](../2026-10-14-state-machine-result-queue-admission/spec.md)

| 术语 | 本文含义 |
| --- | --- |
| manager | 提交 Run 的 Bot，即 `run.created_by`；在谁是卧底中是主持人 |
| 终态迁移 | Run 从 `pending` / `running` 首次进入 `failed` 或 `aborted` 的那一次状态 CAS |
| RunResult | 本文新增的、只投递给 manager 的结构化终态结果消息 |
| 结果检查点 | `bcs_collaboration_delivery_checkpoints` 中 `operation_kind = 'run_result'` 的一行 |
| 受管投递 | 群消息队列：消息行与投递行同事务写入，由调度器异步发送 |
| legacy 投递 | 目标 Bot 未被队列接管时的同步直投（singlebox 默认即此路径） |

## 2. 问题陈述

### 2.1 现象

本地复现见 [repro.md](repro.md)：发言 Run 中杀掉一个玩家 Bot，派发失败后 Run 立即 `failed`
（`NODE_MAX_ATTEMPTS = 1`）；此后 3 分 40 秒内主持人没有收到任何投递，会话没有新消息，
公开事件里也没有 `state_machine.run.failed`。对局只能等人类说「卡住了」。

### 2.2 代码证据链

1. **完成路径会唤醒 manager。** `complete_run_if_done`（`runtime.rs` 约 1774 行）在 Chat 会话中
   先调用 `ensure_chat_result`（`runtime_publication.rs:10`），以 manager 身份把产物发回会话，
   再用 `commit_eventful_transition(CompleteRun)` 把状态迁移和 `state_machine.run.completed`
   同一事务提交。
2. **失败路径什么都不发。** `fail_run`（`runtime.rs:1915`）只调用 `update_run_status`，它是一条
   独立的 `UPDATE … WHERE status NOT IN ('completed','failed','aborted')`
   （`bcs-collaboration-store/src/lib.rs:3265`）；随后作废未完成派发、写日志。Chat 会话中没有任何
   投递，也没有公开事件。
3. **取消路径相同。** `cancel_state_machine_run`（`runtime.rs:4034`）也只更新状态。
4. **事务模型里没有失败变体。** `StateMachineEventfulTransition`（`port/repo/collaboration.rs:122`）
   有 `CompleteRun`，没有失败或取消。

### 2.3 根因

StateMachine 只在成功路径上回答了「下一次谁来叫醒 manager」。

此外，`fail_run` 的终态 CAS 与之后的任何写入不在同一事务。恢复路径会重入 `fail_run`
（例如 `runtime.rs:2102`），此时 CAS 返回 false，函数直接返回。所以如果只是在 CAS 之后
「补发一条消息」，进程在两步之间崩溃时通知会永久丢失。投递的**意图**必须与终态迁移原子提交。

### 2.4 影响面

- 谁是卧底的发言与投票都是 StateMachine Run，`NODE_MAX_ATTEMPTS = 1`。任一玩家节点出错，
  或人类节点超时（15 分钟），都会让 Run 失败、对局停住。
- 一般化：任何由 Bot 在 Chat 会话中提交一次性 Run、并依赖 Run 结束来推进流程的 manager，
  都会在失败时失去唤醒源。

## 3. 目标与非目标

### 3.1 目标

1. Run 首次进入 `failed` / `aborted` 时，向 `run.created_by` 可靠投递**恰好一次** RunResult。
   覆盖回调与控制路径竞争、恢复重放和进程崩溃重启。
2. 补发公开事件 `state_machine.run.failed` / `state_machine.run.aborted`，供前端与副屏使用。
3. RunResult 与公开事件中不出现节点产物、未脱敏的错误全文和玩家的词。
4. 主持人技能收到 RunResult 后按 SX 流程自动重开当前阶段（同一轮最多两次；
   `user_aborted` 不自动重试）。「卡住了」降级为最后兜底。
5. 不发生失败时，行为与现状一致。

### 3.2 非目标

- 不重写调度器，不修改 HumanInput 契约，不新增 YAML 失败钩子。重试次数由 manager 决定，
  平台只负责可靠告知一次。
- 第一版只覆盖 Chat 会话。非 Chat（服务）会话保持现有的会话收尾与 terminal IM。
- 不改变 completed 路径（`ensure_chat_result`）。
- legacy 直投不承诺网络层幂等，这与 2026-10-14 规范的结论一致（见 7.5）。

## 4. 方案总览

```text
fail_run / cancel_state_machine_run
  └─ 一个本地事务：commit_eventful_transition(TerminateRun)
       ├─ CAS：status ∈ {pending, running} → failed | aborted
       ├─ 追加公开事件 state_machine.run.failed | state_machine.run.aborted
       └─ 写入结果检查点 smrun:{run_id}:run-result（status = pending）
  └─ 事务提交后：ensure_run_result（尽力一次）
       └─ 领取租约 → StateMachineResultPublisherPort::publish_run_result
            └─ message-flow：边界检查 → 受管 Send 入队 / legacy 直投
                 └─ manager 收到 chat.send（只给 Bot，不进人类可见历史）
StateMachine progression scanner（周期运行）
  └─ recover_state_machine_run_results：领取 pending 或租约过期的检查点，重走上一步
```

关键点：

- **意图与终态同事务**：检查点和事件跟终态 CAS 一起提交，崩溃后由扫描器补投，不依赖重入 `fail_run`。
- **投递与事务分离**：数据库事务内不做网络 I/O（`AGENTS.md` 的 DB 成本约束）。
- **队列幂等**：消息身份固定为 `run-result:{run_id}`，受管路径由队列幂等保证不重复入队。

## 5. 触发点与事务

### 5.1 新的终态迁移

在 `StateMachineEventfulTransition` 中新增一个变体：

```rust
TerminateRun {
    run_id: String,
    status: StateMachineRunStatus,        // 只允许 Failed 或 Aborted
    error: Option<String>,                // 仍写入 runs.error_message，供排障；不进入结果与事件
    terminated_at_ms: u64,
    event: Option<AppendEventRecord>,     // state_machine.run.failed | .aborted
    result: Option<StateMachineRunResultCheckpoint>,
}
```

三个 store 实现（Memory / SQLite / MySQL）都必须在**一个本地事务**内完成：

1. 锁定并确认 Run 仍在非终态：`status IN (pending, running) AND record_status = active`
   （MySQL 加 `FOR UPDATE`）；
2. 带同样的状态条件更新 `bcs_state_machine_runs` 的 `status`、`error_message`、`completed_at_ms`；
3. 若 `result` 存在，写入 `bcs_collaboration_delivery_checkpoints`：
   - `operation_key = smrun:{run_id}:run-result`
   - `aggregate_kind = state_machine_run`
   - `operation_kind = run_result`
   - `status = pending`，并带 `deadline_ms`
4. 用 `EventAppendTransactionPlan` 追加事件，做法与 `CompleteRun` 相同。

任一步失败则整体回滚，返回 `false` 或 `Conflict`。这张检查点表已经承载
`smrun:{run}:chat-result`（`publication.rs`）和 `smrun:{run}:im-terminal`（`terminal_im.rs`），
`operation_kind` 是不带枚举约束的 `VARCHAR(32)`，**不需要新表或迁移**。

`fail_run` 与 `cancel_state_machine_run` 改用 `TerminateRun`，`update_run_status` 不再用于进入
`failed` / `aborted`。CAS 只有一个赢家，所以：

- **回调与控制路径竞争**（例如 Bot 回报失败与超时扫描同时到达，或用户取消与节点失败同时到达）：
  只有先提交的事务写入检查点和事件，后到者 CAS 失败，整个事务回滚；
- **恢复重放**：重入 `fail_run` 时 CAS 失败，不会产生第二个检查点；已存在的检查点由扫描器负责投递；
- 检查点主键 `(env, operation_key)` 唯一，作为第二道保险。

### 5.2 何时写检查点

满足以下全部条件时写入 `result`，否则只做状态迁移和事件：

1. 会话存在且 `session_kind == Chat`（第一版范围）；
2. `run.created_by` 有值（由 Bot 提交）；
3. 终态原因不是 `session_closed` / `group_closed`：这两类取消由会话结束或群关闭触发，
   收件方上下文已经不存在。

「manager 仍是会话参与者、会话仍在运行、群未关闭」**不在写入时判断，而在投递时判断**
（见 7.3），与 TaskResult 一致：写入时只记录事实，授权以发送前的最新状态为准。

### 5.3 原因类别

`fail_run(run, error)` 改为 `fail_run(run, RunFailure { reason, node, error })`，由调用点给出结构化
原因，不再从错误文本里猜：

| 调用点 | 原因类别 |
| --- | --- |
| `fail_node_and_run`（`runtime.rs:933`）、`resume_failed_node` 末尾（`runtime.rs:2102`） | 失败节点是 HumanInput 且原因是超时时为 `human_timeout`，其余为 `node_failed` |
| 启动与重跑阶段：`runtime.rs:1150`、`1167`、`3088`、`3212`，以及 `converge_missing_startup` | `startup_failed` |
| `ensure_chat_result` 发布失败（`runtime_publication.rs:40`、`64`） | `publication_failed` |
| `cancel_state_machine_run_for_human` / `…_with_access` | `user_aborted` |
| `cancel_session_runs` / `cancel_group_runs` | `session_closed` / `group_closed`（只发事件，不写检查点） |

`publication_failed` 是自反场景：结果发布给 manager 失败，又要把失败告诉同一个 manager。它仍然
写检查点，因为 RunResult 是独立的消息身份，走独立的投递，可能成功；如果再次失败，检查点进入
`failed`，不会递归。

## 6. RunResult 内容契约

固定文本头加短字段，便于 manager 用规则识别，也便于人工排障：

```text
[State Machine Run Result]
run_id: sm-cb8caf8d-e534-4322-96e7-52451f5bf382
workflow: 谁是卧底 第1轮 发言
status: failed
reason: node_failed
failed_node: speak_3
attempt: 1/1
completed_nodes: speak_1, speak_2
```

规则：

- 字段固定为上面七项。`failed_node` / `attempt` 只在 `failed` 时出现；`aborted` 时为空。
- `workflow` 取自 manager 提交的定义名，`failed_node` 与 `completed_nodes` 只含节点 ID。
- **不包含**节点产物（`artifact_text`）、upstream artifacts、`runs.error_message` 原文、
  取消时调用方的自由文本 `reason`。谁是卧底的玩家词只出现在节点 instruction 与产物中，
  因此不会进入 RunResult。
- `completed_nodes` 最多列 32 个，多出部分写 `… (+N)`；整条文本上限 1 KiB。
- 检查点 `payload_json` 存结构化字段（含 `version`、`group_id`、`session_id`、`target_bot_id`），
  文本由 message-flow 在投递时渲染，因此格式演进不需要迁移旧检查点。

## 7. 投递

### 7.1 端口与分层

- 在 `StateMachineResultPublisherPort`（`port/state_machine_result.rs`）上新增
  `publish_run_result(StateMachineRunResultPublishCommand) -> Result<RunResultOutcome>`，
  默认实现返回 `service_not_configured`。
- bootstrap 中的 `MessageFlowStateMachineResultPublisher` 委托给 `MessageFlowService` 的新方法，
  同时在 `message_flow_metrics.rs` 中转发。接线位置与现有结果发布相同
  （`server_construct.rs`、`server_infrastructure.rs`、`state.rs`）。
- message-flow 新增 `group_flow::state_machine_run_result::publish`，与
  `group_flow::state_machine_result::publish` 并列。
- 运行时只依赖端口，不直接依赖 message-flow，符合 `CLAUDE.md` 的 core / port 分层。

`RunResultOutcome` 取 `Delivered`、`Skipped { reason }`、`Failed { error }` 之一。

### 7.2 消息身份与可见性

- 消息 ID 为 `physical_message_id("run-result:{run_id}")`。`run_id` 最长 128 字符，必须哈希，
  与 `state-machine-result:` 相同。`client_msg_id` 为 `run-result:{run_id}`。
- 发送者为 manager 本人（`SenderType::Bot`），与完成结果的发布方式一致。在主从群中，
  无 @ 消息的 lead 就是 manager，worker 收不到群广播。
- 新增 `message_type = "run_result"`，并像 `run_reply` 一样从所有历史查询中排除
  （`bcs-message-store` 的 MySQL 与 Memory 实现各两处）。这样人类时间线和副屏历史都看不到它，
  实现「只给 Bot」。可见性同时显式设为 `StateMachine` / `FullOnly`，作为第二道保险。
- 不写 `message.created` 公开事件。人类可见的信号统一由 8 节的 run 事件提供。
- 消息存储的准入校验（`bcs-message-store/src/delivery/admission.rs`）显式接受这种形态，并且只接受这种形态：
  Group 流、单个 Send 目标、`StateMachine` / `FullOnly`、Bot 发送者、带 `run_id`、字符串内容、没有消息事件。
  其他组合一律拒绝。
- **不预写历史行**。这是 2026-10-14 规范的教训：预写会让队列的幂等查询命中历史行，返回
  `duplicate=true` 且投递为空。admission 结果沿用 `ResultMessage::validate_admission`，
  重复但投递为空时按错误处理。

### 7.3 边界检查（复用 TaskResult 语义）

发送前检查，任一不满足则返回 `Skipped`，不发送：

1. 会话存在、属于同一群，且 `status == Running`；
2. 群存在且 `Active`（未关闭）；
3. `target_bot_id` 仍是该会话的 Bot 参与者，Bot 存在且未隐藏。

受管路径中，这些检查与 TaskResult 的发送准备（`queued_group.rs:599-636`）一样在调度器 prepare
阶段再做一次；准备失败时投递行进入 `Failed`，不做第二次投递。RunResult 的失败
**不触发**公开的「消息投递失败」提示，因为它本来就不在人类时间线上。

### 7.4 受管路径与 legacy 路径

- **受管**（`DeliveryPolicy::manages_group(manager)` 为真）：与完成结果相同，通过
  `handle_web_send_with_result` 进入队列准入，按 `client_msg_id` 幂等。同一 (manager, session)
  车道 FIFO：manager 正在激活时 RunResult 排队等待，不会打断当前激活。
- **legacy**（singlebox 默认）：同步直投 `chat.send`，由检查点租约保证同一时刻只有一个发送者。
  队列正在排空同一车道时（`queue_draining`）返回 `Failed`，由下一轮扫描重试。

### 7.5 检查点状态机与「恰好一次」

```text
pending ──claim(租约)──▶ delivering ──Published──▶ delivered
   │                         │
   │                         ├──Skipped──▶ skipped（边界检查不通过）
   │                         └──Failed────▶ pending（可重试错误，未到 deadline）
   └── deadline 到期 ──▶ expired
delivering 且租约过期 ──▶ failed_uncertain（不重发）
```

复用 `publication.rs` 已有的租约语义：`claim`（令牌递增）→ `begin_send`（`pending → delivering`）
→ `finish`。

- **受管路径**：入队成功即 `delivered`。队列以消息身份幂等，重复准入不会产生第二条投递。
  网络发送由队列负责；队列对 `Unknown` 不重发，所以网络层是至多一次。
- **legacy 路径**：发送前持久化 `delivering`。如果在发送中崩溃，租约过期后视为结果不确定，
  标记 `failed_uncertain`，**不重发**。这与 `ensure_chat_result` 对超时发布的处理一致
  （`runtime_publication.rs` 中的说明）。

合在一起，同一 Run 的 RunResult **至多入队或直投一次**；在进程不崩溃的前提下恰好一次。
崩溃只可能落在 legacy 发送窗口内，这时选择「不重复」而不是「不丢失」，理由有二：

1. 2026-10-14 规范已经明确 legacy 不承诺网络幂等；
2. 主持人侧有「卡住了」兜底。

消费端（10 节）还会按 `run_id` 去重，以后如果要把 legacy 改成至少一次也是安全的。

### 7.6 提交后的首次投递与恢复

- `fail_run` / `cancel_state_machine_run` 提交事务后立即调用一次 `ensure_run_result(run_id)`。
  失败只记日志，不影响终态。
- `bootstrap/bcs/src/state_machine_progression_scanner.rs` 增加第四路扫描
  `recover_state_machine_run_results`，与 `recover_state_machine_terminal_im` 同样分页、带退避，
  用于领取 `pending` 检查点和租约过期的 `delivering` 检查点。
- `deadline_ms` 默认是终态时间加 10 分钟（可配置）。过期的唤醒已经没有意义，也可能与人类的
  「卡住了」重复。

## 8. 公开事件

与 `state_machine.run.completed` 同样由 `prepare_public_event` 生成，并在 `TerminateRun`
事务内提交。

| 事件 | 字段 |
| --- | --- |
| `state_machine.run.failed` | `failed_at`、`reason`、`failed_node`、`attempt`、`max_attempts`、`completed_nodes`、`duration_ms` |
| `state_machine.run.aborted` | `aborted_at`、`reason`（`user_aborted` / `session_closed` / `group_closed`）、`completed_nodes`、`duration_ms` |

- 与 RunResult 相同，不含 `output`、节点产物、错误原文或取消原因文本。
- 契约变更：`api-contracts/events/v1/catalog.yaml` 登记两个事件类型，补 `content-projection`
  与 `metadata_only` fixture，并加对应的契约测试（`AGENTS.md`：契约变更必须同步文档和一致性测试）。
- 副屏可以用这两个事件把「本阶段未正常完成」改成「主持人正在重开」（10.4）。

## 9. 与 TaskResult 对照

| 方面 | TaskResult | RunResult | 说明 |
| --- | --- | --- | --- |
| 触发 | Worker 投递终态 CAS（`queued_task_terminal::commit`） | Run 终态 CAS（`TerminateRun`） | 复用「首次进入终态才产生结果」的语义 |
| 原子性 | 结果作为 `reply` 与 Worker 投递迁移同事务 | 检查点与终态迁移同事务，提交后由 publisher 入队 | **StateMachine 特有**：终态在 collaboration store，不在消息队列里，所以先落检查点再入队 |
| 消息身份 | `task-result:{task_id}` | `run-result:{run_id}`（哈希为物理 ID） | 复用 |
| 内容 | `[任务失败…]` 固定头 + 短字段，无产物 | `[State Machine Run Result]` 固定头 + 短字段，无产物 | 复用形式，字段换成 Run 语义 |
| 只给 Bot | `run_reply`，从历史中排除 | `run_result`，从历史中排除 | 复用机制，新类型名避免混淆 |
| 边界检查 | 发送准备时检查会话、群、角色、参与者 | 同上，外加检查 `created_by` 仍是参与者 | 复用 |
| 投递失败 | 投递行 Failed，不重发，不发公开提示 | 同上，并记录检查点状态 | 复用 |
| 迟到产物 | 迟到的 final 不重新发布（`queued_task_review.rs`） | 节点完成以 Run 为 running 为前提（`CompleteNode` 事务内 `run_is_running`），迟到产物被丢弃；主持人还会按 `--round/--stage/--attempt` 判为 `STALE_ATTEMPT` | **StateMachine 特有**：失败后节点产物仍可能回调 |
| 会话类型 | 任务所在会话 | 第一版只覆盖 Chat 会话；服务会话保持现有收尾与 terminal IM | **StateMachine 特有** |
| 公开事件 | `task.*` | 新增 `state_machine.run.failed` / `.aborted` | 新增 |
| legacy | 直投给 driver，有台账去重 | 直投，检查点租约去重，崩溃窗口不重发 | 语义对齐，载体不同 |

## 10. 主持人技能接入

路径前缀：`singlebox/agents/6bots_undercover_game_profile/referee/`，下文简称 `referee/`。

### 10.1 识别与豁免

- 新增唤醒种类「运行结果」：消息以 `[State Machine Run Result]` 开头。登记在 `SKILL.md`
  的唤醒种类列表、`BOOTSTRAP.md` 和 `references/phase-machine.md` 的阶段表中。
- **必须豁免「运行进行中收到的玩家回复是迟到消息，不跑脚本」这条规则**
  （`phase-machine.md` 第 31 行、`SKILL.md` 第 77 行）。否则主持人会把 RunResult 当成迟到回复丢掉。

### 10.2 自动重开流程（改写 SX 卡住诊断）

`phase-machine.md` 的 `## SX 卡住诊断`（第 258–296 行）改为两个入口：RunResult 优先，
人类的「卡住了」作为兜底。

实现时把自动入口压成**一次工具调用**：`uc recover --session S --run-id R --failure-reason REASON`。
它在脚本里完成下面第 1–5 步（核对 `user_aborted`、过期 run、次数上限，按阶段重开或返回补念内容），
主持人收到 RunResult 后的第一个工具调用就是它。原因见第 13 节：模型服务限流时，每多一次模型调用
都可能多等几十秒。各步骤的语义不变：

1. `reason: user_aborted`：只查一次状态，向人类说一句平实的话，**不重试**，结束激活。
2. 运行 `uc status`。若 RunResult 的 `run_id` 不是当前阶段记录的 run，说明是过期消息，
   静默结束（消费端按 `run_id` 去重）。
3. 查重开次数：同一轮、同一阶段的 `renders[kind]` 已经到 3（即已重开两次）时停止，
   说兜底话术，请人类换一局或说「卡住了」。
4. 按阶段处理：

| `uc status` 阶段 | 典型失败点 | 动作 |
| --- | --- | --- |
| `SPEAK_RUNNING` / `PK_SPEAK_RUNNING` | 玩家发言节点 | `uc open-round --retry` |
| `VOTE_RUNNING` / `PK_VOTE_RUNNING` | 玩家投票节点 | `uc open-vote --retry` |
| `AWAIT_VOTE_START` / `AWAIT_PK_VOTE_START` | `collect` 已记下发言后自身失败，转述稿没有发出 | `uc recap` 复述本轮发言，念完再 `uc open-vote` |
| `AWAIT_NEXT_ROUND` / `AWAIT_PK_SPEAK_START` | `tally` 已记票后自身失败，开票稿没有发出 | `uc recap` 复述开票结果，念完按 S4 继续（派遗言或开下一轮） |
| `FINISHED` | 终局阶段失败 | 按现有终局步骤（`reveal`、`publish-result`、`finish`）继续，不重开 |

5. 自动入口遇到 `RUN_SLOT_BUSY`，说明已有新的 run 在跑（例如人类先说了「卡住了」），静默结束。

BCS 在终态事务提交**之后**才投递 RunResult，所以主持人处理时 Run 槽位已经释放。
`collaborate permission` 不会因为旧 Run 返回占用。

### 10.3 脚本改动（`undercover.py`）

- 在 `prepare_*_run` 的 `--retry` 分支统一执行次数上限，超限返回新错误码 `RETRY_LIMIT`，
  并补单元测试。上限仍复用 `renders[kind]`：它在锁内保存，按轮、阶段、类型分开计数，
  人工重开与自动重开共享。
- 修复 `status` 读不到 PK 阶段重开次数的问题：第 1419 行只读 `rounds[-1].renders`，
  没有读 `rounds[-1].pk.renders`。单行 `status` 也输出 `attempt`，避免主持人为看次数去跑
  `--full`，导致多行 JSON 进入群聊。
- 提交后按阶段保存 `run_id`，并修复解析：`bcs-cli` 输出的是 `run.run_id`，脚本读的是
  `result.run_id` 或 `nodes[0].run_id`，所以现在恒为空（见 `custom_collaboration_test.rs:154`）。
  `--retry` 增加可选的 `--run-id`，拒绝与当前阶段不符的过期 RunResult。
- 新增只读命令 `uc recap`：从状态文件复述当前阶段已经记下的公开内容（发言转述或开票结果），
  输出与 `render-*` 一样不含词和身份。现有命令都做不到这一点：`publish-result` 只在终局
  揭晓后补发结算文件。
- 节点 instruction 文本不增加内容，`tests/fixtures/flow_baseline.json` 的 `baseline_chars`
  预算保持不变。

### 10.4 主持人说什么

规则沿用 `boards.md`：中间不写字，不说流程术语。

- 发言重开：只说重开的开场白（「本轮之前的发言作废，请重新发言。」已存在）。
- 投票重开：静默，由副屏提示旧票作废。
- 达到上限或用户取消：一句平实的话。在 `boards.md` 增加一段话术，不出现
  run、节点、原因码、SX 等词。

副屏（`apps/bcs/assets/panel/src/undercover-game/UndercoverGamePanel.tsx`）：

- 收到 `state_machine.run.failed` 时显示「主持人正在重开本阶段」；`aborted` 不做承诺。
- 「告诉主持人卡住了」按钮保留，作为最后兜底。
- 组件测试只要求文案包含「本阶段未正常完成」，可同时满足。

### 10.5 文档同步

所有把「卡住了」写成唯一入口、或写「运行失败不会唤醒主持人」的位置都要改：

- profile README 第 102、114、260 行；
- `referee/` 下的 `KNOWLEDGE.md`、`HEARTBEAT.md`、`OKR.md`、`RULES.md`、`BOOTSTRAP.md`；
- `SKILL.md`，以及 `references/` 下的 `phase-machine.md`、`commands.md`、`runs.md`、`boards.md`；
- 副屏的 `UNDERCOVER_GAME.md`，以及 `docs/undercover-game-tutorial.zh-CN.md`。

## 11. 测试计划

逐条对应 Issue 的验收标准。

1. **Store**（Memory、SQLite；MySQL 走 SQL 生成测试）：
   - `TerminateRun` 原子提交状态、事件、检查点；
   - 事件追加失败时三者都不落；
   - CAS 失败方不写任何东西；
   - 同一 Run 不会出现第二个检查点。
2. **Runtime**（`runtime_progression`，`MemoryCollaborationStore` 加录制型 publisher）：
   - 节点失败、人类超时、启动失败、发布失败、用户取消各发布恰好一次，原因类别正确；
   - **回调与控制路径竞争**：`tokio::join!` 同时触发 Bot 失败回调与超时扫描、或取消与失败，
     只有一个检查点、一次发布；
   - **重放**：重复调用 `fail_run`，以及扫描器在已投递后再次扫描，都不会再发布；
   - **崩溃窗口**：仿照 `publication_sqlite.rs` 用子进程在提交后、发布前退出，重启后由扫描器
     投递恰好一次；
   - 非 Chat 会话、`session_closed`、`group_closed`：有事件，无检查点；
   - **迟到产物**：Run 失败后提交节点完成被拒绝，Run 与阶段不变。
3. **message-flow 一致性**（`FlowTestSupport`）：
   - 受管路径向 manager 产生一条 Send，`run_result` 不出现在历史查询中；
   - 重复发布不产生第二条投递；重复且投递为空时报错；
   - 会话已结束、群已关闭、manager 已移出时返回 `Skipped`，且无帧发出；
   - legacy 路径只发一次 `chat.send`。
4. **内容不泄露**：给节点产物和错误文本放入哨兵词（模拟玩家的词），断言 RunResult 文本与两个
   事件的 payload 都不含哨兵词。
5. **契约**：事件 catalog 与 fixture 校验；bootstrap 端到端测试仿照
   `state_machine_result_tests.rs`，用真实 publisher、`BcsMessageFlow` 和受管投递，
   队列开、关各跑一遍。
6. **回归**：
   - `cargo test -p bcs-collaboration-runtime -p bcs-collaboration-store -p bcs-message-flow -p bcs`
     全部通过；
   - `bcs-cli` 的 `custom_collaboration_test` e2e 不回归；
   - 无失败的对局行为不变。
7. **主持人技能**：`python3 -m unittest discover` 覆盖上限、`user_aborted`、过期 `run_id`、
   PK 次数显示；`flow_baseline.json` 不变。
8. **故障注入**：脚本放在 `singlebox/agents/6bots_undercover_game_profile/tools/fault_inject.py`。
   - 每轮做三件事：在发言或投票阶段随机杀掉一个存活玩家 Bot 的进程；模拟守护进程，
     Run 失败后立即把它拉起；自动代人类提交发言和投票。
   - 记录 Run 失败时间与新 Run 创建时间。差值不超过 60 秒算恢复成功；失败后 5 分钟内没有新 Run
     算卡死。
   - 连续 10 次，要求卡死 0 次。输出 CSV 与汇总，作为验收与演示视频素材。
   - 结果：10 次全部在 60 秒内恢复，卡死 0 次，见 [fault-injection.md](fault-injection.md)。
   - 打开群消息队列后复测 10 次：卡死 0 次，9 次在 60 秒内恢复，1 次因模型限流用了 71.6 秒。
     复测时发现并修复了队列准备阶段拒收 RunResult 的问题，见 fault-injection.md 第 4 节。

## 12. 兼容性与风险

- **受管路径以入队为交接点**：入队成功即记为 `Delivered`，之后由队列负责发送。
  队列如果把这条投递判为终态失败（例如准备失败或过期），检查点不会回退。
  复测时就这样丢过一次 RunResult（准备阶段拒收，已修复），所以 bootstrap 测试要求对入队的那一行真正准备并发送一次。
- **Schema**：不新增表与迁移，复用检查点表。新增 `message_type` 取值与两个事件类型，都是增量。
- **无失败时**：completed 路径不变。失败与取消原有的副作用都保留（状态、作废派发、
  服务会话收尾、terminal IM），只是多了事件和检查点。
- **双重重开**：RunResult 与人类的「卡住了」同时到达时，靠 `run_id` 去重、`RUN_SLOT_BUSY`
  和次数上限三重保护，最多多一次无害的状态查询。
- **负载**：每个失败 Run 一行检查点，扫描分页带退避，与 terminal IM 相同量级。
- **回滚**：回滚代码即可。旧版本不扫描 `run_result` 检查点，残留行无副作用。

## 13. 加分项：manager 处理 RunResult 时自身激活失败

复现中（repro.md 第 4 节）真实出现过三种情形：

- 模型请求网络中断；
- 模型服务返回 429，OpenClaw 的处理决策为 `surface_error`，不重试；
- 模型请求挂起，OpenClaw 判定会话停滞。

它们的共同点：BCS 已经把消息交给 Bot，投递在平台看来是成功的，但 manager 的这次激活
没有产生任何动作，此后也没有新的唤醒源。「可靠告知一次」只解决了投递，没有解决消费。

候选方案：

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| A：消费确认后补投一次 | 检查点记录投递对应的 Bot run。若该 run 以 error / aborted 结束，且此后会话中没有该 manager 新提交的 Run，就在退避（如 20 秒）后补投一次，物理 ID 为 `run-result:{run_id}#2`，上限 1 次 | 语义变为「一个结果、最多两次投递，第二次只在激活被证明失败后发生」；需要把 Bot 的终态事件关联回检查点 |
| B：环境加固 | 模型提供方层面对 429 和网络错误做有限重试。本次复现用的模型中转已验证：透明退避重试能消除整类 429 失败 | 不改 BCS；只降低概率，不能消除 |
| C：manager 自检 | 心跳时若阶段为 `*_RUNNING` 但 Run 已终态，就走 SX | 心跳 30 分钟一次，太慢，而且每次心跳都消耗 token |
| D：副屏兜底 | 收到 `state_machine.run.failed` 后 60 秒内没有新的 `run.created`，就高亮「卡住了」按钮 | 仍需要人；实现成本低 |

建议采用 A 加 D，并用 B 加固运行环境。按 Issue 要求，本节只给方案，不实现。

实现阶段又观察到一个直接相关的因素：本次环境使用的模型服务每分钟只能成功 5～6 个请求，其余返回 429。
主持人的一次激活需要好几次模型调用，被限流时单次调用就可能等待约 60 秒。所以实现里做了两件事：

- `uc recover` 把「查状态、选命令、重开」压成一次工具调用，新的 Run 在主持人第一次模型调用之后就会提交；
- 测试环境的模型中转对 429 做固定间隔的短重试，作为方案 B 的一个实例。

这两项都不改变平台语义，但直接决定了「60 秒内重开」能否稳定达成。

## 14. 待维护者确认

1. 新增 `message_type = "run_result"`，还是复用 `run_reply`。
2. legacy 崩溃窗口选择「不重发」（与 2026-10-14 一致），是否接受。
3. 检查点 deadline 默认 10 分钟是否合适。
4. `aborted` 事件是否需要区分 `session_closed` / `group_closed`，还是统一为一个原因。
5. 第二版是否把 RunResult 扩展到非 Chat 会话。
6. 受管路径入队即算交付。队列侧的终态失败是否需要回写检查点并重新投递。

## 15. 交付拆分与实现

- **PR 1（BCS）**：`TerminateRun` 与三个 store 实现、原因类别、检查点与扫描、publisher 端口与
  message-flow 投递、两个公开事件与契约、第 11 节第 1–6 项测试。
- **PR 2（主持人技能与副屏）**：第 10 节全部改动与第 11 节第 7 项测试。
- **PR 3（故障注入）**：故障注入脚本与第 11 节第 8 项的 10 次报告，以及不超过 2 分钟的演示视频。
- 文档：本设计、[repro.md](repro.md)、[fault-injection.md](fault-injection.md)。

实现分支（基于 `oss-camp2026`）：

- #3334（`feat/2610-run-result-bcs`）：第 5–9 节与第 11 节第 1–6 项。
- #3335（`feat/2610-undercover-auto-recover`）：第 10 节、`uc recover`、副屏文案与 `tools/fault_inject.py`。
