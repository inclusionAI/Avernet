# Frontend Nextgen 的 BCS 独立加好友 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `apps/frontend-nextgen` 在未部署 Backend/Gateway/BCSFuse 时，仅依赖 BCS v1 完成公开 Bot 发现、申请、管理者审批和好友状态回读；配置 Backend 后只增加展示、服务发布过滤及 Human 自动建私聊。

**Architecture:** `CollaborationSquareGateway` 继续作为页面边界，新增聚焦 BCS 候选列表与搜索的 Controller/mapper；前端组合层显式决定是否读取 Backend metadata，并以 BCS 原始偏移实现过滤后 `hasMore`。当前角色的申请处理复用现有 friend-connections Controller；BCS-only 静态服务使用独立同源代理 profile，同时关闭外壳的 Backend 自动初始化／通知轮询；好友写操作以角色与代次隔离晚到回调，不改变现有多服务部署默认配置。

**Tech Stack:** TypeScript、React 18、Umi Max、Zustand、Jest/jsdom、shadcn 语义样式、Nginx 静态部署。前端单测使用 `npm test -- --runInBand`，DOM 测试首行 `/** @jest-environment jsdom */`。

**共同设计：** `docs/specs/2026-11-09-bcs-standalone-friend-discovery/design.md`。本文件只负责 Frontend；BCS 的 Service API、内存/SQL 与 v1 契约另见 `apps/bcs/specs/2026-11-09-bcs-standalone-friend-discovery/plan.md`，须先提供经测试的契约样例。本计划的 Mock Controller 测试可独立先行，真实 BCS 浏览器联调在 BCS 契约就绪后执行。

---

## 开工约束与文件职责

- 进入 `apps/frontend-nextgen` 后先执行 `npm ci`（依赖当前未安装），使用锁定的公开 npm 依赖；无网络时如实记录无法执行的测试，不绕过开源依赖守卫。
- 先读 `apps/frontend-nextgen/CLAUDE.md`、根 `AGENTS.md`、共同设计以及 BCS 当前的 `api-contracts/v1/openapi/bots.yaml`、`api-contracts/v1/internal.yaml`、`api-contracts/v1/openapi/friendships.yaml`；不要假设 `eligible-candidates` 是新入口，也不要在前端按旧 `/bots/search` DTO 猜字段。
- `src/services/bcs/botCandidatesController.ts`：消费两个现有 BCS v1 候选 GET 的 DTO、校验 20000 信封和身份相关参数，不依赖 Backend Bot Catalog。`src/domain/collaborationSquare/bcsBotMapper.ts`：只将 `BotCandidate.bot` 的安全公开字段和 `is_friend` 映射为页面模型。
- `src/services/collaborationSquare/publicBotMetadata.ts`：列表、语义结果与名称回退共用的 metadata 关联和服务发布过滤；`src/services/collaborationSquare/publicBotCursor.ts`：无 React 依赖的 BCS 原始偏移／可选 Backend metadata 拼页；`src/services/collaborationSquare/collaborationSquareApiAdapter.ts`：只做调用装配、画像降级和已有好友关系写操作，不放扫描循环。
- `src/services/workspace/identityService.ts`：BCS-only 时身份仍由 `bots/mine` 装配，禁止额外查询 Backend `GET /openapi/v1/bots`；`src/pages/Workspace/Chat/hooks/useLegacyShareLinkGuard.ts`：区分 BCS 状态与 Backend 生命周期，不将 `online` 错判为失效。
- `src/hooks/useCollaborationSquareList.ts`、`src/services/collaborationSquare/collaborationSquareGateway.ts`、`src/services/collaborationSquare/collaborationSquareService.ts`、`src/domain/collaborationSquare/types.ts`：为 Bot 列表引入 `nextOffset/hasMore`，Group/Task 不改分页；分享链接查询也改走同一游标语义。
- `src/components/CollaborationSquare/FriendRequestsPanel/index.tsx` 和 `src/components/CollaborationSquare/PublicBotCatalogPanel/index.tsx`：所选角色的发出／收到申请与通过／拒绝入口。新增 `src/hooks/useFriendConnectionRequests.ts` 负责申请列表和写操作编排，`src/hooks/useCollaborationActorOperationScope.ts` 负责当前角色／代次回调守卫；组件不直接编排 Controller。`src/capabilities/types.ts`、`src/capabilities/defaultCapabilities.ts`、`src/typings/global.d.ts`：Backend 可选能力开关，并从同一能力决策派生 `getShellVisibility().value.notificationBell`；`src/hooks/useSpaceContext.ts`：BCS-only 时跳过 App 入口个人空间初始化，不停掉 BCS workspace 身份加载；不得在页面／Service 散落环境变量。
- `deploy/docker-entrypoint.sh`、`deploy/nginx/default.conf.template`、新增的 `deploy/nginx/default.bcs-only.conf.template`、`Dockerfile`、`config/config.local.ts`、`docs/deployment.md`：保留默认全栈部署，额外提供仅转发必要 BCS 路由的单服务 profile。测试文件遵循现有 `test/` 镜像目录，不改未关联的 Mock 或私有 overlay。

### Task 1：BCS 候选 Controller、模型映射与当前角色

**Files:** Create `src/services/bcs/botCandidatesController.ts`; Create `src/domain/collaborationSquare/bcsBotMapper.ts`; Modify `src/domain/collaborationSquare/types.ts`; Create `test/services/bcs/botCandidatesController.test.ts`; Create `test/domain/collaborationSquare/bcsBotMapper.test.ts`。

- [ ] **Step 1: 写失败测试。** 模拟 BCS `{code:20000,data:{items:[{bot:{bot_id,name,avatar_url,created_by,descriptor:{summary},visibility,user_visibility,status},is_friend}],total,offset,limit}}`；断言 Human 当前视角发 `human_<authenticated-id>`，Bot 视角发所选完整 UUID，普通列表只带 `purpose=discovery,name,offset,limit`；智能搜索走内部 `/api/v1/collaboration/bots/{id}/candidates/search` 并保留 `search_mode`、`score`、`short_profile`，空关键词不上送；401／错误信封／无效结构不伪装为空列表，AbortSignal 原样透传。映射时把 BCS `bot.bot_id` 作为唯一申请目标，头像／摘要正常回退，隐藏 descriptor 未审核技能与 `friend_ext`。真实 BCS DTO 的 `status=online|hidden` 必须映射为独立 `bcsStatus`，不得透传为 Backend 生命周期 `status` 或伪造 `ACTIVE`；两种状态都加入 mapper 断言，Task 3 用这些映射结果验证旧分享链接。

```ts
const page = await listBcsBotCandidates('human_7', { name: '规划', offset: 0, limit: 24 });
expect(page.data.items[0].bot.bot_id).toBe('assistant:owner-8');
expect(mockedBackendRequest).toHaveBeenCalledWith(
  '/openapi/v1/collaboration/bots/human_7/candidates',
  expect.objectContaining({ method: 'GET', injectUserId: false }),
);
expect(mapBcsBotCandidate(page.data.items[0], '7').id).toBe('assistant:owner-8');
```

- [ ] **Step 2: 红灯。** Run: `cd apps/frontend-nextgen && npx jest test/services/bcs/botCandidatesController.test.ts test/domain/collaborationSquare/bcsBotMapper.test.ts --runInBand`。Expected: 新模块尚不存在，相关测试失败。
- [ ] **Step 3: 最小实现。** 复用 `backendRequest` 的同源 Cookie、登录跳转和取消语义（其名称不意味着请求发往 Backend），只替换目标 URL；严格校验 `code===20000`、`data.items` 数组、`total/offset/limit` 数值；拒绝畸形 Bot 项而不是映射为伪造身份。Human 查询路径从 `humanBotContext.userId` 规范生成 `human_<id>`；Bot 路径直接使用已由 `bots/mine` 提供的所选 `viewerActorId`，权限仍由 BCS 校验。

```ts
export const BCS_CANDIDATES_PATH = (actorId: string) =>
  `/openapi/v1/collaboration/bots/${encodeURIComponent(actorId)}/candidates`;
export const BCS_CANDIDATE_SEARCH_PATH = (actorId: string) =>
  `/api/v1/collaboration/bots/${encodeURIComponent(actorId)}/candidates/search`;
export type BcsCandidateBot = {
  bot_id: string; name: string; avatar_url: string | null;
  created_by?: string; descriptor: { summary: string };
  visibility: 'public' | 'protected' | 'private';
  user_visibility: 'public' | 'protected' | 'private';
  status: 'online' | 'hidden';
};
// PublicBot 新增 bcsStatus?: 'online' | 'hidden'；BCS mapper 必须赋值。
// 现有 status?: string 只保留 Backend Catalog 生命周期兼容语义。
export type BcsCandidatePage = {
  items: Array<{ bot: BcsCandidateBot; is_friend: boolean }>;
  total: number; offset: number; limit: number;
};
```

- [ ] **Step 4: 绿灯并提交。** Run: `cd apps/frontend-nextgen && npx jest test/services/bcs/botCandidatesController.test.ts test/domain/collaborationSquare/bcsBotMapper.test.ts --runInBand && npm run typecheck`。Expected: Controller 和安全映射全通过。Commit: `git add apps/frontend-nextgen/src/services/bcs apps/frontend-nextgen/src/domain/collaborationSquare apps/frontend-nextgen/test && git commit -m 'feat(frontend-nextgen): consume BCS candidate discovery contract'`。

### Task 2：显式 Backend 选项、外壳冷启动隔离及共用 metadata 过滤

**Files:** Modify `src/capabilities/types.ts`、`src/capabilities/defaultCapabilities.ts`、`src/typings/global.d.ts`、`config/config.ts`; Create `src/services/collaborationSquare/publicBotCursor.ts`、`src/services/collaborationSquare/publicBotMetadata.ts`; Modify `src/services/workspace/identityService.ts`、`src/services/backendApi/bots/botController.ts`（为 `listBotMetadata` 透传可选 AbortSignal）、`src/domain/collaborationSquare/types.ts`; Test `test/services/collaborationSquare/publicBotCursor.test.ts`、`test/services/collaborationSquare/publicBotMetadata.test.ts`（新建）、`test/services/workspace/identityService.test.ts`; Modify `test/deploy/runtimeConfig.test.ts`（部署变量在 Task 5 接通）；Modify `src/hooks/useSpaceContext.ts`、`test/hooks/useSpaceContext.test.ts`、`test/capabilities/defaultCapabilities.test.ts`; Create `test/shell/bcsOnlyDiscoveryBoot.test.tsx`（本 Task 挂载真实 `AppShell` 与 `SquareIdentityPicker`，Task 3 扩展为完整发现页）。

- [ ] **Step 1: 写失败测试。** 先设置显式 `const signal = new AbortController().signal`，构造候选页 100 条里多条有 metadata `service,false`，下一 BCS 页仍有可展示项；断言展示页恰好 24 条，按 BCS 原顺序，`hasMore` 来自第 25 条合格记录，`nextOffset` 指向该记录的 BCS 原始位置，下一页不重叠。`metadata/queries` 每批最多 100 对、`page_size` 覆盖整批、显式带登录 Human 的 `user_id`（不可用所选 Bot 身份代替）、按 `bcn_bot_id` 匹配而非响应顺序；未知元数据保留 BCS 原生 Bot、非 service 的 `online_available:null` 不过滤。测试中显式设置运行时 `PUBLIC_BOT_BACKEND_ENABLED=false`；未配置 Backend 时必须 **0 次** metadata/profile/chat 请求；网络／5xx 返回 BCS 候选并提示增强不可用，401/403 仍走认证错误路径。无候选与过滤完所有候选返回 `hasMore=false`，不重复扫描同一 offset。额外放入上千条全被过滤的候选，确保每次上游读不超过 100 条、扫描受 BCS total 与 AbortSignal 限制，无无限循环；记录最坏情况下为凑满 24 条所需的批次数。

再补身份与共用过滤测试：`identityService.test.ts` 不预填 workspace Store，mock `bots/mine` 返回真实 `human_7` 和一个受管物理 Bot，调用真实 `loadIdentities()`；Backend disabled 时两种身份均保留，`listBots` 为 0 次；enabled 时仍补充 engine/botType，保留原全栈行为。每例恢复 capability/runtime 配置，避免模块单飞状态互相污染。

新增外壳集成用例，不能只直接调用 `loadIdentities()`：从空 workspace Store 挂载真实 `AppShell` 和 `SquareIdentityPicker`，保留 `workspaceService.initWorkspace`、`useSpaceContext` 及 capability 的真实实现，只在网络／Controller 边界供给 OAuth 用户、`bots/mine` 及 full 模式的外壳响应；此时发现列表尚未迁移，Task 3 再将完整发现页接入同一个用例，不提前宣称页面端到端通过。Backend disabled 时断言 Human + 受管 Bot 可选择，且 `GET /openapi/v1/bots`、`POST /openapi/v1/bots/spaces/personal/initialize`、`GET /openapi/v1/bots/work-order-notifications/unread-count` 请求均为零；推进 fake timer 超过 30 秒，再触发 hidden→visible，仍不得出现通知轮询。full 模式验证空间初始化及铃铛首拉／轮询继续运行；每例恢复 timer、可见性和模块单飞状态。`getShellVisibility` 的测试显式覆盖运行时 false、true 以及无覆盖时的默认行为。

共用 metadata 测试以乱序响应验证按 `bcn_bot_id` 匹配；覆盖 service false/true、未知 Bot、非 service null、网络／5xx、401/403 和 AbortError；过滤不得改动输入的 `bcsStatus`、推荐分数或短画像。

```ts
const result = await readPublicBotPage({ actorId: 'human_7', userId: '7', offset: 0, pageSize: 24,
  backendEnabled: true, fetchCandidates, fetchMetadata, signal });
expect(result.items).toHaveLength(24);
expect(result.hasMore).toBe(true);
expect(result.nextOffset).toBe(30); // fixture 原始下标 0..5 被过滤；6..29 为前 24 个，30 是预读项
expect(fetchMetadata).toHaveBeenCalledWith('7', expect.any(Array), signal);
```

- [ ] **Step 2: 红灯。** Run: `cd apps/frontend-nextgen && npx jest test/services/collaborationSquare/publicBotCursor.test.ts test/services/collaborationSquare/publicBotMetadata.test.ts test/services/workspace/identityService.test.ts test/shell/bcsOnlyDiscoveryBoot.test.tsx test/hooks/useSpaceContext.test.ts test/capabilities/defaultCapabilities.test.ts --runInBand`。Expected: 新模块不存在，或 Backend disabled 时身份补充、空间初始化、通知轮询仍发请求。
- [ ] **Step 3: 最小游标实现。** 以 BCS offset 扫描固定上限 100 的页，逐条推进原始位置；每批先映射成 `PublicBot` 并调用共用 `filterPublicBotsByMetadata`，从保留的 Bot ID 集合判断每条原始候选是否合格，原始 offset 不按过滤后的数组下标计算；只有 Backend 已启用时 helper 才查 `listBotMetadata`，以登录 Human 的真实 user ID 调用 Backend（该接口校验请求者身份），并用 BCS UUID（对 Backend 能解析的复合 ID 拆成 Bot/Owner）关联返回的稳定 `bcn_bot_id`；未匹配的记录不排除。第 `pageSize + 1` 条合格记录（展示页 24 条时即第 25 条）用作 `hasMore` 预读，下一页从它在 BCS 的原始下标重新取，不发生永久丢失。增补 `BotCandidatePage={items:PublicBot[],nextOffset:number,hasMore:boolean}`，Group/Task 仍用原 `CollaborationSquarePage<T>`；在 `AppCapabilities` 中加 `getPublicBotBackendEnabled(): boolean`，默认全栈未显式关闭时为 `true`，运行时 `window.AVERNET_RUNTIME_CONFIG.PUBLIC_BOT_BACKEND_ENABLED === false` 时为 `false`，由部署 profile 写入；本地 `config.local.ts` 在 BCS-only profile 下通过编译常量 `TEAMCLAW_PUBLIC_BOT_BACKEND_ENABLED=false` 提供相同选择，部署时显式运行时设置优先。不得将 Backend 403 当成“未部署”，也不应把无对应元数据的 BCS Bot 当作 unavailable。

在 `publicBotMetadata.ts` 实现 `filterPublicBotsByMetadata<T extends PublicBot>({ bots, userId, backendEnabled, fetchMetadata, signal }): Promise<T[]>`，以完整对象筛选和安全字段增强，保持顺序及泛型附带字段，不另复制智能搜索版策略。`fetchMetadata` 回调签名为 `(userId: string, pairs: BotMetadataQueryItem[], signal?: AbortSignal) => Promise<BotMetadataDto[]>`，由 adapter 封装真实 Controller 和信封检查；无可解析的身份对时不发请求。Controller 增加第三个可选 `signal` 参数，保持旧调用方兼容。身份加载在 `enrichBotMetadata` 发请求之前使用同一 capability 返回未增强 items；不要等 Backend 404 后才降级，也不改变 BCS `bots/mine`、单飞逻辑或身份授权。

同一个能力决策还必须覆盖外壳：在 `defaultCapabilities.ts` 定义局部 `publicBotBackendEnabled(): boolean`，读取前述运行时／编译时配置，供 `getPublicBotBackendEnabled` 和 `getShellVisibility` 共用；不从默认能力实现反向调用 registry 造成循环。Backend disabled 时 `notificationBell=false`，沿用 `AppSidebar` 的现有条件挂载自然停止 `NotificationBell/useNotifications`，不在 notification service 捕获 404 来伪装禁用。`ensurePersonalSpaceOnAppEntry()` 在检查或创建单飞 Promise、调用 `ensureUserId` 或 Admin 请求之前直接返回 resolved Promise。保留 `AppShell` 的 `workspaceService.initWorkspace()`，不能因为禁用空间初始化而不加载角色。

```ts
export interface BotCandidatePage {
  items: PublicBot[];
  nextOffset: number;
  hasMore: boolean;
}
// 由 CollaborationSquareApiAdapter 读取能力，再作为参数传给纯游标服务：
const backendEnabled = getCapabilities().getPublicBotBackendEnabled();
const page = await readPublicBotPage({ actorId, userId, offset, pageSize, backendEnabled,
  fetchCandidates, fetchMetadata, signal });
// defaultCapabilities 的能力实现：runtime 值存在时优先，否则使用
// TEAMCLAW_PUBLIC_BOT_BACKEND_ENABLED（默认全栈 true）。
// false → 完全不调用 listBotMetadata；true → 逐批最多 100 个。
// identityService::enrichBotMetadata 在 hasBots 检查和任何 Backend I/O 前：
if (!getCapabilities().getPublicBotBackendEnabled()) return items;
// defaultCapabilities：publicBotBackendEnabled 为本 Task 定义的本地配置读取函数。
getPublicBotBackendEnabled: publicBotBackendEnabled,
getShellVisibility: () => ({
  status: 'available',
  value: { spaceSwitcher: false, notificationBell: publicBotBackendEnabled() },
}),
// useSpaceContext::ensurePersonalSpaceOnAppEntry 的第一条判断；其余原逻辑保留。
if (!getCapabilities().getPublicBotBackendEnabled()) return Promise.resolve();
// botController：现有 listBotMetadata(params, body) 扩展第三个可选参数。
export function listBotMetadata(
  params: BackendUnknownRecord, body: BotMetadataQueryBody, signal?: AbortSignal,
) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotMetadataDto>>>(
    BOT_ENDPOINTS.metadataQueries,
    { method: 'POST', params: userScopedParams(params), data: body, signal },
  );
}
```

- [ ] **Step 4: 绿灯和回归。** Run: `cd apps/frontend-nextgen && npx jest test/services/collaborationSquare/publicBotCursor.test.ts test/services/collaborationSquare/publicBotMetadata.test.ts test/services/workspace/identityService.test.ts test/shell/bcsOnlyDiscoveryBoot.test.tsx test/hooks/useSpaceContext.test.ts test/capabilities/defaultCapabilities.test.ts test/deploy/runtimeConfig.test.ts --runInBand && npm run typecheck`。Expected: 游标、过滤与失败降级符合测试，BCS-only 冷启动和通知周期内没有 Backend 请求，full 原外壳行为保留；Task 5 的 `runtime-config.js` 接线后复跑部署测试。Commit: `git add apps/frontend-nextgen/src/hooks/useSpaceContext.ts apps/frontend-nextgen/src/capabilities apps/frontend-nextgen/src/typings/global.d.ts apps/frontend-nextgen/config/config.ts apps/frontend-nextgen/src/services/collaborationSquare/publicBotCursor.ts apps/frontend-nextgen/src/services/collaborationSquare/publicBotMetadata.ts apps/frontend-nextgen/src/services/workspace/identityService.ts apps/frontend-nextgen/src/services/backendApi/bots/botController.ts apps/frontend-nextgen/src/domain/collaborationSquare/types.ts apps/frontend-nextgen/test && git commit -m 'feat(frontend-nextgen): page BCS bots with optional metadata filter'`。

### Task 3：发现页调用 BCS、图片画像降级及智能搜索模式

**Files:** Modify `src/services/collaborationSquare/collaborationSquareApiAdapter.ts`、`src/services/collaborationSquare/collaborationSquareGateway.ts`、`src/services/collaborationSquare/collaborationSquareService.ts`、`src/services/collaborationSquare/mockCollaborationSquareAdapter.ts`、`src/services/collaborationSquare/unsupportedCollaborationSquareAdapter.ts`; `src/hooks/useCollaborationSquareList.ts`、`src/hooks/useCollaborationSquare.ts`、`src/pages/Workspace/Chat/hooks/useLegacyShareLinkGuard.ts`; `src/domain/collaborationSquare/types.ts`、`src/domain/collaborationSquare/mapper.ts`; `src/components/CollaborationSquare/PublicBotCatalogPanel/index.tsx`、`src/components/CollaborationSquare/SquareSearchBar/index.tsx`; tests `test/services/collaborationSquare/collaborationSquareApiAdapter.test.ts`, `test/hooks/useCollaborationSquare.test.tsx`, `test/hooks/useCollaborationSquareList.test.tsx`（新建）、`test/collaborationSquareService.test.ts`、`test/hooks/useLegacyShareLinkGuard.test.tsx`、`test/services/collaborationSquare/unsupportedCollaborationSquareAdapter.test.ts`、`test/shell/bcsOnlyDiscoveryBoot.test.tsx`。

- [ ] **Step 1: 写失败测试。** 普通列表及名称过滤必须调用 BCS v1 `candidates` 而不是 Backend catalog；智能模式调用 BCS 内部 v1 `candidates/search`，`search_mode=name_fallback` 时展示“名称匹配”，`semantic` 保留分数／短画像。角色或搜索词变化时旧 AbortSignal 被取消，后到结果不能覆盖新角色；Hook 用 `nextOffset/hasMore` 不计算精确 `total`，分享链接反查仍能找到过滤后第二页中的目标。申请弹窗未配置 Backend 时直接从所点候选项构建 `{capabilities:[],skills:[]}`，不读 Backend profile；Backend 启用时可补画像但失败回退 BCS 摘要。

智能模式分别测试 `semantic` 与 `name_fallback`：同一组候选中 service unavailable 在普通列表与两种智能结果中均被过滤，unknown/native Bot 保留；关闭 Backend 时 metadata 请求数为 0。开启时最多查询这一批 20 条的 metadata，不补页；部分或全部被过滤后仍保留原 `search_mode`、顺序及分数／短画像。网络／5xx 保留 BCS 结果并提示增强不可用，401/403 沿既有鉴权路径，AbortError 原样传播。

旧链接测试通过真实 `mapBcsBotCandidate` 产生 `bcsStatus=online` 的目标，即使它在第二展示页也可正常打开申请弹窗；`hidden` 或未查到目标保持失效提示。同时保留旧 Catalog 的 `ACTIVE`、非 ACTIVE、缺失状态用例。不得用 `status:'ACTIVE'` 替代 BCS DTO 来证明迁移成功；普通列表仍不额外排除 hidden Bot。另将 Task 2 的外壳测试扩展为真实 `AppShell` + 发现页 + 角色选择器，使用真实 Gateway/adapter，只在网络／Controller 边界提供 BCS 候选与好友响应；不得 mock 掉身份加载、空间初始化或通知 Hook。复测冷启动、30 秒轮询窗口及 hidden→visible，确认完整页面仍没有任何 Backend 请求。

```ts
expect(mockedListBcsBotCandidates).toHaveBeenCalledWith(
  'human_7', expect.objectContaining({ offset: 0, limit: 24 }), expect.anything(),
);
expect(mockedSearchBcsCandidates).toHaveBeenCalledWith(
  'assistant:owner-7', expect.objectContaining({ q: '规划', purpose: 'discovery' }), expect.anything(),
);
expect(mockedBackendCatalogSearch).not.toHaveBeenCalled();
```

- [ ] **Step 2: 红灯。** Run: `cd apps/frontend-nextgen && npx jest test/services/collaborationSquare/collaborationSquareApiAdapter.test.ts test/hooks/useCollaborationSquareList.test.tsx test/hooks/useLegacyShareLinkGuard.test.tsx --runInBand`。Expected: 仍访问 Backend Catalog、不能使用新游标。
- [ ] **Step 3: 最小接线与清理受影响调用方。** `listBotPage` 消费 Task 2 的 `readPublicBotPage`，`discoverBots` 消费 BCS 搜索及 `search_mode`，将映射后的整批候选交给 Task 2 同一个 `filterPublicBotsByMetadata` 再回填关系，返回结果继续携带原 `search_mode`，过滤不触发另一次 BCS 搜索；将 `discoverBots` 的 Gateway/service/mock 返回类型一并改为 `PublicBotDiscoveryResult={items:PublicBot[],searchMode: 'semantic'|'name_fallback'|'empty_query'}`，映射 wire 的 `search_mode`，Hook 独立保留该模式供名称匹配提示使用，不能仅返回数组而丢失空结果的模式。`PublicBot` 增加可选 `score`（仅语义命中赋值），与已有 `shortProfile` 一起通过共用过滤保留；前端 `getBotProfile` 改为接收已选 `PublicBot`（同时调整 gateway/service/mock 的签名及测试），BCS-only 从该对象生成安全摘要，Backend 增强存在才读旧 profile。普通列表已由 BCS 返回 `is_friend`，只补查 sent/pending 申请；智能搜索从现有 BCS connections 和 pending 回填，不信任 Backend 状态。Hook 保存 BCS 原始 `nextOffset`，当前搜索词／选中角色变化时复位；`resolveSharedBot/resolvePublicBotById` 的停止条件换成 `hasMore` 而不是 `page * pageSize >= total`。在 domain `types.ts` 增加 `isPublicBotShareTargetAvailable`，旧链接守卫用它替换 `!bot.status || bot.status === 'ACTIVE'`：BCS 项明确检查 `bcsStatus`；只有非 BCS 的旧 Catalog 项沿用原生命周期判定。BCS mapper 不写 Backend `status`，可选 metadata/profile 增强保留 `bcsStatus`，不能用返回的 `ACTIVE` 覆盖 hidden。原有 Backend catalog 控制器如有其它消费者，保留其独立 API，不做无关删除。

```ts
// domain/types.ts；只用于原有旧链接可用性守卫，不作为发现列表过滤条件。
export function isPublicBotShareTargetAvailable(bot: PublicBot): boolean {
  if (bot.bcsStatus !== undefined) return bot.bcsStatus === 'online';
  return !bot.status || bot.status === 'ACTIVE';
}
// useLegacyShareLinkGuard.ts：if (bot && isPublicBotShareTargetAvailable(bot)) { ... }
// BCS mapper：bcsStatus: candidate.bot.status，不设置 status。

// Hook：普通 Bot 列表的游标与群 offset 各自独立
currentBotOffset.current = 0;
const result = await collaborationSquareBotService.listBotPage(
  { offset: currentBotOffset.current, pageSize: COLLABORATION_SQUARE_PAGE_SIZE, ...viewerFields },
  humanBotContext ?? undefined, controller.signal,
);
appendBots(result.items);
currentBotOffset.current = result.nextOffset;
applyHasMore(result.hasMore);
```

- [ ] **Step 4: 绿灯与局部提交。** Run: `cd apps/frontend-nextgen && npx jest test/services/collaborationSquare/collaborationSquareApiAdapter.test.ts test/hooks/useCollaborationSquare.test.tsx test/hooks/useCollaborationSquareList.test.tsx test/components/CollaborationSquare/PublicBotCatalogPanel.test.tsx test/hooks/useLegacyShareLinkGuard.test.tsx test/domain/collaborationSquare/bcsBotMapper.test.ts test/collaborationSquareService.test.ts test/shell/bcsOnlyDiscoveryBoot.test.tsx --runInBand && npm run typecheck`。Expected: BCS-only 无 Backend 调用、分享链接和加载更多不漏页、智能回退可识别。Commit: `git add apps/frontend-nextgen/src apps/frontend-nextgen/test && git commit -m 'feat(frontend-nextgen): use BCS for public Bot discovery'`。

### Task 4：所选角色的申请处理、写操作代次隔离与 Human 建聊开关

**Files:** Create `src/components/CollaborationSquare/FriendRequestsPanel/index.tsx`、`src/hooks/useFriendConnectionRequests.ts`、`src/hooks/useCollaborationActorOperationScope.ts`; Modify `src/components/CollaborationSquare/PublicBotCatalogPanel/index.tsx`、`src/components/CollaborationSquare/SquarePageShell/index.tsx`; `src/hooks/useCollaborationSquare.ts`; `src/services/collaborationPrivacy/botVisibilityCopy.ts` 或其消费组件（只改到达申请处理的入口）； `src/domain/collaborationSquare/types.ts`; Create `test/components/CollaborationSquare/FriendRequestsPanel.test.tsx`、`test/hooks/useFriendConnectionRequests.test.tsx`、`test/hooks/useCollaborationActorOperationScope.test.tsx`; Modify `test/hooks/useCollaborationSquare.test.tsx`。

- [ ] **Step 1: 写失败 DOM 测试。** 两名登录用户：Human 发申请后看到 sent+pending；Owner 在角色选择器切到目标 Bot 后拉 `direction=received,status=pending`，通过／拒绝按钮调用现有 BCS Controller 并刷新；无权限或切换角色后不得展示上一角色列表；错误态可重试，不显示假的成功。Backend disabled 时 Human 自动批准后不触发 `createBotSession` 且卡片展示好友，已有好友点击也不触发 Backend 私聊；enabled 时仍保留原私聊跳转，建聊失败保留好友成功态。

用可手动 resolve/reject 的 Promise 分别覆盖申请、批准、拒绝、撤销和建聊：角色 A 发起操作后切到 B，让 B 已加载同一目标 Bot 的不同关系，然后完成 A 的请求；B 的卡片、申请列表、busy 和错误状态不能变化，不能触发旧角色的后续建聊、刷新或导航。再测 A→B→A、卸载／重新挂载，以及成功和失败两类迟到响应；重新挂载后同一目标不能被遗留 busy 永久禁用，旧 finally 也不能清除新操作的 busy；只比较 actor ID 或只检测 AbortError 均不足。建聊前切角色时不得发建聊请求；建聊已经发出后切角色，允许服务端完成但不得导航。切回 A 时新读取可以显示已提交的关系，不发送补偿性撤销请求。

```tsx
/** @jest-environment jsdom */
// 用现有身份 Store fixture 和 Jest mock 渲染已选 Bot；替换 BCS Controller 响应。
expect(mockedListFriendConnectionRequests).toHaveBeenCalledWith(
  expect.objectContaining({ actor_type: 'bot', actor_id: 'bot-7', direction: 'received', status: 'pending' }),
  expect.anything(),
);
expect(mockedAcceptFriendConnectionRequest).toHaveBeenCalledWith('request-1', expect.anything());
expect(mockedCreateBotSession).not.toHaveBeenCalled();
```

- [ ] **Step 2: 红灯。** Run: `cd apps/frontend-nextgen && npx jest test/components/CollaborationSquare/FriendRequestsPanel.test.tsx test/hooks/useFriendConnectionRequests.test.tsx test/hooks/useCollaborationActorOperationScope.test.tsx test/hooks/useCollaborationSquare.test.tsx --runInBand`。Expected: 无处理入口、BCS-only 仍建聊，或旧角色写响应污染新角色／触发过期导航。
- [ ] **Step 3: 最小 UI 与状态更新。** 在 Bot 页标题操作区或 Bot 列表顶部加“好友申请”入口，展开所选角色的「发出／收到」分页列表；`actor_type/id` 来自 `useCollaborationSquareActorContext`，Human 只看本人、Bot 管理权限由 BCS 复验。申请列表与通过／拒绝／撤销由 `useFriendConnectionRequests` 调用 `collaborationFriendConnectionController.ts` 的现有函数；切换角色用 AbortController 清理读取，并为新角色重新加载。好友写操作必须额外使用下面的代次守卫，不能把 abort 当成服务端回滚。Backend 配置关闭时，将 `resolvePublicBotPrimaryAction` 的 `open_human_bot_conversation` 转为“已是好友”显示、禁止建聊；Backend 配置开启时延续已有建聊，失败时提示“已成为好友，但暂未进入对话”而非把已批准好友当成申请失败。当前仅指向 `/ticket-center` 的好友审批说明在 BCS-only 模式指向本入口。

在新 Hook 中定义 `useCollaborationActorOperationScope(scopeKey: string)`，返回 `captureOperation(): () => boolean`。`scopeKey` 由登录 userId、当前 resource、actor.type/id 组成，使用 `JSON.stringify` 编码数组而不是易冲突的字符串拼接。`useLayoutEffect` 在角色提交时更新 key 和递增 epoch，cleanup 再递增 epoch 并置 inactive，覆盖卸载与 A→B→A；每次捕获返回独立 guard。`useCollaborationSquare` 和 `useFriendConnectionRequests` 各自调用同一个 Hook，guard 限制各自生命周期内的回调，不新增全局状态机。

```ts
// src/hooks/useCollaborationActorOperationScope.ts
import { useCallback, useLayoutEffect, useRef } from 'react';

export function useCollaborationActorOperationScope(scopeKey: string) {
  const scope = useRef({ key: scopeKey, epoch: 0, active: false });
  useLayoutEffect(() => {
    scope.current = { key: scopeKey, epoch: scope.current.epoch + 1, active: true };
    return () => {
      scope.current = { ...scope.current, epoch: scope.current.epoch + 1, active: false };
    };
  }, [scopeKey]);
  return useCallback(() => {
    const started = scope.current;
    return () => scope.current.active
      && scope.current.key === started.key && scope.current.epoch === started.epoch;
  }, []);
}
```

在请求发出前捕获 guard；把原 `runBusy` 的好友操作包装补成接受该 guard 的局部分支，catch/finally 也先判断代次，避免旧失败提示或 busy 清理影响新角色的同一目标。角色变化／卸载的同步 cleanup 清理本 Hook 当前代次持有的 busy keys（用局部 ref 记录），不等待旧请求 finally，也不全局 reset 其它模块状态；新代次重新建立自己的 busy 归属。群操作不改语义。成功后申请列表／卡片刷新发起前及刷新结果应用时都核验同一 guard；画像等读取仍用现有取消／过期结果策略。旧写入成功不做回滚，重新选择该角色时以新读回为准。

```ts
// useCollaborationSquare：已通过 humanBotContext / activeActor 非空校验。
// captureOperation 来自上述 Hook；外层包装保留 busy 防重与上述局部 busy 归属/cleanup。
// 以下为好友申请的异步结果处理边界。
const isCurrent = captureOperation();
store.setBusy(busyKey, true);
try {
  const result = await collaborationSquareBotService.requestBotFriendship(
    bot.id, humanBotContext, bot.friendRequestBotId, activeActor,
  );
  if (!isCurrent()) return;
  store.updateBotRelationship(targetId, result.status);
  if (result.status === 'friend') {
    notifySuccess('好友关系已建立');
    if (activeActor.type === 'human' && getCapabilities().getPublicBotBackendEnabled()) {
      try {
        const conversation = await collaborationSquareBotService.openBotConversation(bot.id, humanBotContext);
        if (!isCurrent()) return;
        history.push(getCollaborationBotConversationUrl(bot.id, conversation.sessionId));
      } catch {
        if (isCurrent()) notifyError('已成为好友，但暂未进入对话');
      }
    }
  } else if (result.status === 'applying') notifySuccess('好友申请已提交');
  else notifyError('当前未建立好友关系，申请未提交');
} catch (error) {
  if (isCurrent()) {
    // 保留现有 target_invalid 清卡片逻辑，其它错误沿用现有错误映射。
    if (error instanceof CollaborationSquareError && error.code === 'target_invalid') {
      handleTargetInvalid('bot', bot.id);
    } else notifyError(getCollaborationSquareErrorMessage(error));
  }
} finally {
  if (isCurrent()) store.setBusy(busyKey, false);
}
```

已有好友直接打开私聊的分支也必须在 await 后通过同一 guard 才能导航；通过／拒绝／撤销处理复用该守卫规则，不能仅修复申请分支。能力关闭时继续禁止所有建聊请求。测试双故意在 abort 后仍 resolve/reject，以证明保护来自代次而非 transport 的取消实现。

- [ ] **Step 4: 绿灯并提交。** Run: `cd apps/frontend-nextgen && npx jest test/components/CollaborationSquare/FriendRequestsPanel.test.tsx test/hooks/useFriendConnectionRequests.test.tsx test/hooks/useCollaborationActorOperationScope.test.tsx test/hooks/useCollaborationSquare.test.tsx --runInBand && npm run typecheck`。Expected: 角色列表与审批刷新正确；过期写响应不更新新角色、不导航、不清理新操作状态；无 Backend 环境不建聊。Commit: `git add apps/frontend-nextgen/src apps/frontend-nextgen/test && git commit -m 'feat(frontend-nextgen): complete role-scoped BCS friend approvals'`。

### Task 5：BCS-only 同源部署、配置测试和全链路验收

**Files:** Modify `deploy/docker-entrypoint.sh`; `Dockerfile`; `config/config.local.ts`; `docs/deployment.md`; Create `deploy/nginx/default.bcs-only.conf.template`; Modify `test/deploy/runtimeConfig.test.ts`, `test/config/bbsProxyConfig.test.ts`; Create `test/deploy/bcsOnlyProxy.test.ts`; Create `docs/bcs-only-friend-discovery-validation.md`（人工浏览器验收步骤和证据模板）。不修改 `deploy/nginx/default.conf.template` 原全栈转发规则，除非原模板真实缺少必须的同源路径。

- [ ] **Step 1: 写失败部署测试。** `FRONTEND_API_PROFILE=bcs-only` 时，测试脚本按现有 `runtimeConfig.test.ts` 的模式先把 `/usr/share/nginx/html/runtime-config.js` 和新增的 `/etc/nginx/...` 模板源／目标路径替换到临时目录，再执行 shell；只要求非空 `BCS_UPSTREAM`，不要求 Backend/Gateway/Admin/Task 等变量；缺 BCS 上游即拒绝启动；生成运行时 `PUBLIC_BOT_BACKEND_ENABLED:false`。默认 `full` 保持全部既有必填变量和 `true`；未知 profile 立即报错。BCS-only Nginx 配置精确将 `/openapi/v1/auth/`、必要的 `/openapi/v1/collaboration/`、`/api/v1/collaboration/bots/.../candidates/search` 同源代理 BCS，未匹配的 `/api/` 与 `/openapi/` 明确返回 404，不能回退到 SPA `index.html` 或伪装成可用的 BCS 端点。添加 dev server BCS-only proxy 配置测试，确保该候选搜索路径先于 `/api` 通配规则。

```ts
const bcsOnlyConfig = readFileSync('deploy/nginx/default.bcs-only.conf.template', 'utf8');
expect(bcsOnlyConfig).toContain('BCS_UPSTREAM');
expect(bcsOnlyConfig).toContain('location /openapi/v1/collaboration/');
expect(runtimeConfig.PUBLIC_BOT_BACKEND_ENABLED).toBe(false);
```

- [ ] **Step 2: 红灯。** Run: `cd apps/frontend-nextgen && npx jest test/deploy/runtimeConfig.test.ts test/deploy/bcsOnlyProxy.test.ts test/config/bbsProxyConfig.test.ts --runInBand`。Expected: 尚无 BCS-only 代理模板与 profile 配置。
- [ ] **Step 3: 最小部署实现。** 在 entrypoint 验证 `FRONTEND_API_PROFILE` 的 `full|bcs-only` 两值；`bcs-only` 校验 `BCS_UPSTREAM`，将新增模板拷到 `/etc/nginx/templates/default.conf.template` 后由官方 Nginx entrypoint 进行标准变量替换；`full` 完全保留当前模板及其 required_vars。生成 `window.AVERNET_RUNTIME_CONFIG.PUBLIC_BOT_BACKEND_ENABLED` 布尔值，不能写字符串 `'false'`。Dockerfile 把 BCS-only 模板复制到与当前 `/etc/nginx/templates` 不同的只读配置目录；`config.local.ts` 在显式开发 profile 下编译 `TEAMCLAW_PUBLIC_BOT_BACKEND_ENABLED=false` 并仅把已定义的 BCS 子树优先代理到 BCS，不更改其他 profile 或引入私有域名。静态服务继续通过 `try_files ... /index.html` 处理发现页深链。

```sh
case "${FRONTEND_API_PROFILE:-full}" in
  full) required_vars="BCS_AUTH_UPSTREAM TASK_ENGINE_UPSTREAM TEAMCLAW_GATEWAY_UPSTREAM TEAMCLAW_ADMIN_UPSTREAM PRIVATE_CHAT_MANAGEMENT_UPSTREAM PRIVATE_CHAT_SESSION_UPSTREAM CLAWWEB_UPSTREAM AIXCORE_UPSTREAM BCS_ENDPOINT_PRE BCS_ENDPOINT_PROD" ;;
  bcs-only) required_vars="BCS_UPSTREAM"; cp /etc/nginx/template-profiles/default.bcs-only.conf.template /etc/nginx/templates/default.conf.template ;;
  *) echo "Unknown FRONTEND_API_PROFILE" >&2; exit 1 ;;
esac
# 运行时脚本中输出 PUBLIC_BOT_BACKEND_ENABLED: true/false（JS 布尔量）。
```

- [ ] **Step 4: 绿灯、前端门禁。** Run: `cd apps/frontend-nextgen && npx jest test/deploy/runtimeConfig.test.ts test/deploy/bcsOnlyProxy.test.ts test/config/bbsProxyConfig.test.ts --runInBand && npm run check:open-core && npm run ci`。Expected: 运行时 profile、防私有依赖、类型检查、Lint、全部 Jest 与构建通过。再运行 `docker build -f apps/frontend-nextgen/Dockerfile .`（若 Docker 不可用则准确记录未运行原因）；用临时容器启动两种 profile，验证 BCS-only 不需要 Backend 变量、默认 full 仍需原变量。
- [ ] **Step 5: 用真实浏览器验收并提交。** `docs/bcs-only-friend-discovery-validation.md` 写明两名由现有真实 OAuth provider 登录的测试用户、一个目标 Bot、一个申请者 Human 和一个受管 Bot 的准备步骤；BCS 采用现成的 `[api.auth]` OAuth-only、`trusted_browser_origins`，不使用 mock 生产身份。浏览器依次确认：普通列表与名称搜索、无 BCSFuse 时 `name_fallback`、Human-only 与 Bot-only 交叉可见性、申请后刷新 pending、Owner 切 Bot 角色收件并批准／拒绝、申请方刷新看到好友、无 Backend 请求（从空身份 Store 的页面冷启动开始记录，确保 `bots/mine` 返回 Human + 受管 Bot 时，既没有 `GET /openapi/v1/bots`，也没有个人空间 initialize 或 work-order-notifications 请求；停留至少 30 秒并切后台再恢复可见，验证通知轮询仍未启动，不能只从列表加载后开始抓包）、无自动建私聊、未授权角色被拒。另在 full profile 验证相同 service unavailable 项在普通列表／智能语义／智能名称回退均不出现，metadata 乱序不影响排名，旧分享链接的 BCS online 目标不再被误报为下线；补操作进行中切换 Human/Bot 角色以及 A→B→A 的场景，确认旧写入成功不会污染当前卡片、申请列表或触发旧会话跳转；切回原角色的新读回仍反映真实持久化结果。这些场景分别记录环境与执行结果，不以 BCS-only 验收代替。用浏览器网络面板与 BCS 日志记录实际结果，证据不能虚写为已通过；环境无法提供 OAuth provider 时写“未执行”及原因。Commit: `git add apps/frontend-nextgen && git commit -m 'build(frontend-nextgen): serve BCS-only friend discovery profile'`。

## 完成标准与交付

实施者检查每个新增或修改的源文件 `< 1000` 行，特别关注当前约 756 行的 `capabilities/types.ts` 和 395 行的 `collaborationSquareApiAdapter.ts`；新扫描逻辑不得堆到 adapter。默认多服务部署不退化，BCS-only 从真实外壳冷启动到好友闭环及页面恢复可见均无 Backend/Gateway 请求（含空间初始化和通知轮询）；异步好友写入及建聊结果仅能作用于仍有效的角色代次。与 BCS 计划分别记录提交、测试和不能运行的门禁；该文件不包含 BCS 源码修改步骤，最终跨栈浏览器证据由本计划维护。
