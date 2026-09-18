"""``TaskTrajectoryAssembler`` — read-side assembler (REQ-8, P4).

The READ-SIDE pure function that, given a ``task_id``, reconstructs a
``TaskTrajectory{task_id, timeline, analysis, gmt_create, gmt_modified}``
(with NO ``phases`` / NO ``graph_snapshot``) from the trajectory persistence
tables (``task_trajectory_events`` for the timeline + ``task_trajectory`` for
the head's persisted ``analysis``):

1. ``list_events_by_task(task_id)`` → ``list[TrajectoryEventRecord]`` (the
   P1b repository already returns ``ORDER BY gmt_create ASC, id ASC``; the
   assembler RELIES on that order and additionally applies a CHEAP defensive
   stable sort by ``(gmt_create, id)`` — idempotent on already-sorted input,
   guarding against a future repository regression).
2. Each ``TrajectoryEventRecord`` → ``TrajectoryEvent`` (domain):
   * ``gmt_create``/``gmt_modified``: ``datetime`` (record) → ``int`` ms (domain)
     via ``_datetime_to_int_ms`` — the EXACT inverse of the P2 emitter's
     naive-UTC convention (the emitter writes ``datetime.fromtimestamp(ms/1000,
     tz=utc).replace(tzinfo=None)`` — naive UTC). The inverse MUST treat a
     naive datetime AS UTC (the bare ``int(dt.timestamp()*1000)`` formula a
     casual reader might reach for instead treats naive dt as LOCAL time —
     on a non-UTC host that is off by the local tz offset and breaks the
     round-trip). ``None`` → 0 (matches ``TaskActionLogRecord.to_event``).
   * ``action_type`` (lowercase string) → ``TrajectoryActionType(value)``.
   * ``status_from``/``status_to`` (UPPERCASE string, the ``Status`` enum's
     ``.value``) → ``Status(value)`` or ``None``.
   * ``error_type`` (lowercase string) → ``ReasonCatalog(value)`` or ``None``.
     **Case-mismatch gotcha**: ``action_type`` and ``error_type`` are LOWERCASE,
     ``status_from``/``status_to`` are UPPERCASE. Each field is mapped via its
     OWN enum — a shared enumer (``Status(action_type)``, ``TrajectoryActionType
     (status_from)``) would raise on a realistic value. The mapping below never
     mixes them.
   * ``action_input`` / ``ext_info`` / ``analysis``: passed through as the
     raw JSON strings the emitter wrote (``ext_info``/``analysis`` are JSON
     via ``json.dumps(…, ensure_ascii=False)`` at emit time). The assembler
     does NOT parse them — the P5 analyzer parses on demand.
   * ``attempt`` / ``task_id`` / ``node_id`` / ``action_result`` / ``error_msg``:
     pass through verbatim.
3. Read the head: ``list_head(task_id)`` → ``TaskTrajectoryRecord | None``;
   ``analysis`` is taken from this head (the persisted JSON, or ``None`` if
   never backfilled / no head row yet).
4. UPSERT the head row: ``upsert_head(task_id)`` (P1b's ``upsert_head``
   PRESERVES existing ``analysis``/``gmt_modified`` — it does NOT overwrite;
   calling it on read refreshes the head's existence / ``gmt_create`` when
   absent, and returns the authoritative head row carrying ``gmt_create`` /
   ``gmt_modified`` for the resulting ``TaskTrajectory``).
5. Build ``TaskTrajectory{task_id, timeline, analysis, gmt_create,
   gmt_modified}`` with ``gmt_create`` from the post-UPSERT head (or
   ``now`` if absent) and ``gmt_modified`` from the post-UPSERT head (or
   falls back to the trajectory's own ``gmt_create``).

**Failure handling** — separate regimes per spec decisions:

* **Wholesale read failures propagate (NOT swallowed)** — 决策 #14's
  swallow waiver is EMISSION-only (the fire-and-forget observational旁路); the
  assembler is a READ and a repo error (``list_events_by_task`` /
  ``list_head`` / ``upsert_head``) propagates to the caller (the P5
  service). The assembler does NOT wrap the read paths in ``try/except →
  None`` — that would mask a real DB error and violate AGENTS.md "propagate
  write/read failures".
* **Single corrupt row skipped + WARNING** — when one record's enum-shaped
  field can't deserialize (e.g., an old experiment's ``action_type`` string,
  an unknown UPPERCASE status), the row is skipped with a WARNING log so a
  single corrupt row does not break a whole timeline. This is the ONLY
  defensive thing the read side does at the per-row level. The CHEAP, isolated
  ValueError from a failing ``Enum(bad_value)`` is the trigger; a wholesale
  read error (the repo call itself raising) is the "propagate" case above.

**Independent entity** (spec invariant): the assembler takes ONLY
``TaskTrajectoryRepositoryProtocol``; it does NOT receive or query
``task_action_log`` / ``task_callback`` repos; it never invokes
``append_action_event``; it never imports ``NodeAction``. The trajectory side
is a 独立旁路 (read mirror of the 独立旁路 emission). Pre-trajectory old
tasks (no trajectory rows) get an EMPTY timeline (NO ``task_action_log``
fallback).

Authoritative source: ``specs/2026-09-16-task-trajectory-collection-and-analysis/
spec.md`` (REQ-8 reads ``task_trajectory_events`` ASC ``gmt_create``; maps to
``TrajectoryEvent``; produces ``TaskTrajectory{task_id, timeline, analysis,
gmt_create, gmt_modified}`` — NO phases/graph_snapshot; reads ``analysis`` from
persisted; UPSERT head preserving analysis; pre-trajectory old tasks → empty
timeline; never touches ``task_action_log``) + 决策 #14 + plan.md Phase 4.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskTrajectoryRepositoryProtocol,
)
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.types import TrajectoryEventRecord
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    ReasonCatalog,
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryEvent,
)

logger = logging.getLogger("task.trajectory")


# ---------------------------------------------------------------------------
# datetime → int-ms inverse of the P2 emitter's naive-UTC convention
# ---------------------------------------------------------------------------


def _datetime_to_int_ms(dt: Optional[datetime]) -> int:
    """Convert a record ``datetime`` back to the domain int-ms epoch (the
    inverse of the P2 emitter's ``_now_datetime``).

    The emitter writes naive-UTC datetimes (``datetime.fromtimestamp(ms/1000,
    tz=timezone.utc).replace(tzinfo=None)`` — naive, value-wise UTC). The
    inverse MUST treat a naive datetime AS UTC: a bare ``int(dt.timestamp() *
    1000)`` would instead interpret naive dt as LOCAL time, so on a non-UTC
    host the result is off by the local tz offset (CST-8 cuts -28800000 ms)
    and the round-trip breaks. For tz-aware datetimes the value is already
    unambiguous. ``None`` falls back to ``0`` (mirrors the existing
    ``TaskActionLogRecord.to_event`` ``ts=0`` fallback).
    """
    if dt is None:
        return 0
    if dt.tzinfo is None:
        # Naive dt: treat as UTC (preserves the emitter's naive-UTC convention).
        return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Assembler — pure read side, never touches task_action_log / NodeAction
# ---------------------------------------------------------------------------


class TaskTrajectoryAssembler:
    """Read-side assembler that reconstructs a ``TaskTrajectory`` from the
    trajectory persistence tables.

    Takes ONLY ``TaskTrajectoryRepositoryProtocol`` (the P1b protocol) — no
    action-log / callback repos, no transport. Independent entity: the
    trajectory side never touches ``task_action_log`` / ``NodeAction`` /
    ``append_action_event`` (spec invariant / 决策 #14 boundary).
    """

    @inject
    def __init__(self, repo: TaskTrajectoryRepositoryProtocol) -> None:
        self._repo = repo

    def assemble(self, task_id: str) -> TaskTrajectory:
        """Reconstruct ``TaskTrajectory`` for ``task_id``.

        The four-step flow (see module docstring):
        1. ``list_events_by_task`` — wholesale read; raises propagate (NOT
           swallowed per 决策 #14's emission-only waiver).
        2. Map each record → ``TrajectoryEvent``; a single corrupt row is
           skipped + logged WARNING, the rest survive.
        3. ``list_head`` — read the head's persisted ``analysis`` (None when
           no head row yet); raises propagate.
        4. ``upsert_head`` — ensure the head row exists (P1b PRESERVES existing
           ``analysis``/``gmt_modified``; calling it on a read refreshes the
           head's existence when absent); raises propagate.

        Returns ``TaskTrajectory{task_id, timeline, analysis, gmt_create,
        gmt_modified}`` (no ``phases`` / no ``graph_snapshot``). Pre-trajectory
        old tasks (no trajectory rows) yield an empty ``timeline`` with
        ``analysis=None`` — there is NO ``task_action_log`` fallback.
        """
        # 1. Read events (already ORDER BY gmt_create ASC, id ASC from P1b).
        #    Do NOT swallow a wholesale read error (决策 #14 is emission-only).
        records = self._repo.list_events_by_task(task_id)
        # Defensive stable sort by (gmt_create, id) — CHEAP and idempotent on
        # already-sorted input (Python's sort is stable), guards against a
        # future repository regression. None-safe via datetime.min (naive).
        records = sorted(
            records,
            key=lambda r: (
                r.gmt_create if r.gmt_create is not None else datetime.min,
                r.id,
            ),
        )

        # 2. Map each record → TrajectoryEvent (skip corrupt rows + WARNING).
        timeline: list[TrajectoryEvent] = []
        for rec in records:
            try:
                timeline.append(self._map_record(rec))
            except (ValueError, TypeError) as ex:
                # Single corrupt row: skip + WARNING so one bad row does not
                # break the whole timeline (the only per-row defensive thing
                # the read side does). Name the row_id/action_type for ops.
                logger.warning(
                    "[task][trajectory] task=%s 跳过损坏事件行: id=%s node=%s "
                    "action_type=%r 原因略(%s: %s)",
                    task_id,
                    getattr(rec, "id", None),
                    getattr(rec, "node_id", None),
                    getattr(rec, "action_type", None),
                    type(ex).__name__,
                    ex,
                )

        # 3. Read the head (analysis payload) — wholesale read; raises propagate.
        head_read = self._repo.list_head(task_id)
        # 4. UPSERT head (ensure existence; preserves existing analysis/gmt_modified).
        head_after = self._repo.upsert_head(task_id)

        # 5. Build TaskTrajectory.
        # ``analysis`` from list_head (None when head was absent pre-upsert; the
        # fresh insert path supplies analysis=None too, so equivalent).
        analysis = head_read.analysis if head_read is not None else None

        # ``gmt_create``: post-UPSERT head's gmt_create (fresh DB default on
        # insert; preserved existing value on the existing path), or ``now``
        # fallback only if the DB failed to populate (should not happen).
        now_ms = int(time.time() * 1000)
        head_create_ms = (
            _datetime_to_int_ms(head_after.gmt_create)
            if head_after.gmt_create is not None
            else now_ms
        )
        # ``gmt_modified``: post-UPSERT head's gmt_modified, or fall back to
        # the trajectory's own gmt_create (matches the spec's "gmt_modified =
        # gmt_create when never analyzed" invariant).
        head_modified_ms = (
            _datetime_to_int_ms(head_after.gmt_modified)
            if head_after.gmt_modified is not None
            else head_create_ms
        )

        return TaskTrajectory(
            task_id=task_id,
            gmt_create=head_create_ms,
            gmt_modified=head_modified_ms,
            timeline=timeline,
            analysis=analysis,
        )

    # ------------------------------------------------------------------
    # Per-record mapping — per-field enum (case-mismatch safe); raises on
    # unknown enum values so the caller can skip-the-row + WARNING.
    # ------------------------------------------------------------------

    @staticmethod
    def _map_record(rec: TrajectoryEventRecord) -> TrajectoryEvent:
        """Map one record to a domain ``TrajectoryEvent``.

        Raises ``ValueError`` (or ``TypeError``) when an enum-shaped column's
        string can't deserialize — the caller skip-the-row + WARNINGs the row,
        so a single corrupt row does not break the whole timeline. Wholesale
        repo-read failures are NOT handled here (they propagate from the
        repo call itself).

        ``action_result`` is coerced ``None`` → ``""`` because the domain
        field is ``str`` (non-Optional); the DB column is ``DEFAULT NULL`` but
        the emitter always passes a non-None action_result, so this coercion
        only fires on a stale/legacy row.
        """
        # action_type is required (DB NOT NULL); unknown value → ValueError.
        action_type = TrajectoryActionType(rec.action_type)
        # status_from/status_to are UPPERCASE on the Status enum; Optional.
        status_from = Status(rec.status_from) if rec.status_from is not None else None
        status_to = Status(rec.status_to) if rec.status_to is not None else None
        # error_type is lowercase on ReasonCatalog; Optional.
        error_type = ReasonCatalog(rec.error_type) if rec.error_type is not None else None
        return TrajectoryEvent(
            task_id=rec.task_id,
            node_id=rec.node_id,
            action_type=action_type,
            action_result=rec.action_result if rec.action_result is not None else "",
            attempt=rec.attempt,
            gmt_create=_datetime_to_int_ms(rec.gmt_create),
            gmt_modified=_datetime_to_int_ms(rec.gmt_modified),
            action_input=rec.action_input,
            status_from=status_from,
            status_to=status_to,
            error_type=error_type,
            error_msg=rec.error_msg,
            analysis=rec.analysis,
        )
