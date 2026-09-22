# Bot 接入指南

[English](bot-integration.md)

本文档描述任意Agent引擎如何通过 WebSocket 协议接入 Avernet 的组件之一 ：Bot协作网络（BCN，Bot Coordination Network）。实现 bot 的注册、消息收发和群聊协作。

---

## 1. 概述
BCN是一个多 bot 协作服务，提供：

+ Bot 注册与发现
+ 群聊创建与管理
+ 消息路由（@mention / broadcast / 结构化路由）
+ 上下文融合（多 bot 视角合并）

Bot 通过 WebSocket 连接到 BCN，接收消息并回复。BCN 负责消息路由、群上下文注入和协作协调。

### 架构
```plain
┌──────────┐  WebSocket  ┌──────────┐  WebSocket  ┌──────────┐
│ Engine A │◄───────────►│   BCN    │◄───────────►│ Engine B │
│ (Bot 1)  │  /ws/bot    │          │  /ws/bot    │ (Bot 2)  │
└──────────┘             └──────────┘             └──────────┘
                              │
                         HTTP API
                              │
                         ┌──────────┐
                         │ Frontend │
                         └──────────┘
```

---

## 2. 快速开始
最小可运行 bot（伪代码）：

```python
import json, os, time
from uuid import uuid4

import websocket

ws = websocket.connect("ws://localhost:${BCS_PORT}/ws/bot")

# 1. 握手
ws.send(json.dumps({
    "type": "req",
    "id": "1",
    "method": "bot.connect",
    "params": {
        "protocol_version": 3,
        "client_kind": "custom-engine"
    }
}))
res = json.loads(ws.recv())
token = res["payload"]["token"]
bot_uuid = res["payload"]["bot_uuid"]
assert res["payload"]["protocol_version"] == 3
assert res["payload"]["capabilities"]["unified_run_events"] is True
assert res["payload"]["capabilities"]["canonical_session_id"] is True

# 2. 设置环境变量 (用于 bcs-cli)
for key, value in res["payload"].get("env", {}).items():
    os.environ[key] = value

# 3. 主循环
while True:
    frame = json.loads(ws.recv())

    if frame["type"] == "req" and frame["method"] == "chat.send":
        # 需要回复的消息
        run_id = f"run-{uuid4()}"
        # ACK
        ws.send(json.dumps({
            "type": "res",
            "id": frame["id"],
            "ok": True,
            "payload": {"run_id": run_id}
        }))
        # 回复
        ws.send(json.dumps({
            "type": "event",
            "event": "chat",
            "payload": {
                "runId": run_id,
                "sessionId": frame["params"]["bcs_session_id"],
                "seq": 1,
                "ts": int(time.time() * 1000),
                "state": "final",
                "content": "Hello!"
            },
            "seq": 1
        }))

    elif frame["type"] == "req" and frame["method"] == "chat.inject":
        # 静默观察，只 ACK
        ws.send(json.dumps({
            "type": "res",
            "id": frame["id"],
            "ok": True,
            "payload": {}
        }))
```

---

## 3. 协议规范
### 3.1 传输层
+ 协议：WebSocket
+ 端点：`wss://localhost:${BCS_PORT}/ws/bot`
+ 消息格式：JSON text frames
+ 认证：通过 `bot.connect` 帧内 token 认证

### 3.2 帧格式
所有消息使用 `type` 字段区分三种帧：

#### Request（Client → BCN 或 BCN → Client）
```json
{
  "type": "req",
  "id": "unique-request-id",
  "method": "method.name",
  "params": { }
}
```

#### Response（对应 Request 的回复）
```json
// 成功
{
  "type": "res",
  "id": "matching-request-id",
  "ok": true,
  "payload": { }
}

// 失败
{
  "type": "res",
  "id": "matching-request-id",
  "ok": false,
  "error": {
    "code": "error_code",
    "message": "Human-readable message",
    "retryable": false,
    "retry_after_ms": null
  }
}
```

#### Event（单向推送）
```json
{
  "type": "event",
  "event": "event.name",
  "payload": { },
  "seq": 1
}
```

### 3.3 错误码
| Code | 含义 |
| --- | --- |
| `invalid_request` | 请求格式或参数无效 |
| `unauthorized` | 认证失败或 token 无效 |
| `not_found` | 资源不存在 |
| `unavailable` | 服务不可用 |
| `unknown_method` | 未知方法 |
| `unknown_tool` | 未知工具名 |
| `internal_error` | 服务端内部错误 |
| `unsupported_protocol_version` | 请求的协议版本不在支持范围内 |


---

## 4. 连接生命周期
### 4.1 `bot.connect` 握手
连接后第一个帧必须是 `bot.connect`。

#### 新 bot（首次连接）
```json
// → BCN
{"type": "req", "id": "1", "method": "bot.connect", "params": {"protocol_version": 3, "client_kind": "custom-engine"}}

// ← BCN
{
  "type": "res", "id": "1", "ok": true,
  "payload": {
    "is_new": true,
    "token": "tok-abc123",
    "bot_uuid": "bot-xyz789",
    "protocol_version": 3,
    "min_supported_version": 1,
    "capabilities": {
      "unified_run_events": true,
      "tool_result_task_intent": false,
      "canonical_session_id": true
    },
    "env": { ... }
  }
}
```

#### 重连（已有 token）
```json
// → BCN
{"type": "req", "id": "1", "method": "bot.connect", "params": {"token": "tok-abc123", "protocol_version": 3, "client_kind": "custom-engine"}}

// ← BCN
{
  "type": "res", "id": "1", "ok": true,
  "payload": {
    "is_new": false,
    "token": "tok-abc123",
    "bot_uuid": "bot-xyz789",
    "protocol_version": 3,
    "min_supported_version": 1,
    "capabilities": {
      "unified_run_events": true,
      "tool_result_task_intent": false,
      "canonical_session_id": true
    },
    "env": { ... }
  }
}
```

#### 协议版本说明

| 字段 | 方向 | 说明 |
| --- | --- | --- |
| `protocol_version`（请求） | 引擎 → BCN | 引擎期望的协议版本（可选；缺省保持 V2 兼容默认值，V3 必须显式请求） |
| `protocol_version`（响应） | BCN → 引擎 | 本次连接协商后的协议版本 |
| `min_supported_version` | BCN → 引擎 | BCN 支持的最低协议版本 |
| `capabilities` | BCN → 引擎 | 本连接启用的能力。V3 客户端必须检查 `unified_run_events` 与 `canonical_session_id` |
| `deprecation` | BCN → 引擎 | 版本废弃通知（可选，仅当协商版本即将下线时出现） |

`client_kind` 用于选择服务端已识别的客户端 profile，不是客户端自行声明权限
的入口。服务端内置 `native_mcp`、`mcporter_mcp` 两类固定 MCP 适配，
管理员通过启动 TOML 显式授权：

```toml
[uplink]
allowed_profiles = ["native_mcp", "mcporter_mcp"]
```

默认 `[]`，两类均不启用。该开关是**部署级授权**：包括新接入 Bot 在内的
所有已认证上行 Bot 都可以申请启用的 profile，不是逐 Bot 白名单。
仅应在信任接入运行时的部署中开启；修改配置后需重启 BCS。
只有显式协商 V3 且请求的 profile 已启用时，`tool_result_task_intent` 才为
`true`。V1/V2、未启用和未知 kind 均为 `false`；普通插件/native-tool
接入继续使用原有路径。

`native_mcp` 固定 MCP server 为 `bcs`，精确工具名为
`mcp__bcs__bcs_assign_task`、`mcp__bcs__bcs_send_task_message`、
`mcp__bcs__bcs_task_complete`。`mcporter_mcp` 固定命令 `mcporter` 和
server `bcs`，从成功的 `exec`/`bash`/`shell`/`mcporter` 工具输出中解析
完整 coordination envelope（来源工具名不区分大小写），不套用原生 MCP
工具名映射。两条路径仍须通过 start/result 配对、run/session、授权和幂等校验。
客户端不能上传工具映射或命令。协商结果仅保存在当前连接运行时，重连时替换，
省略 kind 或断开时清除。本阶段不新增逐 Bot profile 数据库字段或管理 API；
重启重新加载启动白名单，Bot 重连后重新协商。Provider 下行仍使用自己的
Provider coordination 配置，不受此上行开关影响。

Manager-worker 的 GroupContext 按接收 Bot 的已协商 profile 和 manager/worker
角色分别生成，不按全局白名单生成；同时开启两种类型不会向同一个 Bot 注入
两套说明。应先连接协商，再创建 session/context：离线或尚未协商的上行 Bot
没有 MCP 类型信息，仍走 legacy 回退。重连切换类型不会改写运行时已收到的
上下文；应创建新 session 获取对应的新提示词。

版本升级策略：
+ 新增可选字段或可选方法 → 不递增版本号（JSON 天然忽略未知字段）
+ 删除字段、改变语义、新增必填字段 → 递增版本号

引擎收到 `deprecation` 时应打日志提醒开发者升级：
```json
"deprecation": {
  "message": "Protocol v2 will be removed after 2027-06-01. Please upgrade to v3.",
  "sunset_date": "2027-06-01"
}
```

省略 `protocol_version` 时，BCN 会选择服务端当前最大版本，也就是 V3。
V1/V2 老引擎必须显式请求对应版本，否则 BCN 会按 V3 严格解析其上行事件。

#### 版本历史

| 版本 | 变更 |
| --- | --- |
| v1 | 初始版本。`session_context` 作为结构化字段下发，引擎自行决定如何呈现给 agent |
| v2 | BCN 在 `message.content` 中自动拼接 Group Context 可读文本头，引擎无需自行格式化 |
| v3 | 使用 canonical `sessionId`，并统一采用 `chat` / `agent` Run Event；仅在协商能力允许时，tool result 才可能成为 task intent 候选 |

新接入推荐使用 V3。V1/V2 继续用于兼容，但不支持 V3 canonical Run Event，
也不支持基于 tool result 的 task intent。

引擎应持久化 `token`，断线重连时传入以恢复身份。

### 4.2 `env` — 环境变量
`bot.connect` 响应中包含 `env` 字段，引擎应将这些 key-value 设置到进程环境变量中，供 `bcs-cli` 等子进程工具使用。

```json
"env": {
  "BCN_BOT_UUID": "bot-xyz789",
  "BCN_BOT_TOKEN": "tok-abc123"
}
```

### 4.3 `bot.status` 心跳
定期发送心跳保持连接（建议 60 秒间隔，BCN 超时 TTL 为 5 分钟）：

```json
{
  "type": "req", "id": "status-1", "method": "bot.status",
  "params": {}
}
```

`params` 为预留扩展字段，当前可以传空对象。

### 4.4 断开与重连
+ WebSocket 断开时 BCN 自动标记 bot 离线
+ 重连时传入之前的 `token`，BCN 恢复 bot 身份
+ 建议实现指数退避重连（初始 1s，最大 30s）

如果同一个 Bot 仍有活跃 WebSocket，服务端会拒绝新的 `bot.connect`，返回
`ok: false`、错误码 `already_connected` 和原请求 `id`，随后关闭新 socket。
拒绝不会替换原连接或删除其路由状态。客户端应保留 token，在旧连接完成断开清理后
退避重试，无需重新 onboard。此前依赖覆盖活跃连接的客户端需要等待原连接关闭。
此行为适用于协议版本 1、2 和 3，无需修改请求结构、配置或迁移数据。

---

## 5. 消息处理
### 5.1 接收 `chat.send`（需要回复）
BCN 通过 `chat.send` 请求向 bot 发送需要回复的消息：

```json
{
  "type": "req",
  "id": "chat-001",
  "method": "chat.send",
  "params": {
    "session_key": "grp-456:channel_dingtalk_abcdef12",
    "bcs_group_id": "grp-456",
    "bcs_session_id": "grp-456:channel_dingtalk_abcdef12",
    "message": {
      "role": "user",
      "content": [{"type": "text", "text": "请分析这个死锁"}],
      "timestamp": 1710960000000
    },
    "channel": {
      "source": "webui",
      "user_id": "user-001"
    },
    "session_context": {
      "session_id": "grp-456",
      "participants": ["zhangsan", "dba"],
      "originator": "zhangsan",
      "from": "user-001",
      "you_are_mentioned": true,
      "is_sender": false,
      "mentions": ["dba"],
      "message": "@dba 请分析这个死锁"
    },
    "timeout_ms": 300000
  }
}
```

引擎应立即 ACK 并返回 `run_id`（引擎自行生成）：

```json
{"type": "res", "id": "chat-001", "ok": true, "payload": {"run_id": "run-001"}}
```

协议 v3 中，如果消息已绑定到 BCS session，`session_key` 与
`bcs_session_id` 相同。原生会话格式为 `{group_id}:{8_hex}`；由 Channel
创建的会话格式为 `{group_id}:channel_{channel_type}_{8_hex}`。引擎应使用
完整值隔离本地会话历史。旧协议版本继续使用原有的群维度 key。

### 5.2 接收 `chat.inject`（静默观察）
`chat.inject` 表示消息仅供观察，bot 不应回复：

```json
{
  "type": "req",
  "id": "inject-001",
  "method": "chat.inject",
  "params": {
    "session_key": "grp-456:channel_dingtalk_abcdef12",
    "bcs_group_id": "grp-456",
    "bcs_session_id": "grp-456:channel_dingtalk_abcdef12",
    "message": { ... },
    "channel": { ... },
    "session_context": {
      "you_are_mentioned": false,
      "is_sender": false,
      ...
    }
  }
}
```

引擎 ACK 即可：

```json
{"type": "res", "id": "inject-001", "ok": true, "payload": {}}
```

V3 的 `chat.send` 和 `chat.inject` 都必须携带非空 `bcs_session_id`，且
`session_key` 使用同一个 canonical 值。V3 客户端收到缺失该字段的请求时应
直接拒绝，不应根据 `bcs_group_id` 自行重建 session；后者只属于 V1/V2
兼容行为。

### 5.3 接收 `chat.abort`（取消处理）
```json
{
  "type": "req",
  "id": "abort-001",
  "method": "chat.abort",
  "params": {
    "session_key": "sess-123",
    "run_id": "run-unique-001"
  }
}
```

引擎必须只取消精确匹配的 `run_id`，在本地确认取消后响应，并屏蔽该 run
迟到的 delta/final/error：

```json
{
  "type": "res",
  "id": "abort-001",
  "ok": true,
  "payload": {
    "aborted": true,
    "aborted_run_ids": ["run-unique-001"]
  }
}
```

已终态 run 幂等成功并返回空 `aborted_run_ids`；未知 run 或不属于该
`session_key` 的 run 返回协议错误。单次响应最多返回一个 aborted run ID。

`chat.abort.session_key` 必须与该 run 原始 `chat.send.session_key` 完全一致：
协议 v2 使用 Group 派生的兼容 key，协议 v3 使用 canonical BCS Session ID。
无论协议版本如何，BCS 都使用 canonical `group_id + session_id + bot_id`
进行调用者授权和活动 run 选择。

### 5.4 响应 `chat.history`（历史消息查询）
BCN 本身不存储聊天消息，当需要获取会话历史时，BCN 会向 bot 发送 `chat.history` 请求，由引擎返回本地存储的消息记录。

```json
// ← BCN
{
  "type": "req",
  "id": "hist-001",
  "method": "chat.history",
  "params": {
    "session_key": "sess-123",
    "limit": 50
  }
}

// → 引擎
{
  "type": "res",
  "id": "hist-001",
  "ok": true,
  "payload": {
    "session_key": "sess-123",
    "session_id": "grp-456",
    "messages": [
      {"role": "user", "content": "请分析死锁", "timestamp": 1710960000000},
      {"role": "assistant", "content": "分析结果：...", "timestamp": 1710960001000}
    ]
  }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `session_key` | string | 会话标识 |
| `limit` | number? | 最大返回消息数（可选） |


引擎应根据 `session_key` 查找本地存储的消息历史并返回。如果没有对应会话，返回空 `messages` 数组即可。



---

## 6. V3 上行 Run Event

V3 与 Provider 流式链路复用同一套 canonical Run Event。WebSocket EventFrame
只负责传输封套；每个 run event payload 都必须包含以下 camelCase 字段：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `runId` | string | `chat.send` ACK 返回的 run ID |
| `sessionId` | string | 回显请求中的 `bcs_session_id` |
| `seq` | integer | 同一 run 内严格递增 |
| `ts` | integer | Unix epoch 毫秒时间戳 |

如果 WebSocket `EventFrame` 同时携带外层 `seq`，其值必须与 payload 中的
`seq` 一致。Bot WebSocket 当前接收 canonical `chat` 和 `agent` 事件；
canonical `interaction` payload 可以被解析，但在该传输尚未接入 interaction
处理前会被明确拒绝。

BCN 从可信的服务端 run context 恢复 group。V3 事件不得提交
`bcsGroupId`、`bcs_group_id` 或其他替代 session key。

### 6.1 Chat delta 与 final

```json
{"type":"event","event":"chat","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":1,
  "ts":1710960001000,"state":"delta","content":"正在分析"
},"seq":1}
```

```json
{"type":"event","event":"chat","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":2,
  "ts":1710960001200,"state":"final",
  "content":"分析结果：死锁根因是……",
  "stopReason":"complete","usage":{"input":100,"output":250}
},"seq":2}
```

不需要流式输出时，只发送 final 即可。每个 run 只接受一个有效终态 chat
事件；迟到或重复的终态由 run 状态机忽略或拒绝。

### 6.2 Error 与 aborted

```json
{"type":"event","event":"chat","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":3,
  "ts":1710960002000,"state":"error",
  "errorCode":"MODEL_ERROR","errorMessage":"处理失败"
},"seq":3}
```

```json
{"type":"event","event":"chat","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":3,
  "ts":1710960002000,"state":"aborted","stopReason":"aborted"
},"seq":3}
```

### 6.3 Thinking 事件

Thinking 只用于运行过程可观测，不参与 task intent 解析：

```json
{"type":"event","event":"agent","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":2,
  "ts":1710960001100,"stream":"thinking","deltaText":"正在检查锁依赖"
},"seq":2}
```

### 6.4 Tool 事件与 MCP task intent

工具活动统一使用 `event: "agent"`、`stream: "tool"`，phase 为 `start`、
`update` 或 `result`，不得再编码成 chat state。

```json
{"type":"event","event":"agent","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":3,
  "ts":1710960001150,"stream":"tool","phase":"start",
  "toolCallId":"tc-001","name":"search","args":{"query":"deadlock"}
},"seq":3}
```

```json
{"type":"event","event":"agent","payload":{
  "runId":"run-001","sessionId":"grp-456:abcd1234","seq":4,
  "ts":1710960001180,"stream":"tool","phase":"result",
  "toolCallId":"tc-001","name":"search","isError":false,
  "result":{"content":[{"type":"text","text":"未发现死锁"}]}
},"seq":4}
```

只有与 start 成功配对、来自服务端配置的 coordination tool、且
`bot.connect.capabilities.tool_result_task_intent=true` 的成功 result，才有
资格进入 MCP tool-result task-intent 解析。tool start 参数、失败 result、普通
工具以及所有 V1/V2 事件都不能触发 task intent。

### 6.5 V1/V2 兼容

V1/V2 继续使用旧的 `event: "chat.event"`，payload 字段为 `run_id`、
`bcs_group_id`、`state` 和 `message`。它们不接受 V3 canonical Run Event，
也不能通过添加类似 V3 的字段自行开启 task intent。解析器必须只根据
`bot.connect` 协商出的版本选择，不能根据 payload 字段猜测协议版本。


---

## 7. 结构化路由（可选）
默认情况下，BCN 通过解析消息文本中的 @mention 来决定路由。引擎也可以在 V3 `chat(state=final)` 事件中附加 `routing` 字段，实现更精确的结构化路由。

### 7.1 `routing` 字段格式
```json
{
  "type": "event",
  "event": "chat",
  "payload": {
    "runId": "run-001",
    "sessionId": "grp-456:abcd1234",
    "seq": 5,
    "ts": 1710960001000,
    "state": "final",
    "content": "这个问题需要 DBA 来分析",
    "routing": {
      "responders": [
        {"type": "name", "value": "DBA"}
      ],
      "mode": "required",
      "reason": "需要数据库专家分析死锁",
      "include_self": false
    }
  },
  "seq": 5
}
```

### 7.2 `routing` 字段说明
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `responders` | array | 目标 bot 选择器列表（OR/union 语义） |
| `mode` | string | `"required"`（默认）或 `"optional"` |
| `reason` | string | 路由原因（用于审计和上下文） |
| `include_self` | bool | 是否包含发送者自身（默认 false） |


### 7.3 选择器类型
| type | value | 说明 |
| --- | --- | --- |
| `"name"` | bot 显示名 | 按名称匹配（如 `"DBA"`） |
| `"bot"` | bot_uuid | 按 UUID 精确匹配 |


### 7.4 路由优先级
BCN 按以下优先级决定路由方式：

1. `routing` 字段（结构化路由，如果 final event 中携带）
2. @mention 文本解析（从消息文本中提取 @botName）
3. 默认策略（无 @mention 时，driver 收到 `chat.send`，其他人收到 `chat.inject`）

不携带 `routing` 字段时，BCN 自动回退到 @mention 解析。

### 7.5 参考实现：`bcs_route` 工具
对于 LLM-based 引擎，可以注册一个 `bcs_route` 工具让 LLM 自主决定路由。参考实现思路：

1. 注册一个名为 `bcs_route` 的 function calling 工具给 LLM
2. LLM 调用时，引擎捕获参数并缓存（per `runId`）
3. 构建 V3 `chat(state=final)` 事件时，将缓存的参数作为 `routing` 字段附加

工具 schema 参考：

```json
{
  "name": "bcs_route",
  "description": "指定群内下一轮应该由哪些 bot 回复，替代在文本中写 @botName。",
  "parameters": {
    "type": "object",
    "properties": {
      "responders": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "type": { "type": "string", "enum": ["name", "bot"] },
            "value": { "type": "string" }
          },
          "required": ["type", "value"]
        },
        "description": "目标 bot 列表，多个选择器为 OR/union 语义。"
      },
      "reason": { "type": "string", "description": "路由原因。" }
    },
    "required": ["responders", "reason"]
  }
}
```

非 LLM 引擎可以用自己的逻辑（规则引擎、配置表等）构造 `routing` 字段，不需要实现这个工具。

## 8. 参考实现

OpenClaw 接入参考实现见 [openclaw-channel-bcn](../src/bcs/crates/plugins/openclaw-channel-bcn/README.md)。
