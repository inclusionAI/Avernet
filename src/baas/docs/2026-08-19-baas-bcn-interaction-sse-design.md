# BaaS 到 BCN 的 Interaction SSE 转换设计

## 背景

Engine 通过 WebSocket 发送 `interaction.requested` 和
`interaction.resolved` 事件。BaaS 当前把 Engine envelope 基本原样放入 SSE
`data`，并使用 Engine 事件名作为 SSE `event`：

```text
id: 1
event: interaction.requested
data: {"type":"event","event":"interaction.requested","payload":{...},"runId":"<BCN request id>","seq":1,"ts":...}
```

BCN Provider 2.0 协议只识别顶层 `event: interaction`，并要求
`interactionId`、`kind`、`phase` 等字段直接位于 `data` 根部。当前格式有两处
协议级不兼容：事件名无法被 BCN interaction parser 识别，以及业务字段被
Engine envelope 嵌套在 `data.payload` 中。即使只修改事件名，BCN 仍无法在
`data` 根部找到必需字段。

## 目标

- 在 BaaS SSE converter 边界将 Engine interaction 转为 BCN Provider 2.0
  的扁平 `interaction` 事件。
- 支持 `ask_user`、`exec`、`mode_switch` 三类 requested interaction。
- 保留 resolved interaction 的现有能力，并统一输出为 BCN 格式。
- 对实际 Engine 消息和旧格式做有限兼容，避免单条异常 interaction 终止
  整条聊天流。
- 更新 BCN SSE 协议，允许 mode switch 的目标模式和推荐选项。
- 对 BCN/前端只暴露 BaaS 生成的 public interaction ID，不暴露 Engine ID。

## 非目标

- 不改变 Engine WebSocket 协议。
- 不改变 BaaS 中用于 interaction resolve 的完整 Engine envelope 持久化。
- 不把 Engine 私有状态、生命周期或 UI 元数据暴露到 BCN 公共协议。
- 不为未知 interaction kind 推测协议结构。

## 设计选择

转换位于 BaaS SSE converter 边界，并按 kind 使用独立的纯转换函数：

```text
_transform_interaction
├── _transform_ask_user_requested
├── _transform_exec_requested
└── _transform_mode_switch_requested
```

converter 从交付副本的 envelope 中读取 `payload`，创建新的 BCN 数据对象，不
修改原始 Engine 对象。interaction service 先按 Engine `sessionKey` 和
`interactionId` 生成、持久化 BaaS public ID；交付副本中的 `interactionId` 被替换
为该 public ID。输出采用字段白名单，防止 Engine 新增内部字段后被自动泄露。
Engine 回调仍保存完整原始 envelope，内部派发仍使用 Engine ID。

不在 Engine 回调处直接归一化，因为该层同时承担 Engine 原始事件持久化；过早
转换会混淆 Engine 合约和 BCN 合约。也不采用通用字段表驱动的深层映射，因为
三类 interaction 的兼容规则不同，显式的 kind converter 更易审查和测试。

## SSE envelope 与序列语义

转换后的事件形式为：

```text
id: <BaaS stream sequence>
event: interaction
data: {"runId":"<BCN chat.send request id>","seq":<BaaS stream sequence>,"ts":..., ...}
```

- SSE `id` 是 BaaS 当前连接内生成的递增序号的字符串形式。
- `data.seq` 使用同一个 BaaS 连接内序号；BCN 使用它做顺序处理和去重。
- `data.runId` 使用 BCN 发起 `chat.send` 时请求中的 `id`，代表当前 Bot 响应
  生命周期；不使用 Engine payload 中的 `runId`。
- `data.ts` 由 BaaS SSE converter 生成。
- Engine payload 中的 `runId`、`seq`、`ts` 不进入 BCN 输出。

该规则让 interaction 与同一 SSE 流中的 `agent`、`chat` 事件共享统一序列。

## 公共字段

三类 requested interaction 均输出以下公共字段：

| Engine 来源 | BCN `data` 字段 | 规则 |
| --- | --- | --- |
| BaaS 持久化的 `baas_interaction_id` | `interactionId` | 必需；格式为 `BAAS-INTERACTION-` + SHA-256 前 32 位 hex |
| `payload.kind` | `kind` | 必需 |
| `payload.phase` | `phase` | 缺失时根据源事件名推导；与源事件名冲突时以源事件名为准并记录 warning |
| `payload.title` | `title` | 缺失时不输出 |
| `payload.description` | `description` | 缺失时不输出 |
| `payload.toolCallId`，fallback `payload.subject.toolCallId` | `toolCallId` | 缺失时不输出 |

可选字段缺失时直接省略，不输出 `null`。

## `ask_user` requested 映射

除公共字段外：

| Engine 来源 | BCN `data` 字段 | 规则 |
| --- | --- | --- |
| `questions[].question` | `questions[].question` | 保留问题正文 |
| `questions[].header` | `questions[].header` | 存在时保留 |
| `questions[].header` | `questions[].questionId` | 作为回答键 |
| `questions[].allowOther` | `questions[].allowOther` | 仅 options 问题可选，且只转发 bool |
| `questions[].multiSelect` | `questions[].multiSelect` | 可选，只转发 bool |
| `questions[].options[].decision`，fallback `.value`，旧格式再 fallback `.label` | `questions[].options[].value` | 使用 label fallback 时记录脱敏 warning |
| `questions[].options[].label` | `questions[].options[].label` | 必需 |
| `questions[].options[].description` | `questions[].options[].description` | 可选 |

Engine 当前不提供独立 `questionId`。正常情况下使用 `header`。如果 header 缺失
或为空，BaaS 按问题位置生成稳定 fallback：`question_1`、`question_2`，依次类推，
并记录 warning。header 和 fallback ID 进入同一个唯一键集合；出现重复
`questionId` 时，转换阶段由后一题覆盖前一题，只输出一个问题并保留该 key 首次
出现的位置，同时记录 warning。

当前 Engine orchestrator bridge 的旧 `InteractionQuestion` options 只有
`label`、可选 `description/preview`，没有 `decision/value`。BaaS 因此按非空
`decision` > 非空 `value` > 非空 `label` 的优先级生成 BCN option value；只有
最后一级属于旧格式兼容，并记录不包含 label 或 question 正文的
`legacy_label_fallback` warning。唯一性、1..4 上限和后写覆盖均基于最终生成的
option value，因此重复 label-only 选项也在原位置稳定覆盖。

BCN 每个 `ask_user` interaction 要求 1..4 个唯一问题；每个显式 options 问题
要求 1..4 个唯一 option value。超过上限的唯一项记录 warning 后跳过，重复
option value 同样采用后写覆盖且保留首次位置。只有源 question 完全没有 options
key 时才按自由文本问题处理并省略 options；这种情况下也必须省略 `allowOther`。
只要 options key 存在（包括值为 `null`），其值就必须是数组且转换后包含 1..4
个有效 option，否则跳过该 question；全部 question 均无效时跳过当前
interaction，但不终止后续 SSE。

示例：

```text
event: interaction
data: {
  "runId": "bcn-run-1",
  "seq": 7,
  "interactionId": "int-ask-1",
  "kind": "ask_user",
  "phase": "requested",
  "title": "Choose deployment",
  "questions": [{
    "header": "Region",
    "questionId": "Region",
    "question": "Which region should be used?",
    "multiSelect": false,
    "options": [{
      "value": "cn-hangzhou",
      "label": "Hangzhou",
      "description": "Lowest latency"
    }]
  }],
  "ts": 1787043219471
}
```

## `exec` requested 映射

除公共字段外：

| Engine 来源 | BCN `data` 字段 | 规则 |
| --- | --- | --- |
| `payload.cwd` | `cwd` | 可选 |
| `payload.command` | `command` | 可选；仅非空字符串会被复制 |
| `payload.options[].label` | `options[].label` | 必需 |
| `payload.options[].decision`，fallback `.value` | `options[].decision` | 两者都缺失时跳过该选项并 warning |

旧 Engine 类型定义中的 exec 可能没有顶层 `options`；BaaS 接受这种输入并合成
BCN 可执行的 `allow-once`、`allow-always`、`deny` 三个标准选项。显式提供
`options` 时必须是非空数组；非法子项被过滤，重复 decision 由后一项覆盖前一项，
过滤后没有有效选项则跳过当前 interaction。显式 `null` 不触发默认选项合成。
`command` 缺失、为 `null`、非字符串、空字符串或仅含空白时不阻断 interaction，
BaaS 省略该可选字段且不记录 warning；非空字符串保持原值透传。

```text
event: interaction
data: {
  "runId": "bcn-run-1",
  "seq": 8,
  "interactionId": "int-exec-1",
  "kind": "exec",
  "phase": "requested",
  "title": "Command approval required",
  "cwd": "/workspace",
  "command": "npm run deploy",
  "options": [{"label":"Proceed","decision":"proceed"}],
  "ts": 1787043219472
}
```

## `mode_switch` requested 映射

除公共字段外：

| Engine 来源 | BCN `data` 字段 | 规则 |
| --- | --- | --- |
| `payload.fromMode`，fallback `payload.subject.fromMode` | `fromMode` | 可选 |
| `payload.toMode`，fallback `payload.subject.toMode` | `targetMode` | 可选 |
| `payload.options[].label` | `options[].label` | 必需 |
| `payload.options[].decision`，fallback `.value` | `options[].decision` | 两者都缺失时跳过该选项并 warning |
| `payload.options[].targetMode` | `options[].targetMode` | 可选 |
| `payload.options[].recommended` | `options[].recommended` | 可选 bool |

实际 Engine 消息在 payload 顶层同时提供 `fromMode`、`toMode`，并在 option 中
同时提供 `decision`、`value`。转换优先使用顶层模式字段和 `decision`；
`subject` 与 `value` 仅用于兼容旧格式。

BCN Provider 2.0 SSE 协议同步增加：

- 与 `fromMode` 平级的可选 `targetMode: string`；
- 明确 `options[].targetMode: string` 为可选；
- 新增可选 `options[].recommended: bool`。

```text
event: interaction
data: {
  "runId": "bcn-run-1",
  "seq": 9,
  "interactionId": "int-mode-1",
  "kind": "mode_switch",
  "phase": "requested",
  "title": "Plan mode transition",
  "description": "Transition from plan to execute",
  "toolCallId": "fc-1",
  "fromMode": "plan",
  "targetMode": "execute",
  "options": [
    {"label":"Continue to execution","decision":"proceed","recommended":true},
    {"label":"Stay in planning","decision":"stay"}
  ],
  "ts": 1787043219473
}
```

## 字段白名单

除了以上定义的公共和 kind 专有字段，Engine 内部字段不进入 BCN 数据，包括但
不限于重复 `id`、`interactionType`、`status`、`subject`、`toolName`、option
中的 `value` 与 `optionId`、`inputSchema`、`uiHints`、时间和生命周期状态、
持久化状态、Engine `runId`、Engine `seq` 与 Engine `ts`。

## Resolved 事件

Engine 的 `interaction.resolved` 和 `mode_transition.resolved` 都转换为
`event: interaction` 和扁平 `data`，phase 为 `resolved`。BaaS 在内部 envelope
保留原始 Engine 事件名；converter 以事件名映射 BCN phase，因此
`mode_transition.resolved` payload 中的 Engine lifecycle phase（例如
`proceeded`）不会泄漏为 BCN phase。公共身份字段及 BCN 已有 resolved 字段按
白名单输出。这样 requested 和 resolved 共享一个协议入口，不保留
`event: interaction.resolved` 或 `event: mode_transition.resolved` 的不兼容 SSE
形式。

mode-switch requested chunk 在活跃 stream 上暴露后，其 Engine interactionId 会
进入该 stream 的内部有界 pending 集合。对应 `mode_transition.resolved` 到达时，
BaaS 通过 Engine 身份找到持久化 public ID，并只投递一次
resolved SSE，即使更早的成功 RPC response 已将 DB record 兜底标记为 resolved。
未暴露 requested chunk、非法 kind 或重复 terminal event 不进入这一兼容投递
路径。

## 容错与日志

单条 interaction 的转换错误不能冒泡到 SSE 流级错误处理，也不能关闭聊天流。

- 缺少或非法 `interactionId`、`kind`：跳过该 interaction 并 warning。
- 未知 kind：跳过该 interaction 并 warning。
- phase 缺失：从 Engine 事件名推导；冲突时使用事件名语义并 warning。
- 单个 question 或 option 结构非法：只跳过该子项；显式 options 过滤后为空时
  跳过所属 question。
- exec/mode_switch option 同时缺少 `decision` 和 `value` 时跳过；ask_user 在 label
  有效时使用上述旧格式 fallback，并记录脱敏 warning。
- header 缺失、为空或重复：使用上述 fallback/后写覆盖策略并 warning。
- 任一 ask_user question 包含 `secret` 或 `isSecret` key 时，无论其值为何都跳过
  整个 interaction 并 warning，避免 BCN 暴露 secret 输入。
- questions 缺失、非数组或过滤后为空：跳过当前 interaction 并 warning；后续
  SSE 和序号不受影响。
- questions 和每题 options 均限制为 1..4 个唯一键；超限项跳过，重复键后写覆盖，
  均记录 warning。
- kind converter 的意外异常在 interaction 转换边界捕获，跳过当前 interaction，
  不中断后续 SSE。

warning 使用结构化上下文，仅记录 BCN runId、interactionId、kind、字段路径或
数组索引、错误类型；不记录 question、command、description、answer 等业务敏感
内容。

## 测试策略

### BaaS converter 单元测试

- 三类 requested 均输出 `event: interaction` 和扁平数据。
- `runId` 来自 BCN `chat.send` request id；Engine runId/seq/ts 不泄露。
- SSE `id` 与 `data.seq` 相同，interaction 与相邻 agent/chat 共享递增序列。
- 公共字段、可选字段省略和 toolCallId 顶层/subject fallback。
- ask_user 的 question 正文、header 派生 questionId、缺失 fallback、重复后写覆盖、
  1..4/唯一性限制、bool-only `multiSelect`/`allowOther`、自由文本约束，以及 option
  decision/value/legacy-label fallback；用真实 Engine label+description-only shape
  验证输出仍满足 BCN 合约且 warning 不泄露业务文本。
- exec 的可选 command 省略规则、有效 command 透传、无 options 默认选项、显式
  非法 options 拒绝、option fallback 与重复 decision 后写覆盖。
- 使用实际抓包形状作为 mode_switch fixture，验证顶层 toMode 到 targetMode、
  subject fallback、decision/value fallback、recommended 和 option targetMode。
- 验证 Engine 私有字段不会进入 BCN 数据。
- 缺少必需身份、未知 kind、非法子项和转换异常只跳过当前 interaction，后续
  chat/agent 事件仍可输出。
- phase 缺失和冲突行为。
- resolved 事件也输出统一的 BCN interaction 形式。

### BCN 合约测试

- 更新 Provider 2.0 SSE 协议示例和字段说明。
- 增加 mode_switch fixture，覆盖顶层 `targetMode`、可选
  `options[].targetMode` 和 `options[].recommended`。
- 验证 parser 接受事件且 raw 数据完整保留新增字段。现有 parser 对 kind 专有
  字段采用 raw 保留，预期无需修改核心解析逻辑。

## 兼容性与风险

这是 BaaS 对外 SSE wire format 的有意修正：旧消费者如果依赖
`event: interaction.requested` 或 `data.payload` 将不再兼容；BCN Provider 2.0
消费者则从无法解析变为可解析。功能继续受现有 BaaS interaction process 配置门控。

主要风险是 Engine 实际消息随版本变化。字段白名单、顶层优先加旧结构 fallback、
子项级容错和结构化 warning 用于限制风险。回滚可恢复旧 converter 输出；完整
Engine envelope 的 payload 快照保持原样；interaction 表新增独立的
`baas_interaction_id` 列。升级前已持久化的行以此前已经暴露的 Engine
`interaction_id` 回填 public ID，避免部署期间的 pending interaction 失效。

interaction 状态 payload 对允许决策采用显式三态兼容历史 pending 数据：旧记录
缺少 `allowedDecisions` 时解码为 `None`，仅这种状态按 legacy unrestricted 处理；
新记录显式写入决策数组，空数组解码为 `()` 并 fail-closed，非空 tuple 按白名单
校验。序列化时 `None` 继续省略字段，而 `()` 必须保留为 `allowedDecisions: []`，
因此既不会把新无效 interaction 误当 unrestricted，也不会阻断升级前已经持久化、
尚待 resolve 的 pending interaction。ask_user 新记录固定允许 `submit`、`cancel`，
并忽略其协议不支持的顶层 options；exec/mode_switch 只持久化可实际展示的选项。
