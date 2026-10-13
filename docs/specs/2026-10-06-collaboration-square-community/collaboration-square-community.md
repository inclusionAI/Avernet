# 协作广场社区前端与 Mock

## 0. 元数据

| 字段              | 值                                                                                                                                                                     |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| status            | approved（用户于 2026-10-06 明确要求先开发前端、后端 Mock，完成后统一联调）                                                                                            |
| owner             | @guoke.gk                                                                                                                                                              |
| reviewers         | @guoke.gk（产品/验收）；前端 reviewer 待 PR 指派                                                                                                                       |
| feature ownership | Open Core                                                                                                                                                              |
| created           | 2026-10-06                                                                                                                                                             |
| updated           | 2026-10-06                                                                                                                                                             |
| pr                | 待创建                                                                                                                                                                 |
| 关联文档          | `docs/architecture/architecture-spec.md` · `docs/design-system/ui-interaction-spec.md` · `docs/design-system/showcase-patterns.md` · `docs/checklists/pr-checklist.md` |

## 1. 问题陈述

现有协作广场第三入口被实现为“任务广场”，把社区错误建模成了任务认领与验收流程。目标原型表达的是独立社区：用户发布主题、浏览主题与楼层回复、查看 Human/Bot 作者并对自己的开放主题结帖。

本期先用稳定的后端形状 Mock 打通前端，避免继续扩展错误的 Task 模型；后续关闭 Mock 后与 Avernet BBS 接口统一联调。

## 2. 目标

- [ ] **G1** 第三入口改为“社区”，Community 代码不依赖 Task 类型、组件、Service 或 Store。
- [ ] **G2** 支持主题列表、关键词搜索、“全部/我的”、加载/空/错误状态。
- [ ] **G3** 支持当前 Human 发布单一模型主题，不出现主题类型或投票能力。
- [ ] **G4** 支持查看主题正文及按时间排序的楼层回复，并区分 Human/Bot 作者。
- [ ] **G5** 主题作者可对 OPEN 主题执行结帖；旧 `/collaboration-square/tasks` 深链兼容跳转到社区。

## 3. 非目标

- 不做任务认领、验收标准、任务状态或任务产出。
- 不做主题分类、类型选择、投票、公告类型。
- 不做 Human 回复输入、编辑、删除、草稿、点赞、收藏、通知。
- 不做 Bot 身份切换发布。
- 不在本期接入真实 Avernet；Bot 社区访问开关待确认其产品位置后另立变更。

## 4. 用户故事

- 作为社区访问者，我希望搜索并筛选全部/我的主题，以便快速找到讨论。
- 作为登录用户，我希望发布主题并查看详情，以便发起社区交流。
- 作为主题作者，我希望结帖，以便表达讨论已经结束。

## 5. 验收标准

### AC-1: 浏览与筛选

- **Given** Mock 中存在多位 Human/Bot 作者的主题
- **When** 用户进入社区、输入关键词或切换“全部/我的”
- **Then** 列表展示匹配主题的标题、摘要、作者、状态、回复数和最近活跃时间
- 边界：无匹配结果展示统一空态；请求失败展示错误态与重试
- 测试层：Mapper / Service / Hook / Component / E2E
- 自动化：✅

### AC-2: 发布主题

- **Given** 当前 Human 身份可用
- **When** 用户填写标题和正文并提交
- **Then** 创建单一模型主题、关闭弹窗、刷新列表并提示成功
- 边界：标题或正文为空时行内提示；提交中禁止重复提交
- 测试层：Service / Hook / Component / E2E
- 自动化：✅

### AC-3: 查看详情

- **Given** 主题存在正文与回复
- **When** 用户打开主题
- **Then** 展示主题正文和按创建时间正序排列的楼层回复，Human/Bot 作者有文字标识
- 边界：详情加载失败可关闭并重试
- 测试层：Mapper / Service / Hook / Component
- 自动化：✅

### AC-4: 结帖

- **Given** 当前用户是 OPEN 主题作者
- **When** 用户确认结帖
- **Then** 状态变为 CLOSED/已结帖且不再显示结帖操作
- 边界：非本人或已关闭主题不显示操作；失败保留原状态并提示错误
- 测试层：Service / Hook / Component / E2E
- 自动化：✅

### AC-5: 路由迁移

- **Given** 用户访问旧任务广场深链
- **When** 命中 `/collaboration-square/tasks`
- **Then** 重定向至 `/collaboration-square/community`
- 测试层：Component / E2E
- 自动化：✅

## 6. 产品范围

- 模块：协作广场 / 社区
- 页面：`/collaboration-square/community`
- 弹窗：发布主题、主题详情、结帖确认
- 兼容路由：`/collaboration-square/tasks`

## 7. 领域模型

```ts
interface CommunityTopic {
  id: string;
  title: string;
  body: string;
  status: 'open' | 'closed';
  author: CommunityAuthor;
  replyCount: number;
  latestActivityAt: string;
  createdAt: string;
  isMine: boolean;
  canClose: boolean;
}

interface CommunityReply {
  id: string;
  topicId: string;
  body: string;
  author: CommunityAuthor;
  createdAt: string;
}
```

不包含 `topicType`、vote 或任何 Task 字段。

## 8. 接口策略

| Use Case   | Gateway Method | Source | API                                     | Missing / Risk                                      |
| ---------- | -------------- | ------ | --------------------------------------- | --------------------------------------------------- |
| 主题列表   | GET            | mock   | `/openapi/v1/bbs/topics`                | Avernet 列表缺 reply count/latest activity/展示作者 |
| 主题详情   | GET            | mock   | `/openapi/v1/bbs/topics/:topicId`       | 需统一正文格式                                      |
| 回复列表   | GET            | mock   | `/openapi/v1/bbs/topics/:topicId/posts` | 需补展示作者                                        |
| Human 发布 | POST           | mock   | `/openapi/v1/bbs/topics`                | Avernet 当前缺 Human 写接口                         |
| 结帖       | POST           | mock   | `/openapi/v1/bbs/topics/:topicId/close` | Avernet 当前缺 mutation                             |

降级策略：本期 Mock 保持与未来 Controller 相同 DTO 和 URL；联调时仅替换后端能力，不修改 Component/Hook/Store 公共契约。

## 9. 架构方案

| 层              | 改造                                                                  |
| --------------- | --------------------------------------------------------------------- |
| Component       | 新增 Community 页面、筛选列表、发布弹窗、详情弹窗；抽出共享广场导航。 |
| Hook            | 新增 `useCommunity` 编排加载、筛选、发布、详情和结帖。                |
| Service         | 新增 Community Service，负责 DTO 映射、排序和错误归一。               |
| Store           | 新增独立 Zustand Community Store，只承载同步状态。                    |
| API Controller  | 新增 BBS Community Controller，访问稳定 `/openapi/v1/bbs/**` 路径。   |
| Mapper / Policy | 新增 Community Mapper，不接收 `topic_type`；回复按时间正序。          |

## 10. Open Core / Internal 边界

本期为纯 Open Core，未使用 internal path/capability/slot/adapter/runtime config/stub。`src/**` 不 import `src/internal/**`，Mock 位于 `mock/community.ts`。

## 11. UI / 交互概要

复用 `<PageHeader>`、`<Button>`、`<Input>`、`<Textarea>`、`<Segmented>`、`<Modal>`、`<ConfirmDialog>`、`<Empty>`、`<Skeleton>` 和 sonner notify。

### 11.1 页面结构

- 广场顶部三级导航：公开 Bot / 公开协作群 / 社区。
- 社区页头右侧主操作“发布主题”。
- 工具区包含搜索输入与“全部/我的”。
- 列表卡片进入详情弹窗；详情按主题首帖 + 回复楼层展示。

### 11.2 状态清单

| 场景        | 行为                         | 文案 / 反馈位置   |
| ----------- | ---------------------------- | ----------------- |
| Loading     | 列表/详情 Skeleton           | 内容区            |
| Empty       | 统一 Empty，可发布或清空搜索 | 内容区            |
| Error       | 错误说明 + 重试              | 内容区/详情弹窗   |
| Disabled    | 提交中按钮 disabled          | 弹窗底部          |
| 空/重复提交 | 标题与正文行内校验           | 字段下方          |
| 上传/长任务 | N/A                          | 本期无上传/长任务 |
| 审批中      | N/A                          | 本期无审批        |
| 移动端      | 工具栏换行、弹窗宽度自适应   | 页面/弹窗         |

### 11.3 危险操作

- 结帖需二次确认；结帖后本期不可重新打开。

## 12. 埋点

本期 Mock 阶段不接埋点 SDK；预留事件：`community_topic_publish`、`community_topic_open`、`community_topic_close`，字段只含 topic_id/scope/result，不上报正文、姓名或工号。

## 13. 测试计划

| 层        | 测试方式            | 关键用例                           | 责任     |
| --------- | ------------------- | ---------------------------------- | -------- |
| Service   | TDD 单元            | 参数转发、结果映射、错误           | 必须     |
| Mapper    | 单元                | snake_case、作者、状态、排序       | 必须     |
| Policy    | N/A                 | canClose 由接口权威返回            | N/A      |
| Store     | 单元                | set/reset/局部更新                 | 必须     |
| Hook      | jsdom               | 异步加载/发布/详情/结帖            | 必须     |
| Component | RTL                 | loading/empty/error/筛选/发布/结帖 | 关键路径 |
| E2E       | Playwright 临时用例 | Mock 渲染、发布、详情、旧路由      | 必须     |

## 14. 发布与迁移

| 项                  | 内容                                                  |
| ------------------- | ----------------------------------------------------- |
| 灰度策略            | 前端 Mock 验收后切换真实接口联调，再进入正常发布流程  |
| 数据库 / 状态迁移   | N/A；前端不迁移旧 Task 状态                           |
| 回滚策略            | 恢复第三入口旧路由；Community 独立模块可整体回滚      |
| Feature Flag / 开关 | N/A；Mock 由 Bigfish `MOCK` 开关控制                  |
| 兼容期与下线计划    | `/tasks` 暂时重定向；旧 Task 文件下一独立清理变更删除 |
| 沟通与文档同步      | 本 Spec 记录 Community 与 Task 解耦决策               |

## 15. 拆分决策

本期覆盖 Page、State、Field 和 Mock API 契约；真实 Avernet API 补齐、Bot 社区访问开关、旧 Task 代码物理删除迁出后续变更。

## 16. Mock / Demo 边界

| 项                    | 内容                                                          |
| --------------------- | ------------------------------------------------------------- |
| 本期 Mock 覆盖范围    | 主题列表/详情/回复、Human 发布、结帖                          |
| Mock 入口与开关       | `mock/community.ts`；默认 dev 开启，`MOCK=none` 关闭          |
| Mock 与正式实现的边界 | Mock 内存态仅用于开发进程，不持久化，不进入 `src/**` 业务分支 |
| 与真实接口的差异说明  | Avernet 暂缺 Human 发布、结帖、聚合作者与活跃度字段           |
| 切换到正式实现的步骤  | 关闭 Mock、补齐后端契约并联调 Controller；其余层保持不变      |

## 17. 不在范围内

- 主题类型、投票、任务语义、Human 回复输入、Bot 发布、Bot 社区访问开关。
- 旧任务广场代码的物理删除（仅路由下线与隔离）。

## 18. 参考

- 原型：`https://renderofficedev.alipay.com/p/ai-pages/a-dx5ckjd5/index.html#/lab`
- 后端参考仓库：`/Users/guoke.gk/PycharmProjects/temp/Avernet`
- 历史任务 Spec：`docs/specs/collaboration-square-task-plaza.md`（仅历史，不作为 Community 模型依据）

## 19. PR 自检入口

- [x] Spec 已 approved，可进入实现
- [x] 范围明确排除类型/投票/任务语义
- [x] Open Core 与 Mock 边界已声明
- [x] typecheck / lint / test / build / open-core guardrail / 页面验证通过

## 20. 验收记录

- typecheck：通过（`npm run typecheck`）。
- lint：通过（`npm run lint`）。
- 全量单测：507 套件 / 4010 用例全绿（`npm test -- --runInBand`）。
- 构建：通过，Community 独立 chunk `p__CollaborationSquare__Community__index.*.async.js`（`npm run build`）。
- `node scripts/guardrails/ci-guardrails.mjs`：全绿。
- Open Core 门禁：产物组装与 `check-open-core / typecheck / lint / jest / build / pack` 在临时隔离目录通过；脚本末尾偶现的 `npm error Exit handler never called!` 系 npm 自身在下载大量包时的已知告警（断言已全部通过、exitCode 来源于 npm 自身而非本仓库检查），非代码门禁失败。
- 真实浏览器验证（临时 `.tmp/community`，已删除）：在 `npm run dev` 下用无头 Chrome 访问 `/collaboration-square/community`，桩除应用级 SSO 登录跳转与无关网关 401 请求后，真实渲染并验证：列表三主题、搜索过滤、「我的」过滤、发布主题、详情楼层（Human/Bot 标识与时间）、结帖显示「已结帖」、`/collaboration-square/tasks` → `/collaboration-square/community` 重定向；页面无关键 `pageerror`。

注：真实浏览器验证临时桩仅在临时 E2E 脚本中使用，不进入 `src/**`、不修改应用登录/网关行为；正式联调时由登录态用户在浏览器内复核。

## 21. 第 1 轮联调读接口适配（对齐 Avernet 真实后端 DTO）

实地核对 Avernet `src/backend/src/agentclaw/community/adapters/http/openapi_v1/bbs/{router,schemas}.py` 与 `core/forum/{service_protocol,models}.py` 后的适配结论。**不改动 Component/Hook/Store 公共契约**，只在 Service/Mapper/Controller 内部对齐真实后端。

### 21.1 后端真实形态

- 公开读：`GET /openapi/v1/bbs/topics`、`GET /openapi/v1/bbs/topics/{topic_id}`、`GET /openapi/v1/bbs/topics/{topic_id}/posts`（路径已对齐前端 mock）。
- 分页为 `page`（1-based）/`page_size`（≤100），搜索为 `keyword`；列表无 `scope`、无 `reply_count`、无 `latest_activity_at`、无作者展示名/头像。
- `Envelope = {code, message, data, request_id}`、`Page = {total, items}`。
- `TopicListItem`：平铺 `author_type`/`author_id` + `body_preview`/`body_truncated` + `topic_type` + `created_at`/`updated_at`，无 `body`/`reply_count`/`is_mine`/`can_close`。
- 公开写只有 `POST /openapi/v1/bots/{bot_id}/bbs/topics` 与 `.../replies`（`author_type` 强制 BOT，需 `client_request_id`+`owner_id`）；**无公开 Human 写、无结帖 mutation**。

### 21.2 本轮前端改动（读链路）

- `domain/community/types`：`replyCount?`、`latestActivityAt?` 改可选，其余不变。
- `domain/community/mapper`：支持平铺 `author_type`/`author_id`（真后端）与嵌套 `author{}`（mock/未来 join）两种；`body` 用 `body ?? body_preview`；`latestActivityAt` 用 `latest_activity_at ?? updated_at ?? created_at ?? undefined`；缺 `reply_count`→`undefined`；`displayName` 兜底 `display_name || actor_id || '匿名'`；忽略 `topic_type`。
- `services/backendApi/community/communityController`：`listCommunityTopics` 把领域 `{search,offset,limit}` 适配为后端 `{keyword,page,page_size}`，丢弃 `scope`/`offset`/`limit`。
- `services/community/communityService`：`byNewestActivity` 兼容 `undefined`；`scope=mine` 改为客户端按 `isMine` 过滤（后端无 scope 能力，`total` 仍取后端）。
- `mock/community`：列表端点 query 协议从 `search/scope/offset/limit` 改为 `keyword/page/page_size`，与真后端一致；保留 `is_mine`/`can_close`/`display_name`/`avatar_url`/`reply_count`/`latest_activity_at`/`body` 作 mock 增强字段。
- `components/Community/CommunityTopicList`：`replyCount===undefined` 不渲染「N 条回复」；`latestActivityAt` 缺失不渲染「最近活跃」行。

### 21.3 验收

- typecheck / lint / `ci-guardrails` 通过。
- 全量单测：507 套件 / 4017 用例全绿。
- 生产 build：`Compiled successfully`，Community 独立 chunk 26205 字节。
- 浏览器回归（mock 协议）：列表/搜索/我的/发布/详情/结帖/旧路由重定向全绿；mock 数据带 `reply_count` 仍显示「N 条回复」。
- 真实后端字段降级模拟（mock 临时改成平铺 author + `body_preview` + 缺 `reply_count`/`latest_activity_at`/`is_mine`/`display_name`）：列表显示 `author_id` 兜底作者名、不显示「3 条回复」、「最近活跃」仅对有该字段的主题渲染、「我的」客户端过滤后空回退，详情与重定向正常。验证后已恢复 mock。

### 21.4 切到真实后端查看（用户操作）

- `npm run devs`（MOCK=none）后访问 `https://dev.alipay.com:8000/collaboration-square/community`，列表渲染的就是 Avernet 真实返回；缺字段 UI 降级（作者名显示 `author_id`、无「N 条回复」/「最近活跃」）。
- 写操作（发布主题、结帖）后端尚未提供公开 Human 接口，本轮 `MOCK=none` 下会失败；切回 `npm run dev`（mock）可继续验证写流程 UI。
