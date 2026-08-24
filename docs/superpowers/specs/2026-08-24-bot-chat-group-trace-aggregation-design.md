# Bot Chats 群组 Trace 聚合与跨 Bot 详情访问规范

- **日期**：2026-08-24
- **状态**：approved
- **涉及模块**：Avernet Backend Bot Chat、新 TeamClaw 前端、TeamClaw Gateway 路由
- **关联文档**：新前端 `docs/specs/bot-chats/spec.md`
- **文档性质**：跨模块行为、路由和权限契约；实现、测试和部署均以本规范为准。

## 1. 背景

TeamClaw 的 Bot Workshop 日志页展示的是 Bot Chat Trace，而不是 BCS 原始群消息。一个协作群可以包含多个 Bot；同一个群内的不同 Bot 可能分别产生多个 Session 和 Trace。

历史上存在两类接口：

- **Legacy Bot Chats API**：`/api/v1/bot-chats`，面向日志页和群组 Trace 聚合；
- **Bot-scoped OpenAPI API**：`/openapi/v1/bots/{bot_id}/chats...`，面向单 Bot 资源访问，有明确的 addressed-bot 约束。

新前端曾将日志页请求错误地切换到 Bot-scoped OpenAPI 路径，导致用户从 Bot A 进入群组后点击 Bot B 的 Trace 时，详情请求被当前 Bot 作用域拒绝或返回 404。即使列表已经能够聚合多个 Bot，详情仍可能因为使用错误路径或使用单 Bot 权限校验而失败。

## 2. 产品目标

当用户在某个 Group 下参与了任意 Bot 时，用户可以查看该 Group 下所有已落库的对话 Trace，包括其他 Bot 产生的 Trace。

完整用户链路：

```text
打开一个属于 Group 的 Trace
  → 读取当前 Trace 的 group_id
  → 按群 ID 查询关联 Trace
  → 展示群内所有 Bot 的 Trace
  → 点击任意 Trace
  → 展示该 Trace 的 Timeline / Observation / Input / Output
```

目标行为：

1. Group 是聚合边界，不是当前 Bot；
2. Group 结果不因入口 Bot、Trace owner 或 Manager/Worker 身份被收窄；
3. Group 内所有已参与并产生可关联 Trace 的 Bot 都能出现在结果中；
4. 详情列表项和详情内容必须保持同一套 Legacy API 语义；
5. 新前端所有日志请求通过 Gateway 暴露的 `/api/v1/bot-chats` 路径访问；
6. 不再从日志页调用 `/openapi/v1/bots/{bot_id}/chats` 或 `/openapi/v1/bots/logs/**`。

## 3. 术语与边界

### 3.1 Trace

Avernet 可查询的单次 Bot Chat 执行记录。Trace 至少包含 `trace_id`，并尽可能关联 `bot_id`、`bot_name`、`group_id`、`session_id`/`session_key`、时间线和观测数据。

### 3.2 Group session

Backend 本地 `bcs_group_sessions` 中记录的 Group 与 Session 关系。它是 Group Trace 聚合的索引边界，不等同于 BCS 原始消息存储。

### 3.3 参与 Bot

本规范不新增独立的 Group-Bot participant 表。当前实现以 Group 下已有的 Session/Trace 关系推断可展示的参与 Bot。未来如果需要在无 Trace 时展示 Bot，必须另立数据模型和兼容性设计。

### 3.4 Legacy API

本规范中的 Legacy API 指现有 Backend 路由：

```http
GET /api/v1/bot-chats
GET /api/v1/bot-chats/{trace_id}
```

“Legacy”只表示历史接口名称，不表示可以绕过 Gateway 或认证。

## 4. API 路由契约

### 4.1 新前端必须使用的路径

新前端日志页统一使用 Gateway 对外路径：

```http
GET /api/v1/bot-chats
GET /api/v1/bot-chats/{trace_id}
```

Gateway 应将上述路径转发到 Avernet Backend 对应路由。前端不得拼接 Bot-scoped 路径，也不得把 `bot_id` 编码到 URL path 中。

### 4.2 禁止使用的路径

以下路径不属于新前端群组日志契约：

```http
GET /openapi/v1/bots/{bot_id}/chats
GET /openapi/v1/bots/{bot_id}/chats/{trace_id}
GET /openapi/v1/bots/logs/**
```

Bot-scoped OpenAPI 接口可以继续服务其他单 Bot OpenAPI 调用，但不得被 Bot Workshop 群组日志页复用。

### 4.3 列表请求

普通列表：

```http
GET /api/v1/bot-chats
  ?bot_id={bot_id}
  &user_id={user_id}
  &page={page}
  &limit={limit}
```

Group 聚合列表：

```http
GET /api/v1/bot-chats
  ?bot_id={entry_bot_id}
  &group_id={group_id}
  &user_id={user_id}
  &match_mode=exact
  &include_output_match=false
  &from_date={iso_datetime}
  &to_date={iso_datetime}
  &page={page}
  &limit={limit}
```

约定：

- `group_id` 存在时，`bot_id` 仅保留为兼容的页面上下文，不得作为聚合过滤条件；
- `match_mode=exact` 时按完整 Group ID 匹配；
- `from_date`/`to_date` 由调用方明确传入时，Backend 遵守该时间范围；
- 如果页面需要完整历史，调用方使用现有 `time_scope=all` 约定，或传入覆盖完整历史的日期范围；
- 分页和 `total` 必须在 Group 聚合后的 Trace 集合上计算；
- 返回记录必须保留真实来源 `bot_id`、`bot_name` 和 `group_id`。

### 4.4 详情请求

```http
GET /api/v1/bot-chats/{trace_id}?user_id={user_id}&owner_id={optional_owner_id}
```

详情 URL 只包含 `trace_id`，不包含入口 Bot ID。入口 Bot ID 不能决定目标 Trace 的访问范围。

### 4.5 响应契约

列表和详情继续复用现有 Legacy API 的响应 Envelope 和 DTO，不新增前端专用响应格式。列表响应至少应保持 `sessions`、`total` 等现有字段；详情响应应保持现有 Timeline、Observation、Input、Output 和元数据结构。

错误语义保持现有契约：

- 参数非法：沿用现有 4xx 参数错误；
- Trace 不存在或不在当前用户可访问 Group 范围：返回现有 `4004`/not-found-or-not-accessible 语义；
- Gateway 未配置路由：应修复配置或部署，不通过前端回退到另一个语义不同的接口。

## 5. 权限模型

### 5.1 Group 模式

如果目标 Trace 能够通过其 `group_id` 解析到 Backend 的 Group-Session 关系，则访问判定为：

```text
当前用户拥有或协作任一该 Group 内的 Bot
    AND
目标 Trace 属于该 Group 下已关联的 Session/Trace 集合
```

通过后，用户可以查看该 Group 下其他 Bot 的 Trace 详情，不要求目标 Trace 的 `bot_id` 等于入口 Bot，也不要求目标 Trace 的 owner 等于当前用户。

### 5.2 非 Group 模式

无法解析到 Group 的 Trace，或请求没有 Group 上下文时，继续使用既有的 Bot owner/collaborator/user access 规则。不得因为本次 Group 规则而放宽普通单 Bot 查询。

### 5.3 认证与鉴权边界

- `/api/v1/bot-chats` 及详情接口继续要求现有用户登录态；
- Gateway 负责请求转发和已有认证上下文透传，不新增匿名访问；
- Backend 负责业务范围校验；
- OpenAPI App Principal 鉴权和 addressed-bot 校验不应被 Legacy 日志接口复用；
- 不在前端实现权限判断或通过隐藏 UI 代替 Backend 鉴权。

## 6. Backend 实现约束

### 6.1 列表

`BotChatService.list_sessions()` 在 `group_id` 模式下：

1. 以 `group_id` 查询 `bcs_group_sessions`；
2. 得到该 Group 关联的全部 `session_id`/`session_key`；
3. 查询这些 Session 下的全部可用 OCB Trace；
4. 必要时回退到已有 Langfuse/Trace 数据源；
5. 聚合、去重、排序、分页；
6. 返回真实来源 Bot 和 Group 元数据。

Group 模式不得附加当前 `bot_id`、当前 owner 或当前用户作为 Trace 集合过滤条件；这些字段只能用于入口上下文或权限校验。

### 6.2 详情

`BotChatService.get_session()` 必须先按 `trace_id` 找到 Trace，再按以下顺序校验：

1. Trace 能否解析到 Group；
2. 若能解析 Group，当前用户是否拥有/协作该 Group 内任一 Bot；
3. 若不能解析 Group，回退到既有单 Bot/owner 规则；
4. 通过后返回完整 Trace 详情。

详情不得调用 Bot-scoped OpenAPI service 来替代 Legacy service，也不得以请求中的入口 `bot_id` 强制比较目标 Trace 的 `bot_id`。

### 6.3 数据来源和一致性

- Group 关系以 Backend 本地 `bcs_group_sessions` 为准；
- 不把 BCS 原始群消息伪造为 Trace；
- 没有持久化 Trace 的消息不出现在 Trace 列表；
- 读取失败必须显式返回错误，不得吞掉数据库或持久化异常并返回空成功；
- Group 列表与详情必须使用同一访问模型，避免“列表可见、点击 404”。

## 7. Gateway 实现约束

Gateway 配置必须存在并验证以下路由：

```text
/api/v1/bot-chats             → Avernet Backend legacy bot chat list
/api/v1/bot-chats/{trace_id}  → Avernet Backend legacy bot chat detail
```

要求：

1. 支持 `OPTIONS` 预检和 `GET` 正常请求；
2. 不丢失 query string 中的 `group_id`、`user_id`、时间和分页参数；
3. 不把 `/api/v1/bot-chats/{trace_id}` 改写成 `/openapi/v1/bots/{bot_id}/chats/{trace_id}`；
4. Gateway 404 与 Backend 业务 4004 必须可区分地记录 request ID 和 upstream 状态；
5. 路由配置变更必须有配置校验或路由契约测试。

## 8. 新前端实现约束

### 8.1 Service 层

`src/services/backendApi/bots/botChatController.ts` 对外保留现有函数签名，内部路径固定为：

```text
listBotChats(...) → /api/v1/bot-chats
getBotChat(...)   → /api/v1/bot-chats/{trace_id}
```

如果为了兼容既有调用方仍保留 `botId` 参数，必须明确该参数不参与 URL path 构造，且在 Group 请求中不用于二次过滤。

### 8.2 页面层

- 从当前 Trace 详情读取 `group_id`；
- Group Tab 激活时发送精确 Group 查询；
- 不在前端删除其他 Bot 的返回项；
- 左侧列表展示每条 Trace 的真实 Bot 名称；
- 点击任意 Trace 使用同一个 Legacy 详情 controller；
- 处理 loading、empty、not-found 和 permission error；
- 不新增 mock 拼接群内数据。

### 8.3 构建与依赖

构建必须继续使用仓库已有命令：

```bash
npm run typecheck
npm run lint
npm run test -- --runInBand
npm run build
```

不得通过添加私有 registry、私有 URL、token 或 cookie 解决构建/运行问题。需要访问预发时，使用已有 `devs:pre`/环境配置和用户本地认证态。

## 9. 测试与验收

### 9.1 Backend 单元/集成测试

必须覆盖：

1. 同一 Group、不同 Bot、不同 owner 的两个 Trace，列表返回两条；
2. Group 请求携带入口 `bot_id` 时仍返回其他 Bot Trace；
3. Group 详情由入口 Bot 访问其他 Bot Trace 成功；
4. Group 详情由 Group 内协作 Bot 访问成功；
5. 不属于该 Group 的用户访问失败；
6. 无 Group 的 legacy Trace 继续走原单 Bot/owner 规则；
7. 非 Group 列表保留原有 Bot access 和时间窗口；
8. 分页、去重、排序和真实 Bot 元数据正确；
9. 数据库/Trace provider 读取异常不会被静默转换成空列表。

### 9.2 Gateway 契约测试

必须验证：

- `GET /api/v1/bot-chats` 能匹配并转发；
- `GET /api/v1/bot-chats/{trace_id}` 能匹配并转发；
- query 参数完整透传；
- `OPTIONS` 返回合法 CORS 预检响应；
- Gateway 不生成 `/openapi/v1/bots/.../chats` 改写；
- upstream 404、业务 4004、Gateway 404 的状态和日志可区分。

### 9.3 新前端测试

Controller 测试必须断言：

```text
listBotChats → /api/v1/bot-chats
getBotChat   → /api/v1/bot-chats/{encoded_trace_id}
```

页面测试至少验证：

- Group 列表请求带 `group_id` 和精确匹配参数；
- 返回多个 Bot 的 Trace 均渲染；
- 点击其他 Bot Trace 使用 `/api/v1/bot-chats/{trace_id}`；
- 前端没有 `/openapi/v1/bots/*/chats` 日志请求。

## 10. 兼容性、部署与排障

### 10.1 兼容性

- 老前端继续使用 `/api/v1/bot-chats`，无需改变其协议；
- 新前端切换到同一路径；
- Bot-scoped OpenAPI API 保持原语义，单 Bot 客户端不受影响；
- Group 详情权限是既有接口行为扩展，非 Group 行为保持兼容。

### 10.2 发布顺序

1. Avernet Backend 发布列表和详情权限实现；
2. Gateway 确认/发布 `/api/v1/bot-chats` 两条路由；
3. 新前端发布使用 Legacy Gateway 路径的版本；
4. 使用同一 Group 的跨 Bot Trace 做线上验证；
5. 观察 Gateway upstream status、Backend request ID 和 4004 比例。

### 10.3 诊断矩阵

| 浏览器 Request URL | 结论 |
|---|---|
| `/openapi/v1/bots/{bot}/chats...` | 前端仍使用旧错误 controller 或线上构建产物未更新 |
| `/api/v1/bot-chats` 返回 Gateway 404 | Gateway 路由未配置/未部署，或环境入口错误 |
| `/api/v1/bot-chats` 返回业务空列表 | Backend Group 查询、关系数据或时间参数需检查 |
| `/api/v1/bot-chats/{trace}` 返回业务 4004 | Backend 详情权限/Group 解析/部署版本需检查 |
| `/api/v1/bot-chats/{trace}` 返回 Gateway 404 | Gateway 详情路由或 path rewrite 配置问题 |
| `OPTIONS=200` 但 `GET` 失败 | CORS 已通过，继续检查 GET 的路由、认证和 upstream |

不能仅凭一次 404 判断“后端未部署”，必须同时核对浏览器实际 URL、Gateway 路由命中日志、upstream 状态和 Backend 版本。

## 11. 非目标

本次不做：

- 新增匿名日志接口；
- 新增独立 Group History API；
- 修改 BCS 原始群消息协议；
- 新增 Group participant 数据表；
- 修改 Bot-scoped OpenAPI 的单 Bot 语义；
- 在前端 mock 或拼接其他 Bot 数据；
- 重构与 Bot Chat 无关的模块；
- 把权限判断迁移到前端或 Gateway。

## 12. 风险与回滚

主要风险：

- 老数据缺少 `group_id` 或 Session 关系，导致无法使用 Group 详情权限；
- Group 关系表存在脏数据，造成结果遗漏或过度可见；
- Gateway 列表路由已部署但详情路由未部署，形成“能查列表、点详情 404”；
- 前端 CDN/构建产物未更新，源码路径正确但线上仍请求 OpenAPI 路径。

回滚策略：

- 前端可回滚到上一个稳定构建；
- Backend 可回滚 Group 聚合和详情权限变更；
- Gateway 路由变更应独立可回滚；
- 回滚不应删除已存在的 Trace 或 Group-Session 数据。

