# claude-code-gateway WebSocket 协议示例

> 本文档是 `websocket-protocol.md` 的配套示例集，提供完整的消息帧示例和时序图。

---

## 目录

- [1. 连接与握手](#1-连接与握手)
- [2. Session 管理](#2-session-管理)
- [3. Chat 对话](#3-chat-对话)
- [4. Interaction 交互](#4-interaction-交互)
  - [4.1 Exec 工具（Bash）](#41-exec-工具bash)
  - [4.2 Exec 工具（Edit）](#42-exec-工具edit)
  - [4.3 Exec 工具（Write）](#43-exec-工具write)
  - [4.4 Exec 工具（Read）](#44-exec-工具read)
  - [4.5 AskUserQuestion](#45-askuserquestion)
  - [4.6 Mode Switch（ExitPlanMode）](#46-mode-switchexitplanmode)
- [5. Agent 事件流](#5-agent-事件流)
  - [5.1 Task 事件](#51-task-事件)
  - [5.2 System 事件](#52-system-事件)
  - [5.3 Memory 事件](#53-memory-事件)
  - [5.4 Notification 事件](#54-notification-事件)
  - [5.5 Prompt Suggestions 事件](#55-prompt-suggestions-事件)
  - [5.6 Plan/Phase 事件](#56-planphase-事件)
  - [5.7 Todo 事件](#57-todo-事件)
  - [5.8 Tool Stream 扩展（progress / summary）](#58-tool-stream-扩展progress--summary)
- [6. 完整时序图](#6-完整时序图)
- [7. 错误码示例](#7-错误码示例)

---

## 1. 连接与握手

```
Client                                   Server
  | ── WebSocket connection ─────────────> |
  | <── event: connect.challenge ───────── |
  | ── req: connect ──────────────────────> |
  | <── res: { ok: true } ───────────────── |
```

**connect.challenge 事件**

```json
{
  "type": "event",
  "event": "connect.challenge",
  "payload": {
    "challenge": "abc123-random-string",
    "minProtocol": 3,
    "maxProtocol": 3
  }
}
```

**connect 请求**

```json
{
  "type": "req",
  "id": "req-connect-001",
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

**connect 响应**

```json
{
  "type": "res",
  "id": "req-connect-001",
  "ok": true,
  "payload": {
    "protocol": 3,
    "serverId": "relay-abc"
  }
}
```

---

## 2. Session 管理

### 2.1 创建会话

```json
{
  "type": "req",
  "id": "req-session-001",
  "method": "session.new",
  "params": {
    "sessionKey": "agent:main:my-session",
    "cwd": "/path/to/project",
    "model": "claude-sonnet-4-20250514",
    "permissionMode": "default",
    "label": "My Session"
  }
}
```

响应：

```json
{
  "type": "res",
  "id": "req-session-001",
  "ok": true,
  "payload": {
    "key": "agent:main:my-session",
    "label": "My Session",
    "cwd": "/path/to/project",
    "model": "claude-sonnet-4-20250514",
    "permissionMode": "default",
    "createdAt": "2024-01-01T00:00:00.000Z",
    "updatedAt": "2024-01-01T00:00:00.000Z",
    "messageCount": 0,
    "preview": ""
  }
}
```

### 2.2 列出会话

```json
{
  "type": "req",
  "id": "req-session-002",
  "method": "sessions.list",
  "params": {}
}
```

### 2.3 更新会话

```json
{
  "type": "req",
  "id": "req-session-003",
  "method": "sessions.patch",
  "params": {
    "key": "agent:main:my-session",
    "permissionMode": "acceptEdits",
    "label": "Updated Session"
  }
}
```

---

## 3. Chat 对话

### 3.1 发送消息

```json
{
  "type": "req",
  "id": "req-chat-001",
  "method": "chat.send",
  "params": {
    "sessionKey": "agent:main:my-session",
    "message": "Hello, please introduce yourself.",
    "cwd": "/path/to/project",
    "model": "claude-sonnet-4-20250514",
    "permissionMode": "default"
  }
}
```

响应：

```json
{
  "type": "res",
  "id": "req-chat-001",
  "ok": true,
  "payload": {
    "runId": "run-abc-123",
    "status": "started",
    "sessionKey": "agent:main:my-session",
    "mode": "claude-agent-sdk",
    "contextTurns": 8,
    "contextApplied": true
  }
}
```

### 3.2 Chat delta 事件（流式文本）

```json
{
  "type": "event",
  "event": "chat",
  "payload": {
    "runId": "run-abc-123",
    "sessionKey": "agent:main:my-session",
    "seq": 5,
    "state": "delta",
    "delta": "Hello!",
    "message": {
      "role": "assistant",
      "content": [
        { "type": "text", "text": "Hello!" }
      ],
      "timestamp": 1710000000000
    }
  },
  "seq": 5
}
```

### 3.3 Chat final 事件

```json
{
  "type": "event",
  "event": "chat",
  "payload": {
    "runId": "run-abc-123",
    "sessionKey": "agent:main:my-session",
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

---

## 4. Interaction 交互

### 4.1 Exec 工具（Bash）

**agent.tool 事件（工具调用开始）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "tool",
    "data": {
      "type": "start",
      "toolName": "Bash",
      "toolCallId": "toolu:001",
      "input": {
        "command": "npm install",
        "description": "Install project dependencies"
      }
    }
  },
  "seq": 10
}
```

**interaction.requested 事件（请求授权）**

```json
{
  "type": "event",
  "event": "interaction.requested",
  "payload": {
    "interactionId": "int:uuid",
    "runId": "run-uuid",
    "sessionKey": "agent:main:main",
    "kind": "exec",
    "title": "需要执行命令",
    "description": "Claude 请求执行以下命令",
    "prompt": "执行命令：npm install",
    "subject": {
      "type": "command",
      "toolName": "Bash",
      "toolCallId": "toolu:001",
      "command": "npm install",
      "cwd": "/repo",
      "description": "Install project dependencies"
    },
    "options": [
      { "value": "allow-once", "label": "仅本次允许", "recommended": true },
      { "value": "allow-always", "label": "本次及后续同类操作均允许" },
      { "value": "deny", "label": "拒绝" }
    ],
    "inputSchema": { "type": "none" },
    "uiHints": { "variant": "warning", "severity": "info" },
    "createdAtMs": 1710000000000,
    "expiresAtMs": 1710000055000
  },
  "seq": 11
}
```

**交互解析**

```json
{
  "type": "req",
  "id": "req-resolve-001",
  "method": "interaction.resolve",
  "params": {
    "interactionId": "int:uuid",
    "decision": "allow-once"
  }
}
```

响应：

```json
{
  "type": "res",
  "id": "req-resolve-001",
  "ok": true,
  "payload": {
    "accepted": true,
    "interactionId": "int:uuid",
    "decision": "allow-once",
    "kind": "exec"
  }
}
```

**interaction.resolved 事件**

```json
{
  "type": "event",
  "event": "interaction.resolved",
  "payload": {
    "interactionId": "int:uuid",
    "runId": "run-uuid",
    "sessionKey": "agent:main:main",
    "kind": "exec",
    "phase": "allowed",
    "decision": "allow-once",
    "resolvedBy": "operator",
    "resolvedAtMs": 1710000003000
  },
  "seq": 12
}
```

**agent.tool 事件（工具调用结果）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "tool",
    "data": {
      "type": "result",
      "toolName": "Bash",
      "toolCallId": "toolu:001",
      "output": {
        "command": "npm install",
        "description": "Install project dependencies"
      }
    }
  },
  "seq": 13
}
```

### 4.2 Exec 工具（Edit）

**agent.tool 事件（工具调用开始）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "tool",
    "data": {
      "type": "start",
      "toolName": "Edit",
      "toolCallId": "toolu:002",
      "input": {
        "file_path": "/repo/src/index.ts",
        "old_string": "const VERSION = '1.0.0';",
        "new_string": "const VERSION = '2.0.0';"
      }
    }
  },
  "seq": 20
}
```

**interaction.requested 事件（请求授权）**

```json
{
  "type": "event",
  "event": "interaction.requested",
  "payload": {
    "interactionId": "int:uuid",
    "runId": "run-uuid",
    "sessionKey": "agent:main:main",
    "kind": "exec",
    "title": "需要编辑文件",
    "description": "Claude 请求修改以下文件",
    "prompt": "修改文件：/repo/src/index.ts",
    "subject": {
      "type": "file",
      "toolName": "Edit",
      "toolCallId": "toolu:002",
      "filePath": "/repo/src/index.ts",
      "old_string": "const VERSION = '1.0.0';",
      "new_string": "const VERSION = '2.0.0';"
    },
    "options": [
      { "value": "allow-once", "label": "仅本次允许", "recommended": true },
      { "value": "allow-always", "label": "本次及后续同类操作均允许" },
      { "value": "deny", "label": "拒绝" }
    ],
    "inputSchema": { "type": "none" },
    "uiHints": { "variant": "warning", "severity": "warning" },
    "createdAtMs": 1710000000000,
    "expiresAtMs": 1710000055000
  },
  "seq": 21
}
```

### 4.3 Exec 工具（Write）

**agent.tool 事件（工具调用开始）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "tool",
    "data": {
      "type": "start",
      "toolName": "Write",
      "toolCallId": "toolu:003",
      "input": {
        "file_path": "/repo/src/new-file.ts",
        "content": "export const hello = 'world';\n"
      }
    }
  },
  "seq": 30
}
```

**interaction.requested 事件（请求授权）**

```json
{
  "type": "event",
  "event": "interaction.requested",
  "payload": {
    "interactionId": "int:uuid",
    "runId": "run-uuid",
    "sessionKey": "agent:main:main",
    "kind": "exec",
    "title": "需要写入文件",
    "description": "Claude 请求创建或覆盖以下文件",
    "prompt": "写入文件：/repo/src/new-file.ts",
    "subject": {
      "type": "file",
      "toolName": "Write",
      "toolCallId": "toolu:003",
      "filePath": "/repo/src/new-file.ts",
      "operation": "create"
    },
    "options": [
      { "value": "allow-once", "label": "仅本次允许", "recommended": true },
      { "value": "allow-always", "label": "本次及后续同类操作均允许" },
      { "value": "deny", "label": "拒绝" }
    ],
    "inputSchema": { "type": "none" },
    "uiHints": { "variant": "warning", "severity": "danger" },
    "createdAtMs": 1710000000000,
    "expiresAtMs": 1710000055000
  },
  "seq": 31
}
```

### 4.4 Exec 工具（Read）

**agent.tool 事件（工具调用开始）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "tool",
    "data": {
      "type": "start",
      "toolName": "Read",
      "toolCallId": "toolu:004",
      "input": {
        "file_path": "/repo/src/index.ts"
      }
    }
  },
  "seq": 40
}
```

**agent.command_output 事件（实际执行结果）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "command_output",
    "data": {
      "toolCallId": "toolu:004",
      "phase": "end",
      "output": "export const VERSION = '1.0.0';\n",
      "exitCode": 0
    }
  },
  "seq": 42
}
```

### 4.5 AskUserQuestion

**agent.tool 事件（工具调用开始）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "tool",
    "data": {
      "type": "start",
      "toolName": "AskUserQuestion",
      "toolCallId": "toolu:005",
      "input": {
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
        ]
      },
      "requiresInteraction": true,
      "interaction": {
        "interactionId": "int:uuid",
        "kind": "ask_user",
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
          { "value": "PostgreSQL", "label": "PostgreSQL", "description": "关系型数据库" },
          { "value": "MongoDB", "label": "MongoDB", "description": "文档数据库" }
        ],
        "inputSchema": {
          "type": "choices",
          "multiSelect": false
        },
        "uiHints": {
          "variant": "question",
          "severity": "info"
        }
      }
    }
  },
  "seq": 50
}
```

**interaction.requested 事件（独立事件）**

```json
{
  "type": "event",
  "event": "interaction.requested",
  "payload": {
    "interactionId": "int:uuid",
    "runId": "run-uuid",
    "sessionKey": "agent:main:main",
    "kind": "ask_user",
    "title": "Claude needs your input",
    "description": "Please answer the following question(s)",
    "prompt": "请选择一个数据库类型：",
    "subject": {
      "type": "tool",
      "toolName": "AskUserQuestion",
      "toolCallId": "toolu:005"
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
      { "value": "PostgreSQL", "label": "PostgreSQL", "description": "关系型数据库" },
      { "value": "MongoDB", "label": "MongoDB", "description": "文档数据库" }
    ],
    "inputSchema": { "type": "choices", "multiSelect": false },
    "uiHints": { "variant": "question", "severity": "info" },
    "createdAtMs": 1710000000000,
    "expiresAtMs": 1710000055000
  },
  "seq": 51
}
```

**交互解析**

```json
{
  "type": "req",
  "id": "req-resolve-002",
  "method": "interaction.resolve",
  "params": {
    "interactionId": "int:uuid",
    "decision": "submit",
    "selectedOptions": ["PostgreSQL"]
  }
}
```

**agent.tool 事件（工具调用结果，含答案）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "tool",
    "data": {
      "type": "result",
      "toolName": "AskUserQuestion",
      "toolCallId": "toolu:005",
      "output": {
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
        ]
      },
      "requiresInteraction": true,
      "interaction": {
        "interactionId": "int:uuid",
        "kind": "ask_user",
        "questions": ["..."],
        "answer": "PostgreSQL",
        "selectedOptions": ["PostgreSQL"]
      }
    }
  },
  "seq": 52
}
```

### 4.6 Mode Switch（ExitPlanMode）

**interaction.requested 事件（模式切换）**

```json
{
  "type": "event",
  "event": "interaction.requested",
  "payload": {
    "interactionId": "int:uuid-mode",
    "runId": "run-uuid",
    "sessionKey": "agent:main:main",
    "kind": "mode_switch",
    "title": "切换到执行模式",
    "description": "Agent 已完成规划，请求切换到执行模式",
    "prompt": "是否允许切换到执行模式？",
    "subject": {
      "type": "mode",
      "toolName": "ExitPlanMode",
      "toolCallId": "toolu:006",
      "fromMode": "plan",
      "toMode": "execute"
    },
    "options": [
      { "value": "proceed", "label": "Continue to execution", "recommended": true },
      { "value": "stay", "label": "Stay in planning" }
    ],
    "inputSchema": { "type": "none" },
    "uiHints": { "variant": "plan", "severity": "info" },
    "createdAtMs": 1710000000000,
    "expiresAtMs": 1710000055000
  },
  "seq": 60
}
```

**交互解析 — 同意切换**

```json
{
  "type": "req",
  "id": "req-resolve-003",
  "method": "interaction.resolve",
  "params": {
    "interactionId": "int:uuid-mode",
    "decision": "proceed"
  }
}
```

**interaction.resolved 事件**

```json
{
  "type": "event",
  "event": "interaction.resolved",
  "payload": {
    "interactionId": "int:uuid-mode",
    "runId": "run-uuid",
    "sessionKey": "agent:main:main",
    "kind": "mode_switch",
    "phase": "allowed",
    "decision": "proceed",
    "resolvedBy": "operator",
    "resolvedAtMs": 1710000003000
  },
  "seq": 61
}
```

**交互解析 — 拒绝切换**

```json
{
  "type": "req",
  "id": "req-resolve-004",
  "method": "interaction.resolve",
  "params": {
    "interactionId": "int:uuid-mode",
    "decision": "stay"
  }
}
```

```json
{
  "type": "event",
  "event": "interaction.resolved",
  "payload": {
    "interactionId": "int:uuid-mode",
    "runId": "run-uuid",
    "sessionKey": "agent:main:main",
    "kind": "mode_switch",
    "phase": "denied",
    "decision": "stay",
    "resolvedBy": "operator",
    "resolvedAtMs": 1710000003000
  },
  "seq": 62
}
```

---

## 5. Agent 事件流

### 5.1 Task 事件

**task_started（任务开始）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "task",
    "data": {
      "type": "task_started",
      "taskId": "task-001",
      "toolUseId": "toolu:010",
      "status": "running",
      "description": "Running tests",
      "taskType": "agent",
      "workflowName": null,
      "prompt": "Run the test suite and report results"
    }
  },
  "seq": 60
}
```

**task_progress（任务进行中）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "task",
    "data": {
      "type": "task_progress",
      "taskId": "task-001",
      "toolUseId": "toolu:010",
      "status": "running",
      "description": "Running tests",
      "lastToolName": "Bash",
      "summary": "Executing test suite..."
    }
  },
  "seq": 61
}
```

**task_notification（任务完成/失败）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "task",
    "data": {
      "type": "task_notification",
      "taskId": "task-001",
      "toolUseId": "toolu:010",
      "status": "completed",
      "description": "Running tests",
      "summary": "All 42 tests passed",
      "outputFile": "/repo/test-results.xml",
      "usage": {
        "totalTokens": 1500,
        "toolUses": 5,
        "durationMs": 12000
      }
    }
  },
  "seq": 62
}
```

**task_updated（任务状态更新）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "task",
    "data": {
      "type": "task_updated",
      "taskId": "task-001",
      "patch": {
        "status": "completed",
        "summary": "All 42 tests passed",
        "endTime": 1710000012000,
        "totalPausedMs": 0
      }
    }
  },
  "seq": 63
}
```

### 5.2 System 事件

**status_change — 上下文压缩**

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

**status_change — 压缩完成**

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
      "status": null,
      "compactResult": {
        "preTokens": 180000,
        "postTokens": 60000,
        "durationMs": 2500
      },
      "compactError": null
    }
  }
}
```

**api_retry — API 重试**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:abc",
    "stream": "system",
    "data": {
      "type": "api_retry",
      "attempt": 2,
      "maxRetries": 3,
      "retryDelayMs": 1000,
      "errorStatus": 429,
      "error": "Rate limit exceeded"
    }
  }
}
```

**rate_limit — 限额警告**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:abc",
    "stream": "system",
    "data": {
      "type": "rate_limit",
      "status": "warning",
      "rateLimitType": "token",
      "utilization": 0.85,
      "resetsAt": "2024-01-01T00:05:00.000Z"
    }
  }
}
```

**compact_boundary — 上下文压缩边界**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:abc",
    "stream": "system",
    "data": {
      "type": "compact_boundary",
      "trigger": "auto",
      "preTokens": 180000,
      "postTokens": 60000,
      "durationMs": 2500
    }
  }
}
```

**files_persisted — 文件持久化确认**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:abc",
    "stream": "system",
    "data": {
      "type": "files_persisted",
      "files": [
        { "path": "/repo/src/index.ts", "status": "written" },
        { "path": "/repo/src/utils.ts", "status": "written" }
      ],
      "failed": [],
      "processedAt": 1710000003000
    }
  }
}
```

### 5.3 Memory 事件

**recall — 记忆召回**

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

**recall — synthesize 模式**

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
      "mode": "synthesize",
      "memories": [
        { "path": "project_architecture.md", "scope": "team", "content": "The system uses a layered architecture with WebSocket gateway..." }
      ]
    }
  }
}
```

### 5.4 Notification 事件

**medium 优先级通知**

```json
{
  "type": "event",
  "event": "notification",
  "payload": {
    "key": "notif-file-saved",
    "text": "File saved successfully",
    "priority": "medium",
    "color": "green",
    "timeoutMs": 5000,
    "sessionKey": "session:abc",
    "runId": "run-uuid"
  }
}
```

**high 优先级通知**

```json
{
  "type": "event",
  "event": "notification",
  "payload": {
    "key": "notif-rate-limit",
    "text": "Rate limit approaching — slowing down requests",
    "priority": "high",
    "color": "orange",
    "timeoutMs": null,
    "sessionKey": "session:abc",
    "runId": "run-uuid"
  }
}
```

**immediate 优先级通知**

```json
{
  "type": "event",
  "event": "notification",
  "payload": {
    "key": "notif-error",
    "text": "API key invalid — please check configuration",
    "priority": "immediate",
    "color": "red",
    "timeoutMs": null,
    "sessionKey": "session:abc",
    "runId": "run-uuid"
  }
}
```

### 5.5 Prompt Suggestions 事件

```json
{
  "type": "event",
  "event": "prompt.suggestions",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:abc",
    "suggestions": [
      { "text": "Run the test suite to verify" },
      { "text": "Add error handling for edge cases" },
      { "text": "Update the documentation" }
    ]
  }
}
```

### 5.6 Plan/Phase 事件

**Phase 变更事件**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "phase",
    "data": {
      "phase": "changed",
      "fromPhase": "plan",
      "toPhase": "execute",
      "timestamp": 1710000000000
    }
  },
  "seq": 70
}
```

**Lifecycle 包含 agentMode**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "lifecycle",
    "data": {
      "phase": "start",
      "agentMode": "plan",
      "sessionId": "session-uuid",
      "cwd": "/repo",
      "tools": ["Read", "Edit", "Write", "Bash"]
    }
  },
  "seq": 71
}
```

### 5.7 Todo 事件

**TodoWrite 事件（列表更新）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "todo",
    "data": {
      "todos": [
        { "content": "分析代码结构", "status": "completed", "activeForm": "分析代码结构" },
        { "content": "实现新功能", "status": "in_progress", "activeForm": "实现新功能" },
        { "content": "编写测试", "status": "pending", "activeForm": "编写测试" }
      ],
      "toolCallId": "toolu:020"
    }
  },
  "seq": 80
}
```

**TodoRead 事件（空更新）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "todo",
    "data": {
      "todos": [],
      "toolCallId": "toolu:021"
    }
  },
  "seq": 81
}
```

### 5.8 Tool Stream 扩展（progress / summary）

**progress 事件**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "tool",
    "data": {
      "type": "progress",
      "toolCallId": "toolu:001",
      "toolName": "Bash",
      "progress": { "elapsedSeconds": 12 },
      "agentContext": {
        "parentToolUseId": "toolu-agent-001",
        "taskId": "task-abc"
      }
    }
  },
  "seq": 90
}
```

**summary 事件**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "tool",
    "data": {
      "type": "summary",
      "precedingToolUseIds": ["toolu:001", "toolu:002"],
      "summary": "Ran 2 bash commands — all tests passed"
    }
  },
  "seq": 91
}
```

**SubagentTools 聚合（Task result 中）**

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "stream": "tool",
    "data": {
      "type": "result",
      "toolCallId": "toolu-agent-001",
      "toolName": "Task",
      "output": "Task completed successfully",
      "subagentTools": [
        {
          "toolId": "toolu-inner-001",
          "toolName": "Read",
          "toolInput": { "file_path": "/repo/src/index.ts" },
          "toolResult": { "content": "file content...", "isError": false },
          "timestamp": 1710000001000
        },
        {
          "toolId": "toolu-inner-002",
          "toolName": "Bash",
          "toolInput": { "command": "npm test" },
          "toolResult": { "content": "All tests passed", "isError": false },
          "timestamp": 1710000002000
        }
      ]
    }
  },
  "seq": 92
}
```

---

## 6. 完整时序图

### 6.1 连接 + 会话创建 + 对话完整流程

```
Client                                   Server
  | ── WebSocket connection ─────────────> |
  | <── event: connect.challenge ───────── |
  | ── req: connect ──────────────────────> |
  | <── res: { ok: true } ───────────────── |
  |                                        |
  | ── req: session.new ─────────────────> |
  | <── res: { key, cwd, model } ───────── |
  |                                        |
  | ── req: chat.send ────────────────────> |
  | <── res: { runId, status: started } ── |
  | <── event: agent (lifecycle: start) ── |
  | <── event: chat (state: delta) ─────── |
  | <── event: chat (state: delta) ─────── |
  | <── event: agent (tool: start) ─────── |
  | <── event: interaction.requested ───── |
  | ── req: interaction.resolve ──────────> |
  | <── res: { accepted: true } ─────────── |
  | <── event: interaction.resolved ─────── |
  | <── event: agent (tool: result) ─────── |
  | <── event: chat (state: final) ──────── |
  | <── event: agent (lifecycle: end) ───── |
```

### 6.2 Exec 工具交互时序

```
Client                                   Server
  | <── event: agent (tool: start) ─────── |
  |     [前端在消息流展示工具调用中]
  | <── event: interaction.requested ───── |
  |     [前端渲染授权面板]
  | ── req: interaction.resolve ──────────> |
  |     (decision: allow-once/allow-always/deny)
  | <── res: { accepted: true } ─────────── |
  | <── event: interaction.resolved ─────── |
  |     (phase: allowed/denied)
  | <── event: agent (tool: result) ─────── |
  |     [前端在消息流展示工具执行结果]
```

### 6.3 AskUserQuestion 交互时序

```
Client                                   Server
  | <── event: agent (tool: start) ─────── |
  |     [前端展示 AskUserQuestion 工具]
  | <── event: interaction.requested ───── |
  |     (kind: ask_user)
  |     [前端渲染问题面板]
  | ── req: interaction.resolve ──────────> |
  |     (decision: submit, selectedOptions: [...])
  | <── res: { accepted: true } ─────────── |
  | <── event: interaction.resolved ─────── |
  |     (phase: answered)
  | <── event: agent (tool: result) ─────── |
  |     [前端展示回答结果]
```

### 6.4 Mode Switch 交互时序

```
Client                                   Server
  | <── event: agent (phase: changed) ──── |
  |     (fromPhase: plan, toPhase: execute)
  | <── event: interaction.requested ───── |
  |     (kind: mode_switch)
  |     [前端渲染模式切换确认面板]
  | ── req: interaction.resolve ──────────> |
  |     (decision: proceed / stay)
  | <── res: { accepted: true } ─────────── |
  | <── event: interaction.resolved ─────── |
  |     (phase: allowed / denied)
```

### 6.5 重连后 Pending Interaction 恢复

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

### 6.6 Subagent (Task) 完整时序

```
Client                                   Server
  | <── event: agent (tool: start) ─────── |
  |     (toolName: Task/Agent)
  | <── event: agent (task: task_started)  |
  |     [前端展开 Task 面板]
  | <── event: agent (task: task_progress) |
  | <── event: agent (tool: start) ─────── |
  |     (agentContext: { parentToolUseId })
  |     [归属到对应 Task 节点下]
  | <── event: agent (tool: progress) ──── |
  |     (agentContext: { parentToolUseId })
  | <── event: agent (tool: result) ────── |
  |     (agentContext: { parentToolUseId })
  | <── event: agent (task: task_notification) |
  |     (status: completed, usage: {...})
  | <── event: agent (tool: result) ────── |
  |     (toolName: Task, subagentTools: [...])
```

### 6.7 Todo 面板生命周期

```
run-1:
  lifecycle(start)           → 显示空面板（可选）
  todo [pending, pending]    → 显示 2 项
  todo [in_progress, pending] → 更新：第 1 项进行中
  todo [completed, in_progress] → 更新：第 1 项完成，第 2 项进行中
  todo [completed, completed]   → 更新：全部完成（可折叠）
  lifecycle(end)             → 关闭/折叠面板

run-2:
  lifecycle(start)           → 清空旧列表
  todo [pending, pending, pending] → 显示新列表
  ...
```

---

## 7. 错误码示例

| code | 说明 | 示例场景 |
|---|---|---|
| `INVALID_REQUEST` | 参数缺失或不合法 | 缺少 `sessionKey` |
| `NOT_FOUND` | 资源不存在 | 会话不存在 |
| `EXPIRED` | 资源已过期 | interaction 超时 |
| `CONFLICT` | 资源冲突 | 重复提交 interaction |
| `UNSUPPORTED_DECISION` | 该 kind 不支持该 decision | `ask_user` 使用 `allow-once` |
| `INTERNAL_ERROR` | 服务端内部错误 | 未知异常 |

**错误响应示例**

```json
{
  "type": "res",
  "id": "req-uuid",
  "ok": false,
  "error": {
    "code": "INVALID_REQUEST",
    "message": "sessionKey required",
    "details": {},
    "retryable": false
  }
}
```