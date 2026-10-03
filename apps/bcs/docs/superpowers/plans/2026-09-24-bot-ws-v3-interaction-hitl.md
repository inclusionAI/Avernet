# Bot WebSocket V3 Interaction(HITL) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让通过 Bot WebSocket V3 接入(plugin 模式)的 bot 支持 interaction(HITL)事件的上行请求与下行确认，功能对齐现有 HTTP Provider 链路。

**Architecture:** 上行在 `run_event_v3.rs` 的 `normalize_v3_event` 内部对 `StreamEvent::Interaction` 分流到新函数，不进入 agent/chat 共用的 `NormalizedBotEvent` 管道；下行在 `BotConnectionRegistry` 新增 `InteractionProviderPort` 实现(复刻现有 `abort_inner` 的 RequestFrame/oneshot 模式)，通过新的 `InteractionProviderMux` 按 `BotDeliveryTarget` 路由到 WS 或既有 HTTP 实现。

**Tech Stack:** Rust, tokio, async-trait, serde_json。

**Spec:** `docs/superpowers/specs/2026-09-24-bot-ws-v3-interaction-hitl-design.md`

## Global Constraints

- 不运行 `cargo fmt`；改动限定在需要修改的行，不重排无关代码。
- 字符串截断必须用 `char_indices()`，禁止按字节索引切片(本计划不涉及字符串截断，记录以防后续任务踩到)。
- WS 侧下行传输超时固定为 **15,000ms**(对齐 `chat.abort` 量级，非 HTTP Provider 的 65,000ms)。
- run 生命周期截止(`run_deadline_ms`)到期后，BCS **不**向下游发送任何失效通知——两条链路行为一致，任何任务都不得新增这类通知逻辑。
- Interaction 的 seq **不**进入 `reserve_run_event_seq` 计数器，也不计算 `terminal_fingerprint`；幂等完全交给 `InteractionService`/`MemoryInteractionStore` 的 `idempotency_key` 机制。
- wire 字段命名统一用 camelCase：`bcsRunId`/`runId`/`interactionId`/`kind`/`idempotencyKey`，与 web dispatcher 的 `InteractionResolveParams` 和 `HttpProviderTransport::resolve_interaction` 已有命名一致。

## Review Focus

- **未知 bot_id 的 Interaction 事件**：连接尚未 `bot.connect` 或 `registered_bot_id` 为 `None` 时收到 interaction 帧，应被安全丢弃并记录警告，而不是 panic 或返回未处理的错误类型（对照 agent/chat 现有的"unregistered bot"处理路径）。
- **run 已终态/已过期后收到 Interaction 请求**：`validate_v3_run_scope` 必须拒绝，不能让一个已经结束的 run 产生新的待审批 interaction 记录。
- **WS 连接在 resolve 请求发出后断开**：`send_frame_json` 失败或连接消失，必须清理 `pending_interaction_requests` 挂起项，不能造成 oneshot sender 泄漏或 map 无限增长。
- **bot 返回 unknown-method 错误**：必须映射为 `retryable:false`，防止旧版本/不支持 HITL 的 bot 被反复判定为可重试而无限重试。
- **`InteractionProviderMux` 收到既非 WebSocket 也非 HttpProvider 的 target**：`BotDeliveryTarget` 目前只有这两个变体（已确认，`bcs-domain/src/provider.rs:260`），match 应为穷尽的，不需要额外 `_` 分支兜底；但如枚举未来扩展，编译器会强制处理，此处不做防御性运行时检查。

---

## Task 1: 提炼 `validate_v3_run_scope` 共享校验 helper

**Files:**
- Modify: `crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs`(提炼 helper，改造现有 agent/chat 分支调用它)
- Test: `crates/adapters/ws/bcs-ws/tests/frame_compat.rs`(验证提炼后 agent/chat 现有行为不变)

**Interfaces:**
- Produces: `pub(super) async fn validate_v3_run_scope(state: &BotDispatchState, bot_id: &str, run_id: &str, session_id: &str, seq: u64, outer_seq: Option<u64>) -> Result<BotRunContext>`(`dispatcher.rs` 内的模块级函数，`pub(super)` 可见性使 `bot` 模块下的同级子模块 `run_event_v3` 可以调用它；供 Task 2 的 Interaction 分支和现有 agent/chat 分支共用)

这是一个纯重构任务：把 `handle_event_frame` 里已经存在的 4 步校验(outer_seq==seq 一致性、run context 存在性、身份匹配、terminal/deadline 检查)原样搬进一个独立函数，不改变任何行为。现有测试(`bot_v3_connect_negotiates_canonical_events_and_validates_run_sequence` 等)必须继续通过，作为"提炼未改变行为"的验证。

- [ ] **Step 1: 运行现有 V3 测试作为重构前基线**

Run: `cargo test --package bcs-ws --test frame_compat bot_v3 -- --nocapture`
Expected: 所有 `bot_v3_*` 测试 PASS（这是重构前的基线，记录下当前全部通过，后续 step 完成后必须仍然全部通过）

- [ ] **Step 2: 提炼 `validate_v3_run_scope` 函数**

在 `crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs` 中，找到 `handle_event_frame` 内 `is_v3` 分支下这一段（当前大约在 `let (real_group_id, mut bcs_session_id) = if is_v3 { ... }` 块的开头到 `reserve_run_event_seq` 调用之前）：

```rust
let session_id = v3_session_id
    .as_deref()
    .ok_or_else(|| BotWsDispatchError::InvalidFrameFormat("V3 event missing sessionId".into()))?;
let seq = v3_seq
    .ok_or_else(|| BotWsDispatchError::InvalidFrameFormat("V3 event missing seq".into()))?;
if event.seq.is_some_and(|outer| outer != seq) {
    return Err(BotWsDispatchError::InvalidFrameFormat(
        "V3 frame seq does not match payload seq".into(),
    ));
}
let context = state
    .bot_run_context
    .get_context(&run_id)
    .await
    .ok_or_else(|| BotWsDispatchError::InvalidFrameFormat("V3 event runId is unknown".into()))?;
if context.bot_id != bot_id || context.bcs_session_id.as_deref() != Some(session_id) {
    return Err(BotWsDispatchError::InvalidFrameFormat(
        "V3 event run/session identity mismatch".into(),
    ));
}
```

和稍后的：

```rust
if context.terminal || context.deadline_ms <= bcs_protocol::now_ms() {
    return Err(BotWsDispatchError::InvalidFrameFormat(
        "V3 event run is terminal or expired".into(),
    ));
}
```

把这 4 步校验提炼成一个新的模块级函数，放在 `handle_event_frame` 函数定义之前：

```rust
/// V3 帳本校验：帳外层 seq 与 payload seq 一致、run 存在、身份匹配、未终态未过期。
/// 不含 agent/chat 专属的 terminal_fingerprint 幂等去重和 reserve_run_event_seq
/// 占用——那两步只服务于流式消息事件的重放语义，Interaction 事件不应共用。
/// pub(super) 可见性:bot 模块下的同级子模块 run_event_v3 需要调用它
/// (对照 run_event_v3.rs 已有的 pub(super) fn normalize_v3_event 先例)。
pub(super) async fn validate_v3_run_scope(
    state: &BotDispatchState,
    bot_id: &str,
    run_id: &str,
    session_id: &str,
    seq: u64,
    outer_seq: Option<u64>,
) -> Result<BotRunContext> {
    if outer_seq.is_some_and(|outer| outer != seq) {
        return Err(BotWsDispatchError::InvalidFrameFormat(
            "V3 frame seq does not match payload seq".into(),
        ));
    }
    let context = state
        .bot_run_context
        .get_context(run_id)
        .await
        .ok_or_else(|| BotWsDispatchError::InvalidFrameFormat("V3 event runId is unknown".into()))?;
    if context.bot_id != bot_id || context.bcs_session_id.as_deref() != Some(session_id) {
        return Err(BotWsDispatchError::InvalidFrameFormat(
            "V3 event run/session identity mismatch".into(),
        ));
    }
    if context.terminal || context.deadline_ms <= bcs_protocol::now_ms() {
        return Err(BotWsDispatchError::InvalidFrameFormat(
            "V3 event run is terminal or expired".into(),
        ));
    }
    Ok(context)
}
```

然后把 `handle_event_frame` 里原来那两段校验代码替换为对这个函数的调用：

```rust
let context = validate_v3_run_scope(state, &bot_id, &run_id, session_id, seq, event.seq).await?;
```

注意：原代码在两段校验之间还夹着 `terminal_fingerprint` 的 `is_terminal_replay` 检查（这一步不属于提炼范围，必须保留在 `handle_event_frame` 里，在调用 `validate_v3_run_scope` 之后、`reserve_run_event_seq` 之前的原位置不变）。提炼后 `handle_event_frame` 内的执行顺序应为：`validate_v3_run_scope` → `is_terminal_replay` 检查 → `reserve_run_event_seq`。

`context` 返回后，原来 `(context.group_id, Some(session_id.to_string()))` 这个元组构造保持不变，只是 `context` 现在来自函数返回值而非内联代码。

- [ ] **Step 3: 运行测试验证重构未改变行为**

Run: `cargo test --package bcs-ws --test frame_compat bot_v3 -- --nocapture`
Expected: 与 Step 1 记录的基线完全一致，全部 PASS。若有测试失败，说明提炼过程改变了校验顺序或逻辑，需回查 diff 修正。

- [ ] **Step 4: Commit**

```bash
git add crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs
git commit -m "refactor(bcs-ws): extract validate_v3_run_scope helper

从 handle_event_frame 提炼出 4 步通用 V3 帳本校验(seq一致性、
run存在性、身份匹配、终态/过期检查)，为后续 Interaction 事件
分支复用做准备。不改变 agent/chat 现有行为。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: 上行 — `BotDispatchState.interactions` 字段 + `handle_interaction_event_v3` 分流

**Files:**
- Modify: `crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs`(新增 `interactions` 字段到 `BotDispatchState`)
- Modify: `crates/adapters/ws/bcs-ws/src/bot/run_event_v3.rs`(新增 `handle_interaction_event_v3` + kind 转换 + 分流)
- Modify: `crates/adapters/ws/bcs-ws/tests/frame_compat.rs`(补 `new_state()` 新字段；新增 interaction 上行测试)
- Modify: `crates/adapters/ws/bcs-ws/tests/token_expiry_e2e.rs`(补 `BotDispatchState` 新字段，仅需编译通过，不新增断言)

**Interfaces:**
- Consumes: `validate_v3_run_scope`(Task 1 产出)；`bcs_service_api::{InteractionService, ProviderInteractionRequestedCommand, ProviderInteractionResolvedCommand, InteractionKind as AppInteractionKind}`；`bcs_protocol::stream::{InteractionEvent as WireInteractionEvent, InteractionPhase, InteractionKind as WireInteractionKind, parse_run_event_v3, StreamEvent}`
- Produces: `pub(super) async fn handle_interaction_event_v3(state: &BotDispatchState, bot_id: &str, event: bcs_protocol::stream::InteractionEvent, outer_seq: Option<u64>) -> Result<()>`（`run_event_v3.rs` 内，供 `dispatcher.rs::handle_event_frame` 调用）；`BotDispatchState.interactions: Arc<dyn InteractionService>` 字段

关键背景（已读码确认，写死在此避免实现时重新猜测）：`bcs_protocol::stream::parse_run_event_v3` 本身已经支持解析 `"interaction"` 顶层事件（`parse.rs:44` 的 `matches!(event, "agent" | "chat" | "interaction")`），并已做完 `runId`/`sessionId` 非空校验。**当前的拒绝逻辑只发生在 `run_event_v3.rs::normalize_v3_event` 里**，是拿到 `StreamEvent::Interaction(_)` 之后手动返回错误——不是解析器本身不支持。因此本任务的分流点就是 `normalize_v3_event` 现有的 `match canonical { ... }` 语句，不需要在 `handle_event_frame` 顶层新增判别逻辑。

`bcs_service_api::InteractionKind`(应用层，`core::interaction` 模块，从顶层重导出)与 `bcs_protocol::stream::InteractionKind`(wire 层)是两个独立但字段等价的枚举（都是 `Exec`/`AskUser`/`ModeSwitch`，`serde(rename_all = "snake_case")`），需要显式转换函数。

- [ ] **Step 1: 写失败测试 — interaction 上行请求被正确转发给 InteractionService**

先修改 `crates/adapters/ws/bcs-ws/tests/frame_compat.rs` 的 `new_state()`：新增一个 recording fixture 记录 `InteractionService` 收到的调用。在文件里现有 `RecordingXxx` fixture 群旁边新增：

```rust
#[derive(Default)]
struct RecordingInteractionService {
    requested: Mutex<Vec<bcs_service_api::ProviderInteractionRequestedCommand>>,
    resolved: Mutex<Vec<bcs_service_api::ProviderInteractionResolvedCommand>>,
}

#[async_trait]
impl bcs_service_api::InteractionService for RecordingInteractionService {
    async fn on_provider_requested(
        &self,
        command: bcs_service_api::ProviderInteractionRequestedCommand,
    ) -> ServiceResult<bcs_service_api::InteractionRequestedOutcome> {
        self.requested.lock().await.push(command);
        Ok(bcs_service_api::InteractionRequestedOutcome::Stored)
    }

    async fn on_provider_resolved(
        &self,
        command: bcs_service_api::ProviderInteractionResolvedCommand,
    ) -> ServiceResult<()> {
        self.resolved.lock().await.push(command);
        Ok(())
    }

    async fn resolve(
        &self,
        _command: bcs_service_api::ResolveInteractionCommand,
    ) -> Result<bcs_service_api::ResolveInteractionResult, bcs_service_api::InteractionServiceError> {
        Err(bcs_service_api::InteractionServiceError::NotFound)
    }

    async fn list_pending(
        &self,
        _bcs_session_id: &str,
    ) -> ServiceResult<Vec<bcs_service_api::InteractionFrontendEvent>> {
        Ok(Vec::new())
    }

    async fn invalidate_run(
        &self,
        _bcs_run_id: &str,
        _reason: &str,
        _invalidated_at_ms: u64,
    ) -> ServiceResult<usize> {
        Ok(0)
    }

    async fn cleanup_terminal(&self, _terminal_before_ms: u64) -> ServiceResult<usize> {
        Ok(0)
    }
}
```

`TestState` struct 新增字段 `interactions: Arc<RecordingInteractionService>`，`new_state()` 里新增：

```rust
let interactions = Arc::new(RecordingInteractionService::default());
```

`BotDispatchState { ... }` 构造里新增一行：

```rust
interactions: interactions.clone(),
```

`TestState { ... }` 返回值里新增 `interactions,`。

然后新增测试（放在现有 `bot_v3_*` 测试群附近）：

```rust
#[tokio::test]
async fn bot_v3_interaction_requested_forwards_to_interaction_service() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-v3-interaction",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-interaction",
            "protocol_version": 3,
            "client_kind": "bcs-bridge"
        })),
    ));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&connect).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();
    recv_response(&mut rx).await;

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-interaction".to_string(),
            bot_id: "bot-interaction".to_string(),
            group_id: "group-2".to_string(),
            bcs_session_id: Some("group-2:11223344".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;

    let payload = serde_json::json!({
        "runId": "run-interaction",
        "sessionId": "group-2:11223344",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-1",
        "phase": "requested",
        "kind": "exec",
        "command": "rm -rf /tmp/x"
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(1)));
    dispatch_frame(
        &state.dispatch_state,
        &serde_json::to_string(&event).unwrap(),
        &tx,
        &mut registered_bot_id,
    )
    .await
    .unwrap();

    let requested = state.interactions.requested.lock().await;
    assert_eq!(requested.len(), 1);
    assert_eq!(requested[0].bcs_run_id, "run-interaction");
    assert_eq!(requested[0].interaction_id, "int-1");
    assert_eq!(requested[0].bcs_session_id, "group-2:11223344");
    assert_eq!(requested[0].group_id, "group-2");
    assert_eq!(requested[0].bot_id, "bot-interaction");
    assert!(matches!(
        requested[0].provider_target,
        bcs_service_api::BotDeliveryTarget::WebSocket { ref bot_id } if bot_id == "bot-interaction"
    ));
    assert!(matches!(requested[0].kind, bcs_service_api::InteractionKind::Exec));
}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cargo test --package bcs-ws --test frame_compat bot_v3_interaction_requested_forwards_to_interaction_service -- --nocapture`
Expected: 编译失败（`BotDispatchState` 没有 `interactions` 字段）或运行失败（`normalize_v3_event` 拒绝 interaction 事件，`dispatch_frame` 返回 `Err`，`.unwrap()` panic）。此时应先只加 `BotDispatchState` 字段和测试 fixture，暂不改 `run_event_v3.rs`，确认失败原因是"interaction 被拒绝"而非编译错误。

- [ ] **Step 3: `BotDispatchState` 新增 `interactions` 字段**

在 `crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs` 的 `BotDispatchState` struct 定义中新增字段：

```rust
pub struct BotDispatchState {
    pub bot_runtime: Arc<dyn BotRuntimeConnectionService>,
    pub message_flow: Arc<dyn MessageFlowService>,
    pub collaboration_runtime: Arc<dyn CollaborationRuntimeService>,
    pub bot_run_context: Arc<dyn BotRunContextPort>,
    pub bot_connections: Arc<BotConnectionRegistry>,
    pub run_channels: Arc<RunChannelManager>,
    pub task_callback: Option<Arc<dyn TaskCallbackHook>>,
    pub session_management: Arc<dyn SessionManagementService>,
    pub group_dispatch: Arc<dyn GroupDispatchContextPort>,
    pub callback_dispatch: Arc<dyn SessionCallbackDispatchPort>,
    pub system_message: Option<Arc<dyn SystemMessageService>>,
    pub coordination_processed: Arc<Mutex<HashMap<String, u64>>>,
    pub agent_credential_backfill: Option<Arc<dyn super::AgentCredentialBackfillPort>>,
    pub interactions: Arc<dyn bcs_service_api::InteractionService>,
}
```

顶部 `use bcs_service_api::{...}` 导入列表中加入 `InteractionService`。

修改 `crates/adapters/ws/bcs-ws/tests/token_expiry_e2e.rs:286` 附近的 `BotDispatchState { ... }` 构造，新增一行（用 `bcs_test_support::NoopInteractionService` 即可，该文件不测 interaction 行为）：

```rust
interactions: Arc::new(bcs_test_support::NoopInteractionService),
```

- [ ] **Step 4: 运行测试确认编译通过、行为仍失败**

Run: `cargo test --package bcs-ws --test frame_compat bot_v3_interaction_requested_forwards_to_interaction_service -- --nocapture`
Expected: 编译通过；运行失败，错误信息包含 "V3 interaction events are not supported"（来自 `normalize_v3_event` 现有拒绝逻辑）。

- [ ] **Step 5: 实现 `handle_interaction_event_v3` 与 kind 转换**

在 `crates/adapters/ws/bcs-ws/src/bot/run_event_v3.rs` 顶部 `use` 列表中新增：

```rust
use bcs_protocol::stream::{InteractionEvent as WireInteractionEvent, InteractionKind as WireInteractionKind, InteractionPhase};
```

在文件末尾新增转换函数与处理函数：

```rust
fn to_app_interaction_kind(kind: WireInteractionKind) -> bcs_service_api::InteractionKind {
    match kind {
        WireInteractionKind::Exec => bcs_service_api::InteractionKind::Exec,
        WireInteractionKind::AskUser => bcs_service_api::InteractionKind::AskUser,
        WireInteractionKind::ModeSwitch => bcs_service_api::InteractionKind::ModeSwitch,
    }
}

/// 处理 V3 Interaction 事件（HITL 上行）。与 `normalize_v3_event` 平级，
/// 不产出 `NormalizedBotEvent`：Interaction 是请求-响应语义，不经过
/// agent/chat 的流式事件管道（不计 terminal_fingerprint，不占用
/// per-run seq 计数器）。幂等完全交由 `InteractionService` 处理。
pub(super) async fn handle_interaction_event_v3(
    state: &super::dispatcher::BotDispatchState,
    bot_id: &str,
    event: WireInteractionEvent,
    outer_seq: Option<u64>,
) -> Result<()> {
    let session_id = event
        .session_id
        .as_deref()
        .ok_or_else(|| BotWsDispatchError::InvalidFrameFormat("V3 interaction missing sessionId".into()))?;
    let seq = event
        .seq
        .ok_or_else(|| BotWsDispatchError::InvalidFrameFormat("V3 interaction missing seq".into()))?;

    let context = super::dispatcher::validate_v3_run_scope(state, bot_id, &event.run_id, session_id, seq, outer_seq).await?;

    match event.phase {
        InteractionPhase::Requested => {
            state
                .interactions
                .on_provider_requested(bcs_service_api::ProviderInteractionRequestedCommand {
                    bcs_run_id: event.run_id.clone(),
                    provider_run_id: event.run_id.clone(),
                    interaction_id: event.interaction_id.clone(),
                    kind: to_app_interaction_kind(event.kind),
                    bcs_session_id: context.bcs_session_id.clone().unwrap_or_default(),
                    group_id: context.group_id.clone(),
                    bot_id: bot_id.to_string(),
                    run_deadline_ms: context.deadline_ms,
                    provider_target: bcs_service_api::BotDeliveryTarget::WebSocket {
                        bot_id: bot_id.to_string(),
                    },
                    provider_bypass_headers: Vec::new(),
                    payload: event.raw.clone(),
                    received_at_ms: bcs_protocol::now_ms(),
                })
                .await
                .map_err(BotWsDispatchError::ServiceError)?;
        }
        InteractionPhase::Resolved => {
            state
                .interactions
                .on_provider_resolved(bcs_service_api::ProviderInteractionResolvedCommand {
                    bcs_run_id: event.run_id.clone(),
                    provider_run_id: event.run_id.clone(),
                    interaction_id: event.interaction_id.clone(),
                    kind: to_app_interaction_kind(event.kind),
                    payload: event.raw.clone(),
                    received_at_ms: bcs_protocol::now_ms(),
                })
                .await
                .map_err(BotWsDispatchError::ServiceError)?;
        }
    }
    Ok(())
}
```

`dispatcher` 与 `run_event_v3` 是 `bot` 模块下的同级子模块（`crates/adapters/ws/bcs-ws/src/bot/mod.rs`：`pub mod dispatcher;` 与 `mod run_event_v3;`），`run_event_v3.rs` 已有的 `pub(super) fn normalize_v3_event` 就是这个可见性模式的先例。因此回到 Task 1 Step 2，`validate_v3_run_scope` 的签名要从 `async fn` 改为 `pub(super) async fn`（`super` 即 `bot` 模块，对同级子模块 `run_event_v3` 可见）。

- [ ] **Step 6: 在 `normalize_v3_event` 分流到新函数**

修改 `crates/adapters/ws/bcs-ws/src/bot/run_event_v3.rs` 中 `normalize_v3_event` 函数签名与函数体。当前签名：

```rust
pub(super) fn normalize_v3_event(event: &EventFrame) -> Result<NormalizedBotEvent> {
```

`StreamEvent::Interaction(_)` 分支目前是：

```rust
StreamEvent::Interaction(_) => Err(BotWsDispatchError::InvalidFrameFormat(
    "V3 interaction events are not supported on Bot WebSocket yet".into(),
)),
```

由于 `handle_interaction_event_v3` 是 `async fn` 而 `normalize_v3_event` 是同步函数、且返回类型不同（`NormalizedBotEvent` vs `()`），不能在 `normalize_v3_event` 内部直接调用。改为在 `dispatcher.rs::handle_event_frame` 中，解析出 `StreamEvent` 之后先行分流，再决定是否进入 `normalize_v3_event`。

具体做法：把 `parse_run_event_v3` 的调用从 `normalize_v3_event` 内部提到 `handle_event_frame` 调用处之前一层。在 `run_event_v3.rs` 新增一个薄的分流函数：

```rust
pub(super) enum V3RunEvent {
    Normalized(NormalizedBotEvent),
    Interaction(WireInteractionEvent),
}

/// 解析原始 V3 EventFrame payload，区分"走 NormalizedBotEvent 管道的
/// 流式事件"与"独立处理的 Interaction 事件"。
pub(super) fn classify_v3_event(event: &EventFrame) -> Result<V3RunEvent> {
    let raw = event
        .payload
        .clone()
        .ok_or_else(|| BotWsDispatchError::InvalidFrameFormat("V3 event payload is required".into()))?;
    let canonical = parse_run_event_v3(&event.event, raw).map_err(|error| {
        BotWsDispatchError::InvalidFrameFormat(format!("invalid V3 run event: {error}"))
    })?;
    match canonical {
        StreamEvent::Agent(agent) => normalize_agent(agent).map(V3RunEvent::Normalized),
        StreamEvent::Chat(chat) => normalize_chat(chat).map(V3RunEvent::Normalized),
        StreamEvent::Interaction(interaction) => Ok(V3RunEvent::Interaction(interaction)),
        StreamEvent::Ping { .. } | StreamEvent::Unknown { .. } => Err(
            BotWsDispatchError::InvalidFrameFormat("unsupported V3 run event".into()),
        ),
    }
}
```

保留原 `normalize_v3_event` 函数不变（其他调用方/测试可能直接依赖它），但改为内部调用 `classify_v3_event` 并对 `Interaction` 变体报同样的错误（保持向后兼容，供未分流路径使用）：

```rust
pub(super) fn normalize_v3_event(event: &EventFrame) -> Result<NormalizedBotEvent> {
    match classify_v3_event(event)? {
        V3RunEvent::Normalized(normalized) => Ok(normalized),
        V3RunEvent::Interaction(_) => Err(BotWsDispatchError::InvalidFrameFormat(
            "V3 interaction events must be routed via classify_v3_event, not normalize_v3_event".into(),
        )),
    }
}
```

在 `dispatcher.rs::handle_event_frame` 中，找到调用 `normalize_v3_event(event)?` 的那一行（`is_v3` 分支内），改为调用 `classify_v3_event`：

```rust
let (
    run_id,
    bcs_group_id,
    event_type,
    mut event_payload,
    event_state,
    is_final,
    v3_session_id,
    v3_seq,
) = if is_v3 {
    match super::run_event_v3::classify_v3_event(event)? {
        super::run_event_v3::V3RunEvent::Interaction(interaction) => {
            return super::run_event_v3::handle_interaction_event_v3(
                state,
                &bot_id,
                interaction,
                event.seq,
            )
            .await;
        }
        super::run_event_v3::V3RunEvent::Normalized(normalized) => normalized,
    }
} else {
    // ... 原 legacy 分支不变
};
```

- [ ] **Step 7: 运行测试验证通过**

Run: `cargo test --package bcs-ws --test frame_compat -- --nocapture`
Expected: 新增的 `bot_v3_interaction_requested_forwards_to_interaction_service` PASS；所有既有 `bot_v3_*` 测试仍 PASS（`classify_v3_event`/`normalize_v3_event` 对 agent/chat 的行为未变）。

- [ ] **Step 8: 补充测试 — resolved phase、seq 校验复用、未知 bot 安全丢弃**

新增三个测试用例到 `frame_compat.rs`：

```rust
#[tokio::test]
async fn bot_v3_interaction_resolved_forwards_to_interaction_service() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-v3-interaction-resolved",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-interaction-2",
            "protocol_version": 3,
            "client_kind": "bcs-bridge"
        })),
    ));
    dispatch_frame(&state.dispatch_state, &serde_json::to_string(&connect).unwrap(), &tx, &mut registered_bot_id)
        .await
        .unwrap();
    recv_response(&mut rx).await;

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-interaction-2".to_string(),
            bot_id: "bot-interaction-2".to_string(),
            group_id: "group-3".to_string(),
            bcs_session_id: Some("group-3:aabbccdd".to_string()),
            deadline_ms: u64::MAX,
            terminal: false,
        })
        .await;

    let payload = serde_json::json!({
        "runId": "run-interaction-2",
        "sessionId": "group-3:aabbccdd",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-2",
        "phase": "resolved",
        "kind": "ask_user",
        "resolution": {"answer": "yes"}
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(1)));
    dispatch_frame(&state.dispatch_state, &serde_json::to_string(&event).unwrap(), &tx, &mut registered_bot_id)
        .await
        .unwrap();

    let resolved = state.interactions.resolved.lock().await;
    assert_eq!(resolved.len(), 1);
    assert_eq!(resolved[0].interaction_id, "int-2");
    assert!(matches!(resolved[0].kind, bcs_service_api::InteractionKind::AskUser));
}

#[tokio::test]
async fn bot_v3_interaction_rejects_terminal_run() {
    let state = new_state();
    let (tx, mut rx) = mpsc::channel(8);
    let mut registered_bot_id = None;

    let connect = BcsFrame::Request(RequestFrame::new(
        "connect-v3-interaction-terminal",
        "bot.connect",
        Some(serde_json::json!({
            "bot_id": "bot-interaction-3",
            "protocol_version": 3,
            "client_kind": "bcs-bridge"
        })),
    ));
    dispatch_frame(&state.dispatch_state, &serde_json::to_string(&connect).unwrap(), &tx, &mut registered_bot_id)
        .await
        .unwrap();
    recv_response(&mut rx).await;

    state
        .bot_run_context
        .put_context(BotRunContext {
            run_id: "run-interaction-3".to_string(),
            bot_id: "bot-interaction-3".to_string(),
            group_id: "group-4".to_string(),
            bcs_session_id: Some("group-4:99887766".to_string()),
            deadline_ms: u64::MAX,
            terminal: true,
        })
        .await;

    let payload = serde_json::json!({
        "runId": "run-interaction-3",
        "sessionId": "group-4:99887766",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-3",
        "phase": "requested",
        "kind": "exec"
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(1)));
    let error = dispatch_frame(&state.dispatch_state, &serde_json::to_string(&event).unwrap(), &tx, &mut registered_bot_id)
        .await
        .expect_err("terminal run must reject new interaction requests");
    assert!(error.to_string().contains("terminal or expired"));

    assert_eq!(state.interactions.requested.lock().await.len(), 0);
}

#[tokio::test]
async fn bot_v3_interaction_from_unregistered_bot_is_dropped_safely() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id: Option<String> = None;

    let payload = serde_json::json!({
        "runId": "run-orphan",
        "sessionId": "group-5:00000000",
        "seq": 1,
        "ts": 100,
        "interactionId": "int-orphan",
        "phase": "requested",
        "kind": "exec"
    });
    let event = BcsFrame::Event(EventFrame::new("interaction", Some(payload), Some(1)));
    let outcome = dispatch_frame(&state.dispatch_state, &serde_json::to_string(&event).unwrap(), &tx, &mut registered_bot_id)
        .await;
    assert!(outcome.is_ok(), "unregistered-bot events must be dropped without error, matching agent/chat behavior");
    assert_eq!(state.interactions.requested.lock().await.len(), 0);
}
```

- [ ] **Step 9: 运行完整测试套件**

Run: `cargo test --package bcs-ws --test frame_compat -- --nocapture`
Expected: 全部 PASS，包括 4 个新增的 interaction 测试和所有既有测试。

- [ ] **Step 10: Commit**

```bash
git add crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs crates/adapters/ws/bcs-ws/src/bot/run_event_v3.rs crates/adapters/ws/bcs-ws/tests/frame_compat.rs crates/adapters/ws/bcs-ws/tests/token_expiry_e2e.rs
git commit -m "feat(bcs-ws): accept V3 interaction events on Bot WebSocket uplink

BotDispatchState 新增 interactions 字段;run_event_v3.rs 新增
classify_v3_event 分流与 handle_interaction_event_v3,Interaction
事件不再进入 NormalizedBotEvent 管道,复用 Task 1 的
validate_v3_run_scope 做身份/终态校验,幂等完全交给
InteractionService。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: 把 `interaction_kind_slug` 提升为 `InteractionKind::as_slug`(两条链路共用)

**Files:**
- Modify: `crates/service-api/bcs-service-api/src/core/interaction.rs`(新增 `impl InteractionKind { pub fn as_slug(&self) -> &'static str }`)
- Modify: `crates/adapters/http/bcs-provider-http/src/lib.rs`(删除私有 `interaction_kind_slug`，调用点改用 `command.kind.as_slug()`)
- Test: `crates/service-api/bcs-service-api/tests/`(新增单元测试文件验证 slug 映射)

**Interfaces:**
- Produces: `impl InteractionKind { pub fn as_slug(&self) -> &'static str }`(`bcs-service-api` 顶层重导出的 `InteractionKind` 类型，Task 4 的 WS 下行实现将调用它)
- Consumes: 无（本任务不依赖前两个任务的产出，可与 Task 1/2 并行，但按顺序执行以保持提交历史线性）

`interaction_kind_slug` 目前是 `bcs-provider-http` 内部私有自由函数（`lib.rs:326`），只有一个调用点（`lib.rs:282`）。提升为 `InteractionKind` 的方法后，两条链路（HTTP 现有实现、WS 新实现）共用同一份映射，避免未来 `InteractionKind` 新增变体时两处遗漏。

- [ ] **Step 1: 写失败测试 — `as_slug` 映射**

新建 `crates/service-api/bcs-service-api/tests/interaction_kind_slug.rs`：

```rust
use bcs_service_api::InteractionKind;

#[test]
fn exec_maps_to_exec_slug() {
    assert_eq!(InteractionKind::Exec.as_slug(), "exec");
}

#[test]
fn ask_user_maps_to_ask_user_slug() {
    assert_eq!(InteractionKind::AskUser.as_slug(), "ask_user");
}

#[test]
fn mode_switch_maps_to_mode_switch_slug() {
    assert_eq!(InteractionKind::ModeSwitch.as_slug(), "mode_switch");
}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cargo test --package bcs-service-api --test interaction_kind_slug -- --nocapture`
Expected: 编译失败，"no method named `as_slug` found for enum `InteractionKind`"。

- [ ] **Step 3: 实现 `as_slug`**

在 `crates/service-api/bcs-service-api/src/core/interaction.rs` 的 `InteractionKind` 定义之后，新增：

```rust
impl InteractionKind {
    /// Wire-level slug used by both the HTTP Provider callback and the Bot
    /// WebSocket `interaction.resolve` params. Kept in sync with the
    /// `#[serde(rename_all = "snake_case")]` derive above.
    pub fn as_slug(&self) -> &'static str {
        match self {
            Self::Exec => "exec",
            Self::AskUser => "ask_user",
            Self::ModeSwitch => "mode_switch",
        }
    }
}
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cargo test --package bcs-service-api --test interaction_kind_slug -- --nocapture`
Expected: 3 个测试全部 PASS。

- [ ] **Step 5: 替换 `bcs-provider-http` 现有调用点**

在 `crates/adapters/http/bcs-provider-http/src/lib.rs` 中：

删除私有函数定义（第 326 行附近）：

```rust
fn interaction_kind_slug(kind: InteractionKind) -> &'static str {
    match kind {
        InteractionKind::Exec => "exec",
        InteractionKind::AskUser => "ask_user",
        InteractionKind::ModeSwitch => "mode_switch",
    }
}
```

调用点（第 282 行）从：

```rust
Value::String(interaction_kind_slug(command.kind).to_string()),
```

改为：

```rust
Value::String(command.kind.as_slug().to_string()),
```

- [ ] **Step 6: 运行 bcs-provider-http 既有测试确认未破坏**

Run: `cargo test --package bcs-provider-http --test provider_transport_contract -- --nocapture`
Expected: 全部 PASS（行为完全等价，只是调用方式变了）。

- [ ] **Step 7: Commit**

```bash
git add crates/service-api/bcs-service-api/src/core/interaction.rs crates/service-api/bcs-service-api/tests/interaction_kind_slug.rs crates/adapters/http/bcs-provider-http/src/lib.rs
git commit -m "refactor(bcs-service-api): promote interaction_kind_slug to InteractionKind::as_slug

原为 bcs-provider-http 内部私有函数,提升为 InteractionKind 的
公开方法,供即将新增的 WS 下行实现(Task 4)与现有 HTTP 实现共用,
避免未来新增 kind 变体时两处定义漏改一处。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: 下行 — `BotConnectionRegistry` 实现 `InteractionProviderPort`

**Files:**
- Modify: `crates/adapters/ws/bcs-ws/src/bot/connection_registry.rs`(新增 `pending_interaction_requests` 字段、`impl InteractionProviderPort`、响应路由方法)
- Modify: `crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs`(在 `handle_response_frame` 中接入新的响应路由)
- Test: `crates/adapters/ws/bcs-ws/tests/bot_delivery_port.rs`(新增单元测试，紧跟现有 `abort` 测试风格)

**Interfaces:**
- Consumes: `bcs_service_api::{InteractionProviderPort, InteractionProviderCommand, InteractionProviderAck, InteractionKind}`(Task 3 产出的 `InteractionKind::as_slug`)；`bcs_domain::BotDeliveryTarget`
- Produces: `impl InteractionProviderPort for BotConnectionRegistry`；`pub async fn resolve_pending_interaction_request(&self, request_id: &str, response: ResponseFrame) -> bool`（对称于现有 `resolve_pending_abort_request`，供 Task 5 组合根接线后、`dispatcher.rs::handle_response_frame` 调用）

本任务完整复刻 `abort_inner`(`connection_registry.rs`)已经验证过的"构造 RequestFrame → oneshot 挂起 → 发送 → 等待 ResponseFrame → 超时清理 → ok/error 映射"模式，应用到 `interaction.resolve` 方法。关键差异：传输超时用 15,000ms（对齐 `chat.abort` 量级，而非 HTTP Provider 的 65,000ms）；错误映射额外区分 unknown-method（判定 `retryable:false`，防止不支持 HITL 的旧版 bot 被无限重试）。

- [ ] **Step 1: 写失败测试 — 正常 ack 往返**

在 `crates/adapters/ws/bcs-ws/tests/bot_delivery_port.rs` 中，参照文件里现有的 `abort` 测试结构（第 29 行、142 行、203 行附近的用例），新增：

```rust
#[tokio::test]
async fn resolve_interaction_returns_ack_on_success_response() {
    let registry = Arc::new(BotConnectionRegistry::new());
    let (tx, mut rx) = mpsc::channel(8);
    registry.connect("bot-resolve-1".to_string(), tx).await;

    let command = bcs_service_api::InteractionProviderCommand {
        target: bcs_domain::BotDeliveryTarget::WebSocket {
            bot_id: "bot-resolve-1".to_string(),
        },
        provider_bypass_headers: Vec::new(),
        bcs_run_id: "run-1".to_string(),
        provider_run_id: "run-1".to_string(),
        bcs_session_id: "group-1:aaaaaaaa".to_string(),
        group_id: "group-1".to_string(),
        bot_id: "bot-resolve-1".to_string(),
        interaction_id: "int-1".to_string(),
        kind: bcs_service_api::InteractionKind::Exec,
        idempotency_key: "idem-1".to_string(),
        resolution: serde_json::json!({"approved": true}),
    };

    let registry_clone = registry.clone();
    let responder = tokio::spawn(async move {
        let sent = rx.recv().await.expect("frame sent to bot");
        let frame: serde_json::Value = serde_json::from_str(&sent).unwrap();
        assert_eq!(frame["method"], "interaction.resolve");
        assert_eq!(frame["params"]["bcsRunId"], "run-1");
        assert_eq!(frame["params"]["interactionId"], "int-1");
        assert_eq!(frame["params"]["kind"], "exec");
        assert_eq!(frame["params"]["idempotencyKey"], "idem-1");
        assert_eq!(frame["params"]["approved"], true);
        let request_id = frame["id"].as_str().unwrap().to_string();
        registry_clone
            .resolve_pending_interaction_request(
                &request_id,
                bcs_protocol::ResponseFrame::ok(request_id, None),
            )
            .await;
    });

    let ack = registry.resolve_interaction(command).await.unwrap();
    assert!(ack.ok);
    assert_eq!(ack.retryable, None);
    assert_eq!(ack.error, None);

    responder.await.unwrap();
}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cargo test --package bcs-ws --test bot_delivery_port resolve_interaction_returns_ack_on_success_response -- --nocapture`
Expected: 编译失败（`BotConnectionRegistry` 未实现 `InteractionProviderPort`，`resolve_pending_interaction_request` 不存在）。

- [ ] **Step 3: 实现 `pending_interaction_requests` 字段与 `InteractionProviderPort`**

在 `crates/adapters/ws/bcs-ws/src/bot/connection_registry.rs` 顶部 `use bcs_service_api::{...}` 列表中新增 `InteractionProviderPort, InteractionProviderCommand, InteractionProviderAck, InteractionKind`：

```rust
use bcs_service_api::{
    BotAbortDeliveryCommand, BotAbortDeliveryResult, BotConnectionControlPort, BotDeliveryCommand,
    BotDeliveryPort, BotDeliveryResult, InteractionKind, InteractionProviderAck,
    InteractionProviderCommand, InteractionProviderPort, KickReason, ServiceError, ServiceResult,
};
```

`BotConnectionRegistry` struct 新增字段：

```rust
#[derive(Debug, Default)]
pub struct BotConnectionRegistry {
    pub connection_epoch: crate::shared::connection_epoch::ConnectionEpoch,
    connections: RwLock<HashMap<String, BotConnection>>,
    pending_requests: RwLock<HashMap<String, oneshot::Sender<serde_json::Value>>>,
    pending_abort_requests: RwLock<HashMap<String, oneshot::Sender<ResponseFrame>>>,
    pending_interaction_requests: RwLock<HashMap<String, oneshot::Sender<ResponseFrame>>>,
}
```

新增常量与 `impl InteractionProviderPort`（放在 `impl BotDeliveryPort for BotConnectionRegistry` 块之后）：

```rust
/// WS 是常驻连接,往返延迟应远低于 HTTP 回调场景(HttpProviderTransport
/// 用 65s);对齐 chat.abort 的量级。
const INTERACTION_RESOLVE_TIMEOUT_MS: u64 = 15_000;

#[async_trait]
impl InteractionProviderPort for BotConnectionRegistry {
    async fn resolve_interaction(
        &self,
        command: InteractionProviderCommand,
    ) -> ServiceResult<InteractionProviderAck> {
        let BotDeliveryTarget::WebSocket { bot_id } = &command.target else {
            return Err(ServiceError::InvalidOperation {
                message: "websocket registry cannot resolve an HTTP Provider interaction".to_string(),
                request_id: Some(command.bcs_run_id),
            });
        };

        let mut params = match command.resolution {
            serde_json::Value::Object(map) => map,
            _ => {
                return Err(ServiceError::InvalidOperation {
                    message: "interaction resolution must be a JSON object".to_string(),
                    request_id: Some(command.bcs_run_id),
                });
            }
        };
        params.insert(
            "bcsRunId".to_string(),
            serde_json::Value::String(command.bcs_run_id.clone()),
        );
        params.insert(
            "runId".to_string(),
            serde_json::Value::String(command.provider_run_id),
        );
        params.insert(
            "interactionId".to_string(),
            serde_json::Value::String(command.interaction_id),
        );
        params.insert(
            "kind".to_string(),
            serde_json::Value::String(command.kind.as_slug().to_string()),
        );
        params.insert(
            "idempotencyKey".to_string(),
            serde_json::Value::String(command.idempotency_key),
        );

        let request_id = uuid::Uuid::new_v4().to_string();
        let frame = BcsFrame::Request(RequestFrame::new(
            request_id.clone(),
            "interaction.resolve",
            Some(serde_json::Value::Object(params)),
        ));
        let frame_json = serde_json::to_string(&frame).map_err(|error| {
            ServiceError::InternalError(format!("serialize interaction.resolve frame: {error}"))
        })?;

        let (tx, rx) = oneshot::channel();
        self.pending_interaction_requests
            .write()
            .await
            .insert(request_id.clone(), tx);

        if self.send_frame_json(bot_id, frame_json).await.is_err() {
            self.pending_interaction_requests
                .write()
                .await
                .remove(&request_id);
            return Err(ServiceError::BotNotConnected(bot_id.clone()));
        }

        let response = match tokio::time::timeout(
            std::time::Duration::from_millis(INTERACTION_RESOLVE_TIMEOUT_MS),
            rx,
        )
        .await
        {
            Ok(Ok(response)) => response,
            Ok(Err(_)) => {
                return Err(ServiceError::InternalError(
                    "interaction.resolve response channel closed".to_string(),
                ));
            }
            Err(_) => {
                self.pending_interaction_requests
                    .write()
                    .await
                    .remove(&request_id);
                return Err(ServiceError::InternalError(format!(
                    "interaction.resolve request timed out after {INTERACTION_RESOLVE_TIMEOUT_MS}ms"
                )));
            }
        };

        if !response.ok {
            let unsupported = response
                .error
                .as_ref()
                .is_some_and(|error| is_unknown_method_code(&error.code));
            let message = response.error.map_or_else(
                || "Bot rejected interaction.resolve".to_string(),
                |error| format!("{}: {}", error.code, error.message),
            );
            return Ok(InteractionProviderAck {
                ok: false,
                retryable: Some(!unsupported),
                error: Some(message),
            });
        }

        Ok(InteractionProviderAck {
            ok: true,
            retryable: None,
            error: None,
        })
    }
}

impl BotConnectionRegistry {
    pub async fn resolve_pending_interaction_request(
        &self,
        request_id: &str,
        response: ResponseFrame,
    ) -> bool {
        let mut pending = self.pending_interaction_requests.write().await;
        let Some(tx) = pending.remove(request_id) else {
            return false;
        };
        let _ = tx.send(response);
        true
    }
}
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cargo test --package bcs-ws --test bot_delivery_port resolve_interaction_returns_ack_on_success_response -- --nocapture`
Expected: PASS。

- [ ] **Step 5: 补充测试 — 超时、unknown-method、业务拒绝三种路径**

```rust
#[tokio::test]
async fn resolve_interaction_fails_immediately_when_bot_not_connected() {
    let registry = Arc::new(BotConnectionRegistry::new());

    let command = bcs_service_api::InteractionProviderCommand {
        target: bcs_domain::BotDeliveryTarget::WebSocket {
            bot_id: "bot-never-connected".to_string(),
        },
        provider_bypass_headers: Vec::new(),
        bcs_run_id: "run-disconnected".to_string(),
        provider_run_id: "run-disconnected".to_string(),
        bcs_session_id: "group-1:bbbbbbbb".to_string(),
        group_id: "group-1".to_string(),
        bot_id: "bot-never-connected".to_string(),
        interaction_id: "int-disconnected".to_string(),
        kind: bcs_service_api::InteractionKind::Exec,
        idempotency_key: "idem-disconnected".to_string(),
        resolution: serde_json::json!({}),
    };

    let error = registry
        .resolve_interaction(command)
        .await
        .expect_err("unconnected bot must fail immediately, without waiting for the transport timeout");
    assert!(matches!(error, bcs_service_api::ServiceError::BotNotConnected(_)));
}

#[tokio::test(start_paused = true)]
async fn resolve_interaction_times_out_when_bot_does_not_respond() {
    let registry = Arc::new(BotConnectionRegistry::new());
    let (tx, _rx) = mpsc::channel(8);
    registry.connect("bot-resolve-timeout".to_string(), tx).await;

    let command = bcs_service_api::InteractionProviderCommand {
        target: bcs_domain::BotDeliveryTarget::WebSocket {
            bot_id: "bot-resolve-timeout".to_string(),
        },
        provider_bypass_headers: Vec::new(),
        bcs_run_id: "run-timeout".to_string(),
        provider_run_id: "run-timeout".to_string(),
        bcs_session_id: "group-1:cccccccc".to_string(),
        group_id: "group-1".to_string(),
        bot_id: "bot-resolve-timeout".to_string(),
        interaction_id: "int-timeout".to_string(),
        kind: bcs_service_api::InteractionKind::Exec,
        idempotency_key: "idem-timeout".to_string(),
        resolution: serde_json::json!({}),
    };

    // bot 已连接但永不回复 ResponseFrame。#[tokio::test(start_paused = true)]
    // 启动一个暂停的 tokio 虚拟时钟：resolve_interaction 内部的
    // tokio::time::timeout(15s, rx) 注册后不会真实等待，tokio 在所有任务
    // 都阻塞于 await 时自动把虚拟时钟推进到下一个到期的 timer，也就是
    // 这里的 15s 超时，整个测试实际运行耗时接近 0，不会拖慢 CI。
    let error = registry
        .resolve_interaction(command)
        .await
        .expect_err("must time out when the bot never sends a ResponseFrame");
    assert!(matches!(error, bcs_service_api::ServiceError::InternalError(_)));
    assert!(error.to_string().contains("timed out"));
}

#[tokio::test]
async fn resolve_interaction_maps_unknown_method_to_non_retryable() {
    let registry = Arc::new(BotConnectionRegistry::new());
    let (tx, mut rx) = mpsc::channel(8);
    registry.connect("bot-resolve-unsupported".to_string(), tx).await;

    let command = bcs_service_api::InteractionProviderCommand {
        target: bcs_domain::BotDeliveryTarget::WebSocket {
            bot_id: "bot-resolve-unsupported".to_string(),
        },
        provider_bypass_headers: Vec::new(),
        bcs_run_id: "run-unsupported".to_string(),
        provider_run_id: "run-unsupported".to_string(),
        bcs_session_id: "group-1:cccccccc".to_string(),
        group_id: "group-1".to_string(),
        bot_id: "bot-resolve-unsupported".to_string(),
        interaction_id: "int-unsupported".to_string(),
        kind: bcs_service_api::InteractionKind::AskUser,
        idempotency_key: "idem-unsupported".to_string(),
        resolution: serde_json::json!({}),
    };

    let registry_clone = registry.clone();
    let responder = tokio::spawn(async move {
        let sent = rx.recv().await.expect("frame sent to bot");
        let frame: serde_json::Value = serde_json::from_str(&sent).unwrap();
        let request_id = frame["id"].as_str().unwrap().to_string();
        registry_clone
            .resolve_pending_interaction_request(
                &request_id,
                bcs_protocol::ResponseFrame::err(
                    request_id,
                    "unknown_method",
                    "interaction.resolve is not supported",
                ),
            )
            .await;
    });

    let ack = registry.resolve_interaction(command).await.unwrap();
    assert!(!ack.ok);
    assert_eq!(ack.retryable, Some(false));

    responder.await.unwrap();
}

#[tokio::test]
async fn resolve_interaction_maps_business_rejection_to_retryable() {
    let registry = Arc::new(BotConnectionRegistry::new());
    let (tx, mut rx) = mpsc::channel(8);
    registry.connect("bot-resolve-rejected".to_string(), tx).await;

    let command = bcs_service_api::InteractionProviderCommand {
        target: bcs_domain::BotDeliveryTarget::WebSocket {
            bot_id: "bot-resolve-rejected".to_string(),
        },
        provider_bypass_headers: Vec::new(),
        bcs_run_id: "run-rejected".to_string(),
        provider_run_id: "run-rejected".to_string(),
        bcs_session_id: "group-1:dddddddd".to_string(),
        group_id: "group-1".to_string(),
        bot_id: "bot-resolve-rejected".to_string(),
        interaction_id: "int-rejected".to_string(),
        kind: bcs_service_api::InteractionKind::ModeSwitch,
        idempotency_key: "idem-rejected".to_string(),
        resolution: serde_json::json!({}),
    };

    let registry_clone = registry.clone();
    let responder = tokio::spawn(async move {
        let sent = rx.recv().await.expect("frame sent to bot");
        let frame: serde_json::Value = serde_json::from_str(&sent).unwrap();
        let request_id = frame["id"].as_str().unwrap().to_string();
        registry_clone
            .resolve_pending_interaction_request(
                &request_id,
                bcs_protocol::ResponseFrame::err(
                    request_id,
                    "resolution_conflict",
                    "another resolution is already in flight",
                ),
            )
            .await;
    });

    let ack = registry.resolve_interaction(command).await.unwrap();
    assert!(!ack.ok);
    assert_eq!(ack.retryable, Some(true));

    responder.await.unwrap();
}
```

- [ ] **Step 6: 运行测试验证全部通过**

Run: `cargo test --package bcs-ws --test bot_delivery_port resolve_interaction -- --nocapture`
Expected: 5 个测试全部 PASS（Step 1 的成功 ack + Step 5 的未连接/超时/unknown-method/业务拒绝）。

- [ ] **Step 7: 在 `handle_response_frame` 接入响应路由**

在 `crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs` 的 `handle_response_frame` 函数中（第 580 行附近，紧跟现有 `resolve_pending_abort_request` 调用逻辑之后），新增对称的路由尝试：

```rust
if state
    .bot_connections
    .resolve_pending_interaction_request(&res.id, res.clone())
    .await
{
    debug!(request_id = %res.id, ok = res.ok, "matched interaction.resolve ResponseFrame");
    return Ok(());
}
```

放置位置：紧跟在现有 `chat.abort` 匹配逻辑（`resolve_pending_abort_request` 分支，命中则 `return Ok(())`）之后，落在通用 `resolve_pending_request` 兜底逻辑之前——保持与 abort 完全对称的路由优先级。

- [ ] **Step 8: 运行 dispatcher 相关测试确认未破坏现有响应路由**

Run: `cargo test --package bcs-ws --test bot_delivery_port -- --nocapture`
Expected: 全部 PASS，包括新增的 5 个 interaction 测试和既有的 abort/chat.send 测试。

- [ ] **Step 9: Commit**

```bash
git add crates/adapters/ws/bcs-ws/src/bot/connection_registry.rs crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs crates/adapters/ws/bcs-ws/tests/bot_delivery_port.rs
git commit -m "feat(bcs-ws): implement InteractionProviderPort for BotConnectionRegistry

复刻 abort_inner 的 RequestFrame/oneshot 挂起模式实现
interaction.resolve 下行发送。15s 传输超时(对齐 chat.abort,
非 HTTP Provider 的 65s)。unknown-method 错误映射为
retryable:false,避免不支持 HITL 的旧版 bot 被无限重试。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: `InteractionProviderMux` 路由层 + 组合根接线

**Files:**
- Modify: `crates/adapters/http/bcs-provider-http/src/lib.rs`(新增 `InteractionProviderMux`，紧邻现有 `HistoryRequestMux`)
- Modify: `crates/bootstrap/bcs/src/server.rs`(`create_interaction_service` 签名新增 `bot_connections` 参数，内部构造 mux；3 处调用点补参数；`bot_ws_dispatch_state()` 新增字段赋值；`BotDispatchState` 字段清单同步——已在 Task 2 完成）
- Test: `crates/adapters/http/bcs-provider-http/tests/provider_transport_contract.rs`(新增 mux 路由测试)

**Interfaces:**
- Consumes: Task 4 产出的 `impl InteractionProviderPort for BotConnectionRegistry`；Task 2 产出的 `BotDispatchState.interactions` 字段
- Produces: `pub struct InteractionProviderMux`(`bcs-provider-http` crate，实现 `InteractionProviderPort`)

**关键约束核对(写代码前必读)**：`create_interaction_service`(`server.rs:3106`)函数体内有一行 `provider_transport.set_interactions(interactions.clone())`——这是 `HttpProviderTransport` 的具体方法（不在 `InteractionProviderPort` trait 上），供其内部的 SSE ingest 逻辑反向持有 `InteractionService` 引用。因此**不能**把 `create_interaction_service` 的 `provider_transport` 参数类型改成 trait object——必须保留 `Arc<HttpProviderTransport>` 具体类型给这一行用，同时新增一个 `bot_connections` 参数，在函数体内部构造 `InteractionProviderMux`，只把 mux（不是裸 `provider_transport`）传给 `InteractionManagement::new`。

- [ ] **Step 1: 写失败测试 — mux 按 target 类型路由**

在 `crates/adapters/http/bcs-provider-http/tests/provider_transport_contract.rs` 顶部找到现有的测试 fixture/mock `InteractionProviderPort` 实现（Task 4 之前设计文档提到 `management.rs` 里有 `BlockingProvider`/`RecordingProvider` 等 mock，`provider_transport_contract.rs` 可能有类似的本地 mock；若没有，在测试文件内新增）：

```rust
struct RecordingInteractionProvider {
    calls: std::sync::Mutex<Vec<bcs_service_api::InteractionProviderCommand>>,
    ack: bcs_service_api::InteractionProviderAck,
}

#[async_trait::async_trait]
impl bcs_service_api::InteractionProviderPort for RecordingInteractionProvider {
    async fn resolve_interaction(
        &self,
        command: bcs_service_api::InteractionProviderCommand,
    ) -> bcs_service_api::ServiceResult<bcs_service_api::InteractionProviderAck> {
        self.calls.lock().unwrap().push(command);
        Ok(self.ack.clone())
    }
}

#[tokio::test]
async fn interaction_provider_mux_routes_websocket_target_to_websocket_impl() {
    let websocket = std::sync::Arc::new(RecordingInteractionProvider {
        calls: std::sync::Mutex::new(Vec::new()),
        ack: bcs_service_api::InteractionProviderAck {
            ok: true,
            retryable: None,
            error: None,
        },
    });
    let provider = std::sync::Arc::new(bcs_provider_http::HttpProviderTransport::allowing_private_networks_for_tests());
    let mux = bcs_provider_http::InteractionProviderMux::new(websocket.clone(), provider.clone());

    let command = bcs_service_api::InteractionProviderCommand {
        target: bcs_domain::BotDeliveryTarget::WebSocket {
            bot_id: "bot-mux-1".to_string(),
        },
        provider_bypass_headers: Vec::new(),
        bcs_run_id: "run-mux-1".to_string(),
        provider_run_id: "run-mux-1".to_string(),
        bcs_session_id: "group-1:eeeeeeee".to_string(),
        group_id: "group-1".to_string(),
        bot_id: "bot-mux-1".to_string(),
        interaction_id: "int-mux-1".to_string(),
        kind: bcs_service_api::InteractionKind::Exec,
        idempotency_key: "idem-mux-1".to_string(),
        resolution: serde_json::json!({}),
    };

    let ack = mux.resolve_interaction(command).await.unwrap();
    assert!(ack.ok);
    assert_eq!(websocket.calls.lock().unwrap().len(), 1);
}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cargo test --package bcs-provider-http --test provider_transport_contract interaction_provider_mux -- --nocapture`
Expected: 编译失败，`InteractionProviderMux` 不存在。

- [ ] **Step 3: 实现 `InteractionProviderMux`**

在 `crates/adapters/http/bcs-provider-http/src/lib.rs` 中，紧邻现有 `HistoryRequestMux`（`pub struct HistoryRequestMux { ... }` 之后，`impl GroupHistoryBotRequestPort for HistoryRequestMux` 之后），新增：

```rust
pub struct InteractionProviderMux {
    websocket: Arc<dyn InteractionProviderPort>,
    provider: Arc<HttpProviderTransport>,
}

impl InteractionProviderMux {
    pub fn new(
        websocket: Arc<dyn InteractionProviderPort>,
        provider: Arc<HttpProviderTransport>,
    ) -> Self {
        Self { websocket, provider }
    }
}

#[async_trait]
impl InteractionProviderPort for InteractionProviderMux {
    async fn resolve_interaction(
        &self,
        command: InteractionProviderCommand,
    ) -> ServiceResult<InteractionProviderAck> {
        match &command.target {
            BotDeliveryTarget::WebSocket { .. } => self.websocket.resolve_interaction(command).await,
            BotDeliveryTarget::HttpProvider { .. } => self.provider.resolve_interaction(command).await,
        }
    }
}
```

（`BotDeliveryTarget` 只有 `WebSocket`/`HttpProvider` 两个变体，已在设计阶段确认，`match` 无需 `_` 分支。）

- [ ] **Step 4: 运行测试验证通过**

Run: `cargo test --package bcs-provider-http --test provider_transport_contract interaction_provider_mux -- --nocapture`
Expected: PASS。追加一个对称测试验证 `HttpProvider` target 路由到 `provider`（复用同一 mux 实例，换 target 类型，断言走了 HTTP 路径而非新增 websocket mock 调用计数）：

```rust
#[tokio::test]
async fn interaction_provider_mux_routes_http_provider_target_to_http_impl() {
    let websocket = std::sync::Arc::new(RecordingInteractionProvider {
        calls: std::sync::Mutex::new(Vec::new()),
        ack: bcs_service_api::InteractionProviderAck {
            ok: true,
            retryable: None,
            error: None,
        },
    });
    let provider = std::sync::Arc::new(bcs_provider_http::HttpProviderTransport::allowing_private_networks_for_tests());
    let mux = bcs_provider_http::InteractionProviderMux::new(websocket.clone(), provider.clone());

    let command = bcs_service_api::InteractionProviderCommand {
        target: bcs_domain::BotDeliveryTarget::HttpProvider {
            bot_id: "bot-mux-2".to_string(),
            provider_id: "provider-x".to_string(),
            provider_bot_ref: "ref-1".to_string(),
            webhook_url: "http://127.0.0.1:9/webhook".to_string(),
            bcs_to_provider_token: bcs_domain::RedactedToken::from("tok"),
            protocol_version: "2.0".to_string(),
        },
        provider_bypass_headers: Vec::new(),
        bcs_run_id: "run-mux-2".to_string(),
        provider_run_id: "run-mux-2".to_string(),
        bcs_session_id: "group-1:ffffffff".to_string(),
        group_id: "group-1".to_string(),
        bot_id: "bot-mux-2".to_string(),
        interaction_id: "int-mux-2".to_string(),
        kind: bcs_service_api::InteractionKind::Exec,
        idempotency_key: "idem-mux-2".to_string(),
        resolution: serde_json::json!({}),
    };

    // HTTP target 会尝试真实网络请求(webhook_url 指向不可达端口)，预期失败，
    // 但关键断言是 websocket mock 完全没被调用——证明路由没有走错分支。
    let _ = mux.resolve_interaction(command).await;
    assert_eq!(websocket.calls.lock().unwrap().len(), 0);
}
```

Run: `cargo test --package bcs-provider-http --test provider_transport_contract interaction_provider_mux -- --nocapture`
Expected: 两个测试均 PASS。

- [ ] **Step 5: 改造 `create_interaction_service` 签名与调用点**

在 `crates/bootstrap/bcs/src/server.rs` 中，`create_interaction_service` 函数签名新增 `bot_connections` 参数：

```rust
fn create_interaction_service(
    provider_transport: Arc<bcs_provider_http::HttpProviderTransport>,
    bot_connections: Arc<bcs_ws::bot::BotConnectionRegistry>,
    authorization: Arc<dyn CanResolveInteraction>,
    frontend_delivery: Arc<dyn FrontendDeliveryPort>,
    terminal_retention_ms: u64,
) -> Arc<dyn InteractionService> {
    let store = Arc::new(MemoryInteractionStore::new());
    let interaction_frontend = Arc::new(bcs_ws::web::WorkbenchInteractionDelivery::new(
        frontend_delivery,
    ));
    // bot_connections: Arc<BotConnectionRegistry> 直接传给
    // InteractionProviderMux::new 的 websocket: Arc<dyn InteractionProviderPort>
    // 参数——函数参数位置是 Rust unsize coercion 的标准触发点，不需要显式
    // `as Arc<dyn ...>` 或额外包装（BotConnectionRegistry 已在 Task 4 中
    // 直接 impl InteractionProviderPort，不像 GroupHistoryBotRequestPort
    // 场景那样需要 BootstrapGroupHistoryBotRequestPort 适配器）。
    let interaction_provider: Arc<dyn bcs_service_api::InteractionProviderPort> =
        Arc::new(bcs_provider_http::InteractionProviderMux::new(
            bot_connections,
            provider_transport.clone(),
        ));
    let interactions: Arc<dyn InteractionService> = Arc::new(InteractionManagement::new(
        store,
        authorization,
        interaction_provider,
        interaction_frontend,
        terminal_retention_ms,
    ));
    provider_transport.set_interactions(interactions.clone());
    interactions
}
```

三处调用点（`2444`/`4166`/`5056` 行）均新增 `bot_connections.clone()` 作为第二个参数。以第一处（`2444` 行）为例，从：

```rust
let interactions = create_interaction_service(
    provider_transport.clone(),
    group_management_impl.clone(),
    frontend_delivery.clone(),
    config.async_chat_run_retention_ms,
);
```

改为：

```rust
let interactions = create_interaction_service(
    provider_transport.clone(),
    bot_connections.clone(),
    group_management_impl.clone(),
    frontend_delivery.clone(),
    config.async_chat_run_retention_ms,
);
```

`4166` 行、`5056` 行两处调用同样在第一个参数之后插入 `bot_connections.clone(),`（这两处的 `bot_connections` 变量分别在 `3857` 行、`4660` 行已声明，作用域覆盖调用点，无需新增声明）。

- [ ] **Step 6: `bot_ws_dispatch_state()` 新增字段赋值**

在 `crates/bootstrap/bcs/src/server.rs` 的 `bot_ws_dispatch_state` 函数（`5767` 行附近）中，`BotDispatchState { ... }` 构造新增一行：

```rust
fn bot_ws_dispatch_state(state: &Arc<BcsServerState>) -> Arc<bcs_ws::bot::BotDispatchState> {
    Arc::new(bcs_ws::bot::BotDispatchState {
        bot_runtime: state.services.bot_runtime.clone(),
        message_flow: state.services.message_flow.clone(),
        collaboration_runtime: state.services.collaboration_runtime.clone(),
        bot_run_context: state.services.bot_run_context.clone(),
        bot_connections: state.bot_connections.clone(),
        run_channels: state.run_channels.clone(),
        task_callback: None,
        session_management: state.services.session_management.clone(),
        group_dispatch: Arc::new(CoreGroupDispatchContext {
            group: state.services.group.clone(),
        }),
        callback_dispatch: Arc::new(bcs_callback::SessionCallbackDispatcher::new(
            state.services.group.clone(),
            state.outbound_url_guard.clone(),
        )),
        system_message: Some(state.services.system_message.clone()),
        coordination_processed: state.coordination_processed.clone(),
        agent_credential_backfill: Some(Arc::new(AgentCredentialBackfill {
            registry: state.services.registry.clone(),
        })),
        interactions: state.services.interactions.clone(),
    })
}
```

- [ ] **Step 7: 编译整个 workspace 验证接线无误**

Run: `cargo build --package bcs --package bcs-ws --package bcs-provider-http`
Expected: 编译成功，无未使用参数/字段警告。若 `create_interaction_service` 的 3 处调用点有遗漏，编译器会在缺参数的调用处报错，逐一修正。

- [ ] **Step 8: 运行受影响 crate 的完整测试套件**

Run: `cargo test --package bcs-ws --package bcs-provider-http --package bcs-service-api`
Expected: 全部 PASS（Task 1-5 新增和既有测试）。

- [ ] **Step 9: Commit**

```bash
git add crates/adapters/http/bcs-provider-http/src/lib.rs crates/adapters/http/bcs-provider-http/tests/provider_transport_contract.rs crates/bootstrap/bcs/src/server.rs
git commit -m "feat(bcs): route interaction.resolve through InteractionProviderMux

新增 InteractionProviderMux(仿 HistoryRequestMux 先例),按
BotDeliveryTarget 分流 WS/HTTP 下行实现。create_interaction_service
新增 bot_connections 参数,函数体内部构造 mux,保留
provider_transport.set_interactions(...) 对具体类型的依赖。
3 处调用点与 bot_ws_dispatch_state() 同步接线。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: 回归验证 — WS provider_target 端到端 + 全量测试

**Files:**
- Modify: `crates/adapters/ws/bcs-ws/tests/interaction_end_to_end.rs`(新增 WS target 版本的 `requested()` 变体 + 回归测试)
- 无新增生产代码文件

**Interfaces:**
- Consumes: 全部前序任务产出，本任务不新增接口

**范围纠正说明**：设计文档"测试计划"一节曾提到"`conformance_interaction.rs` 补充 WS 端口的契约测试"。核实后发现该文件属于 `bcs-interaction` crate 的集成测试，使用的是本地定义的 `AcceptingProvider` mock，且 `bcs-interaction` 不依赖也不应该依赖 `bcs-ws`（分层规则），因此**无法**在该文件里引用 `BotConnectionRegistry` 做契约测试。真正验证 `BotConnectionRegistry` 遵守 `InteractionProviderPort` 契约的测试就是 Task 4 已经写在 `bcs-ws` 自己的 `bot_delivery_port.rs` 里的 5 个用例——不需要额外的"共享契约测试"。本任务改为对 `interaction_end_to_end.rs` 做一次针对性回归补充（验证人侧 resolve 链路对 `provider_target` 类型无感知，这是正确的架构性质：`InteractionManagement` 不应该也不会根据 target 类型改变行为）+ 跑一遍全量相关测试收尾。

- [ ] **Step 1: 新增 WS target 版本回归测试**

在 `crates/adapters/ws/bcs-ws/tests/interaction_end_to_end.rs` 中，在现有 `requested()` 函数之后，新增一个变体：

```rust
fn requested_via_websocket(interaction_id: &str) -> ProviderInteractionRequestedCommand {
    ProviderInteractionRequestedCommand {
        bcs_run_id: "bcs-run-e2e-ws".to_string(),
        provider_run_id: "bcs-run-e2e-ws".to_string(),
        interaction_id: interaction_id.to_string(),
        kind: InteractionKind::Exec,
        bcs_session_id: "session-e2e-ws".to_string(),
        group_id: "group-e2e-ws".to_string(),
        bot_id: "bot-e2e-ws".to_string(),
        run_deadline_ms: u64::MAX,
        provider_target: BotDeliveryTarget::WebSocket {
            bot_id: "bot-e2e-ws".to_string(),
        },
        provider_bypass_headers: Vec::new(),
        payload: json!({
            "runId": "bcs-run-e2e-ws",
            "seq": 1,
            "phase": "requested",
            "interactionId": interaction_id,
            "kind": "exec",
            "command": format!("deploy {interaction_id}"),
            "options": [
                {"decision": "allow_once", "label": "Allow once"},
                {"decision": "deny", "label": "Deny"}
            ]
        }),
        received_at_ms: bcs_protocol::now_ms(),
    }
}

#[tokio::test]
async fn resolve_flow_is_agnostic_to_websocket_provider_target() {
    let connections = Arc::new(WorkbenchConnectionRegistry::new());
    let run_channels = Arc::new(RunChannelManager::new());
    let raw_frontend: Arc<dyn FrontendDeliveryPort> = Arc::new(WorkbenchFrontendDelivery::new(
        connections.clone(),
        run_channels.clone(),
    ));
    let provider = Arc::new(ScriptedProvider {
        responses: Mutex::new(VecDeque::from([InteractionProviderAck {
            ok: true,
            retryable: None,
            error: None,
        }])),
        calls: Mutex::new(Vec::new()),
    });
    let interactions: Arc<dyn InteractionService> = Arc::new(InteractionManagement::new(
        Arc::new(MemoryInteractionStore::new()),
        Arc::new(AllowResolve),
        provider.clone(),
        Arc::new(WorkbenchInteractionDelivery::new(raw_frontend)),
        120_000,
    ));
    let state = Arc::new(WebDispatchState {
        message_flow: Arc::new(NoopMessageFlowService),
        collaboration_runtime: Arc::new(NoopCollaborationRuntimeService),
        workbench_sessions: Arc::new(NoopWorkbenchSessionService),
        interactions: interactions.clone(),
        group_session_connections: None,
        frontend_connections: connections.clone(),
        run_channels,
    });
    let (tx, mut rx) = mpsc::channel(16);
    connections
        .subscribe(
            "session-e2e-ws".to_string(),
            tx.clone(),
            Some("human-e2e-ws".to_string()),
            None,
        )
        .await
        .unwrap();

    interactions
        .on_provider_requested(requested_via_websocket("ws-first"))
        .await
        .unwrap();
    let event = receive_json(&mut rx).await;
    assert_eq!(event["payload"]["interactionId"], "ws-first");

    let auth = WorkbenchConnectionAuth::UserBound {
        actor_id: Some("human-e2e-ws".to_string()),
    };
    let mut connection_state = WebClientConnectionState::default();
    let frame = BcsFrame::Request(RequestFrame::new(
        "resolve-ws-first",
        "interaction.resolve",
        Some(json!({
            "bcsRunId": "bcs-run-e2e-ws",
            "interactionId": "ws-first",
            "idempotencyKey": "idem-ws-first",
            "decision": "allow_once"
        })),
    ));
    let BcsFrame::Request(req) = frame else { unreachable!() };
    dispatch_client_frame(&state, &req, &tx, &mut connection_state, &auth)
        .await
        .unwrap();
    let response = receive_json(&mut rx).await;
    assert_eq!(response["ok"], true);

    let calls = provider.calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert!(matches!(
        calls[0].target,
        BotDeliveryTarget::WebSocket { ref bot_id } if bot_id == "bot-e2e-ws"
    ));
}
```

（`dispatch_client_frame` 的精确参数顺序与调用方式请对照本文件中现有 `requested_events_resolve_in_reverse_order_retry_and_continue_on_same_run` 测试里 `interaction.resolve` 帧的分发逻辑——同一文件内其余部分不变，此处只新增函数与测试，不修改现有测试。）

- [ ] **Step 2: 运行新增测试验证通过**

Run: `cargo test --package bcs-ws --test interaction_end_to_end -- --nocapture`
Expected: 新增的 `resolve_flow_is_agnostic_to_websocket_provider_target` 与既有的 `requested_events_resolve_in_reverse_order_retry_and_continue_on_same_run` 均 PASS。

- [ ] **Step 3: Commit 回归测试**

```bash
git add crates/adapters/ws/bcs-ws/tests/interaction_end_to_end.rs
git commit -m "test(bcs-ws): verify resolve flow is agnostic to WebSocket provider_target

补充回归测试,确认 InteractionManagement 的人侧 resolve 链路对
provider_target 类型(WebSocket vs HttpProvider)无感知,这是
InteractionProviderMux 路由设计能够正确工作的前提性质。

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 4: 跑全量相关 crate 测试收尾**

Run: `cargo test --package bcs-ws --package bcs-provider-http --package bcs-service-api --package bcs-interaction`
Expected: 全部 PASS。这一步确认 Task 1-6 引入的所有改动（`dispatcher.rs`/`run_event_v3.rs`/`connection_registry.rs`/`core/interaction.rs`/`bcs-provider-http/lib.rs`/`server.rs`）组合在一起没有相互破坏，也没有破坏 `bcs-interaction` 的既有集成测试（`InteractionKind::as_slug` 的改动在 Task 3 已单独验证，此处是最终交叉确认）。

- [ ] **Step 5: 编译完整 workspace 做最终确认**

Run: `cargo build --workspace 2>&1 | tail -50`
Expected: 编译成功，无 error。若受磁盘空间限制无法跑全量 workspace build（本 worktree 曾记录磁盘紧张问题），改跑：`cargo check --workspace 2>&1 | tail -50`（只做类型检查，不产出完整二进制，磁盘占用小得多）。

- [ ] **Step 6: 最终 Commit（若 Step 5 发现并修复了跨 crate 遗漏）**

若 Step 5 编译时发现任何遗漏调用点或类型不匹配，修复后提交：

```bash
git add -A
git commit -m "fix(bcs): address workspace-wide compile fallout from interaction HITL changes

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

若 Step 5 无需修复，跳过本步骤，整个功能已在 Task 1-6 的提交中完整交付。
