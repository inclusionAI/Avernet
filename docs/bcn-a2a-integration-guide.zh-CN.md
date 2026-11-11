# BCN A2A 接入指南

更新日期：2026-11-03。

如果你已有一个 Agent，希望用户能在 BCN 中找到它、与它对话，可以通过 A2A 接入。你负责提供 Agent 服务，BCN 负责注册、访问控制和消息转发。

本文说明 Agent 服务准备、Bot 注册和 AgentCard 管理。Provider 管理系统的正式请求、响应及错误码约定见 [Provider AgentCard 管理接口 v0.1](bcn-provider-agent-card-api-v0.1.zh-CN.md)。

本文提供接入步骤和常用示例。完整字段及协议消息定义见 [A2A 接口参考](bcn-a2a-api-reference.zh-CN.md)。示例域名、用户标识和令牌均需替换为实际值。

## 1. 先确认你的服务能否接入

你可以使用自己的 A2A Server，也可以使用适配 SDK 将现有 Agent 包装成 A2A 服务。

```text
用户 / 业务系统 → BCN ── A2A 协议 ──→ 你的 Agent
```

BCN 与 Agent 使用 A2A 通信，用户仍然通过 BCN 工作台或 BCN API 发起调用。注册后的 Agent 在 BCN 中称为一个 Bot。

| 接入能力 | 使用方式 |
| --- | --- |
| HTTP+JSON | 使用 HTTP 接口，流式输出使用 SSE |
| gRPC | 普通操作使用 Unary，流式输出使用 Server streaming；接入时与 BCN 平台确认 MOSN 路由及服务地址 |
| JSON-RPC | 在 Card 中声明 A2A 1.0 的 JSON-RPC 接口，流式输出使用 SSE |
| Agent 业务属性 | 通过 Card 中的 Data-only Extension 描述分类、标签等信息 |
| 用户调用上下文 | 通过请求 metadata 传递 userId 等字段，并验证身份来源 |

BCN 使用的 A2A 方法、请求格式及流式处理要求见第 5 节。

普通对话支持文本输入，以及流式或非流式文本回复。建议首次联调先跑通文本问答，再接入工具展示和审批。

## 2. 准备接入信息

向 BCN 平台申请一个 Provider。可以把 Provider 理解为“你的服务提供方账号”，一个 Provider 下可以注册多个 Bot。

你会用到以下信息：

| 信息 | 用在哪里 |
| --- | --- |
| BCN API 地址 | 注册、管理和调用 Bot |
| `provider_name` | Provider 标识，例如 `order-service`；由响应返回，管理请求无需填写 |
| `provider_admin_token` | 注册 Agent、更新 Card；由管理程序保管 |
| 所有者 ID | 指定谁在 BCN 中拥有和管理这个 Bot |

`provider_admin_token` 用于注册和管理 BCN 中的 Bot，不会转发给 Agent。本期 BCN → Agent 不携带应用层鉴权凭据，接入服务需允许这种调用方式；声明必须鉴权的 Agent 暂不支持接入。

本文使用 Provider 管理接口完成注册，不需要另外申请注册链接或 scoped 注册 token。

`provider_name` 使用小写英文字母、数字、连字符或下划线，以字母开头，长度 1～64，在部署环境内唯一，注册后保持稳定。`agent_name` 直接取 AgentCard.name，支持中文，长度 2～64 个字符，不含首尾空白或控制字符，保留大小写、允许同名，无需单独填写；实际 Agent 调用地址由 AgentCard 提供。

### 准备 AgentCard

AgentCard 是 A2A 协议定义的 Agent 描述对象，包含名称、能力、接口地址、协议版本和鉴权要求。

推荐在注册请求中直接提交 Card JSON。若选择 URL 注册，则在可被 BCN 访问的 HTTP 地址返回 Card，例如：

```text
https://agent.example.org/.well-known/agent-card.json
```

也可以使用自定义地址，例如 `/a2a/order-agent/agent-card.json`。注册时填写完整 URL；BCN 当前不会根据域名自动寻找 Card。

以下是一个 HTTP Agent 的完整示例：

```json
{
  "name": "订单助手",
  "description": "查询订单状态并回答订单相关问题",
  "version": "1.0.0",
  "supportedInterfaces": [
    {
      "url": "https://agent.example.org/a2a/order-agent",
      "protocolBinding": "HTTP+JSON",
      "protocolVersion": "1.0"
    }
  ],
  "capabilities": {"streaming": true},
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "skills": [
    {
      "id": "query_order",
      "name": "查询订单",
      "description": "根据订单号查询订单状态",
      "tags": ["orders"]
    }
  ],
  "securityRequirements": []
}
```

填写时重点关注：

| 字段 | 怎么填 |
| --- | --- |
| `name`、`description` | 用户能看懂的名称和能力介绍 |
| `version` | 你自己的 Agent 版本，例如 1.0.0 |
| `supportedInterfaces[].url` | 实际调用地址，和 Card JSON 的地址不同 |
| `protocolBinding` | 按服务选择 HTTP+JSON、GRPC 或 JSONRPC |
| `protocolVersion` | 当前填写 1.0；与 Agent 软件版本无关 |
| `capabilities.streaming` | true 使用流式输出；false 或省略时，通过普通发送和任务查询获取文本结果 |
| `defaultInputModes`、`defaultOutputModes` | 当前均需包含 text/plain |
| `skills` | 技能名称、说明和标签；BCN 会保留完整技能描述 |
| `securityRequirements` | 本期填写 []，表示不要求应用层鉴权；无需填写 securitySchemes |

Bearer、OAuth 等 Agent 调用鉴权方式后续按需接入；BCN 不会忽略 Card 中的必需鉴权要求。

业务分类、标签等描述数据放在 `capabilities.extensions[].params`，以扩展 URI 标识，并设置 `required:false`。BCN 保留完整可选扩展；需要展示、搜索或筛选的字段由双方约定，不会自动将任意扩展字段变为筛选条件。

Card 地址和 A2A 调用接口均需允许 BCN 不携带应用层凭据访问；服务地址仍需符合平台网络访问策略。

如果一个服务部署了多个 Agent，请给每个 Agent 配置可区分的接口路径，或使用双方约定的 tenant 路由。不要只靠共享凭据判断请求发给哪个 Agent。

### 平台身份与来源分类

注册外层必填 provider_bot_ref（同一 Provider 唯一），不要放进 Card。Card.name 只用于展示，允许重复。BCS 返回 bot_id 与 provider_bot_ref；之后按 bot_id 管理，按 provider_bot_ref 找回或使用 Provider/ref 聊天入口。

分类继续使用 capabilities.extensions 中的可选数据扩展，见 [source-agent/v2](specs/a2a/extensions/source-agent/v2.md)：

```json
{
  "uri": "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/source-agent/v2.md",
  "required": false,
  "params": {"party": "first_party"}
}
```

party 可省略，支持 first_party、second_party、third_party；不表示权限或信任等级，可修改或删除。通用 agentId 和旧 v1 扩展不再接受。

## 3. 注册 Agent

调用以下管理接口：

```http
POST /a2a/agents
Authorization: Bearer <provider_admin_token>
Content-Type: application/json
```

推荐直接提交完整 AgentCard，无需另外部署 Card HTTP 端点。各字段要求见 [AgentCard 字段要求](bcn-provider-agent-card-api-v0.1.zh-CN.md#22-agentcard-字段要求)。

请求示例：

```json
{
  "provider_bot_ref": "order-agent-001",
  "owners": [
    "owner-user-id"
  ],
  "agent_card": {
    "name": "订单助手",
    "description": "查询订单状态",
    "version": "1.0.0",
    "supportedInterfaces": [
      {
        "url": "https://agent.example.org/a2a/orders",
        "protocolBinding": "HTTP+JSON",
        "protocolVersion": "1.0"
      }
    ],
    "capabilities": {
      "streaming": true
    },
    "defaultInputModes": [
      "text/plain"
    ],
    "defaultOutputModes": [
      "text/plain"
    ],
    "skills": [
      {
        "id": "query_order",
        "name": "查询订单",
        "description": "根据订单号查询状态",
        "tags": [
          "orders"
        ]
      }
    ],
    "securityRequirements": []
  }
}
```

| 字段 | 是否必填 | 说明 |
| --- | --- | --- |
| `provider_bot_ref` | 是 | Provider 内稳定唯一键，不从 Card 推导 |
| `owners` | 是 | 当前填写一个有效的 BCN 所有者 ID |
| `agent_card_url` | 与 agent_card 二选一 | BCN 从此地址读取 Card |
| `agent_card` | 与 agent_card_url 二选一 | 直接提交完整 Card 对象，适合不单独发布 Card URL 的场景 |
| `summary` | 否 | BCN 展示摘要；省略时初始值取 Card.description |

BCN 会检查 Card 和接口声明，并从 Card.name 获取 Bot 名称；注册成功后返回 **`provider_name`、`agent_name` 和 `bot_id`**。Provider 管理 token 唯一绑定一个 Provider，BCN 从有效 token 确定注册归属；注册请求不传 provider_name。接入方必须保存 bot_id，后续查询、更新、刷新使用该 ID；不按名称定位。丢失 ID 可用 provider_bot_ref 查询 `/a2a/agents?provider_bot_ref=...`。

重启 Agent 服务时复用已有 Bot，不要重复注册。同一 Provider 下重复 provider_bot_ref 返回冲突，不隐式覆盖；Card.name 可以重复。修改服务信息使用第 4 节的更新接口。

## 4. 更新 Agent 信息

Agent 升级或能力介绍变化后，可以更新 BCN 登记的 Card。这些是 BCN 管理接口，使用 Provider 管理令牌。

| 需要做什么 | 接口 |
| --- | --- |
| 查看已登记的 Card | GET /a2a/agents/{bot_id}/agent-card，ID 放在路径中 |
| 提交新的 Card URL 或完整 Card | PUT /a2a/agents/{bot_id}/agent-card，body 放 Card 来源和/或 visibility，不重复填写 ID |
| 从已保存的 URL 立即重新读取 Card | POST /a2a/agents/{bot_id}/agent-card:refresh，空 body 或 {} |
| 按平台键找回 ID | GET /a2a/agents?provider_bot_ref=...，返回 items（0 或 1 项），限定当前 Provider |

查询示例（bot_id 使用注册返回值）：

```http
GET /a2a/agents/{bot_id}/agent-card
Authorization: Bearer <provider_admin_token>
```

查询、更新和刷新均从 Provider 管理 token 确定归属，使用路径中的 `bot_id` 选择该 Provider 下的 Bot，不传 `provider_name`。这些接口只管理 token 所属 Provider；跨 Provider 的发现、搜索和业务调用仍按各自的目标选择及授权规则执行。

如果使用 URL 登记，只修改原地址的内容后，可以调用刷新接口；BCN 也会周期性重新读取。需要发布后立即生效时，请主动刷新。

如果要更换 URL，直接通过 PUT 提交：

```json
{
  "visibility": "protected",
  "agent_card_url": "https://agent.example.org/a2a/order-agent/agent-card.json"
}
```

也可以在同一 PUT 接口中直接提交完整 Card。以下为匿名服务的请求示例；实际提交时保留所需的技能和扩展，并符合本期无应用层鉴权的接入要求：

```json
{
  "visibility": "protected",
  "agent_card": {
    "name": "订单助手",
    "description": "查询订单状态",
    "version": "1.0.0",
    "supportedInterfaces": [
      {
        "url": "https://agent.example.org/a2a/order-agent",
        "protocolBinding": "HTTP+JSON",
        "protocolVersion": "1.0"
      }
    ],
    "capabilities": {"streaming": true},
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
    "skills": [],
    "securityRequirements": []
  }
}
```

从已登记 URL 刷新时，向 `POST /a2a/agents/{bot_id}/agent-card:refresh` 提交：

```json
{}
```

也可以省略请求体；不传 agent_name 或 bot_id。

`agent_card_url` 和 `agent_card` 每次只能选一个。并发提交更新时，以最后成功写入的内容为准。

直接提交 Card 是完整替换，不是部分字段修改。切换成内联 Card 后，旧 URL 会被清除，也不再自动刷新。这里更新的是 BCN 保存的信息，不会修改 Agent 服务端的文件。

更新或刷新 Card 时，BCN 同步将 Bot 名称更新为 Card.name。新名称可以与同一 Provider 下其他 Bot 重复。改名成功后，后续管理请求仍使用原 bot_id；旧名称可由其他 Bot 使用；软删除后名称同样释放。AgentCard 名称不承担唯一定位；平台身份 provider_bot_ref 不变。已有任务仍通过内部关联查询和取消，不会切换到新地址。

## 5. BCN 支持的 A2A 方法

以下方法由 **BCN 作为 A2A Client 调用你的 Agent Server**，服务地址来自 AgentCard。它们不是 BCN 对外提供的管理接口。

| 方法 | HTTP+JSON 接口 | BCN 在什么时候调用 | 返回内容 |
| --- | --- | --- | --- |
| SendStreamingMessage | POST /message:stream | 向支持流式的 Agent 发起对话 | 持续返回 Task、文本及状态事件 |
| SendMessage | POST /message:send | 向非流式 Agent 发起对话，或向原 Task 提交工具审批结果；使用 returnImmediately:true | 接受请求后的 Task，或直接返回 Message |
| GetTask | GET /tasks/{id} | 非流式任务轮询、断流恢复及最终结果校准 | 当前 Task，包含状态和完整 Artifacts |
| SubscribeToTask | POST /tasks/{id}:subscribe | 断流后继续跟踪未结束的任务，或审批续交后恢复跟踪 | 当前 Task 及后续流式事件 |
| CancelTask | POST /tasks/{id}:cancel | 用户取消当前运行 | Task 及其取消处理后的状态 |

表中的 `{id}` 是 Agent 返回的 Task ID，不是 BCN run_id。BCN 保存两者的关联。当前不使用 ListTasks、Push Notification 或动态认证恢复；Agent 不需要提供向 BCN 回调的地址。

支持流式时，BCN 使用 SendStreamingMessage；否则使用 SendMessage，并在需要时通过 GetTask 跟踪。返回 Message 时直接完成，返回运行中的 Task 时按秒轮询，终态后停止。重复快照不会重复展示文本，已展示的文本暂不支持替换。

非流式接入先支持文本问答、长任务查询和取消，不支持工具过程展示、审批或 ask_user。任务进入 INPUT_REQUIRED 或 AUTH_REQUIRED 时，BCN 会报告不支持该等待流程，并尝试取消对应任务。未返回 Task ID 的 Message 回复不提供远端任务查询和取消。

### 不同协议怎么调用

- **HTTP+JSON**：使用上表路径，普通响应为 JSON，流式响应为 SSE。发送消息的请求体为 SendMessageRequest；取消和订阅的请求体包含 `id`，与路径一致。GetTask 的可选 `historyLength` 放在查询参数中。
- **JSON-RPC**：向 Card 声明的 JSON-RPC URL 发送 POST；`method` 为表中的方法名，`params` 为对应请求对象，例如查询任务使用 `{"id":"task-001"}`。普通响应通过 `result` 返回，流式响应通过 SSE 逐条返回 JSON-RPC 响应。
- **gRPC**：调用 `lf.a2a.v1.A2AService` 下的同名方法，使用标准 protobuf 请求。SendMessage、GetTask、CancelTask 为 Unary；SendStreamingMessage、SubscribeToTask 为 Server streaming，不使用客户端流或双向流。

HTTP 请求使用 A2A-Version: 1.0，JSON 请求使用 Content-Type: application/json，流式请求接受 text/event-stream。本期不附带 Agent 调用鉴权凭据；gRPC 使用对应版本和扩展 metadata。声明了 tenant 时，BCN 同时传递租户信息。扩展通过 A2A-Extensions 协商，不能仅靠发送自定义数据启用必需扩展。

### 发送消息

SendStreamingMessage 和 SendMessage 使用相同的消息结构：

```json
{
  "message": {
    "messageId": "message-001",
    "role": "ROLE_USER",
    "parts": [{"text": "查询订单 ORD-001"}]
  },
  "configuration": {
    "acceptedOutputModes": ["text/plain"]
  }
}
```

| 字段 | 用法 |
| --- | --- |
| message.messageId | 本条消息的标识；不能仅凭此字段假设远端支持幂等重试 |
| message.contextId | 关联多轮会话；首轮由 Agent 建立，后续由 BCN 复用 |
| message.taskId | 续交已有任务时填写，例如工具审批回复；普通新一轮对话不自动复用旧 Task |
| message.role / parts | 用户输入使用 ROLE_USER，文本放在 parts[].text；role 不代表已验证的用户身份 |
| configuration.returnImmediately | SendMessage 设置 true 时，接受任务后立即返回，后续跟踪 Task；非流式 Agent 只查询，不订阅 |
| metadata | 通过双方约定的扩展 URI 传递 userId 等业务上下文；BCN 从可信来源取值，Agent 验证后使用 |

JSON-RPC 的流式发送示例：

```json
{
  "jsonrpc": "2.0",
  "id": "request-001",
  "method": "SendStreamingMessage",
  "params": {
    "message": {
      "messageId": "message-001",
      "role": "ROLE_USER",
      "parts": [{"text": "查询订单 ORD-001"}]
    }
  }
}
```

这里使用 A2A 1.0 的方法和字段，不自动兼容旧版 0.3 的 message/stream 等格式。调用方通过 BCN 异步提交请求时，BCN 仍可使用 SendStreamingMessage 跟踪任务；异步提交不等于回调模式。

### 流式输出与恢复要求

HTTP+JSON 的 SSE 示例（gRPC 使用对应 StreamResponse，JSON-RPC 则增加响应包装）：

```text
data: {"task":{"id":"task-001","contextId":"context-001","status":{"state":"TASK_STATE_WORKING"}}}

data: {"artifactUpdate":{"taskId":"task-001","contextId":"context-001","artifact":{"artifactId":"answer","parts":[{"text":"订单 ORD-001"}]},"append":false,"lastChunk":false}}

data: {"artifactUpdate":{"taskId":"task-001","contextId":"context-001","artifact":{"artifactId":"answer","parts":[{"text":" 已发货。"}]},"append":true,"lastChunk":true}}

data: {"statusUpdate":{"taskId":"task-001","contextId":"context-001","status":{"state":"TASK_STATE_COMPLETED"}}}

```

接入时需要注意以下行为：

- **任务与消息**：任务型响应先返回 Task，后续事件使用一致的 taskId/contextId。简单问答也可直接返回 Message，但没有 Task 就无法查询、重新订阅或精确取消。
- **文本追加**：同一 artifactId 首次使用 append:false 建立文本，后续使用 append:true 追加。BCN 不支持中途改写已输出文本；最终完整快照必须保留已输出的前缀。lastChunk 只表示该 Artifact 结束，Task 仍需明确返回终态。
- **查询与订阅**：GetTask 返回快照，SubscribeToTask 返回持续更新。GetTask 应保存完整文本，而不是最后一小段增量；它不保证包含全部工具过程事件。
- **断流恢复**：拿到 Task 后断流，BCN 先 GetTask；任务已结束则收尾，否则重新订阅。恢复受连续失败次数和运行超时限制，不保证无限重连。未拿到 Task 时，只有协商了幂等扩展才能安全重试初始提交。
- **取消**：CancelTask 请求停止执行，不是业务事务回滚；已完成的工具副作用不会自动撤销。远端不支持取消或取消失败时，不应视为任务已经停止。
- **等待审批**：支持的 exec 审批通过 INPUT_REQUIRED 和 Structured Input 表达。BCN 等待用户回答时关闭订阅，回复通过 SendMessage 续交原 Task，再查询或订阅；详见下一节。

## 6. 如果你的 Agent 有工具调用或人工审批

完成文本接入后，可以按需要增加两类能力：

- **展示工具执行过程**：使用 BCN 工具观察扩展，发送工具调用 ID、名称、参数和结果。
- **执行前请求批准**：使用 Structured Input 的 BCN exec 审批约定。Agent 暂停任务，用户允许或拒绝后，BCN 向原 Task 发送回复。

这两项需要在 Card 中声明并协商，不能把任意工具 JSON 当作可展示的工具事件。当前审批支持 exec 场景，通用 ask_user 尚未支持。等待审批期间，同一 Bot/Session 后续消息会排队。

完整示例见 [接口参考中的工具与审批说明](bcn-a2a-api-reference.zh-CN.md)。

## 7. 上线前确认这几件事

1. Card 可以访问，声明的调用地址和实际服务一致；Card 和 A2A 接口允许 BCN 无应用层凭据访问，且符合平台网络访问策略。
2. 从 BCN 发起一次文本问答，能看到连续输出和明确的完成结果。
3. GetTask 能读到完整结果，取消能作用于真实任务；重新订阅不会重复执行业务。
4. 更新 Card 后能刷新成功；Agent 重启时复用原来的 Bot 注册信息。
5. 涉及个人数据、审批或业务副作用时，完成对应身份、授权和异常场景验证。

遇到问题时，向平台提供 provider_name、agent_name、BCN run_id、发生时间及脱敏后的错误信息即可，不要发送令牌。

| 现象 | 优先检查 |
| --- | --- |
| 注册失败 | Card URL、协议版本、文本能力、必需扩展和 Provider 管理凭据 |
| 重复注册冲突 | 是否已经存在同一 agent_name；已有 Bot 应更新而非重建 |
| 能注册但调用失败 | Agent 接口地址、是否额外要求鉴权、平台网络或 gRPC 路由 |
| 输出后一直不结束 | Agent 是否发出了 Task 终态；关闭流不等于完成 |
| 断线后缺少文本 | GetTask 是否保存完整 Artifact，而非只有最新增量 |
| 更新 Card 失败 | 检查 Card 内容、来源 URL 和 Provider 管理凭据 |

如果提交后立即断线，BCN 可能无法确定远端是否已执行，并阻塞该会话的后续调用。尤其涉及有副作用的工具时，应先核实远端结果，不要直接重新发送。当前也不承诺服务重启后自动恢复所有运行中的任务。
