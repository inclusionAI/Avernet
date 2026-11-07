# BCN Provider AgentCard 管理接口 v0.1

## 版本记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| 0.1 | 2026-11-03 | 初始对接合同：Provider token 定位归属、注册、AgentCard 查询/更新/刷新、统一错误定义 |

状态：对接评审稿

用于 Provider 向 BCN 注册 Agent，以及查询、更新和刷新 AgentCard。

## 1. 接口与认证

| 操作 | 方法与路径 | 成功状态 |
| --- | --- | --- |
| 注册 | POST /a2a/agents | 201 Created |
| 查询 | GET /a2a/agent-card | 200 OK |
| 更新 AgentCard | PUT /a2a/agent-card | 200 OK |
| 刷新 Card | POST /a2a/agent-card/refresh | 200 OK |

### 1.1 请求头

| 请求头 | 必填 | 定义 |
| --- | --- | --- |
| Authorization | 是 | `Bearer <provider_admin_token>`；唯一绑定一个 Provider 的管理凭据 |
| Content-Type | 有 JSON 请求体时是 | `application/json`，编码 UTF-8 |
| Accept | 否 | 省略或 `application/json` |

管理 token 由 BCN 平台发放，唯一绑定一个 Provider。请求无需填写 provider_name，查询和更新仅作用于该 Provider 下的 Agent。平台须先启用 Provider 并配置英文名称；当前注册入口使用 StaticBearer 身份模式的 Provider，其管理凭据与远端 Agent 的匿名调用要求相互独立。

### 1.2 数据格式

- 请求和响应使用 JSON；接口字段使用 snake_case，AgentCard 保持 A2A 标准字段名。
- 请求体不超过 2 MiB，AgentCard 不超过 1 MiB。

## 2. 公共数据定义

### 2.1 名称、归属与来源

| 字段 | 类型 | 定义 |
| --- | --- | --- |
| provider_name | string | 仅作为响应中的 BCN Provider 标识；`^[a-z][a-z0-9_-]{0,63}$`，环境内唯一 |
| bot_id | string | BCN 分配的稳定 Bot 标识，四个管理接口均返回；管理请求仍使用 agent_name |
| agent_name | string | 等于已保存 Card.name；2～64 个 Unicode 码点，支持中文、区分大小写，无首尾空白或控制字符 |
| agent_card_url | string 或响应中的 null | Card 的绝对 HTTP(S) URL；不是 Agent 调用接口地址；最长 2048 个字符 |
| agent_card | object | 完整 A2A 1.0 系列 AgentCard |
| owners | string[] | 注册必填且恰好一个元素；不带 human_ 前缀的所有者 ID；Provider 管理者负责归属授权，BCN 可为该 ID 建立 Human 记录 |
| summary | string | 注册可选展示摘要，最长 4096 个 Unicode 码点；省略时取 Card.description，空字符串表示空摘要 |

Agent 名称在同一 Provider 下唯一，按原文精确匹配。

agent_card 与 agent_card_url 二选一。URL 方式要求地址在 10 秒内直接返回 200 和 Card JSON，不支持重定向或鉴权；URL 不含用户名、密码或片段，且须符合平台网络策略。

### 2.2 AgentCard 字段要求

**推荐直接提交完整 agent_card**。以下按 [A2A 1.0.1](https://a2a-protocol.org/v1.0.1/specification/#441-agentcard) 列出 BCN 接入要求；可选字段不用时省略。

#### 2.2.1 顶层字段

| 字段 | 类型 | BCN 必填 | BCN 要求及用途 |
| --- | --- | --- | --- |
| name | string | 是 | 2～64 个 Unicode 码点，无首尾空白或控制字符；支持中文、区分大小写，在同一 Provider 下唯一；作为 agent_name，更新时同步改名 |
| description | string | 是 | 非空能力介绍；注册未传 summary 时用作摘要，届时须满足摘要 4096 字符限制；完整描述始终保留在 Card 中 |
| version | string | 是 | 非空 Agent 软件版本，例如 1.0.0 |
| supportedInterfaces | object[] | 是 | 至少一项；按偏好排序，BCN 选择首个支持且满足地址策略的接口；各项规则见下表 |
| capabilities | object | 是 | 无可选声明时可为 {}；能力选择规则见 2.2.3 |
| defaultInputModes | string[] | 是 | 必须包含 text/plain；其他声明保留，不代表 BCN 支持对应输入类型 |
| defaultOutputModes | string[] | 是 | 必须包含 text/plain；本期 BCN 主链路仅消费文本结果 |
| skills | object[] | 是 | 可为 []；技能字段见 2.2.4，完整保留描述 |
| provider | object | 否 | 本期无需填写 |
| documentationUrl | string | 否 | 绝对 HTTP(S) 文档地址，建议 HTTPS；保留返回，注册时不抓取文档 |
| iconUrl | string | 否 | 绝对 HTTP(S) 图标地址；保留返回，不自动抓取 |
| securitySchemes | object | 否 | 本期建议省略；见 2.2.5 |
| securityRequirements | object[] | 否 | 推荐 []；省略按无需应用层认证处理。若声明，只能接入有匿名可满足路径的 Agent；见 2.2.5 |
| signatures | object[] | 否 | 本期无需填写，不提供签名验真 |

Card 不接受用旧版顶层 url 或 protocolVersion 代替 supportedInterfaces。来源平台 agentId 放入约定扩展，不是 Card 顶层标准字段。

#### 2.2.2 supportedInterfaces 每项

| 字段 | 类型 | BCN 必填 | BCN 要求及用途 |
| --- | --- | --- | --- |
| url | string | 是 | 实际 A2A 服务入口，绝对 URL；生产使用 HTTPS。gRPC 通过平台配置路由；地址须满足出站策略，不填写 Card 下载地址 |
| protocolBinding | string | 是 | BCN 可选值为 HTTP+JSON、GRPC、JSONRPC，大小写必须一致；其他绑定不参与选择，不得成为唯一可用接口 |
| protocolVersion | string | 是 | BCN 选用接口须为 1.0；旧版 0.3 不自动兼容 |
| tenant | string | 否 | 非空租户路由值；BCN 按原值传递 |

至少一项接口须满足协议及网络要求。注册校验不执行 Agent 业务调用。

#### 2.2.3 capabilities 及 extensions

| 字段 | 类型 | BCN 必填 | BCN 要求及用途 |
| --- | --- | --- | --- |
| streaming | boolean | 否 | true 使用流式发送；false 或省略使用非流式发送及查询，后者仅承诺文本问答、长任务查询和取消 |
| pushNotifications | boolean | 否 | 建议 false 或省略；true 可保留，但 BCN 不配置回调或使用 Push 跟踪 |
| extendedAgentCard | boolean | 否 | 建议 false 或省略；BCN 不获取认证后的扩展 Card，登记的这份 Card 必须足以完成本期接入 |
| extensions | object[] | 否 | 省略或 [] 表示未声明扩展；每项按下表填写 |

| extensions 每项字段 | 类型 | BCN 必填 | BCN 要求及用途 |
| --- | --- | --- | --- |
| uri | string | 是 | 非空绝对 URI，标识扩展及版本 |
| description | string | 否 | 扩展用途说明，保留返回 |
| required | boolean | 否 | 省略等价 false；true 且 BCN 不支持时拒绝接入；纯数据扩展填写 false |
| params | object | 否 | 扩展数据；已支持扩展按规范校验，未知可选扩展保留 |

**BCN 来源信息扩展**

BCN 通过一个来源信息扩展保存 `agentId` 和 `party`。这两个字段由 BCN 定义，不是 A2A 标准字段；它们放在同一条 `capabilities.extensions` 的 `params` 中。

| 字段 | 含义 | 填写要求 | 示例 |
| --- | --- | --- | --- |
| agentId | Agent 在接入方原平台中的标识，用于关联、检索来源 Agent | 使用本扩展时必填。1～256 个 Unicode 字符，无首尾空白或控制字符，同一 BCN Provider 下唯一；保留大小写和前导零。登记后不能通过 Card 更新或刷新修改、删除 | order-agent-001 |
| party | Agent 的来源类别，用于区分一方、二方和三方 | 可选，取值见下表；省略表示未分类，可随 Card 更新 | first_party |

| party 取值 | 分类 | 含义（以所属 Provider 为参照） |
| --- | --- | --- |
| first_party | 一方 | Provider 自身提供的 Agent |
| second_party | 二方 | 与该 Provider 有直接合作关系的合作方提供的 Agent |
| third_party | 三方 | 该 Provider 接入的其他外部 Agent |

`agentId` 不代替 BCN 内部 ID 或 AgentCard.name；查询方结合响应外层的 provider_name 区分不同 Provider 的来源 Agent。`party` 仅表示来源分类，不代表权限或已通过平台认证。

以下为完整扩展结构，放入 `capabilities.extensions` 数组。`uri` 由 BCN 统一发布，所有接入方填写同一个值；固定使用下方 Avernet 仓库规范地址。整个扩展可省略；若使用则填写 agentId，required 固定为 false。

```json
{
  "uri": "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/source-agent/v1.md",
  "description": "Agent 来源标识及分类",
  "required": false,
  "params": {
    "agentId": "order-agent-001",
    "party": "first_party"
  }
}
```

#### 2.2.4 skills 每项

| 字段 | 类型 | BCN 必填 | BCN 要求及用途 |
| --- | --- | --- | --- |
| id | string | 是 | 非空技能标识，同一 Card 内唯一；不是来源平台 agentId |
| name | string | 是 | 非空技能名称，支持中文 |
| description | string | 是 | 非空技能说明，完整保存并返回 |
| tags | string[] | 是 | 技能标签，可为 [] |
| examples | string[] | 否 | 示例提问或使用场景，保留返回 |
| inputModes | string[] | 否 | 省略时继承 defaultInputModes；显式声明会覆盖默认值。仅接受其他媒体类型的技能不在 BCN 本期调用范围内 |
| outputModes | string[] | 否 | 省略时继承 defaultOutputModes；显式声明会覆盖默认值。保留声明不代表支持非文本输出 |
| securityRequirements | object[] | 否 | 本期省略或 []；必须提供凭据的技能不支持接入 |

#### 2.2.5 安全声明

本期推荐省略 securitySchemes，securityRequirements 填写 []。只有无需应用层凭据即可调用的 Agent 和技能可接入。

如保留认证方案说明，按 [A2A Security Objects](https://a2a-protocol.org/v1.0.1/specification/#45-security-objects) 填写。安全要求的多个选项为 OR，同一选项内的方案为 AND；引用的方案必须已声明。BCN 不提供调用凭据，Card 中不得包含真实密钥或 token。

### 2.3 成功响应

四个接口统一返回以下结构：

```json
{
  "provider_name": "order-service",
  "bot_id": "bcn-bot-example",
  "agent_name": "订单助手",
  "agent_card_url": null,
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
    "capabilities": {"streaming": true},
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
    "skills": [],
    "securityRequirements": []
  }
}
```

| 响应字段 | 说明 |
| --- | --- |
| provider_name | token 所属 Provider 名称 |
| agent_name | 已保存的 AgentCard.name |
| bot_id | BCN Bot 标识；改名后保持不变 |
| agent_card_url | URL 来源地址；直接提交 Card 时为 null |
| agent_card | 完整 Card，保留可选字段及扩展 |

响应头包含 X-Request-Id（排障标识）和 Cache-Control: no-store。

## 3. 注册 Agent

```http
POST /a2a/agents
Authorization: Bearer <provider_admin_token>
Content-Type: application/json
```

推荐请求：直接提交完整 Card。

```json
{
  "owners": ["owner-user-id"],
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
    "capabilities": {"streaming": true},
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
    "skills": [
      {
        "id": "query_order",
        "name": "查询订单",
        "description": "根据订单号查询状态",
        "tags": ["orders"]
      }
    ],
    "securityRequirements": []
  }
}
```

| 请求字段 | 必填 | 规则 |
| --- | --- | --- |
| owners | 是 | 见公共定义 |
| agent_card_url | 二选一 | 拉取 Card 后校验、注册 |
| agent_card | 二选一，推荐 | 直接校验并保存完整 Card；字段要求见 2.2 |
| summary | 否 | 仅设置注册时的展示摘要 |

名称从 Card.name 获取，无需另传 agent_name。成功返回 **201 Created**，响应见 2.3；Location 头为查询地址。同一 Provider 下名称或已登记来源 agentId 重复时返回 409。

## 4. 查询 AgentCard

```http
GET /a2a/agent-card?agent_name=%E8%AE%A2%E5%8D%95%E5%8A%A9%E6%89%8B
Authorization: Bearer <provider_admin_token>
```

query 仅接受必填的 agent_name，不发送请求体。查询的是 BCN 已保存的 Card，不实时访问远端 URL，不触发刷新。

成功返回 200 及 2.3 的对象。名称不存在或不属于 token 的 Provider 时返回 404 / BOT_NOT_FOUND。

## 5. 更新 AgentCard

```http
PUT /a2a/agent-card
Authorization: Bearer <provider_admin_token>
Content-Type: application/json
```

推荐直接提交完整 `agent_card`，与注册方式保持一致：

```json
{
  "agent_name": "订单助手",
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
    "capabilities": {"streaming": true},
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
    "skills": [
      {
        "id": "query_order",
        "name": "查询订单",
        "description": "根据订单号查询状态",
        "tags": ["orders"]
      }
    ],
    "securityRequirements": []
  }
}
```

| 请求字段 | 必填 | 规则 |
| --- | --- | --- |
| agent_name | 是 | 更新前的当前名称，在 token 所属 Provider 内匹配 |
| agent_card_url | 二选一，可选方式 | 更新来源 URL，并立即拉取、校验其 Card |
| agent_card | 二选一，推荐 | 提交完整 Card 覆盖原内容，切换为内联来源 |

成功返回 **200 OK**，响应格式见 2.3，agent_name 为更新后的名称。

| 更新规则 | 说明 |
| --- | --- |
| 提交内容 | 推荐完整 agent_card，也可提交 agent_card_url；两者二选一，不做部分合并 |
| 更新范围 | 更新 Card 及来源，不修改 owners、summary |
| 来源切换 | 直接提交 Card 后清除旧 URL、停止自动刷新；提交 URL 后按新地址刷新 |
| 名称变化 | 同步更新 agent_name，后续使用新名称；重名则更新失败 |
| 来源标识 | 已登记的 agentId 不允许修改或移除 |
| 并发更新 | 无需先查询版本；以最后成功写入为准，失败不部分更新 |
| 已有任务 | 保持原任务关联，不切换到新接口地址 |

错误码见第 7 节。

## 6. 刷新 AgentCard

```http
POST /a2a/agent-card/refresh
Authorization: Bearer <provider_admin_token>
Content-Type: application/json
```

```json
{
  "agent_name": "订单助手"
}
```

| 请求字段 | 必填 | 说明 |
| --- | --- | --- |
| agent_name | 是 | 当前 Agent 名称 |

从已保存的 URL 重新读取 Card，更新规则与 PUT 相同。成功返回 **200 OK**，响应见 2.3。

直接提交 Card 的 Agent 没有来源 URL，应使用 PUT 更新。刷新结果若被其他更新取代，返回 409 / REFRESH_SUPERSEDED。

## 7. 错误响应

BCN 产生的非 2xx 响应使用 `application/json`：

```json
{
  "error": {
    "code": "BOT_NAME_CONFLICT",
    "message": "同一 Provider 下已存在该 Agent 名称",
    "request_id": "req-example-001",
    "details": [
      {
        "field": "agent_card.name",
        "reason": "ALREADY_EXISTS"
      }
    ]
  }
}
```

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| error.code | string | 稳定机器错误码，客户端据此处理 |
| error.message | string | 人类可读说明，文案可调整，不用于程序分支 |
| error.request_id | string | 与 X-Request-Id 一致 |
| error.details | object[] | 始终存在，无细节时 []；每项包含 field、reason 字符串 |

details.field 指出错误字段，例如 agent_card.name；reason 为原因标识。

| HTTP | code | 含义与处理 |
| --- | --- | --- |
| 400 | INVALID_REQUEST | 非法 JSON、未知/重复字段、缺失或类型错误；修正请求 |
| 401 | UNAUTHENTICATED | 凭据缺失、无效、用途不匹配或失效；响应含 WWW-Authenticate: Bearer |
| 403 | PERMISSION_DENIED | Provider 已禁用，或当前 Provider 身份模式不支持此注册入口 |
| 404 | BOT_NOT_FOUND | 当前管理范围内未找到该名称 |
| 405 | METHOD_NOT_ALLOWED | 方法不支持；响应含 Allow |
| 406 | NOT_ACCEPTABLE | Accept 不接受 JSON |
| 409 | BOT_NAME_CONFLICT | 当前有效名称已占用 |
| 409 | SOURCE_AGENT_CONFLICT | 同 Provider 下来源 agentId 重复 |
| 409 | SOURCE_AGENT_CHANGE_NOT_ALLOWED | 试图修改或移除已登记来源标识 |
| 409 | TARGET_CHANGED | 请求处理期间目标被改名或移除；核实目标后操作 |
| 409 | CARD_URL_REQUIRED | 内联 Card 无法通过 URL 刷新 |
| 409 | REFRESH_SUPERSEDED | 刷新结果已过期，没有应用；先查询最新配置 |
| 413 | REQUEST_TOO_LARGE | 管理请求体超限 |
| 415 | UNSUPPORTED_MEDIA_TYPE | 请求体 Content-Type 不支持 |
| 422 | INVALID_OWNER | 所有者标识格式不合法或不可指定 |
| 422 | INVALID_CARD | Card 结构、名称或声明不合法 |
| 422 | CARD_SOURCE_INVALID | Card 来源未满足二选一，或 URL 格式不合法 |
| 422 | CARD_TOO_LARGE | 内联或拉取的 Card 超过大小限制 |
| 422 | CARD_ADDRESS_REJECTED | Card URL 或声明的接口违反地址策略 |
| 422 | CARD_INTERFACE_UNSUPPORTED | 没有可用的协议版本/绑定接口 |
| 422 | CARD_CONTENT_UNSUPPORTED | 不支持所需文本输入输出 |
| 422 | CARD_AUTH_UNSUPPORTED | Card 必须鉴权，本期无法满足 |
| 422 | CARD_EXTENSION_UNSUPPORTED | 存在不支持的必需扩展 |
| 429 | RATE_LIMITED | 请求被限流；响应含 Retry-After 秒数 |
| 502 | CARD_FETCH_FAILED | 无法读取远端 Card，包括非 200 或拒绝的重定向 |
| 504 | CARD_FETCH_TIMEOUT | 拉取远端 Card 超时 |
| 503 | SERVICE_UNAVAILABLE | 临时服务不可用；可提供 Retry-After |
| 500 | INTERNAL_ERROR | 内部处理异常；提供 request_id 排查 |

注册、更新或刷新请求超时后，请先查询当前 Card 确认结果，再决定是否重试。
