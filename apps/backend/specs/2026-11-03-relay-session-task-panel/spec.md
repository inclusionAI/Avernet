# 第二棒会话链接 → 自动拉起整图 TaskPanel — 设计文档

日期: 2026-11-03
归属: `apps/frontend-nextgen` + `apps/backend` (`agentclaw.community`)
状态: DRAFT,待评审

## Intent & why

接力(relay)任务的第二棒在这个 Bot/协作群的另一个会话上执行。任务副屏(TaskPanel)现在只
能靠"对话消息里的 `<AixUI type="panel" component="taskPanel.TaskLoopView">` 声明式面板消息"触发——
而这只有**第一棒 / 中心化**发起方才发(发到主 Bot 会话)。第二棒会话里没有这条消息,所以副屏里没有
task tab,用户在第二棒会话里**看不到任务副屏**,无法跟进接力执行的整图(任务总览 + 节点下钻 + 产物)。

需求要点(来自对话):

- 给一个第二棒的 session 会话链接(单 bot 会话 `/workspace/chat` 或群会话
  `/workspace/collaboration` 的现有深链,带 `session`),用户点开进入会话页 → 自动弹出副屏并展示该
  接力任务的**整图 TaskPanel**。
- 不新增按钮、不开抽屉:复用现有 SDK 副屏(`ChatLayoutPanelSlot`)+ 任务副屏 tab 机制。
- 两种会话页面(单 bot 会话 / 群会话)都要支持,体验一致。

## Goals

- 第二棒会话深链打开后,会话页副屏自动弹起,且为该接力任务的整图 TaskPanel tab。
- 复用现有"进入会话时自动开任务副屏"的单点 `useSessionTaskPanelBootstrap`(单 bot 会话与群会话共用),
  不新增按钮 / 抽屉 / 路由,不动会话深链契约(不加 `?openTaskPanel=1` 之类显式参数)。
- 提供后端"按 BCS chat session_id 反查接力执行节点"的只读来源,作为前端副屏自动恢复的依据。
- 普通单 bot 会话 / 群会话的现有进会行为不变(只有确实是接力执行会话才命中)。

## Non-goals

- 不改会话深链的 URL 契约(复用现有 `session`/`bot`/`group` query)。
- 不在会话里渲染/注入 AixUI 面板消息(第二棒不靠消息触发)。
- 不新增"查看任务"按钮或抽屉入口。
- 不改变第一棒 / 中心化的任务副屏触发路径。
- 不做"按会话切换多个任务 tab"的高级切换(沿用现有 `taskPanelOpenIntent`/`activeTaskId` 语义即可)。

## Anti-goals(即便技术上跑通也算失败)

- **不能让普通会话进会误开 task 副屏。** `by_session` 必须只在 session 确为某接力任务执行会话时命中,
  否则非接力会话进会副屏会被无端拉起,打扰用户。
- **不能旁路 env 隔离。** `task_node_run_info.session_id` 列无 `env`/tenant 列,反查拿到 `(task_id,
  node_id)` 后,取 `owner_user_id` 必须经过 env 隔离的 `TaskInfoRepository.get`(`_require_read_scope`),
  跨 env 取到的 session 应返回未命中(404 / null),而不是泄露别的 env 的任务/owner。
- **不能在公开 OpenAPI 文档里出现 `/api/` 或反引号**(公开 doc gate);不能让新路由缺字段/参数描述。
- **不能引入不需要的 repo 新方法 / DI 改动**——能复用现成 `get_by_session_id` 与现有 service 注入
  就不新增。

## Constraints(来自前后端实际惯例,已探查核对)

1. `task_node_run_info.session_id`(`core/task/repository/models.py:227`)就是 **BCS chat session id**,
   即第二棒会话链接带的 `session`;与 BCS 建群/建 session 返的 session_id 同源,与回调的 `main_session_id`
   同源。证实:`core/repository/protocols/task/core.py:184-190`、`core/task/task_service_protocol.py:147-161`
   `converge_by_session` 注释。
2. 现成 repo方法 `TaskNodeRunInfoRepositoryProtocol.get_by_session_id(session_id) -> TaskNodeRunInfoRecord | None`
   (`protocols/task/core.py:184`,impl `task_node_run_info_repository.py:154-163`,按 `retry` 倒序取单行)。
   `session_id` 列**无 env/tenant 过滤**;env 隔离落在 `TaskInfoRepository.get` 的 `_require_read_scope`。
3. 现有"进入会话自动开任务副屏"已在单 bot 会话(`pages/Workspace/Chat/hooks/useChatHeaderPanels.ts:132`)
   与群会话(`pages/Workspace/hooks/useGroupChatHeaderPanels.ts:96`)共用 `useSessionTaskPanelBootstrap`
   (`hooks/useSessionTaskPanelBootstrap.ts:59`):目前靠 `listMyTasks(user_id)` + `findLatestTaskForSession`
   (按 `execution_config.main_session_id == sessionId` 命中)。问题:`main_session_id` 是发起会话(第一棒),
   第二棒会话命中不到,且第二棒当前用户未必是任务发起人,`listMyTasks` 里没有该任务。这正是要补的口。
4. `panelRef.current.openTab(PanelTab)` 是 `PanelHandle` 的方法(`ChatLayoutPanelSlot` 透出),
   `openTab` 由 SDK 自动展开副屏(经 `onIsOpenChange` 同步 `sidePaneOpen`),**无需** 显式 `setSidePaneOpen(true)`。
   `TaskPanelSwitcherSlot.tsx:49-57` 是同款 `openPanelTab` 调用形态可参考;`panel.openTab` 用 `buildSessionTaskPanelTab`
   (`useSessionTaskPanelBootstrap.ts:33-51`)同款 `PanelTab` 形态。
5. `openTab` 前**必须** `useTaskSidePanelStore.markTaskPanelOpenIntent(taskId)`,否则
   `ChatLayoutPanelSlot.taskNavigationAwareHandle.openTab`(`ChatLayoutPanelSlot.tsx:72-90`)会压制非用户意图
   的开 tab(被历史消息渲染兜底逻辑误判)。
6. 公开任务面路由:挂在 `adapters/http/openapi_v1/task/router.py`(`APIRouter(prefix="/openapi/v1/collaboration/tasks",
   route_class=PublicAPIRoute)`),`PrincipalDep = Annotated[Principal, Depends(require_principal)]`,
   `response_model=Envelope[T]` + `@envelope_errors`,`del principal`(身份不在此使用,见 dashboard/list 等)。
7. 公开任务读路由必须同时在两表登记,否则装配期 `PublicRouteNotAuthorized`:
   `authorization_rules_task.py::TASK_PUBLIC_AUTHORIZATION`(`NoCheck`)+ `admission_task_routes.py::TASK_PUBLIC_ADMISSION`
   (`AdmissionMode.OPEN`)。详见 dashboard/list/trajectory/match 同款。
8. 每条新活路由在 `tests/community/endpoints/` 配声明式 `happy + error` 用例,否则
   `framework/test_coverage_gate.py` 挂 gate;gate 按字面 `(method, 完整 path)` 计。
9. 公开契约 gate:`test_explicit_user_id.py` 的 `_NO_USER_DIMENSION`(无 caller-supplied user_id 的路由)、
   `_BOT_ID_PLACEMENT`(无 bot_id 的路由计入 `none` bucket,需 bump pin)、`test_schema_docs.py` 的
   `_FORBIDDEN_IN_PUBLISHED_TEXT`(禁 `/api/`、反引号、缺描述)。
10. 路径前缀:前端 `services/tasks/taskConfig.ts` 的 `TASK_API_BASE=''`(相对,走代理)、
    `TASK_READ_API_BASE='/openapi/v1/collaboration/tasks'`、`resolveTaskApiBase()`(capability 解析 Open Core
    `/openapi/v1` vs 内部 `/api/v1`)。`resolveTaskPanelDefaultWidth()` 在 `services/workspace/panelWidthMemory.ts`。
    `TaskPanelAdapterWithUser`(`extensions/empty.ts:35-64`)会 overlay `taskApiBase: TASK_READ_API_BASE`,所以
    `openTab` params 传 `taskApiBase` 是兜底。

## 设计:已决定的部分

### 后端

#### 路由(挂在公开 task router,镜像 `get_task_dashboard`)

`adapters/http/openapi_v1/task/router.py`:`GET /by_session`,`@router.get("/by_session", response_model=Envelope[BySessionDTO])`
+ `@envelope_errors`,签名 `session_id: Annotated[str, Query(description="...")], request: Request, principal: PrincipalDep,
service: TaskServiceProtocol = Injected(TaskServiceProtocol), # noqa: B008` → `Envelope[BySessionDTO]`。
`del principal`;`service.get_task_node_by_session(session_id)`;`None` → `raise NotFound(...)`(from `core.errors`,已被
task 测试用),`@envelope_errors` 映射 404;命中 → `envelope(by_session_ref_to_dto(ref), request)`。
描述字符串不含 `/api/`、反引号;`session_id` query 带 description。

#### Service

`core/task/task_service_protocol.py`(Protocol,挨着 `converge_by_session`)新增 `get_task_node_by_session(session_id) -> TaskNodeSessionRef | None`;
`core/task/task_center/task_service.py` 实现:`self._run_info_repo.get_by_session_id(session_id)` → `(task_id, node_id)`,
再 `self._task_info_repo.get(task_id)`(env-scoped)→ `owner_user_id`;任一 None → None。**不新增 repo 方法**;DI 已在
`task_persistence_module`/`task_module` 就绪(`task_service.py` 已有 `self._run_info_repo`/`self._task_info_repo`)。

#### DTO & 类型

- `TaskNodeSessionRef` dataclass(`task_id`, `node_id`, `owner_user_id`)加在 `core/task/repository/types.py`(挨着 `TaskInfoRecord`)。
- `BySessionDTO`(`task_id`/`owner_user_id`/`node_id`,每个 `Field(..., description=…)`) + `by_session_ref_to_dto` 转换器
  加在 `adapters/http/task/schemas.py`(挨着 `TaskInfoRecordDTO`)。

#### 鉴权两表

- `authorization_rules_task.py::TASK_PUBLIC_AUTHORIZATION`:`("GET", "/openapi/v1/collaboration/tasks/by_session"): NoCheck("a task, not a bot; read-only relay task/node/owner by chat session_id")`。
- `admission_task_routes.py::TASK_PUBLIC_ADMISSION`:`("GET", "/openapi/v1/collaboration/tasks/by_session"): AdmissionMode.OPEN`。

### 前端

#### 单点改:`hooks/useSessionTaskPanelBootstrap.ts`

现状 effect(`:59-105`):`listMyTasks(user_id)` → `findLatestTaskForSession`(按 `main_session_id==sessionId`)→ 命中开 tab;
开头有 `if (!requestedTaskId && activeTaskId) return` 的"沿用当前副屏"短路。

改动(最小、不动普通会话行为):

1. 新增 service `getTaskBySession(sessionId)` → `DomainResult<{task_id, owner_user_id, node_id} | null>`
   (`services/backendApi/tasks/taskController.ts` 加一函数,走 `TASK_READ_API_BASE`(`/openapi/v1/collaboration/tasks`)
   + `backendRequest`;`unwrapEnvelope` 解析;404 / 其它失败统一当 `null`,不抛、不弹错)。
2. effect 里**先于 `listMyTasks`** 调一次 `getTaskBySession(sessionId)`;命中 → 即便已有 `activeTaskId` 也切到这个 task:
   `useTaskSidePanelStore.setActiveTask(task_id)` + `markTaskPanelOpenIntent(task_id)` + `panelRef.current.openTab(buildRelayTaskPanelTab(task_id, userId))`;
   `consumeTaskPanelOpenIntent`;`return`(不再查 `listMyTasks`)。未命中 → 落回现有 `listMyTasks` 兜底
   (保留原 `activeTaskId` 沿用短路逻辑)。跨用户打开会话时,兜底列表按 session id 末尾
   `:user:<owner>` 解析出的归属人工号查询;panel 的 userId 继续使用当前登录人。
3. 新增 `buildRelayTaskPanelTab(taskId, userId, title?)`(挨着 `buildSessionTaskPanelTab`,`:33-51`):
   `{ id: 'task-'+taskId, title: title ?? '任务', type: 'taskPanel.TaskLoopView', closable: true,
   defaultWidth: resolveTaskPanelDefaultWidth(), params: { taskId, apiBaseUrl: '', bcsBaseUrl: '',
   userId, taskApiBase: TASK_READ_API_BASE } }`。`TASK_READ_API_BASE` from `services/tasks/taskConfig`;
   `TaskPanelAdapterWithUser` 会 overlay `taskApiBase`,传它是兜底。
4. `markTaskPanelOpenIntent` **必须**在 `openTab` 之前(见 Constraint 5)。`panel.openTab` 自动展开副屏,无需 `setSidePaneOpen(true)`。

#### 链接形式

复用现有会话深链:单 bot `/workspace/chat?section=...&bot=...&session=<sessionId>`;群
`/workspace/collaboration?group=<gid>&session=<sessionId>&...`。**不加** `?openTaskPanel=1`;
命中与否完全由后端 `by_session` 判定(只有 `session_id` 在 `task_node_run_info` 有行才返回命中,普通会话天然不命中 → 前端落回原逻辑)。
单 bot 已会话与群会话共用同一 bootstrap hook。发任务的既有单 bot 会话在初始化根节点时把
`execution_config.main_session_id` 写入 root `run_info.session_id`,后续第二棒节点也由 dispatch 写入自身执行
session;两者都能命中 `by_session`。

### 测试

#### 后端 — `tests/community/endpoints/test_openapi_task_endpoints.py`(复用 `_KEY/_boot_verifier/_headers/_BASE`)

- happy `authenticated_existing_session`:`bind_overrides(TaskServiceProtocol, {"get_task_node_by_session": stub})`
  返回 `{task_id, owner_user_id, node_id}`,query `session_id=…`,`ExpectSuccess(status=200,
  json_contains={"code":200000,"data":{"task_id":...,"owner_user_id":...,"node_id":...}})`。
- error `unauthenticated`:无 principal 头 → `ExpectError(status=401, json_contains={"code":401000,"message":"Unauthorized"})`。
- error `session_not_found`:authenticated,stub 返回 None / 未命中 session_id → `ExpectError(status=404)`。
- `tests/community/framework/test_coverage_gate.py` 通过(三条活路由各 happy+error 齐)。

#### 公开契约 gate 同步(必须一起改,否则挂 gate)

- `tests/community/adapters/http/openapi_v1/test_explicit_user_id.py`:
  `_NO_USER_DIMENSION` 加 `("get", f"{PUBLIC_API_PREFIX}/collaboration/tasks/by_session")`(task 公开面簇,挨着
  dashboard/match);`_BOT_ID_PLACEMENT["none"]` **+1**(执行时按当前值,配注释 "`by_session` adds one
  account-level no-bot read: none N -> N+1")。`by_session` 不带 user_id、不进 user-taking pin。
- `tests/community/adapters/http/openapi_v1/test_schema_docs.py`:路由 / `session_id` query / `BySessionDTO`
  三层描述非空、无 `/api/`、无反引号;`BySessionDTO` 若带 example 需满足必需字段。

#### 前端 — `test/hooks/useSessionTaskPanelBootstrap.test.tsx`

- "by_session 命中 → openTab 调用 + activeTaskId=taskId + 不调 listMyTasks"(mock `getTaskBySession` 命中,
  mock `listMyTasks` 不应被调;断言 `openTab.mock.calls[0][0]` 匹配 TaskLoopView tab + params,
  `useTaskSidePanelStore.getState().activeTaskId === taskId`)。
- "by_session 未命中 → 落回 listMyTasks 兜底(现有用例保持)"。
- mock 模式照现有 `jest.mock('@/services/myTask', ...)` + `makePanelRef()` stub `openTab`;
  `window.history.pushState` 设 URL。
- `test/pages/Workspace/Chat/hooks/useChatHeaderPanels.test.ts` 固化单 bot 已会话集成路径:
  打开既有 session 时使用 `by_session`,命中即 `openTab`,且不再调用 `listMyTasks`。
- 本功能不提供前端 mock endpoint;开发代理直接透传到 Backend 的真实接口。

## 留给 builder 的

- 各文件行级实现(本设计给契约与拆分);`BySessionDTO` / `TaskNodeSessionRef` / `by_session_ref_to_dto` 的字段描述措辞
  用纯英文,无 `/api/` 与反引号。
- `_BOT_ID_PLACEMENT["none"]` 当前值以执行时为基 +1(config/match 已合入 release,by_session 再 +1)。
- 前端 `getTaskBySession` 的错误语义:把未命中(404)、网络错误统一收敛为 `null`(进会副屏自动恢复是增强,
  失败不阻断会话,见 `useSessionTaskPanelBootstrap.ts` 既有 `catch` 注释)。
- 第二棒会话"切到自己的 task"会覆盖前一会话沿用下来的 `activeTaskId`——这是预期(第二棒会话进来要展示本任务的整图)。

## 我做的几个决定(供过文档一并核对)

1. 后端走"独立公开只读路由 `by_session`",不挂在现有会话接口字段里——契约清晰、frontent 调用直白,镜像
   dashboard/match 的 OPEN+NoCheck。
2. session_id → 框架反查"只跑一次 +1 次接口",不引入 query 参数显式开关——靠后端只对接力执行会话命中来
   保证不打扰普通会话。
3. env 隔离落在 `TaskInfoRepository.get`(`_require_read_scope`),不直读 run_info 当 owner 来源(避免跨 env 泄露)。
4. 复用 `useSessionTaskPanelBootstrap` 单点,不新增按钮/抽屉;`openTab` 自动展开副屏,不显式 `setSidePaneOpen`。
5. `openTab` 前 `markTaskPanelOpenIntent`,避免 `ChatLayoutPanelSlot` 压制。

## 验证

- 后端:`cd apps/backend && DEPLOY_PROFILE=test uv run pytest tests/community/endpoints/test_openapi_task_endpoints.py
  tests/community/adapters/http/openapi_v1/test_explicit_user_id.py tests/community/adapters/http/openapi_v1/test_schema_docs.py
  tests/community/framework/test_coverage_gate.py -q`(全绿)。
- 前端:`cd apps/frontend-nextgen && npm run check:open-core && npm run typecheck &&
  npx jest test/hooks/useSessionTaskPanelBootstrap.test.tsx test/pages/Workspace/Chat/hooks/useChatHeaderPanels.test.ts`。
- 手测(singlebox / pre env):打开第二棒会话深链 → 副屏自动弹 + 整图 TaskPanel;普通单 bot 会话 / 群会话 deep-link 打开
  行为不变(无 task 副屏或按 listMyTasks 如旧)。
