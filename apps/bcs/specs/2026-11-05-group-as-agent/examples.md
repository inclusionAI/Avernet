# BCN Group A2A 完整请求示例

更新：2026-11-12。依据：[Spec](spec.md) · [方案评审](design-review.html)。可展开版本见 [内部讨论页](discussion.html)。

本文对应本期主从群和自定义协作群。所有请求使用 A2A 1.0.1 的 HTTP+JSON 绑定，线上版本为 1.0；操作与错误定义参见 [A2A 规范](https://a2a-protocol.org/v1.0.1/specification/)。

## 请求约定

- 示例地址、ID 与返回数据按实际部署替换；先读取 Card 声明的接口。
- 身份凭据、caller/user 命名及结构待联合设计，示例不固定认证头或来源结构。
- 请求中的 ROLE_USER 表示客户端发送方向，即使客户端本身也是 Agent。
- SendMessage 返回 task 包装；GetTask 和 CancelTask 返回 Task 本体。
- 同一章节内按描述衔接；取消、超时等明确标注的替代分支各自独立。
- 扩展规范与 Schema 采用 GitHub 地址，文件名暂为候选；自动获取 Schema 要使用原始 JSON 地址或仓库解析方式。当前草稿见 [Schema](call-metadata.schema.json)。

```bash
export BCN_BASE_URL='https://bcn.example.com'
```

## 1. 读取 Group Card

先读取 Card，再按 supportedInterfaces 选择绑定、版本和基地址。本期主从群输出文本，状态机可输出 JSON。认证字段及安全声明待联合设计；示例只固定已确定的协议字段。扩展 URI 和 Schema 是候选仓库路径，正式文件将在合同定稿后发布。

<a id="ex-2-1"></a>

### 1.1 读取主从群 Card

部署地址和 documentationUrl 随实际环境生成；本期不声明 message-dedup/v1。

请求：

```bash
curl -i -X GET "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/agent-card.json" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json'
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "name": "供应商比较协作群",
  "description": "比较供应商并输出采购建议。",
  "version": "1.0.0",
  "supportedInterfaces": [
    {
      "url": "https://bcn.example.com/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93",
      "protocolBinding": "HTTP+JSON",
      "protocolVersion": "1.0"
    }
  ],
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "extensions": [
      {
        "uri": "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.md",
        "description": "公开过程消息配置；来源字段待定",
        "required": false,
        "params": {
          "metadataSchema": {
            "$ref": "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.schema.json"
          }
        }
      }
    ]
  },
  "defaultInputModes": [
    "text/plain",
    "application/json"
  ],
  "defaultOutputModes": [
    "text/plain"
  ],
  "skills": [
    {
      "id": "supplier-comparison",
      "name": "供应商比较",
      "description": "根据预算、交付周期和供应商资料给出比较结论。",
      "tags": [
        "procurement"
      ],
      "examples": [
        "比较两家供应商，预算为 80 万。"
      ]
    }
  ],
  "documentationUrl": "https://bcn.example.com/docs/group-a2a"
}
```

## 2. 首次调用与查询产物

首轮省略 contextId 和 taskId。returnImmediately=true 返回已持久化的当前 Task，可能是 SUBMITTED、WORKING 或已完成；接入方不能假定初态固定。

<a id="ex-3-1"></a>

### 2.1 新建会话并立即取得 Task

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-001",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "比较两家供应商，预算为 80 万。"
      }
    ]
  },
  "configuration": {
    "returnImmediately": true
  }
}
JSON
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "task": {
    "id": "task-001",
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
    "status": {
      "state": "TASK_STATE_SUBMITTED"
    }
  }
}
```

<a id="ex-4-1"></a>

### 2.2 查询完成后的 Task 和 Artifact

GetTask 返回 Task 本体。Artifact 来自独立 artifacts 表，终态与结果在同一事务提交；后续轮次不会覆盖本轮结果。

请求：

```bash
curl -i -X GET "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/tasks/task-001" \
  --get \
  --data-urlencode 'historyLength=0' \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json'
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "id": "task-001",
  "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
  "status": {
    "state": "TASK_STATE_COMPLETED"
  },
  "artifacts": [
    {
      "artifactId": "result-001",
      "name": "供应商比较结论",
      "parts": [
        {
          "text": "建议选择供应商 B，总价 72 万，交付周期 6 周。",
          "mediaType": "text/plain"
        }
      ]
    }
  ]
}
```

## 3. 同一 context 继续下一轮

下例发生在 task-001 已完成且 context 已释放之后。新的消息 ID 创建 task-002；contextId 保持不变。本期不支持运行中的轮内追加。

<a id="ex-5-1"></a>

### 3.1 前轮完成后提交下一轮

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-002",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "预算改为 50 万。"
      }
    ],
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41"
  },
  "configuration": {
    "returnImmediately": true
  }
}
JSON
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "task": {
    "id": "task-002",
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
    "status": {
      "state": "TASK_STATE_SUBMITTED"
    }
  }
}
```

<a id="ex-5-2"></a>

### 3.2 context 忙碌时提交新消息

不创建新 Task，不预留 msg-003，也不排队。待 context 空闲后可重新提交同一请求。

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-003",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "再比较交付周期。"
      }
    ],
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41"
  },
  "configuration": {
    "returnImmediately": true
  }
}
JSON
```

返回：

```http
HTTP/1.1 409 Conflict
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 409,
    "status": "ABORTED",
    "message": "当前会话仍有活动任务，请等待完成后再提交。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "CONTEXT_BUSY",
        "domain": "bcn.example.com",
        "metadata": {
          "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
          "activeTaskId": "task-002"
        }
      }
    ]
  }
}
```

<a id="ex-5-3"></a>

### 3.3 向 working Task 追加消息

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-004",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "补充一个要求。"
      }
    ],
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
    "taskId": "task-002"
  },
  "configuration": {
    "returnImmediately": true
  }
}
JSON
```

返回：

```http
HTTP/1.1 400 Bad Request
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 400,
    "status": "FAILED_PRECONDITION",
    "message": "本期不支持向运行中的 Task 追加消息。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "UNSUPPORTED_OPERATION",
        "domain": "a2a-protocol.org"
      }
    ]
  }
}
```

## 4. 执行完成后同步返回

returnImmediately 默认 false。服务端等待到终态或中断态后返回；本期没有 input_required。单次等待建议最多 60 秒，等待超时不取消执行。长任务宜选择立即返回或 SSE。

<a id="ex-6-1"></a>

### 4.1 等待完成并取得结果

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-sync-001",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "给出采购建议。"
      }
    ]
  },
  "configuration": {
    "returnImmediately": false
  }
}
JSON
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "task": {
    "id": "task-sync-001",
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:c84a902e",
    "status": {
      "state": "TASK_STATE_COMPLETED"
    },
    "artifacts": [
      {
        "artifactId": "result-sync-001",
        "parts": [
          {
            "text": "建议优先比较总价与交付周期。",
            "mediaType": "text/plain"
          }
        ]
      }
    ]
  }
}
```

<a id="ex-6-2"></a>

### 4.2 达到服务端等待上限

这是 BCN 等待边界。网关自身 504 可能没有 JSON 或 Task ID；不能把它理解成请求未受理，也不能自动改 returnImmediately 后重发。

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-timeout-001",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "生成完整供应商评估。"
      }
    ]
  },
  "configuration": {
    "returnImmediately": false
  }
}
JSON
```

返回：

```http
HTTP/1.1 504 Gateway Timeout
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 504,
    "status": "DEADLINE_EXCEEDED",
    "message": "等待已超时，任务继续执行，请按 taskId 查询。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "SEND_WAIT_TIMEOUT",
        "domain": "bcn.example.com",
        "metadata": {
          "taskId": "task-timeout-001",
          "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:cc19e240"
        }
      }
    ]
  }
}
```

## 5. 流式调用与公开过程消息

可选 metadata.includeProgress 控制公开阶段消息，默认 false；状态、错误和最终结果始终可获取。使用该字段必须激活扩展。来源字段与凭据合同待定，不放进执行示例。

<a id="ex-7-1"></a>

### 5.1 启用公开过程消息并流式接收

先 Task，过程事件排空后发送最终 Artifact，再发送终态并关闭。lastChunk 表示产物片段结束，不代表 Task 已结束。空闲时每 15 秒发送注释心跳，心跳不延长任务期限。

请求：

```bash
curl -N -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:stream" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: text/event-stream' \
  -H 'Content-Type: application/a2a+json' \
  -H 'A2A-Extensions: https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.md' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-stream-001",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "比较两家供应商。"
      }
    ]
  },
  "metadata": {
    "includeProgress": true
  }
}
JSON
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream
A2A-Version: 1.0
A2A-Extensions: https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.md

data: {"task":{"id":"task-stream-001","contextId":"7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:3f2a5d87","status":{"state":"TASK_STATE_SUBMITTED"}}}

data: {"statusUpdate":{"taskId":"task-stream-001","contextId":"7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:3f2a5d87","status":{"state":"TASK_STATE_WORKING","message":{"messageId":"task-stream-001-status-working","role":"ROLE_AGENT","contextId":"7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:3f2a5d87","taskId":"task-stream-001","parts":[{"text":"已完成资料整理，正在比较方案。"}]}}}}

: keep-alive

data: {"artifactUpdate":{"taskId":"task-stream-001","contextId":"7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:3f2a5d87","artifact":{"artifactId":"result-stream-001","name":"比较结论","parts":[{"text":"建议选择供应商 B。","mediaType":"text/plain"}]},"append":false,"lastChunk":true}}

data: {"statusUpdate":{"taskId":"task-stream-001","contextId":"7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:3f2a5d87","status":{"state":"TASK_STATE_COMPLETED"}}}
```

## 6. 断线后订阅与终态查询

SSE 断线不取消任务。先查询状态；尚未终态可 subscribe，已终态用 GetTask。重订阅从当前快照开始，不承诺重放断线期间的每条事件。

<a id="ex-8-1"></a>

### 6.1 任务仍在执行时重新订阅

这是上一节流式调用在 WORKING 时断线的分支。过程消息选择已在首次受理固定；订阅不改变配置。

请求：

```bash
curl -N -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/tasks/task-stream-001:subscribe" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: text/event-stream' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{}
JSON
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream
A2A-Version: 1.0

data: {"task":{"id":"task-stream-001","contextId":"7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:3f2a5d87","status":{"state":"TASK_STATE_WORKING"}}}

: keep-alive

data: {"artifactUpdate":{"taskId":"task-stream-001","contextId":"7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:3f2a5d87","artifact":{"artifactId":"result-stream-001","name":"比较结论","parts":[{"text":"建议选择供应商 B。","mediaType":"text/plain"}]},"append":false,"lastChunk":true}}

data: {"statusUpdate":{"taskId":"task-stream-001","contextId":"7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:3f2a5d87","status":{"state":"TASK_STATE_COMPLETED"}}}
```

<a id="ex-8-2"></a>

### 6.2 已终态的 Task 不再建立新订阅

错误发生在 SSE 建立前，返回 JSON。

请求：

```bash
curl -N -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/tasks/task-stream-001:subscribe" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: text/event-stream' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{}
JSON
```

返回：

```http
HTTP/1.1 400 Bad Request
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 400,
    "status": "FAILED_PRECONDITION",
    "message": "任务已结束，请使用 GetTask 查询结果。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "UNSUPPORTED_OPERATION",
        "domain": "a2a-protocol.org"
      }
    ]
  }
}
```

## 7. 列出当前 context 的 Task

列表先做资源归属过滤，再分页；按状态时间倒序。默认 pageSize=50，最大 100；historyLength=0 不带历史，includeArtifacts=true 带产物。

<a id="ex-9-1"></a>

### 7.1 按 context 查询列表

本页已包含全部结果，nextPageToken 为空；有下一页时按服务端返回的不透明 token 继续查询。

请求：

```bash
curl -i -X GET "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/tasks" \
  --get \
  --data-urlencode 'contextId=7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41' \
  --data-urlencode 'pageSize=10' \
  --data-urlencode 'historyLength=0' \
  --data-urlencode 'includeArtifacts=true' \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json'
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "tasks": [
    {
      "id": "task-002",
      "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
      "status": {
        "state": "TASK_STATE_WORKING",
        "timestamp": "2026-11-12T06:02:00Z"
      }
    },
    {
      "id": "task-001",
      "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
      "status": {
        "state": "TASK_STATE_COMPLETED",
        "timestamp": "2026-11-12T06:01:00Z"
      },
      "artifacts": [
        {
          "artifactId": "result-001",
          "name": "供应商比较结论",
          "parts": [
            {
              "text": "建议选择供应商 B，总价 72 万，交付周期 6 周。",
              "mediaType": "text/plain"
            }
          ]
        }
      ]
    }
  ],
  "totalSize": 2,
  "pageSize": 10,
  "nextPageToken": ""
}
```

## 8. 状态机多轮与 JSON 产物

状态机 Card 的 defaultOutputModes 在此场景声明 application/json。文本和至多一个对象 data 进入本轮 Run.input；下轮以新 Run 从入口执行，传入最初目标、本轮要求及上一轮产物。

<a id="ex-10-1"></a>

### 8.1 以文本和结构化数据启动状态机

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/a62d4e18-5b37-4c90-a8f2-19e7c60d4b85/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-sm-001",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "按预算比较供应商。"
      },
      {
        "data": {
          "budget": 800000,
          "currency": "CNY"
        },
        "mediaType": "application/json"
      }
    ]
  },
  "configuration": {
    "returnImmediately": true,
    "acceptedOutputModes": [
      "application/json"
    ]
  }
}
JSON
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "task": {
    "id": "task-sm-001",
    "contextId": "a62d4e18-5b37-4c90-a8f2-19e7c60d4b85:2c70b6de",
    "status": {
      "state": "TASK_STATE_SUBMITTED"
    }
  }
}
```

<a id="ex-10-2"></a>

### 8.2 读取持久化的 JSON Artifact

data 保留 JSON 类型，不编码成文本。artifacts 表也能表达非文本 Part；本期 Group 对外只开放 Card 声明的文本和 JSON。

请求：

```bash
curl -i -X GET "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/a62d4e18-5b37-4c90-a8f2-19e7c60d4b85/tasks/task-sm-001" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json'
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "id": "task-sm-001",
  "contextId": "a62d4e18-5b37-4c90-a8f2-19e7c60d4b85:2c70b6de",
  "status": {
    "state": "TASK_STATE_COMPLETED"
  },
  "artifacts": [
    {
      "artifactId": "result-sm-001",
      "name": "采购建议",
      "parts": [
        {
          "data": {
            "supplier": "B",
            "total": 720000,
            "currency": "CNY",
            "deliveryWeeks": 6
          },
          "mediaType": "application/json"
        }
      ]
    }
  ]
}
```

<a id="ex-10-3"></a>

### 8.3 前轮完成后以新 Run 解释结果

新 Task 不覆盖 task-sm-001 的 Artifact。解释与写入分支由流程定义；执行节点必须实际收到上一轮结果和本轮请求。

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/a62d4e18-5b37-4c90-a8f2-19e7c60d4b85/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-sm-002",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "为什么推荐 B？请解释上一轮结论。"
      }
    ],
    "contextId": "a62d4e18-5b37-4c90-a8f2-19e7c60d4b85:2c70b6de"
  },
  "configuration": {
    "returnImmediately": true,
    "acceptedOutputModes": [
      "application/json"
    ]
  }
}
JSON
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "task": {
    "id": "task-sm-002",
    "contextId": "a62d4e18-5b37-4c90-a8f2-19e7c60d4b85:2c70b6de",
    "status": {
      "state": "TASK_STATE_SUBMITTED"
    }
  }
}
```

## 9. 取消、停止确认与 context 隔离

取消保存意图并停止整轮派发。确认全部底层工作停止、收尾完成后才提交 CANCELED；确认前仍可返回 WORKING。下例是 task-002 执行期间的几种替代分支。

<a id="ex-11-1"></a>

### 9.1 取消已确认停止的整轮执行

CancelTask 返回 Task 本体。主从群包括 manager、Worker 及回送；状态机包括活动/等待节点、重试及已派发工作。

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/tasks/task-002:cancel" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{}
JSON
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "id": "task-002",
  "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
  "status": {
    "state": "TASK_STATE_CANCELED"
  }
}
```

<a id="ex-11-2"></a>

### 9.2 停止仍待确认

停止期间重复取消复用原意图，不延长截止时间。

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/tasks/task-002:cancel" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{}
JSON
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "id": "task-002",
  "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
  "status": {
    "state": "TASK_STATE_WORKING",
    "message": {
      "messageId": "task-002-status-working",
      "role": "ROLE_AGENT",
      "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
      "taskId": "task-002",
      "parts": [
        {
          "text": "已收到取消请求，正在确认执行停止。"
        }
      ]
    }
  }
}
```

<a id="ex-11-3"></a>

### 9.3 收尾期限后仍无法确认停止

收尾建议最多 60 秒；超时提交 FAILED 并隔离 context。失败任务可无 Artifact。后台继续有界核实，确认停止后恢复 context，原 Task 保持 FAILED。

请求：

```bash
curl -i -X GET "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/tasks/task-002" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json'
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "id": "task-002",
  "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
  "status": {
    "state": "TASK_STATE_FAILED",
    "message": {
      "messageId": "task-002-status-failed",
      "role": "ROLE_AGENT",
      "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
      "taskId": "task-002",
      "parts": [
        {
          "text": "未能确认执行停止，当前会话暂不可继续，请等待恢复确认。"
        }
      ]
    }
  }
}
```

<a id="ex-11-4"></a>

### 9.4 隔离期间提交新一轮

不能仅凭租约过期或没有新消息就开放下一轮。

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-after-stop",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "继续比较供应商。"
      }
    ],
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41"
  },
  "configuration": {
    "returnImmediately": true
  }
}
JSON
```

返回：

```http
HTTP/1.1 400 Bad Request
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 400,
    "status": "FAILED_PRECONDITION",
    "message": "当前会话暂停接收新任务，请等待恢复确认。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "CONTEXT_UNAVAILABLE",
        "domain": "bcn.example.com",
        "metadata": {
          "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
          "taskId": "task-002"
        }
      }
    ]
  }
}
```

<a id="ex-11-5"></a>

### 9.5 重复取消已经 CANCELED 的 Task

这是 ex-11-1 成功之后的分支。

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/tasks/task-002:cancel" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{}
JSON
```

返回：

```http
HTTP/1.1 400 Bad Request
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 400,
    "status": "FAILED_PRECONDITION",
    "message": "任务已结束，无法取消。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "TASK_NOT_CANCELABLE",
        "domain": "a2a-protocol.org"
      }
    ]
  }
}
```

## 10. 去重、冲突与结果清理

内部去重范围为 tenant（如有）＋认证 caller_principal＋group_id＋messageId。指纹包含全部 configuration、metadata 和激活扩展集合。Group 本期不发布已有 message-dedup/v1，BCS 出站客户端仍遵守只有协商扩展才自动重试发送的规则。以下展示服务端收到重发时的处理。

<a id="ex-12-1"></a>

### 10.1 相同语义请求读取原 Task

与 ex-3-1 的请求完全相同，首次省略的 contextId 仍省略；不会创建新 Session 或重复派发。

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-001",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "比较两家供应商，预算为 80 万。"
      }
    ]
  },
  "configuration": {
    "returnImmediately": true
  }
}
JSON
```

返回：

```http
HTTP/1.1 200 OK
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "task": {
    "id": "task-001",
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
    "status": {
      "state": "TASK_STATE_COMPLETED"
    },
    "artifacts": [
      {
        "artifactId": "result-001",
        "name": "供应商比较结论",
        "parts": [
          {
            "text": "建议选择供应商 B，总价 72 万，交付周期 6 周。",
            "mediaType": "text/plain"
          }
        ]
      }
    ]
  }
}
```

<a id="ex-12-2"></a>

### 10.2 同 messageId 修改返回配置

returnImmediately、historyLength、acceptedOutputModes 和 includeProgress 的变化都进入指纹。

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-001",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "比较两家供应商，预算为 80 万。"
      }
    ]
  },
  "configuration": {
    "returnImmediately": false
  }
}
JSON
```

返回：

```http
HTTP/1.1 400 Bad Request
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 400,
    "status": "INVALID_ARGUMENT",
    "message": "该 messageId 已用于不同的请求。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "MESSAGE_ID_CONFLICT",
        "domain": "bcn.example.com"
      }
    ]
  }
}
```

<a id="ex-12-3"></a>

### 10.3 结果已实际清理后的相同重发

建议保留终态结果与 Artifact 7 天；隔离中的恢复记录暂缓清理。墓碑随 Group 调用存储保留，不让旧 ID 再次执行。

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-001",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "比较两家供应商，预算为 80 万。"
      }
    ]
  },
  "configuration": {
    "returnImmediately": true
  }
}
JSON
```

返回：

```http
HTTP/1.1 404 Not Found
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 404,
    "status": "NOT_FOUND",
    "message": "原任务结果已清理，请使用新的 messageId 发起新的业务请求。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "MESSAGE_RESULT_EXPIRED",
        "domain": "bcn.example.com"
      }
    ]
  }
}
```

## 11. 受理前错误

以下错误都不创建新 Task。来源字段和凭据传递待定，因此 CONTEXT_SOURCE_CONFLICT 的具体输入示例在来源合同确认后补充。BCN ErrorInfo.domain 暂用 bcn.example.com，最终取值在实现评审确定。

<a id="ex-13-1"></a>

### 11.1 扩展字段缺少激活头

补齐 A2A-Extensions 后可重发，不会留下错误绑定的 context。

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-no-header",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "比较两家供应商。"
      }
    ]
  },
  "metadata": {
    "includeProgress": true
  }
}
JSON
```

返回：

```http
HTTP/1.1 400 Bad Request
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 400,
    "status": "INVALID_ARGUMENT",
    "message": "includeProgress 需要激活 Card 声明的扩展。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "CALL_METADATA_NOT_ACTIVATED",
        "domain": "bcn.example.com"
      }
    ]
  }
}
```

<a id="ex-13-2"></a>

### 11.2 查询未知 Task

跨群或不可访问的 Task 使用相同标准错误。

请求：

```bash
curl -i -X GET "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/tasks/task-missing" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json'
```

返回：

```http
HTTP/1.1 404 Not Found
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 404,
    "status": "NOT_FOUND",
    "message": "找不到可访问的任务。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "TASK_NOT_FOUND",
        "domain": "a2a-protocol.org"
      }
    ]
  }
}
```

<a id="ex-13-3"></a>

### 11.3 未知或跨群 context

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-bad-context",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "继续。"
      }
    ],
    "contextId": "a62d4e18-5b37-4c90-a8f2-19e7c60d4b85:1234abcd"
  },
  "configuration": {
    "returnImmediately": true
  }
}
JSON
```

返回：

```http
HTTP/1.1 404 Not Found
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 404,
    "status": "NOT_FOUND",
    "message": "找不到可访问的 A2A 会话。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "CONTEXT_NOT_FOUND",
        "domain": "bcn.example.com"
      }
    ]
  }
}
```

<a id="ex-13-4"></a>

### 11.4 本期不接受文件输入

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-file",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "分析文件。"
      },
      {
        "url": "https://files.example.com/report.pdf",
        "mediaType": "application/pdf",
        "filename": "report.pdf"
      }
    ]
  },
  "configuration": {
    "returnImmediately": true
  }
}
JSON
```

返回：

```http
HTTP/1.1 400 Bad Request
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 400,
    "status": "INVALID_ARGUMENT",
    "message": "本期支持文本和一个对象类型的 data Part。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "CONTENT_TYPE_NOT_SUPPORTED",
        "domain": "a2a-protocol.org"
      }
    ]
  }
}
```

<a id="ex-13-5"></a>

### 11.5 输出模式没有交集

请求：

```bash
curl -i -X POST "${BCN_BASE_URL}/api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send" \
  -H 'A2A-Version: 1.0' \
  -H 'Accept: application/a2a+json' \
  -H 'Content-Type: application/a2a+json' \
  --data-binary @- <<'JSON'
{
  "message": {
    "messageId": "msg-output-mode",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": "生成报告。"
      }
    ]
  },
  "configuration": {
    "returnImmediately": true,
    "acceptedOutputModes": [
      "application/pdf"
    ]
  }
}
JSON
```

返回：

```http
HTTP/1.1 400 Bad Request
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "error": {
    "code": 400,
    "status": "INVALID_ARGUMENT",
    "message": "请求的输出模式与该群能力不匹配。",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "CONTENT_TYPE_NOT_SUPPORTED",
        "domain": "a2a-protocol.org"
      }
    ]
  }
}
```

## 接入检查与后续范围

保存 contextId 和 taskId。忙碌时等前轮完成再提交；等待超时或断线先查原 Task；终态订阅改用 GetTask。结果来自持久化 Artifact，SSE 心跳和 lastChunk 都不表示任务成功。

已有 BCN agent-card 管理与 A2A 出站接口不等于本方案新增的 Group 入站，路径共存见 [Spec §2](spec.md#2-协议绑定与对外接口)。BCN 单 Agent 标准入站另行排期。

自由聊天群的 Message-only / LLM 总结模式尚待定义结束条件与产物，暂不提供请求合同。自然语言建群移出本方案规划。
