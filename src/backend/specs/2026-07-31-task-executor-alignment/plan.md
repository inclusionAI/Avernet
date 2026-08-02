# TaskExecutionGraph 全生命周期 State/Node/Edge 重构 — plan.md(HOW)

> 隶属:`2026-07-28-goal-driven-task-execution/` 执行架构重构子项;本文实现 `spec.md`(本目录)。
> 落点域:ocb backend 任务内核(开源,代码在 Avernet `src/backend/src/agentclaw/community/core/task/`,sync 到 ocb `ocb-public`)。
> 日期:2026-08-01。
> 状态机沿用 `2026-07-30-task-status-state-machine-alignment/`(7 态 TaskStatus / 6 态 NodeStatus)。

---

## 1. 概述

把 `TaskExecutionGraph` 从"仅执行期控制流图"重构为**全生命周期 State/Node/Edge 统一图谱**。本文落字段 schema、协议签名、状态机守卫、服务层改造、State 三能力(读写/归约/回溯)实现、伪代码边拓扑订正、BBS 衔接链路、受影响文件清单。

**不改变**:三模态/三协作模式语义、事件回投通道(统一 `POST /events`)、7 态状态机枚举。

---

## 2. 目标架构(重构后)

```
Task 聚合根
 ├─ spec: TaskSpec                  (录入面,不变)
 ├─ status: TaskStatus               (7 态,状态机权威)
 ├─ owner_bot_id: Optional[str]      (新增;task-owner 绑定)
 ├─ latest_event_seq: int            (事件日志水位)
 └─ execution_graph: TaskExecutionGraph   (创建即非 None;全生命周期)
        ├─ root_phase: TaskStatus    (镜像 Task.status,快照自描述)
        ├─ graph_status: GraphStatus (带守卫,§5)
        ├─ loop_round: int
        ├─ nodes: list[Node]         (新增 node_type 多态;承载 recognition→…→BBS)
        ├─ edges: list[Edge]
        └─ state: TaskState          (★ 一等图要素,SSOT,§3/§8)

Event Log (TaskEventRepo) = 时间旅行源;graph+state = 物化 fold;snapshot = fold 缓存(§8)
```

**两维度正交(皆一等,图要素 = State / Node / Edge)**:
- **Node 维度(动作/事件粒度)**:每个 Node = 一次动作(谁、用什么 SKILL、做了什么、产出什么 verdict)。**所有动作都是节点**,含三个判定动作 `EXEC_ACCEPT`/`EXEC_AGGREGATE`/`GOAL_VERIFY`(bot 跑 SKILL 判验,有动作人/时刻/verdict 输出)。
- **State 维度(Task / SubTask 实体粒度)**:`TaskState.public` + 每 subtask 一个 `SubtaskState`(累积上下文 + **实体状态 status** + 结果/gap)。

动作节点 fold 驱动实体状态:`DISPATCH` → subtask `RUNNING`;`EXEC_ACCEPT` → 叶子 subtask `DONE`/`REJECTED`;`EXEC_AGGREGATE` → 父 subtask `DONE`/`REJECTED`;`GOAL_VERIFY` → `Task.status DONE`/回 gap。判定动作 = Node 维度记一笔(可审计/可画),其效果 = State 维度翻实体 status;**非二选一,是两维度各落一笔**。

---

## 3. 领域模型变更(`domain/models.py`)

### 3.1 新增 `NodeType` 枚举(节点多态 discriminator)

```python
class NodeType(StrEnum):
    """全生命周期节点类型。决定执行者(spec §7)与状态机侧门。
    re-route / recurse 不是独立类型:重路由复用 BOT_SEARCH + DECOMPOSITION
    节点(spec §6 n14/n17 = BOT_SEARCH、n18 = DECOMPOSITION)。"""
    RECOGNITION = "recognition"        # task-create(owner-bot,task-recognition-skill)
    CLARIFY = "clarify"               # task-clarify(owner-bot,task-recognition-skill;DRAFTING 内不迁态)
    EXECUTE_START = "execute_start"   # task-execute → scheduler.start(系统桥,DRAFTING→DEFINED)
    BOT_SEARCH = "bot_search"         # 搜推匹配(owner-bot / 失败 exec-bot / BBS bot,task-plan-skill)
    DECOMPOSITION = "decomposition"   # 任务分解(同上执行者,task-plan-skill,递归)
    DISPATCH = "dispatch"             # 派发(系统 task-scheduler)
    EXEC_ACCEPT = "exec_accept"      # 子任务验收(执行方 bot,task-exec-skill,仅判子任务 DONE)
    EXEC_AGGREGATE = "exec_aggregate" # ★ 中间层聚合验收(父 owner bot,task-exec-skill,读 State 聚合判父 DONE)
    GOAL_VERIFY = "goal_verify"      # 任务终验(task-owner,goal-verify-skill,判任务 DONE)
    MARK_HANG = "mark_hang"          # ★ 递归上限挂起(系统,graph AWAITING_HUMAN_*,等人确认;不直接升 BBS)
    BBS_DISPATCH = "bbs_dispatch"    # BBS 上升入口(系统,人确认升 BBS 后落点;mark graph ON_PLAZA)
```

> BBS 上升后的执行/分解节点复用 `DISPATCH`/`DECOMPOSITION`/`EXEC_ACCEPT` 类型,执行者按 §7 映射(BBS bot 承担分解+执行);`GOAL_VERIFY` 始终归 task-owner(BBS 后仅此一项)。
> `EXEC_AGGREGATE` / `MARK_HANG` 为 v2 新增一等节点(spec FR-GRAPH-08a/09, AC-S-11/12):分别为"中间层聚合验收"与"递归上限挂起入口"。

### 3.2 新增 `TaskState` + `SubtaskState`(★ 一等图要素;实体维度)

```python
@dataclass
class GapRecord:
    """结构化 gap(消解现 Node.properties 里的字符串 list)。"""
    node_id: str
    round: int
    unmet_criteria: list[str]          # 未达验收点(结构化)
    verdict: AttemptOutcome            # FAIL / PARTIAL
    at: str

@dataclass
class SubtaskState:
    """State 的 per-subtask 实体分区(实体维度)。retrieve-state(node_id) = 公共区 + 此分区。"""
    node_id: str
    status: NodeStatus = NodeStatus.PENDING   # ★ 实体状态;动作节点 fold 驱动(DISPATCH→RUNNING/ACCEPT→DONE/AGGREGATE→DONE/REJECT→REJECTED)
    depth: int = 0                     # 递归深度(§11);根 subtask=0
    execution_context: dict = field(default_factory=dict)   # 传递数据(MERGE 语义)
    intermediate_results: list[dict] = field(default_factory=list)  # 中间结果(APPEND)
    artifacts: list[ArtifactRef] = field(default_factory=list)      # 已产出(APPEND,按 name 去重)
    gap_records: list[GapRecord] = field(default_factory=list)      # gap 历史(APPEND)

@dataclass
class TaskState:
    """图级一等要素,SSOT。承载运行期间传递数据/中间结果/历史记录/累积更新。"""
    public: dict = field(default_factory=dict)   # 任务级公共上下文(MERGE):spec 摘要、全局约束、递归上限、当前 phase
    subtasks: dict[str, SubtaskState] = field(default_factory=dict)  # key=node_id(原 partitions)
```

**归约语义(FR-GRAPH-03b,§8 落实现)**:
| 字段 | 语义 |
|---|---|
| `state.public` | MERGE(深合并) |
| `subtask.status` | OVERWRITE(动作节点 fold 驱动;经状态机 guard) |
| `subtask.execution_context` | MERGE |
| `subtask.intermediate_results` | APPEND |
| `subtask.artifacts` | APPEND(按 `name` 去重,后者覆盖 location/type) |
| `subtask.gap_records` | APPEND |
| `subtask.depth` | OVERWRITE(单调递增,仅 decomposition 节点 +1) |

> `SubtaskState` 即原 `SubtaskStatePartition`(rename);`TaskState.partitions` → `TaskState.subtasks`。`subtask.status` 是实体维度状态,与 Node 维度的动作节点对应(fold 驱动),非新概念。

### 3.3 `Node` 改造(加 `node_type`;数据迁 State)

```python
@dataclass
class Node:
    node_id: str
    node_type: NodeType                # ★ 新增(必填)
    spec: str
    status: NodeStatus = NodeStatus.PENDING
    run_mode: Optional[RunMode] = None
    targets_acceptance: list[AcceptanceCriteria] = field(default_factory=list)
    targets_deliverable: list[Deliverable] = field(default_factory=list)
    attempted_executors: list[AttemptedRecord] = field(default_factory=list)  # 保留:控制/路由历史
    properties: dict = field(default_factory=_default_node_properties)        # 保留:retry_count/max_attempts 等控制旋钮
    assignee: Optional[str] = None
    sub_dag: Optional[SubDagRef] = None
    # 移除:artifacts（→ state.subtasks[node_id].artifacts）
    # 移除:instruction（→ state.subtasks[node_id].execution_context）
    # 移除:properties["loop_round"]（→ state.subtasks[node_id].depth）
```

> 数据面(artifacts/instruction/中间结果/gap)迁 `TaskState` 分区;控制面(status/attempts/路由旋钮)留 `Node`。`Node.artifacts` 删除前先在 §15 兼容窗口保留只读投影。

### 3.4 `TaskExecutionGraph` 加 `state`

```python
@dataclass
class TaskExecutionGraph:
    root_phase: TaskStatus
    graph_status: GraphStatus = GraphStatus.ON_PLAZA
    loop_round: int = 0
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    state: TaskState = field(default_factory=TaskState)   # ★ 新增
```

### 3.5 `SubTaskSpec` 加 `depth`(分解描述符带深度)

```python
@dataclass
class SubTaskSpec:
    node_id: str
    spec: str
    run_mode: Optional[RunMode] = None
    depend_on: list[str] = field(default_factory=list)
    depth: int = 0                     # ★ 新增;由 DecomposerPort 按父+1 填
```

### 3.6 `Task` 聚合根

```python
@dataclass
class Task:
    id: str
    user_id: str
    source: TaskSource
    spec: TaskSpec
    status: TaskStatus = TaskStatus.DRAFTING
    execution_graph: Optional[TaskExecutionGraph] = None   # 创建即建图(n1)
    latest_event_seq: int = 0
    loop_round: int = 0
    owner_bot_id: Optional[str] = None   # ★ 新增(O-7 ③);录入期 user bot 写;BBS 终验绑定
    # 移除:plan: Optional[Plan]  (分解并入图,§3.7)
```

### 3.7 `Plan` 处置

`Task.plan` 字段**移除**。分解结果由 `DecomposerPort.decompose -> list[SubTaskSpec]` 返回后,经 `TaskService.add_node` 直接物化为图节点 + State 分区。`Plan` dataclass 保留作 `DecomposerPort` 的内部中间结构(不再上 `Task`)。`finalize_plan` 接口语义改为"冻结 spec、建空图、置 DEFINED"。

---

## 4. 协议变更(`protocols.py`)

### 4.1 `DecomposerPort` 退单签名(O-7 ①)

```python
@runtime_checkable
class DecomposerPort(Protocol):
    def decompose(self, spec: str, state: TaskState) -> list[SubTaskSpec]:
        """统一分解入口:初始(owner-bot)/ 递归(exec-bot)/ BBS(bbs-bot)均经此。
        输入带 State 以做 retrieve-state(子任务上下文);输出带 depth(父+1)。"""
```

(v1 的 `decompose_node` 删除。)

### 4.2 `OwnerResolver` 缩两方法(O-7 ②)

```python
@runtime_checkable
class OwnerResolver(Protocol):
    def resolve_group_owner(self, group_id: str) -> str:
        """协作群 owner-bot(群成员动态,需查 BCS)。"""
    def resolve_task_owner(self, task_id: str) -> str:
        """task-owner = Task.owner_bot_id(持久化);缺失则报错。"""
```

self(单 bot 自验收)内联,不走 Port。

### 4.3 `TaskService` 图操作 API(新增统一写口)

```python
class TaskService(Protocol):
    # 既有:get/list_by_user/progress/on_event/claim_node/...
    def add_node(self, task_id: str, spec: SubTaskSpec | Node, parent_node: Optional[str],
                 node_type: NodeType, executor: str) -> Node: ...
    def add_edge(self, task_id: str, from_node: str, to_node: str, kind: EdgeKind) -> Edge: ...
    def update_state(self, task_id: str, scope: Optional[str], patch: dict,
                     semantics: StateSemantics) -> None: ...   # scope=None→public;else partition
    def retrieve_state(self, task_id: str, scope: Optional[str]) -> dict: ...
    def snapshot(self, task_id: str) -> GraphSnapshot: ...     # §8 回溯
```

`StateSemantics = Enum("MERGE","APPEND","OVERWRITE")`,`update_state` 按 §3.2 归约表落 fold。所有图变更经此写口 → append 事件 → fold → save(状态组同口)。

---

## 5. 状态机变更(`state_machine.py`)

### 5.1 `GraphStatus` 加迁移守卫(补 v1 gap)

```python
GRAPH_TRANSITIONS = {
    GraphStatus.ON_PLAZA: {GraphStatus.AWAITING_HUMAN_ACCEPT, GraphStatus.VERIFIED},
    GraphStatus.AWAITING_HUMAN_ACCEPT: {GraphStatus.ON_PLAZA, GraphStatus.AWAITING_HUMAN_ADJUST},
    GraphStatus.AWAITING_HUMAN_ADJUST: {GraphStatus.ON_PLAZA, GraphStatus.AWAITING_HUMAN_ACCEPT},
}
# VERIFIED 为终态。
def require_graph_transition(from_: GraphStatus, to: GraphStatus) -> None: ...
```

> **v2 语义澄清(spec FR-GRAPH-09/AC-S-12)**:`ON_PLAZA` = 图处于活性执行态(含 BBS 上升后同图延续,广场 BBS bot 在跑);`AWAITING_HUMAN_ACCEPT` = **`mark_hang` 挂起门**(递归上限触顶,等人确认:升 BBS → `ON_PLAZA` / 不升 → task `FAILED`);`AWAITING_HUMAN_ADJUST` = 人介入调整(补上下文/改 spec)后回 `AWAITING_HUMAN_ACCEPT`;`VERIFIED` = `goal-verify` PASS 终态。即 hang gate 与 BBS gate 同用 `AWAITING_HUMAN_ACCEPT`(本是同一"等人确认"门),不另设态。

`TaskService.mark_graph_status` 改为经此 guard(现实现是直接赋值,补 guard)。

### 5.2 生命周期触点(7 态对齐,§12 细化)

| 节点 | Task 状态迁移 |
|---|---|
| n1 RECOGNITION task-create | (新建)→ DRAFTING |
| n2 CLARIFY task-clarify | DRAFTING(spec 补全,不迁态,对齐 R2) |
| n3 EXECUTE_START(scheduler.start) | DRAFTING → **DEFINED**(spec 冻结 + 建空图) |
| n9 首个 DISPATCH | DEFINED → **EXECUTING** |
| n_agg EXEC_AGGREGATE | 父 subtask DONE/REJECTED;**不迁 Task 态**(中间层,EXECUTING 内) |
| n_hang MARK_HANG | graph → `AWAITING_HUMAN_ACCEPT`(挂起);**不迁 Task 态**(EXECUTING 内挂起) |
| n_goal GOAL_VERIFY PASS | EXECUTING → **DONE**;FAIL → 继续 gap loop(或 FAILED,见 §16 O-P2) |

> **决策(O-P1 resolved)**:n3 落 DEFINED、首个 DISPATCH 落 EXECUTING;保留 DEFINED 作"spec 冻结/规划中"语义。
> `EXEC_AGGREGATE` / `MARK_HANG` 节点不改 Task 状态机(7 态),只动 graph_status / 节点态 / State。

---

## 6. 事件变更(`events.py`)

### 6.1 新增 `EventKind`

```python
class EventKind(StrEnum):
    # 既有:TASK_CREATED/SPEC_AMENDED/PLAN_FINALIZED/NODE_DISPATCHED/NODE_RUNNING/
    #       NODE_ACCEPTED/NODE_REJECTED/NODE_FAILED/GOAL_VERIFIED/GOAL_REJECTED/
    #       LOOP_REROUTED/EXECUTION_ATTEMPTED/CANCELLED/HUNG
    NODE_ADDED = "node_added"          # add_node
    EDGE_ADDED = "edge_added"          # add_edge
    STATE_UPDATED = "state_updated"    # update_state(纯 State 写)
    PLAN_REQUESTED = "plan_requested"  # EXECUTE_START 完成后请 owner-bot 规划(§12)
    EXEC_AGGREGATED = "node.aggregated"  # ★ exec-aggregate 聚合验收结果(父 subtask DONE/REJECTED)
    NODE_HANG = "node.hang"            # ★ mark-hang 挂起(graph → AWAITING_HUMAN_ACCEPT)
```

`PLAN_FINALIZED` 保留(DEFINED 时由 n3 触发,语义改为"spec 冻结")。
`EXEC_AGGREGATED` payload: `parent_node_id` + `verdict`(DONE/REJECTED)+ `unate_criteria`;`NODE_HANG` payload: `trigger_node_id`(触顶的 decomposition 父)+ `depth`。

### 6.2 事件携带 State patch

所有节点事件 payload 增量带 `state_patch`(`scope` + `patch` + `semantics`),`TaskService.on_event` fold 时一并 `update_state`。State 的"历史记录"由事件流重建(§8)。

---

## 7. 服务层变更

### 7.1 `task_service.py`
- 实现 §4.3 的 `add_node`/`add_edge`/`update_state`/`retrieve_state`/`snapshot`;均走 guard→fold→append event→save。
- `mark_graph_status` 加 §5.1 guard。
- `_node_view`/`get_task_graph` 增返 `state` 分区(副屏 query face);`_node_view` 按 `node_type` 输出 `render_kind`(exec / control-gate / system-bridge),供副屏区分聚合验收门等控制节点(O-P5)。
- 移除 `finalize_plan` 的"物化 Plan→execution_graph"逻辑;改为"冻结 spec + 建空图 + n1/n2/n3 由 owner-bot 经 SKILL 回投逐步 add_node"。
- 现有 `compute_gap` 短路 task FAILED 的逻辑(§16 风险 1)改为"unrecoverable 留待 n_goal 终验"。

### 7.2 `task_scheduler.py`
- **职责收窄**:仅 `DISPATCH` 节点派发 + watchdog + 状态写口触发 + **系统侧检测节点**(`MARK_HANG` / 触发 `EXEC_AGGREGATE`)。
- **移除**:inline 搜推(`BotDiscoverPort` 调用迁 `BOT_SEARCH` 节点的 bot SKILL)、inline 运行期分解(迁 `DECOMPOSITION` 节点的 bot SKILL)。
- **搜推先行(FR-GRAPH-14)**:`tick` 对每个 PENDING/REJECTED 的(子)任务节点,先要求其 owner/exec-bot 经 `BOT_SEARCH` 节点搜推;full-cover 命中 → `DISPATCH`;未命中/验收不过 → `DECOMPOSITION`。系统**不**在未搜推时直接拆,也**不**在未匹配时直接 `MARK_HANG`/BBS。
- **exec-aggregate 触发(O-8,FR-GRAPH-08a)**:`tick` 扫描"父 subtask 的子分解 children 全 `EXEC_ACCEPT` DONE 且父未闭合"→ 经 `OwnerResolver.resolve_task_owner`(或父 owner)向父 owner bot 派发"聚合验收请求" → 父 owner 经 `task-exec-skill` 读 State(`retrieve-state`:children 产出验收 + 父 `targets_acceptance`)回投 `EXEC_AGGREGATED`(DONE/REJECTED)。**系统检测子全 DONE + 落图触发,owner 判验**(对齐 §9)。REJECTED → 父回 gap 重路由/再拆。自底向上逐级。
- **mark-hang 触发(FR-GRAPH-09)**:`DECOMPOSITION` 节点 `depth >= MAX_RECURSION_DEPTH` 时拒绝 `add_node`,改为系统落 `MARK_HANG` 节点 + `mark_graph_status(AWAITING_HUMAN_ACCEPT)`;等人确认(§13)。
- `tick`/`compute_gap` 改为读 `state.subtasks`(retrieve-state)而非扫 Node.properties。
- `watchdog` 计数仍读 `Node.properties`(retry_count 等),保留。

### 7.3 `bbs_executor.py`
- `claim`/`post_progress` 不变,但识别新 `node_type`(BBS 阶段的 `DISPATCH`/`DECOMPOSITION`/`EXEC_ACCEPT` 节点 `run_mode=BBS`)。
- 删除 `progress_snapshot` 相关(O-7 ④);BBS bot 经 `retrieve_state` 取上下文。
- BBS 终验由 task-owner 经 `GOAL_VERIFY` 节点回投(§13)。

### 7.4 新增 `graph_checkpoint.py`(或并入 task_service)
- §8 回溯实现。

---

## 8. State 三能力实现

### 8.1 读写契约(FR-GRAPH-03)
- `update_state(task_id, scope, patch, semantics)`:统一写口,按归约表 fold。
- `retrieve_state(task_id, scope)`:读 `state.public` + `state.subtasks[scope]`(scope=None 只读 public)。
- 任意节点经其执行者 SKILL 回投 `STATE_UPDATED` 事件,或节点事件附带 `state_patch`。

### 8.2 累积归约(FR-GRAPH-03b)
- `StateSemantics` 枚举 + `TaskState.fold(patch, semantics)` 实现 §3.2 表。
- APPEND 去重(artifacts 按 name);MERGE 深合并(dict);OVERWRITE 单调(depth / subtask.status,后者经状态机 guard)。

### 8.3 持久化与回溯(FR-GRAPH-03c)
- **时间旅行源 = 事件日志**(`TaskEventRepo`,已存在,单写递增 seq)。
- **物化 fold = `TaskExecutionGraph`(含 `state`)**,每次 `on_event` 重算并 save(TaskRepo)。
- **快照 = fold 缓存**:

```python
@dataclass
class GraphSnapshot:
    task_id: str
    at_seq: int                       # 快照对应的事件 seq
    graph: TaskExecutionGraph          # 含 state 的物化 fold
    taken_at: str
```

- `snapshot(task_id)` 存当前 fold;**断点重跑**:从最近快照 seq 恢复 fold,重放其后的 events 到目标 seq;**回滚**:截断事件日志到目标 seq + 重算 fold(写前留 rollback 记录)。
- 快照策略:每节点边界(DONE/FAILED)落一次;或每 N 条事件落一次(plan 落具体值)。

---

## 9. 节点类型 ↔ 执行者 ↔ 状态侧门(细化,接 spec §7)

| NodeType | 执行者 | SKILL | 状态侧门 |
|---|---|---|---|
| RECOGNITION | owner-bot(需求bot/群master) | task-recognition-skill | DRAFTING 内,不迁态 |
| CLARIFY | owner-bot | task-recognition-skill | DRAFTING 内,不迁态 |
| EXECUTE_START | 系统(scheduler.start 桥) | — | DRAFTING→DEFINED |
| BOT_SEARCH | owner-bot(初始)/ 失败 exec-bot(重路由)/ BBS bot(BBS 阶段) | task-plan-skill | 不改态,产 BOT_SEARCH 结果进 State |
| DECOMPOSITION | owner-bot / 失败 exec-bot / BBS bot | task-plan-skill(调 DecomposerPort.decompose) | 产 SubTaskSpec→add_node;depth=父+1 |
| DISPATCH | 系统 task-scheduler | — | 首个→DEFINED→EXECUTING;后续不改态 |
| EXEC_ACCEPT | 执行方 bot(单bot self / 群 owner-bot / BBS bot) | task-exec-skill | 子任务 PENDING→RUNNING→DONE/FAILED |
| **EXEC_AGGREGATE** | **父 subtask 的 owner bot**(OwnerResolver) | **task-exec-skill**(读 State:children 产出验收 + 父 `targets_acceptance`,聚合;逻辑同 GOAL_VERIFY,FR-GRAPH-07b) | 父 subtask DONE/REJECTED;不改 Task 态;系统 tick 触发(O-8) |
| GOAL_VERIFY | **task-owner**(OwnerResolver.resolve_task_owner) | goal-verify-skill(读 State:根 subtask 产出验收 + Task `goal.acceptances`,聚合;逻辑同 EXEC_AGGREGATE,FR-GRAPH-07b) | EXECUTING→DONE / FAIL→gap loop |
| MARK_HANG | 系统 task-scheduler | — | graph→AWAITING_HUMAN_ACCEPT(挂起,不升 BBS) |
| BBS_DISPATCH | 系统 | — | graph_status ON_PLAZA(AWAITING_HUMAN_ACCEPT→ON_PLAZA,人确认升 BBS) |

> **完成判断逻辑统一(FR-GRAPH-07b)**:`EXEC_AGGREGATE`(父级)与 `GOAL_VERIFY`(任务级)同一判断骨架——"读 State(下属产出验收 + 自身验收标准)→ 聚合判 DONE";区别仅作用域与验收标准来源(父 `targets_acceptance` vs Task `goal.acceptances`)。两者复用同一聚合判定纯函数。

---

## 10. 伪代码边拓扑订正(O-3)

原伪代码笔误,plan 订正(语义对齐):

```
e8  n6  -> n9   ✓(subtask1 search 命中→dispatch)
e10 n7  -> n11  ✏(原 n6→n11;subtask2 search(n7)→dispatch(n11))
e12 n8  -> n13  ✏(原 n6→n13;subtask3 未匹配(n8)→decomposition(n13))
e13 n10 -> n14  ✓
e16 n16 -> n17  ✓
e18 n12 -> n19  ✓
```

>n13 触发点 = n8(subtask3 bot-search 未匹配),非 accept-fail。n18/n20 才是 accept-fail 触发分解。

---

## 11. 递归深度与上限(O-4)

- **落点**:`SubtaskState.depth`(非 Node.properties)。
- **计数**:根 subtask `depth=0`;每次 `DECOMPOSITION` 产出的 children `depth = 父+1`(由 `DecomposerPort.decompose` 按父 `SubtaskState.depth` +1 填入 `SubTaskSpec.depth`)。
- **上限**:`MAX_RECURSION_DEPTH = 3`(plan 值,可配)。超上限的 `DECOMPOSITION` 节点拒绝 `add_node`,改为系统落 `MARK_HANG` 节点 + `mark_graph_status(AWAITING_HUMAN_ACCEPT)` **挂起等人确认**,不直接升 BBS(FR-GRAPH-09/AC-S-12)。人确认后:升 BBS → `BBS_DISPATCH`(§13)/ 不升 → task `FAILED`(终态)。
- **上限计数范围**:按"原 subtask 链路深度"计(即 `SubtaskState.depth`),非全图最大深度。

---

## 12. scheduler.start ↔ n4 驱动时序(O-6)

```
n3 EXECUTE_START:
  owner-bot task-execute(task-spec) → POST /events{kind:PLAN_REQUESTED}
  → TaskService.on_event:
      guard DRAFTING→DEFINED;建空 execution_graph(state=空,root_phase=DEFINED)
      append EXECUTE_START node(n3);append STATE(public: spec 摘要)
  → Scheduler.on_event:经 ExecutionPort.dispatch 向 owner-bot 派发"规划请求"
n4 BOT_SEARCH:
  owner-bot 收规划请求 → task-plan-skill bot-search(task-spec)
  → 回投 BOT_SEARCH 结果(命中/未匹配 + state_patch)→ on_event → add BOT_SEARCH node + update_state
  → 未匹配 → 触发 n5 DECOMPOSITION
```

> scheduler.start 仅做"冻结 + 建图 + 请 owner-bot 规划";n4 仍由 owner-bot SKILL 驱动回投。系统不 inline 规划。

---

## 12A. exec-aggregate 触发机制(O-8)+ 完成判断逻辑统一(FR-GRAPH-07b)

**O-8 触发机制(O-P4 resolved:系统 `tick` 扫描 State + owner 判验)**:
```
scheduler.tick 扫描 State(按依赖关系,自底向上):
  对每个有 children 的父 subtask P:
    若 P 依赖的所有 child 子任务(exec-accept)均已执行完成(DONE),且 P 尚未闭合(DONE/REJECTED):
      → 系统落 EXEC_AGGREGATE 节点(挂 P 之下,children 之后)
      → 经 OwnerResolver 解析 P 的 owner bot → ExecutionPort 派发"聚合验收请求"
      → P 的 owner bot 经 task-exec-skill 读 retrieve-state(P):
            children 各产出验收(artifacts + ACCEPT 状态)+ P 自身 targets_acceptance
        → 聚合判断 → 回投 EXEC_AGGREGATED{parent=P, verdict=DONE|REJECTED, unmet=[...]}
      → DONE   → P 闭合:fold `EXEC_AGGREGATED` → P 的 `SubtaskState.status`=DONE
      → REJECTED → P 的 `SubtaskState.status`=REJECTED → 回 gap(重路由 bot-search / 再 decomposition,受 depth 上限)
  若根 subtask 闭合(经聚合验收 DONE,`SubtaskState.status`=DONE)→ 触发 GOAL_VERIFY 节点(任务终验/复验)
```
触发由**系统 `tick` 扫描 State**(按"父依赖的子任务是否全完成"判定,非子 exec-accept 链式回投),与"系统检测+落图、owner 判验"口径一致;`.tick` 幂等,重复检测由"P 已闭合"短路。父 subtask 复验(REJECTED 后再聚合)与任务终验/复验(GOAL_VERIFY)同款触发路径。

**FR-GRAPH-07b 完成判断逻辑统一**:`EXEC_AGGREGATE` 与 `GOAL_VERIFY` 共用一个聚合判定纯函数:
```python
def aggregate_verdict(self_acceptances, child_results) -> tuple[AttemptOutcome, list[str]]:
    """读 self_acceptances(父 targets_acceptance / Task goal.acceptances)
    + child_results(State 里下属产出验收),逐 AC 比对 → (DONE|PARTIAL|FAIL, unmet_criteria)。"""
```
区别仅入参来源:`EXEC_AGGREGATE` 取父 subtask 分区 + 父 `targets_acceptance`;`GOAL_VERIFY` 取根 subtask 分区 + Task `goal.acceptances`。

---

## 13. BBS 衔接链路(O-5/O-9 收口)

```
递归上限(DECOMPOSITION depth≥MAX,拒绝 add_node)→ 系统 MARK_HANG 节点
  → mark_graph_status(AWAITING_HUMAN_ACCEPT)   [守卫 §5.1]  # 挂起等人确认,不直接升 BBS
  → 人确认(POST /escalate-bbs 确认上升 | /cancel 不升):
     ├ 升 BBS: TaskService.mark_graph_status(AWAITING_HUMAN_ACCEPT → ON_PLAZA) [守卫]
     │         → add BBS_DISPATCH node
     │         → 广场 BBS bot 经 BbsExecutor.claim 认领后续 DISPATCH/DECOMPOSITION/EXEC_ACCEPT 节点(run_mode=BBS)
     │              —— BBS bot 承担分解+执行(O-7 ③);retrieve_state 取上下文(无 progress_snapshot)
     │         → BBS 阶段子任务逐 EXEC_ACCEPT DONE → 各层 EXEC_AGGREGATE 逐级闭合(同 §12A,执行者=BBS bot/owner)
     │         → task-owner(OwnerResolver.resolve_task_owner)经 goal-verify-skill 回投 GOAL_VERIFY
     │              —— BBS 后 task-owner 仅此一项,不做分解/执行
     │         → PASS → EXECUTING→DONE;FAIL → 任务 FAILED(终态,不回环;或再 escalation,§16 O-P3)
     └ 不升: task → FAILED(终态)
```
> BBS 上升衔接契约此阶段定(§4.1/O-9);BBS 广场执行机制(自主认领调度、广场 bot exec/plan 编排)为后续阶段,此阶段仅 stub `BbsExecutor.claim` 路径。

---

## 14. 受影响文件清单

| 文件 | 改动 | 落点 |
|---|---|---|
| `domain/models.py` | 新增 `NodeType`/`GapRecord`/`SubtaskState`(含 status)/`TaskState`/`GraphSnapshot`;改 `Node`(加 node_type、移 artifacts/instruction)、`TaskExecutionGraph`(加 state)、`SubTaskSpec`(加 depth)、`Task`(加 owner_bot_id、移 plan) | [开源] |
| `domain/events.py` | 加 `NODE_ADDED`/`EDGE_ADDED`/`STATE_UPDATED`/`PLAN_REQUESTED`/`EXEC_AGGREGATED`/`NODE_HANG`;事件携带 state_patch | [开源] |
| `domain/state_machine.py` | 加 `GRAPH_TRANSITIONS` + `require_graph_transition`(v2:澄清 ON_PLAZA/AWAITING_HUMAN_ACCEPT 为 hang+BBS gate) | [开源] |
| `protocols.py`(core) | `DecomposerPort` 单签名;新增 `OwnerResolver`;`TaskService` 加图操作 API + `StateSemantics`;新增 `aggregate_verdict` 纯函数(FR-GRAPH-07b)。**Port Protocols 留 core,不经 api re-export**(§17A.4) | [开源] |
| `api/task/service_api.py` | api Protocol 形态不变(`*args/**kwargs->Any`,零 core import);新图 API 经 core Protocol 透传,api 不加具体签名 | [开源] |
| `services/task_service.py` | 图操作实现、State fold、snapshot、mark_graph_status guard、移除 plan 物化、compute_gap 不短路 | [开源] |
| `services/task_scheduler.py` | 职责收窄(仅 DISPATCH+watchdog+MARK_HANG+触发 EXEC_AGGREGATE);移除 inline 搜推/分解;搜推先行;读 state 分区;`tick` 检测父 subtask 子全 DONE(§12A)。**watchdog 计数留 node.properties**(§17A.7) | [开源] |
| `services/bbs_executor.py` | 识别新 node_type;**加 `retrieve_state`**(progress_snapshot 不存在,非删);BBS 终验经 task-owner GOAL_VERIFY;此阶段 stub 认领 | [开源] |
| `services/graph_checkpoint.py`(新) | snapshot/断点重跑/回滚 | [开源] |
| `services/graph_adapter.py` | `_node_view` 增 state 分区;按 `node_type` 输出 `render_kind`(O-P5);sub_dag 映射不变。**仅后端数据面投影;画布渲染归 corp 前端**(§17A.5) | [开源] |
| `di/modules/.../task.py`(community+corp) | `OwnerResolver` 绑定(local/prod);`DecomposerPort` 改单签名实现;新图 API 上 Protocol。corp 复用 community 内核(§17A.1),仅 prod adapter override | [开源]+[corp-only adapter] |
| 测试 `tests/...` | 契约测试 + 场景(spec §6 全链路:搜推先行/递归/hang/exec-aggregate/goal-verify/BBS 衔接) | [开源] |
| 副屏画布 `TaskWorkflowView` | 消费 `render_kind`/state 分区渲染聚合验收门等 | **[corp-only 前端]** 本 plan 不改 |

---

## 15. 迁移与兼容

- 数据无线上历史任务可不迁;新模型从 Task 创建即建图。
- `Node.artifacts`/`instruction` → State 分区:提供 1 个版本的只读投影(`_node_view` 临时回填),下版删除。
- `Task.plan` 字段删除:若有外部读 `plan`,改读 `execution_graph.nodes`(过 SubTaskSpec 投影)。
- DI:新增 `OwnerResolver` Port + `local/`/`prod/` 实现(Rule 20);`DecomposerPort` 实现改单签名。

---

## 16. 风险与待确认(plan 级开放问题)

| # | 项 | 取向 |
|---|---|---|
| R-1 | `compute_gap` 现实现见 `unrecoverable_failed` 短路 task FAILED(spec 意图是留待 n_goal 终验)。 | 改为不短路;unrecoverable → 触发 n_goal 或 MARK_HANG。需单测守护。 |
| O-P1 | n3 落 DEFINED、首个 DISPATCH 落 EXECUTING(§5.2)。保留 DEFINED 语义? | **resolved(用户)**:保留 DEFINED 作"spec 冻结/规划中"语义。 |
| O-P2 | n_goal FAIL 后:继续 gap loop 还是直接 FAILED?BBS 已上升后再 FAIL 呢? | **resolved(用户,采本方案)**:BBS 前 FAIL→回 gap loop(限轮次)或 MARK_HANG;BBS 后 FAIL→FAILED 终态。 |
| O-P3 | `MAX_RECURSION_DEPTH=3`、快照策略(每节点边界 vs 每 N 事件)取值。 | 默认值见 §11/§8.3;可配。 |
| O-P4 | `EXEC_AGGREGATE` 触发方式。 | **resolved(用户)**:`task_scheduler.tick` 扫描 State——按"父 subtask 依赖的子任务(exec-accept)是否全部执行完成"判断:父 subtask 子全 DONE → 触发父 `EXEC_AGGREGATE` 验收/复验;根 subtask 闭合 → 触发 `GOAL_VERIFY` 任务终验。幂等(已闭合短路)。 |
| O-P5 | `EXEC_AGGREGATE` 节点(挂父之下、children 之后)是否影响 `graph_adapter` 副屏投影。 | **resolved(用户,采选项 B)**:保留为独立节点,`_node_view` 按 `node_type` 输出 `render_kind`(exec / control-gate / system-bridge),前端按 kind 用不同形状渲染聚合验收门,与执行节点区分;对齐 FR-OBS-05/06 全生命周期可审计。 |
| R-2 | 事件携带 state_patch 增 payload 体积;State 全量 fold 存储成本。 | 快照压缩 + 增量 patch;大任务分区裁剪。 |

---

## 17. 实现顺序建议(对应 tasks.md 拆分)

1. `models.py` 新增/改字段(+ 单测:State 归约、Node node_type、`NodeType` 含 EXEC_AGGREGATE/MARK_HANG)。
2. `state_machine.py` GraphStatus guard(+ 单测;含 hang→BBS/FAILED 路径)。
3. `events.py` 新事件(含 `EXEC_AGGREGATED`/`NODE_HANG`)+ state_patch fold。
4. `protocols.py` DecomposerPort/OwnerResolver/TaskService 图 API + `aggregate_verdict` 纯函数。
5. `task_service.py` 图操作 + State fold + snapshot。
6. `task_scheduler.py` 职责收窄 + 搜推先行 + `tick` 检测父聚合(exec-aggregate 触发)+ MARK_HANG。
7. `bbs_executor.py` + BBS 衔接(stub 认领)。
8. `graph_checkpoint.py` 回溯/断点重跑。
9. 契约测试(`tests/contracts/test_*.py`)+ 场景(spec §6 全链路:搜推先行/递归/hang/exec-aggregate/goal-verify/BBS)E2E。
   - 测试设计 single source = §19/§20;tasks.md 据此编号 E2E-1..12 为 TDD 实施项(红→绿);纯单测自行补。

---

## 17A. 实现规矩与落点判据(来自 ocb memory,实施硬约束)

> 本节汇总 ocb 项目 memory 中与本设计实现相关的硬性规矩。实施 LLM 必须遵守;与 §18 同级,但 §18 是"代码变更地图",本节是"落点/边界红线"。

### 17A.1 落点判据:按依赖拆,非按功能域拆
- **开源(Avernet / community)**:领域模型 + Protocol 契约 + 业务主路 + local/Noop impl + SQLite 自包含;task loop 内核(领域/状态机/事件/TaskService/TaskScheduler/BBS 核心);自定义协作 workflow 编译注入。**本 plan §3-§14 的领域/服务/状态机/事件/graph_adapter 数据面改动全部落 Avernet `src/backend/src/agentclaw/community/core/task/`(开源)。**
- **corp(ocb)**:prod impl(ZDAS/ZCache/Mist/ARCA/MOSN/Buservice adapter)、真发渠道(钉钉卡片真发 / `<AixUI panel>` chat-WS)、**corp-only 管理前端(含副屏画布 `TaskWorkflowView` 主开发)**、corp-only 私有业务(ARCA/AntCode/collab-auth)、**SKILL 算法/prompt(红线不入 task 模块)**。
- spec/plan/tasks 三份按此判据标 `[开源]`/`[corp-only]`,不按功能域粗粒度拆(§14 已标)。

### 17A.2 镜像方向:Avernet 创作 → 用户 sync ocb-public,绝不直接改 ocb-public
- 开源代码一律在 Avernet 对应路径创作;完成后由用户手动 sync 到 `ocb-public/`(只读 submodule 镜像)。**严禁直接 Edit/Write/cp 到 ocb-public 工作树**(会被下次 sync 覆盖丢失)。验证方向:Avernet 改 → 用户 sync → ocb/singlebox 验证。

### 17A.3 不落 ecb;bcsfuse 不作依赖
- 新增通用功能**不落 `src/ecb`**(不在 ocb-public submodule,且含特定业务域)。本设计落 `core/task/`,不碰 ecb。
- **bcsfuse 仅作参考,task loop 不依赖它**。R1(目标理解)/R2(搜推)/R3(验收 judge)全走 community 本地 impl + owner-bot SKILL,**无 corp bcsfuse httpx adapter**;`corp/core/task/services/bcsfuse_*_service.py` 作废;corp yaml 不配 bcsfuse base_url。

### 17A.4 api 层零 core 依赖(api service-api 规矩)
- task api Protocol(`TaskServiceProtocol`/`TaskSchedulerProtocol`)在 `api/task/service_api.py`,**方法签名全用 `*args, **kwargs -> Any`**,api **import 零 core**;`api/task/__init__.py` 只 re-export 这 2 个 api Protocol。
- **Port Protocols(Discover/Decomposer/Driver/Execution/BcsCollab/Panel/PanelEventPublisher/BbsExecutor)+ core 内部 TaskService/TaskScheduler Protocol 留 `core/task/protocols.py`**,core/plugins/DI 直接从 core 导入,不经 api re-export。
- 本 plan §4.3 新增的图操作 API(`add_node/add_edge/update_state/retrieve_state/snapshot`)上 **core `TaskService` Protocol**;api 层不改签名形态(仍 `*args/**kwargs`)。core↔api 靠 DI 解耦,守 `test_task_service_api_conformance.py`(AST 查 ImportFrom,断言 api 不 import `agentclaw.community.core.*`)。

### 17A.5 副屏:本 plan 仅覆盖后端数据面;画布渲染归 corp 前端
- **副屏画布 `TaskWorkflowView` 主开发在 corp 前端**(`@alipay/aix-chat-ui` + `@xyflow/react`),非 Avernet 开源——原因:corp 产品页 `singlebox --dev` 端到端测试,corp 前端不能 import 开源 `@aix-chat/ui`(双 SDK 冲突)。Avernet 开源版后补同构画布或暂留数据面+Noop。
- 本 plan §7.1/§14 的 `_node_view`(`render_kind`/state 分区)/`graph_adapter` 改动是**后端数据面投影(开源)**;**不改 `src/frontend/`(前端团队独占)**,不写画布组件。副屏面板走后端 assets UMD(Avernet `src/backend/assets/task-panel/`,skill emit `<AixUI type=panel component=taskPanel.TaskWorkflowView cdn=...>`),零前端改动。

### 17A.6 搜推泛化语义;Port 保留,invocation 迁节点
- `BotDiscoverPort.recommend` 是**泛化执行方发现**(单 bot / 协作群 / 多 bot 拼合),产 `RouteRecommendation(route_class, run_mode=SINGLE_BOT|COOP_GROUP|BBS, candidates, confidence)`。真功能缺口 = community 真 impl(现 `BotDiscoverService` 规则 cover)。
- spec v2 的"搜推回到 bot SKILL"= **`BOT_SEARCH` 节点的 owner/exec-bot SKILL 调用 `BotDiscoverPort` 能力 + 回投结果落图**,非删 Port、非 bot 自实现搜推。`BotDiscoverPort` 留 community Port,invocation 从 `scheduler.tick` inline 迁到 `BOT_SEARCH` 节点(§18.1-1)。

### 17A.7 watchdog 计数有意留 node.properties;不强行迁 State
- 现行 `running_ticks/probe_count/redrive_count` 在 `Node.properties` 是 **6.5 异步自上报 + tick 看门狗模型的有意落点**(tick-based 超时,无 wall clock)。本 plan 不强行迁 `SubtaskState`;`SubtaskState.status` 只承载实体生命周期状态,watchdog 探活计数暂留 `Node.properties`(§18.1-14 软化为"可后续收编,非本期")。

### 17A.8 recognition UI 走侧屏 UMD,非 inline card
- 任务识别(n1/n2)UI 走侧屏 UMD 面板(`TaskRecogView`,Avernet `src/backend/assets/task-panel/`),**禁 inline card**(`@alipay/aix-chat-ui` `IframeSandbox` ResizeObserver 崩 → 全页 reload;崩点不在可改面)。本 plan 不改 recognition UI;若实现触及 recognition 节点,SKILL emit `type=panel component=taskPanel.TaskRecogView`,不 emit `cardId`。

---

## 18. 代码现状核对与实现变更地图(实现前校准)

> **定位:设计(spec v2 + 本 plan)是权威目标,不推翻。** 本节是"把设计落到现有 `core/task/` 代码"的变更地图——精读 domain/protocols/services/bbs_executor/graph_adapter/DI/HTTP/plugins 后,标出:复用哪些已有 hook、改哪些签名/方法名、新增哪些路径、顺手修哪些 bug。设计假设与代码**一致项**不列;列的是"实现改动要对齐的代码现实",非设计再审议。
>
> **⚠ 实施者(LLM/人)硬性约束**:
> 1. 本节**不授权**修改 spec/plan 的任何设计决策(状态机、节点类型、State 模型、FR/AC、执行者映射等)。设计与代码冲突时,**以设计为准,改代码**。
> 2. 每一条都是"对**现有实现代码**做 X,以达成**设计 Y**"的动作,不是"设计 Y 需要改成 X"。
> 3. 若实施中发现设计真有 bug/缺口,**先停下反馈**,不得在代码里私自偏离设计。

### 18.1 实现变更校准(对齐现有代码,非改设计)
1. **搜推反转(最大改动)**:现 `TaskScheduler.tick` inline 调 `BotDiscoverPort.recommend(task_id,node_id)->RouteRecommendation`(系统 Port,非 bot SKILL)。spec v2 要迁到 `BOT_SEARCH` 节点、bot 经 `task-plan-skill` 调搜推 → 实现拆出 inline 调用,`tick` 不再直接搜推。
2. **`DecomposerPort` 现签名** `decompose(task_id)->Plan`(`DecomposerService` 规则分句,confidence 0.55/0.75/0.9,无 LLM);非 v1 `decompose_node`、非 v2 `decompose(spec,state)->list[SubTaskSpec]`。退单签名是真实改动;`DecomposerService` 用 plain `__init__(Optional[TaskRepo])`(非 `@inject`),改签名时定 repo-vs-spec 传参。
3. **`ExecutionPort` 真实方法名** `dispatch_single_bot/coop_group/redispatch_node/probe/bbs`;另有 **`TaskDriverPort`**(`dispatch_node/redispatch/escalate_to_bbs`)。plan §4/§9 的 `dispatch/dispatch_bbs` 为缩写;实现用真实名,理清 driver(路由级)vs execution(bot 启动级)分工,两条 bbs 缝(`ExecutionPort.bbs` + `TaskDriverPort.escalate_to_bbs`)择一归口。
4. **BBS 衔接 net-new(比原设计更空)**:`progress_snapshot` **不存在**(不是删除,是 add `retrieve_state`);goal-FAIL 现仅 park `AWAITING_HUMAN_ACCEPT`,**无升 BBS / 无 `AWAITING_HUMAN_ACCEPT→ON_PLAZA` 转移**;`BbsExecutorService.claim/post_progress` 已存在但**无调用方**;`ExecutionPort.bbs()` 全 stub;`escalate_to_bbs` Noop。
5. **runtime 分解现仅在 acceptance-fail `_split_node` 触发**(`route()` C4 在 tick 是死分支);spec v2 "搜推未匹配→decomposition" 需**新增触发路径**,非仅"移除 inline"。
6. **`Task.owner_bot_id` vs 现有 `ExecutionMeta.owner_bot`**:后者 `owner_bot: Optional[str]` 已部分存在。顶层 `owner_bot_id` 作权威(SSOT,`OwnerResolver.resolve_task_owner` 读它),`ExecutionMeta.owner_bot` 作录入期初值或弃用。
7. **scheduler 现直接 `_svc._task_repo.save(task)`**(绕过 `on_event`)→ 破坏"唯一写口"。重构收敛:图/状态变更经 `on_event` fold + `TaskRepo.save`,scheduler 不裸 save。
8. **`mark_graph_status` 现无 guard**(直接赋值,唯一未守卫状态改口)→ §5.1 加 `require_graph_transition` guard。
9. **`TaskService` Protocol 缺图 API**:`add_node/add_edge/update_state/retrieve_state/snapshot` 净新增;现 impl 有 `spawn_build_dag/add_sibling_node/set_node_status/get_task_graph/get_node_detail/get_sub_dag/mark_terminal` 等 impl-only 方法被 scheduler 直调 → 把图操作 API 显式上 Protocol,消类型缺口。
10. **HTTP**:`POST /escalate-bbs`、`/cancel` 不存在;`TaskService.cancel` 是 service 方法未暴露;回投走 `POST /tasks/{id}/events`(envelope `{task_id,kind,seq,payload}`)。升 BBS 确认:加新路由 **或** 走 `/events` 回投确认事件(倾向后者,统一通道)。
11. **ORM `ac_task.status` 默认 `"intake"` ≠ `TaskStatus.DRAFTING="drafting"`** → latent bug,顺手订正默认值。
12. **`_handle_node_failed` retry 仅 `set_node_status(RUNNING)` 不 dispatch**(等下 tick 重新搜推,可能复选失败 bot)→ 重构定 retry 语义(倾向同执行方 inline 重派有限次)。
13. **graph_adapter**:`_node_view`(顶层,在 TaskService)与 `_to_node_view`(SM sub-DAG,在 graph_adapter)两个 projector;均无 state 分区 / `render_kind` / `judge_outputs`(现 fold 进 `acceptance_result`);顶层 `_edge_view` 无 `outcome/guard`。O-P5 净新增,复用 `SmGraphAdapter` pure-function 模式。
14. **watchdog 计数**(`running_ticks/probe_count/redrive_count`)现散在 `Node.properties` untyped dict —— 按 §17A.7 这是 6.5 模型有意落点,**本期保留不迁**;`SubtaskState.status` 只承载实体生命周期状态,不收 watchdog 探活计数(后续可议)。

### 18.2 设计假设已核对一致(确认无误,略)
TaskExecutionGraph/Node/Task 字段、GraphStatus 4 态、TaskStatus 7/NodeStatus 6(PENDING/RUNNING/DONE/FAILED/SKIPPED/HUMAN_REQUIRED)、Plan/SubTaskSpec 无 depth、event-sourced `TaskRepo`/`TaskEventRepo`(append+seq 单调+snapshot)、ORM 三表(JSON blob)、`EventKind` 14 态含 deprecated `HUNG`、搜推返 C1-C5+cover%+candidates、`RouteClass`/`RunMode`/`CollabMode`/`EdgeKind`/`WatchdogAction` 枚举。

### 18.3 可复用 hook(不重造)
- goal-FAIL → BBS:扩展 `_apply_goal_verdict` 的 fail 分支(现 park `AWAITING_HUMAN_ACCEPT`);复用 `AWAITING_HUMAN_ACCEPT` 作"等人确认"门、`TaskDriverPort.escalate_to_bbs` 作上升缝、`BbsExecutorService.claim/post_progress` 作广场 mechanics、`RouteClass.C5`+`route()` 作节点级上升。
- 回投统一通道 `POST /events`;图投影复用 `SmGraphAdapter` pure-fn;审计复用 `AttemptedRecord`(已 fold route_class/trigger/outcome)。
- 快照:复用 `TaskRepo`(materialized fold)+ `ac_task_event`(时间旅行源);`ac_task_execution_graph.version` 是乐观计数,非时间旅行——time-travel 净新增,基于 event log 重放。

---

## 19. 测试策略与 mock 边界

**原则**:event-sourced fold + 状态机 guard + `aggregate_verdict` 纯函数 = **真实**(不 mock),保证图/状态演算是被测的;**外部依赖与"大模型/skill 判验"= mock**。

### 19.1 mock 清单(外部 / 推理依赖)

| 依赖 | mock 方式 | 可注入的返回/行为 |
|---|---|---|
| `BotDiscoverPort.recommend`(搜推) | fake | full-cover 命中 / partial / **未匹配** / 异常;按 (subtask,ctx) 编程返回 |
| `DecomposerPort.decompose(spec, state)` | fake | 返回 `list[SubTaskSpec]`(含 `depth=父+1`);可控 children 数/identity |
| `OwnerResolver` | fake | `resolve_group_owner`/`resolve_task_owner` 返回 self/master/task-owner;三形态可切换 |
| `ExecutionPort.dispatch/probe/redispatch` | spy | 记录派发参数(不真发);可注入 probe 结果(活/超时) |
| SKILL 判验回投(bot 大模型推理) | 直接向 `on_event` 注入事件 | `NODE_ACCEPTED`/`NODE_REJECTED`/`GOAL_VERIFIED`/`GOAL_REJECTED`/`EXEC_AGGREGATED`(verdict+unmet)——把"skill 判了什么"编程化 |
| `BbsExecutor.claim` | stub | 此阶段仅认领占位;BBS 广场执行后续 |
| `TaskEventRepo` / `TaskRepo` | in-memory 真实实现 | 真 append(seq 单调)+ 真 fold + 真 save |
| 状态机 guard / `aggregate_verdict` | **真实** | 不 mock |

> "skill 判验"mock 的本质:不调真实 bot/LLM,而是抢在 bot 回投前由测试**直接回投对应事件**,驱动 fold。这样判验结果(DONE/REJECTED/unmet)完全可控,边界可枚举。

### 19.2 测试金字塔
- **单测(纯函数/模型)**:`TaskState.fold` 归约、`aggregate_verdict`、`require_graph_transition`、`SubtaskState.status` 迁移、`Node` node_type 多态、`next_seq`。
- **契约/服务层(TaskService fold + Scheduler tick)**:注入事件序列 → 断言图/State 演算;注入 mock Port 返回 → 断言派发/触发。**主力层**。
- **场景 E2E(in-memory 全栈)**:走完一条执行链路,mock 外部 + skill 回投,断言 Task 终态 + 图拓扑 + State。

### 19.3 执行链路场景(高层,对应 spec §6/§9)
| 链路 | 覆盖要点 |
|---|---|
| L1 happy | 录入→搜推命中→派发→accept DONE→goal-verify DONE |
| L2 搜推未匹配→分解 | 搜推先行;未匹配触发 decomposition;children 并行无依赖 |
| L3 验收 fail→重路由命中 | accept REJECTED→bot-search(retrieve-state)命中→redispatch→accept DONE |
| L4 重路由未匹配→递归拆解 | 未命中→decomposition;depth+1 |
| L5 递归上限→hang→确认 | depth≥MAX→MARK_HANG→AWAITING_HUMAN_*;升 BBS / 不升 FAILED |
| L6 中间层聚合 | 父 children 全 DONE→tick 触发 exec-aggregate→父 DONE/REJECTED |
| L7 自底向上逐级 | 多层父,下层闭合才检上层 |
| L8 任务终验 | goal-verify PASS/FAIL;BBS 前 FAIL 回 gap;BBS 后 FAIL 终态 |
| L9 BBS 同图延续 | hang→确认升 BBS→BBS bot 执行/分解→逐级聚合→goal-verify |
| L10 搜推先行约束 | 未搜推不直接拆;未匹配不直接 hang/BBS |
| L11 状态机守卫 | 非法 Task/Node/Graph 迁移抛错 |
| L12 并行无依赖 | 同层 children 并行 DISPATCH;跨层 DEPENDENCY |
| L13 State 归约 | MERGE/APPEND/OVERWRITE;artifacts 去重;depth 单调 |
| L14 回溯 | snapshot 重放断点重跑;事件日志截断回滚 |
| L15 边拓扑 | e10(n7→n11)/e12(n8→n13)源点正确 |
| L16 OwnerResolver 三形态 | self/master/task-owner 同案 |
| L17 state_patch fold | 节点事件携带 patch 正确 fold 入 State |

---

## 20. 端到端测试用例集(E2E,review 用)

> **本节为你要 review 的 E2E case 集**:每条从需求录入跑到任务终态,验证一条完整执行链路 + 其分支。外部 API / 大模型 / skill 判验全 mock(按 §19.1)。**纯单测(状态机守卫、State 归约、aggregate_verdict、边拓扑、回溯、seq 单调等)不在此 review,实现时自行补,不阻塞。**
>
> mock 套路:搜推 `BotDiscoverPort.recommend` 按 (task/node) 编程返回序列;分解 `DecomposerPort.decompose` 返回可控 `list[SubTaskSpec]`(含 depth);**skill 判验 = 直接向 `on_event` 注入 `NODE_ACCEPTED/REJECTED/GOAL_VERIFIED/REJECTED/EXEC_AGGREGATED` 事件**(把"判了什么"完全编程化);`ExecutionPort`/`BbsExecutor.claim` 用 spy/stub;`TaskRepo`/`TaskEventRepo`/状态机 guard/`aggregate_verdict` 真实。每条断言:图拓扑节点/边 + `SubtaskState.status` + `Task.status` 终态 + 派发/触发调用记录。

### E2E-1 单 bot happy path(基线链路)
- **链路**:recognition→clarify→execute-start→bot-search 命中(C1)→dispatch→exec-accept DONE→goal-verify PASS→Task DONE
- **mock**:`BotDiscover`=C1 full-cover;注入 `NODE_ACCEPTED(叶子)`、`GOAL_VERIFIED`
- **断言**:图含 recognition/clarify/execute_start/bot_search/dispatch 节点 + 边 e1/e2/e3/e8;叶子 `SubtaskState.status=DONE`;`Task.status=DONE`,`graph_status=VERIFIED`
- **覆盖**:FR-GRAPH-01/06/07/07a/14;AC-S-01/08/14

### E2E-2 协作群 happy path(COOP_GROUP)
- **链路**:搜推 C3(群 cover≥1)→dispatch 群→群 owner-bot(exec-accept)→逐 subtask DONE→goal-verify PASS
- **mock**:`OwnerResolver`=master;`BotDiscover`=C3;群 owner 回投 `NODE_ACCEPTED`
- **断言**:`run_mode=COOP_GROUP` 节点;验收归 group owner;终验归 task-owner;`Task.status=DONE`
- **覆盖**:FR-GRAPH-07;AC-S-13(此阶段范围 SINGLE_BOT+COOP_GROUP)

### E2E-3 搜推未匹配 → 分解 → 子任务各自命中 → 中间层聚合 → 终验(主链路)
- **链路**:顶 task bot-search 未匹配 → decomposition(3 children,并行无依赖)→ 各 child bot-search 命中 → dispatch → exec-accept DONE → 父 exec-aggregate DONE → goal-verify PASS
- **mock**:task-spec 搜推"未匹配";children 搜推 C1;注入 3×`NODE_ACCEPTED` + `EXEC_AGGREGATED(DONE)` + `GOAL_VERIFIED`
- **断言**:decomposition 节点 + 3 children dispatch/accept 节点 + `EXEC_AGGREGATE` 节点;children 间无 DEPENDENCY 边;父 `SubtaskState.status=DONE`;`Task.status=DONE`
- **覆盖**:FR-GRAPH-05/08a/12/14;AC-S-04/11/14(搜推先行 + 中间层聚合 + 并行)

### E2E-4 验收 fail → 重路由命中(节点身份不变)
- **链路**:subtask1 exec-accept REJECTED → bot-search(retrieve-state 带上轮 gap)命中 → redispatch(同 node_id)→ exec-accept DONE → 聚合 → 终验
- **mock`:首轮回投 `NODE_REJECTED`;重路由搜推=botxxx;再回投 `NODE_ACCEPTED`+`EXEC_AGGREGATED`+`GOAL_VERIFIED`
- **断言**:`attempted_executors` 追加(节点身份不变,非新节点);检索上下文含上轮 gap;`Task.status=DONE`
- **覆盖**:FR-GRAPH-08;FR-TASK-04;AC-S-04

### E2E-5 验收 fail → 重路由未匹配 → 递归拆解(depth+1)
- **链路**:subtask1 REJECTED → bot-search 未匹配 → decomposition(subtask1-1/1-2/1-3,depth=父+1)→ 各命中 → accept → 父聚合 DONE → 终验
- **mock`:重路由搜推"未匹配";children depth+1;注入 `NODE_ACCEPTED`×3 + `EXEC_AGGREGATED(DONE)` + `GOAL_VERIFIED`
- **断言**:递归 children `SubtaskState.depth=父+1`;父经聚合闭合;`Task.status=DONE`
- **覆盖**:FR-GRAPH-05/08/09;AC-S-04

### E2E-6 递归上限 → hang → 人确认升 BBS → BBS 同图延续 → 终验(三终止 + BBS)
- **链路**:decomposition 产出 depth≥MAX → 拒 add_node → MARK_HANG → `graph_status=AWAITING_HUMAN_ACCEPT` → 人确认升 BBS → `AWAITING_HUMAN_ACCEPT→ON_PLAZA`(guard)→ BBS_DISPATCH → BbsExecutor.claim 认领后续 DISPATCH/DECOMPOSITION/EXEC_ACCEPT(BBS bot)→ 逐级 EXEC_AGGREGATE → goal-verify(task-owner)PASS
- **mock`:decompose 返 depth=MAX+1;注入 `/escalate-bbs` 确认(走 /events 或新路由,§18.1-10);BbsExecutor.claim stub 认领;BBS 阶段回投 `NODE_ACCEPTED`+`EXEC_AGGREGATED`+`GOAL_VERIFIED`
- **断言**:无新图(同一 `TaskExecutionGraph`);BBS 阶段执行/分解执行者=BBS bot,`GOAL_VERIFY` 归 task-owner;无 `progress_snapshot`;`Task.status=DONE`
- **覆盖**:FR-GRAPH-09/10;AC-S-05/10/12(三终止 + BBS 同图 + task-owner 仅终验)

### E2E-7 递归上限 → hang → 不升 → FAILED
- **链路**:MARK_HANG → 人确认不升 → Task FAILED(终态)
- **mock`:decompose depth≥MAX;注入不升确认(=cancel)
- **断言**:`graph_status=AWAITING_HUMAN_ACCEPT`→`Task.status=FAILED`;无 BBS_DISPATCH 节点;终态不回环
- **覆盖**:FR-GRAPH-09;AC-S-12

### E2E-8 goal-verify FAIL(BBS 前)→ 回 gap(限轮次)/或 MARK_HANG
- **链路**:根 subtask 聚合 DONE → goal-verify REJECTED(未升 BBS)→ 回 gap loop(限轮次)或 MARK_HANG;断言**不**直接 FAILED
- **mock`:注入 `GOAL_REJECTED`(graph 未 ON_PLAZA)
- **断言**:`Task.status` 非 FAILED(回 EXECUTING/gap 或 AWAITING_HUMAN_ACCEPT);触发新一轮 bot-search/decomposition(受轮次上限)
- **覆盖**:O-P2;FR-LOOP-01/04

### E2E-9 goal-verify FAIL(BBS 后)→ FAILED 终态
- **链路**:已升 BBS(graph ON_PLAZA)→ BBS 阶段 goal-verify REJECTED → Task FAILED(终态,不回环/不重试/不再上升)
- **mock`:BBS 阶段注入 `GOAL_REJECTED`
- **断言**:`Task.status=FAILED`;无再回环、无再 escalation
- **覆盖**:O-P2;FR-LOOP-03;AC-S-12

### E2E-10 搜推先行约束(负向断言)
- **链路**:同一 task 在搜推未调用时不应直接分解;搜推未匹配时(未触上限)不应直接 BBS/hang
- **mock**:控制 `BotDiscover` 不被调 / 返回未匹配
- **断言**:`DecomposerPort.decompose` 在未搜推时不被调;未匹配且 depth<MAX 时不落 `BBS_DISPATCH`/`MARK_HANG`,而是走 decomposition
- **覆盖**:FR-GRAPH-14;AC-S-14

### E2E-11 并行无依赖 + 混合分支(同层多子任务不同走向)
- **链路**:decomposition 出 3 children 并行:child-a 搜推命中直派;child-b 搜推未匹配再拆;child-c 验收 fail 重路由 → 三分支并行演进 → 各自闭合 → 父聚合 → 终验
- **mock`:三个 child 搜推/验收分别编程命中/未匹配/REJECTED→命中;注入对应 accept/aggregate/goal 事件
- **断言**:同层 3 children 无相互 DEPENDENCY;父在三者全闭合后才聚合;`Task.status=DONE`
- **覆盖**:FR-GRAPH-08/08a/12;AC-S-04/11

### E2E-12 看门狗超时 → probe → redrive → 节点级 escalation(C5)
- **链路**:某叶子长 RUNNING → watchdog PROBE → REDRIVE → 仍失败 → route C5 → `escalate_to_bbs`(节点级,区别于 goal-FAIL)
- **mock`:RUNNING 节点 `running_ticks`/`probe_count` 推进;probe 返回超时/失败;`route` 返回 C5
- **断言**:watchdog 计数推进(PROBE/REDRIVE/ESCALATE);C5 走 `TaskDriverPort.escalate_to_bbs`(与 goal-FAIL 的 AWAITING_HUMAN_ACCEPT 区分)
- **覆盖**:现有 watchdog 行为保留(§18.1-14);FR-LOOP-04

> E2E 计数 12 条,覆盖全部执行链路分支:happy(1/2)→ 搜推先行+分解+聚合(3)→ gap 重路由命中/未命中递归(4/5)→ 三终止 hang→升BBS/不升(6/7)→ 终验 FAIL BBS前/后(8/9)→ 搜推先行负向(10)→ 并行混合(11)→ watchdog escalation(12)。tasks.md 按 E2E-1..12 编号 TDD 实施(红→绿),每条先写 E2E 测试再实现至绿。

---

## 21. 变更记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-08-01 | 待确认 | 初版 plan:TaskExecutionGraph 全生命周期重构——`TaskState` 一等要素(分区+归约+快照)、`NodeType` 多态、`Node` 数据迁 State、`DecomposerPort` 单签名、`OwnerResolver` 两方法、`Task.owner_bot_id`、`progress_snapshot` 删、GraphStatus 守卫、事件携带 state_patch、scheduler 职责收窄、BBS 同图衔接 + task-owner 仅终验、伪代码边拓扑订正(O-3)、递归深度落 State(O-4)、scheduler.start↔n4 时序(O-6)、受影响文件清单 + 迁移 + 风险。 |
| 2026-08-01 | 待确认 | **v2 校准(对齐 spec.md v2)**:① `NodeType` 补 `EXEC_AGGREGATE`/`MARK_HANG`/`CLARIFY`(FR-GRAPH-08a/09);② 新增 §12A exec-aggregate 触发机制(O-8,系统 tick 检测 + owner 判验,自底向上逐级)+ `aggregate_verdict` 纯函数统一 `EXEC_AGGREGATE`/`GOAL_VERIFY` 完成判断(FR-GRAPH-07b);③ §11 上限处置改为先 `MARK_HANG` 等人确认、不直接升 BBS;④ §13 BBS 衔接补"升 BBS / 不升 FAILED"二分支 + BBS 阶段 EXEC_AGGREGATE 逐级闭合;⑤ §5.1 澄清 `ON_PLAZA`/`AWAITING_HUMAN_ACCEPT` 为 hang+BBS 同门;⑥ §5.2 加 `n_agg`/`n_hang` 行;⑦ §6 加 `EXEC_AGGREGATED`/`NODE_HANG` 事件;⑧ §7.2 scheduler 加搜推先行(FR-GRAPH-14)+ exec-aggregate 触发 + mark-hang;⑨ §9 执行者表补 `EXEC_AGGREGATE`/`MARK_HANG`;⑩ §16 风险加 O-P4/O-P5。 |
| 2026-08-02 | 待确认 | **v2 增订(两维度正交 + rename)**:① §2 补"Node=动作维度 / State=Task·SubTask 实体维度"两维度说明——判定动作是 Node,效果翻实体 status 是 State,非二选一;确认三个判定(exec-accept/exec-aggregate/goal-verify)均为节点。② `SubtaskStatePartition` rename → `SubtaskState`,加 `status` 字段(实体状态,动作节点 fold 驱动);`TaskState.partitions` → `TaskState.subtasks`;§3.2/§3.3/§8/§11/§12A/§14 同步。 |
| 2026-08-02 | 待确认 | **v2 增订(TDD)**:新增 §18 测试策略与 mock 边界(外部/推理依赖 mock 清单 + 测试金字塔 + 17 条执行链路场景)+ §19 TDD 用例清单(40 条,按 L1-L17 链路分组,每条含触发/mock/断言/覆盖 FR-AC)。tasks.md 据此编号 T-01..T-40 红→绿实施。 |
| 2026-08-02 | 待确认 | **v2 增订(代码现状核对 + E2E 重构)**:① 新增 §18 代码现状核对与实现变更地图——设计(spec/plan)为权威不推翻;精读 domain/protocols/services/bbs_executor/graph_adapter/DI/HTTP/plugins 后列出 14 条实现变更校准(搜推反转/DecomposerPort 现签名/ExecutionPort 真名+TaskDriverPort 双缝/BBS 衔接全 net-new 且 progress_snapshot 不存在/runtime 分解触发缺位/owner_bot_id vs ExecutionMeta.owner_bot/scheduler 裸 save 破坏唯一写口/mark_graph_status 无 guard/TaskService Protocol 缺图 API/HTTP 缺 escalate-bbs+cancel/ORM status 默认 intake bug/retry 不 dispatch/graph_adapter 无 state·render_kind·judge_outputs)+ 可复用 hook。② TDD 重构为 §20 端到端 E2E 用例集 12 条(从需求到终态全链路 + 分支,外部/LLM mock;纯单测不在 review,自行补);章节重编号 §18→代码核对 / §19→测试策略 / §20→E2E / §21→变更记录。 |
| 2026-08-02 | 待确认 | **v2 增订(ocb memory 实现规矩)**:新增 §17A 实现规矩与落点判据(落点按依赖拆非功能域/Avernet 创作→用户 sync ocb-public 绝不直改/不落 ecb/bcsfuse 不依赖/api 层零 core 依赖+Port 留 core/副屏画布 corp 前端+本 plan 仅后端数据面/搜推泛化语义 Port 保留迁节点/watchdog 计数留 node.properties/recognition UI 侧屏 UMD);§14 文件清单加 `[开源]`/`[corp-only]` 标注 + DI 行 + 副屏画布 corp 行;§18.1-14 watchdog 软化为本期不迁。 |