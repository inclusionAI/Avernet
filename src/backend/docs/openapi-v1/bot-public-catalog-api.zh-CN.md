# Bot Catalog OpenAPI（前端接入文档）

> 版本：2026-08-21
>
> 机器可读权威契约为 `src/gateway/configs/schemas/bots.openapi.json`。

## 1. 接口与权限

目录接口用于查询 Catalog Bot，User 与 App 均可调用；所有调用者在同一租户和平台下看到相同结果。请求必须携带至少一个可验证的 User 或 App 身份，无有效身份返回 `401000`。

```text
GET /openapi/v1/bots/catalog/search
GET /openapi/v1/bots/catalog/discover
```

目录响应不包含用户关系状态，也不返回 `binding_id`、数据库内部 ID、设备信息、`ext`、运行环境、实例标识或凭据。

## 2. 搜索 Catalog Bot

```text
GET /openapi/v1/bots/catalog/search
```

Backend 将 `search`、`page` 和 `page_size` 映射到 BCS `/v2/bots/search` 的 `q`、`offset` 和
`limit`，并固定传 `tc_bot=true`，只读取当前 BCS 页。该标识仅保留由 TeamClaw Backend onboard 的
Bot。BCS 的 `bot_uuid` 按 `<bot_id>:<entity_id>` 解析后，Backend 在当前租户、当前环境内以精确二元组查询未删除的 live
Bot 并内连接；Catalog Search 不再以 Backend `public="1"` 作为过滤条件，Backend 是全部对外字段的唯一权威来源。

BCS 的排序和分页边界保持不变。`tc_bot=true` 使正常数据下 BCS 当前页数量与 Backend join 数量一致；
这里的 `total` 是**当前页 join 后数量**，不是跨 BCS 页的总数。若迁移或数据同步暂时不一致，接口仍只
返回实际 join 的记录。BCS 不可用或返回非法记录时固定返回 `502000 / Catalog service unavailable`，
不会回退为 Backend-only 搜索。

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
  bot_type: unknown;
  name: string;
  description: string;
  owner_name?: unknown;
  engine: string;
  status: string;
};

type DiscoveredPublicBot = PublicBot & {
  recommendation: {
    score: number;
    reasons: unknown;
    short_profile?: unknown;
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
| `502000` | Catalog Search 的 BCS 请求失败或响应非法，或推荐服务暂不可用 |

前端记录 `request_id` 用于排障，不记录认证信息、完整请求 URL 或搜索关键词。
