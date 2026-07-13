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
import websocket, json

ws = websocket.connect("ws://localhost:${BCS_PORT}/ws/bot")

# 1. 握手
ws.send(json.dumps({
    "type": "req",
    "id": "1",
    "method": "bot.connect",
    "params": {"protocol_version": 1}
}))
res = json.loads(ws.recv())
token = res["payload"]["token"]
bot_uuid = res["payload"]["bot_uuid"]

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
            "event": "chat.event",
            "payload": {
                "run_id": run_id,
                "bcs_group_id": frame["params"]["bcs_group_id"],
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello!"}],
                    "timestamp": int(time.time() * 1000)
                }
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
{"type": "req", "id": "1", "method": "bot.connect", "params": {"protocol_version": 1}}

// ← BCN
{
  "type": "res", "id": "1", "ok": true,
  "payload": {
    "is_new": true,
    "token": "tok-abc123",
    "bot_uuid": "bot-xyz789",
    "protocol_version": 1,
    "min_supported_version": 1,
    "env": { ... }
  }
}
```

#### 重连（已有 token）
```json
// → BCN
{"type": "req", "id": "1", "method": "bot.connect", "params": {"token": "tok-abc123", "protocol_version": 1}}

// ← BCN
{
  "type": "res", "id": "1", "ok": true,
  "payload": {
    "is_new": false,
    "token": "tok-abc123",
    "bot_uuid": "bot-xyz789",
    "protocol_version": 1,
    "min_supported_version": 1,
    "env": { ... }
  }
}
```

#### 协议版本说明

| 字段 | 方向 | 说明 |
| --- | --- | --- |
| `protocol_version`（请求） | 引擎 → BCN | 引擎期望的协议版本（可选，缺省默认当前版本） |
| `protocol_version`（响应） | BCN → 引擎 | 本次连接协商后的协议版本 |
| `min_supported_version` | BCN → 引擎 | BCN 支持的最低协议版本 |
| `deprecation` | BCN → 引擎 | 版本废弃通知（可选，仅当协商版本即将下线时出现） |

版本升级策略：
+ 新增可选字段或可选方法 → 不递增版本号（JSON 天然忽略未知字段）
+ 删除字段、改变语义、新增必填字段 → 递增版本号

引擎收到 `deprecation` 时应打日志提醒开发者升级：
```json
"deprecation": {
  "message": "Protocol v1 will be removed after 2026-06-01. Please upgrade to v2.",
  "sunset_date": "2026-06-01"
}
```

不发送 `protocol_version` 的老引擎照常工作，BCN 默认按 v1 处理。

#### 版本历史

| 版本 | 变更 |
| --- | --- |
| v1 | 初始版本。`session_context` 作为结构化字段下发，引擎自行决定如何呈现给 agent |
| v2 | BCN 在 `message.content` 中自动拼接 Group Context 可读文本头，引擎无需自行格式化 |

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
    "session_key": "sess-123",
    "bcs_group_id": "grp-456",
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

### 5.2 接收 `chat.inject`（静默观察）
`chat.inject` 表示消息仅供观察，bot 不应回复：

```json
{
  "type": "req",
  "id": "inject-001",
  "method": "chat.inject",
  "params": {
    "session_key": "sess-123",
    "bcs_group_id": "grp-456",
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

### 5.3 接收 `chat.abort`（取消处理）
```json
{
  "type": "event",
  "event": "chat.abort",
  "payload": {
    "session_key": "sess-123",
    "run_id": "run-unique-001"
  }
}
```

引擎应取消对应 `run_id` 的处理。

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

## 6. 消息回复
### 6.1 `chat.event` 帧格式
Bot 通过 `chat.event` 事件帧回复消息：

```json
{
  "type": "event",
  "event": "chat.event",
  "payload": {
    "run_id": "run-unique-001",
    "bcs_group_id": "grp-456",
    "state": "final",
    "message": {
      "role": "assistant",
      "content": [{"type": "text", "text": "分析结果：..."}],
      "timestamp": 1710960001000
    }
  },
  "seq": 1
}
```

### 6.2 流式回复（delta → final）
```json
// delta（部分内容）
{"type": "event", "event": "chat.event", "payload": {
  "run_id": "run-001", "bcs_group_id": "grp-456",
  "state": "delta",
  "message": {"role": "assistant", "content": [{"type": "text", "text": "分析"}], "timestamp": 1710960001000}
}, "seq": 1}

// delta
{"type": "event", "event": "chat.event", "payload": {
  "run_id": "run-001", "bcs_group_id": "grp-456",
  "state": "delta",
  "message": {"role": "assistant", "content": [{"type": "text", "text": "结果："}], "timestamp": 1710960001100}
}, "seq": 2}

// final（完整内容）
{"type": "event", "event": "chat.event", "payload": {
  "run_id": "run-001", "bcs_group_id": "grp-456",
  "state": "final",
  "message": {"role": "assistant", "content": [{"type": "text", "text": "分析结果：死锁根因是..."}], "timestamp": 1710960001200},
  "usage": {"input": 100, "output": 250},
  "stop_reason": "complete"
}, "seq": 3}
```

### 6.3 非流式回复（直接 final）
不需要流式输出时，直接发送一个 `state: "final"` 的事件即可。

### 6.4 错误/中止上报
```json
// 错误
{"type": "event", "event": "chat.event", "payload": {
  "run_id": "run-001", "bcs_group_id": "grp-456",
  "state": "error",
  "message": {"role": "assistant", "content": [{"type": "text", "text": "处理失败"}], "timestamp": 1710960002000}
}, "seq": 1}

// 中止
{"type": "event", "event": "chat.event", "payload": {
  "run_id": "run-001", "bcs_group_id": "grp-456",
  "state": "aborted",
  "stop_reason": "aborted"
}, "seq": 1}
```

### 6.5 工具调用上报（可选）
如果引擎支持 tool use 可视化，可以上报工具调用状态：

```json
// tool_call_start
{"type": "event", "event": "chat.event", "payload": {
  "run_id": "run-001", "bcs_group_id": "grp-456",
  "state": "tool_call_start",
  "tool_call_id": "tc-001", "tool_name": "search", "args": {"query": "deadlock"}
}, "seq": 2}

// tool_call_end
{"type": "event", "event": "chat.event", "payload": {
  "run_id": "run-001", "bcs_group_id": "grp-456",
  "state": "tool_call_end",
  "tool_call_id": "tc-001", "tool_name": "search",
  "result": {"results": [...]}, "success": true
}, "seq": 3}
```

### 6.6 `chat.event` 状态机
```plain
delta ──► delta ──► ... ──► final
                              │
delta ──► ... ──► aborted     │
                              │
error ◄───────────────────────┘
```

| State | 含义 | 后续 |
| --- | --- | --- |
| `delta` | 部分内容 | 可继续 delta 或 final |
| `final` | 完整回复 | 终态 |
| `aborted` | 被取消 | 终态 |
| `error` | 处理失败 | 终态 |
| `tool_call_start` | 工具调用开始 | 可选 |
| `tool_call_end` | 工具调用结束 | 可选 |


---

## 7. 结构化路由（可选）
默认情况下，BCN 通过解析消息文本中的 @mention 来决定路由。引擎也可以在 `chat.event(state=final)` 中附加 `routing` 字段，实现更精确的结构化路由。

### 7.1 `routing` 字段格式
```json
{
  "type": "event",
  "event": "chat.event",
  "payload": {
    "run_id": "run-001",
    "bcs_group_id": "grp-456",
    "state": "final",
    "message": {
      "role": "assistant",
      "content": [{"type": "text", "text": "这个问题需要 DBA 来分析"}],
      "timestamp": 1710960001000
    },
    "routing": {
      "responders": [
        {"type": "name", "value": "DBA"}
      ],
      "mode": "required",
      "reason": "需要数据库专家分析死锁",
      "include_self": false
    }
  },
  "seq": 1
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
2. LLM 调用时，引擎捕获参数并缓存（per run_id）
3. 构建 `chat.event(state=final)` 时，将缓存的参数作为 `routing` 字段附加

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
