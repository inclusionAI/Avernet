# Bot WebSocket V3 统一 Run Event 协议实现规格

状态：已实现
日期：2026-09-17
范围：BCS Bot WebSocket、Provider Run Event、MCP tool result coordination

## 1. 背景与问题

BCS 当前存在两套语义相近、结构不同的运行时事件协议：

- Provider 下行流使用 `bcs_protocol::stream` 中的 `agent`、`chat`、`interaction` 等事件；
- Bot WebSocket 上行使用 `ws::protocol` 中的 `AgentEventPayload`、`ChatEventPayload`，字段命名和 tool call 表达方式不同。

这种差异导致同一个 MCP tool call 在不同链路上需要重复解析。尤其是 WebSocket 上行无法自然复用 Provider 链路中“tool result 结束后解析 coordination call，并转换为 task intent”的处理逻辑。

当前仓库还在若干 builder 中预留了 `protocol_version >= 3` 的 session 字段分支，但公开协议最大版本仍为 V2。这些分支不是已发布的 V3 合同，实现时必须按本文重新校正。

## 2. 目标

### 2.1 功能目标

1. 新增 Bot WebSocket V3。
2. 除 `connect`、心跳和传输确认等 WebSocket 专属控制帧外，V3 的业务事件与 Provider Run Event 使用同一个合同。
3. 统一以下概念和字段：
   - tool call
   - chat
   - thinking
   - final
   - session ID
   - run ID
4. V3 支持从可信 MCP tool result 中解析 coordination call，并复用现有 `task.*` 入口。
5. V1、V2 保持兼容，但不承诺、也不新增基于 tool result 的 task intent 能力。

### 2.2 架构目标

- 合同定义只有一份，WebSocket 与 Provider adapter 只负责 framing、鉴权和兼容转换。
- message-flow core 只消费归一化后的 typed event，不感知 JSON 形态和传输来源。
- task intent 的识别、幂等、授权和派发仍由现有 coordination/message-flow 领域逻辑负责。
- 不在 adapter 中执行业务任务或直接写业务状态。

### 2.3 非目标

- 不删除 V1、V2。
- 不改变 `CoordinationCall` 自身的版本语义；WebSocket V3 与 coordination echo 的 `v=1/v=2` 是两个独立版本空间。
- 不允许从 tool start arguments 直接执行 task intent。
- 不把客户端提交的任意 MCP server/tool 名称当作可信授权配置。
- 不在本次改造中重构所有历史 RPC。

## 3. 已确认的版本策略

| 能力 | V1 | V2 | V3 |
| --- | --- | --- | --- |
| connect / auth | 保持 | 保持 | 保持，响应协商结果与能力 |
| 历史 WS event payload | 支持 | 支持 | 不支持 |
| 统一 Run Event payload | 不支持 | 不支持 | 支持 |
| 标准 session ID | 不保证 | 兼容旧映射 | 必须 |
| MCP tool result -> task intent | 不支持 | 不支持 | 支持 |
| Provider/WS 共用 golden fixtures | 否 | 否 | 是 |

服务端将最大协议版本从 2 提升到 3，最小版本保持 1。连接建立后，协议版本固定在该连接上下文中，后续事件不得自行声明或切换版本。未提交 `protocol_version` 的历史客户端固定按 V2 协商；V3 必须显式请求，避免服务端新增版本改变旧客户端语义。

V3 采用严格解析：

- V3 连接只接受 V3 Run Event；
- 不对 malformed V3 payload 静默回退为 V2；
- 不根据字段猜测协议版本；
- 解析错误返回稳定错误码并记录指标。

## 4. 协议分层

### 4.1 Transport Control

以下内容允许按传输分别定义：

- WebSocket：`connect`、`connected`、`ping`、`pong`、请求 ID、ACK；
- Provider：HTTP callback/SSE 的状态码、SSE framing、重连游标；
- 鉴权信息和连接生命周期。

这些控制消息不进入统一 Run Event 合同。

### 4.2 Run Event

以下业务事件必须共用 `bcs-protocol` 中同一组类型、同一序列化规则和同一测试 fixture：

- `agent/tool`
- `agent/thinking`
- 其他已支持的 agent stream
- `chat/delta`
- `chat/final`
- `interaction`

WebSocket frame 和 Provider SSE 只包裹或承载同一份事件 payload，不得各自维护第二套 DTO。

## 5. V3 Run Event 合同

### 5.1 通用字段

所有可归属到一次 run 的事件必须包含：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `runId` | string | 必填；标识一次运行 |
| `sessionId` | string | 必填；BCS 分配的 canonical session ID |
| `seq` | integer | 必填；同一 run 内单调递增 |
| `ts` | integer | 必填；Unix epoch milliseconds |

约束：

- V3 统一使用 camelCase。
- `sessionId` 是跨 WebSocket/Provider 的唯一 session 字段名。
- 客户端必须回显 BCS 在启动 run 时下发的 `sessionId`。
- BCS 必须用服务端 run context 校验 `botId + runId + sessionId`，不能信任客户端仅凭字段改变归属关系。
- Bot 上报事件不再携带 `bcsGroupId`。group 从可信 run context 恢复，防止客户端重定向事件。
- 若外部 runtime 仍有 `sessionKey`，只在 adapter 内作为兼容输入，不能进入 core 合同。

### 5.2 Tool event

tool start：

```json
{
  "event": "agent",
  "stream": "tool",
  "phase": "start",
  "runId": "run-123",
  "sessionId": "session-456",
  "seq": 12,
  "ts": 1789600000000,
  "toolCallId": "call-789",
  "toolName": "mcp__bcs__call",
  "arguments": {
    "v": 2,
    "intent": "task.create"
  }
}
```

tool result：

```json
{
  "event": "agent",
  "stream": "tool",
  "phase": "result",
  "runId": "run-123",
  "sessionId": "session-456",
  "seq": 13,
  "ts": 1789600001000,
  "toolCallId": "call-789",
  "toolName": "mcp__bcs__call",
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"v\":2,\"intent\":\"task.create\",\"intent_ref\":\"intent-abc\"}"
      }
    ]
  },
  "isError": false
}
```

规则：

- `phase` 取值为 `start`、`update`、`result`。
- `toolCallId` 在同一 run 内唯一。
- `toolName` 必须在 start/result 中一致。
- 只有 `phase=result` 且 `isError=false` 的事件可进入 task-intent 候选流程。
- result 保留 MCP structured content，不先压成 stdout string；coordination parser 从 text content 或明确支持的 structured content 中提取调用。
- `update` 仅用于展示或进度，不触发 task intent。

### 5.3 Thinking event

```json
{
  "event": "agent",
  "stream": "thinking",
  "phase": "update",
  "runId": "run-123",
  "sessionId": "session-456",
  "seq": 9,
  "ts": 1789600000000,
  "deltaText": "正在分析任务依赖"
}
```

thinking 是运行时可观测事件，不作为 task intent 输入。

### 5.4 Chat delta 与 final

```json
{
  "event": "chat",
  "state": "delta",
  "runId": "run-123",
  "sessionId": "session-456",
  "seq": 20,
  "ts": 1789600002000,
  "content": "已完成第一步"
}
```

```json
{
  "event": "chat",
  "state": "final",
  "runId": "run-123",
  "sessionId": "session-456",
  "seq": 21,
  "ts": 1789600003000,
  "content": "任务已完成"
}
```

规则：

- final 是 chat 的终态，不再通过 WS 专属字段或事件名表达。
- 相同 run 只能接受一次有效 final；重复 final 按幂等规则忽略并记录。
- seq 在业务处理成功后才提交；处理失败必须释放预留，使客户端可使用相同 seq 重试。
- tool event 不复用 `chat.state=tool_call_end`，避免同一语义存在两种模型。

### 5.5 Interaction

`interaction` 延续共享 stream 合同。若具体 interaction 暂未被 WebSocket client 使用，V3 parser 仍应能解析并安全转交或明确拒绝，不得降级为 unknown 后吞掉。

## 6. 业务下行合同

V3 的“上下行统一”是业务合同统一，不是把所有 frame 做成完全相同的传输协议。

对于 BCS 发往 Bot 的业务消息：

- chat message
- task dispatch
- task result
- abort/cancel
- inject/context

应逐步迁移为 `bcs-protocol` 中的共享 typed payload。WebSocket 和 Provider 仅添加各自 envelope 所需的 request ID、delivery metadata 或 SSE framing。

同一个业务 payload 在两个 adapter 中序列化后的业务字段必须一致，并由 golden fixture 验证。

## 7. Session 与 Run 身份

### 7.1 Canonical session

BCS 为每个活动 session 分配 `sessionId`。它是：

- 运行事件关联键；
- run context 校验的一部分；
- WS 与 Provider 之间统一的 session 标识；
- 可观测性和排障的主键。

旧版中借用 `bcs_group_id` 或使用 `session_key` 的做法只保留在 V1/V2 或 Provider legacy adapter 内。core 不再同时理解多个 session 字段。

### 7.2 Run context

BCS 保存至少以下服务端状态：

```text
bot_id + run_id -> session_id + group_id + ingress + protocol_version
```

收到 V3 event 时：

1. 用连接身份确定 bot；
2. 用 `runId` 查找 run context；
3. 比较 `sessionId`；
4. 从 context 恢复 group；
5. 完成 seq 校验和去重；
6. 转换为 canonical event 交给 message flow。

## 8. MCP tool result 到 task intent

### 8.1 触发条件

只有同时满足以下条件时才解析 task intent：

1. ingress 是 Bot WebSocket V3，或已声明等价能力的 Provider Run Event；
2. event 是 `agent/tool/result`；
3. `isError=false`；
4. tool start/result 能按 `toolCallId` 配对；
5. `toolName` 精确匹配服务端配置的 coordination tool；
6. 该 bot 的 coordination profile 允许此能力；
7. payload 通过 size、schema、session、run 和 seq 校验；
8. coordination call 通过已有版本、授权、claim 和幂等校验。

任何一个条件不满足，都不得派发 task。

### 8.2 信任模型

coordination profile 由 BCS 服务端管理，至少包含：

- `clientKind`；
- 是否支持 `toolResultTaskIntent`；
- 允许的 MCP server；
- coordination tool 的 exact canonical name；
- 支持的 coordination call versions。

客户端在 connect 中可以声明 `clientKind`，但不能上传或覆盖 tool allowlist。BCS 根据已注册配置解析该 kind；未知 kind 默认关闭 task intent。connect 声明也不能创建或覆盖 bot 的 server-owned coordination profile；声明与已注册 profile 不匹配时，task intent 必须 fail closed。

connect 成功响应应返回协商后的能力，例如：

```json
{
  "protocolVersion": 3,
  "capabilities": {
    "unifiedRunEvents": true,
    "toolResultTaskIntent": true,
    "canonicalSessionId": true
  }
}
```

### 8.3 复用现有领域入口

解析成功后继续复用：

```text
CoordinationCall
  -> source tool validation
  -> dedup / claim
  -> task.* command
  -> existing task application service
```

adapter 不直接创建、接受、完成或取消 task。

## 9. 内部归一化边界

### 9.1 Ingress metadata

为 message flow 增加明确的来源信息，避免只靠 `event_type: String` 判断：

```rust
enum BotEventIngress {
    WebSocket { protocol_version: u32 },
    ProviderSse { contract_version: String },
    ProviderCallback { contract_version: String },
}
```

来源和版本必须由 adapter/连接上下文注入，不能取自事件 payload。

### 9.2 Canonical event

目标形态：

```rust
enum CanonicalBotEvent {
    Agent(AgentEvent),
    Chat(ChatEvent),
    Interaction(InteractionEvent),
}
```

`AgentEvent`、`ChatEvent` 等类型定义在 `bcs-protocol::stream`，供 Provider 与 WS V3 共用。`BotEventCommand` 应携带 typed event 或经过校验的 canonical wrapper，而不是让 core 再解析任意 `serde_json::Value`。

允许分阶段迁移：第一阶段可同时保留 legacy raw payload 字段，但 V3 和 Provider 必须先在 adapter 中解析成 canonical event；legacy 字段需有删除计划。

### 9.3 分层职责

| 层 | 职责 |
| --- | --- |
| `bcs-protocol` | 唯一合同、serde、版本和 fixture |
| WS adapter | connect/auth、frame parsing、V1/V2 compatibility、V3 canonical parsing |
| Provider adapter | SSE/callback framing、legacy alias normalization、canonical parsing |
| message-flow | event 状态、持久化编排、coordination 候选识别 |
| interaction/task service | task 领域规则、授权、状态转换 |

## 10. 兼容与迁移

### 10.1 Server first

先发布服务端：

- `BCS_PROTOCOL_VERSION = 3`；
- `BCS_MIN_PROTOCOL_VERSION = 1`；
- V1/V2 走现有 compatibility parser；
- V3 走 strict canonical parser；
- connect response 返回实际协商版本和能力。

### 10.2 V1/V2 行为收口

V1/V2 保留原有连接和消息能力，但 tool result 不再作为 task-intent 合同。

仓库当前 message-flow 可能已经对部分 V2 `agent/tool/result` 执行 coordination echo。落地前必须：

1. 盘点线上是否存在依赖该非正式行为的 V2 client；
2. 将官方 client 升级到 V3；
3. 再关闭 V1/V2 的 echo 入口。

如果无法一次迁完，只允许使用服务端短期 allowlist 作为迁移措施，并必须记录 owner、到期时间和删除条件；不能把兼容逻辑写成永久的 payload 猜测。

V1/V2 仍可通过显式、已有的 task RPC 使用 task 能力，不受影响。

### 10.3 官方 client

至少同步升级：

- `openclaw-channel-bcn`；
- `deepseek-harness-channel-bcn`。

升级内容包括：

- connect protocol version 改为 3；
- 读取 negotiated capabilities；
- 所有 agent/chat/thinking/final 使用共享类型；
- 保存并回显 `sessionId`；
- MCP tool result 保留 structured content；
- 遇到服务端仅支持 V2 时显式降级，且关闭 tool-result task intent。

### 10.4 Provider compatibility

Provider 已有流若只提供 `sessionKey`，adapter 可在迁移期结合可信 run context 归一化为 `sessionId`。该 legacy wire payload 不算 V3 conformance；新 Provider 实现必须直接使用 canonical 字段和 fixture。

## 11. 可观测性

新增或统一以下标签与指标：

- `bot_event_ingress_total{transport,version,event,stream,phase}`
- `bot_event_parse_failure_total{transport,version,reason}`
- `bot_event_session_mismatch_total{transport,version}`
- `bot_event_sequence_rejected_total{reason}`
- `coordination_echo_candidate_total{transport,version}`
- `coordination_echo_rejected_total{reason}`
- `coordination_echo_dispatched_total{intent,transport}`

日志必须包含 `bot_id`、`run_id`、`session_id`、`tool_call_id` 和 rejection reason；不得记录完整 token、敏感 arguments 或未经截断的 tool result。

## 12. 代码改动范围

预计涉及：

1. `crates/contracts/bcs-protocol/src/stream/*`
   - 固化 canonical Run Event 和 serde 规则；
   - 增加 `sessionId`、`seq` 和 MCP structured result；
   - 移除 WS V3 对独立 DTO 的依赖。
2. `crates/contracts/bcs-protocol/src/ws/protocol.rs`
   - 最大版本升到 3；
   - connect response capabilities；
   - 保留 V1/V2 DTO，仅用于 compatibility；
   - 校正现有未发布的 `>=3` session builder 分支。
3. `crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs`
   - 按连接版本选择 parser；
   - V3 解析共享 Run Event；
   - 注入可信 ingress metadata。
4. `crates/adapters/provider/bcs-provider-http/src/lib.rs`
   - 直接产出 canonical event；
   - legacy Provider 字段仅在 adapter 内归一化。
5. `crates/contracts/bcs-service-api/src/application/message_flow.rs`
   - 为 command 增加 ingress metadata；
   - 逐步将 raw payload 收敛为 typed event。
6. `crates/services/bcs-message-flow/src/bot_event.rs` 与 `bot_event/coordination.rs`
   - 基于 typed tool result 判断候选；
   - 限定 V3/等价 Provider capability；
   - 复用现有 coordination parser、claim 和 task dispatch。
7. Bot/client profile 所在 core
   - 增加服务端管理的 coordination capability 和 exact tool mapping。
8. 两个官方 plugin
   - 升级连接版本和事件序列化。

## 13. 实施阶段

### C1：合同与 fixtures

- 定义 canonical Rust types 和 JSON schema。
- 明确 session/run/seq 约束。
- 为 tool start/result、thinking、chat delta/final、interaction 建 golden fixtures。
- 更新协议文档和 conformance matrix。

完成条件：Provider parser 与 WS V3 parser 对相同 fixture 产出完全相同的 typed event。

### C2：服务端 V3 接入

- 完成 version negotiation 和 capability response。
- 增加 V3 strict parser。
- 建立可信 run context 校验。
- 保持 V1/V2 parser 隔离。

完成条件：V1/V2 回归通过，V3 正常事件和错误事件均有稳定结果。

### C3：MCP coordination

- 持久化或缓存 tool start/result 配对所需最小状态。
- 校验 server-owned coordination profile。
- 从 MCP result 提取 `CoordinationCall`。
- 接入现有 dedup/claim/task pipeline。

完成条件：只有可信、成功、匹配的 result 能产生一次 task intent。

### C4：官方 client 升级

- OpenClaw plugin 升级至 V3。
- DeepSeek harness plugin 升级至 V3。
- 增加 V3/V2 negotiation 与显式降级测试。

完成条件：两个 client 均通过共享 fixture 与端到端 task-intent story。

### C5：兼容收口

- 观察 V3 使用率和 V2 echo 依赖。
- 关闭 V1/V2 tool-result task intent。
- 删除临时 allowlist 和 adapter 兼容分支。

完成条件：生产流量中不再依赖 V2 非正式 echo 行为。

## 14. 测试计划

### 14.1 合同测试

- 每个事件提供序列化/反序列化 golden fixture。
- WS V3 与 Provider 对同一 fixture 得到相同 typed event。
- unknown event/field 的 forward-compatibility 行为明确。
- camelCase、必填字段、enum 值、size limit 都有负向用例。

### 14.2 版本测试

- V1/V2 connect 和基础消息回归。
- V1/V2 tool result 不触发 task intent。
- V3 不接受 V2 shape。
- unsupported version 返回稳定错误。
- capability 与实际行为一致。

### 14.3 身份与顺序测试

- session mismatch 拒绝。
- unknown run 拒绝。
- group spoofing 不可能从 payload 生效。
- duplicate/out-of-order seq 按规则处理。
- duplicate final、duplicate tool result 幂等。

### 14.4 MCP/task intent 测试

- 正常 MCP text result 产生一次 task command。
- 支持的 structured result 产生一次 task command。
- `isError=true`、wrong tool、unknown client kind、malformed result 均不触发。
- start/result 的 toolCallId 或 toolName 不一致时拒绝。
- coordination call v1/v2 分别通过既有规则。
- 重放 result 不重复派发。
- tool start arguments 即使包含合法 intent 也不触发。

### 14.5 端到端测试

新增至少两条 BCS user-story：

1. WS V3 Bot 通过 MCP tool result 创建或推进 task；
2. 同一语义经 Provider Run Event 获得等价结果。

两条 story 应复用同一业务 payload fixture，只替换 transport harness。

## 15. 验证命令

实现阶段至少运行：

```bash
cd src/bcs
cargo test -p bcs-protocol
cargo test -p bcs-ws
cargo test -p bcs-provider-http
cargo test -p bcs-message-flow
cargo test --workspace
```

合同变更还需执行仓库定义的协议 conformance 与 BCS singlebox user-story gate。不得通过放宽架构或覆盖率检查来使变更通过。

## 16. 验收标准

1. V3 的 tool/chat/thinking/final/session/run 合同只在 `bcs-protocol::stream` 定义一次。
2. WS V3 与 Provider 对共享 fixture 的 typed representation 相同。
3. transport adapter 中没有 task 领域执行逻辑。
4. V3 成功 MCP tool result 能触发既有 task-intent pipeline。
5. 非可信 tool、失败 result、错误 session/run、重复 result 都不能产生 task。
6. V1/V2 基础能力兼容，且不提供 tool-result task intent。
7. 官方 OpenClaw 与 DeepSeek client 完成 V3 升级和降级测试。
8. 协议文档、conformance tests、E2E stories 和可观测性同时交付。

## 17. 合同传播清单

本变更属于跨边界协议变更，提交时必须同时检查并更新：

- [架构规则](../../../../docs/arch/arch.rules.md)
- [协议合同测试要求](../../../../docs/arch/protocol-contract-tests.md)
- [BCS WebSocket context](../../crates/adapters/ws/bcs-ws/CONTEXT.md)
- [BCS protocol context](../../crates/contracts/bcs-protocol/CONTEXT.md)
- [message-flow context](../../crates/services/bcs-message-flow/CONTEXT.md)
- V3 wire schema / fixtures
- 官方 plugin 的协议说明和测试
- PR 的 Compatibility and risk 部分

任何临时兼容措施都必须写明 owner、到期时间、移除条件和对应测试，不能只存在于代码注释中。
