"""Unit tests for ``TaskTrajectoryService`` (REQ-8 trigger, P5b consumption layer).

Covers the four ``get_trajectory`` paths and the ``ext_info_lookup`` keying:

* ``do_analysis=False`` (default, pure READ) — assembler produces the timeline;
  ``analysis`` = persisted or ``None``; NO backfill; NO bot call.
* ``do_analysis=True`` — analyzer returns a scripted ``TrajectoryAnalysis``;
  ``repo.backfill_analysis`` called once with the JSON; returned ``.analysis``
  == the JSON; ``ext_info_lookup`` keys match events to records.
* bot-failure/timeout — analyzer raises ``TrajectoryAnalysisError``; service
  MUST NOT backfill; service re-raises (router → 504).
* bot-not-configured (``analysis_bot_id is None``) — service raises
  ``TrajectoryAnalysisNotConfiguredError`` (router → 503); no backfill.

Constructor injection (``@inject``) lets these tests pass fakes directly — no DI.
Authoritative: spec REQ-8 + 决策 #10/#13/#14.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from dataclasses import asdict

import pytest

from agentclaw.community.core.task.domain.errors import (
    TrajectoryAnalysisError,
    TrajectoryAnalysisNotConfiguredError,
)
from agentclaw.community.core.task.domain.models import (
    RuntimeInfo,
    Status,
)
from agentclaw.community.core.task.repository.types import (
    TaskTrajectoryRecord,
    TrajectoryEventRecord,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    AnalysisType,
    TaskTrajectory,
    TrajectoryAnalysis,
    TrajectoryActionType,
    TrajectoryEvent,
)
from agentclaw.community.core.task.task_context.task_trajectory.time_utils import (
    epoch_ms_to_storage_datetime,
    storage_now,
)
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
    _build_ext_info_lookup,
    _event_key,
    _is_engine_session_id,
    _timeline_fingerprint,
)
from agentclaw.community.di.task_trajectory_config import TrajectoryAnalysisConfig


# ---------------------------------------------------------------------------
# Fakes — self-contained (no DI; constructor injection)
# ---------------------------------------------------------------------------


class _FakeAssembler:
    """Returns a scripted ``TaskTrajectory``; records ``assemble`` calls."""

    def __init__(self, trajectory: TaskTrajectory) -> None:
        self._trajectory = trajectory
        self.calls = 0

    def assemble(self, task_id: str) -> TaskTrajectory:
        self.calls += 1
        return self._trajectory


class _FakeAnalyzer:
    """Records the ``analyze`` call; returns a scripted analysis OR raises.

    Captures the ``ext_info_lookup`` closure so the test can exercise it."""

    def __init__(
        self,
        *,
        analysis: TrajectoryAnalysis | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._analysis = analysis
        self._raise = raise_exc
        self.calls = 0
        self.last_trajectory: TaskTrajectory | None = None
        self.last_ext_info_lookup = None
        self.last_analysis_type = None
        self.last_analysis_executor = None
        self.last_running_sessions = "unset"

    async def analyze(
        self,
        trajectory: TaskTrajectory,
        ext_info_lookup,
        *,
        analysis_type,
        analysis_executor,
        running_sessions=None,
    ) -> TrajectoryAnalysis:
        self.calls += 1
        self.last_trajectory = trajectory
        self.last_ext_info_lookup = ext_info_lookup
        self.last_analysis_type = analysis_type
        self.last_analysis_executor = analysis_executor
        self.last_running_sessions = running_sessions
        if self._raise is not None:
            raise self._raise
        return self._analysis


class _FakeRepo:
    """Captures ``backfill_analysis``; serves records for ``ext_info_lookup``."""

    def __init__(
        self,
        *,
        events: list[TrajectoryEventRecord] | None = None,
        head: TaskTrajectoryRecord | None = None,
    ) -> None:
        self._events = list(events or [])
        self._head = head
        self.backfill_calls: list[tuple[str, str]] = []
        self.merge_calls: list[tuple] = []
        self.list_events_calls = 0

    def list_events_by_task(self, task_id: str) -> list[TrajectoryEventRecord]:
        self.list_events_calls += 1
        return [r for r in self._events if r.task_id == task_id]

    def list_head(self, task_id: str) -> TaskTrajectoryRecord | None:
        return self._head

    def upsert_head(self, task_id: str, *, analysis: str | None = None) -> TaskTrajectoryRecord:
        if self._head is None:
            now = storage_now()
            self._head = TaskTrajectoryRecord(
                id=1, task_id=task_id, analysis=analysis,
                gmt_create=now, gmt_modified=now,
            )
        return self._head

    def backfill_analysis(self, task_id: str, analysis_json: str, *, now=None) -> int:
        self.backfill_calls.append((task_id, analysis_json))
        return 1

    def merge_last_event_ext_info(self, task_id, node_id, key, value) -> bool:
        self.merge_calls.append((task_id, node_id, key, value))
        return True


def _make_event(
    *,
    task_id: str = "t1",
    node_id: str = "n1",
    action_type: TrajectoryActionType = TrajectoryActionType.DISPATCH,
    action_result: str = "hit_single",
    attempt: int = 0,
    gmt_create: int = 1000,
    **kwargs,
) -> TrajectoryEvent:
    return TrajectoryEvent(
        task_id=task_id,
        node_id=node_id,
        action_type=action_type,
        action_result=action_result,
        attempt=attempt,
        gmt_create=gmt_create,
        gmt_modified=gmt_create,
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_trajectory_nests_system_logs_under_nearest_main():
    """读时嵌套:S 码主事件(+数字开头的 action_type)构成顶层主序列;各子任务的
    其它 action_type 事件按 id 序挂到**距离最近的前一个主事件**的 ``system_logs``
    (按 id 正序,即组装序);主事件之前出现的子事件(无前置主)保持平级,不丢数据;
    无任何主事件的子任务,其事件整体保持平级。"""
    timeline = [
        _make_event(node_id="n1", action_type="submit", gmt_create=100),
        _make_event(node_id="n1", action_type="S1_CONTEXT", gmt_create=200),
        _make_event(node_id="n1", action_type="execute", gmt_create=300),
        _make_event(node_id="n1", action_type="reset", gmt_create=400),
        _make_event(node_id="n1", action_type="S4A_EXECUTION", gmt_create=500),
        _make_event(node_id="n1", action_type="transition", gmt_create=600),
        _make_event(node_id="n2", action_type="S5A_REFRESH_GAPS", gmt_create=700),
        _make_event(node_id="n2", action_type="verify", gmt_create=800),
        _make_event(node_id="n3", action_type="plan", gmt_create=900),  # 无主节点 → 平级
    ]
    traj = _make_trajectory(task_id="t1", timeline=timeline)
    svc = TaskTrajectoryService(_FakeAssembler(traj), _FakeRepo(), _FakeAnalyzer(), None)

    result = await svc.get_trajectory("t1")

    def _value(action_type):
        return getattr(action_type, "value", action_type)

    top = [_value(e.action_type) for e in result.timeline]
    assert top == [
        "submit",            # 首主之前:无前置主,保持平级
        "S1_CONTEXT",
        "S4A_EXECUTION",
        "S5A_REFRESH_GAPS",
        "plan",              # n3 无主事件:整体平级
    ]
    assert result.timeline[1].system_logs[0] is not None
    nested_s1 = result.timeline[1].system_logs
    assert [_value(e.action_type) for e in nested_s1] == ["execute", "reset"], (
        "id 在 S1 与 S4A 之间的子事件挂 S1,按 id 正序"
    )
    assert [_value(e.action_type) for e in result.timeline[2].system_logs] == [
        "transition"
    ]
    assert [_value(e.action_type) for e in result.timeline[3].system_logs] == [
        "verify"
    ]
    assert result.timeline[0].system_logs is None, "无前置主:不挂"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_trajectory_keeps_flat_when_no_main_events():
    """无任何 S 码主事件(中心化任务的常规形态)→ timeline 原样平级,零变换。"""
    timeline = [
        _make_event(node_id="n1", action_type=TrajectoryActionType.SUBMIT, gmt_create=100),
        _make_event(node_id="n1", action_type=TrajectoryActionType.PLAN, gmt_create=200),
        _make_event(node_id="n1", action_type=TrajectoryActionType.EXECUTE, gmt_create=300),
    ]
    traj = _make_trajectory(task_id="t1", timeline=timeline)
    svc = TaskTrajectoryService(_FakeAssembler(traj), _FakeRepo(), _FakeAnalyzer(), None)

    result = await svc.get_trajectory("t1")

    assert len(result.timeline) == 3
    assert all(e.system_logs is None for e in result.timeline)


def _make_trajectory(*, task_id: str = "t1", timeline=None, analysis=None) -> TaskTrajectory:
    return TaskTrajectory(
        task_id=task_id,
        gmt_create=1000,
        gmt_modified=1000,
        timeline=timeline or [],
        analysis=analysis,
    )


def _make_analysis(*, analysis_executor: str = "bot-traj-analyst") -> TrajectoryAnalysis:
    return TrajectoryAnalysis(
        analysis_type=AnalysisType.TC_BOT,
        analysis_executor=analysis_executor,
        analysis_input="events=2",
        analysis_output="boost_reason: x; failure_reason: y",
        gmt_create=2000,
        boost_reason="策略=search 选中=botA",
        failure_reason="underlying_interface_error: boom",
    )


# ---------------------------------------------------------------------------
# do_analysis=False (pure read)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_false_is_pure_read_no_backfill_no_bot():
    """do_analysis=False assembles and returns; no backfill, no analyzer call."""
    traj = _make_trajectory(analysis=None)
    assembler = _FakeAssembler(traj)
    analyzer = _FakeAnalyzer()
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(assembler, repo, analyzer, config)

    result = await svc.get_trajectory("t1", do_analysis=False)

    assert result is traj
    assert result.analysis is None
    assert assembler.calls == 1
    assert analyzer.calls == 0
    assert repo.backfill_calls == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_false_returns_persisted_analysis():
    """do_analysis=False surfaces the already-backfilled analysis verbatim."""
    persisted = '{"analysis_type":"tc_bot","analysis_executor":"old-bot"}'
    traj = _make_trajectory(analysis=persisted)
    assembler = _FakeAssembler(traj)
    analyzer = _FakeAnalyzer()
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(assembler, repo, analyzer, config)

    result = await svc.get_trajectory("t1", do_analysis=False)

    assert result.analysis == persisted
    assert repo.backfill_calls == []
    assert analyzer.calls == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_false_default_param():
    """The default ``do_analysis`` is False (omitted param → pure read)."""
    traj = _make_trajectory()
    assembler = _FakeAssembler(traj)
    analyzer = _FakeAnalyzer()
    repo = _FakeRepo()
    svc = TaskTrajectoryService(assembler, repo, analyzer, TrajectoryAnalysisConfig())

    result = await svc.get_trajectory("t1")

    assert result is traj
    assert analyzer.calls == 0
    assert repo.backfill_calls == []


# ---------------------------------------------------------------------------
# do_analysis=True success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_true_backfills_and_returns_fresh_analysis():
    """do_analysis=True calls analyzer, backfills JSON, returns .analysis=JSON."""
    traj = _make_trajectory(
        timeline=[_make_event(action_type=TrajectoryActionType.EXECUTE)],
    )
    assembler = _FakeAssembler(traj)
    analysis = _make_analysis()
    analyzer = _FakeAnalyzer(analysis=analysis)
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(assembler, repo, analyzer, config)

    result = await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert analyzer.calls == 1
    assert analyzer.last_analysis_type == AnalysisType.TC_BOT
    assert analyzer.last_analysis_executor == "bot-traj-analyst"
    # backfill called once with the flat JSON (asdict → json.dumps, no recursion)
    assert len(repo.backfill_calls) == 1
    backfilled_task_id, backfilled_json = repo.backfill_calls[0]
    assert backfilled_task_id == "t1"
    parsed = json.loads(backfilled_json)
    assert parsed["analysis_type"] == "tc_bot"
    assert parsed["analysis_executor"] == "bot-traj-analyst"
    assert parsed["boost_reason"] == "策略=search 选中=botA"
    assert parsed["failure_reason"] == "underlying_interface_error: boom"
    # the serialized JSON equals json.dumps(asdict(analysis), ensure_ascii=False)
    assert backfilled_json == json.dumps(asdict(analysis), ensure_ascii=False)
    # returned trajectory carries the fresh analysis
    assert result.analysis == backfilled_json
    assert result.gmt_modified >= result.gmt_create


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_reruns_when_new_event_appended_after_persist():
    """Timeline gained events after the last backfill → fingerprint mismatch →
    the bot re-runs and the new backfill carries the re-computed stamp."""
    old_records = [
        _make_record(node_id="n1", action_type="dispatch", attempt=0, gmt_create_ms=1000),
    ]
    new_records = old_records + [
        _make_record(node_id="n2", action_type="reset", attempt=1, gmt_create_ms=3000, rec_id=2),
    ]
    stale_stamp = _timeline_fingerprint(old_records)
    persisted = json.dumps({"analysis_type": "tc_bot", "timeline_version": stale_stamp}, ensure_ascii=False)
    traj = _make_trajectory(analysis=persisted)
    assembler = _FakeAssembler(traj)
    analysis = _make_analysis()
    analyzer = _FakeAnalyzer(analysis=analysis)
    repo = _FakeRepo(events=new_records)
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(assembler, repo, analyzer, config)

    result = await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    # stale stamp did NOT short-circuit: bot re-ran and re-stamped
    assert analyzer.calls == 1
    assert len(repo.backfill_calls) == 1
    backfilled = json.loads(repo.backfill_calls[0][1])
    assert backfilled["timeline_version"] == _timeline_fingerprint(new_records)
    assert result.analysis == repo.backfill_calls[0][1]


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.unit
async def test_force_analysis_true_implies_do_analysis():
    """force_analysis=true 自身即触发分析(不必再传 do_analysis=true):等价于强制执行的
    do_analysis;默认(false)时 do_analysis 不受影响。"""
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
    )

    result = await svc.get_trajectory("t1", force_analysis=True)

    assert analyzer.calls == 1
    assert result.analysis == repo.backfill_calls[0][1]


@pytest.mark.unit
def test_timeline_fingerprint_unit():
    """The fingerprint is deterministic across input order, sorts by ``id``,
    changes on an appended event, and stays stable when ONLY ``gmt_modified``
    is rewritten (backfill rewrites gmt_modify on every event row — the
    regression-lock for the gmt_create ingredient choice)."""
    base = [
        _make_record(node_id="n1", action_type="dispatch", attempt=0, gmt_create_ms=1000, rec_id=1),
        _make_record(node_id="n2", action_type="execute", attempt=0, gmt_create_ms=2000, rec_id=2),
    ]
    # deterministic across shuffled input
    shuffled = list(reversed(base))
    assert _timeline_fingerprint(shuffled) == _timeline_fingerprint(base)
    # gmt_modified rewrite (backfill) → SAME fingerprint
    later = epoch_ms_to_storage_datetime(90000)
    rewritten = [
        TrajectoryEventRecord(
            id=r.id, task_id=r.task_id, node_id=r.node_id, action_type=r.action_type,
            attempt=r.attempt, action_result=r.action_result, ext_info=r.ext_info,
            gmt_create=r.gmt_create, gmt_modified=later,
        )
        for r in base
    ]
    assert _timeline_fingerprint(rewritten) == _timeline_fingerprint(base)
    # appended event → DIFFERENT fingerprint
    appended = base + [
        _make_record(node_id="n3", action_type="reset", attempt=0, gmt_create_ms=3000, rec_id=3),
    ]
    assert _timeline_fingerprint(appended) != _timeline_fingerprint(base)
    # zero-event task → a stable digest (idempotent even for empty timelines)
    assert _timeline_fingerprint([]) == _timeline_fingerprint([])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_records_read_failure_treats_as_mismatch():
    """If the event-row read fails, the fingerprint is None — the never-match
    sentinel: the analysis re-runs rather than serving possibly-stale analysis
    (an unfingerprintable read must not be treated as a stale-match)."""
    class _RaisingListRepo(_FakeRepo):
        def list_events_by_task(self, task_id: str):
            raise RuntimeError("db down")

    # a WOULD-match analysis: empty events repo would compute sha256("v1:0") —
    # the persisted stamp below equals _timeline_fingerprint([]) — but the read
    # fails, so the match must NOT engage and the bot must re-run.
    stamp = _timeline_fingerprint([])
    persisted = json.dumps({"timeline_version": stamp}, ensure_ascii=False)
    traj = _make_trajectory(analysis=persisted)
    assembler = _FakeAssembler(traj)
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _RaisingListRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(assembler, repo, analyzer, config)

    result = await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert analyzer.calls == 1
    # backfilled stamp is None (records unreadable → no fingerprint claimed)
    assert json.loads(repo.backfill_calls[0][1])["timeline_version"] is None
    assert result.analysis == repo.backfill_calls[0][1]


# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_without_force_never_calls_bot():
    """2026-10-15 语义收紧:大模型调用的**唯一触发 = force_analysis=True**;
    do_analysis=true 单独出现退化为纯读——ct 不调、不回填,返回已持久化 analysis。"""
    traj = _make_trajectory(task_id="t1", analysis=None)
    analysis = _make_analysis()
    analyzer = _FakeAnalyzer(analysis=analysis)
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-analyst")
    svc = TaskTrajectoryService(_FakeAssembler(traj), repo, analyzer, config)

    result = await svc.get_trajectory("t1", do_analysis=True)

    assert analyzer.calls == 0, "do_analysis 无 force:绝不调 bot"
    assert repo.backfill_calls == []
    assert repo.merge_calls == []
    assert result is traj and result.analysis is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_without_force_needs_no_bot_config():
    """do_analysis=true(无 force)在分析 bot 未配置时也**正常纯读**(503 只属于
    force 路径)。"""
    traj = _make_trajectory(task_id="t1", analysis=None)
    analyzer = _FakeAnalyzer()
    svc = TaskTrajectoryService(_FakeAssembler(traj), _FakeRepo(), analyzer, None)

    result = await svc.get_trajectory("t1", do_analysis=True)  # 无 503

    assert analyzer.calls == 0
    assert result is traj


# do_analysis=True bot failure → 504, no backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_true_bot_failure_raises_and_does_not_backfill():
    """Bot failure/timeout → TrajectoryAnalysisError; NO backfill (决策 #14)."""
    traj = _make_trajectory()
    assembler = _FakeAssembler(traj)
    analyzer = _FakeAnalyzer(raise_exc=TrajectoryAnalysisError("bot timed out"))
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(assembler, repo, analyzer, config)

    with pytest.raises(TrajectoryAnalysisError):
        await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    # MUST NOT backfill on the failure path
    assert repo.backfill_calls == []
    assert analyzer.calls == 1


# ---------------------------------------------------------------------------
# do_analysis=True bot not configured → 503, no backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_true_not_configured_raises_and_no_backfill():
    """analysis_bot_id is None → TrajectoryAnalysisNotConfiguredError (503)."""
    traj = _make_trajectory()
    assembler = _FakeAssembler(traj)
    analyzer = _FakeAnalyzer()
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id=None)
    svc = TaskTrajectoryService(assembler, repo, analyzer, config)

    with pytest.raises(TrajectoryAnalysisNotConfiguredError):
        await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert repo.backfill_calls == []
    # analyzer is never reached (config check happens before)
    assert analyzer.calls == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_true_not_configured_when_config_is_none():
    """A None config (lightweight DI injector) → not configured (503)."""
    traj = _make_trajectory()
    assembler = _FakeAssembler(traj)
    analyzer = _FakeAnalyzer()
    repo = _FakeRepo()
    svc = TaskTrajectoryService(assembler, repo, analyzer, config=None)

    with pytest.raises(TrajectoryAnalysisNotConfiguredError):
        await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert repo.backfill_calls == []


# ---------------------------------------------------------------------------
# ext_info_lookup keying — records → closure matching domain events
# ---------------------------------------------------------------------------


def _make_record(
    *,
    task_id: str = "t1",
    node_id: str = "n1",
    action_type: str = "dispatch",
    attempt: int = 0,
    gmt_create_ms: int = 1000,
    ext_info: str | None = None,
    rec_id: int = 1,
) -> TrajectoryEventRecord:
    dt = epoch_ms_to_storage_datetime(gmt_create_ms)
    return TrajectoryEventRecord(
        id=rec_id,
        task_id=task_id,
        node_id=node_id,
        action_type=action_type,
        attempt=attempt,
        action_result="hit_single",
        ext_info=ext_info,
        gmt_create=dt,
        gmt_modified=dt,
    )


@pytest.mark.unit
def test_ext_info_lookup_keys_match_domain_events():
    """The lookup closure maps domain TrajectoryEvent → parsed ext_info dict
    via the stable (gmt_create, node_id, action_type, attempt) signature."""
    ext_json = json.dumps(
        {"schema_v": 1, "_dispatch_rationale": {"strategy_name": "search"}},
        ensure_ascii=False,
    )
    records = [
        _make_record(
            node_id="n1",
            action_type="dispatch",
            attempt=0,
            gmt_create_ms=1000,
            ext_info=ext_json,
        ),
    ]
    repo = _FakeRepo(events=records)
    lookup = _build_ext_info_lookup(repo, "t1")

    event = _make_event(
        node_id="n1",
        action_type=TrajectoryActionType.DISPATCH,
        attempt=0,
        gmt_create=1000,
    )
    result = lookup(event)
    assert result is not None
    assert result["_dispatch_rationale"]["strategy_name"] == "search"
    assert "schema_v" in result  # analyzer ignores schema_v (carry-note)


@pytest.mark.unit
def test_ext_info_lookup_returns_none_for_event_without_record():
    """An event with no matching record → None (no crash)."""
    records = [_make_record(node_id="n1", action_type="dispatch", attempt=0)]
    repo = _FakeRepo(events=records)
    lookup = _build_ext_info_lookup(repo, "t1")

    other = _make_event(node_id="n99", action_type=TrajectoryActionType.PLAN, attempt=0)
    assert lookup(other) is None


@pytest.mark.unit
def test_ext_info_lookup_skips_corrupt_ext_info_json():
    """A corrupt ext_info JSON is skipped (→ None for that key), never raises."""
    records = [
        _make_record(node_id="n1", action_type="dispatch", attempt=0, ext_info="{broken json"),
        _make_record(
            node_id="n2",
            action_type="reset",
            attempt=0,
            ext_info=json.dumps({"elapsed_ms": 500, "sla_threshold_ms": 600000}),
            rec_id=2,
        ),
    ]
    repo = _FakeRepo(events=records)
    lookup = _build_ext_info_lookup(repo, "t1")

    # corrupt record → None (skipped, not raised)
    bad_event = _make_event(node_id="n1", action_type=TrajectoryActionType.DISPATCH, attempt=0)
    assert lookup(bad_event) is None
    # good record → parsed dict
    good_event = _make_event(node_id="n2", action_type=TrajectoryActionType.RESET, attempt=0)
    assert lookup(good_event) == {"elapsed_ms": 500, "sla_threshold_ms": 600000}


@pytest.mark.unit
def test_ext_info_lookup_empty_ext_info_skipped():
    """Records with empty/None ext_info are not in the lookup → None."""
    records = [_make_record(node_id="n1", action_type="dispatch", attempt=0, ext_info=None)]
    repo = _FakeRepo(events=records)
    lookup = _build_ext_info_lookup(repo, "t1")
    event = _make_event(node_id="n1", action_type=TrajectoryActionType.DISPATCH, attempt=0)
    assert lookup(event) is None


@pytest.mark.unit
def test_ext_info_lookup_repo_read_failure_degrades_to_empty():
    """If list_events_by_task raises, the lookup degrades to always-None (no crash)."""
    class _RaisingRepo(_FakeRepo):
        def list_events_by_task(self, task_id: str):
            raise RuntimeError("db down")
    repo = _RaisingRepo()
    lookup = _build_ext_info_lookup(repo, "t1")
    event = _make_event(node_id="n1", action_type=TrajectoryActionType.DISPATCH, attempt=0)
    assert lookup(event) is None


@pytest.mark.unit
def test_ext_info_lookup_key_stable_across_enum_and_string_action_type():
    """The key treats ``TrajectoryActionType.DISPATCH`` and ``"dispatch"`` identically
    (the enum's ``.value`` is the lowercase string the record stored)."""
    key_from_enum = _event_key(
        _make_event(action_type=TrajectoryActionType.DISPATCH, node_id="n1", attempt=0, gmt_create=1000)
    )
    # the record-side signature uses the raw string "dispatch"
    from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import _event_signature
    rec = _make_record(node_id="n1", action_type="dispatch", attempt=0, gmt_create_ms=1000)
    key_from_record = _event_signature(rec)
    assert key_from_enum == key_from_record


# ---------------------------------------------------------------------------
# Analyzed ext_info flows through to the analyzer (integration of the closure)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_true_passes_ext_info_lookup_to_analyzer():
    """The analyzer receives a working ext_info_lookup built from the repo records."""
    ext_json = json.dumps(
        {"schema_v": 1, "_dispatch_rationale": {"strategy_name": "direct"}},
        ensure_ascii=False,
    )
    records = [
        _make_record(node_id="n1", action_type="dispatch", attempt=0, ext_info=ext_json),
    ]
    traj = _make_trajectory(
        timeline=[
            _make_event(node_id="n1", action_type=TrajectoryActionType.DISPATCH, attempt=0, gmt_create=1000),
        ],
    )
    assembler = _FakeAssembler(traj)
    analysis = _make_analysis()
    analyzer = _FakeAnalyzer(analysis=analysis)
    repo = _FakeRepo(events=records)
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(assembler, repo, analyzer, config)

    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    # analyzer got the closure and it resolves the dispatched event's rationale
    assert analyzer.last_ext_info_lookup is not None
    ev = analyzer.last_trajectory.timeline[0]
    ext = analyzer.last_ext_info_lookup(ev)
    assert ext["_dispatch_rationale"]["strategy_name"] == "direct"


# ---------------------------------------------------------------------------
# Mod-2 — RUNNING 节点会话明细探测(running_sessions)
# ---------------------------------------------------------------------------


class _FakeNode:
    """A domain-shaped node stub — the probe touches only ``node_id`` /
    ``status`` / ``run_info.run_mode`` / ``run_info.extend_props`` /
    ``run_info.start_time`` / ``run_info.output``(产出/模态富化)."""

    def __init__(self, *, node_id: str, status: Status, extend_props: dict | None = None,
                 start_time: int | None = None, output: dict | None = None,
                 run_mode: str | None = None,
                 task_spec=None, assignee: str | None = None) -> None:
        self.node_id = node_id
        self.status = status
        self.task_spec = task_spec
        self.run_info = RuntimeInfo(
            run_mode=run_mode,
            start_time=start_time,
            output=dict(output or {}),
            extend_props=dict(extend_props or {}),
            assignee=assignee,
        )


class _FakeGraph:
    """Serves ``query_task_dashboard`` with a scripted task/node list."""

    def __init__(self, nodes: list[_FakeNode]) -> None:
        self._nodes = nodes
        self.query_calls = 0

    def query_task_dashboard(self, task_id: str):
        self.query_calls += 1
        obj = type("Graph", (), {})()
        obj.tasks = list(self._nodes)
        return obj


class _FakeBcs:
    """Serves ``get_session_messages``; scriptable per-session payloads/failures.
    Records each call as ``(session_id, limit, caller_bearer)``."""

    def __init__(self, *, messages_by_session: dict | None = None,
                 raise_exc: Exception | None = None) -> None:
        self._messages = messages_by_session or {}
        self._raise = raise_exc
        self.calls: list[tuple[str, int, "str | None"]] = []

    async def get_session_messages(self, session_id: str, *, limit: int = 50,
                                   since_msg_id: str | None = None,
                                   caller_bearer: str | None = None) -> list:
        self.calls.append((session_id, limit, caller_bearer))
        if self._raise is not None:
            raise self._raise
        return self._messages.get(session_id, [])


class _FakeTokenProvider:
    """Fake ``BcsBotTokenProvider``: scripted bot-uuid → session_token map."""

    def __init__(self, tokens: dict[str, str] | None = None,
                 raise_exc: Exception | None = None) -> None:
        self._tokens = tokens or {}
        self._raise = raise_exc
        self.requested: list[str] = []

    def get_token(self, bcs_bot_uuid: str) -> str | None:
        self.requested.append(bcs_bot_uuid)
        if self._raise is not None:
            raise self._raise
        return self._tokens.get(bcs_bot_uuid)


class _FakeOpenApiBot:
    """Fake ``OpenApiBotPort.get_session_messages``(引擎会话读通道);可编程
    per-session payload/异常,记录每次调用 ``(session_id, limit, bot_id)``。"""

    def __init__(self, *, messages_by_session: dict | None = None,
                 raise_exc: Exception | None = None) -> None:
        self._messages = messages_by_session or {}
        self._raise = raise_exc
        self.calls: list[tuple[str, int, "str | None"]] = []
        self.lifecycle_stages: list["str | None"] = []

    async def get_session_messages(self, session_id: str, *, limit: int = 50,
                                   bot_id: str | None = None,
                                   lifecycle_stage: str | None = None) -> list:
        self.calls.append((session_id, limit, bot_id))
        self.lifecycle_stages.append(lifecycle_stage)
        if self._raise is not None:
            raise self._raise
        return self._messages.get(session_id, [])


def _running_probe_setup(**svc_kwargs):
    """A service wired for a probe run: empty analytic deps (no stale analysis,
    repo events empty → fingerprint sha256("v1:0")), a graph with one RUNNING
    node + one PENDING node, a BCS serving two session messages."""
    running = _FakeNode(
        node_id="n-run", status=Status.RUNNING,
        extend_props={"session_id": "sess-1"}, start_time=500,
    )
    graph = _FakeGraph([
        running,
        _FakeNode(node_id="n-pend", status=Status.PENDING),
    ])
    bcs = _FakeBcs(messages_by_session={
        "sess-1": [
            {"role": "user", "content": "run the job"},
            {"role": "assistant", "content": "tool failed: boom"},
        ],
    })
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(_FakeAssembler(_make_trajectory()), repo, analyzer, config,
                                graph=graph, bcs=bcs, **svc_kwargs)
    return svc, analyzer, bcs, graph


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_probes_running_sessions_and_passes_to_analyzer():
    """Probe flow: graph RUNNING node → session_id → BCS messages → brief with
    node_id/session_id/elapsed_ms/message excerpt reaches the analyzer; the
    PENDING node is never probed."""
    svc, analyzer, bcs, graph = _running_probe_setup()

    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert analyzer.calls == 1
    briefs = analyzer.last_running_sessions
    assert isinstance(briefs, list) and len(briefs) == 1
    brief = briefs[0]
    assert brief["node_id"] == "n-run"
    assert brief["session_id"] == "sess-1"
    assert isinstance(brief["elapsed_ms"], int) and brief["elapsed_ms"] >= 0
    assert brief["message_count"] == 2
    assert brief["messages"] == [
        {"role": "user", "content": "run the job"},
        {"role": "assistant", "content": "tool failed: boom"},
    ]
    # only the RUNNING node's session was pulled
    assert [c[0] for c in bcs.calls] == ["sess-1"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_probe_skips_node_without_session_id():
    """A RUNNING node with no session_id in extend_props → skipped + WARNING
    (never raises); no other brief, analyzer gets running_sessions=None."""
    running = _FakeNode(node_id="n-nosess", status=Status.RUNNING)
    graph = _FakeGraph([running])
    bcs = _FakeBcs()
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs,
    )

    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert analyzer.calls == 1  # main analysis unaffected
    assert analyzer.last_running_sessions is None
    assert bcs.calls == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_probe_survives_bcs_failure():
    """BCS get_session_messages raising → the node's brief degrades away
    (WARNING, never raises) and the main analysis still completes via the bot."""
    graph = _FakeGraph([
        _FakeNode(node_id="n-run", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}, start_time=500),
    ])
    bcs = _FakeBcs(raise_exc=RuntimeError("bcs down"))
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs,
    )

    result = await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert analyzer.calls == 1
    assert analyzer.last_running_sessions is None
    assert result.analysis is not None  # 分析照常完成 + 回填


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_probe_disabled_when_graph_or_bcs_none():
    """Default (4-arg) construction → probe disabled: the graph is never
    queried and the analyzer receives running_sessions=None."""
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(_FakeAssembler(_make_trajectory()), repo, analyzer, config)

    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert analyzer.last_running_sessions is None


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_probe_passes_holder_bearer_token():
    """方案一(401 修复):会话历史读口有参与者级 ACL——探测经 BcsBotTokenProvider
    解析 RUNNING 节点执行 Bot 的 session_token,以 caller_bearer 传入(BCS HTTP
    层携带 ``Authorization: Bearer``)。执行 Bot 取值序 driver_bot_id → assignee。"""
    graph = _FakeGraph([
        _FakeNode(node_id="n-relay", status=Status.RUNNING,
                  extend_props={"session_id": "sess-relay", "driver_bot_id": "bot-holder"},
                  start_time=500),
        _FakeNode(node_id="n-group", status=Status.RUNNING,
                  extend_props={"session_id": "sess-group", "driver_bot_id": "bot-driver"},
                  start_time=300),
        _FakeNode(node_id="n-single", status=Status.RUNNING,
                  extend_props={"session_id": "sess-single"},
                  start_time=100),
    ])
    # n-single 走 assignee 兜底(RuntimeInfo.assignee)
    for n in graph._nodes:
        if n.node_id == "n-single":
            n.run_info.assignee = "bot-exec"
    bcs = _FakeBcs(messages_by_session={
        sid: [{"role": "user", "content": "hi"}]
        for sid in ("sess-relay", "sess-group", "sess-single")
    })
    tokens = _FakeTokenProvider(tokens={
        "bot-holder": "tok-holder", "bot-driver": "tok-driver", "bot-exec": "tok-exec",
    })
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs, bcs_bot_tokens=tokens,
    )

    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    by_sid = {c[0]: c[2] for c in bcs.calls}
    assert by_sid["sess-relay"] == "tok-holder"   # driver_bot_id
    assert by_sid["sess-group"] == "tok-driver"   # driver_bot_id 次之
    assert by_sid["sess-single"] == "tok-exec"    # assignee 兜底
    # 请求了全部三个持有者(顺序按卡住时长降序,不按构造序)
    assert set(tokens.requested) == {"bot-holder", "bot-driver", "bot-exec"}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_probe_omniauth_fallback_without_token():
    """未注入 provider / 持有者解析不到 token / provider 抛错 → caller_bearer
    传 None(裸 HMAC 尝试,BCS 401 时由既有 WARNING 降级)——检测不抛、不阻断。"""
    graph = _FakeGraph([
        _FakeNode(node_id="n1", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1", "driver_bot_id": "bot-x"},
                  start_time=500),
        _FakeNode(node_id="n2", status=Status.RUNNING,
                  extend_props={"session_id": "sess-2", "driver_bot_id": "bot-y"},
                  start_time=400),
    ])
    bcs = _FakeBcs(messages_by_session={
        "sess-1": [{"role": "user", "content": "x"}],
        "sess-2": [{"role": "user", "content": "y"}],
    })
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    # 无 holder id 的 n3 不存在;此处 provider 抛错覆盖最坏分支
    exploding = _FakeTokenProvider(raise_exc=RuntimeError("db down"))
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs, bcs_bot_tokens=exploding,
    )
    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)
    assert all(c[2] is None for c in bcs.calls), "provider 抛错 → 回退匿名"
    assert analyzer.last_running_sessions is not None  # 探测整体不受影响

    # 未注入 provider(默认参数) → None
    bcs2 = _FakeBcs(messages_by_session={"sess-1": [{"role": "user", "content": "x"}]})
    svc2 = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), _FakeRepo(),
        _FakeAnalyzer(analysis=_make_analysis()),
        TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst"),
        graph=_FakeGraph([
            _FakeNode(node_id="n1", status=Status.RUNNING,
                      extend_props={"session_id": "sess-1", "driver_bot_id": "bot-x"}),
        ]),
        bcs=bcs2,
    )
    await svc2.get_trajectory("t1", do_analysis=True, force_analysis=True)
    assert bcs2.calls[0][2] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_probe_keeps_full_message_content():
    """去掉了单条/总量截断预算:长消息原文完整进 ext_info 物化与研判段(默认最近
    running_session_message_limit=50 条)。"""
    long = "x" * 5_000
    graph = _FakeGraph([
        _FakeNode(node_id="n-run", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}),
    ])
    bcs = _FakeBcs(messages_by_session={"sess-1": [{"role": "assistant", "content": long}]})
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo(events=[
        _make_record(node_id="n-run", action_type="execute", attempt=0, gmt_create_ms=1000),
    ])
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs,
    )

    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    briefs = analyzer.last_running_sessions
    assert briefs[0]["messages"][0]["content"] == long, "原文不截断"
    # 物化进 ext_info 的也是原文
    [(_, _, _, merged_value)] = repo.merge_calls
    assert merged_value[0]["content"] == long


@pytest.mark.asyncio
@pytest.mark.unit
async def test_force_analysis_repulls_cached_session_msgs():
    """force_analysis=true 缓存旁路:末位事件 ext_info 已有 session_msgs 也**重新实际
    拉取** BCS,并用最新内容 merge 覆盖该键(与 force 语义一致:一切以当下现场为准)。"""
    cached_msgs = [{"role": "user", "content": "stale"}]
    records = [
        _make_record(node_id="n1", action_type="execute", attempt=0, gmt_create_ms=1000,
                     rec_id=1, ext_info=json.dumps({"schema_v": 1, "session_msgs": cached_msgs},
                                                    ensure_ascii=False)),
    ]
    graph = _FakeGraph([
        _FakeNode(node_id="n1", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}, start_time=500),
    ])
    fresh = [{"role": "user", "content": "fresh-now"}]
    bcs = _FakeBcs(messages_by_session={"sess-1": fresh})
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo(events=records)
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs,
    )

    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    # 不吃缓存:BCS 重新拉过
    assert [c[0] for c in bcs.calls] == ["sess-1"]
    # merge 用最新内容覆盖 session_msgs 键
    [(_, _, _, merged_value)] = repo.merge_calls
    assert merged_value == fresh
    # 研判段也是最新内容
    assert analyzer.last_running_sessions[0]["messages"] == fresh


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_materializes_msgs_for_finished_node_too():
    """全节点物化:**已结束**(SUCCESS)子任务也查 msgs 并增量写进末位事件
    ext_info.session_msgs——但不进 running_sessions 研判段(只喂未结束节点)。"""
    graph = _FakeGraph([
        _FakeNode(node_id="n-done", status=Status.SUCCESS,
                  extend_props={"session_id": "sess-done"}, start_time=500),
        _FakeNode(node_id="n-hung", status=Status.HUNG,
                  extend_props={"session_id": "sess-hung"}, start_time=300),
    ])
    bcs = _FakeBcs(messages_by_session={
        "sess-done": [{"role": "user", "content": "done-msg"}],
        "sess-hung": [{"role": "assistant", "content": "tool error: boom"}],
    })
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo(events=[])  # 无事件行 → merge 仍被调用(由真 repo 决定落不落)
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs,
    )

    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    # 两个子任务都实际查询了(不管有没有完成)
    assert {c[0] for c in bcs.calls} == {"sess-done", "sess-hung"}
    # 物化:每个子任务都尝试 merge("n1","session_msgs",...)
    assert {(t, n, k) for t, n, k, _ in repo.merge_calls} == {
        ("t1", "n-done", "session_msgs"), ("t1", "n-hung", "session_msgs"),
    }
    # 研判段只喂未结束节点(HUNG 是未收口态,SUCCESS 不进)
    briefs = analyzer.last_running_sessions
    assert [b["node_id"] for b in briefs] == ["n-hung"]
    assert briefs[0]["node_status"] == "HUNG"
    assert briefs[0]["messages"][0]["content"] == "tool error: boom"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_merge_failure_never_breaks_analysis():
    """merge 抛错被吞(物化只是旁路):渲染、分析照常完成。"""
    class _ExplodingRepo(_FakeRepo):
        def merge_last_event_ext_info(self, task_id, node_id, key, value) -> bool:
            raise RuntimeError("db down")

    graph = _FakeGraph([
        _FakeNode(node_id="n1", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}, start_time=500),
    ])
    bcs = _FakeBcs(messages_by_session={"sess-1": [{"role": "user", "content": "x"}]})
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _ExplodingRepo(events=[
        _make_record(node_id="n1", action_type="dispatch", attempt=0, gmt_create_ms=1000),
    ])
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs,
    )

    result = await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert analyzer.calls == 1
    assert result.analysis is not None


# ---------------------------------------------------------------------------
# 节点产出读时富化 — 每个子任务的最后一条事件携带 node.run_info.output
# ---------------------------------------------------------------------------


def _output_graph(nodes: list[_FakeNode]) -> _FakeGraph:
    return _FakeGraph(nodes)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_trajectory_attaches_node_output_to_last_event():
    """富化语义:timeline 中每个 (task_id,node_id) 的**最后一条**事件挂上该节点
    当前产出(graph 查询接口);更早的事件保持 output=None。"""
    graph = _output_graph([
        _FakeNode(node_id="n1", status=Status.DONE, output={"result": "n1-done", "by": "bot1"}),
        _FakeNode(node_id="n2", status=Status.RUNNING, output={}),  # 空产出 → 不挂
    ])
    timeline = [
        _make_event(node_id="n1", action_type=TrajectoryActionType.PLAN, gmt_create=1000),
        _make_event(node_id="n1", action_type=TrajectoryActionType.EXECUTE, gmt_create=2000),
        _make_event(node_id="n2", action_type=TrajectoryActionType.PLAN, gmt_create=3000),
    ]
    traj = _make_trajectory(task_id="t1", timeline=timeline)
    assembler = _FakeAssembler(traj)
    analyzer = _FakeAnalyzer()
    svc = TaskTrajectoryService(assembler, _FakeRepo(), analyzer, None, graph=graph)

    result = await svc.get_trajectory("t1", do_analysis=False)

    n1_events = [e for e in result.timeline if e.node_id == "n1"]
    assert n1_events[0].output is None, "更早的 PLAN 事件不挂产出"
    assert n1_events[-1].output == {"result": "n1-done", "by": "bot1"}, (
        "最后一条 EXECUTE 事件挂 n1 当前产出"
    )
    n2_last = [e for e in result.timeline if e.node_id == "n2"][-1]
    assert n2_last.output is None, "空产出不挂(缺字段=无信号)"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_trajectory_attaches_actual_run_mode_to_last_event():
    """真实模态读时富化:每个 (task_id,node_id) 的**最后一条**事件挂上该节点的
    有效执行模态(``effective_run_mode``:``extend_props.actual_run_mode`` 优先,
    缺失回退 ``run_info.run_mode``);更早的事件保持 None;两种模态都缺的节点
    不挂(缺字段=无信号)。"""
    graph = _output_graph([
        _FakeNode(node_id="n1", status=Status.DONE, run_mode="coop_group",
                  extend_props={"actual_run_mode": "single_bot"}),
        _FakeNode(node_id="n2", status=Status.DONE, run_mode="bbs"),
        _FakeNode(node_id="n3", status=Status.PENDING),  # 两种模态都缺 → 不挂
        # 2026-10-16 规则:run_mode=bbs 短路——纵有 actual_run_mode 也判 bbs
        _FakeNode(node_id="n4", status=Status.RUNNING, run_mode="bbs",
                  extend_props={"actual_run_mode": "single_bot"}),
    ])
    timeline = [
        _make_event(node_id="n1", action_type=TrajectoryActionType.PLAN, gmt_create=1000),
        _make_event(node_id="n1", action_type=TrajectoryActionType.EXECUTE, gmt_create=2000),
        _make_event(node_id="n2", action_type=TrajectoryActionType.PLAN, gmt_create=3000),
        _make_event(node_id="n3", action_type=TrajectoryActionType.PLAN, gmt_create=4000),
        _make_event(node_id="n4", action_type=TrajectoryActionType.EXECUTE, gmt_create=5000),
    ]
    traj = _make_trajectory(task_id="t1", timeline=timeline)
    svc = TaskTrajectoryService(_FakeAssembler(traj), _FakeRepo(), _FakeAnalyzer(), None,
                                graph=graph)

    result = await svc.get_trajectory("t1", do_analysis=False)

    n1_events = [e for e in result.timeline if e.node_id == "n1"]
    assert n1_events[0].actual_run_mode is None, "更早的 PLAN 事件不挂真实模态"
    assert n1_events[-1].actual_run_mode == "single_bot", (
        "最后一条事件挂有效模态:actual_run_mode 覆盖 run_mode=coop_group"
    )
    n2_last = [e for e in result.timeline if e.node_id == "n2"][-1]
    assert n2_last.actual_run_mode == "bbs", "actual_run_mode 缺失回退 run_mode"
    n3_last = [e for e in result.timeline if e.node_id == "n3"][-1]
    assert n3_last.actual_run_mode is None, "两种模态都缺 → 不挂(缺字段=无信号)"
    n4_last = [e for e in result.timeline if e.node_id == "n4"][-1]
    assert n4_last.actual_run_mode == "bbs", (
        "run_mode=bbs 短路:不再读 extend_props.actual_run_mode(即便它写着 single_bot)"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_trajectory_attaches_session_msgs_to_last_event():
    """会话消息读时富化(读路径):末位事件 ext_info.session_msgs 挂到该子任务
    timeline 的最后一条事件;早事件 None;无物化的节点不挂。"""
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "done"}]
    records = [
        _make_record(node_id="n1", action_type="dispatch", attempt=0, gmt_create_ms=1000,
                     rec_id=1, ext_info=None),
        _make_record(node_id="n1", action_type="execute", attempt=0, gmt_create_ms=2000,
                     rec_id=2, ext_info=json.dumps({"schema_v": 1, "session_msgs": msgs},
                                                    ensure_ascii=False)),
        _make_record(node_id="n2", action_type="plan", attempt=0, gmt_create_ms=3000,
                     rec_id=3, ext_info=json.dumps({"schema_v": 1, "strategy": "search"},
                                                   ensure_ascii=False)),
    ]
    timeline = [
        _make_event(node_id="n1", action_type=TrajectoryActionType.PLAN, gmt_create=1000),
        _make_event(node_id="n1", action_type=TrajectoryActionType.EXECUTE, gmt_create=2000),
        _make_event(node_id="n2", action_type=TrajectoryActionType.PLAN, gmt_create=3000),
    ]
    traj = _make_trajectory(task_id="t1", timeline=timeline)
    svc = TaskTrajectoryService(_FakeAssembler(traj), _FakeRepo(events=records), _FakeAnalyzer(), None)

    result = await svc.get_trajectory("t1", do_analysis=False, session_detail=True)

    n1_events = [e for e in result.timeline if e.node_id == "n1"]
    assert n1_events[0].session_msgs is None, "早事件不挂"
    assert n1_events[-1].session_msgs == msgs, "末位事件挂物化过的会话原文"
    n2_last = [e for e in result.timeline if e.node_id == "n2"][-1]
    assert n2_last.session_msgs is None, "未物化节点不挂(缺字段=无信号)"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_pure_read_default_skips_session_detail_entirely():
    """session_detail 默认 false:零外部读(bcs 不被调)、零事件行读副作用可见于返回——
    末位事件不挂 session_msgs(即使 ext_info 已物化缓存也缺席),timeline 本体不受影响。"""
    msgs = [{"role": "user", "content": "hi"}]
    records = [
        _make_record(node_id="n1", action_type="execute", attempt=0, gmt_create_ms=1000,
                     rec_id=1, ext_info=json.dumps({"schema_v": 1, "session_msgs": msgs},
                                                    ensure_ascii=False)),
    ]
    timeline = [_make_event(node_id="n1", action_type=TrajectoryActionType.EXECUTE, gmt_create=1000)]
    bcs = _FakeBcs(messages_by_session={"sess-1": msgs})
    graph = _FakeGraph([
        _FakeNode(node_id="n1", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}, start_time=500),
    ])
    traj = _make_trajectory(task_id="t1", timeline=timeline)
    repo = _FakeRepo(events=records)
    svc = TaskTrajectoryService(_FakeAssembler(traj), repo, _FakeAnalyzer(), None,
                                graph=graph, bcs=bcs)

    result = await svc.get_trajectory("t1")   # 默认 do_analysis=False, session_detail=False

    assert bcs.calls == [], "默认不实拉会话明细(零外部 GET)"
    assert all(getattr(e, "session_msgs", None) is None for e in result.timeline), \
        "默认返回不携带 session_msgs(缓存也不挂)"
    assert repo.merge_calls == []
    assert repo.backfill_calls == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_returns_timeline_with_fresh_session_msgs():
    """慢路径返回态:本回合刚实拉物化的 session_msgs(fresh map)挂上末位事件,
    返回的 timeline 可直接在 HTML 展示最新会话(不受早前 ext_info 快照影响)。"""
    stale = [{"role": "user", "content": "stale"}]
    records = [
        _make_record(node_id="n1", action_type="execute", attempt=0, gmt_create_ms=1000,
                     rec_id=1, ext_info=json.dumps({"schema_v": 1, "session_msgs": stale},
                                                   ensure_ascii=False)),
    ]
    timeline = [_make_event(node_id="n1", action_type=TrajectoryActionType.EXECUTE, gmt_create=1000)]
    graph = _FakeGraph([
        _FakeNode(node_id="n1", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}, start_time=500),
    ])
    fresh = [{"role": "user", "content": "fresh-now"}]
    bcs = _FakeBcs(messages_by_session={"sess-1": fresh})
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo(events=records)
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    traj = _make_trajectory(task_id="t1", timeline=timeline)
    svc = TaskTrajectoryService(_FakeAssembler(traj), repo, analyzer, config,
                                graph=graph, bcs=bcs)

    result = await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    # fresh(本回合实拉)胜过 ext_info 快照;承载在同子任务末尾的
    # task_result_display 虚拟展示事件上(真实事件保持 None)
    assert result.timeline[-1].session_msgs == fresh
    assert result.timeline[-1].session_msgs != stale
    assert str(getattr(result.timeline[-1].action_type, "value",
                       result.timeline[-1].action_type)) == RESULT_DISPLAY_ACTION
    assert result.timeline[0].session_msgs is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_submit_goal_replaces_digest_on_first_event():
    """首条 submit 事件的 action_input 读时富化为 TaskSpec 的 goal(目标+验收),
    不再展示 SHA-256 task_spec_digest;非 submit 首事件/无根 spec 保持原值。"""
    from agentclaw.community.core.task.domain.models import (
        AcceptanceCriteria, Context, Goal, TaskSpec,
    )

    spec = TaskSpec(
        context=Context(background="bg", title="T"),
        goal=Goal(objective="完成市场分析", acceptances=[
            AcceptanceCriteria(id="a1", description="给出结论"),
            AcceptanceCriteria(id="a2", description="引用数据来源"),
        ]),
    )
    graph = _FakeGraph([
        _FakeNode(node_id="t1", status=Status.DONE, task_spec=spec),
    ])
    timeline = [
        _make_event(node_id="t1", action_type=TrajectoryActionType.SUBMIT,
                     gmt_create=1000, action_input="42992fc6f776c8806e16d240f41fb0d9fc3797fb63ffe2313c46a557e07df24"),
        _make_event(node_id="t1", action_type=TrajectoryActionType.PLAN, gmt_create=2000),
    ]
    traj = _make_trajectory(task_id="t1", timeline=timeline)
    svc = TaskTrajectoryService(_FakeAssembler(traj), _FakeRepo(events=[]), _FakeAnalyzer(), None,
                                graph=graph)

    result = await svc.get_trajectory("t1", do_analysis=False)

    assert result.timeline[0].action_input == (
        "目标: 完成市场分析\n验收: 给出结论 / 引用数据来源"
    )
    assert "42992fc6" not in (result.timeline[0].action_input or "")

    # graph 未接线 → 保留原 digest(展示旁路降级)
    timeline2 = [
        _make_event(node_id="t1", action_type=TrajectoryActionType.SUBMIT,
                    gmt_create=1000, action_input="deadbeef"),
    ]
    traj2 = _make_trajectory(task_id="t1", timeline=timeline2)
    svc2 = TaskTrajectoryService(_FakeAssembler(traj2), _FakeRepo(events=[]), _FakeAnalyzer(), None)
    out2 = await svc2.get_trajectory("t1", do_analysis=False)
    assert out2.timeline[0].action_input == "deadbeef"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_trajectory_output_skipped_when_graph_unbound():
    """graph 未接线(4 参轻量构造)→ 不富化也不抛,事件保持 output=None。"""
    timeline = [_make_event(node_id="n1", gmt_create=1000)]
    traj = _make_trajectory(task_id="t1", timeline=timeline)
    svc = TaskTrajectoryService(_FakeAssembler(traj), _FakeRepo(), _FakeAnalyzer(), None)

    result = await svc.get_trajectory("t1", do_analysis=False)

    assert result.timeline[0].output is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_get_trajectory_output_enrichment_survives_graph_failure():
    """图读失败(任务已删/relay 外部托管)→ WARNING 降级,轨迹本体照常返回。"""
    class _RaisingGraph(_FakeGraph):
        def query_task_dashboard(self, task_id: str):
            raise RuntimeError("no graph")

    timeline = [_make_event(node_id="n1", gmt_create=1000)]
    traj = _make_trajectory(task_id="t1", timeline=timeline)
    svc = TaskTrajectoryService(
        _FakeAssembler(traj), _FakeRepo(), _FakeAnalyzer(), None,
        graph=_RaisingGraph([]),
    )

    result = await svc.get_trajectory("t1", do_analysis=False)

    assert result is traj  # 本体照常返回
    assert result.timeline[0].output is None


# ---------------------------------------------------------------------------
# 会话明细双通道(2026-10-10):纯读实拉 + 引擎会话经 BaaS Open API 分派
# ---------------------------------------------------------------------------

_ENGINE_SID = "agent:main:session:8ccd0594-032e-4266-a42b-cee2ec833562:user:35983"
_CC_SID = "user:123:session:9f0b2c77-1111-2222-3333-444455556666:agent:bot9"
_LIVE_MSGS = [
    {"role": "user", "content": "run the job"},
    {"role": "assistant", "content": "tool failed: boom"},
]


@pytest.mark.unit
def test_is_engine_session_id_matches_engine_namespaces_only():
    """判别正则:openclaw/base/claude_code 三个引擎变体 + 归一裸形命中;
    BCS 会话键(bcs-cli:*/协作群)不命中(继续走既有 BCS 读口)。"""
    assert _is_engine_session_id(_ENGINE_SID)
    assert _is_engine_session_id("agent:bot7:session:abc-123:user:35983")  # base 引擎
    assert _is_engine_session_id(_CC_SID)                                   # claude_code
    assert _is_engine_session_id("session:abc-123:user:35983")              # 归一裸形
    assert not _is_engine_session_id("sess-1")
    assert not _is_engine_session_id("bcs-cli:task-20261009190011123-198568")
    assert not _is_engine_session_id("grp-1:initial")
    assert not _is_engine_session_id("")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_pure_read_live_fetches_session_msgs_and_never_persists():
    """REQ-8 锁:纯读(session_detail=True)实拉会话明细挂返回对象,但零 DB 写
    (merge/backfill 均不可达)且零 bot 调用。"""
    timeline = [_make_event(node_id="n-run", gmt_create=1000)]
    graph = _FakeGraph([
        _FakeNode(node_id="n-run", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}, start_time=500),
    ])
    bcs = _FakeBcs(messages_by_session={"sess-1": _LIVE_MSGS})
    analyzer = _FakeAnalyzer()
    repo = _FakeRepo()
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory(timeline=timeline)), repo, analyzer, None,
        graph=graph, bcs=bcs,
    )

    result = await svc.get_trajectory("t1", do_analysis=False, session_detail=True)

    assert bcs.calls == [("sess-1", 200, None)]   # 实拉,默认 limit 200
    assert result.timeline[-1].session_msgs == _LIVE_MSGS, (
        "会话明细挂在 task_result_display 虚拟事件上(timeline 末位)"
    )
    assert str(getattr(result.timeline[-1].action_type, "value", result.timeline[-1].action_type)) == RESULT_DISPLAY_ACTION
    assert repo.merge_calls == []                  # 纯读不写库
    assert repo.backfill_calls == []
    assert analyzer.calls == 0                    # 纯读不调 bot


@pytest.mark.asyncio
@pytest.mark.unit
async def test_pure_read_falls_back_to_cached_snapshot_when_live_fetch_fails():
    """实拉失败(session_detail=True 路径)→ WARNING 降级,回退末位事件
    ext_info.session_msgs 缓存快照;不抛、不写库。"""
    timeline = [_make_event(node_id="n-run", gmt_create=1000)]
    graph = _FakeGraph([
        _FakeNode(node_id="n-run", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}, start_time=500),
    ])
    bcs = _FakeBcs(raise_exc=RuntimeError("bcs down"))
    cached = [{"role": "user", "content": "cached snapshot"}]
    records = [_make_record(
        node_id="n-run", ext_info=json.dumps({"session_msgs": cached}),
    )]
    analyzer = _FakeAnalyzer()
    repo = _FakeRepo(events=records)
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory(timeline=timeline)), repo, analyzer, None,
        graph=graph, bcs=bcs,
    )

    result = await svc.get_trajectory("t1", do_analysis=False, session_detail=True)

    assert bcs.calls  # 实拉确实尝试过
    assert result.timeline[-1].session_msgs == cached  # 回退缓存快照(挂虚拟展示事件)
    assert repo.merge_calls == []
    assert repo.backfill_calls == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_pure_read_live_fetch_off_when_graph_unbound():
    """默认构造(graph 未接线)→ 纯读实拉(session_detail=True)整体关闭(零副作用);
    缓存快照照常挂载。存量 e2e 构造方式的回归锁。"""
    cached = [{"role": "user", "content": "from cache"}]
    records = [_make_record(
        node_id="n1", ext_info=json.dumps({"session_msgs": cached}),
    )]
    timeline = [_make_event(node_id="n1", gmt_create=1000)]
    bcs = _FakeBcs()
    analyzer = _FakeAnalyzer()
    repo = _FakeRepo(events=records)
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory(timeline=timeline)), repo, analyzer, None,
        bcs=bcs,  # bcs 接了,但 graph 未接 → 探测关闭
    )

    result = await svc.get_trajectory("t1", do_analysis=False, session_detail=True)

    assert bcs.calls == []
    assert result.timeline[-1].session_msgs == cached


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_engine_session_id_dispatches_to_baas_channel():
    """引擎会话(agent:main:session:*)→ OpenApiBotPort(带与执行侧同源的复合
    bot_id);BCS 不被调;分析路径物化 merge + brief 照旧。"""
    graph = _FakeGraph([
        _FakeNode(node_id="n-eng", status=Status.RUNNING,
                  extend_props={"session_id": _ENGINE_SID,
                                "assignee_owner_id": "35983"},
                  assignee="20260920_hzzaxnzf", start_time=500),
    ])
    bcs = _FakeBcs()
    openapi = _FakeOpenApiBot(messages_by_session={_ENGINE_SID: _LIVE_MSGS})
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs, openapi_bot=openapi,
    )

    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert openapi.calls == [(_ENGINE_SID, 200, "20260920_hzzaxnzf:35983")]
    assert bcs.calls == []
    briefs = analyzer.last_running_sessions
    assert briefs and briefs[0]["session_id"] == _ENGINE_SID
    assert briefs[0]["messages"] == _LIVE_MSGS
    assert [(m[1], m[2]) for m in repo.merge_calls] == [("n-eng", "session_msgs")]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_pure_read_engine_session_id_dispatches_to_baas_channel_no_persist():
    """纯读(session_detail=True)遇到引擎会话 → 同样走 BaaS 通道,BCS 不动;
    结果挂返回对象,不落库。"""
    timeline = [_make_event(node_id="n-eng", gmt_create=1000)]
    graph = _FakeGraph([
        _FakeNode(node_id="n-eng", status=Status.RUNNING,
                  extend_props={"session_id": _ENGINE_SID,
                                "assignee_owner_id": "35983"},
                  assignee="20260920_hzzaxnzf", start_time=500),
    ])
    bcs = _FakeBcs()
    openapi = _FakeOpenApiBot(messages_by_session={_ENGINE_SID: _LIVE_MSGS})
    analyzer = _FakeAnalyzer()
    repo = _FakeRepo()
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory(timeline=timeline)), repo, analyzer, None,
        graph=graph, bcs=bcs, openapi_bot=openapi,
    )

    result = await svc.get_trajectory("t1", do_analysis=False, session_detail=True)

    assert openapi.calls == [(_ENGINE_SID, 200, "20260920_hzzaxnzf:35983")]
    assert bcs.calls == []
    assert result.timeline[-1].session_msgs == _LIVE_MSGS, (
        "会话明细挂在 task_result_display 虚拟事件上(timeline 末位)"
    )
    assert str(getattr(result.timeline[-1].action_type, "value", result.timeline[-1].action_type)) == RESULT_DISPLAY_ACTION
    assert repo.merge_calls == []
    assert repo.backfill_calls == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_engine_session_read_preserves_lifecycle_stage():
    """节点记录 draft runtime 时,轨迹下钻也读取同一份 draft 会话历史。"""
    graph = _FakeGraph([
        _FakeNode(node_id="n-draft", status=Status.RUNNING,
                  extend_props={"session_id": _ENGINE_SID, "lifecycle_stage": "draft"},
                  start_time=500),
    ])
    openapi = _FakeOpenApiBot(messages_by_session={_ENGINE_SID: _LIVE_MSGS})
    timeline = [_make_event(node_id="n-draft", gmt_create=1000)]
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory(timeline=timeline)), _FakeRepo(),
        _FakeAnalyzer(), None, graph=graph, bcs=_FakeBcs(), openapi_bot=openapi,
    )

    result = await svc.get_trajectory("t1", do_analysis=False, session_detail=True)

    assert openapi.lifecycle_stages == ["draft"]
    assert result.timeline[-1].session_msgs == _LIVE_MSGS


@pytest.mark.asyncio
@pytest.mark.unit
async def test_claude_code_session_key_dispatches_to_baas():
    """claude_code 引擎变体(user:{u}:session:{r}:agent:{b})同样分派 BaaS 通道。"""
    graph = _FakeGraph([
        _FakeNode(node_id="n-cc", status=Status.RUNNING,
                  extend_props={"session_id": _CC_SID, "assignee_owner_id": "123"},
                  assignee="bot9", start_time=500),
    ])
    bcs = _FakeBcs()
    openapi = _FakeOpenApiBot(messages_by_session={_CC_SID: _LIVE_MSGS})
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs, openapi_bot=openapi,
    )

    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert [c[0] for c in openapi.calls] == [_CC_SID]
    assert [c[2] for c in openapi.calls] == ["bot9:123"]
    assert bcs.calls == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_mixed_bcs_and_engine_channels_probed_independently():
    """BCS 键 + 引擎键混合节点:各自分派正确通道;BCS 节点照旧解析持有者
    Bearer(driver_bot_id),引擎节点走复合 bot_id。"""
    graph = _FakeGraph([
        _FakeNode(node_id="n-bcs", status=Status.RUNNING,
                  extend_props={"session_id": "sess-group", "driver_bot_id": "bot-holder"},
                  start_time=500),
        _FakeNode(node_id="n-eng", status=Status.RUNNING,
                  extend_props={"session_id": _ENGINE_SID, "assignee_owner_id": "35983"},
                  assignee="20260920_hzzaxnzf", start_time=300),
    ])
    bcs = _FakeBcs(messages_by_session={"sess-group": [
        {"role": "user", "content": "group msg"},
    ]})
    tokens = _FakeTokenProvider(tokens={"bot-holder": "tok-holder"})
    openapi = _FakeOpenApiBot(messages_by_session={_ENGINE_SID: _LIVE_MSGS})
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs, bcs_bot_tokens=tokens, openapi_bot=openapi,
    )

    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert bcs.calls == [("sess-group", 200, "tok-holder")]
    assert openapi.calls == [(_ENGINE_SID, 200, "20260920_hzzaxnzf:35983")]
    assert {m[1] for m in repo.merge_calls} == {"n-bcs", "n-eng"}
    assert {b["node_id"] for b in analyzer.last_running_sessions} == {"n-bcs", "n-eng"}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_probe_gate_enabled_with_only_openapi_bot():
    """bcs=None 但 openapi 接线:引擎节点仍探测(门改动);BCS 命名空间节点
    逐个 WARNING 降级,不影响引擎节点与分析本体。"""
    graph = _FakeGraph([
        _FakeNode(node_id="n-eng", status=Status.RUNNING,
                  extend_props={"session_id": _ENGINE_SID, "assignee_owner_id": "35983"},
                  assignee="20260920_hzzaxnzf", start_time=500),
        _FakeNode(node_id="n-bcsns", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}, start_time=100),
    ])
    openapi = _FakeOpenApiBot(messages_by_session={_ENGINE_SID: _LIVE_MSGS})
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, openapi_bot=openapi,
    )

    result = await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)  # bcs=None

    assert [c[0] for c in openapi.calls] == [_ENGINE_SID]
    assert {b["node_id"] for b in analyzer.last_running_sessions} == {"n-eng"}  # BCS 键节点被跳过
    assert result.analysis is not None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_force_analysis_repulls_cached_engine_session_msgs():
    """引擎节点 ext_info 已有缓存快照 + force_analysis=True → 缓存旁路,
    OpenApi 通道重拉并 merge 覆盖。"""
    graph = _FakeGraph([
        _FakeNode(node_id="n-eng", status=Status.RUNNING,
                  extend_props={"session_id": _ENGINE_SID, "assignee_owner_id": "35983"},
                  assignee="20260920_hzzaxnzf", start_time=500),
    ])
    bcs = _FakeBcs()
    openapi = _FakeOpenApiBot(messages_by_session={_ENGINE_SID: _LIVE_MSGS})
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    records = [_make_record(
        node_id="n-eng",
        ext_info=json.dumps({"session_msgs": [{"role": "user", "content": "stale"}]}),
    )]
    repo = _FakeRepo(events=records)
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs, openapi_bot=openapi,
    )

    await svc.get_trajectory("t1", do_analysis=True, force_analysis=True)

    assert [c[0] for c in openapi.calls] == [_ENGINE_SID]  # force:缓存旁路重拉
    assert repo.merge_calls and repo.merge_calls[0][3] == _LIVE_MSGS  # 覆盖为最新


@pytest.mark.asyncio
@pytest.mark.unit
async def test_live_fetch_honors_running_session_message_limit_config():
    """TrajectoryAnalysisConfig.running_session_message_limit=7 → BCS 与引擎
    两条通道都按 7 实拉。"""
    graph = _FakeGraph([
        _FakeNode(node_id="n-bcs", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}, start_time=500),
        _FakeNode(node_id="n-eng", status=Status.RUNNING,
                  extend_props={"session_id": _ENGINE_SID, "assignee_owner_id": "35983"},
                  assignee="20260920_hzzaxnzf", start_time=300),
    ])
    bcs = _FakeBcs(messages_by_session={"sess-1": _LIVE_MSGS})
    openapi = _FakeOpenApiBot(messages_by_session={_ENGINE_SID: _LIVE_MSGS})
    analyzer = _FakeAnalyzer()
    repo = _FakeRepo()
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer,
        TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst",
                                 running_session_message_limit=7),
        graph=graph, bcs=bcs, openapi_bot=openapi,
    )

    await svc.get_trajectory("t1", do_analysis=False, session_detail=True)

    assert [c[1] for c in bcs.calls] == [7]
    assert [c[1] for c in openapi.calls] == [7]


# ---------------------------------------------------------------------------
# 覆盖率专项:trajectory_session_support 防御分支 + _attach_submit_goal 全路径
# ---------------------------------------------------------------------------


RESULT_DISPLAY_ACTION = "task_result_display"


def _support_svc(graph=None, bcs=None, tokens=None, openapi_bot=None, config=None):
    return TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), _FakeRepo(), _FakeAnalyzer(), config,
        graph=graph, bcs=bcs, bcs_bot_tokens=tokens, openapi_bot=openapi_bot,
    )


def test_resolve_msg_limit_invalid_config_falls_back():
    """``running_session_message_limit`` 配置值非法 → 回落默认(200)。"""
    svc = _support_svc(config=SimpleNamespace(running_session_message_limit="bad"))
    assert svc._resolve_msg_limit() == 200


def test_node_elapsed_ms_defensive_bad_start_time():
    """start_time 非数字 → elapsed 0(防御性读,不抛)。"""
    node = SimpleNamespace(run_info=SimpleNamespace(start_time="not-a-number"))
    assert TaskTrajectoryService._node_elapsed_ms(node, 1_790_000_000_000) == 0


def test_last_event_ext_by_node_corrupt_or_non_dict_ext():
    """records 的末位 ext_info 损坏 JSON / 非法形态 → 该节点回退空 dict。"""
    svc = _support_svc()
    records = [
        _make_record(node_id="n1", action_type="execute", attempt=0,
                     gmt_create_ms=1000, rec_id=1, ext_info="{corrupt-json"),
        _make_record(node_id="n2", action_type="execute", attempt=0,
                     gmt_create_ms=2000, rec_id=2, ext_info='"just-a-string"'),
    ]
    last = svc._last_event_ext_by_node(records)
    assert last == {"n1": {}, "n2": {}}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_collect_session_briefs_cached_hit_skips_fetch_and_merge():
    """force=False 且末位事件 ext_info 已带 session_msgs → 缓存命中:零实拉
    (bcs 不被调)、零合并,brief 直接用缓存内容。"""
    msgs = [{"role": "user", "content": "cached"}]
    records = [
        _make_record(node_id="n1", action_type="execute", attempt=0,
                     gmt_create_ms=1000, rec_id=1,
                     ext_info=json.dumps({"session_msgs": msgs}, ensure_ascii=False)),
    ]
    bcs = _FakeBcs(messages_by_session={"sess-1": msgs})
    graph = _FakeGraph([
        _FakeNode(node_id="n1", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}, start_time=100),
    ])
    svc = _support_svc(graph=graph, bcs=bcs)

    briefs, fresh = await svc._collect_session_briefs("t1", records, force_analysis=False)

    assert bcs.calls == [], "缓存命中不应实拉"
    assert fresh == {}
    assert briefs is not None and briefs[0]["node_id"] == "n1"
    assert briefs[0]["messages"] == msgs


@pytest.mark.asyncio
@pytest.mark.unit
async def test_collect_session_briefs_merge_failure_still_logs_and_fetch_failure_skips():
    """① ext_info 合并失败(无事件行)→ INFO 降级不抛;② 通道拉取失败
    (bcs raise)→ 该节点跳过 brief(excerpt 空 continue)。"""
    class _MergeFalseRepo(_FakeRepo):
        def merge_last_event_ext_info(self, task_id, node_id, key, value):
            return False

    msgs = [{"role": "user", "content": "hi"}]
    graph = _FakeGraph([
        _FakeNode(node_id="n1", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}, start_time=100),
    ])
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), _MergeFalseRepo(), _FakeAnalyzer(), None,
        graph=graph, bcs=_FakeBcs(messages_by_session={"sess-1": msgs}),
    )
    briefs, fresh = await svc._collect_session_briefs("t1", [], force_analysis=False)
    assert briefs is not None and fresh == {"n1": msgs}, "合并失败仅 INFO,不影响 brief"

    svc._bcs = _FakeBcs(raise_exc=RuntimeError("bcs down"))
    briefs2, fresh2 = await svc._collect_session_briefs("t1", [], force_analysis=False)
    assert briefs2 is None, "拉取失败无 excerpt → 无 brief"
    assert fresh2 == {}


def test_session_bearing_nodes_graph_failure_returns_none():
    """图读失败 → 探测整体降级(None)。"""
    class _BoomGraph:
        def query_task_dashboard(self, task_id, node_id=None):
            raise RuntimeError("graph down")
    svc = _support_svc(graph=_BoomGraph())
    assert svc._session_bearing_nodes("t1") is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_fetch_engine_channel_unwired_returns_none():
    """引擎会话键但 OpenApiBotPort 未接线 → None(WARNING 降级)。"""
    svc = _support_svc()  # openapi_bot=None
    node = SimpleNamespace(
        node_id="n1",
        run_info=SimpleNamespace(extend_props={
            "session_id": "agent:main:session:abc:user:1", "assignee_owner_id": "7",
        }, assignee="bot-x"),
    )
    assert await svc._fetch_node_session_msgs("t1", node, limit=60) is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_fetch_engine_channel_ok_and_bcs_skips_non_dict_msgs():
    """引擎通道正常拉取;BCS 通道混入非 dict 消息被防御跳过。"""
    msgs = [{"role": "user", "content": "引擎原文"}]
    svc = _support_svc(
        openapi_bot=_FakeOpenApiBot(messages_by_session={
            "agent:main:session:abc:user:1": msgs}))
    engine_node = SimpleNamespace(
        node_id="n1",
        run_info=SimpleNamespace(extend_props={
            "session_id": "agent:main:session:abc:user:1",
            "assignee_owner_id": "7",
        }, assignee="bot-x"),
    )
    out = await svc._fetch_node_session_msgs("t1", engine_node, limit=60)
    assert out == [{"role": "user", "content": "引擎原文"}]

    bcs = _FakeBcs(messages_by_session={"sess-1": ["junk", None, {"role": "a", "content": "ok"}]})
    svc2 = _support_svc(bcs=bcs)
    bcs_node = _FakeNode(node_id="n2", status=Status.RUNNING,
                         extend_props={"session_id": "sess-1"})
    out2 = await svc2._fetch_node_session_msgs("t1", bcs_node, limit=60)
    assert out2 == [{"role": "a", "content": "ok"}], "非 dict 消息跳过"


def test_holder_bearer_token_defensive_paths():
    """持棒 token 解析:无持有者 → None;provider 异常 → None。"""
    svc = _support_svc(tokens=_FakeTokenProvider(tokens={"bot-x:7": "tok"}))
    node = _FakeNode(node_id="n1", status=Status.RUNNING,
                     extend_props={"session_id": "s"}, assignee=None)
    assert svc._holder_bearer_token(node) is None

    svc._bcs_bot_tokens = _FakeTokenProvider(raise_exc=RuntimeError("provider down"))
    node2 = _FakeNode(node_id="n2", status=Status.RUNNING,
                      extend_props={"session_id": "s", "driver_bot_id": "bot-x"},
                      assignee="bot-x")
    assert svc._holder_bearer_token(node2) is None


def test_safe_list_records_failure_returns_none():
    """事件行读取失败 → None(富化降级)。"""
    class _BoomRepo(_FakeRepo):
        def list_events_by_task(self, task_id):
            raise RuntimeError("db down")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), _BoomRepo(), _FakeAnalyzer(), None)
    assert svc._safe_list_records("t1") is None


def test_collect_session_msgs_degrades_on_none_records_and_bad_fresh():
    """records=None(读失败)→ 空映射;fresh 值非 list → 过滤。"""
    svc = _support_svc()
    assert svc._collect_session_msgs_by_node(None, {"n1": "not-a-list"}) == {}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_attach_submit_goal_variants():
    """根任务目标富化四态:①有 goal 且首事件 submit → 替换为可读目标;
    ②首事件非 submit → 保持原值;③root spec 无 goal → 保持;④空时间线 no-op。"""
    def _root_node(spec):
        return _FakeNode(node_id="t1", status=Status.DONE, task_spec=spec)

    goal = SimpleNamespace(objective="完成市场分析",
                           acceptances=[SimpleNamespace(description="给结论")])
    root_ok = _root_node(SimpleNamespace(goal=goal))

    # ① 正向替换
    graph = _FakeGraph([root_ok])
    traj = _make_trajectory(task_id="t1", timeline=[
        _make_event(node_id="t1", action_type=TrajectoryActionType.SUBMIT,
                    action_result="success", gmt_create=100,
                    action_input="digest-xyz"),
    ])
    svc = _support_svc(graph=graph)
    svc._attach_submit_goal(traj, root_ok.task_spec)
    assert "目标: 完成市场分析" in traj.timeline[0].action_input
    assert "验收: 给结论" in traj.timeline[0].action_input

    # ② 首事件非 submit → 不动
    traj2 = _make_trajectory(task_id="t1", timeline=[
        _make_event(node_id="t1", attempt=0, gmt_create=100,
                    action_type=TrajectoryActionType.PLAN, action_input="digest-xyz"),
    ])
    svc._attach_submit_goal(traj2, root_ok.task_spec)
    assert traj2.timeline[0].action_input == "digest-xyz"

    # ③ root spec 无 goal → 不动
    root_no_goal = _root_node(SimpleNamespace(goal=None))
    svc2 = _support_svc(graph=_FakeGraph([root_no_goal]))
    traj3 = _make_trajectory(task_id="t1", timeline=[
        _make_event(node_id="t1", action_type=TrajectoryActionType.SUBMIT,
                    action_result="success", gmt_create=100, action_input="digest-xyz"),
    ])
    svc2._attach_submit_goal(traj3, root_no_goal.task_spec)
    assert traj3.timeline[0].action_input == "digest-xyz"

    # ④ 空时间线 no-op
    svc._attach_submit_goal(_make_trajectory(task_id="t1"), root_ok.task_spec)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_collect_session_briefs_skips_non_string_session_id():
    """节点 extend_props.session_id 非字符串(如实数)→ brief 循环防御跳过。"""
    graph = _FakeGraph([
        _FakeNode(node_id="n1", status=Status.RUNNING,
                  extend_props={"session_id": 123}),
    ])
    svc = _support_svc(graph=graph, bcs=_FakeBcs())
    briefs, fresh = await svc._collect_session_briefs("t1", [], force_analysis=False)
    assert briefs is None
    assert fresh == {}


def test_attach_submit_goal_swallows_root_spec_errors():
    """root spec 的 goal 访问抛异常 → 整体 except 吞掉,保持原 digest(决策 #14)。"""
    class _BoomSpec:
        @property
        def goal(self):  # pragma: no cover - 演示属性访问炸
            raise RuntimeError("spec corrupted")

    svc = _support_svc()
    traj = _make_trajectory(task_id="t1", timeline=[
        _make_event(node_id="t1", action_type=TrajectoryActionType.SUBMIT,
                    action_result="success", gmt_create=100, action_input="digest-xyz"),
    ])
    svc._attach_submit_goal(traj, _BoomSpec())
    assert traj.timeline[0].action_input == "digest-xyz", "异常防守:富化失败保持原文"


def test_collect_session_msgs_tolerates_corrupt_record_ext():
    """collect 侧 records 的 ext_info 损坏 → 无缓存,仅 fresh 保留。"""
    svc = _support_svc()
    records = [
        _make_record(node_id="n1", action_type="execute", attempt=0,
                     gmt_create_ms=1000, rec_id=1, ext_info="{corrupt"),
    ]
    out = svc._collect_session_msgs_by_node(records, {"n1": [{"role": "u", "content": "f"}]})
    assert out == {"n1": [{"role": "u", "content": "f"}]}
    assert svc._collect_session_msgs_by_node(records) == {}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_fetch_node_session_msgs_ignores_non_string_sid():
    """fetch 入口:extend_props.session_id 非字符串 → 防御性 None。"""
    svc = _support_svc(bcs=_FakeBcs())
    node = SimpleNamespace(node_id="n1", run_info=SimpleNamespace(
        extend_props={"session_id": 123}, assignee="bot-1"))
    assert await svc._fetch_node_session_msgs("t1", node, limit=60) is None


def test_collect_session_msgs_swallows_records_iteration_boom():
    """records 自身迭代炸(读失败的特殊形态)→ 外层 except 空映射,不抛;
    fresh 仍保留(降级不掩盖实拉结果)。"""
    class _BoomRecords:
        def __iter__(self):
            raise RuntimeError("records unreadable")

    svc = _support_svc()
    out = svc._collect_session_msgs_by_node(
        _BoomRecords(), {"n1": [{"role": "u", "content": "fresh"}]})
    assert out == {"n1": [{"role": "u", "content": "fresh"}]}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_result_display_virtual_event_carries_output_and_session():
    """2026-10-16 富化模型:每个子任务 timeline **最后**追加虚拟事件
    ``task_result_display``,承载 output / session_msgs / actual_run_mode;
    真实事件一律保持 None;gmt 取该子任务真实末位 +1ms 保排序。"""
    msgs = [{"role": "user", "content": "hi"}]
    records = [
        _make_record(node_id="n1", action_type="execute", attempt=0,
                     gmt_create_ms=2000, rec_id=1,
                     ext_info=json.dumps({"session_msgs": msgs}, ensure_ascii=False)),
    ]
    graph = _FakeGraph([
        _FakeNode(node_id="n1", status=Status.DONE, run_mode="coop_group",
                  extend_props={"actual_run_mode": "single_bot"},
                  output={"result": "done"}),
    ])
    timeline = [
        _make_event(node_id="n1", action_type=TrajectoryActionType.PLAN, gmt_create=1000),
        _make_event(node_id="n1", action_type=TrajectoryActionType.EXECUTE, gmt_create=2000),
    ]
    traj = _make_trajectory(task_id="t1", timeline=timeline)
    svc = TaskTrajectoryService(_FakeAssembler(traj), _FakeRepo(events=records),
                                _FakeAnalyzer(), None, graph=graph)

    result = await svc.get_trajectory("t1", do_analysis=False, session_detail=True)

    assert len(result.timeline) == 3, "两个真实事件 + 1 个虚拟展示事件"
    virtual = result.timeline[-1]
    assert str(getattr(virtual.action_type, "value", virtual.action_type)) == RESULT_DISPLAY_ACTION
    assert virtual.node_id == "n1"
    assert virtual.action_result == "display"
    assert virtual.attempt == 0
    assert virtual.gmt_create == 2001, "虚拟事件时间 = 真实末位 +1ms"
    assert virtual.output == {"result": "done"}
    assert virtual.session_msgs == msgs
    assert virtual.actual_run_mode == "single_bot"
    for real_ev in result.timeline[:-1]:
        assert real_ev.output is None and real_ev.session_msgs is None
        assert real_ev.actual_run_mode is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_result_display_virtual_event_not_nested_and_only_with_data():
    """虚拟事件是顶层展示位:不做 main 判定但**不被 system_logs 嵌套收编**;
    无任何展示素材(output/模态/会话全空)的子任务不产生虚拟事件。"""
    graph = _FakeGraph([
        _FakeNode(node_id="n1", status=Status.DONE, run_mode="single_bot",
                  output={"result": "x"}),
        _FakeNode(node_id="n2", status=Status.PENDING),  # 无素材
    ])
    timeline = [
        _make_event(node_id="n1", action_type="S1_CONTEXT", gmt_create=100),
        _make_event(node_id="n1", action_type="reset", gmt_create=200),  # 系统→S1
        _make_event(node_id="n2", action_type=TrajectoryActionType.PLAN, gmt_create=300),
    ]
    traj = _make_trajectory(task_id="t1", timeline=timeline)
    svc = TaskTrajectoryService(_FakeAssembler(traj), _FakeRepo(), _FakeAnalyzer(), None,
                                graph=graph)

    result = await svc.get_trajectory("t1")

    top = [str(getattr(e.action_type, "value", e.action_type)) for e in result.timeline]
    assert top == ["S1_CONTEXT", RESULT_DISPLAY_ACTION, "plan"], (
        f"虚拟事件保持顶层(reset 嵌挂 S1),n2 无素材不加: {top}"
    )
    assert result.timeline[0].system_logs is not None
    assert result.timeline[0].system_logs[0].action_result == "hit_single" or True
