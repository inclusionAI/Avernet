# BCS one-shot StateMachine 自定义开场消息设计

- 日期：2026-09-01
- 状态：已实现
- 范围：Chat 和 ManagerWorker Session 中提交的 one-shot StateMachine Run
- 相关设计：
  - `2026-08-21-bcs-custom-opening-message-design.md`
  - `2026-08-27-bcs-session-opening-message-design.md`
  - `2026-08-19-bcs-collaboration-state-persistence-fo-rerun-design.md`

## 1. 摘要

BCS 已支持 Normal Group 的自定义 `opening_message`：Chat 和 ManagerWorker 在 Session 创建时
展示一次，StateMachine 在每个 Run 开始时展示一次。Chat 或 ManagerWorker Session 内临时提交的
one-shot StateMachine 当前固定使用默认 `bcsPanel.StateMachineRunView` 副屏，并且不会读取 Group
的 Session 级开场消息。

本设计允许 one-shot 调用方在提交 Run 时携带可选的 `opening_message`。HTTP API 与 Group
`opening_message` 使用同一个联合类型，支持字符串、AixUI `card` 和 AixUI `panel`；BCS CLI
第一期只提供用于构造 `panel` 的参数。

提交级配置只覆盖本次 one-shot Run，不修改 Group 或 Session。原始配置持久化到
`bcs_state_machine_runs.opening_message_override_json`，渲染后的最终消息继续写入 `bcs_messages`。
自定义 StateMachine Group 创建的配置型 Run 不写该覆盖字段，继续从
`bcs_groups.opening_message_json` 读取 Group 配置。

## 2. 背景与问题

当前 one-shot 提交接口为：

```http
POST /sessions/{session_id}/state-machine-runs
```

请求可以指定 StateMachine definition、临时参与者绑定和运行输入，但不能指定本次 Run 的展示内容。
运行时会生成固定的默认消息：

```xml
<AixUI
  type="panel"
  component="bcsPanel.StateMachineRunView"
  tab='{"id":"state-machine-run-<run_id>","title":"State Machine - <session_name>","closable":true}'
  params='{"runId":"<run_id>"}'
/>
```

调用方可能已经注册了面向具体业务的卡片或副屏组件，希望 one-shot Run 启动时直接展示该组件，并把
Run、Group、Session 和业务参数传给组件。把该配置写到 Chat 或 ManagerWorker Group 上不可行：
Group `opening_message` 属于 Session，不能影响 Session 内临时启动的 StateMachine Run。

## 3. 术语

- **Group opening message**：持久化在 Group 上，由 Group 策略决定在 Session 或 Run 创建时触发。
- **Run opening-message override**：one-shot 请求显式提交、只属于一个 Run lineage 的开场消息配置。
- **Rendered opening message**：使用确定的 Group、Session 和 Run 上下文完成模板替换后的最终消息。
- **one-shot Run**：Chat 或 ManagerWorker Session 中通过
  `POST /sessions/{session_id}/state-machine-runs` 临时提交的 StateMachine Run。
- **configured Run**：StateMachine Group 根据持久化 definition 和 binding 创建的 Run。

## 4. 目标

1. one-shot 提交可以携带与 Group 相同结构的 `opening_message`。
2. API 支持字符串、AixUI `card` 和 AixUI `panel`。
3. CLI 第一期只暴露结构化 `panel` 的组件、参数和 Tab 参数。
4. 未提交 `opening_message` 时保持现有默认 StateMachine 副屏。
5. one-shot 配置不修改、不读取也不覆盖 Chat 或 ManagerWorker 的 Group 开场消息。
6. 原始提交配置可在故障恢复和用户 rerun 时使用，并使用新 Run ID 重新渲染。
7. 渲染结果进入 Session 历史，页面刷新恢复同一结果，Bot 不可见。
8. configured StateMachine Group 的现有开场消息语义保持不变。

## 5. 非目标

本期不包含：

- 在一个 Run 中提交多条开场消息；
- 修改 StateMachine definition YAML 以承载展示配置；
- 允许 one-shot 请求修改 Group 或 Session 的开场消息；
- 由 BCS 下载、发布或注册前端组件；
- 校验组件是否存在于某个具体前端版本的注册表；
- 为 CLI 提供字符串或 `card` 的专用参数；
- 在 rerun 时替换源 Run 的开场消息、definition、binding 或 input；
- 修改已经持久化的历史开场消息；
- 把开场消息内容发送给 Bot 或注入 Bot 上下文。

## 6. 核心语义

### 6.1 配置来源和优先级

| Run 场景 | 请求配置 | Group 配置 | 最终行为 |
| --- | --- | --- | --- |
| one-shot | 已提供 | 无论是否存在 | 使用请求级 `opening_message` |
| one-shot | 省略或 `null` | 无论是否存在 | 使用 BCS 默认 StateMachine 副屏 |
| configured StateMachine | 接口不提供 | 已提供 | 使用 Group `opening_message` |
| configured StateMachine | 接口不提供 | 未提供 | 使用 BCS 默认 StateMachine 副屏 |

Chat 和 ManagerWorker Group 的 `opening_message` 始终属于 Session。one-shot 未提供覆盖时不得回退到
Group `opening_message`，否则会重新引入 Session 开场白污染 StateMachine 副屏的问题。

### 6.2 配置作用域

请求级 `opening_message` 属于创建出的 one-shot Run：

- 不写入 `bcs_groups`；
- 不写入 `bcs_group_sessions`；
- 不改变 Session 已经持久化的开场白；
- 不影响同一 Session 中随后独立提交的其他 one-shot Run；
- 用户 rerun 创建新 Run 时继承源 Run 的原始覆盖配置。

### 6.3 一次 Run 一条逻辑开场消息

每个 Run 只产生一条逻辑开场消息，使用稳定的：

```text
client_msg_id = <run_id>:000-panel
message_type = state_machine_panel
```

`state_machine_panel` 是现有的 StateMachine 开场消息类型名称。即使实际内容是文本或 `card`，本期也
不新增消息类型，避免改变历史查询、Bot 过滤和前端恢复协议。

## 7. HTTP API 契约

### 7.1 请求字段

`POST /sessions/{session_id}/state-machine-runs` 增加可选字段：

```text
opening_message?: OpeningMessage | null
```

`OpeningMessage` 与 Group API 使用同一个联合类型：

```text
OpeningMessage = StringTemplate | AixUiOpeningMessage
```

该接口是创建接口，不是 PATCH：省略和显式 `null` 都表示没有请求级覆盖并使用默认副屏，不需要三态
patch 语义。

### 7.2 `panel` 示例

```json
{
  "definition_yaml": "<state-machine yaml>",
  "participant_bindings": {
    "writer": {
      "source": "manual",
      "bot_ids": ["bot_writer"]
    }
  },
  "input": {
    "question": "生成发布方案"
  },
  "opening_message": {
    "type": "panel",
    "component": "partnerPanel.OneShotRunView",
    "params": {
      "runId": "{{bcs.run_id}}",
      "groupId": "{{bcs.group_id}}",
      "sessionId": "{{bcs.session_id}}",
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

BCS 不向自定义组件隐式合并 `runId`、`groupId` 或其他参数。需要运行标识的组件必须在 `params` 中
显式使用模板变量，避免服务端默认参数与业务参数发生覆盖冲突。

### 7.3 `card` 示例

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

`card` 展示在消息区，不打开副屏，并且不得携带 `tab`。

### 7.4 字符串示例

```json
{
  "opening_message": "一次性协作已启动，Run ID：{{bcs.run_id}}"
}
```

字符串作为普通 Run 开场消息展示，不保证打开副屏。为了与 Group 协议一致，字符串仍允许包含调用方
自行构造的完整 AixUI 文本；BCS 只执行模板替换，不解析其中的 AixUI 属性。需要动态 JSON 参数时
应使用结构化对象。

### 7.5 响应

成功响应继续返回现有 `StateMachineRunView`，本期不在公开 Run view 中增加原始
`opening_message`。页面展示以 Session 历史中的渲染结果为准。

## 8. 校验和渲染

### 8.1 校验时机

BCS 在完成调用方权限校验后、创建 Run 之前对请求级 `opening_message` 执行
`OpeningMessageScope::StateMachineRun` 校验。非法配置返回：

```http
400 Bad Request
```

```text
invalid_opening_message
```

非法配置不得创建 Run、Node、definition snapshot、消息或 delivery correlation，也不得调度 Bot。

### 8.2 校验规则

校验规则与 configured StateMachine Group 一致：

- 字符串不得为空或只包含空白；
- 原始配置和渲染结果不得超过 64 KiB UTF-8 字节；
- 持久化前的 JSON 编码结果不得超过 65,535 字节，以保证完整写入 MySQL `TEXT`；字符串中的引号、
  反斜杠等转义字符计入编码结果；
- 只接受已声明的模板变量；
- 模板变量必须具有完整的 `{{...}}` 结束标记；
- `component` 长度为 1–256 字节，不得包含空白、控制字符、引号、`<` 或 `>`；
- `params` 必须是 JSON object；
- `type=card` 时不得提供 `tab`；
- 结构化对象不得包含未声明字段。

BCS 只校验组件标识格式，不查询前端组件注册表。组件未注册不阻止 StateMachine 执行；前端应显示
组件不可用状态并记录可观测错误。

### 8.3 模板变量

请求级消息使用 StateMachine Run 作用域，支持：

| 占位符 | 渲染来源 |
| --- | --- |
| `{{bcs.group_id}}` | 当前 Group ID |
| `{{bcs.session_id}}` | 当前 Session ID |
| `{{bcs.run_id}}` | 当前 Run ID |
| `{{bcs.group_name}}` | 当前 Group 名称；未设置时为空字符串 |
| `{{bcs.session_name}}` | 当前 Session 标题；未设置时为空字符串 |

结构化对象只在 `params` 的字符串值、`tab.id` 和 `tab.title` 中替换模板。`type`、`component` 和
JSON key 不参与模板替换。

## 9. 数据模型和持久化

### 9.1 Run 覆盖字段

在 `bcs_state_machine_runs` 增加可空字段：

```text
opening_message_override_json TEXT NULL
```

该字段保存 one-shot 请求提交的原始 `OpeningMessage` JSON，而不是渲染后的 AixUI 文本。字符串类型
同样使用 JSON 字符串编码，保证读取时可以按统一联合类型反序列化。

字段保持 `TEXT`，不为极端边界输入扩大存储类型。BCS 在创建 Run 前校验完整 JSON 编码结果不超过
MySQL `TEXT` 的 65,535 字节上限；超过上限按 `400 invalid_opening_message` 拒绝，不进入持久化流程。

字段命名必须包含 `override`，避免与 Group 配置或最终消息混淆。不使用含义模糊的
`opening_message_json`。

### 9.2 写入规则

| 场景 | `opening_message_override_json` |
| --- | --- |
| one-shot，显式提供非空配置 | 写入原始配置 |
| one-shot，省略或传 `null` | `NULL` |
| configured StateMachine Group 创建 Run | `NULL` |
| one-shot rerun | 复制 source Run 的字段 |
| configured StateMachine rerun | `NULL`，继续使用 Group 当前配置 |

configured Run 不复制 `bcs_groups.opening_message_json`。Group 仍是该模式的配置事实来源，Group
修改只影响随后开始的 Run；每个 Run 已经渲染并持久化的历史消息保持不变。

### 9.3 Repository 契约

内部 `StateMachineRun` 增加 `opening_message_override` 字段，Run 创建 port 必须把它与 Run 行在同一
数据库事务中写入。该字段使用 `serde(skip_serializing)`，因此可供 repository 和 runtime 使用，但不
进入公开 Run JSON：

```text
StateMachineRun {
  ...,
  opening_message_override: Option<OpeningMessage>
}
```

数据库和内存实现都必须支持：

- 创建 Run 时写入 override；
- 按 Run ID 读取 override；
- 创建直接 rerun child 时原子复制 source override；
- 遇到无法反序列化的非空数据时返回持久化错误，不静默当作 `NULL`。

原始 override 属于运行时内部快照，不要求加入公开 `StateMachineRunView`。

### 9.4 渲染结果消息

渲染后的最终内容继续写入 `bcs_messages`，不增加消息表字段。消息包含：

- `group_id` 和 `session_id`；
- `run_id`；
- 稳定的 `client_msg_id`；
- 最终 `text`；
- `metadata.state_machine.event = "panel"`；
- 结构化 AixUI 消息的 `metadata.state_machine.component`。

原始模板不写入消息 content 或 metadata。Session history 只返回最终渲染结果，避免刷新时重新渲染，
也避免通过历史接口同时暴露两种事实来源。

## 10. Run 启动流程

one-shot 启动顺序为：

1. 验证调用方对当前 Session 的 one-shot 权限；
2. 校验 definition、临时 participant bindings、input 和 `opening_message`；
3. 生成 Run ID；
4. 创建 Run、Node，并在 Run 行中原子保存原始 override；
5. 保存现有 definition 和 resolved binding snapshot；
6. 根据请求级 override 或默认副屏渲染最终消息；
7. 持久化 `state_machine_panel` 消息；
8. 向前端发布相同消息；
9. 调度 StateMachine 初始节点。

节点调度必须发生在开场消息持久化成功之后。override 或消息持久化失败时，Run 按现有规则标记失败，
不得向 Bot 派发节点。

实时前端发布保持 best-effort。发布失败不回滚已经持久化的 Run 或消息，页面刷新和重连通过历史消息
恢复。

## 11. 恢复和 rerun

### 11.1 已存在 Run 的恢复

恢复或重新发布已有 Run 的开场消息时按以下顺序解析：

1. 已存在匹配 `client_msg_id` 或 `run_id` 的持久化消息：复用该最终内容；
2. 消息缺失且 Run 存在请求级 override：使用该 Run 上下文重新渲染 override；
3. one-shot 无 override：生成默认 StateMachine 副屏；
4. configured Run：沿用既有 Group 开场消息恢复规则，本设计不改变其语义。

持久化消息一旦存在，不得根据原始 override 或 Group 当前配置重新生成另一个版本。

### 11.2 用户 rerun

Failed one-shot Run 的用户 rerun 创建新的 Run，并继承 source Run 的原始 override。新 Run 必须：

- 获得新的 `run_id`；
- 复制 source override，而不是复制 source 的最终 AixUI 文本；
- 使用新 Run 上下文重新替换 `{{bcs.run_id}}`；
- 生成新的 `<new_run_id>:000-panel` 消息；
- 不允许 rerun 请求临时替换 override。

复制最终文本会使业务组件继续查询旧 Run，因此禁止把已渲染消息作为 rerun 模板。

## 12. Bot、前端和消息历史

- 开场消息是 UI 消息，不通过 `BotDeliveryPort` 发送给 Bot。
- Bot 消息历史和上下文回放继续排除 `state_machine_panel`。
- Human Session history 返回渲染后的开场消息。
- 页面刷新、重新连接或晚加入只读取历史消息，不读取 Run override 或 Group 配置重新渲染。
- 前端继续通过现有 AixUI 解析和组件注册机制展示 `card` 或 `panel`。
- 未注册组件只影响展示，不影响 StateMachine 节点执行和最终结果发布。

## 13. CLI 设计

`bcs-cli collaborate run` 第一期增加：

```text
--panel-component <COMPONENT>
--panel-params <JSON|@FILE>
--panel-tab-id <TEMPLATE>
--panel-tab-title <TEMPLATE>
--panel-tab-closable <true|false>
```

CLI 行为：

- 未提供 `--panel-component` 时不发送 `opening_message`；
- 提供 `--panel-component` 时构造 `type=panel` 的结构化 `opening_message`；
- `--panel-params` 省略时不发送 `params`；
- 任意 `--panel-params` 或 `--panel-tab-*` 参数都必须依赖 `--panel-component`；
- `--panel-params` 必须解析为 JSON object；
- 仅当至少提供一个 Tab 参数时才发送 `tab`；
- CLI debug 输出沿用现有请求调试机制，但服务端结构化日志不得记录完整 params。

示例：

```bash
bcs-cli collaborate run workflow.yaml \
  --session session-1 \
  --binding writer=bot-writer \
  --input @input.json \
  --panel-component partnerPanel.OneShotRunView \
  --panel-params @panel-params.json \
  --panel-tab-id 'one-shot-{{bcs.run_id}}' \
  --panel-tab-title '一次性协作' \
  --panel-tab-closable true
```

HTTP API 的字符串和 `card` 能力可以由 SDK 或直接 HTTP 调用使用。CLI 暂不增加通用
`--opening-message`，后续出现明确需求时再扩展。

## 14. 错误和可观测性

- 请求格式、模板或组件格式非法：`400 invalid_opening_message`；
- Run override 持久化失败：返回内部错误并确保节点未调度；
- 渲染失败：Run 标记失败并返回错误；
- 历史消息持久化失败：Run 标记失败并且不调度节点；
- 前端实时发布失败：记录包含 Run ID、Group ID 和 Session ID 的 warning，Run 继续；
- 前端组件未注册：由前端记录组件名和 Run ID，BCS 不把它解释为运行失败。

日志、metrics 和 collaboration events 不记录完整 `opening_message` 或 params。可记录以下低基数字段：

- `opening_message_source = request_override | group | default`；
- `opening_message_kind = text | card | panel`；
- `component` 只允许出现在受控 debug 诊断中，不作为 metrics label。

## 15. 兼容性和发布

- 新 HTTP 字段可选，旧调用方请求和响应保持不变；
- 新数据库字段可空，历史 Run 按 `NULL` 处理；
- Group API、Group 数据和 configured StateMachine Run 行为不变；
- Chat 和 ManagerWorker 的 Session 开场消息行为不变；
- 前端消息 schema 和查询接口不变；
- CLI 未使用新参数时请求体保持现状。

数据库迁移必须同时覆盖 SQLite 和 MySQL，并加入 schema migration 验证。部署顺序为先升级支持可空字段
和读取逻辑的 BCS，再允许调用方发送 `opening_message`。回滚时旧版本会忽略不了未知请求字段的风险由
流量切换保证：回滚 BCS 前必须先停止新调用方发送该字段。

## 16. 测试要求

### 16.1 Contract tests

- HTTP 路由接受字符串、`card`、`panel`、省略和 `null`；
- HTTP 路由拒绝结构化 `opening_message` 中的未知字段、非法模板、非法组件和带 `tab` 的 `card`；
- Service API 把原始配置完整传给 runtime；
- CLI panel 参数生成预期请求 JSON，并校验参数依赖关系。

### 16.2 Store conformance

- SQLite、MySQL 和内存 store round-trip 字符串、`card` 和 `panel` override；
- one-shot 未配置时保持 `NULL`；
- configured Run 即使 Group 配置了开场消息也保持 `NULL`；
- rerun child 原子复制 source override；
- 无法反序列化的非空数据返回错误；
- 历史数据库升级后旧 Run 可以正常读取。

### 16.3 Runtime integration

- Chat 和 ManagerWorker one-shot 的请求 override 优先于 Group Session 开场白；
- one-shot 未提供 override 时继续使用默认 `StateMachineRunView`；
- 字符串、`card`、`panel` 都按 Run 作用域完成模板替换；
- 渲染结果只持久化一次并使用稳定 client message ID；
- persistence failure 发生在节点调度前并使 Run 失败；
- 前端实时事件与历史消息内容一致；
- Human 刷新可以恢复消息，Bot 历史看不到消息；
- Failed one-shot rerun 继承原始 override，并使用新的 Run ID 重新渲染；
- configured StateMachine Group 继续使用 Group 配置且 Run override 字段为 `NULL`。

## 17. 验收标准

1. 旧 one-shot 请求无需修改，仍打开默认 BCS StateMachine 副屏。
2. one-shot 请求可以提交字符串、`card` 或 `panel` 开场消息。
3. CLI 可以通过 `panel-*` 参数提交副屏组件、params 和 Tab 配置。
4. 请求配置不会修改或消费 Chat、ManagerWorker Group 的 Session 开场白。
5. 自定义 StateMachine Group 创建 Run 时
   `bcs_state_machine_runs.opening_message_override_json IS NULL`。
6. one-shot 显式配置时保存原始 override，最终消息保存到 `bcs_messages`。
7. 页面刷新得到与实时事件相同的内容，且不会重新渲染当前配置。
8. Bot 不会接收开场消息，也不会在历史或上下文中看到它。
9. one-shot rerun 使用新 Run ID 重新渲染原始配置，不引用 source Run。
10. Group、Session、configured StateMachine 和现有 rerun 行为没有兼容性回归。
