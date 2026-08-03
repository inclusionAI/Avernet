"""端到端 tick 驱动(真实 ``TaskScheduler.tick`` 走动作节点全链路)。

测试原则:任务与分解的**内容**用真实需求 case,只在**外部边界**(搜推 API、
分解器 LLM、执行 skill 验收回投)上 mock,以切换不同执行分支。

真实 case:交付一个用户登录功能。
- 验收:① 账号密码登录成功 ② 失败有明确错误提示 ③ 登录接口回归测试通过
- 顶层分解(真实子任务,并行):
  - 设计登录 API 契约(POST /login 入参/出参/错误码)         → 搜推命中 架构 bot
  - 实现后端登录校验逻辑(密码哈希比对 + 失败计数锁定)       → 搜推命中 后端协作组
  - 编写登录接口回归测试                                     → 搜推未匹配 → 再分解一层
- “编写登录接口回归测试”再分解(真实子任务):
  - 搭建测试环境与 mock 用户数据 / 编写登录成功与失败用例脚本 / 接入 CI 跑回归

分支覆盖(靠端口 mock 切换,不动任务内容):
- happy 单 bot(小任务直接命中,不分解)
- 搜推未匹配 → 分解 → 子各命中 → exec-aggregate → 终验
- 递归上限 → mark-hang(不直升 BBS)
- hang → 人确认升 BBS → 同图延续
- NODE_FAILED tick 驱动同执行方重派 / 到上限派 reroute 判定给 skill(非 C5 规则)
- reroute 命中重派 / 未匹配再拆 / 不可恢复 → 挂起 → 人不升 → task FAILED
- 执行过程中 State 演进:skill 经 update_state 写产出/gap,reroute 经 retrieve_state 读(FR-GRAPH-03)
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceCriteriaKind,
    GraphStatus,
    NodeStatus,
    NodeType,
    RouteClass,
    RunMode,
    SubTaskSpec,
    TaskGoal,
    GraphStatus,
    TaskState,
)
from agentclaw.community.core.task.domain.events import EventKind
from agentclaw.community.core.task.protocols import (
    BotCandidate,
    DispatchResult,
    RouteRecommendation,
)
from agentclaw.community.core.task.services import TaskService, BbsExecutorService
from agentclaw.community.core.task.services.task_scheduler import TaskScheduler
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)

from tests.community.core.task.services._login_case import (
    ACCEPTANCES,
    HASH_CHILDREN,
    HIT_BOTS,
    OBJECTIVE,
    TEST_CHILDREN,
    TOP_CHILDREN,
)


# --- 真实需求 case:交付用户登录功能(见 _login_case)-------------------------


# --- 端口 mock(只 mock 外部边界) -------------------------------------------

class FakeDiscover:
    """搜推 API mock:按 node_id 返回真实候选执行方;未配置 → 未匹配。"""

    def __init__(self, hits: dict[str, list[str]] | None = None, extra_hits: dict[str, list[str]] | None = None):
        self._hits = {**HIT_BOTS, **(hits or {}), **(extra_hits or {})}
        self.calls: list[str] = []

    def recommend(self, task_id: str, node_id: str) -> RouteRecommendation:
        self.calls.append(node_id)
        bots = self._hits.get(node_id, [])
        cands = [BotCandidate(bot_id=b, fit_score=0.9) for b in bots]
        run_mode = RunMode.COOP_GROUP if any("group" in b for b in bots) else RunMode.SINGLE_BOT
        return RouteRecommendation(
            route_class=RouteClass.C1 if cands else RouteClass.C5,
            run_mode=run_mode,
            candidates=cands,
            confidence=0.9 if cands else 0.0,
        )


class FakeDecomposer:
    """分解器 LLM mock:按**真实需求文本**返回真实子任务(内容真实,非占位)。

    ``depth_override`` 仅用于切换“递归上限”分支(控制返回深度,属 LLM 结果属性),
    子任务 spec 内容仍为真实需求。"""

    def __init__(self, depth_override: int | None = None):
        self._depth_override = depth_override
        self.sub_calls: list[tuple[str, int]] = []

    def decompose_subtasks(self, spec: str, state: TaskState) -> list[SubTaskSpec]:
        parent_depth = int(state.public.get("__decompose_parent_depth__", -1))
        child_depth = (
            self._depth_override
            if self._depth_override is not None
            else (parent_depth + 1 if parent_depth >= 0 else 0)
        )
        self.sub_calls.append((spec, child_depth))
        spec_text = spec or ""
        if "交付用户登录功能" in spec_text:
            base = TOP_CHILDREN
        elif "编写登录接口回归测试" in spec_text:
            base = TEST_CHILDREN
        elif "密码哈希比对" in spec_text:
            base = HASH_CHILDREN  # reroute(decompose)“实现密码哈希比对”→ 真实子任务
        else:
            base = TEST_CHILDREN  # 兜底:仍给真实子任务内容
        return [
            SubTaskSpec(node_id=s.node_id, spec=s.spec, run_mode=s.run_mode, depth=child_depth)
            for s in base
        ]


class FakeDriver:
    def __init__(self):
        self.dispatched: list[str] = []
        self.redispatched: list[tuple[str, RouteClass]] = []
        self.escalated: list[str] = []

    def dispatch_node(self, task_id, node_id):
        self.dispatched.append(node_id)
        return DispatchResult(node_id=node_id, executor_id=f"bot-{node_id}", run_mode=RunMode.SINGLE_BOT)

    def redispatch(self, task_id, node_id, route_class):
        self.redispatched.append((node_id, route_class))
        return DispatchResult(node_id=node_id, executor_id="bot-r", run_mode=RunMode.SINGLE_BOT)

    def escalate_to_bbs(self, task_id, reason=""):
        self.escalated.append(reason)
        return DispatchResult(node_id="", executor_id="", run_mode=RunMode.BBS)


class FakeExecution:
    def __init__(self):
        self.single_bots: list[tuple[str, str, str]] = []
        self.coop_groups: list[tuple[str, str, list[str]]] = []
        self.redispatches: list[tuple[str, str, str]] = []
        self.probes: list[tuple[str, str, str]] = []
        self.bbss: list[tuple[str, str, str]] = []

    def dispatch_single_bot(self, task_id, node_id, bot_id):
        self.single_bots.append((task_id, node_id, bot_id))
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def coop_group(self, task_id, node_id, bot_ids):
        self.coop_groups.append((task_id, node_id, list(bot_ids)))
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.COOP_GROUP)

    def redispatch_node(self, task_id, node_id, bot_id):
        self.redispatches.append((task_id, node_id, bot_id))
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def probe(self, task_id, node_id, bot_id):
        self.probes.append((task_id, node_id, bot_id))
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def bbs(self, task_id, node_id, reason=""):
        self.bbss.append((task_id, node_id, reason))
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.BBS)


def _svc() -> TaskService:
    return TaskService(InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher())


def _goal():
    return TaskGoal(objective=OBJECTIVE, acceptances=list(ACCEPTANCES))


def _task(svc: TaskService, sched: TaskScheduler, objective: str = OBJECTIVE, acceptances: int = 3):
    """真实生命周期:create → finalize_plan(空 plan → 根 BOT_SEARCH,spec=真实目标)
    → ``scheduler.start``(DEFINED→EXECUTING 状态机 + init_execution_graph + ON_PLAZA +
    首 tick)。**不手戳 status/graph**,全程走真实转移动作。返回 EXECUTING / ON_PLAZA task。"""
    t = svc.create(title=objective, background="obj")
    task = svc.get(t.id)
    task.spec.goal = TaskGoal(
        objective=objective,
        acceptances=[
            AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": f"ac{i}"})
            for i in range(acceptances)
        ],
    )
    if objective == OBJECTIVE:
        task.spec.goal.acceptances = list(ACCEPTANCES)
    svc._task_repo.save(task)  # noqa: SLF001
    task = svc.get(task.id)
    # DRAFTING → DEFINED(clarify confirmed;init_execution_graph 走根 BOT_SEARCH 搜推先行)
    svc.clarify(task.id, {}, confirmed=True)
    task = svc.get(task.id)
    assert task.status is GraphStatus.DEFINED
    sched.start(task.id)  # DEFINED→EXECUTING + init_execution_graph + ON_PLAZA + 首 tick(真实)
    return svc.get(task.id)


def _planned_task(svc: TaskService, sched: TaskScheduler, node_id: str, spec: str):
    """带 1 个已派发起跑的叶子 DISPATCH 的 task(失败/重试/重路由 e2e 用例的起点)。

    2026-08-03:Plan 退场后不再有 plan.sub_tasks 预拆自定义 id 叶子,故改由真实转移
    (DEFINED→EXECUTING + init_execution_graph + ON_PLAZA)后显式 seed:root BOT_SEARCH
    标 DONE(免 tick 触发搜推/分解),挂 ``node_id`` DISPATCH 叶子并真实 claim+fire 起跑。
    其余失败/重试/重路由链路全程走真实 tick + on_event,不戳状态。"""
    t = svc.create(title=spec, background="obj")
    task = svc.get(t.id)
    task.spec.goal = _goal()
    svc._task_repo.save(task)  # noqa: SLF001
    task = svc.get(task.id)
    svc.clarify(task.id, {}, confirmed=True)
    task = svc.get(task.id)
    assert task.status is GraphStatus.DEFINED
    # DEFINED→EXECUTING + init_execution_graph + ON_PLAZA(真实转移;不跑 start 首 tick
    # 以避免 root BOT_SEARCH 触发分解分支,干扰失败链路断言)
    task.status = GraphStatus.RUNNING
    task.execution_graph.status = GraphStatus.RUNNING
    svc._task_repo.save(task)  # noqa: SLF001
    svc.init_execution_graph(task)
    task = svc.get(task.id)
    svc.mark_graph_status(task, GraphStatus.RUNNING)
    # root BOT_SEARCH 标 DONE,免下 tick 触发搜推/分解;挂叶子并真实 claim+fire 起跑
    root = svc._find_node(task, "n_bot_search")  # noqa: SLF001
    if root is not None:
        root.status = NodeStatus.DONE
        st_root = task.execution_graph.state.subtasks.get("n_bot_search")
        if st_root is not None:
            st_root.status = NodeStatus.DONE
        svc._task_repo.save(task)  # noqa: SLF001
    svc.add_node(
        task.id,
        SubTaskSpec(node_id=node_id, spec=spec, run_mode=RunMode.SINGLE_BOT),
        "n_bot_search",
        NodeType.DISPATCH,
    )
    rec = sched._discover.recommend(task.id, node_id)  # noqa: SLF001
    lead = rec.candidates[0].bot_id if rec.candidates else "bot-x"
    svc.claim_node(task.id, node_id, lead)
    sched._execution.dispatch_single_bot(task.id, node_id, lead)  # noqa: SLF001
    fresh = svc.get(task.id)
    st = fresh.execution_graph.state.subtasks.get(node_id)
    if st is not None and st.status is not NodeStatus.RUNNING:
        st.status = NodeStatus.RUNNING
        svc._task_repo.save(fresh)  # noqa: SLF001
    return svc.get(task.id)


def _accept_leaf(svc: TaskService, task_id: str, node_id: str, verifier: str = "skill"):
    """mock skill 回投:经真实 ``on_event(NODE_ACCEPTED)`` → ``_apply_event`` fold 回写
    Node.status=DONE + SubtaskState.status=DONE(走真实状态回写通道 + 状态机 guard +
    事件日志,非直接戳状态)。节点须先 RUNNING(已 claim/派发)。"""
    svc.on_event({
        "task_id": task_id,
        "kind": EventKind.NODE_ACCEPTED.value,
        "payload": {"node_id": node_id, "verifier": verifier},
    })


def _report_event(svc: TaskService, sched: TaskScheduler, task_id: str, kind: EventKind, payload: dict):
    """模拟生产 ``POST /events``:先 ``TaskService.on_event``(落态 fold)再
    ``Scheduler.on_event``(编排反应:NODE_FAILED → 泵 tick 驱动 _retry_failed)。
    全程真实通道,不戳状态。"""
    envelope = {"task_id": task_id, "kind": kind.value, "payload": payload}
    task = svc.on_event(envelope)
    if task is not None:
        return sched.on_event(envelope)
    return task


def _scheduler(svc, discover, decomposer=None, driver=None, execution=None):
    return TaskScheduler(
        svc,
        discover,
        driver or FakeDriver(),
        decomposer or FakeDecomposer(),
        execution or FakeExecution(),
    )


def _tick_until(svc, sched, task_id, predicate, max_ticks=30):
    """循环 tick 直到 predicate(task) 为真或达到上限(返回最后 task)。"""
    task = svc.get(task_id)
    for _ in range(max_ticks):
        if predicate(task):
            return task
        sched.tick(task_id)
        task = svc.get(task_id)
    return task


# --- ① happy:小任务单 bot 直接命中(不分解)---------------------------------

def test_tick_single_bot_happy_path():
    svc = _svc()
    # 真实小需求:给 README 补安装说明 → 搜推命中 doc-bot,单 bot 直接做
    discover = FakeDiscover(hits={"n_bot_search": ["doc-bot"]})
    sched = _scheduler(svc, discover)
    task = _task(svc, sched, objective="给README补一节安装说明", acceptances=1)
    # start 首 tick:规划链 DONE;root 搜推命中 doc-bot → 落 _disp 子(assignee=doc-bot,PENDING)
    assert next(n for n in task.execution_graph.nodes if n.node_id == "n_bot_search").spec == "给README补一节安装说明"
    disp = next(n for n in task.execution_graph.nodes if n.node_type is NodeType.DISPATCH)
    assert disp.assignee == "doc-bot"
    # tick:dispatch → claim + fire → RUNNING
    sched.tick(task.id)
    # skill 回投验收 PASS
    _accept_leaf(svc, task.id, disp.node_id)
    # 无未闭合 → goal-verify PASS → VERIFIED + DONE
    task = _tick_until(svc, sched, task.id, lambda t: t.status is GraphStatus.DONE)
    assert task.execution_graph.status is GraphStatus.DONE
    assert task.status is GraphStatus.DONE


# --- ② 搜推未匹配 → 分解 → 子各命中 → exec-aggregate → 终验(主链路)-------

def test_tick_decompose_multi_subtask_aggregate_verify():
    """主链路:搜推未匹配 → 分解 → 子各命中 → exec-aggregate → 终验。

    本用例显式断言两件事(回答“怎么驱动 / 节点间怎么传信息”):
    ① 驱动:tick 按 NodeType 派活,_advance_node 一步步推 PENDING→DONE/RUNNING;
       执行器不直接写图,而是经 on_event 回投 → _apply_event fold 回写两维(Node + SubtaskState)。
    ② 信息传递:objective → 根 BOT_SEARCH.spec → DECOMPOSITION 继承 spec → decomposer 入参 →
       子任务 spec → 子 BOT_SEARCH.spec → discover 命中 → DISPATCH.assignee → 执行 →
       叶子 DONE 沿图向上聚合 → EXEC_AGGREGATE → GOAL_VERIFY。"""
    svc = _svc()
    discover = FakeDiscover()  # n_bot_search / s_write_tests 未匹配;其余命中
    decomposer = FakeDecomposer()
    sched = _scheduler(svc, discover, decomposer)
    task = _task(svc, sched)  # 交付用户登录功能,3 验收;start 首 tick:root 搜推未匹配 → 落 DECOMPOSITION 子 + root DONE

    # --- 驱动①:tick 推进动作拓扑。start 首 tick 已把 root 从 PENDING 推到 DONE(非静态图)。
    root = next(n for n in task.execution_graph.nodes if n.node_id == "n_bot_search")
    assert root.spec == OBJECTIVE  # 根 BOT_SEARCH 承载真实目标(init_execution_graph 写入)
    assert root.status is NodeStatus.DONE  # start 的首 tick 真的驱动了动作完成

    # --- 信息传递②a:DECOMPOSITION 子节点**继承父 BOT_SEARCH 的 spec**(真实需求文本)。
    # 这条 spec 才是 decomposer “要分解什么”的入参,而非字面 "decomposition"。
    dec = next(n for n in task.execution_graph.nodes if n.node_type is NodeType.DECOMPOSITION)
    assert dec.spec == OBJECTIVE == root.spec  # 父→子:真实需求原样透传

    # 推进到底(搜推/分解/派发多轮)
    for _ in range(12):
        sched.tick(task.id)
    task = svc.get(task.id)

    # --- 信息传递②b:decompose_subtasks 收到的入参 = 真实任务文本(非占位)。---
    # 两次分解:① objective→顶层 3 子任务;② “编写登录接口回归测试”→再分解 3 子任务。
    called_specs = [s for s, _d in decomposer.sub_calls]
    assert OBJECTIVE in called_specs
    assert any("编写登录接口回归测试" in s for s in called_specs)
    # 分解结果(subtask.spec)成为下一级 BOT_SEARCH 节点的 spec(分解输出→搜推入参)。
    specs = {n.node_id: n.spec for n in task.execution_graph.nodes if n.node_type is NodeType.BOT_SEARCH}
    assert "s_design_api" in specs and "设计登录API契约" in specs["s_design_api"]
    assert "s_write_tests" in specs and "编写登录接口回归测试" in specs["s_write_tests"]
    assert any(nid.startswith("s3_") for nid in specs)  # s_write_tests 被再分解

    # --- 信息传递②c:搜推结果(discover.recommend.candidates)→ DISPATCH.assignee。---
    dispatches = sched._execution.single_bots  # noqa: SLF001
    coop = sched._execution.coop_groups  # noqa: SLF001
    assert any(b == "arch-bot" for _, _, b in dispatches)
    assert any("backend-group" in bs for _, _, bs in coop)

    # --- 驱动①b:派发端口被调后,叶子 PENDING→RUNNING + assignee = 命中执行方。---
    leaves = [n for n in task.execution_graph.nodes if n.node_type is NodeType.DISPATCH]
    assert leaves
    running = [d for d in leaves if d.status is NodeStatus.RUNNING]
    assert running, "派发后应有叶子被 claim 起跑"
    assert all(d.assignee for d in running)  # claim_node 已写 assignee
    # 派发时实体维 SubtaskState 与 Node 同步置 RUNNING(_dispatch 内同步)。
    for d in running:
        st = task.execution_graph.state.subtasks.get(d.node_id)
        assert st is not None and st.status is NodeStatus.RUNNING

    # --- 驱动①c + 信息传递②d:执行器经真实 on_event(NODE_ACCEPTED)回投验收 PASS,
    # _apply_event fold 同时回写动作维(Node=DONE)与实体维(SubtaskState=DONE)。---
    # 执行结果信息靠事件流回图,gap 不在执行器里戳状态。
    for d in leaves:
        _accept_leaf(svc, task.id, d.node_id)
    task = svc.get(task.id)
    for d in leaves:
        nd = next(n for n in task.execution_graph.nodes if n.node_id == d.node_id)
        assert nd.status is NodeStatus.DONE  # 动作维 fold
        st = task.execution_graph.state.subtasks.get(d.node_id)
        assert st is not None and st.status is NodeStatus.DONE  # 实体维 fold

    # --- 信息传递②e:叶子 DONE 沿图向上聚合 → 父 DECOMPOSITION 触发 EXEC_AGGREGATE fold;
    # 终验读 root 验收全 PASS → graph VERIFIED + Task DONE。执行结果信息逐层汇报到整图。---
    task = _tick_until(svc, sched, task.id, lambda t: t.status is GraphStatus.DONE)
    assert any(n.node_type is NodeType.EXEC_AGGREGATE for n in task.execution_graph.nodes)
    assert task.execution_graph.status is GraphStatus.DONE
    assert task.status is GraphStatus.DONE


# --- ③ 递归上限 → mark-hang(不直升 BBS)-----------------------------------

def test_tick_recursion_limit_mark_hang():
    from agentclaw.community.core.task.services.scheduler_ops import MAX_RECURSION_DEPTH

    svc = _svc()
    discover = FakeDiscover()  # root 未匹配
    # depth_override=MAX:分解器返回的 children 深度≥MAX → _decomposition 卡住(节点 HUNG)
    sched = _scheduler(svc, discover, FakeDecomposer(depth_override=MAX_RECURSION_DEPTH))
    task = _task(svc, sched)  # start 首 tick:root 未匹配 → 落 DECOMPOSITION 子
    sched.tick(task.id)  # decomposition → children depth≥MAX → 节点 HUNG + 图 HUMAN_REQUIRED
    task = svc.get(task.id)
    assert any(n.status is NodeStatus.HUNG for n in task.execution_graph.nodes)
    assert task.execution_graph.status is GraphStatus.HUMAN_REQUIRED  # 不直升 BBS


# --- ④ hang → 人确认升 BBS → 同图延续(三终止②)----------------------------

def test_tick_hang_confirm_bbs_continue_same_graph():
    from agentclaw.community.core.task.domain.events import next_seq
    from agentclaw.community.core.task.services.scheduler_ops import MAX_RECURSION_DEPTH

    svc = _svc()
    discover = FakeDiscover()
    sched = _scheduler(svc, discover, FakeDecomposer(depth_override=MAX_RECURSION_DEPTH))
    task = _task(svc, sched)  # start 首 tick:root 未匹配 → 落 DECOMPOSITION 子
    sched.tick(task.id)  # decomposition → children depth≥MAX → 节点 HUNG + 图 HUMAN_REQUIRED
    task = svc.get(task.id)
    assert task.execution_graph.status is GraphStatus.HUMAN_REQUIRED
    hang_node = next(n for n in task.execution_graph.nodes if n.status is NodeStatus.HUNG)
    # 人确认升 BBS(POST /events 回投 BBS_CONFIRMED)→ BBS_ACTIVE(任务级模式,不落节点)
    svc.on_event({
        "task_id": task.id, "kind": EventKind.BBS_CONFIRMED,
        "seq": next_seq(svc._event_repo.latest_seq(task.id)),  # noqa: SLF001
        "payload": {"node_id": hang_node.node_id},
    })
    task = svc.get(task.id)
    assert task.execution_graph.status is GraphStatus.BBS_ACTIVE
    # BBS bot 在同一 TaskExecutionGraph 上认领待办:加一个 BBS 阶段待办节点供认领
    svc.add_node(
        task.id, SubTaskSpec(node_id="n_bbs_review", spec="BBS复核登录安全策略"),
        None, NodeType.DISPATCH,
    )
    bbs = BbsExecutorService(svc)
    claimed = bbs.claim(task.id, "bbs-bot-1")
    assert claimed is not None
    assert claimed.run_mode is RunMode.BBS
    g = svc.get(task.id).execution_graph
    work = next(n for n in g.nodes if n.node_id == "n_bbs_review")
    assert work.status is NodeStatus.RUNNING
    assert work.assignee == "bbs-bot-1"


# --- ⑤ NODE_FAILED tick 驱动同执行方重派,到上限派 reroute 判定给 skill(T-13)---

def test_node_failed_retries_same_executor_then_asks_skill_reroute():
    """失败链路 e2e(只在外部边界注入,不戳状态):
    - 派发起跑 → skill 回投 NODE_FAILED → 落态 fold 置 FAILED → scheduler.on_event 泵 tick
      → _retry_failed 同执行方 re-claim+fire(计数推进)。
    - 再失败 → 计数到上限 → tick 不再重派,改为向失败方 exec-bot 派 reroute 判定(probe);
      reroute **由 skill 判**,非 scheduler 的 redispatch(C5) 规则 → driver.redispatched 为空。
    重路由的图操作(失败方 skill 发起 gap bot-search → 命中重派/未匹配再拆)见 FR-GRAPH-08 /
    E2E-4(T-28),此处断言到"派给 skill 判"的交接。"""
    svc = _svc()
    discover = FakeDiscover(hits={"n_impl_hash": ["crypto-bot"]})  # 命中 crypto-bot
    driver = FakeDriver()
    sched = _scheduler(svc, discover, driver=driver)
    task = _planned_task(svc, sched, "n_impl_hash", "实现登录密码哈希比对")
    # start 首 tick:n_impl_hash DISPATCH → discover 命中 crypto-bot → claim+fire → RUNNING
    task = svc.get(task.id)
    work = next(n for n in task.execution_graph.nodes if n.node_id == "n_impl_hash")
    assert work.status is NodeStatus.RUNNING
    assert work.assignee == "crypto-bot"
    assert len(work.attempted_executors) == 1  # 首次 claim 记录
    assert (task.id, "n_impl_hash", "crypto-bot") in sched._execution.single_bots  # noqa: SLF001

    # skill 回投 NODE_FAILED #1 → 落态 fold(FAILED)+ 泵 tick → 同执行方重派(计数 →2)
    _report_event(svc, sched, task.id, EventKind.NODE_FAILED, {"node_id": "n_impl_hash"})
    task = svc.get(task.id)
    work = next(n for n in task.execution_graph.nodes if n.node_id == "n_impl_hash")
    assert work.status is NodeStatus.RUNNING  # 重派后回 RUNNING
    assert len(work.attempted_executors) == 2  # re-claim 推进了计数(修掉计数不涨 bug)
    assert sum(1 for (_t, nid, b) in sched._execution.single_bots if nid == "n_impl_hash" and b == "crypto-bot") == 2  # noqa: SLF001
    assert driver.redispatched == []  # 未到上限,不 reroute
    assert sched._execution.probes == []  # noqa: SLF001

    # skill 回投 NODE_FAILED #2 → 落态 fold + 泵 tick → 计数到上限 → 派 reroute 判定给 skill
    _report_event(svc, sched, task.id, EventKind.NODE_FAILED, {"node_id": "n_impl_hash"})
    task = svc.get(task.id)
    work = next(n for n in task.execution_graph.nodes if n.node_id == "n_impl_hash")
    assert work.status is NodeStatus.FAILED  # 到上限,停止重派,留 FAILED 等 skill 判 reroute
    assert (task.id, "n_impl_hash", "crypto-bot") in sched._execution.probes  # noqa: SLF001  派给 skill 判
    assert sum(1 for (_t, nid, b) in sched._execution.single_bots if nid == "n_impl_hash" and b == "crypto-bot") == 2  # noqa: SLF001  不再重派
    assert driver.redispatched == []  # reroute 是 skill 判,非 scheduler C5 规则

# --- ⑥ reroute 后半段:skill 判定后发起 gap bot-search(FR-GRAPH-08 / T-28)---------

def _drive_to_reroute_handoff(svc, sched, task_id, leaf_id):
    """把失败叶子驱动到"到上限 + 已派 reroute 判定给 skill"的交接点,返回最新 task。"""
    _report_event(svc, sched, task_id, EventKind.NODE_FAILED, {"node_id": leaf_id})  # retry #1
    _report_event(svc, sched, task_id, EventKind.NODE_FAILED, {"node_id": leaf_id})  # max → probe skill
    return svc.get(task_id)


def test_node_failed_reroute_hit_dispatches_new_executor():
    """reroute 命中:skill 经 open_reroute_search 发起 gap bot-search → tick _bot_search
    命中新执行方 → dispatch 重派(节点身份变为新 reroute 派发链,执行方变 crypto-bot2)。"""
    svc = _svc()
    discover = FakeDiscover(hits={"n_impl_hash": ["crypto-bot"], "n_impl_hash_reroute": ["crypto-bot2"]})
    driver = FakeDriver()
    sched = _scheduler(svc, discover, driver=driver)
    task = _planned_task(svc, sched, "n_impl_hash", "实现登录密码哈希比对")
    task = _drive_to_reroute_handoff(svc, sched, task.id, "n_impl_hash")
    assert (task.id, "n_impl_hash", "crypto-bot") in sched._execution.probes  # noqa: SLF001  已派给 skill 判
    # skill 判定需重路由 → 发起 gap bot-search(真实图操作:挂兄弟 BOT_SEARCH,非 failures 下作子)
    reroute = svc.open_reroute_search(task.id, "n_impl_hash", "重路由:实现登录密码哈希比对(gap:哈希比对未通过)")
    assert reroute.node_id == "n_impl_hash_reroute"
    sched.tick(task.id)  # tick 处理 reroute BOT_SEARCH:搜推命中 crypto-bot2 → 落 _disp 子
    sched.tick(task.id)  # _disp → claim crypto-bot2 + fire → RUNNING
    task = svc.get(task.id)
    disp = next(n for n in task.execution_graph.nodes if n.node_id == "n_impl_hash_reroute_disp")
    assert disp.assignee == "crypto-bot2"  # 重路由到**新执行方**(非原 crypto-bot)
    assert disp.status is NodeStatus.RUNNING
    assert (task.id, "n_impl_hash_reroute_disp", "crypto-bot2") in sched._execution.single_bots  # noqa: SLF001
    assert driver.redispatched == []  # 无 scheduler C5 规则
    # 原失败节点已被 reroute 接管 → 标 superseded(FAILED→DONE),不再挡终验
    task = svc.get(task.id)
    assert next(n for n in task.execution_graph.nodes if n.node_id == "n_impl_hash").status is NodeStatus.DONE
    # reroute 成功:skill 回投验收 PASS → 全图 DONE → goal-verify 终验 → task DONE
    _accept_leaf(svc, task.id, "n_impl_hash_reroute_disp")
    task = _tick_until(svc, sched, task.id, lambda t: t.status is GraphStatus.DONE)
    assert task.status is GraphStatus.DONE
    assert task.execution_graph.status is GraphStatus.DONE


def test_node_failed_reroute_miss_recursive_decompose_depth_plus_one():
    """reroute 未匹配:skill 经 open_reroute_search 发起 gap bot-search → tick _bot_search
    未匹配 → DECOMPOSITION → decompose_subtasks(真实子任务,depth=失败节点+1)。"""
    svc = _svc()
    discover = FakeDiscover(hits={"n_impl_hash": ["crypto-bot"]})  # reroute node 未映射 → miss
    decomposer = FakeDecomposer()
    driver = FakeDriver()
    sched = _scheduler(svc, discover, decomposer, driver=driver)
    task = _planned_task(svc, sched, "n_impl_hash", "实现登录密码哈希比对")
    task = _drive_to_reroute_handoff(svc, sched, task.id, "n_impl_hash")
    assert (task.id, "n_impl_hash", "crypto-bot") in sched._execution.probes  # noqa: SLF001
    # skill 判 reroute → 发起 gap bot-search;reroute node 未命中 → decomposition
    svc.open_reroute_search(task.id, "n_impl_hash", "重路由:实现登录密码哈希比对(gap:哈希比对未通过)")
    sched.tick(task.id)  # reroute BOT_SEARCH miss → 落 DECOMPOSITION 子(spec=gap)
    sched.tick(task.id)  # DECOMPOSITION → decompose_subtasks(gap) → children BOT_SEARCH(depth=1)
    task = svc.get(task.id)
    # decomposer 收到的入参 = 真实 gap 文本(非占位)
    assert any("密码哈希比对" in s for s, _d in decomposer.sub_calls)
    # reroute 的 DECOMPOSITION 子节点
    dec = next(
        n for n in task.execution_graph.nodes
        if n.node_type is NodeType.DECOMPOSITION and n.node_id.startswith("n_impl_hash_reroute")
    )
    assert dec is not None
    # 3 个真实子任务(HASH_CHILDREN),depth = 失败节点(0)+1 = 1
    child_ids = ("h_hash_fn", "h_salt", "h_compare_test")
    children = [n for n in task.execution_graph.nodes if n.node_id in child_ids]
    assert len(children) == 3
    for cid in child_ids:
        assert task.execution_graph.state.subtasks[cid].depth == 1  # 递归 depth+1
    # 后续 tick 把 children 各自派发(crypto-bot),证明 reroute-miss→拆解→派发链由 tick 跑通
    for _ in range(3):
        sched.tick(task.id)
    assert any(
        b == "crypto-bot" for (_t, nid, b) in sched._execution.single_bots  # noqa: SLF001
        if nid in (f"{c}_disp" for c in child_ids)
    )
    assert driver.redispatched == []  # 无 scheduler C5 规则


# --- ⑦ 不可恢复 FAILED → 挂起等人确认 → 人不升 → task FAILED(T-13 unrecoverable)---

def test_node_failed_unrecoverable_hang_then_human_decline_task_failed():
    """不可恢复失败:retry 上限 → probe 给 skill → skill 未发起 reroute(无兄弟 BOT_SEARCH)
    → tick 自动挂起 AWAITING_HUMAN_ACCEPT → 人确认不升(HANG_CANCELLED,经真实 on_event fold)
    → task FAILED 终态。

    与 ⑥ 的区别:⑥ skill 判定 reroute(发起 gap bot-search,可恢复);本用例 skill 放弃
    reroute → 不可恢复 → 挂起等人 → 人不升 → task FAILED。全程真实 on_event + tick,不戳状态。"""
    svc = _svc()
    discover = FakeDiscover(hits={"n_impl_hash": ["crypto-bot"]})
    driver = FakeDriver()
    sched = _scheduler(svc, discover, driver=driver)
    task = _planned_task(svc, sched, "n_impl_hash", "实现登录密码哈希比对")
    _drive_to_reroute_handoff(svc, sched, task.id, "n_impl_hash")  # 2× NODE_FAILED → probe 已派
    task = svc.get(task.id)
    assert (task.id, "n_impl_hash", "crypto-bot") in sched._execution.probes  # noqa: SLF001
    # skill 未发起 reroute(无 n_impl_hash_reroute 兄弟)→ 下次 tick 检"probe 已派 + 无兄弟"→ 自动挂起
    sched.tick(task.id)
    task = svc.get(task.id)
    assert task.execution_graph.status is GraphStatus.HUMAN_REQUIRED
    # 人确认不升 → task FAILED 终态(经真实 on_event HANG_CANCELLED fold)
    _report_event(svc, sched, task.id, EventKind.HANG_CANCELLED, {})
    task = svc.get(task.id)
    assert task.status is GraphStatus.FAILED
    assert driver.redispatched == []  # 无 scheduler C5 规则;不可恢复走人确认→FAILED,非计数 reroute


# --- ⑧ 执行过程中 State 演进:update_state 写产出/gap,retrieve_state 被下游读(方案 B)---

def test_execution_writes_state_and_reroute_reads_it():
    """State 在执行过程中演进 + 被下游消费(走已实现的图操作写口 update_state/retrieve_state,
    非事件 state_patch):

    - skill 执行中(节点 RUNNING)经 ``update_state`` 把中间结果/上下文写进 subtask State;
    - 验收 fail 后把 gap 写进 State(retrieve-state 的 gap 读取源);
    - reroute 前 ``retrieve_state`` 读 gap 上下文 → 拼 gap_spec(非测试写死)→ open_reroute_search;
    - reroute 成功 → superseded → task DONE。

    覆盖 spec FR-GRAPH-03(更新 State / retrieve-state 读写契约)+ FR-GRAPH-08(gap 重路由读 State)。"""
    from agentclaw.community.core.task.domain.models import StateSemantics

    svc = _svc()
    discover = FakeDiscover(hits={"n_impl_hash": ["crypto-bot"], "n_impl_hash_reroute": ["crypto-bot2"]})
    driver = FakeDriver()
    sched = _scheduler(svc, discover, driver=driver)
    task = _planned_task(svc, sched, "n_impl_hash", "实现登录密码哈希比对")
    leaf = "n_impl_hash"
    assert next(n for n in task.execution_graph.nodes if n.node_id == leaf).status is NodeStatus.RUNNING

    # ① skill 执行中:写中间结果 + 执行上下文进 State(执行过程 state 演进,MERGE/APPEND)
    svc.update_state(task.id, leaf, {"execution_context": {"attempt": 1, "approach": "bcrypt"}}, StateSemantics.MERGE)
    svc.update_state(task.id, leaf, {"intermediate_results": ["登录比对逻辑初版"]}, StateSemantics.APPEND)
    st = svc.retrieve_state(task.id, leaf)["subtask"]
    assert st["intermediate_results"] == ["登录比对逻辑初版"]
    assert st["execution_context"]["approach"] == "bcrypt"  # 执行中写的上下文 retrieve 得到

    # ② retry 到上限(2× NODE_FAILED)→ skill 把 gap 写进 State(retrieve-state 的 gap 源)
    _report_event(svc, sched, task.id, EventKind.NODE_FAILED, {"node_id": leaf})  # retry #1
    _report_event(svc, sched, task.id, EventKind.NODE_FAILED, {"node_id": leaf})  # max → probe skill
    svc.update_state(task.id, leaf, {"execution_context": {"last_gap": "哈希比对结果不一致"}}, StateSemantics.MERGE)

    # ③ reroute 前:retrieve_state 读 gap → 拼 gap_spec(非写死),发起 gap bot-search
    st = svc.retrieve_state(task.id, leaf)["subtask"]
    gap = st["execution_context"].get("last_gap", "")
    assert gap == "哈希比对结果不一致"
    gap_spec = f"重路由:实现登录密码哈希比对(gap:{gap})"
    svc.open_reroute_search(task.id, leaf, gap_spec)

    # ④ tick 处理 reroute:命中 crypto-bot2 → 重派新执行方;原失败节点 superseded(DONE)
    sched.tick(task.id)  # reroute BOT_SEARCH 命中 → 落 _disp
    sched.tick(task.id)  # _disp → claim crypto-bot2 → RUNNING
    task = svc.get(task.id)
    disp = next(n for n in task.execution_graph.nodes if n.node_id == "n_impl_hash_reroute_disp")
    assert disp.assignee == "crypto-bot2"
    assert next(n for n in task.execution_graph.nodes if n.node_id == leaf).status is NodeStatus.DONE

    # ⑤ reroute 验收 PASS → 全图 DONE → 终验 DONE(rerouted 成功自行 DONE)
    _accept_leaf(svc, task.id, "n_impl_hash_reroute_disp")
    task = _tick_until(svc, sched, task.id, lambda t: t.status is GraphStatus.DONE)
    assert task.status is GraphStatus.DONE
    assert task.execution_graph.status is GraphStatus.DONE


# --- ⑨ State 变更事件溯源:update_state 记 STATE_UPDATED,GraphCheckpoint.replay 可还原 ---

def test_state_changes_event_sourced_replayable():
    """补 plan §286 缺口:State 变更进事件流(update_state 经 on_event 记 STATE_UPDATED),
    GraphCheckpoint.replay 从写前快照重放事件能还原 State。State 不再是"只在内存里",
    而是可溯源重放。"""
    from agentclaw.community.core.task.domain.models import StateSemantics
    from agentclaw.community.core.task.services.graph_checkpoint import GraphCheckpoint

    svc = _svc()
    discover = FakeDiscover(hits={"n_impl_hash": ["crypto-bot"]})
    sched = _scheduler(svc, discover)
    task = _planned_task(svc, sched, "n_impl_hash", "实现登录密码哈希比对")
    leaf = "n_impl_hash"
    chk = GraphCheckpoint(svc, svc._event_repo, svc._task_repo)  # noqa: SLF001
    before_seq = int(svc._event_repo.latest_seq(task.id) or 0)  # noqa: SLF001 — 真实日志最新 seq
    chk.snapshot(task.id, before_seq)  # 写 State 前的快照(replay 起点)

    # skill 执行中写 State → update_state 经 on_event 记 STATE_UPDATED 入日志
    svc.update_state(task.id, leaf, {"intermediate_results": ["哈希实现v1"]}, StateSemantics.APPEND)
    svc.update_state(task.id, leaf, {"execution_context": {"approach": "bcrypt"}}, StateSemantics.MERGE)
    task = svc.get(task.id)
    assert task.execution_graph.state.subtasks[leaf].intermediate_results == ["哈希实现v1"]

    # 事件日志里有 STATE_UPDATED(增量在日志里,可溯源)
    events = svc._event_repo.load_events(task.id, before_seq)  # noqa: SLF001
    assert any(e.kind is EventKind.STATE_UPDATED for e in events)
    assert sum(1 for e in events if e.kind is EventKind.STATE_UPDATED) == 2

    # replay 从写前快照重放(含 STATE_UPDATED)→ 还原出 State(不依赖内存,从事件流重建)
    replayed = chk.replay(task.id, before_seq)
    st = replayed.state.subtasks[leaf]
    assert st.intermediate_results == ["哈希实现v1"]
    assert st.execution_context.get("approach") == "bcrypt"
