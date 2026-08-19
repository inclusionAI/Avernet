# Bot 公开目录 OpenAPI（前端接入文档）

> 版本：2026-08-19
>
> 本文档面向经由 Gateway 调用 Bot 公开目录的前端页面。机器可读的权威契约为
> [`bots.openapi.json`](../../../gateway/configs/schemas/bots.openapi.json)。接口或字段与本文不一致时，以该 schema 为准。

## 1. 用途与边界

目录接口用于搜索和发现公开 Bot：

- `GET /openapi/v1/bots/public/search`：按 Bot 名称或 owner 名称分页搜索；
- `GET /openapi/v1/bots/public/discover`：按关键词取得推荐 Bot。

**Base URL** 由当前环境的 Gateway 域名与下文路径组成，例如
`https://{gateway-host}/openapi/v1/...`。页面必须复用项目的 Gateway 请求客户端，让它携带当前登录态；不要在浏览器中伪造或手工构造内部 principal / 身份头。

`user_id` 是显式业务作用域，必须传当前登录用户的实体 ID。它不是可供页面切换的“筛选用户”参数：

- 用户身份调用时，`user_id` 必须等于已验证的当前用户；
- 用户附带应用身份时，仍只能代表该用户；
- 纯应用身份不能调用目录接口；
- 前端不能把 `user_id` 缓存在跨账号的全局状态中；切换账号后应重新读取当前用户。

接口**不会**返回也不接受 `binding_id`、数据库内部 `id`、`device_id`、实例标识、`ext`、运行时环境信息或任何 token。不要为了连接 Bot 而猜测这些字段。

## 2. 通用响应与错误处理

两个接口都返回 JSON Envelope：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "total": 1,
    "items": []
  },
  "request_id": "trace-id"
}
```

- `code` 为 6 位数：前三位对应 HTTP status，后三位为业务子码。
- 失败时 `data` 为 `null`。前端先按 HTTP status 分流，再展示 `message`。
- 记录 `request_id` 以便用户反馈和后端定位；不要在前端日志中记录完整请求 URL、关键词或任何认证信息。

| HTTP / `code` | 含义 | 前端处理 |
|---|---|---|
| `400000` / `422000` | 参数缺失或不满足范围、枚举约束。 | 阻止请求或提示用户修正；不要自动重试。 |
| `401000` | 登录态或 Gateway principal 缺失/无效。 | 走统一登录或刷新流程。 |
| `401001` | 已验证但仅有应用身份，没有终端用户身份。 | 不重试；修正调用链路以携带当前用户登录态。 |
| `403001` | 请求中的 `user_id` 与已验证当前用户不一致。 | 不重试；从当前用户上下文重新取值。 |
| `502000` | 推荐服务暂不可用（discover）。 | 有限退避重试，并携带 `request_id` 上报。 |
| `500000` | 服务端内部错误。 | 有限退避重试，并携带 `request_id` 上报。 |

## 3. 搜索公开 Bot

```text
GET /openapi/v1/bots/public/search
```

| Query 参数 | 必填 | 规则 | 说明 |
|---|---:|---|---|
| `user_id` | 是 | 非空字符串 | 当前搜索用户；必须等于登录用户。 |
| `search` | 否 | 字符串 | 按 Bot 名称或 owner 名称模糊搜索；不传时不施加关键词过滤。 |
| `page` | 否 | 整数，默认 `1`，最小 `1` | 从 1 开始的页码。 |
| `page_size` | 否 | 整数，默认 `20`，范围 `1–100` | 每页结果数。 |

示例：

```text
GET /openapi/v1/bots/public/search?user_id={current_user_id}&search=%E8%90%A5%E9%94%80&page=1&page_size=20
```

成功响应示例：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "total": 1,
    "items": [
      {
        "bot_id": "bot_marketing_01",
        "entity_id": "owner_123",
        "bot_type": "service",
        "name": "营销助手",
        "description": "提供营销方案与内容建议",
        "owner_name": "产品团队",
        "engine": "openclaw",
        "status": "online",
        "friendship": {
          "status": "ACCEPTED",
          "requires_approval": false
        }
      }
    ]
  },
  "request_id": "trace-id"
}
```

## 4. 发现推荐 Bot

```text
GET /openapi/v1/bots/public/discover
```

| Query 参数 | 必填 | 规则 | 说明 |
|---|---:|---|---|
| `user_id` | 是 | 非空字符串 | 当前发现用户；必须等于登录用户。 |
| `keyword` | 是 | 非空字符串 | 用于推荐的关键词。 |
| `top_k` | 否 | 整数，默认 `10`，范围 `1–20` | 最多返回的推荐项数。 |
| `min_score` | 否 | 数字，默认 `0.1`，范围 `0–1` | 最低推荐相关度。 |
| `runtime_state` | 否 | `draft`、`verify`、`online`；默认 `online` | 后端转换为推荐服务的运行态过滤条件。 |

示例：

```text
GET /openapi/v1/bots/public/discover?user_id={current_user_id}&keyword=%E5%90%88%E5%90%8C%E5%AE%A1%E6%A0%B8&top_k=10&min_score=0.3&runtime_state=online
```

相较于搜索结果，发现结果的每一项额外包含 `recommendation`：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "total": 1,
    "items": [
      {
        "bot_id": "bot_legal_01",
        "entity_id": "owner_456",
        "bot_type": "service",
        "name": "合同助手",
        "description": "协助审阅常见合同条款",
        "engine": "openclaw",
        "status": "online",
        "recommendation": {
          "score": 0.92,
          "reasons": ["与“合同审核”关键词相关"],
          "short_profile": "面向常见商务合同的审阅助手"
        }
      }
    ]
  },
  "request_id": "trace-id"
}
```

`score` 是相关度数值；`reasons` 可能为空数组，`short_profile` 可能省略。不要依赖推荐服务的其他原始字段，也不要把 `runtime_state=online` 推导为 connection 一定可用。

## 5. 前端数据模型

```ts
type Friendship = {
  status: "PENDING" | "ACCEPTED" | "REJECTED" | "CANCELLED";
  requires_approval: boolean;
};

type PublicBot = {
  bot_id: string;
  entity_id: string; // Bot owner；后续 connection 的 owner_id
  bot_type: "personal" | "service" | "desktop";
  name: string;
  description: string;
  owner_name?: string;
  engine: string;
  status: string;
  friendship?: Friendship;
};

type DiscoveredPublicBot = PublicBot & {
  recommendation: {
    score: number;
    reasons: string[];
    short_profile?: string;
  };
};

type Page<T> = {
  total: number;
  items: T[];
};

type Envelope<T> = {
  code: number;
  message: string;
  data: T | null;
  request_id: string;
};
```

`friendship` 是**当前已验证用户**与该 Bot 的关系投影，而不是 Bot 的全局状态。字段缺失表示没有可展示的关系状态；不要把它缓存后展示给另一位用户。

## 6. 从目录项进入连接能力

目录只负责发现与展示，不返回内部 runtime binding。需要打开一个 Bot 时，可将目录项的公开地址传给既有 connection 接口：

```text
GET /openapi/v1/bots/{bot_id}/connection
  ?user_id={current_user_id}
  &owner_id={entity_id}
  &stage={draft|verify|online}
```

- 使用结果中的 `bot_id` 与 `entity_id`；后者作为 `owner_id`。
- `stage` 由页面的明确产品选择决定。服务 Bot 的 `verify` / `online` 会在后端按发布记录解析，不需要、也不能传 `binding_id`。
- 多实例选择由后端 provider 完成；页面不能传 instance 或 `device_uuid` 来指定某个实例。
- 连接失败时按 connection 接口的错误契约处理，不要回退到内部 `/api/...` 或自行拼接设备地址。

## 7. 前端接入检查清单

- [ ] 所有请求均通过 Gateway 客户端发起，并为 `user_id` 传当前登录用户实体 ID。
- [ ] 搜索分页只使用 `page` 与 `page_size`；`page` 从 1 开始。
- [ ] 发现页将 `keyword` 视为必填，并对 `top_k`、`min_score`、`runtime_state` 做前置校验。
- [ ] relationship UI 仅使用当前响应中的 `friendship`，不跨用户缓存。
- [ ] 点击进入 Bot 时只传 `bot_id`、`entity_id` 和明确选择的 `stage`；不要求或保存 binding/实例字段。
- [ ] 失败反馈包含 `request_id`，但不包含认证信息、完整请求 URL 或用户输入关键词。
