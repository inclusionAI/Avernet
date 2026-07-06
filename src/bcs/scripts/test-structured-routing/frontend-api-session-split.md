# BCS Group/Session 两层拆分 — 前端 API 速查

## 概述

- **Group**：长期存在的协作容器（建群时一次性声明 Bot 成员，事后不变更）
- **Session**：一次具体对话（参与者、消息）。所有动态成员变更（加 Bot / 加 Human / 改 mode）都走 Session 维度
- **视角**：所有列表查询以 Bot 或 Human 为入口（`/bots/{id}/groups`），不调全量 `/groups` 接口
- **加入即可见**：caller 只要出现在 group.participants 或任意 session.participants，该群/session 即对 caller 可见

---

## 一、建群 / 查群

### `POST /groups` — 建群

```json
{
  "driver_bot": "bot_xxx",
  "participants": [
    {"bot_uuid": "bot_xxx", "role": "manager"},
    {"bot_uuid": "bot_yyy", "role": "worker"}
  ],
  "group_strategy": "manager_worker",
  "label": "数据库性能审计"
}
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `group_strategy` | `"chat"` \| `"manager_worker"` | 否 | `"chat"` | chat 群角色=driver/consultant/observer；manager_worker 群角色=manager/worker/observer |

### `GET /bots/{id}/groups` — 查群列表 ⭐ 前端主入口

以 Bot 或 Human 视角拉群。`{id}` = bot_uuid 或 `human_xxx`。默认只返回 normal 群，前端通常不带参数。

```
GET /bots/{id}/groups                → normal（默认）
GET /bots/{id}/groups?group_kind=dm  → 仅 DM 群
GET /bots/{id}/groups?group_kind=all → 全部
GET /bots/{id}/groups?q=数据库        → 搜索（匹配 group_id / label）
GET /bots/{id}/groups?offset=0&limit=10
```

响应中每项含 `group_id` / `label` / `coordinator_bot` / `participants` / `group_strategy` / `group_kind` / `created_at` / `updated_at`。

### `GET /groups/{id}` — 群详情

```
GET /groups/{id}
```

**响应**：
```json
{
  "id": "group_uuid",
  "label": "数据库性能审计",
  "status": "active",
  "context": null,
  "driver_bot": "bot_xxx",
  "participants": [
    {"bot_uuid": "bot_xxx", "bot_name": "Coordinator", "role": "manager", "kind": "Bot", "mode": "auto"},
    {"bot_uuid": "bot_yyy", "bot_name": "DBA", "role": "worker", "kind": "Bot", "mode": "auto"}
  ],
  "group_strategy": "manager_worker",
  "group_kind": "normal",
  "dm_pair_key": null,
  "workspace": null,
  "service_group_uuid": null,
  "created_at": 1715700000000,
  "updated_at": 1715700000000
}
```

---

## 二、建 Session / 查 Session

### `POST /groups/{id}/sessions` — 创建 Session

```json
{ "session_kind": "chat", "session_title": "数据库性能审计" }
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_kind` | `"chat"` | 否 | 默认 chat |
| `session_title` | `string` \| `null` | 否 | 前端展示用 |

响应 201，返回 session 对象，`session_id` 格式 `{group_id}:{8_hex}`。

### `GET /groups/{id}/sessions` — Session 列表 / 搜索

默认只返回 running。**组内搜索**用 `?q=`，同时匹配 session_id 和 session_title。

```
GET /groups/{id}/sessions                     → running（默认）
GET /groups/{id}/sessions?status=completed    → 已完成
GET /groups/{id}/sessions?status=all          → 全部
GET /groups/{id}/sessions?q=数据库             → 组内搜索
GET /groups/{id}/sessions?offset=0&limit=20
```

视角过滤：正式成员见全部 session；临时参与者只见自己在 `participants` 里的 session。

### `GET /sessions/{sid}` — Session 详情

响应字段：`session_id` / `group_id` / `session_title` / `status` / `session_kind` / `participants` / `activation_count` / `input` / `output` / `error_message` / `callback_status` / `created_at` / `updated_at` / `completed_at`。

鉴权：caller 必须是 session 参与者或 group 正式成员，否则 403。

### `PATCH /sessions/{sid}` — 更新 Session 标题

```json
{ "session_title": "数据库性能审计" }
```

传 `null` 清空：`{ "session_title": null }`。不传 `session_title` 字段则无操作，直接返回当前 session。

---

## 三、查 Session 消息
### `GET /sessions/{sid}/messages`

BCS 不存消息，实际调 source bot 的 `chat.history`（session_key=sid）实时拿回。

```
GET /sessions/{sid}/messages
GET /sessions/{sid}/messages?view_bot_id={bot_uuid_or_human_uuid}
```

| `view_bot_id` | 行为 |
|---------------|------|
| 不传 | chat 群走 driver、manager_worker 群走 manager |
| Bot UUID | 调该 Bot 的 chat.history；必须是 caller 持有的 Bot |
| Human UUID | 回退到 strategy leader 的 chat.history；必须是当前登录 Human 自己 |

响应为消息数组，自己发的消息 `role="user"`、别人的 `role="assistant"`。Bot 不在线返回 `[]`。

---

## 四、给 Session 加人

动态成员变更统一走 Session 维度（不对 Group 成员做事后变更）。

### `PATCH /sessions/{sid}/members/{actor_id}` — 加入 / 改模式 ⭐ 加人入口

```json
{"mode": "present"}
```

| mode | 适用 | 说明 |
|------|------|------|
| `"auto"` | Bot | 默认，自动响应 |
| `"muted"` | Bot | 静默观察（收 inject） |
| `"present"` | Human | 在线参与 |
| `"absent"` | Human | 暂离 |

首次调用时 actor 不在 session.participants → 自动 first-insert（Human 以 Observer 身份加入）。


---

## 五、WS 对话请求

### `chat.send` 带 `session_id`

```json
{
  "type": "req",
  "id": "uuid",
  "method": "chat.send",
  "params": {
    "sessionKey": "main",
    "message": "你好",
    "group_id": "group_uuid",
    "bot_uuid": "human_100001",
    "session_id": "group_uuid:cafef00d"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | `string` | 否 | 不传时走 group 维度路由（兼容旧行为） |

**前端流程**：建群 → 建 session → WS connect / subscribe → chat.send（带 session_id）→ 后续每条消息带同一个 session_id → 新对话重新建 session。

---

## 错误码速查

| HTTP | error code | 场景 |
|------|-----------|------|
| 400 | `invalid_params` | 参数校验失败（role 不允许、mode 不合法等） |
| 401 | `unauthorized` | 缺少有效 token/cookie |
| 403 | `forbidden` | 非 driver 操作 / 非 session 参与者也非 group 成员访问 |
| 404 | `not_found` | group/session 不存在 |
