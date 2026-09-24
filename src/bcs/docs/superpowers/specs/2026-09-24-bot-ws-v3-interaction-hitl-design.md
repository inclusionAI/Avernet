# Bot WebSocket V3 支持 Interaction(HITL)设计

## 背景与目标

BCS 的 Bot WebSocket V3 协议(`bbcfe5aea`,unified run events)统一了 bot 上行事件契约，但规范化的 `Interaction` 事件（HITL：human-in-the-loop 审批）目前在这条链路上被显式拒绝，且下行的 `interaction.resolve` 只有 HTTP Provider 一种实现。这意味着通过 WebSocket 接入(plugin 模式)的 bot 无法使用 HITL 审批流程，而通过 HTTP Provider(gateway 模式)接入的 bot 可以。

**目标**：让 WS 接入的 bot 也能完整走 HITL 上行请求(`interaction` phase=requested/resolved)和下行确认(`interaction.resolve`)，功能对齐 HTTP Provider 链路。

**范围**：只做 BCS 服务端改造。bcs-bridge 客户端改造、V3 编码器、端到端验证等后续任务不在本设计内。

**不改动的既有行为**：
- Web 工作台侧 `interaction.resolve`(人→BCS)的处理路径不变。
- HTTP Provider 链路的上行/下行实现(`HttpProviderTransport`)不改。
- run 超时(`run_deadline_ms` 到期)后 BCS **不会**向下游(HTTP Provider 或 WS bot)主动发送任何"已失效"通知——这是当前 HTTP 链路的既有行为，WS 链路照抄，不新增下行通知逻辑。

## 现状分析

### 上行阻塞点

`crates/adapters/ws/bcs-ws/src/bot/run_event_v3.rs` 的 `normalize_v3_event` 对 `StreamEvent::Interaction(_)` 直接返回错误："V3 interaction events are not supported on Bot WebSocket yet"。

调用方 `handle_event_frame`(`dispatcher.rs`)在拿到 `normalize_v3_event` 的返回值(`NormalizedBotEvent` 元组：`run_id, bcs_group_id, event_type, event_payload, event_state, is_final, session_id, seq`)之后，还会做一系列**流式消息事件专属**的处理：`terminal_fingerprint` 计算 + `is_terminal_replay` 幂等去重、`reserve_run_event_seq` 单调序号占用、往 `collaboration_runtime`/`message_flow` 分发消息体。这套逻辑是为 agent/chat 这类"流式消息"设计的，语义上不适配 Interaction 这种"请求-响应式审批"事件——Interaction 有自己的幂等机制(`idempotency_key` + store 的 claim/commit)，不应该被这套逻辑接管。

### 下行阻塞点

`crates/service-api/bcs-service-api/src/port/interaction.rs` 定义的 `InteractionProviderPort::resolve_interaction` 目前唯一实现是 `HttpProviderTransport`(`crates/adapters/http/bcs-provider-http/src/lib.rs:239-257`)，遇到 `BotDeliveryTarget::WebSocket` target 直接返回 `InvalidOperation` 错误。

### 已具备、可复用的基础设施

- **规范事件契约**：`InteractionEvent{run_id, seq, ts, session_id, phase, interaction_id, kind, raw}`(`bcs-protocol::stream::event`)，`phase: Requested|Resolved`，`kind: Exec|AskUser|ModeSwitch`。
- **业务服务**：`InteractionService`(`on_provider_requested`/`on_provider_resolved`/`resolve`/`list_pending`/`invalidate_run`/`cleanup_terminal`)，唯一实现 `InteractionManagement`(`bcs-interaction` crate)，内部持有 `MemoryInteractionStore`，已经实现了完整的 claim/commit 幂等、`run_deadline_ms` 校验、授权检查。这个服务实例是**单例**，两条链路(HTTP/WS)共用同一个 `Arc<dyn InteractionService>`。
- **HTTP 侧上行模板**：`drive_sse_frame`(`bcs-provider-http/src/lib.rs`)展示了完整的 ingest 流程：seq 去重(`SeqDedup`)、deadline 检查+过期时调 `invalidate_run`、`interaction_context` 未接线时安全丢弃。
- **HTTP 侧下行模板**：`HttpProviderTransport::resolve_interaction` 展示了构造下行请求、超时、ack 解析为 `retryable`/`error` 的完整映射逻辑。
- **WS 侧下行请求-响应模板**：`BotConnectionRegistry::abort_inner`(`bcs-ws/src/bot/connection_registry.rs`)展示了"构造 RequestFrame → oneshot 挂起 → 发送到指定连接 → 等待 ResponseFrame → 超时清理 → 解析 ok/error"的完整模式，与 `pending_abort_requests` 挂起表一起工作。这个模式可以原样复用给 `interaction.resolve`。
- **现成的传输路由先例**：`HistoryRequestMux`(`bcs-provider-http/src/lib.rs`)按 `BotDeliveryTarget` 类型分流到 WS 或 HTTP 实现，字段类型是 `Arc<dyn GroupHistoryBotRequestPort>`(trait object，定义在 `bcs-service-api`)，不是具体类型——这避免了 `bcs-provider-http` 与 `bcs-ws` 两个平级 delivery adapter 产生 crate 间依赖。
- **组合根接线点**：`InteractionService` 已经作为 `state.services.interactions` 字段存在（供 `WebDispatchState` 使用），`bot_ws_dispatch_state()` 函数从 `state.services.*` 取值组装 `BotDispatchState`，接入新依赖不需要调整构造顺序。

## 设计

### 1. 上行：新增独立分支，不复用 `NormalizedBotEvent`

在 `handle_event_frame`(`dispatcher.rs`)最前面，事件类型判别之后，Interaction 事件直接分流到新函数 `handle_interaction_event_v3`，完全不进入 `normalize_v3_event`/`NormalizedBotEvent` 管道，不受 `terminal_fingerprint`/`reserve_run_event_seq` 影响。

从现有校验逻辑中提炼共享 helper `validate_v3_run_scope`，覆盖 4 步通用 V3 帐本校验（agent/chat 与 Interaction 共用）：
1. 外层 frame seq 与 payload seq 一致性校验
2. `bot_run_context.get_context(run_id)` 存在性
3. `context.bot_id`/`bcs_session_id` 身份匹配
4. `context.terminal || deadline` 未过期检查

agent/chat 分支调用该 helper 后继续做自己专属的 `terminal_fingerprint` 计算与 `reserve_run_event_seq`；`handle_interaction_event_v3` 调用该 helper 后，直接拿 `BotRunContext` 组装命令调用 `InteractionService`，不进入 seq 计数器，也不计算 terminal_fingerprint（幂等完全交给 `InteractionService`/store 的 `idempotency_key` 机制）。

```rust
async fn validate_v3_run_scope(
    state: &BotDispatchState,
    bot_id: &str,
    run_id: &str,
    session_id: &str,
    seq: u64,
    outer_seq: Option<u64>,
) -> Result<BotRunContext> { /* 见上方 4 步 */ }
```

`handle_interaction_event_v3` 按 `phase` 分派：
- `Requested` → 组装 `ProviderInteractionRequestedCommand{ provider_target: BotDeliveryTarget::WebSocket{bot_id}, run_deadline_ms: context.deadline_ms, ... }` → `InteractionService::on_provider_requested`
- `Resolved` → 组装 `ProviderInteractionResolvedCommand` → `InteractionService::on_provider_resolved`

**已知的设计假设（需明确记录）**：V3 规范 `InteractionEvent` 只有一个 `run_id` 字段，不像 HTTP Provider 场景那样区分"引擎内部 run id"与"BCS run id"两套体系。WS 场景下 `bcs_run_id` 与 `provider_run_id` 填同一个值（`event.run_id`）。`provider_bypass_headers` 填空 `Vec`（WS 无 HTTP header 概念）。

### 2. 下行：`BotConnectionRegistry` 新增 `InteractionProviderPort` 实现

`BotConnectionRegistry`(`bcs-ws/src/bot/connection_registry.rs`)新增字段 `pending_interaction_requests: RwLock<HashMap<String, oneshot::Sender<ResponseFrame>>>`，与现有 `pending_abort_requests` 并列。新增 `impl InteractionProviderPort for BotConnectionRegistry`，完整复刻 `abort_inner` 的模式：

1. 校验 `command.target` 是 `BotDeliveryTarget::WebSocket`，否则返回 `InvalidOperation`。
2. 构造 `params`：复用 web dispatcher `InteractionResolveParams` 与 HTTP `HttpProviderTransport` 已经在用的同一套 camelCase 字段命名——`bcsRunId`/`runId`/`interactionId`/`kind`/`idempotencyKey` + resolution 展开——保证人→BCS / BCS→HTTP Provider / BCS→WS bot 三条链路的 wire 字段名一致。
3. 构造 `RequestFrame::new(request_id, "interaction.resolve", params)`，走 `send_frame_json` 发送。
4. 用 oneshot channel 挂起等待，超时常量 **15,000ms**（对齐 `chat.abort` 量级，而非 HTTP Provider 65s 回调超时量级——WS 是常驻连接，往返延迟应远低于 HTTP 回调场景）。
5. 响应映射：
   - 发送失败 / 连接不存在 → `Err(ServiceError::BotNotConnected)` → `InteractionService` 记为 `RetryableFailure`（复用 `management.rs` 现有的错误处理路径，无需新增分支）。
   - 超时 → `Err(ServiceError::InternalError(...))` → 同上，可重试。
   - `ResponseFrame.ok == false` 且错误码为 unknown-method → `Ok(InteractionProviderAck{ok:false, retryable:Some(false), error:...})`——bot 不支持该方法时**直接判定不可重试**，避免旧版本插件反复被判为"可重试失败"而无限重试，永远拿不到终态。
   - `ResponseFrame.ok == false`，其他业务错误 → `Ok(InteractionProviderAck{ok:false, retryable:Some(true), error:...})`。
   - `ResponseFrame.ok == true` → `Ok(InteractionProviderAck{ok:true, retryable:None, error:None})`。

`resolve_pending_interaction_request(request_id, response)` 方法在 `handle_response_frame`(dispatcher.rs)中接入，与现有 `resolve_pending_abort_request` 路由逻辑对称。

### 3. 下行路由：`InteractionProviderMux`

新增 `InteractionProviderMux`，放在 `bcs-provider-http` crate（与 `HistoryRequestMux` 同一文件，遵循同一先例）：

```rust
pub struct InteractionProviderMux {
    websocket: Arc<dyn InteractionProviderPort>,
    provider: Arc<HttpProviderTransport>,
}

impl InteractionProviderPort for InteractionProviderMux {
    async fn resolve_interaction(&self, command: InteractionProviderCommand) -> ServiceResult<InteractionProviderAck> {
        match &command.target {
            BotDeliveryTarget::WebSocket { .. } => self.websocket.resolve_interaction(command).await,
            BotDeliveryTarget::HttpProvider { .. } => self.provider.resolve_interaction(command).await,
        }
    }
}
```

**依赖方向**：`websocket` 字段类型是 `Arc<dyn InteractionProviderPort>`（trait 定义于 `bcs-service-api`），不是具体的 `BotConnectionRegistry` 类型。`bcs-provider-http` 因此不需要依赖 `bcs-ws`（两者当前互相不依赖，保持平级）。具体类型绑定发生在组合根。

### 4. 组合根接线

`crates/bootstrap/bcs/src/server.rs`：
- `create_interaction_service` 函数内，`provider_transport.clone()` 替换为 `Arc::new(InteractionProviderMux::new(bot_connections.clone(), provider_transport.clone()))`（三处调用点：`server.rs` 中 `create_interaction_service` 目前有 3 个调用位置，均需同步替换，具体行号见"实现要点"）。
- `bot_ws_dispatch_state()` 函数新增一行：`interactions: state.services.interactions.clone()`，写法与旁边现有字段一致。
- `BotDispatchState`(bcs-ws crate)struct 新增字段：`pub interactions: Arc<dyn InteractionService>`。

### 数据流总览

两条链路共用的部分（同一份代码、同一运行实例）：
- 上行契约类型：`InteractionEvent`/`InteractionPhase`/`InteractionKind`(`bcs-protocol::stream`)
- 业务入口与实现：`InteractionService` trait + `InteractionManagement` 实现 + `MemoryInteractionStore`(`bcs-interaction` crate，单例)
- 下行端口契约：`InteractionProviderPort`/`InteractionProviderCommand`/`InteractionProviderAck`(`bcs-service-api::port`)
- wire 字段命名：`bcsRunId`/`runId`/`interactionId`/`kind`/`idempotencyKey`

各链路专有的部分：
- 上行帧解析：HTTP 是 `drive_sse_frame`(SSE)；WS 是新增的 `handle_interaction_event_v3`(EventFrame)+ `validate_v3_run_scope`（后者在 bcs-ws adapter 内部也被 agent/chat 分支共用）
- 下行发送实现：HTTP 是 `HttpProviderTransport`(POST，65s 传输超时)；WS 是新增的 `BotConnectionRegistry` 实现(RequestFrame + oneshot，15s 传输超时)
- 路由层：新增 `InteractionProviderMux`，两条链路的下行调用都经过它分流

### 超时语义（两层，不可混淆）

| 层次 | 含义 | HTTP Provider 链路 | WS Plugin 链路 |
|---|---|---|---|
| run 生命周期截止 | 这次 chat run 还剩多久必须结束；到期后此 run 期间的任何 interaction 一律失效 | `run_deadline_ms`(run 自带，非 interaction 专属) | 同一套字段（`InteractionRecord.run_deadline_ms`，来自 `BotRunContext.deadline_ms`） |
| BCS 等待下行 ack 的传输超时 | BCS 发出 resolve 请求后最多等多久对方确认收到 | 65,000ms(`ProviderClientPolicy::for_request(false).total_timeout`，硬编码，已存在) | **15,000ms**（新定常量，对齐 `chat.abort` 量级：WS 是常驻连接，往返延迟应远低于 HTTP 回调场景） |

**run 截止超时的既有行为（两条链路一致，不新增）**：`InteractionService::invalidate_run` 只做两件事——把 store 记录标记失效、打日志。**不会**调用 `provider.resolve_interaction` 或任何下行发送逻辑向 provider/bot 通知"已失效"。Provider/bot 侧必须依靠自己独立维护的、与 `run_deadline_ms` 对齐的超时机制自行判断，BCS 不会主动推送失效通知。WS 侧照抄这个行为，不新增下行通知路径。

## 错误处理与幂等

- 传输层失败（连接不存在、发送失败、超时）→ `Err(ServiceError::...)`，`InteractionService::resolve` 统一当作 `RetryableFailure` 处理（已有逻辑，WS 侧不新增分支）。
- 业务级拒绝（bot 主动回 `ResponseFrame::err`）→ `Ok(InteractionProviderAck{ok:false, ...})`，`retryable` 根据错误类型判断（unknown-method → false，其他 → true）。
- 幂等完全由 `InteractionService`/`MemoryInteractionStore` 的 `idempotency_key` + claim/commit 机制保证，WS 侧上行/下行改造都不引入第二套幂等逻辑。

## 测试计划

- `crates/adapters/ws/bcs-ws/tests/frame_compat.rs`：新增 V3 interaction 上行用例（合法 requested/resolved、seq 回退、run 已终态后拒绝、run/session 身份不匹配）。
- `crates/adapters/ws/bcs-ws/tests/interaction_end_to_end.rs`：新增 WS `provider_target` 版本的端到端用例（现有测试覆盖的是 web workbench 侧，需要补 WS bot 侧的 requested→resolve→ack 全链路）。
- `crates/services/bcs-interaction/tests/conformance_interaction.rs`：补充 WS 端口的契约测试（复用现有 `InteractionProviderPort` 契约测试模式，验证 `BotConnectionRegistry` 实现符合同一契约）。
- 新增：`InteractionProviderMux` 路由测试——验证 `WebSocket` target 路由到 WS 实现、`HttpProvider` target 路由到 HTTP 实现，两者互不影响。
- 新增：`BotConnectionRegistry::resolve_interaction` 单元测试——覆盖超时、unknown-method、业务拒绝、正常 ack 四种路径。

## 实现要点（供实现阶段核对）

- `create_interaction_service` 在 `server.rs` 中有 **3 处调用**（`2444`/`4166`/`5056` 行，对应不同的服务器构造路径），均需同步替换 provider 参数为 `InteractionProviderMux`。
- `BotDispatchState` struct 新增 `interactions` 字段后，**3 处构造点**需同步补齐：生产路径 `server.rs:5767`（`bot_ws_dispatch_state()`），测试路径 `bcs-ws/tests/token_expiry_e2e.rs:286` 与 `bcs-ws/tests/frame_compat.rs:654`。
- `is_unknown_method_code` 辅助函数当前在 `connection_registry.rs` 内是私有的（`abort_inner` 使用），确认其可见性足够给新的 `resolve_interaction` 实现复用，或按需调整。
