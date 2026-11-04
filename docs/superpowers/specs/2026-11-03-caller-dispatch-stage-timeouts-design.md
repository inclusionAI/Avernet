# Caller 派发分段超时与分段证据设计 Spec（对齐 non-caller 模式）

- **日期**：2026-11-03
- **状态**：user-approved draft
- **范围**：BCS caller 保护派发（root 分支 `dispatch_once` 与 delegate 分支 `dispatch_delegate_once`）的超时结构；不涉及 ordinary（非 caller）投递路径行为变化
- **决策前提**（用户已拍板）：
  1. 采用方案 A：拆掉外层整窗 timeout，各 stage 沿天然上界各自限时；
  2. **外层不留任何兜底 timeout**（与 non-caller 一致，活性由 run_deadline → cancel 仲裁收尾）；
  3. **pre-send 阶段超时不重试**（保持 handoff 身份一次性消费语义，不对齐 non-caller 的自动重试）。
- **涉及模块**：`crates/services/bcs-message-flow`（delivery_runtime、caller_dispatch_pipeline）、`crates/bootstrap/bcs`（测试装配）

---

## 1. 背景与问题

线上事故（2026-11-03 20:33，delivery `9491aa7c-c544-4878-b37e-e8dd254f3bb8`）：caller 保护派发在外层
`tokio::time::timeout(io_timeout=30s)`（`delivery_runtime.rs:506`）内整体超时，被 blanket 转成
`TransportUnknown` → 行进入 `Unknown`、bot 投递 lane 被 `lane_blocked` 挂起、无自动重发。

该外窗把全部 8 个 stage 塞进一个 30 秒预算：

```text
resolve → handoff claim → ensure 容器(重网络) → establish subject → issue proof
        → digest commit → send chat.send(重网络) → 终态回执等待
```

而 ordinary（non-caller）路径是"每个 I/O 微窗口独立限时、各带收敛证据"：

| non-caller 阶段 | 位置 | 超时收敛 |
|---|---|---|
| prepare | `delivery_runtime.rs:516` | 行未提交 StartSend → `PreparationFailed`（Queued 语义） |
| before_send | `:647` | 可证明未入网 → `DefinitelyNotSent{retry}`（自动重试） |
| deliver | `:660` | `TransportUnknown` → Unknown + lane 暂停 |

两个 caller 重网络 stage 底层 adapter **已各自自限**：

- ensure 容器：`bcs-provider-http/src/caller_container.rs:25` `CALLER_ENSURE_TOTAL_TIMEOUT_MS = 10s`（组帧:103 / 响应:142 / body:181 三处包裹）；
- send：`caller_send.rs:209-214` 用 `command.callback_timeout_ms`(30s) 包住 `.send()` 全程（含连接建立、响应 body），自超时返回 `Ack::Unknown` → `OutcomeUnknown`。

事故根因即两层 30s 竞态：前置阶段吃掉外窗预算后，外窗先于 send 自身超时到点，把
"send 出栈后不确定"才应使用的 Unknown 证据错误施加给整链。

## 2. 目标与不变量

1. 每个 stage 沿自己的天然上界限时，移除外层整窗 timeout；
2. 证据分类精确化：**可证明未出栈 → 不占道（NotSent / Failed 释放 lane）；出栈后不确定 → 才 Unknown 占道**；
3. 不重试、不动 admission 一次性身份语义、不动状态机事件表、不新增配置（进程内活性兜底复用现有 `io_timeout`）。

## 3. 设计

### 3.1 改动一：`delivery_runtime.rs` — 删外窗

- `:504-511` spawn 去掉 `tokio::time::timeout(io_timeout, ...)`，只保留 `Abortable::new(...)`
  （cancel 意图中断把手、run_deadline → cancel 仲裁、AbortUnconfirmed 路径**原样保留**，构成无外窗后的活性收尾，与 non-caller 活性模型一致）；
- `WorkResult::CallerFinished(delivery_id, timed_out)` 去掉 `timed_out` 字段；
- `:531-548` 整窗 blanket 转换分支（`Queued→PreparationFailed / Dispatching|Running→TransportUnknown`）整体删除；
- 旧日志文案 `"caller protected dispatch timed out; no automatic resend"` 随之消失，由 3.3 的各 stage WARN 取代。

### 3.2 改动二：`caller_dispatch.rs`（runtime 侧）— 无自限的进程内 stage 包 `io_timeout`

| Stage | 位置 | 超时收敛 | 依据 |
|---|---|---|---|
| `resolve_caller_dispatch` | `:21`（StartSend 前） | 现有 resolve 失败分支 → `PreparationFailed` | 行还在 Queued、未提交 StartSend，等同 non-caller prepare 超时语义 |
| `policies` 查询 | `:111-112` | 包 `io_timeout`；超时按该调用点的既有错误处理路径收敛（实现计划中核对 `policies` 签名的可失败性，若不可失败则不加包） | 只加活性兜底，不改语义 |
| `bounded_contexts`（history header） | `:194` | 现有 `PreparationFailed` 分支 | Post-StartSend，行为沿用现状定义 |

pipeline 侧的 stage 活性上界（`io_timeout` 值）在 bootstrap 组装时注入
`CallerDispatchPipeline`（与 `DeliveryRuntimeConfig.io_timeout` 同源，默认 30s），
pipeline 自身不读全局配置。

### 3.3 改动三：`caller_dispatch_pipeline.rs` — pipeline 内给无自限 stage 包 `io_timeout`

`dispatch_once`（root）与 `dispatch_delegate_once`（worker/delegate）内以下 stage 各包
`tokio::time::timeout(io_timeout, ...)`：

- root：`handoff.claim`、`identity.establish_subject`、`identity.issue_execution`（含
  `target_agent_code` 查找）、`managed.commit_caller_dispatch`；
- delegate：对应物（lineage 查找、issue、commit）。

**同样加包的 stage（计划期修订 2026-11-03）**：ensure 容器与 send——普通路径自身就在
`delivery_runtime.rs:660` 给 `io.deliver` 包 `io_timeout`（超时 → TransportUnknown），watchdog
契约测试 `protected_send_timeout_converges_unknown_without_retry` 也正是以"挂起的 send 端口
在有界窗口内收敛 Unknown"为契约的。故二者在 pipeline 内同样适用 stage_timeout：
ensure 超时 → `StageTimeout{stage:"container_ensure"}`（保守 Failed）；send 超时 →
`OutcomeUnknown`（走 send adapter 自超时完全相同的证据路径）。adapter 自限保留不变。

超时返回新错误变体：

```rust
// root
CallerDispatchError::StageTimeout { stage: &'static str }
// delegate 枚举增加同义变体
```

收敛分类（关键证据规则）：

| 超时 stage | 归入 | 收敛 | lane |
|---|---|---|---|
| handoff claim 超时 | `StageTimeout` → 现有 `other` 保守分支 | `Event::Failed` | **释放**（`failed` 是 terminal，`lane_blocked` SQL 不含 `failed`：`bcs-message-store/src/delivery.rs:626`） |
| establish / issue / commit 超时 | `StageTimeout` → `other` / `CommitFailed` 分支 | `Event::Failed` | 释放 |
| send 自身 30s 到点（**不变**） | adapter `OutcomeUnknown` | `Event::TransportUnknown` → Unknown | 占道（等待 late-callback 或 cancel 仲裁） |

**claim 超时不得复用 `IdentityUnavailable`**：后者表示"可证明 claim 被拒、nothing sent"
→ `DefinitelyNotSent`；超时对 claim 是否已消费不可证明，必须走保守分支。
每个 stage 超时打带 `stage` 字段的 WARN，替代原整窗日志。

### 3.4 明确不改

状态机事件表与 all `transition` 收敛规则、admission 一次性身份、HISTORY_NOT_READY 停靠
（park + redrive）、late-callback 终态仲裁、`may_have_been_sent` 证据规则、`WorkResult`
其余分支、ensure/send 的 adapter 自限值（若生产容器冷启动需放宽，另行调整
`CALLER_ENSURE_TOTAL_TIMEOUT_MS`，不在本 spec 范围）。

## 4. 测试与验证

- **stage 超时注入**：以挂起端口实现（>io_timeout）逐 stage 注入，断言：
  - claim / establish / issue / commit 超时 → `StageTimeout` → `Event::Failed` 且 lane 释放；
  - resolve 超时 → `PreparationFailed`（行回 Queued 语义）；
  - send 依旧走 adapter 自限 → `Unknown`（回归保护，不因本改动退化）；
- **既有测试更新**：搜索并更新依赖旧行为的用例（`timed_out` 字段、旧日志文案、
  `bootstrap/bcs/tests/delivery_worker_load.rs` 的 CallerFinished 断言）；
- **运行**：`cargo nextest run -p bcs-message-flow`（必要时 `-j 2`）+ 受影响的 bootstrap 集成测试。

## 5. 兼容与运维提醒

- 告警/看板若 grep 旧文案 `"caller protected dispatch timed out"`，需同步改为按
  `stage` 字段的 `StageTimeout` WARN 新文案；
- 行为变化：慢 ensure/claim 等前置阶段从"Unknown + lane 长期挂起"变为
  "Failed + lane 立即释放"（identity 已消费、不重发，与现有 stage 失败路径一致）；
  send 不确定性仍保守 Unknown，不产生新投递风险。

## 6. 分支说明

本 spec 落在独立分支 `feat/caller-dispatch-stage-timeouts`（从 `dev` 拉出），不与
`feat/caller-sse-alignment` 的未提交工作混合。
