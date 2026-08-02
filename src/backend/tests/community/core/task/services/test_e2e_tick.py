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
- NODE_FAILED 同执行方重派 / 超限 reroute C5
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceCriteriaKind,
    AttemptedRecord,
    AttemptOutcome,
    AttemptTrigger,
    GraphStatus,
    NodeStatus,
    NodeType,
    Plan,
    RouteClass,
    RunMode,
    SubTaskSpec,
    TaskGoal,
    TaskStatus,
    TaskState,
)
from agentclaw.community.core.task.domain.events import EventKind, TaskEvent
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

    def decompose(self, task_id: str) -> Plan:  # 旧签名过渡
        return Plan(sub_tasks=list(TOP_CHILDREN), confidence=0.8)

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


def _task(svc: TaskService, objective: str = OBJECTIVE, acceptances: int = 3):
    """建真实需求 task + spawn_build_dag(无 plan sub_tasks → 根 BOT_SEARCH,spec=真实目标)。

    返回 EXECUTING / graph ON_PLAZA 的 task。spawn_build_dag 自持久化。"""
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
    # DRAFTING → DEFINED(空 plan:无 sub_tasks → spawn 走根 BOT_SEARCH 搜推先行)
    svc.finalize_plan(task.id, Plan(sub_tasks=[], confidence=0.9))
    task = svc.get(task.id)
    task.status = TaskStatus.EXECUTING
    task.execution_graph = None  # 让 spawn_build_dag 重建
    svc._task_repo.save(task)  # noqa: SLF001
    svc.spawn_build_dag(task)
    task = svc.get(task.id)
    svc.mark_graph_status(task, GraphStatus.ON_PLAZA)
    return svc.get(task.id)


def _planned_task(svc: TaskService, node_id: str, spec: str):
    """带 1 个真实 plan subtask 的 task(spawn 建 planning 链 + 该 subtask DISPATCH)。"""
    t = svc.create(title=spec, background="obj")
    task = svc.get(t.id)
    task.spec.goal = _goal()
    svc._task_repo.save(task)  # noqa: SLF001
    task = svc.get(t.id)
    svc.finalize_plan(
        task.id,
        Plan(sub_tasks=[SubTaskSpec(node_id=node_id, spec=spec, run_mode=RunMode.SINGLE_BOT)], confidence=0.9),
    )
    task = svc.get(task.id)
    task.status = TaskStatus.EXECUTING
    task.execution_graph = None
    svc._task_repo.save(task)  # noqa: SLF001
    svc.spawn_build_dag(task)
    task = svc.get(task.id)
    svc.mark_graph_status(task, GraphStatus.ON_PLAZA)
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
    task = _task(svc, objective="给README补一节安装说明", acceptances=1)
    discover = FakeDiscover(hits={"n_bot_search": ["doc-bot"]})
    sched = _scheduler(svc, discover)
    assert next(n for n in task.execution_graph.nodes if n.node_id == "n_bot_search").spec == "给README补一节安装说明"
    # tick1:规划链 DONE;root 搜推命中 doc-bot → 落 dispatch 子
    r1 = sched.tick(task.id)
    assert r1["action"] == "ticked"
    task = svc.get(task.id)
    disp = next(n for n in task.execution_graph.nodes if n.node_type is NodeType.DISPATCH)
    assert disp.assignee == "doc-bot"
    # tick2:dispatch → claim + fire → RUNNING
    sched.tick(task.id)
    # skill 回投验收 PASS
    _accept_leaf(svc, task.id, disp.node_id)
    # tick3:无未闭合 → goal-verify PASS → VERIFIED + DONE
    task = _tick_until(svc, sched, task.id, lambda t: t.status is TaskStatus.DONE)
    assert task.execution_graph.graph_status is GraphStatus.VERIFIED
    assert task.status is TaskStatus.DONE


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
    task = _task(svc)  # 交付用户登录功能,3 验收
    discover = FakeDiscover()  # n_bot_search / s_write_tests 未匹配;其余命中
    decomposer = FakeDecomposer()
    sched = _scheduler(svc, discover, decomposer)

    # --- 驱动①:tick 推进动作拓扑。tick 前 root PENDING,tick 后 _bot_search 分支落子并置 DONE。
    root = next(n for n in task.execution_graph.nodes if n.node_id == "n_bot_search")
    assert root.spec == OBJECTIVE  # 根 BOT_SEARCH 承载真实目标(spawn_build_dag 写入)
    assert root.status is NodeStatus.PENDING
    sched.tick(task.id)  # 规划链 DONE;root 搜推未匹配 → 落 DECOMPOSITION 子 + root DONE
    task = svc.get(task.id)
    root_now = next(n for n in task.execution_graph.nodes if n.node_id == "n_bot_search")
    assert root_now.status is NodeStatus.DONE  # tick 真的驱动了动作完成(非静态图)

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
    task = _tick_until(svc, sched, task.id, lambda t: t.status is TaskStatus.DONE)
    assert any(n.node_type is NodeType.EXEC_AGGREGATE for n in task.execution_graph.nodes)
    assert task.execution_graph.graph_status is GraphStatus.VERIFIED
    assert task.status is TaskStatus.DONE


# --- ③ 递归上限 → mark-hang(不直升 BBS)-----------------------------------

def test_tick_recursion_limit_mark_hang():
    from agentclaw.community.core.task.services.scheduler_ops import MAX_RECURSION_DEPTH

    svc = _svc()
    task = _task(svc)
    discover = FakeDiscover()  # root 未匹配
    # depth_override=MAX:分解器返回的 children 深度≥MAX → _decomposition 走 mark-hang 分支
    sched = _scheduler(svc, discover, FakeDecomposer(depth_override=MAX_RECURSION_DEPTH))
    sched.tick(task.id)  # 规划链 + root 未匹配 → decomposition
    sched.tick(task.id)  # decomposition → children depth≥MAX → mark-hang
    task = svc.get(task.id)
    assert any(n.node_type is NodeType.MARK_HANG for n in task.execution_graph.nodes)
    assert task.execution_graph.graph_status is GraphStatus.AWAITING_HUMAN_ACCEPT
    assert not any(n.node_type is NodeType.BBS_DISPATCH for n in task.execution_graph.nodes)  # 不直升 BBS


# --- ④ hang → 人确认升 BBS → 同图延续(三终止②)----------------------------

def test_tick_hang_confirm_bbs_continue_same_graph():
    from agentclaw.community.core.task.domain.events import next_seq
    from agentclaw.community.core.task.services.scheduler_ops import MAX_RECURSION_DEPTH

    svc = _svc()
    task = _task(svc)
    discover = FakeDiscover()
    sched = _scheduler(svc, discover, FakeDecomposer(depth_override=MAX_RECURSION_DEPTH))
    sched.tick(task.id)
    sched.tick(task.id)  # → mark-hang + AWAITING_HUMAN_ACCEPT
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.AWAITING_HUMAN_ACCEPT
    hang_node = next(n for n in task.execution_graph.nodes if n.node_type is NodeType.MARK_HANG)
    # 人确认升 BBS(POST /events 回投 BBS_CONFIRMED)→ ON_PLAZA + BBS_DISPATCH
    svc.on_event({
        "task_id": task.id, "kind": EventKind.BBS_CONFIRMED,
        "seq": next_seq(svc._event_repo.latest_seq(task.id)),  # noqa: SLF001
        "payload": {"node_id": hang_node.node_id},
    })
    task = svc.get(task.id)
    assert task.execution_graph.graph_status is GraphStatus.ON_PLAZA
    assert any(n.node_type is NodeType.BBS_DISPATCH for n in task.execution_graph.nodes)
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


# --- ⑤ NODE_FAILED 同执行方重派 / 超限 reroute C5(T-13)----------------------

def test_node_failed_retries_same_executor_then_reroutes_c5():
    svc = _svc()
    # 真实子任务:实现登录密码哈希比对(Search 命中 crypto-bot 后派发)
    task = _planned_task(svc, "n_impl_hash", "实现登录密码哈希比对")
    discover = FakeDiscover(hits={"n_impl_hash": ["crypto-bot"], "n_impl_hash_disp": ["crypto-bot"]})
    driver = FakeDriver()
    sched = _scheduler(svc, discover, driver=driver)
    sched.tick(task.id)  # DISPATCH n_impl_hash → discover 命中 crypto-bot → claim+fire → RUNNING
    task = svc.get(task.id)
    work = next(n for n in task.execution_graph.nodes if n.node_id == "n_impl_hash")
    assert work.assignee == "crypto-bot"
    # skill 回投 NODE_FAILED(1 次 < max=2)→ 同执行方重派
    work.status = NodeStatus.FAILED
    work.attempted_executors = [
        AttemptedRecord(
            executor_id="crypto-bot", paradigm=RunMode.SINGLE_BOT, round=1,
            outcome=AttemptOutcome.FAIL, trigger=AttemptTrigger.ROUTED,
        ),
    ]
    svc._task_repo.save(task)  # noqa: SLF001
    sched.on_event(TaskEvent(task_id=task.id, seq=99, kind=EventKind.NODE_FAILED, payload={"node_id": "n_impl_hash"}))
    assert (task.id, "n_impl_hash", "crypto-bot") in sched._execution.single_bots  # noqa: SLF001
    assert driver.redispatched == []
    # 超限(2 fails ≥ max)→ reroute C5
    task = svc.get(task.id)
    work = next(n for n in task.execution_graph.nodes if n.node_id == "n_impl_hash")
    work.status = NodeStatus.FAILED
    work.attempted_executors = [
        AttemptedRecord(executor_id="crypto-bot", paradigm=RunMode.SINGLE_BOT, round=1, outcome=AttemptOutcome.FAIL, trigger=AttemptTrigger.ROUTED),
        AttemptedRecord(executor_id="crypto-bot", paradigm=RunMode.SINGLE_BOT, round=2, outcome=AttemptOutcome.FAIL, trigger=AttemptTrigger.ROUTED),
    ]
    svc._task_repo.save(task)  # noqa: SLF001
    sched.on_event(TaskEvent(task_id=task.id, seq=100, kind=EventKind.NODE_FAILED, payload={"node_id": "n_impl_hash"}))
    assert ("n_impl_hash", RouteClass.C5) in driver.redispatched