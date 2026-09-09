# Bot Catalog OpenAPI（前端接入文档）

> 版本：2026-09-08
>
> 机器可读权威契约为 `src/gateway/configs/schemas/bots.openapi.json`。

## 1. 接口与权限

目录接口用于查询 Catalog Bot，User 与 App 均可调用；结果由显式 viewer 与筛选条件共同决定。请求必须携带至少一个可验证的 User 或 App 身份，无有效身份返回 `401000`。

```text
GET /openapi/v1/bots/catalog/search
GET /openapi/v1/bots/catalog/discover
```

目录响应不返回 `binding_id`、数据库内部 ID、设备信息、`ext`、运行环境、实例标识或凭据。Catalog Search 对 BCS 当前目录项
可选透传 `visibility`、`is_online`、`actor_kind`、`is_friend`、`friend_ext`、`friend_check_in_strategy` 和
`user_visibility`；BCS 的 `name`、`summary`、`created_by`、`status` 只用于下述公开字段兜底，除此之外不返回 BCS 原始字段。

## 2. 搜索 Catalog Bot

```text
GET /openapi/v1/bots/catalog/search
```

Backend 将 `search`、`page` 和 `page_size` 映射到 BCS `/bots/search` 的 `q`、`offset` 和
`limit`，只读取当前 BCS 页。`viewer_actor_type=bot` 时不传 `tc_bot`，允许 BCS 返回 native Bot；
`viewer_actor_type=human` 或未传 viewer 时传 `tc_bot=true`，只读取 TeamClaw Backend onboard 的 Bot。前端选择性传入的 `visibility`、
`user_visibility`、`status`、`viewer_actor_type`、`viewer_actor_id` 与 `friendship` 经过本地
校验后才映射到同名 BCS query。BCS 的 `bot_uuid` 与 `created_by` 用于构造 Bot 地址；Backend 在当前租户、
当前环境内以精确二元组查询未删除的 live Bot，但最终以 BCS 当前页为准做左连接。BCS 返回而 Backend 未命中的
Bot 不会被删除；Backend 查询仍受 tenant guard 约束，viewer 参数不会扩大数据库租户范围。

BCS 的排序和分页边界保持不变，`total` 原样采用 BCS 返回的总数。若 Backend 没有对应记录，当前页 item 仍使用
BCS `name`、`summary`、`created_by`、`status` 组装；BCS 不提供的 `bot_type` 和 `engine` 返回空字符串。
BCS 不可用或返回非法记录时固定返回 `502000 / Catalog service unavailable`，
不会回退为 Backend-only 搜索。

每个 Search item 返回 BCS `bot_uuid`；已 join 的记录中，Backend 非空 `description`、`entity_id` 优先，
否则分别使用 BCS `summary`、`created_by` 兜底。若 BCS 当前目录项提供 `visibility`、`is_online`、`actor_kind`、`is_friend`、`friend_ext`、
`friend_check_in_strategy` 或 `user_visibility`，Backend 在精确 `(bot_id, entity_id)` join 后返回；字段缺失或为
`null` 时响应中省略。`is_friend` 仅在前端成对传入 `viewer_actor_type` 与 `viewer_actor_id` 且 BCS 返回该字段时出现；未传 viewer 不会以 `false` 代替。`friend_ext` 保留业务结构和键，但明显的凭据值会被置空。Backend 不重新查询、推导或覆盖其他 BCS 值。

| 参数 | 必填 | 规则 |
|---|---:|---|
| `search` | 否 | Bot 名称或 owner 名称关键词 |
| `page` | 否 | 默认 1，最小 1 |
| `page_size` | 否 | 默认 20，范围 1–100 |
| `visibility` | 否 | BCS Bot 可见性；支持 `public`、`protected`、`private`，可重复传入或逗号分隔多值 |
| `user_visibility` | 否 | BCS 用户侧可见性；取值和多值格式同 `visibility` |
| `status` | 否 | 仅在传入时过滤 BCS 状态：`online` 或 `hidden` |
| `viewer_actor_type` | 否 | BCS 关系视角 actor 类型：`human` 或 `bot`；必须与 `viewer_actor_id` 成对传入 |
| `viewer_actor_id` | 否 | BCS 关系视角 actor ID；必须与 `viewer_actor_type` 成对传入 |
| `friendship` | 否 | BCS 关系过滤：`all`、`friends`、`non_friends`；`friends`/`non_friends` 必须传 viewer pair |

示例：

```text
GET /openapi/v1/bots/catalog/search?search=marketing&page=1&page_size=20
```

```text
GET /openapi/v1/bots/catalog/search?visibility=public,protected&user_visibility=public&status=online&viewer_actor_type=human&viewer_actor_id=330429&friendship=non_friends
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
  bot_uuid?: string; // 优先 BCS 返回值，缺失时为 Backend <bot_id>:<entity_id>
  entity_id: string;
  bot_type: unknown;
  name: string;
  description: string;
  owner_name?: unknown;
  visibility?: unknown; // 仅 Catalog Search 中 BCS 提供时返回
  is_online?: unknown;
  actor_kind?: string;
  is_friend?: boolean;
  friend_ext?: unknown;
  friend_check_in_strategy?: unknown;
  user_visibility?: unknown;
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
