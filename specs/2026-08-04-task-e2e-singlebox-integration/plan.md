# Plan — 存储行业尽调 Task 端到端 Singlebox 集成用例
> 关联:`spec.md`(本目录)、源 case `docs/2026-08-03-task-impl-briefing.md`。
> 日期:2026-08-04。
## §0 决策锁定
- **owner-bot 全真**:owner-bot 是真实 singlebox openclaw bot,装入真实 `SKILL.md` skill,**自驱**调 task HTTP API。
- **v1 拉通动态协作群**:SINGLE_BOT + 动态 COOP_GROUP(真实 BCS 拉群 + 群 master 聚合回投)+ BBS + 终验 reroute loop 全跑。
- **本地 singlebox 搜推**:`BotDiscoverService.recommend`(task-module,关键词 cover)+ 本地 `BotCatalogPort` 注入"存储行业尽调"专用 bot,不打云上 BCSFuse。
---
## §1 现状对账(codebase 实际 vs 本用例需要)
| # | 链路要素 | 现状 | 需要做 |
|---|---|---|---|
| 1 | task 13 HTTP 端点 + 1 WS | ✅ `adapters/http/task/router.py` 全在 | 直接用 |
| 2 | `POST /api/bots` 建测试 bot + BCS 连网 | ✅ `scripts/modules/demo_bot.sh` 已有 | 扩成多 bot(profile 化) |
| 3 | owner-bot 5 个 skill(recognition/clarify/execute/plan/goal-verify) | ❌ briefing 概念角色,无 `SKILL.md` | **开发 SKILL.md + 调 task API 的 adapter** |
| 4 | 执行 bot `task-exec-skill` | ❌ | **开发 SKILL.md(自验收 + 回投)** |
| 5 | skill 装入 bot | ✅ 机制=openclaw.json `skills.entries` + `plugin-skills/` 目录(无 per-skill HTTP) | 配置接入 |
| 6 | 给 bot/群发消息(测试→owner-bot) | ⚠️ engine chat WS `_stream_chat_events` / BCN `chat.send`(BCS→bot) | **封装一个测试用 HTTP/WS 发消息客户端**(参考 `plugin_api/http_client.py`) |
| 7 | `dispatch(route, target, prompt, *, group=None)` 统一派发(三分支:`single_bot`/`group`/`unmatched`) | ⚠️ `ExecutionPort.dispatch_single_bot` 在但未暴露;`coop_group` noop-ish | **封装统一派发入口**:`single_bot`→BCN chat.send;`group`→BCS `POST /groups` 建群(动态)+ 群 chat.send;`unmatched`→dispatch(owner-bot, 拆解prompt) |
| 8 | (并入 #7 的 `group` 分支)动态建群 BCS `POST /groups`、`GET /groups/my` | ⚠️ BCS 群接口在,task 侧未调 | **`dispatch(route="group", target=None, group=GroupSpec(...))` 真实拉群 + 群派发** |
| 9 | `BotDiscoverService.search_by_keyword`(bot 搜推) | ✅ `bot_public` 版(走 BCSFuse);task 调度用 task-module `recommend`(cover) | 用 task-module `recommend` + 本地 catalog |
| 10 | `GroupDiscoverService.search_by_keyword`(已有协作群搜推,调 `bcs:/api/groups`) | ❌ | **新建,参考 bot 版,调 BCS `/groups/my`** |
| 11 | 终验 / reroute / gap_records | ✅ 内核已实现(briefing §2.6) | 用;断言 |
| 12 | 集成用例骨架 | ❌ task 端点 0 集成测试 | **新建 singlebox pytest E2E** |
> 关键认知:**bot 不直连写 task 状态**,bot 产出经 BCN `chat.event(final)` 回报,由 **skill/adapter 翻译**成 `POST /api/tasks/{id}/events`。所以"全真 owner-bot"= owner-bot 的 skill 里要内建一个"调 task API 的 HTTP client"。
---
## §2 端到端 API 执行链路(每步真实 API + 入参 + 出参 + 发起方 + 落点)
> `task_id` 实例 `task-8a3f2c1b9d0e4756`(记 `T1`)。所有 task 端点前缀 `/api/tasks`。
> **发起方角色**:测试驱动 / owner-bot 各 skill / 执行 bot task-exec-skill / 群 master / BBS bot / 系统 on_event 泵。
### §2.0 驱动模型(reactor)+ 节点定义 + 执行逻辑(定稿)
> 解答"execute_task 没法同步驱动动态增长的图 / dispatch 异步 / 动作节点与图节点对应不上 / events 触发不了 executor / 搜推·执行·验收应是子任务执行器内部操作 / 终验不特殊"。
> 模型:**execute_task = bootstrap(不背图);on_event = 正向泵;tick = watchdog only**。**节点 = 实体**(子任务/阶段),动作 = 执行器**内部 phase**。**所有拆出来的子任务同构**:搜推→执行实体执行 + 执行实体自调 goal-verify 验收。终验也是一个普通子任务(plan-skill 决定要不要,通常最后一个),执行层不特殊对待。
#### 0. 核心原则(逐条敲定)
1. **节点 = 实体,不是动作**。图节点 = 阶段节点(少量) + 子任务节点。搜推/执行/验收/拆解/上升 = 子任务执行器**内部 phase**(`NodeState.phase`),不是 peer 级图节点。**不再建** `BOT_SEARCH`/`DISPATCH`/`EXEC_ACCEPT` 动作子节点。
2. **所有拆出来的子任务 `SUBTASK` 同构**:搜推命中执行实体 → 执行实体执行 + **自调 goal-verify skill** 验 `TaskSpec.acceptances` → `node.accepted`(verifier=执行实体)/ `node.rejected`(reroute)。无 leaf/aggregate/goal_verify kind 区分。
3. **执行 + 验收 = 同一执行实体的两步**(不拆给不同 bot)。执行实体按命中模式不同:命中单 bot→该 bot;命中协作群→群 master;动态拉自由聊天群→拉群 bot;升 BBS→认领的 BBS bot。验收都由该执行实体自调 goal-verify。
4. **goal-verify skill 在所有执行实体上**,用于自验。子任务级验 `TaskSpec.acceptances`;**终验子任务(plan-skill 拆出的,通常最后一个,如 3_2)的 acceptance = "任务目标达成",它自调 goal-verify 验任务级全 AC** —— "任务级验收"由终验子任务的执行实体跑 goal-verify 完成,owner-bot 不另跑终验。
5. **终验 = 普通 SUBTASK**。有没有终验节点由 plan-skill 拆解决定(通常最后);执行层不关心,按图执行。缺终验后续靠调优 plan-skill 或加固定终验节点修(规划层问题,不动执行层)。
6. **递归保留**:搜推 miss + depth<MAX → 自动拆(owner plan-skill)递归 spawn children(depth+1);miss + depth≥MAX → **HUNG**(仍执行不了,不再拆、不立刻 BBS)。HUNG 向上传播(child HUNG → parent aggregating 卡住 → parent HUNG/阻塞)。
7. **BBS = 任务级、用户确认才升**:HUNG 挂起等,DAG 其它可执行子任务继续(图保持 RUNNING);全可执行子任务 DONE 仍有 HUNG → 图 `HUMAN_REQUIRED` → **用户确认 → 整体任务升 BBS**(`BBS_ACTIVE`)→ HUNG 叶子广场悬赏 → BBS bot 认领执行+自调 goal-verify → 解除 HUNG。对上现成 `GraphStatus`:RUNNING→HUMAN_REQUIRED→BBS_ACTIVE→DONE。
8. **任务 DONE = 全子任务 accepted**(generic,终验若是最后一个,它 accepted 即全 DONE);某子任务 `node.rejected` → reroute(终验 rejected→补任务缺口,如缺 2_3_tech)。
#### 1. 节点定义(两视图 by `node_id`)
**Node**(图拓扑 + 高层状态 + 计划面内嵌;对齐 Yuque canonical):
```python
@dataclass
class Node:
    node_id: str               # 阶段:n_recognition 等;子任务:node_spec.metadata.id
    node_type: NodeType        # 4 种之一(见下),Executor discriminator
    status: NodeStatus         # pending/running/done/failed/hung
    run_mode: Optional[RunMode]   # 子任务搜推命中后填:single_bot/coop_group;BBS 非搜推产物(transition(CONFIRM_BBS)→escalating 期置 bbs)
    assignee: Optional[str]       # 子任务命中后填:bot_id / group_id(执行实体)
    properties: dict             # 运行旋钮 retry_count/max_attempts/loop_round/reroute
    node_spec: Optional[TaskSpec]  # ★计划面内嵌(子任务);阶段节点(RECOGNITION/CLARIFY/DECOMPOSE)为空
```
**TaskSpec**(计划面,内嵌于 `Node.node_spec`,既是 plan.reported 线格式也是持久字段;TaskSpec 已合并于此,根 `Task.spec` 与子任务 `Node.node_spec` 同型):
```python
@dataclass
class TaskSpec:
    metadata{id, title}                  # 子任务 metadata.id = node_id;根 metadata.id = task_id
    context{background, constraints[]}    # 客观事实与动机、红线与边界
    goal{objective, acceptances[AcceptanceCriteria]}  # 执行实体自调 goal-verify 验 goal.acceptances
    depend_on: list[str]                 # root=[];subtask=兄弟依赖(plan 期声明;edges 为 SSOT,此为便捷镜像)
    # depth 留 NodeState(非 TaskSpec);dispatch 文本从 goal.objective 派生(不存字段)
```
**NodeState**(运行面,在 `state.subtasks[node_id]`;对齐 Yuque):
```python
@dataclass
class NodeState:
    node_id: str
    status: NodeStatus
    phase: str                         # ★searching/executing/verifying/decomposing/spawning/aggregating/escalating/done/hung
    depth: int                         # 递归深度(根=0,children=父+1);判 miss+depth≥MAX→HUNG
    execution_result: dict             # 对齐 canonical;v1 内分三键:execution_context(MERGE)/intermediate_results·artifacts(APPEND),由 state.updated.semantics 选
    acceptance_result: Optional[AcceptanceResult]   # 终态验收结论(PASS/FAIL + acceptances_met + gaps + verifier)
    gap_records: list[GapRecord]       # APPEND(验收 gap 历史;reroute 补拆依据)
```
> `Node`(拓扑+计划)+ `NodeState`(运行)两视图 by node_id。`Node.node_spec` 一次性写定、运行期不改(reroute 追加新 Node,不改旧 node_spec);变的是 `NodeState`(phase 前进、产出累积)。
#### 2. NodeType = 4 种(定稿)
| NodeType | 谁执行 / 谁验收 | 何时落图 |
|---|---|---|
| `RECOGNITION` | owner recognition-skill | create_task |
| `CLARIFY` | owner clarify-skill | clarify_task(confirmed) |
| `DECOMPOSE` | owner plan-skill | execute_task 建根 / reroute 补拆 / 子任务 miss 自动拆 |
| `SUBTASK` | 搜推→命中执行实体(bot/协作群/自由聊天群/BBS)执行 + **执行实体自调 goal-verify 验收** | 所有拆出来的子任务(含汇总 3_1、终验 3_2、递归 children) |
> **删/降级**(不再作独立图节点):`BOT_SEARCH`/`DISPATCH`/`EXEC_ACCEPT`→SUB任务执行器内 phase;`EXEC_AGGREGATE`→SUBTASK(汇总就是搜推→汇总 bot 执行+自验的普通子任务);`DECOMPOSITION`→`DECOMPOSE`(规划节点);`GOAL_VERIFY`→**删**(终验是 SUBTASK,不是阶段节点);`EXECUTE_START`→并入 execute_task bootstrap(不单建节点)。
> owner-bot skills = recognition / clarify / plan(3 个,纯规划)。goal-verify skill 在**执行实体**上(各 bot/群 master/拉群 bot/BBS bot/终验 bot)。
#### 3. 驱动 = reactor
- **execute_task = bootstrap**:transition DEFINED→RUNNING + 落 `n_root(DECOMPOSE)` + `dispatch(route=unmatched,"拆解")` 给 owner plan-skill(fire-and-forget)。
- **on_event = 正向泵**:skill 回投 event → ①按 `(node_id, event.kind)` 定位节点 + fold 进 `NodeState` ②按 `(NodeType, phase, event.kind)` 派发到 executor phase handler ③phase 转移 / fold DONE / 发 async dispatch 置 waiting_callback ④后续回投再进 on_event……reactor 闭环。
- **tick = watchdog only**:不正向推进;只对 long-running RUNNING 节点超时探活(WAIT/PROBE/REDRIVE/ESCALATE)。★当前 `_tick` 当主驱动是 v1 简化,M1 降 watchdog。
#### 4. SUBTASK 状态机(定稿,统一一套)
```
SUBTASK: pending → searching(dispatch route=single_bot 给 owner 搜推能力)
  → discover.reported hit → executing(dispatch 命中的 bot/协作群/自由聊天群)
       → 执行实体产出 state.upred(MERGE/APPEND)+ 自调 goal-verify 验 acceptances
       → node.accepted(verifier=执行实体,见下表)/ node.rejected(reroute)
  → discover.reported miss + depth<MAX → decomposing(dispatch route=unmatched 给 owner plan-skill 拆)
       → plan.reported → spawning(children SUBTASK, depth+1)
       → children 全 DONE → aggregating → done
       → 有 child HUNG → 本节点 HUNG(向上传播)
  → discover.reported miss + depth≥MAX → HUNG(仍执行不了)
  → (HUNG 后)任务级用户确认升 BBS → escalating(escalate_to_bbs 广场悬赏)
       → BBS bot 认领 → executing(bbs) → 自调 goal-verify → node.accepted → done
```
**执行实体(=验收 verifier)按命中模式**:
| 命中模式 | 执行方 | 验收方(自调 goal-verify) |
|---|---|---|
| 命中单 bot | 该 bot | 该 bot |
| 命中协作群 | 协作群 | 群 master |
| 动态拉自由聊天群 | 自由聊天群 | 拉群 bot |
| 升 BBS 认领 | 认领的 BBS bot | 该 BBS bot |
#### 5. events → executor phase handler
事件回投到**子任务节点**(`node_id`/`parent_node` = subtask node_id,全程不变,无 `_disp`/`_dec` 后缀);泵按 `(NodeType, event.kind, phase)` 派发:
```
discover.reported{node_id: subtask_X}   → SubtaskExecutor.handle_search → hit:executing / miss+d<MAX:decomposing / miss+d≥MAX:HUNG
plan.reported{parent_node: subtask_X}   → SubtaskExecutor.handle_plan → spawning children
state.updated{scope: subtask_X}         → SubtaskExecutor.handle_exec_progress(fold MERGE/APPEND)
node.accepted{node_id: subtask_X}       → SubtaskExecutor.handle_accept → done + 依赖传播
node.rejected{node_id: subtask_X}       → SubtaskExecutor.handle_reject → reroute(终验 X rejected→补任务缺口)
deps-met subtask_X                       → SubtaskExecutor.enter → 入口 phase(子任务:searching)
```
#### 6. parent_node 感知
on_event `add_node`/drive 时分配 `node_id`,作为 `parent_node` 透传入 dispatch 消息;执行实体回投 payload 带 `node_id`(= parent_node)→ 泵定位节点。链:
```
execute_task add n_root(DECOMPOSE) → dispatch(parent_node=n_root,"拆解")
  → owner plan-skill 回投 plan.reported{parent_node:n_root, subtasks:[1_1,2_1,2_2,2_4,2_5,3_1,3_2]}
  → on_event add 7 SUBTASK 节点 → 泵 drive subtask_2_1(searching)
  → dispatch(parent_node=subtask_2_1,"搜推")
  → 搜推回投 discover.reported{node_id:subtask_2_1, route:group}
  → executing:dispatch(route=group, parent_node=subtask_2_1,"执行") → 动态建群 grp_market_research
  → 群 master 回投 state.updated{scope:subtask_2_1} + node.accepted{node_id:subtask_2_1, verifier:群master}
  → subtask_2_1 DONE → 依赖传播 → ... → 全子任务 accepted → 任务 DONE
```
#### 7. 执行机制(回路 5 要素 + 结果落 State)
> 解答"拆解怎么成节点 / 啥时触发执行 / 执行 vs 数据依赖 / 上游结果怎么传 / 执行验收结果怎么落 State"。**以 case 推演串连的合理性为准则** —— 现有 `graph_state_ops._fold_state`/`open_reroute_search`/旧 NodeType 是 v1 实现,错的由本模型覆盖,不拿代码反推模型。
**5.1 拆解物化(`plan.reported → 节点 + 边`)**
`on_event(plan.reported{parent_node:P, subtasks:TaskSpec[]})` → `SubtaskExecutor.handle_plan`:
- 逐个 `add_node(task_id, sub, SUBTASK)`:`Node.node_spec` 内嵌 `TaskSpec`;建 `NodeState` 分区;`sub.depth → NodeState.depth`
- 逐个 `sub.depend_on` → `add_edge(dep, sub.node_id, DEPENDENCY)`
- 递归 children(P=SUBTASK,decomposing phase):同款 `add_node` 子 + `add_edge(P→child, DEPENDENCY)`(spawn 边)
- `P`(根 DECOMPOSE)规划动作完成 → `P→DONE`
- graph **append-only**:plan-skill 出结构增量,on_event 物化成 Node+Edge+NodeState。
**5.2 驱动触发(reactor pump,reactive 不 poll)**
每个回投 event 进 `on_event`:① fold → NodeState ② 派发 phase handler ③ phase 转移 ④(终态)触发下游:
- **`node.accepted`(DONE)→ 依赖传播**:对每个 downstream `Y` where `Y.depend_on ∋ 本节点`,检查 `Y.depend_on` 全 DONE(聚合父另需 children 全 DONE,见 5.3 spawn)→ 标 drivable → drive(入口 phase=searching → dispatch 搜推)。
- **`state.updated` → 只 fold 数据,不 drive**(mid-execution 产出,非完成)。
- **`plan.reported` 新节点 → 立即 drive** 任何 depend_on 已满足的新节点(根子任务)。
- **`node.rejected` → `handle_reject` → reroute**(不 drive 下游,走补缺口)。
- `tick` = watchdog only。
**5.3 数据依赖(两套语义 + 任务级读,都是数据载体)**
- **`depend_on`(plan-skill 声明的兄弟依赖)**:既 gating(Y 等 dep DONE)又 lineage(Y 上下文 = fold dep 产出)。例:3_1 depend_on 5 叶。
- **`spawn`(递归 decompose 父→子)**:parent SUBTASK `spawning` 计 children 全 DONE(gating 走 phase 计数,非 depend_on 数组)→ `aggregating` fold children 产出。例:2_5 → 2_5_1/2_5_2。
- **任务级读(终验 3_2 等)**:`depend_on=[3_1]` 只作 gating;验收需**全图** → 读全 `TaskState.subtasks` 全分区(不走 depend_on lineage)。
**5.4 上下文流转(`retrieve_state` upstream fold → dispatch prompt)**
> ★现 `retrieve_state(scope)` 只返回 `{public, subtask:本节点}`,**不含上游** —— v1 缺口,M1 扩。
扩展 `retrieve_state(Y, with_upstream=True)` =
```
{ public:   TaskState.public,
  subtask:  NodeState(Y) 自身,
  upstream: fold(D.NodeState for D in Y.depend_on)
          = D.execution_context[MERGE] + D.intermediate_results[APPEND 去重] + D.artifacts[APPEND 按 name 去重] }
```
构造 dispatch:`{task_id, parent_node:Y.node_id, prompt:"执行: Y.spec\n上游产出: <upstream>\n任务上下文: <public>"}`
- 执行实体执行 → 回投 `state.updated{scope:Y, MERGE/APPEND}` → fold 进 `NodeState(Y)` → Y 产出累积,供下游同机制取
- 数据流沿 depend_on/spawn 边;**SSOT = `TaskState.subtasks`**(每节点分区),dispatch prompt = SSOT 瞬时投影
- 终验读全图:dispatch prompt 序列化 `TaskState.subtasks` 全 entries + public
**5.5 结果落 State(两维度 fold 同进同退)**
图两维度 by node_id:**Node**(`status`/`attempted_executors`)+ **NodeState**(`status`/`phase`/数据字段)。fold 必须两维度一致:
| 回投 event | 类型 | fold 落点 | status/phase | 触发 |
|---|---|---|---|---|
| `state.updated{scope:Y, semantics, patch}` | 执行结果(**流式,可多次**,非终态) | `NodeState(Y).execution_result`:MERGE→`execution_context` 子键;APPEND→`intermediate_results`(去重)/`artifacts`(按 name 去重)子键(v1 分键,见 §2.0 模型;canonical 单字段 `execution_result: dict`) | **不变**(仍 executing;Node RUNNING) | **不 drive**(只累积) |
| `node.accepted{Y, verifier, acceptances_met}` | 验收 PASS(**终态 1 次**) | `NodeState(Y).acceptance_result = {verdict=PASS, acceptances_met, verifier}`(canonical 独立字段,可被下游/终验读) | 两维度 → **DONE**;phase→done | **依赖传播 drive 下游** |
| `node.rejected{Y, verifier, gaps}` | 验收 FAIL(**终态 1 次**) | `gap_records` APPEND `GapRecord{node_id, round, unmet_criteria, verdict=FAIL}` | 两维度 → **FAILED** | **`handle_reject` → reroute**(不 drive 下游) |
> **收口契约**:fold 同时维护两维度;`accepted`/`rejected` 是**唯一** status 终态翻转点 + 唯一 downstream drive/reroute 触发点。现状 status 翻转散在 `_advance_node`/`_tick` → M1 收口于此。
**完整执行回路(子任务 Y,deps 已满足)**:
```
①[5.2 触发]   上游 node.accepted 依赖传播 OR plan.reported 新节点 → 泵 drive Y
②[5.4 上下文] retrieve_state(Y) = public + Y自身 + upstream fold(deps) → 拼 dispatch prompt
③[5.1 物化]   (新节点已在 plan.reported 时 add_node+add_edge 落图;此处直 drive)
④ 搜推        dispatch→owner discover-skill→discover.reported{Y:route}→Y phase searching→executing+填 assignee→dispatch 执行给执行实体
⑤[5.5 执行]   执行实体流式回投 state.updated{scope:Y} → fold 数据;status 仍 executing
⑥[5.5 验收]   执行实体自调 goal-verify →
              PASS: node.accepted → 两维度 DONE → 回①drive 下游
              FAIL: node.rejected → 两维度 FAILED + gap_records → handle_reject → reroute
⑦[5.3 数据流] 下游的②upstream fold 取到 Y 累积产出 → 回①
```
递归 miss 分支(④ miss+d<MAX):Y `searching→decomposing`→dispatch 拆解→`plan.reported{parent_node:Y}`→`add_node` children + `add_edge(Y→child)`→Y `spawning`→children 各自①-⑥→children 全 DONE→Y `aggregating`→fold children 产出进 `NodeState(Y)`(APPEND)→Y 自调 goal-verify 验父 acceptance→`node.accepted`→Y DONE→下游。
终验 3_2(任务级读,③):`retrieve_state(3_2)` = public + **全 `TaskState.subtasks`** → dispatch 带全图 → 终验 bot goal-verify 验任务级全 AC → `node.accepted`(全 DONE→任务 DONE)/`node.rejected`(reroute 补缺口)。
#### 8. Scheduler↔Executor 路由 + 异步边界 + 回投续驱(整体执行架构)
> 解答"Scheduler 只认 NodeType,怎么驱动 node 执行 / 怎么路由到具体 executor / bot 执行完怎么触发继续"。
**分层边界**
- **Scheduler(泵,薄,只认 NodeType)**:`on_event` + `drive` 两个入口,按 node 的 `NodeType` 查 `ExecutorRegistry` 路由;phase 逻辑不外露给 Scheduler。
- **ExecutorRegistry**:`NodeType → Executor`(RECOGNITION/CLARIFY→API 驱动不入泵;DECOMPOSE→DecomposeExecutor;SUBTASK→SubtaskExecutor)。
- **Executor(phase 状态机 + handler)**:`enter(node)`(drive 入口,落入口 phase)+ `handle(event,node)`(内部按 `(event.kind, phase)` 细路由到 phase handler);phase 封装在 Executor 内。
**两个异步边界(唯一进出 bot 的通道)**
- **OUT(dispatch,fire-and-forget)**:`Executor.dispatch → ExecutionPort → BCN chat.send → bot openclaw 进程`;Scheduler **不阻塞等**。
- **IN(回投)**:bot 的 skill 执行完(含自调 goal-verify)→ skill 内 `TaskApiClient` → **`POST /api/tasks/{id}/events {kind, payload:{node_id,...}}`** → backend `TaskService.on_event`。
- **续驱**:`on_event` 是唯一泵入口,reactive(不 poll、不 wait);每来一个 `POST /events` 推进一步(fold + 按 NodeType 路由 + phase 转移 + 可能 dispatch/propagate)。bot 是异步"喂数据进泵"的 worker,**不是 driver**。
**skill → 回投 event 映射**(每个 bot 干完后 POST 什么)
| skill(宿主 bot) | 干完后 | 回投 |
|---|---|---|
| task-recognition-skill(owner) | 抽 TaskSpec | `POST /tasks` create_task(非 events) |
| task-clarify-skill(owner) | 补/锁 spec | `POST /tasks/{id}/clarify`(非 events) |
| task-plan-skill(owner) | 拆解/补拆 TaskSpec[] | `POST /tasks/{id}/events` **plan.reported** |
| 搜推能力(owner 内置 discover) | recommendation | `POST /tasks/{id}/events` **discover.reported** |
| task-exec-skill(执行 bot/群 master) | 流式产出 | `POST /tasks/{id}/events` **state.updated**(多次) |
| task-exec-skill + goal-verify(同上) | 自验 | `POST /tasks/{id}/events` **node.accepted / node.rejected** |
> 执行 bot/群 master/汇总 bot/终验 bot/BBS bot 都装 task-exec-skill+goal-verify;`node.accepted` 才触发下游,`state.updated` 只 fold 不触发(§7 5.5)。
**Scheduler 层伪代码**
```python
def on_event(event):
    node = locate_node(event.node_id or event.parent_node)
    if event.kind == STATE_UPDATED: fold_state(...); return          # 只 fold,不路由执行 [5.5]
    ExecutorRegistry[node.node_type].handle(event, node)            # 粗路由按 NodeType
    if event.kind == NODE_ACCEPTED: propagate(node)                 # 依赖传播 → drive 就绪下游 [5.2]
def drive(node_id):
    node = locate_node(node_id)
    ExecutorRegistry[node.node_type].enter(node)                    # 入口 phase(SUBTASK→searching)
def propagate(done_node):
    for Y in dependents(done_node):                                 # depend_on ∋ done_node 或 spawn 父
        if deps_met(Y) and state(Y).status != DONE: drive(Y.node_id)
```
**SubtaskExecutor 内部**(细路由 `(event.kind, phase)` + phase 状态机)
```python
def enter(node): st.phase="searching"; ctx=retrieve_state(node, with_upstream=True); dispatch(搜推)
def handle(event, node):
    match (event.kind, st.phase):
      (DISCOVER_REPORTED,"searching") -> handle_search   # hit→executing / miss+d<MAX→decomposing / miss+d≥MAX→HUNG
      (PLAN_REPORTED,"decomposing")    -> handle_plan     # spawning children SUBTASK(depth+1)
      (STATE_UPDATED,"executing")      -> fold_data        # 数据累积,不翻态,不 drive
      (NODE_ACCEPTED,"executing")      -> handle_accept    # 两维度 DONE(下游由 Scheduler.propagate)
      (NODE_REJECTED,"executing")      -> handle_reject    # 两维度 FAILED + reroute_dec
```
**一次完整路由链(2_1 accept → 后续 drive 3_1)**
```
群 master POST /events node.accepted{node_id:subtask_2_1_market}
 → Scheduler.on_event: locate 2_1(SUBTASK) → ExecutorRegistry[SUBTASK].handle → match(ACCEPTED,executing)→handle_accept
   → 两维度 DONE;on_event 后段 propagate(2_1): dependents=[3_1,...];deps_met(3_1)? 首轮还缺 2_2/2_4/2_5 → 不 drive
 → (2_2/2_4/2_5 各 accept 各 propagate;全 DONE 后)drive(3_1) → ExecutorRegistry[SUBTASK].enter(3_1) → searching → dispatch 搜推
```
**整链一句话**:`drive→dispatch→[async bot]→POST /events→on_event→(fold+按 NodeType 路由+phase 进+dispatch/propagate)→dispatch→...` 一路反应,直到终验 `node.accepted` 全图 DONE;中间无阻塞等待,只有"等下一个 `POST /events`"。
#### 9. 现有代码差距(进 §4 M1)
- `_tick` 当主驱动 + `_advance_node` `if/elif node_type` → 重构:scheduler 保留 pump 骨架(按 `(node_id,event.kind,phase)` 派发到 executor);`_tick` 降 watchdog。
- `_bot_search`/`_decomposition`/`_dispatch` 把动作当节点建子(BOT_SEARCH→DISPATCH child)→ 改:子任务=单节点(无 `_disp`/`_dec` 子节点),搜推/执行/验收/拆解/上升都是 SUBTASK 执行器内 phase。
- `_bot_search`/`_decomposition` 调 in-process `BotDiscoverPort`/`DecomposerPort`(同步)→ 改 async `dispatch` 给 owner,等 `discover.reported`/`plan.reported` 回投(singlebox 保留 in-process 作 fallback profile)。
- `models.py` NodeType 改成 4 种(删 `BOT_SEARCH`/`DISPATCH`/`EXEC_ACCEPT`/`EXEC_AGGREGATE`/`DECOMPOSITION`/`GOAL_VERIFY`/`EXECUTE_START` 作图节点);`Node` 内嵌 `plan: TaskSpec`;`NodeState` 增 `phase`。
- 新建 `SubtaskExecutor`(统一,按 event.kind+phase 派发 phase handler,委托策略对象处理 single/group/bbs 执行 + goal-verify 验收)+ `PhaseExecutor`(recognition/clarify/decompose);registry 按 NodeType。
- 终验 = SUBTASK:plan-skill 拆解须含终验子任务(通常最后);缺则调优 plan-skill 或加固定终验节点(规划层修,执行层不关心)。
- BBS 任务级:graph `RUNNING→HUMAN_REQUIRED`(有 HUNG 且全可执行 DONE)→ 用户确认 → `BBS_ACTIVE` → HUNG 叶子 `escalate_to_bbs` 广场悬赏。对上现成 `GraphStatus` 状态机。
- **`retrieve_state` 扩 upstream fold**(§2.0.7 5.4):现只返 `{public, subtask}`,加 `upstream = fold(depend_on 的 NodeState)`;dispatch prompt 由 SSOT 投影拼装(含上游产出)。
- **fold 收口两维度 + accepted/rejected 唯一终态翻转/downstream 触发点**(§2.0.7 5.5):现 status 翻转散在 `_advance_node`/`_tick`/`_fold_state`(只 fold 数据不触达 status)→ 收口到 `on_event` 的 `handle_accept`/`handle_reject`,两维度同进同退,`state.updated` 只 fold 数据不翻态不 drive。
- **数据依赖显式化**:`depend_on`(gating+lineage)+ spawn(parent→children,`spawning` phase 计数)+ 任务级读(终验读全 `TaskState.subtasks`)三套语义进 `SubtaskExecutor`,替代现 `_unlocked` 纯前驱检查。
- **ExecutorRegistry 新建**(`NodeType → Executor`,§2.0.8):`DecomposeExecutor`(DECOMPOSE,规划节点)+ `SubtaskExecutor`(SUBTASK,统一)+ `RecognitionExecutor`/`ClarifyExecutor`(API 驱动);Scheduler `on_event`/`drive` 按 NodeType 查表路由,删除 `_advance_node` 的 `if/elif node_type`。
- **`on_event` 重构为泵**(§2.0.8):`fold → 按 NodeType 路由 Executor.handle → NODE_ACCEPTED 后 propagate`;`state.updated` 短路(只 fold 不路由不 drive)。propagate/drive/deps_met 依赖传播机制新建(替换 `_tick` 主驱动的推进逻辑)。
- **`ExecutionPort` 统一 dispatch 封装**(§2.0.8 OUT 边界):`dispatch(route, target, msg, *, group=None)` 三分支(`single_bot`/`group` 动态建群/`unmatched`)→ BCN `chat.send`;singlebox 真发,参考 ecb http_client 但 backend 内重写不依赖 ecb。
- **bot skill `TaskApiClient`(§2.0.8 IN 边界)**:owner-bot 的 plan/discover skill、各执行实体的 task-exec-skill(+goal-verify)都内建 HTTP client 调 `POST /api/tasks/{id}/events` 回投;现状 owner-bot 有雏形,执行 bot 需补 task-exec-skill 的回投 client + goal-verify skill 装机。
- **`BbsExecutor.claim` 真实化**(§2.0.7 BBS):`escalate_to_bbs` 调广场认领 + 悬赏 message 经 BCN 发 BBS bot pool;`TaskDriverPort` 现 `Noop`→实现真发。
---
### Step 0 — 测试基础设施:创建 bot / 协作群 / 装入 skill / 建消息通道
#### 0.1 起 singlebox 全栈
- **命令**:`./scripts/singlebox.sh start all`
- **效果**:baas → backend(:8888)→ bcs(:21000)→ bcsfuse → 5 local openclaw bots → demo bot → frontend。`START_ORDER` 见 `scripts/modules/all.sh`。
#### 0.2 创建 owner-bot(owner)+ 若干执行 bot(经真实 `POST /api/bots`)
> 参考 `scripts/modules/demo_bot.sh`;批量化为一个 profile 脚本。
**API**:`POST http://127.0.0.1:8888/api/bots?user_id={entity_id}`  (header `x-user-id: {entity_id}`)
**入参**:
```json
{
  "bot_name": "owner-task-orchestrator",
  "bot_desc": "存储行业尽调 owner-bot,负责任务识别/澄清/规划/验收",
  "entity_id": "mock-user",
  "entity_type": "staff",
  "engine_type": "openclaw",
  "bot_type": "personal",
  "template_type": "normalCC"
}
```
**出参**:`{"success":true,"data":{"bot_id":"default"}}`  → 记 `owner_bot_id`。
**落点**:backend 建档 + 异步拉起 openclaw 引擎;轮询 `GET /api/bots/{bot_id}/status?owner_id=mock-user` 直到 `is_ready=true`。
执行 bot 逐个同款创建(bot_name 列表见 §4.1 的 catalog:`bot_industry_fetch` / `bot_market_demand` / `bot_capital_trend` / `bot_storage_arch` / `bot_ssd_perf` / `bot_semi_process` / `bot_ai_train_engineer` / `bot_procurement_staff`)。
#### 0.3 BCS 连网 + onboard
- **API**:`POST http://127.0.0.1:21000/bots/connect`  body `{"bot_id":"default:mock-user","protocol_version":2}`
- **API**:`POST http://127.0.0.1:21000/admin/bots/onboard`  body `{"bot_id":"default:mock-user","name":"owner-task-orchestrator","summary":"...","hidden":true}`
- **校验**:`GET http://127.0.0.1:21000/bots/{bcs_bot_id}`(header `X-Mock-User-Id: 001`)→ `.bot_uuid==bcs_bot_id` 且 `capabilities.name/summary` 一致。
#### 0.4 装入 skill 到指定 bot(openclaw 配置,无 HTTP)
机制:在 bot workspace 的 `openclaw.json` 注册 skill 路径 + `plugin-skills/` 放 `SKILL.md`。
```json
"skills": {
  "entries": { "task-recognition": {"enabled": true}, "task-clarify": {"enabled": true},
               "task-plan": {"enabled": true}, "task-execute": {"enabled": true},
               "goal-verify": {"enabled": true} },
  "load": { "allowSymlinkTargets": ["/abs/path/to/integration-test-skills"] }
}
```
**落点**:引擎启动时 discovery→加载;无 per-skill install API(`OpenClawSkillsAdapter` 明确不支持install/uninstall,只能走目录/配置)。
#### 0.5 建立"给 bot/群发消息"通道(测试驱动用)
- **约束(已确认)**:任务相关代码全部在 **backend** 模块,**不能依赖 ecb 模块**的代码。`ecb/http_client.py` 只作为**参考实现**——**在 backend 内重写一份**消息发送客户端,不 import ecb。
- **新建** `TaskE2EClient`(放 `src/backend/tests/community/_flows/task/clients/` 或 `src/backend/src/agentclaw/community/.../task/e2e_client.py`),参考 ecb `http_client.py` 的封装思路 + 复用 backend 自有 `plugin_api/http_client.py` 的 `HttpClient` Plugin 模式,提供:
  - `send_to_bot(bcs_bot_id, query, context) -> reply` — 走 engine chat WS `_stream_chat_events` 建会话发消息;
  - `send_to_group(group_id, query, context)` — 走 BCS 群会话;
  - `await_bot_event(task_id, seq, timeout)` — 轮询 `GET /api/tasks/{id}/history?after_seq=N` 等事件。
- **系统→bot 派发**(运行期内):由 `ExecutionPort.dispatch_single_bot` 在 singlebox profile 下**真发 BCN `chat.send`** 给目标 bot(见 §4.3)。该派发同样在 backend 内实现,参考 ecb 但不依赖。
---
### Step 1 — 需求识别
> 前置契约(本用例要改,见 §4):`CreateTaskRequest` 入参对齐 `TaskSpec`(吃 `title/background/goal/deliverables/constraints`;`TaskSpecMetadata` 现仅 `id/title`——**`summary`/`tags` 已删除**),与 `clarify` 同走 `_apply_spec_patch`;**方案 B(延伸)**:`n_recognition` 在 create_task 时落图、`n_clarify` 在 `clarify_task(confirmed=true)` 时落图(均 DONE),execute_task 只落 `n_execute_start` + 根 `n_root(DECOMPOSITION)`(**删 `init_execution_graph` 批量建图**,见 Step 3)。recognition skill 产出的是**部分 TaskSpec**——query 能抽全的抽全,不编造。
#### 流程总览
```
测试驱动 ──send_to_bot(query)──► owner-bot
                                   │ owner-bot skill 路由
                     ┌─────────────▼──────────────┐
                     │ 1.2 关键词触发判定          │──miss──► 普通对话(不建 task)
                     └─────────────┬──────────────┘
                     ┌─────────────▼──────────────┐
                     │ 1.3 LLM 双信号判定          │──false──► 不建 task,反问引导
                     │    (执行性 + 可验收性)       │
                     └─────────────┬──────────────┘
                     ┌─────────────▼──────────────┐
                     │ 1.4 结构化抽取 + kind 分类   │
                     │    query → raw partial      │
                     │    TaskSpec(7 acceptance)   │
                     └─────────────┬──────────────┘
                                   │  (TaskSpec 即 create_task 入参,无投影)
                     ┌─────────────▼──────────────┐
                     │ 1.5 POST /api/tasks/create  │
                     │    建档 + fold spec +        │
                     │    落 n_recognition DONE +   │
                     │    TASK_CREATED seq=1 + 副屏│
                     └─────────────┬──────────────┘
                                   │  task_id=T1
                     ┌─────────────▼──────────────┐
                     │ 1.6 GET /api/tasks/T1 读回  │ (断言 spec+节点)
                     └────────────────────────────┘
```
#### 操作明细
| # | 操作 | 调用者 | 入参 | 出参 | 落点 |
|---|---|---|---|---|---|
| **1.1** | `send_to_bot(owner_bot, query)`(engine chat WS,经 §0.5 `TaskE2EClient`) | 测试驱动 | query = 完整 case 文本(任务描述 + 任务目标 + 7 条验收标准,见下) | owner-bot openclaw agent 收到 chat 帧 | 进入 skill 路由 |
| **1.2** | 关键词触发判定 | **owner-bot `task-recognition-skill`** | query 文本 | hit/miss | 命中"尽调/任务/解决/修复/总结/对齐…"→ 1.3;**未命中 → 普通对话,不建 task**(负例) |
| **1.3** | LLM 双信号判定(执行性 + 可验收性) | **owner-bot `task-recognition-skill`** | query 文本 | `{executability: true, verifiability: true}` | 双信号 true → 1.4;**false → 不建 task**,skill 反问引导(负例) |
| **1.4** | 结构化抽取 + acceptance kind 分类 | **owner-bot `task-recognition-skill`** | query + 双信号 verdict | raw partial `TaskSpec`(近完整,见下) | LLM 抽五要素;能抽全的抽全;不编造 query 没有的;`kind`(output/threshold/invariant)逐条分类 |
| **1.5** | `POST /api/tasks/create` | **owner-bot `task-recognition-skill`**(经 `TaskApiClient`) | `CreateTaskRequest`(新契约,见下) | `{"task_id":"task-8a3f2c1b9d0e4756","status":"drafting","seq":1}` | 见 create 内部时序;记 `T1` |
| **1.6** | `GET /api/tasks/T1`(读回断言) | **owner-bot `task-recognition-skill`** / 测试驱动 | — | `TaskView`(full spec + 图含 `n_recognition(DONE)`) | 确认 spec 已持久 + recognition 节点已落;skill 把 `T1` 记入 session context |
#### 1.1 入参:query(完整 case 文本)
```
任务描述:存储行业尽调:AI基础设施驱动下,企业级与数据中心存储行业的最新变化、竞争格局与进入机会。
任务目标:产出一份尽调报告。
验收标准:
  ① 明确存储行业当前是否具备中短期投资价值
  ② 明确最值得跟踪的细分赛道、公司类型和核心变量
  ③ 提供市场规模、竞争格局、技术演进、客户需求四大维度的系统分析
  ④ 收集一手行业实践落地经验
  ⑤ 至少形成 5 条核心投资判断
  ⑥ 每条投资判断需同时说明:支持证据 / 风险因素 / 需要进一步验证的问题
  ⑦ 至少有 30% 的关键判断来自最近 3 个月内的信息更新
```
#### 1.4 产出:raw partial `TaskSpec`(本 case 近完整)
```json
{
  "metadata": {"title": "存储行业尽调"},
  "context": {"background": "AI基础设施驱动下,企业级与数据中心存储行业的最新变化、竞争格局与进入机会", "constraints": []},
  "goal": {"objective": "产出一份尽调报告", "acceptances": [
    {"kind":"output",    "properties":{"dimension":"investment_value"}},
    {"kind":"output",    "properties":{"dimension":"tracking_targets"}},
    {"kind":"output",    "properties":{"dimensions":["market","competition","tech","customer"]}},
    {"kind":"output",    "properties":{"dimension":"first_hand_practice"}},
    {"kind":"threshold", "properties":{"min_count":5}},
    {"kind":"invariant", "properties":{"structure":["evidence","risk","open_question"]}},
    {"kind":"threshold", "properties":{"ratio":0.3, "recency_months":3}}
  ]}
}
```
> 验收④`first_hand_practice` 是本 case 比 briefing 多出的 1 条,对应 BBS p3 一线实践悬赏。
#### 1.5 入参:`CreateTaskRequest`(新契约,对齐 TaskSpec,无 1.4b 投影)
```json
{
  "title": "存储行业尽调",
  context: {""background": "AI基础设施驱动下,企业级与数据中心存储行业的最新变化、竞争格局与进入机会","constraints": []}
  "goal": {"objective":"产出一份尽调报告", "acceptances":[ ...同 1.4 的 7 条... ]}
}
```
#### 1.5 `create_task()` 内部时序(方案 B)
```
service.create_task(title, background, goal, deliverables, constraints, user_id)
  ├─ ① task_id = _new_task_id()  → "task-8a3f2c1b9d0e4756"
  ├─ ② Task(id, user_id, spec=TaskSpec(metadata={id,title}, context={background, constraints}, goal=goal, deliverables=deliverables))
  ├─ ④ _init_recognition_node(task)
  │      └─ graph.nodes += [Node(n_recognition, RECOGNITION, DONE,
  │                             properties={phase_label:"任务识别", task_title:title})]
  │         graph.state.subtasks[n_recognition] = NodeState(DONE)
  ├─ ⑤ _task_repo.save(task)
  ├─ ⑥ _emit(TASK_CREATED, spec=task.spec.to_payload())   → seq=1
  ├─ ⑦ panel_publisher.publish(PanelMessage("taskPanel.TaskWorkflowView", {task_id}))
  └─ return task
```
#### 1.6 出参:`GET /api/tasks/T1`
```json
{
  "task_id": "task-8a3f2c1b9d0e4756",
  "status": "drafting",
  "spec": { ...同 1.4 完整 TaskSpec... },
  "execution_graph": {
    "status": "drafting",
    "nodes": [
      {"node_id":"n_recognition","spec":"任务识别: 存储行业尽调","status":"done",
       "node_type":"recognition","properties":{"phase_label":"任务识别","task_title":"存储行业尽调","retry_count":0,"max_attempts":2,"loop_round":0}}
    ],
    "edges": [],
    "state": {"public":{}, "subtasks":{"n_recognition":{"node_id":"n_recognition","status":"done","depth":0}}}
  },
  "loop_round": 0
}
```
> 方案 B 前 `nodes:[]`;方案 B 后含 `n_recognition(DONE)`。`n_clarify` 在 Step2 `clarify_task(confirmed=true)` 时落图;`n_execute_start`/根 `n_bot_search` 在 Step3 `start` 时由 `init_execution_graph` 增量补齐(幂等:见 recognition/clarify 已在则不重建)。
#### 待确认(Step 1)
- **负例分支**:1.2 miss / 1.3 false 各覆盖 1 例(断言未调 create_task、无 task 产生)。
- **`TaskCreatedResponse` 不加 `execution_graph`**:保持轻量,recognition 节点靠 1.6 GET 确认。
- **`TASK_CREATED` payload 带初始 spec**(`spec=task.spec.to_payload()`):保证 history 重放能还原 recognition 完整抽取。
---
### Step 2 — 需求澄清
> 前置:Step1 的 create_task 已把 recognition 抽取的整份 TaskSpec(含 goal/7 acceptance/deliverables)落库,故 clarify 角色**从"从零补五要素"收缩为"判完备度 + 补缺 + 确认锁定"**。完备度判定归 **clarify-skill**(后续实现可能把所有 skill 整合成一个,但 v1 按职责拆)。近完备可零轮 `clarify_task(patch={}, confirmed=true)` 一次锁定,不强制多轮。**方案 B 延伸**:`clarify_task(confirmed=true)` 时落 `n_clarify`(DONE)节点 + edge `n_recognition→n_clarify`。
#### 流程总览
```
owner-bot session(T1 来自 Step1)──hand-off──► clarify-skill
   │
   │ 2.1 GET /api/tasks/T1 读回权威 spec
   ▼
┌──────────────────────────────────────────┐
│ 2.2 LLM 判字段完备度(五要素逐项 check)    │ clarify-skill
└──────────────────┬───────────────────────┘
            ┌──────┴──────┐
        完备/可确认        有缺项
            │              │
            │       ┌──────▼──────────────────────┐
            │       │ 2.3 POST /clarify(patch,      │
            │       │   confirmed=false) amend       │
            │       │   → TASK_CLARIFIED seq=n       │
            │       └──────┬──────────────────────────┘
            │       ┌──────▼──────────────────────────┐
            │       │ 2.4 human-in-loop 测试驱动回答  │
            │       │   (可能多轮 2.3↔2.4)            │
            │       └──────┬──────────────────────────┘
┌───────────▼───────▼───────────────────────────┐
│ 2.5 POST /clarify(patch, confirmed=true)       │
│   → DRAFTING→DEFINED + 落 n_clarify(DONE)      │
│   + edge n_recognition→n_clarify + TASK_CLARIFIED│
│   spec 冻结                                     │
└────────────────────────────────────────────────┘
```
#### 操作明细
| # | 操作 | 调用者 | 入参 | 出参 | 落点 |
|---|---|---|---|---|---|
| **2.1** | `GET /api/tasks/T1` | **owner-bot `task-clarify-skill`** | — | `TaskView`(Step1 落库的完整 spec) | 读权威 spec,不依赖 session 内存(防漂移) |
| **2.2** | LLM 判字段完备度 | **owner-bot `task-clarify-skill`** | 2.1 的 spec | `gap_report`:`{complete: bool, missing:[...], questions:[...]}` | 逐项 check 五要素。本 case:title✓/background✓/goal.objective✓/acceptances[7]✓/deliverables[1]✓/constraints 空(非必需)→ **近完备**。缺则生成澄清问题进 2.3 |
| **2.3** | `POST /api/tasks/T1/clarify`(补缺,`confirmed=false`,可选) | **owner-bot `task-clarify-skill`** | `ClarifyTaskRequest`:`{"patch":{<缺项字段>},"confirmed":false}` | `{"task_id":"T1","status":"drafting","spec":{...}}` | `_apply_spec_patch` fold 缺项;`TASK_CLARIFIED` seq=2;status 仍 DRAFTING。**本 case 近完备可跳过** |
| **2.4** | human-in-loop(测试驱动回答) | 测试驱动 ↔ clarify-skill | 澄清问题 → 用户答 | clarify-skill 收回答 | 缺则多轮 2.3↔2.4;本 case 可零轮 |
| **2.5** | `POST /api/tasks/T1/clarify`(锁定,`confirmed=true`) | **owner-bot `task-clarify-skill`**(用户确认后) | `{"patch":{},"confirmed":true}`(若 2.3 已补全则 patch 空;否则带最后补丁) | `{"task_id":"T1","status":"defined",...}` | `require_graph_transition(DRAFTING→DEFINED)`;spec 冻结;**方案B:`_init_clarify_node` 落 `n_clarify`(DONE) + edge `n_recognition→n_clarify`**;`TASK_CLARIFIED` seq=2(零轮)或 seq=3(有一轮 amend) |
#### 2.5 `clarify_task(confirmed=true)` 内部时序(方案 B 延伸)
```
service.clarify_task(task_id, patch, confirmed=True)
  ├─ ① task = _load(task_id); _apply_spec_patch(task, patch)   # 空 patch no-op
  ├─ ② guard: require_graph_transition(DRAFTING→DEFINED) → execution_graph.status=DEFINED(先迁态,再落节点)
  ├─ ③ _init_clarify_node(task)   ← 方案B 延伸新增
  │      └─ graph.nodes += [Node(n_clarify, CLARIFY, DONE,
  │                             properties={phase_label:"任务明确",
  │                               task_spec:{objective, background, constraints, deliverables, acceptances}})]
  │         graph.state.subtasks[n_clarify] = NodeState(DONE)
  │         graph.edges += [Edge(n_recognition → n_clarify, DEPENDENCY)]   # 接上 Step1 的 recognition 节点
  ├─ ④ _task_repo.save(task)
  ├─ ⑤ _emit(TASK_CLARIFIED, patch=patch, confirmed=true)   → seq=2|3
  └─ return task
```
#### 本 case 走法(近完备 → 零轮直接确认)
```json
// 2.5 入参
{"patch": {}, "confirmed": true}
// 2.5 出参(节选)
{"task_id":"task-8a3f2c1b9d0e4756","status":"defined","loop_round":0,
 "spec":{ ...Step1 完整 TaskSpec,现已冻结... },
 "execution_graph":{"status":"defined",
   "nodes":[
     {"node_id":"n_recognition","status":"done","node_type":"recognition",...},
     {"node_id":"n_clarify","status":"done","node_type":"clarify",
      "properties":{"phase_label":"任务明确","task_spec":{
        "objective":"产出一份尽调报告",
        "background":"AI基础设施驱动下,...",
        "constraints":[]
        ,
        "acceptances":[ 7 条 ]}}} ],
   "edges":[{"edge_id":"e-n_recognition-n_clarify","from_node":"n_recognition","to_node":"n_clarify","kind":"dependency"}],
   "state":{"public":{},"subtasks":{"n_recognition":{...done...},"n_clarify":{...done...}}}}}
```
`TASK_CLARIFIED` seq=2(零轮)→ DEFINED。spec 冻结,后续 execute_task 才能 `DEFINED→RUNNING`。
#### 待确认(Step 2)
- **完备度判定归 clarify-skill**(已定;后续可能整合成单 skill)。
- **方案 B 延伸到 clarify**:`n_clarify` 在 `clarify_task(confirmed)` 落图(已定);`init_execution_graph` 在 start 只补 execute_start + 根,幂等跳过 recognition/clarify。
- **`clarify_task(patch={}, confirmed=true)` 一次锁定**允许(已定)。
---
### Step 3 — 需求执行(execute_task,拆解先行)
> **落图原则(贯穿全链):节点在"对应动作发生时"落图,不批量预建。**
> recognition@create_task、clarify@clarify_task(confirm)、execute_start@execute_task、decomposition@execute_task(dispatch 拆解)。每一步只落自己这一步发生的动作节点。
>
> **现有实现要改(M1):** 旧 `init_execution_graph` 在 execute_task 批量补 recognition/clarify/execute_start/root,是 create_task/clarify_task 旧不落图时的补救产物;方案 B 下 create_task/clarify_task 已落图,**`init_execution_graph` 的批量建图要删/拆解**,execute_task 只落 `n_execute_start` + `n_root`。
>
> 顺序:**拆解先行,再搜推**(不采用 briefing"搜推先行,miss 才分解")。execute_task 直接 dispatch "拆解" 给 owner-bot;整体搜推挪到 Step 3.2 子任务级。
>
> 驱动模型:**execute_task + 每次回投的 `on_event` 泵**是主驱动(回投触发同步推进图中 deps-met 的 PENDING 节点);`tick` 仅是超时 watchdog 兜底,非常规驱动。
#### 起点(context before,来自 Step2 终态)
```
T1: status=DEFINED, spec 冻结(goal.objective + 7 acceptances + deliverables)
execution_graph.nodes = [n_recognition(DONE), n_clarify(DONE)]   ← Step1/2 落
edges = [n_recognition→n_clarify]
owner-bot session.current_task_id = "T1"                          ← Step1 create_task 记
```
#### 流程总览
```
测试驱动 ──"确认开始执行"──► owner-bot task-execute-skill
   │ 3.1 POST /api/tasks/T1/transition(入参 action=start;task_id ← session)
   ▼
┌──────────────────────────────────────────────┐
│ 3.2 execute_task 内部(读→翻态→落本步节点→发拆解):    │
│   ① load task(DEFINED)                        │
│   ② guard DEFINED→RUNNING                     │
│   ③ 落 n_execute_start(DONE)+edge n_clarify→它 │  ← 本步动作"用户确认执行"
│   ④ 落 n_root(DECOMPOSITION,PENDING)+edge→它  │  ← 本步动作"发起拆解";node_id="n_root"此刻分配
│   ⑤ retrieve_state(n_root)=spec+public → prompt│
│   ⑥ dispatch(route="unmatched",target=owner-bot,{task_id,parent_node=n_root,prompt="拆解:..."})→BCN chat.send │
│   ⑦ save+return                               │
└──────────────────┬───────────────────────────┘
                    │ ★异步边界(n_root 等 owner-bot 回投),execute_task 返回
   ◄───────────────── {status:running}
        ↓ (后续 Step 3.1)
   owner-bot task-plan-skill 回投 TaskSpec[] → on_event 落 children + 翻 n_root DONE
```
#### 操作明细 + I/O 出处
| # | 操作 | 调用者 | 入参(出处) | 出参(去向) | 落点 |
|---|---|---|---|---|---|
| **3.0** | 测试驱动确认执行 | 测试驱动 → owner-bot | "确认开始执行" | task-execute-skill 触发 | owner-bot 调 execute_task |
| **3.1** | `POST /api/tasks/T1/transition`(body `{action:"start"}`) | **owner-bot `task-execute`** | `task_id="T1"` ← session.current_task_id | `{"task_id":"T1","status":"running",...}`(见 3.3) | 触发 `execute_task`(≡ `transition(START)`,`TaskScheduler.start` 内部 bootstrap) |
| **3.2** | execute_task 内部(见下时序) | 系统 `TaskScheduler.start` | task T1(DEFINED,spec 冻结)← load | graph + [n_execute_start✓, n_root(PENDING)];拆解消息发 owner-bot | guard 翻态;落 2 节点 + 2 边;dispatch 拆解 |
#### 3.2 `execute_task()` 内部时序(无 init_execution_graph,每节点带出处)
```
TaskScheduler.start(task_id="T1")
  ├─ ① task = _load("T1")                       # 入参 task_id ← transition URL
  任务执行Node(
  ├─ ② guard: require_graph_transition(DEFINED→RUNNING) → execution_graph.status=RUNNING
  ├─ ③ 落 n_execute_start(DONE) + edge n_clarify→n_execute_start
  │      ← 本步动作"用户确认执行";_emit(NODE_ADDED)
  ├─ ④ 落 n_root(DECOMPOSITION, PENDING, spec=task.spec.goal.objective) + edge n_execute_start→n_root
  │      ← 本步动作"发起拆解";node_id="n_root" 此刻分配;spec ← task.spec.goal.objective(Step1 抽取)
  │      _emit(NODE_ADDED)
  ├─ ⑤ retrieve_state(n_root): 读 task.spec + state.public(空) → prompt="拆解以下需求:taskspec+state"
  ├─ ⑥ dispatch(route="unmatched", target=owner-bot, message={task_id:"T1", parent_node:"n_root", prompt:"拆解:..."}) → BCN chat.send
  │      ← task_id←①;parent_node←④ 刚分配;prompt←⑤
  │      _emit(PLAN_REQUESTED)
  ├─ ⑦ _task_repo.save(task)
  └─ return task   # status=running; n_root 仍 PENDING(等 Step3.1 回投)
```
#### 3.3 出参(context after)
```json
{
  "task_id": "task-8a3f2c1b9d0e4756",
  "status": "running",            // ② 翻态(task.status @property → execution_graph.status)
  "loop_round": 0,
  "spec": { ...冻结的 TaskSpec... },
  "execution_graph": {
    "status": "running",
    "nodes": [
      {"node_id":"n_recognition","status":"done","node_type":"recognition",...},   // Step1
      {"node_id":"n_clarify","status":"done","node_type":"clarify",...},           // Step2
      {"node_id":"n_execute_start","status":"done","node_type":"execute_start",...}, // ③
      {"node_id":"n_root","status":"pending","node_type":"decomposition",
       "spec":"产出存储行业尽调决策支持报告",...}                                    // ④ spec←goal.objective
    ],
    "edges": [
      {"edge_id":"e-n_recognition-n_clarify","from_node":"n_recognition","to_node":"n_clarify","kind":"dependency"},
      {"edge_id":"e-n_clarify-n_execute_start","from_node":"n_clarify","to_node":"n_execute_start","kind":"dependency"},
      {"edge_id":"e-n_execute_start-n_root","from_node":"n_execute_start","to_node":"n_root","kind":"dependency"}
    ],
    "state": {"public":{}, "subtasks":{
      "n_recognition":{...done...},"n_clarify":{...done...},"n_execute_start":{...done...},
      "n_root":{...pending...}}}
  }
}
```
#### I/O 衔接自检(每个字段追源,无中生有检查)
| 字段 | 出处 | 可追 |
|---|---|---|
| `task_id="T1"` | owner-bot session(Step1 create_task 记) | ✓ |
| `n_recognition`/`n_clarify` | Step1/Step2 落图 | ✓ |
| status DEFINED→RUNNING | Step2 锁 DEFINED → 本步 guard | ✓ |
| `n_execute_start` | 本步动作(用户确认执行) | ✓ |
| `n_root` node_id + spec | 本步分配 / task.spec.goal.objective | ✓ |
| dispatch `parent_node="n_root"` | ④ 刚分配的 node_id | ✓ |
| dispatch `prompt` | ⑤ retrieve_state 读 task.spec+state | ✓ |
#### 现有实现要改(Step 3 相关,进 M1)
1. **删 `init_execution_graph` 批量建图**:execute_task 不再补 recognition/clarify(已落),只落 `n_execute_start` + `n_root`。
2. **dispatch 消息 schema 显式带 `parent_node`**:当前 BCN `chat.send` 是否只传 text 需核实(`inbound-handler.ts:882`),要扩成结构化携带 `{task_id, parent_node, prompt}`。
#### search/dispatch 维度复用(贯穿 Step 3.x)
> 统一派发入口 `dispatch(route, target, prompt, *, group=None)`,三分支靠 `route` 区分:`single_bot`(target=bot_id,执行/拆解 prompt)、`group`(target=已有 group_id 或 None+`group=GroupSpec(topic,participants,driver,collab_mode)` 动态建群,执行 prompt)、`unmatched`(target=owner_bot_id,拆解 prompt)。Step 3 的 `dispatch(route="unmatched", target=owner-bot, 拆解prompt)` 是**第一次** dispatch;Step 3.2 的 dispatch `route=single_bot|group` 目标=执行 bot/群,prompt=执行。统一经 `ExecutionPort` → BCN `chat.send`(singlebox profile 真发;参考 ecb http_client 但 backend 内重写,不依赖 ecb)。
---
### Step 3.1 — 任务拆解(owner-bot plan-skill 回投 TaskSpec[])
> owner-bot 收到 Step 3 的"拆解"消息 → `task-plan-skill`(关键词触发"任务拆解/任务规划")LLM 产出拆解方案 → 回投。本节给出**存储行业尽调 case 的真实拆解实例**(总-分-总 3 阶段 8 子任务),作为集成用例的回投数据 + Step 3.2 搜推路由的接线依据。
#### 操作
| # | 操作 | 调用者 | 入参 | 出参 | 落点 |
|---|---|---|---|---|---|
| **3.1.1** | owner-bot 收到拆解消息 → LLM 拆解 | 系统 → **owner-bot `task-plan-skill`** | prompt="拆解以下需求: taskspec + 执行上下文state" | TaskSpec[8](见下,首轮 7 个——省略 tech) | 按总-分-总结构产出 |
| **3.1.2** | `POST /api/tasks/T1/events`(回投拆解方案) | **owner-bot `task-plan-skill`** | `EventReportRequest`:`{"kind":"<plan.reported>", "payload":{"parent_node":"n_root", "subtasks":[ 8 个 TaskSpec ]}}` | `{"task_id":"T1","accepted":true,"seq":4,"note":""}` | `on_event` → `add_node` 落 8 个 children BOT_SEARCH(按 depend_on 建 DEPENDENCY/PARALLEL_SYNC 边)+ `n_root→DONE` |
| **3.1.3** | (可选)`GET /api/tasks/T1` 读回图断言 | 测试驱动 | — | `TaskView`(图含 n_root✓ + 8 children) | 确认拓扑落图 |
> ⚠️ **契约待确认**:owner-bot 回投 `TaskSpec[]` 的事件 kind。`EventKind` 枚举无显式 `plan.reported`/`decomposition.reported`。需读 `task_service.on_event` 的 plan 解析分支定 kind(候选:新增 `PLAN_REPORTED` kind,或复用 `state.updated`+约定 patch key `subtasks`)。**M1 首要敲定点。** 下文暂用 `"plan.reported"` 占位。
#### on_event 入参推演(I/O 衔接,无中生有检查)
```
Step3 execute_task 发 dispatch 消息给 owner-bot,消息含 {task_id, parent_node:"n_root", prompt}
   ↓ BCN chat.send 透传
owner-bot task-plan-skill 收到(含 parent_node="n_root")
   ↓ LLM 拆解 → TaskSpec[](node_id/depth/depend_on/spec 全 skill 产出)
owner-bot POST /api/tasks/T1/events:
  body = {kind:"plan.reported", payload:{parent_node:"n_root", subtasks:[7首次]}}
   • parent_node ← 收到的 dispatch 消息原样回带(源头=Step3 ④分配的 n_root)
   • subtasks    ← skill LLM 产出
   ↓
router 构造 envelope = {task_id:"T1", kind:"plan.reported", payload:{...}}
   ↓
service.on_event(envelope):  # task_service.py:204
  _unpack → kind=plan.reported, payload
  append TaskEvent(seq=4)
  _apply_event → _apply_plan_reported(task, payload):
    • payload.parent_node="n_root" → 翻 n_root DONE(+NodeState DONE)   ← 靠回带的 parent_node 定位父节点
    • payload.subtasks → 逐个 add_node:
        Node(node_id=s.node_id, BOT_SEARCH, PENDING, spec=s.spec.goal.objective,
             properties={phase_label:s.spec.metadata.title, subtask_spec:s.spec})
        NodeState(node_id, PENDING, depth=s.depth)
    • 建边:parent 边 n_root→s.node_id(DEPENDENCY);依赖边 对 s.depend_on 每个 dep → dep→s.node_id
      (多 dep = 多入边 = PARALLEL_SYNC:等所有 dep DONE 才可驱动)
    • _emit(NODE_ADDED × N) + _emit(EDGE_ADDED × M)
scheduler.on_event(envelope):  # 编排反应
  泵:同步推进图中"deps 已满足的 PENDING 节点"
  → subtask_1_1_market_scan(depend_on=[]) 满足 → 进 Step 3.2 发搜推
```
#### 拆解结构(总-分-总)
```
阶段1【总·宏观扫描】               阶段2【分·四维度专题 + 一线实践】        阶段3【总·汇总】
 subtask_1_1_market_scan ──┬──► subtask_2_1_market      ──┐
                           ├──► subtask_2_2_competition ──┤
                           ├──► subtask_2_3_tech ────────┐ ├─► subtask_3_1_aggregate_report ──► subtask_3_2_judgments
                           ├──► subtask_2_4_customer     │ │
                           └──► subtask_2_5_practice ────┘─┘
```
> ★ `subtask_2_3_tech` 是 **reroute 触发点**:首轮拆解**刻意省略**(模拟规划遗漏),终验 AC#3 缺 tech → FAIL → Step 4 补做。完整 8 个 TaskSpec 见下,首次回投传 **7 个(去掉 2_3_tech)**。
#### TaskSpec 完整列表(8 个;首轮回投去掉 `subtask_2_3_tech`,与示例同款多行 dataclass 格式)
> ⚠️ **模型对账**:
> - `TaskSpecMetadata` 现仅 `id/title`——**`summary`/`tags` 已删除**,下方各 TaskSpec 的 metadata 已不带这两个字段。
> - `TaskSpec.spec` 真实是 `str`,与下方 `spec=TaskSpec(...)` 结构等价(string 即序列化形式,不做区分);`Node.targets_acceptance`/`targets_deliverable` 在落图时由 `spec` 灌注。
> - **`depth` = 递归深度**(n_root=0,其直接 children=1,每层 DECOMPOSITION +1,上限 3)。下方 8 个 TaskSpec 全是 n_root 直接 children → **全部 `depth=1`**(phase 编号 1_/2_/3_ 只是命名,与 depth 无关)。BBS 上升时递归 children 才 depth=2/3(见 Step 3.2 BBS 实例)。
```python
TaskSpec(
    node_id="subtask_1_1_market_scan",
    depth=1,
    depend_on=[],
    spec=TaskSpec(
        metadata=TaskSpecMetadata(
            id="st_1_1",
            title="存储巨头最新财报、新品发布与前沿技术扫描"
        ),
        context=TaskContext(
            background="AI大爆发推动了新一代存储硬件架构的加速落地。NVIDIA Blackwell芯片发布、三大闪存巨头（Micron, Samsung, SK Hynix）及企业级存储先锋（Pure Storage, Solidigm）在近期释放了大量关键信号。",
            constraints=[
                Constraint(kind=ConstraintKind.HARD, text="必须覆盖近3个月（最新一个季度）的厂商财报及新品发布信息。"),
                Constraint(kind=ConstraintKind.HARD, text="核心数据源需溯源至官方发布会、财报PPT或一手中介访谈。")
            ]
        ),
        goal=TaskGoal(
            objective="输出最新季度全球企业级与数据中心存储的动态简报，为后续投资判断提供至少30%的最新信息支撑。",
            acceptances=[
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.THRESHOLD, properties={"min_recent_news_ratio": 0.3}),
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"required_vendors": ["NVIDIA", "Pure Storage", "NetApp", "Solidigm"]})
            ]
        )
    )
)
TaskSpec(
    node_id="subtask_2_1_market",
    depth=1,
    depend_on=["subtask_1_1_market_scan"],
    spec=TaskSpec(
        metadata=TaskSpecMetadata(
            id="st_2_1",
            title="市场规模与需求结构分析（market维度）"
        ),
        context=TaskContext(
            background="在阶段1动态扫描基础上，需把'AI拉动存储'从定性判断落到规模数字与需求结构占比，区分存量替换 vs AI新增。",
            constraints=[Constraint(kind=ConstraintKind.SOFT, text="市场规模需给出量级区间并标明口径与假设来源。")]
        ),
        goal=TaskGoal(
            objective="产出市场规模模型+需求结构拆解，支撑投资价值判断(AC#1)与四大维度的市场维度(AC#3)。",
            acceptances=[
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"dimension": "market"}),
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.THRESHOLD, properties={"min_data_points": 3})
            ]
        )
    )
)
TaskSpec(
    node_id="subtask_2_2_competition",
    depth=1,
    depend_on=["subtask_1_1_market_scan"],
    spec=TaskSpec(
        metadata=TaskSpecMetadata(
            id="st_2_2",
            title="竞争格局与可跟踪标的梳理（competition维度）"
        ),
        context=TaskContext(
            background="需对各细分赛道给出竞争图谱，识别壁垒(颗粒厂/主控/阵列/云)与潜在颠覆者，为可跟踪标的提供依据。",
            constraints=[Constraint(kind=ConstraintKind.HARD, text="每个细分赛道至少给出3家代表公司及其核心变量。")]
        ),
        goal=TaskGoal(
            objective="产出竞争格局图谱+可跟踪标的清单，支撑AC#2（最值得跟踪的细分赛道/公司类型/核心变量）与竞争维度(AC#3)。",
            acceptances=[
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"dimension": "competition"}),
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.THRESHOLD, properties={"min_tracking_targets": 5})
            ]
        )
    )
)
TaskSpec(  # ★ reroute 触发点：首轮省略，终验FAIL后Step4补做
    node_id="subtask_2_3_tech",
    depth=1,
    depend_on=["subtask_1_1_market_scan"],
    spec=TaskSpec(
        metadata=TaskSpecMetadata(
            id="st_2_3",
            title="技术演进路线分析（tech维度）★"
        ),
        context=TaskContext(
            background="AI训练集群的存储架构瓶颈催生了NVMe-oF、ZNS、EC纠删等新方向；需把技术演进映射到对厂商竞争力与投资机会的影响。",
            constraints=[Constraint(kind=ConstraintKind.HARD, text="技术判断需关联到对厂商竞争力的影响。")]
        ),
        goal=TaskGoal(
            objective="产出技术路线图，满足四大维度的技术维度(AC#3 tech)；首轮缺失→终验FAIL→reroute补做。",
            acceptances=[
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"dimension": "tech"}),
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"required_topics": ["NVMe-oF", "ZNS", "3D-NAND-stacking"]})
            ]
        )
    )
)
TaskSpec(
    node_id="subtask_2_4_customer",
    depth=1,
    depend_on=["subtask_1_1_market_scan"],
    spec=TaskSpec(
        metadata=TaskSpecMetadata(
            id="st_2_4",
            title="客户需求与采购行为分析（customer维度）"
        ),
        context=TaskContext(
            background="不同客户群对性能/容量/成本/可靠性的权重差异，决定了SSD/阵列/云存储各细分赛道的商业化潜力。",
            constraints=[Constraint(kind=ConstraintKind.SOFT, text="客户画像至少覆盖3类买家（云厂/运营商/企业）。")]
        ),
        goal=TaskGoal(
            objective="产出客户需求分层图谱，满足客户维度(AC#3)，并回流到投资价值判断(AC#1)。",
            acceptances=[
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"dimension": "customer"}),
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.THRESHOLD, properties={"min_buyer_segments": 3})
            ]
        )
    )
)
TaskSpec(
    node_id="subtask_2_5_practice",
    depth=1,
    depend_on=["subtask_1_1_market_scan"],
    spec=TaskSpec(
        metadata=TaskSpecMetadata(
            id="st_2_5",
            title="一线行业实践落地经验收集（BBS悬赏）"
        ),
        context=TaskContext(
            background="二手资料无法替代一线实践；需悬赏访谈AI训练集群运维、存储架构师等角色，落地痛点是投资判断的现实约束。",
            constraints=[Constraint(kind=ConstraintKind.HARD, text="访谈对象须为一线从业者，需留访谈记录与时间戳。")]
        ),
        goal=TaskGoal(
            objective="收集一手实践落地经验，满足AC#4（一手行业实践落地经验），为投资判断提供现实校验。",
            acceptances=[
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"dimension": "first_hand_practice"}),
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.THRESHOLD, properties={"min_interviews": 2})
            ]
        )
    )
)
TaskSpec(
    node_id="subtask_3_1_aggregate_report",
    depth=1,
    depend_on=["subtask_2_1_market", "subtask_2_2_competition", "subtask_2_3_tech", "subtask_2_4_customer", "subtask_2_5_practice"],
    spec=TaskSpec(
        metadata=TaskSpecMetadata(
            id="st_3_1",
            title="四维度+实践汇总成尽调报告"
        ),
        context=TaskContext(
            background="下游投资判断依赖一份结构化、有证据链的汇总报告；四维度须齐备方能支撑系统分析结论。",
            constraints=[
                Constraint(kind=ConstraintKind.HARD, text="报告须含evidence/risk/open_question三段式结构(AC#6)。"),
                Constraint(kind=ConstraintKind.HARD, text="四维度(市场/竞争/技术/客户)必须齐备(AC#3)。")
            ]
        ),
        goal=TaskGoal(
            objective="产出结构化尽调报告，满足四大维度系统分析(AC#3)与三段式结构(AC#6)，为投资判断提供基底。",
            acceptances=[
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"dimensions": ["market", "competition", "tech", "customer"]}),
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.INVARIANT, properties={"structure": ["evidence", "risk", "open_question"]})
            ]
        )
    )
)
TaskSpec(
    node_id="subtask_3_2_judgments",
    depth=1,
    depend_on=["subtask_3_1_aggregate_report"],
    spec=TaskSpec(
        metadata=TaskSpecMetadata(
            id="st_3_2",
            title="形成≥5条核心投资判断"
        ),
        context=TaskContext(
            background="这是尽调的最终交付：判断需可执行、可追溯、有时效，直接回答中短期投资价值与可跟踪标的。",
            constraints=[
                Constraint(kind=ConstraintKind.HARD, text="每条判断须同时给出支持证据、风险因素、待验证问题(AC#6)。"),
                Constraint(kind=ConstraintKind.HARD, text="至少30%关键判断来自近3个月信息(AC#7)。")
            ]
        ),
        goal=TaskGoal(
            objective="输出≥5条结构化投资判断，满足AC#1(投资价值结论)、AC#2(可跟踪标的)、AC#5(≥5条)与AC#7(时效)。",
            acceptances=[
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.THRESHOLD, properties={"min_count": 5}),
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.INVARIANT, properties={"structure": ["evidence", "risk", "open_question"]}),
                AcceptanceCriteria(kind=AcceptanceCriteriaKind.THRESHOLD, properties={"ratio": 0.3, "recency_months": 3})
            ]
        )
    )
)
```
#### 验收闭环自检(7 条顶层 AC → 子任务映射)
| 顶层验收 | 落点子任务 |
|---|---|
| AC#1 中短期投资价值 | 2_1/2_4 输入 → 3_2 给结论 |
| AC#2 可跟踪标的 | 2_2 → 3_2 |
| AC#3 四维度(market/competition/tech/customer) | 2_1/2_2/2_3/2_4 各一维 → 3_1 齐备 |
| AC#4 一手实践 | 2_5(BBS) |
| AC#5 ≥5 条判断 | 3_2(min_count:5) |
| AC#6 三段式结构 | 3_1(structure)+ 3_2(structure) |
| AC#7 ≥30% 近3月 | 1_1(min_recent_news_ratio)+ 3_2(ratio:0.3,recency:3) |
#### 子任务 → 执行路由映射(Step 3.2 接线依据)
| 子任务 | 路由分支 | 执行方 | 对应验收 | 备注 |
|---|---|---|---|---|
| subtask_1_1_market_scan | 单bot (C1) | `bot_industry_fetch` | AC#7 | 产出回投 MERGE execution_context |
| subtask_2_1_market | 动态群 (C3) | `grp_market_research`:[bot_market_demand, bot_capital_trend] | AC#3 market, AC#1 | 群 master 聚合 APPEND |
| subtask_2_2_competition | 动态群 (C3) | `grp_competition` | AC#3 competition, AC#2 | |
| **subtask_2_3_tech** | **动态群 (C3)** | `grp_tech_research`:[bot_storage_arch, bot_ssd_perf, bot_semi_process] | AC#3 tech | **★首轮省略→终验FAIL→Step4 reroute补做** |
| subtask_2_4_customer | 动态群 (C3) | `grp_customer_analysis`(3 bot CHAT) | AC#3 customer | |
| subtask_2_5_practice | unmatched→多轮拆解→depth≥MAX HUNG | 2 轮拆解出 4 访谈孙任务全 HUNG → 任务级用户确认升 BBS → 4 广场悬赏:`bot_ai_train_engineer`/`bot_procurement_staff` 认领 | AC#4 | 见 Step 3.2 BBS 实例;**任务级 BBS,非 piecemeal** |
| subtask_3_1_aggregate_report | 单bot/hybrid(汇总 bot) | 汇总 bot(自调 goal-verify) | AC#3, AC#6 | **汇总=普通 SUBTASK**:搜推→汇总 bot 执行+自验;非系统 EXEC_AGGREGATE |
| subtask_3_2_judgments | 单bot(终验 bot) | 终验 bot 自调 goal-verify 验**任务级全 AC** | AC#1/2/3/5/7 | **终验=普通 SUBTASK**(通常最后):无 GOAL_VERIFY 阶段节点,owner-bot 不参与终验 |
#### reroute 场景(衔接 Step 4)
- **首轮拆解**:回投 7 个 TaskSpec(**省略 `subtask_2_3_tech`**),`subtask_3_1_aggregate_report.depend_on` 首轮不含 2_3。
- **终验(Step 4)**:终验 bot 自调 goal-verify 检测 AC#3 `dimensions` 缺 `tech` → FAIL → 回投 `node.rejected{node_id:subtask_3_2_judgments}` → 泵 `handle_reject` → reroute-as-mini-decomposition(dispatch "补拆缺维度" 给 owner plan-skill,`properties.reroute=true`)→ plan-skill 回投补拆出 `subtask_2_3_tech` 新 SUBTASK 兄弟节点。
- **补做**:2_3 tech 群执行回投 → 2_3 DONE → 3_1(汇总 SUBTASK)deps 现含 2_3 → 重入 `spawning→aggregating` 重汇总 → 3_1 二次 DONE → 3_2(终验 SUBTASK)重开重验 → 终验 bot 自调 goal-verify 四维度齐 → PASS。
---
### Step 3.2 — 任务搜推 + 路由(on_event 泵驱动,搜推 skill,四分支)
> 驱动:**execute_task + 每次回投的 `on_event` 泵**是主驱动;`tick` 仅超时 watchdog(本节末尾)。搜推**不是直接调现成 keyword API**,是一个 owner-bot `bot-discover-skill`:拆关键词 → 逐个查 `search_by_keyword`(bot)+ `GroupDiscoverService.search_by_keyword`(群)→ 按 bot profile 算匹配度 → 多 bot 推理协作方式 → 回投 recommendation。系统据此四分支路由。
>
> 一个子任务在图里是**单节点 `SUBTASK`**:搜推/执行/验收/拆解/上升都是 `NodeState.phase`(`searching`→`executing`→`verifying` / miss→`decomposing`→`spawning`→`aggregating` / miss+d≥MAX→`HUNG`),不建 `BOT_SEARCH`/`DISPATCH` 子节点。
#### 流程(每个 SUBTASK 子任务节点 pi,deps 已满足)
```
scheduler.on_event 泵 drive pi(PENDING BOT_SEARCH):
   │
   ├─ a. 发搜推 dispatch(route="single_bot", target=owner-bot, prompt:"搜推: subtask_spec+state")  ← owner-bot 内置 discover-skill
   │     message = {task_id, parent_node: pi.node_id, prompt:"搜推: subtask_spec+state"}  ← parent_node 从图中 pi 来
   │     → BCN chat.send   ★异步边界
   │
   ├─ b. owner-bot bot-discover-skill(LLM):
   │     拆关键词[] → 逐个 BotDiscoverService.search_by_keyword(kw) + GroupDiscoverService.search_by_keyword(kw)
   │     → 聚合去重 → 按 bot profile 算 match_score → 多bot 推理 collaboration_mode/suggested_driver
   │     → 回投 recommendation
   │
   ├─ c. owner-bot 回投 discover.reported:
   │     POST /events {kind:"discover.reported", payload:{node_id: pi.node_id, recommendation:{route,...}}}  ← node_id 回带
   │
   ├─ d. on_event 按 discover 推荐路由(SUBTASK 节点本身进 executing/decomposing/hung phase,**不建 _disp/_dec 子节点**):
   │     single_bot → pi(SUBTASK) phase:searching→executing + dispatch(route="single_bot",target=bot,prompt="执行:spec+state")  ★异步边界
   │     group+已有 → pi executing + dispatch(route="group",target=existing_group_id,prompt="执行")
   │     group+无现成 → pi executing + dispatch(route="group",target=None,group=GroupSpec(topic=goal,participants,driver),prompt="执行")→BCS POST /groups 建自由聊天群  ★异步边界
   │     unmatched+depth<MAX → pi phase:decomposing + dispatch(route="unmatched",target=owner,prompt="拆解:spec+state")→plan.reported→spawning children SUBTASK(depth+1),递归回 3.1
   │     unmatched+depth≥MAX → pi HUNG(仍执行不了,不再拆,不立刻 BBS;HUNG 向上传播)
   │
   └─ e. 执行实体回投 state.updated(产出)+ node.accepted(verifier=执行实体自调 goal-verify 验 acceptance)→ pi DONE → on_event 泵推下一个 deps-met 节点
   │     ★HUNG 残留 + 全可执行 DONE → 图 HUMAN_REQUIRED → 用户确认 → BBS_ACTIVE → HUNG 叶子 escalate_to_bbs 广场悬赏 → BBS bot 认领执行+自调 goal-verify → 解除
```
#### 操作明细 + I/O 出处
| # | 操作 | 调用者 | 入参(出处) | 出参(去向) | 落点 |
|---|---|---|---|---|---|
| **a** | 发搜推 dispatch | 系统 `on_event` 泵 | `{task_id, parent_node=pi.node_id, prompt}`;prompt←retrieve_state(pi)读 pi.spec+state | BCN chat.send 给 owner-bot discover-skill | pi 仍 PENDING(等推荐回投) |
| **b** | LLM 搜推 | **owner-bot `bot-discover-skill`** | 搜推 prompt | `recommendation{route,bot_ids,collaboration_mode,suggested_driver,match_scores,existing_group_id}` | 拆词×N→查→匹配→推理协作 |
| **c** | 回投 `discover.reported` | **owner-bot `bot-discover-skill`** | `EventReportRequest`:`{kind:"discover.reported",payload:{node_id:pi.node_id, recommendation}}` | `{"accepted":true,"seq":n}` | on_event→暂存 `Node(pi).properties["discover_result"]`;pi 不翻态(等路由) |
| **d** | 四分支路由 + dispatch(无新子节点) | 系统 `on_event` 泵 → `SubtaskExecutor.handle_search` | recommendation(回投) | DispatchResult | **pi 本身翻 phase**(`searching→executing`/`decomposing`/`HUNG`),填 `run_mode`+`assignee`,按 route dispatch(parent_node=pi.node_id);**不 `add_child`** 任何 `_disp`/`_dec` 节点;NODE_RUNNING 事件 |
| **e** | 执行方回投 | **执行 bot/群 master `task-exec-skill`** | `state.updated`(MERGE/APPEND)+ `node.accepted`(verifier=执行实体自调 goal-verify) | `{"accepted":true,"seq":n}` | fold `NodeState(pi)` + pi `done`;依赖传播 → 泵推下一个 deps-met 节点 |
| **f** | 聚合(父 SUBTASK spawning→aggregating) | 系统 `on_event` 泵(children 全 DONE/有 HUNG) | parent SUBTASK node_id | — | 父 SUBTASK 节点**自身** phase `spawning→aggregating`:APPEND children 产出 → `done`(全 child done)/ `HUNG`(有 child HUNG)。**无系统 `_detect_and_aggregate`/EXEC_AGGREGATE/aggregate_verdict** |
| **g**(watchdog) | `POST /api/tasks/T1/tick`(无 body) | 系统 `TaskScheduler._tick`(仅超时兜底) | — | `{"action":"ticked","progressed":bool,...}` | 扫超时无更新节点重推;**非常规驱动** |
#### c 回投入参示例(单 bot 命中)
```json
{"kind":"discover.reported","payload":{"node_id":"subtask_1_1_market_scan",
  "recommendation":{"route":"single_bot","bot_id":"bot_industry_fetch","match_score":0.9,
                    "existing_group_id":null}}}
```
> `node_id` 出处:owner-bot 从 a 步 dispatch 消息的 `parent_node` 原样回带(源头=图中 pi 的 node_id)。
#### e 回投入参示例(单 bot 产出+验收,MERGE;scope=node_id 全程不变,无 `_disp` 后缀)
```json
{"kind":"state.updated","payload":{"scope":"subtask_1_1_market_scan","semantics":"merge",
  "patch":{"execution_context":{"产业链":"上游控制器/SSD颗粒/主控芯片;下游阵列/服务器/云","龙头":"三星/WDC/希捷/长江存储"}
           }}}
{"kind":"node.accepted","payload":{"node_id":"subtask_1_1_market_scan","verifier":"bot_industry_fetch"}}
```
> 群 master APPEND 示例(scope 换 `subtask_2_1_market`,`intermediate_results` 多条拼接)。
#### 四分支路由(对照 case step3.2)
| 分支 | recommendation.route | 动作 | 底层 |
|---|---|---|---|
| **单bot** | `single_bot` | `dispatch(route="single_bot",target=bot_id,prompt="执行:taskspec+state")` | `ExecutionPort.dispatch_single_bot`→BCN chat.send |
| **协作群(已有)** | `group`+`existing_group_id≠null` | `dispatch(route="group",target=group_id,prompt="执行:taskspec+state")` | `ExecutionPort.coop_group`(复用)→群 chat.send |
| **动态协作群** | `group`+`existing_group_id=null` | `dispatch(route="group",target=None,group=GroupSpec(topic=goal,participants,driver),prompt="执行:taskspec+state")` | BCS `POST /groups` 建群→群 chat.send |
| **未匹配** | `unmatched`+depth<MAX | SUBTASK phase→decomposing + `dispatch(route="unmatched",target=owner,prompt="拆解:taskspec+state")` | 递归回 3.1,spawn children depth+1 |
| **未匹配** | `unmatched`+depth≥MAX | SUBTASK **HUNG**(仍执行不了,不再拆;HUNG 向上传播) | 不立刻 BBS;等全可执行 DONE → 图 HUMAN_REQUIRED → 用户确认升 BBS |
#### 本 case 子任务执行实例(7 子任务,首轮无 2_3_tech;全 SUBTASK 同构)
> 节点 id 示意:子任务全 `node_id=subtask_X`(无 `_disp` 后缀);`verifier`=执行实体(自调 goal-verify)。
| 子任务节点 | discover 命中 | route | 执行方 | 回投 | 说明 |
|---|---|---|---|---|---|
| subtask_1_1_market_scan | bot_industry_fetch(score0.9) | single_bot | bot_industry_fetch | MERGE + node.accepted(verifier=该bot) | 单 bot 执行+自验 |
| subtask_2_1_market | bot_market_demand+bot_capital_trend(union,chat) | group(动态) | grp_market_research | APPEND 2条 + node.accepted(verifier=群master) | 协作群执行,群master自验 |
| subtask_2_2_competition | 多 bot union | group(动态) | grp_competition | APPEND + node.accepted(verifier=群master) | 同上 |
| subtask_2_4_customer | 3 bot union | group(动态) | grp_customer_analysis | APPEND 3条 + node.accepted(verifier=拉群bot) | 自由聊天群,拉群bot自验 |
| subtask_2_5_practice | 无 bot cover→miss 多轮拆解→depth≥MAX HUNG | unmatched→decompose(depth<MAX)→HUNG(depth≥MAX) | (挂起) | HUNG 传播 → 任务级用户确认升 BBS → HUNG 叶子广场悬赏认领 → BBS bot 执行+自验 node.accepted | 详见 BBS 实例;**任务级 BBS,非 piecemeal** |
| subtask_3_1_aggregate_report | 汇总 bot(score 高) | single_bot | 汇总 bot | MERGE 汇总报告 + node.accepted(verifier=汇总bot) | 汇总=普通 SUBTASK,搜推→汇总bot执行+自验;非系统 EXEC_AGGREGATE |
| subtask_3_2_judgments | 终验 bot(score 高) | single_bot | 终验 bot | 终验 bot 自调 goal-verify 验任务级全 AC → node.accepted(DONE)/node.rejected(reroute) | 终验=普通 SUBTASK,通常最后;**无 GOAL_VERIFY 阶段节点** |
#### 其它子任务执行实例(1_1 / 2_1 / 2_2 / 2_4 / 2_3 补做)
> 与 BBS 实例(2_5)并列,覆盖首轮会真实跑到的 4 条标准路径 + 1 条 Step4 补做路径。
> 每条三段:**搜推 discover.reported** → **on_event 路由(SUBTASK 翻 phase searching→executing + 填 run_mode/assignee + dispatch_*)** → **执行回投(state.updated + node.accepted)**。
> 通用:`node_id` 全部源于 a 步 dispatch 消息的 `parent_node` 回带;`dispatch(route, target, {task_id, parent_node, prompt:"执行/拆解: <subtask spec 序列化>+<retrieve-state>"}, *, group=None)`,出参 = 回投 event。`parent_node` = 该 SUBTASK 节点**自身** node_id(全程不变,无 `_disp`/`_dec` 子节点);on_event 不 `add_child`,只翻 phase + 填 assignee + dispatch。
---
**A. `subtask_1_1_market_scan`(SINGLE_BOT / C1 / depth=1,产业链与龙头扫描)**
- 搜推:discover-skill 拆词 `["存储巨头财报","新品发布","前沿技术","近3月动态"]` → `search_by_keyword` → `bot_industry_fetch`(cover 厂商财报/新品/技术扫描)单条 0.9,无需协作:
```json
{"kind":"discover.reported","payload":{"node_id":"subtask_1_1_market_scan",
  "recommendation":{"route":"single_bot","bot_id":"bot_industry_fetch","match_score":0.9,
    "reason":"单bot覆盖产业链/龙头/新品扫描,无需协作"}}}
```
- on_event 路由:`C1` → **SUBTASK 翻 phase `searching→executing`**,填 `run_mode=SINGLE_BOT, assignee=bot_industry_fetch` + `dispatch(route="single_bot", target=bot_industry_fetch, {parent_node:"subtask_1_1_market_scan", prompt:"执行:1_1 spec+state"})`(无 `_disp` 子节点)。
- 执行回投(bot_industry_fetch,见 §e 示例):`state.updated(scope=subtask_1_1_market_scan, MERGE execution_context 产业链/龙头 + artifacts 产业链地图)` + `node.accepted{node_id:subtask_1_1_market_scan, verifier:bot_industry_fetch}` → `1_1 DONE`。
---
**B. `subtask_2_1_market`(COOP_GROUP 动态 / C3 / depth=1,市场规模)**
- 搜推:拆词 `["市场规模","SSD","企业级存储","需求结构","AI驱动"]` → `bot_market_demand`(市场模型/规模)0.85 + `bot_capital_trend`(资本/采购周期)0.80 → 多 bot union,单 bot 不充分,无现成群:
```json
{"kind":"discover.reported","payload":{"node_id":"subtask_2_1_market",
  "recommendation":{"route":"group","existing_group_id":null,
    "suggested_participants":["bot_market_demand","bot_capital_trend"],
    "suggested_driver":"bot_market_demand","collab_mode":"chat","match_score":0.82,
    "reason":"市场规模需需求模型+资本周期双视角,无现成群"}}}
```
- on_event 路由:`C3` 动态 → **SUBTASK 翻 phase `searching→executing`**,填 `run_mode=COOP_GROUP, assignee=grp_market_research(待建)` + `dispatch(route="group", target=None, group=GroupSpec(topic:"存储市场规模分析", participants=["bot_market_demand","bot_capital_trend"], driver="bot_market_demand", collab_mode="chat"), {parent_node:"subtask_2_1_market", prompt:"执行:2_1 spec+state"})` → BCS `POST /groups` 建群 `grp_market_research`(assignee 回填)→ 群 `chat.send`(无 `_disp` 子节点)。
- 执行回投(群 master 聚合 2 bot 产出,APPEND):
```json
{"kind":"state.updated","payload":{"scope":"subtask_2_1_market","semantics":"append",
  "patch":{"intermediate_results":[
    {"bot":"bot_market_demand","content":"2025全球企业级存储~$320B,SSD占比58%,AI训练需求CAGR 35%"},
    {"bot":"bot_capital_trend","content":"资本周期2024H2见底,2025扩张,东汉ICT资本开支+18%"}]
    }}}
{"kind":"node.accepted","payload":{"node_id":"subtask_2_1_market","verifier":"grp_market_research:master"}}
```
→ `2_1 DONE`。
---
**C. `subtask_2_2_competition`(COOP_GROUP 动态 / C3 / depth=1,竞争格局)**
- 搜推:拆词 `["竞争格局","头部玩家","份额","护城河","SSD颗粒","主控","阵列","云存储"]` → `bot_industry_fetch`(厂商动态/份额)0.82 + `bot_market_demand`(份额/结构)0.78 → union:
```json
{"kind":"discover.reported","payload":{"node_id":"subtask_2_2_competition",
  "recommendation":{"route":"group","existing_group_id":null,
    "suggested_participants":["bot_industry_fetch","bot_market_demand"],
    "suggested_driver":"bot_industry_fetch","collab_mode":"chat","match_score":0.80,
    "reason":"格局需厂商动态+份额结构双视角"}}}
```
- on_event 路由:`C3` 动态 → SUBTASK 翻 `executing` + `dispatch(route="group", target=None, group=GroupSpec(topic:"存储竞争格局", participants=["bot_industry_fetch","bot_market_demand"], driver="bot_industry_fetch"), {parent_node:"subtask_2_2_competition", prompt:"执行:2_2 spec+state"})` → 动态建群 `grp_competition` → 群 `chat.send`。
- 执行回投(群 master APPEND 2 条 + artifacts 行业格局图):
```json
{"kind":"state.updated","payload":{"scope":"subtask_2_2_competition","semantics":"append",
  "patch":{"intermediate_results":[
    {"bot":"bot_industry_fetch","content":"头部:三星/WDC/希捷/长江存储;格局:颗粒厂纵向整合,阵列厂横向扩品"},
    {"bot":"bot_market_demand","content":"份额:三星34%/WDC21%/希捷18%,SSD份额加速集中"}]
    }}}
{"kind":"node.accepted","payload":{"node_id":"subtask_2_2_competition","verifier":"grp_competition:master"}}
```
→ `2_2 DONE`。
---
**D. `subtask_2_4_customer`(COOP_GROUP 动态 / C3 / depth=1,客户需求)**
- 搜推:拆词 `["客户需求","采购行为","超大规模云厂","电信运营商","企业客户"]` → `bot_market_demand`(需求结构)0.80 + `bot_capital_trend`(采购周期)0.76 + `bot_industry_fetch`(客户动态)0.72 → 3 bot union:
```json
{"kind":"discover.reported","payload":{"node_id":"subtask_2_4_customer",
  "recommendation":{"route":"group","existing_group_id":null,
    "suggested_participants":["bot_market_demand","bot_capital_trend","bot_industry_fetch"],
    "suggested_driver":"bot_market_demand","collab_mode":"chat","match_score":0.76,
    "reason":"客户需求需需求+采购+动态三视角"}}}
```
- on_event 路由:`C3` 动态 → SUBTASK 翻 `executing` + `dispatch(route="group", target=None, group=GroupSpec(topic:"存储客户需求", participants=["bot_market_demand","bot_capital_trend","bot_industry_fetch"], driver="bot_market_demand"), {parent_node:"subtask_2_4_customer", prompt:"执行:2_4 spec+state"})` → 动态建群 `grp_customer_analysis` → 群 `chat.send`。
- 执行回投(群 master APPEND 3 条 + artifacts 客户画像):
```json
{"kind":"state.updated","payload":{"scope":"subtask_2_4_customer","semantics":"append",
  "patch":{"intermediate_results":[
    {"bot":"bot_market_demand","content":"需求结构:超大规模云厂占62%,运营商18%,企业20%;AI驱动高密度"},
    {"bot":"bot_capital_trend","content":"采购周期:云厂季度集采,运营商年度框架,企业项目制"},
    {"bot":"bot_industry_fetch","content":"客户动态:头部云厂自研定制化SSD,运营商集采向国产倾斜"}]
    }}}
{"kind":"node.accepted","payload":{"node_id":"subtask_2_4_customer","verifier":"grp_customer_analysis:master"}}
```
→ `2_4 DONE`。
---
**E. `subtask_2_3_tech`(COOP_GROUP 动态 / C3 / depth=1,技术演进 — 首轮省略,Step4 reroute 补做)**
- 首轮:owner-bot 拆解时按 case 省略 2_3,**不在 graph**,无搜推/执行(留 AC#3 dimensions.tech 缺口给终验暴露)。
- Step4 reroute 补做时:搜推 拆词 `["技术演进","NVMe-oF","ZNS","3D NAND","存储网络","CXL"]` → `bot_storage_arch`(架构 NVMe-oF)0.88 + `bot_ssd_perf`(SSD 性能 ZNS)0.84 + `bot_semi_process`(3D NAND 工艺)0.82 → 3 bot union:
```json
{"kind":"discover.reported","payload":{"node_id":"subtask_2_3_tech",
  "recommendation":{"route":"group","existing_group_id":null,
    "suggested_participants":["bot_storage_arch","bot_ssd_perf","bot_semi_process"],
    "suggested_driver":"bot_storage_arch","collab_mode":"chat","match_score":0.85,
    "reason":"技术演进需架构+性能+工艺三视角"}}}
```
- on_event 路由:`C3` 动态 → SUBTASK 翻 `executing` + `dispatch(route="group", target=None, group=GroupSpec(topic:"存储技术演进", participants=["bot_storage_arch","bot_ssd_perf","bot_semi_process"], driver="bot_storage_arch"), {parent_node:"subtask_2_3_tech", prompt:"执行:2_3 spec+state"})` → 动态建群 `grp_tech_research` → 群 `chat.send`(Step4 实例见 Step 4)。
- 执行回投(群 master APPEND 3 条 + artifacts 技术路线图):
```json
{"kind":"state.updated","payload":{"scope":"subtask_2_3_tech","semantics":"append",
  "patch":{"intermediate_results":[
    {"bot":"bot_storage_arch","content":"NVMe-oF 成主流,Scale-Out 架构;CXL.mem 存储池化试点"},
    {"bot":"bot_ssd_perf","content":"ZNS Zoned Namespace 兴起,大容量 QLC 量产,EC 软硬件下沉"},
    {"bot":"bot_semi_process","content":"3D NAND 300+ 层,DRAMless SSD 渗透,主控国产化"}]
    }}}
{"kind":"node.accepted","payload":{"node_id":"subtask_2_3_tech","verifier":"grp_tech_research:master"}}
```
→ `2_3 DONE`(Step4),回流补 AC#3 tech 维度,二次终验 PASS。
---
**逐子任务 I/O 衔接自检**:
- discover `bot_id`/`suggested_participants` ← `search_by_keyword` 命中(case bot 能力图谱);`route=single_bot` ← 单 bot match_score≥0.85 即足;`route=group`/`existing_group_id=null` ← 多 bot union 且无现成群。
- on_event 路由分支 ← `recommendation.route`;SUBTASK 翻 `searching→executing`,填 `assignee=bot_id`(单 bot)/ `assignee=新建群 group_id`(动态群)(无 `_disp` 子节点)。
- 回投 `scope` ← dispatch `parent_node`(= SUBTASK node_id,全程不变);`verifier` ← 执行 bot(单)/群 master(群);`semantics` MERGE(单 bot 一份产)/APPEND(群多 bot 多份产)。
- 4 条标准路径(1_1/2_1/2_2/2_4)并发跑,2_5 走 miss 多轮→HUNG;3_1(汇总 SUBTASK)等 5 叶全 DONE 后由其自身 `spawning→aggregating` phase 汇总。
#### BBS 上升实例(`subtask_2_5_practice` 一手实践:无访谈 bot → 多轮拆解 → 升 BBS)
> 前提:存储行业 bot 全是分析型(`bot_industry_fetch`/`bot_market_demand`/`bot_capital_trend`/`bot_storage_arch`/`bot_ssd_perf`/`bot_semi_process`),**无一能做一线访谈** → 每轮搜推都 unmatched → 靠 depth 上限(3)终止 → 升 BBS。`subtask_2_5_practice` 是 n_root 直接 child → depth=1。
**通用规则**(全节点 `SUBTASK`,搜推/拆解 = phase):SUBTASK(depth=d) `searching` 搜推 miss → `d<MAX` 转 `decomposing`(dispatch "拆解" 给 owner plan-skill,无 `_dec` 子节点) → `plan.reported` → `spawning` children SUBTASK(depth=d+1)→ 全 DONE `aggregating→done`;`d≥MAX` 转 **HUNG**(不拆不 BBS)。BBS 是任务级、用户确认后才升。
**节点树**(parent SUBTASK 自身 decompose,无 `_dec` peer 节点):
```
subtask_2_5_practice (SUBTASK, depth=1) "收集一手行业实践落地经验"
  │ R1 searching miss, 1<3 → decomposing(spawn children depth=2)
  ├ subtask_2_5_1_ai_ops (SUBTASK, depth=2) "AI训练集群运维侧一线访谈"
  │   │ R2 searching miss, 2<3 → decomposing(spawn children depth=3)
  │   ├ subtask_2_5_1_1 (SUBTASK, depth=3) "NVMe-oF落地瓶颈访谈"    → R3 miss,3≥3 → HUNG
  │   └ subtask_2_5_1_2 (SUBTASK, depth=3) "EC纠删运维经验访谈"      → HUNG
  └ subtask_2_5_2_procurement (SUBTASK, depth=2) "采购侧一线访谈"
      │ R2 searching miss, 2<3 → decomposing(spawn children depth=3)
      ├ subtask_2_5_2_1 (SUBTASK, depth=3) "超大规模云厂存储采购驱动访谈" → HUNG
      └ subtask_2_5_2_2 (SUBTASK, depth=3) "企业客户存储采购偏好访谈"     → HUNG
```
**Round 1(depth=1,`subtask_2_5_practice` phase=searching)**:
- 依赖满足(directed by 泵)→ enter searching → dispatch `{task_id, parent_node:"subtask_2_5_practice", prompt:"搜推:收集一手行业实践落地经验 spec+state"}` → discover-skill 拆词 `["一手实践","行业访谈","存储落地经验"]` → `search_by_keyword` 候选全分析型、无访谈能力 → 回投:
```json
{"kind":"discover.reported","payload":{"node_id":"subtask_2_5_practice",
  "recommendation":{"route":"unmatched","reason":"no bot covers first-hand interview","match_scores":{}}}}
```
- on_event → `handle_search`:miss + `depth=1<MAX` → 转 `decomposing` → **不翻 DONE、不建 `_dec` 子节点**,直接 `dispatch(route="unmatched", parent_node:"subtask_2_5_practice", prompt:"拆解: 一手实践按访谈角色拆")`。
- plan-skill 回投 → `handle_plan` → `spawning`:`add_node` 2 个 depth=2 children SUBTASK(挂为 `subtask_2_5_practice` 的 children,edge parent→child)+ `depend_on` 自洽;父节点停在 spawning(等 children)。
```json
{"kind":"plan.reported","payload":{"parent_node":"subtask_2_5_practice","subtasks":[
  {"node_id":"subtask_2_5_1_ai_ops","depth":2,"depend_on":[],
   "spec":{"metadata":{"id":"st_2_5_1","title":"AI训练集群运维侧一线访谈"},
     "context":{"background":"NVMe-oF/EC存储架构瓶颈的一线运维经验是技术演进判断的现实校验"},
     "goal":{"objective":"访谈AI训练集群运维,获取NVMe-oF/EC落地痛点一线经验",
       "acceptances":[{"kind":"output","properties":{"dimension":"first_hand_practice"}},
                      {"kind":"threshold","properties":{"min_interviews":1}}]}
     }},
  {"node_id":"subtask_2_5_2_procurement","depth":2,"depend_on":[],
   "spec":{"metadata":{"id":"st_2_5_2","title":"采购侧一线访谈"},
     "goal":{"objective":"访谈云厂/企业采购侧,获取存储采购驱动与偏好一线信息",
       "acceptances":[{"kind":"output","properties":{"dimension":"first_hand_practice"}},
                      {"kind":"threshold","properties":{"min_interviews":1}}]}
     }}]}}
```
**Round 2(depth=2,以 `subtask_2_5_1_ai_ops` 为例;2_5_2 同形)**:
- 依赖满足 → enter searching → dispatch 搜推 → unmatched → `discover.reported{node_id:"subtask_2_5_1_ai_ops", route:unmatched}`。
- on_event → `handle_search`:miss + `depth=2<MAX` → 转 `decomposing` → `dispatch(route="unmatched", parent_node:"subtask_2_5_1_ai_ops", prompt:"拆解: 按访谈主题拆")`(无 `_dec` 子节点)。
- plan-skill 回投 → `handle_plan` → `spawning`:`add_node` 2 个 depth=3 孙 children SUBTASK:
```json
{"kind":"plan.reported","payload":{"parent_node":"subtask_2_5_1_ai_ops","subtasks":[
  {"node_id":"subtask_2_5_1_1","depth":3,"depend_on":[],
   "spec":{"metadata":{"id":"st_2_5_1_1","title":"NVMe-oF落地瓶颈访谈"},
     "goal":{"objective":"访谈获取NVMe-oF在AI训练集群的落地瓶颈(延迟/连接规模/运维)",
       "acceptances":[{"kind":"output","properties":{"topic":"nvme-of-bottleneck"}}]}
     }},
  {"node_id":"subtask_2_5_1_2","depth":3,"depend_on":[],
   "spec":{"metadata":{"id":"st_2_5_1_2","title":"EC纠删运维经验访谈"},
     "goal":{"objective":"访谈获取EC纠删在AI训练存储的运维经验(开销/恢复/选型)",
       "acceptances":[{"kind":"output","properties":{"topic":"ec-ops-experience"}}]}
     }}]}}
```
(`subtask_2_5_2_procurement` 同款:decomposing → spawn `2_5_2_1` 云厂采购 / `2_5_2_2` 企业采购,depth=3)
**Round 3(depth=3,`subtask_2_5_1_1` → HUNG,不再拆、不立刻 BBS)**:
- 搜推 unmatched → `discover.reported{node_id:"subtask_2_5_1_1", route:unmatched}`。
- on_event:`depth=3≥MAX` → ★**HUNG**(仍执行不了,不再拆、不升 BBS):
```python
subtask_2_5_1_1 → status=HUNG (图保持 RUNNING,其它子任务继续)
# HUNG 向上传播:child HUNG → parent subtask_2_5_1_ai_ops(spawning)卡住 → HUNG
#              → subtask_2_5_practice 卡住 → HUNG;3_1(汇总 SUBTASK)depend_on 含 2_5(HUNG)→ 阻塞
```
4 个 depth=3 孙节点(2_5_1_1/2_5_1_2/2_5_2_1/2_5_2_2)全部 HUNG。**不并发升 BBS**(BBS 是任务级、用户确认后)。
**任务级 BBS(其它可执行子任务跑完后)**:
```
1_1/2_1/2_2/2_4 各搜推→命中→执行实体执行+自调 goal-verify→node.accepted→DONE (图保持 RUNNING)
3_1(汇总)depend_on 含 2_5(HUNG)→ 阻塞;3_2(终验)depend_on 3_1 → 阻塞
── 全可执行 DONE + HUNG(2_5 子树)+阻塞(3_1/3_2)残留 ──
→ 图 HUMAN_REQUIRED → 用户确认 → BBS_ACTIVE(整体任务升 BBS)
→ HUNG 叶子(4 孙节点)escalate_to_bbs 广场悬赏(并发 4):
   TaskDriverPort.escalate_to_bbs(task_id, "subtask_2_5_1_1", spec) → BbsExecutor.claim → BCN 广场广播
→ BBS bot 认领 + 执行 + 自调 goal-verify 回投(bot_ai_train_engineer 认领 2_5_1_1/2_5_1_2;bot_procurement_staff 认领 2_5_2_1/2_5_2_2):
```
```json
{"kind":"state.updated","payload":{"scope":"subtask_2_5_1_1","semantics":"merge",
  "patch":{"execution_context":{"一线实践":"AI训练集群NVMe-oF部署瓶颈:远端直访延迟抖动+连接数爆炸,单节点8万连接即打满",
                               "访谈对象":"bot_ai_train_engineer","访谈时间":"2026-08"}
           }}}
{"kind":"node.accepted","payload":{"node_id":"subtask_2_5_1_1","verifier":"bot_ai_train_engineer"}}
```
on_event:`NodeState(subtask_2_5_1_1)` MERGE;HUNG→DONE(verifier=认领 BBS bot 自调 goal-verify)。4 孙节点同形 → DONE。
**逐层聚合回溯**(parent SUBTASK spawning→aggregating phase,非 EXEC_AGGREGATE 节点):
```
2_5_1_1✓ + 2_5_1_2✓ → subtask_2_5_1_ai_ops(SUBTASK, spawning→aggregating)→ DONE
2_5_2_1✓ + 2_5_2_2✓ → subtask_2_5_2_procurement(aggregating)→ DONE
2_5_1_ai_ops✓ + 2_5_2_procurement✓ → subtask_2_5_practice(aggregating)→ DONE
```
聚合时 4 条访谈 `intermediate_results` APPEND 到 `NodeState(subtask_2_5_practice)`,作为一手实践产出,回流到 `subtask_3_1`(汇总 SUBTASK)。
→ 2_5 DONE → 3_1 解除阻塞 → 汇总 bot 搜推→执行+自调 goal-verify → 3_1 DONE → 3_2(终验)解除阻塞 → 终验 bot 自调 goal-verify 验全 AC → 任务终态(见 Step 4)。
**I/O 衔接自检**:R1 `route=unmatched`←拆词查询无 bot cover;R1 `depth=1<MAX`走拆解←node.depth;R3 `depth=3≥MAX`→ **HUNG**(不拆不 BBS)←node.depth;BBS **任务级**:全可执行 DONE + HUNG 残留 → HUMAN_REQUIRED → 用户确认 → BBS_ACTIVE ← `GraphStatus` 状态机;`verifier`←认领该悬赏的 BBS bot(自调 goal-verify);逐层聚合←parent SUBTASK spawning→aggregating(children 全 DONE)。
**现有实现要改(进 M1,补 §4)**:
- `TaskDriverPort` 当前 `NoopTaskDriverPort` → 实现 `escalate_to_bbs`(调 `BbsExecutor.claim` 广场认领)+ 真实 `dispatch_single_bot`/`coop_group`(singlebox 真发 BCN)。
- `BbsExecutor.claim` 真实化:广场认领 + 悬赏 message 经 BCN 发 BBS bot pool。
- **BBS 任务级触发**:子任务 `miss+depth≥MAX` → `HUNG`(不立刻 escalate);图判定"全可执行 DONE + 有 HUNG 残留" → `HUMAN_REQUIRED` → 用户确认 → `BBS_ACTIVE` → 才对 HUNG 叶子 `escalate_to_bbs`。对上现成 `GraphStatus`(RUNNING→HUMAN_REQUIRED→BBS_ACTIVE→DONE)。
- depth 递归上限 guard:SUBTASK miss 时校验 `depth<MAX`,否则 HUNG(防无限递归)。
- HUNG 向上传播:child HUNG → parent SUBTASK(spawning)卡住 → parent HUNG/阻塞;depend_on 含 HUNG 的子任务阻塞不 drive。
#### 汇总与终验衔接(3_1/3_2 都是普通 SUBTASK)
- `subtask_3_1`(汇总 SUBTASK,`depend_on` 5 叶):首轮 2_3_tech 缺,实际 4 叶+practice DONE → deps-met → 搜推→匹配**汇总 bot** → 汇总 bot 读各 leaf 产出、产出汇总报告 + **自调 goal-verify** 验自身 acceptance → `node.accepted` → 3_1 DONE。**无系统 EXEC_AGGREGATE/aggregate_verdict**。
- `subtask_3_2`(终验 SUBTASK,`depend_on` 3_1,通常最后):3_1 DONE → deps-met → 搜推→匹配**终验 bot** → 终验 bot 读任务级全 AC + state + 各子任务产出 + **自调 goal-verify** 验"任务目标达成" → `node.accepted`(全子任务 DONE→任务 DONE)/ `node.rejected`(reroute,见 Step 4)。**无 GOAL_VERIFY 阶段节点,owner-bot 不参与终验**。
#### 新增工作项(进 §4)
- **搜推能力**(owner-bot 内置或 `BotDiscoverService`):拆关键词+调 `search_by_keyword`(bot)+`GroupDiscoverService.search_by_keyword`(群)+bot profile 匹配度+多 bot 协作方式推理+回投 `discover.reported`(recommendation)。
- **新事件 kind `discover.reported`**(M1 契约点,与 `plan.reported` 一起敲定)。
- **`BotDiscoverPort.recommend` 改 async**:搜推 `dispatch(route=single_bot,target=owner,"搜推")` + 等回投(替代 in-process cover);singlebox 保留 in-process 作 fallback profile。
- **`GroupDiscoverService.search_by_keyword` 新建**(调 BCS `/groups/my`),供搜推调(动态群 `existing_group_id` 判定)。
- **执行实体带 goal-verify skill**:各 bot/群 master/拉群 bot/BBS bot/汇总 bot/终验 bot 都装 goal-verify,执行产出后自验回投 `node.accepted`/`node.rejected`。
---
### Step 4 — 终验 FAIL → reroute-as-mini-decomposition → 补做 2_3_tech → 二次终验 PASS
> 衔接 Step 3.2 首轮:1_1/2_1/2_2/2_4/2_5(practice)DONE,2_3_tech **首轮未拆**(owner-bot 拆解时省略)。
> `subtask_3_1_aggregate_report` 首轮 `depend_on` 实际只落 4 叶(2_1/2_2/2_4/2_5)→ 4 叶 DONE 触发聚合 PASS → 3_1 DONE → drive `subtask_3_2_judgments` → 终验。
> 终验判 AC#3(`dimensions=[market,competition,tech,customer]`):market/competition/customer 三维齐,**tech 缺**(2_3 未做)→ FAIL → reroute。
> ★**reroute-as-mini-decomposition**:不再 `open_reroute_search` 挂兄弟 BOT_SEARCH;而是给根 `n_root` 追加一个 reroute 用的 DECOMPOSITION 子节点,复用 Step 3.1 的 plan.reported 通路补拆出 `subtask_2_3_tech`,3_1 重开(DONE→PENDING)等 2_3 DONE 后重新聚合,3_2 重开重验。
#### 4.1 终验 FAIL(3_2 = 终验 SUBTASK,on_event 泵 drive → 搜推→终验 bot→自调 goal-verify)
- 驱动:3_1(汇总 SUBTASK)DONE → 依赖传播 → 泵 drive `subtask_3_2_judgments`(终验 SUBTASK,`depend_on=[3_1]`满足)→ **enter searching** → `dispatch(route="single_bot", target=owner-bot 搜推能力, {parent_node:"subtask_3_2_judgments", prompt:"搜推: 终验全任务 AC — 需读 spec.goal.acceptances + 全 subtask state,逐 AC 判"})`。
- 搜推:discover-skill 拆词 `["任务终验","全验收标准判定","尽调报告完整性"]` → 命中**终验 bot**(`bot_verifier`,profile=读全图 state 逐 AC 判,score 0.9)→ 回投 `discover.reported{node_id:subtask_3_2_judgments, route:single_bot, bot_id:bot_verifier}`。
- on_event → SUBTASK 翻 `searching→executing`,填 `assignee=bot_verifier` + `dispatch(route="single_bot", target=bot_verifier, {parent_node:"subtask_3_2_judgments", prompt:"终验: 读 spec.goal.acceptances + state.public + 全 subtask state,逐 AC 判"})`(无 GOAL_VERIFY 子节点)。
- 终验 bot `goal-verify skill` 读 `TaskState.public` + 7 个 `NodeState`,逐 AC:
  - AC#1 OUTPUT 产出齐(pass:market/competition/customer/practice 四份报告)✓
  - AC#2 THRESHOLD CAGR/pass 齐_THRESHOLD ✓
  - **AC#3 OUTPUT `dimensions=[market,competition,tech,customer]` → tech 缺** ✗
  - AC#4 一手实践(2_5 访谈)✓ …
- 终验 bot 自调 goal-verify 判 FAIL → 回投 `node.rejected`(带结构化 gap;verifier=终验 bot,非 owner-bot):
```json
{"kind":"node.rejected","payload":{"node_id":"subtask_3_2_judgments",
  "verifier":"bot_verifier","verdict":"fail",
  "gaps":[{"node_id":"subtask_3_2_judgments","round":1,
    "unmet_criteria":["acceptance#3.output.dimensions.tech"],"verdict":"fail",
    "note":"技术演进维度未产出:首轮拆解省略 2_3_tech"}]}}
```
**出参**:`{"accepted":true,"seq":20}`
**落点**:泵 `handle_reject` → `NodeState(subtask_3_2_judgments).gap_records` APPEND;`subtask_3_2_judgments→FAILED`(node.rejected);`loop_round 0→1`;图保持 `RUNNING`(reroute 回 gap,不走 REVIEWING 终态);触发 4.2 reroute。
#### 4.2 reroute-as-mini-decomposition(泵 `handle_reject` 触发,**非 open_reroute_search**)
泵 `on_event(node.rejected)` → `SubtaskExecutor.handle_reject`(终验 rejected→补任务缺口)分支(新增逻辑,进 M1):
```python
# 给根 n_root 追加 reroute 用的 DECOMPOSE 规划子节点(不挂兄弟 BOT_SEARCH)
reroute_dec_id = f"reroute_dec_r{task.loop_round}"               # = "reroute_dec_r1"
add_node(reroute_dec_id, NodeType.DECOMPOSE, PENDING,
         properties={"reroute": True, "trigger": "node_rejected",
                     "round": task.loop_round, "rejected_node": "subtask_3_2_judgments"})
edge(n_root → reroute_dec_id, kind=CONDITIONAL)
# 复用 unmatched 通路:派发 owner plan-skill 补拆,只补 gap 指明的缺失维度
dispatch(route="unmatched", target=owner_bot_id,
         payload={task_id:"T1", parent_node:reroute_dec_id,
                  prompt:"补拆: 终验 node.rejected 缺 acceptance#3.dimensions.tech, 拆出对应子任务补做, 已有维度勿重复"})
```
> I/O 衔接:`reroute_dec_id` 由泵 `add_node` 分配并透传入 dispatch `parent_node`;owner plan-skill 据此回带。父节点=n_root 的 DECOMPOSE 子节点(规划层),非终验 3_2 的子节点。
#### 4.3 owner-bot plan-skill 回投补拆结果(`plan.reported`,复用 Step 3.1 通路)
**API**:`POST /api/tasks/T1/events`
```json
{"kind":"plan.reported","payload":{"parent_node":"reroute_dec_r1","subtasks":[
  {"node_id":"subtask_2_3_tech","depth":1,"depend_on":[],
   "spec":{"metadata":{"id":"st_2_3","title":"存储技术演进分析"},
     "context":{"background":"技术演进是尽调 AC#3 dimensions 的 tech 维度,首轮未拆,终验暴露缺失"},
     "goal":{"objective":"梳理存储技术演进趋势(NVMe-oF/ZNS/3D NAND/CXL)及对格局的影响",
       "acceptances":[{"kind":"output","properties":{"dimension":"tech"}},
                      {"kind":"threshold","properties":{"min_trends":3}}]}
     }}]}}
```
**出参**:`{"accepted":true,"seq":21}`
**落点**:`on_event(plan.reported)` → `add_node(subtask_2_3_tech, depth=1)` + `edge(reroute_dec_r1→subtask_2_3_tech)` + `edge(subtask_2_3_tech→subtask_3_1_aggregate_report, DEPENDENCY)`(补 3_1 缺的 tech 依赖);★**重开 3_1**:`NodeState(subtask_3_1_aggregate_report).status DONE→PENDING`(因新依赖未满足);`reroute_dec_r1→DONE`(已产出补拆);`subtask_2_3_tech` drivable。
#### 4.4 搜推 + 路由 2_3(3_1 重开 → 2_3 drivable → 搜推→动态协作群 grp_tech_research)
- 3_1 重开后 `subtask_2_3_tech` 依赖满足(deps 仅 [],drivable)→ 泵 drive → **enter searching** → `dispatch(route="single_bot", target=owner-bot 搜推能力, {parent_node:"subtask_2_3_tech", prompt:"搜推: 2_3_tech spec+state"})` → discover-skill 拆词 `["技术演进","NVMe-oF","ZNS","3D NAND","CXL"]` → 命中实例 E 的 recommendation:
```json
{"kind":"discover.reported","payload":{"node_id":"subtask_2_3_tech",
  "recommendation":{"route":"group","existing_group_id":null,
    "suggested_participants":["bot_storage_arch","bot_ssd_perf","bot_semi_process"],
    "suggested_driver":"bot_storage_arch","collab_mode":"chat","match_score":0.85}}}
```
- `on_event(discover.reported)` → SUBTASK 翻 `searching→executing`,填 `run_mode=COOP_GROUP, assignee=grp_tech_research(待建)` + `dispatch(route="group", target=None, group=GroupSpec(topic:"存储技术演进", participants:["bot_storage_arch","bot_ssd_perf","bot_semi_process"], driver="bot_storage_arch"), {parent_node:"subtask_2_3_tech", prompt:"执行:2_3 spec+state"})` → BCS `POST /groups` 建群 `grp_tech_research`(assignee 回填)→ 群 `chat.send`(无 `_disp` 子节点)。
- 群 master 聚合 3 bot 执行回投(APPEND,实例 E 内容):
```json
{"kind":"state.updated","payload":{"scope":"subtask_2_3_tech","semantics":"append",
  "patch":{"intermediate_results":[
    {"bot":"bot_storage_arch","content":"NVMe-oF 成主流,Scale-Out 架构;CXL.mem 存储池化试点"},
    {"bot":"bot_ssd_perf","content":"ZNS 兴起,大容量 QLC 量产,EC 软硬件下沉"},
    {"bot":"bot_semi_process","content":"3D NAND 300+ 层,DRAMless SSD 渗透,主控国产化"}]
    }}}
{"kind":"node.accepted","payload":{"node_id":"subtask_2_3_tech","verifier":"grp_tech_research:master"}}
```
**出参**:`seq=22`(state.updated)/ `seq=23`(node.accepted)
**落点**:`NodeState(subtask_2_3_tech)` MERGE/APPEND;群 master 自调 goal-verify 验 2_3.acceptances(dimension=tech + min_trends=3)PASS → `subtask_2_3_tech→DONE`。AC#3 `dimensions.tech` 现已产出。
#### 4.5 3_1 重新汇总(2_3 DONE → 3_1 PENDING→重入 executing→DONE)
- `subtask_2_3_tech DONE` → 3_1 依赖(现 5 叶:2_1/2_2/2_3/2_4/2_5)全满足 → 泵 drive 3_1(汇总 SUBTASK)→ 重入 `executing`(复用首轮 assignee=汇总 bot,或重搜推)→ `dispatch(route="single_bot", target=汇总 bot, {parent_node:"subtask_3_1_aggregate_report", prompt:"汇总: 现全 5 叶 DONE,合并出含 tech 的完整尽调报告"})`。
- 汇总 bot 读 5 叶 `NodeState` 产出 + 2_3 的 3 条 `intermediate_results` + artifacts(技术路线图),合并为完整汇总报告,产出 → **自调 goal-verify** 验 3_1.acceptances(aggregate available leaves / AC#6 报告完整性)PASS → 回投:
```json
{"kind":"state.updated","payload":{"scope":"subtask_3_1_aggregate_report","semantics":"merge",
  "patch":{"execution_context":{"dimensions_done":["market","competition","tech","customer"],"leaves":5}
           }}}
{"kind":"node.accepted","payload":{"node_id":"subtask_3_1_aggregate_report","verifier":"bot_aggregator"}}
```
**落点**:`subtask_3_1_aggregate_report→DONE`(二次);**无系统 `_detect_and_aggregate`/EXEC_AGGREGATE/aggregate_verdict**(汇总 = 汇总 bot 执行+自验的普通 SUBTASK)。
#### 4.6 3_2 重开重验 → 二次终验 PASS(终验 bot 自调 goal-verify)
- 3_1 二次 DONE → 3_2 依赖满足 → ★**重开 3_2**(终验 SUBTASK):`NodeState(subtask_3_2_judgments).status FAILED→PENDING`(reroute 后复驱) → 泵 drive → 重入 `executing`(复用 assignee=终验 bot `bot_verifier`)→ `dispatch(route="single_bot", target=bot_verifier, {parent_node:"subtask_3_2_judgments", prompt:"终验: 复判 all ACs + 完整 state"})`。
- 终验 bot `goal-verify skill` 复判:AC#3 `dimensions` 现 market/competition/tech/customer **四维齐** → 全 AC PASS → 回投 `node.accepted`(非 `goal.verified`;verifier=终验 bot):
```json
{"kind":"node.accepted","payload":{"node_id":"subtask_3_2_judgments",
  "verifier":"bot_verifier","verdict":"pass","acceptances_met":["AC#1","AC#2","AC#3","AC#4","AC#5","AC#6","AC#7"]}}
```
**出参**:`{"accepted":true,"seq":24}`
**落点**:泵 `handle_accept` → `subtask_3_2_judgments→DONE`;依赖传播 → 全子任务 DONE → 图 `RUNNING→DONE`(终验为最后节点,其 accepted 即任务 DONE);`execution_graph.status=DONE`,`loop_round=1`。
#### 4.7 终态确认 + 事件重放
- `GET /api/tasks/T1` → `{"status":"done","loop_round":1,...}`
- `GET /api/tasks/T1/history?after_seq=0` → 24 条事件,seq 单调,作为权威执行轨迹断言依据。
- **断言点**:首轮 seq≤19 含 5 子任务执行 + 3_1 汇总(4 维)+ 3_2 终验;seq=20 `node.rejected`(3_2 终验 bot 自验 FAIL);seq=21 `plan.reported`(补 2_3);seq=22/23 2_3 执行回投;seq=24 `node.accepted`(3_2 终验 bot 二次自验 PASS)。
#### 4.8 I/O 衔接自检
- 终验 FAIL 原因 ← `AC#3.dimensions.tech` 缺 ← 首轮 2_3 未拆(Step 3.1 case 省略);gap `unmet_criteria` ← AC#3 子断言路径;**verifier=终验 bot**(自调 goal-verify),非 owner-bot。
- reroute 入口 ← 泵 `SubtaskExecutor.handle_reject` on `node.rejected`(终验 SUBTASK rejected→补任务缺口)新分支(**非** `open_reroute_search`);`reroute_dec_id` ← `add_node` 分配(DECOMPOSE 节点),`loop_round` 标轮次。
- 补拆复用 ← `plan.reported` 通路(Step 3.1 同款),`parent_node=reroute_dec_r1` 回带;`subtask_2_3_tech.depth=1` ← 根 child。
- 3_1 重开 ← 新增 `2_3→3_1` DEPENDENCY 边 + 3_1(汇总 SUBTASK)`DONE→PENDING` 重入 executing;3_2(终验 SUBTASK)重开 ← 3_1 二次 DONE 后 `FAILED→PENDING` 复驱,重入 executing 复用终验 bot。
- 二次终验 PASS ← AC#3 tech 维度由 2_3 回投补齐;`loop_round=1` ← 一次 reroute;终验 bot 回投 `node.accepted` → 全子任务 DONE → 图 DONE。
#### 4.9 现有实现要改(进 M1,补 §4)
- 泵 `SubtaskExecutor.handle_reject`(on `node.rejected`)新增 reroute-as-mini-decomposition 分支:建 `reroute_dec` DECOMPOSE(`properties.reroute=true`)+ `dispatch(route="unmatched")` 补拆。**删除/替换 `open_reroute_search` 挂兄弟 BOT_SEARCH 旧路径**(若内核仍走该路径,改走此分支)。
- 节点重开语义:`NodeState.status` `DONE→PENDING`(3_1 因新依赖)、`FAILED→PENDING`(3_2 reroute 复驱)需有显式 `reopen_node`/`mark_pending` 服务方法 + 状态机 guard 允许回退。
- 终验 / 汇总 = 普通 SUBTASK:不建 GOAL_VERIFY/EXEC_AGGREGATE 节点,无系统 `aggregate_verdict`;终验 bot / 汇总 bot 自调 goal-verify 验各自 acceptance(汇总 bot 验 "aggregate available leaves",终验 bot 验 "任务级全 AC").dimensions 缺由终验 bot goal-verify 暴露(不被首轮 4 叶汇总 PASS 误导)。
---
### Step 5 — 真实执行推演(角色泳道流程图 + hop-by-hop)
> 完整流程图 + 逐步执行 hop-by-hop 已抽出至 **《任务执行链路汇报.md》**(同目录)§五/§六,含领域模型 + API 设计 + 执行层设计 + 端到端推演;本节仅保留集成用例断言用的 seq 时序表。
#### 5.1 流程图 + hop-by-hop
见《任务执行链路汇报.md》§五(角色泳道流程图)/ §六(逐步执行 Phase A-F)。
#### 5.2 seq 时序断言表(集成用例 `GET /history` 校验用)
| seq | 事件 | 落点/断言 |
|---|---|---|
| 1 | `POST /tasks` 建任务 | T1 drafting + n_recognition DONE |
| 2 | `POST /clarify {confirmed:true}` | DEFINED + n_clarify DONE |
| 3 | `POST /transition {action:start}`(= `execute_task`)→ `plan.reported`(7 subtasks,省略 2_3) | RUNNING + n_root DONE + 7 SUBTASK 落图 + drive 5 根 |
| 4-16 | 5 路子任务 `discover.reported`/`state.updated`/`node.accepted`(并发) | 1_1/2_1/2_2/2_4 DONE;2_5 miss 多轮递归 |
| … | 2_5 孙节点 miss+d≥MAX → HUNG;`escalate_to_bbs` ×4 → BBS bot 认领 `node.accepted` | 2_5 子树 DONE(经任务级 BBS) |
| … | 3_1 汇总 `discover`/`state.updated`/`node.accepted` | 3_1 DONE(4 维,缺 tech) |
| … | 3_2 终验 `discover`/`node.rejected`(AC#3.tech 缺) | 3_2 FAILED + reroute_dec_r1 |
| … | `plan.reported`(补 2_3)→ 2_3 `discover`/`state.updated`/`node.accepted` | 2_3 DONE + 3_1 重开 |
| … | 3_1 二次汇总 `node.accepted` | 3_1 DONE(二次,5 维) |
| 24 | 3_2 二次终验 `node.accepted`(全 AC) | 3_2 DONE → 图 DONE,loop_round=1 |
**断言**:`GET /api/tasks/T1` → `{status:"done", loop_round:1}`;`GET /api/tasks/T1/history?after_seq=0` → 24 条事件,seq 单调;各节点 status/phase 落点与上表一致。
> §3 旧 Mermaid 时序图 / ASCII 生命线图(已过时,含 EXEC_AGGREGATE/GOAL_VERIFY/`_disp` 等旧模型残留)已移除,以《任务执行链路汇报.md》§四泳道流程图为准。
---
## §4 需要做的工作(work breakdown)
### A. 测试 bot profile(§4.1)
- 新建 `scripts/storage_due_diligence_profile/`(或 `tests/.../singlebox_bots/`),含:
  - `bots.json` v1 manifest:owner-bot + 8 个执行 bot,每个带 `domains`(中文摘要)与 `skills`(英文 token,供 `LocalBotCatalog` cover 计算)。
  - 对应 `.standalone-openclaw/workspaces/<bot>/` workspace 目录。
- 在 `LocalBotCatalog`(`core/task/services/bot_catalog.py`)注入该 profile 的 bot 集(替代默认 5bot 协同集),让 `BotDiscoverService.recommend` 能命中 `bot_industry_fetch` 等。
- owner-bot skill token 必须覆盖"任务识别/澄清/规划/验收"等,使其能被路由为 owner。
### B. skill 开发(§4.2 — 工作量最大)
为 owner-bot 与执行 bot 各开发 `SKILL.md`,每个 skill 内含一个"调 task API 的 HTTP client"。建议放在 `tests/community/_flows/task/skills/<skill>/SKILL.md` 或独立 `integration-skills/` 仓目录,经 `openclaw.json` `allowSymlinkTargets` 装入。
| skill | 装入 | 触发 | 行为(调 task API) |
|---|---|---|---|
| `task-recognition` | owner-bot | 关键词("尽调/任务/解决") + 双信号(执行性/可验收性) | `POST /api/tasks/create`(入参对齐 `TaskSpec`,见 §4.C-7) |
| `task-clarify` | owner-bot | 判 spec 字段完备度 | `POST /{id}/clarify`(多轮 + confirmed;方案 B,n_clarify 落图) |
| `task-plan` | owner-bot | 收到拆解 dispatch(route=unmatched,prompt="拆解/补拆:...")+ 用户确认执行(execute_task)| `POST /{id}/transition{action:start}`(= `execute_task`,落根 n_root DECOMPOSE)+ `POST /{id}/events` `plan.reported`(TaskSpec[]);首轮拆 7 子任务(含汇总 3_1、终验 3_2,通常最后);reroute 补拆 2_3_tech |
| `task-exec` + `goal-verify` | **执行 bot / 群 master / 拉群 bot / BBS bot / 终验 bot**(各执行实体) | 收到执行 dispatch(route=single_bot/group,prompt="执行/终验:...") | 执行产出→`POST /{id}/events` `state.updated`(MERGE/APPEND);**自调 goal-verify 验 `TaskSpec.acceptances`**→`node.accepted`(verifier=执行实体)/ `node.rejected` |
> owner-bot **3 个 skill**:recognition / clarify / plan(纯规划,不参与执行/验收)。**goal-verify skill 在所有执行实体上**——执行 + 验收两步同实体(见 §2.0):执行实体产出后自调 goal-verify 验子任务 acceptance;终验子任务(3_2)的执行实体自调 goal-verify 验任务级全 AC。搜推能力(`bot-discover`)由 owner-bot 内置或系统 `BotDiscoverService` 提供——单子任务搜推 dispatch 给 owner 拆词→回投 `discover.reported`(见 §4.C-5)。
每个 skill 的 HTTP client 复用统一 `TaskApiClient`(封装 task 13 端点)。**该 client 在 backend 内实现,参考 `ecb/http_client.py` 的封装模式但不依赖 ecb 模块**(任务代码不能跨模块依赖 ecb);同时复用 backend 自有 `plugin_api/http_client.py` 的 `HttpClient`。
### C. 缺失 API / 服务封装(§4.3 — 阻塞链路)
1. **新事件 kind `plan.reported` / `discover.reported`**(最高优先):`EventKind` 新增两枚;`task_service.on_event` 新增对应解析分支——`plan.reported`→`add_node(TaskSpec[])` 落子任务(Step 3.1 / Step 4.3 补拆复用);`discover.reported`→按 `recommendation.route` 四分支路由(Step 3.2)。不定则 owner-bot plan/discover-skill 无从回投。读 `domain/events.py` + `task_service.on_event` 现状确认无冲突。
2. **`dispatch(route, target, prompt, *, group=None)` 统一派发**:三分支——`single_bot` 走 `ExecutionPort.dispatch_single_bot` 真发 BCN chat.send;`group`(`target=group_id`)复用 `coop_group`;`group`(`target=None`+`GroupSpec{topic,participants,driver,collab_mode}`)调 BCS `POST /groups`(`src/bcs/.../groups` 路由)动态建群 + 群内派发;`unmatched` 走 single_bot 通道派发 owner-bot(拆解/补拆 prompt)。**dispatch 消息 schema 必须带 `parent_node`**(on_event `add_child` 分配的 node_id 透传,skill 回投时回带定位节点)。
3. **`ExecutionPort.dispatch_single_bot` / `coop_group` 真实化**(singlebox profile):让 `LocalBotExecutorPort` 在 singlebox 下**真发 BCN `chat.send`**(而非仅自验收),使执行 bot/群真收到消息。
4. **`GroupDiscoverService.search_by_keyword` 新建**:参考 `BotDiscoverService.search_by_keyword`(`core/bot_public/services/bot_discover_service.py`),调 BCS `/groups/my` 过滤出已有协作群,供 `bot-discover-skill` 调(动态群 `existing_group_id` 判定)。
5. **`BotDiscoverPort.recommend` 改 async**:`dispatch` 搜推给 owner-bot `bot-discover-skill` + 等回投(替代当前 in-process cover);singlebox 保留 in-process cover 作 fallback profile。
6. **`TaskDriverPort` / `BbsExecutor` 真实化**(任务级 BBS 上升,§2.0-7):BBS **不再 piecemeal**——子任务 miss+depth≥MAX → `HUNG`(不立刻 BBS);全可执行子任务 DONE 仍有 HUNG → 图 `HUMAN_REQUIRED` → **用户确认 → 整体任务 `BBS_ACTIVE`** → HUNG 叶子 `escalate_to_bbs(task_id, node_id, spec)`(调 `BbsExecutor.claim` 广场认领)→ BBS bot 认领执行+自调 goal-verify → 解除 HUNG。`NoopTaskDriverPort`→实现 `escalate_to_bbs` + 真实 `dispatch_single_bot`/`coop_group`;`BbsExecutor.claim` 真实化:悬赏 message 经 BCN 发 BBS bot pool,等认领。
7. **`CreateTaskRequest` 契约对齐 `TaskSpec`**(Step 1):吃 `title/background/goal/deliverables/constraints`;`TaskSpecMetadata` 现仅 `id/title`(`summary`/`tags` 已删);与 `clarify` 同走 `_apply_spec_patch`;recognition skill 产出部分 `TaskSpec`(query 能抽全的抽全,不编造)。
8. **删 `init_execution_graph` 批量建图**(方案 B,Step 1/3):节点在动作发生时落图——`n_recognition`@create_task、`n_clarify`@clarify_task(confirmed)、根 `n_root(DECOMPOSE)`@execute_task;**删 `task_service.init_execution_graph`**(line ~728)批量建图逻辑,改由各动作 `add_node` 增量落图。
9. **reroute(终验子任务 node.rejected)补拆**(Step 4.2):终验子任务(3_2)自调 goal-verify 验任务级全 AC → 不达 → `node.rejected` → on_event 建 `reroute_dec(DECOMPOSE, properties.reroute=true,round=loop_round)`+ `dispatch(route="unmatched")` 补拆缺失子任务(如 2_3_tech);**替换/删除 `open_reroute_search` 挂兄弟 BOT_SEARCH 旧路径**。
10. **节点重开语义**(Step 4.3/4.6):`NodeState.status` `DONE→PENDING`(3_1 因新依赖 2_3)、`FAILED→PENDING`(3_2 reroute 复驱)需显式 `reopen_node`/`mark_pending` 服务方法 + 状态机 guard 允许回退(非终态→PENDING)。
11. **验收由执行实体自调 goal-verify**(§2.0-3/4):不再系统 `aggregate_verdict`。汇总(3_1)/终验(3_2)都是普通 SUBTASK——搜推→匹配 bot(汇总 bot/终验 bot)执行 + **该 bot 自调 goal-verify 验 `TaskSpec.acceptances`** → `node.accepted`/`node.rejected`。汇总 bot 的 acceptance 含"维度齐",终验 bot 的 acceptance = "任务目标达成"即验任务级全 AC。系统不验、不 fold AC。
12. **`TaskE2EClient`**(测试用发消息客户端):`send_to_bot` / `send_to_group` / `await_event`,封装 engine chat WS + task history 轮询。**在 backend 内实现,参考 `ecb/http_client.py` 模式但零 ecb 依赖**(任务代码全在 backend,不跨模块 import ecb)。
13. **统一 `SubtaskExecutor` + NodeType 4 种**(§2.0,驱动模型骨架,最高优先):新建 `SubtaskExecutor`(统一一套,按 `(event.kind, phase)` 派发 phase handler,委托策略对象处理 single/group/bbs 执行 + goal-verify 自验)+ `PhaseExecutor`(recognition/clarify/decompose)。搜推/执行/验收/拆解/上升 = SubtaskExecutor **内部 phase**(`NodeState.phase`),由回投 event 驱动转移,**不再建 BOT_SEARCH/DISPATCH/EXEC_ACCEPT 动作子节点**。`models.py` NodeType 收成 **4 种**:`RECOGNITION`/`CLARIFY`/`DECOMPOSE`/`SUBTASK`(删 `BOT_SEARCH`/`DISPATCH`/`EXEC_ACCEPT`/`EXEC_AGGREGATE`/`DECOMPOSITION`/`GOAL_VERIFY`/`EXECUTE_START` 作图节点);`Node` 内嵌 `plan: TaskSpec`;`NodeState` 增 `phase`。把现 `_advance_node`/`_bot_search`/`_decomposition`/`_dispatch` 动作迁入 `SubtaskExecutor` phase handler。
14. **全子任务同构 + 终验=SUBTASK**(§2.0-2/5,修 bug):所有拆出来的子任务(含汇总 3_1、终验 3_2)`NodeType=SUBTASK`,无 kind 区分;终验是 plan-skill 拆出的普通子任务(通常最后一个),执行层不特殊对待。★修当前 `_decomposition` 把所有 children 设 BOT_SEARCH 导致 3_1/3_2 错走搜推的 bug → 现统一 SUBTASK,3_1/3_2 也搜推→匹配(汇总 bot/终验 bot)执行+自验。缺终验后续靠调优 plan-skill 或加固定终验节点(规划层修)。
15. **驱动模型重构:tick 降 watchdog,on_event 泵正向驱动**(§2.0):`_tick` 只对 long-running RUNNING 节点超时探活(WAIT/PROBE/REDRIVE/ESCALATE),**不再正向推进 PENDING 节点**;正向推进交 `on_event` 泵——skill 每次回投 event,泵按 `(node_id, event.kind)` 定位节点 + 派发到 executor phase handler。`waiting_callback` 用 `NodeStatus.RUNNING` + `NodeState.phase` 表达。
16. **`DecomposerPort.decompose_subtasks` 改 async**(对齐 §4.C-5 的 discover):`DECOMPOSE` 节点不再 in-process 调 `decompose_subtasks`,改 `dispatch(route="unmatched",target=owner,"拆解")` 给 owner-bot plan-skill,等 `plan.reported` 回投(singlebox 保留 in-process 作 fallback profile)。与 §4.C-5 合起来,discover/decompose 双 async 化。
### D. 集成用例骨架(§4.4)
- 新建 `src/backend/tests/community/singlebox/test_storage_due_diligence_e2e.py`(或 `scripts/test_singlebox_storage_dd.sh` + pytest 混合)。
- 用例编排:
  1. `singlebox start all` + 注入 storage profile bot 集;
  2. `TaskE2EClient.send_to_bot(owner, "帮我做存储行业尽调")`;
  3. 轮询 `GET /T1/history` 驱动断言(seq 示意,以 history 权威为准):seq1 created(TASK_CREATED,n_recognition DONE)→seq2 clarify_task(n_clarify DONE,DEFINED)→seq3 plan.reported(7 TaskSpec 含汇总 3_1/终验 3_2,2_3 未拆;根 n_root DECOMPOSE 落图)→seq4/5 1_1 搜推+执行回投(单 bot 执行+自调 goal-verify,MERGE)→seq6-11 2_1/2_2/2_4 搜推+执行回投(3 动态群,群 master 自调 goal-verify,APPEND)→seq12-18 2_5 miss 多轮拆解→depth≥MAX HUNG→其它可执行 DONE→图 HUMAN_REQUIRED→用户确认→BBS_ACTIVE→HUNG 叶子广场悬赏→BBS bot 认领执行+自调 goal-verify→解除 HUNG→2_5 DONE→seq19 3_1(汇总 SUBTASK)搜推→汇总 bot 执行+自调 goal-verify→DONE→seq20 3_2(终验 SUBTASK)搜推→终验 bot 自调 goal-verify 验全 AC→AC#3 tech 缺→node.rejected→seq21 reroute:plan.reported(补 2_3_tech)→seq22/23 2_3 动态群 grp_tech_research 执行回投→3_1 重开二次汇总→seq24 3_2 重开→终验 bot 自调 goal-verify 四维齐→node.accepted→全子任务 DONE→任务 DONE;
  4. 终态断言 `GET /T1` status=done, loop_round=1;`state.public` 含 4 维度(market/competition/tech/customer)+ 一手实践产出;
  5. 事件轨迹断言:seq 单调,kind 序列含 `plan.reported`×2(首轮+补拆)、`discover.reported`×N、`node.rejected`(终验首轮)、`node.accepted`×N、`state.updated` MERGE/APPEND;**无 `goal.rejected`/`goal.verified` 独立 event**(终验走 SUBTASK 的 node.rejected/accepted)。
- 关键轮询点:owner-bot skill 异步调 API → 用 `await_event(task_id, expected_kind, timeout)` 等而非 sleep。
- marker:`@pytest.mark.singlebox_e2e`;纳入 `scripts/test_singlebox_coverage_gate.sh`。
### E. 数据与验收(§4.5)
- 复用 briefing 的 6 条 acceptances(3 OUTPUT / 2 THRESHOLD / 1 INVARIANT),`tech` 维度首次缺失是 reroute 触发点。
- 本地 artifact location 用 `oss://bucket/...` 占位或本地 file://;集成用例只校验 State 落点不校验 OSS 真存。
---
## §5 v1 落地顺序(milestone)
1. **M1 契约敲定**(先定再写):`plan.reported`/`discover.reported` 事件 kind(§4.C-1);`dispatch(route,target,prompt,*,group=None)+parent_node` schema(§4.C-2);`CreateTaskRequest` 对齐 `TaskSpec`(§4.C-7);删 `init_execution_graph` 方案 B 落图点(§4.C-8);节点重开语义(§4.C-10);`SubtaskExecutor` Protocol + NodeType 4 种 schema(§4.C-13);执行实体自调 goal-verify 验收契约(§4.C-11)。
2. **M1.5 驱动模型重构**(骨架,先于 skill/真发 BCN):`SubtaskExecutor` + `PhaseExecutor` registry 落地(§4.C-13);`_tick` 降 watchdog + on_event 泵正向驱动(§4.C-15);`DecomposerPort`/`BotDiscoverPort` 双 async 化(§4.C-5/16)。验证:discover/decompose 走 owner-bot skill 异步回投能驱动图前进。
3. **M2 bot profile + catalog**:storage profile bot 集 + `LocalBotCatalog` 注入(§4.1)。验证 `recommend` 能 hit/miss 出单 bot/协作群/自由聊天群,无 cover 时 unmatched。
4. **M3 skill 开发**:owner-bot 3 skill(recognition/clarify/plan)+ 执行实体 task-exec&goal-verify(§4.2)。逐个单测:skill 收到 dispatch 消息后能调对 task API + 回投对应 event kind;执行实体产出后自调 goal-verify 回投 node.accepted/rejected。
5. **M4 ExecutionPort/Driver 真实化**:`dispatch` 三分支 + `dispatch_single_bot`/`coop_group` 真发 BCN + 动态建群(含自由聊天群)(§4.C-2/3);`TaskDriverPort`/`BbsExecutor` 真实化 + **任务级 BBS**(HUNG→HUMAN_REQUIRED→用户确认→BBS_ACTIVE→HUNG 叶子 escalate_to_bbs)(§4.C-6);`GroupDiscoverService.search_by_keyword`(§4.C-4);reroute(终验子任务 node.rejected→补拆)(§4.C-9)。
6. **M5 集成用例组装**:pytest E2E + `TaskE2EClient` + 事件轮询断言(§4.4)。
7. **M6 singlebox 接入**:纳入 `singlebox start` 编排 + coverage gate,一条命令跑通。
---
## §6 风险与待确认
- **risk-1**:openclaw 真实 bot 跑 LLM skill 在 CI 里不稳定/慢。→ 保留 `LocalBotExecutorPort`(self-accept) 作为 fallback profile,用 env 切换"全真 / 半真"。
- **risk-2**:`ExecutionPort` 真实化可能改动 community profile DI 默认行为。→ 新建 singlebox 专用 provider,不动 community 默认。
- **待确认-1**:`ecb/http_client.py` 的具体路径(用于"参考其实现"),但已确认**不依赖 ecb 模块、在 backend 内重写**。
- **待确认-2**:`plan.reported`/`discover.reported` 事件 kind 命名与 payload schema(§4.C-1)。
- **待确认-3**:engine chat WS 给 bot 发消息的精确帧格式(读 `ws_server.py._stream_chat_events`)。
- **待确认-4**:dispatch 消息 `parent_node` 在 BCN `chat.send` payload 中的承载字段(skill 回投回带的契约)。
- **待确认-5**:节点重开状态机 guard 的允许回退路径(`FAILED→PENDING` 是否需进一步限制为仅 reroute 触发)。