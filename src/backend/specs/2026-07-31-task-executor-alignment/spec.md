# TaskExecutionGraph 全生命周期 State/Node/Edge 重构 — spec.md

> 隶属:`2026-07-28-goal-driven-task-execution/` 的执行架构重构子项。
> 落点域:ocb backend 任务内核(开源,代码在 Avernet `src/backend/src/agentclaw/community/core/task/`,sync 到 ocb `ocb-public` submodule)。
> 日期:2026-08-01。
> 性质:**spec(WHAT/WHY + 规范行为)**。本文取代本目录 `design.md` v1(2026-07-31)的执行者口径——`design.md` 标 superseded,以本文为权威。后续 `plan.md`/`tasks.md`。
> 状态机沿用 `2026-07-30-task-status-state-machine-alignment/`(7 态 TaskStatus / 6 态 NodeStatus),本文不重定义态名。
> **本版 v2(2026-08-01)**:基于 2026-07-31 重构会话收敛校准——补 `hang→人确认→升 BBS / 不升 FAILED` 三终止分支、中间层聚合验收、此阶段范围(SINGLE_BOT+COOP_GROUP)、伪代码边源点订正、搜推先行强化。

---

## 1. 概述(一句话)

把 `TaskExecutionGraph` 从"仅执行期的控制流图"重构为**贯穿任务全生命周期**的统一 `State / Node / Edge` 图谱:从任务录入(task-recognition)、补全规格、启动执行、搜推匹配、任务分解、派发、执行验收、验收失败重路由、递归拆解,直到递归上限后 `hang` 等人确认(升 BBS 同图延续 / 不升 FAILED)——**全程 `add_node`/`add_edge`/`更新State`,一张图、一个状态黑板**。

**搜推先行,拆解是 fallback**:每个(子)任务节点先 `bot-search`;匹配(full cover)→派发执行;**未匹配 或 验收不通过** 才 `task-decomposition` 拆解;拆出的子任务回到 `bot-search`,递归;递归上限 → `hang`(等人确认),不直接升 BBS。

## 2. 背景与动机(为什么要重构)

`design.md` v1 与现行代码暴露的结构性问题,本次重构一并消解:

1. **plan-graph 与 execution-graph 分裂**:`Plan`(sub_tasks/edges)在 `finalize_plan` 后物化成 `TaskExecutionGraph`,两套结构、两个生命周期,运行期拆解要在两者间同步,易断层。
2. **State 不是一等图要素**:执行上下文散落在 `Node.properties`/`instruction`/事件 payload 里,"retrieve-state(subtask)" 无统一落点;gap 驱动靠事件反推,非状态直读。
3. **搜推/拆解执行者口径反复**:`design.md` v1 定"搜推=系统 Port 非 bot、运行期拆解=系统 inline",但这两步是任务规划语义、需带状态上下文决策,宜由 bot 经 SKILL 驱动,系统只做派发。
4. **递归拆解隐式**:运行期拆解以"sibling + 父 SKIPPED + 聚合验收"隐式表达,递归深度、并行度、上限、**中间层聚合**无显式图结构,难追溯。
5. **BBS 上升断层 + 终止分支不全**:BBS 与主图割裂需 `progress_snapshot`;且 `design.md` v1 把"loop 上限"直接接 BBS/FAILED,缺"`hang` 等人确认是否上升"这个人工介入中间态。

## 3. 核心范式转变(WHAT 变了)

| 维度 | 现状(design.md v1 / 现行代码) | 重构后(本文) |
|---|---|---|
| **图谱覆盖范围** | 仅执行期(`finalize_plan` 后 `spawn_build_dag`) | **全生命周期**(从 task-create 起) |
| **三要素** | Node/Edge(控制流);State 散落 | **State / Node / Edge 三要素**,State 一等 |
| **State** | 无显式字段(散落 properties/instruction) | 图级一等要素;每节点可 `更新 State`/`retrieve-state` |
| **搜推 bot-search** | 系统 `BotDiscoverPort` inline(非 bot) | **owner-bot / exec-bot 经 task-plan-skill 调搜推能力**,作为图节点;**搜推先行** |
| **任务分解 decomposition** | 初始=user-bot SKILL;运行期=系统 inline | **统一由 bot 经 task-plan-skill 驱动**(含递归),系统不拆解 |
| **派发 dispatch** | 系统 `ExecutionPort` | **系统 task-scheduler**(不变,唯一系统侧节点) |
| **子任务验收 exec-accept** | owner-bot 独立验收 SKILL | **执行方 bot 经 task-exec-skill 自判**完成度+验收+上报(单bot=self;群=group owner-bot);仅判**子任务 DONE** |
| **中间层聚合验收 exec-aggregate** | 隐式(sibling+父SKIPPED) | **显式节点**:父 subtask 的子分解产出全 exec-accept 通过 → 父 owner 聚合复核父 `targets_acceptance` vs 子产出 → 父 DONE/REJECTED;自底向上逐级 |
| **任务终验 goal-verify** | 无显式节点 | **task-owner(需求bot/群owner-bot)经 SKILL 依 TaskSpec 验收标准 + State 判任务 DONE**;上升 BBS 与否都归 task-owner |
| **gap 驱动重路由** | 系统 Scheduler `compute_gap`+watchdog | **执行方 bot 验收 fail → bot-search(带 State 上下文)→ 命中则重派 / 未命中则递归拆解** |
| **递归拆解** | sibling + 父 SKIPPED(隐式) | **显式 decomposition 节点**,子任务并行无依赖,带递归上限 |
| **三终止** | loop 上限直接接 BBS/FAILED | **验收通过(聚合)→ DONE / 递归上限 → hang(AWAITING_HUMAN_*)→ 人确认 / 升 BBS 同图 / 不升 → FAILED** |
| **BBS 上升** | 割裂,需 progress_snapshot | **同一张图延续** `add_node/add_edge/更新State`;State 即上下文(删 progress_snapshot) |

> **关键反转(相对 design.md v1)**:搜推与分解回到"bot 经 SKILL 麻驱动";验收拆两级——子任务由执行方自判、任务由 task-owner 终验;**中间层父 subtask 靠子产出聚合验收(exec-aggregate),自底向上逐级**;系统职责收窄为**派发 + 状态写口 + 终态推进**。详见 §7 执行者映射。

> **两维度正交(三要素 = State / Node / Edge,皆一等)**:**Node 维度 = 动作/事件粒度**——每个 Node 是一次动作(谁、用什么 SKILL、产出什么 verdict),**所有动作都是节点**,含三个判定动作 `exec-accept`/`exec-aggregate`/`goal-verify`(bot 跑 SKILL 判验,有动作人/时刻/verdict);**State 维度 = Task / SubTask 实体粒度**——`TaskState`(任务级)+ 每 subtask 一个 `SubtaskState`(累积上下文 + **实体状态 status** + 结果/gap)。判定动作 = Node 维度记一笔(可审计/副屏可画),其**效果** = State 维度翻实体 status(dispatch→RUNNING / accept→DONE / aggregate→父 DONE / goal-verify→Task DONE);**非二选一,是两维度各落一笔**。字段落 `plan.md`。

## 4. 范围

### 4.1 In scope(此阶段)
- `TaskExecutionGraph` 数据模型重构为 State/Node/Edge 三要素(字段落 `plan.md`)。
- 全生命周期节点类型规约(recognition / clarify / execute / bot-search / decomposition / dispatch / exec-accept / exec-aggregate / re-route / recurse / hang / escalate / bbs-*)。
- State 作为一等图要素的读写契约(`更新 State` / `retrieve-state`)。
- 递归拆解的显式图结构与递归上限。
- 两级 DONE + **中间层聚合验收(exec-aggregate,自底向上逐级)**。
- 三终止:验收通过聚合 → DONE / 递归上限 → hang → 人确认 / 升 BBS 同图 / 不升 → FAILED。
- **此阶段执行模态:SINGLE_BOT + COOP_GROUP(CHAT / MANAGER_WORKER)**。
- BBS 上升衔接契约(同图延续 + 人工确认入口 `hang`→确认)——此阶段定义;**BBS 广场执行机制后续阶段**。

### 4.2 Out of scope(本期不改 / 后续)
- **BBS 广场执行机制**(自主认领、广场 bot exec/plan)——后续阶段;此阶段仅定义上升衔接契约与 `hang` 入口。
- 三执行模态(SINGLE_BOT/COOP_GROUP/BBS)与三协作模式(CHAT/MANAGER_WORKER/STATE_MACHINE)语义不变(STATE_MACHINE 协作群内的 workflow 自闭环仍走外部 `SubDagRef`,不在本图)。
- 事件类型与回投通道(统一 `POST /events` → `on_event`)。
- 副屏画布 / 可视化(归母 spec FR-OBS)。
- BCS 协议、引擎适配。

## 5. 需求列表(FR-GRAPH-*)

> 母 spec 的 FR-TASK/FR-DISPATCH/FR-ACCEPT/FR-LOOP/FR-EVENT/FR-COLLAB/FR-OBS 不变;本文为其执行架构重构层。

- **FR-GRAPH-01(全生命周期图)** `TaskExecutionGraph` 自任务创建即存在,节点覆盖 task-recognition→clarify→execute→bot-search→decomposition→dispatch→exec-accept→exec-aggregate→re-route→recurse→hang→escalate→bbs 全链路;不再有"plan-graph 与 execution-graph 两段式"。
- **FR-GRAPH-02(三要素)** 图由 `State`/`Node`/`Edge` 三要素构成;`State` 为图级一等要素,非节点 properties。
- **FR-GRAPH-03(State 读写契约)** 每个 Node 产出可 `更新 State`;任意节点可 `retrieve-state(scope)` 读取所需执行上下文;State 是 gap 驱动的单一读取源。
- **FR-GRAPH-03a(State 作共享工作台/SSOT)** State 是整图唯一事实来源:承载运行期间所有传递数据、中间结果、历史记录;任务/子任务状态在 State 中累积。
- **FR-GRAPH-03b(State 累积与归约)** State 定义明确的累积/归约语义(append / overwrite / merge),各字段按语义更新,避免无序覆盖。
- **FR-GRAPH-03c(持久化与回溯)** TaskExecutionGraph 支持持久化与回溯(time-travel/checkpointing):可在节点边界保存快照,支持**断点重跑与回滚**;快照粒度落 plan.md。
- **FR-GRAPH-04(搜推节点)** `bot-search` 为图节点,执行者 = owner-bot(初始规划期)或 exec-bot(验收 fail 重路由期),经 `task-plan-skill` 调搜推能力;系统不 inline 搜推。
- **FR-GRAPH-14(搜推先行)** 每个(子)任务节点**先 `bot-search`**;full-cover 匹配(C1 单 bot / C3 群)→ dispatch 派发;**未匹配 或 验收不通过 才 `task-decomposition`**;不直接 C5/BBS(无候选触发拆,不触发上升)。
- **FR-GRAPH-05(分解节点)** `task-decomposition` 为图节点,执行者 = bot 经 `task-plan-skill`;含递归分解;系统不分解。
- **FR-GRAPH-06(派发节点)** `task-dispatch` 为图节点,执行者 = **系统 task-scheduler**(唯一系统侧节点)。
- **FR-GRAPH-07(子任务验收节点)** `exec-accept` 为图节点,执行者 = 执行方 bot 经 `task-exec-skill` 自判完成度+验收+上报;**仅判子任务 DONE**;单 bot=self,协作群=group owner-bot。
- **FR-GRAPH-07a(任务终验节点)** `goal-verify` 为图节点,执行者 = **task-owner(需求bot/群owner-bot)** 经 `goal-verify-skill` **读 State**(`retrieve-state`:根 subtask 产出验收 + Task `goal.acceptances`)聚合判断整个 Task 是否 DONE;**上升 BBS 与否,任务终验都归 task-owner**。
- **FR-GRAPH-07b(完成判断逻辑一致)** `exec-aggregate`(父级完成判断)与 `goal-verify`(任务完成判断)**判断维度逻辑一致**:均为"读 State(下属产出验收 + 自身验收标准)→ 聚合判断 DONE";区别仅作用域与验收标准来源——父级 = 下属子 subtask 产出 + 父 `targets_acceptance`;任务级 = 根 subtask 产出 + Task `goal.acceptances`。
- **FR-GRAPH-08a(中间层聚合验收节点)** `exec-aggregate` 为图节点:**递归拆解产出的子 subtask 全 `exec-accept` 通过后,触发父 subtask 的聚合验收**——由父 subtask 的 owner bot 经 `task-exec-skill` **读取 State**(`retrieve-state`:下属子任务产出验收 + 父 subtask 自身 `targets_acceptance`)聚合判断 → 父 subtask DONE / REJECTED(REJECTED→该父继续 gap 重路由/拆解)。**自底向上逐级**,直至根 subtask 完成后才进 `goal-verify`。父 subtask 不靠节点状态二值,靠子产出聚合。
- **FR-GRAPH-08(gap 重路由)** 验收 fail → 由失败方 exec-bot 发起 `bot-search(带 retrieve-state 上下文)`:命中 → `dispatch` 重派;未命中 → `decomposition` 递归拆解。
- **FR-GRAPH-09(递归上限与三终止)** 递归分解带显式深度上限;**触上限仍未验收通过 → `hang`(graph `AWAITING_HUMAN_*`,挂起等人确认),不直接升 BBS**。人确认后二选一:升 BBS(走 FR-GRAPH-10 同图延续)/ 不升 → task `FAILED`(终态)。
- **FR-GRAPH-10(BBS 同图延续)** BBS 上升后不另起图,继续在原 `TaskExecutionGraph` `add_node/add_edge/更新State`;BBS 节点的**执行与分解 = 广场 BBS bot**;**任务终验 = task-owner**(经 goal-verify)。**BBS 上升后 task-owner 仅做终验判断,不再承担任务分解与执行**(分解改由 BBS bot 驱动)。
- **FR-GRAPH-11(状态机对齐)** 图节点状态迁移仍经 `TaskService` 状态组(7 态 TaskStatus / 6 态 NodeStatus)为唯一写口;图结构变更(add_node/add_edge)与状态迁移分离但同口落盘。
- **FR-GRAPH-12(并行无依赖)** 同层分解产出的子任务节点间无 DEPENDENCY 边,可并行;跨层为 DEPENDENCY。
- **FR-GRAPH-13(进度上报,可选/后续)** 执行方 bot 可回投进度事件(非终态),更新 State 的进度分区;此阶段可选,不阻塞主流程。

## 6. 规范流转(伪代码,标注节点类型;边源点已订正)

> 以下为 §5 需求的规范行为,`add_node`/`add_edge`/`更新State` 为图操作原语。`需求bot/bcs_group:owner-bot` 表示起手 owner;后续重路由由失败 exec-bot 承担。

```
# ── 录入期(recognition)──────────────────────────────────────────
n1  add_node(owner-bot : task-recognition-skill  task-create)            → 创建 task-meta-info        ;更新State
n2  add_node(owner-bot : task-recognition-skill  task-clarify)           → 补全 task-spec 必备要素     ;更新State
e1  add_edge(n1 -> n2)
n3  add_node(owner-bot : task-recognition-skill  task-execute(task-spec))→ 调 task-scheduler.start    ;更新State
e2  add_edge(n2 -> n3)

# ── 规划期(plan)──────────────────────────────────────────────────
n4  add_node(owner-bot : task-plan-skill  bot-search(task-spec))         → 未匹配                     ;更新State   # 搜推先行;未匹配 → 进入分解
e3  add_edge(n3 -> n4)
n5  add_node(owner-bot : task-plan-skill  task-decomposition)            → task-plan{subtask1,2,3}    ;更新State   # 3 子任务并行无依赖,不递归占深度
e4  add_edge(n4 -> n5)
n6  add_node(owner-bot : task-plan-skill  bot-search(subtask1))          → bot1                       ;更新State
n7  add_node(owner-bot : task-plan-skill  bot-search(subtask2))          → bcs_group1                 ;更新State
n8  add_node(owner-bot : task-plan-skill  bot-search(subtask3))          → 未匹配                     ;更新State
e5  add_edge(n5 -> n6); e6 add_edge(n5 -> n7); e7 add_edge(n5 -> n8)

# ── 执行期(dispatch + accept)──────────────────────────────────────
n9  add_node(系统 : task-scheduler  task-dispatch(subtask1))             → bot1 执行                   ;更新State
e8  add_edge(n6 -> n9)
n10 add_node(bot1 : task-exec-skill  exec-accept)                        → subtask1 + 执行上下文       ;更新State   # 自判完成度+验收+上报
e9  add_edge(n9 -> n10)
n11 add_node(系统 : task-scheduler  task-dispatch(subtask2))             → bcs_group1 执行             ;更新State
e10 add_edge(n7 -> n11)   # 订正:源点 n7(subtask2 的 bot-search),非 n6
n12 add_node(bcs_group1 : owner-bot : task-exec-skill  exec-accept)      → subtask2 + 执行上下文       ;更新State
e11 add_edge(n11 -> n12)

# ── 递归分解(subtask3 未匹配直接拆)──────────────────────────────
n13 add_node(owner-bot : task-plan-skill  task-decomposition(subtask3))  → {subtask3-1,3-2,3-3}        ;更新State   # 递归[至上限],并行
e12 add_edge(n8 -> n13)   # 订正:源点 n8(subtask3 未匹配),非 n6

# ── gap 重路由(subtask1 验收 fail)────────────────────────────────
n14 add_node(IF n10:subtask1 未通过 → bot1 : task-plan-skill  bot-search(subtask1 + retrieve-state))→ botxxx ;更新State
e13 add_edge(n10 -> n14)
n15 add_node(系统 : task-scheduler  task-dispatch(subtask1 + 上下文))    → botxxx 执行                 ;更新State
e14 add_edge(n14 -> n15)
n16 add_node(botxxx : task-exec-skill  exec-accept)                      → subtask1 + 上下文           ;更新State
e15 add_edge(n15 -> n16)
n17 add_node(IF n16:subtask1 未通过 → botxxx : task-plan-skill  bot-search(subtask1 + retrieve-state))→ 未匹配 ;更新State
e16 add_edge(n16 -> n17)
n18 add_node(botxxx : task-plan-skill  task-decomposition)               → {subtask1-1,1-2,1-3}        ;更新State   # 递归[至上限]
e17 add_edge(n17 -> n18)

# ── 中间层聚合验收(子分解产出全 exec-accept 通过后)──────────────
#   例:subtask1-1/1-2/1-3 各自 exec-accept 通过 → 触发 subtask1 的聚合验收
n_agg1 add_node(subtask1-owner : task-exec-skill  exec-aggregate)
       → 读 State:retrieve-state(子任务 1-1/1-2/1-3 产出验收 + subtask1 自身验收标准)
       → 聚合判断 subtask1 DONE / REJECTED(REJECTED → subtask1 继续 gap)    ;更新State
#   自底向上逐级:每一层父 subtask 都在其子全通过后经 exec-aggregate 判 DONE

# ── gap 重路由(subtask2 验收 fail)────────────────────────────────
n19 add_node(IF n12:subtask2 未通过 → bcs_group1 : owner-bot : task-plan-skill  bot-search(subtask2 + retrieve-state))→ 未匹配 ;更新State
e18 add_edge(n12 -> n19)
n20 add_node(bcs_group1 : owner-bot : task-plan-skill  task-decomposition)→ {subtask2-1,2-2,2-3}       ;更新State   # 递归[至上限]
e19 add_edge(n19 -> n20)

# ── 递归至上限 → hang → 人确认 → BBS(同图) / FAILED ──────────────
#   触上限仍未验收通过 → n_hang
n_hang add_node(系统 : task-scheduler  mark-hang)  → graph AWAITING_HUMAN_* ;更新State   # 挂起等人确认,不直接升 BBS
#   人介入确认(POST /escalate-bbs 或 /cancel):
#     ├ 升 BBS:继续 add_node/add_edge/更新State 于同一 TaskExecutionGraph;BBS 节点执行者 = 广场 BBS bot(自主认领);终验仍归 task-owner
#     └ 不升:task → FAILED(终态)

# ── 任务终验(所有根 subtask 经聚合验收通过后;BBS 路径同样适用)──
n_goal add_node(task-owner : goal-verify-skill  goal-verify)
       → 读 State:retrieve-state(根 subtask 产出验收 + Task goal.acceptances)
       → 聚合判断 Task DONE(全 AC 满足) / 继续 gap(未满足)               ;更新State
       # 判断逻辑同 exec-aggregate(下属产出验收 + 自身验收标准 聚合),作用域升到任务级;
       # task-owner = 需求bot or 需求协作群owner-bot;BBS 上升后终验仍归 task-owner(见 O-2/O-7)
```

> 边源点笔误(e10/e12/n13 触发点)已于本版订正(O-3 resolved)。

## 7. 执行者映射(从伪代码提炼)

| 节点类型 | 执行者 | 机制(SKILL / 系统) | 能力来源 |
|---|---|---|---|
| task-create / task-clarify / task-execute | owner-bot(需求 bot / 群 master) | task-recognition-skill | SKILL |
| bot-search(初始 + 重路由) | owner-bot(初始) / 失败 exec-bot(重路由) | task-plan-skill | SKILL(调搜推能力) |
| task-decomposition(含递归) | owner-bot / 失败 exec-bot | task-plan-skill | SKILL |
| **exec-aggregate(中间层聚合验收)** | **父 subtask 的 owner bot** | task-exec-skill(读 State:下属子任务产出验收 + 父自身验收标准,聚合复核) | SKILL |
| task-dispatch | **系统 task-scheduler** | 系统 inline | ExecutionPort |
| exec-accept(单 bot) | 执行 bot 自身 | task-exec-skill | SKILL(自判子任务) |
| exec-accept(协作群) | 群 owner-bot | task-exec-skill | SKILL(判子任务) |
| **goal-verify(任务终验)** | **task-owner**(需求bot/群owner-bot) | goal-verify-skill(读 State:根 subtask 产出验收 + Task `goal.acceptances`,聚合判断;**逻辑同 exec-aggregate**) | SKILL;**BBS 上升后 task-owner 仅做此,不做分解/执行** |
| mark-hang(挂起) | 系统 task-scheduler | 系统 inline | graph_status→AWAITING_HUMAN_* |
| BBS 节点(执行+分解) | 广场 BBS bot | BBS 认领 + exec/plan-skill | SKILL(BBS 上升后承担分解+执行) |
| **改态(唯一写口)** | 系统 TaskService | 状态组 | guard→fold→append→save |

> 相对 design.md v1 的反转:搜推/分解/验收全部回到 bot SKILL 侧;系统仅保留 dispatch + mark-hang + 改态 + 终态推进。

## 8. 开放问题(需评审拍板)

| # | 问题 | 取向建议 |
|---|---|---|
| O-1 | **State 的结构 schema**:已定功能——State 是共享工作台/SSOT,承载传递数据/中间结果/历史记录,支持累积归约与持久化回溯/断点重跑(FR-GRAPH-03a/b/c)。**具体字段未定**,待 plan.md 落 schema。 | spec 已锁功能,字段不阻塞;落 plan.md。 |
| O-2 | ~~全局终验缺位~~ **resolved(2026-08-01)**:两级 DONE——执行 bot 经 task-exec-skill 仅判**子任务 DONE**;**任务 DONE 由 task-owner 经 goal-verify SKILL 依 TaskSpec 验收标准 + State 判**,上升 BBS 与否都归 task-owner。 | ✅ resolved;节点细节落 plan.md。 |
| O-3 | ~~伪代码边源点笔误~~ **resolved(本版订正)**:e10(n7→n11)、e12(n8→n13)、n13 触发点为 n8(subtask3 未匹配)。 | ✅ resolved,已落 §6。 |
| O-4 | **递归深度计数落点**:递归上限按"原 subtask 链路深度"计还是"全图最大深度"计?计数存 State 还是 Node.properties? | 倾向存 State 的 subtask 分区(`depth`),按链路计;上限值见 plan.md。 |
| O-6 | **task-recognition-skill 与 task-plan-skill 边界**:n3 task-execute 调 scheduler.start 后,n4 bot-search 又由 owner-bot 做——scheduler.start 与 n4 的驱动关系(谁触发 n4)? | 倾向 scheduler.start 仅"启动编排",n4 仍由 owner-bot SKILL 驱动回投;plan.md 明确驱动时序。 |
| O-7 | ~~与 design.md v1 处置~~ **resolved(2026-08-01)**:① `DecomposerPort` 退单签名 `decompose(spec, state)->list[SubTaskSpec]`(分解统一 bot SKILL 调,系统签名删);② `OwnerResolver` 缩为 `resolve_group_owner`+`resolve_task_owner` 两方法(self 内联不走 Port);③ `Task.owner_bot_id` 保留并强化——**BBS 上升后 task-owner 仅做终验,不再做分解/执行**;④ `progress_snapshot` 删除(BBS 同图延续,State 即上下文)。 | ✅ resolved;签名字面落 plan.md。 |
| O-8 | **exec-aggregate 触发时机**:父 subtask 的子分解全 exec-accept 通过后,由系统 tick 检测触发、还是子 exec-accept 回投链式触发? | 倾向系统 tick 检测"父 subtask 子全 DONE"→ 触发父 owner exec-aggregate(对齐"系统检测+落图,owner 判验")。plan.md 定。 |
| O-9 | **BBS 广场执行机制**为后续阶段:此阶段 `hang`→人确认→升 BBS 的衔接契约 + 同图 add_node 落点先定;BBS bot 自主认领/广场执行实现留后续。 | 此阶段定契约,执行后续。 |

## 9. 与既有文档关系

| 文档 | 关系 |
|---|---|
| `2026-07-28-goal-driven-task-execution/spec.md` | 母 WHAT/WHY,不变 |
| `2026-07-28-goal-driven-task-execution/plan.md` §5/§6 | 执行架构层被本文重构;若采纳,母 plan §5/§6 标 superseded-by-本文 |
| `2026-07-30-task-status-state-machine-alignment/` | 7 态状态机权威,本文沿用 |
| 本目录 `design.md` v1(2026-07-31) | **superseded by 本文**;保留作历史 |
| 本目录 `plan.md` | HOW:State/Node/Edge 字段、节点类型枚举、递归上限值、exec-aggregate 触发机制、BBS 衔接细节 |
| 本目录 `tasks.md` | 实现拆分 |

## 10. 验收标准(AC,spec 级)

| ID | 验收标准 |
|---|---|
| AC-S-01 | 存在统一的 `TaskExecutionGraph`,自任务创建即存在,覆盖 recognition→…→hang→BBS/FAILED 全链路(FR-GRAPH-01) |
| AC-S-02 | `State` 为图级一等要素,有 `更新 State`/`retrieve-state` 契约(FR-GRAPH-02/03) |
| AC-S-03 | 搜推/分解/验收均为 bot SKILL 节点;系统仅 dispatch+mark-hang+改态(FR-GRAPH-04/05/06/07) |
| AC-S-04 | 验收 fail 的重路由与递归拆解有显式图结构,带递归上限(FR-GRAPH-08/09) |
| AC-S-05 | BBS 上升后继续在同一图 add_node/add_edge/更新State(FR-GRAPH-10) |
| AC-S-06 | 图节点状态迁移仍经 TaskService 状态组(7 态/6 态),无侧门(FR-GRAPH-11) |
| AC-S-07 | §8 开放问题 O-1/O-4/O-6/O-8/O-9 在 plan.md 前有评审结论 |
| AC-S-08 | 两级 DONE:子任务由执行 bot task-exec-skill 判 DONE;任务由 task-owner 依 TaskSpec+State 判 DONE;BBS 与否终验归 task-owner(FR-GRAPH-07/07a) |
| AC-S-09 | State 具 SSOT/累积归约/持久化回溯三能力(FR-GRAPH-03a/b/c) |
| AC-S-10 | O-7 resolved:DecomposerPort 单签名 / OwnerResolver 两方法 / owner_bot_id 保留 / progress_snapshot 删除;且 BBS 上升后 task-owner 仅终验不做分解/执行 |
| AC-S-11 | **中间层聚合验收(exec-aggregate)**:递归拆解的父 subtask 在子全 exec-accept 通过后,经父 owner 聚合复核判 DONE/REJECTED,自底向上逐级;不靠节点状态二值(FR-GRAPH-08a) |
| AC-S-12 | **三终止完整**:验收通过聚合→DONE;递归上限→hang(AWAITING_HUMAN_*)→人确认;升 BBS 同图 / 不升→FAILED(FR-GRAPH-09) |
| AC-S-13 | **此阶段范围**:执行模态 SINGLE_BOT + COOP_GROUP(CHAT/MANAGER_WORKER);BBS 上升衔接契约此阶段定,广场执行后续(§4.1/O-9) |
| AC-S-14 | **搜推先行**:每(子)任务先 bot-search;未匹配/验收不过才 decomposition;不直接 C5/BBS(FR-GRAPH-14) |

---

## 11. 变更记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-08-01 | 待确认 | 初版 spec:TaskExecutionGraph 重构为全生命周期 State/Node/Edge 统一图谱;State 升一等;搜推/分解/验收回到 bot SKILL;递归显式;BBS 同图延续。supersede 本目录 design.md v1。沿 7 态状态机。 |
| 2026-08-01 | 待确认 | 增订:两级 DONE(子任务 exec-bot / 任务 task-owner),O-2 resolved;State 三能力 SSOT/归约/回溯(FR-GRAPH-03a/b/c);O-7 改为待用户确认。 |
| 2026-08-01 | 待确认 | O-7 resolved(用户确认):DecomposerPort 退单签名;OwnerResolver 缩两方法;owner_bot_id 保留且 BBS 后 task-owner 仅终验不做分解/执行;progress_snapshot 删除。 |
| 2026-08-01 | 待确认 | **v2 校准(基于 2026-07-31 重构会话收敛)**:① 三终止补 `hang→人确认→升 BBS / 不升 FAILED`(FR-GRAPH-09/AC-S-12);② 补中间层聚合验收 exec-aggregate,自底向上逐级(FR-GRAPH-08a/AC-S-11);③ 声明此阶段范围 SINGLE_BOT+COOP_GROUP、BBS 衔接契约此阶段定/广场执行后续(§4.1/AC-S-13/O-9);④ 订正伪代码边源点 e10/e12/n13(O-3 resolved);⑤ 强化搜推先行 FR-GRAPH-14/AC-S-14;⑥ 补 O-8 exec-aggregate 触发时机。 |
| 2026-08-01 | 待确认 | v2 增订:exec-aggregate 机制精确化——由各级 subtask 的 owner bot 经 `task-exec-skill` 读 State(`retrieve-state`:下属子任务产出验收 + 父 subtask 自身 `targets_acceptance`)聚合判断(FR-GRAPH-08a);用户确认保留。 |
| 2026-08-01 | 待确认 | v2 增订:完成判断逻辑统一——`exec-aggregate`(父级)与 `goal-verify`(任务级)均为"读 State(下属产出验收 + 自身验收标准)聚合判断 DONE",区别仅作用域/验收标准来源(FR-GRAPH-07b);goal-verify 改同款聚合表述。 |
| 2026-08-02 | 待确认 | v2 增订:**两维度正交模型**——Node 维度=动作/事件粒度(所有动作皆节点,含 exec-accept/exec-aggregate/goal-verify 三个判定动作);State 维度=Task/SubTask 实体粒度(TaskState + SubtaskState 含 status)。判定动作是 Node,其效果翻实体 status 是 State;两维度各落一笔,非二选一。确认三个判定均为节点(原伪代码口径)。 |