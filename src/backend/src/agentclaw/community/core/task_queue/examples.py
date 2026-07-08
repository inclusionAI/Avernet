"""Example task handlers.

These ship with the component to (a) exercise the worker in tests and (b)
show the shape a real adopter's handler takes. They are **not** registered in
production wiring — the registry is empty until an adopter registers its own
handlers (see ``HandlerRegistry`` docs for the bootstrap-time contract).
"""
from __future__ import annotations

from typing import Callable, Optional, Set

from agentclaw.community.core.task_queue.types import Complete, Fail, Reschedule, TaskOutcome


class NoopTaskHandler:
    """A handler that does nothing and completes immediately.

    Useful as a smoke handler and as the minimal example of the
    ``TaskHandler`` shape (a ``task_type`` plus a ``handle`` returning a
    :class:`TaskOutcome`).
    """

    def __init__(self, task_type: str = "noop") -> None:
        self._task_type = task_type

    @property
    def task_type(self) -> str:
        return self._task_type

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        return Complete()


class PollUntilTerminalExampleHandler:
    """Polls an external status until it reaches a terminal state — the shape
    the publish-approval poller (the motivating use case) would take.

    A real adopter would inject ``BaasService`` and read
    ``get_publish_progress(payload["publish_id"])["status"]``. Here the status
    lookup is a plain ``status_fn(payload) -> str`` so the behavior is
    testable without any external dependency:

    - status in ``success_states`` → :class:`Complete` (done).
    - status in ``failure_states`` → :class:`Fail` (terminal, give up now).
    - otherwise → :class:`Reschedule` after ``delay_seconds`` (poll again).

    The worker's deadline still bounds the whole thing: if the status never
    becomes terminal, the task is abandoned once its deadline elapses — the
    handler itself never has to count attempts or watch the clock.
    """

    def __init__(
        self,
        status_fn: Callable[[Optional[dict]], str],
        *,
        success_states: Set[str],
        failure_states: Optional[Set[str]] = None,
        task_type: str = "poll_until_terminal",
        delay_seconds: float = 5.0,
    ) -> None:
        self._status_fn = status_fn
        self._success_states = success_states
        self._failure_states = failure_states or set()
        self._task_type = task_type
        self._delay_seconds = delay_seconds

    @property
    def task_type(self) -> str:
        return self._task_type

    def handle(self, payload: Optional[dict]) -> TaskOutcome:
        status = self._status_fn(payload)
        if status in self._success_states:
            return Complete()
        if status in self._failure_states:
            return Fail(f"reached failure state: {status}")
        return Reschedule(self._delay_seconds)
