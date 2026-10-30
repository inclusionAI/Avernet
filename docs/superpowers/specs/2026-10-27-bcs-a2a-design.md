# BCS / bcs-bridge 支持 A2A 协议 — 设计方案

> 状态：设计草案（待评审） · 日期：2026-10-27 · 作者：元歌
> 代码归属：Avernet（公共，`apps/bcs`）；OCB 仅升级子模块指针并做内部装配验证
> 参考：A2A 规范 v1.0.0（`a2aproject/A2A` `docs/specification.md`、`specification/a2a.proto`）、`a2a-samples`、Avernet Python 引擎 A2A 实现（`engine/adapter/src/engine/community/api/transport/a2a/`）

## 1. 背景与目标

BCS 现在向 Bot 下行投递有两种连接模式：

- **gateway**：BCS 通过 HTTP 调 Provider 的 `/webhook`，响应为 BCN 私有 SSE 格式（`StreamEvent`：chat / agent / interaction）。
- **plugin**：Bot（如 bcs-bridge）主动连 BCS `/ws/bot`，在 WS 上收发 BCN 帧。

两种都是 BCN 私有协议，第三方 Agent 无法直接接入，bcs-bridge 托管的引擎也无法被其他 A2A 生态调用。

**目标**

1. **bcs-bridge 作为 A2A Server**：把其托管的本地引擎（codex、claude-code 等）以标准 A2A v1.0 Agent 暴露。
2. **BCS 下行增加 A2A 协议调用**：BCS 作为标准 A2A Client，既能调 bcs-bridge，也能直接接入任意第三方 A2A Agent（LangGraph、ADK 等）。
3. A2A Server 提供三种通信方式：**流式输出、Task 结果轮询、Task 结果回调**，并支持**断点续传**。

**非目标（本期不做）**

- gRPC、HTTP+JSON（REST）binding（以后在 AgentCard 的 `supportedInterfaces` 追加即可，不影响已有 Client）
- `ListTasks`、`GetExtendedAgentCard`
- 交互式 `AUTH_REQUIRED` 流程
- BCS 群聊 UI 上“旁白 / 结论”分开展示（独立需求）
- bcs-bridge 作为 A2A Client

## 2. 关键结论（评审讨论记录）

### 2.1 传输：JSON-RPC over HTTP + SSE

A2A v1.0 定义三种 binding，对象模型一致，仅传输层不同：

| Binding | 传输 | 编码 | 调用方式 | 流式 |
| --- | --- | --- | --- | --- |
| **JSONRPC** | HTTP/1.1 或 HTTP/2 | JSON | 所有方法 `POST` 到同一 URL，按 body `method` 区分 | SSE（`text/event-stream`） |
| HTTP+JSON（REST） | HTTP | JSON | 每个方法独立路径，如 `POST /message:send` | SSE |
| GRPC | HTTP/2 | Protobuf | `a2a.proto` 生成 stub | gRPC server-streaming |

- **JSON-RPC 是普通 HTTP POST + JSON 信封，与 gRPC 无关**，可直接穿过现有代理 / 网关 / MOSN，curl 可调试。
- 裸 HTTP chunked / NDJSON 流**不是** A2A 标准 binding，第三方 Client 不认识，不采用。
- gRPC 需要 tonic/prost 和端到端 HTTP/2；bridge `/webhook`、BCS provider SSE、Python 引擎 A2A 均为 HTTP/1.1 + SSE，本期收益低。
- **本期：JSONRPC + SSE**。

### 2.2 报文格式：线上全部用 A2A v1.0 标准格式

`protocol=a2a` 时，BCS 与 Agent 之间不再使用 BCN `StreamEvent` 格式；BCN 格式只保留在两端内部，各自做一次转换：

```
bridge 引擎 ──StreamEvent──▶ [A2A 编码器] ══ A2A 标准报文 ══▶ [BCS a2a transport] ──BotEventCommand──▶ message-flow
                 (bridge 内部)              (JSON-RPC + SSE)                         (BCS 内部，下游不改)
```

请求示例（请求头 `A2A-Version: 1.0`）：

```json
{"jsonrpc":"2.0","id":"1","method":"SendStreamingMessage","params":{
  "message":{"messageId":"<BCS run_id>","role":"ROLE_USER","contextId":"<会话>",
    "parts":[{"text":"帮我看下这个报错"},
             {"url":"https://.../log.txt","filename":"log.txt","mediaType":"text/plain"}],
    "metadata":{}}}}
```

流式响应（SSE，每条 `data` 是 JSON-RPC 响应，`result` 为 `StreamResponse`，四选一：`task` / `message` / `statusUpdate` / `artifactUpdate`）：

```
id: 1
data: {"jsonrpc":"2.0","id":"1","result":{"task":{"id":"t-1","contextId":"c-1","status":{"state":"TASK_STATE_SUBMITTED"}}}}

id: 2
data: {"jsonrpc":"2.0","id":"1","result":{"artifactUpdate":{"taskId":"t-1","contextId":"c-1","artifact":{"artifactId":"response","parts":[{"text":"好的，"}]},"append":true}}}

id: 9
data: {"jsonrpc":"2.0","id":"1","result":{"statusUpdate":{"taskId":"t-1","contextId":"c-1","status":{"state":"TASK_STATE_COMPLETED"}}}}
```

push 回调 body 是同一个 `StreamResponse`（不带 JSON-RPC 信封）；`GetTask` 返回完整 `Task`。

v1 注意点：枚举一律大写前缀（`ROLE_USER`、`TASK_STATE_COMPLETED`）；Part 不再有 `kind`，按出现的成员 `text` / `url` / `raw` / `data` 判别。很多旧样例仍是 0.3 格式，实现时用官方 SDK 做互通测试防止写错。

### 2.3 Task 结果与流式消息、工具调用的关系

Task 是“账本”，流式事件是“账本的每一笔变更”：

| 流式事件 | 对 Task 的作用 |
| --- | --- |
| `task` | 初始快照 |
| `artifactUpdate`（`append:true`） | 往 `artifacts[response]` 追加文本 |
| `artifactUpdate`（`append:false`） | 整体替换该 artifact |
| `statusUpdate` | **覆盖** `status`（旧的 `status.message` 归档进 `history`） |

**Task 结果 = 结论**。按 A2A §3.7：结果用 Artifact 交付；进度用 status message。

以 codex 为例（已核实）：一个 turn 里有多条 `agentMessage`，带 `phase` 字段——`commentary`（过程旁白，如“我先看下这个 PR…”）和 `final_answer`（结论）。本机 codex 会话日志统计：commentary 52 条 / final_answer 22 条。bridge `codex_app_server.rs` 的 `completed_text()` 已按 `final_answer` 取结论，BCS `chat_final` 也是“权威全文覆盖 delta”语义；Claude Code 同理（最终 `result` 才是结论，但无 phase 字段）。

| 引擎内容 | A2A 位置 | 进入 Task 结果？ |
| --- | --- | --- |
| `final_answer` 结论 | `artifacts[response]` | ✅ 这就是 Task 结果 |
| `commentary` 旁白 | `statusUpdate(WORKING).status.message` | ❌ 被覆盖后归档到 `Task.history` |
| 工具调用 / thinking | `statusUpdate(WORKING).metadata`（扩展） | ❌ 只在事件流里 |

最终 Task 形如：

```json
{
  "id": "t-1",
  "status": { "state": "TASK_STATE_COMPLETED" },
  "artifacts": [ { "artifactId": "response",
                   "parts": [{ "text": "问题在于 X，已修复并补了测试。" }] } ],
  "history": [
    { "role": "ROLE_USER",  "parts": [{ "text": "帮我修这个 bug" }] },
    { "role": "ROLE_AGENT", "parts": [{ "text": "我先看下这个 PR…" }] },
    { "role": "ROLE_AGENT", "parts": [{ "text": "定位到了，接下来改测试…" }] }
  ]
}
```

流式阶段两种策略：

- **能区分阶段的引擎（codex，有 `phase` + `itemId`）**：commentary delta → `statusUpdate` + `metadata.kind="commentary.delta"`（扩展，逐字旁白）；commentary item 完成 → 一条标准 `status.message`；final_answer delta → `artifactUpdate(append:true)`；turn 结束 → `artifactUpdate(append:false, lastChunk:true, 权威全文)` 兜底。
- **事先无法区分的引擎（Claude Code、旧版 codex）**：文本 delta 先流到 `artifacts[response]`，结束时用权威 `final_text` 整体替换（`append:false`），冲掉混入的旁白。

### 2.4 三种通信方式

三种方式不是三套实现：bridge 内每个 Task 只有一条按 `seq` 编号的事件序列，同时维护 Task 快照，三种方式是对同一份数据的三种读法，结果必然一致。

```
                         ┌─▶ 流式：SSE 实时推送每个事件（id = seq）
引擎事件 ─▶ Task 事件序列 ─┼─▶ 轮询：GetTask 返回当前 Task 快照
         (seq 1,2,3…)     └─▶ 回调：逐个事件 POST 到 Client 注册的 webhook
```

| | 流式 | 轮询 | 回调 |
| --- | --- | --- | --- |
| A2A 机制 | `SendStreamingMessage` / `SubscribeToTask` | `SendMessage` + `returnImmediately:true` → `GetTask` | Push Notification（`taskPushNotificationConfig`） |
| 连接 | 一条长连接 | 多次短请求 | 反向短请求 |
| 实时性 | 最好，逐 token | 取决于轮询间隔 | 好，逐事件 |
| 拿到的内容 | 增量事件 | 完整快照 | 增量事件 |
| 最终 Task 结果 | ✅ 结束前补发完整 `task` 快照 | ✅ | ✅ 终态推完整快照 |
| 工具调用过程 | ✅ | ❌（仅 history 中的旁白） | ✅ |
| BCS 中定位 | 默认 | 兜底 + 不支持流式的 Agent | 可选，按 Provider 配置 |

补充：

- 任何方式结束后均可 `GetTask` 取结果（bridge 落盘，TTL 可配）。
- 不带 `returnImmediately` 的 `SendMessage` 阻塞到终态或 `INPUT_REQUIRED`，即同步调用。
- 第三方简单 Agent 可能直接返回 `Message` 而不建 Task，BCS 视为已完成回复。
- HITL 在三种方式下都体现为 `TASK_STATE_INPUT_REQUIRED`；流式在此状态按规范关流，续答后 `SubscribeToTask` 重新挂上。

流式：

```
BCS                                       bridge
 │ POST /a2a/{bot}  SendStreamingMessage   │
 │────────────────────────────────────────▶│ 创建 Task，启动引擎
 │◀── id:1 task{SUBMITTED}                 │
 │◀── id:2 statusUpdate{WORKING}           │
 │◀── id:3 artifactUpdate{"好的，"}         │
 │◀── id:9 statusUpdate{COMPLETED}         │
 │        （连接关闭 = 流结束）               │
```

轮询：

```
BCS                                       bridge
 │ SendMessage {returnImmediately:true}    │
 │────────────────────────────────────────▶│ 创建 Task，后台运行
 │◀── {task:{id:t-1, state:WORKING}}       │
 │ GetTask {id:t-1}                        │
 │◀── {state:WORKING, artifacts:[部分文本]} │
 │ GetTask {id:t-1}                        │
 │◀── {state:COMPLETED, artifacts:[全文]}   │
```

回调：

```
BCS                                       bridge
 │ SendMessage {returnImmediately:true,    │
 │   taskPushNotificationConfig:{          │
 │     url:"https://bcs/a2a/push/run-1",   │
 │     authentication:{Bearer <一次性token>}}}│
 │────────────────────────────────────────▶│
 │◀── {task:{id:t-1, state:WORKING}}       │
 │◀═══ POST /a2a/push/run-1  statusUpdate  │ bridge 主动推
 │══ 200 ═════════════════════════════════▶│
 │◀═══ POST …  statusUpdate{COMPLETED}     │
```

### 2.5 断点续传

A2A v1 本身只保证 `SubscribeToTask` 可重新挂上未结束的 Task，并明确断线期间消息可能丢失（§3.7）。本方案分两层：

**第一层：精确续传（BCS 使用，声明扩展 `https://avernet.local/a2a/ext/replay/v1`）**

```
BCS                                       bridge
 │◀── id:4 artifactUpdate                  │
 │      ✗ 网络断开（已收到 seq=4）            │ 引擎继续运行，事件继续写缓冲 5,6,7…
 │ SubscribeToTask {id:t-1}                │
 │ Header: Last-Event-ID: 4                │
 │────────────────────────────────────────▶│ 从缓冲回放 seq>4，再无缝接实时
 │◀── id:5 … id:6 … id:7 …                 │
 │◀── id:9 statusUpdate{COMPLETED}         │
```

SSE 每条带 `id: <seq>`，`metadata.seq` 同步写入（与 Python 引擎约定一致）。不重不漏；Task 生命周期与连接无关。

**第二层：标准兜底（第三方 Client）**：无 `Last-Event-ID` 时，先发完整 `task` 快照（当前状态 + 累积 artifacts），再接实时事件。中间增量可能丢，最终结果不丢；再不行 `GetTask`。

**BCS 恢复策略**

```
流断开
 └─▶ Last-Event-ID 续传（最多 3 次，带退避）
       ├─ 成功 → 继续
       └─ 失败（如 bridge 重启后 Task 不在内存）
            └─▶ GetTask 轮询
                  ├─ 已结束 → 用快照补齐结果
                  └─ 查不到 / 未知 → run 标记 FAILED，不重发请求
```

**边界**

- 回放缓冲在 bridge 进程内存（沿用 `run_buffer` 32MB / 16k 事件上限）；续传点落入已回收区间时退回第二层。
- bridge 进程重启：运行中 Task 无法续传（引擎子进程随之退出），标记 `FAILED(bridge_restarted)`；已结束 Task 落盘仍可 `GetTask`。
- **未知结果绝不自动重发**（避免引擎重复执行，如改两次代码），与 bridge 现有 “unknown outcome 不重放” 原则一致。

## 3. 详细设计

### 3.1 总体架构与代码归属

```
                         BCS Server (A2A Client)                                bcs-bridge (A2A Server)
┌──────────────────────────────────────────────────────┐            ┌──────────────────────────────────────────────┐
│ message-flow ──BotDeliveryPort──▶ BotTransportMux     │            │  GET  /a2a/{bot}/.well-known/agent-card.json │
│                                   ├─ WS (plugin)      │            │  POST /a2a/{bot}   JSON-RPC (+SSE)           │
│                                   ├─ HttpProvider     │            │        │                                     │
│                                   └─ A2aProvider (新) ─┼─JSON-RPC──▶│  A2A Server 层 ──▶ runtime::dispatch (现有)  │
│                                        │  ▲           │  +SSE      │        ▲                  │                  │
│   ProviderEventIngest (现有) ◀─A2A→BCN──┘  │           │            │  TaskProjection ◀─ RunRegistry/seq (现有)    │
│                                           │           │            │        │                  ▼                  │
│   POST /a2a/push/{run_id} (新) ────────────┘ ◀─────────┼─push POST──│  PushDispatcher (新)    Engine (现有, 不改)   │
└──────────────────────────────────────────────────────┘            └──────────────────────────────────────────────┘
```

| 组件 | 位置（Avernet `apps/bcs/crates/`） | 变更 |
| --- | --- | --- |
| A2A v1 报文类型 | `contracts/bcs-a2a-protocol`（新 crate） | 新增 |
| bridge A2A Server | `tools/bcs-bridge/core/src/a2a/` | 新增 |
| bridge 配置 / 注册 | `tools/bcs-bridge/core/src/config.rs`、`app/src/registration/` | 修改 |
| codex phase 透出 | `tools/bcs-bridge/core/src/engine/codex_app_server.rs` | 小改 |
| BCS 下行 A2A 传输 | `adapters/http/bcs-provider-a2a`（新 crate） | 新增 |
| 投递目标 / Provider 配置 | `contracts/bcs-domain/src/provider.rs`、`services/bcs-bot/src/core/provider_core.rs` | 修改 |
| push 回调入口 | `adapters/http/bcs-http` | 新增路由 |
| run ↔ A2A task 映射存储 | 新 port + local / mysql / Noop / Mock 实现 | 新增（Rule 20/21/25） |

- A2A 类型独立成 contract crate：`bcs-protocol` 中已有 `a2a.rs`（BCS 内部 bot-to-bot 对话，非 Google A2A），避免命名混淆；bridge 与 Server 共用同一份报文定义。
- 实现方式：报文类型自研（serde，约 400 行），**生产代码不依赖 pre-1.0 的官方 SDK**；官方 Rust SDK（`a2a-lf` / `a2a-client-lf` / `a2a-server-lf`，`a2aproject/a2a-rs`）仅作 dev-dependency 用于双向互通测试。
- OCB 内部 bcs-bridge 经 `BridgeAppBuilder` 装配，自动获得能力；OCB 侧仅升级 `submodules/avernet` 指针并验证内部装配。

### 3.2 BCS 接入方式：A2A 作为 gateway 模式下的下行协议

**不新增连接模式**（`ProviderBotConnectionMode` 仍为 gateway / plugin），在 Provider `downlink` 配置中增加 `protocol`：

```json
{"downlink": {
   "enabled": true,
   "protocol": "a2a",
   "webhook_url": "https://host/a2a/bot-x",
   "auth_mode": "static_bearer",
   "a2a": { "delivery": "stream" }
}}
```

- `protocol` 缺省 `"bcn"`，现有行为不变；`delivery`：`stream | poll | push`，缺省 `stream`。
- A2A 模式下 `webhook_url` 指 Agent base URL（从此发现 AgentCard）。
- 理由：gateway 语义本就是“BCS 通过 HTTP 主动调 Bot”；注册流程、provider_binding、token、管理 API 全部复用；`ProviderBotConnectionMode` 在 12 个文件有匹配分支，加枚举值波及面大。
- **接第三方 Agent**：管理员通过现有 Provider 管理 API 建 `protocol:"a2a"` 的 Provider + Bot 绑定，`webhook_url` 填对方地址，对方无需实现 BCS 注册。
- 运行时：`BotDeliveryTarget` 新增 `A2aProvider { bot_id, provider_id, base_url, token, delivery }`，`BotTransportMux` 路由到新的 `A2aProviderTransport`（实现现有 `BotDeliveryPort`）。

### 3.3 报文映射

**下行（BCS → Agent）**

| BCS | A2A |
| --- | --- |
| `chat.send` | stream：`SendStreamingMessage`；poll：`SendMessage` + `returnImmediately`；push：再加 `taskPushNotificationConfig`。`messageId = run_id`，兼作幂等键 |
| `chat.abort` | `CancelTask` |
| `interaction.resolve` | 对同一 `taskId` 发 `SendMessage`，`data` part 放决议，`metadata.interactionId` |
| `chat.inject` | `SendMessage` + `A2A-Extensions` 声明 inject 扩展；对方卡片未声明则明确失败 |
| `bot.ping` | 拉取 AgentCard |
| 附件 | `url` part（含 `filename` / `mediaType`） |

**上行（Agent → BCS），在 A2A transport 中转为现有 `BotEventCommand`，下游 message-flow / 持久化 / 前端不改**

| A2A | BCS 内部事件 |
| --- | --- |
| `artifactUpdate` `append:true` | chat delta |
| `artifactUpdate` `append:false` / 终态 `task` 快照 | chat final（权威全文） |
| `statusUpdate` WORKING + `status.message` | 进度旁白（agent 事件） |
| `statusUpdate` WORKING + `metadata.kind = tool / thinking / commentary.delta` | 对应 agent 事件（扩展） |
| `statusUpdate` INPUT_REQUIRED | interaction Requested（走现有 `InteractionService`） |
| COMPLETED / FAILED / CANCELED / REJECTED | final / error / aborted / error |
| AUTH_REQUIRED | error（`auth_required`），本期不做交互式授权 |
| 直接返回 `Message` | 视为 final |

**ID 映射**：taskId 由 Server 生成。BCS 收到首个 `task` 事件后持久化 `run_id → (task_id, context_id, last_seq, base_url)`；并按 `(bot_uuid, session_key)` 持久化对方返回的 `context_id`，后续同会话复用以保持多轮上下文。存储在新 `ProviderTaskBindingRepo` port，BCS 重启后 cancel / 续答 / 轮询仍可找到 task。

**扩展 URI**：沿用 Python 引擎定义的 `https://avernet.local/a2a/ext/{inject,replay,cancel}/v1`，新增 `.../progress/v1` 声明 `metadata.kind` 进度事件词表；`metadata` 字段约定与 Python `wire.py` 一致（`seq`、`interactionId`、`engine_frame`、`kind`）。

### 3.4 bcs-bridge A2A Server

**端点**（与现有 `/webhook` 共用 HTTP listener；一个 bridge 可托管多个 Bot，按 Bot 分路径）

- `GET /a2a/{bot_ref}/.well-known/agent-card.json`
- `POST /a2a/{bot_ref}`（JSON-RPC）
- 只托管一个 Bot 时额外挂根路径 `/.well-known/agent-card.json`

**方法**

- 支持：`SendMessage`、`SendStreamingMessage`、`GetTask`、`CancelTask`、`SubscribeToTask`、4 个 PushNotificationConfig 方法
- 不支持：`ListTasks`、`GetExtendedAgentCard` → `UnsupportedOperationError`
- 校验 `A2A-Version: 1.0`，不支持返回 `VersionNotSupportedError`

**AgentCard**

- `name` / `description`：bridge 配置新增可选项，缺省 bot_ref + 引擎名
- `skills`：配置项，缺省一个 `conversation`
- `capabilities`：`{streaming:true, pushNotifications:true, extensions:[...]}`
- `supportedInterfaces`：`[{url, protocolBinding:"JSONRPC", protocolVersion:"1.0"}]`
- `securitySchemes`：bearer

**消息路由**（全部复用 `runtime::dispatch`，引擎不改）

- 无 taskId 的新消息 → `chat.send`；引擎会话由 `contextId → session_id` 决定
- 带 taskId：
  - task 处于 INPUT_REQUIRED → `interaction.resolve`；`metadata.interactionId` 指定交互；未带且只有一个待答交互时，文本回答绑定到它（方便第三方 Client 直接回复）
  - task 已结束 → `UnsupportedOperationError`

**TaskProjection（新）**：订阅 `RunRegistry` 的 seq 事件流，维护 Task 快照（status、`artifacts[response]`、history）；三种方式共读：

- 流式：SSE 推送，`id: seq`，支持 `Last-Event-ID` 续传
- 轮询：`GetTask` 返回快照
- 回调：`PushDispatcher` 逐事件 POST，终态再推完整快照

**持久化**：终态 Task 快照写入 bridge 现有 session SQLite（新增表），TTL 默认 24h 可配；bridge 重启时运行中 Task 标记 `FAILED(bridge_restarted)`。

**PushDispatcher**：至少一次投递；超时 15s；指数退避，最多 8 次；同一 task 内按 seq 严格保序；URL 白名单默认只含注册时的 BCS 地址，可配置追加（防 SSRF；singlebox / 内网场景不能简单禁止私网地址）。

**codex 小改**：内部 `ChatEvent` 增加可选字段 `segment {item_id, phase}`；为空时不序列化，现有 SSE / plugin 路径不受影响；A2A 编码器据此分流 commentary / final_answer。

**bridge 配置**

```toml
[[bot]]
# ... 现有字段
[bot.a2a]
enabled = true
name = "..."
description = "..."
skills = [{ id = "coding", name = "Coding", description = "...", tags = ["code"] }]
```

注册时上报 `protocol = "a2a"`，`webhook_url` 填 `/a2a/{bot_ref}` 对应地址。

### 3.5 BCS A2A transport 行为

- **卡片缓存**：首次投递 / ping 拉取 AgentCard，缓存 5 分钟；从 `supportedInterfaces` 选 JSONRPC；按 `capabilities` 降级——配置 stream 但 `streaming:false` → poll；配置 push 但 `pushNotifications:false` → poll。
- **stream**：断流 → `SubscribeToTask` + `Last-Event-ID` 续传（最多 3 次，带退避）→ 失败转 `GetTask` 兜底 → 查不到 / 未知则 run FAILED，**绝不重发**。
- **poll**：间隔 1s 起，×1.5 递增，上限 10s，直到终态或 INPUT_REQUIRED；受 run deadline 约束。
- **push**：每个 run 签发一次性 token（只存哈希）；`POST /a2a/push/{run_id}` 校验 token → 按 `taskId + seq` 去重 → 进入现有 ingest；另有低频 `GetTask` 看门狗（60s）防回调全丢导致 run 悬挂。
- **单通道约束**：复用现有 `ProviderRunTransport` 状态机（Negotiating / Sse / Callback / Terminal），poll 归入 Callback 类。
- **多实例（主要风险）**：push 可能打到非发起投递的 BCS 实例。与现有 `/bot/events` callback 的处理方式对齐，plan 阶段先核实现有 callback 的跨实例机制再照搬。

### 3.6 错误处理与安全

**错误映射**

| 情况 | 处理 |
| --- | --- |
| JSON-RPC 标准错误 -32001 ~ -32009 | 映射为 BCS `chat_error` 错误码，如 `a2a_task_not_found` |
| HTTP 401 / 403 | 不可重试 |
| HTTP 5xx / 网络错误 | 未拿到 taskId 前可重试（messageId 幂等，对端去重）；拿到 taskId 后只能续传 / 轮询，不重发 |
| 对端非法报文 | `InvalidAgentResponse`，run FAILED，原始帧落日志 |

**入站鉴权（bridge）**：BCS 用现有 `bcs_to_provider_token`；第三方 Client 用 bridge 配置的额外静态 token 列表；每个 task 记录创建者身份，`GetTask` / `CancelTask` / `SubscribeToTask` 仅创建者可调（规范 §13）。

**push 安全**：bridge 侧 URL 白名单；BCS 侧每 run 一次性 token（只存哈希），payload 的 taskId 必须与该 run 绑定一致。

### 3.7 测试

| 层 | 内容 |
| --- | --- |
| 报文 | `bcs-a2a-protocol` golden JSON 往返：v1 大写枚举、Part 成员判别，防误用 0.3 格式 |
| bridge 单元 | TaskProjection 折叠（codex phase 分流 / 整体替换）、seq 续传、交互续答、PushDispatcher 重试与保序 |
| bridge 互通 | mock 引擎起 bridge，官方 `a2a-client-lf` 当第三方 Client：stream / poll / push / Subscribe + Last-Event-ID |
| BCS 单元 | A2A → BotEventCommand 映射；降级与兜底状态机 |
| BCS 互通 | 官方 `a2a-server-lf` 起第三方 Agent：投递、HITL 续答、取消 |
| 端到端 | `bootstrap/bcs/tests` 新增 BCS ⇄ A2A ⇄ bridge(mock 引擎)：三种方式各一、断流续传、bridge 重启兜底 |
| 实机 | singlebox 下真实 codex 群聊：只显示结论、过程正常展示、HITL 可续答（离线测试不能替代） |

命令（`apps/bcs`）：`cargo test -p bcs-bridge-core`、`-p bcs-bridge-app`、`-p bcs-provider-a2a`、`-p bcs-http`、`--package bcs`；不执行 `cargo fmt`。

## 4. 风险与待定

| 项 | 说明 | 处理 |
| --- | --- | --- |
| push 跨实例 | 回调可能落到其他 BCS 实例 | plan 阶段核实 `/bot/events` callback 机制后对齐 |
| bridge 重启 | 运行中 Task 无法续传 | 标记 `FAILED(bridge_restarted)`，终态快照落盘可查 |
| 回放缓冲上限 | 超长任务早期事件被回收 | 退回快照兜底，结论不丢 |
| 第三方 Agent 差异 | 不建 Task、不发扩展事件、0.3 版本 | 兼容 `Message` 回复；无扩展时降级为文本 + 结论；强制 `A2A-Version: 1.0` |
| 官方 SDK 处于 pre-1.0 | API 可能变动 | 仅用于测试，不进生产依赖 |

## 5. 后续步骤

1. 评审本方案 → 落地为 Avernet spec：`docs/superpowers/specs/2026-10-27-bcs-a2a-design.md`
2. 编写实现计划（分期：报文 crate → bridge A2A Server → BCS a2a transport → 端到端 / 互通测试）
3. Avernet 从最新 `dev` 拉分支实现、PR；OCB 升级子模块指针并验证内部装配

## 附：代码依据

- 投递模式：`contracts/bcs-domain/src/provider.rs`（`ProviderBotConnectionMode`、`BotDeliveryTarget`）
- 下行 HTTP / SSE：`adapters/http/bcs-provider-http/src/lib.rs`（`HttpProviderTransport`、`BotTransportMux`、`stream_and_drive`、`ingest`）
- Provider downlink 配置：`services/bcs-bot/src/core/provider_core.rs`（`parse_downlink_config`）
- 交互：`services/bcs-interaction`、bridge `core/src/interaction.rs`
- bridge 引擎抽象：`tools/bcs-bridge/core/src/engine/mod.rs`（`Engine::run_turn`）；分发 `core/src/runtime.rs`；回放 `core/src/run.rs`、`run_buffer.rs`
- codex 结论提取：`tools/bcs-bridge/core/src/engine/codex_app_server.rs`（`completed_text`）
- BCS 结论覆盖语义：`services/bcs-message-flow/src/bot_event.rs`
- Python A2A 约定：`engine/adapter/src/engine/community/api/transport/a2a/{card.py, wire.py}`

## 6. 实现说明（v1，与上文设计的差异）

| 设计 | v1 实现 | 原因 |
| --- | --- | --- |
| Provider `downlink.protocol = "a2a"` | 复用已有选择器 `downlink.protocol_version = "a2a/1.0"`（别名 `a2a`），`BotDeliveryTarget::is_a2a_provider()` | `protocol_version` 本就是下行协议选择器（1.0/2.0）；不新增字段、不改 `BotDeliveryTarget` 形状（66 处引用） |
| bridge 端点 `/a2a/{bot_ref}` | `/a2a/{provider_id}/{bot_ref}` | 一个 listener 可承载多个 Provider，bot_ref 可能重名 |
| BCS 传输独立 crate `bcs-provider-a2a` | `bcs-provider-http/src/a2a.rs` 模块；A2A 事件翻译为 BCN 帧后复用 `drive_sse_frame` | 复用私有 SSE 摄入流水线（去重、deadline、HITL 登记、终态合成），下游零改动 |
| `ProviderTaskBindingRepo` 持久化 run↔task | 进程内映射 | 与现有 SSE reader 生命周期一致（BCS 重启时 SSE 读者同样丢失）；持久化留待 push 模式一起做 |
| INPUT_REQUIRED 关流 | 流保持到终态 | 规范 §3.1.2（MUST）只要求终态关流；续答在同一流上继续，BCS 侧更简单 |
| BCS 端回调（push）模式 | 未实现；BCS 端走流式 → `Last-Event-ID` 续传 → `GetTask` 轮询；卡片 `streaming:false` 时直接轮询 | push 需要 BCS 公网回调地址、跨实例路由（§4 风险），单独一期 |
| — | 新增 `Task.metadata.pendingInteractions` | 轮询/快照续传的客户端也能拿到结构化 HITL |
| — | 第三方 Agent 的裸 `INPUT_REQUIRED` 合成为单题自由文本 ask_user；答复以文本 part 回传 | 无扩展的 Agent 也能在 BCS 群里完成 HITL |

已知约束：

- Python 引擎 `card.py` 的扩展声明用的是 `{"url": ...}`，规范 `AgentExtension` 字段为 `uri`；Rust 侧按规范使用 `uri`。
- bridge 注册 CLI 未加 `--a2a` 快捷方式：需在 `[bot.a2a]` 中启用，`--webhook-url` 指向 `/a2a/{provider_id}/{bot_ref}`，并将 Provider 的 `protocol_version` 设为 `a2a/1.0`。
- 引擎实测（真实 codex / claude）未在本期离线测试中覆盖。

验证（Avernet `apps/bcs`）：

- `cargo test -p bcs-a2a-protocol`：报文 golden，含规范样例 AgentCard 往返、拒绝 0.3 形状
- `cargo test -p bcs-bridge-core --test a2a_server`：卡片、鉴权/版本、阻塞、流式、轮询、push（含 SSRF 拒绝）、HITL 续答、断线续传、快照订阅、取消、重启恢复
- `cargo test -p bcs-provider-http --test a2a_downlink`：BCS ⇄ 真实 bridge（流式、HITL、abort、错误 token）；BCS ⇄ 第三方 Agent（断流续传、轮询 + 合成提问）
