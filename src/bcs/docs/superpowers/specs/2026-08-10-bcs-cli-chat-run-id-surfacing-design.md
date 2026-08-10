# bcs-cli chat 透出 run_id / session_id 设计

- 日期：2026-08-10
- 状态：设计已确认，待实现（v3：据 Codex PR #927 第二轮 review 修订——detach 接受 `completed`）
- 范围：`crates/tools/bcs-cli`（`main.rs` + `client.rs`），仅 CLI 侧输出契约

## 背景

`bcs-cli chat` 走 `POST /bots/{id}/chat-async` 提交 + 长轮询 `GET /chat/runs/{run_id}` 的流程，分两种模式：

- `--detach`：run 进入 `running`（或 legacy 直接 `completed`，见 §6）即返回（`chat_polling_detach`）。
- 同步默认：阻塞到 run 终态（`chat_polling`）。

两类问题经核对属真实：

1. **同步等待模式下 agent 不知道 run_id**：`chat_polling` 成功返回的 JSON 内含 `run_id`/`session_id`，但 `main.rs` 的同步分支只打印 `Session:`，不打印 `Run:`；`--detach` 分支则有 `Run:`。整个阻塞等待期间 agent 也拿不到任何标识符。
2. **同步失败时响应里没有 run_id/session_id**：失败时 `chat_polling` 返回 `Err(anyhow!(...))`，经 `main.rs` 的 `?` 直接冒泡，结构化结果块（含 `--json` 分支）完全跳过，最终由 `fn main() -> Result<()>` 以纯文本打到 stderr。其中 `run_id` 只是混在错误串里（`run {}`），`session_id` 完全缺失——即便 `reported_session` 在提交后已经拿到（`client.rs:1350`）。`--detach` 失败路径同样丢失 `session_id`。

## 决策

### 1. 架构：submit → ack → poll → print，由 `main.rs` 驱动打印

当前 `chat_polling` / `chat_polling_detach` 把「提交 + 轮询 + 失败即 Err」揉在 `client.rs` 中，导致 `main.rs` 没有机会在提交后、轮询前介入打印。改为把流程切分，让 `main.rs`（交付层，按 CLAUDE.md 分层负责 CLI 打印）显式驱动：

1. `client.chat_async(...)` → `ChatRunSubmitResponse { run_id, session_id, bot_uuid, ... }`（已存在）。
2. `main.rs` 打印早 ack（见 §3）。
3. 新增轮询方法返回结构化结果 `ChatRunOutcome`（见 §5），不再返回裸 `Err`：
   - 同步：`chat_poll_run(run_id, poll_wait_ms, deadline) -> Result<ChatRunOutcome>`（轮询到终态）。
   - detach：`chat_poll_run_until_running(run_id, poll_wait_ms, deadline) -> Result<ChatRunOutcome>`（轮询到 `running`）。
4. `main.rs` 用 `ChatRunOutcome` 决定最终输出与退出码。

`client.rs` 继续保持纯传输层，不做任何 stdout/stderr 打印；所有打印仍在 `main.rs`。

### 1.5 默认输出模式（修复 Codex P1）

实现中 Chat 分支的输出决策**必须**使用 `is_structured_mode(cli)`（= `!cli.no_json`，见 `main.rs:107`），而**不是**当前的 `if cli.json`（`main.rs:3497`）。即：

- no-flag → **JSON**（匹配仓库文档 `main.rs:741`「default: enabled」与 `is_structured_mode` 语义）。
- `--json` 保留为向后兼容（仍出 JSON，因 `no_json` 保持 false；OpenClaw 显式传 `--json` 不受影响）。
- `--no-json` → 人类文本。

**行为变更**：no-flag `bcs-cli chat` 由人类文本改为 JSON。背景：当前 `main.rs:3497` 用 `if cli.json`（显式 flag，默认 false），导致 no-flag 走 non-json 分支，与仓库「JSON 默认开启」文档不一致；本修复纠正之。

> 注：`json` 字段（`main.rs:743`）保留但仅作向后兼容的接受旗标；输出判定一律走 `is_structured_mode`。

### 2. run_id / session_id 透出位置

run_id + session_id 必须在两处出现：早 ack（提交后立刻）与最终块（成功与失败都含），覆盖 sync/detach × json/non-json 全部分支。

### 3. 早 ack：带时间戳的纯文本日志行（非 JSON）

格式（时间戳为本地时区 RFC3339，含毫秒）：

```
2026-08-10T14:23:01.123+08:00 [chat] submitted run_id=run-9f3a session_id=bcs-cli:default:197262:a9c8432f
```

通道按模式分流（混合策略，对 json stdout 保持纯净）：

- **json 模式（默认，或显式 `--json`）**：ack 打到 **stderr**，stdout 保持单个 JSON 对象（`jq` 可直接解析）。
- **non-json 模式（`--no-json`）**：ack 打到 **stdout**，在 `Response from <bot>` 块之前。

> 备选：统一 stderr / 统一 stdout，实现时如需调整以该决策为准；当前选混合是为了同时满足「non-json 在 Response 块前可见」与「json stdout 纯净」。

**早送达范围（澄清）**：主流 agent（OpenClaw / Codex / Claude Code）执行工具时都会消费 stderr（OpenClaw 结果契约 `openclaw-channel-bcn/src/typings/openclaw.d.ts:87-88` 即含 `stderr: Buffer`）。但**前台阻塞调用只在命令退出后才返回输出**，且前台执行有 ~10min 上限 → 长 sync 跑不动前台，agent 须用 `--detach` 或后台流式。因此 stderr 早 ack 的实际受益方是：`--detach`（秒级 ID，比 detach 返回的 ~60s 更早）、流式/后台消费、人类直连。前台阻塞 agent 无论如何都在最终 stdout JSON 拿到 ID（成功+失败都含，见 §6），即完成/失败时拿到而非等待期间——此为已确认取舍以换取 stdout 保持单个 `jq` 可解析对象。

### 4. 输出契约

统一规则：早 ack → 见 §3；最终结果 → stdout 恰一个实体；退出码 → 由 `state` 推导成败。注：json 模式＝默认（或 `--json`），non-json 模式＝`--no-json`（见 §1.5）。

| 模式 | 早 ack（提交后） | 最终结果（stdout） | Exit |
|---|---|---|---|
| sync non-json | stdout，在 Response 块前：`<ts> [chat] submitted run_id=<rid> session_id=<sid>` | 成功：`Response from <bot>`+content+`Run:`+`Session:`+`State: completed`；失败：`Run:`+`Session:`+`State: failed`+`Error: <msg>` | 0/1 |
| sync json | stderr：`<ts> [chat] submitted run_id=<rid> session_id=<sid>` | 成功见下方 §6；失败见下方 §6 | 0/1 |
| detach non-json | stdout，在 Response 块前 | 成功：`Run:`+`Session:`+`State: running`（现有）；失败：`Run:`+`Session:`+`State: failed`+`Error: <msg>` | 0/1 |
| detach json | stderr 同上 | 成功/失败见 §6 | 0/1 |

### 5. `ChatRunOutcome`（替代裸 `Result<Value>` / Err）

```rust
struct ChatRunOutcome {
    delivered: bool,          // 成败：sync=Completed；detach=Running 或 Completed（含 legacy 直接 completed）
    submitted: bool,          // run 是否被创建（chat_async 返回了 run_id）；仅提交级失败为 false
    run_id: Option<String>,   // 仅提交失败时为 None
    session_id: Option<String>,
    state: String,            // running|completed|failed|cancelled|timeout|submitted|submit_failed|poll_error
    bot_uuid: Option<String>,
    response_content: Option<String>,
    error_message: Option<String>,
    content_truncated: bool,
}
```

- 提交成功后轮询方法返回 `Ok(ChatRunOutcome)`；`delivered=false` 表示未交付。
- `submitted` 仅表示「run 是否被创建」，**不**用于表达成败；退出码由 `state` 推导（成功 = sync 下 `completed`，或 detach 下 `running`/`completed`——含 run 在首次状态响应前已完成，对齐现有 `chat_polling_detach` 接受 `Completed|Running` `client.rs:1469`；其余 = 失败）。
- 错误映射（`main.rs` 统一做，确保 stdout 在「run 已创建」时绝不空）：
  - 提交级 HTTP 错误（非 2xx / 反序列化失败） → `submit_failed`（`submitted=false`、`run_id/session_id=null`）。
  - 轮询期 `chat_run_status` 错误（传输 / 非 2xx / 反序列化，发生在 run 已创建后） → `poll_error`（`submitted=true`、IDs 取自提交响应，见 §7）。
- `ChatRunOutcome` 是 `bcs-cli` crate 内部结构（位于 `client.rs`），不是 bcs-protocol 线上 DTO；由现有 `ChatRunSubmitResponse` / `ChatRunStatusResponse` 的既有字段组装，不引入任何新的线上字段。

### 6. 最终 stdout JSON 显式字段

run_id 与 session_id 始终为顶层字段；仅提交失败（服务器未创建 run）时为 `null`。sync 与 detach **均以 `delivered` 表成败**（sync：`delivered`↔`state=completed`；detach：`delivered`↔`state∈{running, completed}`，含 run 在首次状态响应前已完成的快/legacy 情况）。`submitted` 仅表示 run 是否被创建（`chat_async` 返回了 `run_id`），仅在提交级失败时为 false——**不**用于表达成败，退出码由 `state` 推导。sync 因等待到完成携带 `response` / `content_truncated`；detach 因不等到完成故省略 `response` / `content_truncated`。两者均始终包含 `run_id` 与 `session_id`。

sync json 成功（exit 0）：
```json
{
  "delivered": true,
  "submitted": true,
  "bot_uuid": "default:197262",
  "run_id": "run-9f3a",
  "session_id": "bcs-cli:default:197262:a9c8432f",
  "state": "completed",
  "response": { "content": "嘿，我是元歌的分身 …" },
  "error_message": null,
  "content_truncated": false
}
```

sync json 失败（exit 1）：
```json
{
  "delivered": false,
  "submitted": true,
  "bot_uuid": "default:197262",
  "run_id": "run-9f3a",
  "session_id": "bcs-cli:default:197262:a9c8432f",
  "state": "failed",
  "response": { "content": "" },
  "error_message": "provider returned error: …",
  "content_truncated": false
}
```
超时变体：`"state": "timeout"`、`"error_message": "local polling timeout after 1800000 ms; run run-9f3a still active"`。
轮询请求错误变体：`"state": "poll_error"`、`"error_message": "chat_run_status failed: <err>"`（`run_id`/`session_id` 来自提交响应）。

detach json 成功（exit 0）/ 失败（exit 1）：
```json
{ "delivered": true, "submitted": true, "bot_uuid": "…", "run_id": "…", "session_id": "…", "state": "running", "error_message": null }
{ "delivered": true, "submitted": true, "bot_uuid": "…", "run_id": "…", "session_id": "…", "state": "completed", "error_message": null }
{ "delivered": false, "submitted": true, "bot_uuid": "…", "run_id": "…", "session_id": "…", "state": "failed", "error_message": "…" }
```
注意：detach 下 `state=completed` 同样算成功（`delivered:true`、exit 0）—— run 可能在首次状态响应前已完成（快 run 或 legacy server 跳过 `running`），对齐现有 `chat_polling_detach` 接受 `Completed|Running`（`client.rs:1469`）。detach 失败时 `submitted=true`（run 已被创建，只是后来 failed/cancelled），区别于提交级失败的 `submitted:false`（见下方）。

提交本身失败（如 404 bot 不存在，未创建 run）（exit 1）：
```json
{
  "delivered": false,
  "submitted": false,
  "bot_uuid": "default:197262",
  "run_id": null,
  "session_id": null,
  "state": "submit_failed",
  "response": { "content": "" },
  "error_message": "chat_async failed (404): Bot not found"
}
```

### 7. 失败语义

- `Failed` / `Cancelled`：`delivered=false`、`submitted=true`、`state=failed/cancelled`、`error_message` 取自 status。结构化 stdout 输出，exit 1。
- 本地轮询超时（deadline 到，run 在服务端仍活动）：`delivered=false`、`submitted=true`、`state="timeout"`、`error_message="local polling timeout after N ms; run <rid> still active"`。结构化输出，exit 1。透出的 run_id 允许 agent 自行后续查询/取消。
- **轮询请求错误**（修复 Codex P2）：`chat_run_status` 在 run 已创建后的传输错误 / 非 2xx / 反序列化失败：`delivered=false`、`submitted=true`、`state="poll_error"`、`run_id`+`session_id` 取自提交响应（已知）、`error_message="<err>"`。结构化输出，exit 1。`main.rs` 将该 Err 映射为 `poll_error` 的 `ChatRunOutcome`，**不**作为裸 Err 冒泡——避免「stdout 空 + 已知 ID 丢失」的原缺陷。
- 提交本身失败（非 2xx 或反序列化失败）：`submitted=false`、`run_id/session_id=null`、`state="submit_failed"`、`error_message=body`，结构化输出，exit 1。

### 8. 兼容性与退出码

- 退出码：成功 0，任何失败 1（与现状一致，仅输出内容改善）；退出码由 `state` 推导，非布尔字段。
- `--json` stdout 在所有情况下都恰为一个 JSON 对象（成功或失败）；失败路径为严格增量修复（现状打印空）。
- 默认行为变更（见 §1.5）：no-flag 输出由人类文本改为 JSON；人类交互使用 `--no-json`。
- `chat_polling` / `chat_polling_detach` 被 `chat_async` + `chat_poll_run(_until_running)` 取代；`client.rs` 内现有 `test_chat_polling_*` 测试需更新为断言新的结构化结果，而非自由文本 `Err`。
- 该 `client.rs` 为 `bcs-cli` crate 内部，无外部 crate 依赖其 `chat_polling`，API 变更影响面仅在 crate 内及 crate 内测试。

## 跨版本兼容（无服务端变更）

本设计仅改动 `bcs-cli` crate，不触达服务端、端点、或线上 DTO：

- 不改 `POST /bots/{id}/chat-async`、`GET /chat/runs/{run_id}`，不改 `ChatRunSubmitResponse` / `ChatRunStatusResponse`；这些 DTO 已含 `run_id`、`session_id`、`state`、`response`、`error_message` 字段，CLI 今日已解析，只是未打印 / 在失败时丢弃。本设计只改打印与失败结构化。
- 因此本功能**不需要服务端升级**，也不引入 CLI↔Server 新版本耦合。
- 双向兼容：
  - 老存量 CLI ↔ 当前/未来 Server：不受本改动影响（服务端无任何变化），行为与今天一致。
  - 新 CLI ↔ 老 Server：可用。新 CLI 只读老 Server 已发送的既有字段；不新增请求字段、不新增/不提升 `X-BCS-CHAT-VERSION`（仍为 `2`）。
- 失败时 run_id 取自提交响应、session_id 取自提交时的 `reported_session`，因此即便某 Server 的失败状态体遗漏这些字段，CLI 仍可透出，不依赖 Server 状态体形状。
- 仓库已有 `BCS_CHAT_VERSION` / `X-BCS-CHAT-VERSION` 版本协商用于任何真实的线上协议变更；本设计不触碰它。

## 验收标准

- 裸调用 `bcs-cli chat`（无 flag）默认输出 JSON：stdout 为单个 JSON 对象，stderr 为带时间戳的 ack（覆盖 §1.5 默认切换）。
- 同步 non-json 成功时，stdout 出现 `Run:`（修复问题 1 的 run_id）。
- 同步与 detach 在提交后立刻输出带时间戳的纯文本 ack，行内含 `run_id` 与 `session_id`；non-json 走 stdout 且在 `Response from` 块之前，json 走 stderr。
- 同步失败（run failed / local timeout / 轮询请求错误）时，stdout（json：单 JSON 对象；non-json：`Run/Session/State/Error` 行）包含 run_id 与 session_id，且 exit 1。
- detach 成功与失败均输出 run_id 与 session_id；成功接受 `state=running` **或** `state=completed`（run 在首次状态前已完成的快/legacy 情况），均 `delivered:true`、exit 0；失败时 `submitted=true`、`state=failed/cancelled`、exit 1。
- 提交级失败（如 404）输出结构化失败（json：单对象，`run_id/session_id=null`、`submitted=false`；non-json：`Error:` 行），exit 1，无 panic。
- 轮询期 `chat_run_status` 错误映射为 `state="poll_error"` 的结构化结果（含来自提交的 run_id+session_id），exit 1，stdout 非空。
- `--json` 模式下，stdout 成功与失败均为且仅为一个 JSON 对象，`jq '.'` 可解析。
- run_id 与 session_id 同时出现在早 ack 与最终 stdout 块（除提交级失败外，最终块均为非 null 顶层字段）。

## 测试

- 更新 `test_chat_polling_timeout_leaves_server_run_active`：断言 `outcome.delivered==false`、`state=="timeout"`、含 `run_id` 与 `session_id`，而非断言自由文本 `Err` 含 `run-local-timeout is still active`。
- 新增：裸 `bcs-cli chat`（无 flag）默认 JSON——stdout 单 JSON 对象、stderr 含 ack（覆盖 §1.5）。
- 新增：同步 non-json 成功打印 `Run:`（问题 1 回归）。
- 新增：同步 json 失败在 stdout 打印单个失败 JSON 对象且 exit 1（问题 2）。
- 新增：提交后立刻在正确通道（non-json→stdout，json→stderr）输出带时间戳的纯文本 ack（含 run_id+session_id），覆盖 sync 与 detach。
- 新增：detach 失败在 stdout 输出 `submitted=true`、`state=failed`、run_id 与 session_id 且 exit 1。
- 新增：detach 首个状态为 `completed`（快/legacy run）→ `delivered=true`、`state=completed`、exit 0（兼容回归，对齐 `chat_polling_detach` 的 `Completed` 接受）。
- 新增：提交级失败（404）输出结构化失败、`submitted=false`、ID=null、exit 1、无 panic。
- 新增：轮询期 `chat_run_status` 返回 500 → `state="poll_error"` 结构化结果，含 run_id+session_id（取自提交响应），exit 1，stdout 非空。

## 非目标

- 不改服务端 run 生命周期、端点、或 `chat_run_status` 长轮询协议。
- 不新增 CLI 子命令用于复轮询（透出的 run_id 允许 agent 直接打 `GET /chat/runs/{run_id}`，另行跟进）。
- 不动 `wait_for_service_completion`（群组 / state-machine 流程，已打印 `StateRun:`）。
- 不改 `client.rs` 之外 crate 的对外 API。
