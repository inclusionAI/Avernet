# Bot Catalog OpenAPI（前端接入文档）

> 版本：2026-08-20
>
> 机器可读权威契约为 `src/gateway/configs/schemas/bots.openapi.json`。

## 1. 接口与权限

目录接口用于查询公开 Bot，User 与 App 均可调用；所有调用者在同一租户和平台下看到相同结果。请求必须携带至少一个可验证的 User 或 App 身份，无有效身份返回 `401000`。

```text
GET /openapi/v1/bots/catalog/search
GET /openapi/v1/bots/catalog/discover
```

目录响应不包含用户关系状态，也不返回 `binding_id`、数据库内部 ID、设备信息、`ext`、运行环境、实例标识或凭据。

## 2. 搜索公开 Bot

```text
GET /openapi/v1/bots/catalog/search
```

| 参数 | 必填 | 规则 |
|---|---:|---|
| `search` | 否 | Bot 名称或 owner 名称关键词 |
| `page` | 否 | 默认 1，最小 1 |
| `page_size` | 否 | 默认 20，范围 1–100 |

示例：

```text
GET /openapi/v1/bots/catalog/search?search=marketing&page=1&page_size=20
```

## 3. 发现推荐 Bot

```text
GET /openapi/v1/bots/catalog/discover
```

| 参数 | 必填 | 规则 |
|---|---:|---|
| `keyword` | 是 | 非空字符串 |
| `top_k` | 否 | 默认 10，范围 1–20 |
| `min_score` | 否 | 默认 0.1，范围 0–1 |
| `runtime_state` | 否 | `draft`、`verify`、`online`，默认 `online` |

示例：

```text
GET /openapi/v1/bots/catalog/discover?keyword=contract&top_k=10
```

## 4. 返回模型

```ts
type PublicBot = {
  bot_id: string;
  entity_id: string;
  bot_type: "personal" | "service" | "desktop";
  name: string;
  description: string;
  owner_name?: string;
  engine: string;
  status: string;
};

type DiscoveredPublicBot = PublicBot & {
  recommendation: {
    score: number;
    reasons: string[];
    short_profile?: string;
  };
};
```

统一响应：

```json
{
  "code": 200000,
  "message": "OK",
  "data": {"total": 0, "items": []},
  "request_id": "trace-id"
}
```

## 5. 错误处理

| HTTP / code | 含义 |
|---|---|
| `401000` | User/App Principal 缺失或无效 |
| `422000` | 参数缺失或范围错误 |
| `500000` | 搜索服务内部错误 |
| `502000` | 推荐服务暂不可用 |

前端记录 `request_id` 用于排障，不记录认证信息、完整请求 URL 或搜索关键词。
