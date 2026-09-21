"""TDD tests for ``TaskTrajectoryAssembler`` (REQ-8 read side, P4).

The assembler is the READ-SIDE pure function that, given a ``task_id``, reads
``task_trajectory_events`` (ordered by ``gmt_create ASC, id ASC`` from the P1b
repository), maps each ``TrajectoryEventRecord`` to a domain ``TrajectoryEvent``,
reads the head row's persisted ``analysis`` (``list_head``), UPSERTs the head
(``upsert_head`` — preserves existing ``analysis``/``gmt_modified``), and
builds a ``TaskTrajectory{task_id, timeline, analysis, gmt_create, gmt_modified}``
with NO ``phases`` / NO ``graph_snapshot``.

Independent entity: takes ONLY ``TaskTrajectoryRepositoryProtocol``; NEVER
touches ``task_action_log`` / ``NodeAction`` / ``append_action_event``
(spec invariant). A wholesale repo read error propagates (决策 #14's waiver is
EMISSION-only); a single corrupt row is skipped with a WARNING so one bad row
does not break the whole timeline.

Test categories (mirrors the P4 task plan):
1. Ordering: timeline respects (gmt_create, id) ASC; SUBMIT first, terminal
   TRANSITION last; same-second events ordered by id (repo → assembler passes
   the order through).
2. Field round-trip: int-ms inverse of the P2 emitter's timezone-less
   Asia/Shanghai datetime; enum round-trips (action_type/error_type LOWERCASE,
   status_from/status_to UPPERCASE — the case-mismatch gotcha),
   ``ext_info``/``analysis`` passed through as raw JSON strings (NOT parsed).
3. Pre-trajectory old task: empty timeline + analysis=None; NO action_log read.
4. UPSERT head preserves analysis: a backfilled ``analysis`` survives assemble
   (the assembler must NOT overwrite — P1b's ``upsert_head`` preserves it; the
   assembler relies on that and returns the persisted value).
5. Case-mismatch resilience: per-field enum independence (don't accidentally
   ``Status(action_type)``).
6. Independent entity: structurally takes ONLY ``TaskTrajectoryRepositoryProtocol``;
   AST-walk the assembler source — no imports/references to
   ``task_action_log`` / ``NodeAction`` / ``append_action_event``.
7. Corrupt-row resilience: one row with an unknown enum value is skipped + a
   WARNING lands; good rows survive.
8. Read-failure propagation: a wholesale repo error (``list_events_by_task`` /
   ``list_head`` / ``upsert_head``) propagates (NOT swallowed — 决策 #14's
   waiver is emission-only; the assembler is a read).
9. DI registration: the assembler is bound in the task DI module (so P5's
   service can ``Injected(...)`` it).
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import logging
from dataclasses import replace
from datetime import datetime

import pytest
from injector import Injector
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.task.task_trajectory_repository import (
    TaskTrajectoryRepository,
)
from agentclaw.community.core.repository.protocols.task import (
    TaskTrajectoryRepositoryProtocol,
)
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.types import (
    TaskTrajectoryRecord,
    TrajectoryEventRecord,
)
import agentclaw.community.core.task.repository.models  # noqa: F401  registers ORM models
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryEvent,
)

# The assembler module under test (RED: this import fails until implemented).
from agentclaw.community.core.task.task_context.task_trajectory.assembler import (
    TaskTrajectoryAssembler,
)
from agentclaw.community.core.task.task_context.task_trajectory.time_utils import (
    epoch_ms_to_storage_datetime,
    storage_now,
)


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

_MS_BASE = 1_700_000_000_000  # arbitrary epoch-ms anchor for round-trip tests


def _storage_dt(ms: int) -> datetime:
    """Inverse of the P2 emitter's ``_now_datetime``: build the naive-Beijing
    ``datetime`` the record would carry for the given int-ms epoch. The
    assembler must invert this exactly to round-trip back to ``ms``."""
    return epoch_ms_to_storage_datetime(ms)


def _event_record(
    task_id: str = "T-1",
    node_id: str = "N-1",
    *,
    action_type: str = "submit",
    action_result: str = "success",
    attempt: int = 0,
    id: int = 1,
    ms: int = _MS_BASE,
    action_input: str | None = None,
    ext_info: str | None = None,
    analysis: str | None = None,
    status_from: str | None = None,
    status_to: str | None = None,
    error_type: str | None = None,
    error_msg: str | None = None,
    boost_reason: str | None = None,
) -> TrajectoryEventRecord:
    """Build a TrajectoryEventRecord with naive-Beijing gmt_* derived from int-ms
    (mirrors the P2 emitter's ``_now_datetime`` so round-trip tests use the
    actual stored convention rather than a parallel formula)."""
    dt = _storage_dt(ms)
    return TrajectoryEventRecord(
        id=id,
        task_id=task_id,
        node_id=node_id,
        action_type=action_type,
        action_result=action_result,
        attempt=attempt,
        action_input=action_input,
        status_from=status_from,
        status_to=status_to,
        error_type=error_type,
        error_msg=error_msg,
        boost_reason=boost_reason,
        ext_info=ext_info,
        analysis=analysis,
        gmt_create=dt,
        gmt_modified=dt,
    )


class _FakeRepo:
    """A controllable fake of ``TaskTrajectoryRepositoryProtocol``.

    Tracks call counts and mirrors the P1b contract that the assembler relies
    on: ``list_events_by_task`` returns rows ordered by ``(gmt_create, id)
    ASC``; ``upsert_head`` preserves the existing analysis/gmt_modified (does
    NOT mutate the stored head); ``list_head`` returns the stored head or
    ``None``. Any of the three read paths can be made to raise to test
    read-failure propagation (决策 #14: read failures are NOT swallowed).
    """

    def __init__(self, *, events=None, head=None) -> None:
        self._events: list[TrajectoryEventRecord] = list(events or [])
        self._head: TaskTrajectoryRecord | None = head
        self.calls: dict[str, int] = {
            "list_events_by_task": 0,
            "list_head": 0,
            "upsert_head": 0,
        }
        self.raise_events = False
        self.raise_head = False
        self.raise_upsert = False

    # read-side methods the assembler uses
    def list_events_by_task(self, task_id: str) -> list[TrajectoryEventRecord]:
        self.calls["list_events_by_task"] += 1
        if self.raise_events:
            raise RuntimeError("list_events_by_task boom")
        matching = [r for r in self._events if r.task_id == task_id]
        # mirror the P1b contract: (gmt_create ASC, id ASC)
        return sorted(
            matching,
            key=lambda r: (
                r.gmt_create if r.gmt_create is not None else datetime.min,
                r.id,
            ),
        )

    def list_head(self, task_id: str) -> TaskTrajectoryRecord | None:
        self.calls["list_head"] += 1
        if self.raise_head:
            raise RuntimeError("list_head boom")
        return self._head

    def upsert_head(
        self,
        task_id: str,
        *,
        analysis: str | None = None,
    ) -> TaskTrajectoryRecord:
        self.calls["upsert_head"] += 1
        if self.raise_upsert:
            raise RuntimeError("upsert_head boom")
        if self._head is None:
            # Mirror the P1b insert path — fresh naive-Beijing gmt_*.
            now = storage_now()
            self._head = TaskTrajectoryRecord(
                id=1,
                task_id=task_id,
                analysis=analysis,
                gmt_create=now,
                gmt_modified=now,
            )
        # If the head already existed, DO NOT mutate (preserve analysis/gmt_modified).
        return self._head

    # insert/backfill are NOT called by the assembler; provided for completeness
    # so this fake can stand in for the trajectory repo in integration-style setup.
    def insert_event(self, record):
        self._events.append(record)
        return record

    def backfill_analysis(self, task_id, analysis_json, *, now=None):
        ts = now or storage_now()
        if self._head is not None:
            self._head = replace(self._head, analysis=analysis_json, gmt_modified=ts)
        self._events = [
            replace(r, analysis=analysis_json, gmt_modified=ts) if r.task_id == task_id else r
            for r in self._events
        ]
        return 1 + sum(1 for r in self._events if r.task_id == task_id)


def _build_assembler(repo=None) -> TaskTrajectoryAssembler:
    return TaskTrajectoryAssembler(repo or _FakeRepo())


# ---------------------------------------------------------------------------
# 1. Ordering — (gmt_create ASC, id ASC) from the repo carries through
# ---------------------------------------------------------------------------


def test_assemble_orders_timeline_by_gmt_create_then_id():
    """Plant 6 events out of (gmt_create, id) order; the fake repo returns them
    already ASC-sorted (it mirrors the repo contract). The assembler passes
    the order through: timeline[0]==submit (earliest), timeline[-1]==transition
    (terminal last). The two same-gmt_create events must come back in id ASC."""
    same_second = _MS_BASE + 10_000  # +10s
    records = [
        # DELIBERATELY not in (gmt_create, id) order in this list — the fake
        # re-sorts on read; the assembler relies on the repo's order.
        _event_record(  # transition (terminal, latest)
            id=6, ms=same_second + 20_000, action_type="transition",
            action_result="success",
            status_from="DONE", status_to="SUCCESS",
        ),
        _event_record(  # submit (first)
            id=1, ms=same_second - 10_000, action_type="submit",
            action_result="success",
            status_from=None, status_to="PENDING",
        ),
        _event_record(  # dispatch (same second as execute, lower id)
            id=3, ms=same_second, action_type="dispatch",
            action_result="hit_single",
            status_from="PLANNING", status_to="RUNNING",
        ),
        _event_record(id=2, ms=same_second - 5_000, action_type="plan",
                      action_result="success"),
        _event_record(  # execute (same second as dispatch, higher id)
            id=4, ms=same_second, action_type="execute",
            action_result="success",
            status_from="RUNNING", status_to="DONE",
        ),
        _event_record(id=5, ms=same_second + 5_000, action_type="verify",
                      action_result="success"),
    ]
    repo = _FakeRepo(events=records)
    assembler = _build_assembler(repo)
    tj = assembler.assemble("T-1")
    assert [e.action_type for e in tj.timeline] == [
        TrajectoryActionType.SUBMIT,
        TrajectoryActionType.PLAN,
        TrajectoryActionType.DISPATCH,
        TrajectoryActionType.EXECUTE,
        TrajectoryActionType.VERIFY,
        TrajectoryActionType.TRANSITION,
    ]
    # SUBMIT is the first timeline segment; terminal TRANSITION is the last.
    assert tj.timeline[0].action_type is TrajectoryActionType.SUBMIT
    assert tj.timeline[-1].action_type is TrajectoryActionType.TRANSITION
    # The two same-second events (id=3, id=4) come back in id ASC order.
    same_sec = [e for e in tj.timeline if e.gmt_create == same_second * 1]
    # After mapping, e.gmt_create == int ms; identify by the dispatch/execute
    # action types (which share the same gmt_create second).
    dispatch_idx = [e.action_type for e in tj.timeline].index(TrajectoryActionType.DISPATCH)
    execute_idx = [e.action_type for e in tj.timeline].index(TrajectoryActionType.EXECUTE)
    assert tj.timeline[dispatch_idx].gmt_create == tj.timeline[execute_idx].gmt_create
    # DISPATCH (id=3) precedes EXECUTE (id=4) for same gmt_create → id tiebreak.
    assert dispatch_idx < execute_idx


# ---------------------------------------------------------------------------
# 2. Field round-trip — int-ms inverse of naive-Beijing + enum round-trip + raw
#    JSON pass-through (NOT parsed)
# ---------------------------------------------------------------------------


def test_assemble_maps_record_fields_round_trip():
    """Each TrajectoryEvent field comes from the record correctly:
    - int-ms gmt_create is the EXACT inverse of the P2 emitter's naive-Beijing
      datetime (so this test FAILS on a non-UTC host if the assembler uses the
      bare ``int(dt.timestamp()*1000)`` that interprets naive as local time);
    - action_type (lowercase) → TrajectoryActionType;
    - status_from/status_to (UPPERCASE) → Status;
    - error_type (lowercase) → ReasonCatalog (None when absent);
    - ext_info / analysis passed through as raw JSON strings (NOT parsed);
    - attempt / task_id / node_id / action_input / error_msg pass through.
    """
    ms = 1_697_000_000_456  # arbitrary
    records = [
        _event_record(
            id=11, ms=ms, action_type="execute", action_result="failed",
            attempt=2,
            action_input="POST /bot { 'req': 'raw' }",
            status_from="RUNNING", status_to="FAILED",
            error_type="underlying_interface_error",
            error_msg="interface exploded: timeout",
            boost_reason="候选能力匹配",
            ext_info='{"schema_v":1,"holder_id":"bot-holder-1","候选":"中文"}',
            analysis='{"analysis_type":"tc_bot","executor":"bot-1"}',
        ),
    ]
    repo = _FakeRepo(events=records)
    tj = _build_assembler(repo).assemble("T-1")
    assert len(tj.timeline) == 1
    ev = tj.timeline[0]
    # int-ms is the exact round-trip of the emitter's naive-Beijing datetime.
    assert ev.gmt_create == ms, (
        f"gmt_create round-trip failed: expected {ms}, got {ev.gmt_create} "
        f"(assembler may be treating naive dt as local time on a non-UTC host)"
    )
    assert ev.gmt_modified == ms
    # Enum round-trips via per-field enum (case-mismatch safe).
    assert ev.action_type is TrajectoryActionType.EXECUTE  # lowercase
    assert ev.status_from is Status.RUNNING                  # UPPERCASE
    assert ev.status_to is Status.FAILED                     # UPPERCASE
    assert ev.error_type is ReasonCatalog.UNDERLYING_INTERFACE_ERROR  # lowercase
    # ``analysis`` and ``action_input`` are the JSON/str-shaped DOMAIN fields
    # (per REQ-1: ``TrajectoryEvent`` has ``action_input`` & ``analysis`` but
    # NOT ``ext_info``). They are passed through as the RAW strings the emitter
    # wrote (NOT parsed) — the P5 analyzer parses on demand.
    assert ev.action_input == "POST /bot { 'req': 'raw' }"
    assert ev.analysis == '{"analysis_type":"tc_bot","executor":"bot-1"}'
    # ``ext_info`` is the OTHER JSON-shaped column the emitter wrote, BUT it
    # is INTENTIONALLY not mapped onto the domain object (spec REQ-1 / REQ-9:
    # "ext_info 自由 JSON 列(领域对象不映射, analyzer 按需读)"); the analyzer
    # re-queries ``task_trajectory_events.ext_info`` via ``ext_info_lookup``.
    # Assert it is structurally absent from the domain object.
    assert not hasattr(ev, "ext_info"), (
        "TrajectoryEvent must NOT carry ext_info — spec REQ-1 mandates the "
        "domain object does not map it (the analyzer queries the DB directly)"
    )
    # Plain pass-throughs.
    assert ev.task_id == "T-1"
    assert ev.node_id == "N-1"
    assert ev.attempt == 2
    assert ev.action_result == "failed"
    assert ev.error_msg == "interface exploded: timeout"
    assert ev.boost_reason == "候选能力匹配"
    assert ev.holder_id == "bot-holder-1"
    # The record's ext_info is NOT lost — it remains in the table (asserted via
    # ``repo.list_events_by_task`` round-trip; the analyzer reads it later).
    persisted = repo.list_events_by_task("T-1")
    assert len(persisted) == 1
    assert persisted[0].ext_info == (
        '{"schema_v":1,"holder_id":"bot-holder-1","候选":"中文"}'
    )


def test_assemble_keeps_event_when_ext_info_is_invalid_json():
    """损坏的自由 JSON 仅令 holder_id 缺失，不能丢弃整条轨迹事件。"""
    record = _event_record(
        action_type="relay",
        action_result="execution_result",
        ext_info="{invalid-json",
        boost_reason="执行完成",
    )
    trajectory = _build_assembler(_FakeRepo(events=[record])).assemble("T-1")

    assert len(trajectory.timeline) == 1
    assert trajectory.timeline[0].holder_id is None
    assert trajectory.timeline[0].boost_reason == "执行完成"


# ---------------------------------------------------------------------------
# 3. Pre-trajectory old task — empty timeline, analysis=None, no action_log read
# ---------------------------------------------------------------------------


def test_assemble_pre_trajectory_old_task_returns_empty_timeline_and_no_analysis():
    """A task with no trajectory rows and no head: assemble returns an EMPTY
    timeline, analysis=None, and still calls upsert_head (per task step 4 —
    "refreshes the head's existence/gmt_create if absent")."""
    repo = _FakeRepo()  # no events, no head
    assembler = _build_assembler(repo)
    tj = assembler.assemble("ancient-task")
    assert tj.task_id == "ancient-task"
    assert tj.timeline == []
    assert tj.analysis is None
    # gmt_create/gmt_modified populated (fresh head row created by upsert_head).
    assert tj.gmt_create > 0
    assert tj.gmt_modified == tj.gmt_create  # head inserted now: gmt_create == gmt_modified
    # The assembler read the head (None), then UPSERTed (creating it).
    assert repo.calls["list_events_by_task"] == 1
    assert repo.calls["list_head"] == 1
    assert repo.calls["upsert_head"] == 1


def test_assemble_does_not_read_or_query_task_action_log(caplog):
    """The assembler's assembler reads ONLY trajectory tables — it must NOT
    query ``task_action_log``. A runtime guard: a fake that exposes an action-log
    repo attribute (here a sentinel object with a ``list_by_task`` spy) must NOT
    have its action-log spy invoked by assemble. The structural (AST) guard
    lives in the dedicated decoupling test below."""
    class _ActionLogSpy:
        def __init__(self):
            self.calls = 0
        def list_by_task(self, *a, **kw):
            self.calls += 1
            return []

    spy = _ActionLogSpy()
    repo = _FakeRepo()
    # The assembler only holds a trajectory repo; it has NO slot for an
    # action-log repo, so attaching the spy here is purely for instrumentation:
    # if assemble ever fell back to the action_log path via this object's
    # unrelated attribute, spy.calls would increase.
    repo._action_log_spy = spy  # attach somewhere the assembler could touch only by mistake

    with caplog.at_level(logging.WARNING, logger="task.trajectory"):
        _build_assembler(repo).assemble("T-1")
    assert spy.calls == 0, (
        "the assembler MUST NOT read task_action_log (spec invariant: 独立旁路; "
        "pre-trajectory old tasks get an EMPTY timeline, NOT an action_log fallback)"
    )


# ---------------------------------------------------------------------------
# 4. UPSERT head preserves analysis — integration via the REAL repo + SQLite
# ---------------------------------------------------------------------------

@pytest.fixture
def _sqlite_db():
    """Real in-memory SQLite DatabasePlugin stand-in (mirrors the repository
    task conftest's fixture) so the assembler's "UPSERT preserves analysis"
    guarantee is exercised through the actual ORM body, not just the fake."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    class _DB:
        def __init__(self, eng):
            self._factory = sessionmaker(bind=eng, autoflush=False)

        from contextlib import contextmanager

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

    return _DB(engine)


def test_assemble_upsert_head_preserves_backfilled_analysis(_sqlite_db):
    """P1b's ``upsert_head`` preserves existing analysis/gmt_modified; the
    assembler MUST rely on that and NOT overwrite. Flow: build a real head,
    backfill it with a known JSON, then ``assemble``; the resulting
    ``TaskTrajectory.analysis`` equals the backfilled JSON and the persisted
    head row (read back after assemble) still carries the same analysis).

    Integration path: uses the real ``TaskTrajectoryRepository`` + SQLite so
    the ORM's preserve-on-conflict semantics are exercised for real, not just
    the fake."""
    repo = TaskTrajectoryRepository(_sqlite_db)
    # Plant an event so the timeline is non-empty.
    repo.insert_event(
        _event_record(id=1, task_id="T-2", node_id="N-1", ms=_MS_BASE,
                      action_type="submit", action_result="success",
                      status_from=None, status_to="PENDING")
    )
    # Build the head row first.
    repo.upsert_head("T-2")
    # Backfill an analysis JSON (as P5's analyzer would on do_analysis=true).
    backfill_ts = datetime(2026, 9, 17, 12, 0, 0)
    backfill_json = '{"analysis_type":"tc_bot","failure_reason":"underlying_interface_error: x"}'
    repo.backfill_analysis("T-2", backfill_json, now=backfill_ts)

    # Now assemble — must NOT overwrite analysis; must read it back.
    assembler = TaskTrajectoryAssembler(repo)
    tj = assembler.assemble("T-2")

    assert tj.analysis == backfill_json, (
        "assemble must return the head's persisted analysis verbatim (P1b's "
        "upsert_head preserves it; the assembler must NOT overwrite)"
    )
    # The persisted head row still carries the backfilled analysis + mtime.
    head_after = repo.list_head("T-2")
    assert head_after is not None
    assert head_after.analysis == backfill_json
    assert head_after.gmt_modified == backfill_ts, (
        "upsert_head (called by assemble) must NOT overwrite gmt_modified"
    )
    # The timeline carries one submit event whose int-ms round-trips.
    assert len(tj.timeline) == 1
    assert tj.timeline[0].action_type is TrajectoryActionType.SUBMIT
    assert tj.timeline[0].gmt_create == _MS_BASE


# ---------------------------------------------------------------------------
# 5. Case-mismatch resilience — per-field enum (independent of one another)
# ---------------------------------------------------------------------------


def test_assemble_maps_per_field_enum_case_independently():
    """The gotcha: ``action_type``/``error_type`` are LOWERCASE while
    ``status_from``/``status_to`` are UPPERCASE. The assembler maps each via its
    OWN enum, not a shared one. Mixing the two is the realistic crash test."""
    records = [
        _event_record(
            id=1, ms=_MS_BASE,
            action_type="execute",       # lowercase → TrajectoryActionType.EXECUTE
            action_result="failed",
            status_from="RUNNING",       # UPPERCASE → Status.RUNNING
            status_to="FAILED",          # UPPERCASE → Status.FAILED
            error_type="transport_error",  # lowercase → ReasonCatalog.TRANSPORT_ERROR
        ),
    ]
    tj = _build_assembler(_FakeRepo(events=records)).assemble("T-1")
    ev = tj.timeline[0]
    # Each field mapped via its OWN enum (case-mismatch-safe).
    assert ev.action_type is TrajectoryActionType.EXECUTE       # lowercase
    assert ev.status_from is Status.RUNNING                     # UPPERCASE
    assert ev.status_to is Status.FAILED                        # UPPERCASE
    assert ev.error_type is ReasonCatalog.TRANSPORT_ERROR       # lowercase
    # Sanity: bare ``Status(action_type)`` would NOT resolve to a real Status
    # (this asserts the two enums are intentionally NOT shared).
    with pytest.raises(ValueError):
        Status("execute")  # lowercase is not a Status value
    with pytest.raises(ValueError):
        TrajectoryActionType("RUNNING")  # UPPERCASE is not a TrajectoryActionType value


# ---------------------------------------------------------------------------
# 6. Independent entity — AST-level "no action_log / NodeAction / append_action_event"
# ---------------------------------------------------------------------------


def test_assembler_module_does_not_import_or_reference_action_log_symbols():
    """Structural decoupling (mirrors the trajectory intrusion guard):
    the assembler source must NOT import ``NodeAction`` / ``TaskActionLog*`` /
    ``task_action_log`` or invoke ``append_action_event``. A future PR that
    re-couples the read side to the action log surfaces here, not by silence."""
    from agentclaw.community.core.task.task_context.task_trajectory import assembler

    src = inspect.getsource(assembler)
    tree = ast.parse(src)

    forbidden_name_substrings = (
        "NodeAction",
        "TaskActionLog",
        "task_action_log",
        "TaskCallback",  # also forbidden per "independent entity" requirement
        "append_action_event",
    )

    # 1. No imports of forbidden symbols.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                for forbidden in forbidden_name_substrings:
                    assert not alias.name.startswith(forbidden), (
                        f"assembler.py imports {alias.name!r} from "
                        f"{node.module!r} — re-couples the read side to "
                        f"task_action_log / NodeAction (spec forbids)"
                    )
            # Also assert the module path is not action_log.
            if node.module and any(
                "task_action_log" in (s or "") for s in (node.module, "")
            ):
                raise AssertionError(
                    f"assembler.py imports from {node.module!r} — re-couples "
                    f"to task_action_log (spec forbids)"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_name_substrings:
                    assert forbidden not in alias.name, (
                        f"assembler.py imports {alias.name!r} — re-couples "
                        f"the read side to task_action_log / NodeAction"
                    )

    # 2. No code-level Name/Attribute reference to forbidden symbols.
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in forbidden_name_substrings:
            raise AssertionError(
                f"assembler.py references {node.id!r} at code level "
                f"(line {node.lineno}) — re-couples the read side to "
                f"task_action_log / NodeAction"
            )
        if isinstance(node, ast.Attribute) and node.attr in forbidden_name_substrings:
            raise AssertionError(
                f"assembler.py references .{node.attr!r} at line {node.lineno} "
                f"— re-couples the read side to task_action_log / NodeAction"
            )


def test_assembler_constructor_takes_only_trajectory_repository_protocol():
    """The assembler's ``__init__`` must accept EXACTLY one parameter besides
    ``self`` — the trajectory repository — and that parameter must be annotated
    as ``TaskTrajectoryRepositoryProtocol``. It must NOT accept
    ``TaskActionLog*`` or ``TaskCallback*`` repos."""
    import typing
    sig = inspect.signature(TaskTrajectoryAssembler.__init__)
    params = [p for p in sig.parameters.values() if p.name != "self"]
    assert len(params) == 1, (
        f"assembler __init__ must take ONLY the trajectory repo; got params: "
        f"{[p.name for p in params]}"
    )
    the_param = params[0]
    assert the_param.name == "repo", (
        f"the single parameter must be named 'repo'; got {the_param.name!r}"
    )
    # Resolve the annotation (``from __future__ import annotations`` stringises
    # hints, so compare against the resolved type via ``get_type_hints``).
    hints = typing.get_type_hints(TaskTrajectoryAssembler.__init__)
    assert hints.get("repo") is TaskTrajectoryRepositoryProtocol, (
        f"the single parameter must be annotated TaskTrajectoryRepositoryProtocol; "
        f"got {hints.get('repo')!r}"
    )


# ---------------------------------------------------------------------------
# 7. Corrupt-row resilience — skip the bad row + log a WARNING; good rows survive
# ---------------------------------------------------------------------------


def test_assemble_skips_corrupt_row_and_logs_warning(caplog):
    """A single record with an unknown action_type (e.g. an old experiment's
    value) is corrupt and MUST be skipped + logged at WARNING (so one bad row
    doesn't break a whole timeline). The good rows before/after it survive and
    appear in the timeline in (gmt_create, id) order."""
    records = [
        _event_record(id=1, ms=_MS_BASE, action_type="submit", action_result="success"),
        _event_record(
            id=2, ms=_MS_BASE + 1_000,
            action_type="zzz_unrecognized_v1",  # corrupt action_type
            action_result="success",
        ),
        _event_record(id=3, ms=_MS_BASE + 2_000, action_type="execute",
                      action_result="success", status_from="RUNNING", status_to="DONE"),
    ]
    repo = _FakeRepo(events=records)
    with caplog.at_level(logging.WARNING, logger="task.trajectory"):
        tj = _build_assembler(repo).assemble("T-1")
    # Two good rows survived; the corrupt row was skipped.
    assert [e.action_type for e in tj.timeline] == [
        TrajectoryActionType.SUBMIT,
        TrajectoryActionType.EXECUTE,
    ]
    # A WARNING was logged naming the corrupt row (so the corruption is visible
    # to operators, not silently swallowed).
    warning_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "task.trajectory" in r.name
    ]
    assert warning_records, (
        "no WARNING logged for the skipped corrupt row — the row was silently "
        "dropped (must be visible per 决策 #14 visibility spirit)"
    )
    msg = warning_records[0].getMessage()
    assert "zzz_unrecognized_v1" in msg or "映射失败" in msg or "boom" in msg.lower(), (
        f"WARNING should name the corrupt row; got: {msg!r}"
    )


def test_assemble_skips_corrupt_status_row_and_logs_warning(caplog):
    """A row with an unknown UPPERCASE status value is also corrupt (the Status
    enum cannot deserialize it) → skip + WARNING."""
    records = [
        _event_record(id=1, ms=_MS_BASE, action_type="transition",
                      action_result="success",
                      status_from="UNKNOWN_STATE", status_to="SUCCESS"),  # corrupt status
    ]
    with caplog.at_level(logging.WARNING, logger="task.trajectory"):
        tj = _build_assembler(_FakeRepo(events=records)).assemble("T-1")
    assert tj.timeline == [], "the corrupt row should have been skipped"
    assert any(
        r.levelno == logging.WARNING and "task.trajectory" in r.name
        for r in caplog.records
    ), "expected a WARNING log for the skipped corrupt-status row"


# ---------------------------------------------------------------------------
# 8. Read-failure propagation — 决策 #14 waiver is emission-only; a READ error
#    propagates (NOT swallowed)
# ---------------------------------------------------------------------------


def test_assemble_propagates_list_events_failure():
    """A wholesale ``list_events_by_task`` failure (the DB read raised) MUST
    propagate — the assembler is a read; 决策 #14's swallow.gui only covers
    EMISSION (fire-and-forget writes). Do NOT swallow a read error."""
    repo = _FakeRepo()
    repo.raise_events = True
    with pytest.raises(RuntimeError, match="list_events_by_task boom"):
        _build_assembler(repo).assemble("T-1")


def test_assemble_propagates_list_head_failure():
    """``list_head`` failure propagates."""
    repo = _FakeRepo()
    repo.raise_head = True
    with pytest.raises(RuntimeError, match="list_head boom"):
        _build_assembler(repo).assemble("T-1")


def test_assemble_propagates_upsert_head_failure():
    """``upsert_head`` failure propagates (the head UPSERT is part of the read
    flow, not an emission — a failure must NOT be swallowed under 决策 #14)."""
    repo = _FakeRepo()
    repo.raise_upsert = True
    with pytest.raises(RuntimeError, match="upsert_head boom"):
        _build_assembler(repo).assemble("T-1")


# ---------------------------------------------------------------------------
# 9. DI registration — the assembler is bound in the task DI module
# ---------------------------------------------------------------------------


def test_assembler_is_bound_in_di_module():
    """The assembler must be registered in DI (per the task's "lean toward DI
    registration" guidance) so P5's service can ``Injected(...)`` it. Resolving
    the bound interface from an injector configured with the persistence module
    (which binds ``TaskTrajectoryRepositoryProtocol`` → the real repo) must
    yield a working ``TaskTrajectoryAssembler`` instance.

    Uses ``TestingDatabaseModule`` to provide the in-memory SQLite
    ``DatabasePlugin`` the trajectory repo's ``__init__(db: DatabasePlugin)``
    needs (mirrors ``test_task_persistence_module``'s wiring)."""
    from agentclaw.community.di.modules.testing_database_module import (
        TestingDatabaseModule,
    )
    from agentclaw.community.di.modules.task_persistence_module import (
        TaskPersistenceModule,
    )

    injector = Injector([TestingDatabaseModule(), TaskPersistenceModule()])
    # If the assembler binding was added in TaskPersistenceModule, resolving
    # TaskTrajectoryAssembler succeeds and yields a working instance whose
    # only dependency (the trajectory repo) is DI-resolved.
    assembler = injector.get(TaskTrajectoryAssembler)
    assert isinstance(assembler, TaskTrajectoryAssembler)
    # The assembler holds the real trajectory repo (DI-resolved via the same
    # module's TaskTrajectoryRepositoryProtocol → TaskTrajectoryRepository bind).
    assert isinstance(assembler._repo, TaskTrajectoryRepository)
    # Singleton: resolving twice returns the same instance.
    assert injector.get(TaskTrajectoryAssembler) is assembler
    # Note: end-to-end ``assemble`` via the DI-resolved repo is exercised by
    # ``test_assemble_upsert_head_preserves_backfilled_analysis`` below (which
    # sets up its own in-memory SQLite with ``Base.metadata.create_all``); the
    # ``TestingDatabaseModule`` SQLite here doesn't create the trajectory
    # tables, so calling ``.assemble`` from this injector would fail on the
    # missing ``task_trajectory_events`` table. This test stays focused on the
    # DI-binding assertion (the integration test owns the end-to-end coverage).


def test_task_trajectory_record_dataclass_shape_unchanged():
    """Sanity: the storage record dataclasses' field shapes the assembler
    depends on are unchanged (this guards against a silent upstream refactor
    that would break the mapping surface)."""
    event_fields = {f.name for f in dataclasses.fields(TrajectoryEventRecord)}
    head_fields = {f.name for f in dataclasses.fields(TaskTrajectoryRecord)}
    assert {"id", "task_id", "node_id", "action_type", "action_result",
            "attempt", "gmt_create", "gmt_modified", "action_input",
            "status_from", "status_to", "error_type", "error_msg",
            "ext_info", "analysis"} <= event_fields
    assert {"id", "task_id", "analysis", "gmt_create", "gmt_modified"} <= head_fields
