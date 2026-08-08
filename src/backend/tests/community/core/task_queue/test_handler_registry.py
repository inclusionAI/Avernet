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


@pytest.mark.parametrize("variant", ["Job", "JOB", "jOb", "job ", "job\t"])
def test_task_type_colliding_only_by_case_or_trailing_space_is_rejected(variant):
    """The dedup index is UNIQUE (env, task_type, active_idempotency_key) and
    ``task_type`` carries the table's default collation — case-insensitive and
    PAD SPACE on MySQL/OceanBase. So these pairs are two registry keys but one
    index value, and a keyed enqueue for one would silently join the other's
    task. SQLite compares BINARY, so only rejecting at registration keeps the
    two engines agreeing."""
    reg = HandlerRegistry()
    reg.register(_Handler("job"))
    with pytest.raises(ValueError, match="only by case or trailing whitespace"):
        reg.register(_Handler(variant))
    assert reg.get(variant) is None  # and it was not registered


def test_exact_duplicate_still_reports_as_a_duplicate_not_a_near_collision():
    """An exact repeat folds to the same key too, so the order of the two
    checks matters — the plain-duplicate message must win."""
    reg = HandlerRegistry()
    reg.register(_Handler("job"))
    with pytest.raises(ValueError, match="already has a handler"):
        reg.register(_Handler("job"))


def test_leading_whitespace_does_not_block_an_unrelated_type():
    """Only *trailing* space is folded by PAD SPACE, so a leading space makes a
    genuinely distinct index value — do not over-reject."""
    reg = HandlerRegistry()
    reg.register(_Handler("job"))
    reg.register(_Handler(" job"))
    assert reg.get(" job") is not None


def test_real_task_type_constants_do_not_collide_pairwise():
    """The guard only helps if the shipped task types satisfy it. Registering
    every real constant into one registry is the same check the app performs at
    startup, so a future name that folds onto an existing one fails here rather
    than at boot — and the constants are imported, not restated, so a rename
    cannot silently drop out of this list."""
    from agentclaw.community.core.bot_management.services import (
        teclaw_publish_task_handler as teclaw,
    )
    from agentclaw.community.core.devices.services import (
        baas_publish_task_handlers as baas,
    )
    from agentclaw.community.core.service_bot.services.publish_flow import tasks
    from agentclaw.community.core.skills_pool import quarantine, reconcile_task

    modules = (tasks, baas, teclaw, quarantine, reconcile_task)
    task_types = sorted(
        {
            value
            for module in modules
            for name, value in vars(module).items()
            if name.endswith("_TASK") and isinstance(value, str)
        }
    )
    assert len(task_types) >= 14, f"expected the full set, found {task_types}"

    reg = HandlerRegistry()
    for task_type in task_types:
        reg.register(_Handler(task_type))  # raises if any pair folds together


def test_plain_object_satisfies_taskhandler_protocol():
    # No base class required — structural typing.
    assert isinstance(_Handler("x"), TaskHandler)
