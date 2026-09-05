"""M4a TaskDispatcher 单测(对齐 tasks.md T4a.x)。

in-test 策略注入(包 StubBotDiscover 成 DispatchStrategy adapter);真实 TaskGraphService 构图。
覆盖:四态填 TaskNode.run_info、MISS 标 miss_events、HIT_MULTI_BOTS 标 pending_group_formation(拉群归编排核)、
BBS 退化、不写图不起 run。零参 TaskDispatcher(graph);corp 注入策略经 set_strategies。
"""
from __future__ import annotations

import asyncio
import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
from agentclaw.community.core.task.task_dispatch.strategies import (
    GroupFormation,
    SearchBasedDispatchStrategy,
    SearchOutcome,
    SearchResult,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _ClaimBcn:
    """Fake BcnService: 返回 task_claim_mode=true & visibility=public 的 product:owner 池,供 rule 派发。"""

    def list_bots_by_task_modes(self, *, claim=None, dream=None, match="any", visibility=None):
        return [
            {"bot_id": "rule-a:1"},
            {"bot_id": "rule-b:2"},
            {"bot_id": "rule-c:3"},
            {"bot_id": "rule-d:4"},
        ]


def _task_info(task_id: str = "t1") -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="T", instruction="do"),
            context=Context(background="bg"),
            goal=Goal(objective="o", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        source_type="bot",
        owner_bot_id="b1",
    )


def _node(node_id: str = "c1", task_id: str = "t1", run_mode: str | None = None, assignee: str | None = None) -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec,
        run_info=RuntimeInfo(run_mode=run_mode, assignee=assignee),
        node_run_graph=None,  # type: ignore[arg-type]
    )


class _StubDispatchStrategy:
    """包旧 StubBotDiscover(search(node)) 成 DispatchStrategy adapter(测试模拟 corp 策略注入)。"""

    rule_id = "stub"
    priority = 5

    def __init__(self, result: SearchResult):
        self._result = result
        self.search_calls: list[TaskNode] = []

    async def matches(self, node: TaskNode, graph) -> bool:
        return True

    async def apply(self, node: TaskNode, graph) -> SearchResult:
        self.search_calls.append(node)
        return self._result


def _dispatcher(svc, result: SearchResult) -> tuple[TaskDispatcher, _StubDispatchStrategy]:
    strat = _StubDispatchStrategy(result)
    d = TaskDispatcher(svc)
    d.set_strategies([strat])
    return d, strat


@pytest.fixture
def svc() -> TaskGraphService:
    svc = TaskGraphService()
    svc.initialize_graph(_task_info())
    return svc


class TestFourStates:
    def test_hit_single(self, svc):
        d, _ = _dispatcher(svc, SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="bot_market"))
        out = _run(d.dispatch([_node("c1")]))
        assert out[0].run_info.run_mode == "single_bot"
        assert out[0].run_info.assignee == "bot_market"

    def test_hit_single_preserves_owner_metadata(self, svc):
        d, _ = _dispatcher(svc, SearchResult(
            outcome=SearchOutcome.HIT_SINGLE,
            bot_id="default",
            bot_name="默认Bot",
            owner_id="146836",
            owner_name="栖真",
        ))
        out = _run(d.dispatch([_node("c1")]))
        assert out[0].run_info.extend_props["assignee_name"] == "默认Bot"
        assert out[0].run_info.extend_props["assignee_owner_id"] == "146836"
        assert out[0].run_info.extend_props["assignee_owner_name"] == "栖真"

    def test_hit_group(self, svc):
        d, _ = _dispatcher(svc, SearchResult(outcome=SearchOutcome.HIT_GROUP, group_id="grp_tech"))
        out = _run(d.dispatch([_node("c1")]))
        assert out[0].run_info.run_mode == "coop_group"
        assert out[0].run_info.assignee == "grp_tech"

    def test_hit_multi_bots_marks_pending_group(self, svc):
        gf = GroupFormation(bot_ids=["bot_a", "bot_b"], collab_mode="manager_worker")
        d, strat = _dispatcher(svc, SearchResult(outcome=SearchOutcome.HIT_MULTI_BOTS, group_formation=gf))
        out = _run(d.dispatch([_node("c1")]))
        assert out[0].run_info.run_mode == "coop_group"
        assert out[0].run_info.assignee is None  # 拉群归编排核,留空
        assert out[0].run_info.extend_props.get("pending_group_formation") is gf
        assert len(strat.search_calls) == 1

    def test_miss_no_assignee_marks_events(self, svc):
        d, _ = _dispatcher(svc, SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_bot_match"))
        out = _run(d.dispatch([_node("c1")]))
        assert out[0].run_info.run_mode is None
        assert out[0].run_info.assignee is None
        assert out[0].run_info.extend_props.get("miss_events") == ["no_bot_match"]


class TestBbsDegradation:
    def test_bbs_node_skips_search(self, svc):
        d, strat = _dispatcher(svc, SearchResult(outcome=SearchOutcome.MISS))
        node = _node("c1", run_mode="bbs", assignee="bot_bbs")
        out = _run(d.dispatch([node]))
        assert out[0].run_info.run_mode == "bbs"
        assert out[0].run_info.assignee == "bot_bbs"
        assert len(strat.search_calls) == 0


class TestNoWriteGraph:
    def test_dispatch_returns_filled_nodes_only(self, svc):
        # dispatcher 持 graph 只读 config;不写图(不调 add/update/patch)。验证仅填充入参返回。
        d, _ = _dispatcher(svc, SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="b1"))
        out = _run(d.dispatch([_node("c1"), _node("c2")]))
        assert len(out) == 2


class TestEmpty:
    def test_empty_list(self, svc):
        d, _ = _dispatcher(svc, SearchResult(outcome=SearchOutcome.MISS))
        assert _run(d.dispatch([])) == []


class TestSearchBasedDispatchStrategy:
    def test_empty_candidates_returns_miss_without_calling_owner(self):
        class _Discover:
            def search_by_keyword(self, **kwargs):
                return {"items": []}

        class _Bot:
            def __init__(self):
                self.calls = []

            async def send_and_wait_async(self, **kwargs):
                self.calls.append(kwargs)
                return {"status": "COMPLETED", "result": {"content": "{\"outcome\":\"HIT_SINGLE\",\"bot_id\":\"fake\"}"}}

        graph = __import__(
            "agentclaw.community.core.task.domain.models", fromlist=["TaskExecutionGraph"]
        ).TaskExecutionGraph(
            run_id=1, loop_round=0, status=Status.PENDING,
            extend_props={"owner_bot_id": "owner"},
        )
        node = _node("c1")
        strategy = SearchBasedDispatchStrategy(_Bot(), _Discover())

        result = _run(strategy.apply(node, graph))

        assert result.outcome == SearchOutcome.MISS
        assert result.miss_reason == "no_candidates"
        assert strategy._bot.calls == []


def test_search_strategy_default_rule_samples_one_test_bot_for_one_or_two_candidates(monkeypatch):
    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {"items": [{"bot_id": "first"}, {"bot_id": "second"}]}

    class _Bot:
        def __init__(self):
            self.calls = []

        async def send_and_wait_async(self, **kwargs):
            self.calls.append(kwargs)
            raise AssertionError("default dispatch rule must not call search skill")

    from agentclaw.community.core.task.domain.models import TaskExecutionGraph

    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.PENDING,
        extend_props={"owner_bot_id": "owner"},
    )
    bot = _Bot()
    sampled = ["default:146836"]
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.sample",
        lambda population, count: sampled[:count],
    )
    result = _run(SearchBasedDispatchStrategy(bot, _Discover(), bcn=_ClaimBcn()).apply(_node("c1"), graph))

    assert result.outcome == SearchOutcome.HIT_SINGLE
    assert result.bot_id == "default:146836"
    assert result.owner_id == "146836"
    assert bot.calls == []


def test_search_strategy_default_rule_samples_all_count_as_manager_worker_for_more_than_two_candidates(monkeypatch):
    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {
                "items": [
                    {"bot_id": "manager"},
                    {"bot_id": "worker-1"},
                    {"bot_id": "worker-2"},
                ]
            }

    class _Bot:
        async def send_and_wait_async(self, **kwargs):
            raise AssertionError("default dispatch rule must not call search skill")

    from agentclaw.community.core.task.domain.models import TaskExecutionGraph

    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.PENDING,
        extend_props={"owner_bot_id": "owner"},
    )
    sampled = ["bot-a:1", "bot-b:2", "bot-c:3"]
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.sample",
        lambda population, count: sampled[:count],
    )
    result = _run(SearchBasedDispatchStrategy(_Bot(), _Discover(), bcn=_ClaimBcn()).apply(_node("c1"), graph))

    assert result.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert result.group_formation is not None
    assert result.group_formation.bot_ids == sampled
    assert result.group_formation.collab_mode == "manager_worker"
    assert [m["role"] for m in result.group_formation.members_info] == [
        "manager", "worker", "worker"
    ]


def test_search_strategy_composes_owner_identity_for_openapi_call():
    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {"items": [{"bot_id": "candidate"}]}

    class _Bot:
        def __init__(self):
            self.calls = []

        async def send_and_wait_async(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "status": "COMPLETED",
                "result": {
                    "content": (
                        '{"outcome":"MISS","miss_reason":"not matched"}'
                    )
                },
            }

    from agentclaw.community.core.task.domain.models import TaskExecutionGraph

    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.PENDING,
        extend_props={"owner_bot_id": "default:old-owner", "owner_user_id": "146836"},
    )
    bot = _Bot()
    result = _run(
        SearchBasedDispatchStrategy(bot, _Discover(), use_search_skill=True).apply(
            _node("c1"), graph
        )
    )

    assert result.outcome == SearchOutcome.MISS
    assert bot.calls[0]["bot_id"] == "default:146836"
