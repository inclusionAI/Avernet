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
    跳过搜推、不覆写模式/执行者,避免候选抖动在重试时翻转模态或换 bot。
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


def test_search_strategy_default_rule_single_for_one_or_two_candidates():
    """off-path(rule):2 unrestricted candidates → HIT_SINGLE(candidate[0])."""

    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {"items": [
            {"bot_id": "rule-a", "bot_uuid": "rule-a:1"},
            {"bot_id": "rule-b", "bot_uuid": "rule-b:2"},
        ]}

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
    result = _run(SearchBasedDispatchStrategy(bot, _Discover()).apply(_node("c1"), graph))

    assert result.outcome == SearchOutcome.HIT_SINGLE
    assert result.bot_id == "rule-a:1"
    assert result.owner_id == "1"
    assert bot.calls == []


def test_search_strategy_default_rule_group_capped_at_three_for_more_than_two_candidates():
    """off-path(rule):3 unrestricted candidates → HIT_MULTI_BOTS(前 3)."""

    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {"items": [
            {"bot_id": "rule-a", "bot_uuid": "rule-a:1"},
            {"bot_id": "rule-b", "bot_uuid": "rule-b:2"},
            {"bot_id": "rule-c", "bot_uuid": "rule-c:3"},
        ]}

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
    result = _run(SearchBasedDispatchStrategy(_Bot(), _Discover()).apply(_node("c1"), graph))

    assert result.outcome == SearchOutcome.HIT_MULTI_BOTS
    assert result.group_formation is not None
    assert result.group_formation.bot_ids == ["rule-a:1", "rule-b:2", "rule-c:3"]
    assert result.group_formation.collab_mode == "manager_worker"
    assert [m["role"] for m in result.group_formation.members_info] == [
        "manager", "worker", "worker"
    ]


def test_search_strategy_default_rule_accepts_candidate_outside_bbs_claim_roster():
    """Rule dispatch does not consult task_claim_mode; an unmatched BBS roster is irrelevant."""

    class _Discover:
        def search_by_keyword(self, **kwargs):
            return {"items": [{"bot_id": "stranger", "bot_uuid": "stranger:owner"}]}

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
    result = _run(SearchBasedDispatchStrategy(_Bot(), _Discover()).apply(_node("c1"), graph))

    assert result.outcome == SearchOutcome.HIT_SINGLE
    assert result.bot_id == "stranger:owner"

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


class TestDispatchExceptionCarrier:
    """搜推异常 / rationale 装配失败 的诊断 carrier(per REQ-P1 dispatch-failure 可见性):

    dispatcher 顶层 ``except`` 吞搜推异常时写 ``dispatch_error``(短状态串,供 harness 路由)
    + ``_dispatch_failure``(含异常消息,供引擎 dispatch_fail 闸门发射带
    ``error_type=DISPATCH_STUCK`` 的 ``dispatch`` 轨迹事件);rationale 装配抛错时在
    ``sr.assembly_error`` 回填原因(经 ``_dispatch_failure`` 透传到 hit/miss 事件 ``ext_info``
    备注)。``_one`` 入口清上一轮残留 ``_dispatch_failure`` 防重投污染本轮 hit 事件。"""

    def test_search_exception_writes_dispatch_failure_carrier(self, svc):
        class _RaisingStrategy(_StubDispatchStrategy):
            def __init__(self) -> None:
                super().__init__(SearchResult(outcome=SearchOutcome.MISS))

            async def apply(self, node, graph):  # mirrors a search/recommend blow-up
                raise RuntimeError("search/recommend blew up")

        d = TaskDispatcher(svc)
        d.set_strategies([_RaisingStrategy()])
        node = _run(d.dispatch([_node("c1")]))[0]

        # node 留 PENDING(无执行者)+ dispatch_error 短状态串(harness 路由用,类型级)
        assert node.run_info.extend_props.get("dispatch_error") == "dispatch_exception:RuntimeError"
        # 失败 carrier 含异常消息 —— 引擎 dispatch_fail 闸门据此发射轨迹事件(Step 1)
        fail = node.run_info.extend_props.get("_dispatch_failure")
        assert isinstance(fail, dict)
        assert fail["error_type"] == "dispatch_exception"
        assert "RuntimeError" in fail["error_msg"]
        assert "blew up" in fail["error_msg"]

    def test_stale_dispatch_failure_cleared_on_clean_redispatch(self, svc):
        """重投命中时,上一轮残留的 ``_dispatch_failure`` 必须被清掉,避免旧降级备注粘到
        本轮 hit_single 轨迹事件(引擎 hit 闸门据此决定是否附 ``ext_info`` 备注)。"""
        node = _node("c1")
        node.run_info.extend_props["_dispatch_failure"] = {"error_type": "stale", "error_msg": "old"}
        d, _ = _dispatcher(svc, SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="bot1"))
        out = _run(d.dispatch([node]))
        assert out[0].run_info.extend_props.get("_dispatch_failure") is None

    def test_rationale_assembly_failure_sets_assembly_error(self):
        """rationale 装配抛错(malformed score 不可 ``float()``)→ ``_build_search_rationale``
        返回 None 且在 ``sr`` 上回填 ``assembly_error``(Step 2 的 carrier 源头);派发决策不受影响。"""
        from agentclaw.community.core.task.task_dispatch.rationale import _build_search_rationale

        sr = SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id="b1")
        candidates = [{"bot_id": "b1", "recommend": {"score": ["not", "a", "number"]}}]
        result = _build_search_rationale(
            node=_node("c1"), candidates=candidates, sr=sr, use_skill=False,
            prompt_text=None, response_text=None, filter_ran=False, prefetch_tokens=[],
        )
        assert result is None
        assert sr.assembly_error is not None
        assert sr.assembly_error.startswith("rationale_assembly_failed")
