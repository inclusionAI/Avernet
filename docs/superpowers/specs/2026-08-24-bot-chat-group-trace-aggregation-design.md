# Bot Chats 按群 ID 关联 Trace 聚合

- **日期**：2026-08-24
- **状态**：approved
- **基准设计**：Bot Chats 详情抽屉“按群 ID 关联”设计稿
- **涉及模块**：TeamClaw 新前端、Backend Bot Logs、Gateway 路由（如现有配置需同步）
- **关联 Spec**：TeamClaw 新前端 `docs/specs/bot-chats/spec.md`
- **文档性质**：跨模块行为与契约设计。正式 API Contract 是实现权威来源。

---

## 1. 产品结论

Bot Chats 的“按群 ID”不是群消息聊天记录页面，也不是只查询当前 Bot 的 Trace。

其准确语义是：

> 用户打开一条属于 Group 的 Trace 详情后，切换到“按群 ID”，系统根据该 Trace 的 `group_id` 查询这个 Group 下所有 Session 产生的全部关联 Trace；结果包含群内所有 Bot 的 Trace，并在每条 Trace 上标明来源 Bot。选择任意关联 Trace 后，继续展示该 Trace 的 Timeline、Observation、Input 和 Output。

交互链路：

```text
打开当前 Trace 详情
  → 当前 Trace 提供 group_id
  → 切换“按群 ID”
  → 按 group_id 查询全群关联 Trace
  → 左栏展示所有来源 Bot 的 Trace
  → 点击任意 Trace
  → 中栏展示该 Trace Timeline
  → 右栏展示选中 Observation 详情
```

当前 Trace 只是进入 Group 关联视图的上下文，不限制查询结果只能属于当前 Bot。

---

## 2. 与设计稿对齐

设计稿明确要求以下页面结构：

```text
功能区
  按 sessionID | 按任务ID | 按群ID
  sessionID: 当前 Trace 的 sessionID
  群ID: 当前 Trace 的 group_id
  共 N 条关联 Trace

左栏：关联 TRACE（群ID）
  trace_006  来源 Bot C
  trace_005  来源 Bot B
  trace_004  来源 Bot A
  trace_003  来源 Bot B（当前选中）
  ...

中栏：TIMELINE
  当前：trace_003
  Trace / Span / Generation / Tool Observation 树

右栏：Observation 详情
  Observation 名称与类型
  时间、耗时
  来源 Bot
  Input
  Output
  Metadata / Token / 成本（按现有详情能力）
```

### 2.1 必须保留

- 左、中、右三栏 Trace 详情结构；
- 按 sessionID、按任务ID、按群ID三个关联维度；
- Group 左栏为关联 Trace，而不是聊天气泡或消息 Timeline；
- 每条 Group 关联 Trace 展示真实来源 Bot；
- 点击其他 Bot 的 Trace 后可查看该 Trace 的完整 Timeline 和 Observation；
- 当前 Trace 在左栏保持选中态；
- 关联总数使用后端 `total`，不能只显示当前页数组长度。

### 2.2 本期不做

- 不新增 Group Session 选择器；
- 不展示 BCS 原始 Human/Bot/System 群消息列表；
- 不把没有 Trace 的普通群消息补入左栏；
- 不新增聊天消息游标分页；
- 不把 Group 模式改造成 Workspace 群聊页面；
- 不要求新增 Group Message History API；
- 不使用前端 Mock 拼接其他 Bot 的 Trace。

---

## 3. 已确认的查询范围

### 3.1 Group 下所有 Session

只要目标 Bot 是该 Group 的当前正式成员，按群 ID 查询应覆盖：

- Group 下所有 Session；
- 目标 Bot 未参与的 Session；
- 目标 Bot 加入前已存在的 Session；
- 当前 Trace 所在 Session 之外的其他 Session。

Session Participant 关系不用于缩窄 Group 关联 Trace 的查询结果。

### 3.2 全部可用历史 Trace

Group 关联查询使用精确 `group_id` 和全历史范围：

```text
match_mode = exact
time_scope = all
```

“全部历史”指 Backend OTEL/legacy Trace 真源和 BCS Group-Session 映射中仍可查询到的数据；不要求恢复已删除、未上报或超过合法保留期的 Trace。

Group Trace 聚合只查询 Backend 的 Trace 日志和已有 Group-Session 关联表，不进入群消息读取链路，因此不涉及消息可见性、加入时间或消息 Owner Filter。

### 3.3 所有来源 Bot

Group 查询不能增加：

```text
trace.bot_id = 当前 bot_id
```

正确范围是：

```text
Trace.session_key 属于 group_id 映射出的任一 Group Session
```

结果可包含：

- 当前 Bot 的 Trace；
- Group 中其他 Bot 的 Trace；
- Manager Bot 的 Trace；
- 所有 Worker Bot 的 Trace。

Manager-Worker 场景不按 Worker 身份隔离 Trace。所有已写入 Backend 日志库、且 Session 属于目标 Group 的 Trace 都进入结果。

### 3.4 Trace 与群消息的边界

本功能聚合的是 Trace：

- Bot 执行有 Trace 时进入列表；
- Human 普通群消息如果没有 Trace，不进入列表；
- Bot 消息如果没有完成 OTEL 上报，也不伪造 Trace；
- Group 下“全部聊天记录”在本 Spec 中指全部可用关联 Trace，不等同于 BCS 原始消息历史。

---

## 4. 权限模型

Group 关联 Trace 的 Group 范围判定只依赖一个核心条件：

```text
Query 中作为查看上下文的当前 bot_id 是 group_id 的当前正式成员
```

通过后，该 Bot 获得该 Group 的关联 Trace 读取范围：

```text
Group 下所有 Session
+
这些 Session 的所有可用 Trace
+
这些 Trace 的完整详情和 Observation
```

不再按以下条件缩窄结果：

- Trace 是否由当前 Bot 产生；
- 当前 Bot 是否参与对应 Session；
- 当前 Bot 加入 Group 或 Session 的时间；
- Manager/Worker 身份；
- Trace 来源 Bot 的 Owner 是否与当前调用者相同。

Bot Owner 与 Bot Collaborator 通过同一当前 Bot 进入日志页时，Group 关联范围一致，不因 Owner/Collaborator 身份返回不同 Trace 集合。

仍需保留的最低安全边界：

1. 请求经过现有 Gateway 身份认证；
2. `bot_id`、`group_id` 和 Trace ID 必须真实关联，服务端不能只信任前端；
3. 当前 Bot 被移出 Group 后，后续 Group 关联请求失败；
4. Group 关联详情只能打开该 Group 查询结果范围内的 Trace；
5. 仅知道 `group_id`，但不能以该 Group 成员 Bot 建立查询上下文时，不得通过产品接口读取。

---

## 5. API 方案

### 5.1 关联查询使用现有 Backend Bot Logs 接口

本期不新增 Group History 接口，也不需要修改 BCS API。

Backend 已有以下关联 Trace 原子接口：

```http
GET /openapi/v1/bots/logs/sessions/{session_key}/traces
GET /openapi/v1/bots/logs/tasks/{biz_scene}/{biz_task_id}/traces
GET /openapi/v1/bots/logs/groups/{group_id}/traces
GET /openapi/v1/bots/logs/traces/{trace_id}
```

按群 ID 时继续复用原路由，仅增加可选查询上下文：

```http
GET /openapi/v1/bots/logs/groups/{group_id}/traces
    ?bot_id={当前日志页 bot_id}
    &user_id={当前用户}
    &owner_id={Bot 所有者，可选}
    &page=1
    &limit=100
```

选择左栏任意关联 Trace 后继续复用原详情路由，并可携带同一 Group 上下文：

```http
GET /openapi/v1/bots/logs/traces/{trace_id}
    ?bot_id={当前日志页 bot_id}
    &group_id={当前关联 group_id}
    &user_id={当前用户}
    &owner_id={Bot 所有者，可选}
```

`bot_id`、`group_id`、`user_id`、`owner_id` 均为向后兼容的可选参数。增强 Group 上下文中，`owner_id` 未传时使用 `user_id`，Backend 据此生成 BCS 成员身份 `bot_id:owner_id`：

- 新前端 Group 模式必须传入，用于落实“当前 Bot 属于 Group”以及“Trace 属于 Group”的校验；
- 老前端或既有调用方不传时，不产生 422，不改变原路由行为；
- 不修改 URL Path、HTTP Method、分页参数、Envelope 或 DTO 字段；
- 不创建 `/v2`，不复制新增一组 Group logs 接口。

新前端已经定义了这些路径的 `botLogController`，当前问题是 Group 关联流程仍在调用 Bot-scoped 产品接口，没有切换到对应的 logs 原子接口。

### 5.2 Group logs 接口语义

Backend Group logs 查询按以下逻辑执行：

```text
1. 根据 group_id 查询 Backend 本地 bcs_group_sessions 读模型；
2. 将 Group 下全部 session_id 规范化成日志 session_key；
3. 查询 Trace.session_key IN group_session_keys；
4. 不附加 Trace.bot_id = 当前 Bot；
5. 按 start_time DESC, trace_id DESC 稳定排序；
6. enrich 每条 Trace 的 bot_id、bot_name、group_id 和 session 信息；
7. 返回标准 SessionListResponse。
```

Backend 当前已有 `list_group_sessions()`、`OpenBotChatRepository.list_scope_traces()` 和 Group logs route，优先复用这些实现，不新增跨服务调用。

响应示例：

```json
{
  "sessions": [
    {
      "id": "trace_003",
      "bot_id": "BOT_002",
      "bot_name": "客服助手",
      "group_id": "group_001",
      "session_id": "sess_001",
      "session_key": "...",
      "timestamp": "2026-06-29T14:30:00Z",
      "status": "SUCCESS"
    }
  ],
  "total": 6,
  "page": 1,
  "limit": 100,
  "has_more": false
}
```

### 5.3 权限上下文与兼容模式

Group 关联视图从一条当前可查看、且携带 `group_id` 的 Trace 进入。Group 范围不再按照来源 Bot 的 Owner、Collaborator、Session Participant 或 Worker 身份过滤。

产品确认的核心条件仍是“当前日志页 Bot 属于该 Group”。Backend logs 只在调用方显式传入 Group 查询上下文时执行增强校验：

```text
列表：bot_id:(owner_id 或 user_id) 属于 path.group_id
详情：bot_id:(owner_id 或 user_id) 属于 query.group_id
     且 trace_id 对应 Trace 的 session_key 属于 query.group_id
```

兼容规则：

1. `GET /groups/{group_id}/traces` 不传 `bot_id` 时，保留现有 logs 调用语义；
2. `GET /traces/{trace_id}` 不传 `bot_id/group_id` 时，保留现有 logs 详情语义；
3. 增强 Group 上下文至少同时提供 `bot_id`、`group_id`、`user_id`；`owner_id` 可选，Collaborator 查看共享 Bot 时传真实 Owner；详情禁止只传部分上下文；
4. 普通 `/bots/{bot_id}/chats/**` 路由、参数、授权和返回完全不变；
5. 新前端必须使用增强上下文，老前端可以零改造继续运行；
6. 校验只在 Backend logs 边界读取现有 Group Membership 数据，不修改 BCS Contract 或 BCS 运行逻辑。

该兼容分支是已有调用方的迁移保护，不应被新前端当作绕过 Group 校验的降级路径。

### 5.4 不使用 Bot-scoped 接口做 Group 聚合

以下接口继续用于当前 Bot 的普通列表和详情：

```http
GET /openapi/v1/bots/{bot_id}/chats
GET /openapi/v1/bots/{bot_id}/chats/{trace_id}
```

它们带有 Bot scope，不适合作为“按群 ID”跨 Bot 关联 Trace 的数据源。Group Tab 应切换到 `/openapi/v1/bots/logs/**`；这样点击其他 Bot Trace 时也直接使用 logs detail，不需要放宽普通 Bot-scoped 详情接口。

---

## 6. Backend 改造

### 6.1 当前能力

Backend 已具备：

- `bcs_group_sessions`：Group 到 Session 的本地读模型；
- `list_group_sessions()`：将 Group Session 解析为日志 `session_key`；
- `OpenBotChatRepository.list_scope_traces()`：按 Session/Task/Group 查询非 Bot-scoped Trace；
- `/openapi/v1/bots/logs/groups/{group_id}/traces`：跨 Bot Group Trace 列表；
- `/openapi/v1/bots/logs/traces/{trace_id}`：Trace 完整详情；
- 上述路由可通过新增的可选查询上下文增强 Group 校验，同时兼容不传新参数的老调用方。

因此不需要新增 Group History Service，也不需要修改 BCS。

### 6.2 需要核对或修正的 Backend 内容

- Group logs route 在 Gateway 环境可访问；
- Group 查询覆盖 `bcs_group_sessions` 中该 Group 的全部 Session；
- 查询不附加 `bot_id`、Owner 或 Worker Filter；
- OCB Trace 和 legacy Trace 均按相同 Group Scope 查询；
- 列表 DTO稳定返回每条 Trace 的 `bot_id/bot_name`；
- `total/page/limit/has_more` 正确；
- Trace detail 能返回 Group 列表中任意 Trace 的 Timeline 和 Observation；
- Group 列表新增可选 `bot_id/user_id/owner_id` 查看上下文，传入时按 BCS `bot_id:owner_id` 身份校验该 Bot 属于 Path 中的 Group；
- Trace 详情新增可选的 `bot_id/group_id/user_id/owner_id` 查看上下文，传入时同时校验 Bot-in-Group 与 Trace-in-Group；
- 不传新增参数时保持既有 logs 行为，避免老前端和其他既有调用方回归；
- 校验只修改 Backend logs 授权边界并复用已有数据/能力，不扩展 BCS Contract。

### 6.3 明确不修改

- 不修改 BCS Application Service；
- 不修改 BCS HTTP Contract；
- 不修改 BCS Session 或 Message 查询；
- 不新增 Group Message History；
- 不修改 `visible_from_seq`、参与者历史或 Manager-Worker 消息逻辑；
- 不放宽普通 `/bots/{bot_id}/chats/**` 的 Bot scope。

---

## 7. 新前端改造

### 7.1 当前问题

新版前端当前存在以下偏差：

1. `botChatService.related(..., 'group')` 仍调用：

   ```text
   GET /openapi/v1/bots/{当前 bot_id}/chats?group_id=...
   ```

   而当前后端响应仍是 Bot-scoped，导致其他 Bot Trace 缺失。

2. 新前端虽已定义：

   ```text
   BOT_LOG_ENDPOINTS.groupTraces(group_id)
   ```

   但 Group 关联流程没有使用。

3. `BotChatSummary` 未保存 `botId/botName`。

4. `BotChatRelatedTraceList` 在 Group 模式下把每条 Trace 的来源固定渲染为页面当前 `botName/botId`，即使后端返回其他 Bot，也会标错来源。

5. 关联计数使用 `page.items.length`，设计稿要求显示后端 `total`。

### 7.2 必须修改

- Group Tab 按当前 Trace 的 `groupId` 发起全群 Trace 查询，并携带页面当前 `botId/userId/ownerId`；
- Domain Model 增加每条 Trace 的：

  ```text
  botId
  botName
  ```

- Mapper 映射 DTO 的 `bot_id/bot_name`；
- Group 左栏每条 Trace 使用该 Trace 自己的来源 Bot；
- 点击其他 Bot Trace 时，向 logs detail 同时传当前页面 `botId/userId/ownerId` 和关联锚点 `groupId`；
- 选中 Trace 后更新中栏 Timeline 和右栏 Observation；
- 保持 Group Tab 和当前选择，不能因选中其他 Bot Trace 自动退回 Session Tab；
- 总数显示 `page.total`；
- `has_more=true` 时提供加载更多或分页，不能固定只展示前 100 条；
- 当前 Trace 在返回结果中高亮；
- 无 `group_id` 时禁用“按群 ID”并显示原有提示。

### 7.3 不需要修改

- 不新增 Group Session 列表组件；
- 不新增群消息气泡组件；
- 不接入 Workspace Group Chat Provider；
- 不新增消息 Store；
- 不改变普通日志列表和筛选页；
- 不改变 Observation 树的主体展示结构。

---

## 8. 验收标准

### 8.1 Group Trace 范围

- [ ] 当前 Trace 有 `group_id` 时，“按群 ID”可用。
- [ ] 查询覆盖 Group 下所有 Session。
- [ ] 返回当前 Bot 和其他 Bot 的 Trace。
- [ ] 当前 Bot 未参与某 Session 时，该 Session 的 Trace 仍可返回。
- [ ] 当前 Bot 加入 Group 前的仍存 Trace 可以返回。
- [ ] Manager、所有 Worker 的 Trace 均可返回，不做 Worker 隔离。
- [ ] Group 查询不附加 `trace.bot_id = 当前 bot_id`。
- [ ] 当前 Bot 不属于 Group 或已被移出时，查询失败。

### 8.2 页面展示

- [ ] 左栏标题为“关联 TRACE（群ID）”。
- [ ] 左栏每条 Trace 展示真实来源 Bot 名称和 Bot ID。
- [ ] 关联总数使用后端 `total`。
- [ ] 当前 Trace 保持高亮。
- [ ] 点击其他 Bot Trace 后，中栏切换到该 Trace Timeline。
- [ ] 右栏显示该 Trace 选中 Observation 的 Input、Output、模型、耗时、Token 和成本。
- [ ] Group Tab 不被切换 Trace 的动作重置。
- [ ] 超过单页上限时可以继续加载。

### 8.3 权限与回归

- [ ] Group 上下文只要求当前 Bot 是 Group 正式成员，不按来源 Bot Owner/Worker 缩窄结果。
- [ ] Owner 和 Collaborator 通过同一 Bot 查看时返回一致的 Group Trace 集合。
- [ ] 非 Group 模式仍保持原有 Bot-scoped 列表和详情边界。
- [ ] 不能用 Group A 的上下文打开仅属于 Group B 的 Trace。
- [ ] 仅知道 Group ID 不能绕过产品上下文读取。

---

## 9. 测试要求

### 9.1 Backend

至少覆盖：

1. Group G 有 Session S1、S2；
2. Bot A 是 Group G 成员，但只参与 S1；
3. Bot B、Bot C 在 S1/S2 分别产生 Trace；
4. 以 Bot A 查询 Group G，返回 A/B/C 的全部 Trace；
5. 以 Bot A 和 Group G 上下文打开 Bot B Trace 详情成功；
6. 不带新增参数调用既有 logs 列表和详情时，HTTP 契约与历史行为不变；
7. Group 详情缺少 `bot_id`、`group_id`、`user_id` 中任一必需上下文时明确拒绝；
8. Bot A 被移出 Group 后，带 Group 上下文的列表和跨 Bot 详情失败；
9. Group G 上下文不能打开 Group H 的 Trace；
10. 普通 Bot 列表、详情、Session、Task 查询不回归；
11. OCB Trace 和 legacy Trace 路径行为一致；
12. 老前端既有请求不增加必填参数即可继续成功。

### 9.2 新前端

至少覆盖：

1. Group Tab 使用 `detail.groupId` 查询；
2. 返回多个 `bot_id` 时全部展示；
3. 每条 Trace 标签使用自身 `botName/botId`；
4. 点击其他 Bot Trace 携带 Group 上下文；
5. 切换 Trace 后保持 Group Scope；
6. `total` 与当前页长度不同时显示 `total`；
7. `hasMore` 时加载下一页并按 Trace ID 去重；
8. 当前 Trace 高亮；
9. 空态、401、无权、5xx 和重试可用；
10. Component → Hook → Service → Controller 分层不回归。

### 9.3 真实数据验收

不得只使用前端 Mock。至少写入或通过真实调用产生：

```text
Group G
  Session S1
    Bot A → Trace A1
    Bot B → Trace B1
  Session S2
    Bot C → Trace C1
    Worker D → Trace D1
```

从 Bot A 的 Trace A1 进入“按群 ID”后，应看到 A1、B1、C1、D1，并能依次打开完整 Trace 详情。

---

## 10. 实施顺序

1. Backend：验证现有 Group logs 列表和 Trace detail 的真实数据行为；
2. Backend：仅在发现缺口时修正 logs 查询、DTO 或 Gateway 暴露；
3. 新前端：Group Tab 改用 `botLogController.groupTraces()`；
4. 新前端：关联 Trace 详情改用 `botLogController.getBotTrace()`；
5. 新前端：映射并展示每条 Trace 的真实来源 Bot；
6. 新前端：对齐设计稿左中右布局、总数与分页；
7. 使用真实多 Bot Group Trace 完成 E2E。

---

## 11. 兼容与回滚

- 普通 Bot Chats 列表和详情保持 Bot-scoped；
- 新增查询参数全部可选，旧请求 URL 和调用方式继续有效；
- 不删除、不重命名、不改为必填任何既有参数；
- 不改变既有 Envelope、HTTP 状态码映射和响应主体结构；
- Session/Task 关联行为不在本 Spec 中扩大；
- 跨 Bot 聚合只发生在明确的 `/bots/logs/groups/{group_id}/traces` Group logs 接口；
- 现有 Trace/Observation DTO 仅补充或正确使用 `bot_id/bot_name`，不改动主体结构；
- 回滚时恢复新前端原关联调用即可，Backend 现有 logs 原子接口和其他日志能力不受影响；
- 不涉及 Group 消息数据迁移或消息数据库变更。
