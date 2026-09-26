# claude-code-gateway WebSocket 协议

> 本文档定义项目 WebSocket 网关的协议规范。
> 基于 `interaction-protocol-design.md` 设计原则，统一所有 HITL 交互到 `interaction.*` 协议。
> 完整示例参见：[websocket-examples.md](./websocket-examples.md)
---

## 快速参考

### 方法一览

| 方法 | 用途 | 关键参数 |
|---|---|---|
| `connect` | 握手认证 | `minProtocol`, `maxProtocol`, `client`, `role`, `scopes` |
| `session.new` | 创建会话 | `sessionKey?`, `cwd?`, `additionalDirectories?`, `model?`, `permissionMode?`, `label?`, `userId?` |
| `sessions.list` | 列出所有会话 | — |
| `sessions.patch` | 更新会话 | `key`, `cwd?`, `additionalDirectories?`, `model?`, `permissionMode?`, `label?` |
| `sessions.delete` | 删除会话 | `key` |
| `sessions.reset` | 清空历史 | `key` |
| `chat.send` | 发起对话 | `sessionKey`, `message`, `cwd?`, `additionalDirectories?`, `model?`, `permissionMode?` (`mode?`), `newSession?`, `contextTurns?`, `idempotencyKey?` |
| `chat.history` | 获取历史 | `sessionKey`, `limit?` |
| `chat.abort` | 中止运行 | `sessionKey`, `runId` |
| `interaction.resolve` | 响应交互 | `interactionId`, `decision`, `answer?`, `selectedOptions?`, `values?`, `meta?` |
| `interaction.pending.list` | 查询待处理交互 | — |
| `health.claude` | 健康检查 | — |
| `providers.available` | 提供商列表 | — |
| `models.list` | 模型列表 | `provider?` |

### 事件一览

| 事件 | 说明 | 定义章节 |
|---|---|---|
| `chat` | 文本流式输出 | [§5.4](#54-chat-事件) |
| `agent` | 结构化事件流（含多个 stream 子类型） | [§7](#7-事件频道) |
| `tick` | 心跳 | — |
| `connect.challenge` | 握手 challenge | [§3.2](#32-connectchallenge-事件) |
| `interaction.requested` | 用户交互请求 | [§6.1](#61-interactionrequested-事件) |
| `interaction.resolved` | 用户交互完成 | [§6.3](#63-interactionresolved-事件) |
| `notification` | 全局通知（toast/snackbar） | [§7.6](#76-notification-事件) |
| `prompt.suggestions` | 后续问题建议 | [§7.7](#77-prompt-suggestions-事件) |

### agent stream 子类型

| stream | 说明 | 定义章节 |
|---|---|---|
| `lifecycle` | run 生命周期事件 (start/end/error)，含 `agentMode` | [§7.8](#78-planphase-事件) |
| `message` | assistant 消息事件 (start/stop) | — |
| `content_block` | 内容块事件 (start/stop) | — |
| `thinking` | thinking 流式输出 | — |
| `tool` | 工具调用事件 (start/update/progress/result/summary) | [§8.2](#82-tool-stream-扩展--progress--summary) |
| `todo` | TodoWrite/TodoRead 事件，全量替换 | [§7.9](#79-todo-事件todowritetodoread) |
| `task` | 子 Agent 任务生命周期事件 | [§7.3](#73-task-事件子-agent-生命周期) |
| `command_output` | 命令输出流 | — |
| `assistant` | 统计信息 (usage/cost/duration) | — |
| `phase` | Agent 模式切换事件 (plan ↔ execute) | [§7.8](#78-planphase-事件) |
| `system` | 系统状态事件 | [§7.4](#74-system-事件) |
| `memory` | 记忆召回事件 | [§7.5](#75-memory-事件) |

---

## 1. 设计原则

### 1.1 单一 Interaction 出口

所有需要用户介入的场景，统一通过以下协议表达：

- 事件：`interaction.requested`
- 事件：`interaction.resolved`
- 方法：`interaction.resolve`

无论底层来源是 `AskUserQuestion`、`Bash`、`Edit`、`Write`，都不得再为其设计新的顶层交互事件或顶层 resolve 方法。

### 1.2 Interaction Kind

```ts
type InteractionKind = 'ask_user' | 'exec' | 'mode_switch';
```

| kind | 用途 |
|---|---|
| `ask_user` | 承载 `AskUserQuestion` 这类需要用户提供回答/选择的交互 |
| `exec` | 承载命令执行、文件修改等"是否允许"的交互 |
| `mode_switch` | 承载模式切换确认（如 plan → execute） |

### 1.3 Decision 值约定

```ts
type InteractionDecision =
  | 'submit'       // ask_user 提交回答
  | 'cancel'       // ask_user 取消
  | 'allow-once'   // exec 仅本次允许
  | 'allow-always' // exec 本次及后续同类操作均允许（会话级）
  | 'deny'         // exec 拒绝
  | 'proceed'      // mode_switch 同意切换
  | 'stay'         // mode_switch 拒绝切换并继续当前模式
```

---

## 2. 基础帧协议

### 2.1 请求帧

```json
{
  "type": "req",
  "id": "client-generated-id",
  "method": "chat.send",
  "params": {}
}
```

### 2.2 响应帧

成功：

```json
{
  "type": "res",
  "id": "client-generated-id",
  "ok": true,
  "payload": {}
}
```

失败：

```json
{
  "type": "res",
  "id": "client-generated-id",
  "ok": false,
  "error": {
    "code": "INVALID_REQUEST",
    "message": "sessionKey required",
    "details": {},
    "retryable": false
  }
}
```

### 2.3 事件帧

```json
{
  "type": "event",
  "event": "agent",
  "payload": {},
  "seq": 42
}
```

---

## 3. 连接协议

### 3.1 连接握手

```
Client                                   Server
  | ── WebSocket connection ─────────────> |
  | <── event: connect.challenge ───────── |
  | ── req: connect ──────────────────────> |
  | <── res: { ok: true } ───────────────── |
```

### 3.2 `connect.challenge` 事件

```json
{
  "type": "event",
  "event": "connect.challenge",
  "payload": {
    "challenge": "random-string",
    "minProtocol": 3,
    "maxProtocol": 3
  }
}
```

### 3.3 `connect` 方法

```json
{
  "type": "req",
  "id": "req-uuid",
  "method": "connect",
  "params": {
    "minProtocol": 3,
    "maxProtocol": 3,
    "client": {
      "id": "debug-page",
      "version": "0.1.0",
      "mode": "operator"
    },
    "role": "operator",
    "scopes": ["operator.read", "operator.write"]
  }
}
```

---

## 4. Session 协议

### 4.1 `session.new` 方法

创建新会话。

```json
{
  "type": "req",
  "id": "req-uuid",
  "method": "session.new",
  "params": {
    "sessionKey": "agent:main:my-session",
    "cwd": "/path/to/project",
    "additionalDirectories": [ "/path/to/shared-lib" ],
    "model": "claude-sonnet-4-20250514",
    "permissionMode": "default",
    "label": "My Session",
    "userId": "user123"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sessionKey` | `string` | 否 | 自定义会话标识 |
| `cwd` | `string` | 否 | 主工作目录（绝对路径，必须存在）。**不传**时 binding.cwd 维持未设置——此时第一次 `chat.send` 必须显式提供 `cwd`，否则会被拒（不再静默回退到 relay 进程目录）。 |
| `additionalDirectories` | `string[]` | 否 | 额外授权目录（每项绝对路径且存在）。 |
| `model` | `string` | 否 | Claude 模型 id。**缺省时使用 relay 默认模型**，解析优先级：环境变量 `RELAY_DEFAULT_MODEL` > `settings.json` 的 `env.ANTHROPIC_MODEL`（目录同 SDK 子进程：`RELAY_CLAUDE_CONFIG_DIR`/`CLAUDE_CONFIG_DIR`，否则 `<RELAY_CLAUDE_HOME\|HOME>/.claude`）> 硬编码兜底 `GLM-5.1`。后续 `chat.send` 不传 `model` 时回退到此值。 |
| `permissionMode` | `string` | 否 | 权限模式（`default` / `acceptEdits` / `bypassPermissions` / `plan`），`mode` 是别名。 |
| `label` | `string` | 否 | 会话显示名（`title` 是别名）。 |
| `userId` | `string` | 否 | 用户标识 |

响应：

```json
{
  "type": "res",
  "id": "req-uuid",
  "ok": true,
  "payload": {
    "key": "agent:main:my-session",
    "label": "My Session",
    "cwd": "/path/to/project",
    "additionalDirectories": [ "/path/to/shared-lib" ],
    "model": "claude-sonnet-4-20250514",
    "permissionMode": "default",
    "createdAt": "2024-01-01T00:00:00.000Z",
    "updatedAt": "2024-01-01T00:00:00.000Z",
    "messageCount": 0,
    "preview": ""
  }
}
```

### 4.2 `sessions.list` 方法

列出所有会话。

```json
{
  "type": "req",
  "id": "req-uuid",
  "method": "sessions.list",
  "params": {}
}
```

响应：

```json
{
  "type": "res",
  "id": "req-uuid",
  "ok": true,
  "payload": {
    "sessions": [
      {
        "key": "agent:main:main",
        "title": "Main Session",
        "cwd": "/path/to/project",
        "createdAt": "2024-01-01T00:00:00.000Z",
        "updatedAt": "2024-01-01T00:00:00.000Z"
      }
    ]
  }
}
```

### 4.3 `sessions.patch` 方法

更新会话配置。所有字段都是可选的——只更新提供的字段，缺省字段不动。

```json
{
  "type": "req",
  "id": "req-uuid",
  "method": "sessions.patch",
  "params": {
    "key": "agent:main:main",
    "cwd": "/new/path",
    "additionalDirectories": [ "/extra/repo-1", "/extra/repo-2" ],
    "model": "claude-sonnet-4-20250514",
    "permissionMode": "acceptEdits",
    "label": "Updated Title"
  }
}
```

| 字段 | 类型 | 行为 |
|---|---|---|
| `cwd` | `string` | 直接覆盖 binding 的主工作目录（绝对路径，必须存在）。**注意**：与 `chat.send` 的"契约 3 守卫"不同，`sessions.patch` **不**检查 `sdkSessionId`，是显式更新接口。但上游 Claude SDK 在 resume 既有 session 时是否真的切换工作目录由 SDK 决定，不能依赖。想干净换项目目录建议 `sessions.delete` + `session.new`，或确保 patch 后第一条消息让 SDK 重新初始化。 |
| `additionalDirectories` | `string[]` | 整体替换。传空数组 = 清空。 |
| `model` | `string` | 整体替换。 |
| `permissionMode` | `string` | 整体替换（`mode` 是别名）。 |
| `label` | `string` | 改显示名。 |

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 | 会话标识 |
| `cwd` | `string` | 否 | 更新工作目录 |
| `model` | `string` | 否 | 更新会话模型 |
| `permissionMode` | `string` | 否 | 更新权限模式。`mode` 为兼容别名 |
| `label` | `string` | 否 | 更新会话标题 |

响应：

```json
{
  "type": "res",
  "id": "req-uuid",
  "ok": true,
  "payload": {
    "key": "agent:main:main",
    "title": "Updated Title",
    "cwd": "/new/path",
    "model": "claude-sonnet-4-20250514",
    "permissionMode": "acceptEdits",
    "updatedAt": "2024-01-01T00:00:00.000Z"
  }
}
```

### 4.4 `sessions.delete` 方法

删除会话。

```json
{
  "type": "req",
  "id": "req-uuid",
  "method": "sessions.delete",
  "params": {
    "key": "agent:main:my-session"
  }
}
```

响应：

```json
{
  "type": "res",
  "id": "req-uuid",
  "ok": true,
  "payload": {
    "ok": true,
    "deleted": true,
    "key": "agent:main:my-session"
  }
}
```

### 4.5 `sessions.reset` 方法

清空会话历史。

```json
{
  "type": "req",
  "id": "req-uuid",
  "method": "sessions.reset",
  "params": {
    "key": "agent:main:main"
  }
}
```

响应：

```json
{
  "type": "res",
  "id": "req-uuid",
  "ok": true,
  "payload": {
    "ok": true,
    "reset": true,
    "key": "agent:main:main"
  }
}
```

---

## 5. Chat 协议

### 5.1 `chat.send` 方法

发起对话。

```json
{
  "type": "req",
  "id": "req-uuid",
  "method": "chat.send",
  "params": {
    "sessionKey": "agent:main:main",
    "message": "Hello, please introduce yourself.",
    "cwd": "/path/to/project",
    "additionalDirectories": [ "/path/to/shared-lib", "/path/to/specs" ],
    "model": "claude-sonnet-4-20250514",
    "permissionMode": "default",
    "mode": "default",
    "newSession": false,
    "contextTurns": 10,
    "idempotencyKey": "client-run-id"
  }
}
```

**工作目录字段**（与 Claude Code CLI / Agent SDK 对齐，类比 VSCode 的"workspace root + Add Folder to Workspace"）：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `cwd` | `string` | 否 | Claude 进程的**主工作目录**（`process.cwd()`）。相对路径以它为根，Bash 工具在这里执行。一个会话只能有一个，由 `session.new` / 第一条 `chat.send` 锁定；上游 SDK session 建立后切换会被忽略并产生警告日志。 |
| `additionalDirectories` | `string[]` | 否 | 工具白名单：除 `cwd` 外**额外授权 Read / Edit / Write / Glob / Grep 访问**的目录列表。透传给 SDK `options.additionalDirectories` / CLI `--add-dir`。**不影响** `cwd`、不影响 Bash 子进程目录、不参与相对路径解析。 |

**校验规则**：

- `cwd` 与 `additionalDirectories` 的每一项都必须是**绝对路径**且**目录已存在**，否则返回 `INVALID_REQUEST` 错误。
- **首次 `chat.send` 必须能解析出 cwd**：要么 `session.new` 时已设置，要么本次请求显式带上。两者都没有时返回 `INVALID_REQUEST`，**不会**静默回退到 relay 进程的 cwd。
- 这两个字段在 binding 上持久化；后续 `chat.send` 缺省时回退到 binding 值。**显式传**（含空数组）则覆盖并持久化，前端可借空数组清空 `additionalDirectories`。
- 推荐先调用 `session.new` 或 `sessions.patch` 完成绑定，后续 `chat.send` 不再重传 —— 减少冗余、避免 cwd 漂移。

响应：

```json
{
  "type": "res",
  "id": "req-uuid",
  "ok": true,
  "payload": {
    "runId": "run-uuid",
    "status": "started",
    "sessionKey": "agent:main:main",
    "mode": "claude-agent-sdk",
    "contextTurns": 10,
    "contextApplied": true
  }
}
```

#### `permissionMode` 参数

`chat.send.params.permissionMode` 用于指定 Claude 会话的权限/执行模式，直接映射到底层 Claude SDK 的 `permissionMode`。`mode` 为 `permissionMode` 的兼容别名，优先使用 `permissionMode`。

| `permissionMode` | 含义 | 适用场景 |
|---|---|---|
| `default` | 标准权限行为 | 常规聊天与普通开发操作 |
| `acceptEdits` | 自动接受文件编辑类操作 | 希望减少编辑确认弹窗 |
| `bypassPermissions` | 跳过权限检查 | 高信任自动化场景；应谨慎使用 |
| `plan` | 规划模式，不实际执行工具 | 先分析、拆解方案、产出计划 |

**会话级持久化与回退**：

- `permissionMode` 和 `model` 会在会话（`SessionBinding`）级别持久化
- `chat.send` 传入 `permissionMode` 时，更新会话持久化值并使用该值
- `chat.send` 未传入 `permissionMode` 时，回退到会话上一次持久化的值；若会话也无记录，则由底层 Claude / SDK 使用其默认模式
- `mode` 参数为兼容别名：若 `permissionMode` 未传但 `mode` 有值，则使用 `mode` 的值

补充说明：

- 当前服务**不再把 `execute` 作为正式公开的 `permissionMode` 值**；如需"正常执行并允许交互"，建议使用 `default`
- 从当前集成与实测结果看，某些交互型工具（尤其 `AskUserQuestion`）在 `default` / `plan` 下可触发 `interaction.requested`；若需要严格无确认或自动确认行为，请改用 `acceptEdits` 或 `bypassPermissions`，并理解其风险
- 响应 payload 里的 `mode` 字段表示**服务当前采用的桥接实现**（如 `claude-agent-sdk` / `claude-cli-stream-json`），不是请求里的 `permissionMode` 回显

### 5.2 `chat.history` 方法

获取历史消息。

```json
{
  "type": "req",
  "id": "req-uuid",
  "method": "chat.history",
  "params": {
    "sessionKey": "agent:main:main",
    "limit": 20
  }
}
```

响应：

```json
{
  "type": "res",
  "id": "req-uuid",
  "ok": true,
  "payload": {
    "messages": [
      {
        "id": "msg-uuid",
        "role": "user|assistant",
        "content": [{ "type": "text", "text": "消息内容" }],
        "timestamp": "2024-01-01T00:00:00.000Z",
        "metadata": { "runId": "run-uuid" }
      }
    ]
  }
}
```

### 5.3 `chat.abort` 方法

中止当前运行。

```json
{
  "type": "req",
  "id": "req-uuid",
  "method": "chat.abort",
  "params": {
    "sessionKey": "agent:main:main",
    "runId": "run-uuid"
  }
}
```

响应：

```json
{
  "type": "res",
  "id": "req-uuid",
  "ok": true,
  "payload": {
    "ok": true,
    "aborted": true,
    "runIds": ["run-uuid"]
  }
}
```

### 5.4 `chat` 事件

文本流式输出事件。

**delta 事件**（增量文本）：

```json
{
  "type": "event",
  "event": "chat",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:uuid",
    "seq": 5,
    "state": "delta",
    "delta": "Hello!",
    "message": {
      "role": "assistant",
      "content": [
        { "type": "text", "text": "Hello! I'm Claude." }
      ],
      "timestamp": 1710000000000
    }
  },
  "seq": 5
}
```

**final 事件**（运行结束）：

```json
{
  "type": "event",
  "event": "chat",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:uuid",
    "seq": 42,
    "state": "final",
    "stopReason": "end_turn",
    "message": {
      "role": "assistant",
      "content": [
        { "type": "text", "text": "Hello! I'm Claude. How can I help you today?" }
      ],
      "timestamp": 1710000001000
    }
  },
  "seq": 42
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `state` | `'delta' \| 'final' \| 'error' \| 'aborted'` | 事件状态 |
| `delta` | `string?` | 仅 `delta` 事件存在，本次增量文本 |
| `message` | `object?` | 累积全量消息，`content[0].text` 为到当前为止的完整文本 |
| `stopReason` | `string?` | 仅 `final` 事件存在，如 `end_turn`、`max_tokens` |
| `errorMessage` | `string?` | 仅 `error` 事件存在 |

---

## 6. Interaction 协议

所有 HITL 人机交互统一走本协议。参见 [§1.1 单一 Interaction 出口](#11-单一-interaction-出口)。

### 6.1 `interaction.requested` 事件

当 agent 需要用户决策时，发送此事件。

```json
{
  "type": "event",
  "event": "interaction.requested",
  "payload": {
    "interactionId": "int:uuid",
    "runId": "run-uuid",
    "sessionKey": "session:abc:user:default",
    "kind": "ask_user|exec|mode_switch",
    "title": "Claude needs your input",
    "description": "Please answer the following question(s)",
    "prompt": "请选择一个选项",
    "subject": {
      "type": "tool|command|file|mode",
      "toolName": "AskUserQuestion|Bash|Edit|Write",
      "toolCallId": "toolu:001",
      "command": "npm test",
      "cwd": "/repo",
      "filePath": "/repo/src/index.ts",
      "fromMode": "plan",
      "toMode": "execute"
    },
    "questions": [
      {
        "question": "请选择一个数据库类型：",
        "header": "数据库选择",
        "multiSelect": false,
        "options": [
          { "label": "PostgreSQL", "description": "关系型数据库" },
          { "label": "MongoDB", "description": "文档数据库" }
        ]
      }
    ],
    "options": [
      { "value": "proceed", "label": "Continue to execution", "recommended": true },
      { "value": "stay", "label": "Stay in planning" }
    ],
    "inputSchema": {
      "type": "none|text|choices|form",
      "multiSelect": false
    },
    "uiHints": {
      "variant": "question|warning|plan",
      "severity": "info|warning|danger"
    },
    "agentContext": {
      "parentToolUseId": "toolu-agent-001",
      "taskId": "task-abc",
      "agentType": "general-purpose",
      "agentName": "Run test suite"
    },
    "createdAtMs": 1710000000000,
    "expiresAtMs": 1710000055000
  },
  "seq": 9
}
```

### 6.2 `interaction.resolve` 方法

客户端响应 interaction 请求。

```json
{
  "type": "req",
  "id": "req-uuid",
  "method": "interaction.resolve",
  "params": {
    "interactionId": "int:uuid",
    "decision": "submit|cancel|allow-once|allow-always|deny|proceed|stay",
    "answer": "用户回答文本",
    "selectedOptions": ["选项 1", "选项 2"],
    "values": {
      "question1": "answer1"
    },
    "meta": {}
  }
}
```

响应：

```json
{
  "type": "res",
  "id": "req-uuid",
  "ok": true,
  "payload": {
    "accepted": true,
    "interactionId": "int:uuid",
    "decision": "submit",
    "kind": "ask_user"
  }
}
```

### 6.3 `interaction.resolved` 事件

Interaction 处理完成后发送。

```json
{
  "type": "event",
  "event": "interaction.resolved",
  "payload": {
    "interactionId": "int:uuid",
    "runId": "run-uuid",
    "sessionKey": "session:abc:user:default",
    "kind": "ask_user|exec|mode_switch",
    "phase": "answered|allowed|denied|cancelled|expired",
    "decision": "submit|allow-once|allow-always|deny|cancel|proceed|stay",
    "answer": "用户回答",
    "selectedOptions": ["选项 1"],
    "resolvedBy": "operator",
    "resolvedAtMs": 1710000003000
  },
  "seq": 10
}
```

### 6.4 phase 映射规则

| kind | decision | phase |
|---|---|---|
| `ask_user` | `submit` | `answered` |
| `ask_user` | `cancel` | `cancelled` |
| `exec` | `allow-once` | `allowed` |
| `exec` | `allow-always` | `allowed` |
| `exec` | `deny` | `denied` |
| `mode_switch` | `proceed` | `allowed` |
| `mode_switch` | `stay` | `denied` |
| any | timeout | `expired` |

### 6.5 `interaction.pending.list` 方法

查询当前连接关联的待处理交互列表。用于页面刷新或重连后恢复 pending interactions。

```json
{
  "type": "req",
  "id": "req-uuid",
  "method": "interaction.pending.list",
  "params": {}
}
```

响应：

```json
{
  "type": "res",
  "id": "req-uuid",
  "ok": true,
  "payload": {
    "interactions": [
      {
        "interactionId": "int:uuid",
        "runId": "run-uuid",
        "sessionKey": "session:abc:user:default",
        "kind": "ask_user|exec|mode_switch",
        "title": "Claude needs your input",
        "subject": {
          "type": "tool|command|file|mode",
          "toolName": "AskUserQuestion|Bash|Edit|Write"
        },
        "createdAtMs": 1710000000000,
        "expiresAtMs": 1710000055000
      }
    ]
  }
}
```

### 6.6 重连后 Pending Interaction 恢复

当客户端重连（WebSocket 重新连接）后，服务端应 replay 所有与该 session 关联的 pending `interaction.requested` 事件。

时序：

```
Client                                   Server
  | ── WebSocket connection ─────────────> |
  | <── event: connect.challenge ───────── |
  | ── req: connect ──────────────────────> |
  | <── res: { ok: true } ───────────────── |
  | <── [如果有待处理 interaction] ───────── |
  | <── event: interaction.requested (replay) |
  | <── event: interaction.requested (replay) |
```

服务端应在以下时机触发 replay：
1. 客户端调用 `connect` 方法成功认证后
2. 如果存在与该连接关联的 session 的 pending interactions，则依次发送 `interaction.requested` 事件

---

## 7. 事件频道

所有结构化事件通过 `agent` 事件帧发送，通过 `payload.stream` 字段区分子类型。

### 7.1 顶层事件总览

| event | 说明 |
|---|---|
| `chat` | 文本流式输出（[§5.4](#54-chat-事件)） |
| `agent` | 结构化事件流（本节以下各小节） |
| `tick` | 心跳 |
| `connect.challenge` | 握手 challenge（[§3.2](#32-connectchallenge-事件)） |
| `interaction.requested` | 用户交互请求（[§6.1](#61-interactionrequested-事件)） |
| `interaction.resolved` | 用户交互完成（[§6.3](#63-interactionresolved-事件)） |
| `notification` | 全局通知（[§7.6](#76-notification-事件)） |
| `prompt.suggestions` | 后续问题建议（[§7.7](#77-prompt-suggestions-事件)） |

### 7.2 agent stream 分类

| stream | 说明 |
|---|---|
| `lifecycle` | run 生命周期事件 (start/end/error)，包含 `agentMode` 字段 |
| `message` | assistant 消息事件 (start/stop) |
| `content_block` | 内容块事件 (start/stop) |
| `thinking` | thinking 流式输出 |
| `tool` | 工具调用事件 (start/update/progress/result/summary) |
| `todo` | TodoWrite/TodoRead 事件 — 持久化任务面板数据，全量替换 |
| `task` | 子 Agent 任务生命周期事件 (含 taskType/workflowName/prompt/lastToolName/summary) |
| `command_output` | 命令输出流 |
| `assistant` | 统计信息 (usage/cost/duration) |
| `phase` | Agent 模式切换事件 (plan ↔ execute) |
| `system` | 系统状态事件 (compaction/retry/rate_limit/compact_boundary/files_persisted) |
| `memory` | 记忆召回事件 (recall) |

### 7.3 Task 事件（子 Agent 生命周期）

子 Agent 任务事件通过专用 `task` stream 发送，与 `tool` stream 分离。

| data.type | 说明 |
|---|---|
| `task_started` | 子 Agent 任务启动 |
| `task_progress` | 任务进展更新 |
| `task_notification` | 任务完成/失败通知，包含 `usage` 和 `outputFile` |
| `task_updated` | 任务字段增量更新，通过 `patch` 对象传递变更字段 |

Task 事件补充字段：

| 字段 | 类型 | 出现在 | 说明 |
|---|---|---|---|
| `taskType` | `string?` | `task_started` | SDK `task_type` — 'agent' / 'workflow' 等 |
| `workflowName` | `string?` | `task_started` | SDK `workflow_name` |
| `prompt` | `string?` | `task_started` | subagent 收到的原始 prompt |
| `lastToolName` | `string?` | `task_progress` | 最近执行的工具名 |
| `summary` | `string?` | `task_progress` | 进度摘要 |

完整示例参见：[websocket-examples.md](./websocket-examples.md)

### 7.4 System 事件

系统状态事件通过 `agent` event 的 `system` stream 发送。

| data.type | 说明 |
|---|---|
| `status_change` | 系统状态变化 (compacting/requesting/null)，含 `compactResult`/`compactError` |
| `api_retry` | API 重试，含 `attempt`/`maxRetries`/`retryDelayMs`/`errorStatus`/`error` |
| `rate_limit` | 限额警告，含 `status`/`rateLimitType`/`utilization`/`resetsAt` |
| `compact_boundary` | 上下文压缩边界，含 `trigger`/`preTokens`/`postTokens`/`durationMs` |
| `files_persisted` | 文件持久化确认，含 `files`/`failed`/`processedAt` |

status_change 示例：

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:abc",
    "stream": "system",
    "data": {
      "type": "status_change",
      "status": "compacting",
      "compactResult": null,
      "compactError": null
    }
  }
}
```

### 7.5 Memory 事件

记忆召回事件通过 `agent` event 的 `memory` stream 发送。

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:abc",
    "stream": "memory",
    "data": {
      "type": "recall",
      "mode": "select",
      "memories": [
        { "path": "user_role.md", "scope": "personal", "content": null },
        { "path": "project_auth.md", "scope": "team", "content": "Auth rewrite driven by compliance" }
      ]
    }
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `mode` | `'select' \| 'synthesize'` | 召回方式 |
| `memories` | `MemoryItem[]` | 召回的记忆列表 |
| `memories[].path` | `string` | 记忆文件路径 |
| `memories[].scope` | `'personal' \| 'team'` | 来源作用域 |
| `memories[].content` | `string?` | 召回内容（synthesize 模式下） |

### 7.6 Notification 事件

全局 toast 通知，独立顶层事件 `notification`。

```json
{
  "type": "event",
  "event": "notification",
  "payload": {
    "key": "notif-uuid",
    "text": "File saved successfully",
    "priority": "medium",
    "color": "green",
    "timeoutMs": 5000,
    "sessionKey": "session:abc",
    "runId": "run-uuid"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | `string` | 是 | 唯一标识，用于去重和关闭 |
| `text` | `string` | 是 | 显示文本 |
| `priority` | `'low' \| 'medium' \| 'high' \| 'immediate'` | 是 | 前端决定展示行为 |
| `color` | `string?` | 否 | 颜色提示 |
| `timeoutMs` | `number?` | 否 | 自动关闭时间 |
| `sessionKey` | `string?` | 否 | 关联的 session |
| `runId` | `string?` | 否 | 关联的 run |

| priority | 建议行为 |
|---|---|
| `low` | 状态栏小字，或不显示 |
| `medium` | Toast 气泡，自动消失 |
| `high` | Toast 气泡，需手动关闭 |
| `immediate` | 模态或固定位置强提醒 |

### 7.7 Prompt Suggestions 事件

后续问题建议，独立顶层事件 `prompt.suggestions`。

```json
{
  "type": "event",
  "event": "prompt.suggestions",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:abc",
    "suggestions": [
      { "text": "Run the test suite to verify" }
    ]
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `runId` | `string` | 是 | 产生建议的 run |
| `sessionKey` | `string` | 是 | 所属 session |
| `suggestions` | `{ text: string }[]` | 是 | 建议列表（单条或多条） |

前端行为：收到 `prompt.suggestions` → 追加到当前 suggestions 列表；用户点击 → 作为 `chat.send` 的 `message` 发送；新一轮 `chat.send` → 清除旧的 suggestions。

### 7.8 Plan/Phase 事件

Agent 运行模式（plan ↔ execute）切换通过 `agent.phase` 事件透传。

| agentMode | 说明 |
|---|---|
| `plan` | 规划模式，不实际执行工具 |
| `execute` | 执行模式，正常执行工具 |
| `auto` | 自动模式（默认） |

完整示例参见：[websocket-examples.md](./websocket-examples.md)

### 7.9 Todo 事件（TodoWrite/TodoRead）

TodoWrite 和 TodoRead 工具调用通过专用 `todo` stream 发送。**全量替换** 语义：每次事件携带完整的 todo 列表，客户端应替换整个列表而非追加。

| 字段 | 类型 | 说明 |
|---|---|---|
| `content` | `string` | 任务描述 |
| `status` | `'pending' \| 'in_progress' \| 'completed'` | 任务状态 |
| `activeForm` | `string` | 进行中显示文本（通常等同于 content） |

#### 判断 Todo 面板何时可关闭

Todo 面板的生命周期与 **run** 绑定：

1. **面板出现**：收到第一个 `todo` 事件且 `todos` 非空时，显示 Todo 面板
2. **面板更新**：后续 `todo` 事件携带全量列表，直接替换面板内容
3. **面板关闭**：收到 `agent.lifecycle(phase='end')` 事件时，关闭或折叠面板
4. **跨 run 保持**：Todo 列表仅在当前 run 内有效，新 run 开始时应清空旧列表

完整示例参见：[websocket-examples.md](./websocket-examples.md)

---

## 8. AgentContext 与 Subagent

### 8.1 AgentContext — Subagent 归属

所有 tool stream 和 interaction 事件可携带 `agentContext` 字段标识 subagent 来源：

| 字段 | 类型 | 说明 |
|---|---|---|
| `parentToolUseId` | `string?` | 主键。非空 = 属于 subagent，值 = 启动该 subagent 的 Agent tool ID |
| `taskId` | `string?` | 关联的 task 事件 ID |
| `agentId` | `string?` | SDK agent_id（仅 permission/hook 场景可用） |
| `agentType` | `string?` | agent 类型名（如 "general-purpose"、"code-reviewer"） |
| `agentName` | `string?` | 用户自定义名称（Agent tool 的 description） |

前端树构建规则：`agentContext.parentToolUseId` 非空 → 归属对应 Task 节点；无 `agentContext` → 主进程。

### 8.2 Tool Stream 扩展 — progress / summary

tool stream 新增 phase：

| phase | 说明 |
|---|---|
| `progress` | 工具执行进度（含 `elapsedSeconds`），对应 SDK `SDKToolProgressMessage` |
| `summary` | 工具摘要（含 `precedingToolUseIds` + `summary`），对应 SDK `SDKToolUseSummaryMessage` |

progress 事件示例：

```json
{
  "data": {
    "type": "progress",
    "toolCallId": "toolu:001",
    "toolName": "Bash",
    "progress": { "elapsedSeconds": 12 },
    "agentContext": { "parentToolUseId": "toolu-agent-001", "taskId": "task-abc" }
  }
}
```

summary 事件示例：

```json
{
  "data": {
    "type": "summary",
    "precedingToolUseIds": ["toolu:001", "toolu:002"],
    "summary": "Ran 2 bash commands — all tests passed"
  }
}
```

### 8.3 SubagentTools 聚合

Task 工具的 `result` phase 包含 `subagentTools` 聚合字段，提供完整快照便于一次性渲染：

```json
{
  "type": "result",
  "toolCallId": "toolu-agent-001",
  "toolName": "Task",
  "output": "...",
  "subagentTools": [
    {
      "toolId": "toolu-inner-001",
      "toolName": "Read",
      "toolInput": { "file_path": "/repo/src/index.ts" },
      "toolResult": { "content": "file content...", "isError": false },
      "timestamp": 1710000001000
    }
  ]
}
```

---

## 9. 工具映射参考

Claude Agent SDK 工具到 Interaction 的映射关系：

| 工具 | kind | subject.type | decision |
|---|---|---|---|
| AskUserQuestion | `ask_user` | `tool` | `submit` / `cancel` |
| ExitPlanMode | `mode_switch` | `mode` | `proceed` / `stay` |
| Bash | `exec` | `command` | `allow-once` / `allow-always` / `deny` |
| Edit | `exec` | `file` | `allow-once` / `allow-always` / `deny` |
| Write | `exec` | `file` | `allow-once` / `allow-always` / `deny` |
| Read | `exec` | `file` | `allow-once` / `allow-always` / `deny` |

> 注：ExitPlanMode 已在服务端统一映射为 `interaction.requested(kind='mode_switch')`，不再使用独立的 mode_transition 协议。
> 完整事件示例参见：[websocket-examples.md](./websocket-examples.md)

---

## 10. Health & Meta 协议

### 10.1 `health.claude` 方法

Claude 服务健康检查。

```json
{
  "type": "req",
  "id": "req-uuid",
  "method": "health.claude",
  "params": {}
}
```

响应：

```json
{
  "type": "res",
  "id": "req-uuid",
  "ok": true,
  "payload": {
    "supportsStreamJson": true,
    "provider": "anthropic"
  }
}
```

### 10.2 `providers.available` 方法

获取可用的 AI 提供商。

### 10.3 `models.list` 方法

获取模型列表。

---

## 11. 错误码

| code | 说明 |
|---|---|
| `INVALID_REQUEST` | 参数缺失或不合法 |
| `NOT_FOUND` | 资源不存在 |
| `EXPIRED` | 资源已过期 |
| `CONFLICT` | 资源冲突 |
| `UNSUPPORTED_DECISION` | 该 kind 不支持该 decision |
| `INTERNAL_ERROR` | 服务端内部错误 |

---

## A. 协议完善程度评估（附录）

> 本节为协议实现状态追踪，不属于协议规范本身。

### A.1 会话管理

| 功能 | 状态 | 备注 |
|---|---|---|
| 创建会话 | ✅ 已实现 | `session.new` |
| 列出会话 | ✅ 已实现 | `sessions.list` |
| 更新会话 | ✅ 已实现 | `sessions.patch` |
| 删除会话 | ✅ 已实现 | `sessions.delete` |
| 清空历史 | ✅ 已实现 | `sessions.reset` |
| 会话状态查询 | ⚠️ 需补充 | 缺少获取单个会话详情的接口 |
| 会话元数据 | ⚠️ 需补充 | 缺少获取会话消息数、最后活跃时间等 |

**建议补充**：
- `sessions.get` - 获取单个会话详情
- 返回值增加 `messageCount`, `lastActiveAt` 等字段

### A.2 消息管理

| 功能 | 状态 | 备注 |
|---|---|---|
| 发送消息 | ✅ 已实现 | `chat.send` |
| 获取历史 | ✅ 已实现 | `chat.history` |
| 中止运行 | ✅ 已实现 | `chat.abort` |
| 消息搜索 | ❌ 缺失 | 缺少按关键词搜索历史消息 |
| 消息编辑 | ❌ 缺失 | 缺少编辑已发送消息 |
| 消息删除 | ❌ 缺失 | 缺少删除单条消息 |
| 分页查询 | ⚠️ 需补充 | `limit` 但无 `offset`/`before` 分页 |

**建议补充**：
- `chat.search` - 搜索历史消息
- `chat.delete` - 删除单条消息
- `chat.history` 增加 `offset`/`before` 分页参数

### A.3 Interaction 协议

| 功能 | 状态 | 备注 |
|---|---|---|
| interaction.requested | ✅ 已实现 | |
| interaction.resolved | ✅ 已实现 | |
| interaction.resolve | ✅ 已实现 | |
| mode_transition 统一 | ✅ 已完成 | 统一使用 `interaction.requested(kind='mode_switch')` |
| pending 恢复 | ❌ 缺失 | 缺少重连后恢复 pending interaction |

**建议补充**：
- `interaction.pending.list` - 查询 pending interactions
- reconnect 后自动 replay pending interaction