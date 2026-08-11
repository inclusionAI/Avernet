# Task 领域模型 + case API 实例化调用链路

> 范围:`src/backend/src/agentclaw/community/core/task/` 任务内核。
> 内容:① 领域模型设计;② 以**存储行业尽调** case 为实例,给出全链路每个 HTTP API 调用的具体入参/出参 JSON、**发起方**、领域字段实例化落点,并在对应 `state.updated` 执行步骤内联 State 字段级 before→after。
> 日期:2026-08-04。

---

## 1. 领域模型设计

### 1.1 聚合根 `Task` —— 身份 + 两副面孔

```
Task(id, user_id, spec, execution_graph, owner_bot_id)
 ├─ spec: TaskSpec                    # 规划面(录入/澄清渐进式补全)
 └─ execution_graph: TaskExecutionGraph | None   # 运行态面(create 即建)
  ★ status / loop_round 是 property 代理 → execution_graph(无存储字段)
  ★ 事件水位 latest_seq 不驻聚合,按需 event_repo.latest_seq(task_id)
```

- `id` = `"task-"+uuid4().hex[:16]`(`TaskService._new_task_id`)
- `execution_graph` = `TaskExecutionGraph(status=DRAFTING)`(create 时即建)
- `status` 读写经 property 落到 `execution_graph.status`(无存储字段)

### 1.2 规划面 `TaskSpec` —— 五要素

| 字段 | 实例化时机 |
|---|---|
| `metadata: TaskSpecMetadata(id, title)` | `create` 建档 |
| `context: TaskContext(background, constraints[])` | `clarify` patch |
| `goal: TaskGoal(objective, acceptances[])` | `clarify` patch(五要素之 goal) |
| `deliverables: Deliverable[]` | `clarify` patch(五要素之 deliverables) |
| `execution: ExecutionMeta` | pre-execution hint(可选,scheduler 可覆盖) |

`_apply_spec_patch` 是唯一写口:`_parse_acceptances/_parse_deliverables/_parse_constraints` 把 dict 多态还原成 dataclass;`AcceptanceCriteria` 经 `kind`+`properties` bag 多态(无子类爆炸)。

### 1.3 运行态面 `TaskExecutionGraph` + 两维度正交

```
TaskExecutionGraph
 ├─ status: GraphStatus          # ★ 任务运行时状态唯一权威(9 态)
 ├─ loop_round: int              # gap 重路由轮次
 ├─ nodes: list[Node]            # 动作维度
 ├─ edges: list[Edge]            # EdgeKind×4(DEPENDENCY/CONDITIONAL/FALLBACK/PARALLEL_SYNC)
 └─ state: TaskState             # 实体维度(SSOT)
      ├─ public: dict            # 任务级公共上下文(MERGE)
      └─ subtasks: dict[node_id, SubtaskState]
```

**两维度正交(核心范式)**:判定动作是 `Node`,翻实体 status 是 `SubtaskState` —— 各落一笔,经 `_sync_subtask_status` 同步。

- **`Node`(动作维度,11 种 NodeType)**:`node_id / spec / status / node_type / run_mode / targets_acceptance / attempted_executors / properties / sub_dag`。控制面(状态/路由历史/旋钮)留 Node。`properties` 默认 `{"retry_count":0,"max_attempts":2,"loop_round":0}`;规划链节点额外挂 `phase_label/task_spec`(副屏画布读)。
- **`SubtaskState`(实体维度)**:`node_id / status / depth / execution_context(MERGE) / intermediate_results(APPEND) / artifacts(APPEND 去重) / gap_records(APPEND)`。数据面留 State。
- **`SubDagRef`**:协作群外部 run 指针(`ref_kind/bcs_run_id/group_id`),**不持**子节点状态;drill-down 经 `SmGraphAdapter` 实时映射。
- **两种递归**:`DECOMPOSITION` 节点(图内分解,带 `depth`,上限 3)vs `SubDagRef`(协作群外部 SM run 指针)。

### 1.4 状态机(三套独立转移表,guard 已强制)

- **GraphStatus**(9 态,3 终态 `DONE/CANCELLED/FAILED`):`DRAFTING→DEFINED→RUNNING→{HUMAN_REQUIRED|REVIEWING|RUNNING自环}; HUMAN_REQUIRED→{BBS_ACTIVE|FAILED}; BBS_ACTIVE→{DONE|FAILED}; REVIEWING→{DONE|RUNNING回gap}`。`RUNNING→RUNNING` = `loop_round++`。pre-BBS goal-FAIL 回 gap;post-BBS(`BBS_ACTIVE`)goal-FAIL → `FAILED` 终态不回环。
- **NodeStatus**(6 态,仅 `SKIPPED` 终态,`DONE` 幂等自环)。accept-fail 与 exec-fail 都落 `FAILED`,靠 `properties['acceptance_result']` 区分。
- guard = `require_graph_transition/require_node_transition`;非法转移抛 `IllegalTransitionError`、事件被拒、状态原地不动。`_advance_phase`/`mark_graph_status`/节点翻态前都调 guard。

### 1.5 事件溯源 + 单写口 `on_event`

- `TaskEvent(task_id, seq, kind, reported, payload, occurred_at)`;`next_seq` 单写者 watermark(首=1,否则 +1,不重用不跳号)。
- `on_event` 是**唯一状态写路径**:append 事件日志 → `_apply_event`(guard+fold)→ save。
- typed 子类(`TaskCreated/TaskClarified/NodeDispatched/NodeAccepted/...`);v2 图操作 kind(`NODE_ADDED/EDGE_ADDED/STATE_UPDATED/EXEC_AGGREGATED/NODE_HANG/BBS_CONFIRMED/HANG_CANCELLED`)仅枚举字面量,走 `kind+payload:dict`。
- `reported=True` 区分 owner-bot SKILL 回投(`NODE_ACCEPTED/REJECTED/FAILED/GOAL_VERIFIED/REJECTED`)vs 系统驱动。
- `TaskRepo`=物化 fold 快照;`TaskEventRepo`=append-only 时间旅行源;`GraphSnapshot`=fold 缓存(断点重跑)。

### 1.6 State fold 语义(`_fold_state`,graph_state_ops L179-207)

`_fold_state` target = `SubtaskState.__dict__` / `state.public`;patch key 必须是 `SubtaskState` 字段名或 `public` 顶层 key。

| semantics | 代码行为 |
|---|---|
| `MERGE` | 逐 key:若 `patch[k]` 与 `target[k]` 都是 dict → `{**target[k], **patch[k]}`(**深合并一层**,非递归);否则直接覆盖该 key |
| `APPEND` | 逐 key:若都是 list → `cur + v` 拼接;`artifacts` 特判:仅当 patch 元素是 `ArtifactRef` 对象才按 name 去重;经 `/events` 回投是 dict → **拼接不去重** |
| `OVERWRITE` | 逐 key 单调覆盖(用于 `depth`/`status`,但两者实际走 `add_node`/`_sync_subtask_status`,不经 state.updated) |

> ⚠ **三个坑点**:① MERGE 只深合并**一层**;② 经 `POST /events` 回投的 `artifacts` 是 dict,APPEND 走拼接**不按 name 去重**;③ `scope=None` 写 `public` 时 patch 应**扁平**传 `{"k":"v"}`,不要再包一层 `{"public":{...}}`(否则落到 `state.public["public"]` 而非顶层)。

> **State 更新通道边界**:`state.updated` 事件只动 `TaskState.public` 与 `SubtaskState.execution_context/intermediate_results/artifacts/gap_records`;`status`/`depth` **不经此通道**(由节点状态事件 fold + `add_node` 落)。

---

## 2. case 逐 API 实例化入参/出参

> case:存储行业尽调 → 目标"产出尽调决策支持报告";6 条验收(`OUTPUT×3 / THRESHOLD×2 / INVARIANT×1`),其中 `OUTPUT{dimensions:[market,competition,tech,customer]}` 的 `tech` 由专题 B 满足(首次缺失 → 触发修正 loop)。
> `task_id` 用真实格式 `task-8a3f2c1b9d0e4756`(下文记 `T1`)。
> **13 个 HTTP 端点 + 1 WS**(前缀 `/api/tasks`)。**唯一状态写口** = `POST /{id}/events` → `TaskService.on_event`(append→guard→fold→save);`/tick` 与 `/start` 的内部图操作(`add_node/init_execution_graph`)直接 mutate + save,**不经事件日志**(fold 由后续 `on_event` 回投补齐)。
> **发起方角色**:owner-bot 的各类 SKILL(recognition/clarify/execute/plan/exec/goal-verify)、执行 bot / 协作群 master、BBS bot、系统 `TaskScheduler`(`_tick` 自驱 + `on_event` 泵)、副屏画布/审计。bot 不直连 task API,经 BCN `chat.event(final)` 回报,由 SKILL/adapter 翻译成 `POST /events` 回投。

### 2.1 阶段 1 — 录入:create + clarify(规划面实例化)

#### ▶ POST /api/tasks/create

**发起方**:owner-bot `task-recognition-skill`(识别任务后建档)  

**入参** `CreateTaskRequest`
```json
{
  "title": "存储行业尽调",
  "background": "AI 基础设施驱动的企业级与数据中心存储行业最新变化、竞争格局与进入机会"
}
```
**出参** `TaskCreatedResponse`
```json
{"task_id": "task-8a3f2c1b9d0e4756", "status": "drafting", "seq": 1}
```
**字段实例化**(`TaskService.create`,L132)
- `Task(id="T1", user_id="", execution_graph=TaskExecutionGraph(status=DRAFTING))`
- `spec=TaskSpec(metadata=TaskSpecMetadata(id="T1", title="存储行业尽调"))`
- `spec.context.background="AI 基础设施..."`
- 事件 `TASK_CREATED` seq=1(payload `{"title":...}`);`PanelMessage(component="taskPanel.TaskWorkflowView", params={task_id})` 弹副屏

#### ▶ POST /api/tasks/T1/clarify(逐轮 amend,`confirmed=false`)

**发起方**:owner-bot `task-clarify-skill`(逐轮补五要素)

**入参** `ClarifyTaskRequest`
```json
{
  "patch": {
    "background": "产出尽调报告,支撑投资判断",
    "goal": {
      "objective": "产出存储行业尽调决策支持报告",
      "acceptances": [
        {"kind": "output",     "properties": {"dimension": "investment_value"}},
        {"kind": "output",     "properties": {"dimension": "tracking_targets"}},
        {"kind": "output",     "properties": {"dimensions": ["market","competition","tech","customer"]}},
        {"kind": "threshold",  "properties": {"min_count": 5}},
        {"kind": "invariant",  "properties": {"structure": ["evidence","risk","open_question"]}},
        {"kind": "threshold",  "properties": {"ratio": 0.3, "recency_months": 3}}
      ]
    },
    "deliverables": [{"type": "report", "location": "尽调报告.md"}],
    "constraints": [{"kind": "soft", "text": "30% 关键判断来自近 3 月"}]
  },
  "confirmed": false
}
```
**出参** `TaskDetailResponse`(节选 spec)
```json
{
  "task_id": "T1", "status": "drafting", "loop_round": 0,
  "spec": {
    "metadata": {"id": "T1", "title": "存储行业尽调"},
    "context": {"background": "产出尽调报告,支撑投资判断",
                "constraints": [{"kind": "soft", "text": "30% 关键判断来自近 3 月"}]},
    "goal": {"objective": "产出存储行业尽调决策支持报告",
             "acceptances": [
               {"kind": "output",    "properties": {"dimension": "investment_value"}},
               {"kind": "output",    "properties": {"dimension": "tracking_targets"}},
               {"kind": "output",    "properties": {"dimensions": ["market","competition","tech","customer"]}},
               {"kind": "threshold", "properties": {"min_count": 5}},
               {"kind": "invariant", "properties": {"structure": ["evidence","risk","open_question"]}},
               {"kind": "threshold", "properties": {"ratio": 0.3, "recency_months": 3}}
             ]},
    "deliverables": [{"type": "report", "location": "尽调报告.md"}]
  },
  "execution_graph": {"status": "drafting", "loop_round": 0, "nodes": [], "edges": [], "state": {"public": {}, "subtasks": {}}}
}
```
**字段实例化**(`_apply_spec_patch`,L635):`spec.context.constraints[Constraint(SOFT,...)]`、`spec.goal=TaskGoal(objective, acceptances[6 个 AcceptanceCriteria])`(`kind`→枚举,`properties` 整包)、`spec.deliverables=[Deliverable(type=REPORT,...)]`。事件 `TASK_CLARIFIED` seq=2;**status 仍 DRAFTING**(`confirmed=false` 不迁态)。

#### ▶ POST /api/tasks/T1/clarify(锁定,`confirmed=true`)

**发起方**:owner-bot `task-clarify-skill`(用户确认后锁定 spec)

**入参**`{"patch": {}, "confirmed": true}`
**出参**(节选)`{"task_id": "T1", "status": "defined", "loop_round": 0, ...}`
**字段实例化**:`require_graph_transition(DRAFTING→DEFINED)` guard → `execution_graph.status=DEFINED`(spec 冻结);事件 `TASK_CLARIFIED` seq=3。

---

### 2.2 阶段 2 — 启动:start(执行图实例化 + 触发运行期规划)

#### ▶ POST /api/tasks/T1/start

**发起方**:owner-bot `task-execute`(approve 委派)→ 系统响应 `TaskScheduler.start`

**入参**:无 body。**出参** `TaskDetailResponse`(节选 `execution_graph`)
```json
{
  "task_id": "T1", "status": "running", "loop_round": 0,
  "execution_graph": {
    "status": "running", "loop_round": 0,
    "nodes": [
      {"node_id": "n_recognition", "spec": "任务识别: 存储行业尽调", "status": "done", "node_type": "recognition",
       "properties": {"phase_label": "任务识别", "task_title": "存储行业尽调", "retry_count": 0, "max_attempts": 2, "loop_round": 0}},
      {"node_id": "n_clarify", "spec": "任务明确: 产出存储行业尽调决策支持报告", "status": "done", "node_type": "clarify",
       "properties": {"phase_label": "任务明确",
         "task_spec": {"objective": "产出存储行业尽调决策支持报告", "background": "产出尽调报告...",
                       "constraints": [{"kind":"soft","text":"30% 关键判断来自近 3 月"}],
                       "deliverables": [{"type":"report","location":"尽调报告.md"}],
                       "acceptances": [{"kind":"output","properties":{"dimension":"investment_value"}}, "..."]},
         "retry_count": 0, "max_attempts": 2, "loop_round": 0}},
      {"node_id": "n_execute_start", "spec": "确认开始执行", "status": "done", "node_type": "execute_start",
       "properties": {"phase_label": "确认开始执行", "retry_count": 0, "max_attempts": 2, "loop_round": 0}},
      {"node_id": "n_bot_search", "spec": "产出存储行业尽调决策支持报告", "status": "pending", "node_type": "bot_search",
       "properties": {"retry_count": 0, "max_attempts": 2, "loop_round": 0}}
    ],
    "edges": [
      {"edge_id": "e-n_recognition-n_clarify",       "from_node": "n_recognition",  "to_node": "n_clarify",      "kind": "dependency"},
      {"edge_id": "e-n_clarify-n_execute_start",      "from_node": "n_clarify",      "to_node": "n_execute_start","kind": "dependency"},
      {"edge_id": "e-n_execute_start-n_bot_search",   "from_node": "n_execute_start","to_node": "n_bot_search",  "kind": "dependency"}
    ],
    "state": {"public": {}, "subtasks": {
      "n_recognition":  {"node_id": "n_recognition",  "status": "done", "depth": 0},
      "n_clarify":      {"node_id": "n_clarify",      "status": "done", "depth": 0},
      "n_execute_start":{"node_id": "n_execute_start","status": "done", "depth": 0},
      "n_bot_search":   {"node_id": "n_bot_search",   "status": "pending", "depth": 0}}}
  }
}
```
**`/start` 内部**(start 触发执行):`guard DEFINED→RUNNING` → `init_execution_graph`(建规划链 + 根 BOT_SEARCH + 自 save)→ `mark_graph(RUNNING)` → **推进执行(搜推→路由→分发)**:根 `n_bot_search` 搜推 → miss → 落 `DECOMPOSITION` → 发消息给 owner-bot(异步,等回投)→ start 返回。推进细节见 2.3。

> **start 与 tick 分工(不混用)**:start 是执行**同步触发**,推进到首个异步边界(发消息等回投)即返回;**tick 是异步定时 harness**,在 bot/skill 回投 events 后(`on_event` 泵)或定时器周期触发,推进图中已落图的 PENDING 节点(搜推→路由→分发)、看门狗探活、FAILED 重试、聚合触发、终验触发。两者用同一套推进 body,触发时机不同:start 首批、tick 后续。
> **驱动不变量**:start/tick 只驱动**图中已有**的 PENDING 节点;新节点是 `add_child` 副作用,落图后由**后续 tick/回投** 驱动。

建图后出参中每个节点的执行者:

- `n_recognition`(RECOGNITION, DONE):create 时 owner-bot `task-recognition-skill` 已执行,落图即 DONE,不再驱动。
- `n_clarify`(CLARIFY, DONE):clarify 时 owner-bot `task-clarify-skill` 已执行,落图即 DONE,不再驱动。
- `n_execute_start`(EXECUTE_START, DONE):start 时系统 `TaskScheduler` 已执行(DEFINED→RUNNING),落图即 DONE,不再驱动。
- `n_bot_search`(BOT_SEARCH, PENDING):**start 触发(首批)** → `_bot_search` → 调 `BotDiscoverPort`(系统侧 `BotDiscoverService` 本地关键词 cover 搜推);hit → 落 `DISPATCH`(驱动 → 发消息 query=需求+执行上下文 给 bot/群)/ miss 且 depth<3 → 落 `DECOMPOSITION`(驱动 → 发消息 query=需求+执行上下文+拆解上下文 给 owner-bot `task-plan-skill` 回投 `SubTaskSpec[]` 落 children)/ miss 且 depth≥3 → 挂起等人确认升 BBS。children `p1/p2/p3`(同 BOT_SEARCH)由后续 **tick harness** 驱动,路由逻辑同。

> **流程串通**:`/start` 后系统自动按 `task.spec` 调搜推(`BotDiscoverPort`,系统侧)→ 按结果路由:hit → 落 `DISPATCH` 发消息给 bot/群 / miss → 落 `DECOMPOSITION` 发消息给 owner-bot `task-plan-skill` 回投 `SubTaskSpec[]` 递归拆解 / 触上限 → 挂起升 BBS。**owner-bot 不参与搜推,只参与分解与验收。**

---

### 2.3 阶段 3 — 运行期规划 + 阶段一 SINGLE_BOT

> **start 与 tick 分工(不混用)**:start 是执行**同步触发**,自己推进(搜推→路由→分发),到首个异步边界(发消息等回投)即返回;**tick 是异步定时 harness 方法**(回投后 `on_event` 泵 / 定时器周期触发),推进图中已落图 PENDING 节点(搜推→路由→分发)、看门狗探活、FAILED 重试、聚合触发、终验触发。
> **驱动不变量**:start/tick 只驱动 `TaskExecutionGraph` 中**已落图**的 PENDING 节点;新节点是 `add_child` 副作用,落图后由**后续 tick/回投** 驱动。
> **命中/未命中对称模型**(搜推由系统驱动调 `BotDiscoverPort`,系统侧 `BotDiscoverService` 实现,非 owner-bot):
> - **命中** → `add_child(DISPATCH)` 落 topo → 驱动 → **发消息(query=需求+执行上下文)给 bot/群** 跑子任务
> - **未命中** → `add_child(DECOMPOSITION)` 落 topo → 驱动 → **发消息(query=需求+执行上下文+拆解上下文)给 owner-bot `task-plan-skill`** → 回投 `SubTaskSpec[]` → 系统落 children
> - **未命中 + depth≥3** → `_set_hung` 挂起等人确认升 BBS

#### ▶ start 同步推进首批执行(根 `n_bot_search` 搜推 miss → 落 `DECOMPOSITION` → 发消息 owner-bot)

**发起方**:owner-bot `task-execute`(approve 委派)→ 系统 `TaskScheduler.start` 同步推进首批
**出参**:见 2.2 start 出参(`status:running`)
**推进 body**(start 内,搜推→路由→分发,到首个异步边界即返回):系统调 `BotDiscoverPort.recommend(task.spec)` → miss(整体不可单点)→ `add_child(n_bot_search_dec, DECOMPOSITION)` 落 topo + 根 DONE → 推进 `n_bot_search_dec`:系统 `retrieve_state` 组装 query=需求+执行上下文+拆解上下文 → **发消息给 owner-bot `task-plan-skill`**(异步,等回投)→ start 返回。

> owner-bot 回投 `SubTaskSpec[]`(经 `/events`,p1 行业全貌 / p2 深度专题 / p3 BBS 悬赏,`depth=1`),系统 `add_node` 落 children BOT_SEARCH + `DECOMPOSITION` DONE —— miss 与 hit 对称:都发消息、都回投、都落 topo;区别只是执行方(bot/群 跑子任务 vs owner-bot 回投拆解方案)。

#### ▶ tick harness 驱动 children(回投后 `on_event` 泵 / 定时器触发 `tick` 方法)

**发起方**:系统 `TaskScheduler.tick` 方法(异步定时 harness,与 start 不混用)
**出参**`{"task_id":"T1","action":"ticked","progressed":true,"status":"running"}`
**推进 body**(tick 逐个驱动已落图 PENDING 节点):
- `p1` BOT_SEARCH:系统 `BotDiscoverPort.recommend(p1 spec)` → hit `bot_industry_fetch`(C1)→ `add_child(p1_disp, DISPATCH, executor="bot_industry_fetch")` 落 topo + `p1` DONE。
- `p1_disp` DISPATCH:`claim_node`(`PENDING→RUNNING` + `assignee=bot_industry_fetch` + `AttemptedRecord(paradigm=SINGLE_BOT, round=1, trigger=ROUTED)`)+ `_emit(NODE_RUNNING)` → **seq=4**;系统 `retrieve_state(p1_disp)` 组装 query=需求+执行上下文 → `ExecutionPort.dispatch_single_bot` → BCN `chat.send`(query 内嵌上下文)给 `bot_industry_fetch`(异步)→ tick 返回。
- (`p2` miss → 同款分解 → 发消息 owner-bot;`p3` → BBS 上升;各由后续 tick/回投推进)

#### ▶ GET /api/tasks/T1/nodes/p1_disp(bot 读面)

**发起方**:执行 bot `bot_industry_fetch`(query 已内嵌执行上下文;GET 为补充拉详细验收依据)

**出参** `TaskNodeDetailView`
```json
{
  "node_id": "p1_disp", "display_name": "dispatch→bot_industry_fetch", "status": "running",
  "sub_status": "idle", "attempt": 1, "assignee": "bot_industry_fetch",
  "run_mode": "single_bot", "collab_mode": null,
  "attempted_executors": [
    {"executor_id": "bot_industry_fetch", "paradigm": "single_bot", "round": 1,
     "route_class": null, "trigger": "routed", "outcome": null, "at": null, "note": ""}],
  "artifacts": [], "acceptance_result": null, "targets_acceptance": [],
  "instruction": null, "sub_dag_ref": null,
  "properties": {"retry_count": 0, "max_attempts": 2, "loop_round": 0}
}
```
bot 另调 `GET /api/tasks/T1` 拿 `spec.goal.acceptances[6]` 作为验收依据(同一发起方)。

#### ▶ POST /api/tasks/T1/events(bot 产出回投 — state.updated, MERGE)

**发起方**:执行 bot `bot_industry_fetch` 经 `task-exec-skill`/adapter 翻译 BCN `chat.event(final)` 回投

**入参** `EventReportRequest`
```json
{
  "kind": "state.updated", "seq": null,
  "payload": {
    "scope": "p1_disp",
    "patch": {
      "execution_context": {"产业链": "上游控制器/SSD颗粒/主控芯片;下游阵列/服务器/云", "龙头": "三星/WDC/希捷/长江存储"},
      "artifacts": [{"name": "产业链地图", "location": "oss://bucket/T1/p1/industry_map.json", "type": "data"}]
    },
    "semantics": "merge"
  }
}
```
**出参** `EventReportResponse`:`{"task_id": "T1", "accepted": true, "seq": 5, "note": ""}`

**State 字段级更新**(`_fold_state(scope="p1_disp", MERGE)` → `SubtaskState(p1_disp)`):

| 字段 | before | after |
|---|---|---|
| `execution_context: dict` | `{}` | `{"产业链":"上游控制器/SSD颗粒/主控芯片;下游阵列/服务器/云","龙头":"三星/WDC/希捷/长江存储"}` |
| `artifacts: list` | `[]` | `[{"name":"产业链地图","location":"oss://...","type":"data"}]`(MERGE 下 list 走"否则覆盖该 key"→ 整替) |
| `status` | `running`(NODE_RUNNING fold,非此事件) | `running`(不动) |

> `execution_context` 是 dict → MERGE 生效;`artifacts` 是 list → MERGE 走覆盖分支**整替**(追加须用 APPEND)。Scheduler.`on_event`:非 NODE_FAILED → 不泵 tick。
> **产出落 State 作下游上下文**:`execution_context`/`intermediate_results`/`artifacts` 进 `SubtaskState`,经 `retrieve_state(scope)`(`public`+本 subtask 全字段快照)作为下游分解/执行/reroute 的统一上下文读口 —— 这是 skill 执行结果能被下一步消费的链路。

#### ▶ POST /api/tasks/T1/events(bot 自验收 — node.accepted)

**发起方**:执行 bot `bot_industry_fetch`(task-exec-skill 自验收 verdict=PASS 回投)

**入参**`{"kind": "node.accepted", "payload": {"node_id": "p1_disp", "verifier": "bot_industry_fetch"}}`
**出参**`{"task_id": "T1", "accepted": true, "seq": 6, "note": ""}`
**字段实例化**(`_apply_event` NODE_ACCEPTED,L524):`require_node_transition(RUNNING→DONE)` → `Node(p1_disp).status=DONE`、`properties["acceptance_result"]="pass"`;`_sync_subtask_status` → `SubtaskState(p1_disp).status=DONE`。→ p1 闭合,p2 解锁。

> **验收 verdict 落点(PASS)**:PASS verdict 只落 `Node.properties["acceptance_result"]` + 翻 `Node/SubtaskState.status=DONE`,**不进 State 内容字段** —— 闭合即终态,无需作为下游上下文(避免污染)。**FAIL verdict** 才结构化进 `SubtaskState.gap_records`(`unmet_criteria`+`verdict`)作为 reroute 拆解上下文(见 2.6 gap_records)。

---

### 2.4 阶段 4 — 阶段二 COOP_GROUP(动态拉群 + 群 master 回投 + EXEC_AGGREGATE)

#### ▶ POST /api/tasks/T1/tick(p2 miss → 分解 4 专题,首次漏 B)

**发起方**:系统 `TaskScheduler._tick`(推进 p2 的 BOT_SEARCH)

**出参**`{"task_id":"T1","action":"ticked","progressed":true,"status":"running"}`
**驱动 body**:`p2` 搜推 miss(四维度无单群 cover)→ `add_child(p2_dec, DECOMPOSITION)` 落 topo → 后续 tick 驱动 `p2_dec`:系统 `retrieve_state(p2_dec)` 组装 query=需求+执行上下文+拆解上下文 → 发消息给 owner-bot `task-plan-skill` → 回投 `SubTaskSpec[a_search,c_search,d_search]` `depth=2`(**刻意遗漏专题 B**)→ 系统落 3 个 children `BOT_SEARCH`。各 child 后续 tick 搜推:`a_search` 命中群候选 `[bot_market_demand,bot_capital_trend]`(C3)→ 落 `a_disp`(COOP_GROUP)→ `_dispatch` 发消息 query=需求+执行上下文 → `ExecutionPort.coop_group` → BCN 建群 `grp_market_research`(CHAT)→ `NODE_RUNNING` seq=7。

#### ▶ POST /api/tasks/T1/events(群 master 聚合回投 — state.updated, APPEND)

**发起方**:市场研究群 master `task-exec-skill`(聚合群内两 bot 产出后回投)

**入参**
```json
{
  "kind": "state.updated",
  "payload": {
    "scope": "a_disp",
    "patch": {
      "intermediate_results": [
        {"agent": "bot_market_demand", "市场模型": "global SSD 市场规模模型 + 周期判断", "规模": "约 1200亿 USD"},
        {"agent": "bot_capital_trend", "资本周期": "资本开支上行 + AI 新增需求结构倾斜存储"}
      ],
      "artifacts": [{"name": "市场规模模型", "location": "oss://bucket/T1/p2a/market_model.xlsx", "type": "data"}]
    },
    "semantics": "append"
  }
}
```
**出参**`{"task_id":"T1","accepted":true,"seq":8,"note":""}`

**State 字段级更新**(`_fold_state(scope="a_disp", APPEND)` → `SubtaskState(a_disp)`):

| 字段 | before | after |
|---|---|---|
| `intermediate_results: list[dict]` | `[]` | `[{agent:bot_market_demand,市场模型,规模},{agent:bot_capital_trend,资本周期}]`(两条拼接) |
| `artifacts: list` | `[]` | `[{name:市场规模模型,location,type:data}]`(dict 拼接**不去重**) |

#### ▶ POST /api/tasks/T1/events(群 master 验收 — node.accepted)

**发起方**:市场研究群 master `task-exec-skill`(群聚合验收 verdict=PASS)

**入参**`{"kind":"node.accepted","payload":{"node_id":"a_disp","verifier":"群master(market研究群)"}}`
**出参**`{"task_id":"T1","accepted":true,"seq":9,"note":""}` → `a_disp` Node+SubtaskState DONE。专题 C(`bot_supply_chain` 单 bot 自验)、专题 D(`grp_customer_analysis` 3 bot CHAT)同形,各 leaf DONE(seq 10–15 略)。

#### ▶ POST /api/tasks/T1/tick(触发阶段二聚合 — EXEC_AGGREGATE)

**发起方**:系统 `TaskScheduler._tick` 扫图触发 `_detect_and_aggregate`

**出参**`{"task_id":"T1","action":"ticked","progressed":true,"status":"running"}`
**内部字段实例化**(`_detect_and_aggregate`,L366):扫 DONE 的 `p2_dec`(DECOMPOSITION)→ 后代 DISPATCH 叶 `[a_disp,c_disp,d_disp]` 全 DONE 且无 EXEC_AGGREGATE 子 → `add_child(p2_dec_agg, EXEC_AGGREGATE)` + `aggregate_verdict(p2_dec.targets_acceptance, [PASS×3])`=PASS → `Node(p2_dec_agg).status=DONE` + `SubtaskState DONE` + `properties.acceptance_result="pass"`。⚠ 仅 market/competition/customer 三维度闭合,**tech 缺失**(终验 FAIL 伏笔)。此步内部 fold 不经 on_event。

---

### 2.5 阶段 5 — 阶段三 BBS(广场认领 + 回投)

#### ▶ POST /api/tasks/T1/tick(p3 → BBS 上升)

**发起方**:系统 `TaskScheduler._tick`(推进 p3)→ `BbsExecutor.claim`(广场自主认领,无 tick)

内部:`BBS_DISPATCH` → `TaskDriverPort.escalate_to_bbs` → `BbsExecutor.claim` → `chat.send` 给 `bot_ai_train_engineer`。

#### ▶ POST /api/tasks/T1/events(BBS bot 回投 ×2,并发两悬赏)

**发起方**:BBS bot `bot_ai_train_engineer` 经 `BbsExecutor.post_progress` 回投(悬赏2 由 `bot_procurement_staff` 并发)

**入参 1**(悬赏1 产出)
```json
{"kind": "state.updated", "payload": {"scope": "bbs_p3_1", "semantics": "merge",
  "patch": {"execution_context": {"一线实践": "AI训练集群存储架构瓶颈:NVMe-oF + 分布式EC", "访谈对象": "bot_ai_train_engineer"},
            "artifacts": [{"name":"AI训练存储瓶颈访谈","location":"oss://...","type":"data"}]}}}
```
**入参 2**`{"kind":"node.accepted","payload":{"node_id":"bbs_p3_1","verifier":"bot_ai_train_engineer"}}`
**出参**:`accepted:true, seq:16/17`(悬赏2 `bot_procurement_staff` 并发,seq 18/19)。

**State 字段级更新**(`SubtaskState(bbs_p3_1)`,MERGE):

| 字段 | before | after |
|---|---|---|
| `execution_context` | `{}` | `{"一线实践":"AI训练集群存储架构瓶颈:NVMe-oF + 分布式EC","访谈对象":"bot_ai_train_engineer"}` |
| `artifacts` | `[]` | `[{name:AI训练存储瓶颈访谈,...}]`(MERGE 下 list 整替) |

---

### 2.6 阶段 6 — 终验 FAIL → 回 gap reroute → 补做专题 B → 二次终验(多轮 loop)

#### ▶ POST /api/tasks/T1/tick(触发终验 — `_maybe_goal_verify`)

**发起方**:系统 `TaskScheduler._tick`(全图闭合时触发终验判定)

内部(L430):全图无 PENDING/RUNNING/FAILED + `status=RUNNING` → 读 `task.spec.goal.acceptances[6]` → `aggregate_verdict` 检测 **AC#3 `dimensions` 缺 `tech`** → FAIL(tick 内不硬落,等 skill 回投)。

#### ▶ POST /api/tasks/T1/events(goal.rejected — 回 gap)

**发起方**:task-owner `goal-verify-skill`(读 State 聚合判任务 FAIL 后回投)

**入参**`{"kind": "goal.rejected", "payload": {"verifier": "task-owner", "verdict": "fail", "reason": "缺技术演进维度(tech)"}}`
**出参**`{"task_id":"T1","accepted":true,"seq":20,"note":""}`
**字段实例化**(`_apply_event` GOAL_REJECTED→`_apply_goal_verdict(fail)`,L615):pre-BBS(非 BBS_ACTIVE)→ `require_graph_transition(REVIEWING→RUNNING)` ★**回 gap 自环 = loop 落点**;`execution_graph.status=RUNNING`;事件 seq=20。

> **终验 verdict 落点(FAIL)**:`reason`("缺技术演进维度")只存 event payload,**不直接落 State**;由 task-plan-skill 转译成结构化 `gap_records.unmet_criteria`(下一步 `update_state`)才进 State,供 reroute 拆解作为上下文。pre-BBS FAIL 回 gap;post-BBS(`BBS_ACTIVE`)FAIL → `FAILED` 终态不回环。

#### ▶ 写 gap_records + open_reroute_search(内部,无独立 HTTP)

**发起方**:owner-bot `task-plan-skill`(写 gap 记录 + 调内部 `open_reroute_search` 挂 reroute 兄弟 BOT_SEARCH;graph_state_ops L107,无 HTTP 端点)

终验 FAIL 后,task-plan-skill 经内部 `update_state` 写 gap 并挂兄弟 BOT_SEARCH:

**update_state(p2_dec, APPEND gap_records)**
```json
{"kind":"state.updated","payload":{"scope":"p2_dec","semantics":"append","patch":{
  "gap_records":[{"node_id":"p2_dec","round":1,"unmet_criteria":["output.dimensions.tech"],"verdict":"fail","at":""}]}}}
```

**State 字段级更新**(`SubtaskState(p2_dec)`,APPEND):

| 字段 | before | after |
|---|---|---|
| `gap_records: list[GapRecord]` | `[]` | `[{node_id:p2_dec, round:1, unmet_criteria:["output.dimensions.tech"], verdict:fail, at:""}]` |

> 这条 `gap_records` 就是**验收 FAIL 的结构化判断结果落 State**,作为 reroute 拆解的上下文输入;`open_reroute_search` 调用方用 `retrieve_state(p2_dec)` 读出它,组装进 `gap_spec`(兄弟 `p2_tech_search` 的 spec),驱动 reroute。
> `open_reroute_search` 还把原缺失节点标 `superseded`(FAILED→DONE,状态机合法),解锁后续 `_maybe_goal_verify` 的"有 FAILED 不终验" guard。

#### ▶ POST /api/tasks/T1/tick(推进 p2_tech_search → 命中技术群 → DISPATCH b_disp)

**发起方**:系统 `TaskScheduler._tick`(推进 reroute 兄弟 BOT_SEARCH)

**出参**`{"task_id":"T1","action":"ticked","progressed":true,"status":"running"}`
**驱动 body**:`p2_tech_search` 搜推 hit `[bot_storage_arch,bot_ssd_perf,bot_semi_process]`(C3)→ `add_child(b_disp, DISPATCH, COOP_GROUP)` 落 topo → 后续 tick 驱动 `b_disp`:`claim(群master)` + `retrieve_state(b_disp)` 组装 query=需求+执行上下文 → `ExecutionPort.coop_group` → BCN 建群 `grp_tech_research`(CHAT)发消息给三 bot → `NODE_RUNNING` seq=21。

#### ▶ GET /api/tasks/T1/nodes/b_disp

**发起方**:技术研究群 master / 群内 bot(派发后拉本节点上下文)

```json
{"node_id":"b_disp","display_name":"dispatch→bot_storage_arch","status":"running","run_mode":"coop_group","collab_mode":"chat","attempt":1,"assignee":"bot_storage_arch","attempted_executors":[{"executor_id":"bot_storage_arch","paradigm":"coop_group","round":1,"trigger":"routed","outcome":null}],"properties":{"retry_count":0,"max_attempts":2,"loop_round":0}}
```

#### ▶ POST /api/tasks/T1/events(技术研究群产出 — state.updated, APPEND)

**发起方**:技术研究群 master `task-exec-skill`(聚合群内三 bot 产出后回投)

**入参**
```json
{"kind":"state.updated","payload":{"scope":"b_disp","semantics":"append","patch":{
  "intermediate_results":[
    {"agent":"bot_storage_arch","技术路线":"NVMe-oF + ZNS SSD"},
    {"agent":"bot_ssd_perf","性能":"顺序读 14GB/s,QLC 耐久性优化"},
    {"agent":"bot_semi_process","工艺":"176层 3D NAND / CBA"}],
  "artifacts":[{"name":"技术路线图","location":"oss://bucket/T1/p2b/tech_roadmap.json","type":"data"}]}}}
```
**出参**`{"accepted":true,"seq":22}`

**State 字段级更新**(`SubtaskState(b_disp)`,APPEND):

| 字段 | before | after |
|---|---|---|
| `intermediate_results` | `[]` | `[3 条:{agent:bot_storage_arch,技术路线},{agent:bot_ssd_perf,性能},{agent:bot_semi_process,工艺}]` |
| `artifacts` | `[]` | `[{name:技术路线图,location:oss://...,type:data}]`(dict 拼接不去重) |

#### ▶ POST /api/tasks/T1/events(群 master 验收 + 报告 merge public)

**发起方**:技术研究群 master `task-exec-skill`(群聚合验收 PASS + 报告章节合并到任务级 public)

**入参 1**`{"kind":"node.accepted","payload":{"node_id":"b_disp","verifier":"群master(技术研究群)"}}` → 出参 seq=23 → `b_disp` DONE。

**入参 2**(报告合并到 `TaskState.public`,**扁平 patch**,scope=null)
```json
{"kind":"state.updated","payload":{"scope":null,"semantics":"merge","patch":{
  "技术演进章节":"NVMe-oF/ZNS/176层 NAND,见 oss://bucket/T1/p2b/tech_roadmap.json",
  "尽调结论":"四维度齐:市场/竞争/技术/客户"}}}
```

**State 字段级更新**(`TaskState.public`,MERGE 扁平):

| key | before | after |
|---|---|---|
| `技术演进章节` | (无) | `"NVMe-oF/ZNS/176层 NAND,见 oss://bucket/T1/p2b/tech_roadmap.json"` |
| `尽调结论` | (无) | `"四维度齐:市场/竞争/技术/客户"` |

> ⚠ 若 patch 包成 `{"public":{...}}` 会落到 `state.public["public"]` 而非顶层 —— 必须扁平传。

#### ▶ POST /api/tasks/T1/tick(二次终验 PASS — `_maybe_goal_verify`)

**发起方**:系统 `TaskScheduler._tick`(全图再次闭合时触发终验,这次 PASS)

**出参**`{"task_id":"T1","action":"ticked","progressed":true,"status":"done"}`
**内部字段实例化**(L430):全图无未闭合 → `aggregate_verdict(acceptances[6], [...])` 四维度齐 + ≥5 判断 + 结构 + 时效 → **PASS**;`_advance(REVIEWING)` → `mark_graph_status(DONE)`(guard)→ `mark_terminal(DONE)`;`execution_graph.status=DONE`。(若 skill 回投 `goal.verified` → seq=24 → `_apply_goal_verdict(pass)`→`_advance_phase(DONE)`。)

#### ▶ GET /api/tasks/T1(终态确认)

**发起方**:owner-bot / 副屏画布(终态确认 + 渲染终态 DAG)

**出参**(节选)
```json
{"task_id":"T1","status":"done","loop_round":1,
 "execution_graph":{"status":"done","loop_round":1,
   "nodes":["...n_recognition✓,n_clarify✓,n_execute_start✓,n_bot_search✓,p1✓,p2✓,p3✓,p1_disp✓,a_disp✓,c_disp✓,d_disp✓,p2_dec_agg✓,p2_tech_search✓,b_disp✓..."],
   "state":{"public":{"技术演进章节":"...","尽调结论":"..."},"subtasks":{"...":"各 leaf status:done"}}}}
```

#### ▶ GET /api/tasks/T1/history?after_seq=0(事件日志重放)

**发起方**:副屏画布 / owner / 审计(事件日志增量重放,`after_seq` 跟随)

**出参** `TaskHistoryResponse`(seq 单调,权威执行轨迹)
```json
{"task_id":"T1","total":24,"items":[
  {"seq":1,"kind":"task.created","reported":false,"payload":{"title":"存储行业尽调"}},
  {"seq":2,"kind":"task.clarified","reported":false,"payload":{"patch":{".五要素."},"confirmed":false}},
  {"seq":3,"kind":"task.clarified","reported":false,"payload":{"patch":{},"confirmed":true}},
  {"seq":4,"kind":"node.running","reported":false,"payload":{"node_id":"p1_disp","from_status":"pending"}},
  {"seq":5,"kind":"state.updated","reported":false,"payload":{"scope":"p1_disp","patch":{".执行上下文/产物."},"semantics":"merge"}},
  {"seq":6,"kind":"node.accepted","reported":true,"payload":{"node_id":"p1_disp","verifier":"bot_industry_fetch"}},
  "...",
  {"seq":20,"kind":"goal.rejected","reported":true,"payload":{"verifier":"task-owner","verdict":"fail","reason":"缺技术演进维度(tech)"}},
  {"seq":21,"kind":"node.running","reported":false,"payload":{"node_id":"b_disp","from_status":"pending"}},
  {"seq":22,"kind":"state.updated","reported":false,"payload":{"scope":"b_disp","patch":{".技术路线/性能/工艺."},"semantics":"append"}},
  {"seq":23,"kind":"node.accepted","reported":true,"payload":{"node_id":"b_disp","verifier":"群master(技术研究群)"}}
]}
```

---

## 3. case 执行流程图(时序)

> 画法对齐 [数字员工接入](https://yuque.antfin.com/securitytec/otbct4/vl8z5gqyysd5bzte):竖向生命线 `│` + 编号箭头 `──N.label──►` / `◄──N.label──`。参与方:Owner-SKILL(owner-bot 各 SKILL)、Router(`/api/tasks`)、系统(`TaskService`/`TaskScheduler._tick`)、`BotDiscoverPort`(系统侧搜推)、`BCN/bot`、`EventLog`。

### 3.1 录入与启动:create / clarify / start

```plain
Owner-SKILL          Router(/api/tasks)        系统(TaskService/Scheduler)
   │                       │                       │
   │──1.POST /create{title,background}────────►│                       │
   │                       │──2.create────────────►│ Task(DRAFTING)+TASK_CREATED seq1
   │                       │◄──3.{task_id:T1,status:drafting,seq:1}───│
   │◄──4.同────────────────│                       │
   │──5.POST /clarify{patch:五要素}─────────────►│                       │
   │                       │──6.clarify───────────►│ spec 五要素 fold + TASK_CLARIFIED seq2
   │◄──7.status:drafting───│                       │
   │──8.POST /clarify{confirmed:true}───────────►│                       │
   │                       │──9.clarify(confirmed)►│ guard DRAFTING→DEFINED + seq3
   │◄──10.status:defined───│                       │
   │──11.POST /start──────►│                       │
   │                       │──12.start───────────►│ guard DEFINED→RUNNING
   │                       │                       │ init_execution_graph(规划链DONE + 根 BOT_SEARCH PENDING)
   │                       │                       │ mark_graph(RUNNING) + start 自推进首批(→3.2)
   │◄──13.status:running───│◄──14.同──────────────│
```

### 3.2 运行期规划:搜推 → 命中/未命中对称路由(start 自推进首批 / tick harness 驱动 children)

> **start 与 tick 不混用**:首批(根搜推 → 路由 → 发消息)由 `start` 同步推进到首个异步边界即返回;children 到达后由 `tick` harness(回投 `on_event` 泵 / 定时器)推进。两段用同一套推进 body,触发时机不同。

```plain
系统(start)       BotDiscoverPort     owner-bot(plan-skill)      BCN/bot
   │                    │                     │                       │
   │──1.自推进根 n_bot_search(PENDING)────────►│                      │
   │   recommend(task.spec)                   │                      │
   │◄──2.miss(整体不可单点)───────────────────│                      │
   │──3.add_child(DECOMPOSITION) 落 topo + n_bot_search DONE         │
   │──4.自推进 DECOMPOSITION─────────────────────────────────────────►│
   │   发消息(query=需求+执行上下文+拆解上下文)                        │
   │   ★到首个异步边界(等回投),start 返回 ◄──{status:running}─────────│
```

```plain
系统(on_event/tick)  BotDiscoverPort    owner-bot(plan-skill)     BCN/bot
   │                    │                     │                       │
   │◄──5.回投 SubTaskSpec[p1,p2,p3](POST /events)────────────────────│
   │──6.on_event → add_node 落 children BOT_SEARCH + DECOMPOSITION DONE│
   │──7.tick 驱动 p1 BOT_SEARCH(已落图 PENDING)►│                     │
   │   recommend(p1 spec)                     │                     │
   │◄──8.hit bot_industry_fetch(C1)───────────│                     │
   │──9.add_child(p1_disp, DISPATCH) 落 topo + p1 DONE               │
   │──10.tick 驱动 p1_disp DISPATCH──────────────────────────────────►│
   │   claim + 发消息(query=需求+执行上下文) BCN chat.send            │
   │◄──11.NODE_RUNNING seq4(POST /events)────────────────────────────│
   │   (p2 miss→同款递归 → 4 专题;p3 → BBS 上升)                      │
```

### 3.3 子任务执行 + 产出/验收回投落 State

```plain
BCN/bot            Router(/events)      TaskService(on_event)     EventLog
   │                    │                      │                       │
   │──1.chat.event(final,产业链地图)──(SKILL/adapter 翻译)───────────►│
   │──2.POST /events{state.updated,scope:p1_disp,                       │
   │   patch:execution_context+artifacts,merge}──────────────────────►│
   │                    │──3.on_event──────────►│ append STATE_UPDATED►│ seq5
   │                    │                      │ _fold_state MERGE:    │
   │                    │                      │  SubtaskState.execution_context 合并│
   │──4.POST /events{node.accepted,p1_disp,verifier}────────────────►│
   │                    │──5.on_event──────────►│ guard RUNNING→DONE    │
   │                    │                      │ Node/SubtaskState DONE│
   │                    │                      │ + accept_result=pass  │
   │                    │                      │ append NODE_ACCEPTED►│ seq6
   │                    │◄──6.{accepted:true,seq:6}│                  │
   │   → p1 闭合;后续 tick 解锁 p2                                     │
```

### 3.4 协作群 / 聚合 / BBS + 终验 FAIL → reroute → 二次终验

```plain
群master/owner      Router(/events)      系统(_tick)            EventLog
   │                    │                      │                       │
   │ (阶段二 COOP 群 master 聚合回投)          │                       │
   │──1.POST /events{state.updated,a_disp,intermediate_results,append}►│
   │                    │──2.on_event fold APPEND►│ append►│ seq8       │
   │──3.POST /events{node.accepted,a_disp}────►│                       │
   │                    │──4.on_event──────────►│ a_disp DONE + append►│ seq9
   │ (专题 C/D 叶同形,全叶 DONE)               │                       │
   │                    │                      │──5._detect_and_aggregate:│
   │                    │                      │   EXEC_AGGREGATE+aggregate_verdict(PASS)│
   │                    │                      │   p2_dec_agg DONE(缺 tech)│
   │ (BBS 悬赏 bot 回投 ×2)                    │                       │
   │──6.POST /events{state.updated+node.accepted,bbs_p3_1/2}─────────►│ seq16-19
   │                    │                      │──7.全图闭合→_maybe_goal_verify│
   │                    │                      │   aggregate_verdict:AC#3 tech 缺→FAIL│
   │──8.POST /events{goal.rejected,fail,缺tech}(goal-verify-skill)──►│
   │                    │──9.on_event──────────►│ guard REVIEWING→RUNNING│
   │                    │                      │ ★回 gap(loop 落点) append seq20│
   │ (task-plan-skill 写 gap + open_reroute_search)│                  │
   │──10.update_state(p2_dec,APPEND gap_records)►│ gap_records 落 State  │
   │──11.tick 驱动 p2_tech_search(hit 技术群)─►│ 落 b_disp DISPATCH    │
   │   群 master 回投 state.updated + node.accepted(b_disp)──────────►│ seq22/23
   │──12.POST /events{state.updated,scope:null,merge:技术演进章节+尽调结论}►│ public 落 State│
   │                    │                      │──13._maybe_goal_verify:全 AC PASS│
   │                    │                      │   guard RUNNING→REVIEWING→DONE│
   │                    │                      │   append(终态)►│ seq24│
   │◄──13.GET /tasks/T1→status:done─────────────────────────────────│
```