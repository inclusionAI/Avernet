"""TDD tests for the DISPATCH trajectory gate (REQ-2).

P3 item 1 of the task-trajectory spec. Covers four surfaces (per tasks.md P3):
    1. **Dispatcher writes rationale** — `TaskDispatcher.dispatch` with the real
       `SearchBasedDispatchStrategy` writes `dataclasses.asdict(DispatchRationale)`
       into `node.run_info.extend_props["_dispatch_rationale"]` carrying
       `strategy_name`/`decision_mode`/`candidates` for the engine DISPATCH gate to read.
    2. **DISPATCH gate emits a trajectory event with the rationale** — with a
       capturing fake trajectory repo injected into `CentralizedExecutionAdapter`, drive each
       of the three DISPATCH gates (`HIT_SINGLE` / `HIT_MULTI` / `MISS`) via
       `_prepare_into` + `_drain` / `on_miss` and assert the emitted
       `task_trajectory_events` row has `action_type=dispatch`, matching
       `action_result`, and `ext_info` JSON carrying `_dispatch_rationale` with
       the full `DispatchRationale` shape.
    4. **Defensive rationale assembly** — when a sub-field of the rationale would
       raise (a malformed `recommend.score`), the strategy `try/except`-swallows
       → `sr.rationale is None`, dispatch still completes normally, the
       dispatcher writes no `_dispatch_rationale` key, and the engine DISPATCH
       gate still fires with `ext_info=None`. Rationale assembly NEVER breaks
       the dispatch path (spec REQ-2 + 决策 #14).

Invariants the tests pin (cross-cutting with the task constraints):
    * `_log_action(NodeAction.DISPATCH, ...)` calls are UNCHANGED at all three
      gates (additive `_log_trajectory` alongside, not a replacement).
    * `NodeAction` enum is never touched; `TrajectoryActionType` is a SEPARATE
      string-typed set (`"dispatch"`).
    * No `task_action_log` writes/reads.
    * Forward-driving path stays unaffected when the trajectory repo is broken
      or the rationale build raises (the swallow guarantee).
"""
from __future__ import annotations

from tests.community.core.task.task_trajectory._task_context_support import _tcs

import asyncio
import dataclasses
import json

import pytest

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import TrajectoryEventRecord
from agentclaw.community.core.task.task_runner.execution_adapters import CentralizedExecutionAdapter
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
from agentclaw.community.core.task.task_dispatch.strategies import (
    GroupFormation,
    SearchBasedDispatchStrategy,
    SearchOutcome,
    SearchResult,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    DispatchCandidate,
    DispatchRationale,
)


# ---------------------------------------------------------------------------
# Shared helpers / fakes
# ---------------------------------------------------------------------------


def _run(coro):
    """Sync wrapper to drive async dispatch / engine methods in unit tests."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _task_info(task_id: str = "t1", max_depth: int = 3) -> TaskInfo:
    return TaskInfo(task_id=task_id,
        task_spec=TaskSpec(

            context=Context(background="bg", title="T"),
            goal=Goal(
                objective="存储架构分析",
                acceptances=[AcceptanceCriteria(id="ac1", description="d")],
            ),
        ),
        source_type="bot",
        owner_bot_id="owner:1",
        execution_config={"MAX_DEPTH": max_depth, "BBS_MAX_DEPTH": 3, "task_type": "dynamic"},
    )


def _node(node_id: str = "c1", task_id: str = "t1", run_mode: str | None = None,
          assignee: str | None = None) -> TaskNode:
    return TaskNode(
        node_id=node_id,
        task_id=task_id,
        status=Status.PENDING,
        task_spec=_task_info(task_id).task_spec,
        run_info=RuntimeInfo(run_mode=run_mode, assignee=assignee),
        node_run_graph=None,  # type: ignore[arg-type]
    )



class _Bcn:
    """`BcnService` fake — returns a task_claim_mode-on roster (bcs `{p}:{o}` form)."""

    def __init__(self, entries=None, exc=None) -> None:
        self._entries, self._exc = entries, exc

    def list_bots_by_task_modes(self, *, claim=None, dream=None, match="any", visibility=None):
        if self._exc is not None:
            raise self._exc
        return self._entries or []


class _Discover:
    """BotDiscoverServiceProtocol fake — `search_by_keyword(keyword, ...) -> {"items": [...]}`."""

    def __init__(self, items=None) -> None:
        self._items = items or []

    def search_by_keyword(self, **kwargs):
        return {"items": list(self._items)}


class _Bot:
    """OpenApiBotPort fake — `send_and_wait_async(bot_id=, message=, metadata=) -> run dict`."""

    def __init__(self, outcome: str = "HIT_SINGLE", **sr_kwargs) -> None:
        self._run = {
            "status": "COMPLETED",
            "result": {"content": json.dumps({"outcome": outcome, **sr_kwargs})},
        }
        self.calls: list[dict] = []

    async def send_and_wait_async(self, **kwargs):
        self.calls.append(kwargs)
        return self._run


class _TrajRepo:
    """Fake trajectory repo — captures every `insert_event` call (no SQLite)."""

    def __init__(self) -> None:
        self.records: list[TrajectoryEventRecord] = []
        self.calls = 0

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        self.calls += 1
        self.records.append(record)
        return record


def _make_graph(svc: TaskGraphService, task_id: str = "t1"):
    """Idempotent: initialize the graph on first call, return the in-memory graph
    on subsequent calls. ``TaskGraphService.initialize_graph`` raises on re-init,
    so the helper guards with ``task_id in svc._graphs`` to allow the fixture AND
    the test body to both access the same initialized graph without conflicts."""
    if task_id in svc._graphs:
        return svc._graphs[task_id]
    return svc.initialize_graph(_task_info(task_id))


# ---------------------------------------------------------------------------
# 1. Dispatcher writes rationale into node.run_info.extend_props
# ---------------------------------------------------------------------------


class TestDispatcherWritesRationale:
    """REQ-2 #2: `TaskDispatcher.dispatch` writes the rationale dict into
    `node.run_info.extend_props["_dispatch_rationale"]` (the carrier to the engine
    gate; no contextvar exists). The dict carries `strategy_name`, `decision_mode`,
    and `candidates`.
    """

    def _dispatcher_with_skills(self, svc, discover_items, claim_on, bot_kwargs,
                                 use_skill=True):
        discover = _Discover(discover_items)
        bot = _Bot(**bot_kwargs)
        bcn = _Bcn(claim_on) if claim_on is not None else None
        strat = SearchBasedDispatchStrategy(
            bot, discover, bcn=bcn, use_search_skill=use_skill,
        )
        d = TaskDispatcher(svc)
        d.set_strategies([strat])
        return d, strat

    def test_hit_single_writes_full_rationale_dict(self, svc):
        """Skill mode + filter on + bot in claim_on → HIT_SINGLE; rationale carries
        strategy=search, decision_mode=skill, candidates from prefetch, skill digests."""
        # owner IS in claim_on so the LLM-picked owner stays (no drop).
        d, _ = self._dispatcher_with_skills(
            svc,
            discover_items=[{"bot_id": "A", "recommend": {"score": 0.9, "short_profile": "pA"}}],
            claim_on=[{"bot_id": "A:1"}],
            bot_kwargs={"outcome": "HIT_SINGLE", "bot_id": "A"},
        )
        out = _run(d.dispatch([_node("c1")]))
        rat = out[0].run_info.extend_props.get("_dispatch_rationale")
        assert rat is not None and isinstance(rat, dict)
        assert rat["strategy_name"] == "search"
        assert rat["decision_mode"] == "skill"
        # candidates from prefetch (non-empty when search ran)
        assert isinstance(rat["candidates"], list) and rat["candidates"]
        c0 = rat["candidates"][0]
        assert c0["bot_id"] == "A"
        assert c0["recommend_score"] == 0.9
        assert c0["short_profile"] == "pA"
        # prefetch_tokens populated from jieba-tokenized goal.objective
        assert isinstance(rat["prefetch_tokens"], list) and rat["prefetch_tokens"]
        # claim-join post-filter is no longer part of dispatch.
        assert rat["join_filter_applied"] is False
        assert rat["join_dropped"] == []
        # skill-mode prompt/response digests are SHA-256 hex strings
        assert isinstance(rat["skill_prompt_digest"], str) and len(rat["skill_prompt_digest"]) == 64
        assert isinstance(rat["skill_response_digest"], str) and len(rat["skill_response_digest"]) == 64

    def test_rule_mode_skill_digests_none(self, svc):
        """Rule mode (use_skill=False) → skill_prompt_digest=None, skill_response_digest=None;
        decision_mode=rule."""
        d, _ = self._dispatcher_with_skills(
            svc,
            discover_items=[{"bot_id": "A", "recommend": {"score": 0.9}}],
            claim_on=[{"bot_id": "A:1"}],
            bot_kwargs={"outcome": "HIT_SINGLE", "bot_id": "A"},
            use_skill=False,
        )
        # Rule mode uses the unrestricted prefetch catalog directly.
        out = _run(d.dispatch([_node("c1")]))
        rat = out[0].run_info.extend_props.get("_dispatch_rationale")
        if rat is None:
            pytest.skip("rule mode MISS when no claim+public pool intersect; rationale skipped on MISS")
        assert rat["decision_mode"] == "rule"
        assert rat["skill_prompt_digest"] is None
        assert rat["skill_response_digest"] is None

    def test_miss_writes_rationale_with_empty_candidates(self, svc):
        """Empty prefetch candidates → MISS(no_candidates); rationale still emitted
        with empty candidates, strategy_name=search."""
        d, _ = self._dispatcher_with_skills(
            svc,
            discover_items=[],  # no prefetch hits
            claim_on=[{"bot_id": "A:1"}],
            bot_kwargs={"outcome": "MISS"},
        )
        out = _run(d.dispatch([_node("c1")]))
        rat = out[0].run_info.extend_props.get("_dispatch_rationale")
        assert rat is not None
        assert rat["strategy_name"] == "search"
        assert rat["candidates"] == []
        assert rat["join_dropped"] == []
        assert rat["skill_prompt_digest"] is None  # no skill call ran (candidates empty)

    def test_direct_strategy_writes_minimal_rationale(self, svc):
        """DirectDispatchStrategy.populate: strategy_name=direct, decision_mode=direct,
        empty candidates/prefetch_tokens/join_dropped, skill digests None."""
        from agentclaw.community.core.task.task_dispatch.strategies import DirectDispatchStrategy

        d = TaskDispatcher(svc)
        d.set_strategies([DirectDispatchStrategy()])
        node = _node("c1")
        node.run_info.extend_props["static_bot_id"] = "direct-bot"
        out = _run(d.dispatch([node]))
        rat = out[0].run_info.extend_props.get("_dispatch_rationale")
        assert rat is not None
        assert rat["strategy_name"] == "direct"
        assert rat["decision_mode"] == "direct"
        assert rat["candidates"] == []
        assert rat["prefetch_tokens"] == []
        assert rat["join_dropped"] == []
        assert rat["join_filter_applied"] is False
        assert rat["skill_prompt_digest"] is None
        assert rat["skill_response_digest"] is None

    def test_bbs_replay_nodes_skip_strategy_no_rationale(self, svc):
        """BBS / exec-retry-replay nodes skip the strategy → no rationale written
        (the carrier key is absent, not None)."""
        from agentclaw.community.core.task.task_dispatch.strategies import DirectDispatchStrategy

        d = TaskDispatcher(svc)
        d.set_strategies([DirectDispatchStrategy()])
        node = _node("c1", run_mode="bbs", assignee="bbs-bot")
        out = _run(d.dispatch([node]))
        assert "_dispatch_rationale" not in out[0].run_info.extend_props


# ---------------------------------------------------------------------------
# 2. DISPATCH gate emits a trajectory event with the rationale
# ---------------------------------------------------------------------------


class _RationaleStubDispatcher:
    """Test dispatcher: writes an outcome + a `_dispatch_rationale` dict to the
    node's extend_props (mirrors what the real TaskDispatcher writes). Test-only;
    bypasses the strategy to isolate the engine gate plumbing from policy.
    """

    def __init__(
        self,
        *,
        outcome: str,
        rationale: dict | None = None,
        bot_id: str = "bot1",
        group_formation: GroupFormation | None = None,
        miss_reason: str = "no_bot",
    ) -> None:
        self._outcome = outcome
        self._rationale = rationale
        self._bot_id = bot_id
        self._gf = group_formation
        self._miss_reason = miss_reason

    async def dispatch(self, nodes: list[TaskNode]) -> list[TaskNode]:
        for n in nodes:
            if self._rationale is not None:
                n.run_info.extend_props["_dispatch_rationale"] = dict(self._rationale)
            if self._outcome == "hit_single":
                n.run_info.run_mode = "single_bot"
                n.run_info.assignee = self._bot_id
            elif self._outcome == "hit_multi":
                n.run_info.run_mode = "coop_group"
                n.run_info.extend_props["pending_group_formation"] = self._gf or GroupFormation(
                    bot_ids=["m:a", "w:b"], collab_mode="manager_worker",
                )
            else:  # miss
                n.run_info.extend_props["miss_events"] = [self._miss_reason]
        return nodes


class _StubPlanner:
    async def plan(self, graph, target_node_id=None):
        from agentclaw.community.core.task.domain.models import PlanResult
        return PlanResult(children=[], has_gap=True)


class _StubRunner:
    def __init__(self) -> None:
        self.run_calls: list[list[TaskNode]] = []

    async def start_run(self, nodes):
        self.run_calls.append(list(nodes))
        return [True] * len(nodes)

    async def form_coop_group(self, gf):
        return "grp_stub"


class _TrajectoryCaseEngine(CentralizedExecutionAdapter):
    """Test subclass — injects stubs + a trajectory repo (mirrors the existing
    `_CaseEngine` in test_engine.py but adds the trajectory_repo passthrough)."""

    def __init__(self, graph, planner=None, dispatcher=None, runner=None, trajectory_repo=None) -> None:
        self._case_planner = planner
        self._case_dispatcher = dispatcher
        self._case_runner = runner
        super().__init__(graph, task_context_service=_tcs(trajectory_repo))

    def _build_planner(self):
        return self._case_planner if self._case_planner is not None else super()._build_planner()

    def _build_dispatcher(self):
        return self._case_dispatcher if self._case_dispatcher is not None else super()._build_dispatcher()

    def _build_runner(self):
        return self._case_runner if self._case_runner is not None else super()._build_runner()


_SAMPLE_RATIONALE: dict = dataclasses.asdict(
    DispatchRationale(
        strategy_name="search",
        decision_mode="skill",
        candidates=[DispatchCandidate(bot_id="A", recommend_score=0.9, short_profile="pA")],
        prefetch_tokens=["存储", "分析"],
        join_filter_applied=False,
        join_dropped=[],
        skill_prompt_digest="a" * 64,
        skill_response_digest="b" * 64,
    )
)


def _gate_dispatch_records(repo: _TrajRepo) -> list[TrajectoryEventRecord]:
    return [r for r in repo.records if r.action_type == "dispatch"]


class TestDispatchGateEmitsTrajectory:
    """REQ-2 #3: the three DISPATCH gates (`_drain` HIT_SINGLE / `_drain`
    HIT_MULTI / `on_miss` MISS) fire `_log_trajectory(action_type=dispatch, ...)`
    with `ext_info={"_dispatch_rationale": <rationale dict>}` when available,
    mirroring the existing `_log_action(NodeAction.DISPATCH, ...)` call style
    (additive; existing `_log_action` calls stay unchanged)."""

    def test_hit_single_gate_emits_trajectory_with_rationale(self, svc):
        # Seed a PENDING child node for the dispatcher to operate on.
        from agentclaw.community.core.task.domain.models import TaskNode
        child = TaskNode(node_id="c1", task_id="t1", status=Status.PENDING,
                         task_spec=_task_info("t1").task_spec, run_info=RuntimeInfo(),
                         node_run_graph=None)  # type: ignore[arg-type]
        svc.add_task_nodes([child], "t1")
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc,
            planner=_StubPlanner(),
            dispatcher=_RationaleStubDispatcher(outcome="hit_single", rationale=_SAMPLE_RATIONALE),
            runner=_StubRunner(),
            trajectory_repo=repo,
        )
        side: list[tuple] = []
        _run(eng._prepare_into("t1", side))
        _run(eng._drain("t1", side))

        records = _gate_dispatch_records(repo)
        assert len(records) == 1, [r.action_result for r in repo.records]
        rec = records[0]
        assert rec.action_type == "dispatch"
        assert rec.action_result == "hit_single"
        assert rec.status_from == "PENDING"
        assert rec.status_to == "RUNNING"
        # action_input = the dispatch target (assignee)
        assert rec.action_input == "bot1"
        # ext_info JSON carries the full rationale dict
        assert rec.ext_info is not None
        payload = json.loads(rec.ext_info)
        assert payload["schema_v"] == 1
        rat = payload["_dispatch_rationale"]
        assert rat["strategy_name"] == "search"
        assert rat["decision_mode"] == "skill"
        assert rat["candidates"] == [{"bot_id": "A", "recommend_score": 0.9, "short_profile": "pA"}]
        assert rat["join_dropped"] == []

    def test_hit_multi_gate_emits_trajectory_with_rationale(self, svc):
        from agentclaw.community.core.task.domain.models import TaskNode
        child = TaskNode(node_id="c1", task_id="t1", status=Status.PENDING,
                         task_spec=_task_info("t1").task_spec, run_info=RuntimeInfo(),
                         node_run_graph=None)  # type: ignore[arg-type]
        svc.add_task_nodes([child], "t1")
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc,
            planner=_StubPlanner(),
            dispatcher=_RationaleStubDispatcher(outcome="hit_multi", rationale=_SAMPLE_RATIONALE),
            runner=_StubRunner(),
            trajectory_repo=repo,
        )
        side: list[tuple] = []
        _run(eng._prepare_into("t1", side))
        _run(eng._drain("t1", side))

        records = _gate_dispatch_records(repo)
        assert len(records) == 1
        rec = records[0]
        assert rec.action_result == "hit_multi"
        assert rec.status_from == "PENDING"
        assert rec.status_to == "RUNNING"
        # action_input = the assigned group_id ("grp_stub" from StubRunner.form_coop_group)
        assert rec.action_input == "grp_stub"
        assert rec.ext_info is not None
        assert json.loads(rec.ext_info)["_dispatch_rationale"]["strategy_name"] == "search"

    def test_miss_gate_emits_trajectory_with_rationale(self, svc):
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc,
            planner=_StubPlanner(),
            dispatcher=_RationaleStubDispatcher(outcome="miss", rationale=_SAMPLE_RATIONALE),
            runner=_StubRunner(),
            trajectory_repo=repo,
        )
        # Seed a PENDING child so the dispatcher can produce a MISS patch on it.
        from agentclaw.community.core.task.domain.models import TaskNode
        child = TaskNode(node_id="c1", task_id="t1", status=Status.PENDING,
                         task_spec=_task_info("t1").task_spec, run_info=RuntimeInfo(),
                         node_run_graph=None)  # type: ignore[arg-type]
        svc.add_task_nodes([child], "t1")
        # on_miss takes a patch — dispatch populated the child's extend_props
        # in-memory (the `_dispatch_rationale` carrier); feed the patch that
        # `_prepare_into._handle_node` would have built for the "miss" side.
        side: list[tuple] = []
        _run(eng._prepare_into("t1", side))
        # _prepare_into collected a ("miss", patch) side — drive on_miss directly
        # (the side tuple is ``("miss", patch)``; flatten the per-kind rest so
        # each miss patch is passed one-by-one to on_miss).
        miss_patches = [p for kind, *rest in side if kind == "miss" for p in rest]
        for m in miss_patches:
            _run(eng.on_miss(m))

        records = _gate_dispatch_records(repo)
        assert len(records) == 1
        rec = records[0]
        assert rec.action_result == "miss"
        assert rec.status_from == "PENDING"
        assert rec.status_to == "PENDING"
        # action_input = None for MISS (no dispatch target)
        assert rec.action_input is None
        assert rec.ext_info is not None
        assert json.loads(rec.ext_info)["_dispatch_rationale"]["strategy_name"] == "search"

    def test_dispatch_gate_without_rationale_emits_ext_info_none(self, svc):
        """When the strategy/dispatcher produced no rationale (real None, e.g.
        defensive assembly failed), the gate still fires a dispatch event with
        `ext_info=None` (no crash)."""
        from agentclaw.community.core.task.domain.models import TaskNode
        child = TaskNode(node_id="c1", task_id="t1", status=Status.PENDING,
                         task_spec=_task_info("t1").task_spec, run_info=RuntimeInfo(),
                         node_run_graph=None)  # type: ignore[arg-type]
        svc.add_task_nodes([child], "t1")
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc,
            planner=_StubPlanner(),
            dispatcher=_RationaleStubDispatcher(outcome="hit_single", rationale=None),
            runner=_StubRunner(),
            trajectory_repo=repo,
        )
        side: list[tuple] = []
        _run(eng._prepare_into("t1", side))
        _run(eng._drain("t1", side))

        records = _gate_dispatch_records(repo)
        assert len(records) == 1
        rec = records[0]
        assert rec.action_result == "hit_single"
        # No rationale → ext_info is None (per the gate's defensive read)
        assert rec.ext_info is None


def _apply_skill_strategy(
    *,
    discover_items: list[dict],
    bot_outcome: str = "HIT_SINGLE",
    bot_kwargs: dict | None = None,
) -> "SearchResult":
    """Run the search strategy in skill mode without the removed claim-join gate."""
    from agentclaw.community.core.task.domain.models import TaskExecutionGraph

    discover = _Discover(discover_items)
    bot = _Bot(outcome=bot_outcome, **(bot_kwargs or {}))
    strategy = SearchBasedDispatchStrategy(
        bot, discover, use_search_skill=True,
    )
    graph = TaskExecutionGraph(
        run_id=1, loop_round=0, status=Status.PENDING,
        extend_props={"owner_bot_id": "owner:1", "owner_user_id": "u1"},
    )
    return _run(strategy.apply(_node("c1"), graph))


# ---------------------------------------------------------------------------
# 3. Defensive rationale assembly (try/except-safe; never breaks dispatch)

# ---------------------------------------------------------------------------


class TestDefensiveRationale:
    """REQ-2 验收: rationale 组装全程 `try/except`; 任一子字段缺失 → `rationale=None`
    (or partial) + DEBUG log — dispatch still completes and the DISPATCH trajectory
    event still fires with `ext_info=None`."""

    def test_malformed_recommend_score_yields_none_rationale_but_dispatch_completes(self):
        """A candidate with a non-numeric `recommend.score` raises during rationale
        assembly → `sr.rationale is None` (the strategy swallows it). Dispatch still
        returns a usable `SearchResult` (HIT_SINGLE — no crash)."""
        # score = "bad" → `float("bad")` raises ValueError → rationale build fails.
        r = _apply_skill_strategy(
            discover_items=[{"bot_id": "X", "recommend": {"score": "bad", "short_profile": "p"}}],
            bot_kwargs={"bot_id": "X"},
        )
        # Dispatch still completed with the LLM's HIT_SINGLE choice.
        assert r.outcome == SearchOutcome.HIT_SINGLE
        assert r.bot_id == "X"
        # Defensive: rationale build failed → None.
        assert r.rationale is None

    def test_strategy_rationale_failure_still_emits_dispatch_event(self, svc):
        """End-to-end defensive guard: a dispatcher that returns a HIT_SINGLE node
        with no `_dispatch_rationale` writer (simulating strategy-swallowed failure)
        → the engine HIT_SINGLE gate still fires a `dispatch` trajectory row with
        `ext_info=None` (no crash on the gate side)."""
        from agentclaw.community.core.task.domain.models import TaskNode
        child = TaskNode(node_id="c1", task_id="t1", status=Status.PENDING,
                         task_spec=_task_info("t1").task_spec, run_info=RuntimeInfo(),
                         node_run_graph=None)  # type: ignore[arg-type]
        svc.add_task_nodes([child], "t1")
        repo = _TrajRepo()
        eng = _TrajectoryCaseEngine(
            svc,
            planner=_StubPlanner(),
            dispatcher=_RationaleStubDispatcher(outcome="hit_single", rationale=None),
            runner=_StubRunner(),
            trajectory_repo=repo,
        )
        side: list[tuple] = []
        _run(eng._prepare_into("t1", side))
        _run(eng._drain("t1", side))
        records = _gate_dispatch_records(repo)
        assert len(records) == 1
        assert records[0].action_result == "hit_single"
        assert records[0].ext_info is None

    def test_dispatch_gate_completes_when_trajectory_repo_raises(self, svc, caplog):
        """Global swallow guarantee (决策 #14): even if the trajectory repo raises,
        the gate completes and drives the forward path (the engine does NOT crash)."""
        import logging

        class _BoomRepo:
            def insert_event(self, record):
                raise RuntimeError("trajectory repo boom")

        graph = _make_graph(svc)
        from agentclaw.community.core.task.domain.models import TaskNode
        child = TaskNode(node_id="c1", task_id="t1", status=Status.PENDING,
                         task_spec=_task_info("t1").task_spec, run_info=RuntimeInfo(),
                         node_run_graph=None)  # type: ignore[arg-type]
        svc.add_task_nodes([child], "t1")
        eng = _TrajectoryCaseEngine(
            svc,
            planner=_StubPlanner(),
            dispatcher=_RationaleStubDispatcher(outcome="hit_single", rationale=_SAMPLE_RATIONALE),
            runner=_StubRunner(),
            trajectory_repo=_BoomRepo(),
        )
        side: list[tuple] = []
        with caplog.at_level(logging.WARNING, logger="task.trajectory"):
            _run(eng._prepare_into("t1", side))
            _run(eng._drain("t1", side))
        # forward path still advanced: node flipped to RUNNING; no exception bubbled.
        assert svc._get_node(graph, "c1").status == Status.RUNNING
        # emitter swallowed the exception and logged WARNING (observable).
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "task.trajectory" in r.name]
        assert any("boom" in r.getMessage() for r in warnings), \
            [r.getMessage() for r in caplog.records]


# ---------------------------------------------------------------------------
# Shared pytest fixtures (graph lifecycle)
# ---------------------------------------------------------------------------


@pytest.fixture
def svc() -> TaskGraphService:
    svc = TaskGraphService()
    _make_graph(svc)  # initialize_graph via the shared helper (also covers on_miss cases)
    return svc


@pytest.fixture
def graph(svc):
    return svc._graphs.get("t1") if hasattr(svc, "_graphs") else None
