# Task 模块 V2 升级总体方案 —— 协同任务副屏 · 下钻（方案A）

Status: **implemented**（2026-10-21 前端落地完成,实施计划与逐任务记录见本目录 plan.md;P1–P5 全部按 §5 验收通过,`check:open-core && npm run ci` 全绿）

PRD 基准：`/Users/jian.jiangj/Git/frontend/TeamClaw-demo` 中最新的 task 产品原型
`src/pages/Collaboration/components/CoopTaskV2/`（协同任务副屏·下钻方案，"方案A：跳转/下钻
分离 + 面包屑两级栈"），代码即 PRD（demo 仓库 `docs/` 本 checkout 未同步，mock 头注释为其
设计说明）。升级对象：frontend-nextgen 中调用 `/openapi/v1/collaboration/tasks` 的 task
功能（聊天内任务副屏 + 我的任务页）。

Owner: frontend-nextgen。后端契约变更需与 task engine 侧协同（见 §6），其中产物与任务图
两块正对应后端进行中的 `task-artifact-domain`（`apps/backend/specs/2026-10-20-task-artifact-domain/`）
与 `task_graph_repository` 改造。

## 1. PRD 产品形态拆解

demo `CoopTaskV2` 的整体布局是：左侧会话主区（单聊/协作群）+ 右侧任务副屏（默认 420px，
可拖拽 320–720px，可收起）。副屏是一个面包屑两级栈，下钻封顶 2 层：

```
┌───────────────会话主区───────────────┬──────────任务副屏──────────┐
│ 主单聊 / 协作群 / 跨BCN核验群          │ [多任务切换横条（可选）]     │
│ 消息流（每条消息带 nodeId 归属）       │ 任务总览 / {节点} ← 面包屑   │
│ 选中节点 → 滚动定位该节点最后一条消息   │ ─ Level1 任务总览 ─        │
│                                     │   步骤：节点视图 / DAG视图  │
│                                     │   产物：file/link 列表     │
│                                     │ ─ Level2 节点副屏（下钻）─  │
│                                     │   节点信息 / 执行流程DAG    │
│                                     │   / 节点产物               │
└─────────────────────────────────────┴───────────────────────────┘
```

关键产品语义（组件 → 行为）：

| # | PRD 能力 | demo 组件/机制 | 语义 |
|---|---------|---------------|------|
| 1 | 跳转/下钻分离 | `CoopTaskContext.selectNode` vs `drillInto` | 点节点整卡 = 选中 + 跳转对应 session + 消息流滚动定位；下钻按钮（仅群聊节点，Workflow icon）= 进入节点副屏 |
| 2 | 两级栈 + 面包屑 | `CoopTaskPanel`（drillStack 封顶 2 层） | Level1 任务总览 ↔ Level2 节点副屏，面包屑即标题行 |
| 3 | 步骤区双视图 | `TaskOverview` + `DagGraph` | 节点视图（卡片列表）/ DAG视图（SVG 纵向布局），节点含状态 icon、执行体 pill（单聊/群聊）、HITL「人工处理」徽章、完成态划线、返工次数 |
| 4 | DAG 边语义 | `coopV2DagEdges` | `split_work / race / fallback / rework / none`，`rework`、`split_work` 用 warning 虚线渲染，边带 `reason` |
| 5 | 节点副屏三区 | `TaskNodePanel` | ① 节点信息（goal/执行体/状态/返工/起止时间） ② 执行流程（节点内部 execDag，只读） ③ 节点产物（按 nodeId 过滤） |
| 6 | 多任务切换 | `TaskSwitcherBar` + `TaskSwitcherFloat` | 副屏顶部横条：当前任务名 + 状态 + 任务数，下拉列表切换；demo 用悬浮按钮做演示开关（演示脚手架，非产品形态） |
| 7 | 会话联动 | `pendingNavigation` + `nodeScrollSignal` + `msg.nodeId` | 节点 → `targetSessionId` 跳转；消息按 nodeId 归属，实现"点节点 → 定位到该节点最后一条消息" |
| 8 | 任务生命周期系统消息 | demo message mock | 「任务已创建」「Owner Bot 裁决 revise/approve（返工）」「跨 BCN 交接 + 上下文包注入」等系统消息出现在对应 session |
| 9 | PRD 状态词表 | node：`pending/running/rework/done/merged`；task：`running/rework/reviewing/done` | 节点态含 rework（返工执行中）与 merged（已合入） |

副屏壳交互：收起按钮嵌标题行、拖拽手柄调宽（320–720）、收起后会话头出现展开按钮。

## 2. 现状盘点（frontend-nextgen）

已有实现（`src/assets/TaskPanel/` 资产 + 双挂载点）：

- **挂载**：`registerPanelContent('taskPanel.TaskLoopView', TaskPanelAdapter)`
  （`src/assets/TaskPanel/index.ts:44`），聊天里以声明式 `<AixUI type="panel"
  component="taskPanel.TaskLoopView">` 面板消息落库、随聊天历史恢复；另一处挂载在
  `/work/my-task` 的抽屉（`MyTaskDrawers`）。
- **数据**：`TaskPanelFetcher`（5s 轮询，`contract.ts:105` 的 TaskDashboardResponse）——
  节点 `run_info` 已含 run_mode（single_bot/coop_group/bbs）、assignee、起止时间、output、
  per-node artifacts、child_task_id、`acceptance_result`、`action_log`（含 attempt）、
  extend_props；`relations` 仅 `DEPENDENCY` 一种类型；任务级 `main_session` +
  `derived_session_ids`。
- **视图**：`TaskPanel` 三 Tab（info/artifacts/progress）+ 左栏 `GroupDrillDown`
  （协作群下钻）与 `TaskSubTaskPanel`（子任务面板）；`TaskProgressTab` 已有
  节点视图/DAG视图切换，`DagView` 支持拖拽/缩放/连线动画；`NodeDetailDrawer` 节点详情。
- **API**：`taskController.ts` 仅 execute / dashboard / list；grant/revoke、bbs 各自独立。
  无取消/更新操作。
- **状态层**：`taskStore` 极薄（lastTaskId/lastTaskRecord + 两处未使用的 UI 偏好字段）；
  视图状态全部组件局部。
- **实时性**：仅轮询，无推送。

对照结论——**差距清单**：

| PRD 能力 | 现状 | 差距评级 |
|---------|------|---------|
| 两级栈+面包屑的副屏导航 | 三 Tab 平铺 + 抽屉/左栏，交互语义不同 | ★★★ 结构性重构 |
| 边语义（split_work/rework…）+reason | `TaskRelationDto.type` 仅 `'DEPENDENCY'`，需派生（契约不变决策） | ★★★ 纯前端派生 |
| 点节点跳 session + 消息定位 | 无（面板与会话无双向联动；无 node→session 绑定、消息无 nodeId） | ★★★ 纯前端派生（会话规则+时间窗定位，§3.2） |
| 节点内部执行 DAG（execDag） | 无对应数据源（`execution_graph` 是轮次任务图，`action_log` 是事件序列） | ★★ action_log 派生 |
| 节点级 HITL/返工徽章 | 任务级 `needs_attention`；action_log 有 attempt；节点级 rework 状态无 | ★★ 纯前端派生 |
| 产物区（file/link、owner、node 归属） | `TaskArtifactsTab` + DTO 已有 type/url/node_id | ★ UI 对齐 + 产物清单接口先行 mock |
| 多任务切换 | 无 | ★★ 纯前端，list API 驱动 |
| 副屏可拖宽/收起 | `useResizableRail` 已存在（群下钻用） | ★ 复用 |
| PRD 状态词表（rework/merged） | product/runtime 双词表，已收口于 `status.ts` | ★ mapper 扩展即可 |
| 5s 轮询实时性 | 已满足 demo（静态） | — 本期不升级推送 |

## 3. 总体升级方案

### 3.1 架构原则（本方案的取舍，my call）

1. **演进而非重写**：`TaskPanelFetcher`（轮询内核）、`taskPanelMapper`（DTO→视图模型
   收口）、`contract.ts`（契约层）全部保留复用；重构的是壳层与导航结构，不推翻资产。
2. **挂载形态保持双轨，SDK 原生即副屏（spike 已验证，2026-10-20）**：`@tc-chat/ui`
   的 `ChatLayout.Panel` 就是"会话旁右侧副屏"——`PanelArea` 与 `ChatArea` 并列，与
   demo 布局同构。SDK 原生能力：`PanelTab.defaultWidth`（number/px/%，自带安全宽度
   钳制，超界自动修正并 warn）、`panelCommand: 'expand'|'collapse'` 收起/展开、
   tab `closable` 与 TopBar 槽（`slotLeft` 面板级静态 / `slotRight` 随 tab 联动，可放
   面包屑与收起按钮）、per-tab `eventEmitter`（面板内部的节点高亮类联动可优先走此通
   道）、`PanelAction {send_message|fill_input}`（副屏反向注入会话输入区，P4 会话联
   动可用；跨出面板树的状态，如 `pendingNavigation` 由宿主消费，仍走 §3.2 的 store）。
   因此聊天内继续走 AixUI 面板消息（保证历史落库/刷新
   恢复/热更新不回退），挂载机制零改动；`/work/my-task` 抽屉挂载同一组件自然获得升级。
   **唯一降级**：SDK 不支持用户拖拽改宽（宽度由 defaultWidth 静态决定），demo 的
   320–720px 拖宽降级为 `defaultWidth: 420` 固定宽度；收起/展开经 `panelCommand` 保留。
   交互精度损失可接受，不为此fork面板内部双轨（`useResizableRail` 继续服务群执行
   下钻的内部 rail，不挪用为面板肋板）。
3. **契约差异在 mapper 收口**：后端词表/结构不到位时，前端先以派生字段实现 PRD 视觉
   （如 rework ← attempt>0 && running），后端契约落地后只改 mapper。
4. **Open Core 边界**：多任务切换、会话跳转联动的环境差异走 capabilities/extensions
   seam，不内联 flag；`src/assets/TaskPanel` 保持资产纯度（不反向 import app 代码），
   会话跳转等宿主能力通过 props/回调由 adapter 注入（模式同 `TaskPanelAdapterWithUser`）。
5. **单文件 ≤1000 行**：`taskPanelMapper.ts` 已 678 行，新增边语义/状态派生放独立
   `statusDerivation.ts` 一类模块，不继续膨胀。

### 3.2 改造设计（按新组件结构）

```
TaskPanelV2（壳：面包屑两级栈 + 收起/展开 + 多任务切换条；宽度固定 defaultWidth:420）
├─ TaskOverviewV2   Level1：步骤区（节点视图/DAG视图）+ 产物区
│   ├─ NodeListViewV2  （吸收 NodeListView + NodeDetailDrawer 信息区）
│   ├─ DagViewV2       （复用 DagView，加边语义/下钻热区/HITL/返工渲染）
│   └─ ArtifactsSection（吸收 TaskArtifactsTab → PRD 卡片形态）
└─ TaskNodePanelV2  Level2：节点信息 + 执行流程 DAG + 节点产物
    （吸收 GroupDrillDown / TaskSubTaskPanel / NodeDetailDrawer 的存量能力）
```

- **导航状态**（对应 demo `CoopTaskContext`）：新增 `src/stores/taskSidePanelStore.ts`
  ——`drillStack`（封顶 2 层）、`selectedNodeId`、`activeTaskId`、`pendingNavigation`
  （由宿主消费，执行会话跳转）、`panelCollapsed`（经 `panelCommand` 作用于 SDK 副屏，
  不自管宽度）。同时清掉 `taskStore` 里两处 dead 偏好字段。
- **多任务切换条**：数据来自 `listTasks`（现有 list API），横条展示当前任务名 + 状态 +
  数量徽章，下拉切换 `activeTaskId`（重新挂 TaskPanelFetcher）。demo 的全屏可拖悬浮入
  口是演示脚手架，**不产品化**；真实入口 = 发起任务时的自动切换 + 副屏横条手动切换。
- **跳转/下钻分离**与 demo 语义一致：点整卡 = `selectNode`（选中 + 高亮 + 触发
  `pendingNavigation`）；下钻按钮 = `drillInto`（仅 run_mode 为协作群的节点有热区）。
- **消息定位（契约不变决策下的派生方案）**：选中节点后由宿主切换到该节点关联会话并
  滚动至关联消息。会话解析规则（纯前端）：`run_mode=single_bot` 节点 → `main_session`；
  `run_mode=coop_group/bbs` 节点 → `derived_session_ids` 中按 `assignee`/`source_group_id`
  匹配，无法唯一定位时回退跳 `main_session`。消息定位：无消息级 nodeId 契约，用该节点
  `start_time..end_time` 时间窗 + 任务生命周期系统消息兜底定位，做"定位到该节点的
  时间窗"而非"精确最后一条"。

### 3.3 状态与边语义映射（mapper 层，契约不变 ⇒ 全部为前端派生，即正式方案）

契约保持不变（2026-10-20 评审决策），PRD 状态与边语义全部由 mapper 从现有 DTO 派生：

| PRD | 派生规则（输入 → Present） |
|-----|--------------------------|
| node `running` | runtime `RUNNING` 直映 |
| node `rework` | `action_log` 存在 attempt>1 且最近一次 acceptance verdict 非 DONE，且节点执行中 |
| node `merged` | DONE 且是其前驱节点输出已被下游消费（或 `child_task_id` 指向的任务 DONE） |
| node `pending` | `PENDING/PLANNING` |
| `attemptCount` 返工次数 | `action_log[].attempt` 聚合 |
| 节点级 `needsHitl` | 任务 `needs_attention` 为真时，取当前 `running` 且 `acceptance_result.verdict` 非空的节点 |
| 边 `split_work/race/fallback/none` | `relations[].type` 仅 "DEPENDENCY"，统一按 `none` 渲染（实线）；协作群节点若有并行子分支，由跨节点 `sequence` 与共前驱结构派生 `split_work` |
| 边 `rework` + reason | attempt>1 的节点由其"重复执行前后驱"画 warning 虚线；reason 取该 attempt 的 action_log 事件描述 |

派生逻辑收敛到独立模块（如 `statusDerivation.ts`），保证单文件 ≤1000 行约束下
`taskPanelMapper` 不膨胀；派生规则全部配 jest 单测。

## 4. 后端契约结论（2026-10-20 评审决策）

1. **后端契约基本保持不变**——边语义、节点级状态（rework/merged/HITL）、节点→会话
   绑定、消息关联、execDag 数据源 **均不新增/不修改契约**，PRD 表现层差异由前端
   mapper 派生实现（§3.3）。
2. **唯一新增契约：产物清单接口**（产物列表独立出 dashboard，节点/任务两级查询）。
   前端 **先 mock**：`src/services/backendApi/tasks/` 新增产物 controller 骨架 +
   本地 fixture（复用 `assets/TaskPanel/fixture.ts` 模式），P1–P3 期产物区走 mock
   数据；与后端 `task-artifact-domain`（`apps/backend/specs/2026-10-20-task-artifact-domain/`，
   DRAFT 待批）spec 的最终 API 形状对齐后切换真实接口——届时只改 controller 实现，
   组件与 store 不动。
3. execDag（节点内部执行流程）数据源定为 **`action_log` 派生**（此前 §4-5 候选 b），
   不依赖后端；若后续后端愿意下发结构化 execution_graph，mapper 预留升级位。
4. 会话联动走派生规则（§3.2），接受"定位到时间窗"的精确度；当后续 `task-artifact` /
   graph 改造顺带提供节点级 session id 时，mapper 同步升级为精确映射。
5. 取消/更新任务操作、推送通道：契约不变决策下维持不做，仅预留。

## 5. 分阶段实施计划

| 阶段 | 内容 | 前置 | 验收 |
|-----|------|-----|------|
| P1 副屏壳+导航 | store、TaskPanelV2 面包屑两级栈、SDK defaultWidth:420 + collapse、多任务切换条（list API） | 无（纯前端） | 两挂载点（聊天消息面板 + MyTask 抽屉）均获得新壳；`npm run ci` 绿 |
| P2 任务总览层 | TaskOverviewV2：双视图节点卡/徽章/边语义派生渲染 + 产物区（产物接口 mock） | P1 | PRD #2/3/4/9 视觉与交互对齐 demo；statusDerivation 单测覆盖 |
| P3 节点副屏层 | TaskNodePanelV2 三区；吸收 GroupDrillDown/TaskSubTaskPanel/NodeDetailDrawer；execDag 由 action_log 派生 | P1/P2 | PRD #5 对齐；旧下钻入口退役，回归群协作/子任务场景不回退 |
| P4 会话联动 | 点节点跳 session（§3.2 派生规则）+ 时间窗消息定位；生命周期系统消息展示 | P1–P3（无后端依赖） | 跳转正确 + 定位滚动可见（时间窗精度） |
| P5 收尾 | taskStore dead 字段清理、`check:open-core`、全量回归、映射文档回写 | P1–P4 | `npm run check:open-core && npm run ci` 全绿 |

契约不变决策下 **P1–P5 全部为纯前端工作**，无后端排期依赖；唯一契约触点是产物清单
接口（先 mock，后端 task-artifact spec 定稿后切换）。

## 6. 非目标（本期不做）

- 产物清单后端接口实现（前端先 mock，等 task-artifact spec 定稿后切换真实接口）。

- 任务发起/创建流（TaskFormModal、任务卡、任务消息生成）不变。
- 实时推送通道（SSE/WebSocket）——维持 5s 轮询，仅预留升级位。
- 任务取消/更新操作（当前无 API）。
- demo 的可拖全屏悬浮按钮（演示脚手架，非产品形态）。
- BBS 广场、定时/例行任务不受本次影响。

## 7. 决策记录与遗留问题

已拍板（2026-10-20 评审）：

1. **副屏挂载形态**：spike 验证 `@tc-chat/ui` `ChatLayout.Panel` 原生支持会话旁副屏
   （sibling pane、defaultWidth、expand/collapse、slotLeft/slotRight、per-tab 事件
   总线、PanelAction 反注输入区），挂载机制零改动。拖拽改宽 SDK 不支持，降级为
   `defaultWidth: 420` 固定宽度（§3.1-2）。
2. **后端契约基本不变**：不在后端扩展边语义/节点状态/会话绑定/消息关联，全部前端
   mapper 派生；唯一新增的产物清单接口前端先 mock（§4）。
3. execDag 数据源定为 `action_log` 派生。

遗留（不阻塞开工）：

- multi-task 切换条默认常驻（my call，可改）：发起任务自动切换 + 横条手动切换。
- 派生规则的成立依赖 `action_log`/`acceptance_result` 字段在真实数据中的完备性——P2
  开工前用 fixture 与一次真实 dashboard 抓样校准派生阈值。
- 副屏宽度固定后，窄屏（<1024px）下的布局表现需在 P1 验收时过一轮。
- 后端 `task-artifact-domain` spec 定稿后，产物 controller 从 mock 切真实接口（改动
  限定在 service 层）。

## 8. 附录：PRD 组件 ↔ 现有代码映射（2026-10-21 实施后回写）

| demo（CoopTaskV2） | 落地位置 |
|--------------------|----------|
| `CoopTaskContext.tsx` | `src/stores/taskSidePanelStore.ts`（按 task 分片 navByTask）+ `src/hooks/taskPanelNavBinding.ts`（业务层缝合） |
| `CoopTaskPanel.tsx` | `src/assets/TaskPanel/TaskPanel.tsx`（改造为 Fetcher + V2 壳装配）+ `v2/TaskPanelV2.tsx`（面包屑两级栈/收起/切换条槽） |
| `TaskOverview.tsx` | `v2/TaskOverviewV2.tsx`（双视图步骤区 + 产物区 + 任务产出 + 折叠任务信息） |
| `DagGraph.tsx` | `v2/DagViewV2.tsx`（interactive 复用升级版 `DagView.tsx`:边语义/reason/下钻热区;readonly-exec 渲染 execDag） |
| `TaskNodePanel.tsx` | `v2/TaskNodePanelV2.tsx`（三区 + 内嵌 `GroupSessionView` 吸收 grp 群下钻;子任务走既有 onOpenSubTask） |
| `TaskSwitcherBar` / `TaskSwitcherFloat` | `v2/TaskSwitcherBar.tsx`(产品化);Float 演示脚手架不落地(spec §6) |
| `coopTaskDemoV2.ts`（数据模型） | `contract.ts`（+产物 Descriptor 契约）+ `taskPanelMapper.ts`（接线）+ `statusDerivation.ts` / `artifactDescriptor.ts` / `sessionResolution.ts`（派生收口） |
| `CoopV2SessionMain.tsx`（会话侧） | capability `navigateToConversation`（站内会话路由）+ `src/pages/Workspace/Chat/hooks/taskSessionNavigation.ts`（rootContainer 全局观察者）+ `messageWindowLocate.ts`（时间窗定位） |

退役（P3/P5 清理）:`TaskProgressTab` / `NodeDetailDrawer` / `TaskSubTaskPanel` / `TaskArtifactsTab` / `TaskPanelHeader` / `NodeListView` / `useResizableRail` / `GroupDrillDownPanel`。

实施裁决摘录（全量见 plan.md Rulings 与执行 ledger）:
1. 产物接口不建独立 controller mock——对齐后端 `task-artifact-domain` 已实现的读侧契约（dashboard 节点级富化 Descriptor，kind-tagged content），mapper 双兼容归一，后端落地零切换动作。
2. 导航态按 task 分片（聊天多 tab 人任务并存共享单份下钻栈会串场）。
3. "生命周期系统消息展示"为 no-op（真实系统消息走既有消息渲染；面板侧由 L2 步骤追踪承载）。
4. 时间窗定位允许降级（消息 DOM 无时间戳锚点时仅完成跳转，不报错）。