"""TDD for the BBS 兜底租期清扫器 (plan §10.3 / FR-EXT-04, Task 7).

``LeaseSweeper`` is a mechanical thin wrapper over
``TaskService.sweep_expired_leases``: it scans every RUNNING node whose system
``lease_until`` (set on ``claim_node``) is past ``now`` and calls
``expire_lease`` so the node returns to a relayable FAILED state — the
crash-safety net for bots that die or hang mid-execution. Periodic triggering
(timer/loop) is a deployment follow-up; this suite exercises the single
``sweep_once()`` pass only.

Avernet rules: ``from __future__ import annotations``; ``Optional[T]``;
monkeypatch the ``_utcnow`` seam where it actually lives —
``bbs_lease_ops._utcnow`` (moved out of ``task_service`` in Task 3's mixin
extraction).
"""
from __future__ import annotations

import datetime as _dt

from agentclaw.community.core.task.domain.models import (
    NodeType,
    NodeStatus,
    RunMode,
    SubTaskSpec,
)
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


def _svc_with_running_node() -> tuple[TaskService, str, str]:
    """Build a task + a single RUNNING BBS node (n1) carrying a live lease."""
    svc = TaskService(
        InMemoryTaskRepo(), InMemoryTaskEventRepo(), RecordingPanelPublisher()
    )
    t = svc.create(title="t")
    svc.clarify(t.id, {"summary": "s"})
    svc.clarify(t.id, {}, confirmed=True)
    task = svc.get(t.id)
    svc.init_execution_graph(task)
    # add_node requires an explicit node_type (graph_state_ops signature);
    # DISPATCH is the BBS-claimable leaf kind (matches test_bbs_executor).
    svc.add_node(
        t.id,
        SubTaskSpec(node_id="n1", spec="a", run_mode=RunMode.BBS),
        "n_execute_start",
        NodeType.DISPATCH,
    )
    svc.claim_node(t.id, "n1", "bot-A", run_mode=RunMode.BBS)
    return svc, t.id, "n1"


def test_sweep_expires_past_lease():
    """Lease past → node FAILED + assignee cleared + outcome=lease_expired."""
    svc, task_id, node_id = _svc_with_running_node()
    sweeper = LeaseSweeper(svc)
    # Freeze the sweeper's clock past the fallback lease (claim set
    # lease_until = claim_now + BBS_LEASE_FALLBACK_SECONDS). Patch the seam
    # where it lives now — bbs_lease_ops._utcnow (not task_service._utcnow).
    future = _dt.datetime(2099, 1, 1, tzinfo=_dt.timezone.utc)
    orig = lease_mod._utcnow
    lease_mod._utcnow = lambda: future
    try:
        count = sweeper.sweep_once()
    finally:
        lease_mod._utcnow = orig
    assert count == 1
    node = next(
        n for n in svc.get(task_id).execution_graph.nodes if n.node_id == node_id
    )
    assert node.status is NodeStatus.FAILED
    assert node.properties.get("release_outcome") == "lease_expired"
    assert node.assignee is None


def test_sweep_skips_unexpired_lease():
    """Lease still live → sweep reclaims nothing, node stays RUNNING."""
    svc, task_id, node_id = _svc_with_running_node()
    # No clock advance: claim just happened, lease is BBS_LEASE_FALLBACK_SECONDS
    # in the future → sweep_once must reclaim 0.
    assert LeaseSweeper(svc).sweep_once() == 0
    node = next(
        n for n in svc.get(task_id).execution_graph.nodes if n.node_id == node_id
    )
    assert node.status is NodeStatus.RUNNING
