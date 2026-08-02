# tasks.md — TaskExecutionGraph 全生命周期重构实施清单

> 隶属:`2026-07-31-task-executor-alignment/`;实现 `spec.md`(v2)+ `plan.md`(v2)。
> 落点:`src/backend/src/agentclaw/community/core/task/`(开源 Avernet;sync 到 ocb-public,见 plan §17A.2)。
> 约束:plan §17A(落点判据/api 零 core/bcsfuse 不依赖/副屏 corp/搜推 Port 保留/recognition UMD)+ §18(代码变更地图,设计为权威不推翻)+ §18 顶部实施者硬性约束。
> **TDD**:E2E-1..12(plan §20)是 review 里程碑,先红→绿;纯单测按需自补(不在 review,但每个任务列了关键单测作 done-when)。
> 状态机沿用 `2026-07-30-task-status-state-machine-alignment/`(7 态 TaskStatus / 6 态 NodeStatus)。

## 约定
- `T-NN` 编号;`[开源]`/`[corp-only]` 落点标(plan §17A.1);依赖见各任务 `依赖:`。
- `done-when`:可测验收条件;`测试`:E2E-X(plan §20)或单测名(U-*)。
- 实施顺序:Phase 0 → 6;Phase 6 E2E 依赖 Phase 1-5 落地。每步 lint(SAST)+ 相关测试绿才推进。
- 严禁:**不推翻设计**(设计↔代码冲突以设计为准、改代码);**不直接改 ocb-public**(Avernet 改→用户 sync);**不落 ecb**;**api 不 import core**;**不依赖 bcsfuse**。

---

## Phase 0 — 前置 bug 修(顺手,解锁后续)

### T-01 [开源] ORM `ac_task.status` 默认值订正
- 依赖:—
- 输入:plan §18.1-11
- 改动:`repository/models.py` `AcTaskModel.status` 默认 `"intake"`→`"drafting"`;同步 `ac_task.sql` DDL default;存量不迁(新任务生效)。
- done-when:新建任务落 DB `status="drafting"`(= `TaskStatus.DRAFTING`);单测 `U-status-default` 断言默认值。
- 测试:U-status-default

---

## Phase 1 — 领域模型与状态机(§3/§5/§6)

### T-02 [开源] `NodeType` 枚举
- 依赖:— ｜ 输入:plan §3.1 ｜ spec FR-GRAPH-01/08a/09
- 改动:`domain/models.py` 加 `NodeType(StrEnum)`(RECOGNITION/CLARIFY/EXECUTE_START/BOT_SEARCH/DECOMPOSITION/DISPATCH/EXEC_ACCEPT/EXEC_AGGREGATE/GOAL_VERIFY/MARK_HANG/BBS_DISPATCH)。
- done-when:枚举 11 成员;`U-nodetype` 断言值。
- 测试:U-nodetype

### T-03 [开源] `TaskState` + `SubtaskState`(实体维度)
- 依赖:— ｜ 输入:plan §3.2/§2 两维度 ｜ spec FR-GRAPH-02/03a/03b
- 改动:`domain/models.py` 加 `GapRecord`/`SubtaskState`(含 `status: NodeStatus`、depth、execution_context、intermediate_results、artifacts、gap_records)/`TaskState`(public + `subtasks: dict[str, SubtaskState]`)/`GraphSnapshot`。
- done-when:`SubtaskState.status` 默认 PENDING;`U-subtask-state` 断言字段。
- 测试:U-subtask-state

### T-04 [开源] `Node`/`TaskExecutionGraph`/`SubTaskSpec`/`Task` 改造
- 依赖:T-02,T-03 ｜ 输入:plan §3.3-3.7 ｜ spec FR-GRAPH-01/02
- 改动:`Node` 加 `node_type`、移 `artifacts`/`instruction`(→ `SubtaskState`);`TaskExecutionGraph` 加 `state: TaskState`;`SubTaskSpec` 加 `depth`;`Task` 加 `owner_bot_id`(顶层权威,§18.1-6 与 `ExecutionMeta.owner_bot` 定:顶层权威、后者录入初值)、移 `plan`(已在 Task 聚合根,§memory B)。
- done-when:`Node` 无 artifacts/instruction;`Task` 无 plan;`U-model-migration` 断言字段迁出/默认值。
- 测试:U-model-migration

### T-05 [开源] `GraphStatus` 迁移守卫
- 依赖:— ｜ 输入:plan §5.1 ｜ spec FR-GRAPH-11
- 改动:`domain/state_machine.py` 加 `GRAPH_TRANSITIONS` + `require_graph_transition`;澄清 ON_PLAZA/AWAITING_HUMAN_ACCEPT(hang+BBS 同门,plan §5.1 v2 释义)。
- done-when:`mark_graph_status` 经 guard(非裸赋值);`U-graph-guard` 合法过/非法抛错。
- 测试:U-graph-guard

### T-06 [开源] 新 `EventKind` + state_patch
- 依赖:— ｜ 输入:plan §6 ｜ spec FR-GRAPH-03b
- 改动:`domain/events.py` 加 `NODE_ADDED/EDGE_ADDED/STATE_UPDATED/PLAN_REQUESTED/EXEC_AGGREGATED/NODE_HANG`;节点事件 payload 带 `state_patch{scope,patch,semantics}`;`HUNG` 保留 deprecated(无 writer)。
- done-when:`U-events` 枚举 + state_patch payload 结构。
- 测试:U-events

---

## Phase 2 — 协议与契约(§4/§17A.4)

### T-07 [开源] `DecomposerPort` 退单签名
- 依赖:T-03 ｜ 输入:plan §4.1/§18.1-2 ｜ spec O-7①
- 改动:`protocols.py` `DecomposerPort` 改单签名 `decompose(spec, state) -> list[SubTaskSpec]`;`DecomposerService` 改实现(保留规则分句,加 state 入参 + `depth=父 SubtaskState.depth+1` 填充);`DecomposerService.__init__` DI 风格统一(去 plain Optional[TaskRepo] 不一致,§18.1-2)。
- done-when:`U-decompose` 返 `list[SubTaskSpec]` 且 depth=父+1;无 `decompose_node`/`task_id->Plan` 旧签名残留。
- 测试:U-decompose

### T-08 [开源] `OwnerResolver` Port
- 依赖:— ｜ 输入:plan §4.2/§18.1(net-new) ｜ spec O-7②
- 改动:`protocols.py` 加 `OwnerResolver`(`resolve_group_owner(group_id)->str` + `resolve_task_owner(task_id)->str`);self(单 bot 自验收)内联不走 Port;`resolve_task_owner` 读 `Task.owner_bot_id`,缺失抛错。
- done-when:`U-owner-resolver` 两方法 + 缺 owner_bot_id 抛错。
- 测试:U-owner-resolver

### T-09 [开源] `TaskService` 图操作 API + `aggregate_verdict`
- 依赖:T-03,T-05 ｜ 输入:plan §4.3/§12A ｜ spec FR-GRAPH-03/07b
- 改动:`protocols.py` `TaskService`(core)加 `add_node/add_edge/update_state/retrieve_state/snapshot` + `StateSemantics`;加纯函数 `aggregate_verdict(self_acceptances, child_results) -> (AttemptOutcome, list[str])`。
- done-when:`U-aggregate-verdict` 同款判定(父 `targets_acceptance` 与 Task `goal.acceptances` 同输入同输出)。
- 测试:U-aggregate-verdict

### T-10 [开源] api 层零 core 依赖守卫
- 依赖:T-09 ｜ 输入:plan §17A.4
- 改动:`api/task/service_api.py` 形态不变(`*args/**kwargs->Any`,零 core import);新图 API 经 core Protocol 透传,api 不加具体签名;`api/task/__init__.py` 只 re-export 2 api Protocol。
- done-when:`test_task_service_api_conformance.py` 绿(AST 断言 api 不 import `agentclaw.community.core.*`)。
- 测试:test_task_service_api_conformance

---

## Phase 3 — TaskService 实现(§7.1/§8)

### T-11 [开源] 图操作实现 + 唯一写口收敛
- 依赖:T-06,T-09 ｜ 输入:plan §7.1/§8.1/§18.1-7/8 ｜ spec FR-GRAPH-11/FR-EVENT-02
- 改动:`task_service.py` 实现 `add_node/add_edge/update_state/retrieve_state`(guard→fold→append event→save);`mark_graph_status` 加 §5.1 guard;**收敛 scheduler 裸 `_task_repo.save`**(图/状态变更经 `on_event` fold,§18.1-7);TaskService Protocol 显式声明图 API(消 impl-only 缺口,§18.1-9)。
- done-when:`U-graph-ops` add_node/add_edge 落图 + 事件;`U-single-writer` 直改 Node.status 拒写。
- 测试:U-graph-ops, U-single-writer

### T-12 [开源] State fold 归约
- 依赖:T-11 ｜ 输入:plan §8.2 ｜ spec FR-GRAPH-03b
- 改动:`TaskState.fold(patch, semantics)` 实现 §3.2 归约表(MERGE 深合并 / APPEND 去重 / OVERWRITE 单调);`SubtaskState.status` 经状态机 guard;artifacts 按 name 去重。
- done-when:`U-fold` 四语义 + 去重 + depth 单调。
- 测试:U-fold

### T-13 [开源] retry/reroute 统一到 tick 驱动 + reroute 交 skill
- 依赖:T-11 ｜ 输入:plan §7.1/§16 R-1/§18.1-12 ｜ spec FR-LOOP-04/FR-GRAPH-08
- 改动(修订 §18.1-12,原"`_handle_node_failed` 同执行方 inline 重派"旁路退场):
  - **retry 由 `tick` 驱动**:`_tick` 放开"只推进 PENDING"的限制,对 FAILED-Dispatch 节点
    调 `_retry_failed` —— `attempts < max` 经 `claim_node` 同执行方 re-claim+fire(状态机
    FAILED→RUNNING + **追加 AttemptedRecord 推进计数** + fire ExecutionPort),修掉"重派不经
    claim、计数不涨→死循环"bug;完成仍由 skill 经 `on_event` 异步回投。
  - **reroute 由失败方 exec-bot skill 判**(FR-GRAPH-08):到 `max` 不再重派,tick 向失败方
    exec-bot 派"重路由判定请求"(`ExecutionPort.probe`,guard `__reroute_probe_sent__` 只派一次);
    该 bot 的 `task-plan-skill` 判是否 reroute → 发起 gap `bot-search`(retrieve-state 上下文)
    → `add_node(BOT_SEARCH)` → 后续 tick 处理(命中 dispatch / 未匹配 decomposition)。
    **reroute 是 skill 判定 + 图操作,非 scheduler 的 `redispatch(C5)` 规则** → 删 `_handle_node_failed`
    的 C5 reroute 分支;`TaskScheduler.on_event` 改为"NODE_FAILED 落态 fold 后泵一次 `tick`",tick 为
    唯一驱动权威。
  - router `POST /events`:`TaskService.on_event`(落态 fold)+ `Scheduler.on_event`(泵 tick)
    双调,补上原 design §... 漏接的编排反应半。
- done-when:`U-retry-redispatch` 同执行方 re-claim 重派、计数真实推进;`U-retry-exhausted` 到上限
  停止重派 + 派 reroute 判定给 skill(probe 被调一次);`U-no-c5-rule` `driver.redispatch(C5)` 不被调;
  `U-unrecoverable-hang` probe 已派 + skill 未 reroute(无兄弟 BOT_SEARCH)→ tick 自动挂起
  AWAITING_HUMAN_ACCEPT → 人 HANG_CANCELLED → task FAILED(§18.1-12 unrecoverable 留待 MARK_HANG 落地)。
- 测试:U-retry-redispatch, U-retry-exhausted, U-no-c5-rule, U-unrecoverable-hang
  (e2e:test_node_failed_retries_same_executor_then_asks_skill_reroute, test_node_failed_unrecoverable_hang_then_human_decline_task_failed)

### T-14 [开源] `graph_checkpoint` 回溯
- 依赖:T-11 ｜ 输入:plan §8.3 ｜ spec FR-GRAPH-03c/AC-S-09
- 改动:新 `services/graph_checkpoint.py`(或并入 task_service):`snapshot(task_id)` 落 fold@seq;断点重跑(从快照 seq 重放 events);回滚(截断日志 + 重算 fold)。
- done-when:`U-snapshot-replay` 重放与原 fold 一致;`U-rollback` 回到 k 时刻;`U-seq-monotonic` disorder 抛错。
- 测试:U-snapshot-replay, U-rollback, U-seq-monotonic

### T-15 [开源] `graph_adapter` 数据面投影
- 依赖:T-02,T-11 ｜ 输入:plan §7.1/§9/§18.1-13/§17A.5 ｜ spec FR-OBS-05
- 改动:`task_service._node_view` + `graph_adapter._to_node_view` 增 `state` 分区 + `render_kind`(exec/control-gate/system-bridge,按 node_type)+ `judge_outputs`(现 fold 进 acceptance_result,拆出);顶层 `_edge_view` 增 `outcome/guard`。**仅后端数据面;不改 `src/frontend/`,不写画布**(§17A.5)。
- done-when:`U-node-view` render_kind 按 node_type 正确;EXEC_AGGREGATE→control-gate;顶层 edge 带 outcome。
- 测试:U-node-view

---

## Phase 4 — TaskScheduler 重构(§7.2/§12/§12A)

### T-16 [开源] 移除 tick inline 搜推(搜推反转)
- 依赖:T-11 ｜ 输入:plan §7.2/§18.1-1/§17A.6 ｜ spec FR-GRAPH-04/14
- 改动:`task_scheduler.tick` 不再 inline 调 `BotDiscoverPort.recommend`;**Port 保留**(§17A.6),invocation 迁 `BOT_SEARCH` 节点(owner/exec-bot SKILL 调 + 回投结果落图)。
- done-when:`U-no-inline-discover` tick 不直调 `_discover.recommend`;搜推经 BOT_SEARCH 节点回投。
- 测试:U-no-inline-discover

### T-17 [开源] 搜推先行 + 未匹配→分解触发
- 依赖:T-16 ｜ 输入:plan §7.2/§18.1-5 ｜ spec FR-GRAPH-14/05
- 改动:`tick` 对 PENDING/REJECTED (子)任务先要求 BOT_SEARCH;full-cover→DISPATCH;**未匹配→DECOMPOSITION**(现仅 acceptance-fail `_split_node` 触发,C4 route 死分支,§18.1-5,新增该路径);未搜推不拆、未匹配(未触上限)不直接 BBS/hang。
- done-when:`U-search-first` 未搜推 decompose 不被调;未匹配走 decomposition 非 BBS/hang。
- 测试:U-search-first

### T-18 [开源] `EXEC_AGGREGATE` 触发(O-8/O-P4)
- 依赖:T-11,T-09 ｜ 输入:plan §12A ｜ spec FR-GRAPH-08a/AC-S-11
- 改动:`tick` 扫描 State(自底向上):父 subtask 依赖的 child(exec-accept)全 DONE 且父未闭合 → 落 `EXEC_AGGREGATE` 节点 + 经 OwnerResolver 派发父 owner 聚合验收;幂等(已闭合短路);根 subtask 闭合 → 触发 GOAL_VERIFY。
- done-when:`U-agg-trigger` 子全 DONE 触发;`U-agg-partial` 2/3 不触发;`U-agg-idempotent` 不重复派发。
- 测试:U-agg-trigger, U-agg-partial, U-agg-idempotent

### T-19 [开源] 递归上限 → MARK_HANG
- 依赖:T-07,T-11 ｜ 输入:plan §11/§13 ｜ spec FR-GRAPH-09/AC-S-12
- 改动:`DECOMPOSITION` 产 children `depth >= MAX_RECURSION_DEPTH(=3)` → 拒 `add_node` → 落 `MARK_HANG` 节点 + `mark_graph_status(AWAITING_HUMAN_ACCEPT)`(guard);不直接升 BBS。
- done-when:`U-mark-hang` 触上限落 MARK_HANG + graph AWAITING_HUMAN_ACCEPT;无 BBS_DISPATCH。
- 测试:U-mark-hang

### T-20 [开源] `scheduler.start ↔ n4` 驱动时序
- 依赖:T-16 ｜ 输入:plan §12 ｜ spec O-6
- 改动:`EXECUTE_START` 节点:owner-bot task-execute → `PLAN_REQUESTED` → on_event guard DRAFTING→DEFINED + 建空图 → scheduler 经 ExecutionPort 请 owner-bot 规划 → owner-bot task-plan-skill `BOT_SEARCH`。
- done-when:`U-start-timing` 时序:DEFINED 建图 → PLAN_REQUESTED → BOT_SEARCH 节点。
- 测试:U-start-timing

---

## Phase 5 — BBS 衔接 + 终验(§13/§5.2)

### T-21 [开源] `BbsExecutor.retrieve_state` + 识别 node_type
- 依赖:T-11 ｜ 输入:plan §7.3/§18.1-4 ｜ spec FR-GRAPH-10
- 改动:`bbs_executor.py` **加 `retrieve_state`**(progress_snapshot 不存在,非删,§18.1-4);`claim/post_progress` 识别新 node_type(BBS 阶段 DISPATCH/DECOMPOSITION/EXEC_ACCEPT run_mode=BBS);清理 stale docstring "BBS goal-FAIL→HUNG"(与代码矛盾,§18.1-4)。
- done-when:`U-bbs-retrieve` BBS bot 经 retrieve_state 取上下文;无 progress_snapshot 残留。
- 测试:U-bbs-retrieve

### T-22 [开源] BBS 上升衔接(同图延续)
- 依赖:T-19,T-21 ｜ 输入:plan §13/§18.1-4 ｜ spec FR-GRAPH-09/10/AC-S-05/12
- 改动:扩 `_apply_goal_verdict`/hang 确认 fail 分支:`AWAITING_HUMAN_ACCEPT`(+人确认)→ `ON_PLAZA`(guard)→ 落 `BBS_DISPATCH`;接 `BbsExecutorService.claim` 调用方(现无调用方,§18.1-4);BBS 阶段 DISPATCH/DECOMPOSITION/EXEC_ACCEPT 同图 add_node;不升 → task FAILED。
- done-when:`U-bbs-escalate` 升 BBS:graph→ON_PLAZA + BBS_DISPATCH + BbsExecutor.claim 被调 + 同图延续。
- 测试:U-bbs-escalate

### T-23 [开源] 判定节点 fold + 三终止
- 依赖:T-18,T-22 ｜ 输入:plan §5.2/§12A/§13 ｜ spec FR-GRAPH-07/07a/07b/08a/09/AC-S-08/11/12
- 改动:`EXEC_ACCEPT`/`EXEC_AGGREGATE`/`GOAL_VERIFY` 节点 fold:`EXEC_AGGREGATED`(DONE→父 `SubtaskState.status=DONE`/REJECTED→回 gap)、`GOAL_VERIFIED`(→`Task.status=DONE`)/`GOAL_REJECTED`(BBS 前→回 gap/或 MARK_HANG;BBS 后→FAILED 终态);三终止闭合(聚合 DONE/hang→BBS或FAILED/FAIL回gap)。
- done-when:`U-verdict-fold` 三判定 fold 正确;`U-three-terminals` 三终止分支。
- 测试:U-verdict-fold, U-three-terminals

### T-24 [开源] 升 BBS 确认 / cancel 通道
- 依赖:T-22 ｜ 输入:plan §13/§18.1-10 ｜ spec FR-GRAPH-09
- 改动:升 BBS 确认 + 不升 cancel:**倾向走 `POST /tasks/{id}/events` 回投确认事件**(统一通道,§17A/§18.1-10),fold 驱动 §22 转移;若评审定要专用路由则加 `POST /escalate-bbs`+`/cancel`(暴露已有 `TaskService.cancel`)。
- done-when:`U-confirm-channel` 确认事件 fold → AWAITING_HUMAN_ACCEPT→ON_PLAZA 或 →FAILED。
- 测试:U-confirm-channel

---

## Phase 6 — E2E TDD 里程碑(plan §20,红→绿)

> 每条:先写 E2E 测试(红)→ 实现至绿。mock 按 plan §19.1(搜推 `BotDiscoverPort` 编程序列;skill 判验 = 直接 `on_event` 注入 `NODE_ACCEPTED/REJECTED/GOAL_VERIFIED/EXEC_AGGREGATED`;`TaskRepo`/`TaskEventRepo`/guard/`aggregate_verdict` 真实)。依赖:Phase 1-5。

### T-25 E2E-1 单 bot happy path(基线)
- 依赖:Phase 1-5 ｜ 输入:plan §20 ｜ spec FR-GRAPH-01/06/07/07a/14;AC-S-01/08/14
- done-when:recognition→clarify→execute-start→bot-search C1→dispatch→NODE_ACCEPTED→GOAL_VERIFIED→`Task.status=DONE`,`graph_status=VERIFIED`;图含 e1/e2/e3/e8。
- 测试:E2E-1

### T-26 E2E-2 协作群 happy(COOP_GROUP)
- 依赖:T-25 ｜ spec FR-GRAPH-07;AC-S-13
- done-when:C3 群派发;群 owner exec-accept;终验归 task-owner;`Task.status=DONE`。
- 测试:E2E-2

### T-27 E2E-3 搜推未匹配→分解→子任务命中→中间层聚合→终验(主链路)
- 依赖:T-18,T-23 ｜ spec FR-GRAPH-05/08a/12/14;AC-S-04/11/14
- done-when:顶 task 未匹配→decomposition(3 children 并行)→各命中→EXEC_AGGREGATE(DONE)→GOAL_VERIFIED→DONE;children 间无 DEPENDENCY。
- 测试:E2E-3

### T-28 E2E-4 验收 fail→重路由命中(节点身份不变)
- 依赖:T-23 ｜ spec FR-GRAPH-08/FR-TASK-04;AC-S-04
- done-when:NODE_REJECTED→bot-search(retrieve-state 带上轮 gap)→redispatch 同 node_id(`attempted_executors` 追加)→accept→聚合→终验 DONE。
- 实现(NODE_FAILED reroute 后半段,与 T-13 衔接):失败方 skill 经 `TaskService.open_reroute_search`
  发起 gap bot-search(挂失败节点父下兄弟 BOT_SEARCH,带 gap_spec)→ `tick._bot_search` 命中 →
  落 DISPATCH 重派新执行方 → claim+fire。`open_reroute_search` 同时把原失败节点标 superseded
  (FAILED→DONE,状态机合法),免常驻 FAILED 挡 `_maybe_goal_verify` 的"有 FAILED 不终验"guard,
  使 reroute 成功后全图 DONE 能终验。测试:`test_node_failed_reroute_hit_dispatches_new_executor`
  (含 reroute 成功 → task DONE 终验)。
- 测试:E2E-4

### T-29 E2E-5 重路由未匹配→递归拆解(depth+1)
- 依赖:T-19 ｜ spec FR-GRAPH-05/08/09;AC-S-04
- done-when:重路由未匹配→decomposition(children depth=父+1)→各命中→父聚合 DONE→终验 DONE。
- 实现(reroute-miss):`open_reroute_search` 发起的 BOT_SEARCH 未命中 → `tick._bot_search` 落
  DECOMPOSITION 子(spec=gap)→ `decompose_subtasks` 产 children(depth=失败节点+1)→ 各命中派发。
  测试:`test_node_failed_reroute_miss_recursive_decompose_depth_plus_one`。
- 配套修正:`_maybe_goal_verify` 增判"有未解决 FAILED 节点不终验"(否则 FAILED 叶子在、无 PENDING/RUNNING
  时会误判 PASS 终态,把 retry/reroute 中的 task 提前 DONE)。
- 测试:E2E-5

### T-30 E2E-6 递归上限→hang→升 BBS→同图延续→终验
- 依赖:T-22,T-24 ｜ spec FR-GRAPH-09/10;AC-S-05/10/12
- done-when:depth≥MAX→MARK_HANG→AWAITING_HUMAN_ACCEPT→确认升 BBS→ON_PLAZA→BBS bot claim 执行/分解→逐级聚合→GOAL_VERIFIED(task-owner)→DONE;同图、无 progress_snapshot。
- 测试:E2E-6

### T-31 E2E-7 不升→FAILED
- 依赖:T-24 ｜ spec FR-GRAPH-09;AC-S-12
- done-when:MARK_HANG→确认不升→`Task.status=FAILED`;无 BBS_DISPATCH;终态不回环。
- 测试:E2E-7

### T-32 E2E-8 goal-verify FAIL(BBS 前)→回 gap
- 依赖:T-23 ｜ spec O-P2/FR-LOOP-01/04
- done-when:GOAL_REJECTED(graph 未 ON_PLAZA)→不直接 FAILED;回 gap/或 MARK_HANG;触发新一轮 bot-search/decomposition(限轮次)。
- 测试:E2E-8

### T-33 E2E-9 goal-verify FAIL(BBS 后)→FAILED 终态
- 依赖:T-23 ｜ spec O-P2/FR-LOOP-03;AC-S-12
- done-when:BBS 阶段(graph ON_PLAZA)GOAL_REJECTED→`Task.status=FAILED`;无再回环/再 escalation。
- 测试:E2E-9

### T-34 E2E-10 搜推先行约束(负向)
- 依赖:T-17 ｜ spec FR-GRAPH-14;AC-S-14
- done-when:未搜推 decompose 不被调;未匹配(未触上限)不落 BBS_DISPATCH/MARK_HANG,走 decomposition。
- 测试:E2E-10

### T-35 E2E-11 并行无依赖 + 混合分支
- 依赖:T-18 ｜ spec FR-GRAPH-08/08a/12;AC-S-04/11
- done-when:3 children 并行(命中/未匹配再拆/REJECTED→命中)三分支演进;父三者全闭合才聚合;终验 DONE。
- 测试:E2E-11

### T-36 E2E-12 watchdog→probe→redrive→C5 escalation
- 依赖:T-13 ｜ spec FR-LOOP-04(plan §17A.7 watchdog 计数留 node.properties)
- done-when:长 RUNNING→watchdog PROBE→REDRIVE→仍 fail→route C5→`TaskDriverPort.escalate_to_bbs`(节点级,区别 goal-FAIL 的 AWAITING_HUMAN_ACCEPT);计数推进。
- 测试:E2E-12

---

## Phase 7 — 收尾

### T-37 [开源] 契约测试
- 依赖:Phase 2-5 ｜ 输入:plan §17
- 改动:`tests/contracts/test_*.py`:DecomposerPort 单签名 / OwnerResolver 两方法 / TaskService 图 API / mark_graph_status guard / aggregate_verdict 同款判定 / api 零 core 依赖。
- done-when:契约测试全绿。
- 测试:contracts

### T-38 [开源] 迁移与兼容(§15)
- 依赖:T-04 ｜ 输入:plan §15
- 改动:`Node.artifacts`/`instruction` 删前留 1 版只读投影(`_node_view` 临时回填);`Task.plan` 删除后外部读改 `execution_graph.nodes`;DI 加 `OwnerResolver` local/prod 实现(§17A.1 corp 复用 community 内核)。
- done-when:无外部 `task.plan` 读残留;`U-migration-compat` 投影可读。
- 测试:U-migration-compat

### T-39 [开源] 注释/docstring 清理
- 依赖:T-21 ｜ 输入:plan §18.1-4
- 改动:清 `bbs_executor.py`/protocols stale "BBS goal-FAIL→HUNG"(与 7 态机矛盾);`HUNG` deprecated 注释保留;新节点类型 docstring 对齐 §9 执行者表。
- done-when:无 stale HUNG 描述;`U-docstring` 抽检。
- 测试:U-docstring(抽检)

### T-40 [开源] 全量 gate + pre-push
- 依赖:全部 ｜ 输入:AGENTS.md pre-push 契约
- 改动:lint(SAST flake8/antflake)+ 全单测 + E2E-1..12 全绿;`OCB_PRE_PUSH_RUN_CI=1` 对 `origin/dev` 跑模块 gate;Avernet commit → 提示用户 sync ocb-public。
- done-when:本地 gate 全绿;待用户 sync 后 singlebox `--dev` 复跑 E2E。
- 测试:全量

---

## 依赖图(摘要)
- Phase 0(T-01)独立,先行。
- Phase 1(T-02..T-06)模型/状态机/事件基础,互轻依赖。
- Phase 2(T-07..T-10)协议,T-07 依赖 T-03;T-10 守卫。
- Phase 3(T-11..T-15)TaskService/fold/checkpoint/adapter,依赖 Phase 1-2。
- Phase 4(T-16..T-20)Scheduler,依赖 Phase 3;T-18/T-19 依赖 T-09/T-07。
- Phase 5(T-21..T-24)BBS/终验,依赖 Phase 4。
- Phase 6(T-25..T-36)E2E,依赖 Phase 1-5;T-25 基线先行,其余按链路依赖。
- Phase 7(T-37..T-40)收尾,依赖全部。

## E2E → 链路覆盖对照
| E2E | 链路 | 解锁任务 |
|---|---|---|
| E2E-1 | 单 bot happy 基线 | T-25 |
| E2E-2 | 协作群 happy | T-26 |
| E2E-3 | 搜推未匹配→分解→聚合→终验(主) | T-27 |
| E2E-4 | 验收 fail→重路由命中 | T-28 |
| E2E-5 | 重路由未匹配→递归拆解 | T-29 |
| E2E-6 | 递归上限→hang→升 BBS→同图→终验 | T-30 |
| E2E-7 | 不升→FAILED | T-31 |
| E2E-8 | goal-verify FAIL(BBS 前)→回 gap | T-32 |
| E2E-9 | goal-verify FAIL(BBS 后)→FAILED | T-33 |
| E2E-10 | 搜推先行约束(负向) | T-34 |
| E2E-11 | 并行无依赖混合分支 | T-35 |
| E2E-12 | watchdog→C5 escalation | T-36 |