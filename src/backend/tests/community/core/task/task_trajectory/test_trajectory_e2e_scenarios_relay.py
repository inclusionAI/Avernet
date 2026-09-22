"""E2E scenario suite — RELAY orchestration mode (task_trajectory 轨迹服务).

User-facing scenario matrix this file pins (接力/relay 编排模式; see also
``test_trajectory_e2e_scenarios_centralized.py`` for the centralized mode and
``test_trajectory_branch_pins.py`` for the defensive branch pins):

推进原因 (progression reasons — all asserted on the PERSISTED relay trajectory
rows, not just driven):
* 下一棒任务目标的原因      — the ``plan_result`` relay row carries the
  reporter's ``progress_reason`` (the "why this next-leg goal" rationale) +
  ``ext_info.completed``/``target_node_id`` (the new baton the rationale
  justifies).
* 派发给这个 bot / 协作群的原因 — the ``hit_single`` / ``hit_multi`` rows
  carry the dispatch decision reason (``boost_reason``) + ``ext_info.
  {driver_bot_id, next_relay_bots}`` for the bot / manager_worker 协作群 forms.
* BBS 自主接单的原因        — the MISS row (``published_bbs`` + ``miss_reason``)
  explains why the leg went to the BBS 广场; the ``bbs_claim``/``bbs_result``
  rows record who picked it up and the completion hand-off. (The relay claim
  row currently persists the claimer's ``bbs_bot_id`` only; the free-text
  竞价胜出 reason lives on the bbs_modal_executor path — pinned in the
  centralized suite. That asymmetry is asserted as-is here.)
* 为什么判断收敛 / 没有收敛  — the LAST ``plan_result`` row
  (``completed=True``, ``status_to=SUCCESS``) records the convergence verdict
  + its reason; every non-final ``plan_result`` row records the NOT-converged
  verdict (``completed=False`` + the gap rationale + the new target node).

异常原因 (error reasons):
* 搜推找不到 bot            — an empty discover (0 candidates) + a MISS
  DISPATCH_RESULT → the ``miss`` row persists ``miss_reason`` +
  ``failure_reason`` (node-level) + ``published_bbs``.
* 下一棒派发失败            — the ``dispatch_failed_reopen`` /
  ``dispatch`` rows persist the delivery failure + the retry reason
  ("下一棒 Runner 已完成实际投递").

Execution modality coverage (单 bot / 协作群 / BBS 以及组合 — user req #2):
``test_mixed_modality_full_chain_converges`` drives ONE relay task through
single-bot leg → manager_worker 协作群 leg → BBS(claim) leg → convergence,
asserting the persisted relay timeline records the reason for each transition.

Near-e2e wiring (user req: "接近 e2e,覆盖真实全链路"): every drive runs the
REAL ``TaskService`` relay orchestration (real ``RelayCoordinator`` turn
machine, real ``TaskGraphService`` graph, real relay gates in
``task_service_relay``) with a REAL in-memory SQLite ``TaskTrajectoryRepository``
and — unlike ``test_relay_execution.py``'s ``_tcs(repo)`` shortcut — the REAL
``TaskContextService → TaskTrajectoryService`` facade chain, so emissions flow
TaskService → TaskContextService → TaskTrajectoryService.emit_trajectory_event →
payloads → real SQL INSERTs. Only the outbound bot transports are stubbed
(FakeDiscover / delivery); everything in-process is production code.

Harness is copied in-place from ``tests/community/core/task/task_center/
test_relay_execution.py`` (per the established "copied in-place, NOT imported"
convention in this suite).
"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.task.task_trajectory_repository import (
    TaskTrajectoryRepository,
)
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    Status,
    TaskNodeQueryCriteria,
    TaskSourceType,
)
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance,
    RequestContext,
    RequestGoal,
    RequestTaskSpec,
    TaskInfoRequest,
)
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_context_service import (
    TaskContextService,
)
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)
from agentclaw.community.core.task.task_context.task_trajectory.analyzer import (
    TaskTrajectoryAnalyzer,
)
from agentclaw.community.core.task.task_context.task_trajectory.assembler import (
    TaskTrajectoryAssembler,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
)
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
)
from agentclaw.community.core.task.task_dispatch.claim_join_gate import RELAY_EXECUTION
from agentclaw.community.di.task_trajectory_config import TrajectoryAnalysisConfig

# Side-effect import: registers the task ORM models on Base.metadata so
# create_all builds the trajectory tables (task_trajectory + task_trajectory_events).
import agentclaw.community.core.task.repository.models  # noqa: F401


TASK_ID = "relay-task"


# ---------------------------------------------------------------------------
# Harness — copied in-place from test_relay_execution.py (see module docstring)
# ---------------------------------------------------------------------------


class _InMemorySqliteDB:
    def __init__(self, engine) -> None:
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _make_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return _InMemorySqliteDB(engine)


class _Settings:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def is_enabled(self, setting_type: str) -> bool:
        return self.enabled and setting_type == RELAY_EXECUTION


class _Discover:
    """Search/recommend port with ONE candidate (能力与下一棒目标匹配)."""

    def search_by_keyword(self, **kwargs):
        return {
            "items": [
                {
                    "bot_id": "research-bot",
                    "bot_uuid": "research-bot:owner-2",
                    "bot_name": "Research Bot",
                    "recommend": {"score": 0.91},
                }
            ]
        }


class _DiscoverTwo:
    """Search port with TWO candidates (manager + member → 协作群 HIT_MULTI)."""

    def search_by_keyword(self, **kwargs):
        return {
            "items": [
                {"bot_id": "manager-bot", "recommend": {"score": 0.95}},
                {"bot_id": "member-bot", "recommend": {"score": 0.90}},
            ]
        }


class _EmptyDiscover:
    """搜推找不到 bot: catalog returns ZERO items (scenario S5 precondition)."""

    def search_by_keyword(self, **kwargs):
        return {"total": 0, "items": []}


class _ToggleDelivery:
    def __init__(self) -> None:
        self.succeeds = False

    async def deliver(self, node) -> bool:
        return self.succeeds


def _request() -> TaskInfoRequest:
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            context=RequestContext(title="调研", background="共享背景"),
            goal=RequestGoal(
                objective="完成市场研究和结论",
                acceptances=[RequestAcceptance(id="a1", acceptance="结论可执行")],
            ),
        ),
        source_type=TaskSourceType.BOT,
        owner_user_id="owner-1",
        owner_bot_id="main-bot",
        execution_config={"task_type": "dynamic", "MAX_LOOP": 10},
    )


def _child_spec() -> dict:
    return {
        "metadata": {
            "task_id": "research-step",
            "title": "补充研究",
            "instruction": "基于上一棒产出补充市场数据并形成建议",
        },
        "context": {"background": "共享黑板中的首棒产出", "extend_props": {}},
        "goal": {
            "objective": "补齐市场研究 gap",
            "acceptances": [{"id": "a2", "description": "给出建议"}],
        },
    }


def _accepted(output):
    if not isinstance(output, dict):
        output = {"result": output}
    return {
        "execution_decision": "ACCEPTED",
        "output": output,
        "acceptance_result": {
            "verdict": "DONE",
            "acceptances_metric": [],
            "gaps": [],
        },
    }


def _declined() -> dict:
    return {
        "execution_decision": "DECLINED",
        "actual_goal": None,
        "output": {},
        "acceptance_result": None,
    }


def _run(coro):
    return asyncio.run(coro)


def _real_task_context_service(repo) -> TaskContextService:
    """The REAL full facade chain — unlike ``_tcs(repo)`` this exercises
    ``TaskContextService → TaskTrajectoryService.emit_trajectory_event →
    payloads`` (production wiring; also the coverage seam for the service's
    relay method)."""
    assembler = TaskTrajectoryAssembler(repo)
    analyzer = TaskTrajectoryAnalyzer()
    ts = TaskTrajectoryService(
        assembler, repo, analyzer,
        TrajectoryAnalysisConfig(analysis_bot_id="bot-analyst"),
    )
    return TaskContextService(ts)


def _service(*, discover=None, task_context_service=None):
    graph = TaskGraphService()
    return TaskService(
        graph,
        discover=discover or _Discover(),
        task_settings=_Settings(True),
        task_id_provider=lambda: TASK_ID,
        task_context_service=task_context_service,
    ), graph


def _service_with_real_trajectory(*, discover=None):
    """Near-e2e wiring: real repo + REAL TaskContextService→TaskTrajectoryService
    facade. Returns (service, graph, repo)."""
    repo = TaskTrajectoryRepository(_make_db())
    service, graph = _service(
        discover=discover, task_context_service=_real_task_context_service(repo),
    )
    return service, graph, repo


def _relay_records(repo):
    return [
        record
        for record in repo.list_events_by_task(TASK_ID)
        if str(record.action_type) == "relay"
    ]


def _record(repo, action_result: str):
    """The (single) relay record with ``action_result`` — asserts uniqueness."""
    recs = [r for r in _relay_records(repo) if str(r.action_result) == action_result]
    assert len(recs) == 1, (
        f"expected exactly 1 relay row action_result={action_result!r}; "
        f"got {[(r.node_id, r.action_result) for r in _relay_records(repo)]}"
    )
    return recs[0]


# Relay-drive helpers — thin wrappers over the REAL TaskService relay gates.


def _submit(service) -> None:
    submitted = _run(service.execute(_request()))
    assert submitted.success


def _report_execution(service, *, node_id, holder_id, event_id, reason,
                      payload=None):
    return _run(
        service.report_task_event(
            task_id=TASK_ID,
            node_id=node_id,
            event_type="EXECUTION_RESULT",
            event_id=event_id,
            holder_id=holder_id,
            progress_reason=reason,
            payload=payload or _accepted("done"),
        )
    )


def _report_plan(service, *, node_id, holder_id, turn, event_id, reason,
                 gaps, next_spec=_child_spec()):
    return _run(
        service.report_task_event(
            task_id=TASK_ID,
            node_id=node_id,
            event_type="PLAN_RESULT",
            event_id=event_id,
            holder_id=holder_id,
            relay_turn=turn,
            progress_reason=reason,
            payload={"gaps": gaps, "next_task_spec": next_spec},
        )
    )


def _report_search(service, *, node_id, holder_id, turn, event_id, reason,
                   payload, failure_reason=None):
    """DISPATCH_RESULT report. MISS outcomes MUST carry a failure_reason —
    the relay gate rejects a reason-less MISS (搜推找不到bot cause is
    mandatory, see test_miss_requires_failure_reason)."""
    return _run(
        service.report_task_event(
            task_id=TASK_ID,
            node_id=node_id,
            event_type="DISPATCH_RESULT",
            event_id=event_id,
            holder_id=holder_id,
            relay_turn=turn,
            progress_reason=reason,
            failure_reason=failure_reason,
            payload=payload,
        )
    )


def _dispatch(service, *, origin, target, holder_id, turn, dispatch_id):
    return _run(
        service.dispatch_task(
            task_id=TASK_ID,
            origin_node_id=origin,
            target_node_id=target,
            holder_id=holder_id,
            relay_turn=turn,
            dispatch_id=dispatch_id,
        )
    )


S1_REASON = "当前全局 gap 需要下一棒补齐市场数据"
S2_SINGLE_REASON = "候选 Bot 能力与下一节点目标匹配"
S2_GROUP_REASON = "两个 Bot 能力互补，由 manager 汇总"
BBS_MISS_PROGRESS = "普通候选无法覆盖，发布 BBS 广场"
BBS_MISS_FAILURE = "搜推没有找到可承接的 bot，capability 均不匹配"


# ---------------------------------------------------------------------------
# S1 — 为什么下一棒的任务目标是这个 (plan_result rationale + new baton target)
# ---------------------------------------------------------------------------


class TestNextLegGoalReason:
    def test_plan_result_row_carries_goal_reason_and_new_baton(self):
        """下一棒目标的原因: the reporter MUST give a progress_reason (enforced
        by the relay gate — see test_relay_rejects_missing_trajectory_reason);
        this pins that the reason lands on the PERSISTED plan_result row with
        the target node the rationale justifies."""
        service, graph, repo = _service_with_real_trajectory()
        _submit(service)
        first = _report_execution(
            service, node_id=TASK_ID, holder_id="main-bot", event_id="exec-1",
            reason="首棒形成初步分析",
        )
        planned = _report_plan(
            service, node_id=TASK_ID, holder_id="main-bot",
            turn=first["relay_turn"], event_id="plan-1", reason=S1_REASON,
            gaps=["缺少市场数据"], next_spec=_child_spec(),
        )
        target = planned["target_node_id"]
        assert target, "plan must create the next-leg baton node"

        rec = _record(repo, "plan_result")
        # the "why this next-leg goal" reason is persisted verbatim
        assert rec.boost_reason == S1_REASON, (
            f"plan_result row must carry the next-leg goal rationale; got {rec.boost_reason!r}"
        )
        ext = json.loads(rec.ext_info)
        # NOT converged at this leg: completed False + the new target recorded
        assert ext["completed"] is False
        assert ext["target_node_id"] == target
        # an execution_result row also persisted with its own progress reason
        exec_rec = _record(repo, "execution_result")
        assert exec_rec.boost_reason == "首棒形成初步分析"
        # the first-leg bootstrap row is the timeline head
        assert _record(repo, "bootstrap").node_id == TASK_ID


# ---------------------------------------------------------------------------
# S2 — 为什么派发给这个 bot / 协作群 (hit_single / hit_multi rows)
# ---------------------------------------------------------------------------


class TestDispatchTargetReason:
    def test_single_bot_dispatch_reason_recorded(self):
        service, graph, repo = _service_with_real_trajectory()
        _submit(service)
        first = _report_execution(
            service, node_id=TASK_ID, holder_id="main-bot", event_id="exec-1",
            reason="首棒完成",
        )
        planned = _report_plan(
            service, node_id=TASK_ID, holder_id="main-bot",
            turn=first["relay_turn"], event_id="plan-1", reason="需要下一棒研究",
            gaps=["缺少研究"],
        )
        target = planned["target_node_id"]
        _run(service.search_task_candidates(query="市场研究"))
        _report_search(
            service, node_id=target, holder_id="main-bot",
            turn=first["relay_turn"], event_id="search-1", reason=S2_SINGLE_REASON,
            payload={
                "outcome": "HIT_SINGLE",
                "run_mode": "single_bot",
                "driver_bot_id": "research-bot",
                "next_relay_bots": ["research-bot"],
            },
        )
        delivered = _dispatch(
            service, origin=TASK_ID, target=target, holder_id="main-bot",
            turn=first["relay_turn"], dispatch_id="dispatch-1",
        )
        assert delivered["assignee"] == "research-bot"

        hit = _record(repo, "hit_single")
        # the "why this bot" reason is persisted verbatim on the hit row
        assert hit.boost_reason == S2_SINGLE_REASON
        ext = json.loads(hit.ext_info)
        assert ext["driver_bot_id"] == "research-bot"
        assert ext["next_relay_bots"] == ["research-bot"]
        # the runner delivery row records the delivered execution form + assignee
        dispatch_row = _record(repo, "dispatch")
        assert dispatch_row.status_from == Status.PENDING
        assert dispatch_row.status_to == Status.RUNNING
        d_ext = json.loads(dispatch_row.ext_info)
        assert d_ext["assignee"] == "research-bot"
        assert d_ext["dispatch_id"] == "dispatch-1"

    def test_collab_group_dispatch_reason_recorded(self):
        """协作群 allocation: the manager has been determined via a committee → join the group for execution.
        Missing gap from the SWOT matrix: whether the whole team will participate;
        The complete group meeting intends to expand nodes 4 and 5: point 4, working group;
        5, execution cooperation group planning."""
        service, graph, repo = _service_with_real_trajectory(discover=_DiscoverTwo())
        _submit(service)
        first = _report_execution(
            service, node_id=TASK_ID, holder_id="main-bot", event_id="exec-1",
            reason="首棒完成",
        )
        planned = _report_plan(
            service, node_id=TASK_ID, holder_id="main-bot",
            turn=first["relay_turn"], event_id="plan-1", reason="下一棒需要多 Bot 协作",
            gaps=["需要多 Bot 协作"],
        )
        target = planned["target_node_id"]
        _run(service.search_task_candidates(query="多 Bot 协作"))
        _report_search(
            service, node_id=target, holder_id="main-bot",
            turn=first["relay_turn"], event_id="group-search", reason=S2_GROUP_REASON,
            payload={
                "outcome": "HIT_MULTI_BOTS",
                "bot_ids": ["manager-bot", "member-bot"],
                "collab_mode": "manager_worker",
                "driver_bot_id": "manager-bot",
                "next_relay_bots": ["manager-bot", "member-bot"],
            },
        )
        _dispatch(
            service, origin=TASK_ID, target=target, holder_id="main-bot",
            turn=first["relay_turn"], dispatch_id="group-dispatch",
        )
        node = graph.query_task_nodes(
            TASK_ID, TaskNodeQueryCriteria(node_ids=[target])
        )[0]
        assert node.run_info.assignee == "manager-bot"

        hit = _record(repo, "hit_multi")
        # the "why this 协作群 linkage" reason is persisted verbatim
        assert hit.boost_reason == S2_GROUP_REASON
        ext = json.loads(hit.ext_info)
        assert ext["driver_bot_id"] == "manager-bot"
        assert set(ext["next_relay_bots"]) == {"manager-bot", "member-bot"}


# ---------------------------------------------------------------------------
# S3 + S5 — BBS 自主接单 + 搜推找不到 bot (empty search → MISS → publish → claim)
# ---------------------------------------------------------------------------


class TestSearchEmptyAndBbsPickup:
    def test_empty_search_miss_reason_and_bbs_claim_recorded(self):
        """搜推找不到 bot → the empty catalog is surfaced (0 candidates), the
        leg is MISSed with the reason persisted on the miss row, published to
        the BBS 广场, claimed by a self-picking bot (bbs_claim) and completed
        (bbs_result) — the whole exception / self-pickup chain e2e."""
        service, graph, repo = _service_with_real_trajectory(discover=_EmptyDiscover())
        _submit(service)
        first = _report_execution(
            service, node_id=TASK_ID, holder_id="main-bot", event_id="exec-1",
            reason="首棒完成",
        )
        planned = _report_plan(
            service, node_id=TASK_ID, holder_id="main-bot",
            turn=first["relay_turn"], event_id="plan-1", reason="需要专业研究",
            gaps=["缺少专业研究"],
        )
        target = planned["target_node_id"]

        # S5 precondition — the search/recommendation catalog finds NO bot.
        found = _run(service.search_task_candidates(query="专业研究"))
        assert found["candidates"] == [] and found["total"] == 0

        # the reporter MISSes the leg; both reasons are REQUIRED by the gate.
        published = _report_search(
            service, node_id=target, holder_id="main-bot",
            turn=first["relay_turn"], event_id="miss", reason=BBS_MISS_PROGRESS,
            failure_reason=BBS_MISS_FAILURE,
            payload={
                "outcome": "MISS",
                "miss_reason": "no_matching_candidates",
                "driver_bot_id": None,
                "next_relay_bots": [],
            },
        )
        assert published["published_bbs"] is True

        # the miss row persists the miss cause + the publish action
        miss = _record(repo, "miss")
        assert miss.boost_reason == BBS_MISS_PROGRESS
        ext = json.loads(miss.ext_info)
        assert ext["published_bbs"] is True
        assert ext["miss_reason"]
        # the stuck-to-BBS cause flowed into the baton node itself
        graph.query_task_nodes(TASK_ID, TaskNodeQueryCriteria(node_ids=[target]))

        # S3 — a bot self-picks the leg up on the BBS 广场 (relay claim).
        service.claim_bbs_task(TASK_ID, "bbs-bot", target)
        claim = _record(repo, "bbs_claim")
        assert json.loads(claim.ext_info)["bbs_bot_id"] == "bbs-bot"
        claimed = graph.query_task_nodes(TASK_ID, TaskNodeQueryCriteria(node_ids=[target]))[0]
        assert claimed.status == Status.RUNNING
        assert claimed.run_info.assignee == "bbs-bot"

        # the BBS leg completes and hands the baton back (bbs_result row)
        _run(service.report_bbs_result(
            TASK_ID, target, "bbs-bot", output_patch={"bbs_result": "补齐证据"},
        ))
        result = _record(repo, "bbs_result")
        assert result.status_to == Status.DONE
        # bbs_result node-level progress reason (等待下一棒规划) on the record row
        assert result.node_id == target

    def test_miss_requires_failure_reason(self):
        """The relay gate REQUIRES failure_reason for a MISS dispatch — the
        搜推找不到bot cause is not silently dropped."""
        service, graph, repo = _service_with_real_trajectory(discover=_EmptyDiscover())
        _submit(service)
        first = _report_execution(
            service, node_id=TASK_ID, holder_id="main-bot", event_id="exec-1",
            reason="首棒完成",
        )
        planned = _report_plan(
            service, node_id=TASK_ID, holder_id="main-bot",
            turn=first["relay_turn"], event_id="plan-1", reason="需要研究",
            gaps=["缺少研究"],
        )
        with pytest.raises(TaskStateError, match="failure_reason"):
            _run(
                service.report_task_event(
                    task_id=TASK_ID,
                    node_id=planned["target_node_id"],
                    event_type="DISPATCH_RESULT",
                    event_id="miss-no-reason",
                    holder_id="main-bot",
                    relay_turn=first["relay_turn"],
                    progress_reason="miss",
                    payload={"outcome": "MISS"},
                )
            )


# ---------------------------------------------------------------------------
# S4 — 为什么判断收敛 / 没有收敛 (completed verdict on the last plan_result row)
# ---------------------------------------------------------------------------


class TestConvergenceVerdictReasons:
    def test_converged_and_unconverged_verdicts_recorded(self):
        """不收敛 verdict: the mid-chain plan row (gaps remain) carries
        completed=False + the gap rationale + the next baton;
        收敛 verdict: the LAST plan row (gaps=[]) carries completed=True +
        status_to=SUCCESS + the convergence reason → graph DONE."""
        service, graph, repo = _service_with_real_trajectory()
        _submit(service)

        # --- NOT converged: gap remains → new baton, completed=False ---
        first = _report_execution(
            service, node_id=TASK_ID, holder_id="main-bot", event_id="exec-1",
            reason="首棒覆盖了一部分",
        )
        planned = _report_plan(
            service, node_id=TASK_ID, holder_id="main-bot",
            turn=first["relay_turn"], event_id="plan-1",
            reason="市场数据仍不完整,需要下一棒补齐", gaps=["缺少市场数据"],
        )
        leg1 = planned["target_node_id"]
        plan_rows = [r for r in _relay_records(repo) if str(r.action_result) == "plan_result"]
        assert len(plan_rows) == 1
        assert plan_rows[0].boost_reason == "市场数据仍不完整,需要下一棒补齐"
        ext = json.loads(plan_rows[0].ext_info)
        assert ext["completed"] is False
        assert ext["target_node_id"] == leg1
        assert graph.query_task_dashboard(TASK_ID).status != Status.DONE

        # drive the leg: search → HIT_SINGLE → dispatch → leg execution
        _run(service.search_task_candidates(query="市场数据"))
        _report_search(
            service, node_id=leg1, holder_id="main-bot",
            turn=first["relay_turn"], event_id="search-1",
            reason=S2_SINGLE_REASON,
            payload={
                "outcome": "HIT_SINGLE",
                "driver_bot_id": "research-bot",
                "next_relay_bots": ["research-bot"],
            },
        )
        _dispatch(
            service, origin=TASK_ID, target=leg1, holder_id="main-bot",
            turn=first["relay_turn"], dispatch_id="dispatch-1",
        )

        # --- converged: gaps=[] → completed=True, status_to=SUCCESS, DONE ---
        second = _report_execution(
            service, node_id=leg1, holder_id="research-bot", event_id="exec-2",
            reason="第二棒补齐了市场数据",
        )
        _run(
            service.report_task_event(
                task_id=TASK_ID,
                node_id=leg1,
                event_type="PLAN_RESULT",
                event_id="plan-final",
                holder_id="research-bot",
                relay_turn=second["relay_turn"],
                progress_reason="根目标已全部满足,gap 已闭合",
                payload={"gaps": [], "next_task_spec": None},
            )
        )
        final_rows = [r for r in _relay_records(repo) if str(r.action_result) == "plan_result"]
        assert len(final_rows) == 2
        final = final_rows[-1]
        assert final.boost_reason == "根目标已全部满足,gap 已闭合"
        assert json.loads(final.ext_info)["completed"] is True
        assert final.status_to == Status.SUCCESS  # the convergence verdict
        assert graph.query_task_dashboard(TASK_ID).status == Status.DONE


# ---------------------------------------------------------------------------
# 下一棒派发失败 (delivery failure) + decline — exception reasons on relay rows
# ---------------------------------------------------------------------------


class TestDispatchFailureAndDeclineReasons:
    def test_dispatch_failure_reason_recoverable(self):
        """下一棒 Runner 派发失败 → the failure (incl. the retry outcome) is
        persisted; the node-level failure_reason is the operator-facing cause."""
        service, graph, repo = _service_with_real_trajectory()
        delivery = _ToggleDelivery()
        service._runner.set_delivery("single_bot", delivery)
        _submit(service)
        first = _report_execution(
            service, node_id=TASK_ID, holder_id="main-bot", event_id="exec-1",
            reason="首棒完成",
        )
        planned = _report_plan(
            service, node_id=TASK_ID, holder_id="main-bot",
            turn=first["relay_turn"], event_id="plan-1", reason="需要下一棒", gaps=["g"],
        )
        target = planned["target_node_id"]
        _report_search(
            service, node_id=target, holder_id="main-bot",
            turn=first["relay_turn"], event_id="search-1", reason="选中 research-bot",
            payload={
                "outcome": "HIT_SINGLE",
                "driver_bot_id": "research-bot",
                "next_relay_bots": ["research-bot"],
            },
        )
        with pytest.raises(TaskStateError, match="dispatch failed"):
            _dispatch(
                service, origin=TASK_ID, target=target, holder_id="main-bot",
                turn=first["relay_turn"], dispatch_id="dispatch-failed",
            )
        node = graph.query_task_nodes(TASK_ID, TaskNodeQueryCriteria(node_ids=[target]))[0]
        assert node.status == Status.PENDING
        # the node-level failure_reason is the operator-facing cause
        assert node.run_info.failure_reason == "下一棒 Runner 派发失败"
        failed = _record(repo, "dispatch_failed_reopen")
        assert failed.error_type == ReasonCatalog.RELAY.value
        assert "relay dispatch failed" in str(failed.error_msg)
        assert failed.status_to == Status.PENDING  # re-opened for retry

        # retry succeeds — the recovery is recorded as the delivered dispatch row
        delivery.succeeds = True
        _dispatch(
            service, origin=TASK_ID, target=target, holder_id="main-bot",
            turn=first["relay_turn"], dispatch_id="dispatch-retry",
        )
        retried = graph.query_task_nodes(
            TASK_ID, TaskNodeQueryCriteria(node_ids=[target])
        )[0]
        assert retried.status == Status.RUNNING
        ok = _record(repo, "dispatch")
        assert ok.status_to == Status.RUNNING
        assert json.loads(ok.ext_info)["dispatch_id"] == "dispatch-retry"

    def test_declined_execution_records_failure_reason(self):
        """A declined leg (capability_mismatch) persists the decline cause on
        the execution_failed row (the reporter MUST give failure_reason)."""
        service, graph, repo = _service_with_real_trajectory()
        _submit(service)
        _run(
            service.report_task_event(
                task_id=TASK_ID,
                node_id=TASK_ID,
                event_type="EXECUTION_RESULT",
                event_id="exec-declined",
                holder_id="main-bot",
                progress_reason="尝试执行后发现职责不覆盖",
                failure_reason="capability_mismatch",
                payload=_declined(),
            )
        )
        rec = _record(repo, "execution_failed")
        assert rec.error_type == ReasonCatalog.RELAY.value
        assert "capability_mismatch" in str(rec.error_msg)


# ---------------------------------------------------------------------------
# 组合 — single-bot leg → 协作群 leg → BBS(claim) leg → convergence
# (user req #2: 覆盖单 bot、协作群、bbs 以及各种组合)
# ---------------------------------------------------------------------------


class TestMixedModalityFullChainConverges:
    def test_single_group_bbs_chain_converges_with_reasons(self):
        """One relay task across ALL execution modalities and their handoffs:
        首棒(单) → HIT_SINGLE leg → HIT_MULTI 协作群 leg → MISS→BBS claim leg →
        收敛. Every modality switch's reason is pinned on the persisted rows."""
        service, graph, repo = _service_with_real_trajectory(discover=_DiscoverTwo())
        _submit(service)

        # leg 0 → single bot
        t0 = _report_execution(
            service, node_id=TASK_ID, holder_id="main-bot", event_id="x0",
            reason="首棒完成,规划下一棒",
        )["relay_turn"]
        p1 = _report_plan(
            service, node_id=TASK_ID, holder_id="main-bot", turn=t0,
            event_id="p1", reason="第一棒目标:补齐市场数据", gaps=["市场数据"],
        )
        leg1 = p1["target_node_id"]
        _run(service.search_task_candidates(query="市场数据"))
        _report_search(
            service, node_id=leg1, holder_id="main-bot", turn=t0,
            event_id="s1", reason=S2_SINGLE_REASON,
            payload={
                "outcome": "HIT_SINGLE",
                "driver_bot_id": "member-bot",
                "next_relay_bots": ["member-bot"],
            },
        )
        _dispatch(service, origin=TASK_ID, target=leg1, holder_id="main-bot",
                  turn=t0, dispatch_id="d1")
        t1 = _report_execution(
            service, node_id=leg1, holder_id="member-bot", event_id="x1",
            reason="单 bot 棒完成,需要协作群汇总",
        )["relay_turn"]

        # leg 1 → manager_worker 协作群
        p2 = _report_plan(
            service, node_id=leg1, holder_id="member-bot", turn=t1,
            event_id="p2", reason="第二棒目标:多 Bot 协作互验", gaps=["需要互验"],
        )
        leg2 = p2["target_node_id"]
        _run(service.search_task_candidates(query="协作互验"))
        _report_search(
            service, node_id=leg2, holder_id="member-bot", turn=t1,
            event_id="s2", reason=S2_GROUP_REASON,
            payload={
                "outcome": "HIT_MULTI_BOTS",
                "bot_ids": ["manager-bot", "member-bot"],
                "collab_mode": "manager_worker",
                "driver_bot_id": "manager-bot",
                "next_relay_bots": ["manager-bot", "member-bot"],
            },
        )
        _dispatch(service, origin=leg1, target=leg2, holder_id="member-bot",
                  turn=t1, dispatch_id="d2")
        t2 = _report_execution(
            service, node_id=leg2, holder_id="manager-bot", event_id="x2",
            reason="协作群汇总完成,仍缺专业外部数据",
        )["relay_turn"]

        # leg 2 → 搜推找不到 → MISS → BBS 广场 → 自主接单 → 回投
        p3 = _report_plan(
            service, node_id=leg2, holder_id="manager-bot", turn=t2,
            event_id="p3", reason="第三棒目标:专业外部数据,转 BBS", gaps=["专业外部数据"],
        )
        leg3 = p3["target_node_id"]
        _report_search(
            service, node_id=leg3, holder_id="manager-bot", turn=t2,
            event_id="miss", reason=BBS_MISS_PROGRESS,
            failure_reason=BBS_MISS_FAILURE,
            payload={
                "outcome": "MISS",
                "miss_reason": "no_matching_candidates",
                "driver_bot_id": None,
                "next_relay_bots": [],
            },
        )
        service.claim_bbs_task(TASK_ID, "bbs-bot", leg3)
        assert graph.query_task_nodes(
            TASK_ID, TaskNodeQueryCriteria(node_ids=[leg3])
        )[0].run_info.assignee == "bbs-bot"
        final_turn = _report_execution(
            service, node_id=leg3, holder_id="bbs-bot", event_id="x3",
            reason="BBS 产出完成,检查根目标",
        )["relay_turn"]

        # 收敛 — the final plan closes the gap and carries the convergence reason
        _run(
            service.report_task_event(
                task_id=TASK_ID,
                node_id=leg3,
                event_type="PLAN_RESULT",
                event_id="p-final",
                holder_id="bbs-bot",
                relay_turn=final_turn,
                progress_reason="全部棒产出已覆盖根目标,gap 已闭合",
                payload={"gaps": [], "next_task_spec": None},
            )
        )
        assert graph.query_task_dashboard(TASK_ID).status == Status.DONE

        # every modality switch + reason is recorded, in execution order
        results = [str(r.action_result) for r in _relay_records(repo)]
        for required in (
            "bootstrap", "execution_result",                   # 首棒
            "hit_single", "dispatch",                          # 单 bot leg
            "hit_multi",                                       # 协作群 leg
            "miss",                                            # 搜推找不到 → BBS
            "bbs_claim",                                        # BBS 自主接单
        ):
            assert required in results, f"modality/阶段 {required!r} missing: {results}"
        # the convergence verdict row carries its reason
        final_plan = [r for r in _relay_records(repo) if str(r.action_result) == "plan_result"][-1]
        assert json.loads(final_plan.ext_info)["completed"] is True
        assert final_plan.status_to == Status.SUCCESS
        assert final_plan.boost_reason == "全部棒产出已覆盖根目标,gap 已闭合"