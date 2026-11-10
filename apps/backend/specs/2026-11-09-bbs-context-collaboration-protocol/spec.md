# 基于上下文自主协作协议 v2 设计方案（ACCS 按需采用版）

- 日期：2026-11-09
- 修订：2026-11-11
- 状态：Draft / 待评审，尚未实现
- 主题：基于上下文（Context / Collet）的自主协作
- 代码落点：`apps/backend/src/agentclaw/community/core/task/context_collab/`
- 参考架构：ACCS L1-L6 与 G1-G4

> 本版不是把 ACCS 文档中的全部概念对象搬入本方案，而是只选择“基于上下文自主协作”实际需要的对象。没有进入当前运行链路的对象，明确列为外部依赖、未来扩展或不引入；不为保持名义上的层次完整而创建空模型。

## 0. 方案结论

本次只为当前三个 API 提供最小闭环，不为 ACCS 层次完整性新增对象、表或独立生命周期：

```text
三个 API
  ├─ POST 创建协作上下文
  ├─ POST 新增协作上下文事件
  └─ GET 查询协作上下文最新状态
       │
       ├─ L4 Collet：保存上下文和当前任务快照
       └─ G2 ColletEvent：保存追加事件、parent_event 和幂等信息

L1：API 合同与 HTTP/OpenAPI 交付
L2/L3/L5/L6：只作为请求/快照中的引用或枚举字段，不新增独立对象
G1：授权与可见性校验
G3：由 Collet + ColletEvent 组合查询响应
G4：字段、版本和状态值校验
```

本版真正需要新增的持久化对象只有两个：

1. **`Collet`**：一次上下文自主协作的唯一记录，保存 `collab_context_id`、创建参数和当前任务进度快照。
2. **`ColletEvent`**：一次被接纳的协作事件，保存 `event_id`、`request_id`、`parent_event`、事件类型和 `payload`。

`Task`、`Message`、`Participant`、`Resource`、`Method`、`Progress` 等不新增独立表；它们只作为 API 字段、快照字段、事件语义或外部引用存在。GET 只返回由两张表组合出的查询响应。

除 `Collet`、`ColletEvent` 外，本版不新增其它领域对象或独立生命周期。

## 1. 背景、目标与边界

### 1.1 要解决的问题

现有单 Bot、BCS 群、BBS 讨论和 Task / Relay 各自拥有消息、运行或任务视图，无法用同一个上下文回答：

- 本次协作的目标是什么？
- 当前责任主体和执行 Agent 是谁？
- 任务已经做过哪些事情、当前到了哪一步？
- 哪些消息、活动、决策和产物构成了当前任务的事实链？
- 外部执行系统的状态与协作上下文的状态，谁负责什么？

### 1.2 产品目标

- 以 L4 `Collet` 统一一次自主协作的上下文边界。
- 以 L2/L3 引用身份、能力、空间和资源，不复制已有身份或资源系统。
- 以 L5 `Open + Group` 支持 BBS 公开讨论，以 L5 `Single` 支持指定 Agent 执行，并为后续方法扩展保留 L5 位置。
- 以 `Collet` 快照和 `ColletEvent` 让用户看到任务进度、证据和事件 DAG。
- 以 G1-G4 处理授权、可靠性、观察投影和状态一致性。
- 保留以下三个 API 路径及字段兼容要求：`collab_context_id`、`parent_event`、`trunk_events`。

### 1.3 非目标

- 不替代 BCS、TaskGraph、Relay 或外部执行系统的运行时和生命周期。
- 不把 BBS Topic、BCS group、外部 Task 直接变成 L4 核心对象。
- 不在本域复制用户、Bot、Agent Card、能力注册、Space 成员或资源正文。
- 不把 `Capability` 当成授权，不把 `Participant` 当成权限，不把查询响应当成权威状态。
- 不在 `plugin_api`、`task/forum`、`task/bbs` 下新增或修改实现。
- 当前只引入两个产品场景实际需要的 L5 方法：BBS 使用 `Open + Group`，团队代办使用 `Single`；`Swarm` 及 G3 演进对象暂不引入。

## 2. 按需采用 ACCS 层次与对象

### 2.1 L1-L6 选择结果

| 层 | 本次仅保留的 API 必要内容 | 存储形式 | 不新增 |
| --- | --- | --- | --- |
| L1 | 三个 Service API、HTTP/OpenAPI adapter | API 合同和适配器 | Agent Card、A2A Message、A2A Task、Part、Artifact |
| L2 | `principal_ref`、`actor_ref`、`executor_ref` | JSON 引用字段 | Principal / Agent / Capability 注册表 |
| L3 | `space`、`resource_refs` | JSON 引用字段 | Space、Resource、Membership、Grant 表 |
| L4 | `Collet`、`ColletEvent`、任务快照、事件 payload | 两张表 + JSON 字段 | Task、Message、Participant、Role 等独立对象 |
| L5 | `method_kind`、`executor_ref` | `context_spec` / `current_snapshot` 字段 | MethodBinding、Lease、Swarm 对象 |
| L6 | `product_kind`、`workflow_kind`（如需要） | `context_spec` 字段 | ProductProfile、WorkflowProfile 表 |

L1-L6 在本方案中是**字段来源和边界说明**，不是“每一层都必须创建一组代码对象”。只有 L1、L4 以及 G1-G4 中确实被三个 API 调用的逻辑才创建实现文件。

### 2.2 G1-G4 选择结果

| 治理面 | 本次仅保留的 API 必要内容 | 存储形式 | 不新增 |
| --- | --- | --- | --- |
| G1 | 创建、追加、查询的权限检查；事件 `visibility` 过滤 | 调用既有授权能力 | Audit、Authentication、Membership、Grant 表 |
| G2 | `request_id` 幂等、`expected_version` 并发控制、`parent_event` DAG、`trunk_events` 查询过滤 | Collet / ColletEvent 字段和事务 | Lease、Retry、Recovery、Heartbeat |
| G3 | `current_snapshot` + 事件列表组合为 `task_progress` | GET 响应投影 | 独立查询投影、Metric、Candidate、Experiment Ledger 表 |
| G4 | 请求/事件 schema、`context_version`、有限状态值校验 | 代码校验 + 快照字段 | StateMachine、Transition Record、RuntimeBinding 表 |

### 2.3 本方案必要的新增工程对象

当前三个 API 只新增以下两个持久化对象和一个查询响应 DTO：

1. **`Collet`**：保存创建参数和最新状态。
2. **`ColletEvent`**：保存不可变事件事实和事件 DAG 关系。
3. **GET 查询响应**：实时组合出的状态和进度 DTO，不持久化。

字段归属如下：

- L2/L3/L5/L6 的引用和枚举：放入 `Collet.context_spec` 或 `Collet.current_snapshot`。
- 用户互动、执行进度、阻塞、交付和验收：放入 `ColletEvent.event_kind` 和 `ColletEvent.payload`。
- `parent_event`、`trunk_events`、`expected_version`：分别是事件字段、查询参数和写入并发参数。
- 不为上述字段再拆出领域对象、表或 Repository。

## 3. 正式对象模型

### 3.1 Collet

```text
Collet
  ├─ collab_context_id
  ├─ context_spec       # space、principal、resource、method、product 等创建参数
  ├─ current_snapshot   # task、status、executor、progress 等最新状态
  ├─ current_event_id
  └─ context_version
```

### 3.2 ColletEvent

```text
ColletEvent
  ├─ event_id / request_id / sequence_no
  ├─ collab_context_id / parent_event / is_trunk
  ├─ event_kind / primitive_type / actor / visibility
  ├─ payload           # 用户互动直接使用 payload.content
  └─ context_version_after
```

`Principal`、`Agent`、`Space`、`Resource`、`Method`、`Workflow` 只保存稳定引用或枚举值；不复制外部身份、授权、资源正文或执行生命周期。

### 3.3 三个 API 与对象的关系

| API | 必须持久化的事实 |
| --- | --- |
| 创建协作上下文 | 同一事务写 1 行 `Collet` + 1 行初始 `ColletEvent(task.created)` |
| 新增协作上下文事件 | 同一事务写 1 行 `ColletEvent` + 更新同一行 `Collet` |
| 查询协作上下文最新状态 | 读取 1 行 `Collet` + 事件列表，组合查询响应；不写库 |

没有这三个 API 的直接消费者，就不在本版新增对象、表或独立 Repository。

## 4. 事件、状态与进度

### 4.1 ColletEvent

`ColletEvent` 是服务端接纳事实的 append-only 封装。它只服务于三个 API：创建时写入初始 `task.created`，事件接口追加事实，查询接口读取事件 DAG。

```text
ColletEvent
  ├─ event_id
  ├─ collab_context_id
  ├─ event_kind              # 例如 user_interaction.posted / task.progress.updated
  ├─ primitive_type          # Message / Task / Progress（事件语义标签）
  ├─ parent_event            # 主事件为 null；派生事件引用父事件
  ├─ actor_ref / request_id / occurred_at / visibility
  ├─ payload                 # Message 直接使用 payload.content
```

用户互动消息采用：

```text
event_kind: user_interaction.posted
primitive_type: Message
payload.content: 用户消息正文
```

`parent_event` 表示事件 DAG 派生关系；`trunk_events=true` 只过滤 `is_trunk=1` 的事件，不改变状态、统计或当前事件。`primitive_type` 只是事件语义标签，不对应独立数据库表。

### 4.2 最小状态推进

状态只保存在 `Collet.current_snapshot`，由追加事件时按已发布的 G4 schema 更新：

```text
created
  → assigned / discussing
  → executing / discussing
  → reviewing / completed / closed
```

事件 payload 可以表达用户互动、活动进度、阻塞、交付或验收信息，但这些都只是事件数据。LLM 输出不能直接改写快照，必须先经受信 Actor / Adapter 作为事件接纳。

### 4.3 用户进度查询响应

查询接口从 `Collet.current_snapshot` 和事件表组合查询响应，至少包括：

- 当前任务目标、状态、负责人和执行人；
- 已接纳事件总数、已完成/进行中/阻塞统计；
- `task_progress.events[]` 事件 DAG，以及 `current_event`；
- 当前允许的动作（由当前状态和既有授权决定；不落独立 Action 表）。

`task_progress.events[]` 是响应投影，不使用 `steps[]` 或 `step`，也不成为第二个状态源。

## 5. 三个 API 合同

### 5.1 路径

内部 Service API 的 HTTP delivery adapter：

```text
POST /api/v1/collaboration/task/context_collab
POST /api/v1/collaboration/task/context_collab/{collab_context_id}/events
GET  /api/v1/collaboration/task/context_collab/{collab_context_id}
```

OpenAPI delivery adapter：

```text
POST /openapi/v1/collaboration/task/context_collab
POST /openapi/v1/collaboration/task/context_collab/{collab_context_id}/events
GET  /openapi/v1/collaboration/task/context_collab/{collab_context_id}
```

两个 adapter 只做解析、认证上下文转换、schema 校验、序列化和错误映射，共享同一份 Service API contract。

### 5.2 新增协作上下文

该接口只创建一行 `Collet` 和一行初始 `ColletEvent(task.created)`。创建请求只保留三个 API 后续会用到的最小字段；L2/L3/L5/L6 信息作为 `context_spec` 内的引用或枚举保存。

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `request_id` | body | string | 是 | G2 幂等键 |
| `space` | body | object | 是 | L3 Space 引用 |
| `principal_ref` | body | object | 是 | L2 责任主体引用 |
| `product_kind` | body | string | 否 | L6 产品类型，如 `team_todo`、`public_discussion` |
| `workflow_kind` | body | string | 否 | L6 流程类型；仅保存字符串，不建 Profile 表 |
| `method_kind` | body | string | 是 | L5 方法；当前为 `Single` 或 `Open + Group` |
| `executor_ref` | body | object | 条件必填 | `method_kind=Single` 时必填；指定执行 Bot 引用 |
| `objective` | body | string | 是 | 当前协作目标 |
| `resource_refs[]` | body | array | 否 | L3 外部资源引用，不复制资源正文 |

#### 指定执行请求示例

```http
POST /api/v1/collaboration/task/context_collab
Content-Type: application/json
```

```json
{
  "request_id": "create-collet-001",
  "space": {
    "space_id": "space_workspace_001",
    "environment": "prod"
  },
  "principal_ref": {
    "principal_type": "human",
    "principal_id": "u_1001"
  },
  "product_kind": "team_todo",
  "workflow_kind": "team_todo_execution",
  "method_kind": "Single",
  "executor_ref": {
    "agent_type": "bot",
    "agent_id": "bot_coding_001"
  },
  "objective": "整理 Context 协作协议评审结论并提交可评审 PR",
  "resource_refs": [
    {
      "resource_type": "forum_topic",
      "resource_id": "topic_001",
      "revision": "3"
    }
  ]
}
```

#### 响应示例

```json
{
  "collab_context_id": "collet_001",
  "context_version": 1,
  "current_snapshot": {
    "objective": "整理 Context 协作协议评审结论并提交可评审 PR",
    "status": "created",
    "principal_ref": "u_1001",
    "executor_ref": "bot_coding_001",
    "method_kind": "Single",
    "progress": {
      "percent": 0,
      "current_event_id": "evt_001"
    }
  },
  "current_event": {
    "event_id": "evt_001",
    "parent_event": null,
    "event_kind": "task.created",
    "primitive_type": "Task"
  }
}
```

### 5.3 新增协作上下文事件

该接口接收一个事件事实，由服务端执行 G1/G2/G4 校验并追加一行 `ColletEvent`，再更新同一 `Collet` 的当前快照。

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `collab_context_id` | path | string | 是 | L4 Collet ID 的 API 兼容名 |
| `request_id` | body | string | 是 | G2 幂等键 |
| `expected_version` | body | integer | 是 | 乐观并发版本 |
| `event_kind` | body | string | 是 | 例如 `user_interaction.posted` |
| `primitive_type` | body | string | 是 | `Message`、`Task` 或 `Progress` |
| `actor_ref` | body | object | 是 | L2 Principal / Agent 引用 |
| `parent_event` | body | string/null | 是 | 主事件为 `null`，派生事件引用父事件 |
| `visibility` | body | string | 是 | 只能落在调用者已有授权范围内 |
| `payload` | body | object | 是 | 按 G4 Schema 校验；Message 必须直接包含 `content` |

#### 用户新增互动消息示例

```http
POST /api/v1/collaboration/task/context_collab/collet_001/events
Content-Type: application/json
```

```json
{
  "request_id": "append-user-message-001",
  "expected_version": 1,
  "event_kind": "user_interaction.posted",
  "primitive_type": "Message",
  "actor_ref": {
    "principal_type": "human",
    "principal_id": "u_1001"
  },
  "parent_event": "evt_001",
  "visibility": "space_members",
  "payload": {
    "message_kind": "user_interaction",
    "content": "上下文做一下补充xxx"
  }
}
```

#### 响应示例

```json
{
  "accepted": true,
  "collab_context_id": "collet_001",
  "event_id": "evt_002",
  "parent_event": "evt_001",
  "event_kind": "user_interaction.posted",
  "primitive_type": "Message",
  "context_version": 2,
  "current_status": "in_progress"
}
```

### 5.4 查询协作上下文最新状态与任务进度

```http
GET /api/v1/collaboration/task/context_collab/{collab_context_id}?trunk_events=true
```

| 参数 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `collab_context_id` | path | string | 是 | L4 Collet ID |
| `trunk_events` | query | boolean | 否 | 默认 `false`；`true` 只返回事件 DAG 主事件 |

#### 响应示例

```json
{
  "collab_context_id": "collet_001",
  "context_version": 4,
  "current_snapshot": {
    "objective": "整理 Context 协作协议评审结论并提交可评审 PR",
    "status": "in_progress",
    "principal_ref": "u_1001",
    "method_kind": "Single",
    "executor_ref": "bot_coding_001",
    "progress": {
      "percent": 40,
      "completed_count": 2,
      "active_count": 1,
      "blocked_count": 0,
      "current_event_id": "evt_004"
    }
  },
  "current_event": {
    "event_id": "evt_004",
    "parent_event": "evt_003",
    "event_kind": "task.progress.updated",
    "primitive_type": "Progress",
    "summary": "开始整理协议设计",
    "occurred_at": "2026-11-10T08:30:00Z"
  },
  "task_progress": {
    "total_events": 5,
    "events": [
      {
        "event_id": "evt_001",
        "parent_event": null,
        "event_kind": "task.created",
        "primitive_type": "Task",
        "summary": "创建协作任务",
        "status": "completed"
      },
      {
        "event_id": "evt_002",
        "parent_event": "evt_001",
        "event_kind": "user_interaction.posted",
        "primitive_type": "Message",
        "content": "上下文做一下补充xxx",
        "status": "completed"
      },
      {
        "event_id": "evt_003",
        "parent_event": "evt_002",
        "event_kind": "task.assigned",
        "primitive_type": "Task",
        "summary": "指定 bot_coding_001 执行",
        "status": "completed"
      },
      {
        "event_id": "evt_004",
        "parent_event": "evt_003",
        "event_kind": "task.progress.updated",
        "primitive_type": "Progress",
        "summary": "开始整理协议设计",
        "status": "active"
      },
      {
        "event_id": "evt_005",
        "parent_event": "evt_004",
        "event_kind": "task.progress.updated",
        "primitive_type": "Progress",
        "summary": "完成设计并准备提交 PR",
        "status": "pending"
      }
    ]
  }
}
```

该返回直接回答：

- **做了哪些事情**：读取 `task_progress.events[]` 中已经接纳的事件。
- **当前到了哪一步**：读取 `current_snapshot.status`、`progress.current_event_id` 和 `current_event`。
- **主事件或全部事件**：`trunk_events=true` 只过滤 `is_trunk=1`，不改变 `current_snapshot`、统计或当前事件。

### 5.5 OpenAPI 合同

OpenAPI 面必须暴露与内部 API 相同语义的三个路径，并复用以下 schema：

- `ColletCreateRequest`
- `ColletEventCreateRequest`
- `ContextCollabStatusResponse`
- `TaskProgress`
- `ProgressEvent`：必须包含 `event_id`、`parent_event`、`event_kind`、`primitive_type`、`status`
- `ContextSpec`、`EventPayload`、`TaskProgress` 和 L2/L3 引用 DTO

OpenAPI schema 不应暴露未被本方案采用的 Agent Card、A2A Task、Metric、Candidate 等对象。

## 6. 两个产品支持示例

本节仍给出 BBS 公开讨论和团队任务代办的 L1-L6/G1-G4 取值，但只列当前三个 API 真正需要的对象。表中的 `Participant`、`RoleBinding`、`Task`、`Message` 等是 `context_spec`、`current_snapshot` 或事件 payload 的语义字段，不是新增表或独立模块。

### 6.1 BBS 社区公开讨论

| 层次 | 当前 API 使用的最小取值 |
| --- | --- |
| L1 | `POST context_collab`、`POST .../events`、`GET context_collab/{id}`；HTTP/OpenAPI |
| L2 | `principal_ref=u_topic_owner`；可选 `agent_ref=bot_forum_summary_001`；能力由外部系统校验 |
| L3 | `space_id=bbs_community_space`；`resource_ref=forum_topic:topic_001@rev3` |
| L4 | `collab_context_id=collet_forum_topic_001`；`context_spec` 保存 principal 和 Topic resource_ref；`current_snapshot.objective=围绕 Topic 收集和整理观点`；用户消息作为 `user_interaction.posted` 事件的 `payload.content` |
| L5 | `method_kind=Open + Group` |
| L6 | `product_kind=public_discussion`；`workflow_kind=forum_public_discussion`（字段值） |
| G1 | 校验 `topic.read`、`message.post` 和事件 visibility；复用现有审计能力 |
| G2 | `request_id`、`expected_version`、`parent_event`；查询支持 `trunk_events` |
| G3 | 返回公开讨论事件时间线和当前状态，不创建 Outcome 表 |
| G4 | `forum_public_discussion.v1`；状态保存在 `current_snapshot`，不创建独立状态迁移表 |

流程：

```text
已有 BBS Topic
  → POST 创建 Collet（保存 Topic 引用和讨论目标）
  → 用户 POST 事件（user_interaction.posted + payload.content）
  → 服务端追加 ColletEvent，更新 current_snapshot 和 context_version
  → 后续用户 / Bot 继续追加事件，parent_event 形成 DAG
  → GET 查询 Collet + 事件，返回 task_progress.events[]
```

### 6.2 团队空间内创建任务代办并指定 Bot 执行

| 层次 | 当前 API 使用的最小取值 |
| --- | --- |
| L1 | `POST context_collab`、`POST .../events`、`GET context_collab/{id}`；HTTP/OpenAPI |
| L2 | `principal_ref=u_1001`；`executor_ref=bot_coding_001`；能力和授权由外部系统校验 |
| L3 | `space_id=team_space_001`；`resource_ref=repo:repo_001@rev7`（可选） |
| L4 | `collab_context_id=collet_todo_001`；`context_spec` 保存 principal/executor/resource_refs；`current_snapshot.objective=完成团队代办`；执行进度通过事件 payload 追加 |
| L5 | `method_kind=Single`，`executor_ref=bot_coding_001` |
| L6 | `product_kind=team_todo`；`workflow_kind=team_todo_execution`（字段值） |
| G1 | 校验 `todo.create`、`todo.execute`、`todo.accept` 和资源访问权限 |
| G2 | 每次追加携带 `request_id`、`expected_version`、`parent_event`；重复请求不重复写入 |
| G3 | 查询返回当前任务状态、事件进度、阻塞和交付事件，不创建额外事实表 |
| G4 | `team_todo_execution.v1`；事件接纳后写入快照和版本 |

流程：

```text
用户创建指定执行代办
  → POST 创建 Collet（保存 task、Single、executor 和要求）
  → 服务端追加 / 接纳 task.created
  → Bot 读取 GET 当前状态
  → Bot 或适配器追加 `task.progress.updated` 事件，进度和交付信息放在 payload
  → 用户追加 `task.accepted` 或 `task.rejected` 事件
  → 每次追加都更新同一 Collet 的 current_snapshot
  → GET 返回当前状态和 task_progress.events[]
```

### 6.3 两个产品共用的最小边界

```text
Collet（context_spec + current_snapshot）
ColletEvent（parent_event + payload）
GET 响应 / task_progress.events[]
request_id / expected_version / trunk_events
```

BBS 的 `Open + Group` 和团队代办的 `Single` 只是创建参数和快照中的方法配置，不因为产品不同再新增领域表。

## 7. 目录设计：按 L1-L6、G1-G4 分组

### 7.1 目录约束

新增核心代码统一放在：

```text
apps/backend/src/agentclaw/community/core/task/context_collab/
```

不放到：

```text
apps/backend/src/agentclaw/community/core/task/forum/
apps/backend/src/agentclaw/community/core/task/bbs/
apps/backend/src/agentclaw/community/plugin_api/
apps/backend/src/agentclaw/community/api/
```

目录只表达 ACCS 层次和治理面，不代表允许跨目录绕过 Service API 或 G1-G4 合同。

### 7.2 目录结构

只为三个 API 的合同、校验、持久化和查询投影创建以下实现文件：

```text
apps/backend/src/agentclaw/community/
├── core/task/context_collab/
│   ├── l1_interoperate_deliver/service_api.py          # 三个 Service API 合同
│   ├── l4_collet_primitives/models.py                  # Collet / ColletEvent
│   ├── l4_collet_primitives/repository.py              # 两张表的持久化
│   ├── g1_secure_govern/authorization.py               # 复用既有授权能力
│   ├── g2_reliable_live/event_rules.py                 # 幂等、版本、DAG 规则
│   ├── g3_observe_evaluate_evolve/current_status.py    # task_progress 查询投影
│   └── g4_contracts_consistency/schema.py              # 请求、事件、状态校验
├── adapters/http/task/context_collab.py                 # /api/v1 三个接口
└── adapters/http/openapi_v1/task/context_collab.py      # /openapi/v1 三个接口
```

L2、L3、L5、L6 不创建单独 source 文件：它们只在 `models.py` 的引用字段、枚举和 JSON schema 中出现。这样既保留 ACCS 层次映射，又不因每层都建立模块而扩大本次改动。

### 7.3 明确不创建的目录 / 文件

本版不创建以下对象文件或独立模块：

```text
agent_card.py
a2a_task.py
a2a_artifact.py
participant.py
role_binding.py
resource_binding.py
method_binding.py
task.py
message.py
activity.py
decision.py
artifact.py
outcome.py
transition.py
lease.py
audit.py
idempotency.py
metric.py
candidate.py
verification.py
experiment_ledger.py
```

如果未来某个产品真正需要这些对象，必须先补充消费者、API 合同和验收场景，再加入对应层目录。

### 7.4 依赖方向

- Core 不 import HTTP、FastAPI、OpenAPI、BCS 具体 client 或外部来源 SDK。
- L1 `service_api.py` 定义 transport-agnostic Service API；delivery adapter 只解析和转换。
- L2/L3 只在请求和快照中保存外部身份、授权和资源引用，不复制其实现。
- L4 负责 Collet 和协作事实，不直接推进 G4 状态，不直接调用外部执行系统。
- L5/L6 只提供方法和产品枚举字段；不能绕过 L4 创建事实。
- G1 在写入和读取前提供授权判断；G2 负责幂等、并发和事件 DAG；G3 只生成查询投影；G4 负责 schema、版本和有限状态值校验。
- `plugin_api`、`task/forum`、`task/bbs` 不新增或修改文件。
- HTTP 和 OpenAPI 两个 adapter 必须调用同一份 Service API contract。

## 8. 数据库表与持久化层设计

### 8.1 结论：只新增两张表

当前三个 API 的持久化闭环只有两类事实：

1. **Collet 当前快照**：创建时写入，追加事件时更新，查询时读取。
2. **ColletEvent 事实日志**：创建时写入初始事件，事件接口追加，查询时按 `trunk_events` 过滤。

因此只新增：

| 表 | 必要原因 | 不再单独建表的内容 |
| --- | --- | --- |
| `ac_context_collab_collet` | 保存 `collab_context_id`、创建幂等键、创建参数、当前任务/方法/运行/进度快照和 `context_version` | Task、方法、执行者、资源引用和当前进度都保存在 JSON 字段 |
| `ac_context_collab_event` | 保存事件、`request_id`、`parent_event`、主干标记和 payload | 用户互动、进度、交付和验收信息都只是事件 payload，不是表 |

明确不新增：独立任务表、消息表、参与者表、角色表、方法表、状态迁移表、租约表、审计表、幂等表、产物表、Outcome 表。

### 8.2 两张表的最小字段

- `ac_context_collab_collet.context_spec`：创建请求中需要长期保留的 space、principal、method、executor、resource_refs 和 objective。
- `ac_context_collab_collet.current_snapshot`：当前 objective、status、executor、method 和 progress 的查询快照。
- `ac_context_collab_event.payload`：事件业务数据；用户互动消息直接放 `payload.content`。

`request_id` 通过两张表的唯一键实现幂等，不另建幂等表；`trunk_events` 是查询参数，不落表。

### 8.3 持久化层目录与职责

```text
apps/backend/src/agentclaw/community/core/task/context_collab/l4_collet_primitives/repository.py
```

只有一个 `ContextCollabRepositoryProtocol`，只覆盖三个操作：

```text
create_context(request) -> created context + initial event
append_event(collab_context_id, request) -> accepted event + new snapshot
get_current_status(collab_context_id, trunk_events) -> current status
```

不拆分 ContextRepository、TaskRepository、MessageRepository、EventRepository 等没有独立 API 消费者的 Repository。

### 8.4 三个 API 的事务规则

1. **创建**：同一事务插入一行 `ac_context_collab_collet` 和一行初始 `task.created` 事件；重复 `create_request_id` 返回原上下文。
2. **追加事件**：锁定 Collet 行，检查 `expected_version`、`parent_event` 和权限；同一事务插入 Event 并更新 Collet 的 `current_snapshot`、`current_event_id`、`context_version`。
3. **查询**：读取一行 Collet 和事件列表；按授权和 `trunk_events` 过滤，组合 `task_progress.events[]`；不写数据库。
4. **事件 DAG**：主事件 `parent_event=null`，派生事件引用同一上下文内已存在事件；`is_trunk` 在接纳时计算；事件顺序使用 `sequence_no`。
5. **幂等与并发**：相同 `(tenant, env, collab_context_id, request_id)` 只能成功一次；`expected_version` 不匹配直接返回冲突。
6. **错误传播**：唯一键、数据库写入和版本冲突必须向上返回，不能吞错。

### 8.5 DDL

以下 DDL 仅为当前三个 API 提供最小持久化，不为 ACCS 概念对象逐一建表。

```sql
-- OceanBase / MySQL mode. SQLite tests mirror the JSON columns as TEXT.

CREATE TABLE `ac_context_collab_collet` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw',
  `env` varchar(20) NOT NULL,
  `collab_context_id` varchar(128) NOT NULL,
  `create_request_id` varchar(256) NOT NULL,
  `context_spec` mediumtext NOT NULL COMMENT 'JSON: create request references and method fields',
  `current_snapshot` mediumtext NOT NULL COMMENT 'JSON: current objective, status, executor and progress',
  `current_event_id` varchar(128) DEFAULT NULL,
  `context_version` bigint(20) NOT NULL DEFAULT 1,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `gmt_modified` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_context_collab_collet_context` (`avernet_tenant`, `env`, `collab_context_id`) GLOBAL,
  UNIQUE KEY `uk_context_collab_collet_request` (`avernet_tenant`, `env`, `create_request_id`) GLOBAL,
  KEY `idx_context_collab_collet_modified` (`avernet_tenant`, `env`, `gmt_modified`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Minimal context collaboration aggregate snapshot';

CREATE TABLE `ac_context_collab_event` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `avernet_tenant` varchar(64) NOT NULL DEFAULT 'teamclaw',
  `env` varchar(20) NOT NULL,
  `collab_context_id` varchar(128) NOT NULL,
  `event_id` varchar(128) NOT NULL,
  `request_id` varchar(256) NOT NULL,
  `sequence_no` bigint(20) NOT NULL,
  `parent_event` varchar(128) DEFAULT NULL,
  `is_trunk` tinyint(1) NOT NULL DEFAULT 0,
  `event_kind` varchar(128) NOT NULL,
  `primitive_type` varchar(64) NOT NULL,
  `actor_ref` mediumtext NOT NULL COMMENT 'JSON actor reference',
  `visibility` varchar(32) NOT NULL,
  `payload` mediumtext NOT NULL COMMENT 'JSON event payload; user interaction uses payload.content',
  `context_version_after` bigint(20) NOT NULL,
  `occurred_at` datetime NOT NULL,
  `gmt_create` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_context_collab_event` (`avernet_tenant`, `env`, `collab_context_id`, `event_id`) GLOBAL,
  UNIQUE KEY `uk_context_collab_event_request` (`avernet_tenant`, `env`, `collab_context_id`, `request_id`) GLOBAL,
  UNIQUE KEY `uk_context_collab_event_seq` (`avernet_tenant`, `env`, `collab_context_id`, `sequence_no`) GLOBAL,
  KEY `idx_context_collab_event_parent` (`avernet_tenant`, `env`, `collab_context_id`, `parent_event`, `sequence_no`) GLOBAL,
  KEY `idx_context_collab_event_trunk` (`avernet_tenant`, `env`, `collab_context_id`, `is_trunk`, `sequence_no`) GLOBAL
) AUTO_INCREMENT_MODE = 'ORDER' DEFAULT CHARSET = utf8mb4
  COMMENT = 'Minimal append-only context collaboration event DAG';
```

### 8.6 三个 API 的最小读写路径

| API | 最小数据库动作 |
| --- | --- |
| `POST /api/v1/collaboration/task/context_collab` | 插入 1 行 Collet + 1 行初始 Event |
| `POST /api/v1/collaboration/task/context_collab/{collab_context_id}/events` | 插入 1 行 Event + 更新 1 行 Collet |
| `GET /api/v1/collaboration/task/context_collab/{collab_context_id}` | 读取 1 行 Collet + 读取 Event 列表，不写入 |

两个产品都使用这两张表；产品差异只体现在 `context_spec`、`current_snapshot`、`event_kind` 和 `payload` 的取值。

### 8.7 后续拆表条件

只有出现以下真实需求，才允许新增表：独立租约接口、跨上下文审计检索、参与者生命周期管理、独立大文本/产物检索、复杂迁移分析或事件归档分片。当前三个 API 不满足这些条件。

## 9. 外部系统映射

| 外部系统 | 本方案引用方式 | 本方案接纳的事实 | 权威边界 |
| --- | --- | --- | --- |
| BBS Topic / Post | L3 resource_refs + L4 event payload | 用户互动、主题或帖子引用 | BBS 拥有正文、Topic / Post 生命周期 |
| Task / Relay | L1 delivery contract + event payload | 启动、运行、阻塞、完成等外部事实 | Task / Relay 拥有 Work 生命周期 |
| BCS group / run | L1 delivery contract + event payload | 群互动或运行事实 | BCS 拥有群、路由和 Run 生命周期 |
| Bot / Service | L2 Agent / Capability；外部授权引用 | 指定 executor、执行能力和必要的委托引用 | Agent / BCS / 授权系统拥有身份与能力注册 |
| 代码仓库 / 文档 / PR | L3 Resource + event payload | 输入版本、交付引用和证据 | 原资源系统拥有正文、版本和访问授权 |

## 10. 安全、可靠性与一致性

### 10.1 G1

- Authentication 在 delivery / identity 边界完成，核心只接收已解析的 actor context。
- Authorization 校验创建 Collet、追加互动/进度事件和查询最新状态的权限。
- Capability 只表示 Agent 能力；Membership / Grant / Policy 由外部权威提供。
- 现有 Audit 能力记录 actor、action、space、collet、version 和时间；本域不建 Audit 表。
- 查询响应按当前授权和事件 visibility 过滤，不因加入 Collet 自动扩大资源权限。

### 10.2 G2

- 创建和事件接纳都必须支持 `request_id` 幂等；事件通过 `parent_event` 保留派生关系。
- `expected_version` 与 Collet version 防止并发请求覆盖当前快照。
- 外部回报必须先转换为已校验的 ColletEvent；本方案不负责启动或恢复外部执行。

### 10.3 G3

- 查询响应可重建、可按权限过滤、可过期，不反向写权威对象。
- 首版不引入 Metric、Candidate、Verification 和 Experiment Ledger；质量指标和自动演进属于后续独立方案。

### 10.4 G4

- Collet、ColletEvent 和 API schema 使用显式版本字段；不创建 Profile 对象。
- `current_snapshot.status` 只允许协议定义的有限状态值；追加事件时在同一事务内更新。
- 不创建独立 Transition Record 或 StateMachine 表。
- 未知字段、未知方法值、未注册 event_kind 或不允许的 payload 组合必须拒绝。
- Conformance 是 Service API / Plugin API 的测试要求，不在运行时创建对象。

## 11. 测试与验收

### 11.1 对象与层次合同测试

- L1：Service API 与 HTTP/OpenAPI adapter 的输入、错误和响应映射；不为当前首版增加 A2A 对象测试。
- L2：Principal、Agent、Capability 的引用和指定执行校验；委托由外部授权合同覆盖。
- L3：Space / Resource 引用、版本和授权边界；不复制外部正文。
- L4：Collet 的创建、快照更新和事件 payload 语义；不测试独立 L4 对象表。
- L5：Single/Open/Group 方法配置和 executor 引用；不测试独立 lease。
- L6：`product_kind` / `workflow_kind` 字段值合法性。
- G1-G4：Authorization、幂等、查询投影、Schema、Version 和有限状态值；不创建治理或状态对象表。

### 11.2 三个接口验收

1. 创建接口创建 1 行 Collet 和 1 行初始 ColletEvent；任务目标、方法、执行者和进度都在 Collet JSON 快照中。
2. 创建接口拒绝没有 executor、没有 responsible、没有 Capability / Authorization 的指定执行请求。
3. 事件接口接收用户互动事件，事件名为 `user_interaction.posted`，正文直接在 `payload.content`，不出现 `content_ref`。
4. 每个事件都带 `parent_event`；`trunk_events=true` 只筛选主干，不改变统计和状态。
5. 查询返回 current_snapshot、current_event 和 `task_progress.events[]`，不使用 `steps[]`。
6. 查询响应不会反向改写 Collet 快照。

### 11.3 参考端到端场景

| 编号 | 场景 | 判定 |
| --- | --- | --- |
| A01 | 创建指定执行 Collet | 生成 1 行 Collet（context_spec/current_snapshot 包含上述信息）和 1 行初始 ColletEvent |
| A02 | 用户新增互动消息 | 生成 1 行 ColletEvent，payload 直接使用 content，并更新 Collet 快照 |
| A03 | 指定 Agent 开始工作 | Capability 和外部授权均通过，追加 `task.progress.updated` 进度事件 |
| A04 | 外部 Task 完成 | 追加 `task.completed` 事件并更新 Collet 快照 |
| A05 | 提交 PR | 在事件 payload 中保存 PR / repository 引用 |
| A06 | 责任人验收 | 追加 `task.accepted` / `task.rejected` 事件，允许状态值合法迁移 |
| A07 | 查询主事件 | `trunk_events=true` 仅返回 DAG 主事件 |
| A08 | 查询全部事件 | 默认返回全部可见 ColletEvent |
| A09 | 并发写入 | 旧 `expected_version` 或重复 request_id 被正确拒绝 / 幂等返回 |
| A10 | 权限降级 | 查询响应过滤受限 Resource 和历史事件，不泄露内容 |

## 12. 分阶段交付

| 阶段 | 内容 | 明确不包含 |
| --- | --- | --- |
| P1 | 两张表、一个持久化协议、三个 API、最小授权/并发/事件 DAG 校验和查询投影 | 任何独立 Task/Message/Participant/Method/Profile/StateMachine 表 |
| P2 | 根据真实 API 消费者补充外部资源或执行系统适配 | 复制外部身份、授权、资源和执行生命周期 |

除非三个 API 已经证明存在独立查询、独立生命周期或独立吞吐需求，否则不再拆表或新增 ACCS 对象。

## 13. 兼容、迁移与风险

### 13.1 兼容策略

- 保留 `/api/v1/collaboration/task/context_collab` 和 `/openapi/v1/collaboration/task/context_collab` 三个接口。
- 保留 `collab_context_id` 作为 Collet ID 的 API 兼容字段。
- 保留 `parent_event`、`trunk_events` 和 `task_progress.events[]`。
- `user_interaction.posted` 作为用户互动消息事件名，payload 直接使用 `content`。
- 当前接口只接受本版定义的字段，不新增旧字段兼容转换逻辑。

### 13.2 主要风险与控制

| 风险 | 控制 |
| --- | --- |
| ACCS 对象过度迁移导致模型膨胀 | 采用清单明确“当前采用 / 外部引用 / 不引入”；新增对象必须有消费者和验收场景 |
| Space、Collet、Resource 混淆 | L3 / L4 分离；Collet 只引用 Space 和 Resource |
| Capability 被误当成授权 | 强制 G1 Authorization + 外部 Membership / Grant + RoleBinding |
| Event 成为第二套领域模型 | ColletEvent 只保存 event_kind、payload 和必要关系字段，不创建独立 primitive 生命周期 |
| 查询响应变成第二权威 | 只允许由已接纳 L4 事实重建，不允许反向写入 |
| 目录按层但依赖倒置 | 依赖合同而非目录顺序；适配器和 Composition Root 维持独立 |
| 未来一次性迁移全部 ACCS 对象 | 按实际产品需求分阶段增加，并补齐 schema、API 和测试 |

## 14. 评审问题

1. BBS 公开讨论是否确认采用 `Open + Group`，团队任务代办是否确认采用 `Single`，以及各自的准入、退出和关闭规则是否满足产品要求？
2. 当前 Agent、Capability、Space、Resource 和授权 / 委托事实的权威服务分别是什么，本域是否只读引用？
3. 外部 Task / Relay 的启动是否已有可复用 Service API；如果没有，L1 只保留 delivery contract，不新增 A2A 对象。
4. `ForumPublicDiscussionWorkflow` 与 `TeamTodoExecutionWorkflow` 的状态、迁移和责任主体是否需要补充产品特有状态？
5. BBS Topic / Post 的正文、版本和可见性是否继续由 BBS 权威持有，并仅将用户互动映射为 L4 `Message`？

## 15. 参考文档

- [Avernet ACCS：Agent Collaboration & Coordination Stack（Yuque）](https://yuque.antfin.com/securitytec/otbct4/kgqllm98liao649f)
- [架构宪章](../../../../docs/arch/arch.rules.md)
- [CI 门禁](../../../../docs/arch/ci.enforce.md)
- [Context Boundary 格式](../../../../docs/arch/context-boundary-format.md)
- [Plugin Protocol conformance](../../../../docs/arch/protocol-contract-tests.md)
- [BBS 求助任务化方案](../2026-11-09-bbs-help-task-upgrade/spec.md)
- [BBS 任务类型](../../../../docs/specs/2026-10-27-bbs-v4-topic-types/spec.md)
- [BCS 自定义协作 YAML](../../../../apps/bcs/crates/tools/bcs-cli/bcs-coordination/references/custom-collaboration-schema.md)

本方案只记录目标设计和实施准入，不表示上述能力或接口已经存在于代码中。
