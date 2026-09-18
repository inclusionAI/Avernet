"""TDD tests for the trajectory emission helper (REQ-11 payloads half, P2).

The emitter is the **independent direct-INSERT 旁路** the engine gates will later
call (P3 wires the gates; this phase delivers the helper only). It:

* builds a table-faithful ``TrajectoryEventRecord`` from gate-call fields,
  converting domain enums → ``.value`` strings and ``now_ms`` (int epoch ms)
  → ``datetime`` for ``gmt_create``/``gmt_modified``,
* serializes ``ext_info: dict | None`` as a JSON string wrapped in
  ``{"schema_v": 1, **ext_info}`` (or ``None`` when no ``ext_info``),
* calls ``repo.insert_event(record)`` directly — **no** thread through the
  in-memory graph ``append_action_event`` path,
* and is zero-intrusion: any exception is swallowed + logged at WARNING
  (decision #14; AGENTS.md 'propagate persistence write failures' is explicitly
  waived for this fire-and-forget observational旁路), never re-raised.

These tests pin that contract before the implementation exists (TDD red→green).
They use tiny fakes — no engine, no SQLite — so the emitter is exercised in
isolation.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.types import TrajectoryEventRecord
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
    TrajectoryActionType,
)
from agentclaw.community.core.task.task_context.task_trajectory.payloads import (
    emit_trajectory_event,
    build_trajectory_event_record,
)


NOW_MS = 1_700_000_000_123  # arbitrary fixed epoch ms for deterministic asserts


def _now_from_ms(now_ms: int = NOW_MS) -> datetime:
    """Mirror of the emitter's int-ms→datetime conversion (naive UTC, matching
    the existing trajectory repository's ``datetime.utcnow()`` convention +
    the naive ``DateTime`` ORM columns). Used wherever a test asserts
    ``gmt_create``/``gmt_modified`` produced from ``now_ms``."""
    return datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Fake repos — no SQLite, no DI required.
# ---------------------------------------------------------------------------


class _FakeRepo:
    """Records every ``insert_event`` call; returns the record unchanged."""

    def __init__(self) -> None:
        self.records: list[TrajectoryEventRecord] = []
        self.calls = 0

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        self.calls += 1
        self.records.append(record)
        return record


class _RaisingRepo:
    """Always raises on insert — proves the emitter swallows + logs."""

    def __init__(self) -> None:
        self.calls = 0

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        self.calls += 1
        raise RuntimeError("trajectory boom")


# ---------------------------------------------------------------------------
# build_trajectory_event_record — the pure builder half
# ---------------------------------------------------------------------------


def test_build_record_maps_enums_to_value_strings():
    rec = build_trajectory_event_record(
        "T-1",
        "N-1",
        TrajectoryActionType.DISPATCH,
        action_result="hit_single",
        attempt=3,
        action_input="candidates=3,target=N-1",  # not truncated (spec invariant)
        error_type=ReasonCatalog.UNDERLYING_INTERFACE_ERROR,
        error_msg="interface exploded",
        status_from=Status.PLANNING,
        status_to=Status.RUNNING,
        ext_info={"strategy": "direct", "candidates": 3},
        now_ms=NOW_MS,
    )
    assert isinstance(rec, TrajectoryEventRecord)
    # Domain enums serialized to their .value strings on the record.
    # (Status is uppercase: PENDING="PENDING", PLANNING="PLANNING", ...; the
    # other trajectory enums are lowercase — DISC="dispatch", parse_error, ...)
    assert rec.action_type == "dispatch"
    assert rec.status_from == Status.PLANNING.value
    assert rec.status_to == Status.RUNNING.value
    assert rec.error_type == "underlying_interface_error"
    # Flat fields pass through unchanged.
    assert rec.task_id == "T-1"
    assert rec.node_id == "N-1"
    assert rec.action_result == "hit_single"
    assert rec.attempt == 3
    assert rec.action_input == "candidates=3,target=N-1"
    assert rec.error_msg == "interface exploded"
    # ext_info is a JSON STRING wrapped with schema_v + payload keys.
    assert isinstance(rec.ext_info, str)
    payload = json.loads(rec.ext_info)
    assert payload["schema_v"] == 1
    assert payload["strategy"] == "direct"
    assert payload["candidates"] == 3
    # gmt_create / gmt_modified come from now_ms (int ms → datetime), equal.
    assert rec.gmt_create == _now_from_ms()
    assert rec.gmt_modified == rec.gmt_create
    # analysis is always None at emit time (filled only on backfill, P5).
    assert rec.analysis is None
    # id is a placeholder the repo ignores (autoincrement assigns the real id).
    assert rec.id == 0


def test_build_record_ext_info_none_when_not_supplied():
    rec = build_trajectory_event_record(
        "T-1", "N-1", TrajectoryActionType.PLAN,
        action_result="success", now_ms=NOW_MS,
    )
    assert rec.ext_info is None
    # Optional fields default to None when not passed.
    assert rec.action_input is None
    assert rec.error_type is None
    assert rec.error_msg is None
    assert rec.status_from is None
    assert rec.status_to is None
    # attempt defaults to 0 (spec default — gates that omit attempt mean 0).
    assert rec.attempt == 0


def test_build_record_enums_accept_plain_strings_too():
    """Defensive: a caller may pass the raw string (``.value``) directly.
    The builder must not double-wrap or choke — it passes through."""
    rec = build_trajectory_event_record(
        "T-1", "N-1", "execute",
        action_result="failed",
        error_type="parse_error",
        status_from="running", status_to="failed",
        now_ms=NOW_MS,
    )
    assert rec.action_type == "execute"
    assert rec.error_type == "parse_error"
    assert rec.status_from == "running"
    assert rec.status_to == "failed"


# ---------------------------------------------------------------------------
# emit_trajectory_event — normal path (one record, correct fields)
# ---------------------------------------------------------------------------


def test_emit_normal_inserts_exactly_one_record_with_correct_fields():
    repo = _FakeRepo()
    emit_trajectory_event(
        repo,
        "T-9", "N-2", TrajectoryActionType.EXECUTE,
        action_result="failed",
        attempt=1,
        action_input="request_input原文",
        error_type=ReasonCatalog.UNDERLYING_INTERFACE_ERROR,
        error_msg="bot returned exec_error",
        ext_info={"strategy": "search", "candidate": "b1", "score": 0.9, "reason": "中文不转义"},
        status_from=Status.RUNNING,
        status_to=Status.RUNNING,
        now_ms=NOW_MS,
    )
    assert repo.calls == 1
    assert len(repo.records) == 1
    rec = repo.records[0]
    assert rec.task_id == "T-9"
    assert rec.node_id == "N-2"
    assert rec.action_type == "execute"
    assert rec.action_result == "failed"
    assert rec.attempt == 1
    assert rec.action_input == "request_input原文"
    assert rec.error_type == "underlying_interface_error"
    assert rec.error_msg == "bot returned exec_error"
    # Status is uppercase ("RUNNING", not "running") — emitter maps to .value.
    assert rec.status_from == Status.RUNNING.value
    assert rec.status_to == Status.RUNNING.value
    # ext_info JSON string carries schema_v AND the payload.
    assert isinstance(rec.ext_info, str)
    payload = json.loads(rec.ext_info)
    assert payload["schema_v"] == 1
    assert payload["strategy"] == "search"
    assert payload["candidate"] == "b1"
    assert payload["score"] == 0.9
    # ensure_ascii=False (codebase convention): Chinese content stays readable
    # in the raw JSON string, not \uXXXX-escaped (Minor #2 regression guard).
    assert payload["reason"] == "中文不转义"
    assert "中文不转义" in rec.ext_info
    # gmt_* both set from now_ms (int-ms → datetime).
    assert rec.gmt_create == _now_from_ms()
    assert rec.gmt_modified == rec.gmt_create
    # analysis is None at emit time (only backfill writes it).
    assert rec.analysis is None


def test_emit_ext_info_none_means_record_ext_info_is_none():
    """When the gate passes no ext_info, the record's ext_info column is NULL
    (not an empty JSON object) — matches the spec DDL default + the assembler."""
    repo = _FakeRepo()
    emit_trajectory_event(
        repo, "T-1", "N-1", TrajectoryActionType.RESET,
        action_result="success", now_ms=NOW_MS,
    )
    assert repo.records[0].ext_info is None


def test_emit_defaults_attempt_to_zero_when_omitted():
    repo = _FakeRepo()
    emit_trajectory_event(
        repo, "T-1", "N-1", TrajectoryActionType.PLAN,
        action_result="success", now_ms=NOW_MS,
    )
    assert repo.records[0].attempt == 0


# ---------------------------------------------------------------------------
# emit_trajectory_event — repo is None (no-op, no raise, no insert)
# ---------------------------------------------------------------------------


def test_emit_with_repo_none_is_noop_and_does_not_raise(caplog):
    """The engine runs without a trajectory repo in tests / lightweight DI —
    the emitter must skip silently (the engine never depends on the row being
    written). Pins the SILENCE contract, not just "didn't raise": the None
    branch is silent at WARNING too, because decision #14's visibility is
    reserved for actual emission failures, not for an intentionally-disabled
    repo (intentionally-not-configured ≠ misbound)."""
    with caplog.at_level(logging.WARNING, logger="task.trajectory"):
        emit_trajectory_event(
            None,  # type: ignore[arg-type]
            "T-1", "N-1", TrajectoryActionType.EXECUTE,
            action_result="failed",
            action_input="anything",
            ext_info={"a": 1},
            now_ms=NOW_MS,
        )
    # No insert path taken AND no log emitted — None branch is silent.
    assert not caplog.records


# ---------------------------------------------------------------------------
# emit_trajectory_event — repo raises (swallow + WARNING + no re-raise)
# ---------------------------------------------------------------------------


def test_emit_swallows_repo_exception_and_logs_warning(caplog):
    repo = _RaisingRepo()
    with caplog.at_level(logging.WARNING, logger="task.trajectory"):
        # Must NOT raise — the forward-driving gate stays unaffected.
        ret = emit_trajectory_event(
            repo,
            "T-7", "N-3", TrajectoryActionType.EXECUTE,
            action_result="failed",
            attempt=2,
            ext_info={"k": "v"},
            now_ms=NOW_MS,
        )
    # Repo was attempted (exactly once); emitter did not retry or abort early.
    assert repo.calls == 1
    # Return value is None — the emitter never returns a failure to the caller.
    assert ret is None
    # A WARNING record was emitted (level==WARNING, not DEBUG) — observable
    # so operators can see missing trajectory data (decision #14).
    warning_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "task.trajectory" in r.name
    ]
    assert len(warning_records) == 1, [r.getMessage() for r in caplog.records]
    msg = warning_records[0].getMessage()
    # The log carries the identifying context (task/node/action) + the error.
    assert "T-7" in msg
    assert "N-3" in msg
    assert "execute" in msg
    assert "trajectory boom" in msg


def test_emit_swallows_exception_even_without_ext_info(caplog):
    """Even a minimal call (no ext_info/action_input) must swallow + warn."""

    class _RepoAttrError:
        def insert_event(self, record):
            raise AttributeError("missing db handle")

    with caplog.at_level(logging.WARNING, logger="task.trajectory"):
        ret = emit_trajectory_event(
            _RepoAttrError(), "T-8", "N-4", TrajectoryActionType.TRANSITION,
            action_result="done",
        )
    assert ret is None
    warning_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "task.trajectory" in r.name
    ]
    assert len(warning_records) == 1
