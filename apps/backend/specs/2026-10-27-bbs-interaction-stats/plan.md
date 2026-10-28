# Plan: BBS 互动统计与个人中心

- 日期：2026-10-27
- Spec：`apps/backend/specs/2026-10-27-bbs-interaction-stats/spec.md`
- 状态：proposal
- 范围：`apps/backend` 的 BBS 互动事件、统计读取与个人中心；`apps/frontend-nextgen` 的 Community 页面交互

## Approach

以 append-only 事件表为核心新增一个 BBS 互动域：后端先建立 `ac_forum_interaction` 的 DDL / ORM / Repository / Service contract，再暴露统一的写入、单体 summary 与批量 summary 接口。个人中心不新建 Bot 归属模型，而是将当前登录 Human 与 `ac_forum_browse_subscription` 中该 owner 在当前租户、环境下的订阅 Bot 组成 actor set，再按 `actor_scope` / `activity_type` / `target_type` 做服务端分页聚合。前端在 Community 页面内新增个人中心视图与身份筛选框，并复用 summary/batch 避免 N+1。

实现按后端存储与协议、后端 HTTP、前端统计、前端个人中心、前端详情互动五个纵向切片推进。每个切片保持可编译、可测试，并先落 contract test 再落端到端表现。

## Affected Components

### Backend

- `community/core/task/forum/`
  - DDL：`sql/2026_10_27_forum_interaction.sql`
  - ORM：`repository/models.py`
  - Domain records / constants：新增 interaction 子目录
  - Service Protocol 与实现：新增 interaction、personal center、author stats contract
- `community/core/repository/`
  - Protocol：新增 forum interaction / author stats repository contract
  - Implementation：新增 forum interaction repository，并扩展统计聚合与个人中心 activity 查询
  - `README.md`：补充 Context Boundary
- `community/core/schema.py`
  - 注册 `ForumInteractionModel`
- `community/di/modules/task_module.py`
  - 绑定新增 protocols / services / repositories
- HTTP：
  - `community/adapters/http/openapi_v1/task/bbs/`
  - `community/adapters/http/task/bbs/`
  - 两个 surface 共享同一 Service API，只做 schema、校验、错误映射
- Tests：
  - `apps/backend/tests/community/repository/task/forum/`
  - `apps/backend/tests/community/core/task/forum/`
  - `apps/backend/tests/community/adapters/http/openapi_v1/task/bbs/`
  - `apps/backend/tests/community/adapters/http/task/bbs/`
  - `apps/backend/tests/community/architecture/`
  - DI / framing / endpoint coverage 相关测试

### Frontend

- API：
  - `services/backendApi/community/interactionController.ts`
  - `services/backendApi/community/personalCenterController.ts`
- Domain：
  - `domain/community/types.ts`
  - `domain/community/mapper.ts`
  - `domain/community/constants.ts`
- Gateway / service：
  - `services/community/communityGateway.ts`
  - `services/community/communityApiGateway.ts`
  - `services/community/communityService.ts`
- Hooks / store：
  - `hooks/useCommunityInteractions.ts`
  - `hooks/usePersonalCenter.ts`
  - `stores/communityStore.ts`
- Components：
  - `components/Community/CommunityPage/index.tsx`
  - 新增 `components/Community/CommunityPersonalCenter/`
  - 新增 `components/Community/TopicInteractionBar/`
  - 修改 `components/Community/CommunityTopicDetail/`
  - 修改 `components/Community/CommunityAuthorAvatar/`
- Tests：
  - `apps/frontend-nextgen/test/hooks/`
  - `apps/frontend-nextgen/test/components/Community/`
  - `apps/frontend-nextgen/test/services/community/`

## Data Model Changes

新增 additive 表：

```text
ac_forum_interaction
```

对应 DDL 文件：

```text
apps/backend/src/agentclaw/community/core/task/forum/sql/2026_10_27_forum_interaction.sql
```

必须保持 spec 中的约束：

- append-only 交互事件表
- 除 `id` 主键外，不建业务唯一键
- 不建立 `actor + action + target` 的唯一约束
- 不新增租户列；环境隔离由 `env` 承担
- 不新增 `dedupe_date` 或按天聚合字段
- 不新增取消状态、状态列、active 列
- `actor_id` / `target_id` 使用 `utf8mb4_bin`
- 读取索引分别为 target 与 actor 前置

ORM 新模型：

```python
class ForumInteractionModel(Base):
    __tablename__ = "ac_forum_interaction"
```

同时更新 `core/schema.py` 的 ORM side effect import，确保启动建表与 DDL 契约一致。

## API / Interface Changes

### Service API

新增 transport-agnostic contracts：

```python
ForumInteractionServiceProtocol
    record_interaction(...)
    get_summary(query)
    get_summary_batch(items)

ForumInteractionTargetVerifierProtocol
    verify_topic(...)
    verify_post(...)
    verify_human(...)
    verify_bot(...)

ForumAuthorStatsServiceProtocol
    get_author_stats(...)
    get_author_stats_batch(...)

ForumPersonalCenterActivityServiceProtocol
    get_personal_center_activities(...)
```

Domain records：

```text
ForumInteractionRecord
ForumInteractionSummaryQuery
ForumInteractionSummaryItem
ForumInteractionSummaryResult
ForumStatsRecord
ForumAuthorStatsRecord
PersonalCenterActivityQuery
PersonalCenterActivityPage
PersonalCenterActivityRecord
```

契约要点：

- 写入接口只追加事件；同一 actor 对同一 target 可多次写入。
- summary 由参数决定 scope：
  - 只传 actor：`ACTOR`
  - 只传 target：`TARGET`
  - 同时传 actor + target：`ACTOR_TARGET`
- summary batch 最多 100 个 item，整批校验，响应保持请求顺序。
- 不引入 `viewer_author_type` / `viewer_author_id` / `viewer_interactions`。
- 个人中心的 Bot 来源是 `ac_forum_browse_subscription`，不是 `ac_bots.owner_id`。

### Repository API

新增 repository contract 与 implementation：

```python
ForumInteractionRepositoryProtocol
ForumAuthorStatsRepositoryProtocol
ForumPersonalCenterActivityRepositoryProtocol
```

核心方法：

```text
insert_interaction(...)
get_summary(...)

batch_get_actor_summaries(actor_ids)
batch_get_target_summaries(target_refs)
batch_get_actor_target_summaries(pairs)

count_author_topics(...)
count_author_posts(...)
count_author_accepted_helpful_posts(...)

list_personal_center_activities(...)
```

实现约束：

- 所有互动查询必须带当前 `env`；该表按 spec 首版不包含租户列，不得为兼容旧 guard 临时加入租户过滤。
- `target-only` 统计必须按 `(target_type, target_id)` fence。
- `actor-only` 身份统计必须同时按 `author_type + author_id` fence。
- pair 查询必须同时过滤 actor 与 target，不能混入其他维度。
- batch 按 ACTOR / TARGET / ACTOR_TARGET 三类分组执行，再按请求下标还原。
- 重复 item 可复用查询结果，但响应长度和顺序保持不变。
- 个人中心 activity 必须服务端分页与排序，不允许后端逐 actor 查询后在 Python 中拼接分页结果。

### HTTP API

两个 surface 暴露一致契约：

```http
POST /openapi/v1/collaboration/tasks/bbs/interactions
POST /api/v1/collaboration/tasks/bbs/interactions

GET /openapi/v1/collaboration/tasks/bbs/interactions/summary
GET /api/v1/collaboration/tasks/bbs/interactions/summary

POST /openapi/v1/collaboration/tasks/bbs/interactions/summary/batch
POST /api/v1/collaboration/tasks/bbs/interactions/summary/batch

GET /openapi/v1/collaboration/tasks/bbs/personal-center/activities
GET /api/v1/collaboration/tasks/bbs/personal-center/activities
```

HTTP adapters 不实现领域状态校验，只做：

- schema 解析
- enum / 参数完整性校验
- 当前登录身份与 `owner_user_id` 一致性校验
- Service domain error → HTTP error 映射
- response envelope

## Milestone 1：DDL / ORM 与最小 Repository

### Tasks

1. 新增 `2026_10_27_forum_interaction.sql`。
2. 新增 `ForumInteractionModel`：
   - `id`
   - `actor_type`
   - `actor_id`
   - `action_type`
   - `target_type`
   - `target_id`
   - `env`
   - `gmt_create`
   - `gmt_modified`
3. 在 `core/schema.py` 注册模型。
4. 新增 `ForumInteractionRepositoryProtocol`。
5. 新增 `ForumInteractionRepository`：
   - `insert_interaction`
   - 单体 ACTOR / TARGET / ACTOR_TARGET summary
   - batch summary
6. 更新 repository Context Boundary 文档与 DDL/ORM contract test。

### Key Files

```text
apps/backend/src/agentclaw/community/core/task/forum/sql/2026_10_27_forum_interaction.sql
apps/backend/src/agentclaw/community/core/task/forum/repository/models.py
apps/backend/src/agentclaw/community/core/schema.py
apps/backend/src/agentclaw/community/core/repository/protocols/task/forum_interaction.py
apps/backend/src/agentclaw/community/core/repository/implementations/task/forum/forum_interaction_repository.py
```

### Tests

```text
tests/community/repository/task/forum/test_forum_interaction_repository.py
tests/community/core/task/forum/test_forum_interaction_ddl_contract.py
```

必测：

- 插入合法事件。
- 同一 actor + target 可重复插入。
- summary batch 包含多条与重复 item 时仍按请求顺序返回。
- ACTOR / TARGET / PAIR 不互相混数。
- SQL 失败抛错，不静默成功。

## Milestone 2：Domain contract 与 target verifier

### Tasks

1. 新增 forum interaction domain package：
   ```text
   community/core/task/forum/interaction/
   ```
2. 定义 constants、records、exception mapping 与 matrix：
   - `VIEW`
   - `LIKE`
   - `FAVORITE`
   - `FOLLOW`
   - `ACCEPT`
3. 实现 `ForumInteractionService`：
   - 参数规范化
   - actor/action/target enum 校验
   - target matrix 校验
   - `target_id` 空值、空串与长度校验
   - target verifier 调用
   - insert interaction
4. 实现 target verifier protocol，由 DI 在 composition root 选择具体实现。
5. 为 `ACTOR_TARGET` / actor-only / target-only 增加 summary service。
6. 在同一个 service edge 上实现 batch summary，整批失败时无部分成功。
7. 新增或更新 `core/task/forum/interaction/README.md`，声明 provides / consumes / internal dependencies。

### Matrix invariants

```text
VIEW      TOPIC POST
LIKE      TOPIC POST
FAVORITE  TOPIC POST
FOLLOW    TOPIC POST HUMAN BOT
ACCEPT    POST
```

不允许的组返回 `422` / `400` 领域校验错误；target 不存在返回 `404`。

### Tests

```text
tests/community/core/task/forum/interaction/test_forum_interaction_service.py
tests/community/core/task/forum/interaction/test_forum_stats_service.py
```

必测：

- 非法 actor / action / target type 被拒绝。
- HUMAN / BOT target 拒绝 VIEW / LIKE / FAVORITE / ACCEPT。
- HELP Topic 的一级 POST 才能被 accept，且 actor 必须为 Topic 作者。
- target verifier 不可用时失败，不降级为文本猜测。
- batch 长度、参数完整性、请求顺序、重复 item。
- actor-only 的 view/like/favorite 保持 0，target-only 的生产统计保持 0，避免两个方向语义混淆。

## Milestone 3：ACCEPT 与 helpful 的同事务一致性

### Tasks

1. 将现有 helpful 状态更新与 `ACCEPT` 事件写入放进同一个 repository transaction。
2. 建议实现方式：
   ```python
   update_post_helpful(...)
   ```
   改为或补充一个显式 repository 方法：
   ```python
   update_post_helpful_with_accept(...)
   ```
3. 继续由 service 层决定：
   - Topic 是否 HELP
   - actor 是否 Topic 作者
   - post 是否一级回复
4. Repository 只做一个事务内：
   - 取 Topic / Post
   - 更新 `post_config.helpful`
   - append `ForumInteractionModel(action_type=ACCEPT, target_type=POST, target_id=post_id)`
5. 不在 service 层先更新 helpful 后调用另一个 repository 的独立 insert。
6. 清除 helpful 不产生反向事件，也不消除已有 ACCEPT 事件。

### Key Files

```text
community/core/task/forum/services/forum_service.py
community/core/repository/protocols/task/forum.py
community/core/repository/implementations/task/forum/forum_repository.py
community/core/repository/implementations/task/forum/forum_interaction_repository.py
```

### Tests

- helpful false → true 产生一条 ACCEPT。
- helpful true → false 不产生事件。
- 再次置 true 不重复产生 ACCEPT。
- 请求者不是 Topic 作者时返回 `403`。
- Post 为子回复时拒绝。
- 非 HELP Topic 拒绝。
- event insert 失败时 post_config 不提交。

## Milestone 4：HTTP contract 与 DI wiring

### Tasks

1. 新增 HTTP schema：
   ```text
   openapi_v1/task/bbs/interaction_schemas.py
   openapi_v1/task/bbs/personal_center_schemas.py
   ```
2. 在 openapi router 添加：
   - `POST /interactions`
   - `GET /interactions/summary`
   - `POST /interactions/summary/batch`
   - `GET /personal-center/activities`
3. 在 unified router 中镜像同一 schema 与 contract 语义。
4. 统一 mapping：
   - validation error → `400/422`
   - forbidden → `403`
   - not found → `404`
   - persisted event → `201`
   - summary read → `200`
   - batch read → `200`，不返回 `201`
   - repository/db error → `5xx`，不返回成功事件
5. summary batch 与单体 summary 复用同一个 Service API；单体可以作为长度 1 的 batch 退化实现。
6. `personal-center/activities` 校验 `owner_user_id` 与当前登录人一致。

### DI

在 `task_module.py` 中注册：

- `ForumInteractionRepositoryProtocol`
- `ForumInteractionServiceProtocol`
- `ForumInteractionTargetVerifierProtocol`
- `ForumAuthorStatsRepositoryProtocol`
- `ForumAuthorStatsServiceProtocol`
- `ForumPersonalCenterActivityServiceProtocol`
- `ForumPersonalCenterActivityRepositoryProtocol`
- dependencies 保持 `singleton` 与既有 module policy 一致

### Key Files

```text
community/adapters/http/openapi_v1/task/bbs/router.py
community/adapters/http/openapi_v1/task/bbs/schemas.py
community/adapters/http/openapi_v1/task/bbs/interaction_schemas.py

community/adapters/http/task/bbs/router.py
community/adapters/http/task/bbs/schemas.py
community/adapters/http/task/bbs_routes.py

community/di/modules/task_module.py
```

### Tests

```text
apps/backend/tests/community/adapters/http/openapi_v1/task/bbs/test_interaction_endpoints.py
apps/backend/tests/community/adapters/http/openapi_v1/task/bbs/test_personal_center_endpoints.py
apps/backend/tests/community/adapters/http/task/bbs/test_interaction_endpoints.py
apps/backend/tests/community/adapters/http/task/bbs/test_personal_center_endpoints.py
```

必测：

- 两个 HTTP surface 的路径、schema、错误模型一致。
- batch 上限 100，empty items 返回 `400`。
- 半边身份返回 `400`。
- viewer legacy query 返回未知参数 `400`。
- persisted interaction 返回 `201`。
- summary read 不写事件。
- path 与 endpoint coverage 新增到 tests 进度文件。

## Milestone 5：Personal Center activity service

### Tasks

1. 新增 `ForumPersonalCenterActivityServiceProtocol`。
2. 活动类型与目标类型：
   - `AUTHORED` → Topic
   - `FAVORITED` → Topic
   - `VIEWED` → Topic
   - `LIKED` → Topic / Post
   - `REPLIED` → Topic / Post
3. 身份范围：
   - `HUMAN`
   - `BOT`
   - `ALL`
4. Bot 权威来源：
   ```sql
   SELECT bot_id
   FROM ac_forum_browse_subscription
   WHERE avernet_tenant = :current_avernet_tenant
     AND env = :current_env
     AND owner_user_id = :owner_user_id;
   ```
5. 不按 `mode` 过滤，也不读 `ac_bots.owner_id`。
6. 每个结果 item 必须带：
   - `activity_type`
   - `actor_type`
   - `actor_id`
   - `target_type`
   - `target_id`
   - `topic_id`
   - optional `post_id`
   - `happened_at`
   - `body_preview`
7. 去重规则：
   - VIEW / FAVORITE / LIKE 同一 actor + target 只展示一行，按最近时间排序。
   - AUTHORED 同一 Topic 只展示一行。
   - REPLIED 保留每条 Post，不按 Topic 去重。
8. `activity_type=ALL` 使用后端 UNION / aggregate 统一分页，不在 Python 侧将五个完整列表拼接后再截取当前页。
9. service 从 `ForumServiceProtocol.list_subscriptions(owner_user_id=...)` 读取订阅 Bot，实现层最终落到同一 repository；不要通过 HTTP 调用自身接口。

### Repository queries

- 每个 activity source 用 SQL/ORM 查询生成统一投影。
- 排序字段统一映射到 `happened_at` 与稳定 tie-break key。
- 结果行分页由 SQL limit / offset 或等价数据库投影完成。
- `actor_ids` 使用参数化 `IN`。
- 不引入 actor 明细导出，不提供全站用户行为查询。

### Tests

```text
tests/community/core/task/forum/personal_center/test_personal_center_activity_service.py
tests/community/repository/task/forum/test_personal_center_activity_repository.py
```

必测：

- HUMAN / BOT / ALL 构造正确 actor set。
- 当前 owner、tenant、env 之外的订阅行不混入。
- framework / openclaw 订阅均参与。
- `AUTHORED / FAVORITED / VIEWED` 只返回 Topic。
- `LIKED / REPLIED` 的 TOPIC / POST 混合分页正确。
- de-dup 规则正确。
- 任意分页查询不逐 actor 循环查询。
- 空订阅 Bot 页面仍有 HUMAN 行，不 default 到所有 Bot。

## Milestone 6：Topic / 作者统计服务与既有 Topic/Post response

### Tasks

1. 新增 `ForumAuthorStatsServiceProtocol` 查询每个身份：
   - topic_count
   - reply_count
   - accepted_reply_count
2. `follow_count` 由 interaction repository 汇总。
3. Topic 附带 Topic-specific fields：
   - `reply_participant_count`
   - `helpful_reply_count`
4. 保持既有 Topic / Post fields 不变：
   - 不替换既有 `reply_count`
   - 不重定义 `analysis.poll_stats`
5. 详情页统计与列表统计通过 `summary/batch` 完成：
   - Topic target summary
   - current page Post target summaries
   - current page author actor summaries
   - 当前用户与当前内容的 ACTOR_TARGET summaries
6. 不修改 Topic/Post 0/1 状态，不将 summary 修改权限逻辑加入 HTTP adapter。

### Tests

```text
tests/community/core/task/forum/interaction/test_forum_author_stats_service.py
tests/community/adapters/http/openapi_v1/task/bbs/test_bbs_topic_summary_integration.py
```

## Milestone 7：Frontend API service 层

### Tasks

1. 新增 interaction API controller 方法：
   - `createInteraction`
   - `getInteractionSummary`
   - `getInteractionSummaryBatch`
2. 新增 personal center API controller 方法：
   - `getPersonalCenterActivities`
3. 在 domain types / mapper 中定义：
   - `InteractionActionType`
   - `InteractionTargetType`
   - `InteractionSummaryQuery`
   - `InteractionSummary`
   - `ForumActorRef`
   - `ForumTargetRef`
   - `PersonalCenterActivity`
4. 更新 `communityGateway.ts`、`communityApiGateway.ts`、`communityService.ts`，不直接让组件嗅探 DTO。
5. 复用既有 `CommunityEnvelope` / `BackendApiPage` 形态，不额外抽象多个 HTTP client。

### Tests

```text
test/services/community/test_interaction_controller.ts
test/services/community/test_personal_center_controller.ts
test/services/community/test_community_api_gateway.ts
```

## Milestone 8：Frontend Topic 互动与详情页统计

### Tasks

1. 在 `CommunityTopicDetail` 新增 TopicInteractionBar：
   - 点赞
   - 收藏
2. 打开 Topic 详情时自动：
   - 提交 VIEW
   - 再通过 `summary/batch` 或单体 summary 刷新 Topic target 统计
3. 页面加载详情并取得可见 replies 时用一次 batch summary 读取：
   - Topic target
   - 已见 Post targets
   - 当前页作者 actor stats
   - 当前 viewer 与 Topic 的 ACTOR_TARGET stats
4. LIKE / FAVORITE 请求进行中仅禁用当前按钮。
5. 请求失败时显示提示并保留最近服务端 summary，不进入乐观持久状态。
6. 不把按钮渲染成 already-liked / already-favorited 状态。
7. 在头像 hover 卡片中通过 `CommunityAuthorAvatar` 包一层 Popover/Tooltip：
   - load author-only summary
   - 显示关注者、发帖数、回帖数、被采纳回帖数  
   - 显示 Follow 按钮，但 hover 自己时隐藏
8. Follow 成功后重读被 hover 身份的 actor summary，不将按钮改为 stateful toggle。
9. 同一页面相同作者只维护一个 summary cache。
10. 所有多对象请求使用 batch；超过 100 个 item 按当前页或楼层分组。

### Key Files

```text
components/Community/TopicInteractionBar/index.tsx
components/Community/CommunityTopicDetail/index.tsx
components/Community/CommunityAuthorAvatar/index.tsx
hooks/useCommunityInteractions.ts
store/communityStore.ts
```

### Tests

```text
test/components/Community/TopicInteractionBar.test.tsx
test/components/Community/CommunityAuthorAvatar.test.tsx
test/hooks/test_useCommunityInteractions.ts
```

必测：

- Topic open 前后统计更新。
- LIKE / FAVORITE 成功后不产生持久状态。
- request in flight 时 disabled。
- 失败后不增加本地计数。
- batch 请求只发出一次。
- hover 作者后 follow_count 以服务端响应为准。
- 自己头像不出现 Follow。

## Milestone 9：Frontend Personal Center

### Tasks

1. `CommunityPage` 的个人中心按钮由 disabled 改为可点击。
2. 新增独立 `CommunityPersonalCenter` view。
3. 顶部统计卡展示 aggregate values：
   - followers
   - topics
   - replies
   - accepted replies
4. 新增身份筛选框：
   - `个人身份` → `actor_scope=HUMAN`
   - `名下 Bot` → `actor_scope=BOT`
   - `全部` → `actor_scope=ALL`
   - default: `全部`
5. Bot 列表来源：
   - `GET /openapi/v1/collaboration/tasks/bbs/browse-subscriptions?owner_user_id=...`
   - 分页读取到 total
   - 不调用 `useOwnedBots`
6. 统计卡 actor items：
   - 更新统计后调用 summary/batch，一次请求收集 identity stats
7. 列表页调用 personal-center activity endpoint，5 个 tab：
   - 我的发布
   - 我的收藏
   - 我的浏览
   - 我的点赞
   - 我的回复
8. 每个列表共享页级 target filter。
9. 筛选框变化时：
   - 列表页重置到第一页
   - 重新请求 summary 与 activity 列表
10. 列表行展示身份标识：
    - `HUMAN` / `BOT`
    - stable `actor_id`
11. POST item 点击后进入关联 Topic detail。
12. 不增加 fan list / follow 管理页面。

### Key Files

```text
components/Community/CommunityPersonalCenter/index.tsx
components/Community/CommunityPersonalCenter/StatsCards.tsx
components/Community/CommunityPersonalCenter/ActivityList.tsx
components/Community/CommunityPersonalCenter/model.ts
hooks/usePersonalCenter.ts
services/backendApi/community/personalCenterController.ts
services/community/communityGateway.ts
services/community/communityApiGateway.ts
services/community/communityService.ts
domain/community/types.ts
```

### Tests

```text
test/components/Community/PersonalCenter.test.tsx
test/hooks/usePersonalCenter.test.tsx
test/services/community/test_personal_center_api_gateway.ts
```

必测：

- 身份筛选框默认选中 `全部`（`ALL`）。
- 切换筛选只改变对应 identity set。
- 选择 `名下 Bot` 时不读取 `useOwnedBots` 或 `ac_bots.owner_id`。
- 切换筛选后 activity page = 1。
- 列表行包含 actor_type / actor_id。
- Personal Center 统一调用 activity / summary batch 后端接口，不逐 identity 调用独立的 CRUD service 取 count。
- 5 类 activity 列表与 target filter 正确。
- Topic / Post 混合列表点击正确路由回 Topic。

## Milestone 10：契约、回归与验收

### Focus backend commands

```bash
cd apps/backend && uv run pytest \
  tests/community/repository/task/forum \
  tests/community/core/task/forum \
  tests/community/adapters/http/openapi_v1/task/bbs \
  tests/community/adapters/http/task/bbs \
  -q
```

若改动 DI、HTTP framing 或 schema registration，追加：

```bash
cd apps/backend && uv run pytest \
  tests/community/di \
  tests/community/framework \
  tests/community/contracts -q
```

若改动 BBS service / repository architecture boundary，补充执行 architecture tests 对应 focused 目录。  
若改动 core service / repository implementation，应执行 backend SAST / lint gate。

### Frontend tests

按项目既有 Jest 测试配置由近到远执行以下范围的测试：`services/community`、`hooks/useCommunityInteractions`、`hooks/usePersonalCenter`、`components/Community/PersonalCenter`、`components/Community/CommunityTopicDetail`。

### End-to-end / Release tests

- OpenAPI 与 unified route contract coverage 增项，并更新 endpoint / framework coverage progress。
- DI integration test 证明新增 endpoints 在真实 module wiring 下可解析。
- Frontend page tests 证明 interaction summary 不产生 N+1 请求。
- Singlebox acceptance guide 增加一组 BBS interaction stories，保持 BCS coverage baseline 不受影响。
- PR change description 写明未运行的 heavy gate 与原因，不得只以 focused tests 冒充全量发布证据。

## Rollout

1. Backend additive DDL 上线，不加在既有 Topic / Post DDL 后面。
2. 确认 schema / DDL / ORM contract tests 全部通过。
3. 部署 Backend interaction repository、services 与 HTTP surface。
4. 部署 Frontend Topic interaction feature。
5. 部署 Personal Center 与 subscription-based Bot source feature。
6. 运行 openapi / api contract tests；若该变更纳入 singlebox deployment gate，再执行 singlebox acceptance。
7. 按沿用 spec 的 rollback path：
   - frontend stop calling interaction endpoints
   - backend stop routing new endpoints
   - additive table can remain or be dropped separately
   - no rewrite of existing Topic / Post / poll / helpful data

## Rollback

- 停止前端 interaction 写入与 Personal Center 入口。
- 停用或移除新增 interaction HTTP routing。
- 停止 HTTP adapters 返回新增 schema mapping。
- `ac_forum_interaction` 数据默认保留用于审计；确需 drop 时必须走显式数据库回滚流程。
- 保持既有 Topic / Post / poll / helpful 数据不被重写或迁移。
- 采用可回滚的 additive 发布；schema/table 部署与功能开关解耦。

## Risks & Mitigations

1. **对 actor/target 统计数量口径混淆**  
   在 service tests 中分别覆盖三种 scope，并通过 HTTP contract tests 固定 batch 顺序、长度与错误行为。
2. **ACCEPT 事件与 helpful status 事务不一致**  
   必须使用同一 repository transaction，并加 failure injection test，不允许状态更新后直接 append。
3. **personal center Bot 来源误用 `ac_bots`**  
   DI / repository tests 固定 `ac_forum_browse_subscription` 读数，并明确 framework / openclaw 均参与。不读取 `useOwnedBots`。
4. **activity 聚合分页不正确**  
   全部 activity 使用统一服务端 pagination，repository test 检查每页 total、排序和 dedupe。
5. **前端产生 N+1 请求**  
   Topic detail 与 Personal Center 均以 summary/batch 为主，测试固定首轮请求数量。
6. **同一 actor 可重复点赞的语义与用户预期冲突**  
   前端仅在请求进行中禁用按钮，按钮不渲染长期已点赞状态；spec 明确本轮不做取消/幂等。
7. **Topic level 与 Post level 采纳统计混用**  
   服务返回 Post target 的 `accept_count`，Topic target 的 `helpful_reply_count` 独立；前端必须按 target type 选择字段。
8. **Core HTTP coupling**  
   HTTP adapters 仅依赖 Service Protocol，不直接 import repository；architecture focused test 用 imports / boundary tests 固定。

## Out of Scope

- 取消点赞 / 取消收藏 / 取消关注
- 全局幂等 key
- `dedupe_date` / 按天聚合计数
- 用户 profile edit
- follow management / fan list
- cross-tenant stats
- recommendation
- generic analytics / data warehouse
- `SHARE` / `MENTION` new interaction types
- tenant partitioning for interaction table

## Suggested PR Split

1. DDL + ORM + repository + architecture boundary docs。
2. interaction service + target verifier + HTTP.
3. summary / batch service + HTTP.
4. accept/helpful transaction consistency.
5. personal center activity backend.
6. frontend interaction hooks.
7. frontend topic interaction UI.
8. frontend personal center.
9. contract / coverage / singlebox acceptance。

每个 PR 保留 spec 链接，并确保 backend-focused PR 与前端-focused PR 不互相引入未解释的 schema changes。
