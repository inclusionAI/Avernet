# BCS 群自定义开场消息设计

- 日期：2026-08-21
- 状态：已实现，待人工联调验收
- 范围：Normal StateMachine Group 的创建、更新、Run 开场消息、历史查询和前端高级设置

> 2026-08-27 补充：Chat 和 ManagerWorker 的 Session 级行为由
> `2026-08-27-bcs-session-opening-message-design.md` 扩展并作为对应策略的权威定义；本文的
> StateMachine Run 行为保持不变。

## 1. 背景

自定义协作群目前在每次 StateMachine Run 开始时固定生成以下 AixUI 消息：

```xml
<AixUI
  type="panel"
  component="bcsPanel.StateMachineRunView"
  tab='{"id":"state-machine-run-<run_id>","title":"State Machine - <session_name>","closable":true}'
  params='{"runId":"<run_id>"}'
/>
```

这会固定打开 BCS 提供的状态机副屏，群创建者无法使用自己的说明文本、内联卡片或已注册副屏组件。
当前内容由
`bcs-collaboration-runtime/src/runtime.rs::format_state_machine_panel_message` 生成，并用于实时前端事件、
消息持久化和 StateMachine 历史重建。

本需求在 Group 上增加可选的 `opening_message`。StateMachine Run 开始时，BCS 使用该配置生成开场
消息；未配置时继续使用现有默认副屏，保证已有调用方行为不变。

## 2. 术语

- **开场消息（Opening Message）**：StateMachine Run 开始、首个节点调度前发送给前端的 assistant 消息。
- **模板（Template）**：Group 上持久化、可引用 BCS 运行上下文变量的开场消息配置。
- **渲染结果（Rendered Opening Message）**：将模板变量替换完成并将结构化 AixUI 序列化后得到的最终字符串。
- **AixUI `type`**：展示位置；`card` 表示消息区内联展示，`panel` 表示打开副屏。
- **AixUI `component`**：前端已经注册的业务组件资源标识，例如
  `bcsPanel.StateMachineRunView`。`panel` 本身不是资源标识。

## 3. 目标

1. 创建 StateMachine Group 时可以配置自定义开场消息。
2. 已有 StateMachine Group 可以更新或恢复默认开场消息。
3. 支持字符串模板；不含模板变量时，最终消息内容与输入字符串完全一致。
4. 一期支持以下变量：Group ID、Session ID、Run ID、Group 名称和 Session 名称。
5. 支持受限的结构化 AixUI 配置，避免调用方手工拼接 `params` 和 `tab` JSON。
6. AixUI 展示位置沿用现有字段 `type`，取值为 `card` 或 `panel`，不增加同义字段
   `position`。
7. Group 是配置的唯一事实来源；不在每个 Run 上保存一份 Group 配置快照。
8. 已经生成的 Run 开场消息保持稳定；修改 Group 只影响随后开始的 Run。
9. 保持未配置 Group 的现有 `bcsPanel.StateMachineRunView` 行为。

## 4. 非目标

一期不包含：

- Chat、ManagerWorker 或 DM Group 的开场消息触发逻辑；
- 一个 Run 配置多条开场消息；
- 条件、循环、函数、默认值表达式或用户自定义模板函数；
- `cardId` 生码卡片；
- 在开场消息中配置 `cdn`、`entry`、远程脚本地址或组件注册信息；
- BCS 管理前端 AixUI 组件的发布、版本或可用性；
- 修改 StateMachine 的定义快照、执行图或节点调度语义；
- 修改已经持久化的历史开场消息；
- Group 配置变更后主动刷新已经打开的浏览器副屏。

字段位于 Group 层并使用通用名称 `opening_message`，以便后续单独为其他 Normal Group 策略定义触发
时机。其他策略在一期不得静默接受并忽略该字段。

## 5. 核心决策

### 5.1 配置属于 Group

`opening_message` 存储在 `Group`，不存储在：

- CollaborationDefinition：开场消息不参与状态机执行，不应产生 definition 版本；
- Session：同一 Group 的不同 Session 默认使用同一配置；
- StateMachineRun：开场消息是展示配置，不需要运行语义快照。

### 5.2 Group 更新的生效规则

Run 开始时读取 Group 当时最新的 `opening_message`，完成模板解析并生成最终字符串：

- Group 未配置：使用现有默认副屏；
- Group 已配置：使用自定义模板；
- Group 在 Run 开始前更新：该 Run 使用新配置；
- Group 在 Run 开始后更新：该 Run 已生成的消息不变，后续 Run 使用新配置；
- 已打开的副屏或内联卡片不因 Group PATCH 自动刷新。

该语义是“后续 Run 跟随 Group”，不是“历史消息随 Group 动态变化”。

### 5.3 不保存 Run 配置快照

每个 Run 不增加 `opening_message_snapshot` 或类似字段。Run 开始时生成的最终内容作为普通消息持久化，
它就是历史展示的唯一副本。

这样可以同时满足：

- 不复制 Group 配置到 Run 表；
- Group 更新只影响后续 Run；
- 历史查询、刷新页面和实时消息看到相同内容；
- 不需要为了 UI 展示配置参与 StateMachine definition 的版本管理。

### 5.4 一次 Run 只有一条逻辑开场消息

每个 Run 使用确定性的消息 ID。当前运行时为恢复或聚焦副屏而再次发送前端事件时，必须复用已生成的
内容，不能重新读取最新 Group 配置生成第二个版本，也不能在历史中追加重复开场消息。

## 6. 数据契约

### 6.1 `OpeningMessage`

`opening_message` 是字符串或结构化 AixUI 对象的联合类型：

```text
OpeningMessage = StringTemplate | AixUiOpeningMessage
```

OpenAPI 逻辑结构：

```yaml
OpeningMessage:
  oneOf:
    - type: string
      minLength: 1
    - $ref: '#/AixUiOpeningMessage'

AixUiOpeningMessage:
  type: object
  additionalProperties: false
  required:
    - type
    - component
  properties:
    type:
      type: string
      enum: [card, panel]
    component:
      type: string
      minLength: 1
      maxLength: 256
    params:
      type: object
      additionalProperties: true
    tab:
      $ref: '#/AixUiOpeningTab'

AixUiOpeningTab:
  type: object
  additionalProperties: false
  properties:
    id:
      type: string
    title:
      type: string
    closable:
      type: boolean
```

附加约束：

- `type=card` 时不得提供 `tab`；
- `type=panel` 时 `tab` 可选；未提供时沿用 AixUI 当前以 `component` 作为 tab 标识和标题的行为；
- `component`、`type` 不参与模板替换；
- `params` 和 `tab` 内所有字符串值支持模板变量；
- `params` 必须是 JSON object，不接受数组、标量或 `null`；
- 不接受未声明字段，包括 `position`、`cardId`、`payload`、`cdn` 和 `entry`。

### 6.2 创建 Group

V1 `POST /openapi/v1/collaboration/groups` 在 Normal Group 根对象增加可选
`opening_message`：

```json
{
  "group_kind": "normal",
  "name": "发布检查",
  "driver_bot_uuid": "bot_planner",
  "participants": [
    {"actor_id": "bot_planner", "role": "driver"},
    {"actor_id": "bot_reviewer", "role": "consultant"}
  ],
  "collaboration": {
    "strategy": "state_machine",
    "definition": {"content_yaml": "..."},
    "participant_bindings": []
  },
  "opening_message": {
    "type": "panel",
    "component": "releasePanel.RunOverview",
    "params": {
      "groupId": "{{bcs.group_id}}",
      "sessionId": "{{bcs.session_id}}",
      "runId": "{{bcs.run_id}}"
    },
    "tab": {
      "id": "release-run-{{bcs.run_id}}",
      "title": "{{bcs.group_name}} / {{bcs.session_name}}",
      "closable": true
    }
  }
}
```

兼容路由 `POST /groups` 同样在请求根对象接受 `opening_message`。当前前端通过该兼容路由建群，因此
V1 和兼容路由必须在同一版本具备相同行为，不能只更新 OpenAPI DTO。

约束：

- `opening_message` 仅允许用于 `group_kind=normal`；
- DM 请求携带该字段时返回 `400 invalid_opening_message`；
- 省略或传 `null` 时，StateMachine 使用默认开场消息，Chat 和 ManagerWorker 不展示开场白；
- 空字符串或仅包含空白字符的字符串无明确展示意义，返回 `400 invalid_opening_message`。

### 6.3 更新 Group

V1 和兼容 `PATCH /groups/{group_id}` 增加三态字段：

```json
{"opening_message": "Run {{bcs.run_id}} 已开始"}
```

表示安装或替换模板。

```json
{"opening_message": null}
```

表示删除自定义模板并恢复默认副屏。

省略 `opening_message` 表示不修改。Service API 和 repo patch 必须保留这三个状态，不能使用单层
`Option<OpeningMessage>` 混淆“未提供”和“清除”。

只有现有 Group 管理者可以修改该字段，沿用当前 Group PATCH 授权逻辑。DM 或不存在的 Group 按现有
资源隐藏和错误映射处理。

### 6.4 查询 Group

Group detail 在存在自定义值时返回 `opening_message`；未配置时省略该字段。Group summary 不增加该字段，
避免列表接口携带大段消息模板。

返回的是原始 Group 配置，不是某个 Run 的渲染结果，也不返回服务端默认模板。

## 7. 模板变量

所有 BCS 提供的模板变量使用 `bcs.` 命名空间，避免未来与用户输入、业务参数或其他模板上下文发生
命名冲突。一期支持以下大小写敏感的完整占位符：

| 占位符 | 来源 | 缺省值 |
| --- | --- | --- |
| `{{bcs.group_id}}` | `Group.id` | 无；运行时必须存在 |
| `{{bcs.session_id}}` | `Session.id` | 无；运行时必须存在 |
| `{{bcs.run_id}}` | `StateMachineRun.run_id` | 仅 StateMachine 可用；运行时必须存在 |
| `{{bcs.group_name}}` | `Group.label` | 空字符串 |
| `{{bcs.session_name}}` | `Session.session_title` | 空字符串 |

规则：

1. 只替换上表中的完整占位符。变量名中的 `bcs.` 是固定命名空间，不表示实现通用对象路径访问；
   不支持任意路径、过滤器或表达式。
2. 创建或更新时扫描所有可模板化字符串；未知的 `{{...}}` 占位符直接拒绝，避免拼写错误延迟到
   Run 开始时才暴露。
3. 替换只执行一轮。变量值自身包含 `{{bcs.run_id}}` 等文本时，不进行递归展开。
4. 字符串模板使用原始 UTF-8 值替换；BCS 不自动增加 Markdown、XML 或 JSON 引号。
5. 结构化 AixUI 在 JSON 对象内替换字符串值，随后统一 JSON 序列化和属性转义，适合把名称等任意
   文本安全传给 `params` 或 `tab`。
6. 一期不定义占位符转义语法。普通 JSON 的单花括号不受影响。

因为 Group 策略在写入时已知，StateMachine 的 `group_id`、`session_id` 和 `run_id` 在渲染时都必须
存在。Chat 和 ManagerWorker 使用 Session 作用域，只支持除 `run_id` 外的四个变量；对这两种策略引用
`run_id` 会在创建或更新时被拒绝，不会静默替换为空字符串。

## 8. 内容生成

### 8.1 字符串模板

输入：

```text
协作群 {{bcs.group_name}} 已开始执行，Run ID：{{bcs.run_id}}
```

输出：

```text
协作群 发布检查 已开始执行，Run ID：run_123
```

如果字符串不包含占位符，生成结果必须与输入逐字节一致。BCS 不修剪、不增加换行、不自动包裹
`<AixUI>`。

字符串可以由有权限的调用方直接提供完整的 Markdown 或 AixUI 标签。对于需要动态 `params` 或
`tab` 的 AixUI，推荐使用结构化形式，避免调用方自行处理 JSON 和单引号属性转义。

### 8.2 结构化 AixUI

结构化对象：

```json
{
  "type": "card",
  "component": "releaseCard.RunSummary",
  "params": {
    "runId": "{{bcs.run_id}}",
    "groupName": "{{bcs.group_name}}"
  }
}
```

生成：

```xml
<AixUI
  type="card"
  component="releaseCard.RunSummary"
  params='{"runId":"run_123","groupName":"发布检查"}'
/>
```

序列化规则：

- JSON 使用确定性的紧凑序列化；
- `params` 和 `tab` 中的对象 key 保持稳定顺序，便于测试和排障；
- JSON 字符串中的单引号转为 `\u0027`，保证单引号包裹的 AixUI 属性合法；
- 其余字符交给 JSON serializer 转义，不手写字符串拼接；
- 未提供 `params` 或 `tab` 时不输出对应属性；
- 属性顺序固定为 `type`、`component`、`tab`、`params`。

`type=card` 与 `type=panel` 均由现有 `aixUiPlugin` 解析。结构化配置只负责生成消息，不引入新的
前端协议。

### 8.3 默认消息

`opening_message` 未配置时，输出必须保持当前默认：

```xml
<AixUI
  type="panel"
  component="bcsPanel.StateMachineRunView"
  tab='{"id":"state-machine-run-<run_id>","title":"State Machine - <session_title_or_新会话>","closable":true}'
  params='{"runId":"<run_id>"}'
/>
```

默认消息不是隐式写入 Group 的配置值；Group detail 仍省略 `opening_message`。这样服务端可以继续维护
默认行为，同时不把默认模板复制到所有历史 Group。

## 9. 大小和校验

- 原始字符串模板最多 `64 KiB` UTF-8 字节；
- 结构化对象的 canonical JSON 最多 `64 KiB` UTF-8 字节；
- 模板渲染后的最终消息最多 `64 KiB` UTF-8 字节；
- 完整 `OpeningMessage` 的持久化 JSON 编码最多 65,535 字节，以适配 MySQL `TEXT`；
- 字符串不得为空或全空白；
- `component` 去除首尾空白后必须非空、不得包含控制字符、引号、`<`、`>` 或空白；
- 对象未知字段、未知模板变量、`card + tab` 和不支持的 Group 策略统一返回
  `400 invalid_opening_message`，错误 detail 指明具体字段；
- OpenAPI `maxLength` 只作为文档和客户端提示，服务端仍按 UTF-8 字节执行最终限制。

BCS 不在写入时验证前端是否已经注册 `component`，因为组件注册表属于前端运行环境。组件不存在时沿用
现有 AixUI 的“组件库不存在/未注册”展示，不影响 StateMachine 执行。

## 10. Run 生命周期和失败语义

Run 开始顺序：

1. 加载并校验 Group、Session 和 StateMachine definition；
2. 创建/启动 Run 并得到稳定的 `run_id`；
3. 读取 Group 最新 `opening_message`；
4. 构建模板上下文并生成最终内容；
5. 持久化一条确定性的开场消息；
6. 向当前 Session 的前端连接发布相同内容；
7. 调度首批 StateMachine 节点。

持久化失败不得被吞掉：

- 不调度任何节点；
- Run 按现有失败路径进入 Failed；
- 启动请求返回错误；
- 日志记录 Group、Session、Run 和错误，不记录完整模板内容。

前端实时发布超时或失败继续沿用当前非致命语义：内容已经持久化，前端可通过历史查询恢复。该失败不应
导致已经持久化且尚未调度节点的 Run 因瞬时前端连接问题失败。

## 11. 消息持久化和历史兼容

### 11.1 新 Run

所有 StateMachine Run 都必须持久化渲染后的开场消息，不再只为部分 one-shot 路径持久化。实时事件和
历史查询使用同一份最终内容。

为减少兼容性影响，一期保留现有：

- `client_msg_id = "{run_id}:000-panel"`；
- `message_type = "state_machine_panel"`；
- assistant sender 和 bot name；
- `metadata.state_machine` 中的 Run/definition correlation。

上述 `panel` 名称作为历史兼容标识保留，不表示自定义内容必须是副屏。默认配置继续携带
`component=bcsPanel.StateMachineRunView`；结构化配置携带其 `component`；无法可靠解析的字符串模板
不伪造 component metadata。

### 11.2 历史查询

- 存在持久化开场消息：直接使用该消息；
- 不得根据当前 Group 配置重新渲染已经存在的消息；
- 旧 Run 没有持久化开场消息：继续按旧逻辑合成默认
  `bcsPanel.StateMachineRunView`，不得套用 Group 后来新增的自定义模板；
- 确定性 `client_msg_id` 和 repo 幂等约束防止重试产生重复历史消息。

StateMachine 节点输出仍可沿用当前 run/node snapshot 重建逻辑；本需求只改变开场消息来源。

## 12. 持久化模型

### 12.1 Domain

在 `bcs-domain::Group` 增加：

```rust
#[serde(default, skip_serializing_if = "Option::is_none")]
pub opening_message: Option<OpeningMessage>,
```

`OpeningMessage` 和受限 AixUI 类型属于 Group 的领域配置，不放在 HTTP adapter。HTTP DTO、Service API、
repo port 和运行时均依赖同一组明确的 contract type，adapter 只负责协议映射。

### 12.2 MySQL

`009_eventing.sql` 保持不变。本需求新增独立的 MySQL migration version `010`：

```text
migrations/mysql/010_group_opening_message.sql
```

该迁移为 `bcs_groups` 增加 nullable text 列：

```sql
ALTER TABLE bcs_groups
  ADD COLUMN opening_message_json TEXT NULL;
```

- 同步更新 `migrations/README.md`，登记 MySQL version 010；
- `bootstrap/bcs/src/migrations.rs` 的 MySQL 静态校验加载该文件；
- 不修改或重命名已经存在的 `009_eventing.sql`，避免改变已登记迁移的 checksum；
- `NULL` 表示使用默认消息；
- 字符串和对象统一以 JSON 编码保存，避免将字符串与对象分别建列；
- 写入前校验完整 JSON 编码不超过 MySQL `TEXT` 的 65,535 字节上限；
- 旧记录自然读取为 `None`；
- DB adapter 负责序列化和反序列化，解析失败必须返回存储错误，不得静默退回默认值。

SQLite bootstrap/migration 同步增加该列。SQLite 已使用 version 010 记录 Eventing 明文 endpoint
迁移，因此本字段使用 SQLite version 011；两个 dialect 的版本号暂时不对齐，但都只追加迁移且不修改
既有 checksum。内存 repo 保存领域值。文件/JSON 兼容读取依赖
`#[serde(default)]`，旧数据无需迁移。

### 12.3 Patch

repo 的 Group mutable patch 使用三态：

```rust
pub opening_message: Option<Option<OpeningMessage>>,
```

- `None`：不修改；
- `Some(Some(value))`：安装/替换；
- `Some(None)`：清除并恢复默认。

## 13. 前端行为

1. 创建群弹层增加一个“高级设置”折叠区，初始状态为折叠。
2. 现有 Webhook URL 从成员 Bot 上方移入“高级设置”，默认留空；折叠和展开不得清除用户已输入的值。
3. `opening_message` 与 Webhook URL 放在同一“高级设置”区域，默认留空；Chat、ManagerWorker 和
   StateMachine 都显示开场白输入并发送非空字段。
4. 开场白使用支持多行的 textarea。普通用户输入按字符串模板发送；留空时不传
   `opening_message`。StateMachine 由服务端使用默认副屏，Chat 和 ManagerWorker 不展示开场白。
5. UI 提示可用变量，三种策略均支持 `{{bcs.group_id}}`、`{{bcs.session_id}}`、
   `{{bcs.group_name}}` 和 `{{bcs.session_name}}`；仅 StateMachine 显示并支持 `{{bcs.run_id}}`。
6. “高级设置”的展开状态不属于建群请求；关闭或成功提交弹层并 reset form 后恢复为折叠状态，输入值也
   恢复为空。
7. 折叠区中存在非空但校验失败的字段时，提交不得忽略错误；前端自动展开“高级设置”，展示字段级错误
   并聚焦第一个错误输入。
8. API 的结构化对象主要面向程序化调用方；一期前端不需要实现通用 JSON 表单、组件选择器或组件市场。
9. 收到 `type=panel` 时继续打开副屏；收到 `type=card` 时在消息区内联渲染。
10. 前端继续使用现有 `aixUiPlugin` 和组件 registry，不增加专用 BCS 渲染器。
11. Group 设置页是否提供编辑入口不阻塞后端 PATCH；若提供，清空并保存应发送
    `opening_message: null`。

## 14. 安全边界

- 只有通过现有 Group 创建/管理授权的调用方可以写入模板；
- BCS 只生成文本，不执行组件代码；
- 结构化模式只能引用前端已注册的 `component`，不能携带远程地址；
- 结构化模式的 `params` 和 `tab` 必须通过 JSON serializer 生成，禁止手工拼接未转义变量；
- 前端继续经过现有 Markdown/AixUI sanitizer 和组件 registry；
- 不在日志、指标或公开 Event payload 中记录完整模板；
- `group.updated` Event 可以在 `changed_fields` 中包含 `opening_message`，但不得附带模板正文；
- 模板不是 secret。授权的 Group detail 读取者可以看到原始配置，调用方不得在其中存放密钥或 token。

## 15. Contract 传播范围

| 边界 | 变化 |
| --- | --- |
| OpenAPI V1 | Group create/detail/PATCH 增加 `OpeningMessage` schema |
| Legacy HTTP | `POST /groups`、`PATCH /groups/{id}` 接受同名字段，detail 返回同名字段 |
| Service API | create、detail、patch 增加 typed opening-message contract |
| Domain | `Group` 增加可选 `OpeningMessage` |
| Repo port | create/read/三态 patch 传播该字段 |
| MySQL migration/store | 新增 version 010；增加 nullable JSON text 列并严格序列化 |
| Memory/file/SQLite store | 兼容旧数据并保存新字段 |
| Collaboration runtime | Run 开始时渲染、持久化、发布并在重发时复用 |
| Message history | 优先使用持久化开场消息，旧 Run 继续合成旧默认 |
| Frontend | 建群可选字符串输入；现有 AixUI renderer 处理 card/panel |
| Events | Group update 只暴露 changed field，不暴露模板正文 |

这是 additive contract change。旧客户端省略字段时行为不变；旧 Group 记录读取为未配置。

## 16. 测试要求

### 16.1 Contract 和 DTO

1. V1 创建请求省略字段时成功且保持默认行为。
2. V1 创建请求接受字符串和结构化对象。
3. Legacy 创建请求接受相同字段并传入 Service API。
4. V1 和 Legacy PATCH 区分省略、设置和 `null` 清除。
5. detail 返回原始配置，summary 不返回。
6. DM 携带字段时返回 `invalid_opening_message`；Chat、ManagerWorker 携带 `run_id` 时返回该错误。
7. 未知对象字段、`position`、`card + tab`、未知变量和超限内容被拒绝。

### 16.2 模板渲染

1. 五个变量均能正确替换。
2. 未命名 Group/Session 分别替换为空字符串。
3. 不含变量的字符串逐字节保持不变。
4. 替换只执行一轮。
5. 结构化 `params` 和 `tab` 中含引号、单引号、换行和中文的名称仍生成合法 JSON/AixUI。
6. `type=card` 生成内联卡片标签，`type=panel` 生成副屏标签。
7. 未配置时生成内容与现有默认完全一致。
8. 渲染结果超过 64 KiB 时失败且不调度节点。

### 16.3 Runtime 和历史

1. 开场消息在首个节点开始前持久化并发布。
2. 实时事件和历史消息内容完全相同。
3. 同一 Run 重发前端事件不会追加第二条消息。
4. Group 更新后，旧 Run 历史不变，新 Run 使用新配置。
5. Group 清除配置后，新 Run 恢复默认副屏。
6. 持久化失败使 Run 失败且不调度节点。
7. 前端发布失败不回滚已持久化消息，也不阻止节点调度。
8. 旧 Run 缺少持久化开场消息时仍生成旧默认，不读取当前自定义配置。

### 16.4 Store

1. MySQL version 010 迁移能够被 migration check 和相关 `include_str!` 测试加载。
2. `009_eventing.sql` 保持不变，MySQL version 010 和 SQLite version 011 分别增加
   `bcs_groups.opening_message_json`。
3. Memory repo create/get/patch round trip。
4. MySQL repo 字符串和结构化对象 round trip。
5. MySQL `NULL` 映射为 `None`。
6. 非法持久化 JSON 返回错误，不静默降级。
7. 三态 patch 不覆盖并发更新的其他 Group mutable fields。

### 16.5 Frontend

1. 创建群弹层首次打开时“高级设置”折叠，Webhook 和开场白均为空。
2. Webhook URL 不再显示在成员 Bot 上方，而是在高级设置内显示。
3. StateMachine 策略显示 Webhook 和开场白；其他策略只显示 Webhook 且不发送开场白字段。
4. 折叠再展开保留两个输入值，reset form 后清空并恢复折叠。
5. 留空不发送对应字段，输入字符串原样进入 create 请求。
6. 非空字段校验失败时自动展开高级设置并展示错误。
7. 可用变量提示包含完整 `bcs.` 前缀。
8. 默认模板继续打开 `bcsPanel.StateMachineRunView`。
9. 自定义 `type=card` 在消息区渲染，自定义 `type=panel` 打开副屏。
10. 未注册组件沿用现有错误展示，不导致聊天页面崩溃。

## 17. 验收标准

1. 使用字符串创建 StateMachine Group 后，每个新 Run 首先收到变量已替换的自定义消息。
2. 使用结构化 `type=panel` 可以打开指定的已注册副屏组件，并收到正确的 `params` 和 `tab`。
3. 使用结构化 `type=card` 可以在聊天消息区域内联渲染指定组件。
4. 不配置时，用户看到的默认 BCS 状态机副屏与改动前一致。
5. PATCH Group 后，改动只作用于随后开始的 Run；旧 Run 刷新页面后内容不变。
6. 不存在任何 Run 级 opening-message 配置快照字段或表。
7. 所有新 Run 都有一条可恢复的持久化开场消息，写失败不会被吞掉。
8. V1 与当前前端使用的 Legacy 建群接口行为一致。
9. 前端 Webhook 和自定义开场白位于同一个默认折叠、默认留空的高级设置区域。

## 18. Chat / ManagerWorker 扩展

Chat 和 ManagerWorker 的扩展条件已在
`2026-08-27-bcs-session-opening-message-design.md` 中确认并批准。两种策略沿用
`type=card|panel`，在新 Session 创建时持久化一次，禁止引用 `run_id`，并使用确定性的 Session
开场消息 ID。该扩展不改变本设计定义的 StateMachine Run 行为。
