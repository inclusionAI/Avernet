"""行覆盖缺口补测:task_context / task_dispatch / task_plan 三模块。

只新建本文件;对齐既有测试风格(test_task_graph_service / test_dispatcher / test_planner /
test_static_plan_engine):真实 TaskGraphService 构图 + 就地定义 fake/stub;
防御分支用敌意协作对象或受控 monkeypatch 驱动(参照
tests/community/core/task/task_trajectory/test_trajectory_service.py 的 fake 模式)。

本文件只测 baseline /tmp/task_cov_baseline.json 中列出的 missing_lines,对每个
未覆盖行为做真实断言。
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.errors import (
    GraphAlreadyInitializedError,
    GraphIntegrityError,
    GraphVersionConflictError,
    NodeNotFoundError,
    TaskNotFoundError,
    TaskStateError,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceResult,
    AcceptanceVerdict,
    Context,
    Goal,
    NodeAction,
    PlanResult,
    Relation,
    RuntimeInfo,
    Status,
    TaskCallbackData,
    TaskExecutionGraph,
    TaskGraphPatch,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_context import task_graph_support
from agentclaw.community.core.task.task_context.task_context_service import (
    build_task_runner_execution_event_kwargs,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_dispatch.claim_join_gate import (
    TaskClaimJoinGate,
    TaskSettingsService,
)
from agentclaw.community.core.task.task_dispatch.dispatcher import (
    TaskDispatcher,
    _is_exec_retry_replay,
)
from agentclaw.community.core.task.task_dispatch.search import TaskSearch
from agentclaw.community.core.task.task_dispatch.strategies import (
    DirectDispatchStrategy,
    GroupFormation,
    SearchBasedDispatchStrategy,
    SearchOutcome,
    SearchResult,
    _candidate_dispatch_ids,
    _parse_search_result,
    prefetch_candidates,
)
from agentclaw.community.core.task.task_plan.planner import TaskPlanner
from agentclaw.community.core.task.task_plan.static_plan import (
    StaticPlanDefinition,
    StaticPlanReadiness,
    StaticPlanRuntime,
)
from agentclaw.community.core.task.task_plan.strategies import (
    GapBasedPlanningStrategy,
    WorkflowPlanningStrategy,
    _build_child_task_spec,
    _parse_plan_result,
)
from agentclaw.community.core.task.task_runner.execution_adapters import (
    CentralizedExecutionAdapter,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _task_info(task_id: str = "t1", execution_config: dict | None = None,
               owner_bot_id: str = "b1") -> TaskInfo:
    return TaskInfo(
        task_id=task_id,
        task_spec=TaskSpec(
            context=Context(background="bg", title="T"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id=owner_bot_id,
        execution_config=dict(execution_config or {}),
    )


def _child(node_id: str, task_id: str = "t1", run_mode: str | None = None,
           assignee: str | None = None, static_input: dict | None = None) -> TaskNode:
    spec = _task_info(task_id).task_spec
    if static_input is not None:
        spec = TaskSpec(
            context=Context(background="bg", title="T", extend_props={"static_input": static_input}),
            goal=Goal(objective="o", acceptances=[]),
        )
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=spec,
        run_info=RuntimeInfo(run_mode=run_mode, assignee=assignee),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _patch(task_id: str, node_id: str, **kw) -> TaskNodePatch:
    return TaskNodePatch(task_id=task_id, node_id=node_id, **kw)


def _bare_graph(extend_props: dict | None = None) -> TaskExecutionGraph:
    return TaskExecutionGraph(
        run_id=1, loop_round=0, status=Status.PENDING,
        extend_props=dict(extend_props or {}),
    )


class FakeGraphRepo:
    """最小图仓储 fake:create/save 深拷贝快照,冲突/异常可编排,审计/CAS 可断言。"""

    def __init__(self) -> None:
        import copy

        self._copy = copy.deepcopy
        self.store: dict[str, TaskExecutionGraph] = {}
        self.versions: dict[str, int] = {}
        self.create_count = 0
        self.save_count = 0
        self.conflicts_before_success = 0
        self.save_exc: Exception | None = None
        self.action_seqs: dict[tuple[str, str], int] = {}
        self.action_log_groups: dict[str, list] = {}
        self.claim_cas = True
        self.claim_calls: list[str] = []
        self.last_callback_audit = None

    # -- protocol surface (仅实现被真正调用的成员) --
    def create_graph(self, graph, *, runtime_status):
        self.create_count += 1
        self.store[graph.task_id] = self._copy(graph)
        self.versions[graph.task_id] = self.versions.get(graph.task_id, 0) + 1
        return self.versions[graph.task_id]

    def load_graph(self, task_id: str):
        snap = self.store.get(task_id)
        return self._copy(snap) if snap is not None else None

    def get_version(self, task_id: str):
        return self.versions.get(task_id)

    def save_graph(self, graph, *, expected_version, runtime_status,
                   action_events=None, callback_audit=None):
        self.save_count += 1
        self.last_callback_audit = callback_audit
        if self.conflicts_before_success > 0:
            self.conflicts_before_success -= 1
            raise GraphVersionConflictError(f"stale expected={expected_version}")
        if self.save_exc is not None:
            raise self.save_exc
        self.store[graph.task_id] = self._copy(graph)
        self.versions[graph.task_id] = self.versions.get(graph.task_id, 0) + 1
        return self.versions[graph.task_id]

    def next_action_seq(self, task_id: str, node_id: str) -> int:
        return self.action_seqs.get((task_id, node_id), 0)

    def load_action_logs(self, task_id: str, *, node_id=None, limit: int = 200):
        return self.action_log_groups

    def claim_bbs_owner(self, task_id: str, bot_id: str) -> bool:
        self.claim_calls.append(bot_id)
        return self.claim_cas

    # -- test controls --
    def bump_version(self, task_id: str):
        self.versions[task_id] = self.versions.get(task_id, 0) + 1

    def mutate_snapshot(self, task_id: str, mutator):
        """模拟另一实例对共享存储的推进(he advance mutation)。"""
        snap = self.store[task_id]
        mutator(snap)
        self.versions[task_id] = self.versions.get(task_id, 0) + 1


class _FakeRunner:
    def __init__(self):
        self.started: list[list[str]] = []
        self.groups: list = []

    async def start_run(self, nodes):
        self.started.append([n.node_id for n in nodes])
        return [True] * len(nodes)

    async def form_coop_group(self, group):
        self.groups.append(group)
        return "grp-1"

    async def get_group_session(self, group_id):
        return "sess-1"


class _BotP:
    """同步回投规划/搜推端口 fake(返回编排好的 run dict)。"""

    def __init__(self, run):
        self._run = run
        self.calls = []

    async def send_and_wait_async(self, **kwargs):
        self.calls.append(kwargs)
        return self._run


class _DiscoverOne:
    """候选预查端口 fake:恒返回 1 个候选。"""

    def search_by_keyword(self, **kwargs):
        return {"items": [{"bot_id": "cand", "bot_uuid": "cand:owner"}]}


class _FlakyResultRun(dict):
    """敌意 run:get("result") 前两次正常、第三次抛错(模拟传输抖动)。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._result_gets = 0

    def get(self, key, default=None):
        if key == "result":
            self._result_gets += 1
            if self._result_gets >= 3:
                raise RuntimeError("transport glitch")
        return super().get(key, default)


class _DonePlanningStrategy:
    """恒返 gap 闭(children=[], has_gap=False)的规划策略。"""

    rule_id = "always_done"
    priority = 5

    async def matches(self, graph) -> bool:
        return True

    async def apply(self, graph, target) -> PlanResult:
        return PlanResult(children=[], has_gap=False, gap_detail="done",
                          strategy_name="always_done")


class _NeverMatchStrategy:
    rule_id = "never"
    priority = 1

    async def matches(self, *args, **kwargs) -> bool:
        return False

    async def apply(self, *args, **kwargs):  # pragma: no cover - 永不命中
        raise AssertionError("never-match strategy must not apply")


class _StubDispatchStrategy:
    rule_id = "stub"
    priority = 5

    def __init__(self, result: SearchResult):
        self._result = result
        self.search_calls = 0

    async def matches(self, node, graph) -> bool:
        return True

    async def apply(self, node, graph) -> SearchResult:
        self.search_calls += 1
        return self._result


def _make_engine(svc, *, planner=None, runner=None, notify_provider=None):
    runner = runner or _FakeRunner()

    class _Engine(CentralizedExecutionAdapter):
        def _build_runner(self):
            return runner

        def _build_planner(self):
            if planner is not None:
                from agentclaw.community.core.task.task_plan.planner import TaskPlanner as P
                return P(self._graph, pool=[planner])
            return super()._build_planner()

    return _Engine(svc, notify_messages_provider=notify_provider)


ONE_BOT_PLAN = """
template_id: onebot
nodes:
  - id: onlybot
    type: bot
    bot_id: bot-only
"""

EMPTY_PLAN = """
template_id: emptyplan
nodes: []
"""

COVER_PLAN = """
template_id: cover
nodes:
  - id: note
    type: notify
  - id: handoff
    type: bbs_handoff
    bot_id: safety-bot
  - id: naked
    type: bot
"""

RELAY_V2_PLAN = """
template_id: relayv2
entry_bot_id: entry-bot
entry_name: 入口Bot
nodes:
  - id: upstream_collab
    type: collaboration
    collaboration: {bot_ids: [driver-bot, worker-bot]}
    input: {okr: $.input.okr}
    output: {report: $.result}
  - id: mid_bot
    type: bot
    bot_id: mid-bot
    depends_on: [upstream_collab]
    task: 中位 bot 角色任务
    input: {body: $.upstream_collab.output.report}
    output: {r2: $.result}
  - id: down_collab
    type: collaboration
    collaboration:
      bot_ids: [alpha-bot, beta-bot]
      bot_names: [甲Bot, 乙Bot]
    depends_on: [mid_bot]
    task: 下游协作群任务
    input: {summary: $.mid_bot.output.r2}
    output: {r3: $.result}
"""


# ===========================================================================
# task_context/task_context_service.py — build_task_runner_execution_event_kwargs
# ===========================================================================

class TestTaskRunnerEventKwargs:
    def test_unclassified_exception_with_details(self):
        node = _child("c1")
        kw = build_task_runner_execution_event_kwargs(
            node, "single_bot", "failed",
            exception=ValueError("boom"), phase="dispatch", details={"hint": "h1"},
        )
        assert kw["error_type"] == "unclassified"
        assert kw["error_msg"] == "boom"
        assert kw["ext_info"]["hint"] == "h1"
        assert kw["ext_info"]["execution_mode"] == "single_bot"
        assert kw["ext_info"]["phase"] == "dispatch"
        assert kw["status_from"] is Status.PENDING
        assert kw["status_to"] is Status.PENDING

    def test_transport_error_classification_parity(self):
        kw = build_task_runner_execution_event_kwargs(
            _child("c1"), "coop_group", "failed", exception=TimeoutError("late"),
        )
        assert kw["error_type"] == "transport_error"


# ===========================================================================
# task_context/task_graph_service.py + task_graph_support.py
# ===========================================================================

class TestGraphServiceRepoBinding:
    def test_bind_repository_and_hydrate_none_raises(self):
        repo = FakeGraphRepo()
        svc = TaskGraphService()
        assert svc.has_repository is False
        svc.bind_repository(repo)
        assert svc.has_repository is True
        # 图不在共享存储 → hydrate 返回 None → TaskNotFoundError
        with pytest.raises(TaskNotFoundError):
            svc.query_task_dashboard("missing")


class TestGraphVersionRetry:
    def test_conflict_then_success_replays_mutation(self):
        repo = FakeGraphRepo()
        repo.conflicts_before_success = 1
        svc = TaskGraphService(repo)
        svc.initialize_graph(_task_info("t1"))
        assert repo.create_count == 1
        result = svc.update_task_node_info(
            _patch("t1", "t1", status=Status.RUNNING, run_mode="single_bot", assignee="b1")
        )
        assert result.success is True
        assert result.new_status is Status.RUNNING
        # 冲突 1 次 + 成功 1 次
        assert repo.save_count == 2
        root = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "t1")
        assert root.status is Status.RUNNING

    def test_conflict_exhausted_raises_after_max_retries(self):
        repo = FakeGraphRepo()
        repo.conflicts_before_success = 99
        svc = TaskGraphService(repo)
        svc.initialize_graph(_task_info("t1"))
        with pytest.raises(GraphVersionConflictError):
            svc.update_task_node_info(_patch("t1", "t1", status=Status.RUNNING))
        assert repo.save_count == task_graph_support.MAX_GRAPH_VERSION_RETRIES
        # 缓存恢复为共享存储最后快照(节点 PENDING,无脏写)
        root = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "t1")
        assert root.status is Status.PENDING

    def test_retry_loop_assertion_guard(self, monkeypatch):
        monkeypatch.setattr(task_graph_support, "MAX_GRAPH_VERSION_RETRIES", 0)
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t9"))
        with pytest.raises(AssertionError, match="unreachable graph version retry loop"):
            svc.update_task_node_info(_patch("t9", "t9", status=Status.RUNNING))

    def test_save_exception_restores_cache_and_raises(self):
        repo = FakeGraphRepo()
        repo.save_exc = ValueError("db down")
        svc = TaskGraphService(repo)
        svc.initialize_graph(_task_info("t1"))
        with pytest.raises(ValueError, match="db down"):
            svc.update_task_node_info(_patch("t1", "t1", status=Status.RUNNING))
        # 失败写后缓存必须回到共享存储快照(仍 PENDING),不留脏图
        root = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "t1")
        assert root.status is Status.PENDING

    def test_pending_callback_audit_committed_and_cleared(self):
        from agentclaw.community.core.task.task_runner.callback_adapter import (
            _PENDING_CALLBACK_AUDIT,
        )
        repo = FakeGraphRepo()
        svc = TaskGraphService(repo)
        svc.initialize_graph(_task_info("t1"))
        record = SimpleNamespace(run_id="t1", event_id="evt-x")
        token = _PENDING_CALLBACK_AUDIT.set(record)
        try:
            svc.update_task_node_info(_patch("t1", "t1", status=Status.RUNNING))
            # 审计记录随同一事务移交仓储
            assert repo.last_callback_audit is record
            # 成功提交后 contextvar 清空
            assert _PENDING_CALLBACK_AUDIT.get() is None
        finally:
            _PENDING_CALLBACK_AUDIT.reset(token)


class TestActionEventAndOutbox:
    def test_append_action_event_seq_from_repo(self):
        repo = FakeGraphRepo()
        repo.action_seqs[("t1", "t1")] = 7
        svc = TaskGraphService(repo)
        svc.initialize_graph(_task_info("t1"))
        svc.append_action_event("t1", "t1", NodeAction.PLAN, {"k": "v"})
        root = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "t1")
        log = root.run_info.action_log
        assert len(log) == 1
        assert log[0].seq == 7
        assert log[0].action is NodeAction.PLAN
        assert log[0].payload["__node_id"] == "t1"

    def test_load_action_logs_via_repo(self):
        repo = FakeGraphRepo()
        marker = SimpleNamespace(seq=1)
        repo.action_log_groups = {"t1": [marker]}
        svc = TaskGraphService(repo)
        svc.initialize_graph(_task_info("t1"))
        graph = svc.query_task_dashboard("t1")
        svc.load_action_logs(graph)
        root = next(n for n in graph.tasks if n.node_id == "t1")
        assert root.run_info.action_log == [marker]
        repo.load_action_logs  # noqa: B018 - fake 保存 API 形状

    def test_emit_semantic_event_duplicate_is_idempotent(self):
        repo = FakeGraphRepo()
        svc = TaskGraphService(repo)
        svc.initialize_graph(_task_info("t1"))
        key1 = svc.emit_semantic_event("t1", "TASK_VIEW", event_id="e1")
        key2 = svc.emit_semantic_event("t1", "TASK_VIEW", event_id="e1")
        assert key1 == "e1" == key2
        # 去重命中 → 第二次不落库
        assert repo.create_count == 1
        assert repo.save_count == 1
        pending = svc.list_pending_semantic_events("t1")
        assert [item["event_id"] for item in pending] == ["e1"]

    def test_report_outbox_list_and_acknowledge(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        svc.report(TaskCallbackData(data={
            "report_type": "NODE_PATCH",
            "event_id": "evt-1",
            "payload": {"patch": _patch("t1", "t1", status=Status.RUNNING)},
        }))
        pending = svc.list_pending_report_events("t1")
        assert [item["event_id"] for item in pending] == ["evt-1"]
        assert pending[0]["event_type"] == "NODE_PATCH"
        assert svc.acknowledge_report_event("t1", "evt-1") is True
        assert svc.list_pending_report_events("t1") == []
        # 非 PENDING/不存在 → 不提交也不翻
        assert svc.acknowledge_report_event("t1", "evt-1") is False


class TestGraphServiceWriteGuards:
    def test_add_task_nodes_rejects_mixed_task_ids(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        with pytest.raises(GraphIntegrityError, match="同批 task_id 不一致"):
            svc.add_task_nodes([_child("c1", "t1"), _child("c2", "t2")], parent_node_id="t1")

    def test_add_relations_empty_returns_graph(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        graph = svc.add_relations("t1", [])
        assert graph.task_id == "t1"

    def test_add_relations_guards_and_dedup(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        n_rel = len(svc.query_task_dashboard("t1").relations)
        with pytest.raises(GraphIntegrityError, match="自环禁止"):
            svc.add_relations("t1", [("c1", "c1")])
        with pytest.raises(GraphIntegrityError, match="端点未入图"):
            svc.add_relations("t1", [("t1", "zzz")])
        # t1->c1 已存在 → 幂等跳过,不新增边
        svc.add_relations("t1", [("t1", "c1")])
        assert len(svc.query_task_dashboard("t1").relations) == n_rel
        # 新边正常追加(前向依赖)
        svc.add_relations("t1", [("c1", "t1")])
        assert len(svc.query_task_dashboard("t1").relations) == n_rel + 1

    def test_update_node_rejects_invalid_execution_decision(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        with pytest.raises(TaskStateError, match="ACCEPTED or DECLINED"):
            svc.update_task_node_info(_patch("t1", "t1", execution_decision="maybe"))
        # 校验失败不翻态
        root = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "t1")
        assert root.status is Status.PENDING

    def test_delete_task_node_guards_and_subtree_prune(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        svc.add_task_nodes([_child("c1"), _child("c2")], parent_node_id="t1")
        svc.add_task_nodes([_child("c11")], parent_node_id="c1")
        with pytest.raises(TaskStateError, match="根节点不可删"):
            svc.delete_task_node("t1", "t1")
        with pytest.raises(NodeNotFoundError):
            svc.delete_task_node("t1", "zzz")
        svc.delete_task_node("t1", "c1")
        remaining = {n.node_id for n in svc.query_task_dashboard("t1").tasks}
        assert remaining == {"t1", "c2"}
        srcs = {r.src_id for r in svc.query_task_dashboard("t1").relations}
        assert srcs == {"t1"}
        # 再删 c2 → 回到单根图
        svc.delete_task_node("t1", "c2")
        assert len(svc.query_task_dashboard("t1").tasks) == 1

    def test_delete_task_node_never_prunes_root_via_forward_edge(self):
        # 敌意图:前向 DEPENDENCY 边使 root 可达为 c1 的"结构子"→ 删 c1 时
        # root 必须被守卫跳过,永不出 prune 集
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        svc.add_relations("t1", [("c1", "t1")])
        svc.delete_task_node("t1", "c1")
        graph = svc.query_task_dashboard("t1")
        assert [n.node_id for n in graph.tasks] == ["t1"]
        assert graph.relations == []


class TestDispatchReportPayloadGuards:
    """task_graph_support._dispatch_report 的全部非法载荷分支。"""

    def _svc(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        return svc

    def test_payload_not_dict(self):
        with pytest.raises(TaskStateError, match="must be an object"):
            self._svc().report(TaskCallbackData(data="oops"))

    def test_node_patch_requires_patch_object(self):
        with pytest.raises(TaskStateError, match="NODE_PATCH report requires TaskNodePatch"):
            self._svc().report(TaskCallbackData(data={
                "report_type": "NODE_PATCH", "payload": {"patch": "not-a-patch"},
            }))

    def test_graph_patch_requires_task_id_and_patch(self):
        with pytest.raises(TaskStateError, match="GRAPH_PATCH report requires"):
            self._svc().report(TaskCallbackData(data={
                "report_type": "GRAPH_PATCH", "payload": {"task_id": "", "patch": TaskGraphPatch()},
            }))
        with pytest.raises(TaskStateError, match="GRAPH_PATCH report requires"):
            self._svc().report(TaskCallbackData(data={
                "report_type": "GRAPH_PATCH", "payload": {"task_id": "t1", "patch": "x"},
            }))

    def test_add_nodes_requires_node_list(self):
        with pytest.raises(TaskStateError, match="ADD_NODES report requires"):
            self._svc().report(TaskCallbackData(data={
                "report_type": "ADD_NODES", "payload": {"task_id": "t1", "nodes": "nope"},
            }))

    def test_add_relations_requires_edges(self):
        with pytest.raises(TaskStateError, match="ADD_RELATIONS report requires"):
            self._svc().report(TaskCallbackData(data={
                "report_type": "ADD_RELATIONS", "payload": {"task_id": "t1", "edges": "nope"},
            }))

    def test_relay_plan_result_requires_valid_payload(self):
        with pytest.raises(TaskStateError, match="RELAY_PLAN_RESULT report payload is invalid"):
            self._svc().report(TaskCallbackData(data={
                "report_type": "RELAY_PLAN_RESULT", "payload": {"task_id": "", "origin_node_id": "o", "holder_id": "h", "gaps": []},
            }))

    def test_bbs_attach_requires_valid_payload(self):
        with pytest.raises(TaskStateError, match="BBS_ATTACH report payload is invalid"):
            self._svc().report(TaskCallbackData(data={
                "report_type": "BBS_ATTACH",
                "payload": {"task_id": "t1", "parent_node_id": "t1", "bot_id": ""},
            }))

    def test_relay_turn_expire_requires_fields(self):
        with pytest.raises(TaskStateError, match="RELAY_TURN_EXPIRE report payload is invalid"):
            self._svc().report(TaskCallbackData(data={
                "report_type": "RELAY_TURN_EXPIRE",
                "payload": {"task_id": "t1", "node_id": "n", "holder_id": ""},
            }))

    def test_bbs_claim_requires_ids(self):
        with pytest.raises(TaskStateError, match="BBS_CLAIM report requires"):
            self._svc().report(TaskCallbackData(data={
                "report_type": "BBS_CLAIM", "payload": {"task_id": "t1", "bot_id": ""},
            }))

    def test_relay_turn_grant_requires_holder(self):
        with pytest.raises(TaskStateError, match="report requires task_id, node_id and holder_id"):
            self._svc().report(TaskCallbackData(data={
                "report_type": "RELAY_TURN_GRANT",
                "payload": {"task_id": "t1", "node_id": "n", "holder_id": ""},
            }))

    def test_unknown_report_type_rejected(self):
        with pytest.raises(TaskStateError, match="unsupported graph report_type=NOPE"):
            self._svc().report(TaskCallbackData(data={
                "report_type": "NOPE", "payload": {},
            }))


class TestStaleDashboardRehydrate:
    def test_query_rehydrates_when_version_moved(self):
        repo = FakeGraphRepo()
        svc = TaskGraphService(repo)
        svc.initialize_graph(_task_info("t1"))
        local_version = svc._graph_versions["t1"]

        # 另一实例推进共享存储(翻 RUNNING 并 bump 版本)
        def advance(snap):
            snap.tasks[0].status = Status.RUNNING
        repo.mutate_snapshot("t1", advance)

        stale = svc.query_task_dashboard("t1")
        assert stale.tasks[0].status is Status.RUNNING
        assert svc._graph_versions["t1"] == local_version + 1


class TestClaimBbsOwner:
    def _bbs_service(self) -> TaskGraphService:
        svc = TaskGraphService(FakeGraphRepo())
        svc.initialize_graph(_task_info("t1"))
        svc.update_task_graph_info(
            "t1", TaskGraphPatch(extend_props_patch={"bbs_mode": True})
        )
        return svc

    def test_node_claim_rejects_non_bbs_node(self):
        svc = self._bbs_service()
        svc.add_task_nodes([_child("plain", run_mode="single_bot")], parent_node_id="t1")
        with pytest.raises(TaskStateError, match="非可认领 BBS 节点"):
            svc.claim_bbs_owner("t1", "botX", node_id="plain")

    def test_node_claim_same_claim_id_is_noop_success(self):
        svc = self._bbs_service()
        svc.add_task_nodes([_child("b1")], parent_node_id="t1")
        svc.update_task_node_info(
            _patch("t1", "b1", run_mode="bbs",
                   extend_props_patch={"bbs_claim_id": "c9"})
        )
        result = svc.claim_bbs_owner("t1", "botX", node_id="b1", claim_id="c9")
        assert result.success is True
        node = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "b1")
        # 幂等路径:不翻态、不换主
        assert node.status is Status.PENDING
        assert node.run_info.extend_props.get("bbs_owner") is None

    def test_node_claim_conflicting_owner_raises(self):
        svc = self._bbs_service()
        svc.add_task_nodes([_child("b1")], parent_node_id="t1")
        svc.update_task_node_info(
            _patch("t1", "b1", run_mode="bbs", status=Status.PENDING,
                   extend_props_patch={"bbs_owner": "botA"})
        )
        with pytest.raises(TaskStateError, match="已被 botA 占有"):
            svc.claim_bbs_owner("t1", "botX", node_id="b1", claim_id="c8")

    def test_root_claim_db_cas_failure_raises(self):
        svc = self._bbs_service()
        svc._graph_repo.claim_cas = False
        with pytest.raises(TaskStateError, match="DB CAS 失败"):
            svc.claim_bbs_owner("t1", "botZ")

    def test_root_claim_db_cas_success_sets_owner_and_syncs_version(self):
        svc = self._bbs_service()
        repo = svc._graph_repo
        result = svc.claim_bbs_owner("t1", "botZ")
        assert result.success is True
        assert result.node_id == "t1"
        assert repo.claim_calls == ["botZ"]
        root = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "t1")
        assert root.run_info.extend_props["bbs_owner"] == "botZ"
        assert "bbs_claim_at" in root.run_info.extend_props
        assert svc._graph_versions["t1"] == repo.get_version("t1")


class TestRelayPlanResultFacts:
    def test_non_relay_task_rejected(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        with pytest.raises(TaskStateError, match="is not in relay mode"):
            svc.apply_relay_plan_result(
                task_id="t1", origin_node_id="t1", gaps=[], next_task_spec=None,
                holder_id="h", max_rounds=3,
            )

    def test_origin_not_running_rejected(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1", {"orchestration_mode": "relay"}))
        svc.update_task_node_info(_patch("t1", "t1", status=Status.RUNNING))
        svc.update_task_node_info(_patch("t1", "t1", status=Status.PENDING))
        with pytest.raises(TaskStateError, match="origin must be RUNNING"):
            svc.apply_relay_plan_result(
                task_id="t1", origin_node_id="t1", gaps=["g1"], next_task_spec=None,
                holder_id="h", max_rounds=3,
            )

    def test_empty_gaps_with_next_spec_rejected(self):
        spec = TaskSpec(context=Context(background="", title="n"),
                        goal=Goal(objective="x", acceptances=[]))
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1", {"orchestration_mode": "relay"}))
        svc.update_task_node_info(_patch("t1", "t1", status=Status.RUNNING))
        with pytest.raises(TaskStateError, match="cannot create next task"):
            svc.apply_relay_plan_result(
                task_id="t1", origin_node_id="t1", gaps=[], next_task_spec=spec,
                holder_id="h", max_rounds=3,
            )

    def test_gap_handoff_hung_at_max_rounds(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1", {"orchestration_mode": "relay"}))
        svc.update_task_node_info(_patch("t1", "t1", status=Status.RUNNING))
        svc.update_task_graph_info("t1", TaskGraphPatch(loop_round_increment=1))
        result = svc.apply_relay_plan_result(
            task_id="t1", origin_node_id="t1", gaps=["缺一环"], next_task_spec=None,
            holder_id="h", max_rounds=1,
        )
        assert result["hung"] is True
        graph = svc.query_task_dashboard("t1")
        root = next(n for n in graph.tasks if n.node_id == "t1")
        assert root.status is Status.HUNG
        assert graph.status is Status.HUNG
        assert graph.extend_props["hung_reason"] == "达到分布式接力迭代轮次上限"

    def test_completed_relay_aggregates_only_accepted_outputs(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1", {"orchestration_mode": "relay"}))
        svc.add_task_nodes(
            [_child("o1"), _child("acc1"), _child("rej1")], parent_node_id="t1",
            mark_parent_planning=False,
        )
        # origin 起跑
        svc.update_task_node_info(_patch("t1", "o1", status=Status.RUNNING,
                                         execution_decision="ACCEPTED"))
        # 已交接棒:验收通过 + 有产出 → 进聚合
        svc.update_task_node_info(_patch("t1", "acc1", status=Status.DONE,
                                         execution_decision="ACCEPTED"))
        svc.update_task_node_info(_patch("t1", "acc1", output_patch={"result": "R"}))
        # 被拒棒:非 ACCEPTED → 跳过
        svc.update_task_node_info(_patch("t1", "rej1", status=Status.DONE,
                                         execution_decision="DECLINED"))
        svc.update_task_node_info(_patch("t1", "rej1", output_patch={"result": "X"}))

        result = svc.apply_relay_plan_result(
            task_id="t1", origin_node_id="o1", gaps=[], next_task_spec=None,
            holder_id="h", max_rounds=3,
        )
        assert result["completed"] is True
        graph = svc.query_task_dashboard("t1")
        # ACCEPTED 空产出的 origin 不入聚合;DECLINED 跳过;唯一交付是 acc1
        assert graph.output == {"acc1": "R"}
        origin = next(n for n in graph.tasks if n.node_id == "o1")
        assert origin.status is Status.SUCCESS
        assert graph.status is Status.DONE


# ===========================================================================
# task_dispatch/dispatcher.py
# ===========================================================================

class TestExecRetryReplayGuard:
    def test_bbs_mode_retry_is_not_replayable(self):
        node = _child("c1", run_mode="bbs", assignee="bot")
        node.run_info.extend_props["harness_retries"] = 1
        assert _is_exec_retry_replay(node) is False

    def test_single_bot_retry_with_assignee_is_replayable(self):
        node = _child("c1", run_mode="single_bot", assignee="bot")
        node.run_info.extend_props["harness_retries"] = 1
        assert _is_exec_retry_replay(node) is True


class TestDispatchFailureCarriers:
    def _svc(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        return svc

    def test_assembly_error_written_to_failure_carrier(self):
        d = TaskDispatcher(self._svc(), pool=[
            _StubDispatchStrategy(SearchResult(
                outcome=SearchOutcome.HIT_SINGLE, bot_id="bkt",
                assembly_error="rationale_assembly_failed:oops",
            ))
        ])
        node = _run(d.dispatch([_child("c1")]))[0]
        # 派发决策不受影响
        assert node.run_info.assignee == "bkt"
        carrier = node.run_info.extend_props["_dispatch_failure"]
        assert carrier["error_type"] == "rationale_assembly_failed"
        assert "oops" in carrier["error_msg"]

    def test_unserializable_rationale_degrades_without_breaking_dispatch(self):
        class _HostileRationale:  # 非 dataclass → asdict 序列化抛错
            pass

        d = TaskDispatcher(self._svc(), pool=[
            _StubDispatchStrategy(SearchResult(
                outcome=SearchOutcome.HIT_SINGLE, bot_id="bkt",
                rationale=_HostileRationale(),
            ))
        ])
        node = _run(d.dispatch([_child("c1")]))[0]
        assert node.run_info.assignee == "bkt"
        assert "_dispatch_rationale" not in node.run_info.extend_props
        carrier = node.run_info.extend_props["_dispatch_failure"]
        assert carrier["error_type"] == "rationale_serialize_failed"


class TestSelectAndApply:
    def test_no_graph_falls_back_to_miss(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        d = TaskDispatcher(svc)
        result = _run(d._select_and_apply(_child("c1"), None))
        assert result.outcome is SearchOutcome.MISS
        assert result.miss_reason == "no_graph"

    def test_frozen_strategy_filter_selects_direct(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1", {"bot": "B1"}))
        svc.update_task_graph_info(
            "t1", TaskGraphPatch(extend_props_patch={
                "runtime_profile": {"dispatcher_strategy": "direct"},
            })
        )
        d = TaskDispatcher(svc)  # 内置默认池 [Direct, SearchBased]
        out = _run(d.dispatch([_child("c1")]))
        assert out[0].run_info.run_mode == "single_bot"
        assert out[0].run_info.assignee == "B1"

    def test_frozen_strategy_unknown_raises_no_strategy_hit_miss(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1", {"bot": "B1"}))
        # 未知冻结策略 → 组内 ValueError 被容错捕获 → dispatch_error
        d_bogus = TaskDispatcher(svc)
        svc.update_task_graph_info(
            "t1", TaskGraphPatch(extend_props_patch={
                "runtime_profile": {"dispatcher_strategy": "bogus"},
            })
        )
        node = _run(d_bogus.dispatch([_child("c1")]))[0]
        assert node.run_info.extend_props["dispatch_error"] == "dispatch_exception:ValueError"
        assert node.status is Status.PENDING

    def test_all_strategies_unmatched_yields_no_strategy_miss(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        d = TaskDispatcher(svc, pool=[_NeverMatchStrategy()])
        out = _run(d.dispatch([_child("c1")]))
        assert out[0].run_info.extend_props["miss_events"] == ["no_strategy"]


class TestEngineDispatchRequested:
    def _relay_engine(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1", {"orchestration_mode": "relay"}))
        return _make_engine(svc), svc

    def test_dispatch_requested_external_managed_no_op(self):
        engine, svc = self._relay_engine()
        _run(engine.dispatch_requested("t1"))
        # relay 托管:不派发、不产 side effect,图保持根 PENDING
        root = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "t1")
        assert root.status is Status.PENDING

    def test_prepare_into_skips_external_managed_task(self):
        engine, svc = self._relay_engine()
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        side: list[tuple] = []
        _run(engine._prepare_into("t1", side))
        assert side == []
        node = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "c1")
        # 不预填 start_time、不翻态
        assert node.status is Status.PENDING
        assert node.run_info.start_time is None


class TestPrepareStaticCascadeGuard:
    def test_cascade_hits_max_rounds_when_runtime_never_settles(self, caplog):
        from agentclaw.community.core.task.task_plan.static_plan import (
            StaticPlanDefinition,
            StaticPlanNodeDefinition,
        )

        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        svc.add_task_nodes([_child("spin")], parent_node_id="t1")
        engine = _make_engine(svc)

        spin_node = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "spin")
        definition = StaticPlanDefinition(
            template_id="evolving", entry_bot_id=None, input_schema={},
            nodes=(StaticPlanNodeDefinition(
                node_id="spin", name="spin", node_type="bot", depends_on=(),
                bot_id=None, bot_ids=(), input={}, output={},
            ),),
        )

        class _NeverSettledRuntime:
            """敌意 runtime:每轮都返回同一个可 skip 的就绪节点,永不达稳态。"""

            def __init__(self, node):
                self.by_id = {}
                self.node = node
                self.definition = definition

            def ready(self, graph):
                # 每轮返回新鲜 PENDING 同名节点(模拟永远解不开的依赖环)
                self.node.status = Status.PENDING
                return StaticPlanReadiness(skipped=(self.node,))

        runtime = _NeverSettledRuntime(spin_node)
        with caplog.at_level(logging.WARNING, logger="task.dispatcher"):
            _run(engine._prepare_static("t1", runtime, []))
        assert any("cascade hit max_rounds" in rec.message for rec in caplog.records)
        # skip 节点至少被翻过一次 DONE(每轮 skip 事实已写)
        assert spin_node.status is Status.DONE


class TestPrepareStaticIntoBranches:
    def _static_env(self, plan_yaml: str = COVER_PLAN, execution_config: dict | None = None):
        svc = TaskGraphService()
        # notify/bbs_handoff 等静态节点模态需在冻结 runtime profile 白名单内
        cfg = {"allowed_run_modes": ["single_bot", "coop_group", "bbs", "notify"]}
        cfg.update(execution_config or {})
        svc.initialize_graph(_task_info("t1", cfg))
        definition = StaticPlanDefinition.from_yaml(plan_yaml)
        runtime = StaticPlanRuntime(definition, {})
        engine = _make_engine(svc)
        return svc, runtime, engine

    def _graph_node(self, svc, node_id):
        return next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == node_id)

    def test_unknown_definition_node_is_skipped(self):
        svc, runtime, engine = self._static_env()
        side: list[tuple] = []
        _run(engine._prepare_static_into("t1", runtime, [_child("ghost")], side))
        assert side == []

    def test_notify_node_with_provider_sends_and_closes(self):
        class _NotifyOk:
            def __init__(self):
                self.sent = []

            def send(self, message, channel=None):
                self.sent.append((message, channel))
                return "ext-123"

        provider = _NotifyOk()
        svc, runtime, engine = self._static_env(
            execution_config={"owner_account_id": "acct-9"},
        )
        svc.add_task_nodes([_child("note")], parent_node_id="t1")
        provider_env = _make_engine(svc, notify_provider=provider)
        note = self._graph_node(svc, "note")
        side: list[tuple] = []
        _run(provider_env._prepare_static_into("t1", runtime, [note], side))
        assert len(provider.sent) == 1
        message, channel = provider.sent[0]
        assert channel == "tc_card"
        assert message.recipient == "acct-9"
        assert "completed" in message.body or "OKR" in message.title
        refreshed = self._graph_node(svc, "note")
        assert refreshed.status is Status.DONE
        assert refreshed.run_info.run_mode == "notify"
        assert refreshed.run_info.assignee == "dingtalk:acct-9"
        # side 无 run/group(通知不派 bot)
        assert side == []

    def test_notify_node_provider_failure_still_closes_node(self):
        class _NotifyBoom:
            def send(self, message, channel=None):
                raise RuntimeError("dingtalk down")

        svc, runtime, _ = self._static_env()
        svc.add_task_nodes([_child("note")], parent_node_id="t1")
        engine = _make_engine(svc, notify_provider=_NotifyBoom())
        note = self._graph_node(svc, "note")
        _run(engine._prepare_static_into("t1", runtime, [note], []))
        refreshed = self._graph_node(svc, "note")
        assert refreshed.status is Status.DONE
        result = refreshed.run_info.output["notify_result"]
        assert result["sent"] is False
        assert "RuntimeError: dingtalk down" in result["error"]

    def test_notify_node_without_provider_defaults_recipient(self):
        svc, runtime, engine = self._static_env()
        svc.add_task_nodes([_child("note")], parent_node_id="t1")
        note = self._graph_node(svc, "note")
        # 未注入 provider(NullProvider 降级)→ 仍 DONE,缺省收件人
        _run(engine._prepare_static_into("t1", runtime, [note], []))
        refreshed = self._graph_node(svc, "note")
        assert refreshed.status is Status.DONE
        result = refreshed.run_info.output["notify_result"]
        assert result["recipient"] == "guoke.gk"
        assert result["sent"] is False
        assert result["error"] is None

    def test_bbs_handoff_with_structured_items_uses_them(self):
        svc, runtime, engine = self._static_env()
        items = ["复杂任务A", "复杂任务B"]
        svc.add_task_nodes(
            [_child("handoff", static_input={"unhandled_tasks": items})],
            parent_node_id="t1",
        )
        handoff = self._graph_node(svc, "handoff")
        side: list[tuple] = []
        _run(engine._prepare_static_into("t1", runtime, [handoff], side))
        assert side == [("bbs_handoff", handoff, "safety-bot", items)]
        refreshed = self._graph_node(svc, "handoff")
        assert refreshed.run_info.run_mode == "bbs"
        assert refreshed.run_info.assignee == "safety-bot"
        assert refreshed.run_info.extend_props["bbs_status"] == "posted_in_square"
        assert refreshed.run_info.extend_props["bbs_task_items"] == items
        assert refreshed.run_info.extend_props["dispatching"] is True

    def test_bbs_handoff_without_items_falls_back_to_mock(self):
        from agentclaw.community.core.task.task_dispatch.dispatcher import _UHT_MOCK

        svc, runtime, engine = self._static_env()
        svc.add_task_nodes([_child("handoff", static_input={})], parent_node_id="t1")
        handoff = self._graph_node(svc, "handoff")
        side: list[tuple] = []
        _run(engine._prepare_static_into("t1", runtime, [handoff], side))
        kind, node, bot_id, items = side[0]
        assert (kind, node, bot_id) == ("bbs_handoff", handoff, "safety-bot")
        assert items == list(_UHT_MOCK)
        assert handoff.run_info.extend_props["bbs_task_items"] == list(_UHT_MOCK)

    def test_bot_node_without_binding_is_skipped(self, caplog):
        svc, runtime, engine = self._static_env()
        svc.add_task_nodes([_child("naked")], parent_node_id="t1")
        naked = self._graph_node(svc, "naked")
        side: list[tuple] = []
        with caplog.at_level(logging.WARNING, logger="task.dispatcher"):
            _run(engine._prepare_static_into("t1", runtime, [naked], side))
        assert side == []
        assert any("无 bot 绑定" in rec.message for rec in caplog.records)
        assert naked.status is Status.PENDING


# ===========================================================================
# task_dispatch/strategies.py / search.py / claim_join_gate.py
# ===========================================================================

class TestDispatchStrategies:
    def test_direct_strategy_returns_group_formation_when_pending(self):
        gf = GroupFormation(bot_ids=["b1", "b2"], collab_mode="chat")
        node = _child("c1")
        node.run_info.extend_props["pending_group_formation"] = gf
        sr = _run(DirectDispatchStrategy().apply(node, _bare_graph({"execution_config": {}})))
        assert sr.outcome is SearchOutcome.HIT_MULTI_BOTS
        assert sr.group_formation is gf
        assert sr.rationale is not None
        assert sr.rationale.strategy_name == "direct"

    def test_search_strategy_stub_path_misses_with_rationale(self):
        node = _child("c1")
        sr = _run(SearchBasedDispatchStrategy().apply(
            node, _bare_graph({"execution_config": {}})
        ))
        assert sr.outcome is SearchOutcome.MISS
        assert sr.miss_reason == "no_port_stub"
        assert sr.rationale is not None

    def test_search_strategy_missing_owner_misses(self):
        node = _child("c1")
        strategy = SearchBasedDispatchStrategy(bot=object(), discover=_DiscoverOne())
        sr = _run(strategy.apply(node, _bare_graph()))  # 无 owner_bot_id
        assert sr.outcome is SearchOutcome.MISS
        assert sr.miss_reason == "no_owner"

    def test_prefetch_candidates_none_discover_empty_and_real_delegates(self):
        node = _child("c1")
        node.task_spec.goal.objective = "存储行业分析"  # 分词产生 ≥2 字 token
        graph = _bare_graph({"owner_bot_id": "owner"})
        assert _run(prefetch_candidates(None, node, graph)) == []
        cands = _run(prefetch_candidates(_DiscoverOne(), node, graph))
        assert [c.get("bot_id") for c in cands] == ["cand"]

    def test_candidate_dispatch_ids_skip_non_dict_and_compose_owner(self):
        ids = _candidate_dispatch_ids([
            {"bot_uuid": "b1:1"},
            "junk",
            {"bot_id": "b2", "owner_id": "o2"},
            {"bot_uuid": "b1:1"},  # 去重
        ])
        assert ids == ["b1:1", "b2:o2"]


class TestParseSearchResult:
    DONE_RUN = {"status": "COMPLETED", "result": {"content": '{"outcome":"MISS"}'}}

    def test_non_completed_status_misses(self):
        sr = _parse_search_result({"status": "RUNNING", "result": {"content": "{}"}})
        assert sr.outcome is SearchOutcome.MISS
        assert sr.miss_reason == "run_status_RUNNING"

    def test_empty_content_misses(self):
        sr = _parse_search_result({"status": "COMPLETED"})
        assert sr.miss_reason == "empty_content"

    def test_non_object_payload_misses(self):
        sr = _parse_search_result({
            "status": "COMPLETED", "result": {"content": "[1, 2]"},
        })
        assert sr.miss_reason == "not_object"

    def test_hit_group_returns_group_id(self):
        sr = _parse_search_result({
            "status": "COMPLETED",
            "result": {"content": '{"outcome":"HIT_GROUP","group_id":"g9"}'},
        })
        assert sr.outcome is SearchOutcome.HIT_GROUP
        assert sr.group_id == "g9"

    def test_hit_multi_bots_without_ids_misses(self):
        sr = _parse_search_result({
            "status": "COMPLETED",
            "result": {"content": '{"outcome":"HIT_MULTI_BOTS","bot_ids":[]}'},
        })
        assert sr.outcome is SearchOutcome.MISS
        assert sr.miss_reason == "hit_multi_no_bot_ids"


class TestTaskSearchFacade:
    def test_search_requires_query(self):
        searcher = TaskSearch(None)
        with pytest.raises(ValueError, match="search query is required"):
            _run(searcher.search(""))

    def test_search_without_discover_returns_empty_result(self):
        result = _run(TaskSearch(None).search("存储行业"))
        assert result.candidates == []
        assert list(result.tokens) != []

    def test_search_catalog_empty_query_rejected(self):
        with pytest.raises(TaskStateError, match="search query is required"):
            _run(TaskSearch(None).search_catalog(""))

    def test_search_catalog_without_backend_returns_empty_page(self):
        payload = _run(TaskSearch(None).search_catalog("存储行业"))
        assert payload == {"candidates": [], "total": 0}


class TestClaimJoinGate:
    @staticmethod
    def _store_with(value):
        class _Store:
            def get_config(self, *, category, config_key, env):
                return value
        return _Store()

    def test_unsupported_setting_type_raises_value_error(self):
        svc = TaskSettingsService()
        with pytest.raises(ValueError, match="unsupported task setting type"):
            svc.is_enabled("bogus")
        with pytest.raises(ValueError, match="unsupported task setting type"):
            svc.get_enabled(setting_type="bogus", env="prod")

    def test_string_literals_coerced_to_bool(self):
        on = TaskSettingsService(config=self._store_with("yes"))
        assert on.get_enabled(setting_type="search_skill", env="prod") is True
        off = TaskSettingsService(config=self._store_with("off"))
        assert off.get_enabled(setting_type="search_skill", env="prod") is False

    def test_gate_is_enabled_relays_to_settings(self):
        class _Settings:
            def __init__(self):
                self.calls = []

            def is_enabled(self, setting_type):
                self.calls.append(setting_type)
                return False

            def get_enabled(self, *, setting_type, env):
                return False

            def set_enabled(self, *, setting_type, enabled, env, operator=None):
                return True

        settings = _Settings()
        gate = TaskClaimJoinGate(settings=settings)
        assert gate.is_enabled() is False
        assert settings.calls == ["claim_join_filter"]
        assert gate.get_enabled(env="prod") is False
        assert gate.set_enabled(enabled=True, env="prod") is True


# ===========================================================================
# task_plan/planner.py
# ===========================================================================

class TestPlannerFrozenStrategy:
    def test_profile_with_existing_strategy_filters_pool(self):
        graph = _bare_graph({
            "execution_config": {"workflow": ["wA", "wB"]},
            "runtime_profile": {"planner_strategy": "workflow"},
        })
        graph.tasks = [_child("t1", "t1")]
        graph.tasks[0].status = Status.PENDING
        planner = TaskPlanner(TaskGraphService())
        pr = _run(planner.plan(graph))
        assert {n.node_id for n in pr.children} == {"wA", "wB"}
        assert pr.gap_detail == "workflow"

    def test_profile_with_unknown_strategy_raises(self):
        graph = _bare_graph({
            "execution_config": {},
            "runtime_profile": {"planner_strategy": "bogus"},
        })
        graph.tasks = [_child("t1", "t1")]
        graph.tasks[0].status = Status.PENDING
        planner = TaskPlanner(TaskGraphService())
        with pytest.raises(ValueError, match="unknown frozen planner_strategy='bogus'"):
            _run(planner.plan(graph))

    def test_no_strategy_hit_returns_fallback_result(self):
        graph = _bare_graph({"execution_config": {}})
        graph.tasks = [_child("t1", "t1")]
        graph.tasks[0].status = Status.PENDING
        planner = TaskPlanner(TaskGraphService())
        planner.set_strategies([_NeverMatchStrategy()])
        pr = _run(planner.plan(graph))
        assert pr.children == []
        assert pr.has_gap is False
        assert pr.gap_detail == "no_strategy_hit"

    def test_explicit_missing_target_resolves_none(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        graph = svc.query_task_dashboard("t1")
        planner = TaskPlanner(svc)
        pr = _run(planner.plan(graph, target_node_id="ghost"))
        assert pr.children == []
        assert pr.gap_detail == "no_target"

    def test_root_discovery_rejects_nodes_with_structural_parent(self):
        # t1 有子(child)→ 跳过;c1 有结构父 → _get_parent_id 命中返回 t1 → 不选 → no_target
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        svc.add_task_nodes([_child("c1")], parent_node_id="t1")
        planner = TaskPlanner(svc)
        pr = _run(planner.plan(svc.query_task_dashboard("t1")))
        assert pr.gap_detail == "no_target"

    def test_parent_acceptance_result_fallback_without_acceptances(self):
        parent = TaskNode(
            node_id="p1", task_id="t1", status=Status.PLANNING,
            task_spec=TaskSpec(context=Context(background="bg", title="t"),
                               goal=Goal(objective="o", acceptances=[])),
            run_info=RuntimeInfo(), node_run_graph=None,  # type: ignore[arg-type]
        )
        ar = TaskPlanner(TaskGraphService())._build_parent_acceptance_result(parent, None)
        assert ar.verdict is AcceptanceVerdict.DONE
        assert ar.done_items == [{"all": "验收通过"}]
        assert ar.gap_items == []


class TestPlannerEngineEvents:
    def _svc(self, execution_config=None):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1", execution_config or {}))
        return svc

    def test_plan_requested_external_managed_returns_empty(self):
        engine = _make_engine(self._svc({"orchestration_mode": "relay"}))
        assert _run(engine.plan_requested("t1")) == []

    def test_plan_requested_non_pending_root_returns_empty(self):
        svc = self._svc()
        svc.update_task_node_info(
            _patch("t1", "t1", status=Status.RUNNING, run_mode="single_bot", assignee="b1")
        )
        engine = _make_engine(svc)
        assert _run(engine.plan_requested("t1")) == []

    def test_on_execute_frozen_on_terminal_graph(self, caplog):
        svc = self._svc()
        svc.update_task_graph_info("t1", TaskGraphPatch(status=Status.HUNG))
        engine = _make_engine(svc, planner=None)
        with caplog.at_level(logging.INFO, logger="task.planner"):
            _run(engine.on_execute("t1"))
        assert any("图已终态" in rec.message for rec in caplog.records)
        assert len(svc.query_task_dashboard("t1").tasks) == 1

    def test_on_pass_collect_propagates_hung_sibling_to_parent(self):
        # t1 ── p ── l_done(SUCCESS), l_hung(HUNG);t1 ── p_other(RUNNING)
        svc = self._svc()
        svc.add_task_nodes([_child("p"), _child("p_other")], parent_node_id="t1")
        svc.add_task_nodes([_child("l_done"), _child("l_hung")], parent_node_id="p")
        for node_id, status in (("l_done", Status.SUCCESS), ("l_hung", Status.HUNG),
                                ("p_other", Status.RUNNING)):
            svc.update_task_node_info(_patch("t1", node_id, status=status))
        engine = _make_engine(svc)
        _run(engine._on_pass_collect("t1", "l_done", []))
        p = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "p")
        # 兄弟全终态且含 HUNG → 父 HUNG(child_hung);根侧被 RUNNING 兄弟拦住不再上冲
        assert p.status is Status.HUNG
        assert p.run_info.extend_props["hung_reason"] == "child_hung"

    def test_on_pass_collect_gap_closed_flips_parent_with_existing_run_mode(self):
        # t1 ── p(single_bot, 已有 run_mode)── l_done(SUCCESS)
        svc = self._svc()
        svc.add_task_nodes([_child("p")], parent_node_id="t1")
        svc.update_task_node_info(
            _patch("t1", "p", run_mode="single_bot", assignee="botp")
        )
        svc.add_task_nodes([_child("l_done")], parent_node_id="p")
        svc.update_task_node_info(_patch("t1", "l_done", status=Status.SUCCESS))
        engine = _make_engine(svc, planner=_DonePlanningStrategy())
        _run(engine._on_pass_collect("t1", "l_done", []))
        # p 已有执行者信息 → gap 闭直接翻父 SUCCESS(不重落验收复杂 run_info)
        p = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "p")
        assert p.status is Status.SUCCESS
        # 递归收口:根 gap 闭 → t1 SUCCESS + 图终态镜像 + all_done 产出
        t1 = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "t1")
        assert t1.status is Status.SUCCESS
        graph = svc.query_task_dashboard("t1")
        assert graph.status is Status.SUCCESS
        assert graph.output.get("result") == "all_done"

    def test_on_fail_collect_is_fold_only_noop(self):
        svc = self._svc()
        engine = _make_engine(svc)
        assert _run(engine._on_fail_collect("t1", "t1", [])) is None
        root = next(n for n in svc.query_task_dashboard("t1").tasks if n.node_id == "t1")
        assert root.status is Status.PENDING  # 不翻态、不安排重试


class TestPlannerStaticEvents:
    def _static_svc(self, task_id, plan_yaml, config=None):
        svc = TaskGraphService()
        cfg = {"task_type": "static_plan", "static_plan_yaml": plan_yaml,
               "template_input": {"okr": "增长"}}
        cfg.update(config or {})
        svc.initialize_graph(_task_info(task_id, cfg))
        return svc

    def test_on_static_execute_without_runtime_returns(self):
        plain = TaskGraphService()
        plain.initialize_graph(_task_info("t-dyn"))
        engine = _make_engine(plain)
        _run(engine._on_static_execute("t-dyn"))
        assert len(plain.query_task_dashboard("t-dyn").tasks) == 1

    def test_on_static_execute_already_materialized_skips(self):
        svc = self._static_svc("e1", ONE_BOT_PLAN)
        runner = _FakeRunner()
        engine = _make_engine(svc, runner=runner)
        _run(engine.on_execute("e1"))
        assert runner.started == [["onlybot"]]
        graph_after_first = svc.query_task_dashboard("e1")
        assert len(graph_after_first.relations) == 1
        _run(engine._on_static_execute("e1"))
        assert runner.started == [["onlybot"]]  # 二次执行直接早退,不重复入图/派发
        assert len(svc.query_task_dashboard("e1").tasks) == len(graph_after_first.tasks)

    def test_on_static_execute_without_wave0_nodes_returns(self, caplog):
        svc = self._static_svc("e0", EMPTY_PLAN)
        runner = _FakeRunner()
        engine = _make_engine(svc, runner=runner)
        with caplog.at_level(logging.INFO, logger="task.planner"):
            _run(engine._on_static_execute("e0"))
        assert any("no wave0 nodes" in rec.message for rec in caplog.records)
        assert runner.started == []
        assert len(svc.query_task_dashboard("e0").tasks) == 1

    def test_static_next_wave_without_root_aborts(self):
        from agentclaw.community.core.task.task_plan.static_plan import (
            StaticPlanDefinition,
            StaticPlanNodeDefinition,
        )

        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t-wave"))
        svc.update_task_node_info(_patch("t-wave", "t-wave", status=Status.SUCCESS))
        definition = StaticPlanDefinition(
            template_id="wave", entry_bot_id=None, input_schema={},
            nodes=(StaticPlanNodeDefinition(
                node_id="follow", name="follow", node_type="bot",
                depends_on=("t-wave",), bot_id="b", bot_ids=(), input={}, output={},
            ),),
        )
        runtime = StaticPlanRuntime(definition, {})
        engine = _make_engine(svc)
        # 敌意:图存储层宣称 root 缺失(防御分支)→ 终止揭示
        engine._root = lambda task_id: None  # type: ignore[method-assign]
        assert engine._static_next_wave("t-wave", runtime) == 0

    def test_on_static_report_without_runtime_returns(self):
        plain = TaskGraphService()
        plain.initialize_graph(_task_info("t-dyn"))
        engine = _make_engine(plain)
        _run(engine._on_static_report("t-dyn", "t-dyn"))
        root = next(n for n in plain.query_task_dashboard("t-dyn").tasks
                    if n.node_id == "t-dyn")
        assert root.status is Status.PENDING

    def test_on_static_report_swallows_root_flip_failure(self, caplog):
        svc = self._static_svc("e2", ONE_BOT_PLAN)
        engine = _make_engine(svc, runner=_FakeRunner())
        _run(engine.on_execute("e2"))

        # 敌意:终态 root 翻 SUCCESS 被置为非法(模拟图写口抛错)
        orig = engine._report_node_patch

        def hostile_flip(patch):
            if patch.node_id == "e2" and patch.status is Status.SUCCESS:
                raise TaskStateError("simulated illegal root transition")
            return orig(patch)

        engine._report_node_patch = hostile_flip  # type: ignore[method-assign]
        with caplog.at_level(logging.WARNING, logger="task.planner"):
            # 回投收口:异常被吞,流程不崩
            _run(engine.on_report(TaskNodePatch(
                task_id="e2", node_id="onlybot",
                acceptance_result=AcceptanceResult(verdict=AcceptanceVerdict.DONE),
            )))
        assert any("flip-to-DONE skipped" in rec.message for rec in caplog.records)
        root = next(n for n in svc.query_task_dashboard("e2").tasks if n.node_id == "e2")
        assert root.status is Status.PLANNING  # 翻转失败保留原态


# ===========================================================================
# task_plan/strategies.py(规划策略 + _parse_plan_result)
# ===========================================================================

class TestWorkflowPlanningStrategy:
    def test_empty_workflow_returns_done(self):
        graph = _bare_graph({"execution_config": {"workflow": []}})
        pr = _run(WorkflowPlanningStrategy().apply(graph, _child("t1", "t1")))
        assert pr.children == []
        assert pr.has_gap is False
        assert pr.gap_detail == "done"

    def test_dict_workflow_branch_keys_children_by_target(self):
        graph = _bare_graph({
            "execution_config": {"workflow": {"t1": ["wA", "wB"],
                                              "other": ["wX"]}},
        })
        pr = _run(WorkflowPlanningStrategy().apply(graph, _child("t1", "t1")))
        assert {n.node_id for n in pr.children} == {"wA", "wB"}
        assert pr.has_gap is True

    def test_scalar_workflow_shape_falls_back_to_done(self):
        graph = _bare_graph({
            "execution_config": {"workflow": "freeform-not-listed"},
        })
        pr = _run(WorkflowPlanningStrategy().apply(graph, _child("t1", "t1")))
        assert pr.children == []
        assert pr.gap_detail == "done"

    def test_matches_requires_workflow_signal(self):
        assert _run(WorkflowPlanningStrategy().matches(
            _bare_graph({"execution_config": {"workflow": None}})
        )) is False


class TestGapBasedPlanningStrategy:
    def _target(self):
        return _child("t1", "t1")

    def test_no_owner_bot_returns_ungapable_result(self):
        strategy = GapBasedPlanningStrategy(bot=_BotP(self.DONE_RUN))
        pr = _run(strategy.apply(_bare_graph(), self._target()))
        assert pr.children == []
        assert pr.has_gap is True
        assert pr.gap_detail == "no_owner_bot"

    DONE_RUN = {
        "status": "COMPLETED",
        "result": {"content": '{"tasks": [], "has_gap": false, "gap_detail": "done"}'},
    }

    def test_plain_string_response_digest_computed(self):
        run = {"status": "COMPLETED", "result": "直接纯文本非 JSON"}
        strategy = GapBasedPlanningStrategy(bot=_BotP(run))
        pr = _run(strategy.apply(_bare_graph({"owner_bot_id": "owner"}), self._target()))
        # 解析失败 → 有 gap 拆不出;digest 仍按原始响应计算成功
        assert pr.gap_detail == "plan_parse_fail"
        assert pr.prompt_digest is not None
        assert pr.raw_response_digest is not None
        assert pr.strategy_name == "gap_based"

    def test_flaky_transport_response_digest_falls_back_to_none(self):
        run = _FlakyResultRun({
            "status": "COMPLETED",
            "result": {"content": '{"tasks": [], "has_gap": false, "gap_detail": "done"}'},
        })
        strategy = GapBasedPlanningStrategy(bot=_BotP(run))
        pr = _run(strategy.apply(_bare_graph({"owner_bot_id": "owner"}), self._target()))
        # 前 get("result") 正常 → 解析成功;第三次(get digest 分支)抛错 → 吞掉,digest=None
        assert pr.has_gap is False
        assert pr.children == []
        assert pr.prompt_digest is None
        assert pr.raw_response_digest is None
        assert pr.strategy_name == "gap_based"

    def test_stub_without_bot_returns_ungapable_result(self):
        pr = _run(GapBasedPlanningStrategy().apply(_bare_graph(), self._target()))
        assert pr.gap_detail == "no_planning_port"


class TestParsePlanResult:
    def _graph_with_target(self):
        target = _child("t1", "t1")
        graph = _bare_graph({})
        graph.task_id = "t1"
        graph.tasks = [target]
        graph.relations = [Relation(src_id="t1", dst_id="t1")]
        graph.relations = []  # 无边
        return target, graph

    def test_not_completed_status(self):
        target, graph = self._graph_with_target()
        pr = _parse_plan_result({"status": "FAILED", "result": {"content": "{}"}},
                                target, graph)
        assert pr.gap_detail == "plan_not_completed"
        assert pr.has_gap is True

    def test_empty_content(self):
        target, graph = self._graph_with_target()
        pr = _parse_plan_result({"status": "COMPLETED"}, target, graph)
        assert pr.gap_detail == "plan_empty_content"

    def test_dict_with_non_list_tasks_normalized_empty(self):
        target, graph = self._graph_with_target()
        pr = _parse_plan_result({
            "status": "COMPLETED",
            "result": {"content": '{"tasks": "nope", "has_gap": true}'},
        }, target, graph)
        assert pr.children == []
        assert pr.has_gap is True

    def test_scalar_payload_shape_unexpected(self):
        target, graph = self._graph_with_target()
        pr = _parse_plan_result({
            "status": "COMPLETED",
            "result": {"content": '"7"'},
        }, target, graph)
        assert pr.gap_detail == "plan_shape_unexpected"

    def test_non_dict_children_skipped(self):
        target, graph = self._graph_with_target()
        pr = _parse_plan_result({
            "status": "COMPLETED",
            "result": {"content": '[{"node_id": "n1"}, "junk", 42]'},
        }, target, graph)
        assert [c.node_id for c in pr.children] == ["n1"]

    def test_duplicate_node_ids_deduped(self):
        target, graph = self._graph_with_target()
        pr = _parse_plan_result({
            "status": "COMPLETED",
            "result": {"content": '[{"node_id": "t1"}, {"node_id": "d1"}, {"node_id": "d1"}]'},
        }, target, graph)
        # t1 已在图内 → 跳过;d1 图内重复 → 只收一个
        assert [c.node_id for c in pr.children] == ["d1"]

    def test_invalid_acceptance_verdict_degrades_to_none(self):
        target, graph = self._graph_with_target()
        pr = _parse_plan_result({
            "status": "COMPLETED",
            "result": {"content": (
                '{"tasks": [], "has_gap": false, "gap_detail": "done",'
                ' "acceptance_result": {"verdict": "BOGUS", "done_items": [], "gap_items": []}}'
            )},
        }, target, graph)
        assert pr.acceptance_result is None

    def test_valid_acceptance_result_carried(self):
        target, graph = self._graph_with_target()
        pr = _parse_plan_result({
            "status": "COMPLETED",
            "result": {"content": (
                '{"tasks": [], "has_gap": false, "gap_detail": "done",'
                ' "acceptance_result": {"verdict": "DONE", "done_items": [{"id": "ac1"}], "gap_items": []}}'
            )},
        }, target, graph)
        assert pr.acceptance_result is not None
        assert pr.acceptance_result.verdict is AcceptanceVerdict.DONE

    def test_build_child_task_spec_non_dict_returns_none(self):
        assert _build_child_task_spec("junk", self._graph_with_target()[0]) is None


# ===========================================================================
# task_plan/static_plan.py
# ===========================================================================

class TestStaticPlanParsing:
    def _parse(self, *lines):
        return StaticPlanDefinition.from_yaml("template_id: p\n" + "\n".join(lines))

    def test_node_requires_id(self):
        with pytest.raises(ValueError, match="requires id"):
            StaticPlanDefinition.from_yaml("template_id: p\nnodes:\n  - name: anon\n")

    def test_duplicate_node_ids_rejected(self):
        with pytest.raises(ValueError, match="duplicate static plan node"):
            self._parse("nodes:\n  - id: a\n  - id: a\n")

    def test_depends_on_unknown_node_rejected(self):
        with pytest.raises(ValueError, match="depends on unknown nodes"):
            self._parse(
                "nodes:",
                "  - id: a",
                "    depends_on: [ghost]",
            )

    def test_missing_template_id_rejected(self):
        with pytest.raises(ValueError, match="template_id"):
            StaticPlanDefinition.from_yaml("nodes: []\n")

    def test_from_file_missing_and_mismatch(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            StaticPlanDefinition.from_file("wanted", tmp_path)
        (tmp_path / "wanted.yaml").write_text(
            "template_id: other\nnodes: []\n", encoding="utf-8")
        with pytest.raises(ValueError, match="template_id mismatch"):
            StaticPlanDefinition.from_file("wanted", tmp_path)
        (tmp_path / "good.yaml").write_text(
            "template_id: good\nnodes:\n  - id: a\n    bot_id: b\n", encoding="utf-8")
        assert StaticPlanDefinition.from_file("good", tmp_path).template_id == "good"

    def test_validate_input_type_must_be_string(self):
        definition = StaticPlanDefinition.from_yaml(
            "template_id: p\n"
            "input_schema:\n"
            "  okr:\n"
            "    type: string\n"
            "nodes: []\n"
        )
        definition.validate_input({"okr": "text"})  # 合法通过
        with pytest.raises(ValueError, match="must be string"):
            definition.validate_input({"okr": 123})


class TestStaticPlanRuntimeRelayRender:
    def _runtime(self):
        definition = StaticPlanDefinition.from_yaml(RELAY_V2_PLAN)
        return StaticPlanRuntime(definition, {"okr": "增长"})

    def test_collab_upstream_identity_renders_driver(self):
        runtime = self._runtime()
        definition = runtime.by_id["mid_bot"]
        prompt = runtime._relay_instruction(definition, {"body": "上游正文"}, _bare_graph())
        assert "# 接自:upstream_collab(driver driver-bot)" in prompt
        assert prompt.count("## 本角色任务") == 1
        assert prompt.rstrip().endswith("中位 bot 角色任务")

    def test_bot_upstream_identity_renders_bot_id(self):
        runtime = self._runtime()
        definition = runtime.by_id["down_collab"]
        prompt = runtime._relay_instruction(definition, {"summary": "中位产出"}, _bare_graph())
        assert "# 接自:mid_bot(mid-bot)" in prompt

    def test_unknown_upstream_falls_back_to_placeholder(self):
        from agentclaw.community.core.task.task_plan.static_plan import (
            StaticPlanDefinition,
            StaticPlanNodeDefinition,
        )
        definition = StaticPlanDefinition(
            template_id="orphan", entry_bot_id="entry", input_schema={},
            nodes=(StaticPlanNodeDefinition(
                node_id="lost", name="lost", node_type="bot",
                depends_on=("ghost",), bot_id="b", bot_ids=(), input={}, output={},
            ),),
        )
        runtime = StaticPlanRuntime(definition, {})
        prompt = runtime._relay_instruction(runtime.by_id["lost"], {}, _bare_graph())
        assert "# 接自:上游" in prompt

    def test_upstream_output_text_scalar_variants(self):
        runtime = self._runtime()
        assert runtime._upstream_output_text({"k": {"summary": "S"}}) == "S"
        rendered = runtime._upstream_output_text({"k": {"x": 1}})
        assert "x" in rendered and "1" in rendered

    def test_relay_instruction_renders_collab_group_roster(self):
        runtime = self._runtime()
        definition = runtime.by_id["down_collab"]
        prompt = runtime._relay_instruction(definition, {}, _bare_graph())
        assert "## 群组成" in prompt
        assert "- 甲Bot(alpha-bot) = driver / 总结者" in prompt
        assert "- 乙Bot(beta-bot)" in prompt
        assert prompt.count("## 上游产出正文") == 1
        # 协作节点用"本群任务"
        assert "## 本群任务" in prompt
        assert prompt.rstrip().endswith("下游协作群任务")

    def test_enabled_expression_without_true_check_falls_back_true(self):
        runtime = self._runtime()
        graph = _bare_graph({})
        assert runtime._enabled("$.approval.output.approved == false", graph) is True
        assert runtime._enabled("always_ready", graph) is True


class TestStaticPlanRuntimeReady:
    def test_ready_decorates_dependent_node_after_upstream_done(self):
        svc = TaskGraphService()
        svc.initialize_graph(_task_info("t1"))
        definition = StaticPlanDefinition.from_yaml(ONE_BOT_PLAN)
        runtime = StaticPlanRuntime(definition, {})
        # 未入图节点 → 不 ready
        readiness = runtime.ready(svc.query_task_dashboard("t1"))
        assert readiness.ready == () and readiness.skipped == ()
        # 入图且 PENDING → ready(跳过 catalog 搜推,静态 bot 直驱)
        svc.add_task_nodes([_child("onlybot", run_mode=None)], parent_node_id="t1")
        readiness = runtime.ready(svc.query_task_dashboard("t1"))
        assert [n.node_id for n in readiness.ready] == ["onlybot"]
        node = readiness.ready[0]
        assert node.run_info.extend_props["static_bot_id"] == "bot-only"
        assert "execution_prompt" in node.run_info.extend_props


# ===========================================================================
# 集中回归粘合(保证本文件内 fake 与真实服务的一致性)
# ===========================================================================

def test_svc_instruction_helper_matches_domain_shape():
    # _child 继承 TaskInfo spec(带验收项);static_input 变体走独立 spec
    plain = _child("c1")
    assert plain.task_spec.goal.acceptances
    static = _child("h1", static_input={"unhandled_tasks": []})
    assert static.task_spec.context.extend_props["static_input"] == {"unhandled_tasks": []}
    assert static.task_spec.goal.acceptances == []
    # 幂等防重初始化真实服务保持不变
    svc = TaskGraphService()
    svc.initialize_graph(_task_info("t1"))
    with pytest.raises(GraphAlreadyInitializedError):
        svc.initialize_graph(_task_info("t1"))