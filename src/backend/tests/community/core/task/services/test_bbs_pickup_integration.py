"""BBS 自主接单端到端集成场景(plan §10.3/§10.4,Task 9)。

Exercises the three relay paths the BBS pickup skill depends on, against the
real TaskService wiring (Tasks 1-8 already landed):

- **race**: two bots claim the same PENDING node concurrently → exactly one
  wins, the other hits the state-machine guard (``IllegalTransitionError``).
  This is the CAS idempotency guarantee (FR-IDEM-01).
- **handoff**: bot-A claims, checkpoints 30% via the real ``state.updated``
  event fold, then ``release_node`` (RUNNING→FAILED/handoff) → bot-B relays
  immediately (FAILED→RUNNING) and sees the 30% trajectory (FR-IDEM-03/04).
- **crash**: bot-A claims, checkpoints 50%, then dies without release. The
  ``LeaseSweeper`` reclaims the lease past ``BBS_LEASE_FALLBACK_SECONDS``
  (RUNNING→FAILED/lease_expired); bot-B relays and the 50% trajectory survives
  the crash-sweep (FR-IDEM-05 / FR-EXT-04).

Avernet rules: ``from __future__ import annotations``; ``Optional[T]`` (never
``T | None``); monkeypatch the ``_utcnow`` seam where it actually lives —
``bbs_lease_ops._utcnow`` (moved out of ``task_service`` in Task 3's mixin
extraction). Checkpoints go through the real ``on_event`` ``state.updated``
path (the same path ``update_state`` uses), not direct SubtaskState mutation.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from agentclaw.community.core.task.domain.models import (
    NodeType,
    NodeStatus,
    RunMode,
    SubTaskSpec,
)
from agentclaw.community.core.task.domain.state_machine import IllegalTransitionError
from agentclaw.community.core.task.services import TaskService
from agentclaw.community.core.task.services import bbs_lease_ops as lease_mod
from agentclaw.community.core.task.services.lease_sweeper import LeaseSweeper
from agentclaw.community.plugins.community.task.in_memory_repos import (
    InMemoryTaskEventRepo,
    InMemoryTaskRepo,
)
from agentclaw.community.plugins.community.task.panel_publisher import (
    RecordingPanelPublisher,
)


def _svc_with_node(tmp_id: str = "n1"):
    """Build a task + a single PENDING BBS_dispatch node (``tmp_id``) ready to
    be claimed. Mirrors the wiring in ``test_lease_sweeper`` /
    ``test_bbs_executor``: ``add_node`` needs an explicit ``node_type`` (per
    ``graph_state_ops.add_node`` signature) — ``NodeType.DISPATCH`` is the
    BBS-claimable leaf kind."""
    svc = TaskService(
        InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher()
    )
    t = svc.create(title="t")
    svc.clarify(t.id, {"summary": "s"})
    svc.clarify(t.id, {}, confirmed=True)
    task = svc.get(t.id)
    svc.init_execution_graph(task)
    svc.add_node(
        t.id,
        SubTaskSpec(node_id=tmp_id, spec="a", run_mode=RunMode.BBS),
        "n_execute_start",
        NodeType.DISPATCH,
    )
    return svc, t.id, tmp_id


def test_race_only_one_wins():
    """FR-IDEM-01: two bots race one PENDING node — CAS lets exactly one win;
    the second hits the state-machine guard (PENDING is gone → RUNNING is the
    only legal source for a second claim, and RUNNING→RUNNING is not a claim)."""
    svc, task_id, node_id = _svc_with_node()
    r1 = svc.claim_node(task_id, node_id, "bot-A", run_mode=RunMode.BBS)
    assert r1 is not None
    assert r1.executor_id == "bot-A"
    with pytest.raises(IllegalTransitionError):
        svc.claim_node(task_id, node_id, "bot-B", run_mode=RunMode.BBS)


def test_handoff_immediate_relay_with_trajectory():
    """FR-IDEM-03/04: bot-A claims, checkpoints 30% via the real ``state.updated``
    fold, then ``release_node`` (handoff) → bot-B relays immediately and the
    30% trajectory is visible in ``get_node_detail`` (no redo from scratch)."""
    svc, task_id, node_id = _svc_with_node()
    svc.claim_node(task_id, node_id, "bot-A", run_mode=RunMode.BBS)
    # bot-A checkpoint 30% through the real event-sourced write path (same path
    # ``update_state`` routes through); APPEND with scope=node_id lands on
    # SubtaskState.intermediate_results.
    svc.on_event(
        {
            "task_id": task_id,
            "kind": "state.updated",
            "payload": {
                "scope": node_id,
                "semantics": "append",
                "patch": {"intermediate_results": [{"step": 1, "pct": 30}]},
            },
        }
    )
    svc.release_node(task_id, node_id, "bot-A")  # 立即让出 RUNNING→FAILED/handoff
    # bot-B 接力:FAILED→RUNNING,run_mode=BBS
    svc.claim_node(task_id, node_id, "bot-B", run_mode=RunMode.BBS)
    detail = svc.get_node_detail(task_id, node_id)
    assert detail is not None
    assert any(r.get("pct") == 30 for r in detail["intermediate_results"])


def test_crash_relay_after_lease_expiry():
    """FR-IDEM-05 / FR-EXT-04: bot-A claims, checkpoints 50%, then crashes
    (no release). Freezing the clock past the fallback lease and running the
    sweeper reclaims the node (RUNNING→FAILED/lease_expired); bot-B then relays
    and the 50% trajectory survives the crash-sweep."""
    svc, task_id, node_id = _svc_with_node()
    svc.claim_node(task_id, node_id, "bot-A", run_mode=RunMode.BBS)
    svc.on_event(
        {
            "task_id": task_id,
            "kind": "state.updated",
            "payload": {
                "scope": node_id,
                "semantics": "append",
                "patch": {"intermediate_results": [{"step": 1, "pct": 50}]},
            },
        }
    )
    # bot-A 崩溃(不 release) → 冻到租期之后清扫。Patch the seam where it lives
    # now — bbs_lease_ops._utcnow (not task_service._utcnow).
    future = _dt.datetime(2099, 1, 1, tzinfo=_dt.timezone.utc)
    orig = lease_mod._utcnow
    lease_mod._utcnow = lambda: future
    try:
        assert LeaseSweeper(svc).sweep_once() == 1
    finally:
        lease_mod._utcnow = orig
    node = next(
        n for n in svc.get(task_id).execution_graph.nodes if n.node_id == node_id
    )
    assert node.status is NodeStatus.FAILED
    assert node.properties.get("release_outcome") == "lease_expired"
    # bot-B 接力,50% 轨迹保留 across the crash-sweep
    svc.claim_node(task_id, node_id, "bot-B", run_mode=RunMode.BBS)
    detail = svc.get_node_detail(task_id, node_id)
    assert detail is not None
    assert any(r.get("pct") == 50 for r in detail["intermediate_results"])
