# Task 模块 V2 升级(协同任务副屏·下钻方案A)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 frontend-nextgen 的任务副屏升级为 demo `CoopTaskV2` 的产品形态:面包屑两级栈(任务总览 ↔ 节点副屏)、点节点跳会话、下钻分离、边语义/返工/HITL 徽章、产物区、多任务切换;后端契约不变,PRD 差异全部由前端 mapper 派生。

**Architecture:** 演进而非重写——保留 `TaskPanelFetcher`(轮询)/`contract.ts`/`taskPanelMapper.ts`;新建 `v2/` 组件子目录承载两级栈壳层,新建 `statusDerivation.ts`/`artifactDescriptor.ts` 纯派生模块承接 §3.3 全部派生规则,导航态入 zustand store `taskSidePanelStore`(经 props 注入资产层,assets 不 import stores);唯一新契约 = 节点级产物 Descriptor(与后端 `task-artifact-domain` 读侧已实现形状对齐)。

**Tech Stack:** UmiJS Max 4 / React 18 / Zustand / styled-components(资产内)/ Tailwind+Radix(app 层)/ jest(node 默认,DOM 用例需 jsdom docblock)/ @tc-chat/ui ChatLayout.Panel

**Spec:** `specs/2026-10-20-task-module-v2-upgrade/spec.md`(本目录,实施时随行阅读;冲突以 spec 为准)
**PRD:** `/Users/jian.jiangj/Git/frontend/TeamClaw-demo/src/pages/Collaboration/components/CoopTaskV2/`(代码即 PRD)+ `src/mocks/data/coopTaskDemoV2.ts`

## 前置 Setup(开工前,非任务)

- [ ] `npm ci`(当前 checkout 无 node_modules,typecheck/test/build 全部无法运行)。
- [ ] `npm ci` 后读 `node_modules/@tc-chat/ui/es/ChatLayout/types.d.ts` 与 `@tc-chat/core` panel 类型,核实:① `PanelTab.defaultWidth` 归一化;② `panelCommand: 'expand'|'collapse'` 的真实暴露位置(推测在 `PanelHandle` 或 bridge);③ `PanelContentProps` 是否携带面板级 handle。核实结果记入 ledger(后续 Task 5 收起接线依赖此结论)。
- [ ] 确认在分支 `dev_task_cli_1015` 上工作(用户当前任务分支,非 main);本计划不改动 `../backend` 的未提交变更。

## Global Constraints

- 源文件 ≤1000 行;`taskPanelMapper.ts` 已 678 行——新增逻辑一律进独立模块(`statusDerivation.ts`/`artifactDescriptor.ts`/`sessionResolution.ts`),mapper 只做接线。
- `src/assets/**` 不得 import `@/components|@/hooks|@/stores|@/pages|@/domain`(ESLint 强制);`@/services`、`@/shared`、`@/utils` 允许(TaskPanelFetcher 已有先例)。store 与会话跳转能力经 props/回调/adapter 注入(模式:`TaskPanelAdapterWithUser`)。
- 后端契约保持不变:不修改 `TaskDashboardResponse` 任何既有字段;唯一新增为 optional 的节点级 `artifacts`(Descriptor),与后端 `apps/backend` 分支上 `TaskArtifactDescriptorDTO` 的已实现形状逐字段对齐(kind: `text|file|url_link|code`,content 为 kind-tagged 引用元数据)。
- app 层代码禁用 antd(副屏资产用 styled-components 续现有风格);中文注释;注释密度贴邻近代码。
- 测试在 `test/` 镜像目录;node 环境默认,DOM 用例首行 `/** @jest-environment jsdom */`;单文件跑法 `npx jest test/xxx.test.ts`。
- 每任务收口前跑 `npx jest <本任务相关文件>`;全量门禁 = `npm run check:open-core && npm run ci`。
- 提交纪律:`feat(task-panel-v2): <outcome>` / `refactor(task-panel-v2): ...`,一任务 ≥1 commit,禁止 `git reset` 系操作。
- demo 的 TaskSwitcherFloat 悬浮按钮(演示脚手架)**不产品化**;拖宽降级为 `defaultWidth: 420` 固定宽度(SDK 不支持拖宽,spec §3.1-2 降级决策)。

## Rulings(计划级裁决,执行时不再讨论)

1. **产物接口不建独立 controller mock。** spec §4-2 原文预设"产物清单独立出 dashboard + 前端 mock controller"。后端 `task-artifact-domain` 实际实现(dev_task_cli_1015 未提交 diff,`schemas.py` `TaskNodeDTO.artifacts` + `openapi_v1/task/router.py` dashboard 富化)已定:读侧走 **dashboard 节点级富化**,无独立 list 端点。故前端:contract.ts 增加同形 Descriptor optional 字段 + mapper 双兼容归一(新 Descriptor > legacy `TaskArtifactDto`,去重),测试用 fixture 直接注入 Descriptor 形状的 dashboard 数据(P2 期天然走"mock"——后端未前出时该字段为 null,归一化层 fall back legacy 兼容)。组件与 store 不感知来源,后端落地零切换动作。**若后端将来真加独立 list 端点,只需新增 controller,mapper/组件不动**(保留 spec 的演进意图)。成本:偏离 spec 字面一条,已在此记录。
2. **导航态按 task 分片。** 聊天内每任务一个面板 tab(`buildTaskPanelAixUI` tab.id=`task-{taskId}`),单份全局 drillStack/selectedNodeId 会串场。spec 草图为单任务字段;实现为 `navByTask: Record<taskId, {drillStack, selectedNodeId}>` + 全局 `activeTaskId/pendingNavigation/nodeScrollSignal`。成本:store 稍复杂,严格兼容草图的语义。
3. **"生命周期系统消息展示"(PRD #8)为 no-op。** demo 中系统消息是 mock 注入;真实系统消息由后端消息通道进会话、走既有渲染,契约不变决策下前端无新工作。面板侧生命周期事件已由 `action_log → stepTraces` 呈现。仅在 L2 信息区保证步骤追踪可见(Task 7 覆盖)。
4. **定位精度 = 时间窗,并允许优雅降级。** 消息无 nodeId 契约(spec §3.2),时间窗定位对 SDK 消息 DOM 形状有依赖——纯函数定位 + DOM 侧 best-effort 查询,DOM 无时间戳可锚点时不抛错、不白屏,降级为"跳会话即可"并 console.info 一条(DOM 形状核实后可在后续迭代精确化)。
5. **L1 任务信息不删。** demo 的 Level1 只有步骤+产物两区,但现有"任务信息 Tab"(goal/描述/验收)是存量用户价值。L1 在产物区下加默认折叠的「任务信息」区(`<details>` 风格),内容复用 TaskInfoTab 的既有展示块。成本:超出 demo 一小节,PRD 未禁止。
6. **chat 端收起接线的实现方式待 Setup 核实后由 Task 5 钉死**:面包屑行收起按钮调用注入回调;若 SDK `PanelContentProps` 无面板级 handle,则收起按钮仅在宿主注入回调时渲染(宿主已有 header 级 `SidePanelCollapseButton` 兜底)。

## Review Focus

(本计划测试未覆盖、但真实使用最可能踩的五类输入;每条已指定归属任务的补测步骤)

1. **action_log 缺失 / attempt 字段缺失的真实 dashboard**(老数据):派生必须退化为 runtime 状态直映,不得 NaN/undefined 崩溃 → Task 3 Step 3 补测。
2. **pendingNavigation 指向 workspace 里不存在的会话**(任务会话被删/别的空间):消费端静默放弃跳转并 consume,不得跳错页 → Task 9 Step 5 补测。
3. **legacy 产物与 Descriptor 在同节点混布**:归一化后不重复渲染同一 artifact_id → Task 2 Step 4 补测。
4. **drillStack 顶节点在下一次轮询后已不存在**(图变更):L2 渲染空态并提供返回,不得崩溃或把栈卡死 → Task 7 Step 3 补测。
5. **两个任务 tab 各自下钻后互切**:navByTask 分片隔离,切回时各自恢复 → Task 1 Step 3 补测。

---

### Task 1: taskSidePanelStore + taskStore dead 字段清理(P1)

**Files:**
- Create: `src/stores/taskSidePanelStore.ts`
- Modify: `src/stores/taskStore.ts`(删 `preferredTab`/`preferredView` 两个 dead 字段及其 setter)
- Create: `src/hooks/taskPanelNavBinding.ts`(store → 资产层 `TaskPanelNav` 桥,业务层专用)
- Test: `test/stores/taskSidePanelStore.test.ts`

**Interfaces:**
- Produces(Task 5/7/9 消费):

```ts
// src/stores/taskSidePanelStore.ts
export interface TaskNavRecord { drillStack: string[]; selectedNodeId: string | null }
export interface TaskNavTarget {
  taskId: string;
  sessionId: string;
  nodeId?: string;
  startTime?: number | null;   // ms epoch,时间窗定位用,可 null
  endTime?: number | null;
}
export interface TaskSidePanelState {
  activeTaskId: string | null;
  navByTask: Record<string, TaskNavRecord>;
  pendingNavigation: TaskNavTarget | null;
  nodeScrollSignal: number;   // selectNode 每次 +1
  setActiveTask: (taskId: string) => void;   // 同时清 pendingNavigation,不动 navByTask
  selectNode: (taskId: string, nodeId: string, target: TaskNavTarget | null) => void;
  drillInto: (taskId: string, nodeId: string, target: TaskNavTarget | null) => void;
  backToOverview: (taskId: string) => void;   // 清 drillStack,不动 selectedNodeId
  consumePendingNavigation: () => void;
  resetAll: () => void;
}
export function useTaskSidePanelStore(): TaskSidePanelState;   // zustand create
```

```ts
// src/hooks/taskPanelNavBinding.ts —— 业务层(store)→ 资产层(props)桥
// 资产层定义的 TaskPanelNav 结构类型在 Task 5 的 taskPanelNav.ts 中,此处 import type 复述:
export interface TaskPanelNavBridge { nav: TaskPanelNav }  // 语义见 Task 5 Interfaces
export function useTaskPanelNavBinding(taskId: string): TaskPanelNav;
```

语义(demo `CoopTaskContext` 对齐):
- `selectNode`:写 `navByTask[taskId].selectedNodeId`、`pendingNavigation = target?.sessionId ? target : null`、`nodeScrollSignal+1`;target 为 null 时不发跳转但仍选中。
- `drillInto`:`drillStack` 去重 push,封顶 2(结构上 L2 无下钻热区,实际不超 1);置 pendingNavigation;**不加** scrollSignal(demo 语义)。
- `backToOverview`:清空该 task 的 drillStack。

- [ ] **Step 1: 写失败测试** `test/stores/taskSidePanelStore.test.ts`(node 环境),用例:
  - selectNode 更新选中/发跳转/滚动信号+1(target null 时不置 pendingNavigation)
  - drillInto 去重 push、封顶 2、不发滚动信号
  - backToOverview 清栈不清选中;consumePendingNavigation 置 null
  - navByTask 分片:taskA 下钻不影响 taskB(Review Focus #5)
  - setActiveTask 切换 activeTaskId 并清 pendingNavigation

```ts
// 断言骨架(节选)
const s = useTaskSidePanelStore.getState();
s.selectNode('t1', 'n2', { taskId: 't1', sessionId: 'sess-main' });
expect(s.navByTask['t1']?.selectedNodeId).toBe('n2');
expect(s.pendingNavigation?.sessionId).toBe('sess-main');
expect(s.nodeScrollSignal).toBe(1);
```

- [ ] **Step 2: 跑测失败确认** Run: `npx jest test/stores/taskSidePanelStore.test.ts`  Expected: FAIL(模块不存在)
- [ ] **Step 3: 实现 store + binding hook** zustand 单 store,≤150 行惯例(参照 taskStore 头注释守卫);`useTaskPanelNavBinding` 订阅 `navByTask[taskId]` 分片,闭包封 `selectNode/nodeId/target` 形参,返回 `TaskPanelNav` 结构(结构类型先在本文件本地声明,Task 5 落地资产层同名接口后改为 `import type from 'assets/TaskPanel/v2/taskPanelNav'`——同一形状,structural typing 下不改也行,任选并一致)。
- [ ] **Step 4: 清理 taskStore dead 字段** 删 `preferredTab`/`preferredView`/`setPreferredTab`/`setPreferredView`(全仓 grep 确认零消费);跑 `npx jest test/hooks/useTaskExecution.test.tsx`(taskStore 消费者)`、npx run typecheck`。
- [ ] **Step 5: 跑测通过** Run: `npx jest test/stores/taskSidePanelStore.test.ts`  Expected: PASS 全绿
- [ ] **Step 6: Commit** `git add ... && git commit -m "feat(task-panel-v2): add taskSidePanelStore with per-task drill navigation and drop dead taskStore prefs"`

---

### Task 2: 产物 V2 契约与归一化(P2 基座,Ruling 1)

**Files:**
- Modify: `src/assets/TaskPanel/contract.ts`(加 Descriptor DTO + `TaskNodeDto.artifacts`)
- Create: `src/assets/TaskPanel/artifactDescriptor.ts`(归一化纯函数)
- Create: `src/assets/TaskPanel/testdata/artifactFixtures.ts`(fixture,复用"本地静态数据"模式)
- Modify: `src/assets/TaskPanel/types.ts`(`TaskArtifactView` 扩展)
- Test: `test/assets/TaskPanel/artifactDescriptor.test.ts`

**Interfaces:**
- Produces(Task 4 mapper、Task 6 产物区消费):

```ts
// contract.ts 新增(逐字段对齐 apps/backend .../http/task/schemas.py TaskArtifactDescriptorDTO)
export interface TaskArtifactDescriptorDto {
  artifact_id: string;
  artifact_name: string | null;      // file 产物缺省回落 content.file_name
  kind: 'text' | 'file' | 'url_link' | 'code';
  content: Record<string, unknown>;  // kind-tagged: text→{text}; file→{storage_type:'BOT_WORKSPACE'|'SESSION_FILE', storage_id, file_name, file_id?, file_type}; url_link→{url, type}; code→{code_base_name, code_base_address, pr_link, pr_desc}
  attempt?: number | null;
  creator?: string | null;
  created_at: number;                // ms epoch
}
// TaskNodeDto 追加: artifacts?: TaskArtifactDescriptorDto[] | null   (null=后端未富化,视为空,fall back legacy run_info.artifacts)
```

```ts
// types.ts 扩展
export interface TaskArtifactView {
  id: string; name: string;
  type: 'document' | 'report' | 'link' | 'file' | 'other';   // legacy 口径保留
  kind?: 'text' | 'file' | 'url_link' | 'code' | null;       // V2 新口径
  url?: string | null; summary?: string | null;
  nodeId?: string | null;                                   // 新增:归属节点
  owner?: string | null;                                    // 新增:creator
  updatedAt: string;
  fileRef?: { storageType: 'BOT_WORKSPACE'|'SESSION_FILE'; storageId: string; fileId?: string|null; fileType?: string|null } | null; // 新增:文件寻址(前端拼预览链接)
}
```

```ts
// artifactDescriptor.ts
export function normalizeDescriptor(d: TaskArtifactDescriptorDto, fallbackNodeId: string | null): TaskArtifactView;
export function mergeArtifactLists(descriptors: TaskArtifactDescriptorDto[] | null | undefined,
                                   legacy: TaskArtifactDto[] | undefined,
                                   nodeId: string | null): TaskArtifactView[];   // Descriptor 优先,按 id 去重
export function artifactViewHref(a: TaskArtifactView, apiBaseUrl: string): string | null;
// BOT_WORKSPACE → `${apiBaseUrl||''}/api/resources/files/preview?path=${encodeURIComponent(storageId)}`
// SESSION_FILE → `${apiBaseUrl||''}/sessions/${storageId}/files/${fileId}/content`
// url_link→url;code→pr_link;text→null(无链接)
```

- [ ] **Step 1: 写失败测试** `test/assets/TaskPanel/artifactDescriptor.test.ts`,fixture 覆盖四种 kind 各≥1、file 两 storageType、legacy+Descriptor 混布去重(Review Focus #3)、file kind 缺 artifact_name 回落 file_name:
```ts
const merged = mergeArtifactLists([{ artifact_id: 'a1', kind: 'url_link', content: { url: 'https://x', type: 'dima需求' }, created_at: 1, artifact_name: null }],
                                 [{ artifact_id: 'a1', name: '旧名', type: 'link', url: 'https://x', created_at: '2026-10-20' }], 'n1');
expect(merged).toHaveLength(1);
expect(merged[0].kind).toBe('url_link');
expect(merged[0].nodeId).toBe('n1');
```
- [ ] **Step 2: 跑测失败确认** Run: `npx jest test/assets/TaskPanel/artifactDescriptor.test.ts`  Expected: FAIL(模块不存在)
- [ ] **Step 3: 实现三处契约/类型/归一化 + fixture** `MapArtifacts`(mapper 内已有 legacy 归一 `mapArtifacts`,同语义搬用)。
- [ ] **Step 4: 跑测通过** Run: 同 Step 1  Expected: PASS。`npm run typecheck` 通过。
- [ ] **Step 5: Commit** `feat(task-panel-v2): artifact descriptor contract aligned with backend artifact-domain readside`

---

### Task 3: statusDerivation 纯派生模块(P2/P3 基座,spec §3.3 全规则)

**Files:**
- Create: `src/assets/TaskPanel/statusDerivation.ts`
- Test: `test/assets/TaskPanel/statusDerivation.test.ts`(fixture 至少覆盖:串行5节点任务、attempt>1 返工任务、多下游群节点 split_work、action_log 缺失任务)

**Interfaces:**
- Produces:

```ts
export type PresentNodeStatus = 'pending' | 'running' | 'rework' | 'done' | 'merged';
export type PresentEdgeSemantics = 'split_work' | 'race' | 'fallback' | 'rework' | 'none';
export interface NodePresentation { present: PresentNodeStatus; attemptCount: number; needsHitl: boolean }
export interface PresentEdge { from: string; to: string; semantics: PresentEdgeSemantics; reason?: string }
export interface ExecDagStep { id: string; label: string }
export interface ExecDagEdge { from: string; to: string; semantics: PresentEdgeSemantics }

export function deriveNodePresentation(input: {
  runtimeStatus: NodeStatus;                 // mapper 归一后的节点态
  actionLog?: NodeActionEventDto[] | null;
  acceptanceVerdict?: 'DONE' | 'FAILED' | null;
  hasConsumedDownstream: boolean;            // 存在 DONE/running 的下游直接后继
  childTaskDone: boolean | null;             // child_task_id 指向任务 DONE→true;无子任务 null
  needsTaskAttention: boolean;
}): NodePresentation;

export function derivePresentEdges(input: {
  relations: TaskRelationDto[];              // type 恒 'DEPENDENCY'
  nodes: Array<{ id: string; presentation: NodePresentation; actionLog?: NodeActionEventDto[] | null }>;
  outDegree: Record<string, number>;          // from→出边数,split_work 判定用
}): PresentEdge[];

export function deriveNodeExecDag(actionLog: NodeActionEventDto[] | null | undefined): { nodes: ExecDagStep[]; edges: ExecDagEdge[] };
```

派生规则(spec §3.3 唯一权威,全部实现并测):
- `present`:PENDING/PLANNING→`pending`;RUNNING 且无返工征象→`running`;RUNNING 且 (attemptCount>1 且最近 acceptance verdict 非 DONE)→`rework`;DONE 且 (hasConsumedDownstream 或 childTaskDone===true)→`merged`;其余 DONE→`done`;failed/hung/cancelled 按 presentation 缺省回退动词表(见下)。
- **词表兜底(Ruling,Review Focus #1)**:NodeStatus 中 `failed|hung|cancelled` 不在 PRD 五词表内——presentation 直接以对应字符串透传给渲染层由 NODE_STATUS_TONES 渲染,`PresentNodeStatus` 类型因而扩为 `'pending'|'running'|'rework'|'done'|'merged'|'failed'|'hung'|'cancelled'`(渲染层 tokens 已有全部中文 label,不新造)。**计划改此类型定义,Task 4 接口同步。**
- `attemptCount`:`max(actionLog[].attempt ?? 1)`,actionLog 空数组/undefined → 0。
- `needsHitl`:`needsTaskAttention && runtimeStatus==='running' && acceptanceVerdict != null`。
- 边:relations 全部 `none`;`split_work`:出边 from 节点 `outDegree>1` 且目标节点为群执行(runMode group,由调用方传入 `outDegree` 时已限定)的边;`rework`:to 节点 `attemptCount>1` 的前驱边,`reason` = to 节点最近一次 `status_to==='REWORK_RERUN'` 或 attempt 变更事件(action + status_from→status_to)拼接,无则 `undefined`。
- `deriveNodeExecDag`:按 `attempt` 分组、组内按 `seq` 排成线性链;attempt N+1 组首节点从 attempt N 组尾连 `rework` 边;事件 label = `action` + payload 摘要(payload.description/payload.node_id 优先,截断 20 字)。产出仅 id/label,坐标由渲染层(DagViewV2)分层布局。

- [ ] **Step 1: 写失败测试**(node 环境)覆盖上述每条规则含边界:actionLog undefined(Review Focus #1)、attempt 缺省、outDegree=1 不产 split_work、多 attempt 链跨组连边。
- [ ] **Step 2: 跑测失败确认** Run: `npx jest test/assets/TaskPanel/statusDerivation.test.ts`  Expected: FAIL
- [ ] **Step 3: 实现 statusDerivation.ts**(≤300 行预期);无 I/O、无 React。
- [ ] **Step 4: 跑测通过** Run: 同上  Expected: PASS
- [ ] **Step 5: Commit** `feat(task-panel-v2): present-status/edge-semantics/exec-dag derivation from action_log`

---

### Task 4: mapper 接线与视图模型扩展(P2)

**Files:**
- Modify: `src/assets/TaskPanel/types.ts`(TaskNodeView/DagNodeView/DagEdgeView/TaskView 扩展)
- Modify: `src/assets/TaskPanel/taskPanelMapper.ts`(接线 Task 2/3 产物,不膨胀:仅调用+组装,新增行预算 ≤80)
- Create: `src/assets/TaskPanel/sessionResolution.ts`(node→session 目标解析,spec §3.2)
- Test: 扩展 `test/assets/TaskPanel/taskPanelMapper.test.ts`;新建 `test/assets/TaskPanel/sessionResolution.test.ts`

**Interfaces:**
- Produces(Task 5/6/7 消费):

```ts
// types.ts 扩展(PresentNodeStatus 见 Task 3 最终形,含 failed/hung/cancelled 兜底)
export interface TaskNodeView { /* 既有全部字段保留 */
  present?: PresentNodeStatus;      // 派生展示态
  attemptCount?: number;
  needsHitl?: boolean;
  drillable?: boolean;              // run_mode coop_group/bbs 或 sessionId 以 bcs_grp_ 开头
  execDag?: { nodes: ExecDagStep[]; edges: ExecDagEdge[] };
  runMode?: string | null;
}
export interface DagNodeView { /* 既有 */ present?: PresentNodeStatus; drillable?: boolean; short?: string; needsHitl?: boolean; }
export interface DagEdgeView { from: string; to: string; label?: string; semantics?: PresentEdgeSemantics; reason?: string; }
export interface TaskView { /* 既有 */
  mainSessionId?: string | null;       sourceGroupId?: string | null;
  derivedSessionIds?: string[];        // dashboard.derived_session_ids 透传
}
```

```ts
// sessionResolution.ts —— §3.2 会话解析(纯函数)
export interface ResolvedSessionTarget { sessionId: string; kind: 'group'|'bot'|'friend'; groupId?: string|null; botId?: string|null; startTime?: number|null; endTime?: number|null }
export function resolveNodeSessionTarget(node: TaskNodeView, view: TaskView): ResolvedSessionTarget | null;
// 解析顺序:① node.sessionId(mapper 已从 run_info 三键提取)→ 按 node.groupId/runMode 判 kind
//           ② runMode single_bot → TaskView.mainSessionId
//           ③ runMode coop_group/bbs → view.derivedSessionIds 按节点 assignee/source_group 匹配,无法唯一定位回退 ④ mainSessionId
// node.start/end(ms)随行作为时间窗;全链无果 → null(不跳转,Review Focus #2 前置)
```

- [ ] **Step 1: 写失败测试** mapper 测试新增断言(用 Task 2 fixture + Task 3 规则构造 dashboard):节点 present/attemptCount/needsHitl/drillable/execDag 注入、DagEdgeview.semantics/reason、TaskView.mainSessionId/derivedSessionIds、Descriptor 产物走 mergeArtifactLists 接入 nodes[].artifacts;sessionResolution 测试:4 类 run_mode 分支 + 全 miss → null + 时间窗字段随行。既有 894 行 mapper 测试保持全绿(回归红线)。
- [ ] **Step 2: 跑测失败确认** Run: `npx jest test/assets/TaskPanel/taskPanelMapper.test.ts test/assets/TaskPanel/sessionResolution.test.ts`  Expected: FAIL(新断言)
- [ ] **Step 3: 实现** mapper 接线:`mapDashboard` 调 `deriveNodePresentation`(hasConsumedDownstream 由 relations 计算一次)、`derivePresentEdges`、`deriveNodeExecDag`、`mergeArtifactLists`;TaskView 顶层补 mainSessionId/sourceGroupId/derivedSessionIds。
- [ ] **Step 4: 跑测通过 + 既有回归** Run: 同 Step 1  Expected: PASS,`npm run typecheck` 通过
- [ ] **Step 5: Commit** `feat(task-panel-v2): wire derivation into mapper and expose node/session view models`

---

### Task 5: TaskPanelV2 壳 —— 面包屑两级栈 + 收起 + defaultWidth 420(P1)

**Files:**
- Create: `src/assets/TaskPanel/v2/taskPanelNav.ts`(类型 + 本地降级 hook)
- Create: `src/assets/TaskPanel/v2/TaskPanelV2.tsx`(面包屑/L1L2 分流/收起按钮/切换条槽)
- Modify: `src/assets/TaskPanel/TaskPanel.tsx`(壳层改为 Fetcher + TaskPanelV2,三 Tab 结构退场;props 加 `nav`/`switcher`)
- Modify: `src/assets/TaskPanel/TaskPanelAdapter.tsx`(透传 `nav`/`switcher` from params)
- Modify: `src/services/tasks/taskPanelMessage.ts`(tab JSON 加 `defaultWidth: 420`)
- Modify: `src/extensions/empty.ts`(`TaskPanelAdapterWithUser` 接 `useTaskPanelNavBinding(taskId)` + switcher 注入点)
- Modify: `src/pages/MyTask/components/MyTaskDrawers.tsx`(抽屉挂载点注入 nav/switcher)
- Test: `test/assets/TaskPanel/v2/TaskPanelV2.test.tsx`(jsdom docblock)

**Interfaces:**
- Consumes: Task 1 `useTaskPanelNavBinding`、Task 4 `TaskView/TaskNodeView present 等字段`(组件先用 date-形 mock `TaskView`)
- Produces:

```ts
// v2/taskPanelNav.ts(资产层接口,业务层 structural 兼容)
export interface TaskPanelNav {
  selectedNodeId: string | null;
  drillStack: string[];                        // 顶=当前 L2 节点;空=L1
  selectNode: (nodeId: string, target: ResolvedSessionTarget | null) => void;
  drillInto: (nodeId: string, target: ResolvedSessionTarget | null) => void;
  backToOverview: () => void;
}
export function useLocalTaskPanelNav(): TaskPanelNav;   // 宿主未注入时的降级(useState 版)
// TaskPanelProps 追加: nav?: TaskPanelNav; switcher?: TaskSwitcherProps(Task 8 落地前本任务仅留类型槽)
// collapse: 面包屑行右侧收起按钮,onClick=props.onTogglePanel?.();未注入则不渲染(Ruling 6)
```

- [ ] **Step 1: 写失败测试**(jsdom)用 fetch mock 喂 dashboard:
  - L1 默认渲染(空 drilling),面包屑只显「任务总览」,点收起触发 `onTogglePanel`
  - `nav.drillInto('n2')` 后面包屑变「任务总览 › n2short」、渲染 L2 占位组件(Task 7 前 L2 渲染 `TaskNodePanelV2` 的最小占位,本任务先断言 CIS 上 L1/L2 组件名,或断面包屑+children 分流)
  - 面包屑「任务总览」按钮回调 `backToOverview`,selectedNodeId 不被清除
  - `nav` 未注入时用 `useLocalTaskPanelNav` 仍可下钻/返回(降级路径)
- [ ] **Step 2: 跑测失败确认** Run: `npx jest test/assets/TaskPanel/v2/TaskPanelV2.test.tsx`  Expected: FAIL
- [ ] **Step 3: 实现壳层** TaskPanel.tsx 保留 Fetcher/props 契约/API 参数归一;三 Tab UI(任务信息/产物/进度)替换为 `<TaskPanelV2 task={task} nav={nav} .../>`;`buildTaskPanelAixUI` tab 加 `defaultWidth: 420`;extensions wrapper 接 binding(用 `props.params.taskId` 作 key);SdkCollapse 按 Setup 核实结论接线(Ruling 6)。L1/L2 内容本任务用最小占位(TaskOverviewV2/TaskNodePanelV2 的 stub div),Task 6/7 填实。
- [ ] **Step 4: 跑测 + 全量 TaskPanel 测试回归** Run: `npx jest test/assets/TaskPanel`  Expected: 新测试 PASS;`TaskProgress.test.tsx`/`NodeListView.test.tsx` 等既有用例中引用旧三 Tab 壳的用例在本任务内同步改造(允许本步一并修改)。
- [ ] **Step 5: Commit** `feat(task-panel-v2): breadcrumb two-level shell with collapse and nav injection at both mounts`

---

### Task 6: TaskOverviewV2(L1)—— 节点列表/DAG 双视图 + 边语义 + 产物区(P2)

**Files:**
- Create: `src/assets/TaskPanel/v2/TaskOverviewV2.tsx`、`v2/NodeListViewV2.tsx`、`v2/DagViewV2.tsx`、`v2/ArtifactsSection.tsx`、`v2/TaskInfoSection.tsx`
- Modify: `src/assets/TaskPanel/DagView.tsx` 仅按需导出内部布局常量/工具(不 fork 整文件;DagViewV2 基于现有 DagView 扩展 props:`drillable` 热区、`semantics` 边渲染)
- Modify: `src/assets/TaskPanel/index.ts` 若需新增导出
- Test: `test/assets/TaskPanel/v2/TaskOverviewV2.test.tsx`(jsdom docblock)

**Interfaces:**
- Consumes: Task 4 视图模型(`present/attemptCount/needsHitl/drillable`、DagEdgeView.semantics/reason、nodes[].artifacts)、Task 5 `TaskPanelNav`
- Produces(Task 7 复用): `DagViewV2`(props: `{ nodes: DagNodeView[]; edges: DagEdgeView[]; selectedId?; drillableSet?; onNodeClick?; onNodeDrill?; view: 'readonly-exec'|'interactive' }`——exec 只读视图共用)、`ArtifactsSection`(props: `{ artifacts: TaskArtifactView[]; apiBaseUrl: string }`,Task 7 按 nodeId 过滤复用)

行为(demo 语义逐条):
- 点击整卡/DAG节点 = `nav.selectNode(node.id, resolveNodeSessionTarget(...))`——选中+高亮+发跳转,停留 L1;下钻 icon(仅 `node.drillable`)= selectNode+drillInto 三合一,icon 在卡片/DAG 热区均 stopPropagation+键盘可达。
- 双视图切换(默认节点视图):Segmented 沿用 theme.tsx 现成件;卡片:`present` 状态 icon、完成(done/merged)`line-through`、executor pill(群/单 icon)、`needsHitl` 且 running 显「人工处理」warning 徽章、`attemptCount>0` 显「第 N 次返工」、选中态边框高亮。
- DAG 边:`rework|split_work` = warning 色 + `strokeDasharray='4 3'`;其余实线;路径下挂 `<title>{reason}</title>`(PRD「边带 reason」);drillable 节点右上热区。
- 产物区:任务级产物聚合(TaskView.artifacts + 全 nodes[].artifacts merge 去重,复用 Task 2 归一化),卡列 name/kind 徽章/owner/时间,`artifactViewHref` 产链接(text 型展开 summary,无链接不渲染按钮);计数徽章「N 项」。
- 任务信息区:默认折叠(HTML `details`),内容复用 TaskInfoTab 渲染块(Ruling 5)。

- [ ] **Step 1: 写失败测试**(jsdom):data 形 mock TaskView(含 rework 边/split_work 边/群节点/单节点/HITL/product artifact 四 kind)。断言:视图切换按钮、点卡调 selectNode 且不 drill、点下钻 icon 调两者、ring icon 仅 drillable 渲染、rework 边=dashed 虚线(path 检查 stroke-dasharray)、产物卡有 file|link 徽章及 href、HITL 徽章、「第 1 次返工」、信息区默认折叠。
- [ ] **Step 2: 跑测失败确认** Run: `npx jest test/assets/TaskPanel/v2/TaskOverviewV2.test.tsx`  Expected: FAIL
- [ ] **Step 3: 实现** 5 个 v2 组件;DagViewV2 复用 DagView 交互(拖拽缩放)与分层坐标,新增边语义分支与热区;styled-components 续 tokens.ts/theme.tsx 风格,禁新增 UI 库。
- [ ] **Step 4: 跑测** Run: 同 Step 1 + `npx jest test/assets/TaskPanel`  Expected: PASS + 回归绿
- [ ] **Step 5: Commit** `feat(task-panel-v2): overview level with dual-view nodes, semantics edges and artifact section`

---

### Task 7: TaskNodePanelV2(L2)三区 + 吸收群下钻(P3)

**Files:**
- Create: `src/assets/TaskPanel/v2/TaskNodePanelV2.tsx`
- Modify: `src/assets/TaskPanel/GroupDrillDown.tsx`(抽出 `GroupSessionView` 为独立导出文件 `v2/GroupSessionView.tsx`,原 955 行文件同步瘦身,既有导出与 test 不破)
- Retire(本任务内删除): `NodeDetailDrawer.tsx`(信息能力并入 L2)`、TaskPanel.tsx 内残留的三 Tab 装配死代码
- Test: 新建 `test/assets/TaskPanel/v2/TaskNodePanelV2.test.tsx`;改造 `test/assets/TaskPanel/GroupDrillDown.test.tsx`(import 路径不变则零改动)

**Interfaces:**
- Consumes: Task 6 `DagViewV2`(readonly 模式)、`ArtifactsSection`;Task 4 `execDag`;store drillStack 顶为 nodeId
- Produces: `TaskNodePanelV2`(props: `{ task: TaskView; node: TaskNodeView; nav: TaskPanelNav }`)三区:
  1. 节点信息:label、executor pill、present 状态 tag、返工徽章、HITL 徽章(此层不限 running)、goal(taskSpec.instruction/objective)、起止时间(未开始/进行中文案)、步骤追踪折叠区(node.stepTraces 存量能力保留,PRD #8 no-op 裁决的落地物)
  2. 执行流程:`node.execDag` → `DagViewV2` 只读模式(无点击/下钻/选中),空数据显「该节点暂无内部执行流程」
  3. 节点产物:按 nodeId 过滤(先 nodes[].artifacts/task.artifacts 中 nodeId 匹配,套 ArtifactsSection)
  4. 群节点追加区:`node.drillable && bcsBaseUrl` 时嵌 `<GroupSessionView>`(必须经 TaskPanel 既有 `bcsBaseUrl` prop 传来)承载原群消息明细
  5. 子任务保留:node.hasSubTask 仍走宿主 `onOpenSubTask`(既有链路不动,PRD 退役条款=旧 rail 入口退役,子任务场景不回退)

- [ ] **Step 1: 写失败测试**(jsdom):三区分别断言;drillStack 顶节点不存在(数据变更)→ 空态「节点数据缺失」+ 返回按钮(Review Focus #4);exec 只读(点节点无回调);产物过滤正确;群节点含 GroupSessionView(mock fetch)。
- [ ] **Step 2: 跑测失败确认** Run: `npx jest test/assets/TaskPanel/v2/TaskNodePanelV2.test.tsx`  Expected: FAIL
- [ ] **Step 3: 实现 + GroupDrillDown 瘦身拆分**(先拆文件跑既有测试确认绿,再写新组件)。
- [ ] **Step 4: 跑测 + 登记退役** Run: `npx jest test/assets/TaskPanel`  Expected: 全绿(含改造后 GroupDrillDown 用例);确认 NodeDetailDrawer 引用清零(grep)。
- [ ] **Step 5: Commit** `feat(task-panel-v2): level-2 node panel with exec dag, node artifacts and group drilldown absorption`

---

### Task 8: 多任务切换条(P1 后半)

**Files:**
- Create: `src/assets/TaskPanel/v2/TaskSwitcherBar.tsx`
- Create: `src/hooks/useTaskSwitcherTasks.ts`(listTasks 拉取 + 60s 刷新,失败静默空列表)
- Modify: `src/extensions/empty.ts`、`src/pages/MyTask/components/MyTaskDrawers.tsx`(接线)、`src/hooks/useTaskExecution.ts`(task 创建成功 → `setActiveTask(newTaskId)`,实现「发起任务自动切换」)
- Test: `test/assets/TaskPanel/v2/TaskSwitcherBar.test.tsx`、`test/hooks/useTaskSwitcherTasks.test.tsx`

**Interfaces:**
- Consumes: `listTasks({ user_id })`(`src/services/backendApi/tasks/taskController.ts` 现有)、Task 5 switcher 槽
- Produces:

```ts
export interface TaskSwitcherItem { taskId: string; title: string; status: 'running'|'pending'|'done'; statusLabel?: string }
export interface TaskSwitcherProps { items: TaskSwitcherItem[]; activeTaskId: string; onSelectTask: (taskId: string) => void }
// hook: useTaskSwitcherTasks(userId?: string, options?: { enabled?: boolean }): { items: TaskSwitcherItem[]; loading: boolean }
// 状态映射:EXECUTING/REVIEWING/FAILED 任务的活跃节点存在 → running;DRAFTING/DEFINED/PENDING → pending;DONE/CANCELLED → done(label 取 tokens TASK_STATUS_TONES 中文)
```
- 切换语义:chat = wrapper 重写 `params.taskId = store.activeTaskId`(openTab 幂等更新路径同款),面板 Fetcher 换任务重挂;drawer = `setSelectedTaskId`。tab 标题仍显旧动画任务名属已知次要瑕疵 → 记 ledger `minor (deferred)`。

- [ ] **Step 1: 写失败测试**:Bar 渲染(当前名+状态 tag+数量徽章+下拉展开)、点击条目回调 onSelectTask、hook mock listTasks 断言状态映射三桶 + 失败→空数组。
- [ ] **Step 2: 跑测失败确认** Run: `npx jest test/assets/TaskPanel/v2/TaskSwitcherBar.test.tsx test/hooks/useTaskSwitcherTasks.test.tsx`  Expected: FAIL
- [ ] **Step 3: 实现 + 三处接线**(chat wrapper / drawer / 发起自动切换)。
- [ ] **Step 4: 跑测 + useTaskExecution 回归** Run: 上述两测试 + `npx jest test/hooks/useTaskExecution.test.tsx`  Expected: PASS
- [ ] **Step 5: Commit** `feat(task-panel-v2): multi-task switcher bar wired to list API at both mounts`

---

### Task 9: 会话联动 —— pendingNavigation 消费 + 时间窗定位(P4)

**Files:**
- Modify: `src/capabilities/types.ts`(新 capability)、`src/capabilities/defaultCapabilities.ts`(Open Core 默认实现)
- Create: `src/pages/Workspace/Chat/hooks/taskSessionNavigation.ts`(观察者 effect hook + nudge 挂载到 Chat 根,模式参照 `ErrorNotifyObserver`)
- Create: `src/assets/TaskPanel/messageWindowLocate.ts`(纯函数:时间窗定位)
- Test: `test/pages/Workspace/Chat/taskSessionNavigation.test.tsx`(jsdom)、`test/assets/TaskPanel/messageWindowLocate.test.ts`

**Interfaces:**
- Consumes: Task 1 `pendingNavigation`/`consumePendingNavigation`、Task 4 `ResolvedSessionTarget`
- Produces:

```ts
// capabilities
export interface TaskSessionJumpTarget { sessionId: string; kind: 'group'|'bot'|'friend'|'unknown'; groupId?: string|null; botId?: string|null }
navigateToConversation(target: TaskSessionJumpTarget): CapabilityResult<null>;   // default 实现:group→ selectGroup+selectSession;bot/friend→ selectSession(workspace store 既有 action,以实际签名核对)
// messageWindowLocate.ts
export function locateMessageWindowTimestamp(messages: Array<{ ts: number }>, startTime: number|null, endTime: number|null): number | null;
// 返回应滚动到的消息 ts:落在 [start,end] 内的最后一条;窗口无命中返回 start 之前最近一条;全无 → null
```

- DOM 侧接線(Ruling 4):effect 消费 pendingNavigation → capability 跳会话 → 若 target.startTime 非空,`requestAnimationFrame` 内在会话消息容器 DOM 中查询可锚点消息元素(执行时核实消息 DOM 是否带时间戳属性;无论有无,未找到一律 console.info 后静默降级——**不因定位失败报错/循环**)。目标会话不存在 → log + consume 放弃(Review Focus #2)。

- [ ] **Step 1: 写失败测试**:纯函数(窗口命中/窗前命中/空窗/null)全测;jsdom 测 capability default 实现(mock store 调用断言)+ effect 消费(pendingNavigation 置后调 navigate、consume 被调;无能力/无目标会话路径不抛)。
- [ ] **Step 2: 跑测失败确认** Run: `npx jest test/assets/TaskPanel/messageWindowLocate.test.ts test/pages/Workspace/Chat/taskSessionNavigation.test.tsx`  Expected: FAIL
- [ ] **Step 3: 实现** capability seam + default 实现 + Workspace 挂载点 + DOM best-effort。
- [ ] **Step 4: 跑测 + capabilities 回归** Run: 上述 + `npx jest test/capabilities`(若存在该目录用例)`、npm run typecheck`  Expected: PASS
- [ ] **Step 5: Commit** `feat(task-panel-v2): session navigation with time-window message locate via capability seam`

---

### Task 10: 收尾 —— 退役清理 + 文档回写 + 全量门禁(P5)

**Files:**
- Delete: 已在 Task 7 删除的组件外,再 grep 清残留死引用(`TaskProgressTab`/`TaskArtifactsTab` 旧 Tab 若无消费则删,`TaskInfoTab` 内容已被 TaskInfoSection 复用则删原件);旧 fixture.ts 已是空壳保持不动
- Modify: `specs/2026-10-20-task-module-v2-upgrade/spec.md`(§8 附录映射表更新为最终文件名 + Status 改 implemented + 记录 Ruling 1-6)
- Test: 全量

- [ ] **Step 1: 死引用清理** grep `TaskProgressTab|NodeDetailDrawer|TaskArtifactsTab`(含 index.ts barrel)确认引用清零;`npx jest test/assets/TaskPanel test/stores test/hooks test/pages/MyTask` 回归绿;删除对应退役测试文件或改造其引用。
- [ ] **Step 2: spec 回写** §8 表逐项标注"→ 已实现于 `src/assets/TaskPanel/v2/…`",Status 行更新,Rulings 摘要补记(含产物契约对齐裁决)。
- [ ] **Step 3: 全量门禁** Run: `npm run check:open-core && npm run ci`  Expected: 两者全绿(check:open-core 0 违规;typecheck+lint+test+build 通过)
- [ ] **Step 4: Commit** `chore(task-panel-v2): retire legacy drill entries, update spec mapping and pass full ci gate`

---

## Self-Review 结论(已完成)

- **Spec 覆盖**:P1=Task 1/5/8;P2=Task 2/3/4/6;P3=Task 7;P4=Task 9;P5=Task 10。§3.3 派生规则全部落 Task 3(派生规则携带于 Interfaces 步骤);§6 非目标未越界(无 TaskFormModal/推送/取消操作改动)。偏差一处已记 Rulings#1(产物 controller → 契约+归一化对齐后端实际)。
- **类型一致**:`TaskPanelNav`/`ResolvedSessionTarget`/`TaskNavTarget` 三处(store hook/L2 语义/资产接口)structurally 对齐,执行中以 Task 5 `v2/taskPanelNav.ts` 为权威定义。
- **比例**:计划长于 spec 属预期(spec 为愿景,本计划沉淀 API 形状);无 TBD 项。
- **测试性质**:每 Task 均先失败后通过;既有 894 行 mapper 测试为回归红线。