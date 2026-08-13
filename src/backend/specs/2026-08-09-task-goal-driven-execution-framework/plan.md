# Plan — 任务目标驱动的任务动态规划执行框架 (HOW: 架构 / 领域模型 / 模块 API / 交互逻辑)

> 权威源(冲突时以此为准):最新领域模型 classDiagram(2026-08-11)、流程架构图 `apoi9lcedw9u8ivq`、5 模块设计文档(任务中心 `yugg6dorsxo8sgmp`/任务图谱 `lunk1txfuv6gtwk2`/任务规划 `uuq2tlue91q4lkal`/任务派发 `ue1ie0g3supwo2uf`/任务执行 `lxg2mwgmtfqg6d95`)、case 剧本 `gwqie46v7hzr1w6h`。WHAT/WHY 见 `spec.md`;实现计划见 `tasks.md`。

## 0. 一句话定位

一个**事件驱动 + 状态条件触发**的任务动态规划执行框架:外部事件(提交/回投)触发 → 编排核按图谱状态条件协调 `plan→add_task_nodes→dispatch→start_run→回调→update_task_node_info` 推进;**图谱是 SSOT,所有写收口到 `TaskGraphService`**;规划/派发是可插拔的优化策略,规划/派发是引擎内置优化策略(`PlanningStrategy`/`DispatchStrategy`,默认 GapBased/Search;corp 经 ocb 仓覆写,Avernet 用 stub/singlebox 实现),框架本身零 case 知识。**全仓库只有一套实现**,位于规范位置 `core/task`。

## 1. 架构总览(六模块 + 编排核;单一实现,规范位置 `core/task`)

| 模块 | 性质 | 职责一句话 | 对外有 API? |
|---|---|---|---|
| **TaskService** | 对外 facade | 系统唯一对外入口(2 API);内部含编排核协调其余模块 | ✅ 2 个 |
| **TaskGraphService** | 内部图谱 SSOT | 图谱原子变更唯一网关(增删改查);`relations` 依赖派生 | ❌ 内部(8 API:5 核心写/读+3 派生只读) |
| **TaskPlanner**(零参,内置策略池) | 读图按状态条件 first-match-wins 选内置 `PlanningStrategy` 产逻辑子节点(不含执行信息) | ❌ 内部 |
| **PlanningStrategy** | 规划优化策略(引擎内置) | 真正的分解智能:产哪些节点。默认 GapBased/Workflow;corp 覆写 `_build_planner` | ❌ 内部策略 |
| **TaskDispatcher** | 分发策略(可插拔) | 搜推决定"谁来做",把 `run_mode`/`assignee` 填到 `TaskNode.run_info` 后返回 `list[TaskNode]`,多 bot 动态拉协作群;**不写图、不起 run**(编排核落库+起 run) | ❌ 内部 |
| **TaskRunner** | 执行承载 | `start_run(批量)` 三模态自适应 + `query_status/detail/result/bot_tasks`;`TaskLoopCallback` 回投;`form_coop_group` 复用 BCS | ❌ 内部 |
| **TaskHarness** | 旁路常驻 | 周期巡检超时/崩溃,写同网关,不抢正向驱动 | ❌ 内部 |
| **(编排核)** | TaskService 内部 | 事件驱动 + 状态条件触发协调 plan/graph/dispatch/execution | ❌ 非独立模块 |

**关键边界**:
- **编排核只做"决策与转写"**,不自己改图/执行——改图经 `TaskGraphService`,执行经 `TaskDispatcher→TaskRunner`,规划经 `TaskPlanner`(内置 `PlanningStrategy` first-match-wins 选策略)。
- **框架零 case 知识**:planner 是编排壳,分解内容来自引擎内置 `PlanningStrategy`;`N_overview`/`N_market` 等任意节点名只能出现在**case 的策略 stub 产出**或**测试 stub**,绝不写死在框架代码。
- **单一实现**:规范位置 `core/task`;继承旧 seam 命名、DI 接线(`CommunityTaskModule`)、开源边界纪律。
- **开源边界**:Avernet 发**契约 seam + Noop/singlebox double**(本地关键词 cover 的 bot catalog、BCS local/mock 拉群、stub decomposer);真实搜推/真实执行/LLM 规划/验收 SKILL 在 corp `ocb` adapter。

对外只有 `TaskService` facade 2 个 API:`execute`/`get_task_dashboard`;图谱内部 5 核心写/读 API(`initialize_graph`/`add_task_nodes`/`update_task_node_info`/`update_task_graph_info`/`query_task_dashboard`)+ 3 派生只读查询(`query_task_nodes`/`get_child_tasks`/`get_parent_task`)由 `TaskGraphService` 独立持有(不合并进 facade)。

---

## 2. 领域模型类(对齐最新 classDiagram)

```python
class Status(StrEnum):            # 6 态(含 PLANNING)
    PENDING | PLANNING | RUNNING | DONE | FAILED | HUNG
class AcceptanceVerdict(StrEnum): PASS | FAIL
class RelationType(StrEnum):      DEPENDENCY    # 节点间依赖关系

@dataclass Metadata:
    task_id: str                  # 任务 ID
    title: str
    instruction: str              # 核心执行指令(Prompt)
@dataclass Context:
    background: str
    extend_props: dict            # 非结构化补充数据
@dataclass AcceptanceCriteria:
    id: str
    description: str
@dataclass Goal:
    objective: str
    acceptances: list[AcceptanceCriteria]
@dataclass TaskSpec:
    metadata: Metadata
    context: Context
    goal: Goal

@dataclass TaskInfo:              # 对外 execute 入参
    task_spec: TaskSpec
    source_channel_type: str      # "bot" | "coop_group"
    source_channel_id: str
    execution_config: dict        # 用户指定执行配置(指定 bot/workflow yaml/MAX_DEPTH 等)

@dataclass AcceptanceResult:      # 无 verifier 字段
    verdict: AcceptanceVerdict
    acceptances_metric: list[str] # 已满足的验收指标明细
    gaps: list[str]               # 驱动 plan 自算 gap,不是 plan 入参

@dataclass RuntimeInfo:           # 持久运行面(所有 None 均合法域态)
    run_mode: str | None          # "single_bot"/"coop_group"/"bbs";无 collab_mode
    assignee: str | None          # 执行者(bot_id / group_id)
    start_time: float | None; end_time: float | None
    output: dict
    acceptance_result: AcceptanceResult | None
    extend_props: dict            # miss_events(list[str]) / 崩溃栈/超时
    # depth 核内派生(从 relations),非持久

@dataclass Relation:              # 分解树边(一等公民);承载结构归属,单入(每非根节点恰好 1 入边=结构父)
    src_id: str                   # 结构父(分解源/被依赖)
    dst_id: str                   # 结构子(分解产物/依赖方)
    type: RelationType            # DEPENDENCY=分解树
    extend_props: dict            # 关系元数据(可扩展)

@dataclass TaskNode:              # 含 task_id/node_run_graph;结构归属由 graph.relations 分解树表达(无 decomposed_by 字段)
    node_id: str                  # 节点唯一实例 ID
    task_id: str                  # 节点所发整体任务 ID(归属键)
    status: Status
    task_spec: TaskSpec
    run_info: RuntimeInfo
    node_run_graph: "TaskExecutionGraph"   # 节点所属执行图实例引用
                                   # 结构父/结构子查询、验收归属、传播一律从 graph.relations 分解树派生;
                                   # 无跨兄弟/跨层级直接数据边——数据流由步进式批规划顺序 + 执行时结构父聚合上下文承载(见 §3.5.4)

@dataclass TaskExecutionGraph:    # 含 run_id/relations
    run_id: int                   # 运行实例唯一 ID
    loop_round: int               # 外层 BBS 上升轮次(仅升 BBS 时++;达 BBS_MAX_DEPTH→STUCK→HUNG)
    status: Status
    output: dict                  # 图的最终汇总输出
    tasks: list[TaskNode]
    relations: list[Relation]     # 依赖关系(一等公民)
    extend_props: dict
    # 派生不持久: depth / child_tasks / parent_task(均从 relations 分解树派生)
```

> **依赖关系**:结构归属由 `Relation{type=DEPENDENCY}` 分解树表达(单入),`depth`/`get_child_tasks`/`get_parent_task`/传播均从 `relations` 派生;就绪=被 `add_task_nodes` 加入即就绪(无 `dependencies_satisfied` 闸门)。
> **`PLANNING` 语义**:节点被分解委托子执行时进 `PLANNING`(显式状态);子全 PASS → 传播该节点 DONE。
> **`collab_mode`**:`RuntimeInfo` 无 `collab_mode`;协作方式作 `form_coop_group` 内部参数,不持久。 

### 2.1 中间类型(patch/criteria/op_result)

```python
@dataclass TaskNodePatch:          # 节点级原子写(update_task_node_info 入参)
    task_id: str; node_id: str
    status: Status | None = None
    run_mode: str | None = None             # str
    assignee: str | None = None
    output_patch: dict | None = None        # fold 到 run_info.output
    acceptance_result: AcceptanceResult | None = None   # 唯一终态翻转依据
    extend_props_patch: dict | None = None  # miss_events / hung_reason(stuck) / 崩溃栈

@dataclass TaskGraphPatch:         # 图级原子写(update_task_graph_info 入参,增量 patch;未给不动)
    loop_round_increment: int | None = None     # 原子加(升 BBS ++)
    status: Status | None = None                # 置图级终态(DONE 根终验 PASS / HUNG STUCK)
    output_patch: dict | None = None            # 浅合并到图 output
    extend_props_patch: dict | None = None      # 浅合并到图 extend_props(bbs_mode / hung_reason)

@dataclass TaskNodeQueryCriteria:   # 节点查询条件(内部用)
    status: Status | None = None
    node_ids: list[str] | None = None
    has_child_tasks: bool | None = None    # True=仅叶节点(无结构子),False=仅内部节点(有结构子)

@dataclass TaskOpResult:            # facade 级返回
    task_id: str; success: bool
    error: str | None = None
    run_id: int | None = None       # 关联运行实例
@dataclass NodeOpResult:            # 节点级写返回
    task_id: str; node_id: str; success: bool
    prev_status: Status | None = None; new_status: Status | None = None
    error: str | None = None

@dataclass TaskCallbackData:        # 回投数据协议(对齐执行模块文档)
    loop_task_id: str               # 关联回框架侧 (task_id, node_id)
    workflow_type: str              # "single_bot" | "bcn_coop_group" | "bbs" | ...
    workflow_id: int
    instance_id: int                # workflow 运行实例 id
    result: dict                    # {"success": bool, "data": "..."} / {"fail_detail": "..."}
```

---

## 3. 模块类与 API 定义

### 3.0 内部编排核(`TaskService` 内部;非独立模块;无对外 API)

> 编排核为**事件驱动 + 状态条件触发**:在 facade `execute`/回投适配后,按图谱状态条件协调 `plan→add_task_nodes→dispatch→start_run`。是 `TaskService` 内部实现细节,对外只暴露 2 facade API(§3.7)。

> **协程化(CR 反馈:任务执行是耗时任务)**:全链路 `async def`——`on_execute`/`on_report`/`on_miss`/`on_harness`/`start_run`/`form_coop_group`/`report_result`/`DeliveryPort.deliver`/**`plan`/`dispatch`/策略 `matches`/`apply`** 均为协程。`plan`/`dispatch`(corp 为 LLM 规划 / bot catalog 搜推,耗时 IO)在 per-task `threading.RLock` **锁内 `await`**(同 task 串行推进的 IO,设计意图;不同 task 锁隔离互不阻塞)。锁内不 `await` 的是**高并发外部投递 IO**(`start_run`/BCS 拉群 `form_coop_group`/`deliver`)——这些 `await` 在锁外,gather+Semaphore 并发下沉 `TaskRunner.start_run` 内部(`_DELIVER_CONCURRENCY=8`)。**副作用收集模式**:`on_*` 锁内 `async collect`(`await plan/dispatch` + 同步 add/patch,产出 side-effects list)→ 锁外 `_drain` 统一 `await` 执行 run/group/miss/finish(投递 IO)。`on_report` 链路:`report_result(await) → on_report(await) → 锁内翻态+async collect → 锁外 _drain await 投递`。注:`threading.RLock` 在本仓一次性事件循环/跨线程回调模型下跨线程正确串行;若 corp 采用单持久 loop 并发处理同 task 多回投,需切 `asyncio.Lock`(ocb 仓接入时定)。

> **编排核零参自建**(在 `task_center/engine.py` 内定义 Protocol,DI 注入;Avernet 缺省 no-op,singlebox/测试注入 double,corp 注入真实 adapter):
```python
# (V1/V2):无 OwnerBotVerifyPort / 无 BbsMarketPort。验收 100% 走 on_report 回投;
#     BBS 投递归 runner BBS 模态(下次 dispatch→start_run 经 DeliveryPort)。
#     引擎 __init__(graph) 零参自建;_build_planner/_build_dispatcher/_build_runner 工厂方法(corp 覆写 seam)。
```
```python
class ExecutionEngine:   # TaskService 内部编排核(不对外)
    """事件驱动 + 状态条件触发协调 plan/graph/dispatch/execution。
    零参自建 TaskGraphService/TaskPlanner/TaskDispatcher/TaskRunner(内置策略池+stub 投递);BBS 模态统一归 run_mode="bbs"(投递归 runner,无 BbsMarketPort)。
    按事件 + 状态条件分段协调(无 single drive fixpoint 泵)。on_* 入参统一收口为 TaskNodePatch。"""
    def __init__(self, graph, planner, dispatcher, runner,
    def __init__(self, graph) -> None: ...

    async def on_execute(self, task_id) -> None:
        # execute 事件:initialize_graph(根 PENDING)→ 触发首帧推进:
        #   条件 a(根 PENDING)成立 → planner.plan(graph) → graph.add_task_nodes(第一层, parent_node_id=根, 根进 PLANNING)
        #   → 条件:有新 PENDING 就绪 ∧ 无 RUNNING → dispatcher.dispatch(toDo) 返回填执行者后的 list[TaskNode](不写图不起 run)→ 编排核落库(graph.update_task_node_info run_mode/assignee/RUNNING)→ runner.start_run
    async def on_report(self, patch: TaskNodePatch) -> NodeOpResult:
        # 回投事件:patch 内含 (task_id,node_id) + 唯一翻态依据 acceptance_result + output_patch。
        #   graph.update_task_node_info(patch) 翻态(+fold output):
        #   PASS→DONE:查结构父 P=get_parent_task(本);若 P=PLANNING 且 P 的全部结构子(get_child_tasks(P)=本批兄弟)均 DONE ∧ 无 RUNNING(决策C:等本批兄弟全 DONE 才触发父 plan):
        #     → 委托 plan→decompose(P):产新子→add_task_nodes(下一批, parent_node_id=P, P 仍 PLANNING)→dispatch(返 list[TaskNode] 填执行者)→编排核落库(RUNNING)→start_run;
        #       decompose 返 [](P 的 gap 已闭)→P 非根→P→DONE(传播治愈,再上行查 P 的父)/P=根→不自动 DONE,走终验(§5.4)。兄弟未齐则等待,不触发 plan。
        #   FAIL+gaps→FAILED:深度闸门(<MAX 放行)→ 条件 b(FAILED+gaps 叶子)成立 → plan → add_task_nodes(补救子, parent_node_id=该节点, 该节点进 PLANNING)→ dispatch(返 list[TaskNode] 填执行者)→ 编排核落库(RUNNING)→ start_run
        #   (FAIL 无 gaps 已消灭:验收 skill 强制要求给 gaps;STUCK 走 on_miss 升 BBS 链路上限判,不在此分支)
        # 返回 NodeOpResult(prev/new_status)供适配层 ack,不作驱动依据
    async def on_miss(self, patch: TaskNodePatch) -> None:
        # dispatcher MISS → 节点仍 PENDING,patch.extend_props_patch.miss_events 已由 dispatcher 填
        #   → 深度闸门:<MAX→ plan→add_task_nodes(拆细, parent_node_id=该节点)→ 消费 miss_events → dispatch;
        #     ≥MAX(MISS 深度达到 MAX_DEPTH)→ **自动升 BBS**:remove_subtree(task_id,xx_node)(删 xx_node 及其下整个
        #     子树;前提:xx_node 下所有子都 MISS、没走 RUNNING)+ loop_round++ + 标 BBS(挂任务广场供 bot 认领)。
        #   BBS bot 认领任务后才执行:加载完整上下文(已完成 output+验收 vs task_spec/goal/context)算 gap+
        #     据自能力规划子任务 → add_task_nodes(run_mode="bbs",assignee=bot_id,挂根下)→ 上报结果+验收(见 on_report)。
        #   on_report 驱动:PASS→触发根节点 plan / FAIL+gaps→触发该子任务节点 plan;都走正常 plan→decompose→dispatch→execute。
        #   BBS 链路再迭代(loop_round 达 BBS_MAX_DEPTH)仍执行不下去→STUCK→HUNG(stuck)→人介入。
    async def on_harness(self, patch: TaskNodePatch) -> None:
        # Harness 旁路:RUNNING 超时/崩溃→复位回 PENDING(update_task_node_info)→正常 dispatch 重投。
        #   不抢正向驱动;主链下一轮事件自然续驱。不直接写 HUNG(STUCK 走 on_miss 升 BBS 链路上限判)。
    # loop_round: 仅升 BBS 时 graph.loop_round++(外层 BBS 上升轮次;正常补救不再 ++)
```

> 每个事件 on_* 分段推进,由状态条件(a/b/c + plan 三条件)把关是否进入下一阶段。同 task_id 仍串行(per-task `threading.RLock`,仅保护锁内同步编排写;投递/拉群 IO 锁外 await 不受锁约束)。协程化:`on_*` 锁内 collect(同步)→ 锁外 `_drain` await run/group/miss/finish(投递 gather+Semaphore 在 runner)。

### 3.1 `TaskGraphService`(内部图谱 SSOT,8 API:5 核心写/读+3 派生只读,独立模块)

> `TaskGraphService` 为独立模块(对齐任务图谱文档 `lunk1txfuv6gtwk2`),`TaskService` facade 持有其引用。图谱原子变更唯一网关。

```python
class TaskGraphService:
    """任务图谱 SSOT + 原子变更唯一网关。
    边界:只做图结构 + 节点级写 + **图级写**(图终态)+ 派生只读查询;不含编排(不调编排核、不搜推、不规划)。
    图级终态(图 ``status`` DONE/HUNG、图 ``output``、``loop_round``、图 ``extend_props`` 的 bbs_mode/hung_reason)
    经 ``update_task_graph_info(TaskGraphPatch)`` 收口(原子、加锁、SSOT 唯一图级写口);编排核不直写返回的 graph 引用。"""

    def initialize_graph(self, task_info: TaskInfo) -> TaskExecutionGraph:
        """建图首帧(全局 RUNNING,只含根节点 PENDING,task_id=task_spec.metadata.id,run_id 分配);
        幂等:同 task_id 重复调抛冲突。调用方:需求识别 skill(execute 内部)。"""

    def add_task_nodes(self, tasks: list[TaskNode], parent_node_id: str) -> TaskExecutionGraph:
        """并子图(单写 relations 分解树,无 decomposed_by 字段;**显式传父** `parent_node_id`,方案 C)。触发条件(图谱文档 a/b/c,由编排核判后调):
          a. 只有一个根节点且 status=PENDING(初始规划);
          b. 叶子节点验收未通过:存在 FAILED 节点 且 acceptance_result.gaps 非空的叶子节点(补救);
          c. 父节点验收未通过:存在 PLANNING 节点 ∧ 无 RUNNING(下一层规划可推进;前层产出已落 output)。
        登记分解树:每新子挂显式传入的结构父 `parent_node_id`(批规划目标节点)下——写入 graph.relations 的 DEPENDENCY 边
          (src=结构父,dst=新子,单入);父节点进 PLANNING(显式委托态,已在 PLANNING 则维持)。
        单层同构硬约束:本批新节点结构父只能指向已存在节点,本批内不互为父子(单入防环/汇聚);本批兄弟
          无直接数据依赖边——数据流由步进式批规划顺序 + 执行时结构父聚合上下文承载(见 §3.5.4)。
        task_id 从 tasks[0].task_id 取(同批同 task_id)。不改其他已有节点状态。
        返回更新后的整图。调用方:任务规划 skill。"""

    def update_task_node_info(self, patch: TaskNodePatch) -> NodeOpResult:
        """节点级原子状态流转网关。唯一翻态依据=patch.acceptance_result:
          PASS→DONE / FAIL+gaps→FAILED(验收 skill 强制要求给 gaps,不存在 FAIL 无 gaps);
          无 acceptance_result 只 fold output 不翻态(Harness 复位用 patch.status=PENDING 回退)。
          派发写:patch.run_mode(str)/assignee 落库 + 置 RUNNING。
        task_id/node_id 从 patch 内取;幂等。调用方:任务派发 skill + 任务执行 skill。"""

    def update_task_graph_info(self, task_id: str, patch: "TaskGraphPatch") -> TaskExecutionGraph:
        """图级原子写口(收口图级终态 SSOT 唯一网关)。入参 ``TaskGraphPatch``(增量 patch,未给不动):
          ``loop_round_increment`` 非空 → ``loop_round`` 原子加(默认 +1,升 BBS 用);
          ``status`` 非空 → 置图级终态(``DONE`` 根终验 PASS / ``HUNG`` STUCK);
          ``output_patch`` → 浅合并到图 ``output``;
          ``extend_props_patch`` → 浅合并到图 ``extend_props``(承载 ``bbs_mode``/``hung_reason``)。
        加锁原子;编排核升 BBS / 根终验完成等图级终态变更一律经此方法,不直写返回的 graph 引用。
        调用方:编排核(内部)。后续 ORM 适配只改本方法实现。"""

    def query_task_dashboard(self, task_id: str, node_id: str = None) -> TaskExecutionGraph:
        """只读看板快照(整图或按 node_id 子树投影)。调用方:API(经 facade get_task_dashboard)。"""

    # ===== 派生查询(只读;供编排核/planner/dispatcher/runner;均从 relations 分解树派生)=====
    def query_task_nodes(self, task_id: str, criteria: TaskNodeQueryCriteria) -> list[TaskNode]:
        """按条件查节点。就绪扫描:criteria={status=PENDING}→ 返回 PENDING 可派发节点
          (PLANNING 委托态不在 PENDING,天然排除);has_child_tasks 可筛叶/内部节点。"""
    def get_child_tasks(self, task_id: str, node_id: str) -> list[TaskNode]:
        """读某节点【结构子】=relations 中 src_id==node_id 的 dst 节点(直接分解产物)。
          用途:验收时机/验收上下文聚合/传播判定(决策C:本批兄弟全DONE)/规划去重。"""
    def get_parent_task(self, task_id: str, node_id: str) -> TaskNode | None:
        """读某节点【结构父】=relations 中 dst_id==node_id 的 src 节点(单入,至多 1 个;根返回 None)。
          用途:执行上下文聚合结构父 P 的聚合上下文、深度闸门递归上溯、定位兄弟。"""
    def remove_subtree(self, task_id: str, node_id: str) -> TaskExecutionGraph:
        """删节点 + 其下整个子树(递归 get_child_tasks 删;含 relations 边)。
        触发:升 BBS 时——某 xx_node 搜推 MISS 且其下所有子都 MISS、没走 RUNNING(整子树无效)。
        返回更新后的整图。调用方:编排核 on_miss 升 BBS 分支。"""
    def _node_depth(self, task_id: str, node_id: str) -> int:
        """从 relations 分解树递归自算深度(派生不持久)。内层深度闸门(MAX_DEPTH,升 BBS 阈值)读。"""
    def _execution_config(self, task_id: str) -> dict:
        """读 MAX_DEPTH(内层升 BBS 阈值)/ BBS_MAX_DEPTH(外层 STUCK 阈值,默认 3)等(随图 extend_props/task_spec)。"""
```

> 派生查询:`query_task_nodes`/`get_child_tasks`/`get_parent_task` 升为公开(跨模块依赖:dispatcher/runner/planner/传播);`_node_depth`/`_execution_config` 保持内部(仅编排核用,可从已返回查询/relations/dashboard 自算)。
> 旧 `compute_output_projection` 不在图谱;执行/验收上下文由 `TaskRunner` 内聚(内部自动判定,见 §3.5.4)。
> **图级写归属(M1)**:`TaskGraphService` 持节点级写(`update_task_node_info`)+ 图结构写(`add_task_nodes`/`remove_subtree` 的 relations)+ **图级写**(图终态 `update_task_graph_info(TaskGraphPatch)`,收口 SSOT)。图级终态(图 `status`=DONE/HUNG、图 `output`、`loop_round`++、`extend_props` 的 `bbs_mode`/`hung_reason`)由编排核经 `update_task_graph_info` 写,不直写返回的 graph 引用。`TaskGraphPatch` 中间类型见 §2.1。

### 3.2 `TaskPlanner` 规划编排壳(零参 + 内置策略池 `PlanningStrategy`)

```python
class PlanningStrategy(Protocol):     # 规划优化策略契约(引擎内置,first-match-wins;非领域实体,模块层)
    def decompose(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        """读图自行发现规划目标(FAIL 叶子 / PLANNING 父)并产"下一步可执行的子节点"
          (挂该目标下;status=PENDING,run_info 空,task_id 已填,node_run_graph 指向所属图)。
        target-finding 由本 seam 自洽(不再由 planner 预选 target 传入);planner 仅做纯读图去重
          + 步进式 deps 满足才产 + 硬契约兜底。返回 [] 可表"无可规划目标"
          (decompose(root)==[] 的判断属实现侧:stub/corp 各自负责,框架不介入)。默认:corp 走规划
          agent(plan_bot)/LLM SKILL;Avernet stub。"""

class TaskPlanner:     # 编排壳,零 case 知识
    def __init__(self, graph) -> None: ...   # 零参;内置策略池 [WorkflowPlanningStrategy(prio10), GapBasedPlanningStrategy(prio99)]
    def set_strategies(self, strategies: list[PlanningStrategy]) -> None: ...   # 非公开:engine 工厂/corp 子类注入
    async def plan(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        # 协程化:策略 apply 在 corp 是 LLM 耗时 IO,锁内 await(同 task 串行,设计意图)。
        # 触发条件(规划文档):图谱有更新(新增失败节点/PLANNING 节点)
        #   AND 没有派发(RUNNING)或执行中节点 AND 状态图谱有处于 PLANNING 状态的节点
        #   不满足 → 返回 [] 空跑
        # 1) 读图自发现规划目标(不依赖具体节点名):
        #    - FAIL: status=FAILED 且 acceptance_result.gaps 非空 且 无分解子(叶子补救)
        #    - 前向/委托: status=PLANNING 的父节点(planner 不自查 deps;由编排核先确认无 RUNNING 再调 plan)
        # 2) 规划原则(规划文档硬约束):派发/执行中节点不可改(含前序依赖);
        #    只对失败节点 + 子全完成且自身 PLANNING 的父节点规划
        # 3) 调 decomposer.decompose(graph) — 由 seam 自行发现 target + 产子;planner 不预选 target
        # 4) 硬契约兜底:纯读图去重(图上已存则不产);步进式 deps 满足才产
        # 5) 返回并集 list[TaskNode](不含物理执行信息)
```

> **默认实现**:Avernet 默认 `GapBasedPlanningStrategy`(stub 返 [])/`WorkflowPlanningStrategy`(读 config 拓扑 stub);corp 经 ocb 仓 `CorpEngine._build_planner` 覆写注入真实 LLM 规划策略(test/corp adapter 包成 `PlanningStrategy` 注入)。
> **PlanningStrategy 与 TaskPlanner 关系(引擎内置,不开放自定义)**:`TaskPlanner` 零参构造,内置策略池(判触发条件/读图发现目标/硬契约去重,零 case 知识,框架固定);`PlanningStrategy` 是规划优化策略(`matches`/`apply`,first-match-wins by priority,类 SQL optimizer)。分层:① 开源边界(Avernet 框架不绑 corp LLM,只发 stub 策略;corp 经 ocb 仓 `_build_planner` 覆写注入真实策略版本);② 可测试(测试/corp adapter 包成 `PlanningStrategy` adapter 注入,不依赖 LLM);③ 策略版本化(引擎后续加新策略版本,不开放外部自定义)。领域模型无 PlanningStrategy(非领域实体,模块层 §3)。
> **硬契约**:① 产的每个子其父语义已就绪可委托;② 无状态纯读图去重;步进式 deps 满足才产。`plan` 不接收外部 gaps。
> MISS 经 `on_miss` 写 miss_events 后,编排核按条件 b 类(FAILED+gaps)路径处理(或 HUNG);PLANNING 前向目标用显式状态判。

### 3.3 `TaskDispatcher`(决定"谁来做",不做执行)

> **职责**:据搜推 4 态选执行主体 + 多 bot 动态拉协作群;**把 `run_mode`(str)/`assignee` 填到 `TaskNode.run_info` 后返回 `list[TaskNode]`,不写图、不起 run**(对齐派发文档 `dispatch(toDoTaskList)->List[TaskNode]`);执行交 `TaskRunner.start_run`(由编排核拿返回节点后调用)。分层:搜推(谁做)填 TaskNode → 编排骨 `update_task_node_info`(落派发目标+RUNNING)→ `start_run`(真正发)。

```python
class DispatchStrategy(Protocol):    # 派发优化策略契约(引擎内置,first-match-wins)
    def search(self, node: TaskNode) -> "SearchResult": ...
    # -> HIT_SINGLE(bot_id) | HIT_GROUP(group_id) | HIT_MULTI_BOTS(group_formation,含 collab_mode) | MISS
    # collab_mode 在 SearchResult/GroupFormation 内(内部参数),不进 RuntimeInfo 持久

class TaskDispatcher:
    def __init__(self, graph) -> None: ...   # 零参;持 graph 只读 config;内置策略池 [DirectDispatchStrategy(prio10), SearchBasedDispatchStrategy(prio99)]
    def set_strategies(self, strategies: list[DispatchStrategy]) -> None: ...   # 非公开:engine 工厂/corp 注入
    # 不持 runner(HIT_MULTI_BOTS 标 pending_group_formation;拉群归编排核+runner)
    async def dispatch(self, toDoTaskList: list[TaskNode]) -> list[TaskNode]:
        # 协程化:catalog 搜推在 corp 是耗时 IO,锁内 await。
        # 入参=待派发节点;返回=填充执行者信息后的 list[TaskNode](对齐派发文档签名);
        #   不写图、不起 run;per node 仅按 node.task_spec 搜推,把结果填 node.run_info 上:
        #   HIT_SINGLE     → node.run_info.run_mode="single_bot",  assignee=bot_id
        #   HIT_GROUP      → node.run_info.run_mode="coop_group",  assignee=group_id
        #   HIT_MULTI_BOTS → runner.form_coop_group(gf)→ node.run_info.run_mode="coop_group", assignee=gid
        #   MISS → 不填执行者(run_mode/assignee 仍 None,节点 status 仍 PENDING),标 node.run_info.extend_props.miss_events
        # BBS 节点(run_mode 已由 BBS bot 认领时标 "bbs")→ dispatch 退化为直接标 run_mode="bbs"+assignee=bot_id(不走搜推 4 态)
        # 返回 list[TaskNode] 交编排核:有 assignee 的→ graph.update_task_node_info(run_mode/assignee,RUNNING)+ runner.start_run;
        #                     标了 miss_events 的→ 编排核 on_miss(深度闸门)
```

> 搜推/拉群不对外;`HIT_MULTI_BOTS` 时 `collab_mode` 由 `DispatchStrategy.apply` 一并决出(在 `GroupFormation` 内,作 `form_coop_group` 参数)。引擎内置 `SearchBasedDispatchStrategy`/`DirectDispatchStrategy`(§3.4)。
> **派发文档注**:对齐派发文档 `dispatch(toDoTaskList)->List[TaskNode]`——dispatcher 搜推后把 `run_mode`/`assignee` 填到 `TaskNode.run_info` 上返回 `list[TaskNode]`,**不写图、不起 run**;编排核拿返回节点后调 `graph.update_task_node_info(run_mode/assignee,RUNNING)` 落库 + `runner.start_run`(图谱 SSOT,dispatcher 不直接写图;返回的 TaskNode 是入参填充后副本,落库由编排核经 patch 完成)。

### 3.4 引擎内置策略(`PlanningStrategy`/`DispatchStrategy`, first-match-wins by priority)

> 策略是**引擎自带能力,不开放自定义**(类 SQL optimizer rule-based):经 `execution_config` 动态匹配命中,非用户手动 SET。要优化引擎自己加新策略版本;corp 经 ocb 仓 `CorpEngine` 覆写 `_build_*` 替换策略版本。

```python
class PlanningStrategy(Protocol):
    rule_id: str; priority: int
    async def matches(self, graph) -> bool: ...   # 纯读:据图级 execution_config 判适用(workflow 信号)
    async def apply(self, graph) -> list[TaskNode]: ...   # 自发现 target+产子(corp LLM 耗时 IO)
class DispatchStrategy(Protocol):
    rule_id: str; priority: int
    async def matches(self, node, graph) -> bool: ...   # 纯读:据 execution_config(bot 信号)
    async def apply(self, node, graph) -> SearchResult: ...   # 4 态(corp catalog 搜推耗时 IO)
# 默认策略池: TaskPlanner 内置 [WorkflowPlanningStrategy(prio10,config.workflow), GapBasedPlanningStrategy(prio99,兜底 stub 返[])]
#            TaskDispatcher 内置 [DirectDispatchStrategy(prio10,config.bot→HIT_SINGLE), SearchBasedDispatchStrategy(prio99,兜底 stub 恒MISS)]
# first-match-wins:planner/dispatcher.plan/dispatch 遍历按 priority 升序,首个 matches=True 的 apply
```
- Avernet 默认 stub(gap 返 [];search 恒 MISS);corp 真实 LLM 规划/搜推 catalog 经 ocb 仓 `CorpEngine._build_planner/_build_dispatcher` 覆写注入真实策略。`DecomposerPort`/`BotDiscoverPort` 已删(改内置策略类)。
- TaskRunner 三类投递后端(`DeliveryPort`):single_bot/coop_group/bbs 各一;Avernet 默认 stub 记日志;corp `set_delivery` 注入真实 workflow engine/BCS/BBS 广场。

### 3.5 `TaskRunner` 任务执行模块(对齐执行文档 `lxg2mwgmtfqg6d95`)

> 功能:把已派发任务按派发目标发送给**单 bot / 协作群 / BBS**执行,并回收状态/详情/结果。一个 `start_run(批量)` 入口三模态自适应;`form_coop_group`(动态拉群)内部辅助;BBS 认领执行由 bot 自主,**不在此接口内**。

#### 3.5.1 供任务 Loop 内部和产品使用的 API(`TaskRunner`)

```python
class TaskRunner:
    """将已派发 TaskNode 发送给单 bot/协作群/BBS 执行,并回收状态/详情/结果。
    调用方:编排核(经 TaskService facade 驱动)。"""

    async def start_run(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        """图谱上有 TaskNode 完成派发后,立即触发执行。入参批量(刚被 Dispatcher patch 完 run_mode/assignee 的节点);
        返回每个任务派发是否成功 list[bool]。协程化:真实投递(单 bot workflow/BCS 协作群/BBS 广场)是网络 IO,内部对批量节点经 `asyncio.gather` + `_DELIVER_CONCURRENCY`(Semaphore=8)并发投递(对齐 backend lifecycle 模式),`await` 不阻塞编排核。内部按每节点 run_mode(str)自适应分发:
          "single_bot" → 单 bot workflow(workflow_type=single_bot)
          "coop_group" → bcn 协作群(已有群 or 刚 form_coop_group 拉的群)
          "bbs"        → BBS bot 认领任务后,自己算 gap+据自能力规划子任务(add_task_nodes 落图 run_mode="bbs",assignee=bot_id)
                          → 自己执行 → 不管验收通过与否都上报结果+验收(经 on_report 正常驱动)
        派发成功仅表示"已投递给执行主体",不等于完成;完成结果经回调(下)回收。"""

    def query_status(self, task_id: str) -> "Status":
        """产品/系统触发:查询某任务及其所有子任务的状态。"""

    def query_detail(self, node: TaskNode) -> TaskNode:
        """产品触发:查询任务最新详情(回填 node.run_info)。"""

    def query_result(self, node: TaskNode) -> TaskNode:
        """产品/系统触发:查询某任务及其所有子任务的产出结果(回填 node.run_info.output)。"""

    def query_bot_tasks(self, bot_id: str) -> list[TaskNode]:
        """获取某个 Bot 下的所有任务实例列表。"""

    async def form_coop_group(self, gf: "GroupFormation") -> str:
        """(内部)HIT_MULTI_BOTS 动态拉协作群,复用 BCS 建群 → group_id。协程化:BCS 建群是网络 IO,`await`(由 engine 锁外 await 调用,不阻塞编排核)。
        CHAT/MANAGER_WORKER/STATE_MACHINE 三模式(group_strategy=collab_mode;state_machine 注入 workflow yaml)。
        collab_mode 在 GroupFormation 内(内部参数),不进 RuntimeInfo 持久字段。"""
```

#### 3.5.2 回调服务(供单 bot workflow / bcn 协作群,PUSH 回投)`TaskLoopCallback`

```python
class TaskLoopCallback:
    """供执行实体(bot workflow 或 bcn 协作群)PUSH 回投,对接框架 update_task_node_info(经编排核 on_report)。"""

    async def start_run(self, data: TaskCallbackData) -> None:     # 任务开始执行(可选进度信号);协程化与 report_result 链路一致
        ...
    async def report_result(self, data: TaskCallbackData) -> None: # 任务完成或失败(success/data or fail_detail)
        # 框架适配层把 data 组装成 TaskNodePatch(task_id/node_id 从 loop_task_id 映射;
        #              acceptance_result 从 result.success/data 映射;output_patch=fold data;
        #              fail_detail → extend_props_patch)→ 编排核 on_report(patch)(await) → graph.update_task_node_info(patch) → 按 verdict 翻态/传播/补救
        # 协程化:on_report 是 async,await 不阻塞回投调用方(HTTP 适配层/外部 bot workflow)。
```

#### 3.5.3 三模态自适应作用

| 模式 | start_run 内部动作 | 结果回收 | Avernet 实现 | prod 实现 |
|---|---|---|---|---|
| 单 Bot | 调单 bot workflow(workflow_type=single_bot) | `TaskLoopCallback.report_result`(PUSH)或 `query_result`(PULL) | seam + singlebox double(本地 bot stub) | corp adapter |
| 协作群 | 触发 bcn 协作群(群可能刚 `form_coop_group` 拉的) | `TaskLoopCallback`(群终态回投) | seam + BCS local/mock 拉群 | corp BCS wiring |
| BBS | BBS bot 认领任务后自算 gap+规划子任务(落图 `run_mode="bbs"`,`assignee=bot_id`)→ 自执行 | 认领 bot 自主 `report_result` 回投(PASS→触发根 plan / FAIL+gaps→触发该子任务节点 plan) | seam + stub(任务广场) | corp 任务广场 + BBS bot 自能力规划 |

#### 3.5.4 上下文组装(Runner 内聚;内部自动判定,无 NODE/SUBTREE/TASK scope 区分)

验收只按 `(task_id, node_id)` 上报对应节点——执行主体/owner bot 验收后直接把结论回投该节点,**不引入 NODE/SUBTREE/TASK scope 参数**。`start_run` 内部据该节点**是否有结构子**自动判定组装上下文:

- **验收模式**(有结构子,`get_child_tasks(task_id,node)` 非空):本节点已被分解委托子执行 → 聚合【结构子(子树)run_info.output + 本节点 `task_spec.goal/acceptances`】→ 组装**验证 prompt**,经 `source_channel` 派给 owner/master bot 用 skill 验收 → bot 回投 verdict 直接落该节点。(根节点的终验即此模式:结构子=全图非根,聚合得全图 DONE 产出;**非根 PLANNING 节点**:本批结构子全 PASS 后由编排核委托 `decompose` 判定——decompose 返 [](gap 已闭)→ 自动传播该节点 DONE,不另起验收 skill;返新子 → 继续下一批。)
- **执行模式**(无结构子):本节点是叶执行节点 → 取结构父 `P = get_parent_task(task_id,node)` → 聚合【P 的聚合上下文 = `P.task_spec`/`P.goal` + P 的已 DONE 结构子(本节点的已完工兄弟,即 `get_child_tasks(task_id,P.node_id)` 中 status=DONE 且非本节点者)的 `run_info.output` + 本节点 `task_spec`】→ 组装**执行 prompt** 注入执行主体(单 bot/协作群/BBS)。数据流一律经结构父 P 这一层中转,不建跨兄弟直接数据边。

bot/群据 `node.task_spec.goal` + 该上下文产出 → 经 `TaskCallbackData.result` 回投 → 框架适配层按 success/data 映射成 `AcceptanceResult` 落该节点。`TaskGraphService` 不提供 `compute_output_projection`;上下文聚合由 Runner 内部 helper `_build_context(task_id, node)` 用 `get_child_tasks`/`get_parent_task` 组合收口,验收/执行模式自动切换(无 scope 入参)。`form_coop_group` 复用现有 BCS(`crates/contracts/bcs-domain` `GroupStrategy`/`CollaborationRuntimeDefinition`),群自闭环持 `SubDagRef(bcs_run_id)` 收终态回投。

### 3.6 `TaskHarness`(旁路常驻)

```python
class TaskHarness:
    """旁路常驻:周期巡检 SLA 超时/崩溃,经 graph.update_task_node_info 复位回 PENDING,不抢正向驱动。
    超时阈值从 execution_config / extend_props 读(SLA 不在 TaskSpec)。"""
    def run_poll_loop(self) -> None:
        # 周期:graph.query_task_nodes(status=RUNNING) → 比对 start_time + sla_timeout → 超时/崩溃
        #   → graph.update_task_node_info(TaskNodePatch{status=PENDING, extend_props_patch={崩溃栈/超时}})复位 → 正常 dispatch 重投
        # 不调编排核正向;主链下一轮事件自然续驱。RUNNING 复位不直接写 HUNG(STUCK 走 on_miss 升 BBS 链路上限判)
```

### 3.7 对外 API(`TaskService` facade,2 个)

> facade 暴露 2 API(对齐任务中心文档 `yugg6dorsxo8sgmp`):`execute`/`get_task_dashboard`。`add_task_nodes`/`update_task_node_info`/`query_task_dashboard` 下沉 `TaskGraphService`,`dispatch`/`plan`/`start_run` 各归各模块。

| facade 方法 | 调用方 | 触发时机 | 内部委托 |
|---|---|---|---|
| `execute(task_info) -> TaskOpResult` | API or 需求识别 skill | 提交执行任务 | `graph.initialize_graph` + 编排核 `on_execute`(plan→add→dispatch→start_run) |
| `get_task_dashboard(task_id, node_id=None) -> TaskExecutionGraph` | API | 任务执行详情可视化(eg.副屏) | `graph.query_task_dashboard` |

```python
class TaskService:   # facade(2 API);内部持编排核 + TaskGraphService + Planner + Dispatcher + Runner (+ Harness)
    def __init__(self, graph: TaskGraphService, harness=None): ...  # 零参 facade;`_build_engine()` 工厂方法自建 ExecutionEngine(零参自建 planner/dispatcher/runner);
                 # corp 子类覆写 `_build_engine` 返回 CorpEngine;回填 harness.on_harness;暴露 callback(TaskLoopCallback)。
                 # engine 对调用方不可见(无 engine property)
                 # 协程化:execute 为 async;内部 await engine.on_execute(async 链路)。

    async def execute(self, task_info: TaskInfo) -> TaskOpResult:
        # 协程化:await on_execute(async 链路),耗时投递(BCS/真实 workflow)不阻塞调用方。
        # graph.initialize_graph(task_info)(根 PENDING)→ 编排核 await on_execute(task_id)
        #   → 首帧推进(条件 a:根 PENDING → plan → add_task_nodes → dispatch → start_run)
        # 返回 TaskOpResult{task_id, success, run_id}
    def get_task_dashboard(self, task_id: str, node_id: str = None) -> TaskExecutionGraph:
        # graph.query_task_dashboard(task_id, node_id);只读
```

> 无 `report_search_result`(搜推内部,在 Dispatcher);无 `add_task_nodes`/`update_task_node`/`abandon_task`/`rollback_to_node`(5 模块文档未提供 facade 版;若需人工操作,后续扩展确认后补,预留 `on_harness`/人工事件位点)。回投经 `TaskLoopCallback` 适配层 → 编排核 `on_report`(非 facade 直暴露)。
> facade 另暴露只读属性 `callback`(返回 `TaskLoopCallback`,供执行实体 PUSH 回投)与 `engine`(编排核,测试/编排观测用,生产不应跨 facade 直接驱动)。`execute` 内部:`initialize_graph` + `engine.on_execute` + (若注入 harness)`harness.register(task_id)`。

---

## 4. 控制流总图(事件驱动 + 状态条件触发)

```mermaid
flowchart TD
    ExecEvt["Owner/API execute(TaskInfo)"] --> TS["TaskService.execute"]
    TS --> IG["graph.initialize_graph(根 PENDING)"]
    IG --> ORC1["编排核 on_execute"]
    ORC1 --> CONDA{"条件 a:根 PENDING?"}
    CONDA -- 是 --> PLAN1["planner.plan(graph)→decompose(graph)"]
    PLAN1 --> ADD1["graph.add_task_nodes(第一层,根→PLANNING)"]
    ADD1 --> DISP["dispatcher.dispatch(toDo)"]
    DISP --> SEARCH["search 4态"]
    SEARCH -- HIT --> PATCH1["graph.update_task_node_info(run_mode/assignee,RUNNING)"]
    SEARCH -- MISS --> MISS["编排核 on_miss:<MAX→闸门→plan→add→消费;≥MAX→升BBS:remove_subtree+标BBS"]
    MISS --> DISP
    PATCH1 --> RUN["runner.start_run(批量)"]
    RUN --> X["运行主体 单Bot/协作群/BBS"]
    X -.异步.-> CB["TaskLoopCallback.report_result"]
    CB --> ORC2["编排核 on_report"]
    ORC2 --> PATCH2["graph.update_task_node_info(output_patch=fold;acceptance→翻态)"]
    PATCH2 --> VERDICT{"verdict?"}
    VERDICT -- "PASS" --> DONE1["节点→DONE;查结构父P:兄弟全DONE→decompose(P)[]→P→DONE(非根)/终验(根)"]
    DONE1 --> CONDC{"条件 c:有 PLANNING 父 ∧ 无 RUNNING?"}
    CONDC -- 是 --> PLAN2["planner.plan→add_task_nodes(下一层,父→PLANNING)→dispatch→start_run"]
    VERDICT -- "FAIL+gaps" --> FAIL1["节点→FAILED;深度闸门"]
    FAIL1 --> CONDB{"条件 b:FAILED+gaps 叶子 ∧ depth<MAX?"}
    CONDB -- 是 --> PLAN3["planner.plan→add_task_nodes(补救子挂该节点下,该节点→PLANNING)→dispatch"]
    CONDB -- "MISS∧depth≥MAX" --> BBS["自动升BBS:remove_subtree+loop_round+++标BBS+挂广场"]
    BBS --> BBSBOT["BBS bot认领→自算gap→规划子任务(run_mode=bbs)→执行→上报"]
    BBSBOT -.回投(PASS).-> CB
    BBSBOT -.回投(FAIL+gaps).-> CB
    BBS -.loop_round≥BBS_MAX_DEPTH.-> HUNGS["STUCK→HUNG(stuck)→人介入"]
    DONE1 --> FINAL{"plan(root)==[] ∧ 全非根DONE?"}
    FINAL -- 是 --> VERIFY["编排核触发 owner bot 终验 skill(验 root.goal 全AC,验收模式聚合全图 DONE)"]
    VERIFY -.异步回投.-> CB
    FINAL -- 否 --> WAIT["等下一事件"]
    CB -- "root verdict=PASS" --> ENDDONE["root[DONE] + graph.status=DONE"]
    CB -- "root FAIL+gaps" --> PLAN3
    %% (root FAIL 无 gaps 已消灭:验收 skill 强制要求给 gaps)
    HarnessEvt["Harness周期超时"] -.->|"\"update_task_node_info(复位PENDING)重投"|" PATCH2
```

---

## 5. 触发时机与事件(状态条件触发)

> 驱动模型为**事件驱动 + 状态条件触发**。模块调用由图谱状态变化事件驱动,且 `plan`/`add_task_nodes` 有显式状态触发条件。

### 5.0 事件 → 编排骨回调 → 状态条件

| 事件 | 编排核回调 | 状态条件 | 推进动作 |
|---|---|---|---|
| Owner 提交 | `on_execute` | 条件 a:根 PENDING | plan→add_task_nodes(第一层,根→PLANNING)→dispatch→start_run |
| 回投 PASS | `on_report` | 条件 c:有 PLANNING 父 ∧ 无 RUNNING | plan→add_task_nodes(下一层)→dispatch→start_run |
| 回投 FAIL+gaps | `on_report` | 条件 b:FAILED+gaps 叶子 ∧ depth<MAX | plan→add_task_nodes(补救子挂该节点下,该节点→PLANNING)→dispatch |
%% (FAIL 无 gaps 已消灭:验收 skill 强制要求给 gaps;STUCK 走 on_miss 升 BBS 链路上限判)
| 搜推 MISS | `on_miss` | 深度闸门:depth<MAX | 写miss_events→plan→add_task_nodes(拆细)→消费→dispatch |
| 搜推 MISS | `on_miss` | 深度闸门:depth≥MAX | **自动升 BBS**:remove_subtree(删 xx_node+子树)+loop_round+++标 BBS+挂广场 |
| BBS loop_round≥BBS_MAX_DEPTH | `on_miss` | — | STUCK→update_task_node_info(HUNG, hung_reason=stuck)→人介入 |
| Harness 周期超时 | `on_harness` | — | update_task_node_info(复位 PENDING)重投,不抢正向 |

### 5.1 `TaskPlanner.plan` 触发条件(规划文档原文)

```
状态图谱有更新(新增失败节点/PLANNING 节点)
  AND 没有派发(RUNNING)或执行中节点
  AND 状态图谱有处于 PLANNING 状态的节点
```
规划原则(硬约束):处于派发、执行状态的节点不能修改(包括其前序依赖节点);只针对失败的节点 以及 子节点都已经完成并且自身处于 PLANNING 状态的父节点进行。

### 5.2 `TaskGraphService.add_task_nodes` 触发条件(图谱文档 a/b/c)

- **a. 只有一个根节点且 `status=PENDING`**(初始规划)
- **b. 叶子节点验收未通过**:存在 `FAILED` 节点 且 `acceptance_result.gaps` 非空的叶子节点(补救)
- **c. 父节点验收未通过**:存在 `PLANNING` 节点 ∧ 无 RUNNING(下一层规划可推进;前层产出已落 output)

> 不满足任一条件 → `add_task_nodes` 拒绝/空跑;编排核在调 `add_task_nodes` 前先判条件。

### 5.3 其它模块触发时机(5 文档)

| 模块 | 方法 | 触发时机 |
|---|---|---|
| TaskDispatcher | `dispatch` | 每次规划出新 toDo 之后(溯源 task service) |
| TaskRunner | `start_run` | 图谱上有 TaskNode 完成派发后立即执行 |
| TaskLoopCallback | `report_result` | 任务完成或失败(执行主体 PUSH) |
| TaskHarness | `run_poll_loop` | 周期常驻 |

### 5.4 传播与终结

**状态流转表(6 节点态 + 图态):** 由两张机实现于 `TaskGraphService.update_task_node_info`:
_ACCEPTANCE_TRANSITIONS(唯一终态翻转依据=`TaskNodePatch.acceptance_result`,skill 回投):`RUNNING→{DONE,FAILED}`、`PLANNING→{DONE,FAILED}`(根终验从 PLANNING 委托态回投 PASS/FAIL);
_DIRECT_TRANSITIONS(框架内部 status 直驱:派发/复位/传播):`PENDING→{RUNNING}`、`RUNNING→{PENDING}`(Harness 复位)、`PLANNING→{DONE}`(传播治愈)。
另:`add_task_nodes`/`remove_subtree` 侧向改图结构时把父 `PENDING`/`FAILED→PLANNING`(可委托态 `_DELEGATABLE_PARENT={PENDING,FAILED,PLANNING}`)。

| 当前态 | 合法后继 | 触发/依据 |
|---|---|---|
| PENDING | RUNNING | dispatch 落 run_mode/assignance(`update_task_node_info` 直驱) |
| PENDING | PLANNING | 接受分解委托作结构父(`add_task_nodes`,条件 a/c;`_DELEGATABLE_PARENT`) |
| RUNNING | DONE | 回投 verdict=PASS(`update_task_node_info` acceptance 驱动) |
| RUNNING | FAILED | 回投 verdict=FAIL ∧ gaps≠[](acceptance 驱动) |
| RUNNING | PENDING | Harness 复位(超时/崩溃;直驱) |
| PLANNING | DONE | 结构子(`get_child_tasks`(本))全 PASS ∧ `decompose`(本)==[](gap 已闭)→ 传播治愈(直驱)/ 根终验 PASS(acceptance 驱动) |
| PLANNING | FAILED | **根终验** verdict=FAIL ∧ gaps≠[](acceptance 驱动;非根 PLANNING 不走此路:补救挂该节点下不走终验) |
| FAILED | PLANNING | 条件 b ∧ depth<MAX → 接补救子(`add_task_nodes`;`_DELEGATABLE_PARENT` 含 FAILED) |
| FAILED | DONE | (经 PLANNING 中转)补救子全 PASS ∧ `decompose`(本)==[] → FAILED→PLANNING(add)→PLANNING→DONE(传播治愈) |
| HUNG | (终态,人工) | STUCK:正常+BBS 链路迭代都达上限(`MAX_DEPTH` 已升 BBS ∧ `loop_round`≥`BBS_MAX_DEPTH`)→ 人介入 |
| 图 RUNNING | DONE | 全非根 DONE ∧ 终验 PASS → 经 `update_task_graph_info(TaskGraphPatch{status=DONE, output_patch=…})` 收口(见 §3.1) |
| 图 RUNNING | HUNG | STUCK → 经 `update_task_graph_info(TaskGraphPatch{status=HUNG, extend_props_patch={hung_reason:stuck}})` 收口(见 §3.1) |
| 图 RUNNING | (不退回) | 单向;图无 FAILED,terminal FAIL 由节点 STUCK→HUNG 表达 |

> 图态只有 RUNNING/DONE:建图=RUNNING;终验 PASS 后图 DONE;不设图级 FAILED。
> fold 契约:`output_patch` 只 fold 不翻态;`acceptance_result` 唯一终态翻转 + 唯一下游触发/补救点。

- **传播 DONE**:节点 N PASS→DONE 后,查其结构父 P=`get_parent_task`(N)(从 relations 分解树);若 P=PLANNING 且 P 的全部结构子(`get_child_tasks`(P)=本批兄弟)均 DONE ∧ 无 RUNNING(决策C:等本批兄弟全 DONE 才触发父 plan)→ 委托 `decompose(P)`:返新子→加子派发(下一批,P 仍 PLANNING);返 [](P 的 gap 已闭)→ P 非根→P→DONE 传播治愈(再上行查 P 的父)/P=根→不自动 DONE,走终验。兄弟未齐则等待,不触发 plan。无跨兄弟数据边参与传播判定。
- **terminal PASS(主动验证)**:`plan(root)==[]`(无可再产) ∧ 全非根 DONE ∧ 无 RUNNING → 编排核经 `source_channel`(owner/master bot)触发**终验 skill**(验 root.goal 全 AC,输入=验收模式聚合 root 结构子=全图 DONE 产出,Runner 自判无 scope)→ owner bot 回投 `on_report(patch)`(TaskNodePatch 内含 root 的 verdict/gaps):
  - verdict=PASS → root[DONE] ∧ graph.status=DONE(终态)。
  - verdict=FAIL+gaps → **根不特殊化**:plan(root) 按 gaps 产补救子挂 root 下 → dispatch → 继续驱动(根不进终态)。
- **terminal FAIL(= STUCK → HUNG)**:正常链路 MISS 到 `MAX_DEPTH`→自动升 BBS(`loop_round++`);BBS 链路再迭代到 `loop_round`≥`BBS_MAX_DEPTH`(默认 3)仍执行不下去 → STUCK → HUNG(`hung_reason=stuck`)→ 人介入。**自动升 BBS 无人工确认挡板**(已删);HUNG 是唯一人工入口。
- **HUNG 与 `hung_reason`**:`hung_reason` 收敛到**只剩 `stuck`**(正常+BBS 双链路迭代上限);FAIL 无 gaps 被消灭(验收 skill 强制要求给 gaps);`depth_max` HUNG 已改为自动升 BBS(不再 HUNG)。RUNNING 超时由 Harness 复位回 PENDING 重投,不直接写 HUNG。
- **loop_round++(外层 BBS 上升轮次)**:仅升 BBS 时 `graph.loop_round++`(正常补救不再 ++);达 `BBS_MAX_DEPTH`(默认 3)→ STUCK → HUNG。

---

## 6. API 串联推演(事件驱动;节点名来自 case 策略 stub 产出,非框架写死)

```mermaid
sequenceDiagram
    autonumber
    participant U as 业务方
    participant O as Owner-Bot(SKILL)
    participant TS as TaskService(facade)
    participant ORC as 编排核(内部)
    participant G as TaskGraphService
    participant P as TaskPlanner(编排壳)
    participant Dp as PlanningStrategy stub(case strategy)
    participant D as TaskDispatcher
    participant R as TaskRunner
    participant X as 运行主体(单Bot/协作群/BBS)
    participant CB as TaskLoopCallback
    participant H as TaskHarness(旁路)
    U->>O: 一句话需求
    O->>TS: execute(TaskInfo)
    TS->>G: initialize_graph(根 PENDING, run_id 分配)
    TS->>ORC: on_execute(task_id)
    ORC->>P: plan(graph)(条件 a:根 PENDING)
    P->>Dp: decompose(graph)
    Dp-->>P: list[TaskNode]
    P-->>ORC: 去重+硬契约后 list[TaskNode]
    ORC->>G: add_task_nodes(第一层;relations登记 n_root→子,根→PLANNING)
    ORC->>D: dispatch(toDo)
    D->>D: DispatchStrategy.apply → 4态(填 node.run_info,不写图不起 run)
    alt HIT_SINGLE
        D-->>ORC: list[TaskNode](run_mode="single_bot",assignee 已填)
        ORC->>G: update_task_node_info(run_mode/assignee,RUNNING)
    else HIT_MULTI_BOTS(动态拉群)
        D->>R: form_coop_group(GroupFormation{collab_mode})
        R-->>D: group_id
        D-->>ORC: list[TaskNode](run_mode="coop_group",assignee=gid 已填)
        ORC->>G: update_task_node_info(run_mode/assignee,RUNNING)
    else MISS
        D-->>ORC: list[TaskNode](assignee 仍 None,标 miss_events)
        ORC->>ORC: on_miss:<MAX→miss_events→plan→add→消费;≥MAX→升BBS:remove+loop_round+++标BBS
    end
    ORC->>R: start_run(toDoTaskList)
    R-->>ORC: list[Boolean]
    R->>X: 按 run_mode 投递
    X-->>CB: (异步) report_result(TaskCallbackData{loop_task_id, workflow_type, instance_id, result})
    CB->>ORC: on_report(patch)(适配层把 data 组装成 TaskNodePatch)
    ORC->>G: update_task_node_info(output_patch=fold;acceptance→翻态)
    alt PASS
        G-->>G: 节点→DONE;查结构父P,兄弟全DONE→decompose(P):[]→P→DONE(非根)/终验(根)
        opt 条件 c:有 PLANNING 父 ∧ 无 RUNNING
            ORC->>P: plan(graph)→decompose(graph)
            ORC->>G: add_task_nodes(下一批;relations登记 父→子,父仍PLANNING)
            ORC->>D: dispatch→list[TaskNode]填执行者→(ORC)update(RUNNING)+start_run
        end
    else FAIL+gaps
        G-->>G: 节点→FAILED
        ORC->>ORC: 深度闸门 depth<MAX?
        ORC->>P: plan(graph)→decompose(graph)
        ORC->>G: add_task_nodes(relations登记 该节点→补救子;failed_node→PLANNING)
        ORC->>D: dispatch→list[TaskNode]填执行者→(ORC)update(RUNNING)+start_run
    end
    opt 产品/系统探活
        TS->>R: query_status(task_id) / query_detail(node) / query_result(node)
    end
    H-->>G: (旁路) SLA超时→update_task_node_info(HUNG/FAILED)
    ORC->>ORC: plan(root)==[] ∧ 全非根DONE → 触发 owner bot 终验 skill(经 source_channel)
    owner-->>CB: report_result(root_task_id, verdict=PASS, 全AC)
    CB->>ORC: on_report(patch{root,PASS})
    ORC->>G: update_task_node_info(root DONE) + 图 status=DONE
    U->>TS: get_task_dashboard(task_id)
    TS->>G: query_task_dashboard
    G-->>U: TaskExecutionGraph{status=DONE,loop_round,看板}
```

> **分层要点**:facade 2 API(execute/get_task_dashboard);编排核 on_* 事件驱动 + 状态条件(a/b/c + plan 三条件)分段推进;Dispatcher 只搜推把 run_mode/assignee 填到 TaskNode 上返回 list[TaskNode](不写图不起 run);编排核落 update_task_node_info + 置 RUNNING + 立即 start_run;执行结果 PUSH `TaskLoopCallback.report_result` 为主,可选 PULL `query_status/detail/result`;`TaskCallbackData.loop_task_id↔(task_id,node_id)`、`result.success→verdict`、`result.data→output` 由适配层完成,再走 `on_report`→`update_task_node_info`。

## 7. Case 端到端推演:存储行业尽调(权威剧本 `gwqie46v7hzr1w6h`;从任务输入到任务执行完成按 API 流程串联)

> 剧本输入(原文):任务=「存储行业尽调:AI 基础设施驱动下企业级与数据中心存储的最新变化、竞争格局与进入机会」;目标=产出一份尽调报告;验收标准(AI 设定,由 case 策略 stub 据此拆解):
> ① 明确是否具备中短期投资价值;② 最值得跟踪的细分赛道/公司类型/核心变量;③ 市场规模/竞争格局/技术演进/客户需求四维度系统分析;④ ≥5 条核心投资判断,每条含支持证据+风险因素+待验证问题;⑤ ≥30% 判断来自最近 3 个月信息更新。
> 剧本三阶段(执行主体天然覆盖三模态):
> - **阶段一·快速建立行业全貌** → 单 Bot「行业信息抓取 Bot」(single_bot)
> - **阶段二·深度专题研究** → 4 专题各落协作群/单 bot:专题A 市场(2 子bot 协作群)、专题B 技术(3 子bot 协作群)、专题C 供应链(单 bot)、专题D 客户(3 子bot 协作群)
> - **阶段三·一手实践经验** → BBS 悬赏 ×2(认领执行由 bot 自主)

**图结构**(由 singlebox 注入的 case 策略 stub 按三阶段 AC 拆解产出;节点名是 stub 产出非框架写死;仅列主干,子 bot 为协作群内部不进图):

| node | 角色 | 结构父(relations src→dst) | 规划批次 | 执行主体(run_mode) |
|---|---|---|---|---|
| `n_root` | 尽调任务根 | None(根) | — | Planner(decompose) |
| `N_overview` | 阶段一·行业全貌 | n_root→N_overview | 批1(条件a) | single_bot「行业信息抓取 Bot」 |
| `N_market` | 专题A·市场规模与周期 | n_root→N_market | 批2(条件c,等批1全DONE) | coop_group「市场研究群」(2 子bot,manager_worker) |
| `N_tech` | 专题B·技术路线研究 | n_root→N_tech | 批2 | coop_group「技术研究群」(3 子bot,manager_worker) |
| `N_compete` | 专题C·竞争格局 | n_root→N_compete | 批2 | single_bot「供应链专家」 |
| `N_customer` | 专题D·客户与场景 | n_root→N_customer | 批2 | coop_group「客户分析群」(3 子bot,manager_worker) |
| `N_practice_bbs` | 阶段三·一手实践(BBS) | n_root→N_practice_bbs | 批3(条件c,等批2全DONE) | bbs(悬赏 ×2) |
| `N_report` | 汇总成尽调报告(全 AC 验收) | n_root→N_report | 批4(条件c,等批3全DONE) | single_bot「报告聚合 Bot」 |

> 注:relations 为分解树(单入),所有非根节点结构父均为 n_root(产自 decompose(n_root));无跨兄弟直接数据依赖边——数据流由步进式批规划顺序(批 i 全 DONE 才产批 i+1)+ 执行时结构父聚合上下文(批 i+1 节点执行时聚合已 DONE 的批 i 兄弟产出)承载。协作群内部子 bot 不进框架图(群自闭环持 `SubDagRef(bcs_run_id)`)。

**逐 step 端到端 API 流程串联(记法 `node[状态]`):**

1. **任务输入→建图**:`Owner-Bot SKILL` 调 `TaskService.execute(TaskInfo{spec, source_channel=bot})`→`graph.initialize_graph`(run_id 分配,根 n_root[PENDING])→编排核 `on_execute`。
2. **初始规划(委托)**:`on_execute`→条件 a(根 PENDING)成立→`planner.plan(graph)`→委托 `planner.plan(graph)`:策略读图自发现目标=根 PENDING→产 `[N_overview]`(task_id 已填)。`graph.add_task_nodes([N_overview], parent_node_id="n_root")`:relations 登记 `n_root→N_overview`(分解树单入);n_root→PLANNING(委托态)。N_overview 被 add 即就绪(PENDING,无 dependencies_satisfied 闸门)。
3. **派发(N_overview)**:`dispatch([N_overview])`→search→`HIT_SINGLE(行业信息抓取Bot)`→`update_task_node_info(run_mode="single_bot", assignee, RUNNING)`,N_overview[RUNNING]。
4. **开始执行**:`start_run([N_overview])`→投递单 bot workflow。派发与执行分层:dispatcher 决定谁做,start_run 真正投递。
5. **回投(PUSH)**:单 bot 完成→`TaskLoopCallback.report_result(TaskCallbackData{loop_task_id=N_overview 映射, workflow_type=single_bot, instance_id, result{success=true, data=行业全貌}})`→适配层 `on_report`→`update_task_node_info(output=行业全貌, acceptance=PASS)`→N_overview[DONE]。
6. **下一层规划(委托)**:`on_report`(N_overview PASS)→N_overview→DONE。查结构父 n_root=`get_parent_task`(N_overview)=PLANNING;n_root 的结构子(`get_child_tasks`(n_root))=仅 N_overview 已全 DONE ∧ 无 RUNNING(决策C:本批兄弟齐)→条件 c 成立→`plan(graph)`→委托 `decompose(graph)`:seam 发现 n_root 仍 PLANNING(据阶段二 AC)产 `[四专题]`→`add_task_nodes([四专题], parent_node_id="n_root")`:relations 登记 4 条 `n_root→各专题`(分解树单入)。decompose 返非空→n_root 维持 PLANNING(子含未 DONE),不传播。
7. **四专题并行派发执行**:四专题被 add 即就绪(PENDING),无数据依赖闸门(数据流由执行时结构父聚合上下文承载)→`dispatch([四专题])`:
   - N_market/N_tech/N_customer search→`HIT_MULTI_BOTS`→`form_coop_group(GroupFormation{collab_mode=MANAGER_WORKER, member_bots})`(BCS 建群)→`update_task_node_info(run_mode="coop_group", assignee=gid, RUNNING)`。
   - N_compete search→`HIT_SINGLE(供应链专家Bot)`→`update_task_node_info(run_mode="single_bot", RUNNING)`。
   →`start_run([四专题])` 批量投递。
8. **协作群终态回投(PUSH)**:三个协作群各自 `TaskCallbackData(workflow_type=bcn_coop_group)`→`report_result`→`on_report`→`update_task_node_info(PASS)`→DONE。N_compete 单 bot 同理。
9. **BBS 阶段**:四专题全 PASS→DONE。查结构父 n_root=`get_parent_task`(任一专题)=PLANNING;n_root 的结构子(N_overview+四专题)全 DONE ∧ 无 RUNNING(本批齐)→条件 c→`plan`→委托 `decompose(graph)`:seam 据阶段三 AC 产 `[N_practice_bbs]`→`add_task_nodes(…, parent_node_id="n_root")`:relations 登记 `n_root→N_practice_bbs`→`dispatch`→假设搜推 MISS(本 case 演示升 BBS 路径)→`on_miss`:MISS+多轮补救仍 MISS→depth 达 `MAX_DEPTH`→**自动升 BBS**:`remove_subtree`(若该节点下有未执行的 MISS 子树)+`loop_round++`+标 BBS+挂任务广场。n_root 仍根不传播。
10. **BBS bot 认领执行**:BBS bot 主动认领任务→加载完整上下文(已完成 output+验收 **vs** task_spec/goal/context)→**自己算 gap+据自能力规划子任务**→`add_task_nodes(…, parent_node_id="n_root")`(落图 `run_mode="bbs"`,`assignee=bot_id`,挂 `n_root` 下)→自执行→不管验收通过与否都上报:`report_result(TaskCallbackData{workflow_type=bbs, result{success,data=一手实践}})`→`on_report`→`update_task_node_info`(落 output+acceptance→翻态)。
11. **报告聚合(普通子节点)**:BBS bot 上报 PASS→该子任务 DONE。查结构父 n_root=PLANNING;n_root 的结构子全 DONE ∧ 无 RUNNING→条件 c→`plan`→委托 `decompose(graph)`:seam 产 `[N_report]`(挂 n_root 下;`add_task_nodes(…, parent_node_id="n_root")` relations 登记 `n_root→N_report`)(**普通叶节点,不是"终验节点";框架不识别特殊节点**)→`dispatch→HIT_SINGLE(报告聚合Bot)→start_run`。N_report 执行模式聚合结构父 n_root 的聚合上下文={n_root.spec/goal + 已 DONE 兄弟产出(全图 DONE output)}→报告 Bot 据此生成报告→回投 PASS→`update_task_node_info(N_report DONE)`。N_report 自己的 acceptance 是"产出报告",验收由报告 Bot skill 回投,与全 AC 终验无关。
12. **根终验(主动验证)**:`plan(graph)==[]` ∧ 全非根 DONE ∧ 无 RUNNING →委托 `decompose(graph)` 判全 AC 已被现有子产出结构 cover,无可再产,返回 [] → 编排核经 `source_channel_type=bot` 回调 owner bot **终验 skill**(输入=验收模式聚合 root 结构子=全图 DONE 产出,Runner 自判,验 root.goal 5 条全 AC)→ owner bot 回投 `on_report(patch{root,PASS})`→`update_task_node_info(root DONE)`+graph.status=DONE。若终验 FAIL+gaps → plan(root) 按 gaps 补救子(根不特殊化),继续驱动。

**未触分支(可 singlebox 注入验证)**:任一专题 FAIL+gaps→补救子挂该专题节点下(该节点→PLANNING);协作群 MISS(无群 cover)+form_coop_group 不适用→`on_miss` 按深度裁决;MISS+多轮补救仍 MISS→depth 达 MAX_DEPTH→自动升 BBS(remove_subtree+loop_round+++标 BBS+挂广场);BBS loop_round≥BBS_MAX_DEPTH→STUCK→HUNG(人介入);Harness 周期超时 `update_task_node_info(复位 PENDING)重投`。

> 本 case 同时验证三模态自适应:`start_run` 一个入口分发 single_bot/coop_group/bbs,PUSH `TaskLoopCallback.report_result` 统一回收,适配层把 `TaskCallbackData` 翻译成 `on_report`→`update_task_node_info`,事件驱动推进。

---

## 8. 并发与幂等、fold 契约

- **同图串行**:同 task_id 可重入锁(编排核 on_* 串行);跨 task 并行。防止回投并发撕裂图。
- **plan 幂等空跑**:纯读图无副作用;不满足触发条件返回 `[]`;去重靠"图上已存则不产"。`decompose` 应是纯函数。
- **MISS 内联消化**:`on_miss` 写 miss_events→plan→add→消费 同回调内完成,不跨事件持久化 miss_events。
- **fold 契约**:`output_patch` 只 fold 不翻态不触发;`acceptance_result` 唯一终态翻转 + 唯一下游触发/补救点(PASS→DONE 传播,FAIL+gaps→FAILED 补救)。
- **add 单层同构**:本批新节点结构父仅指向已存在节点,本批内不互为父子(单入分解树防环);本批兄弟无直接数据依赖边,数据流由批规划顺序+结构父聚合上下文承载。

---
