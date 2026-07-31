# 目标驱动任务执行 loop — 技术设计计划(plan.md)

> 配套 `spec.md`(WHAT/WHY)。本文给 HOW:领域模型、服务与依赖、执行/路由/验收/重规划 loop、协作模式、Skill 契约、代码落点、跨模块需求。
> 落点域:ocb backend,具体位置见 §7(对照 `src/backend/README.md` 四层 `api→core→plugin_api→plugins` 单向依赖)。
> 任务状态机定义见 `2026-07-30-task-status-state-machine-alignment/`(7 态 / 6 节点态权威定义);本文不重复枚举表,仅在流转点引用态名。
> 日期:2026-07-28。

---

## 0. 关键决策(D1~D9)

| # | 决策 | 取向 |
|---|---|---|
| D1 | 范式 | 目标驱动动态 workflow(deepresearch 式)+ 渐进式任务契约(5 要素,创建可不全,执行中补全) |
| D2 | 代码落点 | backend `core/task/` 独立二级目录(community/corp 双 Profile DI) |
| D3 | 三执行模态 + C1~C5 路由 + deepresearch 两 loop + 全局终验 BBS | 见 §4/§5;**无逐级降级链**,三模态并列 |
| D4 | Skill 归属 | 8 Skill 接口契约归本系分;实现归 skill_center + engine |
| D5 | 复用优先 | 经 Port 契约解耦复用 BCS/bcsfuse/engine/workflow-engine-plugin;不重造 |
| D6 | 终止性兜底 | 节点级 loop_round/recompose_count/连续无进展 + task 级 loop_round 总上限 → 强制全局终验 |
| D7 | 图谱覆盖三模态混合 | Node 范式无关,`run_mode` 标签;同批 Node 各自路由;同 Node 重路由(非派生新实体 / 无降级继承) |
| D8 | 图谱 ↔ workflow yaml 双向 | 三入口(A 用户指定 / B 沉淀 / C 运行期 LLM 自动);统一编译成 workflow-engine 格式 yaml |
| D9 | 副屏画布形态 | 新建独立动态 workflow 画布(参考 `bcsPanel/StateMachineRunView` 实现扩展),非复用 state_machine 画布;任务领域模型(定义/调度/执行图谱)+ API 为 cover 可视化的主扩展面,画布为消费者。下钻协作群节点用路 A 渲染期映射:任务图谱持 `SubDagRef`(bcs_run_id 引用),不持久化 child 态,后端实时拉 BCS SM run graph 经 `SmGraphAdapter` 映射成 `TaskGraphView` 子树。TaskGraphView 是 state_machine 画布信息的超集(§1.3b 对照表),自定义协作群=workflow 固化特例同画布展示。信息字段对照由代码分析得出(AC-12) |

---

## 1. 领域模型(domain)

> 落点:*community* `core/task/domain/`。领域边界见 §7.4。

### 1.1 Task(唯一聚合根,一等公民;spec 定义面 + plan + execution_graph 执行态面)

> Task 为唯一聚合根,内部分 `spec`(定义面,5 要素)+ `plan`(拆分产出物,聚合根字段)+ `execution_graph`(执行态面)。生命周期在单聚合内闭环。Task.status(7 态)是 execution_graph **根节点相位**,归 TaskService 状态组(`advance_phase`)推进。`plan` 是 `Task` 聚合根字段(ORM 落 `ac_task.plan_json` 列),作为 spec(要什么)与 execution_graph(运行态 DAG)之间的桥;`TaskSpec` 不持 plan。

```
Task:                                    # 唯一聚合根(顶层心智对象 1)
  id, user_id, source(IM|API|CLI|MINING)
  spec: TaskSpec                          # 定义面:5 要素(无 plan)
  plan: Plan | None                       # 拆分产出物(聚合根字段;DecomposerPort 产,approve 时消费建图;ORM plan_json)
  execution_graph: TaskExecutionGraph | None   # 执行态面(approve 后生成,根相位=Task.status)
  latest_event_seq, loop_round, created_at, updated_at

class TaskSpec:                           # 定义面(描述"该做什么";Task 子结构,不含 plan)
  metadata: {id, title, properties}
  context: {background, motivation, constraints[HARD|ASSUMPTION], properties}
  goal: {objective, acceptances[AcceptanceCriteria(多态袋)], properties}
  deliverables: [Deliverable{type[CODE|API|WEB_UI|DOC|CONFIG|MIGRATION], location, properties}]
  execution: ExecutionMeta | None         # 渐进式 {executor?, solution?, collaboration_mode?, arguments?}

class Plan:                               # Task 聚合根字段(SKILL-任务拆分 驱动 DecomposerPort 产)
  sub_tasks: [SubTaskSpec]; edges: [EdgeSpec]; confidence: float  # <0.7 打回澄清
```

`AcceptanceCriteria.kind ∈ {BEHAVIOR|PERFORMANCE|INVARIANT|COMPLIANCE|USE_CASE}`,`properties` 为多态袋。

### 1.2 TaskStatus(7 态)= execution_graph 根节点相位

> 7 态枚举与合法迁移见 `2026-07-30-task-status-state-machine-alignment/` §2。终态 = DONE / CANCELLED / FAILED。

```
DRAFTING ──(四要素齐 + finalize_plan)──► DEFINED
DEFINED   ──(approve)──────────────────► EXECUTING
EXECUTING ──(全节点 settled)────────────► REVIEWING
REVIEWING ──(验收通过)─────────────────► DONE(终态)
REVIEWING ──(验收不通过,返工)──────────► EXECUTING    ↺
EXECUTING ──(失败:原子终止 / 节点升级无重规划余地)──► FAILED(终态)
任意非终态 ──(用户否决)─────────────────► CANCELLED(终态)
EXECUTING → EXECUTING 自环(tick 推进 / 子任务汇聚,合法)
```

**Task.status 是 execution_graph 根节点相位**,录入/计划/build/审批/终验/取消 都是根相位推进,归 TaskService 状态组(`advance_phase`)。推进点:`create`=DRAFTING;`amend` 仍 DRAFTING(要素补全期不切态);`finalize_plan`→DEFINED;`approve`→Scheduler.start→DEFINED→EXECUTING;全节点 settled→REVIEWING;验收通过→DONE / 验收不通过→返工 EXECUTING;否决→CANCELLED;失败→FAILED。"被 hung 住 / 上升等人工"不设任务级状态,由节点级 `HUMAN_REQUIRED` 承载(任务整体留 EXECUTING)。

### 1.3 TaskExecutionGraph(执行态图谱,Task 内子结构,层次化,动态生长)

> `TaskExecutionGraph` 是 Task 内子结构,**层次化**:根节点相位=Task.status;build 相位下钻 deepresearch DAG(Node[]);state_machine 协作模式下钻 workflow sub-DAG(群自闭环)。状态增删改查归 TaskService 状态组(唯一写口)。

```
TaskExecutionGraph:
  root_phase: TaskStatus                  # 根节点相位(= Task.status;录入/计划/build/审批/终验/取消 推进)
  graph_status: GraphStatus               # ON_PLAZA | AWAITING_HUMAN_ACCEPT | AWAITING_HUMAN_ADJUST | VERIFIED
  loop_round: int
  nodes: [Node]; edges: [Edge]            # Edge 一等实体;build 相位下钻的 deepresearch DAG

Node:
  node_id, spec, targets_acceptance, targets_deliverable
  artifacts: [ArtifactRef]                # 单值最新态
  status: NodeStatus                      # 见下
  run_mode: SINGLE_BOT|COOP_GROUP|BBS     # 范式标签
  assignee, instruction
  attempted_executors: [AttemptedRecord]  # 仅记录,重路由不排除;含 route_class/from_mode/to_mode/trigger
  properties:{retry_count, error_msg, partial_outcome, unmet_criteria, loop_round, started_at, completed_at, human_approver, max_attempts}
  sub_dag: SubDagRef | None               # 协作群模态节点:持有外部 run 引用(见 §1.3a),不持久化群内 child 态;副屏下钻经 SmGraphAdapter 实时拉取映射(§1.3a/§1.4b)

NodeStatus: PENDING | RUNNING | DONE | FAILED | SKIPPED | HUMAN_REQUIRED   # 6 态(见 2026-07-30 spec §3.3)
  # FAILED = 验收不过(NODE_REJECTED)与执行报错(NODE_FAILED)合一;区分下沉到节点属性 acceptance_result / failure kind
  # SKIPPED = 拆解后父 Node 委托 sibling
  # HUMAN_REQUIRED = 验收/终验回投需人工(任务级不设 HUNG,由此态承载)
Edge: edge_id, from_node, to_node, kind(DEPENDENCY|CONDITIONAL|FALLBACK|PARALLEL_SYNC), properties
AttemptedRecord: executor_id, paradigm, round, outcome(仅记 FAIL 类), route_class(C1~C5), from_mode, to_mode, trigger(ROUTED|REPLANNED|ESCALATED_TO_BBS), at, note
```

### 1.3a 协作群模态节点的 SubDagRef(下钻引用模型,路 A 渲染期映射)

> 协作群模态(root 节点 run_mode=COOP_GROUP 且 collab_mode=state_machine)的 Node **不持久化群内 child 节点态** —— 群自闭环、不逐步回投。任务图谱只持引用(`SubDagRef.bcs_run_id`),不存 child 态,避免双写与悬空 PENDING。
>
> **下钻的两种展示形态(展示位置澄清,§1.4b)**:
> - **任务入口页(用户提需求的单 bot / 协作群入口)副屏**:展示**任务整体执行流程**——顶层 deepresearch 动态 DAG(根相位 + 三模态节点)。此处的"下钻协作群节点"是**跨页导航**:从任务页跳转到该协作群页,该群页副屏自然展示其内部 sub-DAG workflow(即现有 `bcsPanel/StateMachineRunView` 画布,无需在任务画布内展开)。
> - **协作群页(及其它执行上下文)副屏**:只展示**该群自己的执行子 DAG workflow**(state_machine 画布),不展示任务顶层 DAG。
> - 任务页副屏"下钻"仍提供 `get_sub_dag` API(§1.4b)作为**只读预览/同页内联展开**的可选项(不持久化、实时映射),但**主交互是跨页导航到协作群页**;两套画布在不同页面各自独立,不强行把群内 sub-DAG 画进任务画布。

```
SubDagRef:                              # Node.sub_dag,协作群模态节点的下钻引用
  ref_kind: "bcs_state_machine"         # 引用目标类型(目前仅 state_machine;可扩展)
  bcs_run_id: str                       # BCS state_machine run id(sm-xxx)
  group_id: str                         # BCS group id(校验/反查)
  workflow_yaml_snapshot: str | None    # 注入时的 yaml 快照(审计/复现用;不参与 live 态展示)
  # 不含 child nodes/edges —— live 态由 SmGraphAdapter 实时拉取映射
```

**SmGraphAdapter**(community `core/task/services/graph_adapter.py`,纯映射无副作用;被 TaskService 查询组 / HTTP 查询端点调):
```
class SmGraphAdapter:
  def __init__(self, bcs_collab_client: BcsCollaborationProtocol)  # 注入:调 BCS graph 端点(corp impl httpx;local impl mock)
  def fetch_run_graph(self, bcs_run_id) -> BcsSmGraphSnapshot       # GET /state-machine-runs/{id}/graph
  def to_sub_dag_view(self, snap: BcsSmGraphSnapshot) -> TaskGraphView
    # StateMachineRunGraphView → TaskGraphView 子树:
    #   snap.nodes → TaskNodeView[](含 status/sub_status/attempt/artifact/judge_outputs 等,见 §1.3b 对照)
    #   snap.edges → TaskEdgeView[](source→target,outcome→edge.properties.outcome,guard→properties.guard)
    #   snap.definition → 顶部展示元(graph_mode/initial_nodes)
```

**为什么映射而非直透**:TaskGraphView 是任务图谱的统一展示模型(覆盖三模态),state_machine graph 是其子集(§1.3b)。映射保证**同一画布**既能画任务顶层 deepresearch DAG,又能下钻画群内 SM sub-DAG,字段同构、交互一致(点节点看详情)。映射在渲染期做、不落库,实时性由画布轮询(对齐现有 StateMachineRunView 轮 `/graph`)或任务 WS 进度流触发刷新。

### 1.3b 节点展示字段超集与 AC-12 信息对照表

> 任务图谱的 TaskNodeView / TaskEdgeView 是 state_machine 画布信息的**超集**:state_machine 的全部展示字段在任务图谱有对应落点(满足 AC-12);任务图谱额外承载 deepresearch 动态 DAG 字段(拆解/重路由/验收证据/模态标签/attempted 执行方历史等)。

**字段对照表(state_machine 画布信息 → 任务图谱展示字段)**:

| state_machine 画布字段(BCS `StateMachineRunGraphView`/`NodeRunView`/前端 `StateMachineRunView`) | 任务图谱展示字段 | 说明/映射 |
|---|---|---|
| `run.run_id` | `SubDagRef.bcs_run_id`(下钻引用)/ `Node.node_id`(顶层节点自身) | 顶层节点=node_id;下钻 SM run=bcs_run_id |
| `run.status`(pending/running/completed/failed/aborted) | `task.root_phase` + `Node.status` | 根相位对应 run 状态;节点态映射见下 |
| `run.input` / `run.output`(final_output) | `Task.spec` 输入 / `Node.artifacts` + 终验 `Task.output` | final_output 节点 artifact → 任务交付 |
| `definition.id/version/name/graph_mode/initial_nodes` | `SubDagRef.workflow_yaml_snapshot` + 画布顶部展示元 | yaml 快照含定义;graph_mode/initial_nodes 映射为展示元 |
| `node.node_id` | `TaskNodeView.node_id` | 直映 |
| `node.display_name` | `TaskNodeView.display_name` | 直映(Node.spec 派生) |
| `node.kind`(bot_task/human_input/...) | `Node.run_mode` + `Node.collab_mode` | SM kind 是任务模态的子集语义 |
| `node.assignee` / `assignee_bot_id` | `Node.assignee` / `AttemptedRecord.executor_id` | 当前执行方 + 历史 |
| `node.final_output` | `Node.properties.is_final_output` | 标记 |
| `node.status`(pending/ready/running/completed/failed/retry_scheduled/skipped) | `NodeStatus`(PENDING/RUNNING/DONE/FAILED/SKIPPED/HUMAN_REQUIRED) | 状态映射见 §1.3c |
| `node.sub_status`(awaiting_response/judging) | `Node.properties.sub_status` | 直映(超集保留) |
| `node.attempt` | `Node.properties.retry_count` + `AttemptedRecord.round` | 当前轮次 + 历史 |
| `node.started_at` / `completed_at` | `Node.properties.started_at` / `completed_at` | 直映 |
| NodeDetail `artifact_text` | `Node.artifacts[].text` / 引用 | 产出文本/引用 |
| NodeDetail `error` | `Node.properties.error_msg` | 失败原因 |
| NodeDetail `node_timeout_ms` / `max_attempts` | `Node.properties.max_attempts`(+timeout) | 超时/重试上限 |
| NodeDetail `delivery_request_id` / `bot_delivery_run_id` | `AttemptedRecord.properties` | 投递关联(审计) |
| `judge_outputs[].decision` | `Node.acceptance_result`(验收证据) | judge 裁决→验收结果 |
| `edge.source/outcome/target` | `TaskEdgeView.from_node/properties.outcome/to_node` | outcome 作为条件边属性 |
| `edge.guard` | `TaskEdgeView.properties.guard` | 直映 |

**任务图谱超集字段(state_machine 无,deepresearch 动态 DAG 独有)**:
`Node.run_mode`(三模态标签)、`Node.collab_mode`(三协作模式)、`Node.targets_acceptance/targets_deliverable`(验收/交付契约)、`Node.attempted_executors[]`(重路由执行方历史)、`Node.properties.{partial_outcome,unmet_criteria,loop_round,human_approver}`、`Edge.kind`(DEPENDENCY/CONDITIONAL/FALLBACK/PARALLEL_SYNC)、拓扑序/并行汇聚语义(`dispatch_ready_targets` 的"上游全完成才派发",映射到 `Edge.kind=PARALLEL_SYNC`)。

### 1.3c 状态映射(state_machine NodeStatus → 任务 NodeStatus;6 态)

| state_machine | 任务 NodeStatus | 备注 |
|---|---|---|
| pending | PENDING | 未解锁 |
| ready | PENDING(已解锁待派发) | 任务图谱用 Edge 拓扑表达 ready,不单列态;或并入 PENDING+properties.ready |
| running | RUNNING | 执行中 |
| completed | DONE | 完成 |
| failed | FAILED | 执行报错(重试耗尽)/ 验收不过,合一 |
| retry_scheduled | FAILED(acceptance_result=fail,待重路由/重试) | 验收不过待重路由由 FAILED + 节点属性 + compute_gap 表达 |
| skipped | SKIPPED | 拆解后父委托 sibling / 分支裁剪 |
| (SM 无) | HUMAN_REQUIRED | 任务图谱独有(等人工确权) |
| sub_status awaiting_response | properties.sub_status=awaiting_response | 子状态保留 |
| sub_status judging | properties.sub_status=judging | 子状态保留 |

### 1.4 事件 / 副屏投影 / 仓库

> `TaskEvent` 覆盖执行事件与 owner-bot 回投事件(kind + payload 来源字段区分回投来源)。2 个 Repo:`TaskRepo` 持 spec+plan+execution_graph;`TaskEventRepo` 持事件流。`ProgressProjector` 是 TaskService 查询组内的只读投影。

```
TaskEvent: task_id, seq, kind, reported, payload, occurred_at
  # reported=True 标 owner-bot SKILL 回投(ACCEPTANCE_* / GOAL_VERIFIED / GOAL_REJECTED / NODE_FAILED);
  # 其余为系统驱动(Scheduler dispatch / reroute / cancel / human-required escalation)
  # occurred_at 由 ORM repo 从 gmt_create 回填,供 GET /tasks/{id}/history trace
EventKind:
  TASK_CREATED | SPEC_AMENDED | PLAN_FINALIZED |
  NODE_DISPATCHED | NODE_RUNNING | NODE_ACCEPTED | NODE_REJECTED | NODE_FAILED |
  GOAL_VERIFIED | GOAL_REJECTED | LOOP_REROUTED | EXECUTION_ATTEMPTED |
  CANCELLED | HUNG(deprecated,无 writer,仅保留以读历史日志)
ProgressNode(只读投影,TaskService 查询组内产): node_id, seq, way, artifact, status, external, jump_target
Repos(Protocol): TaskRepo(create/get/update/list;持 spec+plan+execution_graph)/ TaskEventRepo(append/list_by_task/get_latest_seq;事件流;append 内部单 writer 分配 seq=get_latest_seq+1)
```

### 1.4b 副屏投影(TaskGraphView 展示模型 + 查询/下钻/详情 API)

> 副屏动态 workflow 画布(新建独立画布,参考 `bcsPanel/StateMachineRunView` 实现)消费 **TaskGraphView**——任务图谱的统一只读展示模型,是 §1.3b 的 state_machine 画布信息超集。TaskService 查询组产 TaskGraphView;SmGraphAdapter(§1.3a)负责把下钻的 BCS SM run graph 映射进同一模型。事件驱动增量 + 画布轻量轮询兜底(对齐现有画布轮询模式)。

```
TaskGraphView(只读,查询组产;画布唯一数据契约):
  task_id, root_phase(TaskStatus), graph_status, loop_round
  definition_meta:{name, graph_mode, initial_nodes}        # 顶层展示元
  nodes: [TaskNodeView]
  edges: [TaskEdgeView]

TaskNodeView:
  node_id, display_name
  run_mode(SINGLE_BOT|COOP_GROUP|BBS), collab_mode(CHAT|MANAGER_WORKER|STATE_MACHINE)  # 模态标签
  status: NodeStatus, sub_status(awaiting_response|judging|None)
  assignee, attempted_executors: [AttemptedRecord]         # 当前 + 重路由历史
  artifacts: [ArtifactRef], acceptance_result              # 产出 + 验收证据(judge_outputs 落此)
  properties:{retry_count, error_msg, partial_outcome, unmet_criteria, started_at, completed_at,
              max_attempts, human_approver, is_final_output, ready}
  sub_dag_ref: SubDagRef | None                            # 协作群模态:下钻引用(非 child 态)

TaskEdgeView:
  edge_id, from_node, to_node, kind(DEPENDENCY|CONDITIONAL|FALLBACK|PARALLEL_SYNC)
  properties:{outcome, guard}                              # outcome/guard 对齐 SM 画布

TaskNodeDetailView(点节点看详情;查询组产):
  # = TaskNodeView 全字段 + 投递关联(delivery_request_id/bot_delivery_run_id 落 attempted_executors.properties)
  # + 验收 evidence 细节(judge_outputs/双轨断言结果)+ 重路由上下文(历次 attempted.note)
```

**查询组方法(TaskService 查询组,只读)**:
```
def get_task_graph(task_id) -> TaskGraphView                # 顶层动态 DAG(根相位 + build 相位 nodes/edges)
def get_node_detail(task_id, node_id) -> TaskNodeDetailView # 节点执行详情(对齐 SM 画布点节点)
def get_sub_dag(task_id, node_id) -> TaskGraphView          # 协作群节点下钻:
    #  1) 读 Node.sub_dag_ref(SubDagRef);非协作群模态或无引用 → 404/skip
    #  2) SmGraphAdapter.fetch_run_graph(bcs_run_id) → to_sub_dag_view 返回(实时态,不落库)
    #  3) 调用 BcsCollaborationProtocol(corp impl httpx BCS;local impl mock/桩)
def get_task_history(task_id, after_seq=0) -> list[TaskEvent]  # append-only 事件流 trace(GET /tasks/{id}/history)
def subscribe_task_graph(task_id) -> AsyncIterator[TaskGraphView]  # WS 增量:TaskEvent 落态后推快照
    #  增量为主(事件驱动,FR-OBS-07);画布侧轻量轮询 get_task_graph 兜底
```

**`BcsCollaborationProtocol`**(查询面 Port;community api 层有 Service API Protocol 镜像,Port 契约落 `core/task/protocols.py`):`fetch_state_machine_run_graph(run_id)` / `fetch_node_detail(run_id,node_id)`,只读拉 BCS state_machine run graph/node 端点;corp impl httpx,local impl mock。仅查询面,不持写态。

**画布契约**:新建独立动态 workflow 画布(参考 `bcsPanel/StateMachineRunView` 实现扩展),消费 `GET /tasks/{id}/graph` + `GET /tasks/{id}/nodes/{nid}` + `GET /tasks/{id}/nodes/{nid}/sub-dag` + `GET /tasks/{id}/history` + WS `/tasks/{id}/graph/stream`;字段同构 TaskGraphView,模态节点按 run_mode/collab_mode 渲染标签,协作群节点下钻调 sub-dag 端点。**信息维度完全 cover state_machine 画布**(§1.3b 对照表),自定义协作群作为"workflow 固化特例"在同一画布展示。

**副屏展示位置与触发(展示语义澄清)**:
- **两套画布分置不同页面**(非同画布切换):
  - **任务入口页(用户提需求的单 bot / 协作群入口)副屏** → 展示**任务整体执行流程**(顶层 deepresearch 动态 DAG:根相位 + 三模态节点 + 下钻入口)。
  - **协作群页(及其它执行上下文)副屏** → 只展示**该群自己的执行子 DAG workflow**(即现有 `bcsPanel/StateMachineRunView` 画布,不展示任务顶层 DAG)。
- **触发弹出(对齐自定义协作群模式)**:任务创建时(`TaskService.create` → `init_root_phase(DRAFTING)`)后端发一条 `<AixUI panel>` 面板消息驱动副屏自动弹出(复用现有 `chatBridge.openPanelTab` → `ChatLayout.openTab` → `setIsOpen(true)` 机制,参考 BCS `publish_state_machine_panel_event`);component 指向新画布(如 `taskPanel.TaskWorkflowView`),params 带 `taskId`。后续根相位推进/节点状态变可通过同一通道 `emitPanelEvent` 增量刷新,或画布轮询 `GET /tasks/{id}/graph` 兜底。
- **下钻协作群节点 = 跨页导航**:任务页点协作群节点 → 跳转到该协作群页 → 该页副屏自然展示群内 sub-DAG(无需在任务画布内展开)。`get_sub_dag` API(§1.4b)作为任务页内**只读预览**可选项(实时映射、不持久化),主交互是跨页导航。

---

## 2. 服务与依赖对象(services)

> 单一 `TaskService`(Task 聚合 Application Service,内分定义/状态/查询三组 + on_event)+ `TaskScheduler`(编排)+ Port + WorkflowCompiler 工具。Task.status 是 execution_graph 根节点相位,归 TaskService 状态组推进。落点:*community* `core/task/services/`,corp impl `corp/core/task/services/`。

### 2.1 TaskService(单一聚合服务,内分三组 + on_event)

> **守门**:TaskService **不跑 loop、不 dispatch、不判定验收、不持编排决策** —— 编排(含路由/重规划/选模式决策 `_route`/`_select_collab`/`_compute_gap`)归 TaskScheduler,执行归 ExecutionPort,判定归 owner-bot SKILL。TaskService 只做 CRUD+写态+读+委托。内部三组方法不互相调复杂逻辑(查询组只读 Repo,不经状态组)。

```
class TaskService:
  # —— 定义组(spec/plan CRUD;接 HTTP)——
  def create(user_id, intent, spec_draft) -> TaskId             # new Task(plan=None)+ Repo + init_root_phase(DRAFTING);★ 发 <AixUI panel> 面板消息触发副屏弹出(经 PanelEventPublisher+PanelDeliveryPort carrier,FR-OBS-11)
  def amend(task_id, deltas) -> Task                            # 要素补全期 amend,任务留 DRAFTING(R2,不切态)
  def finalize_plan(task_id, plan: Plan) -> Task                # task.plan = plan;save;advance(DEFINED);emit PLAN_FINALIZED
  def approve(task_id, user_id) -> Task                         # 校验 plan ready → 委派 TaskScheduler.start
  def cancel(task_id, reason) -> Task

  # —— 状态组(执行态唯一写口;根相位 + 下钻 DAG + sub-DAG)——
  def init_root_phase(task_id, phase)
  def advance_phase(task_id, new: TaskStatus)                   # 根相位推进(录入/计划/build/审批/终验/终态);状态机自验
  def spawn_build_dag(task_id, plan: Plan)                      # build 相位下钻骨架(Scheduler.start 命令)
  def set_node_status(task_id, node_id, status, **kw)           # Node 6 态
  def append_attempted(task_id, node_id, record)                # 仅记录(含 route_class/from/to/trigger)
  def add_sibling_node(task_id, parent, spec, edge)             # 搜推 fail 拆解
  def spawn_sub_dag(task_id, node_id, workflow_yaml)            # state_machine 协作模式:写 SubDagRef(bcs_run_id 引用),不跟踪 child 态
  def mark_graph_status(task_id, gs: GraphStatus)               # ON_PLAZA/AWAITING_HUMAN/VERIFIED
  def mark_terminal(task_id, phase: DONE|FAILED|CANCELLED)
  def claim_node(task_id, node_id, bot_id) -> DispatchResult    # BBS 认领:CAS PENDING→RUNNING+assignee(乐观锁,§2.3)

  # —— 查询组(只读;内含 ProgressProjector 投影 + 副屏 TaskGraphView 投影;不走写锁)——
  def get(task_id) -> TaskView
  def list_by_user(user_id, limit) -> list[TaskView]
  def progress(task_id) -> ProgressView
  def get_execution_graph(task_id) -> ExecutionGraphSnapshot    # BBS bot 主动查询认领用
  def get_task_graph(task_id) -> TaskGraphView                  # 副屏动态 DAG(根相位+nodes/edges,§1.4b)
  def get_node_detail(task_id, node_id) -> TaskNodeDetailView   # 副屏节点详情(对齐 SM 画布)
  def get_sub_dag(task_id, node_id) -> TaskGraphView            # 协作群下钻:SmGraphAdapter 实时映射(§1.3a)
  def get_task_history(task_id, after_seq=0) -> list[TaskEvent] # append-only 事件流 trace(§1.4)
  def subscribe_task_graph(task_id) -> AsyncIterator[TaskGraphView]  # WS 副屏增量(事件驱动)

  # —— 回投(on_event;两条链路同一方法)——
  # Node 完成/验收/全局终验 verdict 都由 owner-bot SKILL 回投(§6.1);on_event 只落态 + 按回投 verdict 推相位,不自调 check_node/check_goal。
  def on_event(event: TaskEvent) -> Task:
    self._apply_event(task_id, ev)                              # 落态(状态组):按 ev payload set Node 态 + append attempted → TaskExecutionGraph
    if ev.kind == GOAL_VERIFIED:                                # 全局终验回投(owner-bot 判断完成 SKILL,§6.1)
      self._apply_goal_verdict(task_id, ev)
    elif ev.run_mode == BBS:
      return  # BBS node 态已 _apply_event 落;BBS 无 Scheduler,等判断完成 SKILL 回投 GOAL_VERIFIED → 上一分支
    else:
      TaskScheduler.on_event(task_id, ev)                       # 编排链路:重路由/派发下游(ACCEPTANCE_FAIL 等);终验 verdict 走 GOAL_VERIFIED 分支

  def _apply_goal_verdict(task_id, ev):                         # 私有:纯落态,按回投 verdict 推终态相位
    if ev.verdict.pass:
      self.mark_graph_status(VERIFIED); self.advance_phase(DONE)
    elif ev.run_mode == BBS:
      self.advance_phase(FAILED)                                # BBS 续做终验 fail → 任务 FAILED 终态
    else:
      self.mark_graph_status(AWAITING_HUMAN_ACCEPT); emit HUMAN_REQUIRED(BBS_ESCALATION_PRIVACY, unfinished, progress)  # 编排 fail → BBS 门 + 通知用户
```

### 2.2 TaskScheduler(编排 + 私有决策 _route/_select_collab/_compute_gap;不写态)

```
class TaskScheduler:
  def start(task_id):                                             # approve 委派
    plan = TaskRepo.get(task_id).plan
    TaskService.advance_phase(task_id, DEFINED); advance_phase(EXECUTING)
    TaskService.spawn_build_dag(task_id, plan)
    TaskService.mark_graph_status(task_id, ON_PLAZA)
    self._enqueue_tick(task_id)
  def tick(task_id):                                              # 拓扑序 loop
    if root_phase != EXECUTING: return
    for node in unblocked_nodes(execution_graph):
      cand = BotDiscoverPort.recommend(node.spec, attempted=node.attempted_executors)
      if cover==1.0:
        dispatch = self._route(node, task, attempted)      # 私有决策(§4)
        if C5 and collab_unset:
          mode = self._select_collab(node, cand, task)
          if mode=="state_machine": yaml = WorkflowCompiler.compile_to_state_machine(...); TaskService.spawn_sub_dag(...)
        TaskExecutor.dispatch(node, task, dispatch, collab=mode, workflow_yaml=yaml)   # 执行(调外部)
        TaskService.set_node_status(task_id, node.id, RUNNING)    # ★ 改态归 TaskService 状态组
      else: self._decompose_on_fail(task_id, node)
    if all_done(graph):
      TaskService.advance_phase(REVIEWING)                          # 进终验等待态
      emit 触发判断完成 SKILL(owner bot 按 §6.1 经 SKILL 调 AcceptanceChecker.check_goal)  # 验收归 owner-bot SKILL,不在 tick 判定
      → owner bot 回投 GOAL_VERIFIED → POST /events → TaskService.on_event → _apply_goal_verdict
        PASS → DONE / FAIL → §5.4 BBS 门(编排)或 FAILED(BBS)
  def on_event(self, task_id, ev):                                # 编排链路回投(TaskService.on_event 转来;终验 verdict 走 GOAL_VERIFIED 不在此)
    # ev 已落态(TaskService._apply_event);此处只做编排动作:重路由/派发下游(终验归 owner-bot SKILL 回投,§6.1)
    if ev.kind == ACCEPTANCE_FAIL:
      gap = self._compute_gap(task, node)
      if gap.need_reroute: self._enqueue_tick(task_id)
      elif gap.need_split: for c in gap.siblings: TaskService.add_sibling_node(...)
  def escalate_to_bbs(task_id, confirmed):
    if not confirmed: TaskService.cancel(task_id, "user_decline_bbs"); return   # P12:用户主动放弃 → cancel
    TaskService.mark_graph_status(ON_PLAZA)
    TaskExecutor.dispatch_bbs(task_id, unfinished, progress)      # bbs_executor 广场,bot 自主认领
    # BBS bot 回投 ACCEPTANCE_*/GOAL_VERIFIED → POST /events(run_mode=BBS) → TaskService.on_event 落态(终验 verdict 走 _apply_goal_verdict,不经 Scheduler)

  # —— 私有编排决策(纯规则无副作用,可单测)——
  def _route(node, task, attempted) -> DispatchPlan               # C1~C5(§4)
  def _select_collab(node, cand, task) -> CollabDecision          # 三模式(可委派 WorkflowCompiler.select_collab_mode)
  def _compute_gap(task, failed_node) -> Gap                      # 重规划 gap(§5.3)
  def watchdog(node) -> WatchdogAction                            # tick 看门狗(WAIT/PROBE/REDRIVE/ESCALATE,§6.5)
```

### 2.3 两条执行链路(BBS 共享黑板)

> BBS 模式无 Scheduler tick,但**读写都对接 TaskService 同一服务**:BBS bot `get_execution_graph` 查询认领 → 自主执行 → `POST /events` 回投 → `TaskService.on_event(run_mode=BBS)` 落态。**终验同编排链路**:由 task-owner 判断完成 SKILL 回投 `GOAL_VERIFIED` → `on_event._apply_goal_verdict` 落态。状态写口同为 TaskService 状态组,与编排链路一致。

| 链路 | 派发/认领 | 执行 | 验收 | 落态 | 终验 |
|---|---|---|---|---|---|
| **编排**(单bot/协作群) | Scheduler.tick → `_route` → Executor.dispatch | ExecutionPort 调 engine/BCS | owner bot SKILL → 回投 ACCEPTANCE_*/GOAL_VERIFIED | `POST /events` → TaskService.on_event → 状态组(转 Scheduler 编排;终验 verdict 走 _apply_goal_verdict) | Scheduler 触发判断完成 SKILL → owner-bot 回投 GOAL_VERIFIED → _apply_goal_verdict → advance_phase |
| **BBS**(task 内部) | bot 主动 `TaskService.get_execution_graph` 查 → 自主认领(assignee CAS) | bbs_executor 广场 / bot 自主 | task-owner SKILL → 回投 ACCEPTANCE_*/GOAL_VERIFIED | `POST /events`(run_mode=BBS) → TaskService.on_event → 状态组(无 Scheduler) | task-owner 判断完成 SKILL 回投 GOAL_VERIFIED → _apply_goal_verdict → advance_phase |

- 两条链路回投都经 `POST /tasks/{id}/events` handler → `TaskEventRepo.append` + `TaskService.on_event`;改态出口同为 **TaskService 状态组**。
- BBS 改态也走 on_event(统一链路);`bbs_executor` 管广场/认领/续做执行细节,状态落地不 bypass。
- 全局终验/终止性两条链路同一套(owner-bot 判断完成 SKILL 调 AcceptanceChecker.check_goal → 回投 GOAL_VERIFIED → TaskService.on_event._apply_goal_verdict → advance_phase);BBS-fail → 任务 FAILED 终态一致。
- BBS 并发认领:经 TaskService 状态组认领(assignee CAS,PENDING→RUNNING 带乐观锁),广场/认领细节落 bbs_executor,状态改仍经 on_event。

### 2.4 被注入依赖(Port 清单)

> **Port 契约全落 `core/task/protocols.py`(非 `api/`)** —— 四层规矩:core+plugins 不可 import api,Port 需被 core+plugins 共享故必须在 core;`api/task/` 仅留 2 个 Service API Protocol(`TaskServiceProtocol`/`TaskSchedulerProtocol`,loose `*args,**kwargs->Any` 风格,router/DI 绑定键,`adapters→api` 不 import core)。core 内部 `TaskService`/`TaskScheduler` Protocol 亦在 `core/task/protocols.py`,api 层是其 loose 镜像。
>
> **Port 清单(8 个 Protocol + WorkflowCompiler 工具)**:`TaskService`/`TaskScheduler`(core 内部 Protocol)/ `DecomposerPort`/`BotDiscoverPort`/`TaskDriverPort`(调度面 dispatch)/ `ExecutionPort`(执行面,含 `probe`)/ `BbsExecutor`(广场自驱,Phase 5)/ `PanelEventPublisher`+`PanelDeliveryPort`(副屏 popup carrier,FR-OBS-11)/ `BcsCollaborationProtocol`(副屏下钻查询面)。`AcceptanceChecker` **deferred**(Phase 4.7;验收由 owner-bot SKILL 回投 `ACCEPTANCE_*` 事件承载)。

> **双仓落点口径**:Port 契约 + local/community 实现 + 2 统一 Repo 全进 **community(Avernet,开源)**;corp(teamclaw/ocb)仅补"调内部域名/蚂蚁中间件的 prod adapter",极薄(corp 侧只 httpx service + CorpModule)。**LLM understand/judge/collab-select 的 prompt 算法不落 task 模块**(归 SKILL/skill_center/engine),community 只持契约 + structured/规则主路。

| 依赖 | 职责 | community(Avernet)实现 | corp(teamclaw/ocb)实现 |
|---|---|---|---|
| **DecomposerPort** | understand(目标理解,由 SKILL-任务录入 做,task 只收结果)+ classify/decompose(SPARC/GOAP,BFS,confidence)+ 运行期增量拆解 | local Noop + community 规则拆解(本地启发式 BFS) | 可选:bcsfuse httpx adapter(P1) |
| **AcceptanceChecker**(deferred) | check_node/check_goal(structured 双轨;LLM judge 兜底由 owner-bot SKILL 做,task 不持 prompt) | **未落(Phase 4.7 待做)**;验收靠 owner-bot SKILL 回投 ACCEPTANCE_* 事件 | 可选:bcsfuse acceptance-judge httpx(P1) |
| **TaskDriverPort**(调度面) | dispatch_node / redispatch / escalate_to_bbs(Scheduler 编排 seam) | local Noop + community impl | corp prod 派活经注入 prod Protocol 承接 |
| **ExecutionPort**(执行面) | dispatch_single_bot(engine,R6)/ coop_group(BCS 建群,B5)/ redispatch_node(同群重派,B5)/ **probe(watchdog 探活,§6.5)**/ bbs(bbs_executor 广场) | community httpx impl(注入 Baas/Device/HttpClient Protocol)+ local_executor doubles(test/singlebox) | 仅当派活需经 ARCA/MOSN 时补 prod adapter(可选) |
| **BotDiscoverPort** | recommend(按 subtask spec)+ cover 计算 | local Noop + community 规则 impl(`BotDiscoverService` + `BotCatalogPort`,默认 singlebox 5-bot 舰队) | 可选:bcsfuse httpx adapter(P1) |
| **BbsExecutor**(广场自驱,Phase 5) | claim(CAS 认领)/ post_progress(经 on_event 落态,无 Scheduler tick) | community bbs_executor(Phase 5 待落) | — |
| **PanelEventPublisher** + **PanelDeliveryPort**(副屏 carrier,FR-OBS-11) | publish(PanelMessage)/ deliver(session_id, content)——create 发 `<AixUI panel>` 触发副屏弹出 | community EventBus + NoopPanelDelivery(开源无 chat 推送总线,前端 openTaskPanel 直弹)+ RecordingPanelDelivery(test) | corp chat-WS `<AixUI panel>` 推送(Phase 6) |
| **WorkflowCompiler**(工具模块,不注入,被 Scheduler 直接调) | compile_to_state_machine / compile_to_workflow_pack / import_workflow_to_graph / select_collab_mode(规则版;LLM 版委派 SKILL) | community 本地 impl(纯计算) | — |
| **BcsCollaborationProtocol**(查询面 Port,副屏下钻用;注入 SmGraphAdapter) | fetch_state_machine_run_graph(run_id)/ fetch_node_detail(run_id,node_id)— 只读拉 BCS graph/node 端点 | local mock(伪造 SM graph 供画布联调)+ community httpx(调本地 BCS) | corp httpx(prod BCS;corp impl)|
| 跨基础设施 NotifySender/Cache/DB/HttpClient | 钉钉触达/KV/DB/httpx | 注入 Protocol(local Noop/Mock) | corp `plugins/prod/` 已有(DingTalk/ZCache/ZDAS),task 只 `Injected` 不重写 |

> **两条红线(不进 Avernet)**:① 蚂蚁中间件 prod adapter(ZDAS/ZCache/Mist/ARCA/Buservice/DingTalk)—— Protocol 已在 community `plugin_api/`,corp `plugins/prod/` 已有 impl,task 只 `Injected(Protocol)` 不重写;② skill/算法代码(8 SKILL prompt + LLM understand/judge/select_collab)—— 归 skill_center/engine/corp,task 模块只持契约 + 收 SKILL 回投。守死这两条,task 模块即纯"产品功能代码",可全部进 Avernet。

### 2.5 数据访问(2 Repo)

| Repo | 存 | 用 |
|---|---|---|
| **TaskRepo** | Task 主体(spec + plan + execution_graph 含根相位/Node/Edge/sub_dag) | TaskService 定义组/状态组写、查询组读、Scheduler 读 |
| **TaskEventRepo** | TaskEvent 事件流(含回投事件,kind/来源字段区分) | on_event append、查询组读历史/WS |

> Repo 是被动存储,无业务逻辑;状态机校验/判定/调度不在 Repo(在 TaskService 状态组/AcceptanceChecker/Scheduler)。

---

## 3. 三执行模态 + 三协作模式

### 3.1 三执行模态(节点 run_mode,图谱同构)

| 模态 | run_mode | assignee | dispatch | 回投路径 |
|---|---|---|---|---|
| 单 bot | SINGLE_BOT | bot_id | ExecutionPort.dispatch_single_bot → engine(R6) | bot→backend `/events`(不经 BCS) |
| 协作群 | COOP_GROUP | group_id | dispatch_coop_group → BCS 建群+dispatch(B5) | BCS→backend `/events`(B2) |
| BBS | BBS | bbs_task_id | dispatch_bbs(task 内部 bbs_executor 广场,仅全局终验 fail 触发;bot 自主认领) | 回投 `POST /events`(run_mode=BBS)→ TaskService.on_event(统一链路) |

### 3.2 三协作模式(协作群内,BCS `CollaborationRuntimeDefinition`)

| 模式 | 何时选(LLM 或用户) | workflow yaml | 回投粒度 | owner bot |
|---|---|---|---|---|
| 自由聊天 chat | 开放探索/头脑风暴,无步骤序 | 不注入 | 终态(讨论收敛) | 拉群 driver |
| 主从协作 manager_worker | 有总指挥但步骤动态 | 不注入 | 逐节点 + 终态 | 群 master(driver) |
| 自定义协作 state_machine | 流程可固化(≥2 有序步骤)+ 角色可静态绑定 + 验收可结构化 | 注入 `collaboration-definition` | 终态(群自闭环) | 群 master(driver) |

模式由 `WorkflowCompiler.select_collab_mode`(入口 C LLM 自动,搜推多 bot 拉群时三选一)或 `Task.execution.collaboration_mode`(入口 A 用户指定)定。confidence<0.7 降级 `manager_worker`。

### 3.3 三"指定 workflow"入口

| 入口 | 时机 | 触发 | 产物 |
|---|---|---|---|
| A 用户录入指定 | DRAFTING→DEFINED | 用户填 workflow_ref/内联 yaml 或 collaboration_mode | Task.execution.solution / collaboration_mode |
| B 场景1→3 沉淀 | 执行后"保存为常驻" | 用户点保存 | 图谱导出 workflow pack yaml 绑群,后续 C2/C4 复用 |
| C 运行期 LLM 自动 | EXECUTING 内,搜推多 bot 后 | select_collab_mode 三选一 | state_machine 时 compile_to_state_machine 产 yaml 注入群 |

> 入口 C 是关键:loop 运行期由 LLM 自动决策,不是用户指定。统一编译成 workflow-engine 格式 yaml(goal/nodes/dependsOn/executor/judge/output_contract),建群时作"自定义协作模式"注入 BCS `collaboration-definition`(`PATCH /groups/{id}/collaboration-definition`)。**注入后执行是协作群自身行为**,task 不逐节点介入,只收终态回投;`spawn_sub_dag` 写 `SubDagRef` 引用,群不逐步回投 child 态(避免悬空 PENDING,§1.3)。若 BCS 侧仍用 `StateMachineDefinition` 则由 BCS 做一次字段映射。

---

## 4. 路由 C1~C5(`TaskScheduler._route` 私有决策,纯规则无副作用)

```
async def _route(node, task, attempted: list[AttemptedRecord]) -> DispatchPlan
```

| # | 条件 | run_mode | ExecutionPort |
|---|---|---|---|
| C1 | 用户指定单 bot | SINGLE_BOT | dispatch_single_bot(engine,R6) |
| C2 | 用户指定协作群 | COOP_GROUP | dispatch_coop_group(已有群,B5) |
| C3 | 搜推单 bot cover=100% | SINGLE_BOT | dispatch_single_bot |
| C4 | 搜推已有协作群 cover=100% | COOP_GROUP | dispatch_coop_group(已有群) |
| C5 | 搜推多 bot 合计 cover=100% 无现成群 | COOP_GROUP(临时拉群) | dispatch_coop_group(新建群 + 三模式选择 + 可选 workflow yaml 注入) |
| — | 搜推 cover<100% | — | **不直接 BBS** → §5.2 运行期拆解 |

优先级 C1/C2 > C3 > C4 > C5。`attempted_executors` 仅记录重路由历史,**不排除候选**,但 `_route` 对 attempted 过的 executor 降权/延后(纯规则,保证重路由优先换新,避免死循环)。三模态并列,非逐级降级链。BBS 仅全局终验 fail 触发。

---

## 5. 执行 loop(execution-loop)

### 5.1 run_loop_tick 主循环(TaskScheduler 编排;改态全调 TaskService 状态组;task_queue 周期 + 事件驱动)

```
1. 取 Task + execution_graph;if root_phase != EXECUTING: return
2. 按拓扑序解锁,每 Node 先搜推再派发:
   for node in topo_order where status in {PENDING, FAILED(acceptance_result=fail, replan 后)} and all(前置 in {DONE,SKIPPED}):
     candidates = BotDiscoverPort.recommend(node.spec, attempted=node.attempted_executors)  # 不排除
     if cover==1.0 (C1~C5):
       dispatch = self._route(node, task, attempted)            # Scheduler 私有决策
       if C5 且未指定 collab: mode = self._select_collab(...); state_machine → yaml = WorkflowCompiler.compile_to_state_machine(...); TaskService.spawn_sub_dag(...;写引用,§1.3)
       TaskExecutor.dispatch(node, task, dispatch, collab=mode, workflow_yaml=yaml)   # 执行(调外部,不改态)
       TaskService.set_node_status(task_id, node.id, RUNNING)         # ★ 改态归 TaskService 状态组
     else (cover<100%) [GAP1]: §5.2 运行期拆解
3. 判完成:if all(node.status in {DONE,SKIPPED}) and 无 PENDING/RUNNING:
     TaskService.advance_phase(REVIEWING)                          # 进终验等待态
     emit 触发判断完成 SKILL(owner bot 按 §6.1 经 SKILL 调 AcceptanceChecker.check_goal)  # ★ 验收归 owner-bot SKILL,不在 tick 同步判定
     → owner bot 回投 GOAL_VERIFIED(PASS/FAIL) → POST /events → TaskService.on_event → _apply_goal_verdict
       PASS → mark_graph_status(VERIFIED); advance_phase(DONE)
       FAIL(编排) → §5.4 BBS 门  /  FAIL(BBS) → advance_phase(FAILED)
   elif exists(FAILED(acceptance_result=fail)) and 无可推进: → 同上触发判断完成 SKILL(终验 fail 走 BBS 门 / FAILED)
4. 终止性兜底(D6):loop_round++;超 MAX_ROUNDS 或连续 N 轮无进展 → 强制全局终验
5. emit PROGRESS_UPDATED;enqueue 下一 tick(若有可推进)
```

> 决策(`_route`/`_select_collab`)、执行(Executor)、**改态(TaskService 状态组)** 三者分离;Scheduler 自身不写状态,只串联调 TaskService。

**NodeStatus 解锁条件**:PENDING 或 replan 后的 FAILED(acceptance_result=fail)可重派发;前置含 SKIPPED(拆解委托)算达成。SKIPPED 委托 sibling 聚合验收。

### 5.2 deepresearch loop ①:搜推失败 → 运行期拆解(loop 任务拆解,GAP1)

```
on cover<100%:
  if is_atomic(node) 或 runtime_recompose_count(node) > 2:
    TaskService.set_node_status(task_id, node_id, FAILED, acceptance_result=fail, unmet_criteria=[targets_acceptance])  # 留待全局终验
  else:
    children = DecomposerPort.decompose(node.spec, mode)   # SPARC/GOAP BFS + dedup 0.92(决策)
    for c: TaskService.add_sibling_node(task_id, parent=node, spec=c, edge=DEPENDENCY)   # ★ 改态归 TaskService
    runtime_recompose_count(node)++
    TaskService.set_node_status(task_id, node_id, SKIPPED)    # 委托 sibling,acceptance 由 sibling 聚合
```

### 5.3 deepresearch loop ②:验收不过 → 同 Node 重路由(loop 搜推执行)

```
on NODE_REJECTED / 验收 fail(回投经 POST /events → TaskService.on_event):
  _apply_event 已落态(set_node_status FAILED(acceptance_result=fail) + append_attempted,guard 校验后)  # ★ 落态归 TaskService
  AcceptanceChecker.check_node 由 owner bot 经 SKILL 完成(§6.1)→ 回投 ACCEPTANCE_*  # 判验归 owner-bot
  → 转 Scheduler.on_event 做 LOOP 重规划决策:
    gap = self._compute_gap(task, node)        # Scheduler 私有决策(纯,§4 归属)
      unmet 可换 executor → Scheduler 重派发同 Node(self._route + ExecutionPort.dispatch_single_bot / redispatch_node(coop,§2.4))
      unmet 需拆开 → TaskService.add_sibling_node(recompose_count≤2)
    node.loop_round++(经 TaskService 状态组)
on NODE_FAILED(执行报错):R7 同 executor 重试 max_attempts(默认 2),仍 FAILED 转重路由
终止:node.loop_round>max 或连续 N 轮不变 → 留待全局终验;无 per-node BBS
```

> 两 loop 接同一终止性(is_atomic / runtime_recompose_count≤2 / node.loop_round≤max)。改态统一经 TaskService 状态组,决策经 `_route`/`_compute_gap` 私有方法,执行经 TaskExecutor。

### 5.4 BBS 上升(仅全局终验 fail + 人工隐私确认,GAP2/GAP3)

```
on GOAL_VERIFIED FAIL(编排链路,owner-bot 判断完成 SKILL 回投,§6.1;on_event._apply_goal_verdict 已落 AWAITING_HUMAN_ACCEPT):
  root_phase 仍 EXECUTING(G-5)
  # emit HUMAN_REQUIRED 已由 _apply_goal_verdict 发(§2.1);此处仅等待用户确认
  on 用户确认 True(POST /escalate-bbs):
    TaskService.mark_graph_status(task_id, ON_PLAZA)               # BBS 执行中
    Scheduler.escalate_to_bbs → TaskExecutor.dispatch_bbs(task_id, unfinished_subtasks, progress_snapshot)
    bbs_executor(广场/认领/续做执行细节,task 内部)→ BBS bot 自主认领 → 节点验收由 task-owner SKILL 回投 ACCEPTANCE_* → POST /events → TaskService.on_event(run_mode=BBS)落 Node 态(★ 与编排链路同出口,§2.3)
    # P9:BBS 内 node 验收 fail→FAILED(acceptance_result=fail):一次性重放认领(1 轮,别的 BBS bot 可接);仍 fail 进 BBS 终验 fail→任务 FAILED,不进 deepresearch 重路由
    BBS 全局终验同样由 task-owner 判断完成 SKILL 回投 GOAL_VERIFIED(run_mode=BBS)→ on_event._apply_goal_verdict:
      PASS → TaskService.mark_graph_status(VERIFIED); TaskService.advance_phase(DONE)
      FAIL → TaskService.advance_phase(FAILED)(终态)+ 反馈用户;不再回环/重试/BBS
  on 拒绝 False: → TaskService.cancel()(用户主动放弃)
```

BBS 仅触发一次(防环);BBS-fail = 终态 FAILED(无下一级兜底)。BBS 执行在 task 内部,改态与编排链路同经 TaskService 状态组(§2.3)。

### 5.5 终止性兜底汇总(D6)

| 兜底 | 条件 | 出口 |
|---|---|---|
| Node 重试 | FAILED,max_attempts 内 | R7 同 executor 重试,仍 FAILED 转重路由 |
| Node 重路由 | FAILED(acceptance_result=fail),loop_round≤max | §5.3 同 Node 带上下文 |
| Node 拆解 | 搜推 cover<100%,recompose_count≤2,非 atomic | §5.2 sibling;父 SKIPPED |
| atomic 留待 | is_atomic 且搜推 fail / recompose 超限 | FAILED(acceptance_result=fail)留待全局终验 fail → BBS |
| BBS 上升 | 全局终验 fail + 人工确认 | §5.4 task 内部 run_bbs |
| BBS 内重放 | BBS node FAILED(acceptance_result=fail) | 一次性重放认领(1 轮);仍 fail→BBS 终验 fail→任务 FAILED(不进 deepresearch) |
| BBS PASS / FAIL | run_bbs overall | 回填 DONE / FAILED(终态) |
| 任务级收尾 | task.loop_round 总上限 | 强制全局终验 |

---

## 6. 操作契约 + owner bot 验收链路

> **口径**:系统 **TaskScheduler 做 deepresearch 动态编排**(路由/拆解/重规划/派发/上升 —— inline 调 Port,系统执行,非 SKILL);**只有"状态判断 + 验收"由 owner-bot 经 SKILL 自判后回投上报**(Node 完成/子任务验收/全局终验)。Scheduler 不判验,owner-bot 不编排;两者经回投 + TaskService 状态组耦合。接口契约归本系分;实现归 skill_center + engine。owner-bot SKILL/执行方**产事件、不直改状态、不直调 task**;统一回投 backend `POST /api/tasks/{id}/events`。

### 6.0a 系统 TaskScheduler 编排执行(非 SKILL,inline Port)

| 操作 | 时机 | 依赖(Port,inline) | 落态(经 TaskService 状态组) |
|---|---|---|---|
| 子任务-路由决策 | EXECUTING 派发前 | `BotDiscoverPort.recommend` + `_route`(C1~C5) | set_node_status(RUNNING)+ ExecutionPort.dispatch |
| 派发执行 | 路由后 | `ExecutionPort.dispatch`(single/coop/bbs) | set_node_status(RUNNING) |
| runtime 拆解 | 搜推 cover<100% | `DecomposerPort.decompose`(§5.2) | add_sibling_node + 父 SKIPPED |
| LOOP 重规划 | FAILED(acceptance_result=fail)(验收 fail 回投后) | `_compute_gap` + replan_route/add_sibling_node(§5.3) | add_sibling_node / set_node_status |
| BBS 上升编排 | 全局终验 fail + 人工确认 | `ExecutionPort.dispatch_bbs` | mark_graph_status + 终态推进 |
| 看门狗探活 | tick 超时(RUNNING 节点) | `ExecutionPort.probe` + `watchdog`(§6.5) | PROBE/REDRIVE/ESCALATE |

### 6.0b owner-bot SKILL(判断 + 验收,回投上报)

| Skill | 装备者 | 时机 | 依赖 | 回投 |
|---|---|---|---|---|
| 任务录入 task-recognition | user Bot | DRAFTING | 产 5 要素草案 + proposal.md(只澄清,ready=False 不入 loop) | SPEC_AMENDED |
| 任务拆分 plan(initial) | user Bot/master | DRAFTING→DEFINED | `DecomposerPort`(SPARC/GOAP,BFS,confidence≥0.7,验收覆盖) | PLAN_FINALIZED → finalize_plan |
| 子任务-执行 | worker/单 bot | Node RUNNING | bot 自有工具 | NODE_DONE/FAILED(状态判断上报) |
| **子任务-验收** | **owner bot**(§6.1) | artifact 后 | `AcceptanceChecker.check_node`(双轨 + LLM 兜底) | NODE_ACCEPTED/NODE_REJECTED |
| 判断完成 | owner bot(同 §6.1) | 全 Node DONE → REVIEWING 触发 | `AcceptanceChecker.check_goal` | GOAL_VERIFIED(PASS→DONE / FAIL→BBS门) |
| 通知用户(钉钉) | user Bot/master | 关键节点/交付 | backend notify | USER_CONFIRM/REJECT |

> **任务拆分两面**:initial plan = owner-bot 任务拆分 SKILL(DRAFTING 期,回投 PLAN_FINALIZED → finalize_plan);runtime 拆解(§5.2 cover<100%)= Scheduler inline 调 `DecomposerPort.decompose`(系统编排,非 SKILL)。`DecomposerPort` 是共享能力,两边都调。

### 6.1 owner bot 链路规则(验收发起人,关键口径)

任务验收由 **owner bot 通过 SKILL-子任务-验收** 实现(非后端服务自行判定)。owner bot 随执行链路不同:

| 链路 | run_mode/collab | owner bot |
|---|---|---|
| 单 bot | SINGLE_BOT | 该 bot 本身(自验收) |
| 协作群·自由聊天 | COOP_GROUP + chat | 拉群 driver(master) |
| 协作群·主从 | COOP_GROUP + manager_worker | master(driver) |
| 协作群·自定义 | COOP_GROUP + state_machine | master(driver,群自闭环跑完汇总后验收) |
| BBS(task 内部) | BBS | task-owner(经 SKILL 验收,回投 run_mode=BBS) |

执行方(delegate)只产 artifact,owner bot 收账后调 `AcceptanceChecker.check_node` 双轨验收,回投 NODE_ACCEPTED/NODE_REJECTED(统一走 backend `/events` → `TaskService.on_event`)→ TaskService 状态组落态(**不在 on_event 重复调 check_node**,验收已在 owner bot 侧经 SKILL 完成)。**全局终验同理**:Scheduler.tick 判 all_done 后 `advance_phase(REVIEWING)` 并触发**判断完成 SKILL**,由同一 owner bot(单bot=该bot/群=master/BBS=task-owner)经 SKILL 调 `AcceptanceChecker.check_goal` 复核全部验收标准,回投 `GOAL_VERIFIED`(PASS/FAIL)→ `on_event._apply_goal_verdict` 落态(PASS→DONE;编排FAIL→BBS门;BBS FAIL→任务 FAILED)。**TaskService/Scheduler 不自调 check_node/check_goal** —— 判定全归 owner-bot SKILL,后端只收 verdict 落态+编排;Node 完成与否、验收是否过都由对应模态 owner bot 上报记录进 TaskExecutionGraph。BBS 节点验收 + 全局终验都经 `/events`(run_mode=BBS)回投,on_event 只落态(不自判)。

### 6.2 回投通道与状态唯一改入口

```
单 bot / 协作群(含三协作模式)→ POST /api/tasks/{id}/events → handler: TaskEventRepo.append + TaskService.on_event
  → on_event 落态(状态组)+ 编排链路转 Scheduler.on_event(重路由/派发下游/终验)
BBS → POST /api/tasks/{id}/events(run_mode=BBS)→ TaskService.on_event → 落态(终验 verdict 由 task-owner 判断完成 SKILL 回投 GOAL_VERIFIED → _apply_goal_verdict;无 Scheduler)
Task 顶层 root_phase → 由 TaskService 状态组 advance_phase/mark_terminal 改(根相位,唯一写口)
状态唯一改入口 = TaskService 状态组(Scheduler 不写态,只调 TaskService)
```

并发:Node 状态更新走 task_queue 单 worker 抢占 + 事件 seq 单调(seq 由 TaskEventRepo.append 内部单 writer 分配,复用 `ac_task_queue` CAS);BBS 多 bot 认领经 TaskService.claim_node CAS(§2.1)。

### 6.5 看门狗(watchdog,tick-based 超时探活)

bot 会因指令遵从或 LLM 服务不稳定 hang 住,故 Scheduler 须主动探活 + 重驱。`watchdog(node)` 是纯规则函数,按 tick 超时对一个长期 RUNNING 的 node 决策:

| WatchdogAction | 条件 | 落态/动作 |
|---|---|---|
| WAIT | 仍在 bot 自上报窗口内 | 继续等(不动作) |
| PROBE | tick 超时,首次探活 | `ExecutionPort.probe` + `probe_count++`(探活 fire-and-forget;bot 收到后回投 NODE_ACCEPTED/NODE_FAILED 或状态事件) |
| REDRIVE | 探活耗尽,重驱执行 | `ExecutionPort.redispatch_node` + `redrive_count++`(开新一轮窗口) |
| ESCALATE | 重驱也耗尽 | `set_node_status(FAILED)` → 走 reroute/split(§5.3) |

状态在 `node.properties`(running_ticks / probe_count / redrive_count);tick-based 超时(无 wall clock)。集成于 `Scheduler.tick`。

---

## 7. 代码落点(D2:backend `core/task/` 独立二级目录)

> 遵循 backend 四层单向依赖(`api→core→plugin_api→plugins`,见 `src/backend/README.md`):`api/` 仅 typing.Protocol 无 import;`core/` 不 import `plugins`(只经 `core/<m>/dependencies/`);`adapters/http/` 不 import `plugins` 也不 import core 具体类(经 `Injected(<X>ServiceProtocol)`)。

### 7.1 结构(community=Avernet 开源全量 / corp=teamclaw/ocb 极薄 adapter)

```
# ============ community(Avernet 开源,产品功能代码全量)============
# 源根:ocb-public/src/backend/src/agentclaw/community
# api ↔ core 解耦:api 只放 adapter-facing Service API Protocol(2 个),
# Port 契约全在 core/task/protocols.py;core+plugins 不 import api(四层规矩)。
community/api/task/                        # Service API Protocol(router/DI 绑定键,loose *args/**kwargs->Any)
  service_api.py             → TaskServiceProtocol / TaskSchedulerProtocol(仅 2 个;structural conformance 由 test_task_service_api_conformance 校验)

community/core/task/
  protocols.py               # Port + DTO 契约(TaskService/TaskScheduler/DecomposerPort/BotDiscoverPort/
                             #   TaskDriverPort/ExecutionPort[含 probe]/BbsExecutor/PanelEventPublisher/
                             #   PanelDeliveryPort/BcsCollaborationProtocol + DTO)。core+plugins 共享,不在 api。
  domain/{models,state_machine,events,repository}.py
    models.py:Task(含 spec+plan+execution_graph)/TaskSpec(不含 plan)/Plan/TaskExecutionGraph/Node(含 sub_dag)/Edge/AttemptedRecord(含 route_class/from/to/trigger)/ProgressNode/SubDagRef/WatchdogAction
    state_machine.py:TaskStateMachine(7 态 VALID_TRANSITIONS,见 2026-07-30 spec;根相位)
    events.py:TaskEvent/EventKind(含 GOAL_VERIFIED/GOAL_REJECTED;HUNG deprecated 无 writer)+ occurred_at
    repository.py:TaskNotFoundError(业务异常)
  repository/models.py        # ORM 模型(挂 community core/base.py:Base):ac_task(含 plan_json 列)/ac_task_event/ac_task_execution_graph
  sql/                         # prod DDL 参考(OceanBase/MySQL,运维手动 provision):ac_task.sql/ac_task_event.sql/ac_task_execution_graph.sql
  services/{task_service,task_scheduler,bot_catalog,bot_discover_service,
            execution_port_client,local_executor,workflow_compiler,bbs_executor,graph_adapter,
            panel_publisher,panel_carrier}.py
    task_service.py:合一 TaskService(定义组/状态组/查询组含 history+get_task_graph 等/on_event+_apply_goal_verdict;create 发 PanelMessage)
    task_scheduler.py:TaskScheduler(start/tick/on_event)+ watchdog(node) 纯规则(WAIT/PROBE/REDRIVE/ESCALATE,§6.5);调 TaskService 状态组,不写态
    bot_catalog.py:BotCatalogPort(默认 singlebox 5-bot 舰队)+ bot_discover_service.py:BotDiscoverPort 规则 impl
    execution_port_client.py:ExecutionPort community impl(dispatch_single_bot/coop_group/redispatch_node/probe/bbs)+ local_executor.py:test doubles(LocalBotExecutorPort/HangingBotExecutor)
    workflow_compiler.py:纯计算(编译/import;select_collab 规则版,LLM 版委派 SKILL)
    bbs_executor.py:task 内部广场/认领/续做(BbsExecutor Port impl;Phase 5)
    graph_adapter.py:SmGraphAdapter(BCS SM run graph → TaskGraphView 子树映射,纯计算;被 get_sub_dag 调)
    panel_publisher.py:EventBusPanelPublisher + panel_carrier.py:TaskPanelCarrier(Lifecycle 参与者,FR-OBS-11 carrier transport)
    acceptance_checker.py:deferred(Phase 4.7)
  README.md(Context Boundary,Rule 22)
  dependencies/(DI 工厂,选 profile 实现)

community/plugins/                          # 统一 Repository + Port impl
  task_repository.py          # 统一 ORM Repo(根目录单份,orm_session 跑 SQLite+ZDAS)
  task_event_repository.py    # 统一 ORM Repo(append-only;occurred_at 从 gmt_create 回填)
  local/task.py               # local Noop/Mock impl(Rule 21;给 test/singlebox,含 BcsCollaboration mock)
  community/task/__init__.py  # community Noop impl(NoopTaskService/NoopExecutionPort[含 probe]/NoopBcsCollaborationPort...)
  community/task/panel_carrier.py # TaskPanelCarrier(Lifecycle 自动发现 install)

community/plugins/local/database.py         # bootstrap() 追加 side-effect import:core.task.repository.models(建表 create_all)
community/adapters/http/task/{router.py,schemas.py}  # prefix=/api/tasks;Injected(TaskServiceProtocol/TaskSchedulerProtocol)+ 跨模块 Bot/Baas/Device Protocol;含 POST /events handler + GET /history
community/di/modules/task_module.py                    # Base TaskModule:自绑定 TaskService/TaskScheduler + 2 统一 Repo → Protocol 别名
community/di/modules/testing_task_module.py            # TestingTaskModule:测试 stub
community/di/modules/infrastructure/community/task.py  # CommunityTaskModule:Port 绑 Noop/community impl(BotDiscover/ExecutionPort 注入真实)
community/di/container.py                   # base list 追加 TaskModule()
community/di/profile_modules.py             # 三列追加 Community/Testing TaskModule
community/adapters/http/app.py              # include_router(task_router)

# ============ corp(teamclaw/ocb,极薄 prod adapter,零业务代码)============
# 源根:src/backend/src/agentclaw/corp
corp/core/task/services/                    # 仅 bcsfuse/ARCA httpx adapter(P1 可选;无业务逻辑,无状态机,无 Repo)
  bcsfuse_decomposer_service.py             # 可选:bcsfuse understand+decompose httpx
  bcsfuse_bot_discover_service.py           # 可选:bcsfuse recommend httpx
  bcsfuse_acceptance_judge_service.py       # 可选:bcsfuse acceptance-judge httpx(LLM judge prompt 在 bcsfuse 侧)
  # 不写:ExecutionPort(prod 派活由 community impl 注入 prod Baas/Device/BCS Protocol 承接)
  # 不写:Repository/ZDAS(统一 Repo 一份 body 经 ZdasDB.orm_session 跑 ZDAS,corp 无 Repo)
corp/di/modules/infrastructure/corp/task.py # CorpTaskModule:把 Port 的 Noop override 成 bcsfuse httpx(B8 explicit provider)
corp/di/modules/infrastructure/corp/column.py  # corp_column()+test_corp_reuse_column() 追加 CorpTaskModule
corp/adapters/http/task/(可选 corp-only 路由,若需内部鉴权)
配置:corp/configs/application-{prod,pre}.yaml task_loop 块(bcsfuse base_url 等,corp-only);community yaml 省略
```

**落点要点**:community 持 Port 契约 + TaskService/TaskScheduler + 2 统一 Repo + router + DI + Noop;corp 仅 3 个 bcsfuse httpx adapter(可选 P1)+ CorpTaskModule override。**corp 无 Repo、无业务 service、无状态机** —— 统一 Repository + 业务逻辑 + 状态机全在 community,corp 只是把"调内部 bcsfuse/中间件"的 prod adapter 装进容器(B8)。

### 7.2 路由端点(挂 adapters/http/task/router.py;prefix=`/api/tasks`)

```
POST /api/tasks                        create → TaskService.create
GET  /api/tasks?user_id=...            list_by_user → TaskService.list_by_user
GET  /api/tasks/{id}                   get → TaskService.get
GET  /api/tasks/{id}/progress          progress → TaskService.progress
POST /api/tasks/{id}/amend             amend → TaskService.amend
POST /api/tasks/{id}/plan              finalize_plan → TaskService.finalize_plan
POST /api/tasks/{id}/approve           approve → TaskScheduler.start(建图 + enqueue tick)
POST /api/tasks/{id}/tick              tick → TaskScheduler.tick
POST /api/tasks/{id}/events            回投 → handler:TaskEventRepo.append + TaskService.on_event(单bot/协作群/BBS 都走)
# —— 副屏动态 workflow 画布 API(新建独立画布消费 TaskGraphView,§1.4b)——
GET  /api/tasks/{id}/graph             get_task_graph → TaskService.get_task_graph(顶层动态 DAG)
GET  /api/tasks/{id}/nodes/{node_id}   get_node_detail → TaskService.get_node_detail(节点执行详情,对齐 SM 画布)
GET  /api/tasks/{id}/nodes/{node_id}/sub-dag  get_sub_dag → TaskService.get_sub_dag(SmGraphAdapter 实时映射 BCS SM run graph)
GET  /api/tasks/{id}/history           get_task_history → TaskService.get_task_history(after_seq)(append-only 事件流 trace;occurred_at 从 gmt_create 回填)
WS   /api/tasks/{id}/graph/stream      subscribe_task_graph → 增量 TaskGraphView(事件驱动;画布轮询兜底)
```

> **未落端点(后续 phase 对齐)**:`/escalate-bbs`、`/deliveries`、`/progress/stream(WS)` 尚未挂载(Phase 5 / 可选 F005 待落)。

副屏画布 API 与 router **直调 Protocol(`Injected`),禁裸 SQL**;`get_sub_dag` 经 `BcsCollaborationProtocol` 调 BCS(查询面,无写态)。画布端字段同构 `TaskGraphView`(§1.4b),信息维度按 §1.3b 对照表 cover state_machine 画布全字段;自定义协作群作为"workflow 固化特例"在同一画布展示(顶层节点 run_mode=COOP_GROUP、collab_mode=STATE_MACHINE,下钻 sub-dag)。回投端点 `/events` 是 handler(非独立服务),调 TaskEventRepo + TaskService.on_event。

### 7.3 跨 core 模块访问

经 Protocol 注入:`BaasServiceProtocol`/`BotServiceProtocol`/`DeviceServiceProtocol`/`CronRelayServiceProtocol`/`CollaboratorServiceProtocol`;不直 import 其他 core 具体类。外部系统(BCS/bcsfuse/engine)httpx(corp impl)。

### 7.4 Context Boundary(README,Rule 22)

持 Task(含 spec+plan+execution_graph)+ TaskEvent 聚合根 + TaskService(合一 Application Service)+ TaskScheduler(编排)+ BBS 内部执行(bbs_executor)+ DecomposerPort/AcceptanceChecker/ExecutionPort/BotDiscoverPort/TaskDriverPort + 2 统一 ORM Repo + 3 表(ac_task/ac_task_event/ac_task_execution_graph)。**双仓落点**:community(Avernet)持全部产品功能代码 + SPI 契约 + local/community impl + 统一 Repo(一份 body 经 `DatabasePlugin.orm_session()` 跑 SQLite/ZDAS)+ 3 表 ORM;corp(teamclaw/ocb)仅中间件/内部域名 prod adapter,零业务代码、零 Repo、零状态机(LLM 算法 prompt 归 SKILL/skill_center,不进 task 模块)。依赖:跨 core 经注入 `BaasServiceProtocol`/`BotServiceProtocol`/`DeviceServiceProtocol`/`CollaboratorServiceProtocol` 等,外部 httpx(corp bcsfuse),bcsfuse 可选非硬依赖。不持有:Bot 配置(engine)、用户资产(backend)、协作群拓扑(BCS)。

### 7.5 测试(Rule 25 契约)

`tests/contracts/test_task_*.py`(community world fixture 注入 local impl 跑上层消费者)+ `tests/architecture/test_task_service_api_conformance.py` + `test_protocol_contracts.py` + `tests/corp/`(CorpTaskModule + httpx Mock 端到端)。

### 7.6 落地分期

P0 骨架(api Protocol + services 桩 + router + TaskModule + Noop + 注册 + 契约测试绿)→ P0 deepresearch loop(Decomposer + run_loop_tick 拓扑序 + 双轨验收 + /events 回投)→ P0 跨模块(engine R6 / BCS B5)→ P1 BBS 内部 `bbs_executor`→ P1 bcsfuse Port(R1/R2/R3)。

---

## 8. 跨模块需求清单(bcn-requirements,本系分出需求契约,由对应团队交付)

> **双仓**:R6/B5/B6/B2 对开源 engine/BCS 的需求契约进 **community(Avernet)** 跨模块清单(httpx 调开源服务);R1/R2/R3 对 bcsfuse 为可选 P1(community 本地 impl 作 P0 默认),corp 仅补 httpx adapter。

| ID | 需求 | 优先级 | 责任方 | 用途 | 落点 |
|---|---|---|---|---|---|
| R6 | 单 bot 程序化派活 + 完成回投 backend | P0 | engine/adapter | dispatch_single_bot + 回投 | community(httpx 调开源 engine) |
| B5 | backend→BCS 服务化建群 + TaskDispatch + 同群重派单 node | P0 | BCS | dispatch_coop_group + redispatch_node | community(httpx 调开源 BCS) |
| B6 | BCS `collaboration-definition` 接受 workflow-engine 格式 workflow yaml + 终态回投粒度 | P0 | BCS | 入口 A/C 自定义协作模式注入 | community |
| B2 | 协作群/单bot/BBS 执行事件回投 backend | P0 | BCS/engine | 回投通道(三链路都走 /events) | community handler |
| R1 | task-understanding 暴露 | P1 | bcsfuse | DecomposerPort.understand(可选;本地规则作 P0) | corp httpx adapter(P1) |
| R2 | recommend 按 subtask spec | P1 | bcsfuse | BotDiscoverPort(可选;本地 BotRepository 作 P0) | corp httpx adapter(P1) |
| R3 | acceptance-judge 暴露 | P1 | bcsfuse | AcceptanceChecker LLM 兜底(可选;structured 主路作 P0) | corp httpx adapter(P1) |
| R4 | 持久协作群 API(场景1→3) | P1 | BCS | 沉淀复用 | community |
| R5 | 协作范式标记(worker profile) | P2 | bcsfuse | cover 评估 | corp(P2) |
| B3 | 副屏动态 workflow 画布数据(graph/下钻/详情/stream)+ BCS SM run graph 查询面 | P0 | backend/BCS/frontend | TaskService 查询组 + SmGraphAdapter + BcsCollaborationProtocol;新建独立画布(参考 bcsPanel/StateMachineRunView) | community(httpx 调本地 BCS)+ frontend 新画布 |

> B1(BCS BBS)/B4(降级续跑)不适用:BBS 续做经 `/events`(run_mode=BBS)回投(统一链路,§2.3/§3.1);无降级续跑(走 deepresearch 重规划)。回投端点 `POST /api/tasks/{id}/events` 挂 community `adapters/http/task`。R6/B5/B6/B2 责任方为开源 engine/BCS(进 Avernet 清单);R1/R2/R3 责任方 bcsfuse 为 corp 可选(P1),community 本地 impl 先行。

---

## 9. 开放问题(需评审拍板)

1. **BBS 承载方式**:BBS 是 task 内部、仅全局终验 fail 触发、续做经 `/events`(run_mode=BBS)回投、fail→任务 FAILED;广场/认领/执行细节归 `bbs_executor`。
2. **LLM 验收兜底是否本期做**:默认结构化双轨主路、LLM 兜底可选,评审定本期范围。
3. **bcsfuse 是否硬依赖**:R1/R2/R3 可切 bcsfuse 或本地 impl,评审定。
4. **workflow yaml 落点**:统一编译成 workflow-engine 格式注入 BCS;若 BCS 仍用 `StateMachineDefinition` 则由 BCS 侧做字段映射(B6)— 落点由 BCS 团队定。
5. **副屏画布落点(frontend)**:新建独立动态 workflow 画布组分在 `src/frontend/` 何处、是否复用 `@aix-chat/ui` 的 ChatLayout/UmdPanel 机制(同 state_machine 画布用 `<AixUI panel>` 消息驱动 openPanelTab,还是任务页内嵌),评审定;本 plan 定数据契约(TaskGraphView)与 backend API,前端落点待 implement 阶段定。
6. **下钻 live 态刷新方式**:路 A 下钻拉 BCS SM run graph,刷新由画布轮询(对齐现 StateMachineRunView 轮 `/graph`)还是经任务 WS `/graph/stream` 中转推送(需后端订阅 BCS run 事件)—— 评审定;默认轮询(P0),WS 中转作 P1 增强项。

---

## 变更记录

| 日期 | 作者 | 变更 |
|---|---|---|
| 2026-07-28 | 栖真 | 初版 plan:目标驱动任务执行 loop 的系统化 HOW(领域模型 / 服务与依赖 / 三模态 + 三协作 / C1~C5 / execution loop + 两 deepresearch loop / BBS / 8 Skill + owner bot 链路 / 落点四层双 DI / 跨模块 R&B 清单)|
| 2026-07-29 | 栖真 | 副屏可视化(对接 spec FR-OBS-01~11/AC-11/12):§1.3a SubDagRef 下钻引用(路 A 渲染期映射)+ §1.3b 节点展示字段超集 & AC-12 信息对照表 + §1.3c 状态映射 + §1.4b TaskGraphView 展示模型 & 查询/下钻/详情/订阅 API;新增 BcsCollaborationProtocol + SmGraphAdapter;§7.2 路由加 graph/nodes/sub-dag/stream 端点;D9 画布形态决策;B3 升 P0 |
| 2026-07-31 | 栖真 | 对齐代码现状:状态机 7 态(见 2026-07-30 spec)、Plan 升 Task 聚合根字段、Port 契约落 core/task/protocols.py(api 仅 2 个 Service API Protocol)、§2.4 Port 清单补 TaskDriverPort/BbsExecutor/Panel 系列/ExecutionPort.probe、§6.5 watchdog、§7.1 结构与 §7.2 端点(prefix=/api/tasks + GET /history)校正;清洗为当前态设计文档 |
