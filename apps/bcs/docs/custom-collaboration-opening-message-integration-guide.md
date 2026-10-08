# BCS 自定义开场消息接入指南

本文面向接入 BCS Group 和 one-shot StateMachine Run 开场消息能力的系统，说明如何用普通文本、
消息区内联卡片或副屏作为开场消息，以及两种互相隔离的配置作用域：

- **Group 开场消息**：建群或更新 Group 时配置。Chat 和 ManagerWorker 在新 Session 创建时展示
  一次；StateMachine 在每次 configured Run 开始时展示一次。
- **one-shot Run 开场消息**：在 Chat 或 ManagerWorker Session 中临时提交 StateMachine Run 时配置，
  只影响本次 Run 及其 rerun lineage。

下文将由 StateMachine Group 绑定定义启动的 Run 称为 configured Run，用于和临时提交的 one-shot
Run 区分。

## 1. 接入范围

该能力适用于：

- Group 开场消息：`group_kind` 为 `normal`，且 `collaboration.strategy` 为 `chat`、
  `manager_worker` 或 `state_machine`；
- one-shot Run 开场消息：当前 Session 属于 `chat` 或 `manager_worker` Normal Group，且调用方具有
  one-shot StateMachine 执行权限；
- 使用 `card` 或 `panel` 时，前端已经通过现有 AixUI 组件注册机制注册目标组件；普通文本不需要
  注册组件。

DM 群不支持 `opening_message`。Chat 和 ManagerWorker 不配置时不展示开场白；StateMachine
不配置时继续打开默认的 `bcsPanel.StateMachineRunView` 副屏。

## 2. 工作方式

### 2.1 Chat 和 ManagerWorker Group 开场消息

Chat 和 ManagerWorker 在新 Session 创建时，BCS 按以下顺序处理：

1. 生成 Session ID；
2. 使用 Group 和 Session 信息渲染 `opening_message`；
3. 将渲染后的最终消息持久化到 Session 消息历史；
4. 将同一条消息发送给当前前端连接。

开场白不会发送给 Bot，并且会从 Bot 的历史/上下文回放中排除。Session 重连、重新激活、成员加入和
页面刷新不会创建第二条开场白。页面刷新直接读取已经持久化的最终消息，不使用 Group 当前配置重新渲染。
Chat 或 ManagerWorker Session 内临时启动的 one-shot StateMachine 不会读取该 Session 级
`opening_message`。one-shot 请求显式提供 Run 级 `opening_message` 时使用请求配置；省略或传
`null` 时使用 StateMachine 默认副屏。

### 2.2 Configured StateMachine Group 开场消息

StateMachine Group 每次 configured Run 开始时，BCS 按以下顺序处理：

1. 生成本次执行的 Group ID、Session ID 和 Run ID；
2. 使用这些运行信息渲染 Group 上配置的 `opening_message`；
3. 将渲染后的最终消息持久化到 Session 消息历史；
4. 将同一条消息发送给当前前端连接；
5. 前端显示普通文本，或由 AixUI 解析器按 `type=card|panel` 渲染内联卡片或打开副屏；
6. BCS 开始调度 StateMachine 节点。

即使前端在 Run 开始后才打开页面，也应通过 Session 历史消息恢复同一条开场消息；`panel` 消息会
据此恢复对应副屏。

### 2.3 one-shot StateMachine Run 开场消息

Chat 或 ManagerWorker Session 中提交 one-shot StateMachine Run 时，配置来源优先级如下：

| 请求级 `opening_message` | Group `opening_message` | 最终行为 |
| --- | --- | --- |
| 显式提供 | 无论是否存在 | 使用请求级配置 |
| 省略或 `null` | 无论是否存在 | 使用默认 `bcsPanel.StateMachineRunView` |

请求级配置不会修改 Group 或 Session，也不会影响同一 Session 中随后独立提交的其他 one-shot Run。
BCS 保存原始请求配置用于故障恢复和 rerun，并把渲染后的最终消息写入 Session 历史；页面刷新只读取
已经持久化的最终消息。

## 3. 创建带自定义开场消息的协作群

使用 BCS OpenAPI 建群接口：

```http
POST /openapi/v1/collaboration/groups
Content-Type: application/json
```

鉴权方式沿用调用方已有的 BCS OpenAPI 接入配置。
以下 `partnerCard.*` 和 `partnerPanel.*` 组件名仅为示例，接入方需要替换成前端实际注册的资源名。

### 3.1 自由聊天群使用普通文本

自由聊天群使用 `strategy=chat`。字符串形式的 `opening_message` 会作为普通文本显示在消息区，不会
打开副屏：

```json
{
  "group_kind": "normal",
  "name": "发布讨论群",
  "context": "讨论本次发布方案与风险",
  "driver_bot_uuid": "bot_host",
  "participants": [
    {
      "actor_id": "bot_host",
      "role": "driver"
    },
    {
      "actor_id": "bot_reviewer",
      "role": "consultant"
    }
  ],
  "collaboration": {
    "strategy": "chat"
  },
  "opening_message": "欢迎来到 {{bcs.group_name}}，请直接描述需要讨论的问题。"
}
```

每次创建新 Session 时，BCS 会把 `group_name` 替换为当时的 Group 名称，并将最终文本持久化一次。

### 3.2 任务协作群使用内联卡片

任务协作群使用 `strategy=manager_worker`。下面的 `card` 显示在消息区中，不创建副屏 Tab：

```json
{
  "group_kind": "normal",
  "name": "发布任务协作群",
  "context": "分工检查发布计划",
  "driver_bot_uuid": "bot_manager",
  "participants": [
    {
      "actor_id": "bot_manager",
      "role": "manager"
    },
    {
      "actor_id": "bot_executor",
      "role": "worker"
    }
  ],
  "collaboration": {
    "strategy": "manager_worker"
  },
  "opening_message": {
    "type": "card",
    "component": "partnerCard.TaskCollaborationGuide",
    "params": {
      "groupId": "{{bcs.group_id}}",
      "sessionId": "{{bcs.session_id}}",
      "groupName": "{{bcs.group_name}}"
    }
  }
}
```

Chat 和 ManagerWorker 的 Group 开场消息都使用 Session 作用域，只能引用第 6 节中标记为
“Session 作用域支持”的变量。它们也可以使用 `panel` 打开副屏，但不能引用 `{{bcs.run_id}}`。

### 3.3 StateMachine 群使用副屏

下面展示与 StateMachine 自定义协作和副屏有关的完整请求骨架。`content_yaml` 需要替换成合法的
自定义协作定义，`participant_bindings` 中的名称需要与定义中的参与者槽位一致。

```json
{
  "group_kind": "normal",
  "name": "发布检查",
  "context": "检查本次发布方案与风险",
  "driver_bot_uuid": "bot_planner",
  "participants": [
    {
      "actor_id": "bot_planner",
      "role": "driver"
    },
    {
      "actor_id": "bot_reviewer",
      "role": "consultant"
    }
  ],
  "collaboration": {
    "strategy": "state_machine",
    "definition": {
      "content_yaml": "<完整的自定义协作 YAML>"
    },
    "participant_bindings": [
      {
        "binding": "planner",
        "actor_ids": ["bot_planner"]
      },
      {
        "binding": "reviewer",
        "actor_ids": ["bot_reviewer"]
      }
    ]
  },
  "opening_message": {
    "type": "panel",
    "component": "partnerPanel.CollaborationRunView",
    "params": {
      "groupId": "{{bcs.group_id}}",
      "sessionId": "{{bcs.session_id}}",
      "runId": "{{bcs.run_id}}",
      "groupName": "{{bcs.group_name}}",
      "sessionName": "{{bcs.session_name}}",
      "businessScene": "release_review"
    },
    "tab": {
      "id": "partner-run-{{bcs.run_id}}",
      "title": "{{bcs.group_name}} / {{bcs.session_name}}",
      "closable": true
    }
  }
}
```

创建成功后，返回的 Group detail 中会保留原始 `opening_message` 配置。对于可以立即执行的定义，BCS
会启动初始 StateMachine Run，并生成类似下面的消息：

```xml
<AixUI
  type="panel"
  component="partnerPanel.CollaborationRunView"
  tab='{"closable":true,"id":"partner-run-run_123","title":"发布检查 / 发布会话"}'
  params='{"businessScene":"release_review","groupId":"bcs_grp_123","groupName":"发布检查","runId":"run_123","sessionId":"bcs_grp_123:session_1","sessionName":"发布会话"}'
/>
```

上面的写法由 BCS 生成 AixUI 文本，并负责其中的模板替换、JSON 序列化和属性转义。接入系统也可以
直接使用字符串配置完整的 AixUI 消息，具体见下文。

## 4. 在当前 Session 提交 one-shot StateMachine 开场消息

调用方可以先查询当前 Bot 是否具有提交权限：

```http
GET /sessions/{session_id}/state-machine-permission
Authorization: Bearer <bot_token>
```

权限通过后调用 one-shot 提交接口：

```http
POST /sessions/{session_id}/state-machine-runs
Authorization: Bearer <bot_token>
Content-Type: application/json
```

请求中的 `opening_message` 与 Group API 使用相同联合类型，支持字符串、`card` 和 `panel`：

```json
{
  "definition_yaml": "<完整的 StateMachine 自定义协作 YAML>",
  "participant_bindings": {
    "planner": {
      "source": "manual",
      "bot_ids": ["bot_planner"]
    },
    "reviewer": {
      "source": "manual",
      "bot_ids": ["bot_reviewer"]
    }
  },
  "input": {
    "question": "检查本次发布方案"
  },
  "opening_message": {
    "type": "panel",
    "component": "partnerPanel.OneShotRunView",
    "params": {
      "groupId": "{{bcs.group_id}}",
      "sessionId": "{{bcs.session_id}}",
      "runId": "{{bcs.run_id}}",
      "businessScene": "release_review"
    },
    "tab": {
      "id": "one-shot-{{bcs.run_id}}",
      "title": "{{bcs.session_name}}",
      "closable": true
    }
  }
}
```

省略 `opening_message` 或显式传 `null` 都表示本次 Run 使用默认 StateMachine 副屏。该字段是创建参数，
没有 PATCH 的三态语义，也不会清空或修改 Group 上的 `opening_message`。

成功响应继续使用现有 `StateMachineRunView`，不返回原始请求级配置。原始配置持久化在内部字段
`bcs_state_machine_runs.opening_message_override_json`；configured StateMachine Run 的该字段保持
`NULL`，继续以 Group 配置作为事实来源。最终渲染内容通过 Session messages 返回，并使用稳定的
`client_msg_id=<run_id>:000-panel` 和 `message_type=state_machine_panel`。

### 4.1 CLI 提交 panel

`bcs-cli` 第一期只提供结构化 `panel` 参数；字符串和 `card` 可以通过 HTTP API 或 SDK 提交：

```bash
bcs-cli collaborate run workflow.yaml \
  --session <session_id> \
  --binding planner=bot_planner \
  --binding reviewer=bot_reviewer \
  --input @input.json \
  --panel-component partnerPanel.OneShotRunView \
  --panel-params @panel-params.json \
  --panel-tab-id 'one-shot-{{bcs.run_id}}' \
  --panel-tab-title '一次性协作' \
  --panel-tab-closable true
```

- `--panel-params` 必须是 JSON object，并支持 `@file`；
- `--panel-params` 和所有 `--panel-tab-*` 参数都依赖 `--panel-component`；
- 未提供 `--panel-component` 时，CLI 不发送 `opening_message`；
- 仅提供了至少一个 Tab 参数时，CLI 才发送 `tab`。

### 4.2 恢复和 rerun

已有 Run 的开场消息按以下顺序恢复：

1. 已存在匹配 Run ID 或稳定 client message ID 的历史消息时，直接复用该最终内容；
2. 历史消息缺失但 Run 保存了请求级配置时，使用该 Run 的上下文重新渲染；
3. one-shot Run 没有请求级配置时，生成默认 StateMachine 副屏。

Failed one-shot Run 的 rerun 会复制 source Run 的原始配置，并使用新的 Run ID 重新渲染。rerun 接口
不允许临时替换开场消息。对同一个 source Run 重复请求 rerun 时，BCS 复用已经创建的 child Run 和其
历史开场消息，不根据 Group 当前名称或配置生成另一个版本。

## 5. `opening_message` 字段

Group 配置和 one-shot 请求共用同一种 `opening_message` 结构，支持结构化对象和字符串两种写法。
可用模板变量取决于消息属于 Session 还是 StateMachine Run，具体见第 6 节。本节包含
`{{bcs.run_id}}` 的示例只适用于 configured StateMachine Run 或 one-shot Run。

| 写法 | 展示效果 | 是否需要前端注册组件 | 是否支持 `tab` |
| --- | --- | --- | --- |
| 普通字符串 | 消息区文本 | 否 | 不适用 |
| `type=card` 对象 | 消息区内联卡片 | 是 | 否 |
| `type=panel` 对象 | 打开副屏 | 是 | 是 |

字符串也可以是完整的 AixUI 消息，此时展示效果和组件依赖由字符串内容决定。

### 5.1 结构化对象

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `type` | 是 | 使用 `panel` 打开副屏；`card` 表示在消息区内联展示。 |
| `component` | 是 | 前端已经注册的 AixUI 组件资源名，最长 256 字节。 |
| `params` | 否 | 传给业务组件的 JSON object，可以包含嵌套对象和数组。 |
| `tab.id` | 否 | 副屏 Tab 标识。建议包含 `{{bcs.run_id}}`，避免不同 Run 复用同一个 Tab。 |
| `tab.title` | 否 | 副屏 Tab 标题。 |
| `tab.closable` | 否 | 是否允许用户关闭 Tab。 |

`component` 表示资源名，`panel` 表示展示位置。不要传 `position`、远程脚本地址、`cdn` 或 `entry`。
BCS 不负责下载或注册业务组件。

`card` 使用同一结构，但不能携带 `tab`：

```json
{
  "opening_message": {
    "type": "card",
    "component": "partnerCard.OneShotSummary",
    "params": {
      "runId": "{{bcs.run_id}}"
    }
  }
}
```

### 5.2 字符串消息

字符串可以是普通文本：

```json
{
  "opening_message": "协作 {{bcs.group_name}} 已开始，Run ID：{{bcs.run_id}}"
}
```

也可以直接拼接完整的 AixUI 副屏消息：

```json
{
  "opening_message": "<AixUI\n  type=\"panel\"\n  component=\"partnerPanel.CollaborationRunView\"\n  tab='{\"id\":\"partner-run-{{bcs.run_id}}\",\"title\":\"协作执行\",\"closable\":true}'\n  params='{\"businessScene\":\"release_review\",\"groupId\":\"{{bcs.group_id}}\",\"sessionId\":\"{{bcs.session_id}}\",\"runId\":\"{{bcs.run_id}}\"}'\n/>"
}
```

渲染后会得到：

```xml
<AixUI
  type="panel"
  component="partnerPanel.CollaborationRunView"
  tab='{"id":"partner-run-run_123","title":"协作执行","closable":true}'
  params='{"businessScene":"release_review","groupId":"bcs_grp_123","sessionId":"bcs_grp_123:session_1","runId":"run_123"}'
/>
```

字符串中的受支持模板变量会先被替换；不含模板变量时，字符串会逐字节原样发送。字符串模式下 BCS
不会解析或重新序列化 AixUI 属性中的 JSON，也不会自动转义变量值。如果把 Group 名称、Session 名称
等任意文本放进 `params` 或 `tab` JSON，调用方需要自行保证 JSON 和单引号属性合法。

## 6. 模板变量

支持以下大小写敏感的完整占位符：

| 变量 | Session 作用域 | StateMachine Run 作用域 | 渲染值 |
| --- | --- | --- | --- |
| `{{bcs.group_id}}` | 支持 | 支持 | 当前 Group ID |
| `{{bcs.session_id}}` | 支持 | 支持 | 当前 Session ID |
| `{{bcs.run_id}}` | 不支持 | 支持 | 当前 StateMachine Run ID |
| `{{bcs.group_name}}` | 支持 | 支持 | 当前 Group 名称；未命名时为空字符串 |
| `{{bcs.session_name}}` | 支持 | 支持 | 当前 Session 名称；未命名时为空字符串 |

- Chat 和 ManagerWorker 的 **Group 开场消息**使用 Session 作用域，因此不能引用
  `{{bcs.run_id}}`；
- StateMachine Group 的 configured Run，以及 Chat 或 ManagerWorker Session 中提交的
  **one-shot Run 开场消息**使用 StateMachine Run 作用域，可以引用 `{{bcs.run_id}}`。

结构化对象中，模板变量可以出现在：

- `params` 内任意层级的字符串值；
- `tab.id`；
- `tab.title`。

`type`、`component` 和 JSON object 的 key 不参与模板替换。BCS 只替换上表列出的完整占位符，
不支持表达式、条件、默认值或任意对象路径。静态业务参数可以直接作为 `params` 的普通值传入。

## 7. 更新 Group 开场消息或让 one-shot 使用默认副屏

修改已经存在的 Normal Group：

```http
PATCH /openapi/v1/collaboration/groups/{group_id}
Content-Type: application/json
```

替换 Group 开场消息（以下以 `panel` 为例）：

```json
{
  "opening_message": {
    "type": "panel",
    "component": "partnerPanel.CollaborationRunViewV2",
    "params": {
      "runId": "{{bcs.run_id}}"
    },
    "tab": {
      "id": "partner-run-{{bcs.run_id}}",
      "title": "协作执行",
      "closable": true
    }
  }
}
```

清除 Group 自定义开场消息：

```json
{
  "opening_message": null
}
```

PATCH 请求省略 `opening_message` 表示不修改。传 `null` 后，Chat 和 ManagerWorker 后续创建的 Session
不再展示 Group 开场消息；StateMachine Group 后续开始的 configured Run 恢复默认 BCS 副屏。更新
只影响未来的 Session 或 Run，已经持久化的历史开场消息不会变化。

one-shot 创建请求省略 `opening_message` 或显式传 `null`，只表示本次 Run 使用默认 BCS 副屏，不会
修改 Group 配置，也不会影响下一次 one-shot 提交。

## 8. 前端接入要求

承载 BCS 聊天页面的前端需要完成以下工作：

1. 普通文本无需注册组件；使用 `card` 或 `panel` 时，将请求中的 `component` 注册到承载 BCS
   聊天页面的 AixUI 组件注册表；
2. 保证聊天消息继续经过现有 `aixUiPlugin` 解析，以识别 `card`、`panel` 或字符串形式的 AixUI；
3. 在组件中通过现有 AixUI 机制读取 `params`。Session 级组件可以使用 `groupId`、`sessionId`；
   StateMachine Run 级组件还可以使用 `runId` 查询或订阅执行数据；
4. 页面首次打开或刷新时加载 Session 历史消息，以恢复已经持久化的文本、卡片或副屏开场消息；
5. `panel` 用于多个 Run 时，为不同 Run 使用不同的 `tab.id`，推荐包含 `{{bcs.run_id}}`。

BCS 只验证 `component` 的格式，不验证该组件是否已经在某个前端版本中注册。组件未注册不会阻止
Session 创建或 StateMachine 执行，但前端无法正常渲染对应卡片或副屏。

联调时可以使用以下接口查询持久化结果：

```http
GET /openapi/v1/collaboration/groups/{group_id}
GET /openapi/v1/collaboration/groups/{group_id}/sessions
GET /openapi/v1/collaboration/sessions/{session_id}/messages
```

Group detail 只返回 Group 上配置的原始模板；one-shot Run 的原始 override 不通过公开
`StateMachineRunView` 返回。Session messages 返回已经完成变量替换的最终消息。不要使用 Group 当前
配置或 Run override 重新推导已经存在历史消息的 Session 或 Run 开场内容。

## 9. 校验规则和错误处理

不合法配置返回：

```http
400 Bad Request
```

错误码为 `invalid_opening_message`。常见原因包括：

- Group 是 DM Group；
- Chat 或 ManagerWorker 的 Group 开场消息使用了 `{{bcs.run_id}}`；one-shot Run 可以使用该变量；
- 字符串为空或只包含空白；
- 使用了未知模板变量；
- 模板变量缺少结束的 `}}`；
- `component` 为空、超过 256 字节，或包含空白、控制字符、引号、`<`、`>`；
- `type=card` 时仍传入 `tab`；
- 对象包含未声明字段；
- 原始配置或渲染结果超过 64 KiB UTF-8 字节；
- 完整 `OpeningMessage` 的 JSON 编码结果超过 MySQL `TEXT` 的 65,535 字节上限。

模板不是密钥容器。具备 Group detail 读取权限的调用方可以读取 Group 原始配置；one-shot 最终渲染
内容会进入 Session 历史。不要在任何 `opening_message` 或 `params` 中放置 token、密码或其他敏感信息。

## 10. 联调检查清单

### 10.1 Group 开场消息

- 建群返回成功，并且 Group detail 中的原始 `opening_message` 与请求一致；
- Chat 或 ManagerWorker 在每个新 Session 中只生成一条 `message_type=opening_message`、
  `client_msg_id=<session_id>:000-opening` 的 Group 开场消息；
- 普通文本显示在消息区，`card` 在消息区内联展示，`panel` 打开副屏；
- StateMachine Group 的初始或后续 configured Run 使用 Group 配置，且 Run 的请求级 override 保持为空；
- 修改 Group 配置后，只影响未来的 Session 或 configured Run，旧历史消息保持不变；
- Group PATCH 传 `opening_message: null` 后，Chat/ManagerWorker 新 Session 不展示 Group 开场消息，
  StateMachine Group 的新 Run 恢复默认副屏。

### 10.2 one-shot Run 开场消息

- Chat 或 ManagerWorker Session 的调用方具有 one-shot 权限，提交接口返回成功并生成稳定的 Run ID；
- 显式提交 `opening_message` 时使用请求级组件和参数，不读取 Group 的 Session 开场消息；
- 省略或传 `null` 时使用默认 `bcsPanel.StateMachineRunView`；
- 公开 `StateMachineRunView` 不包含原始 override；
- 同一 Session 中下一次独立提交不会继承上一次 Run 的 override；
- Failed Run 的 rerun 生成新 Run ID，并用新 ID 重新渲染 source override；
- 对同一 source 重复请求 rerun 时复用同一 child Run 和已经持久化的开场消息。

### 10.3 前端和历史消息

- Session 历史中每个 Run 只有一条 `message_type=state_machine_panel`、
  `client_msg_id=<run_id>:000-panel` 的逻辑开场消息；
- 消息中的 BCS 模板变量均已替换，没有残留 `{{bcs.*}}`；
- 普通文本开场消息无需组件即可展示；
- `type=card` 时消息保持内联，不创建副屏 Tab；
- `type=panel` 时，`component` 与前端注册名完全一致；
- 业务组件收到与作用域匹配的 `params.sessionId`、`params.groupId`、可选 `params.runId` 和业务静态参数；
- 实时事件与 Session 历史中的最终内容一致，页面刷新后可以恢复同一文本、卡片或副屏；
- Bot 不会收到开场消息，Bot 历史和上下文回放中也不存在该消息。
