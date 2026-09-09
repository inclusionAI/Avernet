"""M4a TaskDispatcher 单测(对齐 tasks.md T4a.x)。

in-test 策略注入(包 StubBotDiscover 成 DispatchStrategy adapter);真实 TaskGraphService 构图。
覆盖:四态填 TaskNode.run_info、MISS 标 miss_events、HIT_MULTI_BOTS 标 pending_group_formation(拉群归编排核)、
BBS 退化、不写图不起 run。零参 TaskDispatcher(graph);corp 注入策略经 set_strategies。
"""
from __future__ import annotations

import asyncio
import sys

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
    _PREFETCH_MAX_TOKENS,
    _prefetch_candidates,
    _tokenize,
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
            goal=Goal(objective="目标任务", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
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


class TestExecRetryReplay:
    """exec_error/SLA-timeout 重试节点(harness_retries>0 + 已有 run_mode/assignee)原样重跑:
    跳过搜推、不覆写模式/执行者,避免 mode_coverage/候选抖动在重试时翻转模态或换 bot。
    harness_retries 达 MAX_HARNESS→HUNG→升 BBS 的兜底在编排核侧,不在此测。"""

    def test_single_bot_retry_preserves_mode_and_assignee(self, svc):
        # 策略本会覆写为 bot_other;命中 replay → 保留原 single_bot/bot_orig 且不搜推
        d, strat = _dispatcher(svc, SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="bot_other"))
        node = _node("c1", run_mode="single_bot", assignee="bot_orig")
        node.run_info.extend_props["harness_retries"] = 1
        out = _run(d.dispatch([node]))
        assert out[0].run_info.run_mode == "single_bot"
        assert out[0].run_info.assignee == "bot_orig"
        assert len(strat.search_calls) == 0

    def test_coop_group_retry_preserves_mode_and_group_id(self, svc):
        d, strat = _dispatcher(svc, SearchResult(
            outcome=SearchOutcome.HIT_MULTI_BOTS,
            group_formation=GroupFormation(bot_ids=["bot_a", "bot_b"], collab_mode="manager_worker"),
        ))
        node = _node("c1", run_mode="coop_group", assignee="grp_exist")
        node.run_info.extend_props["harness_retries"] = 1
        out = _run(d.dispatch([node]))
        assert out[0].run_info.run_mode == "coop_group"
        assert out[0].run_info.assignee == "grp_exist"
        assert out[0].run_info.extend_props.get("pending_group_formation") is None  # 不重新拉群
        assert len(strat.search_calls) == 0

    def test_fresh_dispatch_not_replayed(self, svc):
        # harness_retries=0(首次派发)→ 正常搜推覆写
        d, strat = _dispatcher(svc, SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="bot_market"))
        out = _run(d.dispatch([_node("c1")]))
        assert out[0].run_info.run_mode == "single_bot"
        assert out[0].run_info.assignee == "bot_market"
        assert len(strat.search_calls) == 1

    def test_retry_without_assignee_still_searches(self, svc):
        # 重试但 assignee 已失(MISS/stale/start_run_failed 清空)→ 无法原样重跑,正常搜推
        d, strat = _dispatcher(svc, SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="bot_market"))
        node = _node("c1", run_mode="single_bot", assignee=None)
        node.run_info.extend_props["harness_retries"] = 1
        out = _run(d.dispatch([node]))
        assert out[0].run_info.assignee == "bot_market"
        assert len(strat.search_calls) == 1

    def test_bbs_retry_degrades_before_replay(self, svc):
        # bbs 退化优先于 exec-replay(bbs 走自驱,不进 start_run 重投)
        d, strat = _dispatcher(svc, SearchResult(outcome=SearchOutcome.MISS))
        node = _node("c1", run_mode="bbs", assignee="bot_bbs")
        node.run_info.extend_props["harness_retries"] = 1
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


def test_search_strategy_default_rule_single_for_one_or_two_joined():
    """off-path(rule,task_settings=None):2 joined(候选∩池)→ HIT_SINGLE(joined[0]),无随机。"""

    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {"items": [{"bot_id": "rule-a"}, {"bot_id": "rule-b"}]}

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
    result = _run(SearchBasedDispatchStrategy(bot, _Discover(), bcn=_ClaimBcn()).apply(_node("c1"), graph))

    assert result.outcome == SearchOutcome.HIT_SINGLE
    assert result.bot_id == "rule-a:1"
    assert result.owner_id == "1"
    assert bot.calls == []


def test_search_strategy_default_rule_group_capped_at_three_for_more_than_two_joined():
    """off-path(rule,task_settings=None):3 joined → HIT_MULTI_BOTS(前 3,manager_worker),确定性。"""

    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {"items": [{"bot_id": "rule-a"}, {"bot_id": "rule-b"}, {"bot_id": "rule-c"}]}

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
    result = _run(SearchBasedDispatchStrategy(_Bot(), _Discover(), bcn=_ClaimBcn()).apply(_node("c1"), graph))

    assert result.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert result.group_formation is not None
    assert result.group_formation.bot_ids == ["rule-a:1", "rule-b:2", "rule-c:3"]
    assert result.group_formation.collab_mode == "manager_worker"
    assert [m["role"] for m in result.group_formation.members_info] == [
        "manager", "worker", "worker"
    ]


def test_search_strategy_default_rule_no_intersection_misses_without_fallback():
    """off-path:候选与 claim 池无交集 → MISS(no_candidates),不回退池。"""

    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {"items": [{"bot_id": "stranger"}]}

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
    result = _run(SearchBasedDispatchStrategy(_Bot(), _Discover(), bcn=_ClaimBcn()).apply(_node("c1"), graph))

    assert result.outcome == SearchOutcome.MISS
    assert result.miss_reason == "no_candidates"


class _ModeCoverageSettings:
    """task_settings stub:仅 mode_coverage 可配(其余 False),供 on-path 覆盖路由测试。"""

    def __init__(self, mode_coverage: bool) -> None:
        self._mc = mode_coverage

    def is_enabled(self, setting_type: str) -> bool:
        return self._mc if setting_type == "mode_coverage" else False


def test_search_strategy_rule_mode_coverage_forces_single_group_bbs_then_normal(monkeypatch):
    """on-path(mode_coverage ON):同 run 连续派发 → single→group→bbs 依次覆盖,全覆盖后兜底+随机(single/group 平均分配)。
    engine 负责写 graph 标记;此处手动推进 extend_props["mode_coverage"] 模拟 engine 写回。"""

    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {"items": [{"bot_id": "rule-a"}, {"bot_id": "rule-b"}, {"bot_id": "rule-c"}]}

    class _Bot:
        async def send_and_wait_async(self, **kwargs):
            raise AssertionError("rule path must not call search skill")

    from agentclaw.community.core.task.domain.models import TaskExecutionGraph

    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.PENDING,
        extend_props={"owner_bot_id": "owner", "mode_coverage": []},
    )
    strat = SearchBasedDispatchStrategy(
        _Bot(), _Discover(), bcn=_ClaimBcn(),
        task_settings=_ModeCoverageSettings(True),
    )
    node = _node("c1")

    r1 = _run(strat.apply(node, graph))
    assert r1.outcome == SearchOutcome.HIT_SINGLE
    assert r1.bot_id == "rule-a:1"
    graph.extend_props["mode_coverage"] = ["single"]  # 模拟 engine 写回

    r2 = _run(strat.apply(node, graph))
    assert r2.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert r2.group_formation.bot_ids == ["rule-a:1", "rule-b:2", "rule-c:3"]
    graph.extend_props["mode_coverage"] = ["group", "single"]

    r3 = _run(strat.apply(node, graph))
    assert r3.outcome == SearchOutcome.MISS
    assert r3.miss_reason == "mode_coverage_bbs"
    graph.extend_props["mode_coverage"] = ["bbs", "group", "single"]  # 全覆盖

    # 全覆盖后:兜底+随机(single/group 平均分配),不回落 off-path
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.random",
        lambda: 0.6,
    )
    r4 = _run(strat.apply(node, graph))
    assert r4.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert r4.group_formation.bot_ids == ["rule-a:1", "rule-b:2", "rule-c:3"]
    monkeypatch.setattr(
        "agentclaw.community.core.task.task_dispatch.strategies.random.random",
        lambda: 0.3,
    )
    r5 = _run(strat.apply(node, graph))
    assert r5.outcome == SearchOutcome.HIT_SINGLE
    assert r5.bot_id == "rule-a:1"


def test_search_strategy_rule_mode_coverage_pool_fallback_when_join_empty():
    """on-path:关键词候选与 claim 池无交集(join 空)→ 覆盖路由用 claim 池兜底命中 single。"""

    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {"items": [{"bot_id": "stranger"}]}  # 不在 claim 池 → join 空

    class _Bot:
        async def send_and_wait_async(self, **kwargs):
            raise AssertionError("rule path must not call search skill")

    from agentclaw.community.core.task.domain.models import TaskExecutionGraph

    graph = TaskExecutionGraph(
        run_id=1, loop_round=0, status=Status.PENDING,
        extend_props={"owner_bot_id": "owner", "mode_coverage": []},
    )
    strat = SearchBasedDispatchStrategy(
        _Bot(), _Discover(), bcn=_ClaimBcn(),
        task_settings=_ModeCoverageSettings(True),
    )
    result = _run(strat.apply(_node("c1"), graph))

    # join 空 → claim 池兜底 single(_ClaimBcn 首条 rule-a:1)
    assert result.outcome == SearchOutcome.HIT_SINGLE
    assert result.bot_id == "rule-a:1"


def test_search_strategy_rule_mode_coverage_off_falls_back_to_offpath():
    """mode_coverage OFF(task_settings False)→ 走 off-path 正常 join+candidate-count。"""

    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {"items": [{"bot_id": "rule-a"}, {"bot_id": "rule-b"}]}

    class _Bot:
        async def send_and_wait_async(self, **kwargs):
            raise AssertionError("rule path must not call search skill")

    from agentclaw.community.core.task.domain.models import TaskExecutionGraph

    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.PENDING,
        extend_props={"owner_bot_id": "owner", "mode_coverage": []},
    )
    strat = SearchBasedDispatchStrategy(
        _Bot(), _Discover(), bcn=_ClaimBcn(),
        task_settings=_ModeCoverageSettings(False),
    )
    result = _run(strat.apply(_node("c1"), graph))

    # 2 joined → single(off-path),不由覆盖路由强制
    assert result.outcome == SearchOutcome.HIT_SINGLE
    assert result.bot_id == "rule-a:1"


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

class TestTokenize:
    """_tokenize 约束:≥2 字 + 停用词过滤(jieba/fallback 双路径统一)。"""

    def test_empty_returns_empty(self):
        assert _tokenize("") == []

    def test_single_char_filtered_by_min_length(self):
        # ≥2 字过滤挡单字虚词(的/了/是/o)
        assert _tokenize("o") == []
        assert _tokenize("的") == []

    def test_business_words_kept(self):
        assert "存储" in _tokenize("存储行业分析")

    def test_two_char_stopwords_filtered(self):
        # 2 字功能词/语气词 + 泛义动词 + 模糊量词 被 _STOPWORDS 滤掉
        for w in ["进行", "可以", "需要", "产出", "提供", "给出", "不少", "梳理"]:
            assert _tokenize(w) == [], f"{w} 应为停用词被滤掉"
        # 偏业务词带业务语义(覆盖率/构建工具/数据整合/综合平台/数据分析/技术调研/风险评估),保留不滤
        for w in ["分析", "研究", "调研", "评估", "考察", "论证", "覆盖", "构建", "搭建", "整合", "综合"]:
            assert _tokenize(w) != [], f"{w} 带业务语义应保留,不应被停用词滤掉"
        # 业务名词保留(不被误滤)
        assert "存储" in _tokenize("存储行业")

    def test_fallback_without_jieba_still_filters_min_length_and_stopwords(self, monkeypatch):
        # jieba 未装 → 退回整串,但仍受 ≥2 字 + 停用词过滤(双路径统一)
        monkeypatch.setitem(sys.modules, "jieba", None)
        # 整串 ≥2 且非停用词 → 保留整串
        assert _tokenize("存储行业") == ["存储行业"]
        # 整串为单字 → ≥2 滤掉
        assert _tokenize("o") == []
        # 整串恰为 2 字停用词 → 滤掉
        assert _tokenize("进行") == []


def test_prefetch_caps_tokens_to_max():
    """_prefetch_candidates 仅用 goal.objective 分词,token 去重后取 top _PREFETCH_MAX_TOKENS。"""
    from agentclaw.community.core.task.domain.models import TaskExecutionGraph

    class _CountingDiscover:
        def __init__(self) -> None:
            self.keywords: list[str] = []

        def search_by_keyword(self, **kwargs):
            self.keywords.append(kwargs.get("keyword"))
            idx = len(self.keywords)
            return {"items": [{"bot_id": f"b{idx}", "recommend": {"score": float(idx)}}]}

    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.PENDING,
        extend_props={"owner_bot_id": "owner"},
    )
    # objective 分词后 9 个 ≥2 非停用词 token(>5)→ 触发 top-5 截断
    node = _node("c1")
    node.task_spec.goal.objective = "存储系统网络架构计算资源安全策略数据备份监控运维容量"
    discover = _CountingDiscover()
    cands = _run(_prefetch_candidates(discover, node, graph))
    # token 上限:search_by_keyword 调用次数恰为 _PREFETCH_MAX_TOKENS(9→5)
    assert len(discover.keywords) == _PREFETCH_MAX_TOKENS
    # 每 token 返回独立 bot_id → 候选数 == 调用数
    assert len(cands) == _PREFETCH_MAX_TOKENS
