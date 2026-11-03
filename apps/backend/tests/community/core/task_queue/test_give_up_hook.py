"""The give-up hook: the queue tells a task type when it gives up on one of its tasks.

Against the same in-memory SQLite worker rig as ``test_task_worker``. Three
paths give up — the claim scan retiring a past-deadline task, a retry that
would land past the deadline, a handler's ``Fail`` — and each must call the
type's ``on_give_up`` once with the terminal record; success never does.
"""

import asyncio

import pytest

from agentclaw.community.core.task_queue.services.registry import GivesUp
from agentclaw.community.core.task_queue.types import Complete, Fail, TaskStatus
from tests.community.core.task_queue.test_task_worker import APP, ENV, _world

pytestmark = pytest.mark.integration


class _Hooked:
    """Returns what it was told to; records every give-up it hears."""

    task_type = "hooked"

    def __init__(self, outcome=None, *, hook_raises: bool = False) -> None:
        self.outcome = outcome
        self.hook_raises = hook_raises
        self.given_up: list = []

    def handle(self, payload):
        if self.outcome is None:
            raise RuntimeError("boom")
        return self.outcome

    def on_give_up(self, task) -> None:
        self.given_up.append(task)
        if self.hook_raises:
            raise RuntimeError("hook failed")


def test_a_handler_with_on_give_up_is_recognised_and_one_without_is_not() -> None:
    assert isinstance(_Hooked(Complete()), GivesUp)

    class _Plain:
        task_type = "plain"

        def handle(self, payload):
            return Complete()

    assert not isinstance(_Plain(), GivesUp)


def test_the_claim_scan_retiring_a_past_deadline_task_calls_the_hook() -> None:
    w = _world()
    handler = _Hooked(Complete())
    w.registry.register(handler)
    rec = w.enqueue("hooked", {"k": 1}, deadline_seconds=0)
    assert asyncio.run(w.worker.run_once()) == 0
    [task] = handler.given_up
    assert (task.id, task.status, task.payload) == (rec.id, TaskStatus.TIMED_OUT, {"k": 1})


def test_a_retry_that_would_overshoot_the_deadline_calls_the_hook() -> None:
    w = _world(retry_backoff_min_seconds=100.0)
    handler = _Hooked()  # raises → Retry, whose backoff lands past the 1s deadline
    w.registry.register(handler)
    rec = w.enqueue("hooked", deadline_seconds=1)
    asyncio.run(w.worker.run_once())
    assert w.status_of(rec.id) is TaskStatus.TIMED_OUT
    assert [t.status for t in handler.given_up] == [TaskStatus.TIMED_OUT]


def test_a_retry_within_the_deadline_does_not_call_the_hook() -> None:
    w = _world(retry_backoff_min_seconds=1.0)
    handler = _Hooked()
    w.registry.register(handler)
    rec = w.enqueue("hooked", deadline_seconds=3600)
    asyncio.run(w.worker.run_once())
    assert w.status_of(rec.id) is TaskStatus.PENDING
    assert handler.given_up == []


def test_a_handler_fail_calls_the_hook_and_success_does_not() -> None:
    w = _world()
    failing = _Hooked(Fail("cannot"))
    w.registry.register(failing)
    w.enqueue("hooked")
    asyncio.run(w.worker.run_once())
    assert [t.task_type for t in failing.given_up] == ["hooked"]

    w2 = _world()
    succeeding = _Hooked(Complete())
    w2.registry.register(succeeding)
    w2.enqueue("hooked")
    asyncio.run(w2.worker.run_once())
    assert succeeding.given_up == []


def test_a_raising_hook_is_swallowed_and_the_task_stays_terminal() -> None:
    w = _world()
    handler = _Hooked(Complete(), hook_raises=True)
    w.registry.register(handler)
    rec = w.enqueue("hooked", deadline_seconds=0)
    asyncio.run(w.worker.run_once())
    assert len(handler.given_up) == 1
    assert w.status_of(rec.id) is TaskStatus.TIMED_OUT


def test_the_repository_reports_only_the_rows_this_scan_retired() -> None:
    w = _world()
    due = w.enqueue("hooked", deadline_seconds=3600)
    late = w.enqueue("hooked", deadline_seconds=0)
    won, retired = w.repo.claim_batch_reporting_retired(worker_id="w1", env=ENV, app=APP, limit=10, lease_seconds=60)
    assert [t.id for t in won] == [due.id]
    assert [(t.id, t.status) for t in retired] == [(late.id, TaskStatus.TIMED_OUT)]
    again = w.repo.claim_batch_reporting_retired(worker_id="w2", env=ENV, app=APP, limit=10, lease_seconds=60)
    assert again.retired == [], "a row already retired is not retired again"
