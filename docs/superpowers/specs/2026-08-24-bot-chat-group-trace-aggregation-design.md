# Bot Chats 老接口按群 ID 聚合 Trace

- **日期**：2026-08-24
- **状态**：approved
- **涉及模块**：Backend Bot Chat、新 TeamClaw 前端
- **关联 Spec**：新前端 `docs/specs/bot-chats/spec.md`

## 1. 背景与目标

老前端的 Bot Chats 日志页支持三种关联维度：

- 按 sessionID
- 按任务ID
- 按群ID

新前端迁移时应复用老前端已经使用的 Backend 接口：

```http
GET /api/v1/bot-chats
```

本次工作的性质是“迁移 + 修复老接口的 Group 查询缺陷”，不是设计一套新的 Group Logs OpenAPI。

用户从一条包含 `group_id` 的 Trace 进入详情并切换“按群ID”后，页面应展示该 Group 下所有 Session 产生的全部可用 Trace，而不是只展示当前 Bot 或当前用户产生的 Trace。

## 2. 产品语义

Group 模式的查询边界是 `group_id`：

```text
group_id
  → Backend 本地 bcs_group_sessions
  → Group 下所有 session_id/session_key
  → 这些 Session 下的全部可用 Trace
```

确认的行为：

1. 查询 Group 下所有 Session；
2. 查询全部可用历史，不限制为默认 72 小时；
3. 不按当前 Bot、Trace owner、Manager/Worker 身份隔离；
4. Bot Owner 和 Bot Collaborator 看到相同的群关联 Trace 集合；
5. Human 普通群消息若没有对应 Trace，不伪造为 Trace；
6. 结果仍按现有 Trace DTO 返回，并展示每条 Trace 的真实 `bot_id`、`bot_name`、`group_id`、Session、Timeline 和 Observation。

当前 Trace 只用于提供进入 Group 视图所需的 `group_id`，不用于把结果限制在当前 Bot。

## 3. API 契约

### 3.1 不新增、不修改 OpenAPI Group Logs

本次不修改以下接口及其实现：

```http
/openapi/v1/bots/logs/**
```

具体包括：

- 不新增 Group Logs 路由；
- 不给 OpenAPI 路由增加 `bot_id`、`viewer_bot_id`、`user_id`、`owner_id` 或 `group_id` 参数；
- 不修改 OpenBotChatService、OpenBotChatRepository 或对应 Protocol；
- 不增加 App/Gateway Principal 权限依赖；
- 不修改 BCS。

### 3.2 复用老接口

Group 查询继续使用：

```http
GET /api/v1/bot-chats
    ?bot_id={当前页面 Bot ID}
    &group_id={目标 Group ID}
    &match_mode=exact
    &page=1
    &limit=100
```

兼容说明：

- `bot_id` 是老前端已有参数，继续允许传入；
- 当 `group_id` 存在时，Backend 不使用 `bot_id` 收窄 Trace 结果；
- 路由、HTTP Method、请求参数集合、响应 Envelope 和 DTO 均不变；
- 路由继续使用现有登录态 `get_current_user`，不切换到 App 认证；
- 老前端无需修改即可获得修复后的 Group 聚合结果。

非 Group 查询继续保持现有语义，例如：

```http
GET /api/v1/bot-chats?bot_id={bot_id}
GET /api/v1/bot-chats?session_key={session_key}
GET /api/v1/bot-chats?biz_scene={scene}&biz_task_id={task_id}
```

这些请求仍按现有 owner、Bot access、时间范围和过滤规则执行。

## 4. Backend 改造

### 4.1 改造边界

只调整 `BotChatService.list_sessions()` 对 `group_id` 模式的查询策略。现有 Router 和 Repository API 保持不变。

Group 模式：

```text
group_mode = group_id is not null
from_date = 1970-01-01T00:00:00Z
to_date = 当前时间
query_scope = OPEN
owner_id = null
bot_id = null
```

随后复用现有 DB 查询：

```text
BotChatService._list_sessions_db()
  → BotChatDbRepository.list_ocb_traces()
  → 无结果时 BotChatDbRepository.list_traces()
  → list_group_sessions(group_id)
  → Trace.session_key/session_id IN group_session_keys
```

`QueryScope.OPEN` 在这里只表示：Group 已经成为该次查询的聚合边界，因此 Repository 不再附加 Trace owner 或 Bot 条件。它不改变其他入口的默认 `QueryScope.OWNER`。

### 4.2 为什么忽略 Group 请求中的 bot_id

老前端进入日志页时已经选中了一个 Bot，因此历史请求会同时携带 `bot_id` 和 `group_id`。当前缺陷等价于：

```sql
WHERE trace.session_key IN (:group_sessions)
  AND trace.bot_id = :current_bot_id
```

修复后的语义是：

```sql
WHERE trace.session_key IN (:group_sessions)
```

`bot_id` 仍可作为页面上下文存在，但不属于 Group Trace 聚合条件。

### 4.3 Group 聚合页显式开启全历史

Group 查询虽然以 `group_id` 作为聚合边界并开放 Bot/Owner 范围，但不应让所有带 `group_id` 的请求都自动取消时间限制。日志详情页的关联 Trace 查询仍需遵守默认时间窗口或调用方传入的时间范围。

Group 视角聚合页必须显式传入：

```http
GET /api/v1/bot-chats?group_id={groupId}&match_mode=exact&time_scope=all
```

只有 `time_scope=all` 才查询完整 Group 历史；未传时继续使用老接口默认的最近 72 小时，或遵守调用方传入的 `from_date` / `to_date`。

该规则只作用于显式开启 `time_scope=all` 的 Group 聚合请求；其他模式继续保留：

- 默认最近 72 小时；
- 显式 `time_scope=all` 的既有校验；
- contains 查询最多 90 天的既有约束。

## 5. 权限与兼容性

本次不增加新的 Group 成员校验或 App 权限链路。

保留的边界：

- `/api/v1/bot-chats` 仍要求现有用户登录态；
- Group 聚合只读取 Backend 本地已有的 Group-Session 关系和 Trace；
- 不开放新匿名接口；
- 不改变 Trace 详情接口；
- 不改变非 Group 模式的 Bot owner/collaborator access check。

本次修复会同时改善老前端和新前端，因为两者使用同一个老接口。

## 6. 新前端迁移要求

新前端“按群ID”应直接调用：

```http
GET /api/v1/bot-chats?bot_id={currentBotId}&group_id={groupId}&match_mode=exact&time_scope=all&page={page}&limit=100
```

前端职责：

1. 从当前 Trace 详情取得 `group_id`；
2. Group Tab 激活时按精确 `group_id` 请求老接口；
3. 使用响应的 `sessions` 渲染左侧关联 Trace 列表；
4. 使用响应的 `total` 显示“共 N 条”；
5. 每条 Trace 展示真实来源 Bot；
6. 点击 Trace 后继续复用现有 Trace 详情能力展示 Timeline/Observation；
7. 不在前端按当前 `bot_id` 二次过滤 Group 结果；
8. 不调用 `/openapi/v1/bots/logs/groups/**`；
9. 不用 Mock 拼接群内其他 Bot 数据。

## 7. 验收标准

### 7.1 Group 模式

准备同一 Group 的两个 Session：

- Trace A：属于 Bot A、Owner A；
- Trace B：属于 Bot B、Owner B；
- 两条 Trace 均早于 72 小时。

调用：

```http
GET /api/v1/bot-chats?bot_id=bot-a&group_id=group-fixture&match_mode=exact&time_scope=all&page=1&limit=100
```

期望：

- 返回 Trace A 和 Trace B；
- 不调用 Bot A access check 来收窄 Group；
- 返回记录具有正确的来源 Bot 和 Group 标签；
- `total=2`；
- 老前端和新前端均可展示两条记录。

### 7.2 非 Group 回归

必须验证：

- 仅传 `bot_id` 时仍按 Bot 查询并执行原 access check；
- 仅传 session/task/普通列表参数时仍使用 owner scope；
- 非 Group 默认时间范围仍为最近 72 小时；
- OpenAPI `/openapi/v1/bots/logs/**` 相对 `origin/dev` 无代码和契约变化。

## 8. 非目标

- 不读取或返回 BCS 原始群消息；
- 不新增 Group History API；
- 不修改 Gateway；
- 不修改 BCS；
- 不修改 App Principal 认证；
- 不新增 Group participant 数据模型；
- 不做 Manager/Worker 隔离；
- 不重构无关 Bot Chat 模块。
