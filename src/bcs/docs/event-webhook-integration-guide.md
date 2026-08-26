# BCS Group 回调接入说明

接入系统提供一个可被 BCS 访问的 HTTP Webhook 地址。创建 Group 时把该地址放入
`event_subscriptions`，之后 Group 及其 Session、Task、状态机和聊天消息事件会通过
HTTP `POST` 回调到该地址。

## 1. 创建 Group 并配置回调

接口：

```http
POST /openapi/v1/collaboration/groups
Content-Type: application/json
```

示例：

```json
{
  "group_kind": "normal",
  "name": "Demo Group",
  "driver_bot_uuid": "bot_manager",
  "participants": [
    {
      "actor_id": "bot_manager",
      "role": "driver"
    },
    {
      "actor_id": "bot_worker",
      "role": "consultant"
    }
  ],
  "collaboration": {
    "strategy": "chat"
  },
  "event_subscriptions": [
    {
      "name": "group-webhook",
      "event_filters": [
        "group.*",
        "session.*",
        "task.*",
        "state_machine.*",
        "message.created"
      ],
      "payload": {
        "mode": "metadata_only"
      },
      "sink": {
        "type": "webhook",
        "url": "https://callback.example.com/bcs/events",
        "request_timeout_ms": 2000
      }
    }
  ]
}
```

只需将 `sink.url` 替换为接入系统的接收地址。生产环境应使用 HTTPS；URL 不能包含
用户名、密码、query 或 fragment。内网域名/IP 需要由 BCS 部署方配置对应的域名、
CIDR 和端口 allowlist。

## 2. 回调请求格式

BCS 向 `sink.url` 发送：

```http
POST /bcs/events
Content-Type: application/json; charset=utf-8
```

Body 示例：

```json
{
  "spec_version": "1.0",
  "event_id": "evt-message-1",
  "event_type": "message.created",
  "schema_version": "1.0",
  "source": "bcs",
  "occurred_at": "2026-08-18T10:01:00.000Z",
  "recorded_at": "2026-08-18T10:01:00.005Z",
  "subject": {
    "type": "message",
    "id": "message-1"
  },
  "scope": {
    "group_id": "bcs_grp_xxx",
    "session_id": "session-1"
  },
  "stream": {
    "key": "session:session-1",
    "sequence": 18
  },
  "actor": {
    "type": "human",
    "id": "human-1"
  },
  "data": {
    "logical_message_id": "message-1",
    "message_type": "chat",
    "sender": {
      "type": "human",
      "id": "human-1"
    },
    "content": {
      "included": false,
      "content_type": "text/plain",
      "size_bytes": 15,
      "truncated": false
    },
    "attachments": []
  }
}
```

接入方处理要求：

- 使用 Body 中的 `event_id` 做幂等去重；同一事件可能因重试重复到达；
- 事件可靠接收后返回任意 `2xx`，推荐 `204 No Content`；
- 未可靠接收时返回非 `2xx`，BCS 会按投递策略处理失败；
- 如果依赖事件顺序，按 `stream.key` 分区，并按 `stream.sequence` 处理。

### 2.1 正文是否包含 Bot 实际输出

Event Subscription 的 `payload.mode` 默认为 `metadata_only`。该模式下，`output`、`result`、
`summary`、`content` 等正文投影字段仍然存在，但 `included=false`，且不包含 `text` 或 `json`。
需要接收 Bot 实际输出正文时，创建订阅时必须显式配置：

```json
{
  "payload": {
    "mode": "full"
  }
}
```

不同完成事件没有统一的 `output` 字段，接入方应按 `event_type` 读取：

| 协作场景 | 完成事件 | `full` 模式的正文路径 | 正文语义 |
| --- | --- | --- | --- |
| 自定义协作群的单个节点 | `state_machine.node.completed` | `data.output.json` | 该节点本次成功 attempt 的 Bot 输出产物 |
| 自定义协作群的整个 Run | `state_machine.run.completed` | `data.output.json` | 标记为 `final_output` 的节点产物；没有该标记时回退为最后一个有产物的节点 |
| 任务协作群的单个 Worker 子任务 | `task.completed` | `data.result.text` | Worker 返回并被 BCS 接收的 final result |
| 任务协作群的整个 Session | `session.completed` | `data.summary.json` | Manager 调用 `bcs_task_complete` 时提交的 `summary`，不保证等于 Manager 的原始 assistant 消息 |

当前状态机生产方把文本产物编码为 `content_type=application/json` 的 JSON string，因此实际文本位于
`json`；Task 生产方使用 `content_type=text/plain`，因此 Worker 结果位于 `text`。为了兼容后续生产方，
接入方应先检查 `included` 和 `content_type`，再读取 `text` 或 `json`，不要假设所有正文都使用同一字段。
即使使用 `full`，正文仍会经过敏感信息过滤和事件大小限制。

## 3. 自定义协作群执行一次状态机的事件链

当创建 `collaboration.strategy=state_machine` 的自定义协作群、配置内联
`event_subscriptions`，且状态机定义可立即执行时，初始状态机默认自动执行。一次单节点、
无重试且成功完成的典型事件链如下：

```text
group.created
session.created
state_machine.run.created
state_machine.run.started
state_machine.node.started
state_machine.node.completed
state_machine.run.completed
```

各事件的关联方式和主要字段：

| Event type | 含义 | 主要字段 |
| --- | --- | --- |
| `group.created` | 自定义协作群创建完成 | `scope.group_id`；`data.group_kind`、`strategy`、`name`、`status`、`version` |
| `session.created` | 群的初始 Session 创建完成 | `scope.group_id`、`scope.session_id`；`data.session_kind`、`status`、`created_by?`、`initial` |
| `state_machine.run.created` | Run 和状态机定义快照创建完成 | `scope.group_id`、`scope.session_id`、`scope.run_id`；`data.definition_id`、`definition_version`、`run_mode=configured`、`status=running` |
| `state_machine.run.started` | Run 开始调度节点 | `scope.run_id`；`data.run_mode`、`started_at`、`input` |
| `state_machine.node.started` | 一个节点的某次 attempt 开始 | `scope.run_id`；`data.run_id`、`node_id`、`attempt`、`predecessor_node_ids?`、`assignee_id?`、`started_at` |
| `state_machine.node.completed` | 一个节点的某次 attempt 成功完成 | `scope.run_id`；`data.run_id`、`node_id`、`attempt`、`outcome`、`output`、`completed_at`、`duration_ms` |
| `state_machine.run.completed` | 整个 Run 成功完成 | `scope.run_id`；`data.completed_at`、`output`、`duration_ms` |

多节点状态机会为每个实际执行的节点分别发送
`state_machine.node.started` 和 `state_machine.node.completed`。
新产生的 `state_machine.node.started` 始终包含 `predecessor_node_ids`，表示定义中指向该节点的
全部直接前序节点；初始节点为 `[]`。该字段是为了兼容既有已存储事件而保持 schema 可选，接入方读取
旧事件时应把缺省值按“拓扑未知”处理，不应等同于初始节点。

例如，一个汇聚节点开始时的 `data` 为：

```json
{
  "run_id": "run-1",
  "node_id": "join",
  "attempt": 0,
  "predecessor_node_ids": ["branch-b", "branch-c"],
  "assignee_id": "bot-worker",
  "started_at": "2026-08-18T10:01:30.000Z"
}
```

`predecessor_node_ids` 描述定义中的直接入边，而不是“最后一个完成的节点”。对于条件分支共享的
汇聚节点，数组可能包含本次运行中被跳过的分支节点；接入方若要区分实际执行与跳过状态，不能仅凭
这个数组推断。

如果节点执行失败后进入重试，对应片段为：

```text
state_machine.node.started          (attempt=n)
state_machine.node.retry_scheduled  (attempt=n, next_attempt=n+1)
state_machine.node.started          (attempt=n+1)
```

`state_machine.node.retry_scheduled` 的主要字段是 `data.run_id`、`node_id`、`attempt`、
`next_attempt`、`max_attempts`、`retry_at` 和 `reason`。

同一个 Run 的全部 `state_machine.*` 事件使用相同的
`stream.key=state-machine-run:{run_id}`，按 `stream.sequence` 严格投递；前一个事件未成功
投递时，后一个事件不会越过它。可并行执行的节点按照实际状态提交顺序获得 sequence，
接入方不应假设并行节点之间存在固定先后关系。

`group.created`、`session.created` 和 `state_machine.*` 分属不同 stream，不存在跨 stream 的
全局顺序保证；其中初始 `session.created` 通过 `causation_event_id` 保证不会先于同一订阅的
`group.created` 投递。接入方应使用 `scope.group_id`、`scope.session_id` 和 `scope.run_id`
关联整条执行链，而不是依赖不同 stream 的到达先后。

示例配置使用 `payload.mode=metadata_only`，因此 `input`、`output` 等内容字段只包含
`included`、`content_type`、`size_bytes`、`truncated` 等元数据，不包含正文。

如果订阅配置为 `payload.mode=full`，`state_machine.run.completed.data` 示例为：

```json
{
  "completed_at": "2026-08-18T10:09:00.000Z",
  "output": {
    "included": true,
    "content_type": "application/json",
    "size_bytes": 21,
    "delivered_bytes": 21,
    "json": "Release is approved",
    "sha256": "b3a5821ebb0666e48177847ae100a95663b9ce786f5d4ba61c73b1ae5016e40f",
    "truncated": false
  },
  "duration_ms": 540000
}
```

这里的 `data.output.json` 是整个 Run 的最终对外产物，不是所有节点输出的拼接。需要每个节点的实际
输出时，同时订阅并读取各个 `state_machine.node.completed.data.output`。

## 4. 任务协作群的事件

任务协作群指 `collaboration.strategy=manager_worker` 的 Group。一次协作可以包含多个由 Manager
分配给 Worker 的独立子任务，每个子任务拥有自己的 `task_id` 和 Event stream。

典型生命周期涉及以下事件：

```text
group.created
session.created
task.assigned                 (每个子任务一次)
message.created               (零到多条外部可见聊天消息)
task.completed                (每个成功返回 final result 的子任务一次)
session.completed             (Manager 完成整个协作 Session 时一次)
```

这只是业务生命周期示意，不代表这些事件会按该列表形成一个全局有序序列。主要字段如下：

| Event type | 触发条件 | 关联与主要字段 |
| --- | --- | --- |
| `task.assigned` | Manager 的 assign task 被接受并形成有效子任务 | `scope.group_id`、`scope.task_id`、`scope.session_id?`；`data.task_id`、`manager_id`、`worker_id`、`session_id?`、`assignment` |
| `task.completed` | Worker 的 final result 被验证并接收为该子任务结果 | 与 assigned 使用相同 task scope；`data.task_id`、`manager_id`、`worker_id`、`session_id?`、`result`、`completed_at` |
| `message.created` | ManagerWorker Session 中一条外部可见逻辑聊天消息成功持久化 | `scope.group_id`、`scope.session_id`；`data.logical_message_id`、`message_type`、`sender`、`run_id?`、`content`、`attachments` |
| `session.completed` | Manager 完成整个协作，并使目标 Session 首次进入 Completed | `scope.group_id`、`scope.session_id`；`data.completed_by`、`reason`、`summary` |

`task.assigned` 的 metadata-only `data` 示例：

```json
{
  "task_id": "task-1",
  "manager_id": "bot-manager",
  "worker_id": "bot-worker",
  "session_id": "session-1",
  "assignment": {
    "included": false,
    "content_type": "text/plain",
    "size_bytes": 42,
    "truncated": false
  }
}
```

对应的 `task.completed`：

```json
{
  "task_id": "task-1",
  "manager_id": "bot-manager",
  "worker_id": "bot-worker",
  "session_id": "session-1",
  "result": {
    "included": false,
    "content_type": "text/plain",
    "size_bytes": 320,
    "truncated": false
  },
  "completed_at": "2026-08-18T10:02:00.000Z"
}
```

订阅配置为 `payload.mode=full` 时，同一个 `task.completed.data.result` 会包含 Worker 的实际结果文本：

```json
{
  "result": {
    "included": true,
    "content_type": "text/plain",
    "size_bytes": 19,
    "delivered_bytes": 19,
    "text": "Worker final result",
    "sha256": "4291e81b36766968b73a9c9268fbb4b73644b31f3d7e796ce3de5665319b2e78",
    "truncated": false
  }
}
```

Manager 完成整个任务协作 Session 时产生的是 `session.completed`。它没有 `data.output`，最终汇总位于
`data.summary`；`full` 模式示例为：

```json
{
  "completed_by": "bcs-system",
  "reason": "completed",
  "summary": {
    "included": true,
    "content_type": "application/json",
    "size_bytes": 59,
    "delivered_bytes": 59,
    "json": "All workers completed; publish the reviewed release plan.",
    "sha256": "90c81b570bbdc08637ff28688d5af1bafb888fb5c3455dc89048e401bec97940",
    "truncated": false
  }
}
```

任务事件的顺序和完成语义：

- 同一个子任务的 `task.assigned` 与 `task.completed` 使用
  `stream.key=task:{task_id}`，按 `stream.sequence` 严格投递；
- 两个 task 事件都使用 `correlation_id={task_id}`；`task.assigned.actor` 是 Manager，
  `task.completed.actor` 是返回结果的 Worker；
- 不同 `task_id` 属于不同 stream，并行子任务之间没有固定到达顺序；
- `message.created` 和 `session.completed` 使用 `stream.key=session:{session_id}`，与 task stream
  之间没有全局顺序保证，也没有可供接入方依赖的显式因果关系；
- `task.completed` 只表示一个 Worker 子任务完成，不表示整个协作完成；
- 存在未完成子任务时，Manager 的 `bcs_task_complete` 会被阻止；它完成实际 Session 时产生
  `session.completed`，不会额外产生一个代表“全部任务”的虚构 `task.completed`；
- `session.completed.data.reason` 为 `completed` 或 `failed`，`completed_by` 当前为
  `bcs-system`；若命令只更新 Group 状态而没有完成实际 Session，则不会产生该事件；
- ManagerWorker 中只有外部可见、已持久化的最终逻辑聊天消息产生 `message.created`；streaming delta、
  thinking、工具调用参数和内部协调消息不会产生该事件。

`assignment`、`result`、`content` 和 `summary` 都遵循 payload projection。使用
`payload.mode=metadata_only` 时只包含类型、大小等元数据；需要正文时应显式使用 `full`，同时仍受
事件大小与敏感信息过滤策略约束。
