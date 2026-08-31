# BCS 自定义协作开场白控制副屏接入指南

本文面向接入 BCS 自定义协作群的系统，说明如何在建群时指定开场消息。Chat 和
ManagerWorker 在新 Session 创建时展示一次；StateMachine 在每次 Run 开始时展示一次。

## 1. 接入范围

该能力适用于：

- `group_kind` 为 `normal`；
- `collaboration.strategy` 为 `chat`、`manager_worker` 或 `state_machine`；
- 前端已经通过现有 AixUI 组件注册机制注册目标副屏组件。

DM 群不支持 `opening_message`。Chat 和 ManagerWorker 不配置时不展示开场白；StateMachine
不配置时继续打开默认的 `bcsPanel.StateMachineRunView` 副屏。

## 2. 工作方式

Chat 和 ManagerWorker 在新 Session 创建时，BCS 按以下顺序处理：

1. 生成 Session ID；
2. 使用 Group 和 Session 信息渲染 `opening_message`；
3. 将渲染后的最终消息持久化到 Session 消息历史；
4. 将同一条消息发送给当前前端连接。

开场白不会发送给 Bot，并且会从 Bot 的历史/上下文回放中排除。Session 重连、重新激活、成员加入和
页面刷新不会创建第二条开场白。页面刷新直接读取已经持久化的最终消息，不使用 Group 当前配置重新渲染。

StateMachine 每次 Run 开始时，BCS 按以下顺序处理：

1. 生成本次执行的 Group ID、Session ID 和 Run ID；
2. 使用这些运行信息渲染 Group 上配置的 `opening_message`；
3. 将渲染后的最终消息持久化到 Session 消息历史；
4. 将同一条消息发送给当前前端连接；
5. 前端的 AixUI 解析器根据 `type=panel` 打开副屏，并从组件注册表加载 `component`；
6. BCS 开始调度 StateMachine 节点。

即使前端在 Run 开始后才打开页面，也应通过 Session 历史消息恢复同一条开场消息和副屏。

## 3. 创建带自定义副屏的协作群

使用 BCS OpenAPI 建群接口：

```http
POST /openapi/v1/collaboration/groups
Content-Type: application/json
```

鉴权方式沿用调用方已有的 BCS OpenAPI 接入配置。

下面只展示与自定义协作和副屏有关的完整请求骨架。`content_yaml` 需要替换成合法的自定义协作定义，
`participant_bindings` 中的名称需要与定义中的参与者槽位一致。

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

## 4. `opening_message` 字段

用于打开副屏时，`opening_message` 支持结构化对象和字符串两种写法。

### 4.1 结构化对象

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

### 4.2 字符串消息

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

## 5. 模板变量

支持以下大小写敏感的完整占位符：

| 变量 | 渲染值 |
| --- | --- |
| `{{bcs.group_id}}` | 当前 Group ID |
| `{{bcs.session_id}}` | 当前 Session ID |
| `{{bcs.run_id}}` | 当前 StateMachine Run ID；仅 `state_machine` 可用 |
| `{{bcs.group_name}}` | 当前 Group 名称；未命名时为空字符串 |
| `{{bcs.session_name}}` | 当前 Session 名称；未命名时为空字符串 |

结构化对象中，模板变量可以出现在：

- `params` 内任意层级的字符串值；
- `tab.id`；
- `tab.title`。

`type`、`component` 和 JSON object 的 key 不参与模板替换。BCS 只替换上表列出的完整占位符，
不支持表达式、条件、默认值或任意对象路径。静态业务参数可以直接作为 `params` 的普通值传入。

## 6. 更新或恢复默认开场消息

修改已经存在的 Normal Group：

```http
PATCH /openapi/v1/collaboration/groups/{group_id}
Content-Type: application/json
```

替换副屏配置：

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

恢复 BCS 默认副屏：

```json
{
  "opening_message": null
}
```

PATCH 请求省略 `opening_message` 表示不修改。更新只影响随后创建的 Session（Chat、ManagerWorker）
或随后开始的 Run（StateMachine）；已经持久化的历史开场消息不会变化。

## 7. 前端接入要求

承载 BCS 聊天页面的前端需要完成以下工作：

1. 将请求中的 `component` 注册到承载 BCS 聊天页面的 AixUI 组件注册表；
2. 保证聊天消息继续经过现有 `aixUiPlugin` 解析；
3. 在组件中通过现有 AixUI 机制读取 `params`，其中的 `runId` 可用于查询或订阅本次执行数据；
4. 页面首次打开或刷新时加载 Session 历史消息，以恢复已经持久化的副屏开场消息；
5. 为不同 Run 使用不同的 `tab.id`。推荐直接包含 `{{bcs.run_id}}`。

BCS 只验证 `component` 的格式，不验证该组件是否已经在某个前端版本中注册。组件未注册不会阻止
StateMachine 执行，但前端无法正常渲染对应副屏。

联调时可以使用以下接口查询持久化结果：

```http
GET /openapi/v1/collaboration/groups/{group_id}
GET /openapi/v1/collaboration/groups/{group_id}/sessions
GET /openapi/v1/collaboration/sessions/{session_id}/messages
```

Group detail 返回原始模板；Session messages 返回已经完成变量替换的最终消息。不要使用 Group 当前
配置重新推导旧 Session 或 Run 的副屏内容。

## 8. 校验规则和错误处理

不合法配置返回：

```http
400 Bad Request
```

错误码为 `invalid_opening_message`。常见原因包括：

- Group 是 DM Group；
- Chat 或 ManagerWorker 使用了 `{{bcs.run_id}}`；
- 字符串为空或只包含空白；
- 使用了未知模板变量；
- 模板变量缺少结束的 `}}`；
- `component` 为空、超过 256 字节，或包含空白、控制字符、引号、`<`、`>`；
- `type=card` 时仍传入 `tab`；
- 对象包含未声明字段；
- 原始配置或渲染结果超过 64 KiB UTF-8 字节。

模板不是密钥容器。具备 Group detail 读取权限的调用方可以读取原始配置，不要在 `opening_message`
中放置 token、密码或其他敏感信息。

## 9. 联调检查清单

- 建群返回成功，并且 Group detail 中的 `opening_message.component` 与预期一致；
- 初始 Run 或后续 Run 已生成稳定的 `run_id`；
- Session 历史中存在一条 `message_type=state_machine_panel` 的开场消息；
- 消息中的五类 BCS 模板变量均已替换，没有残留 `{{bcs.*}}`；
- `type` 为 `panel`，`component` 与前端注册名完全一致；
- 业务组件收到正确的 `params.runId`、`params.groupId` 和业务静态参数；
- 页面刷新后可以从历史消息恢复副屏；
- 修改 Group 配置后，新 Run 使用新副屏，旧 Run 的历史消息保持不变；
- 传 `opening_message: null` 后，新 Run 恢复默认 BCS 状态机副屏。
