# Session Favorites OpenAPI 接口文档

本文面向 Web、客户端等 OpenAPI 调用方，说明会话收藏列表、收藏和取消收藏接口。

## 接口概览

| 功能 | Method | Path |
|---|---|---|
| 查询收藏会话 | `GET` | `/openapi/v1/bots/{bot_id}/sessions/favorites` |
| 收藏会话 | `PUT` | `/openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite` |
| 取消收藏 | `DELETE` | `/openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite` |

收藏状态按 `user_id` 隔离。同一 Bot 的不同用户拥有相互独立的收藏列表。收藏和取消收藏均为幂等操作，可以安全重试。

## 调用约定

### 网关地址与认证

请求发送至当前环境的 OpenAPI Gateway：

```text
{gateway_base_url}/openapi/v1/...
```

认证信息沿用 OpenAPI Gateway 的统一认证方式。前端不得自行构造内部身份头，例如 `X-Avernet-Principal`；该头由网关验证调用方身份后生成并转发。

每个接口都需要显式传递 `user_id`。Gateway/Backend 会校验它与认证身份或应用授权是否一致，不能通过修改 `user_id` 访问其他用户的收藏。

### 公共参数

| 参数 | 位置 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `bot_id` | Path | 是 | - | Bot ID，例如 `20260813_a7k2m9p1` |
| `user_id` | Query | 是 | - | 当前操作对应的终端用户 ID |
| `stage` | Query | 否 | `draft` | Bot 运行阶段：`draft`、`verify` 或 `online` |
| `owner_id` | Query | 否 | 当前用户 | Bot 所有者 ID；仅操作别人共享给自己的 Bot 时传递 |

`session_id` 必须使用会话接口返回的原始值。放入 URL path 时应交给浏览器或请求库做一次标准 URL 编码，不要提前编码后再二次编码。

### 统一响应结构

所有接口都返回 OpenAPI 统一 envelope：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {},
  "request_id": "01JZ7P9A5TF7Y9S4N6Q0M2K8RX"
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | `number` | 6 位业务码，前三位是 HTTP 状态码，例如成功为 `200000` |
| `message` | `string` | 状态描述，当前统一使用英文 |
| `data` | `object \| null` | 业务数据；失败时为 `null` |
| `request_id` | `string` | 请求 Trace ID，反馈问题时请一并提供 |

前端应同时判断 HTTP 状态码和响应中的 `code`，不要只根据 `message` 判断成功。

## 1. 查询收藏会话

```http
GET /openapi/v1/bots/{bot_id}/sessions/favorites
```

### Query 参数

除公共参数外，还支持：

| 参数 | 必填 | 默认值 | 限制 | 说明 |
|---|---:|---|---|---|
| `agent_id` | 否 | - | string | 只返回属于指定 Agent 的收藏会话 |
| `page` | 否 | `1` | `>= 1` | 页码，从 1 开始 |
| `page_size` | 否 | `20` | `1～100` | 每页条数 |

### 请求示例

```http
GET /openapi/v1/bots/20260813_a7k2m9p1/sessions/favorites?user_id=149608&stage=draft&page=1&page_size=20
```

```ts
const query = new URLSearchParams({
  user_id: currentUserId,
  stage: 'draft',
  page: '1',
  page_size: '20',
});

const response = await fetch(
  `${gatewayBaseUrl}/openapi/v1/bots/${encodeURIComponent(botId)}/sessions/favorites?${query}`,
  { credentials: 'include' },
);
const result = await response.json();
```

### 成功响应

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "total": 2,
    "items": [
      {
        "session_id": "session:2d20edc1:user:149608",
        "title": "Quarterly report",
        "agent_id": "main",
        "model": "openai/gpt-5.3",
        "permission_mode": "on-miss",
        "cwd": "/workspace",
        "runtime": "",
        "message_count": 12,
        "gmt_create": "2026-08-18T09:00:00+00:00",
        "gmt_modified": "2026-08-18T09:12:04+00:00"
      }
    ]
  },
  "request_id": "01JZ7P9A5TF7Y9S4N6Q0M2K8RX"
}
```

`data.total` 是当前可确认的数量下界。由于 Engine 不提供精确总数，前端不要使用它直接计算总页数；应持续翻页，直到 `items.length < page_size`。

会话字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `session_id` | `string` | 会话 ID，收藏/取消收藏时原样使用 |
| `title` | `string` | 会话标题 |
| `agent_id` | `string` | 会话所属 Agent，可能为空 |
| `model` | `string` | 会话使用的模型，可能为空 |
| `permission_mode` | `string` | 当前权限模式，可能为空 |
| `cwd` | `string` | 工作目录，可能为空 |
| `runtime` | `string` | Runtime 标识，可能为空 |
| `message_count` | `number` | 消息数量下界，不保证是精确值 |
| `gmt_create` | `string` | ISO 8601 创建时间，Engine 未返回时为空 |
| `gmt_modified` | `string` | ISO 8601 修改时间，Engine 未返回时为空 |

## 2. 收藏会话

```http
PUT /openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite
```

接口没有请求体。重复收藏同一个会话仍返回成功。

### 请求示例

```ts
const query = new URLSearchParams({
  user_id: currentUserId,
  stage: 'draft',
});

const response = await fetch(
  `${gatewayBaseUrl}/openapi/v1/bots/${encodeURIComponent(botId)}` +
    `/sessions/${encodeURIComponent(sessionId)}/favorite?${query}`,
  {
    method: 'PUT',
    credentials: 'include',
  },
);
const result = await response.json();
```

### 成功响应

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "session_id": "session:2d20edc1:user:149608",
    "favorited": true
  },
  "request_id": "01JZ7P9A5TF7Y9S4N6Q0M2K8RX"
}
```

## 3. 取消收藏

```http
DELETE /openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite
```

接口没有请求体。重复取消收藏仍返回成功。

### 请求示例

```ts
const query = new URLSearchParams({
  user_id: currentUserId,
  stage: 'draft',
});

const response = await fetch(
  `${gatewayBaseUrl}/openapi/v1/bots/${encodeURIComponent(botId)}` +
    `/sessions/${encodeURIComponent(sessionId)}/favorite?${query}`,
  {
    method: 'DELETE',
    credentials: 'include',
  },
);
const result = await response.json();
```

### 成功响应

```json
{
  "code": 200000,
  "message": "OK",
  "data": {
    "session_id": "session:2d20edc1:user:149608",
    "favorited": false
  },
  "request_id": "01JZ7P9A5TF7Y9S4N6Q0M2K8RX"
}
```

## 错误处理

三个接口使用相同的错误 envelope：

```json
{
  "code": 404000,
  "message": "Not found",
  "data": null,
  "request_id": "01JZ7P9A5TF7Y9S4N6Q0M2K8RX"
}
```

常见 HTTP 状态码：

| HTTP 状态码 | 典型含义 | 前端建议 |
|---:|---|---|
| `400` | 请求参数或上游请求不合法 | 提示用户并检查请求参数 |
| `401` | 未认证或认证已失效 | 进入统一登录/续期流程 |
| `403` | `user_id` 与身份不匹配，或应用没有对应授权 | 禁止继续重试，提示无权限 |
| `404` | Bot 不存在，或调用方不可见/不可操作 | 按资源不存在处理，避免暴露权限信息 |
| `409` | 指定 stage 没有活跃 Runtime，或 Runtime 状态冲突 | 提示切换阶段或稍后重试 |
| `422` | 参数校验失败，例如分页范围错误 | 展示参数错误，不要自动重试 |
| `500` | Backend 内部错误 | 可有限重试并上报 `request_id` |
| `501` | 当前 Engine 不支持该能力 | 隐藏或禁用收藏功能 |
| `502` | Engine/设备不可用或上游响应异常 | 提示 Bot 暂不可用，可有限重试 |
| `504` | Engine 请求超时 | 提示超时，可有限重试 |

## 前端接入建议

- 收藏按钮可先展示加载态，收到 `code === 200000` 后再更新本地状态。
- PUT/DELETE 是幂等的，网络超时后可以重试；最终状态以响应中的 `favorited` 为准。
- 切换 `bot_id`、`user_id`、`stage` 或 `agent_id` 时清空收藏列表缓存，这些参数共同决定列表范围。
- 查询列表使用“加载更多”或根据短页判断结束，不要根据 `total` 预生成固定页码。
- 日志和错误上报保留 `request_id`，不要记录认证 Cookie、Token 或内部身份头。
