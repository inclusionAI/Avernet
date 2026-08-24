"""Unit tests for the TaskRecoveryLifecycle startup/shutdown wiring."""
from __future__ import annotations

from agentclaw.community.core.task.task_center.recovery_lifecycle import (
    TaskRecoveryLifecycle,
)


class _FakeInjector:
    """Resolves the recovery worker's two dependencies to in-memory fakes."""

    def __init__(self, graph_repo, task_service):
        self._graph_repo = graph_repo
        self._task_service = task_service
        self.calls: list = []

    def get(self, interface):
        self.calls.append(interface)
        from agentclaw.community.api.task.task_service import TaskServiceProtocol
        from agentclaw.community.core.repository.protocols.task import (
            TaskGraphRepositoryProtocol,
        )
        if interface is TaskGraphRepositoryProtocol:
            return self._graph_repo
        if interface is TaskServiceProtocol:
            return self._task_service
        raise KeyError(interface)


class _FakeGraphRepo:
    def __init__(self, recoverable):
        self._recoverable = recoverable

    def list_recoverable(self, *, limit=100):
        return list(self._recoverable)[:limit]

    def acquire_lease(self, task_id, *, instance_id, lease_seconds):
        return True

    def load_graph(self, task_id):
        return object()  # truthy → resume is invoked

    def release_lease(self, task_id, *, instance_id):
        return True


class _FakeTaskService:
    def __init__(self):
        self.redriven: list = []

    async def redrive_task(self, task_id):
        self.redriven.append(task_id)


def _make(injector):
    # Bypass __init__ @inject decoration to drive config directly.
    lc = TaskRecoveryLifecycle.__new__(TaskRecoveryLifecycle)
    lc._injector = injector
    lc._enabled = False
    lc._interval = 0
    lc._lease_seconds = 10
    lc._instance_id = "test-instance"
    lc._worker = None
    lc._stop_event = __import__("threading").Event()
    lc._thread = None
    return lc


def test_recovery_disabled_does_not_start_thread():
    lc = _make(_FakeInjector(_FakeGraphRepo([]), _FakeTaskService()))
    lc._enabled = False
    import asyncio
    asyncio.run(lc.startup())
    assert lc._thread is None


def test_recovery_worker_resolves_and_runs_once():
    repo = _FakeGraphRepo(["t-recover"])
    svc = _FakeTaskService()
    inj = _FakeInjector(repo, svc)
    lc = _make(inj)
    lc._enabled = True
    worker = lc._resolve_worker()
    assert worker is not None
    import asyncio
    recovered = asyncio.run(worker.recover_once())
    assert recovered == ["t-recover"]
    assert svc.redriven == ["t-recover"]
    # graph repo + task service resolved lazily exactly once each
    from agentclaw.community.api.task.task_service import TaskServiceProtocol
    from agentclaw.community.core.repository.protocols.task import (
        TaskGraphRepositoryProtocol,
    )
    assert inj.calls.count(TaskGraphRepositoryProtocol) == 1
    assert inj.calls.count(TaskServiceProtocol) == 1
