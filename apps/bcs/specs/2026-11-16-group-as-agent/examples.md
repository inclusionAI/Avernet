# Group A2A 请求响应示例

状态：随 [spec](spec.md) 评审的协议样例，不是当前服务已经支持的接口。所有 ID、地址和内容均为合成数据；`${BCS_A2A_TOKEN}` 表示调用方通过管理面获得并安全保存的凭据，不应把 token 写入仓库。

以下使用 public、`supportA2A=true`、无 Human 成员的主从群 `group-demo`。接口基地址为 `https://bcs.example.com/api/v1/collaboration/a2a/groups/group-demo`，协议 1.0；context 示例沿用 BCS Session ID 形状。

## 1. 管理面开启

以下是现有 Group PATCH body 的新增字段示例，不是 A2A 消息；必须使用现有群管理身份，而不是 A2A 调用 token。

```json
{
  "visibility": "public",
  "supportA2A": true
}
```

校验修改后的群快照。若含 Human 成员或不支持的群类型，整次修改失败，不残留开启开关或注册 Card 的副作用。关闭使用 `{"supportA2A": false}`，停止新调用但不取消已有任务。

## 2. 认证后读取 Card

```http
GET /api/v1/collaboration/a2a/groups/group-demo/agent-card.json HTTP/1.1
Host: bcs.example.com
Authorization: Bearer ${BCS_A2A_TOKEN}
Accept: application/a2a+json
A2A-Version: 1.0
```

200 响应示例：

```json
{
  "name": "供应商比较协作群",
  "description": "比较候选供应商并给出采购建议。",
  "version": "1.0.0",
  "supportedInterfaces": [{
    "url": "https://bcs.example.com/api/v1/collaboration/a2a/groups/group-demo",
    "protocolBinding": "HTTP+JSON",
    "protocolVersion": "1.0"
  }],
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "extendedAgentCard": false,
    "extensions": [{
      "uri": "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.md",
      "required": false,
      "description": "使用 metadata.includeProgress 请求公开阶段进度。",
      "params": {
        "metadataSchema": {
          "$ref": "https://raw.githubusercontent.com/inclusionAI/Avernet/dev/docs/specs/a2a/extensions/call-metadata/v1.schema.json"
        }
      }
    }]
  },
  "securitySchemes": {
    "bcsBearer": {
      "httpAuthSecurityScheme": {
        "scheme": "Bearer",
        "description": "BCS 签发、用于 A2A 调用的访问凭据。"
      }
    }
  },
  "securityRequirements": [{"schemes": {"bcsBearer": {"list": []}}}],
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["text/plain"],
  "skills": [{
    "id": "group.execute",
    "name": "供应商比较",
    "description": "接受比较需求，交付采购建议。",
    "tags": ["procurement"]
  }]
}
```

扩展 URI／Schema 在本次只是设计地址，实施发布对应文件后才能声明上线支持。Card 没有把 Bearer 描述成 JWT，没有匿名 security requirement，也不暴露 manager／Worker 身份。public 群的 Task 并不公开。

## 3. 首轮调用并立即获得 Task

```http
POST /api/v1/collaboration/a2a/groups/group-demo/message:send HTTP/1.1
Host: bcs.example.com
Authorization: Bearer ${BCS_A2A_TOKEN}
Content-Type: application/a2a+json
A2A-Version: 1.0
```

```json
{
  "message": {
    "messageId": "msg-demo-001",
    "role": "ROLE_USER",
    "parts": [
      {"text": "比较两家供应商，预算为 80 万。", "mediaType": "text/plain"},
      {"data": {"budget": 800000, "currency": "CNY"}, "mediaType": "application/json"}
    ]
  },
  "configuration": {
    "returnImmediately": true,
    "historyLength": 0,
    "acceptedOutputModes": ["text/plain"]
  }
}
```

200 响应，**没有** BCN 通用 code/data envelope，也不是旧服务调用的 202：

```json
{
  "task": {
    "id": "task-demo-001",
    "contextId": "group-demo:1234abcd",
    "status": {
      "state": "TASK_STATE_SUBMITTED",
      "timestamp": "2026-11-16T08:00:00Z"
    }
  }
}
```

状态是提交后的真实快照，也可能已推进到 WORKING／终态。客户端保留 messageId、原始请求、taskId、contextId；客户端是应用系统也仍使用 ROLE_USER。

## 4. 查询结果与续聊

以同一主体 token 请求 `GET /tasks/task-demo-001?historyLength=0`，200 直接返回 Task：

```json
{
  "id": "task-demo-001",
  "contextId": "group-demo:1234abcd",
  "status": {
    "state": "TASK_STATE_COMPLETED",
    "timestamp": "2026-11-16T08:01:00Z"
  },
  "artifacts": [{
    "artifactId": "artifact-summary",
    "name": "采购建议",
    "parts": [{"text": "建议优先选择方案 A，总报价 70 万。", "mediaType": "text/plain"}]
  }]
}
```

确认前轮终态、未隔离且开关仍开启后，续聊发送新 messageId，带 contextId，**省略 taskId**：

```json
{
  "message": {
    "messageId": "msg-demo-002",
    "contextId": "group-demo:1234abcd",
    "role": "ROLE_USER",
    "parts": [{"text": "预算调整为 50 万，请重新比较。"}]
  },
  "configuration": {"returnImmediately": true}
}
```

返回 `task-demo-002`，保留同一 context；Task 1 产物不覆盖。状态机群以新输入从入口创建新 Run，而不是复用旧失败 Run 的 rerun。

若 context 仍占用，返回 409，不预留 msg-demo-002：

```json
{
  "error": {
    "code": 409,
    "status": "ABORTED",
    "message": "该 context 仍有执行，请等待当前任务结束。",
    "details": [{
      "@type": "type.googleapis.com/google.rpc.ErrorInfo",
      "reason": "CONTEXT_BUSY",
      "domain": "inclusionai.avernet.bcs",
      "metadata": {
        "contextId": "group-demo:1234abcd",
        "activeTaskId": "task-demo-001"
      }
    }]
  }
}
```

## 5. 默认同步等待超时

省略 configuration.returnImmediately 等同 false。任务在等待上限内未终态，返回 504；后台继续执行，Task 不因 HTTP 超时变成 FAILED。

```json
{
  "error": {
    "code": 504,
    "status": "DEADLINE_EXCEEDED",
    "message": "同步等待已结束，任务仍在执行，请查询原 Task。",
    "details": [{
      "@type": "type.googleapis.com/google.rpc.ErrorInfo",
      "reason": "SEND_WAIT_TIMEOUT",
      "domain": "inclusionai.avernet.bcs",
      "metadata": {
        "taskId": "task-demo-002",
        "contextId": "group-demo:1234abcd"
      }
    }]
  }
}
```

网关自己产生的 504 可能没有此 JSON／ID。重试须保留完整请求；若把 returnImmediately 从 false 改为 true 并沿用 messageId，会触发 MESSAGE_ID_CONFLICT，不能当成一次无差异重试。

## 6. 流式进度和产物

独立的新调用示例，使用 `POST /message:stream` 和正常 Bearer／版本头，并增加：

```http
A2A-Extensions: https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.md
```

```json
{
  "message": {
    "messageId": "msg-stream-001",
    "role": "ROLE_USER",
    "parts": [{"text": "整理候选方案并给出建议。"}]
  },
  "metadata": {"includeProgress": true}
}
```

200 `text/event-stream`，服务器确认实际激活的扩展头；下面每个 data 对象均为 StreamResponse：

```sse
data: {"task":{"id":"task-stream-001","contextId":"group-demo:abcdef12","status":{"state":"TASK_STATE_SUBMITTED","timestamp":"2026-11-16T09:00:00Z"}}}

data: {"statusUpdate":{"taskId":"task-stream-001","contextId":"group-demo:abcdef12","status":{"state":"TASK_STATE_WORKING","timestamp":"2026-11-16T09:00:10Z","message":{"messageId":"progress-001","contextId":"group-demo:abcdef12","taskId":"task-stream-001","role":"ROLE_AGENT","parts":[{"text":"资料比较已完成，正在汇总建议。"}]}}}}

: keep-alive

data: {"artifactUpdate":{"taskId":"task-stream-001","contextId":"group-demo:abcdef12","artifact":{"artifactId":"artifact-summary","parts":[{"text":"建议采用方案 A。","mediaType":"text/plain"}]},"append":false,"lastChunk":true}}

data: {"statusUpdate":{"taskId":"task-stream-001","contextId":"group-demo:abcdef12","status":{"state":"TASK_STATE_COMPLETED","timestamp":"2026-11-16T09:01:00Z"}}}

```

最终 Artifact 和 COMPLETED 已经同事务提交后才发送最后两帧。没有 `kind`／`final` 等旧协议字段；心跳不生成 history，不重置期限。

断线且未终态时，用同一主体凭据 `POST /tasks/task-stream-001:subscribe`，body 可为 `{}`；先收到当前 Task，再收到后续更新。已终态则新 subscribe 返回 UNSUPPORTED_OPERATION，改用 GetTask。重复 message:stream 命中原终态请求则可只返回 Task 快照后关闭，两种情况不混淆。

## 7. 取消与停止未知

```http
POST /api/v1/collaboration/a2a/groups/group-demo/tasks/task-demo-002:cancel HTTP/1.1
Host: bcs.example.com
Authorization: Bearer ${BCS_A2A_TOKEN}
A2A-Version: 1.0
Content-Type: application/a2a+json
```

```json
{}
```

停止仍在确认时，200 可以返回 WORKING：

```json
{
  "id": "task-demo-002",
  "contextId": "group-demo:1234abcd",
  "status": {
    "state": "TASK_STATE_WORKING",
    "timestamp": "2026-11-16T10:00:00Z",
    "message": {
      "messageId": "cancel-progress-001",
      "contextId": "group-demo:1234abcd",
      "taskId": "task-demo-002",
      "role": "ROLE_AGENT",
      "parts": [{"text": "取消请求已接收，正在确认关联工作停止。"}]
    }
  }
}
```

全部停止后为 CANCELED；收尾超时仍无法核实则为 FAILED 并隔离 context。对隔离 context 发新消息返回 CONTEXT_UNAVAILABLE，不因旧 Task 已终态就启动新轮次。原调用方可继续查询。终态后的重复 cancel 返回 TASK_NOT_CANCELABLE。

## 8. 列表、身份隔离与结果过期

`GET /tasks?contextId=group-demo%3A1234abcd&pageSize=2&historyLength=0&includeArtifacts=false`，返回当前主体在该群的记录；以下示例有两条，均省略 Artifact：

```json
{
  "tasks": [
    {"id": "task-demo-002", "contextId": "group-demo:1234abcd", "status": {"state": "TASK_STATE_CANCELED", "timestamp": "2026-11-16T10:00:30Z"}},
    {"id": "task-demo-001", "contextId": "group-demo:1234abcd", "status": {"state": "TASK_STATE_COMPLETED", "timestamp": "2026-11-16T08:01:00Z"}}
  ],
  "nextPageToken": "",
  "pageSize": 2,
  "totalSize": 2
}
```

其他主体即使知道 Task ID，GetTask 也返回与不存在相同的 404：

```json
{
  "error": {
    "code": 404,
    "status": "NOT_FOUND",
    "message": "任务不存在或不可访问。",
    "details": [{
      "@type": "type.googleapis.com/google.rpc.ErrorInfo",
      "reason": "TASK_NOT_FOUND",
      "domain": "a2a-protocol.org"
    }]
  }
}
```

结果被实际清理后，相同 messageId 不得再次执行，返回 BCS 的 MESSAGE_RESULT_EXPIRED；不是简单地查不到 Task 就创建新任务。原 context 若依赖已清理背景，返回 CONTEXT_HISTORY_EXPIRED，调用方需新建 context 并提供所需背景。

## 9. 接入检查

- 凭据由管理面发放，调用系统不必注册 Bot，也不需要 Group 授权名单。
- 只有 public 且 supportA2A 开启、资格有效的群才可新调用。
- 区分 Group ID、contextId、A2A taskId、输入 messageId，不传内部 Run／Worker task ID。
- 每次新轮次新 messageId；网络重试保持原 messageId 和完整语义请求。
- 关闭开关／目录撤销不等同于任务取消；任务历史仍按主体隔离。
- Bearer token 不写入 metadata，不转发给群内成员；token 轮换后使用同一 client_id 对应的新凭据查询旧任务。
- 文件、自由聊天、HumanInput、轮内追加、FIFO 和匿名调用均不是首期能力。
