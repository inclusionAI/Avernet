"""Trajectory emission helper (REQ-11 payloads half, P2).

The **independent direct-INSERT 旁路** the engine gates will later call (P3 wires
the gates; this phase delivers the helper only). The emitter:

* Builds a table-faithful ``TrajectoryEventRecord`` from gate-call fields —
  domain enums (``TrajectoryActionType`` / ``ReasonCatalog`` / ``Status``) are
  serialized to their ``.value`` strings, ``now_ms`` (int epoch ms) is converted
  to a timezone-less Asia/Shanghai wall-clock ``datetime`` for
  ``gmt_create``/``gmt_modified``, and ``ext_info: dict | None`` is
  JSON-serialized as ``{"schema_v": 1, **ext_info}`` (or ``None`` when no
  ``ext_info``).
* Calls ``repo.insert_event(record)`` directly — **no** thread through the
  in-memory graph ``append_action_event`` path (spec invariant: the trajectory
  side is fully decoupled from ``task_action_log``; see plan §"Spec
  clarifications" #2).
* Is zero-intrusion: any exception is swallowed + logged at ``WARNING``
  (已确认决策 #14) with ``# noqa: BLE001`` — **never re-raised, never returns a
  failure to the caller**. AGENTS.md 'propagate persistence write failures' is
  explicitly waived for this fire-and-forget observational 旁路 (a failure does
  not affect any caller's result; nobody depends on "the row was written").
* No-ops when ``repo is None`` so the engine can run without a trajectory repo
  in tests / lightweight DI injectors.

``action_input`` is **not truncated** (execute/verify request 原文; plan
prompt_digest; submit task_spec_digest) — passed through to the record as-is.
``analysis`` is always ``None`` at emit time (filled only on backfill, P5).
``gmt_create`` = ``gmt_modified`` at emit time (the latter updates on backfill).

Authoritative source: ``specs/2026-09-16-task-trajectory-collection-and-analysis/
spec.md`` (REQ-11, "领域定位/风险" lines, 已确认决策 #14) + ``plan.md`` Phase 2.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from agentclaw.community.core.task.repository.types import TrajectoryEventRecord
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
    TrajectoryActionType,
)
from agentclaw.community.core.task.task_context.task_trajectory.time_utils import (
    epoch_ms_to_storage_datetime,
)

if TYPE_CHECKING:
    # ``Status`` is used only in annotations (the emitter accepts a ``Status``
    # instance at runtime and maps it to ``.value`` via ``getattr``; no runtime
    # reference needs the class). Import under TYPE_CHECKING to keep the
    # runtime import surface minimal and avoid a cross-module cycle in narrow
    # test setups. ``from __future__ import annotations`` stringises the
    # hints so they are never evaluated at runtime.
    from agentclaw.community.core.task.domain.models import Status

logger = logging.getLogger("task.trajectory")

# ``schema_v`` versions every ext_info JSON envelope so the analyzer can
# degrade gracefully on future schema drift (spec §风险与边界). 1 is the first
# (and so far only) revision; P2 emits only this one.
_EXT_INFO_SCHEMA_VERSION = 1

# Domain status enum: the emitter accepts ``Status`` objects and maps them to
# ``.value`` strings; plain strings pass through unchanged. ``Status`` is a
# ``StrEnum`` so a name import would also work, but the lazy
# ``getattr(x, "value", x)`` path keeps the emitter robust to either an enum
# instance OR the already-serialized ``.value`` string (a gate may pre-
# serialize for its own reasons).


class TaskTrajectoryRepositoryLike(Protocol):
    """Structural protocol for the emitter's ``repo`` argument.

    The real ``TaskTrajectoryRepositoryProtocol`` (``core/repository/protocols/
    task.py``) is the canonical contract; this local structural protocol lets
    unit tests pass a tiny fake without importing the full DI-bound protocol
    (and avoids a TYPE_CHECKING cycle). Any object exposing ``insert_event``
    with the right signature satisfies it.
    """

    def insert_event(self, record: TrajectoryEventRecord) -> TrajectoryEventRecord:
        ...


# ---------------------------------------------------------------------------
# Pure builder — produces a TrajectoryEventRecord from gate-call fields
# ---------------------------------------------------------------------------


def _enum_to_str(value: Any) -> str | None:
    """Map a domain enum (or already-string) to its ``.value`` string.

    ``None`` is passed through (e.g. a success event has no ``error_type`` /
    a non-transition event leaves ``status_from``/``status_to`` None).
    A plain ``str`` (a gate that pre-serialized) also passes through unchanged.
    """
    if value is None:
        return None
    # ``TrajectoryActionType`` / ``Status`` / ``ReasonCatalog`` are StrEnums,
    # so ``.value`` is the lowercase string the record expects.
    return getattr(value, "value", value)


def _now_datetime(now_ms: int | None) -> datetime:
    """Convert domain epoch-ms to the naive Beijing ``gmt_*`` convention.

    When ``now_ms`` is omitted, default to the current instant so the event
    still carries an emit-time stamp (spec: "gmt_create = 事件发射时间").
    """
    ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return epoch_ms_to_storage_datetime(ms)


def _serialize_ext_info(ext_info: dict[str, Any] | None) -> str | None:
    """Wrap ``ext_info`` in the versioned envelope and JSON-serialize.

    ``{"schema_v": 1, **ext_info}`` — the ``schema_v`` key lets the analyzer
    degrade on future schema drift (spec §风险与边界). ``None`` (no ext_info)
    stays ``None`` so the record column is NULL and the table DDL default
    is not engaged (matches spec DDL: ``ext_info text DEFAULT NULL``).
    """
    if ext_info is None:
        return None
    if not isinstance(ext_info, dict):
        # Defensive: a gate passing a non-dict is a usage error, but the
        # emitter is zero-intrusion — degrade by stringifying as the whole
        # payload rather than raising. (Logs WARNING via the outer guard /
        # the gate's own try/except; the record still lands with schema_v.)
        ext_info = {"_payload": str(ext_info)}
    # ``ensure_ascii=False`` keeps Chinese error_msg/short_profile/etc.
    # readable in DB rows + logs (matches the codebase convention at 15+
    # other json.dumps call sites). The trajectory DB column is TEXT utf8mb4.
    return json.dumps(
        {"schema_v": _EXT_INFO_SCHEMA_VERSION, **ext_info},
        ensure_ascii=False,
    )


def build_trajectory_event_record(
    task_id: str,
    node_id: str,
    action_type: TrajectoryActionType | str,
    *,
    action_result: str,
    attempt: int = 0,
    action_input: str | None = None,
    error_type: ReasonCatalog | str | None = None,
    error_msg: str | None = None,
    status_from: "Status | str | None" = None,
    status_to: "Status | str | None" = None,
    ext_info: dict[str, Any] | None = None,
    boost_reason: str | None = None,
    now_ms: int | None = None,
) -> TrajectoryEventRecord:
    """Build a table-faithful ``TrajectoryEventRecord`` from gate-call fields.

    Pure function: no IO, no logging, no side effects. Domain enums
    (``action_type``/``error_type``/``status_from``/``status_to``) are
    serialized to their ``.value`` strings; ``now_ms`` (int epoch ms) becomes
    a timezone-less Asia/Shanghai ``datetime``; ``ext_info`` dict is
    JSON-encoded and wrapped in ``{"schema_v": 1, **ext_info}``; ``analysis`` is
    always ``None`` at emit
    time. ``id=0`` is a placeholder the repository ignores (autoincrement
    assigns the real id, see ``TaskTrajectoryRepository._to_event_row``).

    ``action_input`` is **not truncated** (execute/verify request 原文,
    submit task_spec_digest, plan prompt_digest) — passed through as-is.
    """
    gmt = _now_datetime(now_ms)
    return TrajectoryEventRecord(
        id=0,  # placeholder — repo ignores, autoincrement assigns the real id
        task_id=task_id,
        node_id=node_id,
        action_type=_enum_to_str(action_type),
        action_result=action_result,
        action_input=action_input,
        status_from=_enum_to_str(status_from),
        status_to=_enum_to_str(status_to),
        attempt=attempt,
        boost_reason=boost_reason,
        error_type=_enum_to_str(error_type),
        error_msg=error_msg,
        ext_info=_serialize_ext_info(ext_info),
        analysis=None,  # filled only on backfill (P5); ALWAYS None at emit
        gmt_create=gmt,
        gmt_modified=gmt,  # == gmt_create at emit; updates on backfill
    )


# ---------------------------------------------------------------------------
# Emitter — zero-intrusion direct INSERT via the repo
# ---------------------------------------------------------------------------


def emit_trajectory_event(
    repo: TaskTrajectoryRepositoryLike | None,
    task_id: str,
    node_id: str,
    action_type: TrajectoryActionType | str,
    *,
    action_result: str,
    action_input: str | None = None,
    error_type: ReasonCatalog | str | None = None,
    error_msg: str | None = None,
    ext_info: dict[str, Any] | None = None,
    status_from: "Status | str | None" = None,
    status_to: "Status | str | None" = None,
    attempt: int = 0,
    boost_reason: str | None = None,
    now_ms: int | None = None,
) -> None:
    """Emit one trajectory event via ``repo.insert_event``; **never raises**.

    Builds the record from the gate-call fields (``build_trajectory_event_record``)
    and INSERTs it directly through the trajectory repository — independent of
    the ``task_action_log`` / ``append_action_event`` path. Any failure
    (repo exception, JSON encode error, …) is swallowed + logged at WARNING
    (已确认决策 #14; AGENTS.md "propagate persistence write failures" is
    explicitly waived for this fire-and-forget observational 旁路) so the
    forward-driving gate stays unaffected — the emitter returns ``None``
    whether or not the row landed.

    ``repo is None`` → no-op (the engine can run without a trajectory repo in
    tests / lightweight DI injectors; no insert, no raise, no log). Returns
    ``None`` in all cases — callers never get a result/failure handle.
    """
    if repo is None:
        return
    try:
        record = build_trajectory_event_record(
            task_id,
            node_id,
            action_type,
            action_result=action_result,
            attempt=attempt,
            boost_reason=boost_reason,
            action_input=action_input,
            error_type=error_type,
            error_msg=error_msg,
            status_from=status_from,
            status_to=status_to,
            ext_info=ext_info,
            now_ms=now_ms,
        )
        repo.insert_event(record)
    except Exception as ex:  # noqa: BLE001  fire-and-forget 观测旁路:吞而不抛 (已确认决策 #14)
        logger.warning(
            "[task][trajectory] task=%s node=%s action=%s 发射失败:%s",
            task_id,
            node_id,
            _enum_to_str(action_type),
            ex,
        )
