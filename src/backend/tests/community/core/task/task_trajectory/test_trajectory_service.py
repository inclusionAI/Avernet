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
    _persisted_timeline_version,
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

    result = await svc.get_trajectory("t1", do_analysis=True)

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
@pytest.mark.unit
async def test_do_analysis_true_returns_existing_analysis_without_recalling_bot():
    """``do_analysis=true`` is INCREMENTALLY idempotent: the persisted analysis
    carries a ``timeline_version`` stamp; when the re-computed fingerprint of the
    task's current event rows EQUALS that stamp (timeline unchanged since the
    last backfill), return the persisted analysis directly WITHOUT calling the
    bot / no backfill (省一次 bot 调用 / 504 风险). 决策 #13's "every
    do_analysis=true OVERWRITES" is revised to "re-analyze only when the
    timeline moved". 决策 #13's no-merge (when it DOES run) still holds."""
    records = [
        _make_record(node_id="n1", action_type="dispatch", attempt=0, gmt_create_ms=1000),
        _make_record(node_id="n1", action_type="execute", attempt=0, gmt_create_ms=2000, rec_id=2),
    ]
    stamp = _timeline_fingerprint(records)
    persisted = json.dumps({"analysis_type": "tc_bot", "timeline_version": stamp}, ensure_ascii=False)
    traj = _make_trajectory(analysis=persisted)
    assembler = _FakeAssembler(traj)
    analyzer = _FakeAnalyzer(analysis=_make_analysis())  # would run only if NOT short-circuited
    repo = _FakeRepo(events=records)
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(assembler, repo, analyzer, config)

    result = await svc.get_trajectory("t1", do_analysis=True)

    # version-matched analysis returned verbatim; bot NOT called; no backfill
    assert result.analysis == persisted
    assert analyzer.calls == 0
    assert repo.backfill_calls == []


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

    result = await svc.get_trajectory("t1", do_analysis=True)

    # stale stamp did NOT short-circuit: bot re-ran and re-stamped
    assert analyzer.calls == 1
    assert len(repo.backfill_calls) == 1
    backfilled = json.loads(repo.backfill_calls[0][1])
    assert backfilled["timeline_version"] == _timeline_fingerprint(new_records)
    assert result.analysis == repo.backfill_calls[0][1]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_reruns_legacy_analysis_without_timeline_version():
    """A legacy persisted analysis (pre-versioning) carries no stamp → mismatch
    → re-analyzes exactly once; the backfill then carries the stamp."""
    traj = _make_trajectory(analysis='{"old": true}')
    assembler = _FakeAssembler(traj)
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(assembler, repo, analyzer, config)

    result = await svc.get_trajectory("t1", do_analysis=True)

    assert analyzer.calls == 1
    assert len(repo.backfill_calls) == 1
    assert json.loads(repo.backfill_calls[0][1])["timeline_version"] is not None
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


@pytest.mark.unit
def test_persisted_timeline_version_defensive_parse():
    """``_persisted_timeline_version`` degrades to None on every legacy/corrupt
    shape (the never-matches sentinel) and reads the stamp from valid JSON."""
    assert _persisted_timeline_version(None) is None
    assert _persisted_timeline_version("") is None
    assert _persisted_timeline_version("{broken") is None
    assert _persisted_timeline_version('["array-not-dict"]') is None
    assert _persisted_timeline_version('{"old": true}') is None
    assert _persisted_timeline_version('{"timeline_version": ""}') is None
    assert _persisted_timeline_version('{"timeline_version": "abc"}') == "abc"


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

    result = await svc.get_trajectory("t1", do_analysis=True)

    assert analyzer.calls == 1
    # backfilled stamp is None (records unreadable → no fingerprint claimed)
    assert json.loads(repo.backfill_calls[0][1])["timeline_version"] is None
    assert result.analysis == repo.backfill_calls[0][1]


# ---------------------------------------------------------------------------
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
        await svc.get_trajectory("t1", do_analysis=True)

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
        await svc.get_trajectory("t1", do_analysis=True)

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
        await svc.get_trajectory("t1", do_analysis=True)

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

    await svc.get_trajectory("t1", do_analysis=True)

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
    ``status`` / ``run_info.extend_props`` / ``run_info.start_time``."""

    def __init__(self, *, node_id: str, status: Status, extend_props: dict | None = None,
                 start_time: int | None = None) -> None:
        self.node_id = node_id
        self.status = status
        self.run_info = RuntimeInfo(start_time=start_time, extend_props=dict(extend_props or {}))


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
    """Serves ``get_session_messages``; scriptable per-session payloads/failures."""

    def __init__(self, *, messages_by_session: dict | None = None,
                 raise_exc: Exception | None = None) -> None:
        self._messages = messages_by_session or {}
        self._raise = raise_exc
        self.calls: list[tuple[str, int]] = []

    async def get_session_messages(self, session_id: str, *, limit: int = 50,
                                   since_msg_id: str | None = None) -> list:
        self.calls.append((session_id, limit))
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

    await svc.get_trajectory("t1", do_analysis=True)

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
    assert [sid for sid, _ in bcs.calls] == ["sess-1"]


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

    await svc.get_trajectory("t1", do_analysis=True)

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

    result = await svc.get_trajectory("t1", do_analysis=True)

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

    await svc.get_trajectory("t1", do_analysis=True)

    assert analyzer.last_running_sessions is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_fast_path_skips_probe():
    """Timeline-version match (fast path) short-circuits BEFORE the probe —
    neither the graph nor the BCS session read is touched."""
    records: list = []
    stamp = _timeline_fingerprint(records)
    persisted = json.dumps({"timeline_version": stamp}, ensure_ascii=False)
    graph = _FakeGraph([
        _FakeNode(node_id="n-run", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}),
    ])
    bcs = _FakeBcs(messages_by_session={"sess-1": [{"role": "user", "content": "hi"}]})
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo(events=records)
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory(analysis=persisted)), repo, analyzer, config,
        graph=graph, bcs=bcs,
    )

    await svc.get_trajectory("t1", do_analysis=True)

    assert analyzer.calls == 0
    assert graph.query_calls == 0
    assert bcs.calls == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_do_analysis_probe_truncates_long_messages():
    """Single messages over the per-message budget and briefs over the total
    budget are truncated + flagged (token-growth containment)."""
    long = "x" * 5_000
    graph = _FakeGraph([
        _FakeNode(node_id="n-run", status=Status.RUNNING,
                  extend_props={"session_id": "sess-1"}),
    ])
    bcs = _FakeBcs(messages_by_session={"sess-1": [{"role": "assistant", "content": long}]})
    analyzer = _FakeAnalyzer(analysis=_make_analysis())
    repo = _FakeRepo()
    config = TrajectoryAnalysisConfig(analysis_bot_id="bot-traj-analyst")
    svc = TaskTrajectoryService(
        _FakeAssembler(_make_trajectory()), repo, analyzer, config,
        graph=graph, bcs=bcs,
    )

    await svc.get_trajectory("t1", do_analysis=True)

    briefs = analyzer.last_running_sessions
    assert len(briefs) == 1 and briefs[0]["messages"][0]["content"] is not None
    assert len(briefs[0]["messages"][0]["content"]) <= 600 + 1
    assert briefs[0]["message_count"] == 1
