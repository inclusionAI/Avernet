"""Unit tests for HandlerRegistry."""
import pytest

from agentclaw.community.core.task_queue.services.registry import HandlerRegistry, TaskHandler
from agentclaw.community.core.task_queue.types import Complete, Reschedule


class _Handler:
    def __init__(self, task_type, outcome=Complete()):
        self._task_type = task_type
        self._outcome = outcome

    @property
    def task_type(self):
        return self._task_type

    def handle(self, payload):
        return self._outcome


def test_register_then_get_returns_handler():
    reg = HandlerRegistry()
    h = _Handler("poll_publish")
    reg.register(h)
    assert reg.get("poll_publish") is h


def test_get_unregistered_returns_none():
    assert HandlerRegistry().get("missing") is None


def test_duplicate_task_type_raises():
    reg = HandlerRegistry()
    reg.register(_Handler("dup"))
    with pytest.raises(ValueError, match="already has a handler"):
        reg.register(_Handler("dup"))


def test_distinct_task_types_coexist():
    reg = HandlerRegistry()
    a = _Handler("a", Complete())
    b = _Handler("b", Reschedule(5))
    reg.register(a)
    reg.register(b)
    assert reg.get("a") is a and reg.get("b") is b


def test_plain_object_satisfies_taskhandler_protocol():
    # No base class required — structural typing.
    assert isinstance(_Handler("x"), TaskHandler)
