# BCN Group A2A 接口与执行规范

- 状态：方案草案；已确定的范围与设计、待确认的参数和合同分别列出。
- 更新日期：2026-11-12。
- 所属模块：BCS / BCN。
- 方案依据：[方案评审文档](design-review.html)，以及 2026-11-12 讨论更新。
- 代码依据：评审稿核对的 `origin/dev`，提交 `9a61798cb7752308aaa01f8e59d78a46809066cb`。
- 协议：A2A 1.0.1；线上 `A2A-Version` 和 Card 的 `protocolVersion` 使用 `1.0`。

本规范定义已有协作群对外提供 A2A 调用的合同、执行生命周期、持久化和模块分工。以下将上游调用 BCN 的一方称为客户端，将一个对外发布的 Group 视为一个 Agent。

## 1. 范围与核心决策

### 1.1 本期范围

本期支持主从群（ManagerWorker）和自定义协作群（StateMachine）：

- 发布 Group Agent Card，提供发送、流式发送、Task 查询/列表、订阅与取消。
- 首轮创建 Session；同 context 的后续独立请求创建新 Task。
- 同 context 只执行一轮，忙碌时拒绝，不排队。
- 通过可选扩展控制公开过程消息；调用来源字段及认证信息传递方式待定。
- 最终 Artifact 单独建表持久化；本期对外支持文本和 JSON，存储支持非文本类型的表达。
- 补齐执行期限、取消确认、异常收尾、重启恢复及资源归属校验。

| 范围 | 安排 |
| --- | --- |
| 本期 | ManagerWorker、StateMachine 的 Task 型调用与 Artifact 持久化 |
| 下一期 | 文件输入；Task 等待补充输入与恢复；同 context FIFO 排队是否支持另行确认 |
| 后续讨论 | 自由聊天群接入：返回 Message，或由 LLM 总结为 Artifact，见 §12 |
| 联合设计 | 来源和用户字段的命名、内部结构、凭据传递、认证链及 Card 安全声明 |
| 单独排期 | BCN 单 Agent 标准 A2A 入站、Push Notification |
| 暂不考虑 | 自然语言建群，因复杂度与风险较高，本方案不规划其接口、助手或 Fuse/MCP 建群流程 |

### 1.2 核心模型

| 对外概念 | BCN 中的对应 | 本期规则 |
| --- | --- | --- |
| Agent | Group | 以 `group_id` 定位，客户端无需提交 manager、成员或流程定义 |
| context | Session | `contextId = Session.id`，不新增一套 context ID 或映射表 |
| Task | 一轮独立执行 | 绑定一次 Session activation，或一个 StateMachine Run |
| Message | 一条输入或公开回复 | `messageId` 标识输入；本轮输入和公开历史持久化 |
| Artifact | 本轮可交付的结果 | 保存到独立 `artifacts` 表，一个 Task 可有多个 Artifact |

本期受理的业务执行统一采用 Task-generating 模式，即使很快结束也返回 Task。此选择适用于本期两类群；自由聊天群的响应模式在 §12 单独讨论。

### 1.3 已有能力与本期新增

| 领域 | 已有能力 | 本期需要补齐 |
| --- | --- | --- |
| BCN 单 Bot | 单 Bot 聊天、Card 管理、A2A 出站 | 单 Bot 标准 A2A 入站另行排期 |
| Group Card | Group Card 模板及存储 | 生成、发布描述 BCN Group 入站地址和实际能力的完整 Card |
| Session | `ServiceInvocation`、`caller_principal`、activation、输入/输出 | A2A 来源记录、原子占用、结束后再激活、各轮 Task 关联 |
| 主从群 | manager 的 `task.complete`、summary、待办及结果回送检查 | 精确绑定本轮、完成 ack 与执行退出分离、公开结果保存、整轮取消 |
| 状态机 | Run input/output、启动、失败 rerun、底层 abort 能力 | 新输入启动下一轮、整轮停止确认、HumanInput 能力检查 |
| 事件 | `bcs_events`、事件标识和 stream 内顺序 | A2A Task/history 投影、消费位置恢复、SSE |
| 结果 | Session.output、Run.output | 独立 Artifact 表、每轮不可覆盖的结果、与终态一致提交 |
| Task 管理 | 底层 Session/Run/delivery 各自状态 | 对外 Task、ID 预留、统一查询/列表/订阅/取消及恢复 |

## 2. 协议绑定与对外接口

### 2.1 绑定选择

本期采用标准 HTTP+JSON 绑定，流式输出采用 SSE。接入方需要支持 A2A 1.0 的 HTTP+JSON 绑定，并从 Card 读取地址和能力。

JSON-RPC 同样可以承载 SSE；本期不增加 JSON-RPC 入站。若后续有实际调用方需要，新增 delivery adapter，复用同一套调用应用服务与持久化记录。

请求头：

```http
Content-Type: application/a2a+json
A2A-Version: 1.0
```

流式响应使用 `Content-Type: text/event-stream`。认证凭据及来源凭据如何传递列入 §13，以下示例省略尚未定稿的认证字段。

### 2.2 路由

基地址：

```text
/api/v1/collaboration/a2a/groups/{group_id}
```

| 方法与相对路径 | 操作 | 响应 |
| --- | --- | --- |
| `GET /agent-card.json` | Group Card 发布 | AgentCard |
| `POST /message:send` | SendMessage | `SendMessageResponse`，本期使用 `task` 分支 |
| `POST /message:stream` | SendStreamingMessage | SSE，先 Task，再状态和产物事件 |
| `GET /tasks/{task_id}` | GetTask | Task 快照 |
| `GET /tasks` | ListTasks | 标准列表响应及游标分页 |
| `POST /tasks/{task_id}:subscribe` | SubscribeToTask | SSE，先当前 Task，再后续事件 |
| `POST /tasks/{task_id}:cancel` | CancelTask | 更新后的 Task |

新增入站只使用版本化路径。上表中的 `task_id` 是 A2A Task ID，不是 BCS 内部派发任务 ID。

当前 axum 0.8.9 / matchit 0.8.4 不支持直接注册 `/tasks/{task_id}:cancel` 形式的动态后缀。适配层将 GET 与 POST 合并注册到 `/tasks/{task_action}`：

- GET 将捕获值整体视为 Task ID。
- POST 只解析末尾的 `:cancel` 或 `:subscribe`；去掉后缀后 ID 必须非空。
- 未知动作或缺少动作后缀返回 404；非法 ID、请求体 `id` 与路径不一致返回 400。
- 不重复 URL 解码；对外路径仍保持上表的标准形式。

### 2.3 与已有 Card 管理接口共存

| 既有接口 | 用途 |
| --- | --- |
| `GET / POST /api/v1/collaboration/a2a/agents` | 列表、注册外部 Agent |
| `GET / PUT /api/v1/collaboration/a2a/agents/{bot_id}/agent-card` | 查看、更新已注册外部 Agent 的 Card 配置 |
| `POST /api/v1/collaboration/a2a/agents/{bot_id}/agent-card:refresh` | 重新拉取外部 Agent 的 Card |

上述管理接口保留 `/a2a/...` 旧前缀入口，沿用其原处理器和权限。

本期新增的 `/groups/{group_id}/agent-card.json` 发布 BCN 自己的群调用能力。BCN 单 Agent 入站仍是另行排期的设想，候选基地址为 `/api/v1/collaboration/a2a/agents/{bot_id}`，发布 Card 使用 `agent-card.json`，与已有管理接口分开。

### 2.4 Agent Card

Card 根据群的实际能力生成：

- `supportedInterfaces` 声明真实基地址、`protocolBinding: "HTTP+JSON"`、`protocolVersion: "1.0"`。
- `defaultInputModes` 为 `text/plain`、`application/json`；skill 层覆盖按实际能力处理。
- 主从群 `defaultOutputModes` 为 `text/plain`；状态机按最终输出节点声明文本、JSON 或两者。
- `capabilities.streaming = true`，`capabilities.pushNotifications = false`。
- `documentationUrl` 指向接入说明，说明输入限制、忙碌拒绝、等待上限和结果保留策略。
- 可选扩展在 `capabilities.extensions` 声明；认证及安全声明待联合设计。

### 2.5 标准字段与 BCN 选择

“标准”列表示协议已有字段或允许的行为；“BCN 约定”列表示本方案另外固定的处理方式。

| 能力 | 标准 | BCN 约定 | 本期处理 |
| --- | :---: | :---: | --- |
| Task / contextId / Message / Artifact | ✅ | — | 使用标准对象和标识字段 |
| 每轮新 Task | ✅ | ✅ | 本期两类群统一采用 Task-generating，同 context 关联多轮 |
| `returnImmediately` | ✅ | — | 默认 false 等终态或中断态；true 受理后立即返回 |
| `historyLength` | ✅ | — | 限制返回历史数量，不影响过程消息是否生成 |
| `acceptedOutputModes` | ✅ | ✅ | 与实际输出能力求交集，无交集时受理前拒绝 |
| `messageId` 去重 | ✅ | ✅ | 标准允许 MAY；BCN 内部持久化去重，不发布 message-dedup/v1 |
| 同 context 忙碌拒绝 | — | ✅ | 不并行、不排队；409 / CONTEXT_BUSY |
| working Task 不接受追加 | ✅ | ✅ | 返回 UnsupportedOperationError |
| 过程消息承载 | ✅ | — | 使用 status.message、公开 history 等标准结构 |
| `includeProgress` | — | ✅ | 可选扩展开关，默认 false |
| Artifact 独立表及持久化期限 | — | ✅ | 采用标准 Artifact/Part 结构，保存策略由 BCN 定义 |
| HTTP 等待上限及 504 reason | — | ✅ | HTTP 等待结束，后台 Task 继续执行 |
| 来源字段、凭据传递 | — | ✅ | 尚未定稿，见 §4 与 §13 |

标准定义参见 [A2A 1.0.1](https://a2a-protocol.org/v1.0.1/specification/)。

## 3. 输入、返回配置与查询

### 3.1 输入 Parts 与角色

发送请求需要 `message.messageId`、`message.role`、`message.parts`。客户端向 Group 发消息使用 `ROLE_USER`；Group 输出的 Message 使用 `ROLE_AGENT`。调用方即使是另一个 Agent，发送方向仍使用 `ROLE_USER`。

本期接收保序的文本 Part，以及至多一个对象类型的 data Part：

- 每个 Part 的 `text`、`data`、`raw`、`url` 四个内容分支只能选择一个。
- 数组或标量 data、多个 data Part 返回 400 / INVALID_ARGUMENT。
- 文件 `raw` / `url` Part 本期不接收，返回 ContentTypeNotSupportedError。
- 原始结构保存在本轮输入中；主从群把对象作为独立 JSON 数据块交给 manager，状态机映射到 `Run.input.data`。
- 文本和 data 的限制属于本期输入范围；Artifact 的存储格式不受“输入 data 必须是对象”的限制。

请求体大小建议为 1 MiB，覆盖 message、configuration 和 metadata；网关与适配层一致校验，超限在解析和受理前返回 413。数值待 §13 确认。

### 3.2 返回时机

| 调用方式 | 返回行为 | 后续获取方式 |
| --- | --- | --- |
| `message:send`，`returnImmediately=false` 或省略 | 等到终态或中断态后返回当前 Task 和结果 | 成功返回时直接读取结果 |
| `message:send`，`returnImmediately=true` | 持久化受理后立即返回当前 Task，不等待执行完成 | GetTask 或 subscribe |
| `message:stream` | 立即建立流，先返回 Task 快照，再推送更新 | 同一 SSE 连接 |

`returnImmediately` 对流式操作无效。初始 Task 通常为 SUBMITTED，也可能已推进到后续状态；服务端返回已提交的真实快照。

默认阻塞发送需要新增“服务端等待执行完成或中断后再返回 Task”的能力。单次 HTTP 等待建议上限 60 秒，达到上限返回 `504 / DEADLINE_EXCEEDED`、`SEND_WAIT_TIMEOUT`；不以成功响应返回 working Task。能够确定 Task 时，错误 details 携带其 `taskId` 和 `contextId`。

HTTP 超时或客户端断线不取消业务执行；Task 继续受独立执行期限控制。网关等待期限应长于服务端等待并留余量。网关自身返回的 504 可能没有 JSON 错误体或 Task ID，此时执行结果未知；上游已有 Task ID 时查询原任务，不把超时当作未受理。

接入说明和 Card.documentationUrl 需说明等待上限；长任务推荐 `returnImmediately=true` 或流式。

### 3.3 输出模式、历史与列表

`acceptedOutputModes` 与 Card/skill 实际输出能力协商，无交集时受理前返回 ContentTypeNotSupportedError。受理时保存选定输出类型，最终结果和公开消息遵循已接受的模式；实际结果无法满足约定或发布校验时，该轮失败收尾。

`historyLength` 只裁剪响应中的公开历史。省略时使用服务端默认量；0 不包含历史；正数返回至多该数量的最近消息。它不改变已保存历史，不控制 `includeProgress`。

GetTask 只读取已提交快照和 Artifact，不推进执行。ListTasks 按已解析主体和目标群过滤后，执行标准筛选与游标分页，按状态更新时间倒序；支持标准 `contextId`、`status`、`pageSize`、`pageToken`、`historyLength`、`statusTimestampAfter`、`includeArtifacts` 参数。默认不返回 artifacts；需要产物时显式请求，并批量加载当前页的产物。

## 4. metadata、来源与资源归属

### 4.1 已确定与待定的边界

| 内容 | 当前结论 |
| --- | --- |
| `metadata.includeProgress` | 可选 boolean，默认 false；本轮首次受理时固定，下一轮可重新选择 |
| 上游 Agent 来源 | 需要表达；暂用 `caller` 指代，字段名与内部结构待定 |
| 上游背后的用户来源 | 需要表达；暂用 `user` 指代，字段名与内部结构待定；自动调用可无用户 |
| 凭据与认证信息传递 | 待定；metadata、HTTP 头或其他载体，与认证机制联合设计 |
| 来源与认证的关系 | 自报来源用于上下文展示和追踪；资源归属和去重范围使用已解析的 `caller_principal` |

此前提出的 `caller: {id, name?}`、`user: {id}` 保留为讨论候选。接入示例只使用已明确的 `includeProgress`；来源字段和最终 Schema 在命名、结构及凭据传递结论确定后一起更新。

来源绑定的当前建议为：首次创建 context 时保存来源，后续省略则继承；更换或补绑定来源 ID 返回 CONTEXT_SOURCE_CONFLICT，另建 context。展示名称不作为绑定键。此行为与来源合同一并确认，每轮保存实际采用的来源快照。

### 4.2 扩展发布与激活

扩展 URI 和 Schema 采用 GitHub 仓库中的版本化文件地址，参照已有 [source-agent/v2](https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/source-agent/v2.md) 的发布方式。

候选地址如下，扩展名和具体文件名在定稿时确认：

```text
扩展规范 / URI：
https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.md

Schema：
https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.schema.json
```

扩展 URI 标识规范，各环境使用同一 URI。Card 声明与 `A2A-Extensions` 头使用同一个扩展 URI；Schema 的 `$id` 对应 Schema 自身标识，并与 Card 中 Schema 引用一致。需要自动获取 JSON 时使用对应的仓库原始内容地址或明确的仓库解析方式，避免把 GitHub blob 展示页当成 JSON。

Card 片段：

```json
{
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "extensions": [{
      "uri": "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.md",
      "description": "公开过程消息配置；来源字段待定",
      "required": false,
      "params": {
        "metadataSchema": {
          "$ref": "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.schema.json"
        }
      }
    }]
  }
}
```

`params.metadataSchema` 是本扩展的发现约定。扩展行为为：

1. 不使用扩展字段时，允许普通标准调用；`includeProgress` 按 false 处理。
2. 使用本扩展拥有的字段时，必须在 `A2A-Extensions` 中请求激活。
3. 带字段但缺少激活头，在创建 Session、Task 和 ID 预留前返回 CALL_METADATA_NOT_ACTIVATED；补头后可重新提交。
4. 响应确认已激活的扩展 URI；不把未激活字段静默当作已生效。
5. 最终 Schema 定义本扩展拥有的键；其他扩展键可以共存，同名键冲突需在发布组合时解决。

现有 [call-metadata.schema.json](call-metadata.schema.json) 只定义已明确的 includeProgress，`$id` 使用候选仓库地址。来源字段定稿后补齐 Schema，再将正式规范和 Schema 发布到对应目录，同步最终引用。

### 4.3 资源归属

沿用现有调用主体解析，使用 `caller_principal` 校验。context 的来源信息不替代此校验。

- 新建 A2A context 使用 `ServiceInvocation` Session，保存 A2A 创建来源；该记录保留到 Session 删除。
- 续聊只接受属于该目标群、当前调用主体且由 A2A 创建的 Session。
- GetTask、ListTasks、subscribe、cancel 和重复请求查询使用同样的归属范围。
- 列表先按归属过滤，再分页和计算可见结果。
- 未知、跨群或不可访问的 Task 返回 TaskNotFoundError；不合格 context 返回 CONTEXT_NOT_FOUND，不悄悄替换成新会话。

具体凭据、调用方用户与认证主体的映射、Card 安全声明列为待定，不在本规范中新增认证方案。

## 5. 受理、并发与去重

### 5.1 受理顺序

1. 校验调用主体、协议版本、群能力、输入、输出模式与扩展激活。
2. 校验目标群和已提供资源的归属；带 Task ID 时先解析其所属 context。
3. 检查同范围 messageId：相同语义请求复用原 Task，不同指纹返回冲突。
4. 对新请求校验来源绑定及 Task/context 状态。
5. 原子保存 messageId 预留、初始 Task、执行代次及 context 占用，关联新建或已有 Session。
6. 持久化成功后启动群执行；受理与外部派发之间的恢复按 §7 处理。

查重、唯一预留和占用须在并发下共同成立。两个首轮相同 messageId 的请求最多生成一个逻辑 context 和一轮执行。数据库事务不跨外部投递等待；写入失败必须返回失败，不执行未持久化的请求。

### 5.2 接受与拒绝规则

以下“新消息”指未命中同请求去重的消息：

| 请求情形 | 处理 |
| --- | --- |
| 无 contextId、无 taskId | 创建 Session 和新 Task |
| 有 contextId、无 taskId，context 空闲 | 原 Session 新 activation / 新 Run，创建新 Task |
| 有 contextId、无 taskId，已有占用 | 409 / ABORTED，CONTEXT_BUSY；不创建 Task |
| context 尚未确认旧执行停止 | 400 / FAILED_PRECONDITION，CONTEXT_UNAVAILABLE |
| 对 working Task 发新消息 | UnsupportedOperationError，不追加、不排队 |
| 对终态 Task 发新消息 | UnsupportedOperationError；新一轮应省略 taskId |
| taskId 未知、跨群或不可访问 | TaskNotFoundError |
| contextId 未知、跨群、不可访问或不是 A2A 创建 | CONTEXT_NOT_FOUND |
| 同时提供 taskId 与不匹配的 contextId | 400 / INVALID_ARGUMENT |
| 来源与已绑定来源冲突 | CONTEXT_SOURCE_CONFLICT，具体来源合同待定 |

忙碌、参数错误、来源冲突等受理前错误不预留新的 messageId。客户端可在前一轮结束后重新提交被拒绝的请求。

占用覆盖 SUBMITTED、启动中、WORKING、取消中以及收尾未确认的全过程。不能只检查 `status == working`。Task 失败后如仍需隔离，context 继续暂停接受新一轮。

### 5.3 messageId 去重

A2A 对发送去重允许 MAY。本期采用内部持久化去重，不发布既有 message-dedup/v1 扩展。

去重键：`tenant（若有）+ caller_principal + group_id + messageId`。

指纹覆盖规范化后的完整语义请求和已激活扩展集合，包括 parts、客户端提供的 context/task ID、configuration 与 metadata。`returnImmediately`、`historyLength`、`acceptedOutputModes`、`includeProgress` 都参与指纹；JSON 键顺序与等价默认值先规范化。服务端生成的 contextId 不反写原请求。

| 同一去重键 | 处理 |
| --- | --- |
| 指纹相同，结果存在 | 复用原 Task，不再派发；返回方式继续遵循本次 send/stream 合同 |
| 指纹不同 | MESSAGE_ID_CONFLICT |
| 结果已实际清理 | MESSAGE_RESULT_EXPIRED，预留继续保留，不重新执行 |

仅从 send 切换到 stream 不改变业务请求指纹，扩展激活集合和语义参数保持一致。重复的阻塞 send 仍需等待原 Task 终态或等待超时；stream 命中终态 Task 可返回该 Task 快照后关闭，这是重复发送的处理，与对终态 Task 新建 subscribe 分开。

已有 [message-dedup/v1](../../../../docs/specs/a2a/extensions/message-dedup/v1.md) 面向认证 Bot endpoint，包含明确的重启行为与客户端重试前提。Group 的范围和恢复方式需另行确认后才能发布或协商该扩展。BCS 出站客户端未协商它时，对首轮结果未知的调用不自动重发；有 Task ID 时查询原执行。

## 6. 执行与完成

### 6.1 状态映射

| 执行事实 | A2A 状态 | 提交条件 |
| --- | --- | --- |
| 已原子受理，尚未开始 | SUBMITTED | Task 与占用已持久化 |
| manager 或 Run 执行中 | WORKING | 关联明确的 activation / Run |
| 已取得本轮最终结果且执行收尾完成 | COMPLETED | 对外 Artifact 与终态同事务提交 |
| 启动失败、manager 报错、Run 失败、超时、产物校验失败 | FAILED | 停止相关工作并完成收尾，或转入已隔离的失败状态 |
| 用户取消且全部关联工作已停止并收尾 | CANCELED | 与完成、失败提交互斥 |
| 意外进入 HumanInput | FAILED | 本期没有输入恢复，按有界收尾处理 |

线上状态使用 `TASK_STATE_` 前缀。本期不主动产生 REJECTED，但终态校验和订阅/取消规则仍识别这个标准状态。本期不接入依赖补充输入的群执行路径；含静态 HumanInput 的流程在能力校验时拒绝开放。

### 6.2 主从群

已有 manager `task.complete` 支持 completed/error、summary 及待办检查。本期完成判定要求：

1. 完成信号精确指向当前 Session、activation 和关联 Task；禁止回退到群的最新 Running Session。
2. 原子关闭本轮新派发，确认待办和结果回送完成，固定候选最终 summary 与事件边界。
3. 向 `task.complete` 返回受理 ack。
4. 等待 manager 当前执行退出并完成本轮清理。
5. 完成公开事件投影、结果发布校验；在同一事务保存 Artifact 与 COMPLETED，并释放 context 占用。

manager 可能在自身执行结束前调用完成工具。ack 必须先于等待其退出，避免互相等待；收到完成信号时不能立即开放下一轮。退出或收尾超时转入 §7。

最终结果来自 manager 确认的 summary。普通群聊天、阶段总结、Worker 单项完成或一段时间无消息，都不触发 COMPLETED。manager 的执行上下文或 Skill 需要明确完成动作。

### 6.3 manager 未提交完成

本轮没有运行中或排队的 manager/Worker、待回送结果、定时重试、未确认停止的工作时，才开始空闲计时。

建议连续空闲 60 秒仍无有效 `task.complete`，提醒 manager 一次，再给 120 秒完成宽限。有效完成信号走正常完成路径；派发实际新工作则退出本次空闲检查，工作结束后再检查。每 Task 最多提醒一次，提醒标记和截止时间持久化；提醒后仍不完成，或再次空闲超过宽限，触发 FAILED 收尾。

SSE 心跳、日志及普通过程文本不重置期限。正常长时间运行的 Worker 不因没有过程消息而失败。提醒调用自身也受整轮执行和取消机制约束，不延长整轮预算。具体时长待确认。

### 6.4 自定义协作群与多轮输入

每个新 Task 从入口创建新 Run，保留原 Run 终态与结果。现有失败 rerun 保持原语义：只重跑 Failed Run、沿用原 input；不用于正常续聊改目标。

| 传给执行方的信息 | 来源 |
| --- | --- |
| 最初目标 | 首轮输入，作为 Session 背景保存 |
| 本轮文本和 data | 当前 Message.parts；主从群进入 manager 输入，状态机进入 Run.input |
| 上轮结果和关联 Task | 同 context 的已保存结果及 Artifact |
| 上游来源、用户来源 | 本轮实际采用的来源快照；字段合同待定 |

节点输入绑定和提示必须能够同时消费背景及本轮新要求。只复用 Session ID 不代表已经完成输入传递。适配层传递确定的信息，由执行 Agent 理解“预算改为 50 万”等目标调整；不在适配层解释自然语言或重写业务目标。

新 Run 重新执行；读取旧结果作背景不会替代本轮产物。`opening_message` 继续用于开场和展示，目标输入落到 Run.input 及节点输入合同。上一轮结果已经过期清理时，如何要求上游补齐背景列入 §13。

状态机成功且取得指定最终输出节点的结果后，生成对应类型的 Artifact。公开结果节点必须明确；不依赖节点遍历顺序猜测最后一个输出。

## 7. 取消、失败与恢复

### 7.1 取消整轮执行

取消须先持久化意图，并与成功/失败提交互斥，然后停止新派发：

- 主从群覆盖 manager、Worker 和结果回送。
- 状态机覆盖活动节点、等待节点、定时重试及已派发 Bot 工作。
- 对已启动工作发出取消并追踪停止确认；收尾期限内保持 context 占用。
- 确认全部停止并完成收尾后提交 CANCELED、释放占用。

确认前 CancelTask 可返回仍处于非终态的 Task，并通过公开 status.message 表达停止进度。处理中重复取消复用同一意图；终态后再次取消返回 TaskNotCancelableError。取消、完成只能有一个提交成功；迟到结果不能覆盖终态。

### 7.2 无法确认停止时

| 情形 | Task | context |
| --- | --- | --- |
| 执行超时、Worker 失联或意外 HumanInput | 停止派发，发出取消并等待收尾 | 保持占用 |
| 失败收尾已确认完成 | FAILED | 释放，可开始下一轮 |
| 用户取消且已确认停止、收尾完成 | CANCELED | 释放，可开始下一轮 |
| 收尾期限已过，仍无法确认旧执行停止 | FAILED，公开说明“未能确认停止” | 隔离，暂停接受新一轮 |

隔离只暂停该 context 的新执行，原 Task 仍可查询。新消息返回 `400 / FAILED_PRECONDITION`、`CONTEXT_UNAVAILABLE`。旧代次的新派发和公开事件不再接纳，未核实的底层工作继续记录用于恢复。

后台有限次重试核实并告警，确认旧执行停止后恢复 context。之后可以创建新 Task；原 FAILED Task 保持终态。不能仅凭锁过期或未收到消息就释放占用。

自动恢复失败时需有排查和人工处置入口。恢复条件与处置方式在实现评审确定；具体告警接收人留到部署安排。

### 7.3 重启与故障恢复

持久化执行代次、关联工作、取消意图、事件消费位置和绝对截止时间。重启不重置时间预算，不重复启动已派发工作。

| 故障位置 | 恢复要求 |
| --- | --- |
| 受理持久化前失败 | 不启动执行，客户端收到失败 |
| 受理已提交，尚未启动 | 核实派发记录；只有明确未启动时才能启动一次，无法确定则按未知执行处理 |
| 执行中进程重启 | 恢复占用，追踪已有 activation/Run/delivery |
| 取消或收尾中重启 | 恢复已保存意图与期限，继续确认停止 |
| 完成事务前崩溃 | 不暴露不完整结果，核实本轮执行并继续收尾 |
| 完成事务后、响应前崩溃 | 查询或相同请求重试读取已提交 Task 与 Artifact |
| Task 已失败但 context 隔离 | 恢复隔离及待核实记录，不因 Task 终态自动放开 |
| 旧轮次事件迟到 | 按代次丢弃公开投影，只保留内部追踪 |

受管 Session 删除也要遵守停止确认，不能通过删除 Session 绕过当前执行的收尾。

## 8. Artifact 与其他持久化记录

### 8.1 Artifact 的定义

Artifact 是本轮任务产出的可交付结果，至少含一个 Part。主从群的最终 summary、自定义协作的最终输出都以 Artifact 保存。阶段进展保存在公开消息中。

**已确定：独立建立 `artifacts` 表。** 一个 Task 可有多个 Artifact，结果不依赖内存、SSE 连接或下一轮会覆盖的 Session.output。`artifactId` 在 Task 内唯一。Artifact 的类型由 Parts 表达，表结构不限定为文本，也不限定只能由 manager 或状态机生成。

### 8.2 artifacts 表设计

以下为逻辑字段和约束；物理表前缀、数据库类型、索引细节在实现评审时按现有 store 规范落地。

| 字段 | 建议类型 | 约束与用途 |
| --- | --- | --- |
| `task_id` | string | 必填，所属 A2A Task；与 artifact_id 组成唯一键 |
| `artifact_id` | string | 必填，对应 A2A artifactId；重试读取保持不变 |
| `context_id` | string | 必填，与所属 Task 的 session_id 一致 |
| `name` | nullable string | 可选展示名称 |
| `description` | nullable string | 可选描述 |
| `parts` | JSON | 必填、非空、保序；支持内联内容和存储对象引用 |
| `metadata` | nullable JSON object | 可选的公开 Artifact metadata |
| `extensions` | nullable JSON array | Artifact 声明的扩展 URI |
| `position` | integer | 必填，同 Task 内的产物顺序 |
| `created_at` | timestamp | 必填，本轮提交时间，使用 UTC |

主键/唯一约束采用 `(task_id, artifact_id)`；按 `(task_id, position)` 读取本轮产物。归属从关联 Task 校验，context_id 不单独作为授权依据。无需在每个产物上复制来源和凭据。

建议先用保序 JSON 保存 Parts，避免把所有产物压成一列 text。若实现评审选择 Part 子表，须保持同样的类型、顺序和读写原子性。

### 8.3 Parts 与非文本内容

| 内容类型 | 持久化方式 | 对外序列化 |
| --- | --- | --- |
| `text` | 原文本内联保存 | 标准 `text` Part |
| `data` | 原 JSON 值内联保存，保留类型 | 标准 `data` Part |
| `raw` | 内容由存储插件保存；Part 记录对象引用、大小、摘要和属性 | 开放对应能力后，按标准 raw 分支读取并编码内容 |
| `url` | 保存托管内容引用及必要属性；文件处理流程负责形成可读取内容 | 开放对应能力后，生成标准 url Part |

每个 Part 保留可选的 `mediaType`、`filename` 和公开 `metadata`；顺序不变。内部对象引用属于存储格式，由 adapter 转成标准 Part，不能直接作为未定义的 wire 字段返回。

本期明确 raw/url 的存储引用约定，为其他场景保留非文本能力；Group A2A 对外只开放 Card 声明的文本和 JSON。文件上传、外部 URL 拉取、下载及发布能力后续设计，并在开放时更新 Card。

artifacts 表按类型保存 Parts，来源可由本期两类群及后续调用场景提供。对象存储引用、大小和摘要的具体格式在实现评审确定。

### 8.4 写入、读取与清理

1. 根据完成信号形成最终产物，进行输出类型和公开内容校验。
2. 完成已确定边界内的公开事件投影及执行收尾。
3. 在同一数据库事务中写入本 Task 的全部 Artifact 和成功终态，完成占用释放。
4. 提交后，GetTask 从 Task 与 artifacts 表读取；SSE 按保存的顺序生成 artifactUpdate，再发终态并关闭。

对本期成功任务，COMPLETED 与最终产物一起可见；FAILED/CANCELED 可以没有产物。写入失败不返回成功、不先发送最终产物事件。后续轮次不得覆盖本轮 Artifact。

大对象写入与数据库事务分开：对象先持久化，事务只提交已准备好的对象引用；对象写入失败不提交成功终态。事务失败留下的未引用对象按清理策略处理。

建议终态结果、公开历史、公开事件和 Artifact 保留 7 天，自终态提交起算，读取不续期。非终态或仍在隔离恢复中的记录暂缓清理。实际清理后，GetTask 返回 TaskNotFoundError，相同 messageId 重试返回 MESSAGE_RESULT_EXPIRED；不是仅到达候选期限就假定已经删除。

清理删除产物记录并回收其托管对象；对象回收失败应可重试，只回收 BCN 管理且已无引用的对象。最小 ID 墓碑继续保留，不允许相同 ID 再次执行。依赖恢复或来源校验的最小记录按各自生命周期保留。

### 8.5 其他记录

| 记录 | 至少保存内容 | 约束 |
| --- | --- | --- |
| Task / 调用记录 | task_id、group_id、session_id、activation、Run 关联、原始语义请求、来源快照、输出模式、includeProgress、状态、期限、提醒标记、取消意图 | 独立轮次；成功状态与 Artifact 同事务提交 |
| messageId 预留与墓碑 | tenant、caller_principal、group_id、messageId、指纹、激活扩展集合、task_id、清理标记 | 范围内唯一；随目标 Group 调用存储保留，不使用会重新执行 ID 的短 TTL |
| context 占用 | session_id、当前 task_id、执行代次/状态版本、隔离原因、恢复进度 | 同 context 最多一份占用 |
| 执行关联 | Run、节点、delivery、manager/Worker 工作及停止进度 | 可在重启后核实，不凭猜测重新派发 |
| 公开消息历史 | 原始输入、公开回复及启用后的过程消息、顺序 | 返回按 historyLength 裁剪 |
| 事件消费位置 | task_id、源 stream、sequence、event_id、输出顺序、完成边界 | 重复消费不重复发布，终态后不追加公开事件 |
| A2A Session 来源 | Session 创建来源及归属所需信息 | 保留至 Session 删除，不随首轮 Task 结果过期而丢失 |

GetTask 按 Task ID 加载产物；ListTasks 先分页，再批量读取当前页产物，避免逐 Task 查询。恢复和清理按有界批次执行，失败退避；不在持有数据库事务时等待外部网络。

## 9. 公开事件、SSE 与内容边界

### 9.1 一份持久化结果，两个出口

复用 `bcs_events` 记录执行事实，新增 A2A 投影。内部事件的 task_id 不是 A2A taskId，必须通过本轮执行关联转换。sequence 只在源 stream 内有序；聚合后为 A2A Task 保存确定的输出顺序和消费位置，并按 event_id 去重。

启用所需业务事件记录，沿用业务变更与事件同事务落库的要求。投影更新 Task 与公开 history；最终 Artifact 来自 artifacts 表。GetTask 与 SSE 使用同一份已提交状态和产物，查询本身不推进执行。

终态前关闭本轮派发，固定各源完成边界，投影边界内的公开事件，再原子保存 Artifact 和终态。SSE 顺序为“公开过程事件 → 最终产物 → 终态”。失联源不能无限等待，以已经持久化的边界封存失败轮次；迟到回调仅进入内部追踪。

本期最终 Artifact 在完成时一次提交；不要求逐 token 生成 Artifact。后续若增加分片产物，需要明确 append/lastChunk 对持久化的更新规则。

### 9.2 订阅与保活

- message:stream 和 subscribe 的首帧为当前 Task 快照，然后发送后续更新。
- 新订阅已终态 Task 返回 UnsupportedOperationError，客户端改用 GetTask。
- 已连接的流收到终态后关闭。
- 无业务事件时每 15 秒发送 SSE 注释 `: keep-alive\n\n`。
- 心跳不生成 A2A 事件、不写 history、不受 includeProgress 控制，不延长执行或完成宽限。
- 适配层及时 flush；网关关闭该路径的代理缓冲，空闲超时大于心跳间隔并留余量。
- 总连接期限与空闲期限分开，部署应覆盖执行和收尾，或允许按 taskId 重新订阅。
- 重连先读取当前快照，不承诺断线期间事件逐条重放；断线不触发业务消息重发。

### 9.3 对外内容

includeProgress 只控制公开业务进展，状态、错误和最终结果始终可获取。筛选在写入公开 history、status.message、Artifact 和事件输出之前完成。

| 来源 | 对外规则 |
| --- | --- |
| 派发、节点开始/完成等事实 | 使用固定业务阶段模板，可带允许的阶段名和完成数量 |
| manager 阶段总结、最终 summary、流程最终输出 | 按明确的对外输出字段与发布规则生成；普通内部群聊不直接转发 |
| 失败、取消异常、协议错误 | 稳定的业务原因和下一步提示；内部诊断用关联 ID 排查 |
| 成员身份、内部地址、系统提示、模型思考、凭据、工具原始返回、堆栈 | 保留在内部；确需作为业务结果的数据经已定义字段投影 |

约束通过字段和确定的模板实现。无法确认可公开的过程内容不产生对应进展；最终产物无法通过发布校验则失败收尾。

## 10. 错误合同

HTTP 状态与 `error.code` 一致，`error.status` 使用相应状态名，具体原因放在 `error.details[]` 的 `google.rpc.ErrorInfo` 中。标准 A2A 错误使用标准 reason 和 `a2a-protocol.org` domain；BCN 业务 reason 的 domain 在实现评审确定。

| 场景 | HTTP / status | 标准错误或 BCN reason |
| --- | --- | --- |
| Task 未知、跨群或不可访问 | 404 / NOT_FOUND | TaskNotFoundError |
| 向 working/终态 Task 追加消息，或新订阅终态 Task | 400 / FAILED_PRECONDITION | UnsupportedOperationError |
| 取消终态 Task | 400 / FAILED_PRECONDITION | TaskNotCancelableError |
| 输入/输出媒体类型不支持 | 400 / INVALID_ARGUMENT | ContentTypeNotSupportedError |
| 使用未支持的 Push Notification | 400 / FAILED_PRECONDITION | PushNotificationNotSupportedError |
| 协议版本不支持 | 400 / FAILED_PRECONDITION | VersionNotSupportedError |
| context 有活动占用 | 409 / ABORTED | CONTEXT_BUSY；附可见的 contextId / activeTaskId |
| context 暂停接受新一轮 | 400 / FAILED_PRECONDITION | CONTEXT_UNAVAILABLE；附原 taskId 和恢复提示 |
| 来源更换或补绑定冲突 | 400 / FAILED_PRECONDITION | CONTEXT_SOURCE_CONFLICT；来源合同待定 |
| context 未知、跨群、不可访问或非 A2A 创建 | 404 / NOT_FOUND | CONTEXT_NOT_FOUND |
| 相同 messageId 的请求指纹不同 | 400 / INVALID_ARGUMENT | MESSAGE_ID_CONFLICT |
| 原 Task 结果已实际清理 | 404 / NOT_FOUND | MESSAGE_RESULT_EXPIRED |
| 使用扩展字段但未激活 | 400 / INVALID_ARGUMENT | CALL_METADATA_NOT_ACTIVATED |
| 同步等待到达上限 | 504 / DEADLINE_EXCEEDED | SEND_WAIT_TIMEOUT |
| 参数格式或 Task/context 关联不合法 | 400 / INVALID_ARGUMENT | 参数校验错误 |
| 请求体超限 | 413 | 受理前拒绝 |

标准错误映射见 [规范 §5.4](https://a2a-protocol.org/v1.0.1/specification/#54-error-code-mappings)。504 等等待边界为 BCN 约定，不改变后台 Task 状态。

## 11. 模块分工与接口示例

### 11.1 模块边界

遵循 [BCS 分层约定](../../CLAUDE.md)：delivery adapter 只调用 `bcs_service_api::application`；application 编排 core 合同及业务 port；services 实现合同；store 实现 repo port 并访问数据库/存储插件。合同层不依赖具体 services 实现，装配由 composition root 完成。

| 模块 | 交付内容 |
| --- | --- |
| bcs-http | 标准 DTO、版本化路由、动作解析、媒体/扩展校验、错误映射、等待、SSE 与保活 |
| Group 调用应用服务 | 群能力与归属、新轮次、原子受理、去重、占用、执行期限、结果提交、查询与取消编排 |
| bcs-session | ServiceInvocation 复用、activation、重新激活、收尾及受管删除 |
| bcs-message-flow | 来源/data 注入、精确 task.complete、ack/退出顺序、一次提醒、整轮取消与公开结果 |
| Collaboration Runtime | 新 Run 的本轮输入和背景、最终输出、HumanInput 检查、取消确认和恢复 |
| Eventing / Event Store | 执行关联、公开投影、事件去重、消费位置和完成边界 |
| Store / repo port | Task、artifacts、预留、占用、执行关联、来源、期限、清理及恢复查询 |
| 上游接入方 | 读取 Card，保存返回 ID，处理 busy/等待超时，按 taskId 获取结果和取消 |

A2A DTO、HTTP/SSE 编码留在 adapter；跨模块合同表达执行、结果和产物语义。Artifact 表属于 BCS 协作数据，不把其持久化责任交给上游客户端。

### 11.2 新会话与产物读取

```http
POST /api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:send
Content-Type: application/a2a+json
A2A-Version: 1.0

{
  "message": {
    "messageId": "msg-001",
    "role": "ROLE_USER",
    "parts": [{"text": "比较两家供应商，预算为 80 万。"}]
  },
  "configuration": {"returnImmediately": true}
}
```

受理响应示例：

```json
{
  "task": {
    "id": "task-001",
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
    "status": {"state": "TASK_STATE_SUBMITTED"}
  }
}
```

完成后 GET 同一基地址下的 `/tasks/task-001`，从已提交记录读取：

```json
{
  "id": "task-001",
  "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
  "status": {"state": "TASK_STATE_COMPLETED"},
  "artifacts": [{
    "artifactId": "result-001",
    "name": "供应商比较结论",
    "parts": [{"text": "建议选择供应商 B，总价 72 万，交付周期 6 周。", "mediaType": "text/plain"}]
  }]
}
```

JSON 类型 Artifact 采用相同表和接口，例如：

```json
{
  "artifactId": "result-json-001",
  "name": "供应商比较数据",
  "parts": [{
    "data": {"recommendedSupplier": "B", "totalPrice": 720000, "currency": "CNY"},
    "mediaType": "application/json"
  }]
}
```

### 11.3 同 context 下一轮

上一轮结束且收尾完成后，使用新 messageId、原 contextId，省略 taskId：

```json
{
  "message": {
    "messageId": "msg-002",
    "contextId": "7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93:6d2f8a41",
    "role": "ROLE_USER",
    "parts": [{"text": "预算改为 50 万，重新比较并给出建议。"}]
  },
  "configuration": {"returnImmediately": true}
}
```

空闲时返回新 Task，旧 Artifact 保留；仍有占用时返回 CONTEXT_BUSY，未确认停止的隔离 context 返回 CONTEXT_UNAVAILABLE。

### 11.4 请求公开进展

以下使用候选扩展 URI，定稿后的 Card 和请求头保持一致：

```http
POST /api/v1/collaboration/a2a/groups/7f3b9c21-6a84-4d52-9e17-2c8f0a6b5d93/message:stream
Content-Type: application/a2a+json
A2A-Version: 1.0
A2A-Extensions: https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/call-metadata/v1.md

{
  "message": {
    "messageId": "msg-progress-001",
    "role": "ROLE_USER",
    "parts": [{"text": "比较供应商的报价和交付周期。"}]
  },
  "metadata": {"includeProgress": true}
}
```

流中先返回 Task，再给出公开进展；Artifact 提交后发送 artifactUpdate 和终态，关闭流。caller/user 示例在字段和凭据合同定稿后补齐。

## 12. 后续：自由聊天群与文件能力

### 12.1 自由聊天群

自由聊天群后续需要接入 A2A。如何确定结束、如何定义产物尚待讨论，初步考虑提供群级设置，在下列两种模式中选择：

| 候选方式 | 对外行为 | 尚需讨论 |
| --- | --- | --- |
| 返回聊天消息 | 标准 Message-only 响应；没有 Task，也没有 Artifact | 如何确定一次回复结束；多成员消息如何归并为一次响应；是否流式；context 如何续聊；没有 Task 时的控制方式 |
| LLM 总结为产物 | 创建 Task；按确定的结束条件收集群聊，由 LLM 总结为 Artifact 并持久化 | 谁触发结束；总结输入范围、模型和超时；失败处理；响应模式如何对上游说明 |

LLM 总结模式沿用 artifacts 表和公开内容规则，总结失败按 Task 失败处理。Message-only 模式直接返回标准 Message。

这两种模式的群设置字段、响应模式声明方式及结束合同在后续讨论确定，再据此开放自由聊天群入口。

### 12.2 文件及其他非文本内容

本期 Artifact 数据模型保留 text/data/raw/url 的表达。开放文件时，继续使用标准 Part 和 Card 媒体类型声明，另行确定上传/引用、对象存储、下载、大小和数量限制、允许的媒体类型、外部 URL 处理、执行节点绑定及保留期限。

Group 输入仍按本期范围拒绝文件。文件接入与 Task 等待补充输入分别评审，不把它们隐含到本期 Artifact 表建设中。

## 13. 已确定项与待定项

### 13.1 已确定的设计方向

| 事项 | 结论 |
| --- | --- |
| Group/context/Task | Group 是 Agent，Session 是 context，本期每轮独立执行一个 Task |
| 本期群类型 | 主从群、自定义协作群；自由聊天群留待后续 |
| 同 context 并发 | 忙碌拒绝，不排队；占用覆盖受理到停止确认 |
| Artifact | 独立表持久化，Part 支持非文本表达；本期公开文本和 JSON |
| 来源与凭据的定稿状态 | caller/user 命名、结构和凭据传递方式列为待定，不作为已冻结合同 |
| 扩展发布位置 | 采用 GitHub 仓库中的版本化规范与 Schema 文件地址 |
| 自然语言建群 | 暂不考虑，移出本方案规划 |

### 13.2 待确认的合同与参数

| 事项 | 当前建议或待确认内容 | 涉及模块 |
| --- | --- | --- |
| 入站路径与调用方兼容 | 使用版本化 HTTP+JSON 路径；确认主要接入方支持此绑定 | bcs-http、上游 |
| 主从群完成动作 | 复用精确绑定的 task.complete + summary；确认 manager Skill/上下文的动作要求 | MessageFlow、Session |
| 来源字段 | caller/user 仅为候选名称；最终键名、结构、必填性与跨轮绑定规则待定 | 协议、身份、执行上下文 |
| 凭据和认证信息传递 | metadata、HTTP 头或其他载体；认证链、主体映射、Card 安全声明；对请求持久化和指纹的影响 | 认证相关模块、bcs-http、调用方 |
| 扩展合同 | 使用仓库地址已定；扩展名、规范/Schema 文件名、Schema 获取方式及最终字段待定 | 协议维护、客户端 |
| 同步等待 | 建议 60 秒，确认默认阻塞调用完成率、504 说明与网关余量 | bcs-http、部署 |
| 执行与收尾期限 | 建议执行 30 分钟、收尾 60 秒 | 调用服务、Runtime、MessageFlow |
| manager 完成提醒 | 建议无待执行工作空闲 60 秒后提醒一次，再给 120 秒宽限 | MessageFlow |
| Artifact 物理设计 | 逻辑字段见 §8；物理表名、JSON/Part 存储布局、对象引用格式、批量读写和对象清理 | Store、存储插件 |
| 结果与墓碑 | 建议结果 7 天；墓碑随 Group 调用存储保留；确认容量和清理策略 | Store、部署 |
| 过期后续聊背景 | 上轮产物已清理时，如何要求调用方补齐背景或另建 context | 调用服务、上游 |
| 请求大小 | 建议 JSON 1 MiB；网关与适配层一致 | bcs-http、部署 |
| 去重扩展 | 本期不发布 message-dedup/v1；需要自动重试时确认 Group 范围和重启行为 | 协议、A2A 出站 |
| BCN 错误 domain | ErrorInfo 的稳定 domain 取值 | 协议 |
| 异常会话恢复 | 自动核实和恢复条件、失败后的排查/人工入口；告警接收人部署前确认 | 调用服务、执行、部署 |
| 自由聊天群 | Message-only / LLM 总结模式、结束触发、产物定义及响应模式声明 | 后续方案 |

## 14. 交付顺序与验收

先确定外部合同和待定身份字段，再打通受理、两类群执行及 artifacts 表；最后完成事件投影、取消、期限、隔离和恢复。合同与实现测试同步交付。

| 验收场景 | 预期结果 |
| --- | --- |
| 只用标准字段完成两轮 | 同 context、两个 Task，各轮结果独立可查询 |
| 非阻塞 send + GetTask | 立即获得 ID，轮询读取同一已提交快照和最终 Artifact |
| 默认阻塞 send、等待超时和断线 | 终态成功返回或约定 504；不成功返回 working；后台执行继续 |
| 相同请求并发与重启重试 | 最多一轮执行、一份 ID 预留，原 Task 可恢复 |
| 相同 messageId 改 configuration 或 metadata | MESSAGE_ID_CONFLICT，不再次派发 |
| 两个新请求争用 context | 最多一个受理，其余 CONTEXT_BUSY 且不创建 Task |
| 主从群 task.complete 在 manager 退出前到达 | 先 ack，再等退出和收尾，最终提交后才开放下一轮 |
| 完成信号携带旧 activation | 拒绝旧信号，不能完成当前轮次 |
| manager 空闲不完成、正常长任务无消息 | 前者只提醒一次并按宽限收尾；后者不因消息静默失败 |
| 状态机下一轮修改预算 | 节点拿到最初目标、上轮结果和本轮请求，新 Run 重新执行 |
| 主从群和状态机在执行中取消 | 全部停止确认后 canceled；确认前保持占用 |
| 失联、HumanInput、收尾超时、进程重启 | 预算不重置；必要时 FAILED 并隔离，确认停止后才恢复 |
| Artifact 事务写入失败 | 不发布成功终态或最终产物事件，不出现部分成功结果 |
| 一个 Task 多产物、文本和 JSON | ID 与顺序稳定，数据类型保留，重启前后 GetTask/SSE 一致 |
| 非文本存储表达 | 可保存 raw/url 的引用和属性；本期 Group 发布校验不开放未声明类型 |
| 结果清理与对象回收失败 | 去重墓碑保留；已清理结果不重执行；托管对象回收可重试 |
| 事件重复消费、终态与迟到回调竞争 | 历史不重复，公开过程在最终产物之前，终态后不追加 |
| 终态后 subscribe / cancel | 分别返回 UnsupportedOperationError / TaskNotCancelableError |
| SSE 无业务事件经过网关 | 心跳及时送达，不延长任务期限，断线可按 ID 恢复查询 |
| 内部事件携带身份、工具输出或堆栈 | 所有公开出口使用同一内容投影 |
| 跨调用方、跨群、非 A2A Session 操作 | 归属校验生效；列表过滤在分页之前 |
| 扩展字段缺激活头 | 受理前拒绝；补头可重发；不创建错误绑定的 context |
| acceptedOutputModes 无交集、文件输入、请求体超限 | 受理前按约定拒绝 |
| 路由动作解析 | 标准 cancel/subscribe URL 可访问；错误 ID/后缀得到约定错误 |
| 来源字段定稿后跨轮继承与冲突 | 根据最终 Schema 和绑定合同增加一致性用例 |

## 15. 实现依据

- [方案评审文档](design-review.html)：本次范围、Artifact 表、来源待定项与后续自由聊天方向的来源。
- [HTTP router](../../crates/adapters/http/bcs-http/src/router.rs)、[A2A 管理接口](../../crates/adapters/http/bcs-http/src/routes/a2a_management.rs)：已有路径和管理职责。
- [Group 服务调用入口](../../crates/adapters/http/bcs-http/src/routes/services.rs)、[Session 模型](../../crates/contracts/bcs-domain/src/session.rs)：服务调用与执行轮次。
- [task.complete](../../crates/services/bcs-message-flow/src/task_flow.rs)、[summary 保存](../../crates/services/bcs-message-flow/src/task_flow/support.rs)：主从群显式完成能力。
- [状态机启动](../../crates/services/bcs-collaboration-runtime/src/runtime/start.rs)、[完成](../../crates/services/bcs-collaboration-runtime/src/runtime/completion.rs)、[失败 rerun](../../crates/services/bcs-collaboration-runtime/src/runtime/rerun.rs)：Run 生命周期。
- [Group Card 存储](../../crates/services/bcs-group-store/src/card_template.rs)、[事件目录](../../api-contracts/events/v1/catalog.yaml)：可复用的持久化与事件基础。
- [BCS 分层约定](../../CLAUDE.md)、[架构规则](../../../../docs/arch/arch.rules.md)、[CI 约束](../../../../docs/arch/ci.enforce.md)：模块合同和实现边界。
